---
name: run-merge-into
description: Run the Hudi or Iceberg MERGE INTO demo on minikube. Creates a customers table, inserts 10 rows, merges 3 updates and 3 inserts, and verifies 13 rows with 3 'vip'. Use whenever the user wants to demo, test, or verify MERGE INTO, upserts, or row-level updates on Hudi or Iceberg with Quanton, or asks for a small merge correctness check.
compatibility: Requires kubectl and helm, a minikube cluster with the Spark Operator and the Quanton Operator installed, and network access to pull the Quanton image and, for Hudi, Maven Central.
allowed-tools: Bash, Read, AskUserQuestion
metadata:
  version: "2"
---

# Run the MERGE INTO demo on Hudi or Iceberg

Each demo is one self-contained manifest: a ConfigMap with the PySpark script inline, a PVC,
and a `QuantonSparkApplication`. The script creates a `customers` table, inserts 10 rows, runs
`MERGE INTO ... USING source_updates s` with 3 updates and 3 inserts, and asserts the result.
Your job is to run the chosen demo, report progress you observed, and quote the script's own
verdict line.

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
| Manifest | `examples/merge-into-demo/quanton-hudi-merge-into-demo.yaml` | `examples/merge-into-demo/quanton-iceberg-merge-into-demo.yaml` |
| Script (for reading only) | `examples/merge-into-demo/hudi_merge_into_demo.py` | `examples/merge-into-demo/iceberg_merge_into_demo.py` |
| App name | `quanton-hudi-merge-into-demo` | `quanton-iceberg-merge-into-demo` |
| PVC | `quanton-hudi-merge-into-demo-pvc` | `quanton-iceberg-merge-into-demo-pvc` |
| Expected driver pod | `quanton-hudi-merge-into-demo-driver` | `quanton-iceberg-merge-into-demo-driver` |
| Log prefix | `[hudi-merge]` | `[iceberg-merge]` |
| Verdict line starts with | `[hudi-merge] PASS —` | `[iceberg-merge] PASS —` |
| Table setup | COW table, primaryKey `id`, preCombineField `ts` | Hadoop catalog on the PVC; no Glue or S3 |

All resources live in namespace `default`. Each README in `examples/merge-into-demo/` is the
source of truth if this table and the repository disagree.

## Procedure

### Step 1: Confirm the cluster

Run:

```bash
kubectl config current-context
helm list -A -o json | python3 -c 'import json,sys; print("\n".join(f"{r[\"name\"]} {r[\"namespace\"]} {r[\"chart\"]}" for r in json.load(sys.stdin) if "spark-operator" in r["chart"] or "quanton-operator" in r["chart"]) or "no operator releases")'
```

The context must be `minikube`. These manifests write to a PVC in `default` and are not meant
for a shared cluster. If it is anything else, tell the user and stop; do not offer to switch on
their behalf without a yes.

Both a `spark-operator` chart and a `quanton-operator` chart must appear. If one is missing,
name it and point the user to the `setup-and-run-example` skill, then stop.

### Step 2: Ask which format

Ask: "Which MERGE INTO demo should I run?" with options **Hudi**, **Iceberg**, **Both**. For
Both, run Hudi first, then Iceberg. The PVCs are independent; running them one after the other
keeps the progress report readable.

### Step 3: Run one format

Repeat this step for each chosen format. Substitute the manifest, app name, and PVC from the
fact table.

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
user asks what the job is doing, run
`kubectl logs <driver-pod> -n default --tail=20` and quote the `[<prefix>]` lines.

4. Read the verdict:

```bash
kubectl logs <driver-pod> -n default 2>&1 | grep -E "^\[(hudi|iceberg)-merge\]|PASS|FAIL"
```

Quote the matching lines verbatim. The line that matters begins with the verdict prefix from
the fact table, for example `[hudi-merge] PASS — 10 -> 13 rows, 3 updated to 'vip'`. Point out
the `10 -> 13 rows` transition only if the quoted line shows it.

If there is no `PASS` line, or the phase was `Failed`, run
`kubectl logs <driver-pod> -n default --tail=50` and quote any `Exception` or `Caused by`
line. Do not call the demo passed.

### Step 4: Offer cleanup

Ask: "Demo finished. Clean up the resources, or keep them for inspection?" On clean up, run the
two delete commands from Step 3.1 for each format you ran.

## Report

```
MERGE INTO demo on context minikube
  <Format>: phase <Completed|Failed> — <quoted verdict line, or "no PASS line found">
  Cleanup: <done | kept>
```

## Failure handling

- **`SIGILL` or `signal 4` in the log.** The native engine in the image does not match the CPU.
  Run `uname -m` and quote the `image:` line from the manifest. Images at
  `release-v0.9.0-al2023` or later carry an aarch64 build for Apple Silicon; earlier images
  carry only a Graviton build. Report both facts; this is an image and hardware match problem.
- **`ClassCastException` mentioning parquet on Iceberg.** Two Iceberg copies are on the
  classpath. The image bundles Iceberg at `/opt/spark/user-jars/`, and the manifest uses
  `extraClassPath`. Check whether `spark.jars.packages` was added to the manifest; that is the
  usual cause.
- **Hudi driver stuck before `Running` for more than 3 minutes.** Run
  `kubectl describe pod <driver-pod> -n default | tail -20`. Image pull or Maven download is the
  usual reason. This is a network or registry problem.
- **`Pending` with `Insufficient cpu` or `Insufficient memory`.** A minikube sizing problem.
