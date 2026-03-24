#!/usr/bin/env bash
# TPC-DS Benchmark Suite: OSS Spark vs Quanton
# Usage: ./benchmarks/run.sh [--scale-factor N] [--timeout N] [--force-datagen]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCALE_FACTOR=1
NAMESPACE="default"
TIMEOUT=7200  # 2 hours per job
FORCE_DATAGEN=false

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --scale-factor) SCALE_FACTOR="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --force-datagen) FORCE_DATAGEN=true; shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# Scale-factor-aware paths on the PVC
DATA_BASE="/data/tpcds/sf_${SCALE_FACTOR}"
PARQUET_DIR="${DATA_BASE}/parquet"
RESULTS_PVC_DIR="${DATA_BASE}/results"

echo "=== TPC-DS Benchmark Suite ==="
echo "Scale factor: SF=${SCALE_FACTOR}"
echo "Data path:    ${DATA_BASE}"
echo "Timeout:      ${TIMEOUT}s"
echo "Force regen:  ${FORCE_DATAGEN}"
echo ""

# --- Helper functions ---

wait_for_spark_app() {
  local app_name="$1"
  local kind="$2"  # sparkapplications or quantonsparkapplications
  local elapsed=0

  echo "Waiting for ${app_name} to complete..."
  while [ $elapsed -lt $TIMEOUT ]; do
    local phase
    if [ "$kind" = "sparkapplications" ]; then
      phase=$(kubectl get sparkapplication "$app_name" -n "$NAMESPACE" -o jsonpath='{.status.applicationState.state}' 2>/dev/null || echo "PENDING")
    else
      phase=$(kubectl get quantonsparkapplication "$app_name" -n "$NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null || echo "PENDING")
    fi

    case "$phase" in
      COMPLETED|Completed)
        echo "  ${app_name}: COMPLETED"
        return 0
        ;;
      FAILED|Failed)
        echo "  ${app_name}: FAILED"
        echo "  Driver logs:"
        kubectl logs "${app_name}-driver" -n "$NAMESPACE" --tail=50 2>/dev/null || true
        return 1
        ;;
      *)
        sleep 10
        elapsed=$((elapsed + 10))
        ;;
    esac
  done
  echo "  ${app_name}: TIMED OUT after ${TIMEOUT}s"
  return 1
}

cleanup_app() {
  local app_name="$1"
  local kind="$2"
  kubectl delete "$kind" "$app_name" -n "$NAMESPACE" --ignore-not-found=true 2>/dev/null || true
}

# Patch a YAML manifest: replace placeholder paths with SF-specific paths
# Note: only replace argument/path strings, NOT the mountPath /data/tpcds (PVC mount stays the same)
apply_with_sf() {
  local yaml_file="$1"
  sed \
    -e "s|/data/tpcds/parquet|${PARQUET_DIR}|g" \
    -e "s|/data/tpcds/results|${RESULTS_PVC_DIR}|g" \
    "$yaml_file" | kubectl apply -f -
}

# --- Phase 0: Build datagen Docker image ---
echo "=== Phase 0: Building datagen Docker image ==="
eval $(minikube docker-env)
docker build -t tpcds-datagen:latest -f "${SCRIPT_DIR}/Dockerfile.datagen" "${SCRIPT_DIR}"
echo "  Image built: tpcds-datagen:latest"
echo ""

# --- Phase 1: Create PVC and ConfigMaps ---
echo "=== Phase 1: Creating PVC and ConfigMaps ==="
kubectl apply -f "${SCRIPT_DIR}/k8s/pvc.yaml"

# Delete existing configmaps (idempotent)
kubectl delete configmap tpcds-scripts tpcds-sql-queries tpcds-sql-ddl \
  -n "$NAMESPACE" --ignore-not-found=true 2>/dev/null

# Create configmaps from directories
kubectl create configmap tpcds-scripts \
  --from-file="${SCRIPT_DIR}/scripts/" \
  -n "$NAMESPACE"

kubectl create configmap tpcds-sql-queries \
  --from-file="${SCRIPT_DIR}/sql/tpcds/" \
  -n "$NAMESPACE"

kubectl create configmap tpcds-sql-ddl \
  --from-file="${SCRIPT_DIR}/sql/ddl/" \
  -n "$NAMESPACE"

echo "  PVC and ConfigMaps created."
echo ""

# --- Phase 2: Generate TPC-DS data ---
echo "=== Phase 2: Generating TPC-DS data (SF=${SCALE_FACTOR}) ==="
cleanup_app "tpcds-datagen" "sparkapplication"

# Generate a patched datagen manifest with correct SF and data dir
DATAGEN_MANIFEST=$(sed \
  -e 's|"1"|"'"${SCALE_FACTOR}"'"|' \
  -e 's|"/data/tpcds"|"'"${DATA_BASE}"'"|' \
  "${SCRIPT_DIR}/k8s/datagen-job.yaml")

if [ "$FORCE_DATAGEN" = true ]; then
  # Insert --force-datagen argument after the --data-dir block
  DATAGEN_MANIFEST=$(echo "$DATAGEN_MANIFEST" | sed "s|\"${DATA_BASE}\"|\"${DATA_BASE}\"\n    - \"--force-datagen\"|")
