#!/usr/bin/env bash
# TPC-DS 1 TB benchmark: OSS Apache Spark vs Quanton, on Parquet, Hudi, and Iceberg.
#
# Phases:
#   preflight   check the cluster, the namespace, the operators, and the config
#   configmaps  publish the benchmark scripts and the 99 query files to the cluster
#   datagen     generate the TPC-DS Parquet dataset into object storage
#   load        build the Hudi and Iceberg copies of the dataset
#   query       run the 99 queries on each engine and each format
#   merge       run the lake-loader merge rounds on each engine, Hudi and Iceberg
#   report      collect the JSON results and print the comparison
#
# Usage:
#   ./run.sh                                  # every phase, using ./bench.env
#   ./run.sh --phase datagen                  # one phase
#   ./run.sh --phase query --formats parquet  # narrow the scope
#   ./run.sh --dry-run                        # render the manifests without applying them
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

CONFIG_FILE="${CONFIG_FILE:-${SCRIPT_DIR}/bench.env}"
PHASE="all"
RUN_ID=""
DRY_RUN=false
OVERRIDE_FORMATS=""
OVERRIDE_ENGINES=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --phase) PHASE="$2"; shift 2 ;;
    --config) CONFIG_FILE="$2"; shift 2 ;;
    --run-id) RUN_ID="$2"; shift 2 ;;
    --formats) OVERRIDE_FORMATS="$2"; shift 2 ;;
    --engines) OVERRIDE_ENGINES="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ ! -f "${CONFIG_FILE}" ]]; then
  echo "Config file ${CONFIG_FILE} not found. Copy bench.env.example to bench.env first." >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
. "${CONFIG_FILE}"
set +a

# --- Defaults for anything the config file left out ------------------------------------

: "${SCALE_FACTOR:=1000}"
: "${FORMATS:=parquet hudi iceberg}"
: "${ENGINES:=oss quanton}"
: "${DSDGEN_PARALLEL:=400}"
: "${QUERY_ROUNDS:=1}"
: "${QUERY_WARMUP:=true}"
: "${QUERY_NUMBERS:=}"
: "${MERGE_ROUNDS:=5}"
: "${MERGE_TABLE:=store_sales}"
: "${MERGE_UPDATE_FRACTION:=0.01}"
: "${MERGE_INSERT_FRACTION:=0.002}"
: "${MERGE_SOURCE_FRACTION:=1.0}"
: "${SPARK_VERSION:=3.5.2}"
: "${DRIVER_CORES:=4}"
: "${DRIVER_MEMORY:=24g}"
: "${EXECUTOR_INSTANCES:=4}"
: "${EXECUTOR_CORES:=32}"
: "${EXECUTOR_CORE_LIMIT:=32500m}"
: "${EXECUTOR_MEMORY:=200g}"
: "${MEMORY_OVERHEAD_FACTOR:=0.2}"
: "${SHUFFLE_PARTITIONS:=2000}"
: "${LOCAL_DIR_SIZE:=200Gi}"
[[ -z "${NODE_SELECTOR:-}" ]] && NODE_SELECTOR="{}"
: "${IMAGE_PULL_SECRETS:=[]}"
: "${IMAGE_PULL_POLICY:=IfNotPresent}"
: "${PARTITION_FACTS:=none}"
: "${HUDI_TABLE_TYPE:=COPY_ON_WRITE}"
: "${HUDI_INDEX_TYPE:=BLOOM}"
: "${ICEBERG_WRITE_MODE:=copy-on-write}"
: "${ICEBERG_CATALOG:=lakehouse}"
: "${ICEBERG_DATABASE:=tpcds}"
: "${QUANTON_HUDI_JARS:=}"
: "${HUDI_BUNDLE_COORDINATES:=org.apache.hudi:hudi-spark3.5-bundle_2.12:0.15.0}"
: "${S3_ENDPOINT:=}"
: "${S3_CREDENTIALS_PROVIDER:=}"
: "${EXTRA_ENV:=[]}"
[[ -z "${EXTRA_SPARK_CONF:-}" ]] && EXTRA_SPARK_CONF="{}"
: "${PHASE_TIMEOUT:=43200}"
: "${POLL_INTERVAL:=20}"

