# Real P/D KV transfer over NVLink: verified

On September 20 Toronto time (September 21 UTC), one Nebius H200 host completed actual prefill/decode inference with KV payloads verified on NVLink. Only one GPU host was rented: eight GPUs billed, two used. No remote host or separate CPU worker was rented.

## Deployment

One Kubernetes Pod contains an engine container allocated two GPUs and the pinned llm-d P/D sidecar. Two vLLM processes inside the engine container use `CUDA_VISIBLE_DEVICES=0` and `1`. Both GPU device allocations are exposed to that shared container. Each engine uses a different HTTP and NIXL metadata port. Pod IPC/PID namespaces remain private; `IPC_LOCK` is added, with no privileged mode or host namespaces. This is a deliberate shared-container arrangement, not proof of CUDA IPC between standard separate one-GPU Pods.

Model: **Qwen3-0.6B**, revision `c1899de289a04d12100db370d81485cdf75e47ca`; vLLM 0.26.0 and sidecar 0.10.0 with image digests pinned in the controller. BF16, TP=1, eager execution, prefix caching disabled. The smaller model avoids paying for an 8B model load during transport qualification. Do not pool these results with the earlier Qwen3-8B runs.

The UCX diagnostic allowlist is `cuda_ipc,cuda_copy,sm,self,tcp`, leaving CUDA IPC enabled and excluding RDMA. TCP remains available for control traffic and fallback; the pass requires observed CUDA-IPC payload selection and hardware counters, not merely successful requests. No CUDA-IPC GET override was needed.

## Evidence

- Raw CUDA IPC copied and checked bytes between **different physical GPU UUIDs**.
- Nine NIXL READs checked every byte at 256 KiB, 64 MiB and 1 GiB, using the same per-process GPU visibility arrangement. The three 1 GiB completion times were **4.625, 4.744 and 4.642 ms**. These are diagnostics with polling overhead, not a controlled comparison with older RDMA runs.
- **16 real inference requests:** eight fixed copy/retrieval cases on direct decode and P/D, covering 512 and 8192 input tokens. Each case produced identical normalized output on both routes. All expected token counts passed; each P/D call added one KV transfer, direct calls added none, and failure counters stayed unchanged. GPU process identities were stable.
- Gold accuracy was **8/16**, four of eight cases per route. The accepted criterion is direct/P/D parity, not universal gold correctness. The low small-model gold accuracy is preserved, not hidden or fixed by selecting cases.
- The decoder's actual GPU-memory READ protocol table selected **`cuda_ipc/cuda` zero-copy**. TCP active-message lines elsewhere in the log are control traffic, not the selected GPU payload operation.
- `nvidia-smi topo -m` reports **NV18** between the selected pair. During every P/D request, prefill-GPU NVLink transmit and decode-GPU receive deltas matched the NIXL payload within integer-counter rounding. All eight direct controls and the idle interval had **zero NVLink data deltas**.

| Input tokens | Each P/D KV payload | Observed NVLink data, each direction |
|---:|---:|---:|
| 512 | 58,720,256 bytes (56 MiB) | 57,342–57,346 KiB |
| 8192 | 939,524,096 bytes (896 MiB) | 917,502–917,506 KiB |

Counter tolerance is at most 18 KiB when summing differences of 18 integer-KiB link counters. It is a rounding bound, not a fitted performance threshold. Source transmit and destination receive matched one another; reverse-direction data was zero.

## Reproduction and limits

The worker and controller are `infra/topology/single-host-worker.py` and `run-single-host.py`. The independent offline validator is `validate-single-host.py`. Extract `evidence.tar.gz` into a directory and run the validator with `--evidence DIRECTORY --out result.json`. The case generator's full template has four routes; the local-only execution scope was explicitly frozen as direct/P/D, 16 calls, before the requests. `scope.json`, the suite and its checksum are retained.

A transient Kubernetes status-read EOF stopped the original observer while the Pod continued normally. Reconnection checked that the saved worker manifest still matched the source and did not reapply or restart the workload. Bounded retries now cover transient read errors. The earlier rejected host's provider-health failure and cost are preserved separately.

**The local transport gate passes.** This establishes a working shared-container NVLink recipe on Nebius; it does not isolate the root cause of every earlier cross-pod failure. No client TTFT benchmark, remote comparison, tuned allowance or router-policy gain is established. The next stage is to prepare the remote path and rehearse that workflow before renting it, while retaining and rechecking this local path.

## Cleanup and cost

Cleanup was independently verified at 2026-09-21T00:51:55.037030+00:00: instances, Kubernetes clusters, disks, filesystems and GPU clusters were empty in the experiment project. The successful attempt cost **$5.32**; including the rejected unhealthy host, today cost **$9.47**. Evaluation total **$99.08**, router-project total **$99.68**, and **$48.83** remains authorized. These are estimates before tax. No rental remains active.
