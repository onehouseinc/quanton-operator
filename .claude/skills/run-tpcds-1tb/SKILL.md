---
name: run-tpcds-1tb
description: Run the TPC-DS 1 TB benchmark on a dev or staging Kubernetes cluster, comparing OSS Apache Spark against Quanton on Parquet, Hudi, and Iceberg. Generates the dataset, loads the lakehouse formats, runs the 99 queries, and runs a lake-loader merge workload that upserts change batches into a fact table and validates the result. Use when the user asks to benchmark Quanton at scale, validate query or merge performance, generate TPC-DS at 1 TB, or compare engines on a real cluster.
allowed-tools: Bash, Read, Glob, Grep, Write, AskUserQuestion
---

# Run the TPC-DS 1 TB benchmark

You drive the benchmark suite in `benchmarks/tpcds-1tb/`. Your job is to get the user to a
trustworthy comparison of OSS Apache Spark against Quanton on their own cluster, and to keep
them informed while it runs.

This is a long job. A full 1 TB run takes many hours and costs real money in compute and
storage. Treat the user's cluster with care, confirm before each expensive phase, and never
start a phase the user has not agreed to.

## What the suite does

| Phase | What it runs |
|---|---|
| `preflight` | Checks the cluster, namespace, service account, and operator CRDs |
| `configmaps` | Publishes the benchmark scripts and the 99 query files |
| `datagen` | Runs `dsdgen` across the executors, writing Parquet to object storage |
| `load` | Builds the Hudi and Iceberg copies from that Parquet |
| `query` | Runs the 99 TPC-DS queries per engine per format |
| `merge` | Runs the lake-loader upsert rounds per engine per format |
| `report` | Collects the JSON results and prints the comparison |

Read `benchmarks/tpcds-1tb/README.md` before you start. Read `run.sh` when you need to know
exactly what a phase does. Do not reimplement any of it inline: call `run.sh`.

## Phase 0: Configuration

Check first, ask second. Run these before asking anything:

```bash
kubectl config current-context
kubectl get nodes -L node.kubernetes.io/instance-type,kubernetes.io/arch
kubectl get crd sparkapplications.sparkoperator.k8s.io \
  quantonsparkapplications.quantonsparkoperator.onehouse.ai 2>&1
ls benchmarks/tpcds-1tb/bench.env 2>/dev/null
```

Tell the user which cluster they are pointed at and confirm it is the one they meant. A 1 TB
benchmark submitted to the wrong cluster is an expensive mistake.

If either operator CRD is missing, say which one and stop. The Spark Operator is required.
The Quanton Operator is required unless the user only wants the OSS baseline.

### If `bench.env` does not exist

Copy the template and fill it in with the user. Ask these, using `AskUserQuestion`:

**Q1: Object storage.** "Which object-storage path should the benchmark read and write?"
Free text. Expect an `s3a://` or `gs://` URI. Everything the run produces goes underneath it.

**Q2: Namespace and service account.** Offer the namespaces that already hold a Spark
service account, which you can find with:
```bash
kubectl get sa -A -o json | python3 -c "
import json,sys
for item in json.load(sys.stdin)['items']:
    ann = item['metadata'].get('annotations', {})
    if any('role-arn' in k or 'gcp-service-account' in k for k in ann):
        print(item['metadata']['namespace'], item['metadata']['name'])
"
```
The account needs read and write access to the storage URI. Say so.

**Q3: Scale factor.** "What scale factor should I generate?"
- `1000` for the full 1 TB run, which is the point of this suite.
- `100` for a shorter run that still exercises every phase.
- `10` to validate the pipeline end to end in well under an hour.

Recommend that a first-time user starts at 10, confirms the whole pipeline works, then reruns
at 1000. A failure eight hours into datagen is a bad way to discover a missing permission.

**Q4: Formats and engines.** "Which formats and which engines?" Default to all three formats
and both engines. Parquet alone is the fastest way to get a first number.

**Q5: Executor shape.** Show the node types you found and propose a shape that fits. State
the total ask. The defaults are four executors of 32 cores and 200 GB, which is about 128
cores. Let the user change the count, cores, and memory.

Write the answers into `benchmarks/tpcds-1tb/bench.env`, starting from `bench.env.example`.
That file is gitignored, so it is the right place for cluster-specific values. Keep the JSON
values in single quotes.

### If `bench.env` exists

Read it back to the user in a short table: storage URI, namespace, service account, image,
scale factor, formats, engines, executor shape. Ask whether to use it as is or change
anything.

### The benchmark image

Check whether the image in `bench.env` exists in the registry. If the user has not built it:

```bash
cd benchmarks/tpcds-1tb
docker buildx build --platform linux/<arch> -t <registry>/tpcds-bench:1.0 --push .
```

Use the architecture you saw on the nodes. An `amd64` image will not run on `arm64` nodes.
The build takes several minutes because it compiles `dsdgen` and downloads the format jars.

## Phase 1: Preflight and dry run

Always do this before submitting anything:

```bash
cd benchmarks/tpcds-1tb
./run.sh --phase preflight
./run.sh --dry-run
```

The dry run renders every manifest without applying it. Skim the rendered output for the
storage URIs, the service account, and the executor shape, and show the user the resource ask
in one line. Then confirm before going further.

