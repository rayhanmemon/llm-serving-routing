# Combined runner prepared locally — no GPU result

**Step 1 is complete locally. No cloud resources were allocated and cloud spending was $0.** The combined runner retains one selected packed deployment through local qualification, remote qualification, calibration and the actual five-policy comparison. It does not rent separate packing or calibration experiments.

## What passed

- 252 test methods: 249 passed on the Mac and 3 Linux-specific process tests skipped there; those 3 passed in the pinned Linux container.
- Real published router and real P/D sidecar, with synthetic model workers: 46 native inference-perf requests across diagnostic routing and all five policies. Every request joined to its decoder route; nonzero router-owned in-flight load was observed.
- Actual local Kubernetes job launch/poll/Pod-identity checks and deadline-file update/acknowledgement calls. The guard's provider SDK was substituted; no provider deletion occurred in this test.
- Actual cloud-guard program in network-disabled Docker: it acknowledged a shorter deadline and requested simulated GPU deletion before CPU deletion.
- Native vLLM 0.29 argument parsing, layout allocation and worker-probe checks on CPU; exact GPU/client/guard manifests passed local Kubernetes server validation. The reviewed AMD64 EPP archive was checked without importing it into a cloud node.

The main real-router rehearsal used ARM64 EPP in Kind and the exact pinned AMD64 inference-perf image in Docker through local port forwards. Kind's ARM image handling prevented a reliable AMD64 benchmark Pod, so the execution location was explicitly substituted. The real cloud path uses AMD64 CPU nodes. The complete orchestration replay substitutes external phases; it is not a real provision-to-delete trial. Later host-only job/Pod-identity guards were tested separately, including real Kubernetes RPCs; the preflight records this rather than pretending the earlier routing rehearsal executed them.

## Corrections made before spending

- CPU-side benchmark and router image preparation now precedes GPU allocation. The working local model is retained while adding remote.
- A new single-attempt approval is required. The generic full-plan launcher cannot bypass staged admission, and the same approval cannot fund a second attempt.
- The remote admission calculation reserves the remainder of the experiment plus 20 minutes for collection/deletion. Both guards must acknowledge the shorter budget-derived deadline before remote allocation.
- The benchmark uses inference-perf's scheduler, HTTP client and reports with a frozen-token adapter. Every background request traverses the same endpoint picker as the foreground requests.
- The local integration uncovered Envoy's UUID-version rewriting, asynchronous access-log delivery, and rotation of verbose per-chunk logs. The runner uses deterministic valid UUIDv4 identifiers, waits for complete route records, and measures at normal verbosity.
- Model and picker identity changes stop a trial. Complete request records and off-host collection are required; real GPU trials additionally verify KV-transfer counts, bytes, errors and stable GPU workers. Warmup transfers settle before measured counters start.

## Workload correction

The earlier calibration capped local background work at five requests despite an eight-sequence vLLM limit. It also placed long probes late enough that background work might already be finished. Previously saved 32B idle timings imply about 14.6 seconds to generate 2048 short-context tokens at idle; that is an estimate, not a loaded-runtime prediction.

The frozen calibration now uses background states (1,0),(9,8),(17,0),(17,8), 1024-token background outputs, close local/remote probe pairs, and reversed route order on repetition. The crossover decision averages the reversed orders before checking signs, so prefill ordering alone cannot manufacture a crossover. Existing soft-scoring and absolute-limit tuning grids include the relevant concurrency range. The high-load held-out trace uses two 18-request background bursts; the low trace uses two 2-request bursts. Client concurrency 64 avoids imposing a 32-request client bottleneck on these traces.

There remain 140 planned foreground requests, now with 600 background requests, plus declared short warmups. Fixed trial deadlines and the $70 session ceiling are unchanged. Actual GPU trial duration is unverified. The runner must observe real decoder saturation/queueing and a useful training tradeoff before spending on held-out policy trials; otherwise it stops as inconclusive. Qualification or completing a request count is not a performance result.

Router-owned load is sampled just before client timing. It is not an exact internal scheduling snapshot. Server running/waiting counts are sampled separately. All background outcomes are retained; chunk gaps are not mislabeled token-level latency. The criteria are practical engineering rules for controlled mean latency, not a statistical significance claim or a p95/p99 result.

## What remains unverified

Packed CUDA execution, fresh remote RDMA qualification in that layout, real GPU background durations, a useful load/locality crossover and advantage over tuned existing policies have not been measured. Fresh capacity, a valid Terraform plan, a new execution window and adequate single-attempt budget authorization are still required. Current accounting retains at least $33.46; the proposed $70 ceiling has not been newly authorized.

The prior serving and router results remain separate. No Go change, PR update, public push, permission expansion or cloud allocation occurred in this work. See the [frozen workload](../../workloads/router-session/README.md) and [combined runner](../../infra/topology/run-router-session.py).