[[ -n "${OVERRIDE_FORMATS}" ]] && FORMATS="${OVERRIDE_FORMATS}"
[[ -n "${OVERRIDE_ENGINES}" ]] && ENGINES="${OVERRIDE_ENGINES}"
[[ -z "${RUN_ID}" ]] && RUN_ID="sf${SCALE_FACTOR}-$(date +%Y%m%d-%H%M%S)"

for required in STORAGE_URI NAMESPACE SERVICE_ACCOUNT BENCH_IMAGE; do
  value="${!required:-}"
  if [[ -z "${value}" || "${value}" == *CHANGE-ME* ]]; then
    echo "Set ${required} in ${CONFIG_FILE}." >&2
    exit 1
  fi
done

BASE_URI="${STORAGE_URI%/}"
PARQUET_URI="${BASE_URI}/parquet/sf${SCALE_FACTOR}"
HUDI_URI="${BASE_URI}/hudi/sf${SCALE_FACTOR}"
ICEBERG_WAREHOUSE="${BASE_URI}/iceberg/sf${SCALE_FACTOR}"
STAGE_URI="${BASE_URI}/merge-batches/sf${SCALE_FACTOR}"

RESULTS_DIR="${SCRIPT_DIR}/results/${RUN_ID}"
SCRIPTS_CONFIGMAP="tpcds-bench-scripts"
SQL_CONFIGMAP="tpcds-bench-sql"

export NAMESPACE SERVICE_ACCOUNT BENCH_IMAGE SPARK_VERSION RUN_ID
export DRIVER_CORES DRIVER_MEMORY EXECUTOR_INSTANCES EXECUTOR_CORES EXECUTOR_CORE_LIMIT
export EXECUTOR_MEMORY LOCAL_DIR_SIZE NODE_SELECTOR IMAGE_PULL_SECRETS IMAGE_PULL_POLICY
export EXTRA_ENV SCRIPTS_CONFIGMAP SQL_CONFIGMAP

# The four structured values below are copied into the manifests verbatim, so a quoting slip
# in bench.env would produce a manifest that is valid YAML but means something else. Check
# them here, where the error can name the variable.
validate_json() {
  python3 -c '
import json, sys
name, value = sys.argv[1], sys.argv[2]
try:
    json.loads(value)
except ValueError as exc:
    sys.exit(
        "%s is not valid JSON: %s\n  value: %s\n"
        "  Wrap the value in single quotes in bench.env so the shell keeps the double "
        "quotes." % (name, exc, value)
    )
' "$1" "$2"
}

validate_json EXTRA_ENV "${EXTRA_ENV}"
validate_json IMAGE_PULL_SECRETS "${IMAGE_PULL_SECRETS}"
validate_json NODE_SELECTOR "${NODE_SELECTOR}"
validate_json EXTRA_SPARK_CONF "${EXTRA_SPARK_CONF}"

log()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }

# --- Small JSON helpers, so YAML flow values are always well formed ---------------------

