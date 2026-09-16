# Frozen known-answer qualification

Preparation performs tokenization only; it sends no model request. Execution is a separate, explicitly controlled step.

The eight cases are fixed before outputs: four exact-copy and four early-context retrieval cases, with two cases at each of 512 and 8192 input tokens per family. Every case has an eight-word answer chosen by the recorded seeded rule. All 32 route/case combinations must match the gold text after outer-whitespace stripping only. Returned token IDs are diagnostic because leading whitespace is intentionally normalized. No case may be filtered, replaced or rerun selectively.

The original five-token equality failure remains evidence and remains unchanged. This suite is an additional aggregate known-answer diagnosis. Keep deployed images, TRITON_ATTN, compilation/CUDA graphs and non-eager execution unchanged. Route, worker identity and NIXL counter qualification remain separate mandatory checks.
