# Benchmark collection and TTFT instrumentation notes

The client image pins `inference-perf==0.6.1`. The source paths below are relative to its pinned `inference_perf` package.

## TTFT source

The primary v0.6.1 stream parser defines `chunk_times` as timestamps for content-bearing chunks only and `response_chunks` as the corresponding JSON strings, one-to-one. Usage-only chunks, `[DONE]`, empty/role-only deltas and unparsable events are excluded. The implementation appends both fields together only when extracted content is truthy:

- [`streaming_parser.py` lines 62–74 and 83–121](https://github.com/kubernetes-sigs/inference-perf/blob/v0.6.1/inference_perf/apis/streaming_parser.py#L62-L121)
- Local pinned source: `inference_perf/apis/streaming_parser.py:62-121`

The completion adapter passes completion text into that parser, stores the returned `response_chunks` and `chunk_times`, and derives its tokenized `output_tokens` separately from the concatenated text:

- [`completion.py` lines 51–76](https://github.com/kubernetes-sigs/inference-perf/blob/v0.6.1/inference_perf/apis/completion.py#L51-L76)
- Local pinned source: `inference_perf/apis/completion.py:51-76`

Accordingly, client TTFT is `chunk_times[0] - start_time`, after verifying that parsed `response_chunks` exactly equal the truthy content events in the retained raw SSE stream. Server usage remains authoritative for the required 512/8192 prompt tokens and 128 completion tokens. The separately serialized `output_token_times` series must remain finite, ordered and inside the request interval, but its cardinality is not an SSE-content invariant.

The observed short-remote warmup demonstrates the distinction: the raw stream contained 128 choice-text events, of which 126 were truthy content events and two carried empty text. The report therefore contained 126 `response_chunks` and 126 `chunk_times`, while `output_token_times` had 127 entries and the client tokenizer estimated 126 output tokens. Server usage correctly reported 512 prompt and 128 completion tokens. The validated TTFT was 0.311193282 seconds.

## Two bounded fixes

1. [`192299bcf2f6d8d9a83c9fd161df79bd0dfa9f4d`](https://github.com/rayhanmemon/disagg-boundary/commit/192299bcf2f6d8d9a83c9fd161df79bd0dfa9f4d) scoped benchmark log collection to the explicitly requested benchmark Pod. Retained warmup Pods can disappear during a later snapshot; collecting their logs again created a race unrelated to the current arm.
2. [`9ef24c356ca1c6fbcf214d76c322f0aca19f3a31`](https://github.com/rayhanmemon/disagg-boundary/commit/9ef24c356ca1c6fbcf214d76c322f0aca19f3a31) changed forced-route validation to use the source-defined `response_chunks`/`chunk_times` pair and added failures for mismatched chunks, invalid cardinality, out-of-interval timestamps and non-finite estimated timestamps.

Both preserved short warmups were revalidated from their original reports and matched request payloads; no request was regenerated for the repair. No measured arm had run before these instrumentation fixes. The measured block resumed afterward, and this note makes no latency claim from work still in progress.
