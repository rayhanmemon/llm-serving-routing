# First GPU session

Prepared September 15, 2026. No infrastructure or model requests have run. Rental authorization and the operator's expected outcomes must be recorded before the relevant actions.

## Cost and stopping conditions

Three nodes: a two-GPU AMD L40S node, a one-GPU AMD L40S node, and a 16-vCPU/64-GiB utility node. GPU nodes are preemptible. Compute is $3.5148/hour. Two 256-GiB GPU boot disks plus one 64-GiB utility disk total 576 GiB; at $0.071/GiB per 730 hours this adds $0.05602/hour. Total is approximately **$3.57/hour before tax**, or **$7.14 for two hours**.

Kubernetes management, network traffic and public IPs are listed as free. No object storage, registry, load balancer or filesystem is requested. Sources checked September 15: [compute/storage pricing](https://docs.nebius.com/compute/resources/pricing), [Kubernetes pricing](https://docs.nebius.com/kubernetes/resources/pricing), [networking and service prices](https://nebius.com/prices).

**Proposed session authorization: up to $15 before tax, with teardown starting no later than 90 minutes after provisioning begins and a target of all billable resources removed by two hours.** The extra allowance covers delayed cleanup; it is not permission to extend measurement. This is an operational spending limit, not a provider-enforced billing cap.

One placement attempt. If the full GPU topology has not placed after 30 minutes, tear down. If engines have not become ready after 30 minutes on ready nodes, stop troubleshooting on the meter and tear down. A preemption ends the affected experiment; no unapproved on-demand substitution or larger instance. Save failure evidence and delete the session's resources. Earlier termination is appropriate if transfer qualification fails decisively.

## Prepared evidence

- Terraform plan: four creates only (cluster, CPU group, local GPU group, remote GPU group). No update or deletion of existing resources. Local plan `/tmp/topology-2026-09-15.tfplan`; replan if configuration or credentials change.
- EPP source `c1e44596c67aaff3a949e78fda1be57df217ffb2`; local AMD64 image built and startup-tested for four routing policies.
- Archive `/tmp/topology-epp-c1e44596-amd64.tar`, 22.3 MiB, SHA-256 `52a9b1771d53ea384efeccc0d2bb52ff3de2c58cd852f8a38ea2d68ba5b6608c`. This file is a local deployment artifact, not a public registry image.
- Pinned inference-perf image: actual CLI, configuration/import checks, and exact-revision tokenizer download/load passed locally without serving requests. Report collection has five mocked success/failure cases, including failed metrics, failed harness, and missing files.
- Image helper manifest and archive validated locally. Host containerd paths, Kubernetes admission and real report/metrics access remain runtime checks; they cannot be established from local schema validation.

## Execution sequence

1. Record the actual approval and start time. Recheck capacity, confirm the dedicated Terraform state has no unrelated resources, and apply the reviewed plan. Do not switch GPU presets on a placement failure.
2. Write credentials to a dedicated kubeconfig with context `router-topology`; every operation names that context. `nebius mk8s cluster get-credentials` supports `--kubeconfig`, `--context-name` and `--external`. Identify local/remote/CPU nodes from the Terraform node-group IDs and live metadata.
3. Record node/provider identity, GPU UUIDs, driver/device visibility and GPU topology. Check actual local GPU peer access and evidence that the remote worker occupies the intended separate host/VM boundary. Do not silently call separate VMs separate physical servers.
4. Render manifests with actual hostname-label values. Create the experiment namespace, then import the EPP image onto the CPU node using `import-image.py --execute`. The temporary privileged helper mounts that node's filesystem to invoke its containerd client. It imports only the checksum-verified image, verifies the image name, and deletes its own pod. No registry upload. Stop if the expected containerd path is absent; do not alter the node's runtime configuration.
5. Start the model workers and the rendered standalone router. EPP image pull policy is Never. Inspect actual images, readiness, endpoint discovery, topology attributes and resolved profile order. Envoy must fail closed if EPP is unavailable. Preserve diagnostics before any failure teardown.
6. Before any inference request, record the operator's directional prediction and mechanism in a committed experiment record. Send a correctness probe through each pinned route; confirm the returned decoder, output, P/D execution and actual NIXL transfer. This is not a benchmark result.
7. Run a small agreed batch of sequential forced-local/remote requests for 512/8192-token prompts, with 128 output tokens, matching software/settings. Do not overlap the local and remote diagnostic arms. Warm each path separately, collect a fresh before snapshot, run the arm, and collect an after snapshot. Alternate local/remote order across repeats and record seeds/order. Final batch size/order is agreed before running. This calibration data does not tune held-out evaluation after the fact.
8. Use `collect.py` before/after the arm. After completion, include `--benchmark-pod` to save per-request reports, exit code and checksums before the ten-minute report-retention period ends. Verify expected request count, errors, actual tokens, requested/selected decoder matches, unchanged worker identities and transfer histogram deltas. Collection success alone does not establish experiment validity. Review results together before introducing congestion or prompt-size overrides.
9. At the agreed stop boundary, save evidence and run `bash infra/topology/teardown.sh --execute`. It destroys this dedicated Terraform state and runs an independent API check even if destroy fails. Investigate any failed listing or remaining resource; never delete unrelated resources merely to make a project-wide listing empty. Record actual duration and cost separately from human attention.

## Useful commands

Dry-run the image helper; add `--execute` only within the authorized session:

```sh
python infra/topology/import-image.py --context router-topology --node ACTUAL_CPU_NODE \
  --archive /tmp/topology-epp-c1e44596-amd64.tar \
  --sha256 52a9b1771d53ea384efeccc0d2bb52ff3de2c58cd852f8a38ea2d68ba5b6608c
```

Capture before the arm, then after the harness finishes:

```sh
python infra/topology/collect.py --context router-topology --out RUN_DIRECTORY/before
python infra/topology/collect.py --context router-topology \
  --benchmark-pod ACTUAL_BENCHMARK_POD --out RUN_DIRECTORY/after
```

Use a new collection directory each time. Missing observations remain recorded failures; do not replace them with zeros. Raw vLLM NIXL metrics of interest include `vllm:nixl_bytes_transferred_{sum,count}`, `vllm:nixl_xfer_time_seconds_{sum,count}` and failure/expiry counters. Verify the pinned engine's actual exposition. Histogram deltas provide aggregate bytes/time, not a per-request trace; client latency also includes prefill, queuing and response work.

## Capacity change before provisioning

The preemptible session was approved at 15:19 UTC. At 15:20 UTC the fresh advisor response no longer contained a positive preemptible `available` value for any AMD L40S preset; the single-GPU on-demand presets reported available 4 / limit 32, LOW. There is still no two-GPU advisor row. No apply or billing began.

An on-demand alternative has been prepared and validated as a separate four-create plan, `/tmp/topology-ondemand-2026-09-15.tfplan`, using `gpu_preemptible=false` and reservation policy FORBID. It keeps the same hardware and session stop conditions. Compute plus disks is **$6.83882/hour before tax**, or **$13.68 for two hours**, within the same $15 operational allowance. Switching the purchase type still needs explicit authorization because the approved procedure excluded automatic on-demand substitution. This alternative is not applied or approved yet.

## Active session

On-demand was explicitly approved at 15:23 UTC with the maximum raised to $25 before tax. Provisioning began at **15:23:49 UTC**. Teardown deadline **16:53:49 UTC**; target deletion **17:23:49 UTC**. A local deadline guard is active. This supersedes the earlier purchase type and $15 limit; the time limits are unchanged. The pre-request expectation is committed in `results/2026-09-15-topology-transfer/EXPECTED.md`.

## First placement result

The two-GPU VM failed to place. Compute operation `computeoperation-e00pn8e3fftjmfdn6b`, finished 15:33:42 UTC, returned `NotEnoughResources` for `2gpu-64vcpu-384gb`: VM schedule timeout, most likely insufficient hardware. The VM was STOPPED and never joined Kubernetes. CPU/remote nodes did start, and the reviewed EPP plus Envoy ran healthy. Apply was interrupted after preserving evidence; the wrapper started scoped teardown. No inference requests or performance measurements occurred.

Runtime preparation fixes: the installed containerd client is `/usr/local/bin/ctr`, so import discovers the installed path and checks the existing socket; image import then succeeded and its helper was deleted. UCX logging value `y` serialized without quotes and was interpreted as a boolean by Kubernetes; it is now `yes` (quoted by the YAML emitter), and all three engine deployments pass server-side dry-run. The CPU benchmark image lacks `nvidia-smi`; use the GPU engine image for hardware inspection in the next attempt.

**Closed 15:47:46 UTC:** all four Terraform resources destroyed; independent lists show zero instances, clusters, disks and filesystems. Deadline guard exited. Approximately $0.60 before tax estimated, not invoiced; no inference requests. See the dated RESULT.md. No active rental remains.
