# Methodology for the completed routing comparison

The [September 25 run](../results/2026-09-25-full-router-comparison/RESULT.md) tested whether a fixed load-aware topology filter improves client time to first token after prefill. The [frozen trial order, parameter grid and pass criterion](../workloads/router-session/plan.json) and [serving configuration](../workloads/router-session/config.json) are the source of exact settings. The configuration file retains some pre-run status notes; the completed run report records what actually executed.

## Deployment and qualification

One eight-H200 host ran the Qwen3-32B BF16 tensor-parallel-four prefiller and local decoder. A second eight-H200 host ran the tensor-parallel-four remote decoder. The prompt lengths were 4,096 and 122,880 tokens with 32 output tokens. The engines used the same model revision, KV layout and serving settings across routing policies; prefix caching was disabled. Six local and six remote qualification requests passed answer, route and per-rank KV-transfer checks. The local path used CUDA IPC/NVLink; the cross-host path used RDMA/InfiniBand. Those are observed transfer paths, not conclusions drawn from topology labels alone.

## Calibration, then evaluation

Calibration forced local and remote decode under four background-load states, with two repeats per state and route: **16 episodes**. It used client first-token timings and sampled load to choose the fixed allowance and tune the existing soft-locality and absolute-cap baselines before evaluating their real routing decisions. The selected allowance was **0**; soft locality was the preselected existing reference. The tuning procedure and its output are in [`frozen-tuning.json`](../results/2026-09-25-full-router-comparison/frozen-tuning.json).

The held-out phase ran five actual router policies: unrestricted load scoring, hard topology filtering, tuned soft locality, an absolute load cap, and the proposed fixed allowance. Each policy ran under low and high background traces twice, for **20 cells**. Four more cells confirmed the proposed policy and preselected reference under both traces, giving **24 evaluation cells**. Their sequence and seeds were frozen in the plan. Each evaluation cell used four foreground probes; the reported **848 benchmark requests** across calibration and evaluation also count background traffic. They are not 848 independent comparisons of the proposed policy.

The pass criterion required more than 50 ms and 2% lower mean first-token latency in each matched block, within the predefined regression limits. The proposed rule was **9.75–10.77% slower** than the selected reference in the three matched blocks. The other policies were reported descriptively; small differences between them are not ranked as a stable winner.

## Measurement and limits

The harness joined client results to Envoy's selected decoder and sampled the endpoint picker's in-flight counts, vLLM running/waiting load and KV-transfer counters. It checked stream completion, worker identity, payload movement and transfer failures, then saved each completed trial off-host. The performance metric is client-observed **time to first token**, which includes prefill, transfer and decode waiting. A faster KV transfer by itself does not establish a faster request.

Preemptible-node interruptions and application failures were recorded separately. The protocol did not stitch surviving requests from an interrupted block into a complete comparison; a resumed attempt would repeat the entire affected block. The final run completed all planned cells, and the cloud cleanup was checked independently.

This is one model, topology, implementation and controlled traffic mix, with request means rather than tail-latency estimates. Route shifts and latency differences are associated observations, not a causal decomposition of every millisecond. Prompt-dependent allowances were **not implemented or evaluated**. The [reproduction guide](reproducing.md) explains how to recompute the published result without renting GPUs and which pieces of the live deployment remain provider-specific.
