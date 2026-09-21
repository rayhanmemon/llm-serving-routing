# TP4 launch preflight completed locally

The pinned Qwen3-32B TP4 workflow passes local preparation checks. **No paid GPU resources or real-model requests were created by this preflight.** Actual GPU execution and all-rank transport qualification remain runtime checks.

- **208 tests pass**, including staged-allocation ordering, rejection of remote allocation without local qualification, and extraction of node IDs directly from partially applied Terraform state. Targeted Terraform applies can leave combined outputs incomplete; allocation does not depend on those outputs.
- The complete client executed **144 synthetic HTTP/SSE calls**: 16 local qualification, 16 remote qualification, 16 warmups and 96 timings in 48 matched pairs. Simulated responses/counters/transport logs exercise the real client but are not performance evidence.
- Exact engine, client and cleanup-guard manifests passed server validation on an isolated local Kind API. Compressed request data and source/configuration round-tripped unchanged. The temporary cluster was deleted; the existing user cluster was untouched.
- The cloud plan was inspected read-only: CPU node, two eight-H200 preemptible (spot) nodes on one fabric, and the restricted cleanup identity. No Terraform apply was run.

## Frozen workload and scope

[The request suite](../../workloads/tp4-locality/code-suite/manifest.json) uses real Go source from llm-d-router revision `32d4ed2ac5ff1cc09f1dc8326caa0bab56d234b4`. It does not copy the user's uncommitted checkout. Two source-location questions at each of 4,096, 32,768, 65,536 and 122,880 fully rendered input tokens give eight cases. The source corpus is large enough without repeating filler. Its license is included with the suite.

Qualification compares direct and P/D output after outer-whitespace normalization; source-location accuracy is reported separately. Qualification permits normal early EOS within 32 output tokens. Timings use a fixed 32-token output budget with EOS ignored, explicitly a controlled TTFT screen rather than representative output-length traffic. Each length receives twelve pairs across three blocks, with balanced randomized route order in every block. Warmups are recorded separately. No EPP policy comparison, load-allowance calibration or live concurrency sweep is part of this screen.

The staged controller first arms independent local and cloud cleanup guards, allocates only the local GPU host, deploys TP4 prefill/decode and collects local qualification. Only a successful saved local result permits remote allocation. Raw results are copied off-box between stages and at completion. The session starts cleanup at 120 minutes and targets deletion by 145 minutes. At the conservative full two-host rate of $39.70/hour, even the entire 145 minutes would estimate below the authorized $100; local-only staging reduces actual exposure. This is not a provider-enforced billing limit.

## Launch dependencies

The existing automation identity cannot create the guard's IAM group with its ordinary editor access. Temporary admin access, limited to the experiment project, is staged in the signed-in browser and requires action-time confirmation. It is not granted by this preflight. After that confirmation, recheck the exact source hashes, capacity/account status and absence of paid resources before admission. Revoke temporary admin access after verified cleanup.

The evidence files contain source/configuration digests and local API/HTTP validation results. The rendered node names, cluster ID and deadline are placeholders, not deployed resources.
