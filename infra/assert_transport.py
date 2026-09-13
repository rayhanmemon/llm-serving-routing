#!/usr/bin/env python3
"""
The transport assertion: a small client that FAILS THE RUN if the key/value
cache did not move, or moved over a wire other than the one declared.

STARTING CONFIGURATION. The defaults describe two single-GPU L40S machines
using TCP and a small model. Validate the namespace, model and declared
transport for the actual router evaluation, and derive the transfer bands on
that deployment. This script supplies no measured result.

This is a path check, not a load generator. It prevents a successful request
from being mistaken for proof that the declared transport carried the cache.
Use it for remote-prefill probes; intentional local processing by the router
legitimately transfers no remote cache.

What it does, in this order:
  1. finds the prefill and decode pods (llm-d's role label plus the pool label)
     and prints which node each landed on;
  2. reads, BEFORE sending anything: the endpoint picker's ready-endpoints gauge
     and its routing-decision counter — through the picker's Service on port
     9090, since the picker image is distroless and cannot be exec-ed into —
     and, from each vLLM pod through a port-forward, the decode side's
     transfer-bytes and transfer-time histograms and the prefill side's
     expired-request and failed-transfer counters;
  3. sends N streaming completions through the router, each with a UNIQUE
     random prefix so the decode pod's prefix cache cannot shrink the pull to a
     single block, recording each request's time to first token;
  4. reads everything again and asserts, in order:
       a. the routing-decision counter moved   — else the SPLIT is not
          happening, which is not a transport problem;
       b. bytes moved and time was spent       — else the split happened and the
          transfer itself is broken;
       c. the transport the library reported in the decode engine's log (needs
          UCX_PROTO_INFO=y on both containers) is the declared one, if
          --expect-transport was given; with nothing declared it is recorded;
       d. the throughput is inside --band, if --band was given — optional, and
          the band is always re-derived on the hardware under test, never
          imported from another machine;
  5. prints the computed reference (bytes per token x prompt tokens, marked
     "computed - not graded") beside the measured megabytes per request; the
     throughput beside the transport name (the pairing IS the assertion); the
     transfer-cost line (the transfer time implied by the measured rate beside
     the measured per-request transfer time, and that time as a share of the
     median time to first token); the ratio verdict if --other-arm-mbs was given.

Usage — two machines, the library's default transport menu, asserting the wire:
    python3 assert_transport.py --expect-transport tcp --requests 20 \
        --input-tokens 1800 --require-different-nodes
The same pair with the menu forced to a known transport, to compare paths:
    python3 assert_transport.py --expect-transport tcp --requests 20 \
        --input-tokens 1800 --require-different-nodes --other-arm-mbs <the other arm>
One machine, same file — record whatever the library chose:
    python3 assert_transport.py --requests 20 --input-tokens 1800

Exit 0 = the cache moved, over the wire declared (or, with nothing declared,
         over the wire recorded).
Exit 1 = something is not what the YAML claims. That is a GOOD outcome — it
         means the instrument caught something. A red assertion is a failed run,
         not a slow data point.
Exit 2 = the script could not do its job (no pods, no port-forward).
"""
import argparse
import json
import random
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

# --------------------------------------------------------------- metric names
# vLLM v0.26.0, read from vllm/distributed/kv_transfer/kv_connector/v1/nixl/stats.py
# in the pinned image. Bytes and time are HISTOGRAMS (so the
# exposition carries _bucket, _count, _sum and _created samples — only _sum and
# _count are read; _created is a timestamp, not a total). The expiry and failure
# counters are Counters, which the Prometheus client exposes with a _total
# suffix; both spellings are tried.
M_BYTES_SUM = ("vllm:nixl_bytes_transferred_sum",)
M_BYTES_COUNT = ("vllm:nixl_bytes_transferred_count",)
M_TIME_SUM = ("vllm:nixl_xfer_time_seconds_sum",)
M_EXPIRED = ("vllm:nixl_num_kv_expired_reqs_total", "vllm:nixl_num_kv_expired_reqs")
M_FAILED = ("vllm:nixl_num_failed_transfers_total", "vllm:nixl_num_failed_transfers")
# The endpoint picker (llm-d-router-endpoint-picker v0.10.0). These names are
# present in the binary; which of them actually MOVE is recorded from a real
# scrape before any measured run, never assumed. The current routing-decision
# counter first, the deprecated handler's counter as a fallback, and the
# ready-endpoints gauge.
M_DECISION = ("llm_d_epp_disagg_decision_total",)
M_DECISION_OLD = ("llm_d_epp_pd_decision_total",)
M_READY = ("llm_d_epp_ready_endpoints",)

