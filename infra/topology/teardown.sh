#!/usr/bin/env bash
# Destroy only the resources in this experiment's dedicated Terraform state.
set -euo pipefail
cd "$(dirname "$0")"
if [ "${1:-}" != "--execute" ]; then
  echo "Usage: bash infra/topology/teardown.sh --execute"
  echo "Destroys this experiment's Terraform resources, then independently checks the project."
  exit 2
fi
failed=0
terraform -chdir=terraform destroy -input=false -auto-approve || failed=1
bash verify-empty.sh || failed=1
exit "$failed"
