# Reproducing

*Stub — populated as the infrastructure lands.*

The target shape: the scenario / harness / workload-profile triplet and one command per phase —

1. `terraform apply` from a bare account to a two-node InfiniBand GPU cluster,
2. one command to run a scenario against a workload profile,
3. `terraform destroy` to zero.

Until that exists, nothing here claims to be reproducible.

## What has landed

The files below were written and exercised on a small two-machine rehearsal —
single-GPU L40S nodes over plain TCP, no InfiniBand, a small model — and are
published for their shape and their reasoning. Each one carries the same
disclosure at the top. **No number measured on that hardware appears anywhere in
this repository**, and no threshold derived there is imported: the transport
band and the rate ladder are re-derived on the cluster under test.

| File | What it is | What changes on the H200 cluster |
|---|---|---|
| `infra/gpu.tf` | The GPU node-group delta: two nodes, preemptible (spot), with the reservation policy as its mutually-exclusive alternative. A delta beside a base `main.tf` (not published) that declares the managed Kubernetes cluster, a CPU node group for the router and harness, and this group. | Platform and preset re-pointed to the 8×H200 machine type; the InfiniBand GPU-cluster resource added back and the node group joined to it. Each node then has eight cards, so the "one GPU per node" placement guarantee disappears and only the pod anti-affinity survives. |
| `infra/modelserver/kustomization.yaml` | The engine overlay: llm-d's single-host prefill/decode vLLM base by remote URL pinned at `v0.9.0`, both images pinned in the leaf overlay (release, never nightly), and the pool label the router selects on. | Model label re-pointed. The base and image pins stay. |
| `infra/modelserver/patch-prefill.yaml`, `patch-decode.yaml` | The two engine patches: the 15-minute startup-probe override, the Recreate rollout strategy, the cross-pod side-channel host and port, the transport instrument on both containers, the explicit engine knobs held identical across every arm, the connector's load-failure policy stated rather than inherited, and the pod anti-affinity. | The model, the tensor-parallel sizes and the replica counts; an RDMA device request added to the resources. The anti-affinity is kept — on eight-GPU nodes it is the only thing keeping prefill and decode on different machines. |
| `scenarios/scenario.yaml` | The stack in llm-d-benchmark's own format: the four fields that carry the configuration notation, the transport keys, the side-channel port override, and every unused deploy method explicitly disabled. | Model, accelerator count, replica counts and tensor-parallel sizes re-pointed; the two RDMA keys stop being empty. Structure and the disabled-methods block carry over unchanged. |
| `scenarios/spec.yaml.j2` | The four-field entry point the benchmark CLI reads. Paths are placeholders. | Nothing but the paths. |
| `workloads/rate_ladder.yaml.in` | The rate ladder: a fixed-arrival-rate workload profile whose four rungs are **derived** from the measured transfer rate and the measured bytes per request, so the ladder crosses the transfer path's capacity by construction. Rates are placeholders. | The stages list grows into the full sweep and the length distributions change; the shape, the token convention and the reporting settings are identical. |
| `infra/assert_transport.py` | Methodology, not data: the client that fails a run when the cache did not move, or moved over a wire other than the one declared. It asserts the split first, then the bytes, then the transport the library itself reported. | The namespace, the model, the declared transport, and the band — re-derived on the cluster under test, never imported. |
| `infra/results-scaffold.sh` | Creates a run directory in the shape `results/` publishes in and captures the machine identity at creation time. Runs on a laptop with no cluster. | Nothing; the hardware, model and transport are environment variables. |
| `results/README.md` | The per-run directory layout, the rerun rule for reclaimed preemptible nodes, and the traceability rule. | Nothing. |

One note that is worth carrying forward, because it is invisible from the
configuration: when the benchmark tool deploys the engine pods itself, it stamps
its own default transport menu — which includes plain TCP — onto both
containers, even when the scenario leaves that menu unset. Assert the wire, never
the file.
