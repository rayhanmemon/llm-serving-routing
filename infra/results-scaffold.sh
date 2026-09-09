#!/usr/bin/env bash
# Creates a per-run results directory in the shape this repository publishes in:
# one self-contained directory per run holding the rendered configuration, a
# timestamped command log, the raw per-request numbers, the seed — and the
# identity of the machines the run used, so a preemptible (spot) reclaim can be
# written up against a named node.
#
#   bash infra/results-scaffold.sh [results-root] [run-name]
#
# Everything specific to a cluster comes from the environment, so this runs on a
# laptop with no cluster and no cloud credentials — it degrades to a directory
# with the identity files marked unavailable, which is the point: the layout is
# rehearsed before anything bills.
#
#   HARDWARE   free text, e.g. "8xH200 SXM, InfiniBand"      (default: unset)
#   MODEL      the served model                              (default: unset)
#   TRANSPORT  what the wire is EXPECTED to be; the assertion output in the run
#              directory is what says what it WAS                (default: unset)
#   TFVARS     path to the Terraform variables file the project id is read from
#   PROJECT_ID the cloud project id, if not read from TFVARS
#   NEBIUS     path to the provider CLI                (default: ~/.nebius/bin/nebius)
#   SEED       the random seed to record                  (default: a random one)
set -euo pipefail
ROOT="${1:-results/$(date +%F)}"
RUN="${2:-run-$(date +%H%M%S)}"
D="$ROOT/$RUN"
HARDWARE="${HARDWARE:-(not stated - set HARDWARE)}"
MODEL="${MODEL:-(not stated - set MODEL)}"
TRANSPORT="${TRANSPORT:-(not stated - set TRANSPORT)}"
NEBIUS="${NEBIUS:-$HOME/.nebius/bin/nebius}"
TFVARS="${TFVARS:-}"
PROJECT="${PROJECT_ID:-}"
if [ -z "$PROJECT" ] && [ -n "$TFVARS" ] && [ -f "$TFVARS" ]; then
  PROJECT="$(grep -E '^project_id' "$TFVARS" 2>/dev/null | cut -d'"' -f2 || true)"
fi
mkdir -p "$D"/{config,logs,raw,analysis}

# ---- node identity: name, provider id, creation time, and the labels that matter
kubectl get nodes -o json 2>/dev/null | python3 -c '
import json,sys
d=json.load(sys.stdin)
print("# Kubernetes nodes at run creation (name · providerID · created · preemptible · driver preset · GPUs allocatable · instance type)")
for n in d.get("items",[]):
    m=n["metadata"]; l=m.get("labels",{}); st=n.get("status",{})
    print("  ".join([m["name"], n["spec"].get("providerID","-"), m.get("creationTimestamp","-"),
                     "preemptible=%s" % l.get("nebius.com/preemptible","-"),
                     "drivers=%s" % l.get("nebius.com/drivers-preset","-"),
                     "gpu=%s" % st.get("allocatable",{}).get("nvidia.com/gpu","0"),
                     l.get("node.kubernetes.io/instance-type","-")]))
' > "$D/config/nodes.txt" 2>/dev/null || echo "# kubectl unavailable at scaffold time" > "$D/config/nodes.txt"

# ---- the cloud's view: instance ids, names, states, creation times (best effort, free)
if [ -n "${PROJECT:-}" ] && [ -x "$NEBIUS" ]; then
  "$NEBIUS" compute instance list --parent-id "$PROJECT" --format json 2>/dev/null | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: print("# instance list unavailable"); raise SystemExit
items=d.get("items",[]) if isinstance(d,dict) else d
print("# compute instances at run creation (id · name · state · created)")
for x in items:
    m=x.get("metadata",{}); s=x.get("status",{})
    print("  ".join([m.get("id","?"), m.get("name","?"), s.get("state","?"), m.get("created_at","?")]))
' > "$D/config/instances.txt"
else
  echo "# no project id / provider CLI at scaffold time" > "$D/config/instances.txt"
fi

cat > "$D/README.md" <<EOF2
# $RUN

- **date:** $(date -u +%FT%TZ)
- **hardware:** $HARDWARE
- **model:** $MODEL
- **transport, expected:** $TRANSPORT — what it actually was is in
  \`transport-assertion.txt\` in this directory, read from the library's own
  selection printout, never from the YAML
- **machines:** \`config/nodes.txt\` (Kubernetes view) and \`config/instances.txt\`
  (the cloud's view), captured when this directory was created

**Rerun rule.** On preemptible (spot) nodes, if a node is reclaimed mid-run the
node group is re-applied once and the affected measurement is rerun in full; the
reclaimed node's name and id, the time, and what was in flight are written here
under *Evictions*. A second reclaim ends the session with whatever is green. No
partial run is ever reported as complete.

## Evictions

(none)

| File | What |
|---|---|
| \`config/\` | the rendered scenario, spec and workload profile actually used; the node and instance identity |
| \`logs/\` | the exact command line, plus stdout and stderr |
| \`raw/\` | per-stage and per-request metrics JSON |
| \`analysis/\` | charts |
| \`seed.txt\` | the random seed |
EOF2
echo "${SEED:-$RANDOM}" > "$D/seed.txt"
echo "created $D"
echo "  nodes:     $(grep -vc '^#' "$D/config/nodes.txt" || echo 0) listed in config/nodes.txt"
echo "  instances: $(grep -vc '^#' "$D/config/instances.txt" || echo 0) listed in config/instances.txt"
