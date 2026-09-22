# Cleanup-account attachment permission blocked startup

The one-host layout attempt `20260922T010348Z-decb451a` stopped before creating any CPU or GPU VM. The Kubernetes control plane was created, then the CPU node-group request was rejected with `resource.serviceaccount.issueAccessToken` denied on the reusable cleanup service account. The cleanup account's project Editor grant existed; the launcher separately needed permission to issue tokens for that account when attaching it to the VM.

This is a launcher permission-preflight omission, not a CUDA IPC, model or packed-layout result. No inference ran. The control plane was removed and independent deletion checks passed at **2026-09-22 01:11:49 UTC**. Estimated incremental compute cost is **$0**; [Nebius Kubernetes pricing](https://docs.nebius.com/kubernetes/resources/pricing) charges the nodes, and no nodes or disks were created. This is not an invoice reconciliation.

The launcher now probes cleanup-identity token issuance and project access before creating any infrastructure. The full suite passes **220 tests**. The already-rehearsed GPU/client manifests are unchanged.

A separate narrow permission has been staged for confirmation: the automation launcher may administer/use the cleanup service account itself, with no project-wide admin grant. The existing cleanup account retains Editor on the experiment project. After approval, verify the free identity-access probe before retrying. No retry is running or automatically scheduled; the evaluation balance remains approximately **$42.95**.
