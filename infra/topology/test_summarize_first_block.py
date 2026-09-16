"""Offline tests for the first-block summarizer."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parent))
spec = importlib.util.spec_from_file_location("summarizer", Path(__file__).with_name("summarize-first-block.py"))
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)


class SummarizerTest(unittest.TestCase):
    def collection(self, folder, measured=False):
        folder.mkdir(parents=True)
        files = []
        if measured:
            reports = folder / "reports"; reports.mkdir()
            exit_file = reports / "exit-code"; exit_file.write_text("0\n"); files.append(exit_file)
        manifest = {"collection_complete": True, "errors": [], "sha256": {}}
        for path in files:
            manifest["sha256"][str(path.relative_to(folder))] = hashlib.sha256(path.read_bytes()).hexdigest()
        if not files:
            sentinel = folder / "pods.json"; sentinel.write_text('{"items":[]}\n'); files.append(sentinel)
            manifest["sha256"]["pods.json"] = hashlib.sha256(sentinel.read_bytes()).hexdigest()
        (folder / "collection.json").write_text(json.dumps(manifest))

    def fixture(self, temporary):
        root = Path(temporary); attempt = root / "measurement-attempts" / "attempt"
        session_id = "session"
        (root / "session.json").write_text(json.dumps({"session_id": session_id}))
        labels = [arm[0] for arm in s.ARMS]
        run_ids = ["run-" + str(index) for index in range(4)]
        marker = {"session_id": session_id, "validated": True, "arms": labels,
                  "run_ids": run_ids, "attempt_dir": str(attempt)}
        (root / "first-timing-block-complete.json").write_text(json.dumps(marker))
        hashes = [f"hash-{index}" for index in range(12)]
        for index, ((label, _, _), run_id) in enumerate(zip(s.ARMS, run_ids)):
            arm = attempt / label
            self.collection(arm / "before")
            self.collection(arm / "after", measured=True)
            ttfts = [0.1 + sample / 1000 + index / 100 for sample in range(12)]
            validation = {"run_id": run_id, "request_hashes": hashes,
                          "client_ttft_seconds": ttfts,
                          "request_route_and_identity_checks_passed": True}
            transfer = {"bytes": 1200, "operations": 12, "seconds": 0.12,
                        "timed_operations": 12, "effective_bytes_per_second": 10000,
                        "mean_observed_transfer_seconds": 0.01,
                        "failure_observations": {"failed": 0, "expired": 0, "notification": 0}}
            (arm / "after" / "validation.json").write_text(json.dumps(validation))
            (arm / "after" / "transfer-delta.json").write_text(json.dumps(transfer))
        return root, attempt

    def test_complete_report_has_all_samples_and_no_overclaim(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _ = self.fixture(temporary)
            summary = s.summarize(root)
            self.assertEqual(summary["measured_request_count"], 48)
            self.assertEqual(len(summary["paired_samples"]), 24)
            self.assertTrue(summary["warmups_excluded"])
            markdown = s.markdown_text(summary)
            self.assertIn("do not establish p99", markdown)
            self.assertIn("physical-link bandwidth", markdown)
            self.assertEqual(len(s.arm_csv_text(summary).splitlines()), 5)
            self.assertEqual(len(s.csv_text(summary).splitlines()), 25)

    def test_wrong_paired_payload_hashes_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, attempt = self.fixture(temporary)
            path = attempt / "short-remote-b1" / "after" / "validation.json"
            value = json.loads(path.read_text()); value["request_hashes"][0] = "different"
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(s.SummaryError, "payload hashes differ"):
                s.summarize(root)

    def test_missing_completion_marker_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / "session.json").write_text('{"session_id":"s"}')
            with self.assertRaisesRegex(s.SummaryError, "cannot read valid JSON"):
                s.summarize(root)

    def test_invalid_number_writes_no_partial_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, attempt = self.fixture(temporary)
            path = attempt / "long-local-b1" / "after" / "validation.json"
            value = json.loads(path.read_text()); value["client_ttft_seconds"][3] = float("nan")
            path.write_text(json.dumps(value))
            out = root / "report"
            with self.assertRaisesRegex(s.SummaryError, "must be finite"):
                s.main(["--run-dir", str(root), "--out", str(out)])
            self.assertFalse(out.exists())

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, _ = self.fixture(temporary); out = root / "report"; out.mkdir()
            sentinel = out / "keep"; sentinel.write_text("unchanged")
            with self.assertRaisesRegex(s.SummaryError, "already exists"):
                s.write_report(s.summarize(root), out)
            self.assertEqual(sentinel.read_text(), "unchanged")


if __name__ == "__main__":
    unittest.main()
