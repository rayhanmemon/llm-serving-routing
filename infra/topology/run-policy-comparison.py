#!/usr/bin/env python3
"""Run one already-active unpinned policy batch; dry-run and make no RPCs by default."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import yaml


HERE = Path(__file__).resolve().parent


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


measurement = load_module("topology_measurements", HERE / "run-measurements.py")
evidence = load_module("policy_evidence", HERE / "policy-evidence.py")
PolicyError = measurement.MeasurementError
POLICIES = {"none", "hard", "soft", "absolute-cap", "allowance"}


def contained(root, value):
    root = root.resolve()
    path = (root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    if path != root and root not in path.parents:
        raise PolicyError(f"path escapes its allowed root: {value}")
    return path


def validate_plan(plan_path, rendered_dir, run_dir):
    try:
        plan = json.loads(plan_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise PolicyError(f"cannot read policy plan: {error}") from error
    if plan.get("schema_version") != 1 or plan.get("policy") not in POLICIES:
        raise PolicyError("plan requires schema_version 1 and one evaluated policy")
    runs = plan.get("runs")
    if not isinstance(runs, list) or not runs:
        raise PolicyError("plan must contain an ordered nonempty runs list")
    if len(runs) > 12:
        raise PolicyError("one policy batch is bounded to at most 12 runs")
    labels = set()
    modes = set()
    normalized = []
    expected_manifest = f"router-{plan['policy']}.values.yaml"
    for item in runs:
        required = ("block_id", "workload_config", "policy_manifest", "mode", "seed",
                    "expected_count", "arrival_trace_sha256")
        if not isinstance(item, dict) or any(key not in item for key in required):
            raise PolicyError("each run needs block_id, workload_config, policy_manifest, mode, and seed")
        if not all(isinstance(item[key], str) for key in ("block_id", "workload_config",
                                                          "policy_manifest", "mode")):
            raise PolicyError("block and path fields must be strings")
        label = measurement.safe_name(str(item["block_id"]))
        if not label or label in labels or label != item["block_id"]:
            raise PolicyError("block IDs must be unique Kubernetes-safe names")
        labels.add(label)
        if item["mode"] not in ("calibration", "heldout"):
            raise PolicyError("run mode must be calibration or heldout")
        modes.add(item["mode"])
        config = contained(rendered_dir, item["workload_config"])
        manifest = contained(rendered_dir, item["policy_manifest"])
        if manifest.name != expected_manifest or not config.is_file() or not manifest.is_file():
            raise PolicyError("run paths do not match the declared rendered policy/workload")
        workload = evidence.load_workload(config)
        if workload["kind"] != item["mode"] or workload["seed"] != item["seed"]:
            raise PolicyError("plan mode/seed differs from the rendered workload")
        if item["expected_count"] != workload["count"]:
            raise PolicyError("plan expected_count differs from the rendered workload")
        if item["arrival_trace_sha256"] != workload["trace_sha256"]:
            raise PolicyError("plan arrival_trace_sha256 differs from the rendered workload")
        values = yaml.safe_load(manifest.read_text())
        plugins = values.get("router", {}).get("epp", {}).get("pluginsConfig")
        if not isinstance(plugins, dict) or "session-affinity-filter" in yaml.safe_dump(plugins):
            raise PolicyError("evaluated policy manifest is invalid or supports diagnostic pins")
        pair = item.get("paired_with")
        pair_path = None
        if pair:
            pair_path = contained(run_dir, pair)
            if not pair_path.is_file():
                raise PolicyError("paired evidence file does not exist: " + str(pair_path))
        normalized.append({**item, "label": label, "config": str(config),
                           "manifest": str(manifest), "paired_path": str(pair_path) if pair_path else None,
                           "expected_requests": workload["count"],
                           "intended_arrival_trace_sha256": workload["trace_sha256"]})
    if len(modes) != 1:
        raise PolicyError("calibration and held-out runs need separate plans")
    mode = next(iter(modes))
    if mode == "heldout":
        if plan.get("frozen") is not True:
            raise PolicyError("held-out plan must be explicitly frozen after calibration")
        if plan.get("reference_policy") is not True and any(not item["paired_path"] for item in normalized):
            raise PolicyError("non-reference held-out runs require paired evidence for every block")
    return plan, normalized, mode


def validate_markers(run_dir, session, args, plan_sha, mode, now):
    correctness = measurement.read_json(run_dir / "correctness-verified.json")
    if (correctness.get("session_id") != session["session_id"] or
            correctness.get("verified") is not True or correctness.get("validated") is not True):
        raise PolicyError("correctness marker is not validated for this session")
    for key, value in (("namespace", args.namespace), ("cpu_node", args.cpu_node)):
        if correctness.get(key) != value:
            raise PolicyError(f"correctness marker {key} differs from this batch")
    verified = correctness.get("verified_unix")
    if not isinstance(verified, (int, float)) or not session["started_unix"] <= verified <= now + 1:
        raise PolicyError("correctness marker time is outside this session")
    first = measurement.read_json(run_dir / "first-timing-block-complete.json")
    if first.get("session_id") != session["session_id"] or first.get("validated") is not True:
        raise PolicyError("validated first timing block is required before policy runs")
    if mode == "heldout":
        review = measurement.read_json(run_dir / "policy-plan-reviewed.json")
        if (review.get("session_id") != session["session_id"] or review.get("reviewed") is not True or
                review.get("frozen") is not True or review.get("plan_sha256") != plan_sha):
            raise PolicyError("held-out plan lacks the exact reviewed/frozen marker")
    return correctness


class PolicyRunner(measurement.Runner):
    def render_workload(self, item, manifest):
        command = [self.python, str(HERE / "workload.py"), item["config"],
                   "--name", item["run_id"], "--cpu-node", self.args.cpu_node,
                   "--namespace", self.args.namespace]
        result = self.command(command, timeout=30)
        if "x-benchmark-decoder" in result.stdout.lower():
            raise PolicyError("rendered policy workload unexpectedly contains a decoder pin")
        manifest.write_text(result.stdout)

    def validate_policy(self, item, arm_dir):
        command = [self.python, str(HERE / "policy-evidence.py"),
                   "--before", str(arm_dir / "before"), "--after", str(arm_dir / "after"),
                   "--run-id", item["run_id"], "--block-id", item["label"], "--config", item["config"],
                   "--policy-manifest", item["manifest"], "--policy", self.policy,
                   "--mode", item["mode"], "--seed", str(item["seed"]),
                   "--local-decoder", self.args.local_decoder, "--remote-decoder", self.args.remote_decoder,
                   "--namespace", self.args.namespace]
        if item["paired_path"]:
            command += ["--paired-with", item["paired_path"]]
        self.command(command, timeout=120, log=arm_dir / "policy-evidence")

    def run_item(self, item, completed):
        arm_dir = self.attempt_dir / item["label"]
        arm_dir.mkdir()
        measurement.write_json(arm_dir / "run.json", item)
        applied = False
        retained = False
        try:
            before = self.settled_snapshot(arm_dir, "before")
            evidence.running_policy(before, Path(item["manifest"]))
            manifest = arm_dir / "workload.yaml"
            self.render_workload(item, manifest)
            self.kubectl(["apply", "-f", "-"], input_text=manifest.read_text(), timeout=45)
            applied = True
            exit_code = self.wait_for_exit(item["run_id"])
            after = self.settled_snapshot(arm_dir, "after", item["run_id"])
            retained = (after / "reports" / "exit-code").exists()
            if exit_code != 0:
                raise PolicyError(f"benchmark harness exited {exit_code}")
            self.validate_policy(item, arm_dir)
            evidence_path = after / "policy-evidence.json"
            completed[item["label"]] = evidence_path
            self.events.append({"event": "validated", "block_id": item["label"],
                                "run_id": item["run_id"], "unix": self.now()})
            measurement.write_json(self.attempt_dir / "policy-state.json",
                                   {"status": "running", "events": self.events})
        except Exception:
            if applied and not retained:
                self.preserve_failure(item, arm_dir)
                retained = (arm_dir / "failure-collection" / "collection.json").exists()
            raise
        finally:
            if applied and retained:
                self.delete_benchmark(item["run_id"])

    def run_policy(self, items, policy):
        self.policy = policy
        self.assert_workers()
        completed = {}
        try:
            for item in items:
                self.run_item(item, completed)
        except Exception as error:
            measurement.write_json(self.attempt_dir / "policy-state.json", {
                "status": "failed", "error": str(error), "events": self.events,
                "failed_unix": self.now(),
            })
            raise
        return completed


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--rendered-dir", required=True, type=Path)
    parser.add_argument("--cpu-node", required=True)
    parser.add_argument("--context", default="router-topology")
    parser.add_argument("--namespace", default="topology-measurement")
    parser.add_argument("--settle-interval", type=float, default=15)
    parser.add_argument("--settle-timeout", type=float, default=90)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if args.settle_interval <= 0 or args.settle_timeout < args.settle_interval:
        parser.error("settle timing must allow at least one positive interval")
    return args


def main(argv=None):
    args = parse_args(argv)
    session = measurement.read_json(args.run_dir / "session.json")
    for key in ("session_id", "started_unix", "cleanup_start_deadline_unix"):
        if key not in session:
            raise PolicyError("session.json lacks " + key)
    plan_bytes = args.plan.read_bytes()
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()
    plan, items, mode = validate_plan(args.plan, args.rendered_dir, args.run_dir)
    suffix = hashlib.sha256((session["session_id"] + uuid.uuid4().hex).encode()).hexdigest()[:8]
    for item in items:
        item["run_id"] = measurement.safe_name(f"{item['label']}-{suffix}")
    preview = {"mode": "execute" if args.execute else "dry-run", "session_id": session["session_id"],
               "policy": plan["policy"], "batch_kind": mode, "plan_sha256": plan_sha,
               "runs": items, "rpc_count": 0 if not args.execute else "bounded at execution",
               "policy_switching": "external; declared policy must already be active"}
    if not args.execute:
        print(json.dumps(preview, indent=2))
        print("DRY RUN: no Kubernetes RPCs were made and no policy was applied.")
        return
    if not os.environ.get("KUBECONFIG"):
        raise PolicyError("--execute requires inherited KUBECONFIG")
    now = time.time()
    correctness = validate_markers(args.run_dir, session, args, plan_sha, mode, now)
    if now + measurement.DEADLINE_RESERVE_SECONDS >= session["cleanup_start_deadline_unix"]:
        raise PolicyError("not enough time remains before the session cleanup deadline")
    args.local_decoder = correctness["local_decoder_pod"]
    args.remote_decoder = correctness["remote_decoder_pod"]
    attempts = args.run_dir / "policy-attempts"
    attempts.mkdir(exist_ok=True)
    attempt_dir = attempts / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now)) + "-" + suffix)
    attempt_dir.mkdir()
    measurement.write_json(attempt_dir / "policy-plan.json", preview)
    runner = PolicyRunner(args, session, correctness, attempt_dir,
                          session["cleanup_start_deadline_unix"])
    completed = runner.run_policy(items, plan["policy"])
    if len(completed) != len(items):
        raise PolicyError("not every declared policy block passed strict validation")
    result = {"session_id": session["session_id"], "validated": True, "policy": plan["policy"],
              "mode": mode, "heldout": mode == "heldout", "plan_sha256": plan_sha,
              "blocks": list(completed), "attempt_dir": str(attempt_dir), "finished_unix": time.time(),
              "interpretation": "Descriptive policy observations; no automatic winner or p99 claim."}
    measurement.write_json(attempt_dir / "policy-batch-complete.json", result)
    measurement.write_json(attempt_dir / "policy-state.json", {"status": "complete", "events": runner.events})
    print("PASS: declared one-policy batch strictly validated: " + str(attempt_dir))


if __name__ == "__main__":
    try:
        main()
    except (PolicyError, evidence.EvidenceError) as error:
        raise SystemExit("FAIL: " + str(error))
