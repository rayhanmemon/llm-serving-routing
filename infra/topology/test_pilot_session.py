"""Mocked lifecycle tests; no Terraform or cloud command is run."""

import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("pilot-session.py")
SPEC = importlib.util.spec_from_file_location("pilot_session", SCRIPT)
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)


class Clock:
    def __init__(self, value):
        self.value = value

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class PilotSessionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.plan = self.root / "pilot.tfplan"
        self.plan.write_text("reviewed test plan")
        self.approval = self.root / "approval.json"
        self.approval.write_text(json.dumps({
            "approved": True,
            "approval_reference": "test approval",
            "approved_max_usd_pretax": "50",
            "purchase_type": "preemptible",
        }))
        self.args = types.SimpleNamespace(
            execute=True,
            plan=self.plan,
            approval_record=self.approval,
            approved_max_usd_pretax="50",
            purchase_type="preemptible",
        )

    def test_duplicate_launch_is_permanently_rejected(self):
        pilot.prepare_session(self.args, self.root / "state", now=100)
        with self.assertRaisesRegex(pilot.SessionError, "already recorded"):
            pilot.prepare_session(self.args, self.root / "state", now=101)
        self.assertEqual(len(list((self.root / "state/runs").iterdir())), 1)

    def test_guard_waits_until_deadline_then_starts_cleanup(self):
        run_dir, session = pilot.prepare_session(self.args, self.root / "state", now=100)
        session["cleanup_start_deadline_unix"] = 110
        pilot.write_json(run_dir / "session.json", session)
        clock = Clock(100)
        calls = []

        result = pilot.guard_run(
            run_dir,
            now_fn=clock.now,
            sleep_fn=clock.sleep,
            cleanup_fn=lambda path: calls.append((path, clock.now())) or 0,
        )

        self.assertEqual(result, 0)
        self.assertEqual(calls, [(run_dir, 110)])

    def test_failed_apply_starts_targeted_cleanup(self):
        run_dir, session = pilot.prepare_session(self.args, self.root / "state", now=100)
        process = mock.Mock()
        process.wait.return_value = 1
        cleanup = mock.Mock(return_value=0)

        result = pilot.apply_plan(
            run_dir,
            session,
            popen_factory=mock.Mock(return_value=process),
            cleanup_fn=cleanup,
        )

        self.assertEqual(result, 1)
        cleanup.assert_called_once_with(run_dir)
        self.assertEqual(json.loads((run_dir / "apply-result.json").read_text())["exit_code"], 1)

    def test_cleanup_timeout_terminates_the_whole_process_group(self):
        process = mock.Mock(pid=4321)
        process.wait.side_effect = [
            pilot.subprocess.TimeoutExpired("teardown", 10),
            pilot.subprocess.TimeoutExpired("teardown", 10),
            -9,
        ]
        kill_group = mock.Mock()
        with (self.root / "cleanup.log").open("w") as log:
            result = pilot.run_teardown_once(
                log,
                10,
                popen_factory=mock.Mock(return_value=process),
                killpg_fn=kill_group,
            )
        self.assertEqual(result, 124)
        self.assertEqual(kill_group.call_args_list, [
            mock.call(4321, pilot.signal.SIGTERM),
            mock.call(4321, pilot.signal.SIGKILL),
        ])

    def test_budget_comparison_is_numeric_and_rejects_non_finite(self):
        self.args.approved_max_usd_pretax = "50.0"
        pilot.prepare_session(self.args, self.root / "numeric", now=100)
        self.args.approved_max_usd_pretax = "NaN"
        with self.assertRaisesRegex(pilot.SessionError, "positive and finite"):
            pilot.prepare_session(self.args, self.root / "invalid", now=100)

    def test_overdue_cleanup_keeps_retrying_until_verified(self):
        run_dir, session = pilot.prepare_session(self.args, self.root / "state", now=100)
        session["deletion_target_unix"] = 99
        pilot.write_json(run_dir / "session.json", session)
        clock = Clock(100)
        outcomes = iter((1, 0))

        result = pilot.cleanup_until_target(
            run_dir,
            now_fn=clock.now,
            sleep_fn=clock.sleep,
            run_teardown_fn=lambda log, timeout: next(outcomes),
        )

        self.assertEqual(result, 0)
        self.assertEqual(clock.now(), 160)
        self.assertTrue((run_dir / "cleanup-verified.json").exists())
        self.assertFalse((run_dir / "cleanup-overdue.json").exists())


if __name__ == "__main__":
    unittest.main()
