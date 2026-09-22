# v0.29 attempt blocked by eight-GPU capacity

The prepared vLLM 0.29 layout attempt `20260922T035147Z-d1d57f2b` did not reach a GPU workload. The CPU cleanup guard armed successfully. Nebius then rejected the eight-H200 VM with `NotEnoughResources` and a VM scheduling timeout, recorded at **04:05:52 UTC**. The agent interrupted only that attempt's pending Terraform apply after observing the native error; the controller completed teardown.

Independent cleanup verification passed at **2026-09-22 04:14:15 UTC**. Estimated cost was **$0.11 before tax**, comprising CPU lifetime plus a conservative full-session disk allowance. GPU compute is estimated at zero because placement failed before a successful VM start. [Nebius pricing](https://docs.nebius.com/compute/resources/pricing) charges running compute; this estimate is not an invoice reconciliation. **$49.89 remains** from the new $50 checkpoint.

The subsequent fresh capacity response had no positive availability for `gpu-h200-sxm / 8gpu-128vcpu-1600gb` on `us-central1-a`. Availability for eight single-GPU VMs is a different resource shape and does not satisfy this deployment.

The launcher now checks exact-shape fresh positive capacity before creating infrastructure. After a failed placement it also requires a report newer than that failure and the recorded backoff to have elapsed. The new checks pass within the **229-test** suite. Native layout checks, mounted GPU sources and the matched request matrix are unchanged from the [completed preparation](../2026-09-21-v029-preparation/RESULT.md).

Overnight continuation remains read-only while capacity is unavailable. No model correctness, KV-transfer, layout performance or router-performance result follows from this attempt.