fi

echo "$DATAGEN_MANIFEST" | kubectl apply -f -

wait_for_spark_app "tpcds-datagen" "sparkapplications"
echo ""

# --- Phase 3: OSS Spark TPC-DS on Parquet ---
echo "=== Phase 3: OSS Spark TPC-DS on Parquet ==="
cleanup_app "oss-spark-tpcds" "sparkapplication"
apply_with_sf "${SCRIPT_DIR}/k8s/oss-spark-tpcds.yaml"
wait_for_spark_app "oss-spark-tpcds" "sparkapplications"
echo ""

# --- Phase 4: Quanton TPC-DS on Parquet ---
echo "=== Phase 4: Quanton TPC-DS on Parquet ==="
cleanup_app "quanton-tpcds-parquet" "quantonsparkapplication"
apply_with_sf "${SCRIPT_DIR}/k8s/quanton-tpcds-parquet.yaml"
wait_for_spark_app "quanton-tpcds-parquet" "quantonsparkapplications"
echo ""

# --- Phase 5: Collect results and print comparison ---
echo "=== Phase 5: Collecting results ==="
RESULTS_DIR="${SCRIPT_DIR}/results/sf_${SCALE_FACTOR}"
mkdir -p "$RESULTS_DIR"

# Copy results from PVC via a temporary pod
kubectl delete pod tpcds-results-copier -n "$NAMESPACE" --ignore-not-found=true 2>/dev/null || true
kubectl run tpcds-results-copier --image=busybox --restart=Never \
  --overrides='{
    "spec": {
      "containers": [{
        "name": "copier",
        "image": "busybox",
        "command": ["sleep", "300"],
        "volumeMounts": [{"name": "data", "mountPath": "/data/tpcds"}]
      }],
      "volumes": [{
        "name": "data",
        "persistentVolumeClaim": {"claimName": "tpcds-data"}
      }]
    }
  }' 2>/dev/null || true

# Wait for pod to be ready
kubectl wait --for=condition=Ready pod/tpcds-results-copier -n "$NAMESPACE" --timeout=60s

# Copy result files
for f in oss-spark-parquet.json quanton-parquet.json; do
  kubectl cp "tpcds-results-copier:${RESULTS_PVC_DIR}/${f}" "${RESULTS_DIR}/${f}" 2>/dev/null || \
    echo "  Warning: could not copy ${f}"
done

# Cleanup copier pod
kubectl delete pod tpcds-results-copier -n "$NAMESPACE" --ignore-not-found=true 2>/dev/null || true

echo "  Results copied to ${RESULTS_DIR}/"
echo ""

# --- Print comparison table ---
echo "=== TPC-DS Benchmark Results (SF=${SCALE_FACTOR}) ==="
echo ""

python3 -c "
import json, os, sys

results_dir = '${RESULTS_DIR}'
files = {
    'OSS-Parquet': 'oss-spark-parquet.json',
    'Quanton-Parquet': 'quanton-parquet.json',
}

data = {}
for label, fname in files.items():
    path = os.path.join(results_dir, fname)
    if os.path.exists(path):
        with open(path) as f:
            d = json.load(f)
        data[label] = {r['query']: r['time_seconds'] for r in d['results'] if r['status'] == 'success'}

if not data:
    print('No results found.')
    sys.exit(0)

# Collect all queries
all_queries = sorted(set(q for d in data.values() for q in d))

# Print header
header = f\"{'Query':<10}\"
for label in files:
    if label in data:
        header += f'{label + \"(s)\":<22}'
if 'OSS-Parquet' in data and 'Quanton-Parquet' in data:
    header += f'{\"Speedup\":<10}'
print(header)
print('-' * len(header))

# Print rows
totals = {label: 0 for label in data}
for q in all_queries:
    row = f'{q:<10}'
    for label in files:
        if label in data:
            t = data[label].get(q)
            row += f'{t:<22.1f}' if t is not None else f'{\"N/A\":<22}'
            if t is not None:
                totals[label] += t
    if 'OSS-Parquet' in data and 'Quanton-Parquet' in data:
        oss_t = data['OSS-Parquet'].get(q)
        qt_t = data['Quanton-Parquet'].get(q)
        if oss_t and qt_t and qt_t > 0:
            row += f'{oss_t/qt_t:<10.1f}x'
        else:
            row += f'{\"N/A\":<10}'
    print(row)

# Print totals
print('-' * len(header))
row = f'{\"TOTAL\":<10}'
for label in files:
    if label in data:
        row += f'{totals[label]:<22.1f}'
if 'OSS-Parquet' in data and 'Quanton-Parquet' in data:
    if totals['Quanton-Parquet'] > 0:
        row += f'{totals[\"OSS-Parquet\"]/totals[\"Quanton-Parquet\"]:<10.1f}x'
print(row)

" 2>/dev/null || echo "  (Install python3 to see formatted results. Raw JSON in ${RESULTS_DIR}/)"

echo ""
echo "=== Benchmark complete ==="
