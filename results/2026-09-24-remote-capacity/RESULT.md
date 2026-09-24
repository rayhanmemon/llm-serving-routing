# Local qualification passed; remote capacity unavailable

Run `20260924T035558Z-c55b66c8`; source `a3bea03`. Six local qualification requests passed with saved CUDA-IPC evidence on all four receiving ranks. The second GPU node failed native creation with `NotEnoughResources`. Remote qualification, allowance calibration and policy comparison did not run.

The remote failure completed at 2026-09-24T04:35:30.839061Z; the ten-minute heartbeat noticed it around04:46UTC and interrupted the owned Terraform apply. This exposed a monitoring limitation: the allocation controller kept waiting after the provider had reported failure. The approximately11-minute detection delay kept the healthy local GPU node billed unnecessarily (roughly$3.6). This was avoidable orchestration cost, distinct from the provider capacity failure. The existing cloud and desktop deadlines remained active; no deadline was extended.

Local qualification was already copied off-host; the final collection before cleanup also completed. There are no completed calibration or policy trials to resume. All five scoped paid resource types were independently empty at **2026-09-24T04:52:52.217439+00:00**. This is not an account-wide audit.

Estimated run cost **$14.26 before tax**, including local GPU **$13.78** and support allowance **$0.47**. Native remote history shows failed creation and deletion only; remote GPU compute estimated zero. These are lifecycle estimates, not invoices. The current$80pool has **$65.74 remaining**; cumulative router estimate **$267.15**. Automatic launch is paused: remaining funds are below the prepared$74full-run admission. Do not silently shorten the protocol or replenish funds.

Next: make provider allocation failures terminate promptly in the controller before considering another rental. Preserve the saved qualification evidence; a new serving deployment would still need its own startup validation.
