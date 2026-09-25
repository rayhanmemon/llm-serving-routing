# Real-model routing comparison: fixed allowance lost to tuned soft locality

This run compared decoder-routing policies in a prefill-first llm-d/vLLM deployment. One eight-H200 host ran Qwen3-32B BF16 with tensor-parallel-four prefill and local decode; a second eight-H200 host ran the remote decoder. The engines used the packed BHLNC KV layout. Foreground prompts were 4,096 or 122,880 tokens with 32 output tokens. The [frozen plan](../../workloads/router-session/plan.json) and [serving settings](../../workloads/router-session/config.json) define the workload and model. Run ID: `20260925T050507Z-8b00979f`; harness source: `4eb34bf`.

Six local and six remote serving requests passed output, route and per-rank KV-transfer qualification. Calibration forced each route under four background-load states, twice each: **16 episodes**. From those measurements, the predeclared tuning procedure selected a fixed allowance of **0** and tuned soft locality as the existing reference. The evaluation then ran **20 held-out cells** covering five policies, two load traces and two repeats, plus **four confirmation cells** for the proposed rule and selected reference. All 24 cells completed. The 40 calibration and evaluation trials generated **848 benchmark requests, including background traffic**; the 12 serving-qualification requests are separate. Every completed trial has saved route and transfer checks.

The engineering criterion set before held-out evaluation required the allowance to improve mean time to first token by more than 50 ms and 2% in every matched block, within the stated regression limits. It **failed**. A positive difference below means the proposed rule was slower:

| Matched block | Allowance minus soft mean TTFT | Relative to soft |
|---|---:|---:|
| Held-out block 1 | +0.581 s | 9.87% slower |
| Held-out block 2 | +0.573 s | 9.75% slower |
| Confirmation block | +0.633 s | 10.77% slower |

The three paired blocks averaged about **0.595 s / 10.13% worse** for the fixed allowance. The table below shows all five policies in the **two held-out repeats only**. Each policy/trace mean is based on two cells with four foreground probes per cell; these descriptive means are not a tail-latency estimate or a claim that small differences between baselines are stable.

| Decode policy | Low-load mean TTFT | High-load mean TTFT |
|---|---:|---:|
| Unrestricted load scoring | 5.678 s | 7.302 s |
| Hard topology filter | 5.644 s | 7.626 s |
| Tuned soft locality | 5.659 s | 6.097 s |
| Absolute load cap | 5.643 s | 6.081 s |
| Fixed load allowance | 5.653 s | 7.257 s |

Low-load times were close. Across the three paired high-load blocks, the allowance sent six of 12 foreground probes remote, versus three of 12 for soft locality, while its mean first-token time was 7.29 s versus 6.10 s. The long-prompt means were close and the short-prompt means differed more. These route and latency observations occurred together; this experiment does not isolate which part of transfer, queueing, policy interaction or request order caused every millisecond. The result covers **one model, topology and controlled workload**, using request means rather than tail latency. It rejects a speedup claim for this fixed rule in that setting, not locality-aware routing in general.

## Evidence and reproduction

The compact [`verified-trials.tar.gz`](verified-trials.tar.gz) contains per-request records, chosen decoders, aligned engine-load samples and transfer checks for all 40 trials, without the large token-bearing raw prompts. [`recompute.py`](recompute.py) reconstructs the frozen tuning choice and 24-cell summary from the published archive and plan; it passed against this saved run. Run it from the repository root with Python 3.12 or newer:

```bash
python3 results/2026-09-25-full-router-comparison/recompute.py
```

The expected result reports `calibration_episodes: 16`, `comparison_cells: 24`, `reference: "soft"`, `allowance: {"allowance": 0}` and `engineering_criterion_met: false`. The [reproduction guide](../../docs/reproducing.md) distinguishes this offline check from recreating the provider-specific GPU deployment. [Hashes of the private full native files](raw-source-hashes.json) and the [saved analysis](analysis.json) remain available for evidence matching; the complete token-bearing native reports are not published.

## Resource record

All five scoped paid resource types were independently empty at **2026-09-25 06:39:06 UTC**. Native instance histories yield a conservative **$52.99 before-tax lifecycle estimate** for this completed run, not an invoice. The broader project's cumulative cloud estimate is **$371.95 before tax**. The cost record is included for reproducibility and accounting; it is not part of the performance result.
