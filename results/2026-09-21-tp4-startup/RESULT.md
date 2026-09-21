# TP4 startup stopped before inference

The local H200 host passed node/GPU health checks and downloaded the pinned Qwen3-32B weights. An engine subprocess then exited during startup. **No model request ran and the remote GPU host was never allocated.** All paid experiment resources were independently verified deleted; estimated cost **$7.40 before tax**, leaving **$92.60** in the authorized session budget.

## Configuration error and evidence limitation

The prepared command used `--rope-scaling`. The pinned vLLM 0.26 parser does not accept that option. A local reproduction using vLLM 0.26's real `ServeSubcommand` parser returned exit code 2 with `unrecognized arguments: --rope-scaling {}`. The command must pass the selected YaRN configuration through `--hf-overrides` instead. The experiment's model, precision, TP width and prompt lengths do not change.

The saved supervisor log reports the engine exit, but child stdout/stderr had been redirected to files in the engine container's results volume. Once the supervisor exited, collection could no longer execute in that container. Those child logs were not recovered before automatic teardown. Therefore the obsolete flag is a reproduced startup blocker, while the saved run alone does not identify every possible child error. This is a configuration/preflight failure, not evidence that TP4 or the transport failed.

## Corrections before another attempt

- Use `--hf-overrides` for the fixed YaRN settings.
- Run all generated commands through vLLM 0.26's native parser locally with networking disabled, and repeat argument validation in the actual GPU image before downloading weights. This validates arguments, not GPU execution.
- On initialization failure, write a failure marker, forward child-log tails to the supervisor log and keep the container readable until collection. The controller detects the marker and collects before cleanup; deadline guards remain active.
- Rehearse the corrected workflow and use a smaller retry admission envelope within the remaining budget. The temporary project-admin grant is retained for this same authorized logical session and must be revoked after its final cleanup.

Cost uses native VM create-start to delete-finish timestamps plus 320 GiB of disk capacity conservatively charged over the whole session. No provider invoice reconciliation or successful inference/transport result is claimed.
