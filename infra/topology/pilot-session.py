#!/usr/bin/env python3
"""Start one approved H100 pilot apply with an independent teardown deadline."""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid


HERE = Path(__file__).resolve().parent
TERRAFORM_DIR = HERE / "terraform"
TEARDOWN = HERE / "teardown.sh"
STATE_ROOT = Path.home() / ".codex/run-state/router-h100-pilot"
PLACEMENT_TIMEOUT_SECONDS = 30 * 60
CLEANUP_START_SECONDS = 90 * 60
DELETION_TARGET_SECONDS = 120 * 60


class SessionError(RuntimeError):
    pass


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise SessionError(f"Expected a JSON object in {path}")
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def positive_money(value) -> Decimal:
    try:
        money = Decimal(str(value))
    except InvalidOperation as error:
        raise SessionError("Approved budget must be a number") from error
    if not money.is_finite() or money <= 0:
        raise SessionError("Approved budget must be positive and finite")
    return money


def cleanup_verified(run_dir: Path, session_id: str) -> bool:
    marker = run_dir / "cleanup-verified.json"
    try:
        value = read_json(marker)
    except (FileNotFoundError, json.JSONDecodeError, OSError, SessionError):
        return False
    return value.get("session_id") == session_id and value.get("teardown_exit_code") == 0


def prepare_session(args, state_root: Path = STATE_ROOT, now: float | None = None) -> tuple[Path, dict]:
    if not args.execute:
        raise SessionError("Refusing to spend: --execute is required")
    if not args.plan.is_file():
        raise SessionError(f"Terraform plan does not exist: {args.plan}")
    if not args.approval_record.is_file():
        raise SessionError(f"Approval record does not exist: {args.approval_record}")

    approval = read_json(args.approval_record)
    if approval.get("approved") is not True:
        raise SessionError("Approval record must contain approved: true")
    if not approval.get("approval_reference"):
        raise SessionError("Approval record must contain a non-empty approval_reference")
    approved_budget = positive_money(approval.get("approved_max_usd_pretax"))
    cli_budget = positive_money(args.approved_max_usd_pretax)
    if approved_budget != cli_budget:
        raise SessionError("CLI budget does not match the approval record")
    if approval.get("purchase_type") != args.purchase_type:
        raise SessionError("CLI purchase type does not match the approval record")

    started = time.time() if now is None else now
    session_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(started)) + "-" + uuid.uuid4().hex[:8]
    run_dir = state_root / "runs" / session_id
    state_root.mkdir(parents=True, exist_ok=True)

    # This permanent, atomically-created record is the one-attempt gate. A dead
    # process or an old lock file must never turn into permission to relaunch.
    attempt = {
        "session_id": session_id,
        "created_unix": started,
        "run_dir": str(run_dir),
        "status": "reserved",
    }
    attempt_path = state_root / "attempt.json"
    try:
        with attempt_path.open("x") as destination:
            json.dump(attempt, destination, indent=2, sort_keys=True)
            destination.write("\n")
    except FileExistsError as error:
        raise SessionError(
            f"Pilot attempt already recorded in {attempt_path}; automatic relaunch is forbidden"
        ) from error

    run_dir.mkdir(parents=True)

    session = {
        "session_id": session_id,
        "started_unix": started,
        "placement_deadline_unix": started + PLACEMENT_TIMEOUT_SECONDS,
        "cleanup_start_deadline_unix": started + CLEANUP_START_SECONDS,
        "deletion_target_unix": started + DELETION_TARGET_SECONDS,
        "approved_max_usd_pretax": float(cli_budget),
        "purchase_type": args.purchase_type,
        "approval_reference": approval["approval_reference"],
        "approval_record_path": str(args.approval_record.resolve()),
        "approval_record_sha256": file_sha256(args.approval_record),
        "terraform_plan_path": str(args.plan.resolve()),
        "terraform_plan_sha256": file_sha256(args.plan),
        "run_dir": str(run_dir),
    }
    write_json(run_dir / "session.json", session)
    return run_dir, session


