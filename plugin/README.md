# plugin — a load-aware prefill/decode decider for llm-d's router

> **Designed and specified by me; implemented with AI assistance; measured on real hardware.**

**Status: not started.** Nothing here runs yet. The design — the decision the plugin makes per request, the signals it reads, the interface it plugs into, and the tests that define it — is written and reviewed before any code; the code lands after it. When the plugin is proposed upstream to llm-d, this file carries the link and its status stated plainly (open, under review, merged, or closed) and keeps that line current.

## What it is

llm-d's router — the endpoint picker that the Kubernetes Gateway API inference extension calls to choose a backend pod — decides for each request whether to send the prompt through a separate prefill server (the processing of the prompt) and then hand the resulting key/value cache to a decode server (the generation of output tokens), or to serve the whole request on one decode server. The component that makes that per-request yes/no call is the *decider*. This plugin is a decider that reads load — the queue and cache pressure the router can already see on the candidate servers — and decides from it.

## How it is measured

If the comparison runs inside the sweep, the results report it as an A/B against the router's shipped deciders on the same workload profiles at the same GPU count, with the analytical performance model stating beforehand where the decider is expected to gain and where to lose, and the table giving predicted / measured / relative error. The code and the upstream proposal ship regardless of whether the comparison runs; this file says which happened.

## What this directory will hold

- the design note (decision inputs, the plugin interface, the tests) — before the code
- the Go source and its tests
- the upstream proposal link and its current status
