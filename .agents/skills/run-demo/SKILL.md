---
name: run-demo
description: Run one of the small Quanton correctness demos on minikube, on Hudi or Iceberg. The merge-into demo creates a customers table, inserts 10 rows, merges 3 updates and 3 inserts, and verifies 13 rows with 3 'vip'. The clustering demo writes 100 rows with a nested Struct, Array, and Map schema, forces many tiny files, runs the format's native clustering procedure with spark.quanton.clustering.accelerate=true, and verifies the result. Use whenever the user wants to demo, test, or verify MERGE INTO, upserts, row-level updates, clustering, compaction, file layout optimisation, rewrite_data_files, or run_clustering on Hudi or Iceberg with Quanton.
compatibility: Requires kubectl and helm, a minikube cluster with the Spark Operator and the Quanton Operator installed, and network access to pull the Quanton image and, for Hudi, Maven Central. kubectl and helm need network access from the agent's shell.
allowed-tools: Bash, Read, AskUserQuestion
argument-hint: "[merge-into|clustering] [hudi|iceberg|both]"
metadata:
  version: "3"
---

# Run a Quanton demo on Hudi or Iceberg

Each demo is one self-contained manifest: a ConfigMap with the PySpark script inline, a PVC,
and a `QuantonSparkApplication`. The script does the work and prints its own verdict line.
Your job is to run the chosen demo, report the progress you observed, and quote that verdict.

Follow the working rules in `AGENTS.md`. Helper scripts live in `scripts/agent/`. Shared
failure cases live in `docs/troubleshooting.md`.

## Facts this skill relies on

### merge-into

| Item | Hudi | Iceberg |
|---|---|---|
| Manifest | `examples/merge-into-demo/quanton-hudi-merge-into-demo.yaml` | `examples/merge-into-demo/quanton-iceberg-merge-into-demo.yaml` |
| Script (for reading only) | `examples/merge-into-demo/hudi_merge_into_demo.py` | `examples/merge-into-demo/iceberg_merge_into_demo.py` |
| App name | `quanton-hudi-merge-into-demo` | `quanton-iceberg-merge-into-demo` |
| PVC | `quanton-hudi-merge-into-demo-pvc` | `quanton-iceberg-merge-into-demo-pvc` |
| Log prefix | `hudi-merge` | `iceberg-merge` |
| Verdict line starts with | `[hudi-merge] PASS —` | `[iceberg-merge] PASS —` |
| Table setup | COW table, primaryKey `id`, preCombineField `ts` | Hadoop catalog on the PVC; no Glue or S3 |

### clustering

| Item | Hudi | Iceberg |
|---|---|---|
| Manifest | `examples/clustering-demo/quanton-hudi-clustering-demo.yaml` | `examples/clustering-demo/quanton-iceberg-clustering-demo.yaml` |
| Script (for reading only) | `examples/clustering-demo/hudi_clustering_demo.py` | `examples/clustering-demo/iceberg_clustering_demo.py` |
| App name | `quanton-hudi-clustering-demo` | `quanton-iceberg-clustering-demo` |
| PVC | `quanton-hudi-clustering-demo-pvc` | `quanton-iceberg-clustering-demo-pvc` |
| Procedure the script calls | `run_clustering(order='region,ts', op='scheduleandexecute')` | `rewrite_data_files(strategy='sort', sort_order='region ASC, ts ASC')` |
| Log prefix | `hudi-clustering` | `iceberg-clustering` |
| Verdict line starts with | `[hudi-clustering] PASS —` | `[iceberg-clustering] PASS —` |
| What the verdict asserts | 100 rows preserved and at least one `.replacecommit` on the timeline | 100 rows preserved and the data file count dropped |

Shared facts: every resource lives in namespace `default`. The driver pod is `<app name>-driver`.
The `README.md` in each example directory is the source of truth if a table and the repository
disagree.

## Procedure

### Step 1: Confirm the cluster

Run:

```bash
scripts/agent/check-cluster.sh --require-context minikube --require-charts spark-operator,quanton-operator
```

Continue only if the last line is `result: OK`. On `context check: FAIL`, tell the user which
context is active and stop; these manifests write to a PVC in `default` and are not meant for a
shared cluster. Do not switch context without a yes. On `chart <name>: MISSING`, name the
missing operator, point the user to the `setup-and-run-example` skill, and stop.

### Step 2: Choose the demo and the format

If the user named the demo and the format when they invoked the skill, use those. Otherwise
Ask: "Which demo should I run?" with options **merge-into**, **clustering**. Then Ask: "Which
format?" with options **Hudi**, **Iceberg**, **Both**. For Both, run Hudi first, then Iceberg.
The PVCs are independent.

### Step 3: Run one format

Repeat this step for each chosen format. Substitute the manifest, app name, PVC, and log prefix
from the fact table for the chosen demo.

1. Clean up a prior run:

```bash
kubectl delete -f <manifest> --ignore-not-found
kubectl delete pvc <pvc> -n default --ignore-not-found
```

2. Apply:

```bash
kubectl apply -f <manifest>
```

Tell the user the app name you submitted. The first run pulls the Quanton Spark image (about
3.5 GB) and, for Hudi, downloads `hudi-spark3.5-bundle_2.12:0.15.0` from Maven Central. Expect
a quiet 1 to 3 minutes before the driver reaches `Running`.

3. Wait:

```bash
scripts/agent/wait-for-app.sh --kind quantonsparkapplication --name <app-name> --log-prefix <log-prefix> --max-seconds 480
```

The script prints one status line per 20 seconds and exits 0 at a terminal phase. If it exits 2
with `deadline ... passed`, tell the user the last phase and marker you saw in one line, then
run it again. If the user asks what the job is doing, quote the `marker=` value from the last
status line.

4. Read the verdict:

```bash
scripts/agent/app-verdict.sh --name <app-name> --prefix <log-prefix>
```

Quote the marker lines verbatim. The demo passed only if the script printed `verdict: PASS`.
Otherwise quote the error lines it printed and do not call the demo passed.

Demo-specific notes for the report:

- **merge-into.** Point out the `10 -> 13 rows` transition only if the quoted verdict shows it.
- **clustering.** Point out the file-count or timeline change only from the quoted lines.
  Do not report wall-clock times. The table has 100 rows, so startup dominates and a Hudi
  versus Iceberg timing would mislead.

5. **Clustering only, and only if the user asks whether the native path engaged**, run:

```bash
kubectl logs <app-name>-driver -n default 2>&1 | grep -iE "NativeClusteringGroupWriter|libvelox.so|VeloxBackend|Components registered"
```

Quote whatever prints. Known markers are a `Loaded clustering group writer` line naming
`NativeClusteringGroupWriterImpl` (Hudi native writer) and `libvelox.so has been loaded` or
`Components registered within order` (native engine up). If the grep prints nothing, say the
markers were not found; do not infer either way. `docs/troubleshooting.md` explains why an
`acceleratedStages: 0` line does not mean the native path was off.

### Step 4: Offer cleanup

Ask: "Demo finished. Clean up the resources, or keep them for inspection?" On clean up, run the
two delete commands from Step 3.1 for each format you ran.

## Report

```
<demo> demo on context minikube
  <Format>: phase <Completed|Failed> — <quoted verdict line, or "no PASS line found">
  Cleanup: <done | kept>
```

## Failure handling

Read `docs/troubleshooting.md` for `SIGILL`, an Iceberg `ClassCastException`, a driver stuck
in `Pending`, a slow Hudi start, and a Hudi file count that did not drop after clustering. Name
the evidence, then the category, as the entry describes.
