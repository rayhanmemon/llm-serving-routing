#!/usr/bin/env python3
"""Freeze or validate the bounded RTX known-answer correctness suite; never send model requests."""
import argparse
import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import shutil
import tempfile


MODEL = "Qwen/Qwen3-8B"
REVISION = "b968826d9c46dd6066d109eabc6255188de91218"
SEED = 17
SUITE_SEED = 17092026
ROUTES = ("direct-local", "direct-remote", "pd-local", "pd-remote")
WORD_POOL = ("red", "blue", "green", "yellow", "black", "white", "orange", "purple",
             "brown", "pink", "gray", "silver", "gold", "cyan", "navy", "teal")


class SuiteError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise SuiteError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def encode(tokenizer, text):
    return tokenizer.encode(text, add_special_tokens=False)


def choose_filler_token(tokenizer):
    for text in (" filler", " neutral", " context", " token"):
        values = encode(tokenizer, text)
        if len(values) == 1:
            return text, values[0]
    raise SuiteError("no declared filler candidate is one token in the pinned tokenizer")


def build_prompt(tokenizer, family, words, target_tokens, filler_id):
    gold = " ".join(words)
    if family == "copy":
        prefix = ("You are a literal copier. Complete ANSWER with exactly the words after SOURCE.\n"
                  "SOURCE: red blue green\nANSWER: red blue green\n"
                  "SOURCE: silver gold cyan\nANSWER: silver gold cyan\n"
                  "REFERENCE PADDING:\n")
        tail = f"\nSOURCE: {gold}\nANSWER:"
    else:
        prefix = (f"MEMORY: The secret sequence is {gold}.\n"
                  "Keep the secret sequence unchanged.\nREFERENCE PADDING:\n")
        tail = "\nQUESTION: What is the secret sequence? Answer with only the sequence.\nANSWER:"
    prefix_ids, tail_ids = encode(tokenizer, prefix), encode(tokenizer, tail)
    filler_count = target_tokens - len(prefix_ids) - len(tail_ids)
    require(filler_count >= 1, f"{family} prompt scaffolding exceeds {target_tokens} tokens")
    prompt_ids = prefix_ids + [filler_id] * filler_count + tail_ids
    require(len(prompt_ids) == target_tokens, "prompt token padding failed")
    gold_ids = encode(tokenizer, " " + gold)
    require(len(gold_ids) == 8, "gold must contain exactly eight generated tokens")
    return prompt_ids, gold, gold_ids


def make_suite(tokenizer, suite_seed=SUITE_SEED, criterion="gold"):
    require(criterion in ("gold", "direct-parity"), "unknown qualification criterion")
    for word in WORD_POOL:
        require(len(encode(tokenizer, " " + word)) == 1,
                f"declared answer word is not one token: {word}")
    filler_text, filler_id = choose_filler_token(tokenizer)
    rng = random.Random(suite_seed)
    cases = []
    for family in ("copy", "retrieval"):
        for tokens in (512, 8192):
            for repeat in range(2):
                words = tuple(rng.sample(WORD_POOL, 8))
                prompt_ids, gold, gold_ids = build_prompt(
                    tokenizer, family, words, tokens, filler_id)
                case_id = f"{family}-{tokens}-{repeat + 1}"
                body = {"model": MODEL, "prompt": prompt_ids, "max_tokens": len(gold_ids),
                        "temperature": 0, "seed": SEED, "ignore_eos": True, "stream": False,
                        "logprobs": 5, "return_token_ids": True}
                cases.append({"case_id": case_id, "family": family, "input_tokens": tokens,
                              "gold_text": gold, "gold_token_ids": gold_ids,
                              "request_body": body, "request_sha256": digest(body)})
    require(len(cases) == 8 and Counter(case["family"] for case in cases) == {"copy": 4, "retrieval": 4},
            "suite topology is invalid")
    return {"schema_version": 1, "model": MODEL, "revision": REVISION, "request_seed": SEED,
            "suite_seed": suite_seed,
            "selection_rule": "Using one seeded RNG, sample eight distinct words from the declared 16-word pool for each case in recorded case order.",
            "normalization": "outer whitespace only (response_text.strip())",
            "routes": list(ROUTES), "expected_requests": 32,
            "filler": {"text": filler_text, "token_id": filler_id}, "cases": cases,
            "qualification": {"criterion": criterion, "required_gold_matches": 32,
                              "required_per_route": "8/8",
                              "direct_parity_rule": "For every case, normalized text must be identical across direct-local, direct-remote, pd-local and pd-remote.",
                              "filtered_or_replaced_cases_allowed": False,
                              "separate_transfer_requirement": "Each P/D request must add exactly one transfer; all failure deltas zero.",
                              "hardware_change_allowed": False,
                              "settings": "Keep the deployed images, TRITON_ATTN, compilation/CUDA-graph mode and non-eager execution unchanged."}}


