# Default slowdown reproduced; packed comparison stopped at runner compatibility

**The one-host test reproduced the expensive default transfer state with identical repeated inputs. The packed-layout comparison did not run.** vLLM selected Model Runner V2; the single-group cross-layer path reviewed before rental belongs to the older runner. The activation check stopped the packed phase before any request, and the return-to-default phase was not started.

All paid resources were independently verified deleted at **2026-09-22 02:05:24 UTC**. This run cost an estimated **$10.06 before tax**, bringing router-project cloud spend to **$192.54** and leaving **$32.88** of the existing session authorization. The earlier permission-only attempt cost $0 estimated compute. No retry is running.

## What was measured

The default phase completed 26 requests: eight direct/P-D qualification calls, two short warmups, eight identical consecutive 120K P/D requests, and four producer-only/P-D pairs. There were **12 timed long P/D observations**. All four qualification pairs matched; source-location gold was correct for three of four cases, or six of eight qualification calls. Actual local CUDA-IPC transfer was qualified on all receiving ranks, with unchanged error counters and expected payload bytes.

For the same 30 GiB total cache payload (7.5 GiB per GPU rank):

| Descriptors per rank | Timed requests | Mean posting per rank | Mean total transfer per rank |
|---|---:|---:|---:|
| 64 | 4 | 0.70 ms | 22.82 ms |
| 122,880 | 8 | 414.28 ms | 467.44 ms |

The eight consecutive copies of the **same request** alternated exactly between 64 and 122,880 descriptors. Different prompt content is therefore not necessary for the slowdown. Block reuse order remains a plausible explanation; block-ID sequences were not directly recorded, so physical fragmentation or a particular allocator mechanism is not established.

The whole fixed set of twelve timings had mean TTFT **11,376.81 ms**, mean posting **276.42 ms/rank**, and mean total transfer **319.23 ms/rank**. These are isolated local-route measurements, not a local/remote or routing-policy comparison. Transfer includes posting, and rank averages are not the TP group's critical-path timing. No observations were discarded based on performance.

## Why the proposed packed phase stopped

The flag was present and accepted by the native vLLM parser on both engines, but startup logs explicitly say **Using V2 Model Runner**. No expected cross-layer allocation log appeared. The client rejected activation before sending packed requests; `packed-failure.log` and the packed startup logs preserve the failure.

Pinned vLLM 0.26 source explains the missing path:

- The older runner invokes the uniform cross-layer allocator when the connector requests it. [Runner mixin](https://github.com/vllm-project/vllm/blob/v0.26.0/vllm/v1/worker/kv_connector_model_runner_mixin.py).
- V2 calls its own cache allocator and constructs its connector afterward. [V2 initialization](https://github.com/vllm-project/vllm/blob/v0.26.0/vllm/v1/worker/gpu/model_runner.py), [V2 allocation](https://github.com/vllm-project/vllm/blob/v0.26.0/vllm/v1/worker/gpu/attn_utils.py).
- The separate packed cache planner enables this flag for **multiple cache groups**, or its special DeepSeek-V4 case. It does not enable it for this single-group Qwen3 configuration. [Planner conditions](https://github.com/vllm-project/vllm/blob/v0.26.0/vllm/v1/core/kv_cache_utils.py).

The three captured Python-module hashes match the v0.26.0 tag. Installed package metadata includes `nixl` 1.3.1, `nixl-cu12` 1.3.1 and `nixl-cu13` 1.3.2; do not label the entire installed NIXL stack simply 1.3.1. UCX logs identify 1.21.0.

This was an avoidable source-review omission: attention-backend compatibility and valid syntax did not establish that the selected model runner invoked the feature. The earlier three-agent review did not trace that selection. The activation check prevented an invalid performance claim, but the review should have caught the mismatch before rental.

## Next decision

Prefer an offline review of **vLLM 0.29's V2-compatible layout selection**, followed by a fresh matched comparison on that version if prepared and budgeted. Its NIXL compatibility document specifies `VLLM_KV_CACHE_LAYOUT=BLHNC` for cross-layer contiguity, and the cache planner now creates explicit layout strides. [Versioned compatibility guide](https://github.com/vllm-project/vllm/blob/v0.29.0/docs/features/nixl_connector_compatibility.md), [versioned planner](https://github.com/vllm-project/vllm/blob/v0.29.0/vllm/v1/core/kv_cache_utils.py).

An alternative is the older runner on v0.26 for **all** comparison phases, but that would establish an older-runner result. Do not compare old-runner packed timings directly with this V2 baseline and attribute the difference entirely to packing. v0.29's release notes deprecate the older runner, strengthening the case for reviewing the supported V2 path. [Release notes](https://github.com/vllm-project/vllm/releases/tag/v0.29.0).

No newer image, older-runner experiment, packed performance improvement or router gain has been demonstrated. Do not rerun the unchanged 0.26 launcher expecting this single-group flag to activate under V2.

## Evidence

The client and local archives include every default request, per-request metric deltas, rank identities, transport logs, both startup configurations, installed package versions and the packed activation failure. `summary.json` is independently derived by `infra/topology/summarize-layout.py`. Native VM operations and conservative full-session disk accounting support `cost-estimate.json`; `cleanup-verified.json` records the independent deletion check. Source used for the run: local commit `531cd97`; no public push or upstream PR change occurred.
