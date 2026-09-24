# AGENTS.md

Instructions for any coding agent that works in this repository. Codex reads this file
directly. Claude Code reads it through the `@AGENTS.md` import in `CLAUDE.md`. Keep project
facts here, not in a vendor-specific file.

## Project Overview

Quanton Operator is a Kubernetes operator (Helm chart) that extends the Kubeflow Spark Operator to run Apache Spark jobs on the Onehouse Quanton compute engine. It introduces the `QuantonSparkApplication` CRD (`apiVersion: quantonsparkoperator.onehouse.ai/v1beta2`) which wraps a standard `SparkApplication` spec under `spec.sparkApplicationSpec`.

The Quanton image bundles an in-driver AI agent surfaced as a Spark UI sidebar with five tabs (Chat, Monitor, Diagnostics, Savings, Settings). It's opt-in per `QuantonSparkApplication` via two `sparkConf` keys (`spark.plugins=ai.quanton.spark.agent.SparkAgentPlugin` + `spark.quanton.agent.enabled=true`), or auto-injected when the operator is installed with `--set onehouseConfig.enableAIAgent=true`. Optional `spark.quanton.agent.await.termination=true` keeps the Spark UI alive past `sc.stop()` for post-mortem inspection. `examples/tpcds-agent/` is the reference example; the `run-tpcds-benchmark` skill has an interactive agent-enable mode.

## Repository Structure

- `charts/quanton-operator-chart/` — Helm chart (Chart v2.0.0, App v1.0.0)
  - `crds/` — QuantonSparkApplication CRD definition
  - `templates/` — Deployment, RBAC, services, mTLS, JWT, OTel, monitoring
  - `values.yaml` — Default configuration
- `docs/` — Getting started, configurations, security, metrics, Airflow integration, troubleshooting
- `examples/` — Sample SparkApplication and QuantonSparkApplication YAMLs
- `scripts/` — Python transform tool (SparkApplication → QuantonSparkApplication)
- `scripts/agent/` — Helper scripts the skills call (cluster check, bounded wait, verdict, skill validation)
- `benchmarks/` — TPC-DS benchmark suite (OSS Spark vs Quanton)
- `.agents/skills/` — Agent skills in the open Agent Skills format; `.claude/skills/` holds symlinks to them
- `.github/workflows/` — Helm chart release to Docker Hub OCI registry; skill validation on pull requests

## Commands

### Tests
```bash
cd scripts && pytest test_transform.py test_agent_scripts.py -v
python3 scripts/agent/validate_skills.py
```

### Transform Tool
```bash
python scripts/transform.py -input <sparkapplication.yaml> -output <output.yaml>
```

### Helm Install
```bash
helm upgrade --install quanton-operator oci://registry-1.docker.io/onehouseai/quanton-operator \
    --namespace quanton-operator --create-namespace -f onehouse-values.yaml
```

### TPC-DS Benchmark
```bash
./benchmarks/run.sh                    # default SF=1
./benchmarks/run.sh --scale-factor 10  # custom scale factor
```

## Code Conventions

- **Shell scripts**: `set -euo pipefail`, helper functions, phase-based execution
- **Python**: `#!/usr/bin/env python3`, type hints, argparse, custom exceptions, pytest for tests
- **YAML/Helm**: `{{ }}` templating, values-driven configuration, no hardcoded values
- **CRD**: `QuantonSparkApplication` (short name: `qsa`), namespaced scope

## Key Patterns

- QuantonSparkApplication nests SparkApplication spec under `spec.sparkApplicationSpec`
- Benchmark jobs use SparkApplication/QuantonSparkApplication CRDs submitted via `kubectl apply`
- PVC (`ReadWriteOnce`) shared across sequential benchmark phases
- Iceberg JARs are pre-packaged in the Quanton image at `/opt/spark/user-jars/`
- CI releases Helm chart to Docker Hub OCI registry via GitHub Actions
- The operator names a job's driver pod `<name>-driver`

## Important Notes

- `onehouse-values.yaml` contains credentials — gitignored, never commit, never print
- Quanton's native engine is compiled for Graviton (SVE2) — does NOT work on Apple Silicon (SIGILL). TPC-DS reads work on arm64 because they don't trigger the native SQL engine
- The `onehouse-spark-catalog.jar` in the Quanton image bundles old fabric8 classes — use narrow classpath (`iceberg-spark-runtime.jar:iceberg-aws-bundle.jar`) to avoid conflicts

## Skills

Skills live in `.agents/skills/<name>/SKILL.md`, in the open Agent Skills format. Each entry in
`.claude/skills/` is a symlink to the matching directory, so Claude Code and Codex load one
copy. Invoke a skill with `/name` in Claude Code or `$name` in Codex, or let the runtime pick
it from its description. `.agents/skills/README.md` holds the authoring conventions.
`docs/troubleshooting.md` holds the failure catalog every skill points to.

Helper scripts in `scripts/agent/` do the repetitive work, so a skill runs them instead of
retyping shell loops:

| Script | Purpose |
|---|---|
| `check-cluster.sh` | Print the kubectl context, nodes, and operator releases. Fail on a missing tool, a wrong context, or a missing chart. |
| `wait-for-app.sh` | Poll one `SparkApplication` or `QuantonSparkApplication` until a terminal state or a deadline, one status line per interval. |
| `app-verdict.sh` | Print the `[prefix]` marker lines from a driver log and say whether a `PASS` line exists. |
| `validate_skills.py` | Check every skill's frontmatter, symlink, and body against the conventions. CI runs it. |

## Working rules for agents

These rules apply to every skill and to any other work that touches a cluster from this
repository. The skills do not repeat them.

### Actions, not tools

Skills name four actions. Map them onto the tools your runtime has.

| Action | What to do |
|---|---|
| **Run** | Execute the command with your shell tool. Read the whole output before you continue. |
| **Read** | Open the file with your file-read tool. |
| **Ask** | Put the question to the user and end your turn. Use a structured-choice tool if you have one, otherwise plain text. |
| **Wait** | Run the bounded command the step gives you, usually `scripts/agent/wait-for-app.sh`. Keep its deadline below your runtime's command timeout. Never sleep or poll in your own turns. |

### Ground rules

1. **Report only what a command printed.** Quote the line that supports each claim. If you did not see a value, say so.
2. **One check per claim.** "Installed", "Running", "Completed", and "PASS" each need command output that shows the word.
3. **A skill's fact table is a plan, not the truth.** If a command contradicts it, trust the command, tell the user, and stop if the difference matters.
4. **Every wait is bounded.** Never run a command that can block without a timeout. Do not use `kubectl logs -f`.
5. **Ask before destructive or costly actions.** Deleting, overwriting, and submitting work to a paid cluster each need a fresh yes. A yes covers one action.
6. **Never print secrets.** This includes `onehouse-values.yaml`, Kubernetes Secret data, and API keys.
7. **Separate environment problems from engine problems**, and name the evidence for the split. `docs/troubleshooting.md` lists the known cases.
8. **Do not guess names, phases, or numbers.** Get pod names from `kubectl get pods`. Get counts from `grep -c`.
9. **Check the kubectl context before the first `kubectl apply` or `helm install`.** Run `scripts/agent/check-cluster.sh`. The demo skills require `minikube`. Anything else needs the user's explicit yes for the named cluster.
