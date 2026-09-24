---
name: run-clustering
description: Run the Hudi or Iceberg clustering demo on minikube. Writes a 100-row table with a nested Struct, Array, and Map schema, forces many tiny files, runs the format's native clustering procedure with spark.quanton.clustering.accelerate=true, and verifies the result. Use whenever the user wants to demo, test, or verify clustering, compaction, file layout optimisation, rewrite_data_files, or run_clustering on Hudi or Iceberg with Quanton.
compatibility: Requires kubectl and helm, a minikube cluster with the Spark Operator and the Quanton Operator installed, and network access to pull the Quanton image and, for Hudi, Maven Central.
allowed-tools: Bash, Read, AskUserQuestion
metadata:
  version: "2"
---

# Run the clustering demo on Hudi or Iceberg

Each demo is one self-contained manifest: a ConfigMap with the PySpark script inline, a PVC,
and a `QuantonSparkApplication`. The script writes 100 rows with a complex schema, forces a
many-tiny-files layout, then calls the format's native clustering procedure with
`spark.quanton.clustering.accelerate=true` and checks the outcome. Your job is to run the
chosen demo, report progress you observed, and quote the script's own verdict line.

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

| Item | Hudi | Iceberg |
|---|---|---|
| Manifest | `examples/clustering-demo/quanton-hudi-clustering-demo.yaml` | `examples/clustering-demo/quanton-iceberg-clustering-demo.yaml` |
| Script (for reading only) | `examples/clustering-demo/hudi_clustering_demo.py` | `examples/clustering-demo/iceberg_clustering_demo.py` |
| App name | `quanton-hudi-clustering-demo` | `quanton-iceberg-clustering-demo` |
| PVC | `quanton-hudi-clustering-demo-pvc` | `quanton-iceberg-clustering-demo-pvc` |
| Expected driver pod | `quanton-hudi-clustering-demo-driver` | `quanton-iceberg-clustering-demo-driver` |
| Procedure the script calls | `run_clustering(order='region,ts', op='scheduleandexecute')` | `rewrite_data_files(strategy='sort', sort_order='region ASC, ts ASC')` |
| Log prefix | `[hudi-clustering]` | `[iceberg-clustering]` |
| Verdict line starts with | `[hudi-clustering] PASS —` | `[iceberg-clustering] PASS —` |
| What the verdict asserts | 100 rows preserved and at least one `.replacecommit` on the timeline | 100 rows preserved and the data file count dropped |

All resources live in namespace `default`. `examples/clustering-demo/README.md` is the source
of truth if this table and the repository disagree.

## Procedure

### Step 1: Confirm the cluster

Run:

```bash
kubectl config current-context
helm list -A -o json | python3 -c 'import json,sys; print("\n".join(f"{r[\"name\"]} {r[\"namespace\"]} {r[\"chart\"]}" for r in json.load(sys.stdin) if "spark-operator" in r["chart"] or "quanton-operator" in r["chart"]) or "no operator releases")'
```

The context must be `minikube`. These manifests write to a PVC in `default` and are not meant
for a shared cluster. If it is anything else, tell the user and stop.

Both a `spark-operator` chart and a `quanton-operator` chart must appear. If one is missing,
name it, point the user to the `setup-and-run-example` skill, and stop.

### Step 2: Ask which format

Ask: "Which clustering demo should I run?" with options **Hudi**, **Iceberg**, **Both**. For
Both, run Hudi first, then Iceberg.

### Step 3: Run one format

Repeat for each chosen format, substituting values from the fact table.

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
3.5 GB) and, for Hudi, downloads `hudi-spark3.5-bundle_2.12:0.15.0` from Maven Central.
Expect a quiet 1 to 3 minutes before the driver reaches `Running`.

3. Wait with this loop. It returns after at most 8 minutes or at a terminal phase. If it
   returns without one, report the last phase you saw and run it again.

```bash
app=<app-name>; ns=default; bound=$((SECONDS + 8*60))
while [ "$SECONDS" -lt "$bound" ]; do
  phase=$(kubectl get quantonsparkapplication "$app" -n "$ns" -o jsonpath='{.status.phase}' 2>/dev/null || true)
  pod=$(kubectl get pods -n "$ns" --no-headers 2>/dev/null | grep "^${app}-driver" | awk '{print $1" "$3}')
  echo "$(date +%T) phase=${phase:-<none>} driver=${pod:-<none>}"
  case "$(printf '%s' "$phase" | tr '[:lower:]' '[:upper:]')" in COMPLETED|FAILED) break ;; esac
  sleep 20
done
```

Between loop runs, tell the user the phase and driver status you saw, in one line. If the
user asks for detail, run `kubectl logs <driver-pod> -n default --tail=20` and quote the
`[<prefix>]` lines.

4. Read the verdict:

```bash
kubectl logs <driver-pod> -n default 2>&1 | grep -E "^\[(hudi|iceberg)-clustering\]|PASS|FAIL"
```

Quote the matching lines verbatim. Point out the file-count or timeline change only from the
quoted lines. **Do not report wall-clock times.** The table has 100 rows, so startup dominates
and a Hudi versus Iceberg timing would mislead.

If there is no `PASS` line, or the phase was `Failed`, run
`kubectl logs <driver-pod> -n default --tail=50` and quote any `Exception` or `Caused by`
line. Do not call the demo passed.

5. Only if the user asks whether the native path engaged, run:

```bash
kubectl logs <driver-pod> -n default 2>&1 | grep -iE "NativeClusteringGroupWriter|libvelox.so|VeloxBackend|Components registered"
```

Quote whatever prints. Known markers are a `Loaded clustering group writer` line naming
`NativeClusteringGroupWriterImpl` (Hudi native writer) and `libvelox.so has been loaded` or
`Components registered within order` (native engine up). If the grep prints nothing, say the
markers were not found; do not infer either way. Note also that an `acceleratedStages: 0`
line from `QuantonAccelerationTracker` counts accelerated Spark query stages only, not the
clustering procedure, so it does not mean acceleration was off.

### Step 4: Offer cleanup

Ask: "Demo finished. Clean up the resources, or keep them for inspection?" On clean up, run the
two delete commands from Step 3.1 for each format you ran.

## Report

```
Clustering demo on context minikube
  <Format>: phase <Completed|Failed> — <quoted verdict line, or "no PASS line found">
  Cleanup: <done | kept>
```

## Failure handling

- **`SIGILL` or `signal 4` in the log.** The native engine in the image does not match the CPU.
  Run `uname -m` and quote the `image:` line from the manifest. Images at
  `release-v0.9.0-al2023` or later carry an aarch64 build for Apple Silicon; earlier images
  carry only a Graviton build. Report both facts; this is an image and hardware match problem.
- **`ClassCastException` mentioning parquet on Iceberg.** Two Iceberg copies are on the
  classpath. The image bundles Iceberg at `/opt/spark/user-jars/` and the manifest uses
  `extraClassPath`. Check whether `spark.jars.packages` was added; that is the usual cause.
- **Hudi file count did not drop.** Expected. Hudi tombstones old files through the
  `.hoodie` timeline with a `.replacecommit` instead of deleting them. The verdict asserts on
  the timeline, not the file count.
- **Driver stuck before `Running` for more than 3 minutes.** Run
  `kubectl describe pod <driver-pod> -n default | tail -20`. Image pull or Maven download is the
  usual reason. This is a network or registry problem.
