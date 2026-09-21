# TP4 deployment preparation: local checks passed

The pinned Qwen3-32B TP4 configuration and Kubernetes engine manifests pass local checks. **No GPU was rented or inference executed. This is not a complete launch preflight.**

Model revision `9216db5781bf21249d130ec9da846c4624c16137` and architecture digest are recorded in [the workload configuration](../../workloads/tp4-locality/config.json). Prefill and local decode occupy disjoint four-GPU groups inside the eight-GPU local Pod; the remote Pod requests four GPUs. Each engine has the same BF16 model/cache, fixed YaRN extension, 131,072 total-context limit, eight-sequence limit, chunked prefill and default graph execution. Transport discovery remains unrestricted. Per-process UCX log files separate rank evidence from shared stdout and tensor-parallel collective traffic.

The renderer checks immutable model revision shape, architecture digest, head sharding, device separation, context/output limits and consistent serving settings. Its memory table includes screening output growth and block rounding but excludes runtime overhead. Actual capacity requires GPU verification. Host-RAM limits are 256 GiB for the shared local Pod and 128 GiB for the remote Pod; model cache is shared between the two local engines.

## Verification

- Nine new configuration checks and nine rank-evidence checks passed. Saved real TP1 logs exercise UCX parsing, not TP4 execution. The parser separates UCX threads, ignores host-memory tables and distinguishes tiny-message protocols from bulk KV protocols.
- The full existing utility suite plus these checks passed: **200 tests**, using the benchmark Python virtual environment. An initial system-Python run failed because that interpreter lacked the existing Prometheus/YAML dependencies; no code change was needed for those failures.
- A new isolated local Kind cluster accepted the exact ConfigMap through server-side apply; stored data matched byte-for-byte.
- The real Kubernetes API accepted both GPU Pod manifests using server dry-run. No GPU Pods were created, and no image, model or GPU execution is established by this check.
- The temporary cluster was deleted; the user's existing cluster was untouched.

`preflight.json`, `engines.json`, `engine-commands.json` and `memory.json` preserve these checks and rendered settings. Node names and Pod IPs are placeholders. No cloud credentials are included.

## Still required before rental

Prepare the frozen long-document request suite and timed client; connect the tested rank/transfer validator to live collection and qualify every receiving rank's actual KV READ transport; rehearse the full client against synthetic streaming responses; and implement local-first cloud provisioning with validated guards and budget admission. Existing TP1 transfer checks cannot certify TP4. The prior paired launcher provisions both GPU hosts together and must not be used to claim local-first allocation.

These files perform offline rendering and in-Pod engine execution only. There is no new admitted cloud session or ready TP4 rental controller.
