# plugin — a bounded congestion check for llm-d routing

**Status: not started.** This is the planned component; no implementation or measured improvement is claimed. The design and tests precede code. Upstream filing is optional; any later proposal will be linked with its true status.

## Decision

Extend the shipped prefix policy with a small Go congestion veto. If all eligible prefill endpoints are congested and the selected decode endpoint has headroom, serve the request on decode; otherwise retain the stock decision. The selected decode endpoint is known at this point, but the eventual prefill endpoint has not been selected.

Use the existing producer interface to copy candidate metrics into request-local data before the decider runs. Queue validity and freshness must be checked specifically: an overall metrics timestamp can advance while an old queue value survives. Missing or stale data preserves stock behavior. Pin the in-package patch and custom router image; no scheduler-interface redesign is planned. This is not an EDPP implementation or a novelty claim.

## Measurement

Compare stock and modified routing on two workloads, chosen from calibration before evaluation: one expected to benefit and one expected to expose a weakness. Three repeats for each policy/workload pair produce 12 runs on the same hardware. Report the measured result even if the extension does not improve performance. No performance claim is made without this comparison.

## Contents to add

- Design note: decision, eligibility, signals, freshness and fallback behavior.
- Go source and focused correctness tests.
- Reproducible policy comparison and links to raw results.
