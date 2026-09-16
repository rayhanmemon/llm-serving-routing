# Prospective direct-versus-P/D confirmation

Rayhan approved this revised acceptance rule before these outputs. Seed 17092027 fixes eight new cases: copy and early-context retrieval, two cases each at 512 and 8192 input tokens, across four routes (32 requests).

Pass requires all 32 request-integrity checks and, for each of the eight cases, identical output across direct-local, direct-remote, P/D-local and P/D-remote after outer-whitespace stripping only. Gold-answer accuracy is reported separately; it is not the acceptance criterion. The suite JSON's explicit `criterion: direct-parity` selects this behavior; its legacy gold reference-count fields do not override that criterion.

Exact route, one transfer per P/D request, zero transfer failures and stable worker/GPU identity remain mandatory separate checks. No case may be dropped, replaced or selectively rerun. The engine configuration is unchanged. Both earlier failed gates remain failed. This suite was frozen before any of its model requests.
