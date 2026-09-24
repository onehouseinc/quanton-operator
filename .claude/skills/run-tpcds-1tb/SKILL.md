---
name: run-tpcds-1tb
description: Run the TPC-DS 1 TB benchmark suite in benchmarks/tpcds-1tb/ on a dev or staging Kubernetes cluster, comparing OSS Apache Spark against Quanton on Parquet, Hudi, and Iceberg. Generates the dataset into object storage, loads the lakehouse formats, runs the 99 queries, runs lake-loader merge rounds with validation, and reports. Use whenever the user wants to benchmark Quanton at scale, compare engines on a real cluster, generate TPC-DS at scale factor 100 or 1000, or validate query or merge performance beyond minikube.
compatibility: Requires kubectl, docker with buildx, python3, and a kubeconfig context for a cluster with the Spark Operator and the Quanton Operator installed. Needs object storage the driver service account can read and write, and a container registry the cluster can pull from.
allowed-tools: Bash, Read, Write, AskUserQuestion
metadata:
  version: "2"
---

# Run the TPC-DS 1 TB benchmark

You drive the suite in `benchmarks/tpcds-1tb/`. `run.sh` does all the work; you configure it,
start each phase with the user's agreement, read progress back from its log, and present the
report without spin. This is a long job. A full 1 TB run takes many hours and costs real money
in compute and storage. Never start a phase the user has not agreed to.

## How to read this skill

This skill is written for any agent runtime. It names actions, not tools. Map them like this:

| Action | What to do in your runtime |
|---|---|
| **Run** | Execute the command with your shell tool. Read the whole output before you continue. |
| **Read** | Open the file with your file-read tool. |
| **Ask** | Put the question to the user and end your turn. Use a structured-choice tool if you have one, otherwise plain text. |
| **Wait** | Use the bounded loop the step gives you. Set its bound below your runtime's command timeout. Never sleep or poll in your own turns. |

## Ground rules

1. **Report only what a command printed.** Quote the line that supports each claim. If you did not see a value, say so.
2. **One check per claim.** "Installed", "Running", "Completed", and "PASS" each need command output that shows the word.
3. **The fact table below is a plan, not the truth.** If a command contradicts it, trust the command, tell the user, and stop if the difference matters.
4. **Every wait is bounded.** Never run a command that can block without a timeout. Do not use `kubectl logs -f`.
5. **Ask before destructive or costly actions.** Deleting, overwriting, and submitting work to a paid cluster each need a fresh yes. A yes covers one action.
6. **Never print secrets.** This includes `onehouse-values.yaml`, Kubernetes Secret data, and API keys.
7. **Separate environment problems from engine problems**, and name the evidence for the split.
8. **Do not guess names, phases, or numbers.** Get pod names from `kubectl get pods`. Get counts from `grep -c`.

## Facts this skill relies on

| Item | Value |
|---|---|
| Suite directory | `benchmarks/tpcds-1tb/`. Read its `README.md` first. Read `run.sh` when you need to know what a phase does. Never reimplement a phase inline. |
| Config file | `benchmarks/tpcds-1tb/bench.env`, copied from `bench.env.example`. Gitignored. |
| Required config keys | `STORAGE_URI`, `NAMESPACE`, `SERVICE_ACCOUNT`, `BENCH_IMAGE`. `run.sh` refuses to start while any of them is empty or contains `CHANGE-ME`. |
| Phases, in order | `preflight`, `configmaps`, `datagen`, `load`, `query`, `merge`, `report` |
| `run.sh` flags | `--phase <name>`, `--run-id <id>`, `--formats "<list>"`, `--engines "<list>"`, `--config <file>`, `--dry-run` |
| Run id | Defaults to `sf<SF>-<timestamp>` **per invocation**. Pass the same `--run-id` to every phase, or the results land in different directories and the report only sees the last one. |
| Results | `benchmarks/tpcds-1tb/results/<run-id>/`, with `summary.md` written by the report phase |
| Operator CRDs | `sparkapplications.sparkoperator.k8s.io` and `quantonsparkapplications.quantonsparkoperator.onehouse.ai` |
| Merge validation line | `merge <s>s \| rows <n> (expected <n>) \| probe mismatches <n> \| PASS` or `FAIL` |
| Query progress lines | `Running <n> queries, <r> round(s), warmup=<bool>`, then `--- warmup round (not timed) ---`, `--- timed round i/r ---`, and `Total <s>s \| success <n> \| failed <n>` |
| Datagen progress lines | `Generating <table> (parallel=<n>) -> <uri>` then `  <table>: <rows> rows in <s>s` |
| Timeout | `PHASE_TIMEOUT` in `bench.env`, default 43200 seconds |

