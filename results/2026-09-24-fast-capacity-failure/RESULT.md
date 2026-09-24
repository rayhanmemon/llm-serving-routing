# Local qualification passed; remote capacity unavailable

Run `20260924T084558Z-50232672`; source `1afd24d`. Six local qualification requests passed with saved CUDA-IPC evidence on all four receiving ranks. The second GPU node failed native creation with `NotEnoughResources`. Remote qualification, allowance calibration and policy comparison did not run.

The remote native creation failure completed at 2026-09-24T09:24:23.333618Z. The node-group event was published at09:24:28UTC; the corrected detached controller detected it at09:24:32.363UTC, about4.36seconds later (about9.03seconds after native operation completion). Terraform exited at09:24:32.558UTC and cleanup began automatically, before the09:27heartbeat. No operator interruption or deadline extension was needed. This verifies the failure-detection repair on the actual provider failure path; it does not resolve capacity scarcity.

Local qualification was already copied off-host; the completed local qualification snapshot was verified before requesting remote; a post-cleanup collection cannot contact deleted Pods. There are no completed calibration or policy trials to resume. All five scoped paid resource types were independently empty at **2026-09-24T09:30:14.994227+00:00**. This is not an account-wide audit.

Estimated run cost **$10.56 before tax**, including local GPU **$10.19** and support allowance **$0.37**. Native remote history shows failed creation and deletion only; remote GPU compute estimated zero. These are lifecycle estimates, not invoices. The current$75single-attemptpool has **$64.44 remaining**; cumulative router estimate **$277.71**. Automatic launch is paused: the one authorized attempt is consumed, and remaining funds are below the prepared$74full-run admission. Do not silently shorten the protocol or replenish funds.

Next: review the placement strategy before any further authorized rental; repeated remote capacity failures remain the blocker. Preserve the saved qualification evidence; a new serving deployment would still need its own startup validation.
