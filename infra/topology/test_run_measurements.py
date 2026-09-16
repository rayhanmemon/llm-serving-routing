"""Test measurement orchestration without making Kubernetes RPCs."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("run_measurements", Path(__file__).with_name("run-measurements.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def pod(name, uid, container_name, image, phase="Running", exit_code=None):
    status = {"name": container_name, "ready": True, "restartCount": 0,
              "containerID": "containerd://" + uid, "state": {"running": {}}}
    value = {"metadata": {"name": name, "uid": uid},
             "spec": {"nodeName": "node-" + uid,
                      "containers": [{"name": container_name, "image": image}]},
             "status": {"phase": phase, "containerStatuses": [status]}}
    if exit_code is not None:
        value["status"]["containerStatuses"][0]["state"] = {"terminated": {"exitCode": exit_code}}
        value["status"]["containerStatuses"][0]["ready"] = False
    return value


def workers():
    return [
        pod("prefill", "p", "modelserver", "vllm"),
        pod("local", "l", "modelserver", "vllm"),
        pod("remote", "r", "modelserver", "vllm"),
        pod("epp", "e", "epp", "example/llm-d-router-endpoint-picker:test"),
    ]


class Args:
    context = "ctx"
    namespace = "topology-measurement"
    settle_timeout = 10
    settle_interval = 1


class MeasurementRunnerTest(unittest.TestCase):
    def runner(self, temporary, now_fn=lambda: 1000):
        return m.Runner(Args(), {"session_id": "s"}, {"transfers_per_request": 1},
                        Path(temporary), 2000, now_fn=now_fn, sleep_fn=lambda _: None)

    def test_running_pod_finishes_when_exit_code_appears(self):
        with tempfile.TemporaryDirectory() as temporary:
            subject = self.runner(temporary)
            inventory = workers() + [pod("bench", "b", "benchmark", "inference-perf")]
            subject.inventory = lambda: inventory
            subject.assert_workers(inventory)
            subject.kubectl = lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "0\n", "")
            self.assertEqual(subject.wait_for_exit("bench"), 0)

    def test_nonzero_exit_is_returned_while_pod_is_still_running(self):
        with tempfile.TemporaryDirectory() as temporary:
            subject = self.runner(temporary)
            inventory = workers() + [pod("bench", "b", "benchmark", "inference-perf")]
            subject.inventory = lambda: inventory
            subject.assert_workers(inventory)
            subject.kubectl = lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "7\n", "")
            self.assertEqual(subject.wait_for_exit("bench"), 7)

    def test_nonzero_exit_stops_before_validation_after_reports_are_retained(self):
        with tempfile.TemporaryDirectory() as temporary:
            subject = self.runner(temporary)
            item = {"label": "arm", "run_id": "bench", "config": "fixture", "decoder_pod": "local",
                    "input_tokens": 512, "expected_requests": 12, "paired_with": None}
            snapshots = []
            def settled(arm_dir, phase, benchmark_pod=None):
                destination = arm_dir / phase
                (destination / "reports").mkdir(parents=True)
                (destination / "reports" / "exit-code").write_text("7\n" if phase == "after" else "0\n")
                snapshots.append((phase, benchmark_pod))
                return destination
            subject.settled_snapshot = settled
            subject.render_workload = lambda item, path: path.write_text("manifest")
            subject.kubectl = lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", "")
            subject.wait_for_exit = lambda _: 7
            subject.validate = lambda *args: self.fail("validation must not run after nonzero harness exit")
            deleted = []
            subject.delete_benchmark = lambda name: deleted.append(name)
            with self.assertRaisesRegex(m.MeasurementError, "exited 7"):
                subject.run_item(item, {})
            self.assertEqual(snapshots, [("before", None), ("after", "bench")])
            self.assertEqual(deleted, ["bench"])

    def test_wait_timeout_is_bounded(self):
        clock = [1000.0]
        def now():
            return clock[0]
        with tempfile.TemporaryDirectory() as temporary:
            subject = self.runner(temporary, now)
            inventory = workers() + [pod("bench", "b", "benchmark", "inference-perf")]
            subject.inventory = lambda: inventory
            subject.assert_workers(inventory)
            subject.kubectl = lambda args, **kwargs: subprocess.CompletedProcess(args, 1, "", "not ready")
            subject.sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
            with self.assertRaisesRegex(m.MeasurementError, "timed out"):
                subject.wait_for_exit("bench", timeout=3)

    def test_timeout_bytes_are_normalized_before_logging(self):
        with tempfile.TemporaryDirectory() as temporary:
            subject = self.runner(temporary)
            log = Path(temporary) / "timeout"
            error = subprocess.TimeoutExpired(["command"], 1, output=b"partial\xff", stderr=b"late\xfe")
            with patch.object(m.subprocess, "run", side_effect=error):
                result = subject.command(["command"], timeout=1, check=False, log=log)
            self.assertEqual(result.returncode, 124)
            self.assertIn("partial", (Path(str(log) + ".stdout")).read_text())
            self.assertIn("late", (Path(str(log) + ".stderr")).read_text())

    def test_first_plan_has_unique_runs_and_exact_pair_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for stem in ("benchmark-512", "benchmark-8192", "warmup-512", "warmup-8192"):
                (root / (stem + ".yaml")).write_text("fixture\n")
            plan = m.first_block_plan(root, "decode-local", "decode-remote", "abc123")
            m.validate_plan(plan)
            arms = plan["arms"]
            self.assertEqual([(a["input_tokens"], a["route"]) for a in arms],
                             [(512, "local"), (512, "remote"), (8192, "remote"), (8192, "local")])
            self.assertEqual([a["paired_with"] for a in arms],
                             [None, "short-local-b1", None, "long-remote-b1"])
            self.assertEqual(len({a["run_id"] for a in plan["warmups"] + arms}), 8)

    def test_default_main_is_dry_and_never_starts_a_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "session.json").write_text(json.dumps({
                "session_id": "session", "started_unix": time.time(),
                "cleanup_start_deadline_unix": time.time() + 1000,
            }))
            rendered = root / "rendered"; rendered.mkdir()
            for stem in ("benchmark-512", "benchmark-8192", "warmup-512", "warmup-8192"):
                (rendered / (stem + ".yaml")).write_text("fixture\n")
            argv = ["--run-dir", str(root), "--rendered-dir", str(rendered), "--cpu-node", "cpu",
                    "--local-decoder", "local", "--remote-decoder", "remote"]
            with patch.object(m.subprocess, "run", side_effect=AssertionError("RPC/process attempted")), \
                 patch("builtins.print") as output:
                m.main(argv)
            self.assertIn("no Kubernetes RPCs", output.call_args_list[-1].args[0])
            self.assertFalse((root / "first-timing-block-complete.json").exists())

    def test_worker_restart_aborts(self):
        initial = workers()
        changed = workers()
        changed[1]["status"]["containerStatuses"][0]["restartCount"] = 1
        with tempfile.TemporaryDirectory() as temporary:
            subject = self.runner(temporary)
            subject.assert_workers(initial)
            with self.assertRaisesRegex(m.MeasurementError, "identity, placement, or restart"):
                subject.assert_workers(changed)

    def test_settlement_requires_zero_queues_and_stable_transfer_counters(self):
        idle = {"decode-metrics.txt": {
            "vllm:num_requests_running": 0.0,
            "vllm:num_requests_waiting": 0.0,
            "vllm:nixl_bytes_transferred_count": 12.0,
        }}
        self.assertTrue(m.metrics_settled(idle, idle))
        busy = json.loads(json.dumps(idle)); busy["decode-metrics.txt"]["vllm:num_requests_running"] = 1.0
        changed = json.loads(json.dumps(idle)); changed["decode-metrics.txt"]["vllm:nixl_bytes_transferred_count"] = 13.0
        self.assertFalse(m.metrics_settled(busy, busy))
        self.assertFalse(m.metrics_settled(idle, changed))

    def test_metric_parser_rejects_missing_or_nonfinite_required_gauges(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(3):
                (root / f"engine-{index}-metrics.txt").write_text(
                    "vllm:num_requests_running 0\n"
                    "vllm:nixl_bytes_transferred_count 1\n")
            with self.assertRaisesRegex(m.MeasurementError, "gauges missing"):
                m.metric_state(root)
            for path in root.glob("*-metrics.txt"):
                path.write_text("vllm:num_requests_running NaN\nvllm:num_requests_waiting 0\n")
            with self.assertRaisesRegex(m.MeasurementError, "non-finite"):
                m.metric_state(root)

    def test_late_counter_change_rejects_complete_collection(self):
        stable = {f"engine-{index}-metrics.txt": {
            "vllm:num_requests_running": 0.0,
            "vllm:num_requests_waiting": 0.0,
            "vllm:nixl_bytes_transferred_count": 12.0,
        } for index in range(3)}
        with tempfile.TemporaryDirectory() as temporary:
            subject = self.runner(temporary)
            subject.poll_metrics = lambda destination: (destination.mkdir() or stable)
            def collect(destination, benchmark_pod=None):
                destination.mkdir()
                for index in range(3):
                    (destination / f"engine-{index}-metrics.txt").write_text(
                        "vllm:num_requests_running 0\n"
                        "vllm:num_requests_waiting 0\n"
                        "vllm:nixl_bytes_transferred_count 13\n")
                return subprocess.CompletedProcess([], 0, "", "")
            subject.collect = collect
            arm = Path(temporary) / "arm"; arm.mkdir()
            with self.assertRaisesRegex(m.MeasurementError, "late request or transfer-counter change"):
                subject.settled_snapshot(arm, "before")


if __name__ == "__main__":
    unittest.main()