CLIENT = r'''#!/usr/bin/env python3
import argparse,base64,hashlib,json,urllib.error,urllib.request
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument("--plan",required=True,type=Path); p.add_argument("--case-id",required=True)
p.add_argument("--route",required=True,choices=("direct-local","direct-remote","pd-local","pd-remote"))
p.add_argument("--base-url",required=True); p.add_argument("--run-id",required=True)
p.add_argument("--out",required=True,type=Path); p.add_argument("--decoder-pod")
p.add_argument("--namespace",default="topology-measurement")
a=p.parse_args(); raw=a.plan.read_bytes(); expected=a.plan.with_name("suite.sha256").read_text().split()[0]
if hashlib.sha256(raw).hexdigest()!=expected: p.error("suite.json digest mismatch")
plan=json.loads(raw); case=next(x for x in plan["cases"] if x["case_id"]==a.case_id)
if a.out.exists(): p.error("--out exists")
if a.route.startswith("pd-") != bool(a.decoder_pod): p.error("P/D routes require --decoder-pod; direct routes forbid it")
headers={"Content-Type":"application/json","x-benchmark-run":a.run_id}; requested=None
if a.decoder_pod:
 requested=base64.b64encode(f"{a.namespace}/{a.decoder_pod}-rank-0".encode()).decode(); headers["x-benchmark-decoder"]=requested
body=case["request_body"]; record={"case_id":a.case_id,"route":a.route,"run_id":a.run_id,"request_body":body,
 "request_sha256":hashlib.sha256(json.dumps(body,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest(),
 "requested_decoder":requested,"selected_decoder":None,"status":None,"body":None,"error":None}
req=urllib.request.Request(a.base_url.rstrip("/")+"/v1/completions",data=json.dumps(body).encode(),headers=headers)
try:
 with urllib.request.urlopen(req,timeout=90) as response:
  record["status"]=response.status; record["selected_decoder"]=response.headers.get("x-benchmark-decoder")
  record["body"]=json.loads(response.read().decode(errors="replace"))
except Exception as error: record["error"]=str(error)
a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(record,sort_keys=True)+"\n")
raise SystemExit(0 if record["status"]==200 and record["error"] is None else 1)
'''


PLAN_DOC = """# Frozen known-answer qualification\n\nPreparation performs tokenization only; it sends no model request. Execution is a separate, explicitly controlled step.\n\nThe eight cases are fixed before outputs: four exact-copy and four early-context retrieval cases, with two cases at each of 512 and 8192 input tokens per family. Every case has an eight-word answer chosen by the recorded seeded rule. All 32 route/case combinations must match the gold text after outer-whitespace stripping only. Returned token IDs are diagnostic because leading whitespace is intentionally normalized. No case may be filtered, replaced or rerun selectively.\n\nThe original five-token equality failure remains evidence and remains unchanged. This suite is an additional aggregate known-answer diagnosis. Keep deployed images, TRITON_ATTN, compilation/CUDA graphs and non-eager execution unchanged. Route, worker identity and NIXL counter qualification remain separate mandatory checks.\n"""


def prepare(out, suite_seed=SUITE_SEED, criterion="gold"):
    require(not out.exists(), "output directory already exists")
    try:
        from transformers import AutoTokenizer
    except ImportError as error:
        raise SuiteError("prepare requires transformers in the selected Python environment") from error
    tokenizer = AutoTokenizer.from_pretrained(MODEL, revision=REVISION)
    suite = make_suite(tokenizer, suite_seed, criterion)
    temporary = Path(tempfile.mkdtemp(prefix="known-answer-suite-", dir=out.parent.resolve()))
    try:
        plan = temporary / "suite.json"
        plan.write_bytes(json.dumps(suite, indent=2, sort_keys=True, ensure_ascii=False).encode() + b"\n")
        (temporary / "suite.sha256").write_text(hashlib.sha256(plan.read_bytes()).hexdigest() + "  suite.json\n")
        (temporary / "client.py").write_text(CLIENT)
        (temporary / "PLAN.md").write_text(PLAN_DOC)
        temporary.replace(out.resolve())
    except Exception:
        shutil.rmtree(temporary)
        raise


