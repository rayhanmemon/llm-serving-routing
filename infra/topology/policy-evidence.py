#!/usr/bin/env python3
"""Strictly validate one unpinned policy run and write descriptive evidence."""
import argparse
import base64
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import statistics
import uuid

from prometheus_client.parser import text_string_to_metric_families
import yaml

from evidence import require_collection


class EvidenceError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise EvidenceError(message)


def load_workload(config_path):
    config = yaml.safe_load(config_path.read_text())
    headers = config.get("api", {}).get("headers", {}) or {}
    require(config.get("api", {}).get("type") == "completion" and
            config.get("api", {}).get("streaming") is True and
            config.get("server", {}).get("ignore_eos") is True,
            "workload must use streaming completion with EOS ignored")
    require("x-benchmark-decoder" not in {str(key).lower() for key in headers},
            "evaluated policy workload contains a diagnostic decoder pin")
    seed = config.get("load", {}).get("base_seed")
    require(isinstance(seed, int), "workload lacks an integer base_seed")
    expected_inputs = []
    trace_hash = None
    load_type = config.get("load", {}).get("type")
    if load_type == "concurrent":
        stages = config["load"].get("stages", [])
        require(stages and all(isinstance(stage.get("num_requests"), int) and
                               stage["num_requests"] > 0 for stage in stages),
                "concurrent workload has invalid stages")
        count = sum(stage["num_requests"] for stage in stages)
        distribution = config.get("data", {}).get("input_distribution", {})
        token_values = {distribution.get(key) for key in ("min", "max", "mean")}
        require(len(token_values) == 1 and next(iter(token_values)) in (512, 8192),
                "fixed workload input tokens must be exactly 512 or 8192")
        expected_inputs = [int(next(iter(token_values)))] * count
        output = config.get("data", {}).get("output_distribution", {})
        require({output.get(key) for key in ("min", "max", "mean")} == {128},
                "fixed workload output tokens must be exactly 128")
        kind = "calibration"
    elif load_type == "trace_replay":
        data_trace = config.get("data", {}).get("trace", {}).get("file")
        load_trace = config.get("load", {}).get("trace", {}).get("file")
        require(data_trace and data_trace == load_trace, "data/load trace paths differ")
        trace_path = (config_path.parent / data_trace).resolve()
        require(trace_path.is_file() and trace_path.parent == config_path.parent.resolve(),
                "trace must be a sibling of the rendered workload")
        rows = trace_path.read_text().splitlines()
        require(rows and rows[0].split(",") == ["TIMESTAMP", "ContextTokens", "GeneratedTokens"],
                "trace header is invalid")
        for row in rows[1:]:
            fields = row.split(",")
            require(len(fields) == 3 and int(fields[1]) in (512, 8192) and int(fields[2]) == 128,
                    "trace contains unsupported token counts")
            datetime.fromisoformat(fields[0].replace("Z", "+00:00"))
            expected_inputs.append(int(fields[1]))
        require(expected_inputs, "trace contains no requests")
        trace_hash = hashlib.sha256(trace_path.read_bytes()).hexdigest()
        kind = "heldout"
    else:
        raise EvidenceError("unsupported workload load.type")
    return {"config": config, "count": len(expected_inputs), "input_tokens": expected_inputs,
            "seed": seed, "trace_sha256": trace_hash, "kind": kind,
            "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest()}


def validate_stream(raw):
    require(isinstance(raw, str) and raw.strip(), "missing raw streamed response")
    done = False
    finishes = []
    usage = None
    content_chunks = []
    response_ids = []
    every_chunk_has_id = True
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        lines = [line for line in block.splitlines() if line and not line.startswith(":")]
        if not lines:
            continue
        require(not done, "data after terminal [DONE]")
        require(all(line.startswith("data:") for line in lines), "unexpected SSE field")
        data = "\n".join(line[5:].lstrip() for line in lines)
        if data == "[DONE]":
            done = True
            continue
        value = json.loads(data)
        require(isinstance(value, dict) and not value.get("error"), "error or invalid SSE payload")
        response_id = value.get("id")
        if isinstance(response_id, str) and response_id:
            response_ids.append(response_id)
        else:
            every_chunk_has_id = False
        choices = value.get("choices")
        require(isinstance(choices, list), "missing choices")
        has_content = False
        for choice in choices:
            require(choice.get("index", 0) == 0, "unexpected completion index")
            if choice.get("text"):
                require(not finishes, "generated text after finish reason")
                has_content = True
            if choice.get("finish_reason"):
                finishes.append(choice["finish_reason"])
        if value.get("usage") is not None:
            usage = value["usage"]
        if has_content:
            content_chunks.append(value)
    require(done and finishes == ["length"], "missing completion terminator or incorrect finish reason")
    require(content_chunks, "empty generated output")
    require(isinstance(usage, dict) and usage.get("completion_tokens") == 128,
            "missing usage or output token count mismatch")
    require(usage.get("prompt_tokens") in (512, 8192), "server prompt token count is unsupported")
    require(len(set(response_ids)) <= 1, "streamed response changed completion ID")
    join_id = response_ids[0] if every_chunk_has_id and response_ids else None
    return usage["prompt_tokens"], content_chunks, join_id


def envoy_request_id(response_id):
    if not isinstance(response_id, str) or not response_id.startswith("cmpl-"):
        return None
    candidate = response_id[len("cmpl-"):]
    try:
        parsed = uuid.UUID(candidate)
    except (ValueError, AttributeError):
        return None
    return candidate if str(parsed) == candidate.lower() else None


def validate_records(records, workload):
    require(isinstance(records, list) and len(records) == workload["count"],
            "per-request record count mismatch")
    results = []
    for index, row in enumerate(records):
        require(not row.get("error"), "harness recorded a request error")
        request = json.loads(row["request"]) if isinstance(row.get("request"), str) else row.get("request")
        require(isinstance(request, dict), "request payload missing")
        require(isinstance(request.get("prompt"), str), "completion prompt is missing")
        require(request.get("stream") is True and request.get("ignore_eos") is True and
                request.get("max_tokens") == 128, "request settings mismatch")
        prompt_tokens, content_chunks, response_id = validate_stream(row.get("response"))
        info = row.get("info", {})
        response_metrics = info.get("response_metrics", {})
        recorded_chunks = response_metrics.get("response_chunks")
        chunk_times = response_metrics.get("chunk_times")
        require(isinstance(recorded_chunks, list) and isinstance(chunk_times, list) and
                len(recorded_chunks) == len(chunk_times) == len(content_chunks) and content_chunks,
                "content chunk/timestamp count mismatch")
        try:
            parsed_chunks = [json.loads(chunk) for chunk in recorded_chunks]
        except (TypeError, json.JSONDecodeError) as error:
            raise EvidenceError("invalid recorded response chunk") from error
        require(parsed_chunks == content_chunks, "recorded response chunks differ from raw SSE content")
        require(all(isinstance(value, (int, float)) and math.isfinite(value) for value in chunk_times) and
                all(left <= right for left, right in zip(chunk_times, chunk_times[1:])),
                "invalid content timestamps")
        start, end = row.get("start_time"), row.get("end_time")
        require(isinstance(start, (int, float)) and isinstance(end, (int, float)) and
                math.isfinite(start) and math.isfinite(end) and
                start <= chunk_times[0] <= chunk_times[-1] <= end,
                "invalid request timing interval")
        estimated = response_metrics.get("output_token_times")
        require(isinstance(estimated, list) and estimated and
                all(isinstance(value, (int, float)) and math.isfinite(value) for value in estimated) and
                all(left <= right for left, right in zip(estimated, estimated[1:])) and
                start <= estimated[0] <= estimated[-1] <= end,
                "invalid estimated output-token timestamps")
        generated_input = info.get("request_metrics", {}).get("text", {}).get("input_tokens")
        require(generated_input == prompt_tokens and info.get("input_tokens") == prompt_tokens,
                "client/server input token counts disagree")
        request_hash = hashlib.sha256(json.dumps(request, sort_keys=True,
                                                  separators=(",", ":")).encode()).hexdigest()
        prompt_hash = hashlib.sha256(request["prompt"].encode()).hexdigest()
        results.append({"report_index": index, "request_hash": request_hash,
                        "prompt_hash": prompt_hash,
                        "response_id": response_id,
                        "prompt_tokens": prompt_tokens, "start_time": start, "end_time": end,
                        "ttft_seconds": chunk_times[0] - start, "completion_seconds": end - start})
    require(Counter(item["prompt_tokens"] for item in results) == Counter(workload["input_tokens"]),
            "actual prompt-token multiset differs from the workload")
    return results


def worker_identity(snapshot):
    identities = {}
    for pod in json.loads(snapshot.read_text())["items"]:
        specs = {container["name"]: container for container in pod["spec"].get("containers", [])}
        relevant = {name for name, spec in specs.items()
                    if name == "modelserver" or "llm-d-router-endpoint-picker" in spec.get("image", "")}
        if not relevant:
            continue
        statuses = {status["name"]: status for status in pod.get("status", {}).get("containerStatuses", [])}
        require(relevant <= statuses.keys() and not pod["metadata"].get("deletionTimestamp"),
                "worker/EPP status missing or stopping")
        selected = []
        for name in sorted(relevant):
            status = statuses[name]
            require(status.get("ready") and "running" in status.get("state", {}) and
                    status.get("containerID"), "worker/EPP is not running")
            selected.append((name, status["containerID"], status.get("restartCount", 0)))
        identities[pod["metadata"]["name"]] = {
            "uid": pod["metadata"]["uid"], "node": pod["spec"].get("nodeName"),
            "containers": selected,
        }
    require(len(identities) == 4, "expected three model workers and one EPP")
    return identities


def decoder_ips(snapshot, local_decoder, remote_decoder):
    pods = {pod["metadata"]["name"]: pod for pod in json.loads(snapshot.read_text())["items"]}
    result = {}
    for route, name in (("local", local_decoder), ("remote", remote_decoder)):
        require(name in pods and pods[name].get("status", {}).get("podIP"),
                f"{route} decoder pod/IP is missing")
        require(pods[name]["metadata"].get("labels", {}).get("llm-d.ai/role") == "decode" and
                any(container.get("name") == "modelserver" for container in
                    pods[name]["spec"].get("containers", [])),
                f"{route} route target is not a decode model worker")
        result[pods[name]["status"]["podIP"] + ":8000"] = (route, name)
    require(len(result) == 2, "decoder pods do not have distinct IPs")
    return result


def parse_log_time(line):
    prefix = line.split(None, 1)[0]
    return datetime.fromisoformat(prefix.replace("Z", "+00:00")).timestamp()


def route_rows(paths, run_id, ip_map, namespace):
    rows = []
    for path in paths:
        for line in path.read_text().splitlines():
            start = line.find("{")
            if start < 0:
                continue
            try:
                value = json.loads(line[start:])
            except json.JSONDecodeError:
                continue
            if value.get("run_id") != run_id:
                continue
            require(value.get("requested_decoder") in (None, "", "-"),
                    "policy request carried a diagnostic decoder pin")
            require(str(value.get("status")) == "200" and value.get("response_flags") == "-",
                    "proxy recorded an unsuccessful response")
            upstream = value.get("upstream_host")
            require(upstream in ip_map, "Envoy upstream is not one of the two known decoder pod IPs")
            route, pod = ip_map[upstream]
            selected = value.get("selected_decoder")
            if selected not in (None, "", "-"):
                try:
                    decoded = base64.b64decode(selected, validate=True).decode()
                except Exception as error:
                    raise EvidenceError("selected decoder response header is malformed") from error
                require(decoded == f"{namespace}/{pod}-rank-0", "selected decoder header disagrees with upstream IP")
            request_id = value.get("request_id")
            require(request_id not in (None, "", "-"), "route log lacks a request ID")
            rows.append({"request_id": request_id, "route": route, "decoder_pod": pod,
                         "upstream_host": upstream, "completed_unix": parse_log_time(line)})
    require(len({row["request_id"] for row in rows}) == len(rows), "duplicate route request IDs")
    return rows


def exact_route_join(records, routes):
    route_by_id = {row["request_id"]: row for row in routes}
    record_ids = [envoy_request_id(row.get("response_id")) for row in records]
    if (all(record_ids) and len(set(record_ids)) == len(records) and
            set(record_ids) == set(route_by_id) and len(route_by_id) == len(routes)):
        joined = []
        for record, request_id in zip(records, record_ids):
            route = route_by_id[request_id]
            joined.append({**record, "envoy_request_id": request_id, "route": route["route"],
                           "decoder_pod": route["decoder_pod"],
                           "local_route": route["route"] == "local"})
        return joined, "exact: consistent SSE cmpl-UUID values form a unique bijection with Envoy request_id"
    return records, ("unknown: absent, invalid, duplicate, or unmatched SSE completion IDs prevent an exact "
                     "bijection with Envoy request_id")


def prometheus_values(path):
    values = {}
    for family in text_string_to_metric_families(path.read_text()):
        for sample in family.samples:
            if sample.name.startswith("vllm:nixl_"):
                value = float(sample.value)
                require(math.isfinite(value), "non-finite NIXL metric")
                values[sample.name] = values.get(sample.name, 0.0) + value
    return values


def transfer_delta(before, after, decoder_names, expected_count):
    required = ("vllm:nixl_bytes_transferred_count", "vllm:nixl_xfer_time_seconds_count")
    totals = {metric: 0.0 for metric in required}
    by_decoder = {}
    for name in decoder_names:
        old = prometheus_values(before / (name + "-metrics.txt"))
        new = prometheus_values(after / (name + "-metrics.txt"))
        deltas = {}
        for metric in required:
            require(metric in old and metric in new, "decoder lacks required NIXL transfer counter")
            delta = new[metric] - old[metric]
            require(delta >= 0, "NIXL transfer counter reset")
            deltas[metric] = delta
            totals[metric] += delta
        by_decoder[name] = deltas
    for metric, total in totals.items():
        require(total == expected_count, "summed decoder transfer count differs from completed requests")
    failures = ("vllm:nixl_num_failed_transfers_total", "vllm:nixl_num_kv_expired_reqs_total",
                "vllm:nixl_num_failed_notifications_total")
    failure_deltas = {}
    model_files = []
    for path in sorted(before.glob("*-metrics.txt")):
        other = after / path.name
        require(other.exists(), "after snapshot lacks a worker metrics file")
        old, new = prometheus_values(path), prometheus_values(other)
        if not old and not new:
            continue
        model_files.append(path.name)
        for metric in failures:
            require(metric in old and metric in new, "required transfer failure counter is missing")
            delta = new[metric] - old[metric]
            require(delta == 0, "transfer failure/expiry counter changed")
            failure_deltas[path.name + ":" + metric] = delta
    require(len(model_files) == 3, "expected transfer/error counters from three model workers")
    return {"by_decoder": by_decoder, "totals": totals, "failure_deltas": failure_deltas}


def active_policy(snapshot, policy_manifest):
    values = yaml.safe_load(policy_manifest.read_text())
    expected = values.get("router", {}).get("epp", {}).get("pluginsConfig")
    require(isinstance(expected, dict), "policy manifest lacks router.epp.pluginsConfig")
    require("session-affinity-filter" not in yaml.safe_dump(expected),
            "evaluated policy manifest contains diagnostic pin support")
    configmaps = json.loads(snapshot.read_text())["items"]
    matches = []
    for configmap in configmaps:
        for key, text in configmap.get("data", {}).items():
            try:
                candidate = yaml.safe_load(text)
            except (yaml.YAMLError, TypeError):
                continue
            if candidate == expected:
                matches.append(configmap["metadata"]["name"] + ":" + key)
    require(len(matches) == 1, "active EPP ConfigMap does not uniquely match the declared policy manifest")
    return matches[0]


def normalized_policy(config):
    require(isinstance(config, dict), "EPP startup config is not an object")
    plugins = []
    for plugin in config.get("plugins", []):
        require(isinstance(plugin, dict) and plugin.get("type"), "EPP config contains an invalid plugin")
        plugins.append({"name": plugin.get("name", plugin["type"]), "type": plugin["type"],
                        "parameters": plugin.get("parameters")})
    profiles = []
    for profile in config.get("schedulingProfiles", []):
        require(isinstance(profile, dict) and profile.get("name"), "EPP config contains an invalid profile")
        refs = []
        for ref in profile.get("plugins", []):
            require(isinstance(ref, dict) and ref.get("pluginRef"), "EPP profile contains an invalid plugin ref")
            refs.append({"pluginRef": ref["pluginRef"], "weight": ref.get("weight")})
        profiles.append({"name": profile["name"], "plugins": refs})
    return {"plugins": plugins, "schedulingProfiles": profiles}


def running_policy(collection, policy_manifest):
    configmap_match = active_policy(collection / "configmaps.json", policy_manifest)
    pods = json.loads((collection / "pods.json").read_text())["items"]
    epp_pods = []
    for pod in pods:
        if any("llm-d-router-endpoint-picker" in container.get("image", "")
               for container in pod["spec"].get("containers", [])):
            epp_pods.append(pod)
    require(len(epp_pods) == 1 and not epp_pods[0]["metadata"].get("deletionTimestamp"),
            "collection does not identify one current EPP pod")
    pod_name = epp_pods[0]["metadata"]["name"]
    log_path = collection / (pod_name + "-epp.log")
    require(log_path.is_file(), "current EPP startup log is missing from the collection")
    startup = []
    for line in log_path.read_text().splitlines():
        start = line.find("{")
        if start < 0:
            continue
        try:
            value = json.loads(line[start:])
        except json.JSONDecodeError:
            continue
        if value.get("body") == "Raw config after phase one" and isinstance(value.get("config"), dict):
            startup.append(value["config"])
    require(startup, "current EPP log lacks 'Raw config after phase one'")
    values = yaml.safe_load(policy_manifest.read_text())
    expected = values.get("router", {}).get("epp", {}).get("pluginsConfig")
    require(normalized_policy(startup[-1]) == normalized_policy(expected),
            "current EPP startup config does not match the declared policy manifest")
    return {"configmap": configmap_match, "pod": pod_name,
            "pod_uid": epp_pods[0]["metadata"]["uid"], "startup_records": len(startup)}


def epp_decision_observations(paths):
    observations = []
    for path in paths:
        for line in path.read_text().splitlines():
            if "Active request counts" in line:
                observations.append({"file": path.name, "line": line})
    return {"observations": observations,
            "interpretation": "Optional EPP trace observations are post-filter counts, not proof of every gate input."}


def percentile95(values):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def validate_pair(reference, request_hashes, prompt_hashes, workload, mode, block_id):
    if mode == "heldout":
        require(reference.get("mode") == "heldout" and reference.get("block_id") == block_id,
                "paired held-out evidence has a different mode or block_id")
    require(request_hashes == sorted(reference.get("request_hashes", [])),
            "paired policy request-payload multisets differ")
    require(prompt_hashes == sorted(reference.get("prompt_hashes", [])),
            "paired policy prompt multisets differ")
    require(workload["trace_sha256"] == reference.get("intended_arrival_trace_sha256"),
            "paired intended arrival traces differ")
    require(workload["seed"] == reference.get("seed"), "paired workload seeds differ")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--block-id", required=True)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--policy-manifest", required=True, type=Path)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--mode", choices=("calibration", "heldout"), required=True)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--local-decoder", required=True)
    parser.add_argument("--remote-decoder", required=True)
    parser.add_argument("--namespace", default="topology-measurement")
    parser.add_argument("--paired-with", type=Path)
    args = parser.parse_args()
    require_collection(args.before)
    require_collection(args.after, measured=True)
    workload = load_workload(args.config)
    require(workload["kind"] == args.mode, "declared mode does not match workload kind")
    require(workload["seed"] == args.seed, "declared seed does not match workload")
    identities = worker_identity(args.before / "pods.json")
    require(identities == worker_identity(args.after / "pods.json"), "worker/EPP identity changed during run")
    running_before = running_policy(args.before, args.policy_manifest)
    running_after = running_policy(args.after, args.policy_manifest)
    require(running_after["configmap"] == running_before["configmap"] and
            running_after["pod_uid"] == running_before["pod_uid"],
            "active policy ConfigMap or EPP pod changed during the run")
    report_paths = list((args.after / "reports").rglob("per_request_lifecycle_metrics.json"))
    require(len(report_paths) == 1, "expected one per-request report")
    records = validate_records(json.loads(report_paths[0].read_text()), workload)
    ip_map = decoder_ips(args.after / "pods.json", args.local_decoder, args.remote_decoder)
    routes = route_rows(args.after.glob("*envoy-proxy.log"), args.run_id, ip_map, args.namespace)
    require(len(routes) == len(records), "route-log count differs from completed request count")
    client_metrics, route_mapping = exact_route_join(records, routes)
    transfer = transfer_delta(args.before, args.after, (args.local_decoder, args.remote_decoder), workload["count"])
    request_hashes = sorted(row["request_hash"] for row in records)
    prompt_hashes = sorted(row["prompt_hash"] for row in records)
    if args.paired_with:
        reference = json.loads(args.paired_with.read_text())
        validate_pair(reference, request_hashes, prompt_hashes, workload, args.mode, args.block_id)
    ttfts = [row["ttft_seconds"] for row in records]
    completions = [row["completion_seconds"] for row in records]
    starts = sorted(row["start_time"] for row in records)
    result = {
        "run_id": args.run_id, "block_id": args.block_id, "policy": args.policy, "mode": args.mode,
        "heldout": args.mode == "heldout", "seed": workload["seed"],
        "expected_and_completed_requests": workload["count"],
        "expected_input_tokens": workload["input_tokens"],
        "request_hashes": request_hashes,
        "prompt_hashes": prompt_hashes,
        "intended_arrival_trace_sha256": workload["trace_sha256"],
        "workload_config_sha256": workload["config_sha256"],
        "actual_start_delay_seconds": [value - starts[0] for value in starts],
        "per_request_client_metrics": client_metrics,
        "per_request_route_mapping": route_mapping,
        "envoy_routes": routes,
        "route_counts": dict(Counter(row["route"] for row in routes)),
        "ttft_seconds": {"mean": statistics.fmean(ttfts), "median": statistics.median(ttfts),
                         "descriptive_p95_nearest_rank": percentile95(ttfts)},
        "completion_seconds": {"mean": statistics.fmean(completions),
                               "median": statistics.median(completions),
                               "descriptive_p95_nearest_rank": percentile95(completions)},
        "transfer": transfer, "worker_identities": identities,
        "running_policy": running_before,
        "epp_active_request_counts": epp_decision_observations(args.after.glob("*epp.log")),
        "interpretation": "Descriptive observations only; this file does not declare a policy winner or estimate p99.",
    }
    (args.after / "policy-evidence.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print("PASS: unpinned requests, actual routes, payloads, outputs, identities, and transfer counts validated")


if __name__ == "__main__":
    try:
        main()
    except EvidenceError as error:
        raise SystemExit("FAIL: " + str(error))
