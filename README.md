# disagg-boundary

Request-sensitive topology routing for llm-d: keep decode near the selected prefill worker while the KV-transfer saving justifies extra local load, and widen the choice when a farther decoder is preferable.

**Status: single-allowance prototype implemented and tested locally; source not published and GPU evaluation not started.** The planned change extends existing prefill-first topology routing with a calibrated relative-load allowance for two prompt-size ranges. No performance improvement, upstream submission or merge is claimed. See [plugin/](plugin/README.md) for the decision and prior work.

## Evaluation

[Three-worker deployment draft](infra/topology/README.md): standalone infrastructure, routing policies and benchmark configuration, with local validation status and remaining checks.

The evaluation must first establish a real local-versus-remote transfer difference and a useful operating regime beyond tuned existing routing. The functional minimum is three independent GPU workers across two hosts: one prefiller, one local decoder and one remote decoder. A fourth active worker adds another local decoder. Count all rented capacity, including unused GPUs required by an instance preset.

Compare six policies on the same model, hardware, engine, transport and workload:

1. No topology preference.
2. Existing hard topology filter.
3. Existing soft topology scorer, tuned during calibration.
4. A tuned load/capacity filter followed by topology affinity.
5. The new gate with the best single global allowance.
6. The new gate with two prompt-size allowances.

Three held-out workload families and three paired repeats give **54 short policy runs**, plus calibration. Measure client first-token latency, useful completed throughput, streaming gaps, failures, rejections and unfinished requests. Verify the selected workers, transferred blocks and actual transport. Publish regressions and neutral results with uncertainty. If two allowances cannot improve on one, simplify the rule.

Use the existing inference-perf harness, pinned versions and self-contained run records. The current one-prefill/one-decode templates are legacy starting files: they do not instantiate the local and remote choices required by this evaluation. Rework and validate them before use.

## Layout

| Path | Contents |
|---|---|
| `plugin/` | Planned Go topology extension, design and tests; exact upstream status once available |
| `scenarios/` | Serving descriptions in llm-d-benchmark’s format; current topology still needs revision |
| `workloads/` | Input/output lengths, arrival profiles and dataset references |
| `infra/` | Starting manifests, Terraform delta, transport checks and result capture; not yet a qualified deployment |
| `results/` | One self-contained directory per run: environment, configuration, commands, raw data and analysis |
| `docs/` | [Methodology](docs/methodology.md) · [Reproducing](docs/reproducing.md) |

Every reported number will link to its run record. A result applies to the measured topology, workload and implementation; it does not establish a general architecture-wide disaggregation boundary.

## Funding

Self-funded. Actual evaluation spend will be published with results.
