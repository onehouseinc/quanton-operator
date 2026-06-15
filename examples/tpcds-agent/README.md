# TPC-DS with the Quanton AI Agent

Runs all 99 TPC-DS queries against a configurable scale factor (default 10 GB)
of pre-generated Parquet data on minikube, with the **Quanton AI Agent**
enabled. Once the driver pod is running, the Spark Web UI at
`http://localhost:4040` shows an agent sidebar with five tabs — **Chat,
Monitor, Diagnostics, Savings, Settings** — that can reason about the
live job (stages, tasks, SQL plans, executor metrics), break compute
waste down into actionable categories, and — when a Spark History Server
is reachable — compare the current run against your historical baseline.

## Why this is useful

- **Ask questions in plain English while the job is running.** "What's
  the slowest stage right now?" / "Any data skew in the current SQL?" /
  "What's wasting the most compute?" — the agent has live access to
  every stage, executor, task, and SQL plan in your driver and answers
  grounded in the actual state, not a generic Spark mental model.
- **Your LLM API key stays in your browser.** It travels straight to
  your chosen provider; it does not pass through the driver JVM and
  there is no Onehouse-hosted server in the request path. Exactly one
  outbound call leaves your VPC — the LLM request itself.
- **Cost in percentages, never dollars.** The Savings tab speaks
  percentages of allocated executor-hours and absolute executor-seconds.
  Instance pricing varies 3–4× across spot / reserved / cloud / region;
  the agent shows you waste, not a fictional dollar figure.
- **Answers cite public docs.** Every factual claim about Spark
  behaviour resolves to a public URL. When you've configured a History
  Server, claims about your historical baseline cite the app-id range
  and the metric.
- **Multi-provider.** Bring an Anthropic, OpenAI, or Gemini API key —
  the provider is auto-detected from the key prefix.
- **Keeps working after the job ends.** With one extra config flag, the
  sidebar stays usable past `sc.stop()` so you can come back to a
  finished job and keep asking questions until you click dismiss.

## Prerequisites

- minikube running with at least 12 CPUs / 16 GB RAM allocated
- Spark Operator and Quanton Operator already installed and `Running`
  (see [`../../README.md`](../../README.md) and [`../../benchmarks/README.md`](../../benchmarks/README.md))
- `tpcds-datagen:latest` image built and loaded into minikube
  (one-time, also covered in `benchmarks/README.md`)
