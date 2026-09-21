# Qwen3-32B TP4 locality screening

The selected model is pinned in `config.json`; `model-config.json` preserves its architecture and is digest-checked. All three serving instances use TP4, BF16 weights/cache, the same YaRN extension, chunked prefill and default graph execution. Node A holds separate four-GPU prefill and decode groups; node B holds one four-GPU decode group.

**Preparation only.** The renderer does not create resources or validate GPU execution. Request content, rank-aware transfer qualification, client rehearsal and staged cloud admission must be completed before a run. Existing TP1 validators must not certify this deployment. In particular, aggregate NVLink traffic can include tensor-parallel collectives.

Input lengths are 4,096, 32,768, 65,536 and 122,880 tokens, counted after rendering the complete input. Screening proposes 32 output tokens to measure TTFT with a bounded decode tail; this is not the output distribution for the later routing-policy benchmark. Prefix caching is disabled for the controlled comparison. Concurrency candidates include the foreground request and must fit measured runtime capacity.

Render without a cluster or cloud account:

```sh
python3 infra/topology/tp4-config.py \
  --local-node LOCAL_NODE --remote-node REMOTE_NODE \
  --out /tmp/router-tp4-preview
python3 -m unittest discover -s infra/topology -p test_tp4_config.py
```

Omit `--remote-node` for the first, local-only qualification manifest. A subsequent staged launcher must verify actual local TP4 transfers before provisioning a remote GPU node. A Pod lifetime is not a substitute for deleting the rented nodes.

The memory table includes generated tokens and cache-block rounding, but excludes runtime allocations. Model weights are approximately evenly sharded; replicated tensors and other buffers must be accounted for by startup measurements. The pinned model supports the selected longest input with its documented YaRN configuration and output headroom.

Sources: [pinned model architecture](https://huggingface.co/Qwen/Qwen3-32B/blob/9216db5781bf21249d130ec9da846c4624c16137/config.json), [long-context setup](https://huggingface.co/Qwen/Qwen3-32B), [NIXL deployment](https://docs.vllm.ai/en/v0.26.0/features/nixl_connector_usage/).
