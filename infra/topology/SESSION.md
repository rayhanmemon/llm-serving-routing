# H100 controlled transfer check

**Current authorization — September 16:** multiple unattended attempts are authorized within **$50 total before tax** to finish this first transfer pilot. Count the prior H100 cost ($0.50098345) in that total. One attempt runs at a time; cleanup and cost accounting precede each retry. The previous one-attempt restriction is superseded. The [September 15 failure](../../results/2026-09-15-h100-topology-transfer/RESULT.md) remains preserved.

## What this check answers

Does transferring KV to a decoder on the prefiller's H100 host cost less than transferring it to a decoder on another VM, and is the intended local CUDA IPC payload path actually used? This check does not compare routing policies or establish the allowance's performance value.

Placement: one prefill GPU plus one local decode GPU on an eight-GPU H100 VM; one remote decode GPU on a one-GPU H100 VM; one CPU utility node. Three GPUs work and nine are billed. The GPU-cluster resource selects an available fabric (2, 3, 4 or 6) for the eight-GPU VM only; the fresh Terraform plan must name the selected fabric. The remote VM is outside it and uses ordinary networking. Record actual placement and transport; separate VM identities do not establish separate physical chassis.

Use `router-none.values.yaml` for this diagnostic. The same prefill-first handler, role/capacity filters and session pin serve both forced routes. The topology/load policies are staged for later comparison, but do not select the diagnostic destination.

## Price and approved limits

Nine preemptible H100s: $19.35/hour including their associated CPU/RAM. Utility node: $0.3968/hour. 576 GiB Network SSD: $0.05602/hour. **Total about $19.80/hour before tax; two fully running hours about $39.61.** Approved cumulative allowance: **$50 before tax**, including earlier H100 attempts, teardown starting by minute 90 from provisioning, deletion targeted by minute 120. Rayhan approved this separate H100 session; the previous $25 authorization covered the closed L40S attempt.