## Procedure

### Step 1: Confirm the cluster before anything else

Run:

```bash
kubectl config current-context
kubectl get nodes -L node.kubernetes.io/instance-type,kubernetes.io/arch --no-headers | awk '{print $1, $NF, $(NF-1)}' | sort | uniq -c | sort -rn | head
kubectl get crd sparkapplications.sparkoperator.k8s.io quantonsparkapplications.quantonsparkoperator.onehouse.ai 2>&1
ls benchmarks/tpcds-1tb/bench.env 2>&1
```

Ask: "This benchmark will submit multi-hour Spark jobs to the cluster in context `<name>`. Is
that the cluster you mean?" Continue only on a clear yes. Quote the node types and
architectures you saw.

If a CRD line says `NotFound`, name the missing operator and stop. The Spark Operator is always
required. The Quanton Operator is required unless the user only wants the OSS baseline.

### Step 2: Configure `bench.env`

**If the file does not exist**, run `cp benchmarks/tpcds-1tb/bench.env.example benchmarks/tpcds-1tb/bench.env`,
then Ask these in order and write the answers into the file. Change only the keys the user
answers; keep the JSON values in single quotes.

1. **Object storage.** "Which object-storage URI should the benchmark read and write under?"
   Expect `s3a://` or `gs://`. Everything the run produces goes underneath it.
2. **Namespace and service account.** Offer what exists. Run:

```bash
kubectl get sa -A -o json | python3 -c '
import json,sys
for it in json.load(sys.stdin)["items"]:
    ann = it["metadata"].get("annotations", {})
    if any("role-arn" in k or "gcp-service-account" in k for k in ann):
        print(it["metadata"]["namespace"], it["metadata"]["name"])'
```

   Say that the account needs read and write access to the storage URI, and that preflight
   cannot verify that.
3. **Scale factor.** Options `10` (validate the pipeline end to end, well under an hour),
   `100` (every phase, a few hours), `1000` (the 1 TB run). Recommend a first-time user start
   at 10 and rerun at 1000 once the pipeline works. A missing permission discovered eight hours
   into datagen is expensive.
4. **Formats and engines.** Default `parquet hudi iceberg` and `oss quanton`. Parquet alone is
   the fastest way to a first number.
5. **Executor shape.** Show the node types from Step 1 and propose a shape that fits. Defaults:
   `EXECUTOR_INSTANCES=4`, `EXECUTOR_CORES=32`, `EXECUTOR_MEMORY=200g`. State the total core
   and memory ask in one line.

**If the file exists**, read it back in a short table (storage URI, namespace, service account,
image, scale factor, formats, engines, executor shape) and Ask whether to use it as is.

### Step 3: Make sure the image exists

Read `BENCH_IMAGE` from `bench.env`. Run:

```bash
docker manifest inspect "<BENCH_IMAGE>" >/dev/null 2>&1 && echo "image found" || echo "image not found or registry not reachable"
```

If it prints `image found`, continue. Otherwise tell the user exactly that: the check failed,
which means either the image is not there or this machine cannot reach the registry. Ask
whether to build and push it. On yes, with the architecture from Step 1:

```bash
cd benchmarks/tpcds-1tb && docker buildx build --platform linux/<arm64|amd64> -t <BENCH_IMAGE> --push .
```

An `amd64` image does not run on `arm64` nodes. The build compiles `dsdgen` and downloads the
format jars, so it takes several minutes.

### Step 4: Preflight and dry run

Pick a run id now and reuse it for every phase: `RUN_ID=sf<SF>-$(date +%Y%m%d-%H%M)`.
Tell the user the id.

```bash
cd benchmarks/tpcds-1tb && ./run.sh --phase preflight
cd benchmarks/tpcds-1tb && ./run.sh --dry-run --run-id <RUN_ID> 2>&1 | grep -E "serviceAccount|s3a://|gs://|instances:|cores:|memory:" | sort -u
```

Quote the preflight result. From the dry run, show the user the storage URIs, service account,
and executor shape in one short list. Ask for a go before Step 5.

### Step 5: Run each phase in the background and read its log

Before each of `datagen`, `load`, `query`, `merge`, tell the user what it costs and Ask for a
fresh yes. Then start it detached and record the pid:

```bash
cd benchmarks/tpcds-1tb && mkdir -p results && nohup ./run.sh --phase <phase> --run-id <RUN_ID> > results/<RUN_ID>-<phase>.log 2>&1 &
echo $! > benchmarks/tpcds-1tb/results/<RUN_ID>-<phase>.pid
```

Then Wait with this loop. It returns after at most 8 minutes or when the process exits.

