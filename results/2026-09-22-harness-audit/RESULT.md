# Local harness audit; no GPU run

The one-host controller now defaults to **restart qualification**, not the long timing comparison. It sends one direct/P-D pair at 4K and one at120K in each of four configurations: default, documented packed, alternate packed, then default again. That is16 requests, with no performance timing block. Every transition must acknowledge worker shutdown; every phase must be collected off the host before another starts.

A full comparison requires an explicit mode plus saved qualification from the same configuration, workload and controller sources. Qualification results are labelled separately and the independent summarizer does not mark them as a completed performance comparison. Returning to a fresh baseline for the later comparison preserves its original request history.

## Corrections

- A supervisor that ignores shutdown is forcibly stopped before bounded descendant/GPU draining. Completion markers are written atomically. The existing process marker and PID-start-time checks restrict worker signals.
- Image/container failures stop readiness promptly. Guard startup and keyboard interruption now fall inside the controller's cleanup path.
- Failed, corrupt or incomplete evidence downloads prevent advancing. Archives are checked in a fresh temporary directory so old extracted files cannot satisfy a missing-file check. A failure collecting one Pod does not prevent attempting the other.
- Source checks now include the inherited command runner and teardown/verification scripts. Old preflight records cannot admit the changed fixture.

## Verification and its limits

| Check | Result | What was substituted |
|---|---|---|
| Host suite |237 test methods run,234 passed,3 Linux-only skipped | External services as documented per test |
| Linux shutdown tests | All3 passed in the pinned container | GPU inventory; processes and signals are real |
| Four layout cycles | Both actual supervisors passed, with detached TERM-ignoring worker processes | Model/API workers, downloads, CUDA inventory and parser; parser tested separately |
| Controller command replay | Success plus14 injected failure cases reached cleanup; no later phase after failed required collection | Cloud/Kubernetes command responses and cleanup action |
| HTTP/SSE replay |16 qualification +104 comparison requests passed | Model responses, GPU evidence and metrics |
| HTTP failure replay |503 and incomplete stream retained partial records without a success marker | Loopback server responses |
| Native vLLM0.29 checks | Argument parser, layout resolution, CPU tensor allocation and worker-probe extraction passed | GPU initialization, model loading and CUDA execution |
| Local Kubernetes | Engine/client/guard manifests accepted by server dry-run; code and request ConfigMaps read back identically | Nodes and engines were not deployed; no cloud API |

The14 controller failures were desktop guard startup, invalid cloud guard acknowledgement, allocation failure, image failure, engine startup failure, nonzero client exit, incomplete completion marker, failed archive command, corrupt archive, missing request records, unsuccessful worker drain, missing shutdown acknowledgement, keyboard interruption and manual stop. The actual controller methods execute; the test replaces external commands at their boundary. Existing guard/teardown tests cover failed apply, deadline cleanup, whole-process-group termination and deletion retries; this was not a new real-cloud deletion test.

The real vLLM checks use the pinned ARM64 CPU0.29 image. The paid fixture uses the separately pinned AMD64 GPU0.29 image. Native parser/layout success is not proof of GPU-kernel or CUDA-IPC behavior.

## Remaining risk and proposed paid scope

No packed-layout GPU request or corrected CUDA shutdown has completed. The previous real default baseline is retained; the added native checks do not establish packed correctness, performance, remote transport or router benefit. Provider capacity/preemption and model startup duration are also unverified for the next attempt. No defensible90% end-to-end success probability follows from these tests.

The next one-host profile reserves **$27 before tax**, starts cleanup by minute55 and targets deletion by minute75. At the retained$20.05/hour planning rate, the full75-minute window is$25.0625, leaving$1.9375 of the reservation as margin. This is an admission estimate, not a guarantee against delayed provider deletion. Stop on the first failed gate, retain partial evidence and clean up; do not expand it into the full comparison automatically.

No cloud resources, cloud permissions, budget ledger or automation were changed in this audit. Cloud spend:$0. The dedicated local Kubernetes cluster was deleted; the existing learning cluster was preserved. The expired overnight execution window remains closed. A fresh valid execution window, plan, capacity and budget check are needed before any rental. The two-host controller and real-GPU router integration are still separate unfinished work.