## Phase 2: Data generation

Tell the user what this costs before starting: at 1 TB it writes roughly 300 GB of Parquet
and typically runs for a few hours.

```bash
./run.sh --phase datagen
```

`run.sh` polls the job and prints progress. While it runs, give the user an update every
minute or two in your own words. Useful checks:

```bash
kubectl get pods -n <namespace> | grep tpcds-datagen
kubectl logs <driver-pod> -n <namespace> --tail=5
```

The driver logs one line per table as it finishes, with the row count and the elapsed time.
Small dimension tables complete in seconds and the fact tables take the bulk of the time, so
tell the user which table is in flight rather than implying steady progress.

Data generation is skipped per table when a committed Parquet directory already exists, so a
rerun after a failure picks up where it left off.

## Phase 3: Load the lakehouse formats

```bash
./run.sh --phase load
```

This builds the Hudi and Iceberg copies from the Parquet dataset. It is skipped entirely when
the user chose Parquet only.

## Phase 4: Queries

```bash
./run.sh --phase query
```

This is the headline comparison: the same 99 queries, on the same data, on both engines. It
runs every requested engine against every requested format, so six combinations at the
default settings.

While each job runs, report progress by counting completed queries in the driver log. The
runner prints one line per query with its elapsed time. Tell the user roughly where it is,
for example "Quanton on Parquet, 61 of 99 queries done".

If a query fails on one engine, note it and keep going. The report excludes any query that
did not succeed on both sides, which keeps a failure from flattering either engine.

## Phase 5: Lake-loader merges

```bash
./run.sh --phase merge
```

This is the part a query benchmark misses. Each round builds a change batch of updates to
existing rows plus brand-new rows, stages it as Parquet, then merges it into the target.
Hudi is merged through its `upsert` write operation and Iceberg through `MERGE INTO`, because
those are each format's native loader path.

Each engine merges into its own copy of the target, prepared at the start of its run and
reported separately from the merge rounds. Say so if the user asks why the merge phase begins
with a long untimed step.

Every round is validated. Watch for the per-round line in the log:

```
merge 412.3s | rows 2885700000 (expected 2885700000) | probe mismatches 0 | PASS
```

A `FAIL` means the merge did not produce the expected table state. Surface it immediately and
do not present that round's timing as a result. A fast merge that lost rows is not a fast
merge.

## Phase 6: Report

```bash
./run.sh --phase report
```

This writes `results/<run-id>/summary.md` and prints it. Then present the findings to the
user yourself. Lead with the number that answers their question:

- Total and geometric-mean speedup per format.
- How many of the 99 queries each engine won.
- The best and worst queries, both named.
- Mean merge time per format, and whether every round validated.

Be straight about the results. If Quanton loses on a query or a format, say so and say by how
much. A benchmark the user cannot trust is worth nothing to them, and they will run it again
themselves. If a phase did not finish, say which numbers are missing rather than presenting a
partial run as a complete one.

## Rerunning and cleaning up

Phases are independent and safe to repeat. The expensive artefacts persist in object storage,
so a rerun of the query phase costs minutes of setup rather than hours of generation.

To clear the Kubernetes objects from a run:

```bash
kubectl delete sparkapplication,quantonsparkapplication -n <namespace> -l benchmark=tpcds
```

Object storage is not touched by that. Tell the user what is still there and roughly how much
it is, so they can decide. Never delete their data without being asked.

## Error handling

- **Pods Pending.** The executor shape does not fit the cluster. Run
  `kubectl describe pod <pod>` and read the scheduling events back. Propose a smaller shape.
  This is a capacity problem, not an engine problem.
- **Access denied on object storage.** The driver service account cannot reach the bucket.
  Name the exact path that failed. Preflight cannot catch this, so it surfaces at the first
  write.
- **`ClassNotFoundException` for Hudi on the Quanton side.** The Quanton engine image bundles
  the Iceberg runtime but not Hudi. Either the pods need to reach Maven Central, or
  `QUANTON_HUDI_JARS` in `bench.env` should point at a Hudi bundle jar in object storage.
- **Iceberg class conflicts on the Quanton side.** The suite already keeps the classpath
  narrow. If the user added Iceberg through `spark.jars.packages` in `EXTRA_SPARK_CONF`,
  that second copy is the cause.
- **Executor lost, or out of memory, during datagen.** Each concurrent `dsdgen` chunk needs
  local disk. Raise `DSDGEN_PARALLEL` to make chunks smaller, or raise `LOCAL_DIR_SIZE`.
- **A phase times out.** `PHASE_TIMEOUT` defaults to 12 hours. Results are still collected
  from whatever the driver logged, so run the report phase before deciding what to retry.

Always separate a cluster problem from an engine problem when you explain a failure. Capacity,
credentials, image architecture, and registry access are environment problems. Say which one
you are looking at.

## Related skills

- `/run-tpcds-benchmark` runs the same benchmark on minikube at 1 GB to 10 GB, on Parquet
  only. Point the user there when they want a laptop-scale demo rather than a cluster-scale
  validation.
- `/run-merge-into` runs a small correctness demo of `MERGE INTO` on Hudi and Iceberg. Point
  the user there when a merge fails validation here and they want a minimal reproduction.