json_list() { python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$@"; }

# spark_conf key=value ... ; empty values are dropped, EXTRA_SPARK_CONF wins.
spark_conf() {
  python3 - "${EXTRA_SPARK_CONF}" "$@" <<'PY'
import json, sys
extra = json.loads(sys.argv[1] or "{}")
conf = {}
for item in sys.argv[2:]:
    key, _, value = item.partition("=")
    if value != "":
        conf[key] = value
conf.update(extra)
print(json.dumps(conf))
PY
}

render() {
  python3 - "$1" <<'PY'
import os, string, sys
with open(sys.argv[1]) as handle:
    template = string.Template(handle.read())
sys.stdout.write(template.substitute(os.environ))
PY
}

# --- Spark configuration -----------------------------------------------------------------

base_conf_pairs() {
  echo "spark.kubernetes.memoryOverheadFactor=${MEMORY_OVERHEAD_FACTOR}"
  echo "spark.sql.shuffle.partitions=${SHUFFLE_PARTITIONS}"
  echo "spark.dynamicAllocation.enabled=false"
  echo "spark.local.dir=/spark-local-dir-1"
  echo "spark.network.timeout=2000s"
  echo "spark.executor.heartbeatInterval=300s"
  echo "spark.serializer=org.apache.spark.serializer.KryoSerializer"
  echo "spark.hadoop.fs.s3a.impl=org.apache.hadoop.fs.s3a.S3AFileSystem"
  echo "spark.hadoop.fs.s3a.connection.maximum=200"
  echo "spark.hadoop.fs.s3a.fast.upload=true"
  echo "spark.hadoop.mapreduce.fileoutputcommitter.algorithm.version=2"
  echo "spark.hadoop.fs.s3a.endpoint=${S3_ENDPOINT}"
  echo "spark.hadoop.fs.s3a.aws.credentials.provider=${S3_CREDENTIALS_PROVIDER}"
  if [[ -n "${S3_ENDPOINT}" ]]; then
    echo "spark.hadoop.fs.s3a.path.style.access=true"
  fi
}

# format_conf_pairs <format> <engine>
format_conf_pairs() {
  local format="$1" engine="$2"
  case "${format}" in
    hudi)
      echo "spark.kryo.registrator=org.apache.spark.HoodieSparkKryoRegistrar"
      echo "spark.sql.extensions=org.apache.spark.sql.hudi.HoodieSparkSessionExtension"
      echo "spark.sql.catalog.spark_catalog=org.apache.spark.sql.hudi.catalog.HoodieCatalog"
      if [[ "${engine}" == "quanton" ]]; then
        # The Quanton engine image does not bundle Hudi. Prefer a jar you control in object
        # storage; fall back to resolving the bundle from Maven, which needs egress.
        if [[ -n "${QUANTON_HUDI_JARS}" ]]; then
          echo "spark.jars=${QUANTON_HUDI_JARS}"
        else
          echo "spark.jars.packages=${HUDI_BUNDLE_COORDINATES}"
          echo "spark.jars.ivy=/tmp/ivy"
        fi
      fi
      ;;
    iceberg)
      echo "spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions"
      echo "spark.sql.catalog.${ICEBERG_CATALOG}=org.apache.iceberg.spark.SparkCatalog"
      echo "spark.sql.catalog.${ICEBERG_CATALOG}.type=hadoop"
      echo "spark.sql.catalog.${ICEBERG_CATALOG}.warehouse=${ICEBERG_WAREHOUSE}"
      if [[ "${engine}" == "quanton" ]]; then
        # The Iceberg jars ship on the Quanton image. Keep the classpath narrow: adding a
        # second Iceberg copy alongside the bundled one breaks with a class conflict.
        local cp="local:///opt/spark/user-jars/iceberg-spark-runtime.jar:local:///opt/spark/user-jars/iceberg-aws-bundle.jar"
        echo "spark.driver.extraClassPath=${cp}"
        echo "spark.executor.extraClassPath=${cp}"
        echo "spark.jars.ivy=/tmp/ivy"
      fi
      ;;
  esac
}

# --- Job submission ------------------------------------------------------------------------

resolve_driver_pod() {
  local kind="$1" name="$2" app="$2"
  if [[ "${kind}" == "quantonsparkapplication" ]]; then
    app="$(kubectl get quantonsparkapplication "${name}" -n "${NAMESPACE}" \
      -o jsonpath='{.status.sparkApplicationName}' 2>/dev/null || true)"
    [[ -z "${app}" ]] && app="${name}"
  fi
  local pod
  pod="$(kubectl get sparkapplication "${app}" -n "${NAMESPACE}" \
    -o jsonpath='{.status.driverInfo.podName}' 2>/dev/null || true)"
  [[ -z "${pod}" ]] && pod="${app}-driver"
  echo "${pod}"
}

job_state() {
  local kind="$1" name="$2"
  if [[ "${kind}" == "quantonsparkapplication" ]]; then
    kubectl get quantonsparkapplication "${name}" -n "${NAMESPACE}" \
      -o jsonpath='{.status.phase}' 2>/dev/null || true
  else
    kubectl get sparkapplication "${name}" -n "${NAMESPACE}" \
      -o jsonpath='{.status.applicationState.state}' 2>/dev/null || true
  fi
}

