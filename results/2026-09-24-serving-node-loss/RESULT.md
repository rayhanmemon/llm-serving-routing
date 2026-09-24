# Local serving node lost after qualification

**Local qualification passed, but the local node became unreachable while the remote model was starting. No calibration or routing-policy comparison ran.** Run `20260924T005415Z-9497d26c` used the unchanged Qwen3-32B BF16 TP4/BHLNC configuration.

Six local qualification requests completed, all six matched the expected answers, and the CUDA-IPC path was validated on all four receiving ranks. The results were already saved off-host. The remote node started and its four rank-allocation probes were collected, but it did not complete remote qualification.

## Observed failure sequence

- 01:42:29 UTC: last recorded local kubelet heartbeat.
- 01:44:14 UTC: Kubernetes marked local Ready, MemoryPressure, DiskPressure and PIDPressure Unknown because the kubelet stopped posting status.
- A health request from the CPU client to the local decoder timed out after five seconds. The native VM API still reported RUNNING; that did not establish guest responsiveness.
- 01:46:00–01:46:14 UTC: the native API recorded a successful Stop Instance operation for the local VM.
- The controller failed its live serving-Pod check (`Serving/client Pod missing`). Its collection retained the healthy remote/client data and explicitly reported the missing local Pod. The earlier local qualification remained available.
- 2026-09-24T01:48:27.527481+00:00: the operator interrupted the owned controller after observing the loss. The native VM stop preceded this interruption and the scheduled cleanup deadline of02:43:32UTC.
- Teardown deleted all experiment resources. The operator interruption also left the completed teardown without its final marker; the standard cleanup command was run again to independently recheck emptiness and finalize the marker at **01:55:40 UTC**. Controller, desktop guard and task keep-awake exited.

**The cause of the initial host/guest unreachability is not established.** These records do not prove preemption, a hardware fault, a guest/kernel problem or another cause. Existing False GPU/kernel conditions were last-reported values and do not rule out a later failure after heartbeat loss. A post-teardown attempt to retrieve the cloud guard's final logs was denied as the cluster was being removed; that limitation is retained. The saved deadlines and operation times are evidence, not a provider root-cause explanation.

## Cost and status

Estimated cost: **$21.67 before tax**, not an invoice. Local GPU time is conservatively counted from create-operation start to the successful stop's completion (**$13.57**); remote GPU time from create start to deletion completion (**$7.59**); the supporting-resource allowance covers admission through final cleanup verification (**$0.51**). Full native histories and calculations are retained.

The current $75 pool has **$52.81 remaining**. Router-project lifecycle estimate is **$252.89**. All five scoped paid resource types are empty. Automation is **paused**, the launch recipe is disabled, and the task-owned monitoring keep-awake was released. No identical paid retry is authorized by this failure outcome; the remaining balance also falls below the existing full-run admission requirement.

The outcome establishes fresh local serving/transfer qualification only. It does not establish a calibrated allowance, a remote-path result in this run, or any routing-policy performance improvement. Preserve this failure in the project story; do not recast it as successful benchmarking.
