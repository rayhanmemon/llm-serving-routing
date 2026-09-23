# Corrected router evaluation prepared locally

No cloud resources or GPU measurements were used for this preparation. Source: `cc964fd`.

- Corrected absolute-cap tuning to match fail-open utilization filtering followed by hard locality.
- Split calibration into sixteen matched route episodes, preserving 32 foreground probes and all four load states. Only one long probe runs per episode; background is recreated for each route.
- Kept all twelve GPU startup checks and the twenty comparison plus four confirmation trials.
- Finished telemetry export before the client completion marker. Collected each completed trial once, retaining verified previous copies and a full final backup.
- Ran 267 test methods: 264 passed on macOS, three Linux-only cases passed separately in the pinned container. Success, inconclusive and interrupted summary paths are covered.
- Ran the real router and sidecar with the native benchmark client and explicitly synthetic workers: all 848 requests and 40 calibration/comparison/confirmation trials completed with route joins, growing-log snapshots and live-identity checks. Actual tuning and final summarization executed; no GPU performance claim is derived from these timings.
- Expanded soft-locality weights to include 0.01 and 0.05 before real measurements, so a near-tie locality preference is available to the baseline. The full replay used the earlier weight grid; a strict syntax-tree check establishes that this later change only adds weights, leaving request generation and control flow unchanged. The current grid passed actual tuning/summary tests and 56,108 cases against the real Go filters/scorers, with Go test caching disabled. Both grids and source hashes are retained in the proof.
- A 1.17 GB result snapshot completed in 33.8 seconds under two CPUs/2 GiB, with checksums validated after extraction. This bounds the final backup; regular collection now transfers only the completed trial.
- Native vLLM arguments/layout/worker checks, Kubernetes manifest checks and real local job/deadline acknowledgement calls passed. Provider calls and GPU workers were substituted in local rehearsals.

The revised session has $75 total authorization including setup and cleanup, replacing the prior $44.09 balance. The GPU workload, real calibration crossover, selected allowance and policy benefit remain unmeasured. Fresh capacity, no overlapping rental and both cleanup guards remain admission requirements.

## Capacity admission correction before the paid run

Nebius returned a newly fetched response marked `DATA_STATE_FRESH`, with two exact matching nodes and an older infrastructure-measurement timestamp. Its [official API definition](https://github.com/nebius/gosdk/blob/main/proto/nebius/capacity/v1/resource_advice.pb.go#L25-L35) defines FRESH as reflecting the current system state. Our separate thirty-minute timestamp cutoff was an unsupported client assumption. The admission check now uses the provider freshness state, still rejects unknown/stale/future-dated advice, and records measurement age. Availability advice remains non-reserving and can lose a race with placement. Capacity is still rechecked before each GPU request. Budget, usage and cleanup guards are unchanged.

The entire 267-method suite passed again. A syntax-tree comparison proves that only the cloud-advice function changed relative to the completed workflow proof; other production hashes match. The original proof and the current validated proof are both retained. This did not require rerunning unchanged inference tests or renting a node.
