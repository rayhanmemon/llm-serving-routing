"""Offline tests for unpinned policy evidence and orchestration."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).parent))


def load(name):
    path = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evidence = load("policy-evidence")
runner = load("run-policy-comparison")


def stream(prompt_tokens=512, response_id=None):
    chunks = [
        {"id": response_id, "choices": [{"index": 0, "text": "answer", "finish_reason": None}]},
        {"id": response_id, "choices": [{"index": 0, "text": "", "finish_reason": "length"}]},
        {"id": response_id, "choices": [],
         "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 128}},
    ]
    return "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"


def record(prompt_tokens=512, start=10.0, error=None, response_id=None):
    return {"request": json.dumps({"prompt": "x" * prompt_tokens, "stream": True,
                                    "ignore_eos": True, "max_tokens": 128}),
            "response": stream(prompt_tokens, response_id), "error": error,
            "start_time": start, "end_time": start + 2,
            "info": {"request_metrics": {"text": {"input_tokens": prompt_tokens}},
                     "input_tokens": prompt_tokens,
                     "response_metrics": {
                         "response_chunks": [json.dumps({"id": response_id, "choices": [
                             {"index": 0, "text": "answer", "finish_reason": None}]})],
                         "chunk_times": [start + 1],
                         "output_token_times": [start + 1, start + 1.5]}}}


class PolicyEvidenceTest(unittest.TestCase):
    def test_workload_rejects_pins_and_accepts_mixed_trace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trace = root / "heldout.trace.csv"
            trace.write_text("TIMESTAMP,ContextTokens,GeneratedTokens\n"
                             "2026-09-16T00:00:00.000Z,512,128\n"
                             "2026-09-16T00:00:01.000Z,8192,128\n")
            config = {"api": {"type": "completion", "streaming": True, "headers": {}},
                      "server": {"ignore_eos": True}, "data": {"trace": {"file": trace.name}},
                      "load": {"type": "trace_replay", "base_seed": 9,
                               "trace": {"file": trace.name}, "stages": [{"rate": 2, "duration": 1}]}}
            path = root / "heldout.yaml"; path.write_text(yaml.safe_dump(config))
            loaded = evidence.load_workload(path)
            self.assertEqual(loaded["input_tokens"], [512, 8192])
            self.assertEqual(loaded["count"], 2)
            self.assertEqual(loaded["trace_sha256"], hashlib.sha256(trace.read_bytes()).hexdigest())
            config["api"]["headers"]["x-benchmark-decoder"] = "pin"
            path.write_text(yaml.safe_dump(config))
            with self.assertRaisesRegex(evidence.EvidenceError, "diagnostic decoder pin"):
                evidence.load_workload(path)

    def test_errors_and_partial_completion_are_rejected(self):
        workload = {"count": 2, "input_tokens": [512, 8192]}
        with self.assertRaisesRegex(evidence.EvidenceError, "count mismatch"):
            evidence.validate_records([record(512)], workload)
        with self.assertRaisesRegex(evidence.EvidenceError, "request error"):
            evidence.validate_records([record(512), record(8192, error={"message": "timeout"})], workload)
        rows = evidence.validate_records([record(512), record(8192, start=13)], workload)
        self.assertEqual([row["prompt_tokens"] for row in rows], [512, 8192])
        expanded = record(512)
        expanded["info"]["response_metrics"]["output_token_times"].append(11.75)
        self.assertEqual(evidence.validate_records([expanded], {"count": 1, "input_tokens": [512]})[0]["ttft_seconds"], 1)
        broken = record(512)
        broken["info"]["response_metrics"]["response_chunks"] = ["{}"]
        with self.assertRaisesRegex(evidence.EvidenceError, "differ from raw SSE"):
            evidence.validate_records([broken], {"count": 1, "input_tokens": [512]})

    def test_unpinned_routes_use_actual_decoder_ips(self):
        ip_map = {"10.0.0.1:8000": ("local", "decode-local"),
                  "10.0.0.2:8000": ("remote", "decode-remote")}
        row = {"run_id": "run", "request_id": "request-1", "requested_decoder": "-",
               "selected_decoder": "-", "upstream_host": "10.0.0.1:8000",
               "status": 200, "response_flags": "-", "duration_ms": 2}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "envoy.log"
            path.write_text("2026-09-16T00:00:01.000000000Z " + json.dumps(row) + "\n")
            routes = evidence.route_rows([path], "run", ip_map, "ns")
            self.assertEqual(routes[0]["decoder_pod"], "decode-local")
            row["upstream_host"] = "10.0.0.9:8000"
            path.write_text("2026-09-16T00:00:01Z " + json.dumps(row) + "\n")
            with self.assertRaisesRegex(evidence.EvidenceError, "not one of the two"):
                evidence.route_rows([path], "run", ip_map, "ns")

    def test_concurrent_client_records_are_not_joined_to_envoy_routes(self):
        client = evidence.validate_records([record(512), record(8192, start=10.1)],
                                           {"count": 2, "input_tokens": [512, 8192]})
        routes = [{"request_id": "a", "route": "local", "decoder_pod": "local"},
                  {"request_id": "b", "route": "remote", "decoder_pod": "remote"}]
        joined, status = evidence.exact_route_join(client, routes)
        self.assertIn("unknown", status)
        self.assertTrue(all("route" not in row and "local_route" not in row for row in joined))

    def test_exact_ids_join_routes_even_when_completion_order_is_reversed(self):
        first = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        second = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        client = evidence.validate_records([
            record(512, start=10, response_id="cmpl-" + first),
            record(8192, start=10.1, response_id="cmpl-" + second),
        ], {"count": 2, "input_tokens": [512, 8192]})
        routes = [
            {"request_id": second, "route": "local", "decoder_pod": "local"},
            {"request_id": first, "route": "remote", "decoder_pod": "remote"},
        ]
        joined, status = evidence.exact_route_join(client, routes)
        self.assertIn("exact", status)
        self.assertEqual([row["route"] for row in joined], ["remote", "local"])
        mismatched, status = evidence.exact_route_join(client, routes[:-1])
        self.assertIn("unknown", status)
        self.assertTrue(all("route" not in row for row in mismatched))

    def test_transfer_counts_sum_across_actual_decoders(self):
        def metrics(count, failures=0):
            return (f"vllm:nixl_bytes_transferred_count {count}\n"
                    f"vllm:nixl_xfer_time_seconds_count {count}\n"
                    f"vllm:nixl_num_failed_transfers_total {failures}\n"
                    "vllm:nixl_num_kv_expired_reqs_total 0\n"
                    "vllm:nixl_num_failed_notifications_total 0\n")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); before = root / "before"; after = root / "after"
            before.mkdir(); after.mkdir()
            for name, count in (("local", 1), ("remote", 1)):
                (before / f"{name}-metrics.txt").write_text(metrics(0))
                (after / f"{name}-metrics.txt").write_text(metrics(count))
            (before / "prefill-metrics.txt").write_text(metrics(0))
            (after / "prefill-metrics.txt").write_text(metrics(0))
            result = evidence.transfer_delta(before, after, ("local", "remote"), 2)
            self.assertEqual(result["totals"]["vllm:nixl_bytes_transferred_count"], 2)
            (after / "remote-metrics.txt").write_text(metrics(0))
            with self.assertRaisesRegex(evidence.EvidenceError, "differs from completed"):
                evidence.transfer_delta(before, after, ("local", "remote"), 2)

    def test_updated_configmap_does_not_substitute_for_loaded_epp_config(self):
        expected = {"apiVersion": "llm-d.ai/v1alpha1", "kind": "EndpointPickerConfig",
                    "plugins": [{"type": "decode-filter"}],
                    "schedulingProfiles": [{"name": "decode", "plugins": [
                        {"pluginRef": "decode-filter"}]}]}
        old = {**expected, "plugins": [{"type": "session-affinity-filter", "name": "pin"}],
               "schedulingProfiles": [{"name": "decode", "plugins": [{"pluginRef": "pin"}]}]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "router-none.values.yaml"
            manifest.write_text(yaml.safe_dump({"router": {"epp": {"pluginsConfig": expected}}}))
            (root / "configmaps.json").write_text(json.dumps({"items": [{
                "metadata": {"name": "epp-config"}, "data": {"topology.yaml": yaml.safe_dump(expected)}}]}))
            pod = {"metadata": {"name": "epp-new", "uid": "uid-new"},
                   "spec": {"containers": [{"name": "epp",
                                              "image": "repo/llm-d-router-endpoint-picker:test"}]}}
            (root / "pods.json").write_text(json.dumps({"items": [pod]}))
            log = {"body": "Raw config after phase one", "config": old}
            (root / "epp-new-epp.log").write_text("2026-09-16T00:00:00Z " + json.dumps(log) + "\n")
            with self.assertRaisesRegex(evidence.EvidenceError, "startup config does not match"):
                evidence.running_policy(root, manifest)
            log["config"] = expected
            (root / "epp-new-epp.log").write_text("2026-09-16T00:00:00Z " + json.dumps(log) + "\n")
            self.assertEqual(evidence.running_policy(root, manifest)["pod_uid"], "uid-new")

    def test_paired_heldout_evidence_requires_same_block(self):
        workload = {"trace_sha256": "trace", "seed": 7}
        reference = {"mode": "heldout", "block_id": "block-1", "request_hashes": ["request"],
                     "prompt_hashes": ["prompt"], "intended_arrival_trace_sha256": "trace", "seed": 7}
        evidence.validate_pair(reference, ["request"], ["prompt"], workload, "heldout", "block-1")
        with self.assertRaisesRegex(evidence.EvidenceError, "different mode or block_id"):
            evidence.validate_pair(reference, ["request"], ["prompt"], workload, "heldout", "block-3")


class PolicyPlanTest(unittest.TestCase):
    def fixture(self, temporary, heldout=False):
        root = Path(temporary)
        rendered = root / "rendered"; rendered.mkdir()
        policy = {"apiVersion": "llm-d.ai/v1alpha1", "kind": "EndpointPickerConfig",
                  "plugins": [{"type": "decode-filter"}]}
        manifest = rendered / "router-none.values.yaml"
        manifest.write_text(yaml.safe_dump({"router": {"epp": {"pluginsConfig": policy}}}))
        if heldout:
            trace = rendered / "heldout-low.trace.csv"
            trace.write_text("TIMESTAMP,ContextTokens,GeneratedTokens\n"
                             "2026-09-16T00:00:00.000Z,512,128\n"
                             "2026-09-16T00:00:01.000Z,8192,128\n")
            config = {"api": {"type": "completion", "streaming": True, "headers": {}},
                      "server": {"ignore_eos": True}, "data": {"trace": {"file": trace.name}},
                      "load": {"type": "trace_replay", "base_seed": 7,
                               "trace": {"file": trace.name}, "stages": [{"rate": 2, "duration": 1}]}}
            config_name, mode = "heldout-low.yaml", "heldout"
            trace_hash = hashlib.sha256(trace.read_bytes()).hexdigest()
            count = 2
        else:
            config = {"api": {"type": "completion", "streaming": True, "headers": {}},
                      "server": {"ignore_eos": True},
                      "data": {"input_distribution": {"min": 512, "max": 512, "mean": 512},
                               "output_distribution": {"min": 128, "max": 128, "mean": 128}},
                      "load": {"type": "concurrent", "base_seed": 7,
                               "stages": [{"num_requests": 3, "concurrency_level": 1}]}}
            config_name, mode, trace_hash, count = "calibration-512.yaml", "calibration", None, 3
        (rendered / config_name).write_text(yaml.safe_dump(config))
        plan = {"schema_version": 1, "policy": "none", "frozen": heldout,
                "reference_policy": True, "runs": [{"block_id": "block-1",
                    "workload_config": config_name, "policy_manifest": manifest.name,
                    "mode": mode, "seed": 7, "expected_count": count,
                    "arrival_trace_sha256": trace_hash}]}
        plan_path = root / "plan.json"; plan_path.write_text(json.dumps(plan))
        (root / "session.json").write_text(json.dumps({"session_id": "s", "started_unix": 1,
                                                        "cleanup_start_deadline_unix": 9999999999}))
        return root, rendered, plan_path, plan

    def test_heldout_plan_must_be_frozen_and_exact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, rendered, plan_path, plan = self.fixture(temporary, heldout=True)
            plan["frozen"] = False; plan_path.write_text(json.dumps(plan))
            with self.assertRaisesRegex(runner.PolicyError, "explicitly frozen"):
                runner.validate_plan(plan_path, rendered, root)
            plan["frozen"] = True
            plan["runs"][0]["expected_count"] = 3
            plan_path.write_text(json.dumps(plan))
            with self.assertRaisesRegex(runner.PolicyError, "expected_count"):
                runner.validate_plan(plan_path, rendered, root)

    def test_dry_attempts_have_unique_run_ids_and_no_processes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, rendered, plan_path, _ = self.fixture(temporary)
            argv = ["--plan", str(plan_path), "--run-dir", str(root),
                    "--rendered-dir", str(rendered), "--cpu-node", "cpu"]
            outputs = []
            for _ in range(2):
                stream = io.StringIO()
                with patch.object(runner.subprocess, "run", side_effect=AssertionError("process attempted")), \
                     patch("sys.stdout", stream):
                    runner.main(argv)
                text = stream.getvalue()
                outputs.append(json.loads(text[:text.rfind("}\n") + 1])["runs"][0]["run_id"])
            self.assertNotEqual(outputs[0], outputs[1])
            self.assertFalse((root / "policy-attempts").exists())

    def test_expired_attempt_stops_before_process(self):
        class Args:
            context = "ctx"; namespace = "ns"; cpu_node = "cpu"
            local_decoder = "local"; remote_decoder = "remote"
            settle_interval = 15; settle_timeout = 90
        with tempfile.TemporaryDirectory() as temporary:
            subject = runner.PolicyRunner(Args(), {"session_id": "s"}, {}, Path(temporary),
                                          deadline=10, now_fn=lambda: 10)
            with patch.object(runner.measurement.subprocess, "run",
                              side_effect=AssertionError("process attempted")):
                with self.assertRaisesRegex(runner.PolicyError, "deadline reached"):
                    subject.command(["kubectl"])


if __name__ == "__main__":
    unittest.main()
