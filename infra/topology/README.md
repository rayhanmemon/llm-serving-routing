# Three-worker topology measurement

**Status: first placement attempt blocked by two-GPU capacity; no inference measured.** See [the first-session procedure and quote](SESSION.md). One prefill GPU and one decoder share a node; another decoder occupies a second node. A CPU node runs the standalone Envoy/EPP and benchmark client. One EPP accounts for all traffic.

The model is Qwen3-8B at revision `b968826d9c46dd6066d109eabc6255188de91218`, BF16, one GPU per worker. Engine v0.26.0, routing sidecar v0.10.0 and inference-perf v0.6.1 are pinned to AMD64 image digests. The EPP is built from reviewed source commit `c1e44596c67aaff3a949e78fda1be57df217ffb2`; its local AMD64 image has passed startup checks. These checks do not establish correct inference or fast transfer.

## Render without creating resources

Python requires PyYAML. Use the existing inference-perf environment for benchmark schema validation. `render.py` creates a new output directory and refuses to overwrite one.

```sh
python infra/topology/render.py \
  --local-node ACTUAL_LOCAL_NODE --remote-node ACTUAL_REMOTE_NODE \
  --cpu-node ACTUAL_CPU_NODE --out infra/topology/rendered
```

Use actual Kubernetes hostname-label values. Both the node selector and the pod's topology label receive that value; confirm they agree with actual placement. Separate Kubernetes node names do not prove different physical hosts. Record provider/node/GPU identity before calling the remote path physically remote.

The output contains model-worker manifests, four policy values files and short/long diagnostic benchmark configurations. Render the router using the standalone chart from the same EPP source commit:

```sh
helm template topology /PATH/TO/PINNED/llm-d-router-standalone \
  -n topology-measurement \
  -f infra/topology/rendered/router-allowance.values.yaml \
  | python infra/topology/record-routes.py > infra/topology/rendered/router.yaml
```

Build the chart's local `routerlib` dependency first in a copied chart directory. `record-routes.py` adds a structured Envoy access log containing requested/selected decoder tokens, upstream host, status and duration. It fails if the expected log configuration is absent. Its duration is proxy-side evidence, not the primary client timing.

## Traffic and routing controls

Decode order: role filter, capacity filter, diagnostic pin, policy filter if any, scoring and picker. Normal measured requests omit `x-benchmark-decoder`; a diagnostic or background request carries the base64 encoding of `namespace/pod-name`. The existing session-affinity filter pins that request while the ordinary EPP request lifecycle counts it. A response echoes the selected decoder.

The pin fails open when the pod is absent or excluded by an earlier filter. Compare requested and selected decoder in the access logs; invalidate forced-worker runs with mismatches, missing routing evidence or replaced workers. Do not silently classify them as local measurements. A replacement changes the pod name and requires a new pin.

Generate a workload Pod plus ConfigMap, without applying it:

```sh
python infra/topology/workload.py infra/topology/rendered/benchmark-512.yaml \
  --name local-diagnostic --cpu-node ACTUAL_CPU_NODE \
  --decoder-pod ACTUAL_LOCAL_DECODER_POD > infra/topology/rendered/local-workload.yaml
```

Omit `--decoder-pod` for policy-evaluated traffic. Tokenizer files are fetched from the exact model revision into a shared volume; the client reads that path because this inference-perf version has no tokenizer revision parameter. The Pod records the harness exit code and retains reports for ten minutes after completion; copy `/reports` and all logs before deleting it. Its 20-minute active deadline is a diagnostic bound, not a final benchmark duration. A deadline expiry or missing reports is a failed run.

The initial examples use 512/8192 requested input tokens, 128 output tokens, one load-generator process, a fixed seed and 12 requests sent sequentially (concurrency one). This isolates low-load latency; use fixed arrival rates in later policy comparisons. These are uncalibrated diagnostic settings. Verify actual token counts and actual arrival times. The worker count/seed combination helps reproducibility but does not guarantee identical achieved traffic.

Prefix caching is disabled for the transfer diagnostic, identically across all policies. With one prefiller there is no prefill-placement choice; its token-load scorer retains the appropriate scheduling input but does not demonstrate cache-aware placement. Restore and control cache reuse in later evaluation. The default EPP tokenizer estimates prompt size; exact prompt-token input must be configured before testing prompt-size allowances. The current filter uses request counts only.

The local-transfer candidate enables `UCX_CUDA_IPC_ENABLE_GET_ZCOPY=on` on every worker, with `UCX_PROTO_INFO=y` for evidence. Inspect actual GPU peer access and selected operations. Do not infer fast CUDA IPC from the environment variable or from sharing a node. Transport setup is identical across policy arms; any gain from fixing transport is not attributed to the routing change.

## Infrastructure and image delivery

`terraform/` is standalone and uses a distinct `router-topology` cluster and state. Provider 0.6.46 is locked. It requests a two-GPU AMD L40S node, a one-GPU AMD L40S node and a 16-vCPU utility node in the supplied eu-north1 project/subnet. GPU nodes are preemptible. Kubernetes 1.35 / cuda13.0 comes from the earlier tested recipe; the two-GPU AMD combination remains unqualified. No apply has run.

The EPP image is local-only. Generated values use `pullPolicy: Never` so deployment cannot accidentally fetch another image. `import-image.py` verifies the local archive and prepares a temporary privileged node helper; `--execute` imports the reviewed image directly into that CPU node's containerd and removes the helper. The manifest/archive checks passed locally; actual node-runtime compatibility remains to be checked during deployment.

Before rental: agree the priced session limit in SESSION.md. Before requests: record the expected outcomes. The dedicated teardown script uses the repaired independent API checker. Node startup, AMD driver/peer access, complete P/D serving, runtime image compatibility, exact benchmark image behavior and report collection still require validation. A successful Terraform validation or EPP health probe does not satisfy them.

## Local verification, September 15

- Terraform initialized and `terraform validate` passed; no resource plan/apply executed.
- Router chart rendered and the embedded plugin configuration/order was inspected.
- Four policies started in the actual AMD64 EPP image using placeholder file discovery; gRPC health and resolved filter order passed. No inference requests were issued.
- Both benchmark configurations passed the installed inference-perf 0.6.1 schema.
- Image manifest inspection confirmed the pinned engine, sidecar and benchmark digests target AMD64.

The complete six-policy evaluation, baseline tuning, held-out traffic and repeated comparisons remain future work. Global allowance 2 and soft topology weight 0.5 are examples, not recommended settings or measured winners.

Image delivery is private to the selected experiment node. `collect.py` saves observations and completed reports with checksums; `test_collection.py` exercises five collection outcomes without a cluster. Benchmark CLI/configuration checks also passed in the pinned image. Terraform plan contains four creates only; no apply has run.

September 15 runtime check: EPP/Envoy ran on Nebius after successful private image import. The local two-GPU VM returned NotEnoughResources and no engine request was sent. The corrected engine manifests passed server-side dry-run; use the GPU engine image for GPU-tool inspection because the benchmark image lacks nvidia-smi. Full evidence and cleanup status are in the dated result directory.
