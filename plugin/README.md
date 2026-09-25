# Load-aware topology filtering

**Status:** the single fixed-allowance implementation is in [draft upstream PR #2870](https://github.com/llm-d/llm-d-router/pull/2870), not merged. The [completed real-model evaluation](../results/2026-09-25-full-router-comparison/RESULT.md) found it 9.75–10.77% slower than tuned soft locality across three matched blocks. Prompt-size-dependent configuration was deferred and was **not** implemented or measured. The result supports neither a speedup claim nor a broad claim that a threshold policy cannot help elsewhere.

## Existing behavior and prior work

llm-d can choose prefill first, then prefer nearby decode endpoints with a topology filter or scorer. The hard filter preserves locality but can retain busy local workers while less-loaded remote workers remain available. A tuned soft scorer and a capacity/load filter before topology can already mitigate this; both are required comparisons.

Abdullah Gharaibeh requested a congestion escape from local decode selection in [llm-d-router #2315](https://github.com/llm-d/llm-d-router/issues/2315#issuecomment-5409527857). The topology implementation and motivating benchmark belong to their existing authors. This work follows that request and evaluates whether a fixed load-aware rule improves the choice. The initial research used source baseline `38cb83316ea49840e10d3d180e67b08beca1d1ca`; the published draft uses the newer baseline stated above.

## Decision

Preserve the existing prefill-first cache/load decision. For eligible decode endpoints, let **L** be the lowest in-flight request count in the selected prefill worker’s locality domain and **R** the lowest outside it. Let **K** be a configured fixed load allowance.

Keep local candidates when `L − R ≤ K`. Otherwise return the eligible input set and let the existing active-request scorer choose. Prompt-size-specific allowances were a design idea, not part of this draft or the measured comparison. Request count is a load proxy, not predicted waiting time or transferred KV bytes.

Hard model, role, readiness and capacity filters precede this gate. Never restore an excluded worker. Read existing load observations once, consider every eligible local worker, define missing-input/no-match behavior, and preserve stock behavior when disabled. Verify that later scorers do not undo the intended remote escape. No second load tracker, learned predictor or engine-scheduler change is planned.

## Validation and measured result

The upstream draft passed 22 focused tests with race detection and the local presubmit checks before publication. The real serving evaluation compared **five** frozen policies, including tuned soft scoring, hard topology, an absolute active-request cap, unrestricted load routing, and this filter. Twelve serving checks, 16 forced-route calibration episodes and 24 held-out/confirmation cells completed with verified routes and KV transfer. Prompt-size-specific allowances were not evaluated.

Calibration selected allowance **0**. In three matched comparison blocks, this configuration was **9.75–10.77% slower** in mean first-token latency than the tuned soft scorer selected as reference. The observed negative result is documented with [recomputable evidence](../results/2026-09-25-full-router-comparison/RESULT.md). It applies to the measured Qwen3-32B TP4 topology and workload; no general performance advantage is claimed for this filter.
