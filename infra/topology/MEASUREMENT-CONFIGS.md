# Measurement configuration contract

`render.py` creates one diagnostic router and five evaluated routing policies. The numeric defaults are staging candidates. Calibration must choose and record the final soft weight, absolute cap and relative allowance before held-out runs.

| File | Decode pipeline |
|---|---|
| `router-diagnostic.values.yaml` | capacity filter, diagnostic endpoint pin, active-request scoring |
| `router-none.values.yaml` | capacity filter, active-request scoring |
| `router-hard.values.yaml` | capacity filter, hard host affinity, active-request scoring |
| `router-soft.values.yaml` | capacity filter, weighted topology scoring plus active-request scoring |
| `router-absolute-cap.values.yaml` | capacity filter, absolute active-request cap, hard host affinity, active-request scoring |
| `router-allowance.values.yaml` | capacity filter, relative-load host-affinity gate, active-request scoring |

All profiles use prefill-first scheduling and the same prefill pipeline. Only the diagnostic profile contains `session-affinity-filter` and accepts `x-benchmark-decoder`. Policy workloads contain no destination pin. The absolute-cap policy always writes both its `maxValue` and `fallbackOnEmpty` choice; the filter runs before topology affinity.

Render candidate parameters explicitly when preparing a run:

```sh
python infra/topology/render.py \
  --local-node LOCAL --remote-node REMOTE --cpu-node CPU \
  --allowance 2 --soft-weight 0.5 --absolute-cap 2 \
  --absolute-cap-fallback-on-empty \
  --heldout-low-spacing 4 --heldout-burst-spacing 0.25 \
  --out RENDERED
```

`--attention-backend BACKEND` is optional and applies the same vLLM override to the prefiller and both decoders. Omitting it preserves the H100/H200 manifests. RTX PRO 6000 SM120 qualification uses `--attention-backend TRITON_ATTN`: the unmodified vLLM 0.26 image has a confirmed first-call failure in its SM120 CuTe FlashAttention path, while a separate v0.26 SM120 report demonstrates correct BF16 execution with `TRITON_ATTN`. This is a compatibility safeguard whose correctness still requires real-model startup and output checks, not a measured performance choice. Sources: https://github.com/vllm-project/vllm/issues/51776 and https://github.com/vllm-project/vllm/issues/53481.

The first forced-route block uses `router-diagnostic.values.yaml` and these stable inputs:

- `warmup-512.yaml`, `warmup-8192.yaml`
- `benchmark-512.yaml`, `benchmark-8192.yaml`

Each measured benchmark remains 12 sequential requests with 128 output tokens. The batch driver calls:

```sh
python infra/topology/workload.py CONFIG \
  --name UNIQUE --cpu-node CPU --namespace topology-measurement \
  --decoder-pod ACTUAL_DECODER_POD
```

`workload.py` emits a ConfigMap and Pod as multi-document YAML, writes `/reports/exit-code`, and retains the Pod for report collection. The tokenizer is cached on the dedicated CPU node under a revision-specific host path. Its init container locks the cache and writes a marker only after a complete download. Trace CSVs referenced beside a rendered config are bundled into the same ConfigMap; paths outside the rendered directory are rejected.

`calibration-512.yaml` and `calibration-8192.yaml` use unpinned concurrency stages 1, 2, 4 and 8 through the EPP. Later stages are candidates for both-decoders-busy conditions; the operator must confirm actual per-decoder in-flight counts in EPP trace logs and routes. Requested concurrency does not prove the gate observed the intended load. One load-generator worker serves all stages so the configured seed produces the same prompt bytes across policy runs; inference-perf raises that worker's concurrency limit for each concurrent stage.

`heldout-low.yaml` and `heldout-burst.yaml` replay exact alternating 512/8192-token traces with 128 output tokens. The low trace spaces requests evenly. The burst trace has four 30-second phases: modest load, configured burst, both-busy pressure and recovery. Spacing values are candidates until calibration. Every request enters through the EPP, so its in-flight producer observes the same traffic used to evaluate the policy.

Inference-perf v0.6.1 reads every trace row and normalizes the first timestamp to zero. It schedules the remaining offsets directly; it does not rescale them to the configured stage duration. Its timestamp parser keeps centisecond precision, so generated spacing must remain a multiple of 0.01 seconds. In multiprocessing trace replay, the trace row count overrides `rate * duration`; the single standard stage is required by the schema, and its rate is only nominal metadata. With the default spacing, the low trace has 30 requests ending at 116 seconds and the burst trace has 210 requests ending at 118 seconds.

Trace and calibration configs use one inference-perf worker. Version 0.6.1 reseeds a separate random-data generator in each worker, so multiple workers can assign the same trace index to different RNG streams and change prompt bytes between policy runs. One worker with a higher concurrency ceiling preserves exact prompt hashes for a fixed trace and `load.base_seed`. An offline replay through the pinned image confirmed exact trace counts, alternating lengths, 128-token request limits, unscaled offsets and repeatable prompt hashes.