Multiple placement attempts are allowed under the cumulative budget. Provision the scarce eight-H100 node first; CPU and remote node groups depend on it. Stop the current attempt if the topology has not placed within 30 minutes, an engine has not become ready within 30 minutes on ready nodes, or an instance is preempted. Begin cleanup at the time limit even if diagnostics are incomplete. Preserve failed results. Keep preemptible pricing and the same topology. No on-demand substitution or larger rental. Before each attempt require at least $44.60564 remaining: two fully running hours plus a $5 cleanup reserve. Pricing sources: [compute/storage](https://docs.nebius.com/compute/resources/pricing), [other services](https://nebius.com/prices). Refresh live capacity immediately before the approved attempt.

## Exact software and prepared artifacts

- Source: `0217d29924ba93b90f952e7a0281dd8dda146703` from draft PR #2870; same tested source tree as implementation commit `8f3f2838`.
- EPP image: `ghcr.io/llm-d/llm-d-router-endpoint-picker:topology-0217d299-amd64`; local image ID `sha256:d8dfea7c43d1683a3f47abb74483f5c2d2d3cd2ccb9322c0fd8df493ba52f8f8`.
- Durable archive: `/Users/rayhanmemon/.codex/run-state/router-h100-pilot/prepared/topology-epp-0217d299-amd64.tar`; SHA-256 `7ab220d2eb3a56d62ffb5595f3981944256c4072be7e88913be4bb3e182dd0d5`.
- Prepared fabric-2 plan: `/Users/rayhanmemon/.codex/run-state/router-h100-pilot/prepared/fabric2.tfplan`, five creates only. Refresh capacity and plan before a delayed launch; replan whenever inputs change.
- Qwen3-8B BF16 revision, vLLM 0.26, sidecar 0.10, inference-perf 0.6.1 and Envoy image digests are pinned in the renderer/workload scripts.

**September16 startup correction:** default UCX selection attempted InfiniBand and exceeded the container's8MiB locked-memory limit, failing NIXL backend initialization. Before any inference, all three engines were configured with `UCX_TLS=tcp,cuda_copy,cuda_ipc,self`. This preserves the intended ordinary-network remote leg and CUDA-IPC local option without changing privileges. Actual executed transport must still be qualified. [UCX transport selection](https://openucx.readthedocs.io/en/master/faq.html).

`render.py` defaults to host IPC/PID namespaces and mounts the host's `/dev/shm` rather than shadowing it with a private mount. GPU resource allocation remains one per engine, CPU limit 6 per engine, with no privileged mode. This is a deliberate transfer-qualification setting on dedicated experiment VMs. `--ipc-mode isolated` renders the original namespace arrangement for diagnosis. Do not silently change namespaces or device access between local/remote arms. If the staged host mode cannot reach the peer GPU, stop for a focused configuration review rather than broadening privileges on the meter. CUDA IPC flags alone do not prove reachability or payload selection.

## Unattended launch procedure

Use `/Users/rayhanmemon/.codex/run-state/router-h100-pilot/approval-retries-2026-09-16.json`. Its authorization scope is `h100-pilot-2026-09-16`, meaning the cumulative September 16 retry authorization. It explicitly permits multiple attempts with a $50 total ceiling and the exact historical H100 cost. Keep the old `approval.json` and `attempt.json` unchanged as historical records.

`budget.json` under that state directory is the current attempt/spend ledger. The helper uses a lock to prevent overlapping launches and requires prior cleanup and cost records. New attempts have their own `runs/SESSION_ID/attempt.json`; do not infer current activity from the legacy top-level `attempt.json`.

Regenerate and inspect the five-create Terraform plan: old saved plans predate the local-node-first dependency order and must not be applied. Run `pilot-session.py --execute --approval-record /Users/rayhanmemon/.codex/run-state/router-h100-pilot/approval-retries-2026-09-16.json --approved-max-usd-pretax 50 --purchase-type preemptible --plan FRESH_PLAN`. The helper starts its detached shutdown guard before apply. During provisioning inspect the actual instance create operation: the Kubernetes node-group operation can keep waiting after an instance has already failed with NotEnoughResources. Interrupt only this attempt's apply promptly on confirmed failure.

After the block or a failure, run `python infra/topology/pilot-session.py cleanup RUN_DIR --execute`. The guard and normal cleanup share a lock. Cleanup completion marks the attempt as awaiting cost accounting. Save `RUN_DIR/cost-estimate.json` with the matching `session_id`, nonnegative `estimate_usd_pretax`, methodology and per-resource operation evidence. Count actual allocated resource lifetimes plus a conservative disk allowance; an unallocated eight-GPU VM has no running GPU charge. The next admission reconciles this record into the ledger. Missing cleanup/cost records block another attempt; never erase history to reset spending.

Require a positive fresh capacity sample newer than the failed placement, plus single-H100 capacity. Wait at least 30 minutes after one capacity failure, 60 minutes after two consecutive failures, and two hours after three or more. After three consecutive capacity failures without material improvement, keep monitoring without paid retries until advice shows a larger eight-GPU count or a positive opening on another fabric. Repeated unresolved serving/transfer failures stop paid retries too. Stop after the first valid transfer block or when safe budget is insufficient; report the evidence after cleanup.

The guard is local, so keep the Mac awake and connected. If system PodDisruptionBudgets block the last node during full-cluster teardown, preserve their state and remove only the observed blockers in this dedicated experiment cluster. Deletion-only retries continue until verification succeeds. The $50 limit is managed locally, not a provider-enforced billing cap. Private reusable correctness clients and operating notes are in the state directory's `prepared/` folder.

## Before any measured request

1. Record session approval/start time and start the scoped deadline guard. Apply the reviewed plan and use a dedicated kubeconfig/context. Identify actual nodes from node-group IDs and verify GPU counts.
2. Inspect the allocated hardware using the GPU engine image, which contains GPU utilities. The CPU benchmark image cannot supply `nvidia-smi`. `gpu-inspect.py --expected-count 2` can inspect two allocated local GPUs before model startup. This checks capability only; real cross-pod payload transfer must follow.
3. Render with actual hostname labels, import the checksum-verified EPP archive on the CPU node, then deploy the model workers and `router-none` configuration. Inspect resolved profiles, actual container images, readiness and distinct engine GPU UUIDs. Import uses the installed containerd client and removes its temporary helper.
4. Carry the operator's committed directional expectation forward only while the stated request comparison remains unchanged; record any revision before requests. The earlier expectation remains untested.
5. Run explicit correctness probes through direct decode and both P/D routes, checking selected endpoint, output, P/D execution and transfer. Establish the number of transfer operations per request during these probes (expected one for this TP1 setup), before measured arms. Keep them separate from latency samples. Warm **both prompt sizes on both routes**, with the same settings, then drain all requests before snapshots. Allow the engine metrics reporter to catch up; confirm cumulative transfer counters are stable across its reporting interval before taking the baseline.

## First measured block

Completion API, streaming, prefix cache disabled, 128 output tokens with EOS ignored, one request at a time. Each arm sends 12 requests with the same seed and one generator process. Request timeout 60 seconds. The exact request payloads are retained and must match across paired routes; a seed is not accepted as proof.

| Order | Input tokens | Forced decoder | Run name |
|---:|---:|---|---|
| 1 | 512 | local | short-local-b1 |
| 2 | 512 | remote | short-remote-b1 |
| 3 | 8192 | remote | long-remote-b1 |
| 4 | 8192 | local | long-local-b1 |

The first block is 48 measured requests plus separate warmups/correctness probes. For the requested unattended pilot, collect evidence and tear down after this block, then review it together before further work. Any planned repeats use new run names and reverse route order; do not selectively rerun an unfavorable outcome. These are pilot/calibration observations, not a final tail-latency or goodput claim.

For each arm: take a settled before snapshot; generate the workload with its unique run name and actual decoder pod; run it; wait for requests to drain and metrics to settle, then collect reports plus the after snapshot; validate before moving on. `x-benchmark-run` identifies the arm in Envoy logs, while `x-benchmark-decoder` carries the requested worker. The response log records the actual selected worker. The session filter can fail open on a stale pin, so verify every route.

```sh
python infra/topology/collect.py --context router-topology --out RUN/before
python infra/topology/workload.py RENDERED/benchmark-512.yaml \
  --name short-local-b1 --cpu-node CPU_NODE --decoder-pod LOCAL_DECODER > RUN/workload.yaml
# Apply the workload only within the authorized session, then collect after completion.
python infra/topology/collect.py --context router-topology \
  --benchmark-pod short-local-b1 --out RUN/after
python infra/topology/validate-transfer.py --before RUN/before --after RUN/after \
  --run-id short-local-b1 --decoder-pod LOCAL_DECODER --input-tokens 512
python infra/topology/transfer-delta.py --before RUN/before --after RUN/after \
  --decoder-pod LOCAL_DECODER --expected-transfers 12
```

For the opposite route add `--paired-with FIRST_RUN/after/validation.json`. Copy reports within their ten-minute retention period; missing reports or a nonzero harness exit are failures. Python needs the existing benchmark environment's PyYAML and prometheus-client packages.

## What passes qualification

- Correct selected decoder for all requests, complete valid SSE with terminal `[DONE]`, `finish_reason=length`, server-reported token counts, and valid client content timestamps.
- Unchanged pod UID, container ID, restart count, node and GPU identity; three distinct allocated engine GPUs. No undrained work or transfer failure/expiry.
- NIXL bytes/time histogram counts matching the qualified expected number of operations, positive bytes/time observations, and UCX evidence connecting the executed local GPU READ payload to `cuda_ipc`. Identify remote payload transport separately. Seeing TCP control traffic or a CUDA IPC capability listing is not enough to identify the KV payload path.
- Client TTFT comes from first generated content. Sidecar `true_ttft_ms` ends before decode KV loading and cannot replace it. Histogram deltas are aggregate connector observations, not per-request transfer decomposition or raw NVLink bandwidth.

A small or absent latency advantage is a valid outcome when the transfer path is qualified. Missing/ambiguous path evidence is unqualified. Stop on route mismatch, malformed/truncated output, transfer errors, missing observations, changed workers or an unresolved slow local path. Do not claim the gate helps from this experiment alone.

## Cleanup and next stage

Use a new durable private session directory under `/Users/rayhanmemon/.codex/run-state/router-h100-pilot/` before provisioning; retain capacity snapshots, reviewed plan metadata, guard/apply/teardown logs and successful or failed collections. Never put credentials in the public results. On any failure, collect available diagnostics without delaying the cleanup deadline. Save/checksum evidence, destroy the dedicated Terraform resources and run the independent checker. It must successfully list zero instances, Kubernetes clusters, disks, filesystems **and GPU clusters**. Record actual resource costs separately from human attention.

Later work calibrates unrestricted routing, hard locality, tuned soft scoring, load-filter-plus-locality and our allowance; prompt-size configuration remains unfinished. Held-out comparisons must cover low-load locality benefit, congestion/bursts and mixed workloads, followed by a more representative multi-local-decoder placement. That deployment and budget require a separate decision. No result here is a direct numerical comparison against Nili's different deployment.

## Latest capacity read

At 04:40 UTC September 16, no eligible eight-H100 preemptible availability was reported; positive single-H100 capacity remained. Check the live advisor before every launch. The positive September 15 snapshots did not guarantee placement and must not be replayed as authorization to rent.