# UCX transport names as they appear in the selection printout ("tcp/eth0",
# "rc_mlx5/mlx5_0:1", "cuda_ipc/cuda", "sysv/memory" ...). Grouped so
# --expect-transport can name a transport or a family.
FABRIC = {"rc_mlx5", "rc_verbs", "dc_mlx5", "ud_mlx5", "ud_verbs", "rc", "ud", "dc"}
SHARED = {"sysv", "posix", "sm", "shm", "cuda_ipc", "self"}
KNOWN = FABRIC | SHARED | {"tcp", "cuda_copy"}
GROUPS = {"tcp": {"tcp"}, "fabric": FABRIC, "rdma": FABRIC, "infiniband": FABRIC, "shared-memory": SHARED}


# --------------------------------------------------------------- kubectl glue
def kc(ns, *args, check=True):
    r = subprocess.run(["kubectl", "-n", ns, *args], capture_output=True, text=True)
    if check and r.returncode:
        print(f"kubectl {' '.join(args)} failed:\n{r.stderr}", file=sys.stderr)
        sys.exit(2)
    return r.stdout


def pods_of(ns, role, guide):
    sel = f"llm-d.ai/role={role}" + (f",llm-d.ai/guide={guide}" if guide else "")
    out = kc(ns, "get", "pods", "-l", sel, "-o",
             "jsonpath={range .items[*]}{.metadata.name}={.spec.nodeName} {end}")
    pods = [tuple(x.split("=", 1)) for x in out.split() if "=" in x]
    if not pods:
        print(f"FAIL: no {role} pod matches '{sel}' in namespace {ns}", file=sys.stderr)
        sys.exit(2)
    return pods


