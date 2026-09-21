# Local Qwen3-32B TP4 qualified through 120K input tokens

**Local qualification passed:** 16 real requests, eight matched direct/P-D cases, and selected CUDA-IPC GPU READs on every receiving TP rank. Inputs were 4,096, 32,768, 65,536 and 122,880 tokens. Source-location gold accuracy was 14/16 and is separate from the approved direct/P-D parity criterion.

Both local engines reported 1,673,408 KV-cache tokens. This exceeds the calculated memory demand of eight longest requests under the selected output/block settings; eight concurrent requests were not actually exercised. Prefill and decode used disjoint four-GPU groups on one H200 host, the same pinned 32B model, BF16 and fixed YaRN settings, with chunked prefill and default graph execution.

The eight P/D requests moved **110 GiB total KV payload**, with four rank transfers per request and no transfer-error changes. The producer group's net NVLink outflow matched that payload within 4 KiB of counter rounding. The decoder group's aggregate net inflow was approximately 112.51 GiB. Additional tensor-parallel traffic or snapshot effects are not isolated by these aggregate counters, so this is not a complete per-request hardware-byte attribution. Actual per-rank CUDA-IPC protocol selection and physical NVLink topology are preserved separately.

## Why remote timings did not run

The remote H200 VM was created, but its first observed Kubernetes health record reported `NebiusContainerRuntimeError=True`, reason `ContainerRuntimeUnhealthy`, with `nvidia-container-runtime` in D-state. Kubernetes Ready was true but no GPUs were registered as allocatable. No remote engine was deployed.

The controller aborted immediately on that flag, collected the successful local evidence and cleaned up. The saved observation does not establish that the condition would have persisted or prove a permanent hardware defect. The readiness check is being revised to allow a bounded 120-second recovery period, still requiring healthy, registered GPUs before any engine deployment.

All paid resources were independently verified deleted. This attempt cost an estimated **$15.33 before tax**. Together with the earlier startup and capacity attempts, approximately **$25.42** of the authorized $100 is recorded. The staged design preserved a useful local qualification, but the planned remote qualification and 96 timed requests remain unexecuted. No router-policy improvement is claimed.

The compressed archives hold the client and engine evidence. `node-health.json` preserves the remote startup condition; `cost-estimate.json` uses native create/delete operation lifetimes. These estimates are not reconciled provider invoices.
