# Real-model P/D inference over RDMA: qualified; client benchmark unfinished

Two eight-H200 Nebius spot hosts shared one InfiniBand fabric. Prefill and local decode occupied separate GPUs on one host; remote decode occupied one GPU on the second host. A separate CPU node hosted the actual router and Envoy. Model: Qwen3-8B, pinned revision and vLLM 0.26.0 image, FlashAttention 3, default compilation/CUDA graphs, prefix caching disabled. Router source remained `0217d29924ba93b90f952e7a0281dd8dda146703`.

## Verified result

- **32/32 successful requests**, spanning eight fixed cases on direct-local, direct-remote, P/D-local and P/D-remote.
- **8/8 cases matched across all four routes** under the approved direct-parity rule. Gold accuracy remained **24/32 (6/8 per route)**; parity is not a claim that every answer matched the intended gold text.
- **Eight transfers per P/D route**, one per request, correct selected-decoder pins, zero transfer/notification/expiry error deltas, unchanged worker/GPU identities.
- Both decoders' actual peer protocol tables (`inter-node cfg#2`, four UCX worker threads) show CUDA-to-CUDA zero-copy READ through `rc_mlx5`. Both local and remote serving paths therefore exercised RDMA.

Pods used private IPC/PID namespaces plus `IPC_LOCK`. The diagnostic allowlist was `UCX_TLS=rc,cuda_copy,self`: TCP payload and CUDA IPC were excluded, while TCP control/metadata traffic remained possible. UCX's “inter-node” label does not establish physical separation: Pod/node identities establish the local versus remote placement.

## Limited transfer measurements

| Route | Transfers | Total bytes | Total connector time | Mean connector time |
|---|---:|---:|---:|---:|
| Local | 8 | 5,133,828,096 | 113.253 ms | 14.156625 ms |
| Remote | 8 | 5,133,828,096 | 115.467 ms | 14.433375 ms |

Each route saw four 512-token and four 8192-token prompts. These are deltas of vLLM's NIXL transfer counters collected around the correctness suite, including cold transfers. The roughly **0.28 ms difference in pooled means is not a robust locality benefit**. There are no per-prompt-size transfer-time samples, confidence intervals, client TTFT results or load-sweep results here. Neither routing-policy gain nor an allowance calibration is established.

This RDMA-only fixture does not exercise a faster local CUDA IPC/NVLink path. Near-equal transfer means cannot rule out a benefit from such a path or from other network topologies.

## Why client timings did not run

The agent staged the pre-run digest as `frozen-suite.sha256`, but the existing validator expects the adjacent filename `suite.sha256`. After all requests and both complete snapshots had been saved, that filename mismatch caused validation to exit before writing its result and triggered cleanup. This was a harness error, not failed model output or RDMA.

Offline validation supplied the expected filename from the preserved pre-run digest. Its value matches both the unchanged plan and the pre-request source record. The validator then passed; route, counters, worker/GPU identity and peer-protocol checks were independently completed against the saved evidence. No cases, requests or outputs were edited. The digest staging error now has a preflight check and regression test. The earlier duplicate-render error is retained in the separate failed-attempt report.

## Next useful step

Exercise the entire measurement orchestration locally with the actual directory and digest layout before another paid run. Then measure matched client TTFT using a qualified transport setup; allow normal UCX transport selection with IPC_LOCK and observe whether local CUDA IPC is available, rather than assuming RDMA-only co-location creates a substantial transfer advantage. Any forced-transport comparison must state that restriction. Preserve the strong-baseline routing comparison requirement; this result does not justify presenting the PR as a measured optimization.

## Cleanup and cost

Five experiment-project resource types were independently verified empty at 2026-09-17T00:00:40.913899+00:00. This retry cost **$13.68**, and the preceding failed attempt cost **$10.85**. Evaluation spend is **$89.61**, router-project spend including the earlier $0.60 attempt **$90.21**, and **$8.30** remains under the additional-$40 authorization. No new rental is running. Weekly usage was 97% used, below the 99% stop threshold. All costs are conservative estimates before tax.
