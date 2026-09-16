# Deployment reference audit — September 16, 2026

The renderer is a small routing experiment built from llm-d's deployment pattern. It is not a claim that the exact Nebius RTX topology is an upstream-tested performance configuration.

## Sources and the main correction

Reviewed current llm-d main at [`baaaef6fc954ea7f7c396976163da37d526e23ca`](https://github.com/llm-d/llm-d/tree/baaaef6fc954ea7f7c396976163da37d526e23ca), including the [P/D guide](https://github.com/llm-d/llm-d/blob/baaaef6fc954ea7f7c396976163da37d526e23ca/guides/pd-disaggregation/README.md), its NVIDIA model-server recipes, the [networking guide](https://github.com/llm-d/llm-d/blob/baaaef6fc954ea7f7c396976163da37d526e23ca/docs/infrastructure/rdma/README.md), and [vLLM operations](https://github.com/llm-d/llm-d/blob/baaaef6fc954ea7f7c396976163da37d526e23ca/docs/operations/disaggregation/vllm.md). The local llm-d checkout was older, so the changed reference files were fetched by this immutable commit before comparison.

**Separate Kubernetes GPU allocations do not imply CUDA IPC access.** The networking guide explicitly warns that same-node and cross-node transfers can both use RDMA, and that `hostIPC` does not grant access to peer GPUs. Its linked [NVIDIA guidance](https://docs.nvidia.com/dynamo/dev/kubernetes/installation/rdma-setup/overview) recommends RDMA for standard separate-pod deployments, including colocated workers. This is a deployment constraint, not proof of the exact reason UCX skipped IPC in our earlier run.

The RTX experiment had no InfiniBand fabric and used a restrictive transport allowlist. Its measured TCP behavior is valid evidence about that configuration, not a stock fast-transfer baseline. Adding ordinary shared-memory transport alone would not establish GPU-direct access. CUDA IPC is not a prerequisite for the router contribution: the requirement is a useful measured locality/load tradeoff under a representative, verified transport.

## Configuration comparison

| Area | Reference and disposition |
|---|---|
| Engine and sidecar | The release components select vLLM 0.26.0 and routing sidecar 0.10.0. Our versions match and retain immutable image digests. No dependency upgrade is needed. |
| P/D mechanism | Keep separate prefill/decode Deployments, role labels, the restartable decode routing sidecar, `NixlConnector`, `kv_both`, and the Pod-IP metadata side channel. These match the recipes. |
| GPU memory/backend | Render `kv_buffer_device: cuda` and `backends: [UCX]` explicitly, matching the current NVIDIA base patches. Preserve `kv_load_failure_policy: fail`, which the operations guide recommends. |
| UCX selection | Omit `UCX_TLS` by default, as the generic NVIDIA base does. UCX discovers available transports. `--ucx-tls` makes restrictions an explicit diagnostic override; there is no silent TCP fallback patch after a startup error. |
| CUDA IPC tuning | Omit `UCX_CUDA_IPC_ENABLE_GET_ZCOPY` by default. It remains available through `--cuda-ipc-get-zcopy` for a declared diagnostic. Neither enabling it nor listing `cuda_ipc` proves the payload selected that lane. |
| Namespaces/shared memory | Default to private Pod IPC/PID namespaces and memory-backed `/dev/shm`, matching the generic base pattern. Keep the smaller 8 GiB shared-memory limit for this smaller model. `--ipc-mode host` is an explicit diagnostic variant, not a GPU-access fix or standard requirement. |
| Sidecar permissions | Match the reference's non-root sidecar with privilege escalation disabled. The pinned sidecar source declares user 65532. No broad model-server privilege is added. |
| Logging | Suppress routine model health/metrics access logs, as the recipe does. Keep UCX protocol/info logging for transport qualification; its output must identify the large GPU-memory operation's selected lane. |
| Experimental model/size | Qwen3-8B, TP=1 on every worker, block size 64, one prefiller and two decoders are intentional bounded experiment choices. The main guide's gpt-oss-120b, TP ratios, replicas and block size are model/workload choices, not universal requirements. |
| Attention backend | The explicit RTX `TRITON_ATTN` override is a documented compatibility adaptation. It remains identical across workers and requires fresh correctness checks. |
| Routing | Prefill-first ordering, disabled prefix caching and diagnostic endpoint pins isolate this contribution. They are intentional differences from the general guide. Pins are absent from evaluated policies. |
| Gateway | The existing upstream `llm-d-router-standalone` chart supplies Envoy/EPP for this experiment. It is a development fixture, not the full Gateway API deployment from the production guide. |

The current P/D overlay tree has AWS, CoreWeave, GKE and generic NVIDIA configurations; it has no Nebius-specific or RTX PRO 6000 Blackwell P/D overlay. The repository's DigitalOcean RTX Ada files concern different hardware and do not qualify this fixture.

## Provider requirements before another performance run

1. Select and price hardware with the intended transport. For the standard isolated-pod fast path, use an RDMA-capable deployment and confirm actual devices inside the participating Pods. A provider-correct RDMA baseline is the next planning step; the earlier standalone RTX IPC probe remains optional diagnosis, not the default next rental.
2. Follow [Nebius's managed GPU setup](https://docs.nebius.com/kubernetes/gpu/set-up). Our managed `drivers_preset=cuda13.0` is a supported automatic driver/device-plugin route. Do not install a second operator blindly. For fabric-equipped node groups, verify the provider's network/RDMA setup and exposed resources; do not copy GKE DRANet or another provider's resource names.
3. For RDMA, check `ibv_devinfo`, device grants, memory-lock limits and the capabilities required by that provider. `IPC_LOCK` appears in llm-d's RDMA examples. Resolve registration failures at that layer; merely excluding RDMA changes the experiment.
4. Run a byte-checked NIXL **READ** probe in the same image and Pod access configuration, before filling GPU memory with the model. The guide's `nixlbench --op_type=READ --check-consistency` pattern tests the connector operation. NCCL results and raw CUDA IPC copies answer different questions.
5. Send real P/D requests and retain selected payload-protocol evidence, token counts, routes, failure counters and worker/GPU identity. Seeing a TCP metadata connection or an available CUDA-IPC lane is insufficient to identify the cache payload path.
6. Freeze the deployment across routing policies. Requalify any image, namespace, transport or device-access change; do not merge the earlier restricted-TCP measurements into a corrected baseline.

These changes were rendered and tested locally. They do not establish runtime RDMA/IPC performance, create cloud resources, or change the preserved September 16 results.
