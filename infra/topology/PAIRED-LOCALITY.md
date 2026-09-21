# Paired local and remote measurements

September 20 overnight authorization: finish transport checks, matched client latency and a bounded local-load sweep within the remaining $48.83. Leave allowance calibration and policy comparisons for review. No public PR updates or automatic project-scope changes.

Two eight-H200 spot hosts on one InfiniBand fabric, plus one CPU node: **16 GPUs billed, three used**. Local prefill/decode share one two-GPU container; the remote decoder has one GPU. Keep the model and serving settings from the verified local run: Qwen3-0.6B, pinned revision/images, BF16, TP1, eager execution, block64, max-seqs4, prefix caching off. UCX chooses among all available transports. This is a small-model transport/latency fixture, not a production-size benchmark or an EPP routing-policy test.

## Safety before GPU allocation

The admitted profile reserves $46 under the cumulative $147.91 evaluation cap. Its conservative full-running rate is $39.70/hour; cleanup begins at minute50 and targets deletion at minute65 ($43.01 at full rate). The remaining reserve covers variation; this is not a provider-enforced billing cap.

Provision only the control plane, CPU node and temporary cleanup identity first. The CPU node gets an attached service account with editor access **only to this otherwise-empty experiment project**, using renewable instance metadata credentials. Nebius does not list Kubernetes clusters among supported individual-resource access-permit scopes. The guard script restricts its actions to the admitted cluster and its three named node groups. No credentials are logged, stored in the result archive, or granted to GPU/client Pods.

A guard Pod on that non-preemptible CPU node must acknowledge its identity, exact cluster, absolute deadline and absence of GPU groups before allocation proceeds. At the deadline it requests deletion of both GPU node groups, retries until they disappear, then requests deletion of its own CPU group. It does not depend on the Mac or conversation remaining alive. The local independent guard and normal `finally` cleanup also remain active. Full Terraform cleanup removes the control plane, fabric allocation and temporary IAM resources; API listings independently verify paid-resource deletion. Cloud/provider failure can still delay deletion; do not call the budget a hard cap.

Node drain timeout is60seconds for this profile; experiment-cluster disruption-budget blockers are removed before arming the cloud guard. No GPU admission if guard bootstrap leaves fewer than20minutes of work time. Stop a failed transport/correctness attempt; do not automatically retry identical failures.

## Measurement gates and frozen requests

`workloads/paired-locality/suite.json` preserves the eight copy/retrieval cases already used with this model. Its settings description is corrected for this fixture before launch. Hash the exact file and save the derived timing plan before any model request.

1. Record three distinct physical GPU identities and local NV18 topology. Confirm idle NVLink counters do not move.
2. Execute32 correctness calls: eight cases × direct-local/direct-remote/P-D-local/P-D-remote. All four normalized outputs must match for every case; gold-answer accuracy is separate. Exact input/output token counts, one transfer on the selected decoder, no transfer on other workers, unchanged error counters and stable GPU processes are mandatory.
3. Each local P/D call must produce local NVLink bytes matching its KV payload within integer counter rounding. Direct and remote controls must not move bytes between the two local GPUs. Actual engine GPU READ protocol tables must select CUDA IPC locally and rc_mlx5 RDMA remotely. Available-transport lists are insufficient.
4. Warm each prompt-size/route combination twice. Measure12 paired requests per prompt size at zero background load. Identical token-ID prompts and32 generated tokens; balance/randomize which route goes first with a frozen seed. Client runs on the CPU node and records HTTP-start to first nonempty generated text, full SSE completion, token usage and transfer counters. Save each result immediately.
5. If the above passes and time remains, repeat six pairs per prompt size with one, then three, direct-local background requests. Start a fresh4096-token background cohort for each foreground request. Require the requested active count before sending; reject a cohort that ends before the foreground completes. Keep observed before/after engine gauges and background responses. This isolates local congestion; it is not matched open-loop production traffic.
6. Collect partial results on failure or deadline, then delete resources. Report complete pairs by load/prompt size, route-order effects and uncertainty. Do not pool model sizes or erase negative results. With12 baseline pairs, tail estimates are exploratory.

Low-load48 measured requests; loaded48; eight warmups;32 qualification calls. Background requests are additional. The collector preserves engine logs, physical/transport evidence, frozen bodies, responses, client timings and cloud guard logs. No allowance is fitted and no router-performance improvement is claimed here.

## Rehearsal boundaries

Local tests exercise the actual client qualification-to-timing flow with saved output fixtures and substituted GPU/network operations; a controller replay runs actual rendering and archive extraction while replacing cloud/Kubernetes calls. Failure tests cover transport evidence, route counters, SSE completeness and guard acknowledgement. These do not prove live provider permissions, CUDA transport choice or future performance. Those remain live gates.

References: [Nebius metadata](https://docs.nebius.com/compute/virtual-machines/instance-metadata), [permission scopes](https://docs.nebius.com/iam/authorization/groups/index), [SDK authentication](https://docs.nebius.com/sdk/python/install-auth).
