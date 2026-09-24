---
name: run-tpcds-benchmark
description: Run the TPC-DS read benchmark on minikube, comparing OSS Apache Spark against Quanton on Parquet at scale factor 1 or 10, with interactive configuration, live progress from the driver logs, and a per-query comparison table and chart. Optionally enables the in-driver Spark Agent sidebar (Chat, Monitor, Diagnostics, Savings, Settings) on the Quanton run. Use whenever the user wants to benchmark, compare, or demo Quanton versus Spark locally, run TPC-DS on a laptop, or see per-query speedups, even if they do not say "TPC-DS".
compatibility: Requires minikube, kubectl, helm, docker, and python3 with PyYAML. A minikube cluster with the Spark Operator and the Quanton Operator installed. About 14 GB of RAM and 50 GB of disk for scale factor 1. matplotlib is optional for the PNG chart.
allowed-tools: Bash, Read, Write, AskUserQuestion
metadata:
  version: "2"
---

# Run the TPC-DS benchmark: OSS Spark versus Quanton on minikube

You run the 99 TPC-DS queries on Parquet twice, once on OSS Apache Spark and once on Quanton,
and present the comparison. `benchmarks/run.sh` is the reference implementation. This skill
follows its five phases and diverges only where the user's answers require it: reusing data,
changing the executor shape, and enabling the Spark Agent. Explain each phase in one sentence
before you start it. Describe a result only after the command that proves it has printed.

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

Two rules specific to a benchmark:

9. **Never predict the winner.** Do not write "notice this is faster" while a job runs. The
   comparison exists only after both result files are on disk.
10. **Never edit the checked-in manifests.** Patched copies go to a temporary directory.

## Facts this skill relies on

| Item | Value |
|---|---|
| Skill directory | `.claude/skills/run-tpcds-benchmark/` in the repository. `scripts/` and `references/` below are relative to it. |
| Reference implementation | `benchmarks/run.sh`, flags `--scale-factor N`, `--timeout N`, `--force-datagen`. Read it if a step here is unclear. |
| Namespace | `default` for every object |
| PVC | `benchmarks/k8s/pvc.yaml`, name `tpcds-data`, mounted at `/data/tpcds` |
| ConfigMaps | `tpcds-scripts` from `benchmarks/scripts/`, `tpcds-sql-queries` from `benchmarks/sql/tpcds/`, `tpcds-sql-ddl` from `benchmarks/sql/ddl/` |
| Datagen image | `tpcds-datagen:latest`, built from `benchmarks/Dockerfile.datagen` inside minikube's Docker daemon (`eval $(minikube docker-env)`); `imagePullPolicy: Never` |
| Datagen job | `benchmarks/k8s/datagen-job.yaml`, `SparkApplication` named `tpcds-datagen` |
| OSS job | `benchmarks/k8s/oss-spark-tpcds.yaml`, `SparkApplication` named `oss-spark-tpcds` |
| Quanton job | `benchmarks/k8s/quanton-tpcds-parquet.yaml`, `QuantonSparkApplication` named `quanton-tpcds-parquet` |
| Driver pod | `<job-name>-driver`. Confirm with `kubectl get pods -n default`. |
| Job state | `SparkApplication`: `.status.applicationState.state`. `QuantonSparkApplication`: `.status.phase`. Terminal values `COMPLETED`, `FAILED`, `SUBMISSION_FAILED`, in any casing. |
| Checked-in executor shape | 2 instances, 1 core, `3072m` in every manifest |
| Data on the PVC | `/data/tpcds/sf_<SF>/parquet/` and `/data/tpcds/sf_<SF>/results/{oss-spark-parquet,quanton-parquet}.json` |
| Local results | `benchmarks/results/sf_<SF>/`, gitignored |
| Query files | 103 files in `benchmarks/sql/tpcds/` for the 99 queries; four have `a` and `b` variants. Take the total from the driver's `Found N queries` line. |
| Datagen log lines | `Converting <table> -> <path>`, then `  <table>: <n> rows written`; `Skipping <table>: ...` on reuse; `Data generation complete.` at the end. 24 tables. |
| Query log lines | `Found <n> queries in ...`, `Running q<N>...`, `  q<N>: <s>s (<rows> rows)`, `  q<N>: FAILED (<s>s) - <error>`, `Total: <s>s \| Success: <n> \| Failed: <n>` |
| Agent conf keys | `spark.plugins=ai.quanton.spark.agent.SparkAgentPlugin`, `spark.quanton.agent.enabled=true`; optional `spark.quanton.agent.await.termination=true` and `spark.quanton.agent.await.termination.timeout` |
| Agent documentation | `examples/tpcds-agent/README.md` is the source of truth. `references/spark-agent-walkthrough.md` is the short version for this skill. |
| Helper scripts | `scripts/patch_manifest.py` writes patched manifest copies. `scripts/compare_results.py` prints the comparison. Run them; do not retype their logic. |

