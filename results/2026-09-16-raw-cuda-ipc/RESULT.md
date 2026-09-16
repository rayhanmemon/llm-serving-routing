# Raw CUDA IPC diagnosis on one eight-GPU H100 node (partial)

## Summary

Two controls passed on 2026-09-16: raw CUDA IPC copied and verified a 2 MiB payload on the same GPU and from GPU ordinal 0 to GPU ordinal 1 inside one eight-GPU container. The consumer opened the exported handle, copied device-to-device, synchronized, copied to host, verified all 2,097,152 bytes, closed the import, and returned success in both cases.

This is a partial diagnosis. The separate one-GPU producer and consumer pods did not run. After the two controls passed, the orchestrator timed out after 30 seconds while waiting for the control pod deletion and stopped before creating the split-pod pair. A later operator check found the pod gone, but that follow-up query is not in this evidence bundle. The split-pod case remains the missing distinguishing test.

## Scenario

- Provider shape: one Nebius `gpu-h100-sxm` node with the `8gpu-128vcpu-1600gb` preset, configured as a preemptible (spot) instance.
- Kubernetes pod: one container requesting all eight GPUs, with host IPC, host PID and host `/dev/shm` enabled.
- Image: `docker.io/vllm/vllm-openai:v0.26.0@sha256:770fe65b2c73ee74a5c42165cf3433de4048cc2cd9c57a937ca4e35aba5aa87b`.
- Probe: direct CUDA Driver API calls through Python `ctypes`; no model, vLLM inference, NIXL transfer, UCX transfer or router request.
- Allocation: 2 MiB from `cuMemAlloc_v2`, filled with byte value 73.
- Source: evaluation repository commit `6b619cc9f70daef29482e8853275a3f874407061`.

The probe saw eight CUDA devices. `cuDriverGetVersion` returned 13000. The loaded user-space library was `/usr/lib/x86_64-linux-gnu/libcuda.so.580.173.02`; the kernel reported NVIDIA open kernel module 580.173.02. These are observed runtime facts, not a complete CUDA toolkit inventory.

## Results

| Case | Processes | Producer → consumer | Result | Verification |
|---|---|---:|---|---|
| Same GPU | separate processes, same container | ordinal 0 → 0 | PASS | all bytes were 73; checksum 153,092,096; SHA-256 `917a183dbbbce30ffd53cb9b09eb16e854c4450fda03d636641cfa1b321b2ac4` |
| Cross GPU | separate processes, same container | ordinal 0 → 1 | PASS | all bytes were 73; checksum 153,092,096; SHA-256 `917a183dbbbce30ffd53cb9b09eb16e854c4450fda03d636641cfa1b321b2ac4` |
| Split pods | separate one-GPU pods | not run | MISSING | control-pod deletion timed out before this case began |

Every recorded CUDA API call in the two completed paths returned code 0. The consumer paths include successful `cuIpcOpenMemHandle`, `cuMemcpyDtoD_v2`, `cuCtxSynchronize`, `cuMemcpyDtoH_v2` and `cuIpcCloseMemHandle` calls. Both import closures and both producer allocations were cleaned up successfully.

Elapsed times in the probe records are diagnostic control-flow durations, not transfer benchmarks. The same-GPU consumer recorded 1.046 seconds and the cross-GPU consumer 0.496 seconds, but each includes process, file-handshake, allocation, synchronization and host-verification work. No bandwidth comparison is valid from these values.

## What this narrows

The earlier `cuIpcOpenMemHandle` rejection is not explained by a general inability of this H100 node, driver or loaded `libcuda` to perform raw CUDA IPC. It is also not explained by crossing from GPU 0 to GPU 1 when both processes run in the same eight-GPU container. Container boundaries, one-GPU device exposure, allocation provenance, and differences in the vLLM/NIXL/UCX path remain live explanations.

This run does not establish that raw CUDA IPC works across two Kubernetes pods, that one-GPU containers can import each other's handles, or that the exact physical GPU pair used by the model path succeeds. It does not trace vLLM's allocator, reproduce the model allocation, measure NVLink bandwidth, qualify the local transfer path, compare local with remote decode, or support a routing-policy performance claim.

## Stop, cleanup and cost

The diagnostic ran from 14:11:52 to 14:21:58 UTC. Its top-level result is false only because the post-control pod deletion returned a timeout; the two completed cases independently record `PASS`. Provider cleanup began at 14:25:46 UTC. Terraform destroyed all three managed resources, and an independent project-scoped check found zero instances, Kubernetes clusters, disks, filesystems and GPU clusters at **14:34:56.717943 UTC**. Other resource types and other projects were not checked.

The provider records place the compute create operation at 14:10:11.075027 UTC and its completion at 14:14:17.400719 UTC. The delete operation ran from 14:30:20.585769 to 14:31:31.689373 UTC. The operator allowed cleanup to continue for up to ten extra minutes because provider deletion was already in progress; this did not authorize or launch another diagnostic.

Estimated cost: **$6.13 before tax**, calculated conservatively as **$6.1185** for 1,280.614346 seconds from compute create-operation start through delete-operation finish at $17.20/hour, plus **$0.0121** for the 256 GiB SSD over the full 1,750.036334-second session. This is not invoice-verified. Cumulative estimated H100 evaluation cost is **$26.07**, leaving **$23.93** of the $50 authorization.

A split-pod-first follow-up was prepared after this run and had not been launched when this record was finalized.

## Evidence

[`evidence.tar.gz`](evidence.tar.gz) contains the sanitized source record, runtime environment, pod specification/status, two per-case probe records, orchestration stop record, rendered diagnostic shape, cost calculation, provider operation timestamps and cleanup verification. [`evidence-manifest.json`](evidence-manifest.json) lists each member's SHA-256 hash and size. Credentials, kubeconfig, approval and private session-control records, provider resource identifiers, GPU UUIDs, process IDs, memory addresses and raw CUDA IPC handles are excluded.
