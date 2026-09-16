#!/usr/bin/env python3
"""Send one deterministic direct or pinned P/D correctness request."""
import argparse
import base64
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request


BODY = {
    "model": "Qwen/Qwen3-8B",
    "prompt": "The capital of France is",
    "max_tokens": 16,
    "temperature": 0,
    "seed": 17,
    "ignore_eos": True,
    "stream": False,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route", required=True, choices=("direct", "local", "remote"))
    parser.add_argument("--base-url", required=True, help="Endpoint root, without /v1/completions")
    parser.add_argument("--decoder-pod", help="Actual decoder pod name; required for local/remote")
    parser.add_argument("--run-id", required=True, help="Unique value for x-benchmark-run")
    parser.add_argument("--namespace", default="topology-measurement")
    parser.add_argument("--out", type=Path, required=True, help="New JSON result file")
    args = parser.parse_args()

    if args.route == "direct" and args.decoder_pod:
        parser.error("--decoder-pod is invalid for a direct request")
    if args.route != "direct" and not args.decoder_pod:
        parser.error("--decoder-pod is required for local/remote")
    if args.out.exists():
        parser.error("--out must not already exist")

    requested = None
    headers = {"Content-Type": "application/json", "x-benchmark-run": args.run_id}
    if args.decoder_pod:
        requested = base64.b64encode(
            f"{args.namespace}/{args.decoder_pod}-rank-0".encode()
        ).decode()
        headers["x-benchmark-decoder"] = requested

    url = args.base_url.rstrip("/") + "/v1/completions"
    record = {
        "route": args.route,
        "run_id": args.run_id,
        "url": url,
        "decoder_pod": args.decoder_pod,
        "requested_decoder": requested,
        "request_body": BODY,
        "status": None,
        "selected_decoder": None,
        "body": None,
        "error": None,
        "error_body": None,
    }
    ok = False
    request = urllib.request.Request(url, data=json.dumps(BODY).encode(), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode(errors="replace")
            record["status"] = response.status
            record["selected_decoder"] = response.headers.get("x-benchmark-decoder")
            try:
                record["body"] = json.loads(raw)
            except json.JSONDecodeError as error:
                record["error"] = f"invalid JSON response: {error}"
                record["error_body"] = raw
            choices = record["body"].get("choices") if isinstance(record["body"], dict) else None
            text = choices[0].get("text") if isinstance(choices, list) and choices else None
            ok = response.status == 200 and bool(text) and record["error"] is None
            if requested is not None:
                ok = ok and record["selected_decoder"] == requested
            if not ok and record["error"] is None:
                record["error"] = "status, output, or selected decoder check failed"
    except urllib.error.HTTPError as error:
        record["status"] = error.code
        record["selected_decoder"] = error.headers.get("x-benchmark-decoder")
        record["error"] = str(error)
        record["error_body"] = error.read().decode(errors="replace")
    except Exception as error:
        record["error"] = str(error)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps(record, sort_keys=True), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
