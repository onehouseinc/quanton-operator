# TPC-DS with the Quanton AI Agent

Runs all 99 TPC-DS queries against a configurable scale factor (default 10 GB)
of pre-generated Parquet data on minikube, with the **Quanton AI Agent**
enabled. Once the driver pod is running, the Spark Web UI at
`http://localhost:4040` shows an agent sidebar (Chat, Recommendations,
Diagnostics, Monitor) that can reason about the live job — stages, tasks,
SQL plans, and executor metrics — backed by an in-pod LLM.

## Prerequisites

- minikube running with at least 12 CPUs / 16 GB RAM allocated
- Spark Operator and Quanton Operator already installed and `Running`
  (see [`../../README.md`](../../README.md) and [`../../benchmarks/README.md`](../../benchmarks/README.md))
- `tpcds-datagen:latest` image built and loaded into minikube
  (one-time, also covered in `benchmarks/README.md`)

## Run

```bash
# Default — 10 GB scale factor (~10-15 min datagen, ~8-30 min queries)
./run.sh

# Smaller (faster) — 1 GB (~3-5 min datagen, ~2-3 min queries)
SCALE_FACTOR=1 ./run.sh

# Larger — 20 GB (~20-30 min datagen, ~15-25 min queries)
SCALE_FACTOR=20 ./run.sh
```

The script:

1. Creates the `tpcds-data` PVC and three TPC-DS ConfigMaps from
   the resources in `../../benchmarks/`.
2. Submits the **datagen** SparkApplication (skips if data already present
   for that scale factor).
3. Submits the **`quanton-tpcds-agent`** QuantonSparkApplication with the
   agent enabled, then prints the `port-forward` command you need to view
   the UI.

## View the agent UI

Once the script reports `Driver is Running`:

```bash
kubectl port-forward quanton-tpcds-agent-driver 4040:4040 -n default
```

Open <http://localhost:4040> — agent sidebar appears in the bottom-right.

In **Settings**, paste an Anthropic / OpenAI / Gemini API key. The agent's
context buffer (`Chat`, `Recommendations`, `Diagnostics`) covers the entire
live run — for SF=10 that's ~1000 stages and 99 SQL queries.

## Files

| File | Role |
|---|---|
| `quanton-tpcds-agent.yaml` | The QuantonSparkApplication manifest. The image field is overridden by the operator's `quantonSparkImage` setting, and the agent plugin is enabled via two `sparkConf` keys. Substitute `${SCALE_FACTOR}` is done by `run.sh`. |
| `run.sh` | End-to-end orchestration: PVC + ConfigMaps + datagen + Quanton submission. Reuses scripts and SQL files from `../../benchmarks/`. |
| `README.md` | This file. |

## Cleanup

```bash
kubectl delete quantonsparkapplication quanton-tpcds-agent -n default
# Keeps the PVC and the cached Parquet data — re-runs are fast.
# To wipe everything:
# kubectl delete pvc tpcds-data -n default
```

## Notes on agent behavior

- If you installed the operator with `--set onehouseConfig.enableAIAgent=true`,
  the controller injects the agent plugin automatically and you can drop the
  two `spark.plugins` / `spark.quanton.agent.enabled` lines from the
  QuantonSparkApplication's `sparkConf`.
- The agent UI is only reachable **while the driver pod is running**. When
  the queries finish, the SparkContext shuts down and the UI goes with it.
  For longer interactive sessions, use a larger `SCALE_FACTOR` (which keeps
  queries running longer) or set `spark.quanton.agent.await.termination=true`
  in `sparkConf` — note that with current builds this only delays JVM exit,
  it does not preserve the HTTP UI.
