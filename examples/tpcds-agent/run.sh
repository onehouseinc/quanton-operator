#!/usr/bin/env bash
# run.sh — Run all 99 TPC-DS queries against a configurable scale factor of
# Parquet data on minikube, with the Quanton AI Agent enabled.
#
# Reuses the datagen image, scripts, and SQL files from ../../benchmarks/.
#
# Usage:
#   ./run.sh                       # default SF=10
#   SCALE_FACTOR=1 ./run.sh        # smaller dataset (~3 min queries)
#   SCALE_FACTOR=20 ./run.sh       # larger dataset (~15-20 min queries)
#
# Prereqs:
#   - Quanton Operator installed on minikube (and 3/3 Running)
#   - Spark Operator installed
#   - tpcds-datagen:latest image built and loaded into minikube — see
#     ../../benchmarks/README.md for build instructions

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCHMARKS_DIR="$(cd "${SCRIPT_DIR}/../../benchmarks" && pwd)"

SCALE_FACTOR="${SCALE_FACTOR:-10}"
NAMESPACE="default"
TIMEOUT="${TIMEOUT:-7200}"  # 2 hours
DATA_BASE="/data/tpcds/sf_${SCALE_FACTOR}"

echo "=== TPC-DS + Quanton AI Agent ==="
echo "Scale factor: SF=${SCALE_FACTOR}"
echo "Data path:    ${DATA_BASE}"
echo ""

wait_for_state() {
  local kind="$1" name="$2" want="$3" elapsed=0
  echo "Waiting for ${name} to reach ${want}..."
  while [ "$elapsed" -lt "$TIMEOUT" ]; do
    local state
    if [ "$kind" = "sparkapplication" ]; then
      state=$(kubectl get sparkapplication "$name" -n "$NAMESPACE" \
        -o jsonpath='{.status.applicationState.state}' 2>/dev/null || echo "PENDING")
    else
      state=$(kubectl get quantonsparkapplication "$name" -n "$NAMESPACE" \
        -o jsonpath='{.status.phase}' 2>/dev/null || echo "PENDING")
    fi
    case "$state" in
      "$want")  echo "  ${name}: ${state}"; return 0 ;;
      FAILED|Failed)
        echo "  ${name}: FAILED — last 30 driver log lines:"
        kubectl logs "${name}-driver" -n "$NAMESPACE" --tail=30 2>/dev/null || true
        return 1 ;;
      *) sleep 10; elapsed=$((elapsed + 10)) ;;
    esac
  done
  echo "  ${name}: TIMED OUT after ${TIMEOUT}s"
  return 1
}

# --- Phase 1: PVC + ConfigMaps ---
echo "=== Phase 1: PVC + ConfigMaps ==="
kubectl apply -f "${BENCHMARKS_DIR}/k8s/pvc.yaml"
kubectl delete configmap tpcds-scripts tpcds-sql-queries tpcds-sql-ddl \
  -n "${NAMESPACE}" --ignore-not-found
kubectl create configmap tpcds-scripts     --from-file="${BENCHMARKS_DIR}/scripts/" -n "${NAMESPACE}"
kubectl create configmap tpcds-sql-queries --from-file="${BENCHMARKS_DIR}/sql/tpcds/" -n "${NAMESPACE}"
kubectl create configmap tpcds-sql-ddl     --from-file="${BENCHMARKS_DIR}/sql/ddl/"   -n "${NAMESPACE}"
echo ""

# --- Phase 2: Datagen (skip if data already present) ---
echo "=== Phase 2: Datagen (SF=${SCALE_FACTOR}) ==="
kubectl delete sparkapplication tpcds-datagen -n "${NAMESPACE}" --ignore-not-found
sed -e 's|"1"|"'"${SCALE_FACTOR}"'"|' \
    -e 's|"/data/tpcds"|"'"${DATA_BASE}"'"|' \
    "${BENCHMARKS_DIR}/k8s/datagen-job.yaml" | kubectl apply -f -
wait_for_state sparkapplication tpcds-datagen COMPLETED
echo ""

# --- Phase 3: Quanton TPC-DS with the agent enabled ---
echo "=== Phase 3: Quanton TPC-DS (agent enabled) ==="
kubectl delete quantonsparkapplication quanton-tpcds-agent -n "${NAMESPACE}" --ignore-not-found
sed -e "s|\\\${SCALE_FACTOR}|${SCALE_FACTOR}|g" \
    "${SCRIPT_DIR}/quanton-tpcds-agent.yaml" | kubectl apply -f -

# Wait for the driver pod to be running, then tell the user how to view the agent
echo "Waiting for driver pod to be Running..."
until kubectl get pod quanton-tpcds-agent-driver -n "${NAMESPACE}" \
    -o jsonpath='{.status.phase}' 2>/dev/null | grep -q "Running"; do
  sleep 3
done

echo ""
echo "=== Driver is Running ==="
echo "To open the Spark UI + AI Agent sidebar in your browser:"
echo ""
echo "  kubectl port-forward quanton-tpcds-agent-driver 4040:4040 -n ${NAMESPACE}"
echo "  open http://localhost:4040"
echo ""
echo "To watch the job complete:"
echo "  wait_for_state quantonsparkapplication quanton-tpcds-agent COMPLETED"
echo ""
echo "Cleanup when done:"
echo "  kubectl delete quantonsparkapplication quanton-tpcds-agent -n ${NAMESPACE}"
