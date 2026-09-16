#!/usr/bin/env python3
"""Run the frozen forced-route timing block; dry-run and make no RPCs by default."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
from prometheus_client.parser import text_string_to_metric_families


HERE = Path(__file__).resolve().parent
NAMESPACE = "topology-measurement"
FIRST_BLOCK_SECONDS = 90 * 60
DEADLINE_RESERVE_SECONDS = 60
REQUIRED_CORRECTNESS_CHECKS = (
    "direct_decode", "local_pd", "remote_pd", "route_pins",
    "transfer_counts", "workers_stable",
)


class MeasurementError(RuntimeError):
    pass


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise MeasurementError(f"cannot read valid JSON from {path}: {error}") from error


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def as_text(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def safe_name(value):
    value = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    return value[:63].rstrip("-")


def first_block_plan(rendered_dir, local_decoder, remote_decoder, suffix):
    def item(label, tokens, route, warmup=False, pair=None):
        decoder = local_decoder if route == "local" else remote_decoder
        prefix = label
        return {
            "label": label,
            "run_id": safe_name(f"{prefix}-{suffix}"),
            "config": str(rendered_dir / (("warmup" if warmup else "benchmark") + f"-{tokens}.yaml")),
            "input_tokens": tokens,
            "expected_requests": 1 if warmup else 12,
            "route": route,
            "decoder_pod": decoder,
            "paired_with": pair,
            "warmup": warmup,
            "route_policy": "forced-diagnostic",
        }

    warmups = [
        item("warm-short-local", 512, "local", True),
        item("warm-short-remote", 512, "remote", True, "warm-short-local"),
        item("warm-long-remote", 8192, "remote", True),
        item("warm-long-local", 8192, "local", True, "warm-long-remote"),
    ]
    arms = [
        item("short-local-b1", 512, "local"),
        item("short-remote-b1", 512, "remote", pair="short-local-b1"),
        item("long-remote-b1", 8192, "remote"),
        item("long-local-b1", 8192, "local", pair="long-remote-b1"),
    ]
    return {"warmups": warmups, "arms": arms}


def validate_plan(plan):
    items = plan["warmups"] + plan["arms"]
    labels = [item["label"] for item in items]
    run_ids = [item["run_id"] for item in items]
    if len(labels) != len(set(labels)) or len(run_ids) != len(set(run_ids)):
        raise MeasurementError("plan labels and run IDs must be unique")
    seen = {}
    for item in items:
        if not Path(item["config"]).is_file():
            raise MeasurementError("missing workload configuration: " + item["config"])
        if item["expected_requests"] <= 0 or item["input_tokens"] <= 0:
            raise MeasurementError("request counts and input tokens must be positive")
        pair = item.get("paired_with")
        if pair:
            if pair not in seen:
                raise MeasurementError(f"{item['label']} pairs with a missing or later arm: {pair}")
            other = seen[pair]
            if other["input_tokens"] != item["input_tokens"] or other["route"] == item["route"]:
                raise MeasurementError(f"invalid local/remote payload pair: {pair}, {item['label']}")
        seen[item["label"]] = item


def validate_correctness_marker(path, session, args, now):
    marker = read_json(path)
    if marker.get("session_id") != session["session_id"] or marker.get("verified") is not True:
        raise MeasurementError("correctness marker is not verified for this exact session")
    checks = marker.get("checks", {})
    missing = [name for name in REQUIRED_CORRECTNESS_CHECKS if checks.get(name) is not True]
    if missing:
        raise MeasurementError("correctness marker lacks passed checks: " + ", ".join(missing))
    expected = {
        "namespace": args.namespace,
        "cpu_node": args.cpu_node,
        "local_decoder_pod": args.local_decoder,
        "remote_decoder_pod": args.remote_decoder,
    }
    for key, value in expected.items():
        if marker.get(key) != value:
            raise MeasurementError(f"correctness marker {key} does not match the requested deployment")
    verified = marker.get("verified_unix")
    if not isinstance(verified, (int, float)) or not session["started_unix"] <= verified <= now + 1:
        raise MeasurementError("correctness marker verification time is outside this session")
    transfers = marker.get("transfers_per_request")
    if not isinstance(transfers, int) or transfers <= 0:
        raise MeasurementError("correctness marker needs a positive integer transfers_per_request")
    return marker


def metric_values(text, source):
    values = {}
    try:
        families = text_string_to_metric_families(text)
        for family in families:
            for sample in family.samples:
                metric = sample.name
                tracked = metric in ("vllm:num_requests_running", "vllm:num_requests_waiting")
                tracked = tracked or (metric.startswith("vllm:nixl_") and
                                       (metric.endswith("_count") or metric.endswith("_sum") or
                                        metric.endswith("_total")))
                if tracked:
                    value = float(sample.value)
                    if not math.isfinite(value):
                        raise MeasurementError(f"non-finite metric in {source}: {metric}")
                    values[metric] = values.get(metric, 0.0) + value
    except ValueError as error:
        raise MeasurementError(f"invalid Prometheus metrics in {source}: {error}") from error
    required = ("vllm:num_requests_running", "vllm:num_requests_waiting")
    missing = [metric for metric in required if metric not in values]
    if missing:
        raise MeasurementError(f"required engine gauges missing in {source}: {', '.join(missing)}")
    return values


def metric_state(folder):
    state = {}
    for path in sorted(folder.glob("*-metrics.txt")):
        if "vllm:num_requests_" not in path.read_text(errors="replace"):
            continue
        state[path.name] = metric_values(path.read_text(errors="replace"), path)
    if len(state) != 3:
        raise MeasurementError(f"expected metrics from three model workers, found {len(state)}")
    return state


def metrics_settled(first, second):
    if first.keys() != second.keys():
        return False
    for filename in first:
        if first[filename].keys() != second[filename].keys():
            return False
        for metric, value in first[filename].items():
            if metric in ("vllm:num_requests_running", "vllm:num_requests_waiting"):
                if value != 0 or second[filename][metric] != 0:
                    return False
            elif value != second[filename][metric]:
                return False
    return True


class Runner:
    def __init__(self, args, session, marker, attempt_dir, deadline, now_fn=time.time, sleep_fn=time.sleep):
        self.args = args
        self.session = session
        self.marker = marker
        self.attempt_dir = attempt_dir
        self.deadline = deadline
        self.now = now_fn
        self.sleep = sleep_fn
        self.python = sys.executable
        self.events = []
        self.baseline_workers = None

    def remaining(self, reserve=DEADLINE_RESERVE_SECONDS):
        remaining = self.deadline - self.now() - reserve
        if remaining <= 0:
            raise MeasurementError("first-timing/session deadline reached")
        return remaining

    def command(self, command, *, timeout=45, input_text=None, check=True, log=None):
        limit = max(0.1, min(timeout, self.remaining()))
        try:
            result = subprocess.run(command, input=input_text, text=True, capture_output=True,
                                    timeout=limit, check=False)
        except subprocess.TimeoutExpired as error:
            result = subprocess.CompletedProcess(command, 124, as_text(error.stdout),
                                                 as_text(error.stderr) or "timeout")
        result.stdout = as_text(result.stdout)
        result.stderr = as_text(result.stderr)
        if log:
            Path(str(log) + ".stdout").write_text(result.stdout or "")
            Path(str(log) + ".stderr").write_text(result.stderr or "")
        if check and result.returncode:
            raise MeasurementError(f"command failed ({result.returncode}): {' '.join(map(str, command))}")
        return result

    def kubectl(self, arguments, **kwargs):
        command = ["kubectl", "--context", self.args.context, "--request-timeout=20s"] + arguments
        return self.command(command, **kwargs)

    def inventory(self):
        result = self.kubectl(["-n", self.args.namespace, "get", "pods", "-o", "json"], timeout=25)
        try:
            return json.loads(result.stdout)["items"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise MeasurementError("invalid pod inventory") from error

    @staticmethod
    def worker_identity(pods):
        workers = {}
        for pod in pods:
            specs = {c["name"]: c for c in pod["spec"].get("containers", [])}
            relevant = {name for name, spec in specs.items()
                        if name == "modelserver" or "llm-d-router-endpoint-picker" in spec.get("image", "")}
            if not relevant:
                continue
            statuses = {s["name"]: s for s in pod.get("status", {}).get("containerStatuses", [])}
            if pod["metadata"].get("deletionTimestamp") or not relevant <= statuses.keys():
                raise MeasurementError("a model worker or EPP is stopping")
            selected = []
            for name in sorted(relevant):
                status = statuses[name]
                if (not status.get("ready") or "running" not in status.get("state", {}) or
                        not status.get("containerID")):
                    raise MeasurementError("a model worker or EPP is not running and ready")
                selected.append((name, status["containerID"], status.get("restartCount", 0)))
            workers[pod["metadata"]["name"]] = (pod["metadata"]["uid"], pod["spec"].get("nodeName"), selected)
        if len(workers) != 4:
            raise MeasurementError(f"expected three model workers and one EPP, found {len(workers)}")
        return workers

    def assert_workers(self, pods=None):
        current = self.worker_identity(pods if pods is not None else self.inventory())
        if self.baseline_workers is None:
            self.baseline_workers = current
        elif current != self.baseline_workers:
            raise MeasurementError("model worker/EPP identity, placement, or restart count changed")

    def collect(self, destination, benchmark_pod=None):
        command = [self.python, str(HERE / "collect.py"), "--context", self.args.context,
                   "--namespace", self.args.namespace, "--out", str(destination)]
        if benchmark_pod:
            command += ["--benchmark-pod", benchmark_pod]
        return self.command(command, timeout=180, check=False,
                            log=destination.parent / (destination.name + "-collect"))

    def poll_metrics(self, destination):
        destination.mkdir()
        pods = self.inventory()
        self.assert_workers(pods)
        (destination / "pods.json").write_text(json.dumps({"items": pods}, indent=2) + "\n")
        model_pods = []
        for pod in pods:
            engine = next((container for container in pod["spec"].get("containers", [])
                           if container["name"] == "modelserver"), None)
            if not engine:
                continue
            port = next((port.get("containerPort") for port in engine.get("ports", [])
                         if port.get("name") == "modelserver"), None)
            if not port:
                raise MeasurementError("model worker is missing its modelserver metrics port")
            model_pods.append((pod["metadata"]["name"], port))
        if len(model_pods) != 3:
            raise MeasurementError(f"expected three model workers, found {len(model_pods)}")
        for name, port in model_pods:
            path = f"/api/v1/namespaces/{self.args.namespace}/pods/http:{name}:{port}/proxy/metrics"
            result = self.kubectl(["get", "--raw", path], timeout=25, check=False)
            metrics_path = destination / (name + "-metrics.txt")
            metrics_path.write_text(result.stdout)
            if result.returncode:
                (destination / (name + "-metrics.txt.stderr")).write_text(result.stderr)
                raise MeasurementError("engine metrics poll failed: " + name)
        return metric_state(destination)

    def settled_snapshot(self, arm_dir, phase, benchmark_pod=None):
        settle_root = arm_dir / ("settling-" + phase)
        settle_root.mkdir()
        previous_path = settle_root / "sample-01"
        previous = self.poll_metrics(previous_path)
        index = 2
        settle_end = min(self.now() + self.args.settle_timeout, self.deadline - DEADLINE_RESERVE_SECONDS)
        while self.now() < settle_end:
            self.sleep(min(self.args.settle_interval, max(0, settle_end - self.now())))
            current_path = settle_root / f"sample-{index:02d}"
            current = self.poll_metrics(current_path)
            if metrics_settled(previous, current):
                destination = arm_dir / phase
                result = self.collect(destination, benchmark_pod)
                if result.returncode:
                    raise MeasurementError(f"incomplete {phase} collection")
                collected = metric_state(destination)
                if not metrics_settled(current, collected):
                    raise MeasurementError(f"late request or transfer-counter change during {phase} collection")
                return destination
            previous_path, previous = current_path, current
            index += 1
        raise MeasurementError(f"engine requests or transfer counters did not settle before {phase}")

    def render_workload(self, item, manifest):
        command = [self.python, str(HERE / "workload.py"), item["config"],
                   "--name", item["run_id"], "--cpu-node", self.args.cpu_node,
                   "--namespace", self.args.namespace, "--decoder-pod", item["decoder_pod"]]
        result = self.command(command, timeout=30)
        manifest.write_text(result.stdout)

    def wait_for_exit(self, pod_name, timeout=1200):
        end = min(self.now() + timeout, self.deadline - DEADLINE_RESERVE_SECONDS)
        while self.now() < end:
            pods = self.inventory()
            self.assert_workers(pods)
            pod = next((pod for pod in pods if pod["metadata"]["name"] == pod_name), None)
            if pod is not None:
                statuses = pod.get("status", {}).get("initContainerStatuses", [])
                if any(s.get("state", {}).get("terminated", {}).get("exitCode", 0) != 0 for s in statuses):
                    raise MeasurementError("benchmark init container failed")
                result = self.kubectl(["-n", self.args.namespace, "exec", pod_name, "-c", "benchmark",
                                       "--", "cat", "/reports/exit-code"], timeout=10, check=False)
                if result.returncode == 0:
                    value = result.stdout.strip()
                    if not re.fullmatch(r"[0-9]+", value):
                        raise MeasurementError("benchmark exit-code is malformed")
                    return int(value)
                if pod.get("status", {}).get("phase") in ("Failed", "Succeeded"):
                    raise MeasurementError("benchmark pod terminated before reports were retained")
            self.sleep(min(2, max(0, end - self.now())))
        raise MeasurementError("timed out waiting for /reports/exit-code")

    def validate(self, item, arm_dir, paired_validation):
        command = [self.python, str(HERE / "validate-transfer.py"), "--before", str(arm_dir / "before"),
                   "--after", str(arm_dir / "after"), "--run-id", item["run_id"],
                   "--decoder-pod", item["decoder_pod"], "--namespace", self.args.namespace,
                   "--input-tokens", str(item["input_tokens"]), "--count", str(item["expected_requests"])]
        if paired_validation:
            command += ["--paired-with", str(paired_validation)]
        self.command(command, timeout=90, log=arm_dir / "validate-transfer")
        expected_transfers = item["expected_requests"] * self.marker["transfers_per_request"]
        self.command([self.python, str(HERE / "transfer-delta.py"),
                      "--before", str(arm_dir / "before"), "--after", str(arm_dir / "after"),
                      "--decoder-pod", item["decoder_pod"],
                      "--expected-transfers", str(expected_transfers)], timeout=90,
                     log=arm_dir / "transfer-delta")

    def preserve_failure(self, item, arm_dir):
        destination = arm_dir / "failure-collection"
        if destination.exists():
            return
        result = self.collect(destination, item["run_id"])
        if not (destination / "collection.json").exists():
            fallback = arm_dir / "failure-observation"
            if not fallback.exists():
                self.collect(fallback)
        self.events.append({"event": "failure_collection", "label": item["label"],
                            "complete": result.returncode == 0, "unix": self.now()})

    def delete_benchmark(self, run_id):
        self.kubectl(["-n", self.args.namespace, "delete", "pod", run_id,
                      "--ignore-not-found=true", "--wait=false"], timeout=20, check=False)
        self.kubectl(["-n", self.args.namespace, "delete", "configmap", run_id,
                      "--ignore-not-found=true", "--wait=false"], timeout=20, check=False)

    def run_item(self, item, completed):
        arm_dir = self.attempt_dir / item["label"]
        arm_dir.mkdir()
        write_json(arm_dir / "arm.json", item)
        applied = False
        retained = False
        try:
            self.settled_snapshot(arm_dir, "before")
            manifest = arm_dir / "workload.yaml"
            self.render_workload(item, manifest)
            self.kubectl(["apply", "-f", "-"], input_text=manifest.read_text(), timeout=45)
            applied = True
            exit_code = self.wait_for_exit(item["run_id"])
            after = self.settled_snapshot(arm_dir, "after", item["run_id"])
            retained = (after / "reports" / "exit-code").exists()
            if exit_code != 0:
                raise MeasurementError(f"benchmark harness exited {exit_code}")
            paired = completed.get(item.get("paired_with"))
            self.validate(item, arm_dir, paired)
            validation = arm_dir / "after" / "validation.json"
            completed[item["label"]] = validation
            self.events.append({"event": "validated", "label": item["label"],
                                "run_id": item["run_id"], "unix": self.now()})
            write_json(self.attempt_dir / "measurement-state.json", {"status": "running", "events": self.events})
        except Exception:
            if applied and not retained:
                self.preserve_failure(item, arm_dir)
                retained = (arm_dir / "failure-collection" / "collection.json").exists()
            raise
        finally:
            if applied and retained:
                self.delete_benchmark(item["run_id"])

    def run(self, plan):
        self.assert_workers()
        completed = {}
        try:
            for item in plan["warmups"] + plan["arms"]:
                self.run_item(item, completed)
        except Exception as error:
            write_json(self.attempt_dir / "measurement-state.json", {
                "status": "failed", "error": str(error), "events": self.events,
                "failed_unix": self.now(),
            })
            raise
        return completed


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path, help="guarded session directory with session.json")
    parser.add_argument("--rendered-dir", required=True, type=Path)
    parser.add_argument("--cpu-node", required=True)
    parser.add_argument("--local-decoder", required=True)
    parser.add_argument("--remote-decoder", required=True)
    parser.add_argument("--context", default="router-topology")
    parser.add_argument("--namespace", default=NAMESPACE)
    parser.add_argument("--correctness-marker", type=Path,
                        help="defaults to RUN_DIR/correctness-verified.json; required for --execute")
    parser.add_argument("--settle-interval", type=float, default=15,
                        help="seconds between metric polls; default covers the observed reporter interval")
    parser.add_argument("--settle-timeout", type=float, default=90)
    parser.add_argument("--execute", action="store_true", help="issue bounded Kubernetes RPCs")
    args = parser.parse_args(argv)
    if args.local_decoder == args.remote_decoder:
        parser.error("local and remote decoder pods must differ")
    if args.settle_interval <= 0 or args.settle_timeout < args.settle_interval:
        parser.error("settle timing must be positive and allow at least one interval")
    return args


def main(argv=None):
    args = parse_args(argv)
    session = read_json(args.run_dir / "session.json")
    for key in ("session_id", "started_unix", "cleanup_start_deadline_unix"):
        if key not in session:
            raise MeasurementError("session.json lacks " + key)
    suffix = hashlib.sha256((session["session_id"] + uuid.uuid4().hex).encode()).hexdigest()[:8]
    plan = first_block_plan(args.rendered_dir, args.local_decoder, args.remote_decoder, suffix)
    validate_plan(plan)
    preview = {
        "mode": "execute" if args.execute else "dry-run",
        "session_id": session["session_id"],
        "context": args.context,
        "namespace": args.namespace,
        "plan": plan,
        "rpc_count": 0 if not args.execute else "bounded at execution",
    }
    if not args.execute:
        print(json.dumps(preview, indent=2))
        print("DRY RUN: no Kubernetes RPCs were made.")
        return
    kubeconfig = os.environ.get("KUBECONFIG")
    if not kubeconfig:
        raise MeasurementError("--execute requires inherited KUBECONFIG")
    marker_path = args.correctness_marker or args.run_dir / "correctness-verified.json"
    now = time.time()
    marker = validate_correctness_marker(marker_path, session, args, now)
    first_deadline = session["started_unix"] + FIRST_BLOCK_SECONDS
    if isinstance(session.get("first_measurement_deadline_unix"), (int, float)):
        first_deadline = min(first_deadline, session["first_measurement_deadline_unix"])
    deadline = min(first_deadline, session["cleanup_start_deadline_unix"])
    if now + DEADLINE_RESERVE_SECONDS >= deadline:
        raise MeasurementError("not enough time remains before the first-timing/session deadline")
    completion = args.run_dir / "first-timing-block-complete.json"
    if completion.exists():
        raise MeasurementError("first timing block is already complete; refusing an unplanned rerun")
    attempts = args.run_dir / "measurement-attempts"
    attempts.mkdir(exist_ok=True)
    attempt_dir = attempts / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now)) + "-" + suffix)
    attempt_dir.mkdir()
    write_json(attempt_dir / "measurement-plan.json", preview)
    runner = Runner(args, session, marker, attempt_dir, deadline)
    completed = runner.run(plan)
    measured = plan["arms"]
    if not all(item["label"] in completed for item in measured):
        raise MeasurementError("strict validation did not complete every measured arm")
    result = {
        "session_id": session["session_id"],
        "validated": True,
        "arms": [item["label"] for item in measured],
        "run_ids": [item["run_id"] for item in measured],
        "attempt_dir": str(attempt_dir),
        "finished_unix": time.time(),
    }
    write_json(completion, result)
    write_json(attempt_dir / "measurement-state.json", {"status": "complete", "events": runner.events})
    print("PASS: frozen first timing block strictly validated: " + str(completion))


if __name__ == "__main__":
    try:
        main()
    except MeasurementError as error:
        raise SystemExit("FAIL: " + str(error))
