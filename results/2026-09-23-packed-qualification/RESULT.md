# Packed local and remote transfer qualified; policy comparison incomplete

**Both packed serving paths passed. The run then stopped because our collector mishandled a growing log file.** All resources were independently verified deleted at **2026-09-23 08:17:34 UTC**. No held-out routing-policy comparison ran, and no router improvement is claimed.

## What completed

Qwen3-32B BF16 ran with four-way tensor parallelism and the fixed BHLNC cache layout. Local prefill/decode shared one eight-H200 host; the four-GPU remote decoder ran on a second eight-H200 host on the same InfiniBand fabric. Every rented GPU counts toward cost.

Twelve logical qualification requests completed: six per location, comprising direct and P/D versions of a 4K prompt, a 120K prompt and a repeated 120K prompt. All 12 matched the expected answers. Output parity, payload, actual layout and selected transport passed on all four receiving GPU ranks for each route: local CUDA-IPC zero-copy and remote RDMA. Both engines remained alive as the real llm-d router started calibration.

| Input tokens | KV bytes across four receiving ranks | Local mean rank-transfer time | Remote mean rank-transfer time | Logical P/D requests per route |
|---|---:|---:|---:|---:|
| 4,096 | 1 GiB | 2.76 ms | 8.92 ms | 1 |
| 122,880 | 30 GiB | 43.32 ms | 180.28 ms | 2 |

These are connector transfer metrics averaged over the receiving ranks, then over the two long-prompt requests. Ranks are not independent repetitions. The long-request observations were 44.80/41.85 ms local and 181.72/178.85 ms remote. This small qualification sample demonstrates a transfer tradeoff in this fixture. It is not client TTFT, a controlled before/after packing speedup, a policy benchmark, or a production-wide claim.

## What failed — our collector

After the second calibration batch, GNU tar returned exit 1 with `tar: ./prefill.log: file changed as we read it`. The running engine was still appending diagnostics while the collector archived `/results`. The collector treated every nonzero exit as incomplete evidence and aborted the run. That strict treatment of a growing diagnostic log was a harness mistake. Local rehearsals had not reproduced continuously growing engine logs. This failure occurred after successful GPU/model operation; it was not a Nebius placement failure.

The failure handler's second collection succeeded, preserving both qualification records and both completed client calibration batches. Teardown had already been selected; the full comparison did not continue. Original failure details remain in the private run record; `failure-summary.json` records the bounded diagnostic without embedding the binary archive in an exception string.

## Recovered calibration — incomplete, not balanced evidence

The real router served 26 calibration requests: 8 foreground probes and 18 background requests across load states (1,0) and (9,8). Both batches have complete client records. Offline validation after the failure handler's successful collection matched every route and verified the transfer counters/payload; `transfer-verified-recovered.json` explicitly labels that recovery. The first batch also retains its original runtime verification marker.

Only the first route-order repeat ran. The opposite-order repeats and remaining load states are missing. In particular, observed long-prompt client TTFT around 11 seconds for the first route and 22 seconds for the second is dominated by shared-prefill ordering; it must not be advertised as a locality speedup. No tuning result, useful-crossover decision, held-out policy result or success criterion is complete.

## Cost and remaining work

Conservative lifecycle estimate: **$25.50 before tax**, including both GPU hosts and supporting resources. Native create/delete histories and the calculation are retained; this is not an invoice. The original $70 pool has used about **$25.91** across its three attempts, leaving **$44.09**. Router-project lifecycle accounting is about **$230.71**.

The collector now captures bounded log prefixes and verifies file inventories, sizes and SHA-256 checksums. Changed structured data, truncated files, missing completion markers and corrupt evidence still fail. The local suite ran 261 tests (258 passed on macOS; three Linux-only cases separately passed in the pinned Linux image). A real-router/sidecar replay served 46 requests across all five policies while both synthetic engines continuously appended logs. Every trial checked live Pod identities and validated the collected snapshots. Native vLLM parsing/layout/worker checks, local Kubernetes manifests, job launch and deadline acknowledgement also passed. The temporary test cluster was deleted; the learning cluster was preserved. These checks validate the repaired collection path locally, not a completed GPU policy comparison. Paid launches are paused: the old prepared full run requires $69 admission, exceeding the remaining balance. No budget increase or changed measurement protocol is assumed.

Data: gzipped qualification records, raw calibration batches, recovered validation records, model/workload/plan, cost histories and cleanup proof are included. Compressed qualification JSON retains full native evidence and can be read with Python's gzip/json modules. Personal lab results are not part of this evidence.