wait_for_job() {
  local kind="$1" name="$2" elapsed=0 ticks=0 state=""
  info "waiting for ${name} (timeout ${PHASE_TIMEOUT}s)"
  while (( elapsed < PHASE_TIMEOUT )); do
    state="$(job_state "${kind}" "${name}")"
    case "$(printf %s "${state}" | tr "[:lower:]" "[:upper:]")" in
      COMPLETED)
        info "${name}: COMPLETED after ${elapsed}s"
        return 0
        ;;
      FAILED|SUBMISSIONFAILED|FAILING|INVALIDATINGCACHE)
        info "${name}: ${state} after ${elapsed}s"
        kubectl logs "$(resolve_driver_pod "${kind}" "${name}")" -n "${NAMESPACE}" \
          --tail=60 2>/dev/null || true
        return 1
        ;;
      *)
        ticks=$((ticks + 1))
        if (( ticks % 10 == 0 )); then
          info "${name}: ${state:-PENDING} (${elapsed}s)"
          kubectl logs "$(resolve_driver_pod "${kind}" "${name}")" -n "${NAMESPACE}" \
            --tail=2 2>/dev/null | sed 's/^/      /' || true
        fi
        sleep "${POLL_INTERVAL}"
        elapsed=$((elapsed + POLL_INTERVAL))
        ;;
    esac
  done
  info "${name}: timed out after ${PHASE_TIMEOUT}s"
  return 1
}

collect_result() {
  local kind="$1" name="$2" outfile="$3"
  local pod
  pod="$(resolve_driver_pod "${kind}" "${name}")"
  mkdir -p "${RESULTS_DIR}"
  if kubectl logs "${pod}" -n "${NAMESPACE}" 2>/dev/null \
    | awk '/^===QUANTON_BENCH_RESULT_BEGIN===$/{flag=1;next} /^===QUANTON_BENCH_RESULT_END===$/{flag=0} flag' \
    > "${RESULTS_DIR}/${outfile}" && [[ -s "${RESULTS_DIR}/${outfile}" ]]; then
    info "results -> ${RESULTS_DIR}/${outfile}"
  else
    rm -f "${RESULTS_DIR}/${outfile}"
    info "no result block found in the log of ${pod}"
  fi
}

# submit <engine> <app-name> <main-script> <arguments-json> <spark-conf-json> [result-file]
submit() {
  local engine="$1" name="$2" script="$3" arguments="$4" conf="$5" outfile="${6:-}"
  local kind template
  if [[ "${engine}" == "quanton" ]]; then
    kind="quantonsparkapplication"
    template="${SCRIPT_DIR}/k8s/quanton-job.yaml"
  else
    kind="sparkapplication"
    template="${SCRIPT_DIR}/k8s/spark-job.yaml"
  fi

  export APP_NAME="${name}" MAIN_SCRIPT="${script}" ARGUMENTS="${arguments}" SPARK_CONF="${conf}"

  if [[ "${DRY_RUN}" == true ]]; then
    log "dry run: ${kind}/${name}"
    render "${template}"
    return 0
  fi

  log "${kind}/${name}"
  kubectl delete "${kind}" "${name}" -n "${NAMESPACE}" --ignore-not-found=true >/dev/null 2>&1 || true
  render "${template}" | kubectl apply -f -
  local status=0
  wait_for_job "${kind}" "${name}" || status=$?
  if [[ -n "${outfile}" ]]; then
    collect_result "${kind}" "${name}" "${outfile}"
  fi
  return ${status}
}

# --- Phases -------------------------------------------------------------------------------

