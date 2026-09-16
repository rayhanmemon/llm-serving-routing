# Controlled topology-transfer check

**Status — September 16:** the closed RTX run retained 36 of 48 forced-route timing samples under the explicitly restricted TCP configuration. The experiment resources are deleted. [Results](../../results/2026-09-16-rtx-serving/RESULT.md). Routing-policy performance remains unmeasured.

Read [the deployment reference audit](DEPLOYMENT-REFERENCE.md) before another run. It records the official llm-d/provider sources, corrected renderer defaults and intentional experimental differences. Standard separate-pod GPU allocations do not guarantee CUDA IPC; the next fast-transfer baseline needs provider-correct RDMA qualification. [SESSION.md](SESSION.md) preserves the earlier run protocol; its hardware/deadline blocks are historical, not launch instructions for a new session.


## Files

| File | Purpose |
|---|---|
| `terraform/` | Five resources: Kubernetes cluster, CPU/local/remote node groups, and the local node's GPU-cluster fabric allocation. Previous resources were destroyed; future plans allocate the eight-GPU node before CPU and remote. |
| `raw-ipc.py`, `run-ipc-diagnostic.py` | Bounded raw CUDA export/import controls and their one-node orchestration; no model or network-transfer fallback. |
| `DEPLOYMENT-REFERENCE.md` | Pinned official guide comparison, justified deviations and provider/transport qualification before a new run. |
| `render.py` | Render three workers, one diagnostic and five evaluated router policies, sequential timings, calibration workloads and candidate replay traces. Uses the published PR image tag and pinned supporting images/model. |
| `workload.py` | Render a benchmark Pod/ConfigMap with a pinned tokenizer, unique run header and optional decoder pin. It does not deploy or send requests. |
| `record-routes.py` | Add run ID and requested/selected decoder fields to the rendered Envoy access log. |
| `gpu-inspect.py` | Inspect visible GPU UUIDs and peer capability inside the GPU engine image. It does not measure KV transfer. |
| `collect.py` | Save logs, metrics, GPU identities and completed benchmark reports with checksums; errors remain failures. |
| `evidence.py` | Reject incomplete collections, changed file checksums and failed harness exits. |
| `validate-transfer.py` | Reject incomplete streams, wrong token counts, wrong routes, changed workers and mismatched paired request payloads. |
| `transfer-delta.py` | Require the qualified transfer count, check failure counters, and summarize histogram deltas separately from client latency. It cannot identify the transport by itself. |
| `import-image.py` | Validate and import the local EPP archive onto the selected CPU node using a temporary helper; remove the helper. |
| `run-serving.py`, `correctness-client.py`, `render-correctness-pod.py` | Start the guarded serving fixture, verify exact direct/local/remote correctness and collect the first timing block. Dry-run by default. |
| `run-measurements.py` | Validate correctness admission, warm four input/route pairs, collect the frozen 48 requests, and retain strictly validated reports before deleting benchmark Pods. |
| `pilot-session.py` | Admit retries against one cumulative budget, prohibit overlap, require prior cleanup/cost records and run the independent shutdown guard. |
| `verify-empty.sh`, `teardown.sh` | Destroy the dedicated state and independently check instances, clusters, disks, filesystems and GPU clusters. |

## Render and review

Use Python with the benchmark environment's PyYAML and prometheus-client. Output directories must be new:

```sh
python infra/topology/render.py \
  --local-node ACTUAL_LOCAL_NODE --remote-node ACTUAL_REMOTE_NODE \
  --cpu-node ACTUAL_CPU_NODE --out infra/topology/rendered
```

The default `--ipc-mode isolated` uses private Pod namespaces and memory-backed `/dev/shm`. UCX transport discovery is unrestricted by the renderer. `--ipc-mode host`, `--ucx-tls` and `--cuda-ipc-get-zcopy` are explicit diagnostic overrides. One GPU is requested per engine; this does not grant peer-GPU access. Verify provider device access and actual payload transport before drawing conclusions.

Render the standalone chart from published router commit `0217d29924ba93b90f952e7a0281dd8dda146703`, then add route evidence:

```sh
helm template topology /PATH/TO/PINNED/llm-d-router-standalone \
  -n topology-measurement -f infra/topology/rendered/router-diagnostic.values.yaml \
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