```bash
pid=$(cat benchmarks/tpcds-1tb/results/<RUN_ID>-<phase>.pid); log=benchmarks/tpcds-1tb/results/<RUN_ID>-<phase>.log
bound=$((SECONDS + 8*60))
while kill -0 "$pid" 2>/dev/null && [ "$SECONDS" -lt "$bound" ]; do sleep 60; done
kill -0 "$pid" 2>/dev/null && echo "still running" || echo "exited"
tail -n 15 "$log"
```

Between runs of the loop, give the user one or two sentences built from the tail. Use the
progress lines in the fact table. Say which table or query is in flight rather than implying
steady progress; dimension tables finish in seconds and fact tables take the bulk of the time.
If you want a finer view, resolve the driver pod with
`kubectl get pods -n <NAMESPACE> -l benchmark=tpcds --no-headers` and run
`kubectl logs <pod> -n <NAMESPACE> --tail=5`.

Phase notes:

- **datagen.** At 1 TB it writes roughly 300 GB of Parquet and typically runs for a few hours.
  Tables with a committed `_SUCCESS` marker are skipped, so a rerun resumes.
- **load.** Builds the Hudi and Iceberg copies from the Parquet. Skipped when the user chose
  Parquet only.
- **query.** Every requested engine against every requested format, so six jobs at the default
  settings. A query that fails on one engine is excluded from the comparison on both, which
  keeps a failure from flattering either side.
- **merge.** Each engine first builds its own copy of the target, untimed, then runs the
  rounds. Every round prints the validation line from the fact table. A `FAIL` means the merge
  did not produce the expected table state. Surface it at once and never present that round's
  time as a result. A fast merge that lost rows is not a fast merge.

When the process has exited, quote the last `run.sh` line from the log. `run.sh` prints
`<job>: COMPLETED after <n>s` on success and `<job>: FAILED after <n>s` or a `TIMED OUT` line
otherwise. Report the line you saw.

### Step 6: Report

```bash
cd benchmarks/tpcds-1tb && ./run.sh --phase report --run-id <RUN_ID>
```

Read `results/<RUN_ID>/summary.md`. Present the findings yourself, leading with the number
that answers the user's question:

- Total and geometric-mean speedup per format, with the direction stated. If OSS Spark is
  faster on a format, write that.
- How many of the 99 queries each engine won.
- The best and worst queries, both named.
- Mean merge time per format, and whether every round validated.

If a phase did not finish, name the numbers that are missing. Do not present a partial run as
complete.

## Rerunning and cleaning up

Phases are independent and safe to repeat with the same `--run-id`. The expensive artefacts
live in object storage, so a query rerun costs minutes rather than hours.

To clear the Kubernetes objects from a run, after a yes:

```bash
kubectl delete sparkapplication,quantonsparkapplication -n <NAMESPACE> -l benchmark=tpcds
```

That does not touch object storage. Tell the user what is still under `STORAGE_URI` and
roughly how much. Never delete their data without being asked.

## Failure handling

Name the evidence, then the category. Capacity, credentials, image architecture, and registry
access are environment problems.

- **Pods `Pending`.** The executor shape does not fit. Run `kubectl describe pod <pod> -n <NAMESPACE> | tail -20`,
  quote the scheduling event, and propose a smaller `EXECUTOR_CORES` and `EXECUTOR_MEMORY`.
- **Access denied on object storage.** The driver service account cannot reach the bucket.
  Quote the exact path from the log. Preflight cannot catch this; it surfaces at the first write.
- **`ClassNotFoundException` for Hudi on the Quanton side.** The Quanton image bundles Iceberg
  but not Hudi. Either the pods need Maven Central, or set `QUANTON_HUDI_JARS` in `bench.env`
  to a Hudi bundle jar in object storage.
- **Iceberg class conflicts on the Quanton side.** The suite keeps the classpath narrow. If
  `EXTRA_SPARK_CONF` adds Iceberg through `spark.jars.packages`, that second copy is the cause.
- **Executor lost or out of memory during datagen.** Each `dsdgen` chunk needs local disk.
  Raise `DSDGEN_PARALLEL` to make chunks smaller, or raise `LOCAL_DIR_SIZE`.
- **A phase times out.** `PHASE_TIMEOUT` defaults to 12 hours. Run the report phase before
  deciding what to retry; results are collected from whatever the driver logged.
- **`exec format error` in a pod.** The image architecture does not match the nodes. Rebuild
  with the other `--platform`.

## Related skills

- `run-tpcds-benchmark` runs the same comparison on minikube at 1 GB to 10 GB on Parquet only.
- `run-merge-into` is a small `MERGE INTO` correctness demo, useful as a minimal reproduction
  when a merge round fails validation here.
