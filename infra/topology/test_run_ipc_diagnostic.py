#!/usr/bin/env python3
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import time
import types
import unittest
from unittest.mock import patch


PATH = Path(__file__).with_name("run-ipc-diagnostic.py")
SPEC = importlib.util.spec_from_file_location("run_ipc_diagnostic", PATH)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class FakeProcess:
    def __init__(self, command, events):
        self.command, self.events, self.returncode = command, events, 1

    def communicate(self, timeout=None):
        self.events.append("producer-communicate")
        return "producer stdout", "producer stderr"

    def kill(self):
        self.events.append("producer-kill")


class OfflineTests(unittest.TestCase):
    def test_manifest_resource_and_namespace_contract(self):
        common, pods = runner.manifests("h100-node", "deadbeef")
        self.assertEqual([item["kind"] for item in common], ["Namespace", "ConfigMap"])
        self.assertTrue(common[1]["immutable"])
        self.assertEqual([pods[key]["spec"]["containers"][0]["resources"]["requests"]["nvidia.com/gpu"]
                          for key in ("control1", "producer", "consumer", "control2")], ["8", "1", "1", "8"])
        for value in pods.values():
            self.assertNotIn("nodeName", value["spec"])
            self.assertEqual(value["spec"]["nodeSelector"], {"kubernetes.io/hostname": "h100-node"})
            self.assertTrue(value["spec"]["hostIPC"])
            self.assertTrue(value["spec"]["hostPID"])
            self.assertEqual(value["spec"]["volumes"][1]["hostPath"]["path"], "/dev/shm")
            self.assertNotIn("securityContext", value["spec"]["containers"][0])

    def test_probe_command_keeps_namespace_and_bounds(self):
        producer = runner.probe_exec_args("p", "producer", 1, "/tmp/s", "a", "/tmp/r")
        consumer = runner.probe_exec_args("c", "consumer", 0, "/tmp/s", "a", "/tmp/r", "GPU-one")
        self.assertEqual(producer[:5], ["-n", runner.NAMESPACE, "exec", "p", "--"])
        self.assertEqual(producer[5:9], ["timeout", "-k", "5", "100"])
        self.assertEqual(producer[-1], "90")
        self.assertEqual(consumer[5:8], ["env", "CUDA_VISIBLE_DEVICES=GPU-one", "timeout"])
        self.assertEqual(consumer[8:11], ["-k", "5", "40"])
        self.assertEqual(consumer[-1], "35")

    def test_render_only_never_invokes_subprocess(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            runner.write_json(run_dir / "session.json", {"session_id": "dry", "profile": "ipc-diagnostic",
                              "cleanup_start_deadline_unix": time.time() + 1200})
            with patch.object(runner.subprocess, "run", side_effect=AssertionError("subprocess invoked")), \
                 contextlib.redirect_stdout(io.StringIO()):
                code = runner.main(["--context", "unused", "--node", "h100-node", "--run-dir", str(run_dir)])
            self.assertEqual(code, 0)
            self.assertEqual(len(json.loads((run_dir / "ipc-diagnostic-manifests.json").read_text())), 6)

    def test_missing_ack_stops_consumer_before_producer_wait(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = types.SimpleNamespace(context="ctx", node="node", run_dir=Path(temporary))
            session = {"session_id": "session", "cleanup_start_deadline_unix": time.time() + 1200}
            common, pods = runner.manifests("node", "deadbeef")
            subject = runner.Runner(args, session, (common, pods))
            events = []

            def remote_json(pod, path, timeout=8):
                if path.endswith(".metadata.json"):
                    return {"session_id": "session"}
                if path.endswith(".consumer.json"):
                    return {"ok": False, "errors": [{"error": "CUDA failure"}]}
                if path.endswith(".producer.json"):
                    return {"ok": False, "errors": [{"error": "no acknowledgement"}]}
                return None

            subject.remote_json = remote_json
            subject.copy_json = lambda *args, **kwargs: None
            subject.kubectl = lambda arguments, **kwargs: subprocess.CompletedProcess(arguments, 1, "", "failed")
            subject.stop_role = lambda pod, arm, role: (events.append("stop-" + role) or
                                                         {"returncode": 0, "confirmed_stopped": True, "stderr": ""})
            def fake_popen(command, **kwargs):
                self.assertEqual(command[:4], ["kubectl", "--context", "ctx", "-n"])
                return FakeProcess(command, events)

            with patch.object(runner.subprocess, "Popen", side_effect=fake_popen), \
                 contextlib.redirect_stdout(io.StringIO()):
                result = subject.run_case("failure", "producer", "consumer", 0, 0)
            self.assertEqual(result["status"], "FAIL")
            self.assertLess(events.index("stop-consumer"), events.index("producer-communicate"))


if __name__ == "__main__":
    unittest.main()
