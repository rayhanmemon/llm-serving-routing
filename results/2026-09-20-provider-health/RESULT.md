# Single-host allocation rejected by provider health checks

The September 20 local CUDA IPC/NVLink attempt did not run a probe or inference request. After the Kubernetes node became Ready and advertised eight GPUs, Nebius marked it unhealthy and evicted the test Pod while its image was still being pulled.

The recorded `NebiusGPUError` reported `rdma_cm` not loaded and GPU persistence mode disabled. A later `NebiusContainerRuntimeError` reported a `runc` process in D-state. The Pod's disruption condition explicitly records `EvictionByEvictionAPI`. The VM was still RUNNING at the time; the native operation record did not identify spot preemption. These are provider-reported health symptoms, not a diagnosis of a physical GPU defect or a CUDA IPC failure.

The test was stopped, its own Pod force-deleted to unblock draining, and the rental sent through the existing guarded teardown. No health check was bypassed. The controller now requires a healthy provider GPU condition before deploying, rejects reported runtime health errors, and detects Pod eviction promptly. Local tests cover the rejection.

The already-qualified September 16 raw IPC and real-model RDMA results are unaffected. One replacement attempt is permitted under the September 20 additional-$50 authorization after cleanup and cost reconciliation. A repeated equivalent provider failure stops further identical retries.

Cleanup independently verified across all five resource types. Cost **$4.14** before tax; **$54.15** remains authorized.
