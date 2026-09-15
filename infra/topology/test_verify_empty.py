"""Exercise all cleanup listings without accessing or modifying cloud resources."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("verify-empty.sh")
KINDS = [
    "compute instance list",
    "mk8s cluster list",
    "compute disk list",
    "compute filesystem list",
    "compute gpu-cluster list",
]


class VerifyEmptyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.mock = self.root / "nebius"
        self.calls = self.root / "calls.jsonl"
        self.mock.write_text(
            "#!/usr/bin/env python3\n"
            "import json,os,sys\n"
            "args=sys.argv[1:]\n"
            "with open(os.environ['CALLS'],'a') as f:\n"
            "    f.write(json.dumps(args)+'\\n')\n"
            "kind=' '.join(args[:3])\n"
            "replies=json.loads(os.environ['REPLIES'])\n"
            "reply=replies.get(kind,replies.get('*',{'body':'{}','exit':0}))\n"
            "print(reply['body'])\n"
            "sys.exit(reply['exit'])\n"
        )
        self.mock.chmod(0o755)

    def run_checker(self, data=None, code=0, kind="*", **overrides):
        if self.calls.exists():
            self.calls.unlink()
        env = {
            **os.environ,
            "NEBIUS": str(self.mock),
            "PROJECT_ID": "test-project",
            "CALLS": str(self.calls),
            "REPLIES": json.dumps({kind: {
                "body": data if isinstance(data, str) else json.dumps(data),
                "exit": code,
            }}),
            **overrides,
        }
        return subprocess.run(
            ["bash", str(SCRIPT)], env=env, capture_output=True, text=True
        )

    def assert_result(self, result, expected):
        self.assertEqual(result.returncode == 0, expected, result.stdout + result.stderr)
        self.assertEqual("PASS —" in result.stdout, expected)

    def assert_all_listings(self):
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual([" ".join(args[:3]) for args in calls], KINDS)
        for args in calls:
            self.assertEqual(args[3:], [
                "--parent-id", "test-project", "--format", "json", "--all",
                "--timeout", "20s", "--no-check-update",
            ])

    def test_listing_outcomes(self):
        cases = [
            ("empty", {"items": []}, 0, True),
            ("protobuf-empty", {}, 0, True),
            ("nonempty", {"items": [{"metadata": {"name": "remaining"}}]}, 0, False),
            ("api-error", {}, 1, False),
            ("error-with-empty-items", {"items": []}, 1, False),
            ("missing-output", "", 0, False),
            ("malformed", "{", 0, False),
            ("unexpected-object", {"error": "failed"}, 0, False),
            ("missing-items", {"total_size": 0}, 0, False),
            ("null-items", {"items": None}, 0, False),
            ("wrong-items-type", {"items": {}}, 0, False),
            ("unread-page", {"items": [], "next_page_token": "more"}, 0, False),
            ("unread-camelcase-page", {"items": [], "nextPageToken": "more"}, 0, False),
            ("page-without-items", {"next_page_token": "more"}, 0, False),
            ("null-document", None, 0, False),
            ("list-form", [], 0, True),
            ("nonempty-list-form", [{"metadata": {"name": "remaining"}}], 0, False),
        ]
        for name, data, code, expected in cases:
            with self.subTest(name=name):
                self.assert_result(self.run_checker(data, code), expected)
                self.assert_all_listings()

    def test_gpu_cluster_is_checked_independently(self):
        cases = [
            ("leftover", {"items": [{"metadata": {"name": "router-local-h100"}}]}, 0),
            ("api-error", {}, 1),
            ("missing-output", "", 0),
            ("invalid-output", {"items": None}, 0),
            ("paginated", {"items": [], "next_page_token": "more"}, 0),
        ]
        for name, data, code in cases:
            with self.subTest(name=name):
                self.assert_result(self.run_checker(data, code, "compute gpu-cluster list"), False)
                self.assert_all_listings()

    def test_missing_cli_fails(self):
        self.assert_result(self.run_checker({}, NEBIUS=str(self.root / "missing")), False)
        self.assertFalse(self.calls.exists())

    def test_missing_project_fails_before_listing(self):
        # A copied script has no adjacent ignored tfvars to supply a fallback.
        script = self.root / "verify-empty.sh"
        script.write_text(SCRIPT.read_text())
        result = subprocess.run(
            ["bash", str(script)],
            env={**os.environ, "PROJECT_ID": "", "NEBIUS": str(self.mock)},
            capture_output=True, text=True,
        )
        self.assert_result(result, False)
        self.assertIn("project ID is required", result.stderr)
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
