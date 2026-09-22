# One-host cache-layout comparison: preparation complete

The single-host default → packed → default diagnostic is prepared and locally rehearsed. **No GPU run or performance result is represented here.** A reusable project-scoped cleanup identity has been created; its Editor access grant is staged but not saved, pending user confirmation in the browser.

Validation: 219 tests passed, including one-host admission and the actual controller constructor; the native vLLM 0.26 parser accepted the packed configuration; 78 synthetic HTTP/SSE requests exercised all three client phases; the actual restart wrapper completed three epochs in a CPU container with substitute child processes. Exact manifests passed a local Kubernetes API dry-run and source/request round-trip. The temporary test cluster and lifecycle container were removed. These checks do not prove GPU execution or packed-layout correctness.

## Fixed protocol

- One eight-H200 preemptible host: Qwen3-32B BF16 with four-GPU prefill and four-GPU decode, unchanged image/model revision/attention/backend settings from the preceding screen. A separate inexpensive CPU node runs the client and independent deadline guard. No remote GPU resource appears in the admitted Terraform plan.
- Each epoch: four 4K/120K source-location cases, each direct and P/D (eight correctness calls); two short P/D warmups; eight consecutive identical 120K P/D requests; four producer-only/P-D pairs to vary producer allocation history without a second decoder. Total 26 requests per epoch, 12 timed long P/D observations; 78 requests if all three epochs finish.
- Producer-only controls use one output token and are allocator-history probes, not remote-inference measurements. The same sequence runs in every epoch. All observations are retained.
- Reproduction gate: at least two timed default requests have at least 60,000 descriptors/rank and at least 100 ms posting/rank. If absent, collect evidence and stop without a packed restart. This threshold identifies the previously observed expensive state; it does not exclude other observations from reporting.
- Both engines restart between layouts while retaining downloaded weights and compilation cache in the same container. Verify old CUDA processes have exited. Packed execution requires positive allocation logs from all ranks, correctness parity, unchanged transfer bytes and all-rank CUDA-IPC evidence. Save installed package versions and source hashes.
- Report complete sequence latency, descriptor/posting/transfer metrics, direct controls and return-to-default behavior. Runtime cache capacity and actual registered layout must be inspected from retained logs. No packing or router gain is inferred from configuration alone.

**Expected mechanism, recorded before execution:** packing should reduce the many-descriptor 120K case from roughly 122,880 descriptors/rank toward at most 1,920 before further merging, lowering submission overhead. Memory reuse order may prevent the original slow state from recurring in this one-decoder sequence. Attention-stride changes may affect compute latency. These are hypotheses to test, not measured outcomes.

## Budget and stop behavior

The existing session has approximately $42.95 remaining. The prepared admission reserves $40, starts cleanup by 90 minutes and targets deletion by 110 minutes, at approximately $20.05/hour fully running. No second host or automatic retry is scheduled. A restart requires at least 20 minutes before the work cutoff; correctness, startup or transport failures collect evidence and enter cleanup. Cloud and desktop deadline guards remain required before GPU work. The reusable identity is outside session Terraform so ordinary teardown does not revoke it; it is intended for removal at router-project completion.

The preceding [three-host screen](../2026-09-21-tp4-locality/RESULT.md) includes two GPU hosts and one CPU host. Its measurements are not pooled with this unexecuted layout comparison.
