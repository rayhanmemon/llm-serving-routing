# H100 placement failed; no inference measurement

The advisor reported one preemptible eight-H100 allocation on fabric-3 and positive single-H100 capacity before this attempt. Nebius nevertheless rejected the eight-GPU VM with `NotEnoughResources` (VM scheduling timeout). The operation finished at **2026-09-15 21:10:27 UTC**. Capacity advice was not a reservation.

Provisioning began **20:59:59 UTC** under an approved $50 pre-tax limit for one attempt. The CPU node and single-GPU remote node started; the eight-GPU local node never ran or joined Kubernetes. After the failure was confirmed, Terraform apply was interrupted and scoped teardown began. Independent API checks verified zero instances, Kubernetes clusters, disks, filesystems and GPU clusters at **2026-09-15 21:19:48 UTC**. Terraform state is empty. The guard and keep-awake process exited. Session wall time was **19.8 minutes**.

**Estimated cost: approximately $0.50 before tax**, against the approved $50. The conservative calculation is $0.0522 for CPU, $0.4302 for the remote GPU, plus $0.0185 for disks. It includes some non-billed startup/deletion time; it is not invoice-verified. See [the calculation](cost-estimate.json).

No model engine or router was deployed in this attempt. No inference request was sent, and no KV transfer or routing performance was measured. The [existing directional expectation](../2026-09-15-topology-transfer/EXPECTED.md) remains untested. The prepared source and measurement design are unchanged.

The persistent launch helper prevented an automatic second attempt and started targeted cleanup after apply failed. Raw capacity, provider operation and apply evidence are in `provisioning/`. Credentials and full private runtime files are excluded.

Cleanup of the last node was delayed by six system PodDisruptionBudgets with zero allowed disruptions. They were removed only in this experiment cluster, which was already being fully destroyed; node deletion then completed. A late pod-status read returned Forbidden while cluster authorization was being removed; cloud operation records and the final independent listings establish cleanup.

The capacity monitor is paused after the one authorized attempt. No second rental or alternative purchase type was attempted. The contribution still needs its own real-model evaluation.
