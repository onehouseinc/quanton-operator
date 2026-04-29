#!/usr/bin/env bash
set -euo pipefail

# Clean up secrets created by quanton-operator after chart uninstall.
#
# The operator creates these secrets in the release namespace and replicates
# them to every job namespace:
#   - quanton-operator-cert           (Opaque)
#   - quanton-operator-docker-secret  (kubernetes.io/dockerconfigjson)
#   - quanton-operator-mtls-secret    (Opaque)
#
# Usage:
#   ./cleanup-secrets.sh                        # dry-run (default)
#   ./cleanup-secrets.sh --confirm              # actually delete
#   ./cleanup-secrets.sh --namespace default     # target a single namespace

SECRETS=(
  "quanton-operator-cert"
  "quanton-operator-docker-secret"
  "quanton-operator-mtls-secret"
)

DRY_RUN=true
TARGET_NAMESPACE=""

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Clean up secrets left behind by quanton-operator after Helm uninstall.

Options:
  --confirm              Actually delete secrets (default is dry-run)
  --namespace <ns>       Only clean secrets in the specified namespace
                         (default: all namespaces)
  -h, --help             Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --confirm)
      DRY_RUN=false
      shift
      ;;
    --namespace)
      TARGET_NAMESPACE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

if $DRY_RUN; then
  echo "=== DRY RUN (pass --confirm to delete) ==="
  echo
fi

deleted=0

for secret_name in "${SECRETS[@]}"; do
  if [[ -n "$TARGET_NAMESPACE" ]]; then
    namespaces="$TARGET_NAMESPACE"
  else
    namespaces=$(kubectl get secrets --all-namespaces --field-selector="metadata.name=${secret_name}" \
      -o jsonpath='{range .items[*]}{.metadata.namespace}{"\n"}{end}' 2>/dev/null || true)
  fi

  for ns in $namespaces; do
    if $DRY_RUN; then
      echo "[dry-run] would delete secret ${ns}/${secret_name}"
    else
      echo "Deleting secret ${ns}/${secret_name}"
      kubectl delete secret "${secret_name}" -n "${ns}"
    fi
    ((deleted++))
  done
done

if [[ $deleted -eq 0 ]]; then
  echo "No quanton-operator secrets found."
else
  if $DRY_RUN; then
    echo
    echo "Found ${deleted} secret(s). Re-run with --confirm to delete."
  else
    echo
    echo "Deleted ${deleted} secret(s)."
  fi
fi
