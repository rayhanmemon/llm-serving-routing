# Methodology

**Status: design outline.** No evaluation has run. Hardware qualification, implementation and frozen acceptance criteria remain unfinished.

## Policy comparison

Compare six policies: no topology preference, existing hard topology, tuned soft topology, a tuned load/capacity filter followed by topology, the new gate with the best global allowance, and the gate with two prompt-size allowances. Preserve prefill-first selection in the primary comparison and use the same model, precision, physical GPU allocation, engine/connector settings, cache behavior, arrival profile and hard overload protections.

Calibrate first, then freeze the policy parameters and evaluation workload. Three held-out families cover balanced traffic where locality can help, localized congestion/bursts where escape can help, and mixed prompt/output/cache behavior that challenges request-count estimates. Three paired repeats per family and policy give **54 short held-out runs**, plus separate calibration. Automate warmup, order, completion/drain and result extraction. Three run-level observations can leave wide uncertainty; report it and repeat only when needed to resolve a material conclusion.

Do not remove an inconvenient workload, force a speedup, or interpret an underpowered comparison as equivalence. Beating only the hard filter is insufficient. If the two-range rule cannot beat the best global allowance, simplify it.

## Verify the execution path

Use at least one prefiller, one local decoder and one remote decoder on distinct physical GPUs across two hosts. A fourth active worker permits two local decoders. Verify output correctness, actual worker identities and reachable local congestion while hard eligibility still permits both choices.

For each transfer path verify:

1. **The split happened:** the actual request used the selected prefill and decode workers.
2. **The expected state moved:** byte/block accounting reflects the model layout, connector and destination cache, not just the prefiller’s uncached input tokens.
3. **The intended operation carried it:** inspect transfer diagnostics/counters, not merely available transports or topology labels.

Keep the chosen transfer mode, memory layout and transport configuration identical across routing policies. Enabling an existing fast connector path is baseline preparation; its gain is not attributed to the new router code. Physical host/fabric labels must describe actual placement. Controlled link throttling is a sensitivity experiment, not evidence about an ordinary production fabric.

Verify the gate’s observed inputs, local retention and remote escape after the full filter/scorer chain. Missing data is not zero load. Preserve and test disabled-mode behavior and endpoint lifecycle changes.

## Metrics and outcome accounting

Measure client time to first token, completed throughput under declared latency objectives, ongoing streaming behavior, failures/rejections, unfinished requests, actual output lengths, cache reuse, transfer bytes/times and endpoint-picker overhead. A pre-decode timestamp is not client first-token latency. If several tokens arrive per response chunk, label observed gaps as chunk gaps rather than exact inter-token latency.

Include every offered request and drain or explicitly account for unfinished work. Set the minimum useful improvement and permitted regressions before held-out runs, informed by calibration noise and workload objectives. Use engine/connector telemetry to explain causality and client measurements for the reported serving result.

## Interruptions and application failures

Price startup, loading, calibration, runs, retries, storage and teardown before renting. With preemptible (spot) instances, retain interrupted attempts and costs, recheck physical placement/transport and repeat the complete affected paired block. Do not stitch surviving samples into a complete result. Application failures remain outcomes, not infrastructure interruptions to discard.

Teardown verification requires successful resource listings. Preserve unrelated resources. Failure/recovery tests cover the actual change; a successful HTTP status alone does not establish a complete stream.

## Reproduction record

Every published result links to a self-contained directory under `results/` containing:

- Hardware and physical worker placement, model, router/engine versions, image digests and actual transport.
- Rendered configuration, workload, commands and seeds.
- Calibration/evaluation designation, policy settings and tuned baseline choices.
- Per-request output, routing decisions, transfer evidence and relevant telemetry.
- Analysis, uncertainty, limitations, failures, interruptions and cost including unused rented capacity.

Simulator output describes simulated behavior. Inference-performance claims require this project’s own real-model measurements. An independent reproduction check follows the published instructions before end-to-end reproducibility is claimed.
