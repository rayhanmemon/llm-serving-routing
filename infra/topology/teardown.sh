#!/usr/bin/env bash
# Destroy only the resources in this experiment's dedicated Terraform state.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TERRAFORM_ROOT="${ROUTER_TERRAFORM_DIR:-$SCRIPT_DIR/terraform}"
case "$TERRAFORM_ROOT" in
  "$SCRIPT_DIR/terraform"|"$SCRIPT_DIR/terraform-rtx"|"$SCRIPT_DIR/terraform-rdma") ;;
  *) echo "Refusing unknown Terraform root: $TERRAFORM_ROOT" >&2; exit 2 ;;
esac
if [ "${1:-}" != "--execute" ]; then
  echo "Usage: bash infra/topology/teardown.sh --execute"
  echo "Destroys this experiment's Terraform resources, then independently checks the project."
  exit 2
fi
failed=0
python3 "$SCRIPT_DIR/prepare-teardown.py" --terraform-dir "$TERRAFORM_ROOT" || echo "Drain preparation unavailable; attempting scoped destroy." >&2
terraform -chdir="$TERRAFORM_ROOT" destroy -input=false -auto-approve || failed=1
ROUTER_TERRAFORM_DIR="$TERRAFORM_ROOT" bash "$SCRIPT_DIR/verify-empty.sh" || failed=1
exit "$failed"
