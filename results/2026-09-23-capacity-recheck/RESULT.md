# Capacity changed to stale before GPU allocation

Run `20260923T151931Z-c74d32f1` completed CPU preparation, cloud cleanup-guard arming, benchmark-client readiness and router-image import. A newly fetched provider-FRESH response allowed initial admission. Before the first GPU request, Nebius reported the matching H200 advice as STALE. The controller refused the GPU request and cleaned up automatically.

No GPU VM was requested and no inference or policy measurement ran. This was an admission block, not a GPU placement failure, transport failure or router result. The generic exception text says no infrastructure was created; specifically, no **GPU** infrastructure was created—CPU/control-plane resources did run.

Both resources and all five scoped paid resource types were verified deleted at **15:36:15 UTC September 23**. Controller, desktop guard and task keep-awake exited. Estimated support cost is **$0.13946 before tax**, using the existing $0.50/hour allowance over the full admitted-to-cleanup interval; this is not an invoice. **$74.86 remains** in the approved $75 continuation pool; cumulative router estimate **$230.85**.

The previous client-only thirty-minute cutoff was corrected using the provider's official freshness semantics. This refusal was different: the provider itself marked the response STALE. The response reported three potential nodes but did not qualify as current advice. No physical absence or reservation is inferred from that response.

Continue free capacity checks and automatically admit a new guarded attempt when the provider returns FRESH eligible advice and all budget/source/cleanup checks pass. No software or workload retry is needed; local preparation remains validated. Do not reuse the consumed single-attempt approval or replenish the pool.
