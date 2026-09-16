"""CPU-only protocol/ABI/error-path tests; these do not establish CUDA support."""
import base64
import contextlib
import ctypes as C
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("raw_ipc", Path(__file__).with_name("raw-ipc.py"))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class FakeDriver:
    fail = None
    def __init__(self, path, result):
        self.result = result

    def call(self, name, *args):
        self.result["stage"] = name
        self.result["calls"].append({"api": name, "code": 1 if name == self.fail else 0})
        if name == self.fail:
            raise RuntimeError("injected CUDA failure")
        if name == "cuDriverGetVersion":
            args[0]._obj.value = 13000
        elif name == "cuDeviceGetCount":
            args[0]._obj.value = 2
        elif name == "cuDeviceGet":
            args[0]._obj.value = args[1]
        elif name == "cuDeviceGetUuid_v2":
            args[0]._obj.bytes[15] = args[1].value
        elif name in ("cuDevicePrimaryCtxRetain", "cuMemAlloc_v2", "cuIpcOpenMemHandle"):
            args[0]._obj.value = 4096
        elif name == "cuMemcpyDtoH_v2":
            C.memset(args[0], 73, args[2])


class ProbeTests(unittest.TestCase):
    def packet(self, session="test", size=16):
        raw = bytes(range(64))
        return {"session_id": session, "bytes": size, "fill_byte": 73,
                "handle_base64": base64.b64encode(raw).decode(),
                "handle_sha256": hashlib.sha256(raw).hexdigest(),
                "producer_gpu_uuid": "GPU-source"}

    def test_handle_is_64_bytes_passed_by_value(self):
        self.assertEqual(C.sizeof(probe.Handle), 64)
        self.assertIs(probe.SIGNATURES["cuIpcOpenMemHandle"][1], probe.Handle)
        self.assertEqual(bytes(probe.decode_handle(self.packet(), "test", 16)), bytes(range(64)))

    def test_packet_rejects_stale_corrupt_and_wrong_size(self):
        for change in ({"session_id": "stale"}, {"bytes": 1}, {"handle_sha256": "bad"},
                       {"handle_base64": base64.b64encode(b"short").decode()}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                probe.decode_handle(self.packet() | change, "test", 16)

    def test_help_and_timeout_validation_load_no_driver(self):
        with patch.object(probe.C, "CDLL", side_effect=AssertionError("must not load")):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as caught:
                probe.parse_args(["--help"])
            self.assertEqual(caught.exception.code, 0)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                probe.parse_args(["producer", "--device", "0", "--stem", "s", "--session-id", "x",
                                  "--result", "r", "--timeout", "91"])

    def run_fake(self, role="consumer", fail=None):
        with tempfile.TemporaryDirectory() as tmp:
            stem, result = str(Path(tmp) / "case"), str(Path(tmp) / "result.json")
            probe.atomic_json(stem + ".metadata.json", self.packet())
            args = probe.parse_args([role, "--device", "0", "--stem", stem, "--session-id", "test",
                                     "--result", result, "--bytes", "16"])
            with patch.object(probe, "Driver", FakeDriver), patch.object(FakeDriver, "fail", fail), contextlib.redirect_stdout(io.StringIO()):
                code = probe.run(args)
            done = Path(stem + ".done.json")
            return code, json.loads(Path(result).read_text()), json.loads(done.read_text()) if done.exists() else None

    def test_consumer_verifies_and_closes_before_success_ack(self):
        code, result, done = self.run_fake()
        self.assertEqual(code, 0)
        self.assertTrue(result["verification"]["all_bytes_equal_73"])
        self.assertEqual(result["verification"]["checksum_sum"], 73 * 16)
        names = [call["api"] for call in result["calls"]]
        self.assertLess(names.index("cuIpcCloseMemHandle"), names.index("cuMemFree_v2"))
        self.assertEqual(done, {"session_id": "test", "ok": True, "import_closed": True})

    def test_close_failure_never_sends_safe_ack(self):
        code, result, done = self.run_fake(fail="cuIpcCloseMemHandle")
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])
        self.assertFalse(result["import_closed"])
        self.assertIsNone(done)

    def test_free_failure_cannot_report_success(self):
        code, result, done = self.run_fake(fail="cuMemFree_v2")
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])
        self.assertFalse(done["ok"])
        self.assertTrue(done["import_closed"])

    def test_open_failure_ack_has_no_active_import(self):
        code, result, done = self.run_fake(fail="cuIpcOpenMemHandle")
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])
        self.assertTrue(done["import_closed"])
        self.assertNotIn("cuIpcCloseMemHandle", [call["api"] for call in result["calls"]])

    def test_exporter_free_requires_consumer_closure(self):
        for ack in ({"session_id": "test", "ok": True, "import_closed": True},
                    TimeoutError("consumer did not finish")):
            with self.subTest(ack=str(ack)), tempfile.TemporaryDirectory() as tmp:
                stem, path = str(Path(tmp) / "case"), str(Path(tmp) / "result.json")
                args = probe.parse_args(["producer", "--device", "0", "--stem", stem,
                                         "--session-id", "test", "--result", path, "--bytes", "16"])
                def acknowledge(*unused):
                    # Allocation must still be live when the exporter waits.
                    self.assertTrue(Path(stem + ".metadata.json").exists())
                    if isinstance(ack, Exception):
                        raise ack
                    return ack
                with patch.object(probe, "Driver", FakeDriver), patch.object(probe, "wait_packet", acknowledge), contextlib.redirect_stdout(io.StringIO()):
                    code = probe.run(args)
                result = json.loads(Path(path).read_text())
                names = [call["api"] for call in result["calls"]]
                if isinstance(ack, Exception):
                    self.assertEqual(code, 1)
                    self.assertNotIn("cuMemFree_v2", names)
                    self.assertNotIn("cuDevicePrimaryCtxRelease_v2", names)
                else:
                    self.assertEqual(code, 0)
                    self.assertIn("cuMemFree_v2", names)

    def test_inspect_never_allocates_or_retains_context(self):
        code, result, done = self.run_fake(role="inspect")
        self.assertEqual(code, 0)
        self.assertEqual(len(result["visible_gpus"]), 2)
        names = [call["api"] for call in result["calls"]]
        self.assertNotIn("cuMemAlloc_v2", names)
        self.assertNotIn("cuDevicePrimaryCtxRetain", names)
        self.assertIsNone(done)


if __name__ == "__main__":
    unittest.main()
