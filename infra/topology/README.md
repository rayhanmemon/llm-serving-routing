# Controlled topology-transfer check

**Status:** real-model direct/local/remote P/D correctness passed on September 16. Fast local transport remains unqualified after a CUDA IPC handle-open error; no latency benchmark ran. All rentals were deleted; the monitor is paused for diagnosis. Estimated H100 spending: $19.94 of $50, with $30.06 remaining. [Result](../../results/2026-09-16-h100-transfer-qualification/RESULT.md).

[SESSION.md](SESSION.md) is the launch/measurement procedure and quote. One eight-GPU H100 VM supplies a prefiller and local decoder; a separate one-GPU H100 VM supplies the remote decoder. A CPU node runs Envoy, EPP and inference-perf. Three GPUs work; nine are billed. The first test qualifies the transfer path rather than comparing routing policies.

[Short raw CUDA diagnostic](IPC-DIAGNOSTIC.md) is the next authorized step: one node, no model loading, three controlled process/container comparisons within the existing budget.

## Files

| File | Purpose |
|---|---|
| `terraform/` | Five resources: Kubernetes cluster, CPU/local/remote node groups, and the local node's GPU-cluster fabric allocation. Previous resources were destroyed; future plans allocate the eight-GPU node before CPU and remote. |
| `raw-ipc.py`, `run-ipc-diagnostic.py` | Bounded raw CUDA export/import controls and their one-node orchestration; no model or network-transfer fallback. |
| `render.py` | Render three workers, four router policies and sequential short/long workloads. Uses the published PR image tag and pinned supporting images/model. |
| `workload.py` | Render a benchmark Pod/ConfigMap with a pinned tokenizer, unique run header and optional decoder pin. It does not deploy or send requests. |
| `record-routes.py` | Add run ID and requested/selected decoder fields to the rendered Envoy access log. |
| `gpu-inspect.py` | Inspect visible GPU UUIDs and peer capability inside the GPU engine image. It does not measure KV transfer. |
| `collect.py` | Save logs, metrics, GPU identities and completed benchmark reports with checksums; errors remain failures. |
| `evidence.py` | Reject incomplete collections, changed file checksums and failed harness exits. |
| `validate-transfer.py` | Reject incomplete streams, wrong token counts, wrong routes, changed workers and mismatched paired request payloads. |
| `transfer-delta.py` | Require the qualified transfer count, check failure counters, and summarize histogram deltas separately from client latency. It cannot identify the transport by itself. |
| `import-image.py` | Validate and import the local EPP archive onto the selected CPU node using a temporary helper; remove the helper. |
| `pilot-session.py` | Admit retries against one cumulative budget, prohibit overlap, require prior cleanup/cost records and run the independent shutdown guard. |
| `verify-empty.sh`, `teardown.sh` | Destroy the dedicated state and independently check instances, clusters, disks, filesystems and GPU clusters. |

## Render and review

Use Python with the benchmark environment's PyYAML and prometheus-client. Output directories must be new:

```sh
python infra/topology/render.py \
  --local-node ACTUAL_LOCAL_NODE --remote-node ACTUAL_REMOTE_NODE \
  --cpu-node ACTUAL_CPU_NODE --out infra/topology/rendered
```

The default `--ipc-mode host` shares host IPC/PID namespaces and mounts host `/dev/shm` for cross-pod transport qualification. It requests one GPU per engine, limits each engine to six CPUs and does not enable privileged mode. `--ipc-mode isolated` prepares the separate-namespace alternative. Verify actual GPU assignment and payload transport before drawing conclusions; settings and hardware capabilities do not prove use.

Render the standalone chart from published router commit `0217d29924ba93b90f952e7a0281dd8dda146703`, then add route evidence:

```sh
helm template topology /PATH/TO/PINNED/llm-d-router-standalone \
  -n topology-measurement -f infra/topology/rendered/router-none.values.yaml \
  | python infra/topology/record-routes.py > infra/topology/rendered/router.yaml
```

Build the local `routerlib` chart dependency in a copied chart directory first. Import the EPP image before deployment: its pull policy is Never. Do not reuse the historical c1e44596 image to evaluate the published head.

## Local checks completed

- H100 Terraform validation and a five-create plan; local-only GPU-cluster attachment and preemptible settings inspected, with empty state before/after planning.
- Cleanup regressions include leftover GPU clusters, API failures and malformed/paginated responses.
- Published-head AMD64 image built; all four EPP policies passed actual local startup/health and resolved-order checks with placeholder discovery. An initial fixture expected the old role-filter type name; the assertion was corrected after checking upstream source. Production configuration was unchanged.
- Helm rendering and actual Envoy configuration validation passed. The pinned inference-perf image accepted the 60-second timeout, sequential workload and run/pin headers.
- Response/route validation and collection error-path tests passed. No synthetic test data is an inference result.

Run the focused utility checks with:

```sh
python infra/topology/test_verify_empty.py
python infra/topology/test_collection.py
python infra/topology/test_transfer_validation.py
```

The first diagnostic uses one fixed prefiller, 512/8192 requested input tokens, 128 outputs, twelve sequential requests per arm and disabled prefix caching. All actual payloads and selected routes must be checked. It does not represent Nili's decode-first evaluation or establish an advantage over tuned soft/unrestricted routing. The full policy comparison and a more representative multi-local-decoder deployment remain later work requiring their own chosen configuration and budget.