## Phase 0: Check, then ask

### Step 0.1: Cluster, tools, operators

Run:

```bash
kubectl config current-context
command -v minikube kubectl helm docker python3
python3 -c "import yaml; print('pyyaml ok')"
minikube status 2>&1
helm list -A -o json | python3 -c 'import json,sys; print("\n".join(f"{r[\"name\"]} {r[\"namespace\"]} {r[\"chart\"]}" for r in json.load(sys.stdin) if "spark-operator" in r["chart"] or "quanton-operator" in r["chart"]) or "no operator releases")'
```

The context must be `minikube`. Every `kubectl apply` in this skill goes to the current
context, and a benchmark submitted to a shared or production cluster is not recoverable by
this skill. If the context is anything else, tell the user which context is active and stop.
Switch only if the user asks you to.

If a tool is missing or `pyyaml ok` did not print, name it and stop. If either operator chart
is missing, name it, point to the `setup-and-run-example` skill, and stop.

### Step 0.2: Ask for the scale factor

Ask: "Which TPC-DS scale factor?" with options **1** (about 1 GB, a quick demo) and **10**
(about 10 GB, a more realistic run). Do not offer 100 on a laptop; say a cloud VM is the right
place for it.

### Step 0.3: Decide about the minikube cluster

If `minikube status` showed `host: Running`, run
`kubectl get node minikube -o jsonpath='{.status.capacity.cpu} cpu, {.status.capacity.memory} memory{"\n"}'`
and show the result. Then Ask: "Reuse this cluster, or delete it and create one sized for the
benchmark?" Deleting removes every workload and PVC on it, including any TPC-DS data from a
previous run. Say that in the question. On delete, run `minikube delete` only after the yes.

Sizes for a new cluster:

| Scale factor | Command |
|---|---|
| 1 | `minikube start --cpus 4 --memory 14g --disk-size 50g` |
| 10 | `minikube start --cpus 6 --memory 16g --disk-size 100g` |

After a fresh start, both operators are gone. Point the user to the `setup-and-run-example`
skill and stop; resume here once it reports both operators installed.

### Step 0.4: Check for existing data

Run:

```bash
kubectl get pvc tpcds-data -n default --no-headers 2>/dev/null || echo "no PVC"
kubectl get sparkapplication tpcds-datagen -n default -o jsonpath='{.status.applicationState.state} {.spec.arguments}{"\n"}' 2>/dev/null || echo "no previous datagen job"
```

Data for this scale factor exists only if the state is `COMPLETED` and the arguments contain
`/data/tpcds/sf_<SF>`. Only then Ask: "Reuse the existing scale-factor <SF> data, or regenerate
it?" Otherwise tell the user you will generate it; no question needed.

### Step 0.5: Ask for the executor shape

Show the defaults and Ask whether to keep them or change any value:

| Scale factor | Executors | Cores each | Memory each |
|---|---|---|---|
| 1 | 1 | 1 | `3072m` |
| 10 | 1 | 2 | `6144m` |

The checked-in manifests carry 2 instances, 1 core, `3072m`. Whatever the user picks becomes
flags for `scripts/patch_manifest.py` in Phases 2 to 4. The datagen job uses 4 executors at
scale factor 10 for speed unless the user objects.

### Step 0.6: Ask about the Spark Agent

Ask: "Enable the Spark Agent on the Quanton run? It is an AI sidebar inside the Spark UI with
five tabs: Chat, Monitor, Diagnostics, Savings, Settings. It runs only on the Quanton job, so
the OSS baseline stays untouched." Options: **No** (default), **Yes**.

On yes, Ask two follow-ups:

- "Keep the Spark UI alive after the queries finish, so you can keep asking the agent about
  the run? Default window is 30 minutes." Options: **Yes**, **No**. On yes, Ask for a
  timeout or accept the default.
- "Do you have a Spark History Server URL reachable from the driver? Paste it, or leave blank
  for live-only mode." The user enters it in the sidebar's Settings tab later; you do not
  patch it.

Keep all answers. They become `--agent`, `--await-termination`, and `--await-timeout` flags
in Phase 4.

## Phase 1: Infrastructure

Tell the user you are building the datagen image inside minikube's Docker and creating the PVC
and ConfigMaps. The image build can take several minutes on first run, so start it detached:

```bash
eval "$(minikube docker-env)" && nohup docker build -t tpcds-datagen:latest -f benchmarks/Dockerfile.datagen benchmarks/ > /tmp/tpcds-image-build.log 2>&1 &
echo $! > /tmp/tpcds-image-build.pid
```

Then Wait:

