#!/usr/bin/env python3
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