- An Anthropic / OpenAI / Gemini API key (you'll paste it into the
  sidebar's Settings tab once the UI is up)

## Run

```bash
# Default — 10 GB scale factor (~10-15 min datagen, ~8-30 min queries)
./run.sh

# Smaller (faster) — 1 GB (~3-5 min datagen, ~2-3 min queries)
SCALE_FACTOR=1 ./run.sh

# Larger — 20 GB (~20-30 min datagen, ~15-25 min queries)
SCALE_FACTOR=20 ./run.sh
```

The script creates the PVC and ConfigMaps, generates the dataset
(skipped if cached for that scale factor), submits the
`quanton-tpcds-agent` QuantonSparkApplication, and prints the
`port-forward` command once the driver is running.

## Enabling the agent on your own jobs

The Quanton image ships the agent. Two `sparkConf` keys turn it on
per `QuantonSparkApplication`:

```yaml
spec:
  sparkApplicationSpec:
    sparkConf:
      spark.plugins: "ai.quanton.spark.agent.SparkAgentPlugin"
      spark.quanton.agent.enabled: "true"
```

If you installed the operator with `--set onehouseConfig.enableAIAgent=true`,
the controller injects these for you and you can leave them out.

### Keep the sidebar alive past job-end (optional)

By default the Spark UI tears down when `SparkContext.stop()` runs. For
post-mortem inspection — coming back to a finished job and continuing to
ask the agent questions — add:

```yaml
spark.quanton.agent.await.termination: "true"
# Optional. Default 30m. Spark duration string: 30s / 15m / 4h / 1d / 0 (indefinite).
spark.quanton.agent.await.termination.timeout: "1h"
```

While await-termination is active, a banner above the sidebar shows a
live countdown with two buttons:

- **Extend** — adds another timeout chunk to the deadline. **Additive,
  not reset** — each click pushes the deadline further by the
  configured timeout. No upper bound.
- **Allow termination** — releases immediately; the driver exits.

Every Spark UI page (Jobs, Stages, SQL, Storage, Environment, Executors)
keeps rendering its frozen final state for the entire window.

## View the agent UI

Once the script reports `Driver is Running`:

```bash
kubectl port-forward quanton-tpcds-agent-driver 4040:4040 -n default
```

Open <http://localhost:4040>. The sidebar toggle appears in the
bottom-right.

In **Settings**:

- Paste an Anthropic / OpenAI / Gemini API key. Keys are stored in your
  browser's `localStorage` only.
- Optionally paste a **Spark History Server URL** reachable from the
  driver pod. With this set, the **Savings** tab moves from `Live only`
  to `Cohort-grounded`, a **cohort strip** above Chat lights up with
  the matched prior runs, and Chat answers can cite historical metrics.

## The five sidebar tabs

| Tab | What it shows |
|---|---|
| **Chat** | Streaming LLM conversation grounded in the live driver state. `@`-mention typeahead for stages / executors / jobs; `/`-slash skill picker. With a History Server configured, a **cohort strip** above the messages shows which prior runs matched the current one and how they matched (operator override, SQL-plan hash, orchestration metadata, or normalised app name). |
| **Monitor** | Live executor + stage + GC + shuffle metrics. Refreshes every ~2 s while the panel is visible; pauses when it's hidden. |
| **Diagnostics** | Auto-detected health alerts — spill, GC pressure, skew, straggler, OOM, failed tasks, shuffle explosion, small / large partitions, record skew, fetch-wait, executor churn. Each alert has an **Ask Agent** button that jumps into Chat with the alert payload pre-loaded. When a CRITICAL alert fires, a red dot appears on the Diagnostics toolbar icon — visible even with the sidebar collapsed. |
| **Savings** | Compute-waste breakdown across documented categories — idle executors, straggler tax, GC overhead, spill, retry overhead, failed-task overhead, skew, speculative waste, over-provisioning, dynamic-allocation churn. The headline is `(total − useful) / total`. Findings carry severity tiers: **critical ≥15 %**, **high 5–15 %**, **medium 3–5 %**, **minor <3 %**. Sub-threshold findings collapse into a "minor opportunities" tail (threshold configurable in browser `localStorage`). Per-category impacts can sum to more than 100 % — that's intentional (overlapping waste; idle + GC + spill can consume the same executor-seconds); the headline is the source of truth. |
| **Settings** | LLM provider / API key / model / Spark History Server URL. |

## What to ask in Chat

A few prompts that exercise the agent's grounding:

- `What stage has the worst data skew right now?`
- `Which executor is GC-thrashing?`
- `What's my dominant cost driver?`  (exercises the Savings tools)
- `Diff this run against the historical median`  (requires a History Server)
- `Why did stage 23 fail?`

## Files

| File | Role |
|---|---|
| `quanton-tpcds-agent.yaml` | The QuantonSparkApplication manifest. The image field is overridden by the operator's `quantonSparkImage` setting; the agent plugin is enabled via two `sparkConf` keys. `${SCALE_FACTOR}` substitution is done by `run.sh`. |
| `run.sh` | End-to-end orchestration: PVC + ConfigMaps + datagen + Quanton submission. Reuses scripts and SQL files from `../../benchmarks/`. |
| `README.md` | This file. |

## Cleanup

```bash
kubectl delete quantonsparkapplication quanton-tpcds-agent -n default
# Keeps the PVC and the cached Parquet data — re-runs are fast.
# To wipe everything:
# kubectl delete pvc tpcds-data -n default
```
