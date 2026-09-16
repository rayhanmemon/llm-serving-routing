# On-demand H200 capacity failure before allocation

## Summary

Nebius could not allocate the requested on-demand eight-GPU H200 VM. The native create operation ran from **2026-09-16 17:23:54.274908 UTC** to **17:29:54.419284 UTC**, then failed with provider status code **8**, `NotEnoughResources`, for the `8gpu-128vcpu-1600gb` shape.

No GPU VM was allocated. The dependent CPU and remote GPU node groups never started. No model, router, correctness probe, warmup or benchmark request ran. This is a capacity outcome and provides no H200, transfer or routing-policy performance evidence.

## Intended scenario

- Purchase type: on-demand, with reservation use forbidden.
- Planned topology: one eight-H200 local node, one one-H200 remote node and one CPU utility node.
- Planned sequence: correctness, the frozen 48-request forced-route block, then reviewed calibration and held-out comparison.
- Source: evaluation commit `673de86490872afefab1ffb1f5b2602f6cfd375f`; router commit `0217d29924ba93b90f952e7a0281dd8dda146703`.

The scarce local node was ordered first so the dependent CPU and remote nodes would not accrue cost before local capacity existed.

## Timeline

| UTC | Recorded event |
|---|---|
| 17:19:31.052660 | Guarded session started. |
| 17:23:54.274908 | Native eight-H200 create operation started. |
| 17:29:54.419284 | Create operation failed: status 8, `NotEnoughResources`. |
| 17:36:03.572214 | The stopped apply was recorded for cleanup; no GPU had been allocated. |
| 17:39:52.597027 | Cleanup and the independent five-resource empty check completed. |

Terraform apply exited 1 after the native capacity failure; it did not reach the guarded placement timeout.

## Result and limits

| Intended stage | Result |
|---|---|
| Local eight-H200 VM allocated | FAILED — `NotEnoughResources` |
| CPU and remote GPU nodes started | NO |
| Kubernetes GPU node ready | NOT REACHED |
| Model or router deployment | NOT RUN |
| Inference or benchmark requests | 0 |

The result does not imply an application failure and cannot be combined with samples from another attempt. It says only that the requested on-demand shape was unavailable during this create operation.

## Cleanup and cost

Terraform destroyed the three created control resources, its state was empty, the guard exited, and an independent project-scoped check found zero instances, Kubernetes clusters, disks, filesystems and GPU clusters at **17:39:52.597027 UTC**. Other resource types and other projects were not checked.

Estimated cost: **$0.008448550386070015 before tax** (about **$0.01**). Compute cost is estimated as zero because no GPU VM was allocated. The estimate conservatively counts a 256 GiB network disk for the full 1,221.544367-second session. It is not invoice-verified.

## Evidence

The sanitized [evidence record](evidence.json) preserves the operation timing and error, cleanup scope, cost method and source commits. Cloud account and resource identifiers, private paths, approval/billing details, SSH material and raw logs are excluded.
