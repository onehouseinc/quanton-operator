# TPC-DS 1 TB: OSS Apache Spark vs Quanton

Generates the TPC-DS dataset at 1 TB, loads it into Parquet, Apache Hudi, and Apache Iceberg,
then measures both halves of a lakehouse workload on each format:

- **Queries.** All 99 TPC-DS queries, run identically on OSS Apache Spark and on Quanton.
- **Merges.** A lake-loader loop that repeatedly upserts change batches into a fact table,
  again on both engines, so you can see merge throughput and not only scan throughput.

The suite runs on any Kubernetes cluster with object storage. It is meant for a dev or
staging cluster, where you can validate Quanton against your own data layout and your own
node types before trusting a number somebody else published.

The smaller sibling of this suite lives in [`../`](../README.md). That one runs on minikube
at 1 GB to 10 GB and reads from a PVC. This one reads and writes object storage, generates
data across the whole cluster, and covers Hudi and Iceberg as well as Parquet. The two share
their table schemas and their query timing code, so the results are directly comparable.

## What it measures

| Phase | What runs | Reported |
|---|---|---|
| datagen | `dsdgen` in parallel across the executors, written as Parquet | rows and wall clock per table |
| load | Hudi bulk insert and Iceberg CTAS from the Parquet dataset | load time per table per format |
| query | 99 TPC-DS queries per engine per format | time per query, speedup, geometric mean |
| merge | repeated upsert rounds into one fact table per engine per format | time per round, speedup, validation |

Every merge round is validated. The row count must grow by exactly the number of inserted
rows, and a sample of updated rows must come back carrying the new values. A round that
fails validation is reported as a failure rather than counted as a fast merge.

## Prerequisites

- A Kubernetes cluster with enough capacity for the executor shape you configure. The
  defaults ask for four executors of 32 cores and 200 GB, which is a reasonable 1 TB shape.