def cleanup_until_target(
    run_dir: Path,
    *,
    now_fn=time.time,
    sleep_fn=time.sleep,
    run_teardown_fn=None,
) -> int:
    run_teardown_fn = run_teardown_once if run_teardown_fn is None else run_teardown_fn
    session = read_json(run_dir / "session.json")
    session_id = session["session_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "cleanup.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if cleanup_verified(run_dir, session_id):
            return 0

        attempt_number = 0
        while True:
            attempt_number += 1
            started = now_fn()
            with (run_dir / "cleanup.log").open("a") as log:
                log.write(f"cleanup attempt {attempt_number} started at {started}\n")
                log.flush()
                remaining_before_target = session["deletion_target_unix"] - now_fn()
                timeout = min(10 * 60, max(1, remaining_before_target)) if remaining_before_target > 0 else 10 * 60
                exit_code = run_teardown_fn(log, timeout)
            write_json(run_dir / f"cleanup-attempt-{attempt_number:02d}.json", {
                "attempt": attempt_number,
                "started_unix": started,
                "finished_unix": now_fn(),
                "teardown_exit_code": exit_code,
            })
            if exit_code == 0:
                (run_dir / "cleanup-overdue.json").unlink(missing_ok=True)
                write_json(run_dir / "cleanup-verified.json", {
                    "session_id": session_id,
                    "verified_unix": now_fn(),
                    "teardown_exit_code": 0,
                })
                return 0
            remaining = session["deletion_target_unix"] - now_fn()
            if remaining <= 0:
                write_json(run_dir / "cleanup-overdue.json", {
                    "session_id": session_id,
                    "last_attempt": attempt_number,
                    "last_exit_code": exit_code,
                    "updated_unix": now_fn(),
                    "message": "Deletion is not verified; deletion-only retries continue.",
                })
            sleep_fn(60 if remaining <= 0 else min(60, remaining))


def run_teardown_once(log, timeout_seconds: float, *, popen_factory=subprocess.Popen, killpg_fn=os.killpg) -> int:
    environment = {key: value for key, value in os.environ.items() if key != "NEBIUS_IAM_TOKEN"}
    process = popen_factory(
        ["bash", str(TEARDOWN), "--execute"],
        cwd=HERE,
        stdout=log,
        stderr=subprocess.STDOUT,
        env=environment,
        start_new_session=True,
    )
    try:
        return process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        try:
            killpg_fn(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                killpg_fn(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        return 124


def guard_run(run_dir: Path, *, now_fn=time.time, sleep_fn=time.sleep, cleanup_fn=cleanup_until_target) -> int:
    session = read_json(run_dir / "session.json")
    write_json(run_dir / "guard-ready.json", {
        "session_id": session["session_id"],
        "pid": os.getpid(),
        "ready_unix": now_fn(),
    })
    while not cleanup_verified(run_dir, session["session_id"]):
        remaining = session["cleanup_start_deadline_unix"] - now_fn()
        if remaining <= 0:
            return cleanup_fn(run_dir)
        sleep_fn(min(5, remaining))
    return 0


def spawn_guard(run_dir: Path) -> subprocess.Popen:
    with (run_dir / "guard.log").open("a") as log:
        return subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "guard", str(run_dir)],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )


def wait_guard_ready(run_dir: Path, process: subprocess.Popen) -> None:
    for _ in range(50):
        if (run_dir / "guard-ready.json").exists():
            return
        if process.poll() is not None:
            raise SessionError(f"Deadline guard exited before becoming ready (exit {process.returncode})")
        time.sleep(0.1)
    raise SessionError("Deadline guard did not become ready; Terraform apply was not started")


def apply_plan(
    run_dir: Path,
    session: dict,
    *,
    popen_factory=subprocess.Popen,
    cleanup_fn=cleanup_until_target,
) -> int:
    plan = Path(session["terraform_plan_path"])
    if not plan.is_file() or file_sha256(plan) != session["terraform_plan_sha256"]:
        raise SessionError("Terraform plan is missing or changed since the session was reserved")
    environment = {key: value for key, value in os.environ.items() if key != "NEBIUS_IAM_TOKEN"}
    with (run_dir / "apply.log").open("a") as log:
        process = popen_factory(
            [
                "terraform",
                f"-chdir={TERRAFORM_DIR}",
                "apply",
                "-input=false",
                session["terraform_plan_path"],
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=environment,
        )
        timed_out = False
        try:
            exit_code = process.wait(timeout=PLACEMENT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=90)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            exit_code = 124
        except KeyboardInterrupt:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=90)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            exit_code = 130

    write_json(run_dir / "apply-result.json", {
        "exit_code": exit_code,
        "finished_unix": time.time(),
        "placement_timeout": timed_out,
    })
    if exit_code != 0:
        cleanup_code = cleanup_fn(run_dir)
        write_json(run_dir / "failure-cleanup-result.json", {
            "cleanup_exit_code": cleanup_code,
            "finished_unix": time.time(),
        })
    return exit_code


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--execute", action="store_true", help="permit the single paid Terraform apply")
    result.add_argument("--approval-record", type=Path, required=True)
    result.add_argument("--approved-max-usd-pretax", required=True)
    result.add_argument("--purchase-type", choices=("preemptible",), required=True)
    result.add_argument("--plan", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["guard"]:
        if len(argv) != 2:
            raise SystemExit("Usage: pilot-session.py guard RUN_DIR")
        return guard_run(Path(argv[1]))
    if argv[:1] == ["cleanup"]:
        if len(argv) != 3 or argv[2] != "--execute":
            raise SystemExit("Usage: pilot-session.py cleanup RUN_DIR --execute")
        return cleanup_until_target(Path(argv[1]))
    args = parser().parse_args(argv)
    try:
        run_dir, session = prepare_session(args)
        guard = spawn_guard(run_dir)
        wait_guard_ready(run_dir, guard)
        session["guard_pid"] = guard.pid
        write_json(run_dir / "session.json", session)
        exit_code = apply_plan(run_dir, session)
        print(json.dumps({"run_dir": str(run_dir), "guard_pid": guard.pid, "apply_exit_code": exit_code}))
        return exit_code
    except (SessionError, json.JSONDecodeError, OSError) as error:
        print(f"pilot-session: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
