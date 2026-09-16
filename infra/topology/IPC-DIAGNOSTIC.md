# Short raw CUDA IPC diagnosis

This uses one preemptible eight-H100 node, with no LLM, router, CPU utility node or remote GPU. The existing $50 cumulative authorization and cost history remain in force. Reserve $13 before admitting a run; estimated 20–30-minute cost is $6–9 before tax. Placement timeout is 15 minutes, teardown begins by 20 minutes, and deletion targets 30 minutes from launch. The local guard is not a provider-enforced billing cap.

## Launch

First confirm no active attempt and successful cleanup/cost records for prior attempts. Require a fresh positive eight-H100 capacity sample on an approved eu-north1 fabric. Generate a fresh plan with `ipc_diagnostic_only=true`, `gpu_preemptible=true` and the selected fabric. The plan must contain exactly the Kubernetes cluster, GPU cluster, and one local eight-H100 node: three creates, no other changes.

Use the private approval record `/Users/rayhanmemon/.codex/run-state/router-h100-pilot/approval-ipc-diagnostic-2026-09-16.json`:

```sh
python3 infra/topology/pilot-session.py --execute --profile ipc-diagnostic \
  --approval-record /Users/rayhanmemon/.codex/run-state/router-h100-pilot/approval-ipc-diagnostic-2026-09-16.json \
  --approved-max-usd-pretax 50 --purchase-type preemptible --plan FRESH_PLAN
```

The helper starts the shutdown guard and records the new run directory in `budget.json`. Keep the Mac awake with a process tied to that guard. Save the capacity snapshot and plan JSON into the run. Observe native VM operations; confirmed NotEnoughResources stops the attempt promptly. After the node is ready, fetch a dedicated kubeconfig and set KUBECONFIG for the commands below. Confirm the node's hostname label matches its name and it advertises eight GPUs.

## Run the controls

```sh
python3 infra/topology/run-ipc-diagnostic.py --context router-topology \
  --node ACTUAL_NODE --run-dir RUN_DIR --execute
```

Omit `--execute` to render only; that makes no Kubernetes calls. The pinned GPU image supplies Python/CUDA without loading model weights. The control pod requests all eight devices so it can retest the exact physical GPU pair; only two are used by the tests. This does not increase the rented capacity or enable privileged mode.

Each fresh producer/consumer pair uses a 2 MiB raw CUDA allocation and verifies every byte. The sequence is same GPU in one container, different GPUs in one container, then separate one-GPU pods. If the split-pod case fails, the runner recreates the control pod, retests those exact GPU UUIDs with all devices accessible, and compares process-only CUDA_VISIBLE_DEVICES masking. Host IPC/PID and host /dev/shm settings match the prior deployment. Logs record loaded driver libraries, kernel driver, device nodes, GPU UUIDs, namespaces and exact CUDA errors.

Cases stop adaptively after an earlier failure. The runner preserves time for shutdown and does not extend the guard deadline. A failed case is diagnostic evidence, not an infrastructure failure to discard. It does not prove UCX/vLLM will choose a particular transport. If raw CUDA succeeds, review whether time remains for the existing small PyTorch/NIXL follow-up; do not start full model serving in this profile.

## Close

Save the results and Pod records, then run `python3 infra/topology/pilot-session.py cleanup RUN_DIR --execute`. If observed system PodDisruptionBudgets block full-cluster deletion, preserve and remove only those blockers in this experiment cluster. Verify zero instances, clusters, disks, filesystems and GPU clusters. Write the run's cost-estimate.json with matching session_id and per-resource operation lifetimes, then reconcile it into the existing budget ledger. Never reset history. Update the result, tracker and spend record before another attempt.
