# LLM Serving Routing

A real-model evaluation of **where to decode after prefill** in a disaggregated LLM serving system. This repository contains the deployment and measurement harness, router configurations, validation code and a completed comparison. The related llm-d-router code change is [draft PR #2870](https://github.com/llm-d/llm-d-router/pull/2870); it is **not merged**.

**Result:** the proposed fixed load allowance changed real routing decisions but **did not improve latency** in the tested configuration. Against the strongest existing policy selected during calibration, tuned soft locality scoring, it was **9.75–10.77% slower** in each of three matched comparison blocks. The complete [run report](results/2026-09-25-full-router-comparison/RESULT.md) states the protocol, evidence and limits. No performance improvement is claimed.

## What was built

- **Routing change:** an optional `loadAllowance` on llm-d-router's topology-affinity filter. After prefill is selected, the filter compares the least-loaded local and remote decoders. It keeps local candidates when the best local decoder's extra in-flight load is within the allowance; otherwise it lets all eligible decoders proceed to downstream load scoring. Existing behavior remains available when the option is omitted. The upstream implementation and review state live in [draft PR #2870](https://github.com/llm-d/llm-d-router/pull/2870).
- **Real serving setup:** two rented eight-H200 hosts running Qwen3-32B in BF16 with tensor parallelism of four GPUs per engine. One host ran a prefiller and local decoder; the other ran a remote decoder. We verified local CUDA IPC/NVLink and cross-host RDMA/InfiniBand KV paths with the llm-d sidecar and vLLM.
- **Measurement harness:** frozen request traces driven by inference-perf through Envoy and the real endpoint picker. The harness joins each response to Envoy's selected decoder, samples EPP in-flight counts and vLLM running/waiting load, verifies NIXL transfer bytes/failures, saves each completed trial off-host, and enforces independent cost and teardown deadlines. See [`infra/topology/`](infra/topology/) and the [visual walkthrough](docs/evaluation-visual.html).

## The comparison

Serving integrity passed **12 local/remote qualification requests**. Calibration then forced both decode routes under four background-load states, twice each: **16 episodes**. It selected an allowance of **0** and tuned soft locality as the strongest existing reference. The held-out phase ran five actual router policies—unrestricted load, hard topology, soft topology, an absolute load cap, and the proposed allowance—on low and high background traces, followed by confirmation trials. All **24 policy cells** completed. In total, **40 benchmark trials and 848 client requests** have saved routing and KV-transfer verification.

| Matched block | Proposed allowance vs. tuned soft locality |
|---|---:|
| Held-out block 1 | **0.581 s / 9.87% slower** mean time to first token |
| Held-out block 2 | **0.573 s / 9.75% slower** |
| Confirmation block | **0.633 s / 10.77% slower** |

Low-pressure times were nearly equal. Under high background load, the proposed rule sent more foreground probes to the remote decoder and had higher first-token latency. That association does not isolate every cause. The result covers **one model, topology and controlled workload**; it is not a theorem about every routing regime. It also does not support a speedup claim for this fixed setting.

The [compact verified run](results/2026-09-25-full-router-comparison/) contains per-request records, routes, engine-load samples, transfer checks, configuration choices, native instance histories and cleanup evidence. The raw token-bearing native reports remain in local run state and are identified by hashes; they were too large to include in Git. The published compact data are sufficient to recompute the frozen tuning and 24-cell summary:

```bash
python3 results/2026-09-25-full-router-comparison/recompute.py
```

The script was run successfully against the saved archive. It does not rent hardware.

## What this project demonstrates

The practical question was not just whether local KV movement can be faster. It was whether a **specific routing rule** makes better *whole-request* decisions once prefill, KV transfer, decode queueing and background load all contribute to time to first token. The outcome forced a clear answer for this setup: the new fixed rule lost to a tuned existing scorer. The project documents the design, production-code patch, real P/D deployment, disciplined validation, failures corrected in the harness, and the decision **not** to claim an unsupported gain.

The upstream PR is still a draft. Whether an optional threshold-based policy is useful despite this result is a maintainer decision. Further tuning on the same held-out measurements would be exploratory and would need a new hypothesis and fresh evaluation before any performance claim.

## Pointers

- [Visual architecture, harness and results](docs/evaluation-visual.html)
- [Complete comparison and interpretation](results/2026-09-25-full-router-comparison/RESULT.md)
- [Frozen workload plan](workloads/router-session/plan.json) and [serving configuration](workloads/router-session/config.json)
- [Controller](infra/topology/run-router-session.py), [route and transfer checks](infra/topology/router-session-transfer.py), [measurement analysis](infra/topology/router-session-results.py)
- [Upstream llm-d-router draft PR #2870](https://github.com/llm-d/llm-d-router/pull/2870)
- [Original design and decision context](plugin/README.md)

The evaluation was self-funded. Estimated router-project cloud spending was **$371.95 before tax**, based on native resource lifecycles rather than an invoice. All scoped paid resources were verified deleted after the final run.
