#!/usr/bin/env python3
"""Summarize a strictly validated first 48-request timing block offline."""
import argparse
from collections import Counter, defaultdict, deque
import csv
import io
import json
import math
from pathlib import Path
import shutil
import statistics
import uuid

from evidence import require_collection


ARMS = (
    ("short-local-b1", 512, "local"),
    ("short-remote-b1", 512, "remote"),
    ("long-remote-b1", 8192, "remote"),
    ("long-local-b1", 8192, "local"),
)


class SummaryError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise SummaryError(message)


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise SummaryError(f"cannot read valid JSON from {path}: {error}") from error


def finite_number(value, label, *, positive=False):
    require(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
            label + " must be finite")
    require(value > 0 if positive else value >= 0, label + " has an invalid sign")
    return float(value)


def contained(root, value):
    root = root.resolve()
    path = Path(value)
    path = path.resolve() if path.is_absolute() else (root / path).resolve()
    require(path != root and root in path.parents, "completion marker path escapes the run directory")
    return path


def read_arm(attempt_dir, label, expected_run_id, tokens, route):
    arm_dir = attempt_dir / label
    before, after = arm_dir / "before", arm_dir / "after"
    try:
        require_collection(before)
        require_collection(after, measured=True)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SummaryError(f"invalid collection for {label}: {error}") from error
    validation = read_json(after / "validation.json")
    transfer = read_json(after / "transfer-delta.json")
    require(validation.get("run_id") == expected_run_id and
            validation.get("request_route_and_identity_checks_passed") is True,
            f"{label} lacks strict route/identity validation")
    hashes = validation.get("request_hashes")
    ttfts = validation.get("client_ttft_seconds")
    require(isinstance(hashes, list) and isinstance(ttfts, list) and
            len(hashes) == len(ttfts) == 12 and all(isinstance(value, str) and value for value in hashes),
            f"{label} does not contain exactly 12 request hashes and TTFT samples")
    ttfts = [finite_number(value, f"{label} TTFT") for value in ttfts]
    operations = finite_number(transfer.get("operations"), f"{label} transfer operations")
    timed_operations = finite_number(transfer.get("timed_operations"), f"{label} timed operations")
    require(operations == 12 and timed_operations == 12,
            f"{label} transfer operation counts must both equal 12")
    failures = transfer.get("failure_observations")
    require(isinstance(failures, dict) and failures and
            all(finite_number(value, f"{label} failure delta") == 0 for value in failures.values()),
            f"{label} has missing or nonzero transfer failure observations")
    transfer_summary = {
        "bytes": finite_number(transfer.get("bytes"), f"{label} transferred bytes", positive=True),
        "operations": int(operations),
        "timed_operations": int(timed_operations),
        "observed_seconds": finite_number(transfer.get("seconds"), f"{label} observed transfer time",
                                           positive=True),
        "mean_observed_transfer_seconds": finite_number(
            transfer.get("mean_observed_transfer_seconds"), f"{label} mean transfer time", positive=True),
        "effective_bytes_per_second": finite_number(
            transfer.get("effective_bytes_per_second"), f"{label} effective bytes/second", positive=True),
        "failure_status": "no observed transfer/expiry/notification counter deltas",
    }
    return {"label": label, "tokens": tokens, "route": route, "run_id": expected_run_id,
            "request_hashes": hashes, "ttft_seconds": ttfts,
            "ttft_mean_seconds": statistics.fmean(ttfts),
            "ttft_median_seconds": statistics.median(ttfts),
            "transfer": transfer_summary,
            "validation_path": str((after / "validation.json").resolve()),
            "transfer_delta_path": str((after / "transfer-delta.json").resolve())}


def pair_samples(local, remote):
    require(Counter(local["request_hashes"]) == Counter(remote["request_hashes"]),
            f"{local['tokens']}-token local/remote request payload hashes differ")
    remote_values = defaultdict(deque)
    for request_hash, ttft in zip(remote["request_hashes"], remote["ttft_seconds"]):
        remote_values[request_hash].append(ttft)
    rows = []
    for index, (request_hash, local_ttft) in enumerate(
            zip(local["request_hashes"], local["ttft_seconds"]), start=1):
        remote_ttft = remote_values[request_hash].popleft()
        rows.append({"input_tokens": local["tokens"], "sample": index, "request_hash": request_hash,
                     "local_ttft_seconds": local_ttft, "remote_ttft_seconds": remote_ttft,
                     "remote_minus_local_seconds": remote_ttft - local_ttft})
    require(all(not values for values in remote_values.values()), "unpaired remote payload samples remain")
    deltas = [row["remote_minus_local_seconds"] for row in rows]
    return rows, {"input_tokens": local["tokens"], "sample_count": len(rows),
                  "remote_minus_local_mean_seconds": statistics.fmean(deltas),
                  "remote_minus_local_median_seconds": statistics.median(deltas)}


