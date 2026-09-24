---
name: run-tpcds-benchmark
description: Run the TPC-DS read benchmark on minikube, comparing OSS Apache Spark against Quanton on Parquet at scale factor 1 or 10, with interactive configuration, live progress from the driver logs, and a per-query comparison table and chart. Optionally enables the in-driver Spark Agent sidebar (Chat, Monitor, Diagnostics, Savings, Settings) on the Quanton run. Use whenever the user wants to benchmark, compare, or demo Quanton versus Spark locally, run TPC-DS on a laptop, or see per-query speedups, even if they do not say "TPC-DS".
compatibility: Requires minikube, kubectl, helm, docker, and python3 with PyYAML. A minikube cluster with the Spark Operator and the Quanton Operator installed. About 14 GB of RAM and 50 GB of disk for scale factor 1. matplotlib is optional for the PNG chart. kubectl, helm, and docker need network access from the agent's shell.
allowed-tools: Bash, Read, Write, AskUserQuestion
metadata:
  version: "3"
---

# Run the TPC-DS benchmark: OSS Spark versus Quanton on minikube

You run the 99 TPC-DS queries on Parquet twice, once on OSS Apache Spark and once on Quanton,
and present the comparison. `benchmarks/run.sh` is the reference implementation. This skill
follows its five phases and diverges only where the user's answers require it: reusing data,
changing the executor shape, and enabling the Spark Agent. Explain each phase in one sentence
before you start it. Describe a result only after the command that proves it has printed.

Follow the working rules in `AGENTS.md`. Helper scripts live in `scripts/agent/`. Shared
failure cases live in `docs/troubleshooting.md`.

Two rules specific to a benchmark:

1. **Never predict the winner.** Do not write "notice this is faster" while a job runs. The
   comparison exists only after both result files are on disk.
2. **Never edit the checked-in manifests.** Patched copies go to a temporary directory.

## Facts this skill relies on

| Item | Value |
|---|---|
| Skill directory | `.agents/skills/run-tpcds-benchmark/` in the repository. `scripts/` and `references/` below are relative to it. |
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
scripts/agent/check-cluster.sh --require-context minikube --require-charts spark-operator,quanton-operator --require-tools minikube,kubectl,helm,docker,python3
python3 -c "import yaml; print('pyyaml ok')"
minikube status 2>&1
```

Continue only if the first command ends with `result: OK`. Every `kubectl apply` in this skill
goes to the current context, and a benchmark submitted to a shared or production cluster is
not recoverable by this skill. On `context check: FAIL`, tell the user which context is active
and stop. Switch only if the user asks you to. On `tool <name>: MISSING` or a missing
`pyyaml ok`, name it and stop. On `chart <name>: MISSING`, point to the
`setup-and-run-example` skill and stop.

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

## The wait command used by Phases 2 to 4

Every Wait in Phases 2 to 4 is:

```bash
scripts/agent/wait-for-app.sh --kind <sparkapplication|quantonsparkapplication> --name <job-name> --progress-regex '<see phase>' --max-seconds 480 --interval 30
```

It prints one status line per 30 seconds with `phase=`, `driver=`, `progress=<matching log
lines>`, and `last=<last log line>`, and exits 0 at a terminal state. If it exits 2 with
`deadline ... passed`, give the user one sentence built from the last status line, then run it
again.

## Phase 2: Data generation

Skip this phase if the user chose to reuse data, and say so.

Write the patched manifest, then apply it. Add `--force-datagen` only if the user asked to
regenerate over existing data.

```bash
tmp=$(mktemp -d)
python3 .agents/skills/run-tpcds-benchmark/scripts/patch_manifest.py \
  --in benchmarks/k8s/datagen-job.yaml --out "$tmp/datagen.yaml" \
  --scale-factor <SF> --executor-instances <2 or 4> [--force-datagen]
kubectl delete sparkapplication tpcds-datagen -n default --ignore-not-found=true
kubectl apply -f "$tmp/datagen.yaml"
```

Show the user the change lines the script printed. Then Wait with
`--kind sparkapplication --name tpcds-datagen --progress-regex 'rows written$'`. Report progress as
"`<progress>` of 24 tables written; last line: `<last>`". `store_sales` is the largest table
and takes the longest.

On `COMPLETED`, run `kubectl logs tpcds-datagen-driver -n default | grep -c "rows written"` and
quote the count.

## Phase 3: OSS Spark baseline

```bash
python3 .agents/skills/run-tpcds-benchmark/scripts/patch_manifest.py \
  --in benchmarks/k8s/oss-spark-tpcds.yaml --out "$tmp/oss.yaml" \
  --scale-factor <SF> --executor-instances <n> --executor-cores <n> --executor-memory <m>
kubectl delete sparkapplication oss-spark-tpcds -n default --ignore-not-found=true
kubectl apply -f "$tmp/oss.yaml"
```

Wait with `--kind sparkapplication --name oss-spark-tpcds --progress-regex '^  q[0-9]+[a-z]?: [0-9.]+s \('`.
Once the log shows `Found <n> queries`, report progress as "`<progress>` of `<n>` queries done
on OSS Spark". Count failures with `grep -cE '^  q[0-9]+[a-z]?: FAILED'` and mention them
without stopping.

On a terminal state, quote the `Total:` line.

## Phase 4: Quanton run

```bash
python3 .agents/skills/run-tpcds-benchmark/scripts/patch_manifest.py \
  --in benchmarks/k8s/quanton-tpcds-parquet.yaml --out "$tmp/quanton.yaml" \
  --scale-factor <SF> --executor-instances <n> --executor-cores <n> --executor-memory <m> \
  [--agent] [--await-termination] [--await-timeout <dur>]
kubectl delete quantonsparkapplication quanton-tpcds-parquet -n default --ignore-not-found=true
kubectl apply -f "$tmp/quanton.yaml"
```

If the operator was installed with `onehouseConfig.enableAIAgent=true`, the controller injects
the same agent keys itself; passing them again is harmless.

Wait with `--kind quantonsparkapplication --name quanton-tpcds-parquet` and the same
`--progress-regex` as Phase 3. Report progress the same way. Do not compare against the OSS
numbers while it runs.

**If the agent is enabled**, as soon as a status line shows `driver=Running`, Read
`references/spark-agent-walkthrough.md` and follow it: port-forward in the background, give
the user the URL, and keep running the wait command between their questions.

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
python3 .agents/skills/run-tpcds-benchmark/scripts/compare_results.py --results-dir benchmarks/results/sf_<SF> --png
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

- **Query failures.** Some TPC-DS queries fail on one engine. The comparison script excludes
  them from both sides and names them. Report them; do not hide them.
- **A job runs past the 2-hour `run.sh` default.** Ask before killing it. If killed, say the
  comparison is partial or absent.
- **`patch_manifest.py` exits non-zero.** Quote its message. The manifest shape changed; Read
  the manifest and stop rather than patching by hand.
- **Driver `Pending` for more than 2 minutes, `ErrImageNeverPull` on the datagen job, or
  `SIGILL` in a Quanton pod.** Read the matching entry in `docs/troubleshooting.md`.

## Related skills

- `setup-and-run-example` installs the operators this skill needs.
