---
name: setup-and-run-example
description: Set up a local minikube cluster, install the Kubeflow Spark Operator and the Quanton Operator, and run the SparkPi example as a QuantonSparkApplication to prove the stack works. Use whenever the user wants to get started with Quanton locally, install the operators, run a first Quanton job, or check that their minikube setup works, even if they do not say "example".
compatibility: Requires minikube, kubectl, helm, and a running Docker daemon. Needs onehouse-values.yaml at the repository root to install the Quanton Operator. Network access to kubeflow.github.io and registry-1.docker.io.
allowed-tools: Bash, Read, AskUserQuestion
metadata:
  version: "2"
---

# Set up minikube and run the Quanton example job

You walk the user from an empty laptop to a completed Quanton Spark job on minikube. Each step
checks what already exists before it installs anything. Explain each step in one sentence
before you run it. Describe an outcome only after the command that proves it has printed.

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
command -v minikube kubectl helm docker
docker info --format '{{.ServerVersion}}' 2>&1 | head -1
```

Report each tool as present or missing from the first command's output. If `docker info` prints
an error, Docker is not running. If anything is missing, list it, give the install hint
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

### Step 3: Spark Operator

Run:

```bash
helm list -A -o json | python3 -c 'import json,sys; rows=[r for r in json.load(sys.stdin) if "spark-operator" in r["chart"] and "quanton" not in r["chart"]]; print("\n".join(f"{r[\"name\"]} {r[\"namespace\"]} {r[\"chart\"]} {r[\"status\"]}" for r in rows) or "not installed")'
```

- If a line is printed, quote the chart version and continue.
- If it prints `not installed`, Ask whether to install it. On yes, run:

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

### Step 4: Quanton Operator

Run the same `helm list` command with `"quanton-operator" in r["chart"]` as the filter.

- If installed, quote the chart version and continue.
- If not installed, run `ls onehouse-values.yaml`. If the file is missing, Ask the user to
  download it from the Onehouse console and place it at the repository root, then stop. Do
  not `cat` the file at any point. When it exists, run:

```bash
helm upgrade --install quanton-operator oci://registry-1.docker.io/onehouseai/quanton-operator \
  --namespace quanton-operator --create-namespace \
  --set "quantonOperator.jobNamespaces={default}" \
  -f onehouse-values.yaml
kubectl wait --for=condition=Ready pods --all -n quanton-operator --timeout=240s
kubectl get pods -n quanton-operator
```

Success is the same as Step 3. If the Helm output contains `401`, `unauthorized`, or
`invalid`, the credentials file is wrong; say so without quoting its contents.

### Step 5: Run the example job

Tell the user you are submitting SparkPi as a `QuantonSparkApplication`. Run:

```bash
kubectl delete -f examples/quanton-application.yaml --ignore-not-found=true
kubectl apply -f examples/quanton-application.yaml
```

Then Wait with this loop. It returns after at most 8 minutes or at a terminal phase. If it
returns without a terminal phase, tell the user the last phase you saw and run it again.

```bash
app=quanton-spark-pi-java-example; ns=default; bound=$((SECONDS + 8*60))
while [ "$SECONDS" -lt "$bound" ]; do
  phase=$(kubectl get quantonsparkapplication "$app" -n "$ns" -o jsonpath='{.status.phase}' 2>/dev/null || true)
  pod=$(kubectl get pods -n "$ns" --no-headers 2>/dev/null | grep "^${app}-driver" | awk '{print $1" "$3}')
  echo "$(date +%T) phase=${phase:-<none>} driver=${pod:-<none>}"
  case "$(printf '%s' "$phase" | tr '[:lower:]' '[:upper:]')" in COMPLETED|FAILED) break ;; esac
  sleep 15
done
```

Between runs of the loop, tell the user the phase and the driver pod status you saw. Do not
report a phase you did not see.

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
  Spark Operator:   <chart version from helm list | already installed: <version>>
  Quanton Operator: <chart version from helm list | already installed: <version>>
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
- **Driver stays `Pending` for more than 2 minutes.** Run `kubectl describe pod <pod> -n default | tail -20`.
  `Insufficient cpu`, `Insufficient memory`, `ImagePullBackOff`, and `ErrImagePull` are minikube
  or registry problems, not Quanton problems. Say which one you saw.
- **`SIGILL` or `signal 4` in the driver or executor log.** The native engine in the image does
  not match the CPU. Run `uname -m` and quote the `image:` line from the manifest. Images at
  `release-v0.9.0-al2023` or later carry an aarch64 build; older ones carry only a Graviton
  build. Report the two facts and let the user pick an image. This is an image and hardware
  match problem.
- **`onehouse-values.yaml` missing or rejected.** A user configuration problem. Point at the
  Onehouse console. Never quote the file.
