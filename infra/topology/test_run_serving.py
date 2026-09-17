"""Test serving qualification without making cloud or Kubernetes RPCs."""
import base64
import copy
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("run_serving", Path(__file__).with_name("run-serving.py"))
serving = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(serving)


def node(name, group, gpus, ready=True):
    return {
        "metadata": {"name": name, "labels": {
            "nebius.com/node-group-id": group,
            "kubernetes.io/hostname": name,
        }},
        "spec": {},
        "status": {
            "allocatable": {"nvidia.com/gpu": str(gpus)},
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
        },
    }


def pod(name, uid, node_name, container_name, image):
    return {
        "metadata": {"name": name, "uid": uid},
        "spec": {
            "nodeName": node_name,
            "containers": [{"name": container_name, "image": image}],
        },
        "status": {"containerStatuses": [{
            "name": container_name,
            "ready": True,
            "containerID": "containerd://" + uid,
            "imageID": "sha256:" + uid,
            "restartCount": 0,
            "state": {"running": {}},
        }]},
    }


def metric_text(operations, byte_count, seconds):
    return "\n".join([
        "vllm:num_requests_running 0",
        "vllm:num_requests_waiting 0",
        f"vllm:nixl_bytes_transferred_sum {byte_count}",
        f"vllm:nixl_bytes_transferred_count {operations}",
        f"vllm:nixl_xfer_time_seconds_sum {seconds}",
        f"vllm:nixl_xfer_time_seconds_count {operations}",
        "vllm:nixl_num_failed_transfers_total 0",
        "vllm:nixl_num_kv_expired_reqs_total 0",
        "vllm:nixl_num_failed_notifications_total 0",
    ]) + "\n"


class RunServingTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def make_archive(self):
        archive_path = self.root / "epp.tar"
        manifest = [{"RepoTags": [serving.EXPECTED_EPP_IMAGE], "Config": "config.json"}]
        config = {"os": "linux", "architecture": "amd64"}
        with tarfile.open(archive_path, "w") as archive:
            for name, value in (("manifest.json", manifest), ("config.json", config)):
                data = json.dumps(value).encode()
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        digest = __import__("hashlib").sha256(archive_path.read_bytes()).hexdigest()
        return archive_path, digest

    def snapshot(self, name, local_ops, remote_ops, mutate=None):
        folder = self.root / name
        folder.mkdir()
        pods = [
            pod("prefill-a", "p", "local-node", "modelserver", "vllm"),
            pod("decode-local-a", "l", "local-node", "modelserver", "vllm"),
            pod("decode-remote-a", "r", "remote-node", "modelserver", "vllm"),
            pod("epp-a", "e", "cpu-node", "epp", "example/llm-d-router-endpoint-picker:test"),
        ]
        if mutate:
            mutate(pods)
        (folder / "pods.json").write_text(json.dumps({"items": pods}))
        for worker, gpu in (("prefill-a", "GPU-1"), ("decode-local-a", "GPU-2"), ("decode-remote-a", "GPU-3")):
            (folder / f"{worker}-gpu.json").write_text(json.dumps({"gpu_uuids": [gpu]}))
        (folder / "prefill-a-metrics.txt").write_text(metric_text(0, 0, 0))
        (folder / "decode-local-a-metrics.txt").write_text(
            metric_text(local_ops, local_ops * 100, local_ops * 0.1)
        )
        (folder / "decode-remote-a-metrics.txt").write_text(
            metric_text(remote_ops, remote_ops * 100, remote_ops * 0.1)
        )
        return folder

    def results(self):
        def result(route, decoder=None):
            requested = None if decoder is None else base64.b64encode(
                f"topology-measurement/{decoder}-rank-0".encode()
            ).decode()
            return {
                "route": route,
                "status": 200,
                "error": None,
                "requested_decoder": requested,
                "selected_decoder": requested,
                "request_body": {"max_tokens": 16},
                "body": {
                    "choices": [{"text": " Paris is France's capital.", "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 16},
                },
            }
        return {
            "direct": result("direct"),
            "local": result("local", "decode-local-a"),
            "remote": result("remote", "decode-remote-a"),
        }

    def snapshots(self, mutate_final=None):
        return {
            "before": self.snapshot("before", 0, 0),
            "after-direct": self.snapshot("after-direct", 0, 0),
            "after-local": self.snapshot("after-local", 1, 0),
            "after-remote": self.snapshot("after-remote", 1, 1, mutate_final),
        }

    def test_default_main_is_dry_and_starts_no_subprocess(self):
        run_dir = self.root / "run"
        run_dir.mkdir()
        chart = self.root / "chart"
        chart.mkdir()
        (chart / "Chart.yaml").write_text("apiVersion: v2\nname: test\nversion: 1.0.0\n")
        archive, digest = self.make_archive()
        argv = [
            "--run-dir", str(run_dir), "--chart-path", str(chart),
            "--epp-archive", str(archive), "--epp-sha256", digest,
        ]
        for profile, terraform_dir, attention_backend in (
            ("h200-evaluation", serving.TERRAFORM_DIR, None),
            ("h200-on-demand", serving.TERRAFORM_DIR, None),
            ("rtx-on-demand", serving.RTX_TERRAFORM_DIR, "TRITON_ATTN"),
        ):
            with self.subTest(profile=profile):
                (run_dir / "session.json").write_text(json.dumps({
                    "session_id": "session",
                    "profile": profile,
                    "terraform_dir": str(terraform_dir.resolve()),
                    "attention_backend": attention_backend,
                    "first_measurement_deadline_unix": 9999999999,
                }))
                with patch.object(
                    serving.subprocess, "run", side_effect=AssertionError("process attempted")
                ), patch("builtins.print") as output:
                    serving.main(argv)
                self.assertIn("no subprocesses or RPCs", output.call_args_list[-1].args[0])
                self.assertFalse((run_dir / "serving").exists())

    def test_rtx_render_passes_required_attention_backend(self):
        args = types.SimpleNamespace(run_dir=self.root, namespace="topology-measurement")
        subject = serving.Runner(args, {
            "profile": "rtx-on-demand",
            "terraform_dir": str(serving.RTX_TERRAFORM_DIR.resolve()),
            "attention_backend": "TRITON_ATTN",
            "first_measurement_deadline_unix": 1000,
        }, self.root / "serving", now_fn=lambda: 0)
        subject.root.mkdir()
        captured = []
        subject.command = lambda command, **kwargs: (
            captured.append(command)
            or serving.subprocess.CompletedProcess(command, 0, "", "")
        )

        subject.render({"local": "local", "remote": "remote", "cpu": "cpu"})

        self.assertIn("--attention-backend", captured[0])
        self.assertEqual(captured[0][captured[0].index("--attention-backend") + 1], "TRITON_ATTN")

    def test_node_groups_map_to_exact_ready_shapes(self):
        groups = {"local": "group-local", "remote": "group-remote", "cpu": "group-cpu"}
        items = [
            node("cpu-node", "group-cpu", 0),
            node("remote-node", "group-remote", 1),
            node("local-node", "group-local", 8),
        ]
        self.assertEqual(serving.ready_node_mapping(items, groups), {
            "local": "local-node", "remote": "remote-node", "cpu": "cpu-node",
        })
        items[0]["status"]["allocatable"]["nvidia.com/gpu"] = "1"
        with self.assertRaisesRegex(serving.ServingError, "CPU|cpu node advertises"):
            serving.ready_node_mapping(items, groups)

    def test_combined_epp_envoy_pod_and_decode_sidecars_are_ready(self):
        def status(name, uid):
            return {"name": name, "ready": True, "restartCount": 0,
                    "containerID": "containerd://" + uid, "state": {"running": {}}}

        pods = []
        for app, uid, node_name in (
            ("prefill", "p", "local-node"),
            ("decode-local", "l", "local-node"),
            ("decode-remote", "r", "remote-node"),
        ):
            value = pod(app + "-abc", uid, node_name, "modelserver", "vllm")
            value["metadata"]["labels"] = {"app.kubernetes.io/name": app}
            value["status"]["podIP"] = "10.0.0." + str(len(pods) + 1)
            if app.startswith("decode"):
                value["spec"]["initContainers"] = [{"name": "routing-proxy", "image": "sidecar"}]
                value["status"]["initContainerStatuses"] = [status("routing-proxy", uid + "-sidecar")]
            pods.append(value)
        routing = pod(
            "topology-epp-abc", "e", "cpu-node", "epp",
            "example/llm-d-router-endpoint-picker:test",
        )
        routing["spec"]["containers"].append({"name": "envoy-proxy", "image": "envoyproxy/envoy:test"})
        routing["status"]["containerStatuses"].append(status("envoy-proxy", "envoy"))
        pods.append(routing)
        args = types.SimpleNamespace(namespace="topology-measurement")
        subject = serving.Runner(
            args, {"first_measurement_deadline_unix": 1000}, self.root, now_fn=lambda: 0
        )

        result = subject.serving_inventory(pods, {
            "local": "local-node", "remote": "remote-node", "cpu": "cpu-node",
        })

        self.assertEqual(result["local_decoder"], "decode-local-abc")
        self.assertEqual(result["remote_decoder"], "decode-remote-abc")
        self.assertEqual(result["local_decoder_ip"], "10.0.0.2")

    def test_exact_correctness_outputs_pins_transfers_and_stability_pass(self):
        identities = serving.validate_correctness(
            self.results(), self.snapshots(), "decode-local-a", "decode-remote-a",
            "topology-measurement",
        )
        self.assertEqual(set(identities), {"prefill-a", "decode-local-a", "decode-remote-a", "epp-a"})

    def test_bad_correctness_output_is_rejected(self):
        results = self.results()
        results["remote"]["body"]["usage"]["completion_tokens"] = 15
        with self.assertRaisesRegex(serving.ServingError, "remote correctness output"):
            serving.validate_correctness(
                results, self.snapshots(), "decode-local-a", "decode-remote-a",
                "topology-measurement",
            )

    def test_different_request_body_or_prompt_token_count_is_rejected(self):
        snapshots = self.snapshots()
        results = self.results()
        results["remote"]["request_body"] = {"max_tokens": 15}
        with self.assertRaisesRegex(serving.ServingError, "request bodies differ"):
            serving.validate_correctness(
                results, snapshots, "decode-local-a", "decode-remote-a",
                "topology-measurement",
            )
        results = self.results()
        results["remote"]["body"]["usage"]["prompt_tokens"] = 6
        with self.assertRaisesRegex(serving.ServingError, "prompt token counts differ"):
            serving.validate_correctness(
                results, snapshots, "decode-local-a", "decode-remote-a",
                "topology-measurement",
            )

    def test_wrong_decoder_pin_is_rejected(self):
        results = self.results()
        results["local"]["selected_decoder"] = "wrong"
        with self.assertRaisesRegex(serving.ServingError, "local decoder pin"):
            serving.validate_correctness(
                results, self.snapshots(), "decode-local-a", "decode-remote-a",
                "topology-measurement",
            )

    def test_wrong_transfer_count_is_rejected(self):
        snapshots = self.snapshots()
        (snapshots["after-local"] / "decode-local-a-metrics.txt").write_text(metric_text(2, 200, 0.2))
        with self.assertRaisesRegex(serving.ServingError, "exactly one qualified transfer"):
            serving.validate_correctness(
                self.results(), snapshots, "decode-local-a", "decode-remote-a",
                "topology-measurement",
            )

    def test_worker_restart_is_rejected(self):
        def restart(pods):
            pods[1]["status"]["containerStatuses"][0]["restartCount"] = 1
        with self.assertRaisesRegex(serving.ServingError, "identity, placement, or restart"):
            serving.validate_correctness(
                self.results(), self.snapshots(restart), "decode-local-a", "decode-remote-a",
                "topology-measurement",
            )

    def test_timeout_bytes_are_normalized_before_logging(self):
        args = types.SimpleNamespace()
        subject = serving.Runner(
            args, {"first_measurement_deadline_unix": 1000}, self.root, now_fn=lambda: 0
        )
        log = self.root / "timeout"
        error = serving.subprocess.TimeoutExpired(
            ["command"], 1, output=b"partial\xff", stderr=b"late\xfe"
        )
        with patch.object(serving.subprocess, "run", side_effect=error):
            result = subject.command(["command"], timeout=1, check=False, log=log)
        self.assertEqual(result.returncode, 124)
        self.assertIn("partial", Path(str(log) + ".stdout").read_text())
        self.assertIn("late", Path(str(log) + ".stderr").read_text())

    def test_metric_parser_requires_finite_gauges_and_ignores_unrelated_nan(self):
        path = self.root / "metrics.txt"
        path.write_text(
            "vllm:num_requests_running 0\n"
            "vllm:num_requests_waiting 0\n"
            "unrelated_metric NaN\n"
        )
        self.assertEqual(serving.prometheus_values(path), {
            "vllm:num_requests_running": 0.0,
            "vllm:num_requests_waiting": 0.0,
        })
        path.write_text("vllm:num_requests_running NaN\nvllm:num_requests_waiting 0\n")
        with self.assertRaisesRegex(serving.ServingError, "non-finite"):
            serving.prometheus_values(path)

    def test_settlement_uses_light_polls_then_one_full_collection_and_late_check(self):
        clock = [0.0]
        args = types.SimpleNamespace(settle_timeout=90, settle_interval=15)
        subject = serving.Runner(
            args,
            {"first_measurement_deadline_unix": 1000},
            self.root,
            now_fn=lambda: clock[0],
            sleep_fn=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        )
        state = {"engine-metrics.txt": {
            "vllm:num_requests_running": 0.0,
            "vllm:num_requests_waiting": 0.0,
            "vllm:nixl_bytes_transferred_count": 1.0,
        }}
        poll_calls = []
        full_calls = []
        subject.poll_metrics = lambda path: poll_calls.append(path.name) or copy.deepcopy(state)
        subject.collect = lambda path: full_calls.append(path.name) or copy.deepcopy(state)

        result = subject.settled_collection("after-local")

        self.assertEqual(result.name, "full-02")
        self.assertEqual(poll_calls, ["sample-01", "sample-02", "late-02"])
        self.assertEqual(full_calls, ["full-02"])
        self.assertEqual(clock[0], 30)


class ResumeRenderTest(unittest.TestCase):
    def test_existing_render_is_preserved_and_fresh_render_succeeds(self):
        import subprocess, sys
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subject = serving.Runner.__new__(serving.Runner)
            subject.args = types.SimpleNamespace(run_dir=root, namespace="test")
            subject.root = root
            subject.session = {}
            subject.command = lambda command, **kwargs: subprocess.run(command, capture_output=True, text=True)
            nodes = {"local":"local-host", "remote":"remote-host", "cpu":"cpu-host"}
            first = subject.render_for_resume(nodes)
            (first / "evidence-marker").write_text("keep")
            second = subject.render_for_resume(nodes)
            self.assertTrue((second / "modelservers.yaml").is_file())
            backups = list(root.glob("rendered-before-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "evidence-marker").read_text(), "keep")


class FrozenSuiteStagingTest(unittest.TestCase):
    def test_custom_filename_gets_validator_sidecar_without_changing_plan(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = root / "frozen-suite.json"
            plan.write_text('{"cases": []}\n')
            expected = hashlib.sha256(plan.read_bytes()).hexdigest()
            (root / "frozen-suite.sha256").write_text(expected)
            self.assertEqual(serving.stage_frozen_suite(root), plan)
            self.assertEqual((root / "suite.sha256").read_text().split()[0], expected)
            plan.write_text("changed")
            with self.assertRaises(serving.ServingError):
                serving.stage_frozen_suite(root)


if __name__ == "__main__":
    unittest.main()
