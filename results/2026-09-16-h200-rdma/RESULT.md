# RDMA READ qualification between isolated GPU Pods

On September 16, 2026, two separate one-GPU Pods on one eight-H200 Nebius host completed **18 byte-correct NIXL READs over RDMA**. This qualifies transport; it is not an inference benchmark or routing-policy result.

## Controlled permission test

1. With `IPC_LOCK`, all nine reads passed: three repeats each of 256 KiB, 64 MiB and 1 GiB.
2. In the same consumer Pod and GPU, a child process dropped `IPC_LOCK` with `setpriv --bounding-set=-ipc_lock`. UCX repeatedly failed memory registration under the 8 MiB locked-memory limit. The bounded negative control timed out after 90 seconds (exit 124).
3. A fresh child retaining the original capabilities passed all nine reads again.

The effective capability mask changed from `a80465fb` to `a80425fb` (bit 14). The reported memlock limit stayed 8192 KiB; the capability permits bypassing that limit. This identifies a permission-related blocker in this controlled fixture, not necessarily the sole cause of every earlier failure.

## Payload evidence and timings

`UCX_TLS=rc,cuda_copy,self` excluded TCP payload transport and CUDA IPC. The peer protocol table selected **zero-copy `rc_mlx5/mlx5_2:1` for CUDA-to-CUDA READs**. HTTP exchanged metadata over TCP separately. Both Pods ran on the same physical VM despite UCX naming its peer configuration “inter-node.” This is neither cross-host data nor proof of NVLink use.

The six successful 1 GiB reads took **22.35–25.55 ms**. All returned bytes equaled 73 and checksums matched. These are observed transfer completion times with connection/polling overhead, not statistically rigorous bandwidth or client TTFT estimates. The probe polls at 1 ms, especially affecting small transfers.

The pinned vLLM 0.26.0 image supplied NIXL; no model was loaded. Pods retained private IPC/PID namespaces and each requested one GPU plus `IPC_LOCK`. Nebius exposed InfiniBand devices inside the GPU Pods without an advertised `rdma/*` resource. `ibv_devinfo` was absent from the image; its invocation did not validate hardware. Device nodes, successful transfers and the peer protocol table provide runtime evidence instead.

## Evidence and reproduction

See initial/restored transfer JSON, protocol evidence, Pod configuration and hardware snapshots in this directory. The negative-control JSON contains representative errors and the SHA-256 of the full private log; the 246 MB repeated-error log is deliberately not committed. Full raw evidence remains in the private run directory. Probe and runner: `infra/topology/rdma-read-probe.py` and `infra/topology/run-rdma-read.py`. The runner now waits for GPU resource advertisement after Node readiness; the first startup attempt exited before any transfer because those events are asynchronous.

## Next step

Carry this verified device/permission configuration into real vLLM P/D serving and verify the actual payload path again. Then compare forced local/remote routes on matched prompts before calibrating allowances or comparing hard locality, tuned soft scoring, unrestricted routing and the proposed filter. Multiple local decoders remain necessary for representative policy evaluation. No claim of router speedup or local-over-remote advantage follows from this test.

## Cleanup and cost

All three Terraform resources were destroyed and five resource types independently verified empty at 2026-09-16T22:53:02.925799+00:00. Estimated cost: **$7.16 before tax**, leaving **$32.83** of the additional $40 slice. Evaluation spending totals $65.08; overall router spending including the earlier $0.60 attempt is $65.68. No new rental is running. See cost-estimate.json and cleanup.json.
