# Capacity retry — no GPU workload

**The revised pre-allocation check passed, but Nebius again failed GPU placement.** No model request, packed-cache qualification or routing-policy trial ran. Remote was never requested. All five scoped paid resource types were independently verified empty at **2026-09-23 03:54:25 UTC** (September22 Toronto). Automatic free monitoring continues within the same original$70pool.

Run: `20260923T033056Z-886eca0b`; source `64e3be5`; unchanged router head `0217d299` and frozen workload.

- Admission03:30:56UTC followed exact source/proof checks, a fresh scoped plan, empty project and cleanup access. Native CPU/client/image/guard preparation completed.
- Immediately before the first GPU request at03:41:12UTC, the added check again reported two exact eight-H200 spot nodes on us-central1-a, MEDIUM availability, with source timestamp03:15:36UTC. The observation passed the declared30-minute rule; it was not a reservation.
- The compute create operation ran03:41:18–03:47:16UTC and failed with `NotEnoughResources` / VM schedule timeout. The node group never became ready. The agent preserved the native event and interrupted only the matching Terraform apply, triggering cleanup.
- Complete GPU operation history contains that failed create and successful deletion, with no successful start. This is another provider-placement failure; it supplies no evidence for or against the selected packed layout.
- Controller, desktop guard and task-owned keep-awake process exited after cleanup. No source, model, transport or measurement rule changed during the attempt.

## Accounting and continuation

Estimated cost is **about$0.20 before tax**: the conservative$0.50/hour support allowance over23.5minutes. GPU compute is estimated zero from complete native operation history and [running-VM billing](https://docs.nebius.com/compute/resources/pricing). This is not invoice verification. Exact values are in `cost-estimate.json`.

The two attempts in the current$70pool have used an estimated **$0.41**, leaving **$69.59**. Router-project accounting is now about **$205.21**, after the earlier native-history reconciliations. No additional budget was added.

Read-only polling remains active. The next paid attempt must wait until04:47:16UTC, use a capacity observation newer than this failure, pass all current source/plan/budget checks, and remain within the shared pool. The updated check prevents using the pre-CPU snapshot blindly; it cannot guarantee the provider will place a VM. Packed GPU qualification and the five-policy comparison remain pending.
