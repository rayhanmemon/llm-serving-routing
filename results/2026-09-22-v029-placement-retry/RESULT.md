# Corrected restart attempt blocked during GPU allocation

**Cost reconciliation — September23, 02:40UTC:** complete native GPU operation history is now saved in `gpu-operations.json`. The VM creation succeeded at06:12:23UTC, a stop completed at06:17:21UTC, and deletion completed at06:30:02UTC. The node group still failed to reach usable readiness and no model request ran. Charging conservatively from create-operation start to stop completion reduces the estimate from$7.05 to **$2.78**, including the unchanged CPU/disk allowances. This is a lifecycle-based estimate, not an invoice. The earlier missing-history upper reservation remains in `cost-estimate-before-operation-history.json`.

Billing basis: [Nebius Compute pricing](https://docs.nebius.com/compute/resources/pricing). Earlier cost statements below describe the initial report and are superseded by this reconciliation.

Run `20260922T060053Z-bb2ce4f2` armed its CPU/cloud guard and attempted the same one-host layout matrix. Fresh advice reported two suitable preemptible eight-H200 VMs; advice was not a reservation. The GPU node group never became ready within its 20-minute placement allowance. The controller interrupted Terraform and tore down resources. **No model or layout request ran.** This attempt therefore neither validates nor refutes the corrected restart logic.

Independent cleanup verification completed at **06:35:13 UTC September22**. The recorded node-group creation was aborted by cleanup. The precise physical GPU creation outcome was not captured before deletion, so this report does not label it a proven `NotEnoughResources` error or assume zero GPU cost.

## Conservative accounting

Known CPU lifetime and disk allowance total approximately $0.19. To protect the cap, the ledger additionally charges the **entire GPU-group create-to-delete interval** at the eight-GPU hourly rate, a $6.87 upper allowance. Total **$7.05 is an accounting upper bound, not a measured charge**. The running overnight bound is $16.54 of $50, leaving **at least $33.46**. Router-project cloud accounting is at most $209.08 on this basis, pending billing reconciliation. CLI audit access was denied and the browser session requires a user passkey; no new permissions were requested.

Another rental requires a fresh exact-shape positive capacity update newer than06:29:25UTC and at least a60-minute backoff through07:29:25UTC. The next bounded profile retains the same four epochs and checks but permits75 minutes of work and20 minutes for cleanup. At the conservative $20.05/hour planning rate, the entire95-minute window is $31.75; a $33 admission fits the remaining conservative balance with over$1 reserved above that window. Insufficient time for a later epoch remains a stop condition, not permission to omit evidence or change the measurements.

Actual-router GPU readiness remains false. The existing five-policy local rehearsal is synthetic; the packed local configuration, matched remote path and real-model EPP integration still need qualification.
