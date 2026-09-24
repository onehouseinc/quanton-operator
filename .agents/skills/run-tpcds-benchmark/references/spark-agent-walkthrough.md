# Spark Agent walkthrough during the Quanton run

Read this only when the user enabled the Spark Agent in the benchmark configuration and the
Quanton driver pod is `Running`. The source of truth for the agent's behaviour is
`examples/tpcds-agent/README.md` in this repository. If this file and that README disagree,
the README wins. The UI details below describe the agent as documented at the time this file
was written; look at the UI before you assert any of them to the user.

## Reach the UI

Resolve the driver pod name from `kubectl get pods -n default`, then port-forward in the
background and record the pid so you can stop it later:

```bash
pod=$(kubectl get pods -n default --no-headers | awk '/^quanton-tpcds-parquet-driver/ {print $1}')
nohup kubectl port-forward "$pod" 4040:4040 -n default > /tmp/tpcds-agent-pf.log 2>&1 &
echo $! > /tmp/tpcds-agent-pf.pid
sleep 2; head -2 /tmp/tpcds-agent-pf.log
```

Success is a `Forwarding from 127.0.0.1:4040` line. Tell the user the Spark UI is at
http://localhost:4040 and the agent sidebar toggle is in the bottom-right corner. If the log
shows an error, quote it; the usual cause is a stale pod name or a busy local port.

To stop it later:

```bash
kill "$(cat /tmp/tpcds-agent-pf.pid)" 2>/dev/null && echo stopped
```

## What to tell the user, in one breath

- The sidebar answers plain-English questions about the live job, with access to every stage,
  executor, and SQL plan in the driver.
- The user's LLM API key is entered in the Settings tab and stays in the browser. It goes to
  the provider they picked, not through the driver JVM and not through any Onehouse server.
- The Savings tab reports compute waste as percentages, never as currency.
- Answers about Spark behaviour cite public documentation.

## The five tabs

| Tab | What it shows |
|---|---|
| Chat | Streaming conversation grounded in live driver state. `@` mentions stages, executors, and jobs; `/` opens a skill picker. With a Spark History Server configured, a cohort strip above the messages shows matched prior runs. |
| Monitor | Live executor, stage, GC, and shuffle metrics. Refreshes about every 2 seconds while visible. |
| Diagnostics | Auto-detected health alerts such as spill, GC pressure, skew, stragglers, OOM, failed tasks, and shuffle explosion. Each alert has an **Ask Agent** button. A red dot on the tab icon means a CRITICAL alert is firing. |
| Savings | Compute-waste breakdown by category with an impact percentage and a severity tier per finding. The headline is `(total − useful) / total`. Per-category impacts can sum above 100% because waste overlaps; the headline is the number to quote. Confidence reads `Cohort-grounded` with a History Server, otherwise `Live only`. |
| Settings | LLM provider, API key, model, and optional Spark History Server URL. |

## Await-termination banner

Present only when `spark.quanton.agent.await.termination=true` was set. Before job end it
offers **Allow termination**. After job end it shows a countdown with **Extend**, which adds
another timeout chunk to the deadline, and **Allow termination**, which lets the driver exit.
Every Spark UI page keeps its final state for the whole window. The default window is 30
minutes unless `spark.quanton.agent.await.termination.timeout` was set.

## Prompts worth suggesting

- While queries run: `What's my dominant cost driver right now?`
- Any time: `Anything I should worry about — skew, GC, spill?`
- With a History Server configured: `Diff this run against the historical median`

Keep the benchmark wait loop going while the user explores. When the queries finish and the
user is done, stop the port-forward. If await-termination is on, the UI stays reachable for
the configured window and the port-forward can be re-established.