```bash
pid=$(cat /tmp/tpcds-image-build.pid); bound=$((SECONDS + 8*60))
while kill -0 "$pid" 2>/dev/null && [ "$SECONDS" -lt "$bound" ]; do sleep 20; done
kill -0 "$pid" 2>/dev/null && echo "still building" || echo "build exited"
tail -n 5 /tmp/tpcds-image-build.log
```

Success is a `naming to docker.io/library/tpcds-datagen:latest` or `Successfully tagged` line.
Then confirm with `eval "$(minikube docker-env)" && docker image ls tpcds-datagen:latest`.

Then run exactly what `run.sh` runs:

```bash
kubectl apply -f benchmarks/k8s/pvc.yaml
kubectl delete configmap tpcds-scripts tpcds-sql-queries tpcds-sql-ddl -n default --ignore-not-found=true
kubectl create configmap tpcds-scripts --from-file=benchmarks/scripts/ -n default
kubectl create configmap tpcds-sql-queries --from-file=benchmarks/sql/tpcds/ -n default
kubectl create configmap tpcds-sql-ddl --from-file=benchmarks/sql/ddl/ -n default
kubectl get pvc tpcds-data -n default
```

Quote the PVC status line. `Bound` or `Pending` with a `WaitForFirstConsumer` event are both
fine on minikube.

## The wait loop used by Phases 2 to 4

Set `kind`, `app`, and `progress_regex` per phase. The loop returns after at most 8 minutes or
at a terminal state. Between runs, give the user one sentence built from the last line the
loop printed. Run it again until a terminal state.

```bash
kind=<sparkapplication|quantonsparkapplication>; app=<job-name>; ns=default
progress_regex='<see phase>'; bound=$((SECONDS + 8*60))
jp='{.status.applicationState.state}'; [ "$kind" = quantonsparkapplication ] && jp='{.status.phase}'
while [ "$SECONDS" -lt "$bound" ]; do
  st=$(kubectl get "$kind" "$app" -n "$ns" -o jsonpath="$jp" 2>/dev/null || true)
  done_n=$(kubectl logs "${app}-driver" -n "$ns" 2>/dev/null | grep -cE "$progress_regex" || true)
  last=$(kubectl logs "${app}-driver" -n "$ns" --tail=1 2>/dev/null || true)
  echo "$(date +%T) state=${st:-<none>} progress=${done_n} last=${last}"
  case "$(printf '%s' "$st" | tr '[:lower:]' '[:upper:]')" in COMPLETED|FAILED|SUBMISSION_FAILED) break ;; esac
  sleep 30
done
```

## Phase 2: Data generation

Skip this phase if the user chose to reuse data, and say so.

Write the patched manifest, then apply it. Add `--force-datagen` only if the user asked to
regenerate over existing data.

```bash
tmp=$(mktemp -d)
python3 .claude/skills/run-tpcds-benchmark/scripts/patch_manifest.py \
  --in benchmarks/k8s/datagen-job.yaml --out "$tmp/datagen.yaml" \
  --scale-factor <SF> --executor-instances <2 or 4> [--force-datagen]
kubectl delete sparkapplication tpcds-datagen -n default --ignore-not-found=true
kubectl apply -f "$tmp/datagen.yaml"
```

Show the user the change lines the script printed. Then Wait with
`kind=sparkapplication app=tpcds-datagen progress_regex='rows written$'`. Report progress as
"`<progress>` of 24 tables written; last line: `<last>`". `store_sales` is the largest table
and takes the longest.

On `COMPLETED`, run `kubectl logs tpcds-datagen-driver -n default | grep -c "rows written"` and
quote the count.

## Phase 3: OSS Spark baseline

```bash
python3 .claude/skills/run-tpcds-benchmark/scripts/patch_manifest.py \
  --in benchmarks/k8s/oss-spark-tpcds.yaml --out "$tmp/oss.yaml" \
  --scale-factor <SF> --executor-instances <n> --executor-cores <n> --executor-memory <m>
kubectl delete sparkapplication oss-spark-tpcds -n default --ignore-not-found=true
kubectl apply -f "$tmp/oss.yaml"
```

Wait with `kind=sparkapplication app=oss-spark-tpcds progress_regex='^  q[0-9]+[a-z]?: [0-9.]+s \('`.
Once the log shows `Found <n> queries`, report progress as "`<progress>` of `<n>` queries done
on OSS Spark". Count failures with `grep -cE '^  q[0-9]+[a-z]?: FAILED'` and mention them
without stopping.

On a terminal state, quote the `Total:` line.

## Phase 4: Quanton run

```bash
python3 .claude/skills/run-tpcds-benchmark/scripts/patch_manifest.py \
  --in benchmarks/k8s/quanton-tpcds-parquet.yaml --out "$tmp/quanton.yaml" \
  --scale-factor <SF> --executor-instances <n> --executor-cores <n> --executor-memory <m> \
  [--agent] [--await-termination] [--await-timeout <dur>]
kubectl delete quantonsparkapplication quanton-tpcds-parquet -n default --ignore-not-found=true
kubectl apply -f "$tmp/quanton.yaml"
```