def summarize(run_dir):
    run_dir = run_dir.resolve()
    session = read_json(run_dir / "session.json")
    completion = read_json(run_dir / "first-timing-block-complete.json")
    require(completion.get("validated") is True and
            completion.get("session_id") == session.get("session_id"),
            "first timing completion marker is not validated for this session")
    labels = [item[0] for item in ARMS]
    require(completion.get("arms") == labels, "completion marker does not name the frozen four arms")
    run_ids = completion.get("run_ids")
    require(isinstance(run_ids, list) and len(run_ids) == 4 and len(set(run_ids)) == 4,
            "completion marker lacks four unique run IDs")
    attempt_dir = contained(run_dir, completion.get("attempt_dir", ""))
    require(attempt_dir.is_dir(), "completion marker attempt directory is missing")
    arms = {}
    for (label, tokens, route), run_id in zip(ARMS, run_ids):
        arms[label] = read_arm(attempt_dir, label, run_id, tokens, route)
    short_rows, short_delta = pair_samples(arms["short-local-b1"], arms["short-remote-b1"])
    long_rows, long_delta = pair_samples(arms["long-local-b1"], arms["long-remote-b1"])
    source_path = run_dir / "source-record.json"
    source = read_json(source_path) if source_path.is_file() else None
    return {
        "schema_version": 1, "session_id": session["session_id"],
        "kind": "descriptive-first-48-request-pilot", "validated": True,
        "measured_request_count": 48, "warmups_excluded": True,
        "attempt_dir": str(attempt_dir),
        "completion_marker_path": str((run_dir / "first-timing-block-complete.json").resolve()),
        "source_record_path": str(source_path.resolve()) if source else None,
        "source": source,
        "arms": [arms[label] for label in labels],
        "paired_remote_minus_local": [short_delta, long_delta],
        "paired_samples": short_rows + long_rows,
        "limits": [
            "Twelve samples per arm are descriptive and do not estimate p99.",
            "Forced-route observations do not establish routing-policy gain.",
            "NIXL timing and effective bytes/second are connector observations, not physical-link bandwidth.",
            "Cold setup and four warmup requests are excluded from measured samples.",
        ],
    }


def csv_text(summary):
    output = io.StringIO()
    fields = ("input_tokens", "sample", "request_hash", "local_ttft_seconds",
              "remote_ttft_seconds", "remote_minus_local_seconds")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(summary["paired_samples"])
    return output.getvalue()


def arm_csv_text(summary):
    output = io.StringIO()
    fields = ("input_tokens", "route", "samples", "ttft_mean_seconds", "ttft_median_seconds",
              "nixl_bytes", "nixl_operations", "nixl_mean_observed_transfer_seconds", "failure_status")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for arm in summary["arms"]:
        transfer = arm["transfer"]
        writer.writerow({"input_tokens": arm["tokens"], "route": arm["route"], "samples": 12,
                         "ttft_mean_seconds": arm["ttft_mean_seconds"],
                         "ttft_median_seconds": arm["ttft_median_seconds"],
                         "nixl_bytes": transfer["bytes"], "nixl_operations": transfer["operations"],
                         "nixl_mean_observed_transfer_seconds": transfer["mean_observed_transfer_seconds"],
                         "failure_status": transfer["failure_status"]})
    return output.getvalue()


def markdown_text(summary):
    arm_rows = []
    for arm in summary["arms"]:
        transfer = arm["transfer"]
        arm_rows.append(
            f"| {arm['tokens']} | {arm['route']} | 12 | {arm['ttft_mean_seconds'] * 1000:.3f} | "
            f"{arm['ttft_median_seconds'] * 1000:.3f} | {transfer['bytes']:.0f} | "
            f"{transfer['operations']} | {transfer['mean_observed_transfer_seconds'] * 1000:.3f} |")
    delta_rows = [
        f"| {row['input_tokens']} | {row['sample_count']} | "
        f"{row['remote_minus_local_mean_seconds'] * 1000:.3f} | "
        f"{row['remote_minus_local_median_seconds'] * 1000:.3f} |"
        for row in summary["paired_remote_minus_local"]]
    source = summary.get("source") or {}
    source_line = (f"Source commit: `{source.get('evaluation_commit', source.get('source_commit', 'unrecorded'))}`; source record: "
                   f"`{summary['source_record_path']}`.\n\n") if source else ""
    return "".join([
        "# First forced-route timing block\n\n",
        "This is a descriptive 48-request pilot. Four cold-setup warmups are excluded.\n\n",
        source_line,
        "| Input tokens | Route | Samples | Mean TTFT (ms) | Median TTFT (ms) | NIXL bytes | "
        "NIXL ops | Mean observed NIXL time (ms) |\n",
        "|---:|---|---:|---:|---:|---:|---:|---:|\n", *[row + "\n" for row in arm_rows],
        "\nAll recorded transfer/expiry/notification failure-counter deltas are zero.\n\n",
        "| Input tokens | Matched samples | Mean remote − local TTFT (ms) | Median paired delta (ms) |\n",
        "|---:|---:|---:|---:|\n", *[row + "\n" for row in delta_rows],
        "\nThe CSV retains all 24 matched local/remote sample pairs. These observations do not establish "
        "p99, routing-policy gain, or physical-link bandwidth. NIXL values are aggregate connector observations.\n",
    ])


def write_report(summary, out):
    out = out.resolve()
    require(not out.exists(), "output directory already exists")
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.parent / ("." + out.name + ".tmp-" + uuid.uuid4().hex)
    temporary.mkdir()
    try:
        (temporary / "first-block.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n")
        (temporary / "first-block.csv").write_text(arm_csv_text(summary))
        (temporary / "paired-ttft.csv").write_text(csv_text(summary))
        (temporary / "FIRST-BLOCK.md").write_text(markdown_text(summary))
        temporary.replace(out)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    summary = summarize(args.run_dir)
    write_report(summary, args.out)
    print("PASS: descriptive first-block report written to " + str(args.out.resolve()))


if __name__ == "__main__":
    try:
        main()
    except SummaryError as error:
        raise SystemExit("FAIL: " + str(error))
