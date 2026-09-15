# H100 controlled transfer check

**Prepared, not rented.** The closed L40S attempt and its approximately $0.60 estimated cost are recorded in `results/2026-09-15-topology-transfer/RESULT.md`. This procedure uses the published draft PR and requires a separately approved H100 session. Rayhan requested unattended execution with a review afterwards; the proposed $50 pre-tax limit is still pending. The capacity monitor is saved paused.

## What this check answers

Does transferring KV to a decoder on the prefiller's H100 host cost less than transferring it to a decoder on another VM, and is the intended local CUDA IPC payload path actually used? This check does not compare routing policies or establish the allowance's performance value.

Placement: one prefill GPU plus one local decode GPU on an eight-GPU H100 VM; one remote decode GPU on a one-GPU H100 VM; one CPU utility node. Three GPUs work and nine are billed. The GPU-cluster resource selects an available fabric (2, 3, 4 or 6) for the eight-GPU VM only; the fresh Terraform plan must name the selected fabric. The remote VM is outside it and uses ordinary networking. Record actual placement and transport; separate VM identities do not establish separate physical chassis.

Use `router-none.values.yaml` for this diagnostic. The same prefill-first handler, role/capacity filters and session pin serve both forced routes. The topology/load policies are staged for later comparison, but do not select the diagnostic destination.

## Price and proposed limits

Nine preemptible H100s: $19.35/hour including their associated CPU/RAM. Utility node: $0.3968/hour. 576 GiB Network SSD: $0.05602/hour. **Total about $19.80/hour before tax; two fully running hours about $39.61.** Proposed allowance: **$50 before tax**, teardown starting by minute 90 from provisioning, deletion targeted by minute 120. Approval is still required; the previous $25 authorization covered the closed L40S attempt.

One placement attempt. Stop if the topology has not placed within 30 minutes, an engine has not become ready within 30 minutes on ready nodes, or an instance is preempted. Begin cleanup at the time limit even if diagnostics are incomplete. Preserve failed results. No on-demand substitution or larger rental without a new decision. Pricing sources: [compute/storage](https://docs.nebius.com/compute/resources/pricing), [other services](https://nebius.com/prices). Refresh live capacity immediately before the approved attempt.

## Exact software and prepared artifacts

- Source: `0217d29924ba93b90f952e7a0281dd8dda146703` from draft PR #2870; same tested source tree as implementation commit `8f3f2838`.
- EPP image: `ghcr.io/llm-d/llm-d-router-endpoint-picker:topology-0217d299-amd64`; local image ID `sha256:d8dfea7c43d1683a3f47abb74483f5c2d2d3cd2ccb9322c0fd8df493ba52f8f8`.
- Durable archive: `/Users/rayhanmemon/.codex/run-state/router-h100-pilot/prepared/topology-epp-0217d299-amd64.tar`; SHA-256 `7ab220d2eb3a56d62ffb5595f3981944256c4072be7e88913be4bb3e182dd0d5`.
- Prepared fabric-2 plan: `/Users/rayhanmemon/.codex/run-state/router-h100-pilot/prepared/fabric2.tfplan`, five creates only. Refresh capacity and plan before a delayed launch; replan whenever inputs change.
- Qwen3-8B BF16 revision, vLLM 0.26, sidecar 0.10, inference-perf 0.6.1 and Envoy image digests are pinned in the renderer/workload scripts.

`render.py` defaults to host IPC/PID namespaces and mounts the host's `/dev/shm` rather than shadowing it with a private mount. GPU resource allocation remains one per engine, CPU limit 6 per engine, with no privileged mode. This is a deliberate transfer-qualification setting on dedicated experiment VMs. `--ipc-mode isolated` renders the original namespace arrangement for diagnosis. Do not silently change namespaces or device access between local/remote arms. If the staged host mode cannot reach the peer GPU, stop for a focused configuration review rather than broadening privileges on the meter. CUDA IPC flags alone do not prove reachability or payload selection.

## Unattended launch procedure

The saved monitor is paused until Rayhan approves the H100 budget. After that approval, write a private JSON authorization record with `approved: true`, the actual approval reference, `approved_max_usd_pretax`, and `purchase_type`. Do not copy approval from the old L40S attempt. Verify the five-create plan and purchase type against the approval before invoking `pilot-session.py` with `--execute`, `--approval-record`, `--approved-max-usd-pretax`, `--purchase-type`, and `--plan`.

The helper claims one persistent attempt, records its start/deadlines and plan hash, starts a detached shutdown guard, then applies the reviewed plan. A failed or timed-out apply starts cleanup. After a successful measured block, invoke `python infra/topology/pilot-session.py cleanup RUN_DIR --execute` so normal completion and the deadline guard use the same lock and verified-cleanup marker. Never delete the attempt record to retry a rental. Cleanup continues deletion-only retries if verification fails; an overdue marker requires immediate attention. This local guard depends on the Mac remaining awake and connected; it is not a provider-side budget cap.

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

At **20:13 UTC on September 15**, the advisor reports six preemptible eight-GPU H100 allocations on fabric-2 (sample effective 19:38:47 UTC), plus positive one-GPU H100 capacity (sample effective 19:50:12 UTC). Other H100 eight-GPU fabrics do not report positive capacity. Both samples are marked fresh. This is advice, not a reservation; recheck exact platform `gpu-h100-sxm` and both shapes immediately before launch. A matching preset name on H200 is not eligible for this session. The refreshed fabric-2 plan validates as five creates only; no resources were created.
