# One-session router comparison

This is the prepared **single-allowance** evaluation. It uses Qwen3-32B BF16, four GPUs per model instance, and one fixed BHLNC cache layout (block, head, layer, token, channels). It does not perform the earlier four-layout study. No GPU result is implied by these files.

`plan.json` freezes the parameter grid, request lengths, arrival traces, policy order and engineering decision criteria. `suite.json.gz` contains four frozen code-reading prompts: training and evaluation variants at 4K and 120K tokens. `qualification.json.gz` selects three direct/disaggregated pairs per host, including a repeated long prompt. Source/model provenance and the accompanying source license are preserved. Calibration includes loads above the configured eight-sequence limit: local/remote background counts (1,0),(9,8),(17,0),(17,8). Background requests generate 1024 tokens; calibration route pairs arrive close together and reverse order on the repeat. The crossover screen averages those reversed orders first. A measured decoder queue, not merely a large router count, is required to continue.

The combined runner is `infra/topology/run-router-session.py`. It:

1. Arms cleanup and prepares the benchmark image and router image on the CPU node before requesting GPUs.
2. Rechecks exact capacity after CPU/image preparation, rejecting observations older than 30 minutes, then starts the selected layout on one host, verifies six real requests, and saves the evidence.
3. Requires remaining capacity and funded time, shortens both cleanup guards, waits for acknowledgement, and only then adds remote.
4. Qualifies remote, deploys the actual published router/sidecar, and waits for all three serving endpoints.
5. Uses 32 calibration probes to select a fixed allowance and tune existing configurations. All background work goes through the same endpoint picker.
6. Runs 20 held-out trials across five policies and two traces, followed by four predeclared confirmation trials when time permits. There are 96 evaluation probes plus background requests. Policy changes require drained requests; the engines stay running.
7. Saves results and deletes the deployment. An inconclusive or incomplete run is recorded as such. The runner never retries itself; any separately authorized capacity retry needs a fresh admission and counts against the same spending pool.

The foreground total is 140 including 12 qualification calls. The background workload adds 600 requests. Every policy also receives a declared short warmup; warmup transfers must settle before measured counters begin. All request outcomes, including background completion/stream gaps, are retained. Routing-policy trials are unpinned; only calibration uses forced routes. The soft baseline's locality weight and idle/busy scoring preferences are included in the same training-data search. The existing absolute-load filter is another baseline.

The benchmark uses pinned **inference-perf 0.6.1** for scheduling, HTTP, streaming parsing and per-request reporting. `router-perf-adapter.py` supplies the frozen token-ID payloads and validates server token counts without downloading a tokenizer or re-tokenizing the inputs. It samples the router's own in-flight gauges before native request timing. These are **pre-request observations, not exact internal scheduling snapshots**. A separate half-second sampler records vLLM running/waiting requests to distinguish decoder queues from requests still waiting on prefill. Envoy access records establish actual routes. Normal router verbosity is used; per-chunk trace logging would distort the experiment and rotate away evidence.

The engineering rule uses mean latency and per-block consistency, not statistical significance or tail-latency claims. The small sample cannot demonstrate broad production behavior or absence of regressions. Beating hard locality alone is insufficient. Failure to beat the tuned alternatives does not authorize expanding the experiment.

## Local preparation

Use a Python environment with PyYAML, the local Docker daemon, kubectl and Helm. The preparation scripts make no cloud allocations. `rehearse-router-session.py` requires the dedicated `kind-router-session` loopback context. It uses real router and sidecar binaries with synthetic workers. The pinned AMD64 benchmark runs in Docker through local port forwards because the ARM Kind runtime cannot reliably start that image; production CPU nodes are AMD64.

`preflight-router-session.py` checks the native vLLM parser/layout/probe, tests, manifests, image archive, guard protocol and source-bound router rehearsal. Its output binds the exact code, workload, chart and image hashes. Changed inputs require new relevant validation. A local pass is not GPU, CUDA-IPC or RDMA qualification.

## Paid admission boundary

The new profile is `tp4-router-comparison`. It requires a **fresh single-attempt approval** (`allow_multiple_attempts: false`), a new execution window, the exact fresh Terraform plan, and the matching preflight record. Old approvals do not admit this profile. Direct full-plan execution through `pilot-session.py` is blocked.

The spending pool is capped at $70 before tax across authorized attempts. A new full-comparison attempt requires at least $69 remaining; its own ceiling is the lesser of $70 and the actual remaining balance at admission. Accounting charges each requested eight-GPU node at $19.60/hour plus $0.50/hour supporting resources, reserves $3 margin and 20 minutes for collection/deletion, and refuses remote admission without 69 funded working minutes afterward. A smaller balance shortens the deadline; it never resets the clock or restores money spent on prior attempts. These are conservative admission controls, not a provider-enforced billing cap. A concrete run still needs budget authorization and fresh capacity checks before each GPU allocation.
