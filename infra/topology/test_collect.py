"""Regression tests for selecting logs from a changing Pod inventory."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "topology_collect", Path(__file__).with_name("collect.py")
)
collect = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collect)


def report_archive():
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, data in {
            "exit-code": b"0\n",
            "per_request_lifecycle_metrics.json": b"[]\n",
        }.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return stream.getvalue()


class FakeKubectl:
    def __init__(self, failed_log=None):
        self.failed_log = failed_log
        self.calls = []
        self.pods = {
            "items": [
                {"metadata": {"name": "old-benchmark"},
                 "spec": {"containers": [{"name": "benchmark", "image": "benchmark"}]}},
                {"metadata": {"name": "current-benchmark",
                              "labels": {"app.kubernetes.io/name": "topology-benchmark"}},
                 "spec": {"containers": [{"name": "benchmark", "image": "benchmark"}]}},
                {"metadata": {"name": "engine"},
                 "spec": {"containers": [{"name": "modelserver", "image": "vllm",
                                             "ports": [{"name": "modelserver",
                                                        "containerPort": 8000}]}]}},
                {"metadata": {"name": "epp"},
                 "spec": {"containers": [{"name": "epp",
                                             "image": "llm-d-router-endpoint-picker:test"}]}},
            ]
        }

    def __call__(self, command, **_kwargs):
        self.calls.append(command)
        if "pods" in command and "get" in command:
            return subprocess.CompletedProcess(command, 0, json.dumps(self.pods).encode(), b"")
        if "logs" in command:
            pod = command[command.index("logs") + 1]
            container = command[command.index("-c") + 1]
            key = (pod, container)
            return subprocess.CompletedProcess(
                command, int(key == self.failed_log), b"log\n",
                b"pod disappeared\n" if key == self.failed_log else b"")
        if "--raw" in command:
            return subprocess.CompletedProcess(
                command, 0, b"vllm:num_requests_running 0\n", b"")
        if "exec" in command and "tar" in command:
            return subprocess.CompletedProcess(command, 0, report_archive(), b"")
        if "exec" in command:
            return subprocess.CompletedProcess(command, 0, b'{"gpu_uuids":["GPU-test"]}\n', b"")
        return subprocess.CompletedProcess(command, 0, b'{"items":[]}\n', b"")


class CollectLogSelectionTest(unittest.TestCase):
    def run_collect(self, root, fake):
        out = root / "collection"
        argv = ["collect.py", "--context", "mock", "--benchmark-pod",
                "current-benchmark", "--out", str(out)]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(
                collect.subprocess, "run", side_effect=fake):
            collect.main()
        return out

    def test_ignores_stale_benchmark_but_collects_requested_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeKubectl(failed_log=("old-benchmark", "benchmark"))
            out = self.run_collect(Path(tmp), fake)
            log_targets = [(c[c.index("logs") + 1], c[c.index("-c") + 1])
                           for c in fake.calls if "logs" in c]
            self.assertNotIn(("old-benchmark", "benchmark"), log_targets)
            self.assertIn(("current-benchmark", "benchmark"), log_targets)
            self.assertTrue(json.loads((out / "collection.json").read_text())["collection_complete"])

    def test_requested_benchmark_log_failure_is_not_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeKubectl(failed_log=("current-benchmark", "benchmark"))
            with self.assertRaisesRegex(SystemExit, "current-benchmark-benchmark.log"):
                self.run_collect(Path(tmp), fake)

    def test_required_serving_log_failure_is_not_ignored(self):
        for failed_log in (("engine", "modelserver"), ("epp", "epp")):
            with self.subTest(failed_log=failed_log), tempfile.TemporaryDirectory() as tmp:
                fake = FakeKubectl(failed_log=failed_log)
                with self.assertRaisesRegex(SystemExit, f"{failed_log[0]}-{failed_log[1]}.log"):
                    self.run_collect(Path(tmp), fake)


if __name__ == "__main__":
    unittest.main()
