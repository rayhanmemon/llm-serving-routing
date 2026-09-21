# Both GPU hosts ready; request packaging rejected before inference

The cloud deadline guard armed with its own service account before GPU allocation. Both eight-H200 hosts joined the same fabric and passed GPU/runtime health checks. All three Qwen3-0.6B engines became ready. **No model request ran:** Kubernetes rejected the request ConfigMap because client-side `kubectl apply` duplicated the frozen suite in its last-applied annotation, exceeding the262144-byte annotation limit. The ConfigMap itself was634300bytes, below the1MiB ConfigMap data limit. The client Pod was created but could not mount its missing ConfigMap.

This was an agent-introduced packaging error, not a measured transport failure. The controller's immediate `finally` cleanup removed the healthy deployment before a live correction could be made. Cleanup was independently verified at02:15:30UTC September21; all five paid-resource lists and Terraform state were empty. Estimated cost **$9.60627 before tax**, not invoice-verified; **$39.18 remains**.

## Fix and stronger preflight

The runner now uses server-side apply. A fresh local kind Kubernetes API reproduced the old annotation failure, accepted and stored the exact full ConfigMap unchanged with server-side apply, and validated all three actual manifests. The temporary local cluster was deleted; the user's existing lab cluster was untouched.

A separate rehearsal executed the complete client over real local HTTP/SSE with simulated engines:32 qualification calls,8 warmups,96 foreground timings,96 background requests and48 complete pairs. The independent saved-results checker passed. These are integration results, not GPU results or latency evidence. All182 local tests also pass.

The paid entry point now requires the successful preflight record with matching suite and deployed-code hashes. A shorter retry retains the same model, cases, protocol gates and transport settings, reserves$38, begins cleanup at40minutes and targets deletion at55minutes. No extra budget or policy-performance claim.

Before this failed run's engine/client deployment, background cohorts were shortened from4096 to1024 tokens to reduce unused generation after foreground completion. No inference output had been observed. The client still rejects a cohort that completes before the foreground request. The exact premeasurement record is retained.
