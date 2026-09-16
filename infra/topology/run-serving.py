#!/usr/bin/env python3
"""Boot, qualify, and measure one guarded topology session; dry-run by default."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import time

from prometheus_client.parser import text_string_to_metric_families


HERE = Path(__file__).resolve().parent
TERRAFORM_DIR = HERE / "terraform"
NAMESPACE = "topology-measurement"
EXPECTED_EPP_IMAGE = "ghcr.io/llm-d/llm-d-router-endpoint-picker:topology-0217d299-amd64"
TRANSFER_METRICS = {
    "bytes": "vllm:nixl_bytes_transferred_sum",
    "operations": "vllm:nixl_bytes_transferred_count",
    "seconds": "vllm:nixl_xfer_time_seconds_sum",
    "timed_operations": "vllm:nixl_xfer_time_seconds_count",
}
FAILURE_METRICS = (
    "vllm:nixl_num_failed_transfers_total",
    "vllm:nixl_num_kv_expired_reqs_total",
    "vllm:nixl_num_failed_notifications_total",
)


class ServingError(RuntimeError):
    pass


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ServingError(f"cannot read valid JSON from {path}: {error}") from error


def write_json(path: Path, value) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def as_text(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def verify_archive(path: Path, expected_sha256: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ServingError("EPP SHA-256 must be 64 lowercase hexadecimal characters")
    try:
        with path.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != expected_sha256:
                raise ServingError("EPP archive checksum mismatch")
        with tarfile.open(path) as archive:
            manifest = json.load(archive.extractfile("manifest.json"))
            if len(manifest) != 1 or EXPECTED_EPP_IMAGE not in manifest[0].get("RepoTags", []):
                raise ServingError("EPP archive does not contain the pinned image")
            config = json.load(archive.extractfile(manifest[0]["Config"]))
    except (OSError, KeyError, TypeError, AttributeError, tarfile.TarError, json.JSONDecodeError) as error:
        raise ServingError("EPP archive is malformed") from error
    if (config.get("os"), config.get("architecture")) != ("linux", "amd64"):
        raise ServingError("EPP archive is not linux/amd64")


def terraform_values(output):
    try:
        cluster_id = output["cluster_id"]["value"]
        groups = output["node_group_ids"]["value"]
        values = {name: groups[name] for name in ("local", "remote", "cpu")}
    except (KeyError, TypeError) as error:
        raise ServingError("Terraform outputs lack the cluster or node-group IDs") from error
    if not isinstance(cluster_id, str) or not cluster_id or any(
        not isinstance(value, str) or not value for value in values.values()
    ):
        raise ServingError("Terraform cluster and node-group IDs must be non-empty strings")
    if len(set(values.values())) != 3:
        raise ServingError("Terraform node-group IDs must be distinct")
    return cluster_id, values


def ready_node_mapping(items, group_ids):
    expected_gpus = {"local": 8, "remote": 1, "cpu": 0}
    result = {}
    for role, group_id in group_ids.items():
        matches = [
            node for node in items
            if node.get("metadata", {}).get("labels", {}).get("nebius.com/node-group-id") == group_id
        ]
        if len(matches) != 1:
            raise ServingError(f"expected exactly one Kubernetes node for {role}, found {len(matches)}")
        node = matches[0]
        conditions = {item.get("type"): item.get("status") for item in node.get("status", {}).get("conditions", [])}
        if conditions.get("Ready") != "True" or node.get("spec", {}).get("unschedulable") is True:
            raise ServingError(f"{role} node is not Ready and schedulable")
        try:
            gpu_count = int(node.get("status", {}).get("allocatable", {}).get("nvidia.com/gpu", "0"))
        except (TypeError, ValueError) as error:
            raise ServingError(f"{role} node has invalid GPU allocatable capacity") from error
        if gpu_count != expected_gpus[role]:
            raise ServingError(f"{role} node advertises {gpu_count} GPUs, expected {expected_gpus[role]}")
        hostname = node.get("metadata", {}).get("labels", {}).get("kubernetes.io/hostname")
        if not isinstance(hostname, str) or not hostname:
            raise ServingError(f"{role} node lacks kubernetes.io/hostname")
        result[role] = hostname
    if len(set(result.values())) != 3:
        raise ServingError("local, remote, and CPU roles must map to distinct nodes")
    return result


def prometheus_values(path: Path):
    values = {}
    try:
        for family in text_string_to_metric_families(path.read_text(errors="replace")):
            for sample in family.samples:
                name = sample.name
                tracked = name in ("vllm:num_requests_running", "vllm:num_requests_waiting")
                tracked = tracked or (
                    name.startswith("vllm:nixl_")
                    and (name.endswith("_count") or name.endswith("_sum") or name.endswith("_total"))
                )
                if not tracked:
                    continue
                value = float(sample.value)
                if not math.isfinite(value):
                    raise ServingError(f"non-finite metric in {path}: {name}")
                values[name] = values.get(name, 0.0) + value
    except ValueError as error:
        raise ServingError(f"invalid Prometheus metrics in {path}: {error}") from error
    required = ("vllm:num_requests_running", "vllm:num_requests_waiting")
    missing = [name for name in required if name not in values]
    if missing:
        raise ServingError(f"required engine gauges missing in {path}: {', '.join(missing)}")
    return values


def metric_state(folder: Path):
    state = {}
    for path in sorted(folder.glob("*-metrics.txt")):
        text = path.read_text(errors="replace")
        if "vllm:num_requests_" in text:
            state[path.name] = prometheus_values(path)
    if len(state) != 3:
        raise ServingError(f"expected metric snapshots for three model workers, found {len(state)}")
    return state


def metrics_settled(first, second):
    if first.keys() != second.keys():
        return False
    for filename in first:
        if first[filename].keys() != second[filename].keys():
            return False
        for name, value in first[filename].items():
            current = second[filename][name]
            if name in ("vllm:num_requests_running", "vllm:num_requests_waiting"):
                if value != 0 or current != 0:
                    return False
            elif value != current:
                return False
    return True


def worker_identity(folder: Path):
    pods = read_json(folder / "pods.json").get("items", [])
    result = {}
    for pod in pods:
        specs = pod.get("spec", {}).get("containers", []) + pod.get("spec", {}).get("initContainers", [])
        if not any(
            item.get("name") == "modelserver"
            or "llm-d-router-endpoint-picker" in item.get("image", "")
            for item in specs
        ):
            continue
        statuses = pod.get("status", {}).get("containerStatuses", []) + pod.get("status", {}).get("initContainerStatuses", [])
        if not statuses or any(
            not item.get("containerID")
            or not item.get("ready")
            or "running" not in item.get("state", {})
            for item in statuses
        ):
            raise ServingError("a model worker or EPP container is not running and ready")
        result[pod["metadata"]["name"]] = {
            "uid": pod["metadata"]["uid"],
            "node": pod["spec"].get("nodeName"),
            "containers": sorted(
                (item["name"], item["containerID"], item.get("imageID"), item.get("restartCount", 0))
                for item in statuses
            ),
        }
    if len(result) != 4:
        raise ServingError(f"expected three model workers and one EPP, found {len(result)}")
    return result


def gpu_identity(folder: Path):
    files = sorted(folder.glob("*-gpu.json"))
    if len(files) != 3:
        raise ServingError(f"expected three GPU identity records, found {len(files)}")
    result = {path.name: read_json(path).get("gpu_uuids") for path in files}
    if any(not isinstance(values, list) or len(values) != 1 for values in result.values()):
        raise ServingError("each model worker must see exactly one GPU")
    if len({values[0] for values in result.values()}) != 3:
        raise ServingError("model workers must use three distinct GPUs")
    return result


def transfer_delta(before: Path, after: Path, decoder_pod: str):
    old = prometheus_values(before / f"{decoder_pod}-metrics.txt")
    new = prometheus_values(after / f"{decoder_pod}-metrics.txt")
    result = {}
    for label, metric in TRANSFER_METRICS.items():
        if metric not in old or metric not in new:
            raise ServingError(f"missing required transfer metric: {metric}")
        result[label] = new[metric] - old[metric]
    return result


def validate_correctness(results, snapshots, local_decoder, remote_decoder, namespace):
    if set(results) != {"direct", "local", "remote"}:
        raise ServingError("correctness results must contain direct, local, and remote")
    texts = []
    request_bodies = []
    prompt_tokens = []
    for route, record in results.items():
        body = record.get("body")
        choices = body.get("choices") if isinstance(body, dict) else None
        text = choices[0].get("text") if isinstance(choices, list) and choices else None
        finish = choices[0].get("finish_reason") if isinstance(choices, list) and choices else None
        usage = body.get("usage") if isinstance(body, dict) else None
        if (
            record.get("status") != 200
            or record.get("error") is not None
            or not isinstance(text, str)
            or not text
            or finish != "length"
            or not isinstance(usage, dict)
            or usage.get("completion_tokens") != 16
        ):
            raise ServingError(f"{route} correctness output is incomplete or invalid")
        texts.append(text)
        request_bodies.append(record.get("request_body"))
        prompt_tokens.append(usage.get("prompt_tokens"))
    if len(set(texts)) != 1:
        raise ServingError("direct, local P/D, and remote P/D output text differs")
    if any(not isinstance(value, dict) for value in request_bodies) or any(
        value != request_bodies[0] for value in request_bodies[1:]
    ):
        raise ServingError("direct, local P/D, and remote P/D request bodies differ")
    if any(not isinstance(value, int) or value <= 0 for value in prompt_tokens) or len(set(prompt_tokens)) != 1:
        raise ServingError("server prompt token counts differ across correctness routes")
    expected_pins = {
        "local": base64.b64encode(f"{namespace}/{local_decoder}-rank-0".encode()).decode(),
        "remote": base64.b64encode(f"{namespace}/{remote_decoder}-rank-0".encode()).decode(),
    }
    for route, expected in expected_pins.items():
        if (
            results[route].get("requested_decoder") != expected
            or results[route].get("selected_decoder") != expected
        ):
            raise ServingError(f"{route} decoder pin was not selected exactly")

    ordered = [snapshots[name] for name in ("before", "after-direct", "after-local", "after-remote")]
    identities = [worker_identity(path) for path in ordered]
    if any(value != identities[0] for value in identities[1:]):
        raise ServingError("model worker/EPP identity, placement, or restart count changed")
    gpu_sets = [gpu_identity(path) for path in ordered]
    if any(value != gpu_sets[0] for value in gpu_sets[1:]):
        raise ServingError("model worker GPU identity changed")

    direct_local = transfer_delta(snapshots["before"], snapshots["after-direct"], local_decoder)
    direct_remote = transfer_delta(snapshots["before"], snapshots["after-direct"], remote_decoder)
    if any(value != 0 for value in direct_local.values()) or any(
        value != 0 for value in direct_remote.values()
    ):
        raise ServingError("direct decode unexpectedly changed a decoder transfer metric")
    local = transfer_delta(snapshots["after-direct"], snapshots["after-local"], local_decoder)
    local_other = transfer_delta(snapshots["after-direct"], snapshots["after-local"], remote_decoder)
    remote = transfer_delta(snapshots["after-local"], snapshots["after-remote"], remote_decoder)
    remote_other = transfer_delta(snapshots["after-local"], snapshots["after-remote"], local_decoder)
    for route, value in (("local", local), ("remote", remote)):
        if (
            value["operations"] != 1
            or value["timed_operations"] != 1
            or value["bytes"] <= 0
            or value["seconds"] <= 0
        ):
            raise ServingError(f"{route} P/D request did not record exactly one qualified transfer")
    if any(value != 0 for value in local_other.values()) or any(
        value != 0 for value in remote_other.values()
    ):
        raise ServingError("P/D transfer changed an unselected decoder metric")
    for path in snapshots["before"].glob("*-metrics.txt"):
        final = snapshots["after-remote"] / path.name
        old, new = prometheus_values(path), prometheus_values(final)
        for metric in FAILURE_METRICS:
            if metric in old or metric in new:
                if metric not in old or metric not in new or new[metric] - old[metric] != 0:
                    raise ServingError(f"transfer failure/expiry counter changed: {path.name}:{metric}")
    return identities[0]


class Runner:
    def __init__(self, args, session, root: Path, now_fn=time.time, sleep_fn=time.sleep):
        self.args = args
        self.session = session
        self.root = root
        self.now = now_fn
        self.sleep = sleep_fn
        self.deadline = session["first_measurement_deadline_unix"]
        self.kubeconfig = root / "kubeconfig"
        self.environment = {**os.environ, "KUBECONFIG": str(self.kubeconfig)}
        self.context_ready = False
        self.baseline_workers = None

    def remaining(self, reserve=30):
        value = self.deadline - self.now() - reserve
        if value <= 0:
            raise ServingError("first-measurement deadline reached")
        return value

    def command(self, command, *, timeout=60, input_text=None, check=True, log=None):
        try:
            result = subprocess.run(
                command,
                input=input_text,
                text=True,
                capture_output=True,
                timeout=max(0.1, min(timeout, self.remaining())),
                check=False,
                env=self.environment,
            )
        except subprocess.TimeoutExpired as error:
            result = subprocess.CompletedProcess(
                command, 124, as_text(error.stdout), as_text(error.stderr) or "timeout"
            )
        result.stdout = as_text(result.stdout)
        result.stderr = as_text(result.stderr)
        if log:
            Path(str(log) + ".stdout").write_text(result.stdout or "")
            Path(str(log) + ".stderr").write_text(result.stderr or "")
        if check and result.returncode:
            raise ServingError(f"command failed ({result.returncode}): {' '.join(map(str, command))}")
        return result

    def kubectl(self, arguments, **kwargs):
        return self.command(
            ["kubectl", "--context", self.args.context, "--request-timeout=20s"] + arguments,
            **kwargs,
        )

    def inventory(self):
        result = self.kubectl(
            ["-n", self.args.namespace, "get", "pods", "-o", "json"], timeout=25
        )
        try:
            return json.loads(result.stdout)["items"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise ServingError("invalid pod inventory") from error

    def wait_apply(self):
        path = self.args.run_dir / "apply-result.json"
        while not path.exists():
            self.remaining()
            self.sleep(min(2, self.remaining()))
        result = read_json(path)
        if result.get("exit_code") != 0:
            raise ServingError(f"Terraform apply did not succeed: {result.get('exit_code')}")

    def terraform_outputs(self):
        result = self.command(
            ["terraform", f"-chdir={TERRAFORM_DIR}", "output", "-json"],
            timeout=30,
            log=self.root / "terraform-output",
        )
        output = json.loads(result.stdout)
        write_json(self.root / "terraform-output.json", output)
        return terraform_values(output)

    def fetch_kubeconfig(self, cluster_id):
        self.command([
            "nebius", "mk8s", "cluster", "get-credentials", "--id", cluster_id,
            "--external", "--kubeconfig", str(self.kubeconfig),
            "--context-name", self.args.context, "--force",
        ], timeout=90, log=self.root / "get-credentials")
        self.kubeconfig.chmod(0o600)
        self.context_ready = True

    def wait_nodes(self, group_ids):
        last_error = None
        while True:
            result = self.kubectl(["get", "nodes", "-o", "json"], timeout=25, check=False)
            if result.returncode == 0:
                try:
                    items = json.loads(result.stdout)["items"]
                    nodes = ready_node_mapping(items, group_ids)
                    write_json(self.root / "ready-nodes.json", {"nodes": nodes, "items": items})
                    return nodes
                except (json.JSONDecodeError, KeyError, ServingError) as error:
                    last_error = str(error)
            else:
                last_error = result.stderr.strip()
            if self.remaining(60) <= 0:
                raise ServingError("nodes did not become ready: " + str(last_error))
            self.sleep(min(10, self.remaining(60)))

    def render(self, nodes):
        rendered = self.args.run_dir / "rendered"
        result = self.command([
            sys.executable, str(HERE / "render.py"),
            "--local-node", nodes["local"], "--remote-node", nodes["remote"],
            "--cpu-node", nodes["cpu"], "--namespace", self.args.namespace,
            "--out", str(rendered),
        ], timeout=30, log=self.root / "render")
        if result.returncode:
            raise ServingError("render.py failed")
        return rendered

    def apply_models_and_router(self, rendered, cpu_node):
        self.kubectl(["apply", "-f", str(rendered / "modelservers.yaml")], timeout=45)
        self.command([
            sys.executable, str(HERE / "import-image.py"), "--context", self.args.context,
            "--node", cpu_node, "--namespace", self.args.namespace,
            "--archive", str(self.args.epp_archive), "--sha256", self.args.epp_sha256,
            "--execute",
        ], timeout=420, log=self.root / "import-image")
        helm = self.command([
            "helm", "template", "topology", str(self.args.chart_path),
            "--namespace", self.args.namespace,
            "-f", str(rendered / "router-diagnostic.values.yaml"),
        ], timeout=60, log=self.root / "helm-template")
        routes = self.command(
            [sys.executable, str(HERE / "record-routes.py")],
            input_text=helm.stdout,
            timeout=30,
            log=self.root / "record-routes",
        )
        router_manifest = rendered / "router.yaml"
        router_manifest.write_text(routes.stdout)
        self.kubectl(["apply", "-f", str(router_manifest)], timeout=45)

    @staticmethod
    def ready_status(pod, name):
        statuses = pod.get("status", {}).get("containerStatuses", []) + pod.get("status", {}).get("initContainerStatuses", [])
        status = next((item for item in statuses if item.get("name") == name), None)
        return bool(
            status
            and status.get("ready")
            and status.get("restartCount", 0) == 0
            and "running" in status.get("state", {})
            and status.get("containerID")
        )

    def serving_inventory(self, pods, nodes):
        model_matches = {"prefill": [], "decode-local": [], "decode-remote": []}
        routing_pods = {}
        for pod in pods:
            app = pod.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/name")
            specs = pod.get("spec", {}).get("containers", []) + pod.get("spec", {}).get("initContainers", [])
            if app in ("prefill", "decode-local", "decode-remote"):
                if not self.ready_status(pod, "modelserver"):
                    raise ServingError(f"{app} modelserver is not ready")
                if app.startswith("decode") and not self.ready_status(pod, "routing-proxy"):
                    raise ServingError(f"{app} routing proxy is not ready")
                model_matches[app].append(pod)
            if any(
                "llm-d-router-endpoint-picker" in item.get("image", "")
                or "envoyproxy" in item.get("image", "")
                for item in specs
            ):
                identity = pod.get("metadata", {}).get("uid") or pod.get("metadata", {}).get("name")
                routing_pods[identity] = pod
        if any(len(values) != 1 for values in model_matches.values()):
            raise ServingError("expected one ready prefill and two ready decode Pods")
        models = {name: values[0] for name, values in model_matches.items()}
        if len(routing_pods) != 1:
            raise ServingError(f"expected one combined EPP/Envoy Pod, found {len(routing_pods)}")
        routing_pod = next(iter(routing_pods.values()))
        relevant = {
            "epp": [
                item["name"] for item in routing_pod.get("spec", {}).get("containers", [])
                if "llm-d-router-endpoint-picker" in item.get("image", "")
            ],
            "envoy": [
                item["name"] for item in routing_pod.get("spec", {}).get("containers", [])
                if "envoyproxy" in item.get("image", "")
            ],
        }
        if any(len(names) != 1 for names in relevant.values()) or not all(
            self.ready_status(routing_pod, name)
            for names in relevant.values()
            for name in names
        ):
            raise ServingError("combined EPP/Envoy Pod containers are not ready")
        expected_nodes = {
            "prefill": nodes["local"],
            "decode-local": nodes["local"],
            "decode-remote": nodes["remote"],
        }
        for name, expected in expected_nodes.items():
            if models[name].get("spec", {}).get("nodeName") != expected:
                raise ServingError(f"{name} is placed on the wrong node")
        if routing_pod.get("spec", {}).get("nodeName") != nodes["cpu"]:
            raise ServingError("EPP and Envoy must run on the CPU node")
        return {
            "local_decoder": models["decode-local"]["metadata"]["name"],
            "remote_decoder": models["decode-remote"]["metadata"]["name"],
            "local_decoder_ip": models["decode-local"].get("status", {}).get("podIP"),
        }

    def wait_serving(self, nodes):
        last_error = None
        while True:
            result = self.kubectl(["-n", self.args.namespace, "get", "pods", "-o", "json"], timeout=25, check=False)
            if result.returncode == 0:
                try:
                    pods = json.loads(result.stdout)["items"]
                    inventory = self.serving_inventory(pods, nodes)
                    if not inventory["local_decoder_ip"]:
                        raise ServingError("local decoder Pod lacks a Pod IP")
                    write_json(self.root / "serving-ready.json", {"inventory": inventory, "pods": pods})
                    return inventory
                except (json.JSONDecodeError, KeyError, ServingError) as error:
                    last_error = str(error)
            else:
                last_error = result.stderr.strip()
            if self.remaining(60) <= 0:
                raise ServingError("serving Pods did not become ready: " + str(last_error))
            self.sleep(min(10, self.remaining(60)))

    def deploy_correctness_client(self, cpu_node):
        rendered = self.command([
            sys.executable, str(HERE / "render-correctness-pod.py"),
            "--cpu-node", cpu_node, "--namespace", self.args.namespace,
        ], timeout=30)
        manifest = self.root / "correctness-client.yaml"
        manifest.write_text(rendered.stdout)
        self.kubectl(["apply", "-f", str(manifest)], timeout=45)
        self.kubectl([
            "-n", self.args.namespace, "wait", "--for=condition=Ready",
            "pod/topology-correctness", "--timeout=180s",
        ], timeout=200)

    def collect(self, destination):
        result = self.command([
            sys.executable, str(HERE / "collect.py"), "--context", self.args.context,
            "--namespace", self.args.namespace, "--out", str(destination),
        ], timeout=180, check=False, log=Path(str(destination) + "-collect"))
        if result.returncode:
            raise ServingError("incomplete correctness collection: " + str(destination))
        return metric_state(destination)

    def poll_metrics(self, destination):
        destination.mkdir()
        pods = self.inventory()
        (destination / "pods.json").write_text(json.dumps({"items": pods}, indent=2) + "\n")
        current_workers = worker_identity(destination)
        if self.baseline_workers is None:
            self.baseline_workers = current_workers
        elif current_workers != self.baseline_workers:
            raise ServingError("model worker/EPP identity, placement, or restart count changed")
        model_pods = []
        for pod in pods:
            engine = next(
                (
                    container
                    for container in pod.get("spec", {}).get("containers", [])
                    if container.get("name") == "modelserver"
                ),
                None,
            )
            if not engine:
                continue
            port = next(
                (
                    item.get("containerPort")
                    for item in engine.get("ports", [])
                    if item.get("name") == "modelserver"
                ),
                None,
            )
            if not port:
                raise ServingError("model worker is missing its modelserver metrics port")
            model_pods.append((pod["metadata"]["name"], port))
        if len(model_pods) != 3:
            raise ServingError(f"expected three model workers, found {len(model_pods)}")
        for name, port in model_pods:
            path = f"/api/v1/namespaces/{self.args.namespace}/pods/http:{name}:{port}/proxy/metrics"
            result = self.kubectl(["get", "--raw", path], timeout=25, check=False)
            metrics_path = destination / f"{name}-metrics.txt"
            metrics_path.write_text(result.stdout)
            if result.returncode:
                (destination / f"{name}-metrics.txt.stderr").write_text(result.stderr)
                raise ServingError("engine metrics poll failed: " + name)
        return metric_state(destination)

    def settled_collection(self, label):
        root = self.root / "correctness" / ("settling-" + label)
        root.mkdir(parents=True)
        prior_path = root / "sample-01"
        prior = self.poll_metrics(prior_path)
        end = min(self.now() + self.args.settle_timeout, self.deadline - 30)
        index = 2
        while self.now() < end:
            self.sleep(min(self.args.settle_interval, end - self.now()))
            current_path = root / f"sample-{index:02d}"
            current = self.poll_metrics(current_path)
            if metrics_settled(prior, current):
                full_path = root / f"full-{index:02d}"
                full = self.collect(full_path)
                self.sleep(min(self.args.settle_interval, max(0, end - self.now())))
                late_path = root / f"late-{index:02d}"
                late = self.poll_metrics(late_path)
                if metrics_settled(full, late):
                    return full_path
                prior = late
                index += 1
                continue
            prior = current
            index += 1
        raise ServingError(f"metrics did not settle after {label}")

    def correctness_request(self, route, base_url, decoder_pod=None):
        output = f"/results/{route}.json"
        command = [
            "-n", self.args.namespace, "exec", "topology-correctness", "-c", "client", "--",
            "python3", "/opt/probe/correctness-client.py", "--route", route,
            "--base-url", base_url, "--run-id", f"correctness-{route}-{self.session['session_id'][-8:]}",
            "--namespace", self.args.namespace, "--out", output,
        ]
        if decoder_pod:
            command += ["--decoder-pod", decoder_pod]
        self.kubectl(command, timeout=90)
        result = self.kubectl([
            "-n", self.args.namespace, "exec", "topology-correctness", "-c", "client", "--",
            "cat", output,
        ], timeout=20)
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise ServingError(f"invalid {route} correctness result") from error
        write_json(self.root / "correctness" / f"{route}.json", value)
        return value

    def run_correctness(self, nodes, inventory):
        correctness = self.root / "correctness"
        correctness.mkdir()
        snapshots = {"before": self.settled_collection("before")}
        results = {}
        results["direct"] = self.correctness_request(
            "direct", f"http://{inventory['local_decoder_ip']}:8200"
        )
        snapshots["after-direct"] = self.settled_collection("after-direct")
        epp_url = f"http://topology-epp.{self.args.namespace}.svc.cluster.local"
        results["local"] = self.correctness_request(
            "local", epp_url, inventory["local_decoder"]
        )
        snapshots["after-local"] = self.settled_collection("after-local")
        results["remote"] = self.correctness_request(
            "remote", epp_url, inventory["remote_decoder"]
        )
        snapshots["after-remote"] = self.settled_collection("after-remote")
        identities = validate_correctness(
            results,
            snapshots,
            inventory["local_decoder"],
            inventory["remote_decoder"],
            self.args.namespace,
        )
        marker = {
            "session_id": self.session["session_id"],
            "verified": True,
            "validated": True,
            "verified_unix": self.now(),
            "namespace": self.args.namespace,
            "cpu_node": nodes["cpu"],
            "local_decoder_pod": inventory["local_decoder"],
            "remote_decoder_pod": inventory["remote_decoder"],
            "transfers_per_request": 1,
            "qualified_transfers_per_request": 1,
            "worker_identities": identities,
            "checks": {
                "direct_decode": True,
                "local_pd": True,
                "remote_pd": True,
                "route_pins": True,
                "transfer_counts": True,
                "workers_stable": True,
            },
        }
        marker_path = self.args.run_dir / "correctness-verified.json"
        write_json(marker_path, marker)
        return marker_path

    def run_measurements(self, rendered, nodes, inventory, marker):
        self.command([
            sys.executable, str(HERE / "run-measurements.py"),
            "--run-dir", str(self.args.run_dir), "--rendered-dir", str(rendered),
            "--cpu-node", nodes["cpu"], "--local-decoder", inventory["local_decoder"],
            "--remote-decoder", inventory["remote_decoder"],
            "--context", self.args.context, "--namespace", self.args.namespace,
            "--correctness-marker", str(marker), "--execute",
        ], timeout=max(1, self.remaining(15)), log=self.root / "run-measurements")

    def failure_snapshot(self):
        if not self.context_ready:
            return
        destination = self.root / "failure-snapshot"
        if destination.exists():
            return
        self.command([
            sys.executable, str(HERE / "collect.py"), "--context", self.args.context,
            "--namespace", self.args.namespace, "--out", str(destination),
        ], timeout=120, check=False, log=self.root / "failure-collect")

    def run(self):
        self.wait_apply()
        cluster_id, group_ids = self.terraform_outputs()
        self.fetch_kubeconfig(cluster_id)
        nodes = self.wait_nodes(group_ids)
        rendered = self.render(nodes)
        self.apply_models_and_router(rendered, nodes["cpu"])
        inventory = self.wait_serving(nodes)
        self.deploy_correctness_client(nodes["cpu"])
        marker = self.run_correctness(nodes, inventory)
        self.run_measurements(rendered, nodes, inventory, marker)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--chart-path", required=True, type=Path)
    parser.add_argument("--epp-archive", required=True, type=Path)
    parser.add_argument("--epp-sha256", required=True)
    parser.add_argument("--context", default="router-topology")
    parser.add_argument("--namespace", default=NAMESPACE)
    parser.add_argument("--settle-interval", type=float, default=15)
    parser.add_argument("--settle-timeout", type=float, default=90)
    parser.add_argument("--execute", action="store_true", help="issue bounded cloud and Kubernetes RPCs")
    args = parser.parse_args(argv)
    if args.settle_interval < 15 or args.settle_timeout < 2 * args.settle_interval:
        parser.error("settle timing must use at least 15-second samples and allow a late check")
    return args


def main(argv=None):
    args = parse_args(argv)
    session = read_json(args.run_dir / "session.json")
    for key in ("session_id", "profile", "first_measurement_deadline_unix"):
        if key not in session:
            raise ServingError("session.json lacks " + key)
    if session["profile"] != "h200-evaluation":
        raise ServingError("run-serving requires an h200-evaluation session")
    if not args.chart_path.is_dir() or not (args.chart_path / "Chart.yaml").is_file():
        raise ServingError("--chart-path must be a staged Helm chart directory")
    verify_archive(args.epp_archive, args.epp_sha256)
    preview = {
        "mode": "execute" if args.execute else "dry-run",
        "session_id": session["session_id"],
        "deadline_unix": session["first_measurement_deadline_unix"],
        "chart_path": str(args.chart_path.resolve()),
        "epp_archive": str(args.epp_archive.resolve()),
        "namespace": args.namespace,
        "sequence": [
            "wait for successful guarded Terraform apply",
            "fetch a dedicated kubeconfig and verify exact Ready node-group shapes",
            "render and apply model servers, import pinned EPP, render/apply diagnostic router",
            "wait for three engines, EPP, sidecars, and Envoy",
            "validate direct/local/remote output, pins, transfer deltas, failures, and identities",
            "write correctness-verified.json and run the frozen 48-request timing block",
        ],
    }
    if not args.execute:
        print(json.dumps(preview, indent=2))
        print("DRY RUN: no subprocesses or RPCs were started.")
        return
    serving_root = args.run_dir / "serving"
    if serving_root.exists():
        raise ServingError("serving directory already exists; refusing an unplanned rerun")
    if (args.run_dir / "correctness-verified.json").exists():
        raise ServingError("correctness is already verified; refusing an unplanned rerun")
    serving_root.mkdir()
    write_json(serving_root / "plan.json", preview)
    runner = Runner(args, session, serving_root)
    try:
        runner.run()
        write_json(serving_root / "result.json", {
            "session_id": session["session_id"],
            "status": "first timing block complete",
            "finished_unix": time.time(),
        })
    except Exception as error:
        write_json(serving_root / "failure.json", {
            "session_id": session["session_id"],
            "error": str(error),
            "failed_unix": time.time(),
        })
        try:
            runner.failure_snapshot()
        except Exception as snapshot_error:
            write_json(serving_root / "failure-snapshot-error.json", {"error": str(snapshot_error)})
        raise


if __name__ == "__main__":
    try:
        main()
    except ServingError as error:
        raise SystemExit("FAIL: " + str(error))
