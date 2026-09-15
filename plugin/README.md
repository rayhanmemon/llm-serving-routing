# Request-sensitive topology routing

**Status: [draft PR #2870](https://github.com/llm-d/llm-d-router/pull/2870) is open.** The single-allowance extension is implemented at `8f3f28382081cad500f00342c9133312eeb044d6`, based on upstream `32d4ed2ac5ff1cc09f1dc8326caa0bab56d234b4`. All 22 focused tests with race detection and local presubmit passed. Prompt-size configuration and real-model evaluation remain unfinished. GitHub verifies both commit signatures after registration of the existing public signing key. Current head `0217d29924ba93b90f952e7a0281dd8dda146703` adds an empty CI-refresh commit with an identical source tree. Hosted workflows await maintainer approval; no merge is claimed.

## Existing behavior and prior work

llm-d can choose prefill first, then prefer nearby decode endpoints with a topology filter or scorer. The hard filter preserves locality but can retain busy local workers while less-loaded remote workers remain available. A tuned soft scorer and a capacity/load filter before topology can already mitigate this; both are required comparisons.

Abdullah Gharaibeh requested a congestion escape from local decode selection in [llm-d-router #2315](https://github.com/llm-d/llm-d-router/issues/2315#issuecomment-5409527857). The topology implementation and motivating benchmark belong to their existing authors. This work follows that request and evaluates whether a small request-sensitive rule improves the choice. The initial research used source baseline `38cb83316ea49840e10d3d180e67b08beca1d1ca`; the published draft uses the newer baseline stated above.

## Decision

Preserve the existing prefill-first cache/load decision. For eligible decode endpoints, let **L** be the lowest in-flight request count in the selected prefill worker’s locality domain and **R** the lowest outside it. Let **K** be a calibrated allowance for the incoming prompt-size range.

Keep local candidates when `L − R ≤ K`. Otherwise return the eligible input set and let the existing active-request scorer choose. Two prompt-size ranges are planned; larger prompts do not automatically deserve larger allowances. Request count is a load proxy, not predicted waiting time or transferred KV bytes.

Hard model, role, readiness and capacity filters precede this gate. Never restore an excluded worker. Read existing load observations once, consider every eligible local worker, define missing-input/no-match behavior, and preserve stock behavior when disabled. Verify that later scorers do not undo the intended remote escape. No second load tracker, learned predictor or engine-scheduler change is planned.

## Validation

Test threshold boundaries, multiple local endpoints, missing observations, no local/remote choices, configuration errors, endpoint replacement, concurrent requests and disabled-mode compatibility. The performance claim targets one endpoint picker, one text model and a fixed connector/engine configuration; it does not promise exact global counts across independent pickers.

Use the [six-policy evaluation](../docs/methodology.md). Establish correct P/D execution and a real transfer-cost difference before the full comparison. A simulator can establish decision behavior, not GPU serving gains. If tuned existing configuration solves the case, or prompt-size allowances do not outperform the simpler global allowance, report that result and narrow the implementation accordingly.