- The [Kubeflow Spark Operator](https://github.com/kubeflow/spark-operator) installed.
- The Quanton Operator installed, unless you only want the OSS baseline. See
  [`../../docs/getting-started.md`](../../docs/getting-started.md).
- Object storage, and a Kubernetes service account that can read and write it. On AWS that
  normally means IRSA; on Google Cloud, Workload Identity.
- A container registry your cluster can pull from.
- About 2 TB of object storage. The Parquet dataset is roughly 300 GB at 1 TB scale, and each
  of Hudi and Iceberg holds another copy, plus the merge targets and staged batches.

## Quick start

```bash
cd benchmarks/tpcds-1tb

# 1. Build and push the benchmark image. Match your nodes' architecture.
docker buildx build --platform linux/amd64 -t <registry>/tpcds-bench:1.0 --push .

# 2. Configure.
cp bench.env.example bench.env
$EDITOR bench.env          # storage URI, namespace, service account, image

# 3. Check the cluster without submitting anything.
./run.sh --phase preflight
./run.sh --dry-run          # render every manifest and read it

# 4. Run it.
./run.sh
```

A full 1 TB run takes many hours. Run the phases separately if you would rather keep control:

```bash
./run.sh --phase datagen
./run.sh --phase load
./run.sh --phase query
./run.sh --phase merge
./run.sh --phase report
```

Narrow the scope while you are still shaking out the configuration:

```bash
./run.sh --phase query --formats parquet --engines oss
```

Or validate the whole pipeline cheaply before committing to 1 TB, by setting
`SCALE_FACTOR=10` and `QUERY_NUMBERS="1,23,67"` in `bench.env`.

## Results

Each job prints its results as JSON between two markers in the driver log. The orchestrator
scrapes that block out with `kubectl logs`, so the machine you launch from never needs
object-storage credentials. Files land in `results/<run-id>/`:

```
results/sf1000-20260101-120000/
├── datagen.json
├── load-hudi.json
├── load-iceberg.json
├── query-oss-parquet.json
├── query-quanton-parquet.json
├── merge-oss-iceberg.json
├── merge-quanton-iceberg.json
└── summary.md
```

`./run.sh --phase report` rebuilds `summary.md` from whatever is present, so a partial run
still produces a readable report.

## Layout

```
benchmarks/tpcds-1tb/
├── README.md
├── Dockerfile              # Spark + dsdgen + Hudi + Iceberg + S3A
├── bench.env.example       # configuration template
├── run.sh                  # phase orchestration
├── k8s/
│   ├── spark-job.yaml      # SparkApplication template, the OSS side
│   └── quanton-job.yaml    # QuantonSparkApplication template, the Quanton side
└── scripts/
    ├── datagen_parallel.py # dsdgen across the cluster, straight to Parquet
    ├── load_tables.py      # Parquet to Hudi or Iceberg
    ├── run_benchmark.py    # the 99 queries, any format, any engine
    ├── merge_benchmark.py  # lake-loader upsert rounds, with validation
    └── report.py           # comparison tables, no Spark needed
```

The scripts are mounted from a ConfigMap rather than baked into the image, so editing one
does not mean rebuilding and pushing. The ConfigMap carries this directory and
[`../scripts/`](../scripts/) together, because the large-scale scripts import the TPC-DS
schemas and the query timing from the original single-node benchmark.

## Keeping the comparison honest

A benchmark is only worth running if the result means something. This suite is built so that
the only difference between the two sides is the engine.

- **Identical submission.** The two manifest templates carry the same script, arguments,
  resources, and volumes. The engine is selected by which CRD the job is submitted as.
- **Identical SQL.** The 99 query files are used byte-for-byte on all three formats. Tables
  are registered as temporary views rather than through a session catalog, so no query is
  rewritten per format.
- **Warm-up.** `QUERY_WARMUP=true` runs one untimed round first, so a cold object-storage
  cache does not decide the outcome. With `QUERY_ROUNDS` above one, the reported time per
  query is the best round.
- **Private merge targets.** Merging changes the table. Each engine merges into its own copy
  of the target, so the second engine does not start from a table the first one already grew.
- **Structurally identical formats.** `PARTITION_FACTS=none` by default, which leaves all
  three formats unpartitioned, so a format comparison is not really a partitioning
  comparison. Set it to `date` when you want a layout closer to production.

Two caveats worth stating plainly. Format numbers are not directly comparable to each other
unless you keep the layout and the file sizes aligned; the comparison this suite is built for
is engine against engine, within one format. And the merge phase reports the merge only. The
batch is staged as Parquet beforehand, untimed, so what you see is merge cost rather than
change-capture cost.

## Tuning notes

- **`DSDGEN_PARALLEL`** controls how many chunks the scaling tables are generated in. About
  four times the total executor core count works well. Each concurrent chunk needs roughly
  `SCALE_FACTOR / DSDGEN_PARALLEL` GB of executor local disk, so raise the parallelism or
  `LOCAL_DIR_SIZE` if generation runs out of space.
- **Hudi upserts** default to the `BLOOM` index. `HUDI_INDEX_TYPE=RECORD_INDEX` is usually
  much faster for upserts into a large table, at the cost of a bigger metadata table.
- **Iceberg** defaults to copy-on-write. `ICEBERG_WRITE_MODE=merge-on-read` shifts the cost
  from the merge to the readers, which is worth measuring if your loader is latency bound.
- **`MERGE_SOURCE_FRACTION`** builds each engine's merge target from a sample of the source.
  Lower it to keep the merge phase short while still merging into a large table.
- **Architecture.** Build the benchmark image for the architecture of your nodes. The Quanton
  engine image is selected by the operator, so it is already correct for your install.

## Troubleshooting

- **Pods stay Pending.** The requested executor shape does not fit. Check
  `kubectl describe pod`, then lower `EXECUTOR_CORES` and `EXECUTOR_MEMORY`, or give the
  cluster room to scale up.
- **Access denied on object storage.** The driver service account is missing permissions.
  `./run.sh --phase preflight` confirms the account exists but cannot confirm what it can
  reach, so test with a small job first.
- **`ClassNotFoundException` for Hudi on the Quanton side.** The Quanton engine image bundles
  the Iceberg runtime but not Hudi. Either allow the pods to reach Maven Central, or set
  `QUANTON_HUDI_JARS` to a Hudi bundle jar in your own object storage.
- **Iceberg class conflicts on the Quanton side.** Keep the classpath narrow. The suite sets
  `extraClassPath` to just the bundled Iceberg jars; adding a second Iceberg copy through
  `spark.jars.packages` alongside them causes a conflict.
- **A phase times out.** `PHASE_TIMEOUT` defaults to 12 hours. The orchestrator still tries
  to collect whatever the driver logged, so run the report phase and see how far it got.
