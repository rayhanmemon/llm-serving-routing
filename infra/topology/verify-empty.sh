#!/usr/bin/env bash
# Run after EVERY terraform destroy.
# Terraform reporting success is NOT the same thing as the account being empty.
set -uo pipefail
NEBIUS="${NEBIUS:-$HOME/.nebius/bin/nebius}"
PROJECT="${PROJECT_ID:-$(grep -E '^project_id' "$(dirname "$0")/terraform/terraform.tfvars" 2>/dev/null | cut -d'"' -f2)}"

echo "== independent teardown verification (project: ${PROJECT:-UNSET}) =="
[ -n "$PROJECT" ] || { echo "UNKNOWN — project ID is required." >&2; exit 1; }
fail=0
for kind in "compute instance list" "mk8s cluster list" "compute disk list" "compute filesystem list" "compute gpu-cluster list"; do
  echo "--- nebius $kind"
  read -r -a args <<< "$kind"
  if ! out=$("$NEBIUS" "${args[@]}" --parent-id "$PROJECT" --format json --all --timeout 20s --no-check-update); then
    echo "UNKNOWN — listing failed; emptiness is not verified." >&2
    fail=1
    continue
  fi
  if ! printf '%s' "$out" | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin)
    # The CLI omits empty protobuf fields: successful empty lists return {}.
    # API failures never reach this parser; do not synthesize {} on failure.
    if d == {}:
        items=[]
    elif isinstance(d,list):
        items=d
    elif isinstance(d,dict) and isinstance(d.get("items"),list):
        if d.get("next_page_token") or d.get("nextPageToken"):
            raise ValueError("unread page remains")
        items=d["items"]
    else:
        raise ValueError("expected an explicit items list")
    print("    count:",len(items))
    if items:
        print("    Resources remain; inspect the listing before any deletion.")
        sys.exit(1)
except (ValueError,TypeError) as e:
    print("UNKNOWN — invalid listing:",e,file=sys.stderr)
    sys.exit(1)
'; then
    fail=1
  fi
done
if [ "$fail" = "0" ]; then
  echo "PASS — instances, Kubernetes clusters, disks, filesystems and GPU clusters are empty in this project."
  echo "Other resource types and other projects were not checked."
else
  echo "*** NOT VERIFIED EMPTY — resources remain or a listing failed. Resolve and recheck. ***"
  exit 1
fi
