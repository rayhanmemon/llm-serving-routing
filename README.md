# disagg-boundary

Prefill/decode disaggregation for LLM serving, measured on 8×H200 nodes over InfiniBand at iso-GPU-count: where the saturation boundary sits, what the KV-transfer path costs a live request, what breaks under failure and what the client sees, and what it all costs per million tokens.

The study compares one tuned chunked-prefill colocated configuration with one split configuration at the same total GPU count, using one model and precision on two nodes. It measures a bounded workload grid, the transfer cost of a live request, targeted failures, and a small Go routing extension. Results apply to the measured setting; a crossover or routing improvement is not assumed.

**Status: work in progress.** The reduced scope was adopted September 10, 2026. Main acquisition targets September 24, paid reruns end September 26, and the report targets September 30. No capstone measurement result is claimed yet.

The planned architecture comparison is 54 runs: three prompt lengths × three shared offered rates × two configurations × three repeats, at a fixed output length. A separate 12-run comparison evaluates stock routing against a congestion veto on an expected-benefit workload and an expected-weakness workload. Model calibration and held-out validation are separate. If the priced schedule requires a cut, remove one prompt-length slice before evaluation, retaining 36 architecture and all 12 policy runs.

## Stack

vLLM + llm-d (prefill/decode disaggregation, NIXL KV transfer, Gateway API InferencePool routing) on managed Kubernetes (Nebius), two 8×H200 nodes on an InfiniBand fabric, Terraform from bare account to teardown. Load generation via an existing harness (inference-perf), never hand-rolled. Image digests and engine versions pinned in source.

## Layout

| Path | Contents |
|---|---|
| `scenarios/` | Benchmark scenario definitions, in llm-d-benchmark's format |
| `workloads/` | Input/output length distributions, arrival model, dataset references |
| `infra/` | Terraform + Kubernetes manifests — one command to stand up, one to destroy |
| `model/` | The analytical performance model, committed before the runs it is validated against |
| `plugin/` | A load-aware prefill/decode decider for llm-d-router (Go), developed and measured here, with implementation and any upstream status stated |
| `results/` | One self-contained directory per run: environment, rendered configs, timestamped command log, raw per-request latencies and token counts, seed, analysis |
| `docs/` | [Methodology](docs/methodology.md) · [Reproducing](docs/reproducing.md) |

Every number in the eventual report links back to a run directory under `results/`.

## Reproducing

See [docs/reproducing.md](docs/reproducing.md) — populated as the infrastructure lands.

## Funding

Self-funded. Total spend will be published with the results.
