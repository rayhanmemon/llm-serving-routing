# Methodology

Every number in the report links to a run directory under `results/`, and the measurements are taken on the real cluster the scenario describes.

*Status: stub — populated before the first measured run, and frozen before the sweep.* The two design rules below are already fixed; the sections after them are the outline of what the rest of this document will cover.

## Design rules fixed before the sweep grid is frozen

The transport rule was fixed on August 29, 2026; the bounded grid below replaces the earlier pruning rule on September 10. Dated commits record their adoption. They are rules about what counts as data, not about what the data will say.

### 1. The transport check — a mismatch is a failed run, not a data point

Terms used below: *prefill* is the processing of the prompt; *decode* is the generation of output tokens one at a time. A *disaggregated* arm runs prefill on one set of servers and decode on another, so the prompt's key/value cache (the KV cache — the per-token state a transformer keeps for the prompt) must be transferred from the prefill server to the decode server over the network. The transfer runs beneath vLLM's KV connector through the NIXL transfer library, which chooses a transport (the high-speed fabric, or plain TCP) per pair of servers.

Every run on a disaggregated arm asserts three things before any of its numbers are trusted:

1. **The split actually happened.** The router's endpoint picker keeps a count of its routing decisions by kind — routed through a prefill server, or served decode-only. That count must have moved toward prefill-then-decode during the run. Zero transferred bytes has two unrelated causes — no split was attempted, or the split was attempted and nothing moved — and this count is what separates them.
2. **The cache actually moved.** The bytes transferred per request, read from the engine's own transfer counters on the decode side (decode pulls the cache), must be consistent with the size computed from the model's per-token cache footprint times the input length.
3. **The declared transport carried it.** The transport that carried the cache is read from the transfer library's own selection line for the pair — not from the list of transports it *could* have used, which is a menu, not a choice — and the measured transfer rate must be consistent with that transport.

If any of the three fails, the run is recorded as failed and its numbers are discarded: never reported, never averaged in, never quietly re-run. The thresholds behind "consistent with" are derived on the cluster that is measured and committed with the frozen grid; nothing is imported from a rehearsal on other hardware.

Why the rule exists: a fast transport listed as available says nothing about whether it was used. When the fast path is unavailable to a pair, the transfer library falls back to plain TCP without a word, and the result is a run that completes, returns a success status to every client, and produces plausible-looking numbers that measure the wrong thing.

### 2. Bounded comparison, fixed before held-out evaluation

Use one model and precision, one tuned colocated configuration and one split configuration at equal total GPU count. H200 calibration selects three prompt lengths and three shared offered rates at fixed output length. Two configurations and three repeats give 54 architecture runs. A separate stock-versus-modified Go policy comparison uses two workload regimes and three repeats, giving 12 runs. Preserve a separate held-out set for analytical model validation.

Price the complete schedule, including startup, model loading, arm changes, interruptions and teardown, before freezing it. The first fallback removes one input-length slice before held-out evaluation: 36 architecture plus 12 policy runs. Do not remove a cell because its measured outcome is inconvenient, and do not force a crossover or a policy win.

The cheaper lab's shape comparison was cut and supplies no ranking or completed experiment. No lab throughput, latency or transfer rate enters the capstone model or figures. Its arm-change timing may inform scheduling, and its cache-bytes derivation may be reused as a method with the actual model's dimensions.

### 3. Interruptions and failures

Recorded runs may use preemptible nodes. If an interruption changes placement or breaks a comparison block, retain the failed attempt and its cost, restore node identity and transport checks, and repeat the complete affected comparison block within the priced retry allowance. Never stitch surviving samples into a complete result or compare one arm before replacement with another after it. Report interrupted attempts and their cost alongside performance from complete blocks. Do not label application failures as infrastructure interruptions to discard them.

Failure drills distinguish confirmed process death from graceful deletion. An API force-delete alone does not prove that the serving process exited. The RDMA/TCP counterfactual uses separately verified static configurations; it is not evidence of live transport failover.

## Outline of the rest

Will cover:

- **Scenario**: nodes, GPUs, NIC count and per-NIC bandwidth, fabric, model, engine and llm-d versions with image digests.
- **Workload profiles**: input/output length distributions, arrival model (open-loop vs closed-loop stated explicitly), dataset.
- **Baseline**: how the chunked-prefill colocated baseline was tuned — the tuning sweep is published, not just the chosen configuration.
- **The analytical performance model**: committed before the runs it is validated against; reported as predicted / measured / relative error.
- **Run rules**: seeds recorded, one uninterrupted run per recorded result, node and fabric identifiers logged per run so placement changes are detectable; the transport check above applied to every disaggregated run.
- **Traceability**: every number in the report links to a self-contained run directory under `results/`.