phase_preflight() {
  log "Preflight"
  kubectl version --request-timeout=10s >/dev/null 2>&1 || {
    echo "kubectl cannot reach a cluster." >&2; exit 1; }
  info "context: $(kubectl config current-context)"
  kubectl get namespace "${NAMESPACE}" >/dev/null 2>&1 || {
    echo "Namespace ${NAMESPACE} does not exist." >&2; exit 1; }
  kubectl get serviceaccount "${SERVICE_ACCOUNT}" -n "${NAMESPACE}" >/dev/null 2>&1 || {
    echo "Service account ${SERVICE_ACCOUNT} does not exist in ${NAMESPACE}." >&2; exit 1; }
  kubectl get crd sparkapplications.sparkoperator.k8s.io >/dev/null 2>&1 || {
    echo "The Spark Operator CRD is missing." >&2; exit 1; }
  if [[ " ${ENGINES} " == *" quanton "* ]]; then
    kubectl get crd quantonsparkapplications.quantonsparkoperator.onehouse.ai >/dev/null 2>&1 || {
      echo "The Quanton Operator CRD is missing. Install it or drop quanton from ENGINES." >&2
      exit 1; }
  fi
  info "storage:  ${BASE_URI}"
  info "parquet:  ${PARQUET_URI}"
  info "formats:  ${FORMATS}"
  info "engines:  ${ENGINES}"
  info "run id:   ${RUN_ID}"
  info "results:  ${RESULTS_DIR}"
}

phase_configmaps() {
  log "Publishing scripts and queries"
  kubectl delete configmap "${SCRIPTS_CONFIGMAP}" "${SQL_CONFIGMAP}" \
    -n "${NAMESPACE}" --ignore-not-found=true >/dev/null 2>&1 || true
  # Both script directories land in one ConfigMap: the large-scale scripts import the
  # schemas and the query timing from the original single-node benchmark scripts.
  kubectl create configmap "${SCRIPTS_CONFIGMAP}" -n "${NAMESPACE}" \
    --from-file="${REPO_ROOT}/benchmarks/scripts/" \
    --from-file="${SCRIPT_DIR}/scripts/"
  kubectl create configmap "${SQL_CONFIGMAP}" -n "${NAMESPACE}" \
    --from-file="${REPO_ROOT}/benchmarks/sql/tpcds/"
  info "published ${SCRIPTS_CONFIGMAP} and ${SQL_CONFIGMAP}"
}

phase_datagen() {
  local args conf
  args="$(json_list --scale-factor "${SCALE_FACTOR}" --output-uri "${PARQUET_URI}" \
    --parallel "${DSDGEN_PARALLEL}")"
  conf="$(spark_conf $(base_conf_pairs))"
  submit "oss" "tpcds-datagen-${SCALE_FACTOR}" "datagen_parallel.py" \
    "${args}" "${conf}" "datagen.json"
}

phase_load() {
  local format args conf
  for format in ${FORMATS}; do
    [[ "${format}" == "parquet" ]] && continue
    if [[ "${format}" == "hudi" ]]; then
      args="$(json_list --format hudi --source-uri "${PARQUET_URI}" --target-uri "${HUDI_URI}" \
        --partition-facts "${PARTITION_FACTS}" --hudi-table-type "${HUDI_TABLE_TYPE}" \
        --hudi-index-type "${HUDI_INDEX_TYPE}" --shuffle-parallelism "${SHUFFLE_PARTITIONS}")"
    else
      args="$(json_list --format iceberg --source-uri "${PARQUET_URI}" \
        --catalog "${ICEBERG_CATALOG}" --database "${ICEBERG_DATABASE}" \
        --partition-facts "${PARTITION_FACTS}" --iceberg-write-mode "${ICEBERG_WRITE_MODE}" \
        --shuffle-parallelism "${SHUFFLE_PARTITIONS}")"
    fi
    conf="$(spark_conf $(base_conf_pairs) $(format_conf_pairs "${format}" "oss"))"
    submit "oss" "tpcds-load-${format}" "load_tables.py" "${args}" "${conf}" "load-${format}.json"
  done
}

