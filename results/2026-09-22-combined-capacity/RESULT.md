# Combined comparison attempt — stopped at GPU placement

**Cost reconciliation — September23, 02:40UTC:** complete native GPU operation history is now saved in `gpu-operations.json`. The only operations are failed creation (`NotEnoughResources`) and successful deletion; no successful start occurred. Under Nebius’s running-VM billing rule, GPU compute is estimated zero. Keeping the conservative support allowance reduces this attempt from the$4.95 reservation to **about$0.21**. This is not invoice verification. The prior reservation remains in `cost-estimate-before-operation-history.json`. The current$70continuation pool therefore has **about$69.79 remaining**; the older attempt’s refund does not add authority to that pool. The automatic monitor has been resumed at Rayhan’s instruction; revised launch preparation is in progress.

Billing basis: [Nebius Compute pricing](https://docs.nebius.com/compute/resources/pricing). Earlier cost statements below describe the initial report and are superseded by this reconciliation.

**No inference or policy benchmark ran.** Nebius reported two available eight-H200 spot nodes, but the first VM failed native scheduling with `NotEnoughResources`. The selected packed configuration was never executed on GPUs. All experiment resources were independently verified deleted at **2026-09-23 01:03:44 UTC** (September22 Toronto). The automatic launcher is paused after its one authorized attempt.

## What happened

- **00:37 UTC:** capacity advice reported two exact `8gpu-128vcpu-1600gb` H200 nodes on `us-central1-a`. The source timestamp was00:16:13UTC, about21minutes old and within the declared30-minute freshness rule. This was advice, not a reservation.
- **00:38:29 UTC:** the attempt was admitted with a$70total ceiling, staged allocation and independent cleanup guards. Source/chart hashes, the fresh Terraform plan, empty project and cleanup-identity access passed.
- CPU/control-plane setup completed. The real cloud cleanup guard armed; the native benchmark client became ready and the custom router image imported on the CPU host.
- **00:49:14 UTC:** the local eight-H200 node group was requested. A VM resource existed in `STARTING`, but never became a usable GPU worker. A created VM object is not proof of physical GPU placement.
- **00:55:08 UTC:** the node-group event recorded a failed compute create operation: `NotEnoughResources`, `VM schedule timeout`. The VM was subsequently observed `STOPPED`, and no GPU node joined Kubernetes.
- On observing that failure, the agent stopped this run’s Terraform apply with one graceful interrupt. The controller then deleted the scoped resources. The remote host was never requested.
- **01:03:44 UTC:** independent checks reported zero instances, Kubernetes clusters, disks, filesystems and GPU-cluster objects. Controller, desktop guard and task-owned keep-awake process exited. No retry was launched.

The provider’s failure is captured in `provider-events.json`. It does not establish why the availability advice differed from placement, and it does not test or refute the packed layout. No workload, tuning grid, source code or comparison rule changed during this attempt.

## Cost and time

The session lasted about **25.2 minutes** from admission to verified cleanup. This is autonomous/cloud elapsed time, not recorded human attention or GPU-hours.

The ledger conservatively reserves **$4.95 before tax**: **$0.21 support allowance** plus **$4.74 uncertain GPU allowance**. The latter prices the entire local-request-to-verified-cleanup interval at the prepared$19.60per eight-GPU host-hour even though scheduling failed. **This is not an invoice or a measured GPU charge.** Actual GPU billing history was not obtained. The precise method and intervals are in `cost-estimate.json`.

With this reservation, cumulative evaluation accounting is **$213.43**, and router-project accounting including the initial separate$0.60 is **$214.03**. At least **$65.05** remains under the approved cumulative ceiling. No new budget was added.

## Remaining work

Packed local/remote GPU qualification, actual calibration and the five-policy comparison remain unperformed. The existing draft PR and prepared software are unchanged. This attempt confirms the cloud CPU/guard/image preparation path, but supplies no new serving-performance evidence. Further paid work requires a new bounded attempt decision; neither the remaining balance nor the old heartbeat authorizes an automatic retry.
