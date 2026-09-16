# H200 evaluation attempt: provider preemption before Kubernetes readiness

## Summary

One preemptible eight-GPU H200 VM was allocated and reached the provider's running state, but Nebius preempted it before the node joined Kubernetes. The authenticated Audit Logs console records a `Preemption` / `STOP` event at **2026-09-16 16:55:40.162438296 UTC**. No model, router, correctness probe, warmup or benchmark request ran.

This is an infrastructure interruption, not a router or inference result. It establishes neither H200 performance nor any local/remote transfer or routing-policy behavior.

## Intended scenario

- Local shape: preemptible `gpu-h200-sxm`, `8gpu-128vcpu-1600gb`, CUDA 13.0 driver preset.
- Planned deployment: one local prefill GPU, one local decode GPU, one separate remote decode GPU and a CPU utility node.
- Source: evaluation commit `8e12ca8eab4e848bc255b1357d50da126ff63f7f`; router commit `0217d29924ba93b90f952e7a0281dd8dda146703`.
- Planned order: real-model correctness, the frozen 48-request forced-route block, then reviewed calibration/evaluation work.

Only the scarce local eight-H200 node was created. The dependent CPU and remote node groups did not start.

## Timeline

| UTC | Recorded event |
|---|---|
| 16:46:39.553086 | Guarded session started. |
| 16:50:58 | Local node group created in `PROVISIONING`. |
| 16:51:00.601199 | Eight-H200 VM create operation started. |
| 16:51:56.507410 | Create operation finished; the VM reached the provider running state. |
| 16:55:40.145330 | Provider stop operation started before Kubernetes readiness. |
| 16:55:40.162438296 | Audit event recorded source `Preemption`, action `STOP`, status `STARTED`. |
| 16:55:57.151840 | Stop operation finished. |
| 16:57:30 | Provisioning monitor recorded the VM `STOPPED`; the node group was still provisioning. |
| 17:00:47.685950 | VM delete operation started. |
| 17:01:14.136886 | VM delete operation finished. |
| 17:04:19.008602 | Terraform teardown and independent five-resource empty check completed. |
| 17:12:44.502708 | The authenticated audit record was transcribed and the preemption cause confirmed. |

Terraform apply exited 1 because the stopped allocation was interrupted for cleanup; placement did not time out. The audit event ID and exact operation times are retained in the sanitized evidence.

## Result and limits

| Intended stage | Result |
|---|---|
| Local H200 Kubernetes node ready | NOT REACHED |
| Model deployment | NOT RUN |
| Direct/local/remote correctness | NOT RUN |
| Forced-route 48-request timing block | NOT RUN |
| Policy calibration or comparison | NOT RUN |

The provider preemption explains this VM stop. It does not imply an application failure, establish serving reliability, or supply any inference sample that can be combined with another attempt.

## Cleanup and cost

Terraform destroyed the three created managed resources. Its state was empty, the deadline guard exited, and an independent project-scoped check found zero instances, Kubernetes clusters, disks, filesystems and GPU clusters at **17:04:19.008602 UTC**. Other resource types and other projects were not checked.

Estimated cost: **$3.347688460067212 before tax**, calculated conservatively from the local eight-H200 create-operation start through delete-operation finish, including the stopped interval, plus the 256 GiB disk for the full session. The CPU and remote GPU nodes did not start. This estimate is not invoice-verified.

The cumulative estimated router-evaluation spend after this attempt was **$32.52751313379798**, leaving **$167.472486866202** of the $200 cumulative ceiling available at that point.

## Evidence

[`evidence.tar.gz`](evidence.tar.gz) contains sanitized provider operation times, allocation state, the transcribed audit event, cleanup verification, cost calculation, execution boundary and source record. [`evidence-manifest.json`](evidence-manifest.json) records each member's size and SHA-256 hash.

Credentials, kubeconfigs, certificates, tokens, provider resource identifiers, tenant/project/subnet identifiers, private console URLs, raw console boot logs, billing-readiness/account data, payment data and authorization records are excluded.
