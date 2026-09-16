# Dual-host RDMA serving attempt: orchestration failure

Both eight-H200 spot VMs and the CPU VM were allocated on the intended Nebraska placement. The model servers were still starting; no inference qualification or latency comparison completed.

An early setup path rendered into the same directory the main runner later tried to create with `exist_ok=False`. The resulting FileExistsError triggered the runner's automatic cleanup. This was an agent-introduced orchestration bug, not a transport failure. The separate early image importer also raced CPU-node readiness and failed before importing the router image.

The previous raw RDMA result remains valid; this attempt supplies no real-model RDMA evidence. The local engines reached Qwen3-8B loading with FlashAttention 3. The frozen suite's metadata was prospectively updated for this new H200 fixture before any request; its eight prompts and request bodies were unchanged from the approved parity suite.

Correction: preserve any existing render directory under a unique evidence name before fresh rendering. A regression test executes both renders and checks preservation. Subsequent provisioning can start all three node groups concurrently rather than serializing GPU nodes. No overlapping early full setup should be needed. Billing cleanup remains independent of these runners.

Cleanup independently verified at 2026-09-16T23:32:11.992409+00:00; all five resource types empty. Cost $10.85; remaining authorized evaluation funds $21.98.
