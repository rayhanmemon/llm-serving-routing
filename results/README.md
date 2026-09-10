# results

One self-contained directory per run. Nothing here is a result yet.

`infra/results-scaffold.sh` creates a directory in this shape and records the
identity of the machines the run used at the moment it is created. Every number
in the report links back to one of these directories.

## Layout

```
results/<date>/<run>/
  README.md          the run's own header: date, hardware, model, expected transport,
                     where the machine identity is, and the Evictions section
  config/            the rendered scenario, the spec, and the workload profile
                     ACTUALLY used — not the templates, the rendered forms;
                     plus nodes.txt (the Kubernetes view of every node: name,
                     provider id, creation time, preemptible flag, driver preset,
                     allocatable GPUs, instance type) and instances.txt (the
                     cloud's own view: instance id, name, state, creation time)
  logs/              the exact command line that produced the run, plus its
                     stdout and stderr
  raw/               per-stage and per-request JSON, as the harness emitted it
  analysis/          charts and derived tables, each traceable to raw/
  seed.txt           the random seed
```

The transport assertion's output belongs in the run directory as
`transport-assertion.txt`. What the wire actually was is read from the transfer
library's own selection printout, never from the configuration that asked for
it.

## Rerun rule, for preemptible (spot) nodes

If a node is reclaimed during a comparison, retain the interrupted attempt and its charges. Record node identity, reclaim time and the requests in flight. Restore the configuration, recheck the node identities and selected transport, then rerun the **complete affected comparison block**, including both policies or architectures, within the remaining priced retry allowance. Stop or narrow the remaining scope when that allowance cannot fit.

A partial run is never reported as complete, and surviving samples are never stitched into a rerun. Keep interrupted blocks visible alongside complete-block performance and total cost. Application failures are outcomes to report, not a reason to relabel a run as a spot interruption.

## Traceability

Every number in the report links back to a directory here, and the command in
`logs/` plus the rendered configuration in `config/` is enough to produce it
again on the same cluster shape.