phase_query() {
  local engine format args conf extra=()
  [[ "${QUERY_WARMUP}" == "true" ]] && extra+=(--warmup)
  [[ -n "${QUERY_NUMBERS}" ]] && extra+=(--query-numbers "${QUERY_NUMBERS}")
  for format in ${FORMATS}; do
    for engine in ${ENGINES}; do
      case "${format}" in
        parquet) args="$(json_list --format parquet --data-uri "${PARQUET_URI}" \
                   --rounds "${QUERY_ROUNDS}" --engine "${engine}" ${extra[@]+"${extra[@]}"})" ;;
        hudi)    args="$(json_list --format hudi --data-uri "${HUDI_URI}" \
                   --rounds "${QUERY_ROUNDS}" --engine "${engine}" ${extra[@]+"${extra[@]}"})" ;;
        iceberg) args="$(json_list --format iceberg --catalog "${ICEBERG_CATALOG}" \
                   --database "${ICEBERG_DATABASE}" --rounds "${QUERY_ROUNDS}" \
                   --engine "${engine}" ${extra[@]+"${extra[@]}"})" ;;
      esac
      conf="$(spark_conf $(base_conf_pairs) $(format_conf_pairs "${format}" "${engine}"))"
      submit "${engine}" "tpcds-q-${engine}-${format}" "run_benchmark.py" \
        "${args}" "${conf}" "query-${engine}-${format}.json" || true
    done
  done
}

phase_merge() {
  local engine format args conf
  for format in ${FORMATS}; do
    [[ "${format}" == "parquet" ]] && continue
    for engine in ${ENGINES}; do
      args="$(json_list --format "${format}" --table "${MERGE_TABLE}" \
        --source-uri "${PARQUET_URI}" --target-uri "${HUDI_URI}" --stage-uri "${STAGE_URI}" \
        --catalog "${ICEBERG_CATALOG}" --database "${ICEBERG_DATABASE}" \
        --rounds "${MERGE_ROUNDS}" --update-fraction "${MERGE_UPDATE_FRACTION}" \
        --insert-fraction "${MERGE_INSERT_FRACTION}" \
        --source-fraction "${MERGE_SOURCE_FRACTION}" \
        --target-suffix "_merge_${engine}" --engine "${engine}" \
        --partition-facts "${PARTITION_FACTS}" --hudi-table-type "${HUDI_TABLE_TYPE}" \
        --hudi-index-type "${HUDI_INDEX_TYPE}" --iceberg-write-mode "${ICEBERG_WRITE_MODE}" \
        --shuffle-parallelism "${SHUFFLE_PARTITIONS}")"
      conf="$(spark_conf $(base_conf_pairs) $(format_conf_pairs "${format}" "${engine}"))"
      submit "${engine}" "tpcds-merge-${engine}-${format}" "merge_benchmark.py" \
        "${args}" "${conf}" "merge-${engine}-${format}.json" || true
    done
  done
}

phase_report() {
  log "Report"
  if [[ "${DRY_RUN}" == true ]]; then
    info "dry run: skipping the report, no job produced results"
    return 0
  fi
  # A bare "--phase report" gets a fresh run id, which would point at an empty directory.
  # Fall back to the most recent results directory so the common case just works.
  if [[ ! -d "${RESULTS_DIR}" ]]; then
    local latest
    latest="$(ls -1dt "${SCRIPT_DIR}"/results/*/ 2>/dev/null | head -1 || true)"
    if [[ -n "${latest}" ]]; then
      RESULTS_DIR="${latest%/}"
      info "using the most recent run: $(basename "${RESULTS_DIR}")"
    else
      echo "No results found under ${SCRIPT_DIR}/results/." >&2
      return 1
    fi
  fi
  python3 "${SCRIPT_DIR}/scripts/report.py" --results-dir "${RESULTS_DIR}"
}

case "${PHASE}" in
  preflight)  phase_preflight ;;
  configmaps) phase_preflight; phase_configmaps ;;
  datagen)    phase_preflight; phase_configmaps; phase_datagen ;;
  load)       phase_preflight; phase_configmaps; phase_load ;;
  query)      phase_preflight; phase_configmaps; phase_query ;;
  merge)      phase_preflight; phase_configmaps; phase_merge ;;
  report)     phase_report ;;
  all)
    phase_preflight
    phase_configmaps
    phase_datagen
    phase_load
    phase_query
    phase_merge
    phase_report
    ;;
  *) echo "Unknown phase: ${PHASE}" >&2; exit 1 ;;
esac

log "Done. Run id ${RUN_ID}"
