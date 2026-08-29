# disagg-boundary

Prefill/decode disaggregation for LLM serving, measured on 8×H200 nodes over InfiniBand at iso-GPU-count: where the saturation boundary sits, what the KV-transfer path costs a live request, what breaks under failure and what the client sees, and what it all costs per million tokens.

Most published disaggregation results come from rack-scale NVLink systems, where the prefill→decode KV transfer never crosses a network. Most of the world's GPU capacity is 8-GPU HGX baseboards stitched together with InfiniBand — the tier where the transfer cost is real. That tier is where this measurement runs, against a tuned chunked-prefill colocated baseline at the same total GPU count.

**Status: work in progress.** Infrastructure and the analytical performance model land first; measured runs land October–November 2026; the technical report ships with the final results. Nothing in this repository is a result yet.

## Stack

vLLM + llm-d (prefill/decode disaggregation, NIXL KV transfer, Gateway API InferencePool routing) on managed Kubernetes (Nebius), two 8×H200 nodes on an InfiniBand fabric, Terraform from bare account to teardown. Load generation via an existing harness (inference-perf), never hand-rolled. Image digests and engine versions pinned in source.

## Layout

| Path | Contents |
|---|---|
| `scenarios/` | Benchmark scenario definitions, in llm-d-benchmark's format |
| `workloads/` | Input/output length distributions, arrival model, dataset references |
| `infra/` | Terraform + Kubernetes manifests — one command to stand up, one to destroy |
| `model/` | The analytical performance model, committed before the runs it is validated against |
| `plugin/` | A load-aware prefill/decode decider for llm-d-router (Go), designed and specified here, implemented with AI assistance, proposed upstream with its status stated |
| `results/` | One self-contained directory per run: environment, rendered configs, timestamped command log, raw per-request latencies and token counts, seed, analysis |
| `docs/` | [Methodology](docs/methodology.md) · [Reproducing](docs/reproducing.md) |

Every number in the eventual report links back to a run directory under `results/`.

## Reproducing

See [docs/reproducing.md](docs/reproducing.md) — populated as the infrastructure lands.

## Funding

Self-funded. Total spend will be published with the results.