@contextmanager
def port_forward(ns, target, remote_port):
    """Forward a random local port to <target>:<remote_port>; yield the local port.
    target is 'pod/<name>' or 'svc/<name>'. The picker is reached ONLY this way."""
    p = subprocess.Popen(
        ["kubectl", "-n", ns, "port-forward", "--address", "127.0.0.1", target, f"0:{remote_port}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    local = None
    deadline = time.time() + 40
    while time.time() < deadline and local is None:
        line = p.stdout.readline()
        if not line:
            if p.poll() is not None:
                break
            time.sleep(0.1)
            continue
        m = re.search(r"Forwarding from 127\.0\.0\.1:(\d+) ->", line)
        if m:
            local = int(m.group(1))
    if local is None:
        p.kill()
        print(f"FAIL: kubectl port-forward {target} {remote_port} did not start", file=sys.stderr)
        sys.exit(2)
    # keep kubectl's stdout drained ("Handling connection for ..." per request)
    threading.Thread(target=lambda: [None for _ in p.stdout], daemon=True).start()
    try:
        yield local
    finally:
        p.kill()
        p.wait()


def fetch(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode()


def scrape_pod(ns, pod, port):
    with port_forward(ns, f"pod/{pod}", port) as lp:
        return fetch(f"http://127.0.0.1:{lp}/metrics")


def scrape_epp(ns, release):
    with port_forward(ns, f"svc/{release}-epp", 9090) as lp:
        return fetch(f"http://127.0.0.1:{lp}/metrics")


# --------------------------------------------------------------- metrics text
def samples(text):
    """{metric name -> sum of its samples across label sets}. The name is the
    text before '{' or the first space, so a histogram's _sum, _count, _bucket
    and _created land under distinct names and never mix."""
    acc = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[{ ]", line, 1)[0]
        try:
            acc[name] = acc.get(name, 0.0) + float(line.rsplit(None, 1)[1])
        except (ValueError, IndexError):
            pass
    return acc


def total(text, names):
    s = samples(text)
    return sum(s.get(n, 0.0) for n in names)


def present(text, names):
    s = samples(text)
    return any(n in s for n in names)


def labelled(text, name):
    """{label string -> value} for one metric name, to show the decision counter's per-outcome breakdown."""
    out = {}
    for line in text.splitlines():
        if line.startswith(name + "{"):
            labels = line[len(name) + 1:line.index("}")]
            try:
                out[labels] = float(line.rsplit(None, 1)[1])
            except (ValueError, IndexError):
                pass
    return out


# --------------------------------------------------------------- main
ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--namespace", default="disagg-boundary")
ap.add_argument("--release", default="disagg-boundary",
                help="the router's Helm release; its Service is <release>-epp (one release per arm when arms run side by side)")
ap.add_argument("--guide-label", default="disagg-boundary",
                help="the pool label value (llm-d.ai/guide) that selects this arm's pods; '' to select on the role label alone")
ap.add_argument("--model", default="Qwen/Qwen3-0.6B")
ap.add_argument("--requests", type=int, default=16)
ap.add_argument("--input-tokens", type=int, default=1800,
                help="repeated-word prompt length; a unique random prefix is added to every request")
ap.add_argument("--max-tokens", type=int, default=16)
ap.add_argument("--expect-transport", default=None,
                help="a UCX transport name (tcp, rc_mlx5, cuda_ipc, ...) or a family (tcp, fabric, shared-memory); omitted = record, do not assert")
ap.add_argument("--band", default=None,
                help="optional throughput band LOW:HIGH in MB/s. Whatever is passed here is DERIVED ON THE HARDWARE "
                     "UNDER TEST — the threshold for 'this fell back' is re-derived per cluster, never imported")
ap.add_argument("--ratio-band", default="0.85:1.15",
                help="the interpretation rule, fixed before the comparison run: a forced/default throughput ratio "
                     "inside this band means the default arm was already taking the forced arm's path")
ap.add_argument("--other-arm-mbs", type=float, default=None, help="MB/s measured on the other arm, to apply the ratio rule")
ap.add_argument("--bytes-per-token", type=float, default=114688,
                help="the cache size per token, DERIVED FROM THE MODEL CONFIG and not measured: layers x kv heads x "
                     "head dim x 2 (key and value) x bytes per element. The default is that arithmetic for the "
                     "rehearsal model (28 x 8 x 128 x 2 x 2); recompute it for the model under test")
ap.add_argument("--require-different-nodes", action="store_true",
                help="fail unless prefill and decode sit on different machines; omit for deliberately colocated arms")
ap.add_argument("--settle", type=float, default=5.0, help="seconds to wait after the last request before the second scrape")
ap.add_argument("--log-tail", type=int, default=6000)
a = ap.parse_args()
NS, REL = a.namespace, a.release
band = tuple(float(x) for x in a.band.split(":")) if a.band else None
rlo, rhi = (float(x) for x in a.ratio_band.split(":"))

prefill = pods_of(NS, "prefill", a.guide_label)
decode = pods_of(NS, "decode", a.guide_label)
print("prefill pods:", ", ".join(f"{n} on {node}" for n, node in prefill))
print("decode  pods:", ", ".join(f"{n} on {node}" for n, node in decode))

# --- placement ---------------------------------------------------------------
hosts = {node for _, node in prefill + decode}
if a.require_different_nodes and len(hosts) < 2:
    print("\nFAIL: prefill and decode are on the SAME node. The cache never crossed a "
          "network, so nothing below measures a cross-machine transfer.")
    sys.exit(1)
print("placement:", "different machines" if len(hosts) > 1 else "ONE machine — the wire is whatever the library can reach")

# --- the transport menu as configured, for the record ---------------------------
menu = kc(NS, "get", "pod", decode[0][0], "-o",
          "jsonpath={range .spec.containers[?(@.name=='modelserver')].env[*]}{.name}={.value} {end}", check=False)
ucx_tls = [kv for kv in menu.split() if kv.startswith("UCX_TLS=")]
print("UCX_TLS on the decode engine:", ucx_tls[0].split("=", 1)[1] if ucx_tls else "(unset — the library's full default menu; read the menu from its own printout)")

# --- baseline scrapes: picker, BOTH sides -------------------------------------
# Transfer bytes and transfer time live on the DECODE side, because the decode
# worker executes the pull; scraping the producing side for them returns nothing
# at all, which reads like a broken transfer and is not one. The counter for
# cache blocks that EXPIRED before the reader pulled them is on the PREFILL
# side. A successful client status alone cannot establish that no cache
# expired. Scrape both.
e0 = scrape_epp(NS, REL)
ready = total(e0, M_READY) if present(e0, M_READY) else None
print(f"picker ready endpoints: {ready if ready is not None else 'gauge not present'}")
if ready is not None and ready <= 0:
    print("\nFAIL: the picker sees ZERO ready endpoints — an empty pool (pool label mismatch, or pods not Ready). "
          "Nothing sent; fix the label before touching anything else.")
    sys.exit(1)
d0 = {p: scrape_pod(NS, p, 8200) for p, _ in decode}
p0 = {p: scrape_pod(NS, p, 8000) for p, _ in prefill}

use_old = not present(e0, M_DECISION) and present(e0, M_DECISION_OLD)
dec_names = M_DECISION_OLD if use_old else M_DECISION
dec0 = total(e0, dec_names)
bytes0 = sum(total(t, M_BYTES_SUM) for t in d0.values())
count0 = sum(total(t, M_BYTES_COUNT) for t in d0.values())
time0 = sum(total(t, M_TIME_SUM) for t in d0.values())
exp0 = sum(total(t, M_EXPIRED) for t in p0.values())
fail0 = sum(total(t, M_FAILED) for t in p0.values())
if not any(present(t, M_BYTES_SUM) for t in d0.values()):
    print("WARN: no vllm:nixl_bytes_transferred samples on the decode side yet — normal before the first "
          "transfer; if it is still absent after the requests, the metric names have moved: scrape /metrics "
          "by hand and grep for nixl.")

# --- drive N completions, each with a unique prefix ----------------------------
# The prefix defeats prefix caching: the first block of every prompt differs, so
# no request's cache can be served from an earlier request's blocks and every
# pull is the full prompt. Identical prompts would shrink the pull to one block
# after the first request and make the MB/request meaningless.
body_words = ("token " * a.input_tokens).strip()
ttfts, statuses, prompt_tokens = [], [], []
print(f"\nsending {a.requests} streaming completions (~{a.input_tokens} tokens each, unique prefix) via svc/{REL}-epp ...")
with port_forward(NS, f"svc/{REL}-epp", 80) as lp:
    url = f"http://127.0.0.1:{lp}/v1/completions"
    for i in range(a.requests):
        prompt = f"request {random.getrandbits(48):012x} " + body_words
        body = json.dumps({"model": a.model, "prompt": prompt, "max_tokens": a.max_tokens,
                           "stream": True, "stream_options": {"include_usage": True}}).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        t0 = time.time()
        first = None
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                statuses.append(r.status)
                for raw in r:
                    line = raw.decode(errors="replace").strip()
                    if not line.startswith("data:") or line.endswith("[DONE]"):
                        continue
                    if first is None:
                        first = time.time() - t0
                    try:
                        chunk = json.loads(line[5:].strip())
                        if chunk.get("usage") and chunk["usage"].get("prompt_tokens"):
                            prompt_tokens.append(chunk["usage"]["prompt_tokens"])
                    except ValueError:
                        pass
        except urllib.error.HTTPError as e:
            statuses.append(e.code)
        except (urllib.error.URLError, OSError) as e:
            statuses.append(f"error:{e}")
        if first is not None:
            ttfts.append(first)
        print(f"  {i + 1:3d}: status {statuses[-1]}  ttft {first:.3f}s" if first is not None else f"  {i + 1:3d}: status {statuses[-1]}  (no token)")
time.sleep(a.settle)

# --- second scrapes -------------------------------------------------------------
e1 = scrape_epp(NS, REL)
d1 = {p: scrape_pod(NS, p, 8200) for p, _ in decode}
p1 = {p: scrape_pod(NS, p, 8000) for p, _ in prefill}
dec1 = total(e1, dec_names)
bytes1 = sum(total(t, M_BYTES_SUM) for t in d1.values())
count1 = sum(total(t, M_BYTES_COUNT) for t in d1.values())
time1 = sum(total(t, M_TIME_SUM) for t in d1.values())
exp1 = sum(total(t, M_EXPIRED) for t in p1.values())
fail1 = sum(total(t, M_FAILED) for t in p1.values())

# --- a. ASSERT THE SPLIT BEFORE ASSERTING THE TRANSPORT ---------------------------
# A zero-byte transfer counter has two very different causes with nothing in
# common and different fixes: (1) requests silently fell back to non-split
# serving, so no transfer was ever attempted; (2) the split happened and the
# transfer broke. From the outside they look identical. The routing-decision
# counter separates them.
print(f"\nrouting decisions ({dec_names[0]}{' — the DEPRECATED handler counter' if use_old else ''}): {dec0:.0f} -> {dec1:.0f}")
before, after = labelled(e0, dec_names[0]), labelled(e1, dec_names[0])
for lab in sorted(after):
    delta = after[lab] - before.get(lab, 0.0)
    if delta:
        print(f"    +{delta:.0f}  {{{lab}}}")
if dec1 <= dec0:
    print("\nFAIL: the routing-decision counter did not move. The prefill/decode SPLIT is "
          "not happening — this is not a transport problem. Check the decider's two "
          "references in the router values (the plugin and deciders.prefill), that the "
          "picker was restarted after the last values change, and the pool's ready "
          "endpoints — before touching any transport setting.")
    sys.exit(1)

# --- the transport the library actually reported ----------------------------------
transports, sel_lines, menu_lines = {}, [], []
for p, _ in decode:
    logs = kc(NS, "logs", p, "-c", "modelserver", f"--tail={a.log_tail}", check=False)
    for line in logs.splitlines():
        if "UCX_TLS" in line:
            menu_lines.append(line.strip())
        for m in re.finditer(r"\b(" + "|".join(sorted(KNOWN, key=len, reverse=True)) + r")/[\w:.\-]+", line):
            transports[m.group(1)] = transports.get(m.group(1), 0) + 1
            sel_lines.append(line.strip())
if transports:
    print("\ntransport selection lines in the decode engine log (UCX_PROTO_INFO=y):")
    for line in sel_lines[-6:]:
        print("   ", line[:200])
    print("   transports named:", ", ".join(f"{k} x{v}" for k, v in sorted(transports.items(), key=lambda kv: -kv[1])))
if menu_lines:
    print("   the menu, as the library printed it:", menu_lines[-1][:200])
if any(t in FABRIC for t in transports):
    wire = max((t for t in transports if t in FABRIC), key=transports.get)
elif "tcp" in transports:
    wire = "tcp"
elif any(t in SHARED for t in transports):
    wire = max((t for t in transports if t in SHARED), key=transports.get)
else:
    wire = "UNKNOWN"
print(f"WIRE the library reported for the data path: {wire}")

# --- b. the numbers -------------------------------------------------------------------
dbytes, dtime, dcount = bytes1 - bytes0, time1 - time0, count1 - count0
if dtime <= 0 or dbytes <= 0:
    print(f"\nbytes moved: {dbytes:.0f}   transfer time: {dtime:.4f}s")
    print("FAIL: the split happened but no bytes moved. The transfer itself is broken. "
          "Check the side-channel host/port on both engines and both engine logs for the handshake.")
    sys.exit(1)

n_xfer = dcount if dcount > 0 else a.requests
mbs = (dbytes / 1e6) / dtime
per_req_mb = (dbytes / 1e6) / n_xfer
per_req_time = dtime / n_xfer
tok = statistics.mean(prompt_tokens) if prompt_tokens else a.input_tokens
computed_mb = a.bytes_per_token * tok / 1e6
print(f"\nMB per request:     measured {per_req_mb:,.1f} MB over {n_xfer:.0f} transfers   |   computed — not graded: "
      f"{computed_mb:,.1f} MB ({a.bytes_per_token:,.0f} bytes/token x {tok:,.0f} prompt tokens)")
print(f"transfer time:      {dtime:.3f} s total, {per_req_time * 1000:.1f} ms per request")
print(f"THROUGHPUT:         {mbs:,.1f} MB/s   over   {wire}      <- the pairing is the assertion; the number alone is not")
if ttfts:
    med = statistics.median(ttfts)
    print(f"transfer cost:      transfer time implied by the measured rate {computed_mb / mbs * 1000:.1f} ms; measured "
          f"{per_req_time * 1000:.1f} ms; median time to first token {med * 1000:.0f} ms -> transfer is "
          f"{100 * per_req_time / med:.0f}% of it   (true of the hardware this ran on and nothing else; re-derived per cluster)")
print(f"expired on prefill: {exp0:.0f} -> {exp1:.0f}    failed transfers: {fail0:.0f} -> {fail1:.0f}")
if exp1 > exp0:
    print("  ^^ cache blocks expired before the reader pulled them. Inspect client streams and server events to establish their effect.")
bad = [s for s in statuses if s != 200]
if bad:
    print(f"non-200 statuses:   {bad}")

ok = True
# --- c. THE PAIRING IS THE ASSERTION ---------------------------------------------------
if wire == "UNKNOWN":
    print("\nWARN: no transport-selection line found in the decode engine log. Is UCX_PROTO_INFO=y set on "
          "BOTH containers, and is --log-tail large enough? Without the printout this script is guessing.")
if a.expect_transport:
    want = GROUPS.get(a.expect_transport.lower(), {a.expect_transport.lower()})
    if wire not in want:
        print(f"\nFAIL: expected transport {a.expect_transport!r}, the library reported {wire!r}. "
              "This run is a FAILED run, not a slow data point.")
        ok = False
# --- d. the optional band --------------------------------------------------------------
if band and not (band[0] <= mbs <= band[1]):
    print(f"\nFAIL: {mbs:,.1f} MB/s is outside the band {band[0]}-{band[1]} MB/s")
    ok = False

# --- the ratio rule — fixed before the comparison run, never after -----------------------
if a.other_arm_mbs:
    ratio = mbs / a.other_arm_mbs
    print(f"\nthis arm / other arm throughput ratio: {ratio:.2f}  (rule band {rlo}-{rhi})")
    if rlo <= ratio <= rhi:
        print("  => VERDICT: the two arms took the SAME path — the default was already the forced arm's transport; forcing it changed nothing.")
    else:
        print("  => VERDICT: the two arms took DIFFERENT paths. The default was not TCP.")

print(f"\nSUMMARY wire={wire} mbs={mbs:.1f} mb_per_request={per_req_mb:.1f} transfers={n_xfer:.0f} "
      f"decisions=+{dec1 - dec0:.0f} expired=+{exp1 - exp0:.0f} failed=+{fail1 - fail0:.0f} "
      f"placement={'different-machines' if len(hosts) > 1 else 'one-machine'}")
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
