"""Mocked lifecycle tests; no Terraform or cloud command is run."""

import importlib.util
import json
from decimal import Decimal
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
            "authorization_scope": pilot.AUTHORIZATION_SCOPE,
            "allowed_profiles": [pilot.FULL_TOPOLOGY_PROFILE, pilot.IPC_DIAGNOSTIC_PROFILE],
            "allow_multiple_attempts": True,
            "max_total_usd_pretax": "50",
            "historical_spend_usd_pretax": "0",
            "purchase_type": "preemptible",
        }))
        self.args = types.SimpleNamespace(
            execute=True,
            plan=self.plan,
            approval_record=self.approval,
            approved_max_usd_pretax="50",
            purchase_type="preemptible",
        )
        plan_validator = mock.patch.object(pilot, "validate_terraform_plan")
        self.validate_plan = plan_validator.start()
        self.addCleanup(plan_validator.stop)

    def plan_json(self, diagnostic):
        addresses = set(pilot.PROFILE_POLICIES[
            pilot.IPC_DIAGNOSTIC_PROFILE if diagnostic else pilot.FULL_TOPOLOGY_PROFILE
        ]["expected_creates"])
        changes = []
        for address in sorted(addresses):
            after = {}
            if address == "nebius_mk8s_v1_node_group.local":
                after = {
                    "fixed_node_count": 1,
                    "template": {
                        "resources": {
                            "platform": "gpu-h100-sxm",
                            "preset": "8gpu-128vcpu-1600gb",
                        },
                        "boot_disk": {"type": "NETWORK_SSD", "size_gibibytes": 256},
                        "preemptible": {},
                    },
                }
            changes.append({
                "address": address,
                "change": {"actions": ["create"], "after": after},
            })
        return {
            "variables": {
                "gpu_preemptible": {"value": True},
                "ipc_diagnostic_only": {"value": diagnostic},
            },
            "resource_changes": changes,
        }

    def complete(self, run_dir, verified_unix, estimated_cost):
        session = json.loads((run_dir / "session.json").read_text())
        pilot.write_json(run_dir / "cleanup-verified.json", {
            "session_id": session["session_id"],
            "verified_unix": verified_unix,
            "teardown_exit_code": 0,
        })
        pilot.write_json(run_dir / "cost-estimate.json", {
            "session_id": session["session_id"],
            "estimate_usd_pretax": str(estimated_cost),
        })
        pilot.mark_attempt_cleanup_verified(run_dir, verified_unix)

    def test_overlapping_launch_is_rejected(self):
        pilot.prepare_session(self.args, self.root / "state", now=100)
        with self.assertRaisesRegex(pilot.SessionError, "still active; overlapping launch rejected"):
            pilot.prepare_session(self.args, self.root / "state", now=101)
        self.assertEqual(len(list((self.root / "state/runs").iterdir())), 1)

    def test_prior_cleanup_must_be_verified(self):
        run_dir, _ = pilot.prepare_session(self.args, self.root / "state", now=100)
        budget_path = self.root / "state/budget.json"
        budget = json.loads(budget_path.read_text())
        budget["attempts"][0].update({
            "status": "completed",
            "estimated_cost_usd_pretax": "1",
        })
        pilot.write_json(budget_path, budget)
        with self.assertRaisesRegex(pilot.SessionError, "cleanup is not verified"):
            pilot.prepare_session(self.args, self.root / "state", now=101)

    def test_prior_attempt_without_recorded_cost_fails_closed(self):
        run_dir, session = pilot.prepare_session(self.args, self.root / "state", now=100)
        pilot.write_json(run_dir / "cleanup-verified.json", {
            "session_id": session["session_id"],
            "verified_unix": 110,
            "teardown_exit_code": 0,
        })
        budget_path = self.root / "state/budget.json"
        budget = json.loads(budget_path.read_text())
        budget["attempts"][0].update({
            "status": "cleanup_verified_cost_pending",
            "cleanup_verified_unix": 110,
        })
        pilot.write_json(budget_path, budget)
        with self.assertRaisesRegex(pilot.SessionError, "no recorded cost estimate"):
            pilot.prepare_session(self.args, self.root / "state", now=111)

    def test_cumulative_budget_requires_one_full_run_and_cleanup_reserve(self):
        first, _ = pilot.prepare_session(self.args, self.root / "state", now=100)
        five_dollar_seconds = float(Decimal("5") * 3600 / pilot.FULLY_RUNNING_HOURLY_USD)
        self.complete(first, 100 + five_dollar_seconds, "5")
        second, _ = pilot.prepare_session(self.args, self.root / "state", now=1000)
        one_dollar_seconds = float(Decimal("1") * 3600 / pilot.FULLY_RUNNING_HOURLY_USD)
        self.complete(second, 1000 + one_dollar_seconds, "1")
        with self.assertRaisesRegex(pilot.SessionError, "below the .* attempt admission"):
            pilot.prepare_session(self.args, self.root / "state", now=2000)

    def test_low_cost_failed_placement_allows_another_attempt(self):
        first, _ = pilot.prepare_session(self.args, self.root / "state", now=100)
        self.complete(first, 130, "0.01")

        _, second = pilot.prepare_session(self.args, self.root / "state", now=200)

        self.assertEqual(second["budget_snapshot"]["completed_spend_usd_pretax"], "0.01")
        self.assertEqual(second["budget_snapshot"]["remaining_before_attempt_usd_pretax"], "49.99")

    def test_legacy_attempt_is_preserved_and_seeded_from_exact_cost(self):
        state = self.root / "state"
        legacy_run = state / "runs/legacy"
        legacy_run.mkdir(parents=True)
        legacy = {
            "session_id": "legacy",
            "run_dir": str(legacy_run),
            "status": "closed; cleanup verified",
        }
        pilot.write_json(state / "attempt.json", legacy)
        original = (state / "attempt.json").read_bytes()
        pilot.write_json(legacy_run / "cleanup-verified.json", {
            "session_id": "legacy",
            "verified_unix": 99,
            "teardown_exit_code": 0,
        })
        exact = "0.5009834534903475"
        pilot.write_json(legacy_run / "cost-estimate.json", {"estimate_usd_pretax": exact})
        approval = json.loads(self.approval.read_text())
        approval["historical_spend_usd_pretax"] = exact
        self.approval.write_text(json.dumps(approval))

        run_dir, _ = pilot.prepare_session(self.args, state, now=100)

        self.assertEqual((state / "attempt.json").read_bytes(), original)
        budget = json.loads((state / "budget.json").read_text())
        self.assertEqual(budget["attempts"][0]["estimated_cost_usd_pretax"], exact)
        session = json.loads((run_dir / "session.json").read_text())
        self.assertEqual(session["budget_snapshot"]["completed_spend_usd_pretax"], exact)

    def test_legacy_cli_defaults_to_full_topology_deadlines(self):
        _, session = pilot.prepare_session(self.args, self.root / "state", now=100)

        self.assertEqual(session["profile"], pilot.FULL_TOPOLOGY_PROFILE)
        self.assertEqual(session["placement_deadline_unix"], 100 + 30 * 60)
        self.assertEqual(session["cleanup_start_deadline_unix"], 100 + 90 * 60)
        self.assertEqual(session["deletion_target_unix"], 100 + 120 * 60)
        self.assertEqual(
            session["budget_snapshot"]["attempt_admission_usd_pretax"],
            str(pilot.ATTEMPT_ADMISSION_USD),
        )

    def test_ipc_diagnostic_uses_bounded_deadlines_rate_and_admission(self):
        self.args.profile = pilot.IPC_DIAGNOSTIC_PROFILE
        _, session = pilot.prepare_session(self.args, self.root / "state", now=100)

        self.assertEqual(session["placement_deadline_unix"], 100 + 15 * 60)
        self.assertEqual(session["cleanup_start_deadline_unix"], 100 + 30 * 60)
        self.assertEqual(session["deletion_target_unix"], 100 + 40 * 60)
        self.assertEqual(session["hourly_rate_usd_pretax"], "17.225")
        self.assertEqual(session["budget_snapshot"]["attempt_admission_usd_pretax"], "13")
        forty_minute_cost = Decimal("40") * Decimal("17.225") / Decimal("60")
        self.assertEqual(forty_minute_cost, Decimal("11.48333333333333333333333333"))
        self.assertGreater(Decimal("13") - forty_minute_cost, Decimal("1.51"))

    def test_ipc_diagnostic_is_admitted_with_thirty_dollars_remaining_and_repairs_cache(self):
        first, _ = pilot.prepare_session(self.args, self.root / "state", now=100)
        exact_spend = Decimal("19.935387814552864")
        elapsed = float(exact_spend * 3600 / pilot.FULLY_RUNNING_HOURLY_USD)
        self.complete(first, 100 + elapsed, str(exact_spend))
        budget_path = self.root / "state/budget.json"
        budget = json.loads(budget_path.read_text())
        budget["completed_spend_usd_pretax"] = "0.5"
        budget["remaining_usd_pretax"] = "49.5"
        pilot.write_json(budget_path, budget)
        self.args.profile = pilot.IPC_DIAGNOSTIC_PROFILE

        _, session = pilot.prepare_session(self.args, self.root / "state", now=5000)

        remaining = Decimal("50") - exact_spend
        self.assertEqual(
            session["budget_snapshot"]["completed_spend_usd_pretax"], str(exact_spend)
        )
        self.assertEqual(
            session["budget_snapshot"]["remaining_before_attempt_usd_pretax"], str(remaining)
        )
        repaired = json.loads(budget_path.read_text())
        self.assertEqual(repaired["completed_spend_usd_pretax"], str(exact_spend))
        self.assertEqual(repaired["remaining_usd_pretax"], str(remaining))

    def test_ipc_diagnostic_requires_explicit_profile_approval(self):
        approval = json.loads(self.approval.read_text())
        approval["allowed_profiles"] = [pilot.FULL_TOPOLOGY_PROFILE]
        self.approval.write_text(json.dumps(approval))
        self.args.profile = pilot.IPC_DIAGNOSTIC_PROFILE

        with self.assertRaisesRegex(pilot.SessionError, "does not allow profile ipc-diagnostic"):
            pilot.prepare_session(self.args, self.root / "state", now=100)

    def test_plan_validation_accepts_exact_diagnostic_shape(self):
        pilot.validate_plan_structure(self.plan_json(diagnostic=True), pilot.IPC_DIAGNOSTIC_PROFILE)

    def test_plan_validation_rejects_profile_mismatch(self):
        with self.assertRaisesRegex(pilot.SessionError, "does not match profile full-topology"):
            pilot.validate_plan_structure(self.plan_json(diagnostic=True), pilot.FULL_TOPOLOGY_PROFILE)

    def test_plan_validation_rejects_non_preemptible_local_node(self):
        plan = self.plan_json(diagnostic=True)
        plan["variables"]["gpu_preemptible"]["value"] = False

        with self.assertRaisesRegex(pilot.SessionError, "must use preemptible"):
            pilot.validate_plan_structure(plan, pilot.IPC_DIAGNOSTIC_PROFILE)

    def test_plan_validation_rejects_extra_noop_state(self):
        plan = self.plan_json(diagnostic=True)
        plan["resource_changes"].append({
            "address": "nebius_mk8s_v1_node_group.unrelated",
            "change": {"actions": ["no-op"], "after": {}},
        })

        with self.assertRaisesRegex(pilot.SessionError, "contains non-create changes"):
            pilot.validate_plan_structure(plan, pilot.IPC_DIAGNOSTIC_PROFILE)

    def test_diagnostic_cost_upper_bound_uses_profile_rate(self):
        run_dir = self.root / "run"
        run_dir.mkdir()
        session = {
            "session_id": "diagnostic",
            "profile": pilot.IPC_DIAGNOSTIC_PROFILE,
            "hourly_rate_usd_pretax": "17.225",
            "started_unix": 100,
        }
        pilot.write_json(run_dir / "cost-estimate.json", {
            "session_id": "diagnostic",
            "estimate_usd_pretax": "17.24",
        })

        with self.assertRaisesRegex(pilot.SessionError, "exceeds the full-rate"):
            pilot.validated_attempt_cost(run_dir, session, 3700)

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

    def test_diagnostic_cleanup_passes_matching_terraform_profile(self):
        process = mock.Mock(pid=4321)
        process.wait.return_value = 0
        popen = mock.Mock(return_value=process)
        with (self.root / "cleanup.log").open("w") as log:
            result = pilot.run_teardown_once(
                log,
                10,
                profile=pilot.IPC_DIAGNOSTIC_PROFILE,
                popen_factory=popen,
            )

        self.assertEqual(result, 0)
        self.assertEqual(popen.call_args.kwargs["env"]["TF_VAR_ipc_diagnostic_only"], "true")

    def test_cleanup_uses_profile_from_session_record(self):
        run_dir = self.root / "diagnostic-cleanup"
        run_dir.mkdir()
        pilot.write_json(run_dir / "session.json", {
            "session_id": "diagnostic",
            "profile": pilot.IPC_DIAGNOSTIC_PROFILE,
            "deletion_target_unix": 100,
        })
        teardown = mock.Mock(return_value=0)

        with mock.patch.object(pilot, "run_teardown_once", teardown):
            result = pilot.cleanup_until_target(run_dir, now_fn=lambda: 100)

        self.assertEqual(result, 0)
        self.assertEqual(
            teardown.call_args.kwargs["profile"], pilot.IPC_DIAGNOSTIC_PROFILE
        )

    def test_legacy_cleanup_passes_full_topology_terraform_profile(self):
        process = mock.Mock(pid=4321)
        process.wait.return_value = 0
        popen = mock.Mock(return_value=process)
        with (self.root / "cleanup.log").open("w") as log:
            result = pilot.run_teardown_once(log, 10, popen_factory=popen)

        self.assertEqual(result, 0)
        self.assertEqual(popen.call_args.kwargs["env"]["TF_VAR_ipc_diagnostic_only"], "false")

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
