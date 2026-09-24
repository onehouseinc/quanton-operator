---
name: setup-and-run-example
description: Set up a local minikube cluster, install the Kubeflow Spark Operator and the Quanton Operator, and run the SparkPi example as a QuantonSparkApplication to prove the stack works. Use whenever the user wants to get started with Quanton locally, install the operators, run a first Quanton job, or check that their minikube setup works, even if they do not say "example".
compatibility: Requires minikube, kubectl, helm, and a running Docker daemon. Needs onehouse-values.yaml at the repository root to install the Quanton Operator. Network access to kubeflow.github.io and registry-1.docker.io from the agent's shell.
allowed-tools: Bash, Read, AskUserQuestion
metadata:
  version: "3"
---

# Set up minikube and run the Quanton example job

You walk the user from an empty laptop to a completed Quanton Spark job on minikube. Each step
checks what already exists before it installs anything. Explain each step in one sentence
before you run it. Describe an outcome only after the command that proves it has printed.

Follow the working rules in `AGENTS.md`. Helper scripts live in `scripts/agent/`. Shared
failure cases live in `docs/troubleshooting.md`.

## Facts this skill relies on

| Item | Value |
|---|---|
| Canonical guide | `docs/getting-started.md`. Read it first. If it disagrees with this table, follow the guide and tell the user. |
| Example manifest | `examples/quanton-application.yaml` |
| Example app name and namespace | `quanton-spark-pi-java-example` in `default` |
| Expected driver pod | `quanton-spark-pi-java-example-driver`. Confirm with `kubectl get pods`. |
| Spark Operator chart | `spark-operator/spark-operator` version `2.5.0` from `https://kubeflow.github.io/spark-operator`, namespace `spark-operator`, job namespace `default` |
| Quanton Operator chart | `oci://registry-1.docker.io/onehouseai/quanton-operator`, namespace `quanton-operator`, job namespace `default` |
| Credentials file | `onehouse-values.yaml` at the repository root. Gitignored. The user downloads it from the Onehouse console. Never print it. |
| Success marker in the driver log | a line containing `Pi is roughly` |

## Procedure

### Step 1: Check tools

Run:

```bash
scripts/agent/check-cluster.sh --require-tools minikube,kubectl,helm,docker
docker info --format '{{.ServerVersion}}' 2>&1 | head -1
```

The first command lists each tool as `present` or `MISSING` and exits 1 on a missing tool; the
context and release lines it prints are informational at this point. If `docker info` prints an
error, Docker is not running. If anything is missing, list it, give the install hint
(`brew install minikube` or `brew install helm` on macOS, start Docker Desktop), and stop.

### Step 2: Start or reuse minikube, then pin the context

Run:

```bash
minikube status 2>&1
kubectl config current-context 2>&1
```

- If `minikube status` shows `host: Running` and `kubelet: Running`, the cluster is up.
- Otherwise run `minikube start` and Wait for it to finish, then run `minikube status` again.

The context check is the important part. Everything after this step uses `kubectl apply`
and `helm install`, and those go to whatever context is current. If the context is not
`minikube`, Ask: "Your kubectl context is `<name>`, not `minikube`. Switch to minikube?"
On yes, run `kubectl config use-context minikube`. On no, stop. Never install into a
non-minikube context from this skill.

Then run `kubectl get nodes` and quote the `VERSION` column to the user.

### Step 3: See which operators are installed

Run:

```bash
scripts/agent/check-cluster.sh --require-context minikube
```

Read the `operator releases:` block. Each line is `<release> <namespace> <chart> <status>`.
Quote the chart version of each operator that is present. Steps 4 and 5 install the ones that
are absent.

### Step 4: Spark Operator

Skip this step if Step 3 listed a `spark-operator-*` chart. Otherwise Ask whether to install
it. On yes, run:

```bash
helm repo add spark-operator https://kubeflow.github.io/spark-operator
helm repo update
helm install spark-operator spark-operator/spark-operator \
  --namespace spark-operator --create-namespace \
  --version 2.5.0 \
  --set "spark.jobNamespaces={default}"
kubectl wait --for=condition=Ready pods --all -n spark-operator --timeout=180s
kubectl get pods -n spark-operator
```

Success is the `kubectl wait` line ending in `condition met` and every pod in `Running`. If
`kubectl wait` times out, show `kubectl get pods -n spark-operator` and
`kubectl describe pod <pod> -n spark-operator | tail -20`, then stop.

### Step 5: Quanton Operator

Skip this step if Step 3 listed a `quanton-operator-*` chart. Otherwise run
`ls onehouse-values.yaml`. If the file is missing, Ask the user to download it from the
Onehouse console and place it at the repository root, then stop. Do not `cat` the file at any
point. When it exists, run:

```bash
helm upgrade --install quanton-operator oci://registry-1.docker.io/onehouseai/quanton-operator \
  --namespace quanton-operator --create-namespace \
  --set "quantonOperator.jobNamespaces={default}" \
  -f onehouse-values.yaml
kubectl wait --for=condition=Ready pods --all -n quanton-operator --timeout=240s
kubectl get pods -n quanton-operator
```

Success is the same as Step 4. If the Helm output contains `401`, `unauthorized`, or
`invalid`, the credentials file is wrong; say so without quoting its contents.

### Step 6: Run the example job

Tell the user you are submitting SparkPi as a `QuantonSparkApplication`. Run:

```bash
kubectl delete -f examples/quanton-application.yaml --ignore-not-found=true
kubectl apply -f examples/quanton-application.yaml
```

Then Wait:

```bash
scripts/agent/wait-for-app.sh --kind quantonsparkapplication --name quanton-spark-pi-java-example --max-seconds 480
```

The script prints one status line per 20 seconds and exits 0 at a terminal phase. If it exits 2
with `deadline ... passed`, tell the user the last phase and driver status you saw, then run
it again. Do not report a phase you did not see.

When the phase is `Completed` (any casing), run:

```bash
kubectl logs quanton-spark-pi-java-example-driver -n default | grep -i "pi is roughly"
```

Quote that line. That is the result. If the grep prints nothing, show the last 30 lines of the
log and say the marker was not found.

## Report

Fill every field from command output in this session:

```
Setup complete on context <ctx>

  Minikube:         <status line from minikube status>
  Spark Operator:   <chart version from the operator releases block | installed in this session: <version>>
  Quanton Operator: <chart version from the operator releases block | installed in this session: <version>>
  Example job:      <phase> — <quoted "Pi is roughly ..." line>

Next steps:
  - Submit your own job:    kubectl apply -f <your-job.yaml>
  - Run the benchmark:      the run-tpcds-benchmark skill
  - Spark UI while a job runs: kubectl port-forward <driver-pod> 4040:4040 -n default
```

## Failure handling

Name the evidence, then the category.

- **Phase `Failed`.** Run `kubectl logs <driver-pod> -n default --tail=50` and
  `kubectl describe quantonsparkapplication <app> -n default | tail -20`. Quote the first
  `Exception` or `Error` line.
- **`onehouse-values.yaml` missing or rejected.** A user configuration problem. Point at the
  Onehouse console. Never quote the file.
- **Driver stuck in `Pending`, `SIGILL` in a log, or a Helm `401`.** Read
  `docs/troubleshooting.md` and follow the matching entry.