The stage report records `load_summary.count`, aggregate `schedule_delay`, `send_duration`, `requested_rate` and `achieved_rate`. For trace replay, `requested_rate` is the nominal stage field; use the trace timestamps and `achieved_rate` to assess offered-load fidelity. The per-request report is an array. Each row includes monotonic `start_time` and `end_time`, the serialized request, error, configured input-token count, response chunks, chunk times, output-token times and server usage. It does not include the intended trace timestamp or Envoy request ID. Pair samples across policies by SHA-256 of the serialized prompt within the same block and verify the report's copied `config.yaml`, trace hash, seed and request count.

`run-measurements.py` supports only the forced diagnostic block. `run-policy-comparison.py` runs one already-active evaluated policy per invocation. It never changes the router configuration. Install the declared `router-POLICY.values.yaml`, wait for the EPP rollout and resolved ConfigMap, then run a dry preview:

```sh
python infra/topology/run-policy-comparison.py \
  --plan PLAN.json --run-dir RUN --rendered-dir RUN/rendered --cpu-node CPU
```

Add `--execute` only for the reviewed run. The default makes no Kubernetes calls. A version-1 plan has one policy and an ordered `runs` list. Every run names a unique Kubernetes-safe `block_id`, sibling `workload_config`, matching `router-POLICY.values.yaml`, mode (`calibration` or `heldout`), seed, expected request count, and arrival-trace SHA-256 (`null` for fixed-concurrency calibration). Calibration and held-out items cannot share a plan. A held-out plan requires `frozen: true`; every non-reference held-out block points to the prior reference block's `policy-evidence.json`. Execution also requires `RUN/policy-plan-reviewed.json` to approve the SHA-256 of the exact held-out plan bytes. The runner verifies that the active EPP ConfigMap uniquely matches the declared manifest before sending traffic.

Use this shape:

```json
{
  "schema_version": 1,
  "policy": "allowance",
  "frozen": true,
  "reference_policy": false,
  "runs": [{
    "block_id": "mixed-b1",
    "workload_config": "heldout-burst.yaml",
    "policy_manifest": "router-allowance.values.yaml",
    "mode": "heldout",
    "seed": 17092027,
    "expected_count": 210,
    "arrival_trace_sha256": "TRACE_SHA256",
    "paired_with": "policy-attempts/REFERENCE/heldout-burst/after/policy-evidence.json"
  }]
}
```

Run the same frozen traces and block order for all policies, drain between blocks, and complete a policy batch before externally switching to the next policy.

Evaluated-policy route logs have `request_id`, `run_id`, `requested_decoder`, `selected_decoder`, `upstream_host`, `status`, `duration_ms` and `response_flags`. The diagnostic response fields are unavailable without the session-affinity plugin, so unpinned policy runs normally record `-` for requested and selected decoder. Map `upstream_host` to the current decoder Pod IP and sidecar port; prior runs used `IPv4:8000`. Require a unique request ID, the expected run ID, HTTP 200, `response_flags: -`, and an upstream host belonging to one of the two unchanged decoder Pods. Aggregate route counts by upstream host are exact. Attach a route to an individual client record only when every non-terminal SSE chunk carries one consistent canonical `cmpl-<UUID>` and those UUIDs form an exact unique bijection with Envoy request IDs. Otherwise record per-request route mapping as unknown. Never join client records and Envoy routes by completion order under concurrency.

EPP verbosity 5 logs active-request counts at the active-request scorer, after preceding filters have run. Those logs show only surviving endpoints. When hard affinity, the absolute cap or the relative allowance excludes the remote decoder, the scorer log cannot prove the remote count that the filter compared. The utilization filter logs candidate and survivor totals but not the excluded endpoint's count; the topology gate does not log its raw local/remote minima. Periodic EPP in-flight metrics can add context, but settled before/after snapshots are usually zero and are not a per-decision trace. Raw pre-filter decision instrumentation would be needed for that stronger claim and is outside this measurement preparation.

Pinned foreground observations are diagnostic mechanism checks. They can compare known destinations under imposed load, but they are excluded from policy throughput, goodput and route-share claims. Evaluated policy traffic is unpinned. Report achieved request rate, scheduling delay, failures, actual routes and observed in-flight counts; an offered trace alone does not establish those outcomes.

## Deployment reference defaults

Follow [DEPLOYMENT-REFERENCE.md](DEPLOYMENT-REFERENCE.md). The renderer uses isolated Pod namespaces, explicit CUDA buffers and UCX backend selection, and no forced `UCX_TLS` or CUDA-IPC GET setting. Provider-specific RDMA devices/capabilities must be qualified before a fast-transfer performance run. Host namespaces and restricted transports are explicit diagnostic flags. Saved September 16 manifests remain unchanged and describe the earlier restricted-TCP fixture.
