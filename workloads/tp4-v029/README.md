# vLLM 0.29 V2 layout qualification

This fixture keeps the Qwen3-32B revision, BF16 precision, TP4 groups and frozen token-ID inputs from the preceding screen. It upgrades **every arm** to pinned vLLM 0.29 images and explicitly selects Model Runner V2 and FLASH_ATTN. It is preparation, not a GPU result.

The predeclared sequence is layer-first default, documented block-first packing, head-first block packing, then a return to the default. The layout names list physical memory order: **L** = layer, **B** = cache block, **H** = KV head, **N** = token slot, **C** = packed key/value contents. Thus the four epochs are `LBHNC → BLHNC → BHLNC → LBHNC`.

Native CPU checks execute the installed version's environment resolver, cache planner and tensor allocator. Both block-first layouts use a 4 MiB block stride for this model/rank. A source-derived model of NIXL registration predicts 64 transfer regions for LBHNC/BLHNC and one whole-block region for BHLNC. That is not live NIXL registration or a GPU speedup claim. Both candidates are retained in the test instead of treating their similar names as equivalent.

Each epoch has 26 requests, including 12 timed 120K P/D requests. The maximum is 104 requests. The same correctness checks, warmups, repeated prompt and producer-only allocation-history controls run in each epoch. If the new default no longer reproduces expensive submission, stop the layout experiment and retain its qualified baseline; do not manufacture a packing benefit.

`v029_worker_probe.ProbedWorker` subclasses the native GPU worker and calls its original cache initialization unchanged. After initialization, it records the actual runner, resolved layout, attention backend, kernel block size, tensor shape/strides, storage size and shared-storage count. It reads metadata only, before measurements. Every receiving rank must still pass actual CUDA-IPC protocol, payload and error checks. The probe must activate on every worker; valid command syntax alone is insufficient.

The suite reuses the earlier public-source corpus and model revision; only its configuration binding is updated. `SOURCE-LICENSE` accompanies the source-derived inputs. Numerical timing results from different vLLM versions must not be pooled.

Source references: [v0.29 layout compatibility](https://github.com/vllm-project/vllm/blob/v0.29.0/docs/features/nixl_connector_compatibility.md), [V2 allocation](https://github.com/vllm-project/vllm/blob/v0.29.0/vllm/v1/worker/gpu/attn_utils.py), [native tensor allocation](https://github.com/vllm-project/vllm/blob/v0.29.0/vllm/v1/worker/utils.py), [NIXL region construction](https://github.com/vllm-project/vllm/blob/v0.29.0/vllm/distributed/kv_transfer/kv_connector/v1/nixl/base_worker.py).