If the operator was installed with `onehouseConfig.enableAIAgent=true`, the controller injects
the same agent keys itself; passing them again is harmless.

Wait with `kind=quantonsparkapplication app=quanton-tpcds-parquet` and the same
`progress_regex` as Phase 3. Report progress the same way. Do not compare against the OSS
numbers while it runs.

**If the agent is enabled**, as soon as the loop shows the driver pod `Running`, Read
`references/spark-agent-walkthrough.md` and follow it: port-forward in the background, give
the user the URL, and keep the wait loop going between their questions.

On a terminal state, quote the `Total:` line. Stop the port-forward if you started one and
await-termination is off.

## Phase 5: Results

Copy both result files off the PVC exactly as `run.sh` does:

```bash
kubectl delete pod tpcds-results-copier -n default --ignore-not-found=true
kubectl run tpcds-results-copier --image=busybox --restart=Never -n default \
  --overrides='{"spec":{"containers":[{"name":"copier","image":"busybox","command":["sleep","300"],"volumeMounts":[{"name":"data","mountPath":"/data/tpcds"}]}],"volumes":[{"name":"data","persistentVolumeClaim":{"claimName":"tpcds-data"}}]}}'
kubectl wait --for=condition=Ready pod/tpcds-results-copier -n default --timeout=60s
mkdir -p benchmarks/results/sf_<SF>
for f in oss-spark-parquet.json quanton-parquet.json; do
  kubectl cp "tpcds-results-copier:/data/tpcds/sf_<SF>/results/$f" "benchmarks/results/sf_<SF>/$f" || echo "could not copy $f"
done
kubectl delete pod tpcds-results-copier -n default --ignore-not-found=true
ls -l benchmarks/results/sf_<SF>/
```

Both files must be listed. If one is missing, say which, and do not produce a comparison.

Then run the comparison. It prints the table, the summary, and the log-scale chart, and
writes `comparison.png` when matplotlib is installed:

```bash
python3 .claude/skills/run-tpcds-benchmark/scripts/compare_results.py --results-dir benchmarks/results/sf_<SF> --png
```

Show the user the script's output as printed. If a PNG was written, open it with your
file-read tool so the user can see it. If the script printed `PNG skipped`, say so.

## Report

Build every number from the `Summary` block the comparison script printed. Do not round
differently, and do not drop a line because it favours OSS Spark.

```
TPC-DS benchmark on minikube, scale factor <SF>

  Queries compared:        <n> (<excluded list, or "none excluded">)
  OSS Spark total:         <s> s
  Quanton total:           <s> s
  Total-time speedup:      <x> (<engine> faster overall)
  Geometric-mean speedup:  <x>
  Quanton faster on:       <n> of <n>   OSS Spark faster on: <n> of <n>
  Best / worst for Quanton: <q> at <x> / <q> at <x>

  Results: benchmarks/results/sf_<SF>/   Chart: <path or "not produced">
```

Then two or three sentences in your own words. Same Spark job, no code changes, and whatever
the numbers say. If the executor shape differed from the defaults, state it, because it affects
how the numbers compare with the reference runs in `benchmarks/data/`.

## Failure handling

Name the evidence, then the category.

- **Driver `Pending` for more than 2 minutes.** Run `kubectl describe pod <pod> -n default | tail -20`.
  `Insufficient cpu` or `Insufficient memory` means the executor shape or the minikube size is
  too small. `ErrImageNeverPull` on the datagen job means the image was built against a
  different Docker daemon; rebuild after `eval "$(minikube docker-env)"`. A PVC still attached
  to a previous pod resolves itself once that pod is gone. All of these are environment
  problems.
- **Query failures.** Some TPC-DS queries fail on one engine. The comparison script excludes
  them from both sides and names them. Report them; do not hide them.
- **`SIGILL` or `signal 4` in a Quanton pod.** The native engine in the image does not match
  the CPU. Run `uname -m` and quote the image the operator injected
  (`kubectl get pod <pod> -n default -o jsonpath='{.spec.containers[0].image}'`). Images at
  `release-v0.9.0-al2023` or later carry an aarch64 build; earlier ones carry only a Graviton
  build. Report both facts. This is an image and hardware match problem.
- **A job runs past the 2-hour `run.sh` default.** Ask before killing it. If killed, say the
  comparison is partial or absent.
- **`patch_manifest.py` exits non-zero.** Quote its message. The manifest shape changed; Read
  the manifest and stop rather than patching by hand.

## Related skills

- `setup-and-run-example` installs the operators this skill needs.
