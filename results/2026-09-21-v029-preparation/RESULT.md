# v0.29 preparation and real-router functional rehearsal

**Local preparation passed; no GPU performance result is represented here.** The native vLLM 0.29 parser, environment-based layout resolver and CPU tensor allocator passed. Read-only worker-probe extraction passed with GPU initialization substituted in its CPU check. All **226 utility tests** passed. The four-epoch client completed **104 synthetic HTTP/SSE requests**, and the actual restart wrapper completed all four epochs with substitute child processes. Exact GPU/client/guard manifests and mounted source/request data passed the local Kubernetes API checks.

The published router source at `0217d29924ba93b90f952e7a0281dd8dda146703` was built for the local host and deployed with the real llm-d P/D sidecar. **Forty concurrent streaming requests** passed across hard locality, unrestricted load scoring, weighted locality/load scoring, absolute load cap plus locality, and the proposed allowance filter. Scorer logs observed nonzero in-flight counts; hard locality chose the local decoder for every request. All streams completed. The workers were synthetic HTTP servers: this verifies routing, long token-ID bodies, request accounting and sidecar/proxy compatibility, not model correctness, KV transfer or performance.

## Endpoint wiring for the shared-container fixture

The qualified GPU fixture places prefill and local decode processes in one Pod, on ports 8100 and 8200, with the decode sidecar on 8000. The EPP needs a distinct endpoint for each role. A small Envoy prefill proxy, pinned to the same physical node, forwards port8000 to the actual prefiller's8100. Its role and host labels describe that backing engine. Local and remote decoder Pods expose their existing sidecars on8000.

This adds one prefill HTTP hop, consistently across every routing policy. It does not carry KV data. Record this controlled deployment choice in any final benchmark; it is not evidence that every separate-GPU-Pod arrangement has been qualified.

The first synthetic request exposed missing chunked-body handling in the test server. That fixture was corrected before the successful rehearsal; real vLLM was not changed. Policy weights, cap and allowance used here are illustrative and remain uncalibrated.

See the [workload configuration](../../workloads/tp4-v029/README.md) for the matched layout matrix and its limits. GPU qualification and a fresh local/remote comparison remain required before evaluating policy performance. No new PR, public post or measured gain follows from this preparation.
