# Methodology

Every number in the report links to a run directory under `results/`, and the measurements are taken on the real cluster the scenario describes.

*Status: stub — populated before the first measured run, and frozen before the sweep.* The two design rules below are already fixed; the sections after them are the outline of what the rest of this document will cover.

## Design rules fixed before the sweep grid is frozen

Both rules were fixed on 2026-08-29, ahead of the sweep grid being frozen; the commit that added them is the record of when. They are rules about what counts as data, not about what the data will say.

### 1. The transport check — a mismatch is a failed run, not a data point

Terms used below: *prefill* is the processing of the prompt; *decode* is the generation of output tokens one at a time. A *disaggregated* arm runs prefill on one set of servers and decode on another, so the prompt's key/value cache (the KV cache — the per-token state a transformer keeps for the prompt) must be transferred from the prefill server to the decode server over the network. The transfer runs beneath vLLM's KV connector through the NIXL transfer library, which chooses a transport (the high-speed fabric, or plain TCP) per pair of servers.

Every run on a disaggregated arm asserts three things before any of its numbers are trusted:

1. **The split actually happened.** The router's endpoint picker keeps a count of its routing decisions by kind — routed through a prefill server, or served decode-only. That count must have moved toward prefill-then-decode during the run. Zero transferred bytes has two unrelated causes — no split was attempted, or the split was attempted and nothing moved — and this count is what separates them.
2. **The cache actually moved.** The bytes transferred per request, read from the engine's own transfer counters on the decode side (decode pulls the cache), must be consistent with the size computed from the model's per-token cache footprint times the input length.
3. **The declared transport carried it.** The transport that carried the cache is read from the transfer library's own selection line for the pair — not from the list of transports it *could* have used, which is a menu, not a choice — and the measured transfer rate must be consistent with that transport.

If any of the three fails, the run is recorded as failed and its numbers are discarded: never reported, never averaged in, never quietly re-run. The thresholds behind "consistent with" are derived on the cluster that is measured and committed with the frozen grid; nothing is imported from a rehearsal on other hardware.

Why the rule exists: a fast transport listed as available says nothing about whether it was used. When the fast path is unavailable to a pair, the transfer library falls back to plain TCP without a word, and the result is a run that completes, returns a success status to every client, and produces plausible-looking numbers that measure the wrong thing.

### 2. Grid pruning — what a small-scale rehearsal is allowed to change

Before the sweep, the same serving shapes were rehearsed at small scale on cheaper GPUs with no high-speed fabric and a small model. That rehearsal may change the sweep grid in exactly two ways:

- **drop** a shape that was illegal or failed to start there;
- **demote** a shape that the sweep plan already limits to a single spot-check cell from that spot check to not measured.

It may never remove or alter the primary disaggregated arm or the tuned colocated control arm (the baseline where prefill and decode share the same engine on the same GPUs, with chunked prefill, tuned by a published sweep). A performance ranking from the rehearsal is not a reason to change anything: a small model on a fabric-less tier cannot rank the shapes on the target hardware, and the rehearsal's own expectation was that the colocated baseline would win there. The freeze record lists, cell by cell, what changed and names the rehearsal as the reason without quoting a number from it. No number from the rehearsal enters the analytical performance model, a figure, a table or a claim; the one thing it may inform is scheduling — how long switching between arms takes. If the rehearsal did not run, the freeze record says so and nothing changes.

## Outline of the rest

Will cover:

- **Scenario**: nodes, GPUs, NIC count and per-NIC bandwidth, fabric, model, engine and llm-d versions with image digests.
- **Workload profiles**: input/output length distributions, arrival model (open-loop vs closed-loop stated explicitly), dataset.
- **Baseline**: how the chunked-prefill colocated baseline was tuned — the tuning sweep is published, not just the chosen configuration.
- **The analytical performance model**: committed before the runs it is validated against; reported as predicted / measured / relative error.
- **Run rules**: seeds recorded, one uninterrupted run per recorded result, node and fabric identifiers logged per run so placement changes are detectable; the transport check above applied to every disaggregated run.
- **Traceability**: every number in the report links to a self-contained run directory under `results/`.
