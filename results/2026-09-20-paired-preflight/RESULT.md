# Overnight comparison stopped before GPU allocation

The prepared NVLink-versus-RDMA experiment could not start. Nebius rejected creation of the temporary cleanup service account's IAM group with **PermissionDenied** in the experiment project. Creating a service account itself succeeded; creating its permission group did not. No CPU worker or GPU node was allocated, no cleanup guard was armed, and no inference request was sent.

The program stopped at the required safety gate. Cleanup independently verified at 2026-09-21 01:23:59 UTC: instances, Kubernetes clusters, disks, filesystems and GPU clusters were empty; the temporary service account was deleted and no cleanup group remained. Terraform state is empty. Estimated additional cost **$0**; **$48.83 remains**. No further attempt is armed.

## What was prepared

- Frozen eight-case correctness suite for the same Qwen3-0.6B model/settings as the successful local NVLink test.
- Explicit two-host transport gates: actual local KV payload bytes on NVLink; remote GPU-memory READ on RDMA; four-route output parity and transfer/error checks.
- CPU-client TTFT measurement with balanced route order, identical short/long prompts, warmups, repeated pairs and a bounded local-congestion sweep.
- Cloud-side deadline guard designed to start on a CPU node before either GPU host is allocated. Renewable credentials belong to a temporary identity scoped to the otherwise-empty experiment project.
- Local client/controller rehearsals, independent saved-results validation and 179 passing tests. The live guard's permissions and deletion behavior remain unqualified.

The source at launch was `d9be1df`; the independent summarizer was completed after launch. No code was pushed and the upstream router PR was unchanged.

## Next action

Have the project administrator enable the dedicated cleanup identity. The rejected operation was `iam group create`, for project `project-u00k8gmbpr0067akfrxdah`. The documented workflow requires project-level administrator permission to create/manage groups and access permits. Do not grant tenant-wide access. Alternatively an administrator can pre-create a project-scoped cleanup service account/group/permit and have those exact resources incorporated into the prepared Terraform state.

Then repeat the guard bootstrap and verify its live acknowledgement before allocating GPUs. Permission repair does not itself validate the cloud guard or either transport. The agreed unattended run must not silently fall back to a Mac-only shutdown timer.

[Prepared procedure](../../infra/topology/PAIRED-LOCALITY.md). [Nebius group-management prerequisites](https://docs.nebius.com/iam/authorization/groups/manage). [Node-based Kubernetes pricing](https://docs.nebius.com/kubernetes/resources/pricing).
