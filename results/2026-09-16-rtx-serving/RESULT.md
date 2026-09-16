# RTX serving session: correctness qualified, first timing block running

## Status

This regular-capacity UK session is active on eight-GPU and one-GPU RTX PRO 6000 Blackwell workers plus one CPU worker. Only one GPU serves each inference engine. No NVLink or RDMA path is claimed. The user approved the prospective direct-versus-P/D parity rule, and a fresh frozen suite passed it. The first 48-request forced-route timing block started at **18:50:51 UTC**. **No validated latency result exists yet.**

An initial image upload ended with an exec-stream EOF before inference. Setup correction `1d09dff` used a bounded SPDY upload, succeeded, and imported the unchanged reviewed image. The router and model configuration were unchanged.

## Original oracle: failed

The exact-string oracle used the same five-token prompt, 16 greedy output tokens and seed 17. It remains failed:

| Path | Exact output |
|---|---|
| Direct local decoder | ` Paris. The capital of Italy is Rome. The capital of Germany is Berlin.` |
| Local P/D | ` Paris. The capital of France is Paris. The capital of France is Paris.` |
| Remote P/D | ` Paris. The capital of Italy is Rome. The capital of Spain is Madrid.` |

All three calls returned HTTP 200. The P/D requests reached their requested decoders, transferred 9,437,184 bytes each, and recorded no NIXL transfer or notification failure. Pods stayed ready with zero restarts. These facts support successful routing and transfer; they do not prove KV-cache bit identity or rule out numerical error.

## Numerical diagnostic

Five logprob calls preserved the same request: direct-local twice, direct-remote once, and both P/D routes once. The direct-local repeats were identical. Direct-remote and local P/D both produced the repeated-France text.

The first divergence was generated token 6: direct-local selected ` Italy` over ` France` by **0.125** natural-log units, while direct-remote had an exact ` France`/` Italy` tie. Remote P/D selected ` Italy` by **0.250**. At token 13 on the Italy/Rome trajectory, direct-local had an exact ` Germany`/` Spain` tie; remote P/D selected ` Spain` by **0.125**. The route-independent ties make the original open-ended prompt a poor discriminator. They are evidence against gross corruption, not proof of P/D correctness.

vLLM [does not guarantee reproducibility by default](https://docs.vllm.ai/en/latest/usage/reproducibility/), and a maintainer explains that [kernel differences can flip greedy near-ties](https://github.com/vllm-project/vllm/issues/11526). Current releases offer opt-in, beta [batch invariance for online serving](https://docs.vllm.ai/en/stable/features/batch_invariance/). This session did not change its engine to force equality.

## Frozen known-answer suite

Eight copy/retrieval cases were committed before execution (`a9e911b`): two cases per task at each of 512 and 8192 input tokens, with eight-word expected outputs. All 32 case/route combinations ran under unchanged engine settings. The declared all-gold rule **failed: 24/32, or 6/8 on every route**. Both long retrieval cases produced the same explanatory prefix on all four routes, using part of the eight-token output limit. No case was replaced or dropped.

**All eight cases produced identical text across direct-local, direct-remote, local P/D and remote P/D.** Each P/D request added exactly one transfer; both decoders added eight transfers total. Transfer-failure counters stayed unchanged, and the three GPU identities plus worker/EPP identities stayed stable.

This demonstrates functional agreement on this small suite, including the two shared task failures. It does not turn either failed acceptance rule into a pass. The user then approved an explicit amendment: compare P/D against direct inference while reporting gold accuracy separately, and confirm it on a fresh suite frozen before its outputs.

The fresh seed-`17092027` suite was committed before execution (`ef898194df31d2601bba1e3e0fe992fb009180e2`; SHA-256 `cf6fd79fe756d6dd75d7589bb1d05cd740fb6e9d70a2307d04d970d87d4e515c`). All 32 request-integrity checks passed and all eight cases produced identical normalized text across direct-local, direct-remote, local P/D and remote P/D. Each P/D request added exactly one transfer on its selected decoder; transfer-failure counters and worker/GPU identities stayed stable. Gold accuracy remained **24/32, 6/8 per route**, and is reported separately rather than relabelled.

That prospective confirmation qualified correctness under the approved parity rule. A truthful correctness marker admitted the frozen first timing block. The timing block is still running; partial observations are not latency results.

Evidence: [original frozen plan](known-answer-suite/PLAN.md), [original known-answer analysis](known-answer-analysis.json), [original sanitized responses](known-answer-responses.json), [original numerical diagnostic](numerical-diagnostic.json), [approved amendment](method-amendment.json), [fresh confirmation plan](confirmation-suite/PLAN.md), and [fresh confirmation analysis](confirmation-analysis.json).
