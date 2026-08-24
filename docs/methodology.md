# Methodology

*Stub — populated before the first measured run, and frozen before the sweep.*

Will cover:

- **Scenario**: nodes, GPUs, NIC count and per-NIC bandwidth, fabric, model, engine and llm-d versions with image digests.
- **Workload profiles**: input/output length distributions, arrival model (open-loop vs closed-loop stated explicitly), dataset.
- **Baseline**: how the chunked-prefill colocated baseline was tuned — the tuning sweep is published, not just the chosen configuration.
- **The analytical performance model**: committed before the runs it is validated against; reported as predicted / measured / relative error.
- **Run rules**: seeds recorded, one uninterrupted run per recorded result, node and fabric identifiers logged per run so placement changes are detectable.
- **Traceability**: every number in the report links to a self-contained run directory under `results/`.
