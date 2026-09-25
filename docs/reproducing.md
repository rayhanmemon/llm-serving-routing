# Reproducing the saved routing result

The completed Qwen3-32B TP4, two-host H200 policy comparison is documented in [the run report](../results/2026-09-25-full-router-comparison/RESULT.md) and [methodology](methodology.md). Its compact trial archive records completed per-request metrics, selected routes, sampled load, transfer checks and configuration choices. The full token-bearing native reports are not published; [hashes of those files](../results/2026-09-25-full-router-comparison/raw-source-hashes.json) are available for evidence matching. The compact data are enough to recompute the tuning and summary exactly.

With Python 3.12 or newer, from this repository root:

```bash
python3 results/2026-09-25-full-router-comparison/recompute.py
```

The verified output is 16 calibration episodes, 24 comparison cells, soft as the selected existing reference, allowance 0, and `engineering_criterion_met: false`. This command is read-only and does not rent GPUs. It was run against the committed archive after the final experiment.

## The live serving run

The deployed path lives under [`infra/topology/`](../infra/topology/): `run-router-session.py` orchestrates preflight, guarded two-host GPU allocation, real vLLM and llm-d sidecar startup, both transport qualifications, inference-perf traffic, evidence collection and cleanup. The frozen [plan](../workloads/router-session/plan.json) and [serving settings](../workloads/router-session/config.json) are committed. The source was exercised on Nebius H200 capacity, but the launch uses provider-specific Terraform, service accounts, cached images, resource IDs and a single-use authorization record held outside Git. It is **not** a turnkey command to reproduce on an arbitrary cloud account. No paid rerun is authorized by this document.

## Historical starting templates

The older single-prefiller/single-decoder L40S/TCP templates in `infra/`, `scenarios/` and `workloads/rate_ladder.yaml.in` remain historical scaffolding. They are not the qualified three-engine topology or the source of the reported performance numbers. Use the frozen files under `infra/topology/` and `workloads/router-session/` when reviewing the completed run.
