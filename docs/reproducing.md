# Reproducing

**Status: incomplete starting configuration.** The topology extension and its evaluation have not run. This page will gain verified commands for building the patched image, running tests, deploying the selected serving configuration, comparing policies, collecting results and tearing down resources.

## Existing files

The serving templates currently describe one prefiller and one decoder on two single-GPU L40S machines over TCP. **This is a legacy starting configuration, not a valid topology-policy test:** it has no simultaneous local and remote decode choices. Revise it for at least three independent physical GPU workers across two hosts; a fourth active worker can add a second local decoder. Verify actual transfer asymmetry, truthful topology labels and namespace/device requirements. No earlier measurements are imported. Re-derive workload rates, transfer-byte expectations and transport thresholds on the selected deployment, and pin all versions/image digests.

| File | Purpose and remaining integration |
|---|---|
| `infra/gpu.tf` | Two preemptible (spot) GPU nodes. This is a Terraform delta that refers to a base managed-Kubernetes configuration not included here; it cannot provision a cluster by itself. |
| `infra/modelserver/kustomization.yaml` | The pinned llm-d vLLM base, image overrides, pool label and engine patches. |
| `infra/modelserver/patch-prefill.yaml`, `patch-decode.yaml` | Startup probes, rollout strategy, side-channel settings, explicit engine knobs, transfer instrumentation and pod anti-affinity. Revise roles, replica counts and placement for the selected topology, then verify compatibility. |
| `scenarios/scenario.yaml` | The llm-d-benchmark serving description. It must match the engine overlay when evaluating an already-standing deployment. |
| `scenarios/spec.yaml.j2` | Benchmark entry point; replace the checkout and repository path placeholders. |
| `workloads/rate_ladder.yaml.in` | A fixed-arrival-rate template with placeholder rates. Select workloads during router calibration; the existing transfer-capacity ladder does not establish the local-versus-remote decoder-load tradeoff. |
| `infra/assert_transport.py` | Checks the remote-prefill path: routing decision, cache bytes and selected transport. Its existing assumptions are unvalidated for this project; revise bands for actual layout, destination cache and local/remote operation. |
| `infra/results-scaffold.sh` | Creates a run directory and records machine identity. Hardware, model and transport are supplied as environment variables. |
| `results/README.md` | Per-run layout, interruption handling and traceability. |

The benchmark tool may set its own transport menu when deploying engines. Record the selected path in the actual serving process; a configuration file alone cannot prove which transport carried a request.

## Completion target

A reader should be able to build and test the exact patch, run the policy comparison from committed configuration, obtain the reported metrics from raw output, and verify resource teardown. Until those commands have been executed for this project, the repository does not claim end-to-end reproducibility.
