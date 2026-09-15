# Expected behavior before requests

Recorded September 15, 2026, while infrastructure provisioning was underway, before any inference requests. This is calibration of local/remote transfer, not the final held-out evaluation.

Operator's stated expectation, verbatim:

> TTFT should increase for both prompts (512 token prompt & 8,192 token prompt) when moving from local to remote decode. Because in both cases, we will need to shuttle KV Cache from the prefill GPU's memory to the decode GPU's memory.
>
> As for prompt size, we will see the most meaningful TTFT latency increase in the 8192 token prompt, as the KV cache size will be larger and will demand more bandwidth for the transfer.

Clarification discussed before measurement: both routes transfer KV. The expectation depends on the local path having lower effective transfer cost. More KV bytes take longer at a given effective bandwidth; the longer prompt does not necessarily demand a higher transfer rate. Overlap and other work can reduce the visible first-token latency difference. No outcome has been measured yet.

The initial batch covers correctness/warmup on both paths and sequential diagnostic requests with requested prompt sizes 512 and 8192, output limit 128. Twelve requests per diagnostic arm, one active request at a time. Inspect actual input/output counts, transfer observations, selected endpoints and errors; do not infer pure transfer time from client TTFT alone. Repeat and reverse route order only after reviewing the initial batch.
