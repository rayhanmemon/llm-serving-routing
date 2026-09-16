# RTX serving session: numerical diagnostic, timing pending

## Status

This regular-capacity UK session is active on eight-GPU and one-GPU RTX PRO 6000 Blackwell workers plus one CPU worker. Only one GPU serves each inference engine. No NVLink or RDMA path is claimed. **No validated latency measurement exists yet.**

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

The frozen eight-case known-answer suite is pending. Until it passes, correctness remains unqualified and timing collection remains unvalidated. Exact sanitized response data are in [numerical-diagnostic.json](numerical-diagnostic.json).