def validate(plan_path, responses_path):
    raw = plan_path.read_bytes()
    digest_path = plan_path.with_name("suite.sha256")
    require(digest_path.is_file() and digest_path.read_text().split()[0] == hashlib.sha256(raw).hexdigest(),
            "suite.json digest mismatch")
    plan = json.loads(raw)
    cases = {case["case_id"]: case for case in plan["cases"]}
    require(len(cases) == 8 and plan.get("expected_requests") == 32 and
            plan.get("routes") == list(ROUTES), "frozen suite structure is invalid")
    for case in cases.values():
        require(len(case["request_body"]["prompt"]) == case["input_tokens"] and
                digest(case["request_body"]) == case["request_sha256"],
                "frozen case prompt length or request digest is invalid")
    rows = [json.loads(line) for line in responses_path.read_text().splitlines() if line.strip()]
    expected = {(case_id, route) for case_id in cases for route in ROUTES}
    actual = {(row.get("case_id"), row.get("route")) for row in rows}
    require(len(rows) == 32 and actual == expected and len(actual) == 32,
            "responses must contain every frozen case/route exactly once")
    results = []
    for row in rows:
        case = cases[row["case_id"]]
        body = row.get("body")
        choices = body.get("choices") if isinstance(body, dict) else None
        text = choices[0].get("text") if isinstance(choices, list) and choices else None
        usage = body.get("usage") if isinstance(body, dict) else None
        gold_match = (isinstance(text, str) and text.strip() == case["gold_text"])
        token_ids = choices[0].get("token_ids") if isinstance(choices, list) and choices else None
        token_ids_match = None if token_ids is None else token_ids == case["gold_token_ids"]
        actual_request = row.get("request_body")
        request_match = (isinstance(actual_request, dict) and actual_request == case["request_body"] and
                         digest(actual_request) == case["request_sha256"] and
                         row.get("request_sha256") == case["request_sha256"])
        if row["route"].startswith("direct-"):
            pin_ok = row.get("requested_decoder") in (None, "", "-") and row.get("selected_decoder") in (None, "", "-")
        else:
            pin_ok = bool(row.get("requested_decoder")) and row.get("selected_decoder") == row["requested_decoder"]
        integrity_passed = (row.get("status") == 200 and not row.get("error") and
                            request_match and pin_ok and isinstance(text, str) and
                            isinstance(usage, dict) and usage.get("prompt_tokens") == case["input_tokens"] and
                            usage.get("completion_tokens") == len(case["gold_token_ids"]))
        results.append({"case_id": row["case_id"], "route": row["route"],
                        "request_integrity_passed": bool(integrity_passed),
                        "gold_match": gold_match, "request_match": request_match,
                        "token_ids_match_diagnostic_only": token_ids_match,
                        "actual_text": text, "gold_text": case["gold_text"]})
    integrity_counts = {route: sum(item["request_integrity_passed"] for item in results
                                   if item["route"] == route) for route in ROUTES}
    gold_counts = {route: sum(item["gold_match"] for item in results if item["route"] == route)
                   for route in ROUTES}
    text_by_case = {case_id: {item["route"]: (item["actual_text"].strip()
                                                   if isinstance(item["actual_text"], str) else None)
                              for item in results if item["case_id"] == case_id}
                    for case_id in cases}
    parity_by_case = {case_id: len(set(texts.values())) == 1 for case_id, texts in text_by_case.items()}
    criterion = plan.get("qualification", {}).get("criterion", "gold")
    require(criterion in ("gold", "direct-parity"), "frozen suite criterion is invalid")
    integrity_ok = all(value == 8 for value in integrity_counts.values())
    gold_ok = all(value == 8 for value in gold_counts.values())
    parity_ok = all(parity_by_case.values())
    passed = integrity_ok and (gold_ok if criterion == "gold" else parity_ok)
    return {"validated": passed, "criterion": criterion,
            "request_integrity": {"passed": sum(integrity_counts.values()), "required": 32,
                                  "per_route": integrity_counts},
            "gold_accuracy": {"passed": sum(gold_counts.values()), "required": 32,
                              "per_route": gold_counts},
            "direct_parity": {"passed_cases": sum(parity_by_case.values()), "required_cases": 8,
                              "per_case": parity_by_case},
            "normalization": plan["normalization"], "results": results,
            "limits": "Transfer deltas, failure counters and worker stability are separate required evidence."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("prepare"); freeze.add_argument("--out", required=True, type=Path)
    freeze.add_argument("--suite-seed", type=int, default=SUITE_SEED)
    freeze.add_argument("--criterion", choices=("gold", "direct-parity"), default="gold")
    check = sub.add_parser("validate"); check.add_argument("--plan", required=True, type=Path)
    check.add_argument("--responses", required=True, type=Path); check.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        prepare(args.out, args.suite_seed, args.criterion)
        print("PASS: frozen suite prepared without model requests: " + str(args.out.resolve()))
    else:
        require(not args.out.exists(), "validation output already exists")
        result = validate(args.plan, args.responses)
        args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        gold = result["gold_accuracy"]["passed"]
        parity = result["direct_parity"]["passed_cases"]
        print(("PASS" if result["validated"] else "FAIL") +
              f": criterion={result['criterion']}, gold={gold}/32, parity={parity}/8")
        if not result["validated"]: raise SystemExit(1)


if __name__ == "__main__":
    try: main()
    except SuiteError as error: raise SystemExit("FAIL: " + str(error))
