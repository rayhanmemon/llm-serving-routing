# Split-pod raw CUDA IPC retry: node stopped before probes

## Summary

The split-pod-first retry produced no CUDA, NIXL, inference or router result. One preemptible (spot) eight-GPU H100 VM started, joined the Kubernetes cluster briefly, became unreachable when its kubelet stopped posting status, and then stopped before the node group became ready. The diagnostic supervisor never received Terraform's cluster output and ran no probe.

The provider cause is now confirmed as preemption. At initial close, provider operations showed only a native `Stop Instance` and a targeted audit query was denied. A later authenticated Audit Logs console review found a `Preemption` / `STOP` event at **2026-09-16 14:48:11.582901030 UTC**, matching the stop operation. This run still must not be cited as a CUDA IPC failure.

### Audit follow-up — 2026-09-16 17:17 UTC

The sanitized [preemption audit record](preemption-audit.json) retains the audit event ID, source, action, time and status while removing the instance identifier and private console URL. This resolves the infrastructure cause only; it adds no CUDA, NIXL, inference or routing observation.

The [preceding run's two same-container controls](../2026-09-16-raw-cuda-ipc/RESULT.md) remain valid: same-GPU and cross-GPU raw CUDA IPC passed inside one eight-GPU container. The missing split-pod case remains unmeasured.

## Intended scenario

- Provider shape: one Nebius `gpu-h100-sxm` node using the `8gpu-128vcpu-1600gb` preset as a preemptible (spot) instance.
- Mode: run the missing one-GPU producer-pod to one-GPU consumer-pod case first; then run same-container controls on that exact GPU pair, with enumeration-only masking if the split case failed.
- Source: evaluation repository commit `478a29ad0da5fe037deaa357c84dade6feb74c22`.
- Node software reported before failure: CUDA 13.0.3-1, NVIDIA driver 580.173.02, NCCL 2.30.7-1 for CUDA 13.0, Linux 6.11.0-1016-nvidia, containerd 2.2.6 and kubelet 1.35.7.

The planned diagnostic used direct CUDA Driver API calls. No diagnostic pod was created and no raw CUDA allocation, handle export, handle import or payload copy occurred.

## Timeline

| UTC | Recorded event |
|---|---|
| 14:40:14.152721 | Provider compute create operation started. |
| 14:42:43.478605 | Provider compute create operation finished. |
| 14:44:52 | Kubernetes node object created. |
| 14:45:02 | Last recorded kubelet heartbeat. |
| 14:45:57 | Node conditions changed to `Unknown`; Kubernetes reported “Kubelet stopped posting node status” and added the unreachable taint. |
| 14:48:11.540563 | Provider `Stop Instance` operation started. |
| 14:48:24.686849 | Provider `Stop Instance` operation finished without a recorded error. |
| 14:48:53 | Provisioning monitor observed the VM `STOPPED` while the node group was still provisioning. |
| 14:50:02.028354 | The operator interrupted Terraform apply and stopped the waiting diagnostic supervisor. |
| 14:50:34.289192 | Provider VM deletion started. |
| 14:51:04.287838 | Provider VM deletion finished. |

The Terraform apply exit code was 1 because it was deliberately interrupted after the stopped VM was confirmed; it did not time out waiting for placement. The provider VM deletion completed. Terraform later destroyed all three managed resources, and the project-scoped cleanup check passed.

## Result and limits

| Intended check | Result |
|---|---|
| Raw CUDA IPC across separate one-GPU pods | NOT RUN |
| Same-GPU raw CUDA control | NOT RUN in this retry |
| Cross-GPU, same-container raw CUDA control | NOT RUN in this retry |
| NIXL or UCX transfer | NOT RUN |
| vLLM inference or router behavior | NOT RUN |

This run establishes a provider preemption before the measurement. It does not establish a CUDA failure, a container-boundary failure, a transport result, H100 performance, locality benefit or routing-policy behavior. The node snapshot confirms the requested H100 shape and software labels, but no GPU user-space probe reached execution.

At initial close, no further paid attempt was scheduled pending provider evidence. The later audit resolved this stop as preemption; subsequent evaluation attempts are recorded separately.

## Cleanup and cost

The VM delete operation finished at 14:51:04.287838 UTC. Terraform destroyed all three managed resources, its state was empty, and the guard, parent and diagnostic-supervisor processes exited. At **14:54:25.654570 UTC**, an independent project-scoped check found zero instances, Kubernetes clusters, disks, filesystems and GPU clusters. Other resource types and other projects were not checked.

Estimated cost: **$3.11 before tax**, calculated conservatively as **$3.1062** for 650.135117 seconds from compute create-operation start through delete-operation finish at $17.20/hour, including the stopped interval, plus **$0.0076** for the 256 GiB SSD over the full 1,104.816686-second session. This is not invoice-verified. Cumulative estimated H100 evaluation cost is **$29.18**, leaving **$20.82** of the $50 authorization.

## Evidence

[`evidence.tar.gz`](evidence.tar.gz) contains the original sanitized source, node-health, provisioning, provider-operation, supervisor, stop, cleanup and session-cost records. [`evidence-manifest.json`](evidence-manifest.json) lists every archived member's SHA-256 hash and size. The later sanitized [preemption audit record](preemption-audit.json) is intentionally separate so the historical archive remains unchanged. Credentials, kubeconfig, approval and private session-control records, provider resource identifiers, node UUIDs, network addresses and budget-authorization records are excluded.
