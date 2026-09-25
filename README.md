# LLM Serving Routing

**Locality versus load after prefill.** A nearby decoder can receive KV cache faster, but its queue may be busier than a decoder on another host. This project prototypes a fixed load-aware topology rule for llm-d and tests whether it improves the complete request in a real-model deployment. The repository contains the deployment harness, frozen workloads, route and transfer checks, and the resulting comparison. The Go change is [draft PR #2870](https://github.com/llm-d/llm-d-router/pull/2870), which remains unmerged.

**Finding:** the proposed rule changed real decoder choices but increased mean time to first token by **9.75–10.77%** against tuned soft locality in each of three matched blocks. This result rejects a performance claim for the tested fixed rule and workload. The [run report](results/2026-09-25-full-router-comparison/RESULT.md) gives the protocol and limits; the [visual walkthrough](docs/evaluation-visual.html) explains the system.

## What was built

- **Routing change:** an optional `loadAllowance` on llm-d-router's topology-affinity filter. After prefill is selected, the filter compares the least-loaded local and remote decoders. It keeps local candidates when the best local decoder's extra in-flight load is within the allowance; otherwise it lets all eligible decoders proceed to downstream load scoring. Existing behavior remains available when the option is omitted. The upstream implementation and review state live in [draft PR #2870](https://github.com/llm-d/llm-d-router/pull/2870).
- **Real serving setup:** two rented eight-H200 hosts running Qwen3-32B in BF16 with tensor parallelism of four GPUs per engine. One host ran a prefiller and local decoder; the other ran a remote decoder. We verified local CUDA IPC/NVLink and cross-host RDMA/InfiniBand KV paths with the llm-d sidecar and vLLM.
- **Measurement harness:** frozen request traces driven by inference-perf through Envoy and the real endpoint picker. The harness joins each response to Envoy's selected decoder, samples EPP in-flight counts and vLLM running/waiting load, verifies NIXL transfer bytes/failures, saves each completed trial off-host, and enforces independent cost and teardown deadlines. See [`infra/topology/`](infra/topology/).

## The comparison

Serving integrity passed **12 local/remote qualification requests**. Calibration then forced both decode routes under four background-load states, twice each: **16 episodes**. It selected an allowance of **0** and tuned soft locality as the existing reference. The held-out phase compared five router policies—unrestricted load, hard topology, soft topology, an absolute load cap, and the proposed allowance—on low and high background traces. A final block repeated the proposed rule and selected reference. All **24 policy cells** completed. Together, calibration and evaluation produced **40 verified trials and 848 client requests**; the request count includes background traffic.

| Matched block | Proposed allowance vs. tuned soft locality |
|---|---:|
| Held-out block 1 | **0.581 s / 9.87% slower** mean time to first token |
| Held-out block 2 | **0.573 s / 9.75% slower** |
| Confirmation block | **0.633 s / 10.77% slower** |

Low-pressure times were nearly equal. Under high background load, the proposed rule sent more foreground probes to the remote decoder and had higher first-token latency. That association does not isolate every cause. The result covers **one model, topology and controlled workload**, measured with request means rather than tail latency.

The [compact verified run](results/2026-09-25-full-router-comparison/) contains per-request records, routes, engine-load samples, transfer checks, configuration choices, native instance histories and cleanup evidence. The raw token-bearing native reports remain in local run state and are identified by hashes; they were too large to include in Git. The published compact data are sufficient to recompute the frozen tuning and 24-cell summary:

```bash
python3 results/2026-09-25-full-router-comparison/recompute.py
```

The script was run successfully against the saved archive. It does not rent hardware.

## Engineering finding

Faster local KV movement did not make the fixed locality rule a better *whole-request* policy. We separated calibration from evaluation, checked the actual decoder choice and data path on every trial, and compared against tuned existing policies instead of only a hard locality filter. The evidence supports rejecting this fixed rule as a latency improvement in the tested deployment. It does not establish the best rule for other models or traffic mixes.

The upstream PR remains a draft. Whether the optional configuration has value apart from a performance gain is for maintainers to decide. Further tuning on these held-out measurements would be exploratory and require fresh evaluation before any new claim.

## Pointers

- [Visual architecture, harness and results](docs/evaluation-visual.html)
- [Complete comparison and interpretation](results/2026-09-25-full-router-comparison/RESULT.md), [methodology](docs/methodology.md) and [reproduction guide](docs/reproducing.md)
- [Frozen workload plan](workloads/router-session/plan.json) and [serving configuration](workloads/router-session/config.json)
- [Controller](infra/topology/run-router-session.py), [route and transfer checks](infra/topology/router-session-transfer.py), [measurement analysis](infra/topology/router-session-results.py)
- [Upstream llm-d-router draft PR #2870](https://github.com/llm-d/llm-d-router/pull/2870)
- [Original design and decision context](plugin/README.md)
