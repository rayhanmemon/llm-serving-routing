# Real P/D correctness passed; fast local transport remains unqualified

The H100 allocation succeeded and served correct real-model requests through llm-d. Direct decode, correctly pinned local prefill/decode (P/D), and correctly pinned remote P/D returned identical output. **The 48-request latency benchmark was not run:** a separate transfer diagnostic selected TCP after CUDA rejected the peer's IPC memory handle. The intended fast local path therefore remains unqualified.

## Setup and scope

One eight-H100 VM hosted one prefiller and one local decoder on separate GPUs; a one-H100 VM hosted the remote decoder. A CPU VM ran the router, Envoy and client. The scarce eight-GPU node was allocated before the other nodes. Three GPUs served inference; nine were rented.

Software: published router commit `0217d29924ba93b90f952e7a0281dd8dda146703`, Qwen3-8B BF16 at revision `b968826d9c46dd6066d109eabc6255188de91218`, vLLM 0.26.0, sidecar 0.10.0, NIXL 1.3.1, UCX 1.21.0 and driver 580.173.02. One GPU per engine, prefix caching disabled. The prefill-first routing configuration used the diagnostic endpoint pin and no topology preference. This does not evaluate the new allowance policy.

The deployed manifests are in [config/](config/). The source/image and hardware identities are recorded alongside this report. The final fixture fixes are committed at `1daa3c7` in the evaluation repository; the upstream router implementation was unchanged.

## Correctness evidence

Each correctness request used the same five-token prompt, 16 output tokens, temperature 0 and seed 17. These were non-streaming completion probes, not the planned 512/8192-token streaming benchmark.

| Path | Result | Transfer evidence |
|---|---|---|
| Direct decode | HTTP 200, 16 tokens | Decoder NIXL transfer counters stayed zero. |
| Local P/D, corrected pin | HTTP 200, same output, exact requested/selected endpoint | One additional received NIXL transfer ; 9,437,184 bytes. |
| Remote P/D, corrected pin | HTTP 200, same output, exact requested/selected endpoint | One received NIXL transfer ; 9,437,184 bytes. |

One earlier local P/D probe used a bare Pod name as its pin. The filter failed open and local happened to win. Its correct output is retained, but its routing assertion is explicitly excluded. Four inference requests were issued in total; three routes passed the qualified correctness checks. The local counter totals two transfers because it includes the excluded probe; remote totals one.

All serving Pod/container identities and all three GPU UUIDs remained unchanged across the correctness snapshots. Transfer/notification failure counters remained zero. Four complete collections have checked file hashes. [Correctness summary](correctness-summary.json).

## Problems found and corrections

1. **UCX startup:** default transport selection tried InfiniBand registration with an 8 MiB locked-memory limit and failed NIXL backend creation. Before any request, all engines were changed consistently to `UCX_TLS=tcp,cuda_copy,cuda_ipc,self`, matching the intended ordinary-network remote leg and keeping CUDA IPC available. No privilege change was made. All engines then became healthy with zero restarts in their new Pods.
2. **GPU collection:** the GPU image exposes `python3`, without a `python` alias. The collector was corrected and fresh successful snapshots were taken; the earlier incomplete collection was not treated as valid.
3. **Endpoint pinning:** this router names endpoints `namespace/pod-rank-0`, where the suffix is the configured target-port index. The workload/client and validator now use that actual ID. The validator still requires exact requested/selected equality, with a regression case for the observed bare-Pod failure.

The 21 utility tests passed after these fixes. They validate the helpers; they do not establish routing-policy performance.

## Why benchmarking stopped

The actual vLLM transfer counters establish real KV movement. Its logs did not directly identify the CUDA-source READ payload protocol; host-to-CUDA PUT tables and transport availability were insufficient, and UCX output buffering complicated interpretation.

Two bounded, separate NIXL diagnostics ran inside the existing local pods, with the same environment and privileges and their own small CUDA allocations. Each READ transferred 262,144 bytes filled with 73; every byte and the checksum 19,136,512 matched. Both producer and consumer exited 0. These processes did not use the model's KV buffers.

The flushed protocol table selected **software-emulated READ over `tcp/eth0`**. The debug run then captured `cuIpcOpenMemHandle() failed: invalid argument` and `failed to open ipc mem handle`. UCX had recognized an intra-node peer and created an IPC lane before rejecting the handle. The reported backing allocation was 2 MiB; the requested transfer was 256 KiB.

This identifies the isolated diagnostic's fallback, **not a direct trace of vLLM's allocation path**. The underlying CUDA rejection is unresolved. One visible GPU per container and the GET-enable setting were checked against pinned source; neither alone justifies declaring IPC impossible or widening privileges. No NVLink performance, locality advantage, TTFT improvement or allowance benefit is claimed. The previously recorded 512/8192-token expectation remains untested.

## Cleanup and cost

Provisioning began **2026-09-16 05:02:16 UTC**. All instances, Kubernetes clusters, disks, filesystems and GPU clusters were independently verified absent at **06:09:48 UTC**. Terraform state is empty; the shutdown guard exited. Wall time: **67.5 minutes**. System PodDisruptionBudgets were removed only as part of destroying this dedicated experiment cluster.

Estimated cost: **$19.43 before tax** for this attempt, calculated conservatively from per-resource operation lifetimes plus disk allowance; not invoice-verified. Including the previous H100 placement failure, approximately **$19.94 of the $50 authorization** has been used, leaving **$30.06**. [Cost calculation](cost-estimate.json). Human attention was not recorded; execution was unattended.

Paid retries are paused because the transport problem needs a targeted correction. Before another rental, reduce the IPC-handle rejection to a small export/import test and prepare the distinguishing checks. Requalify the actual vLLM payload before running the frozen latency block. No new deployment or budget is scheduled by this report.

## Evidence

[evidence.tar.gz](evidence.tar.gz) contains 128 selected files: complete correctness snapshots, responses, startup failure/fix logs, endpoint evidence, diagnostic programs/output, provider operation timestamps and cleanup verification. [evidence-manifest.json](evidence-manifest.json) records their SHA-256 hashes. Credentials, authorization files and downloaded third-party source trees are excluded. Raw transfer timings are preserved for diagnosis and are not benchmark results.
