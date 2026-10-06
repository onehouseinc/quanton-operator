---
name: spark-ui-profiler
description: Find out why a Spark job is slow from its Spark UI / History Server. Builds a wall-clock timeline (jobs, stages, driver gaps), the jobs -> stages -> SQL mapping with the operator split per execution, and a per-stage profile (task durations vs bytes per task, CPU share, spill, GC, fetch wait, slowest tasks per executor) with the flags SKEW, TOO_BIG, TOO_SMALL, GC, IDLE, LOW_CPU and FETCH_WAIT. Writes an HTML report that explains what the numbers show, the possible bottlenecks, and config plus code changes to try. Use when the user shares a Spark History Server or Spark UI URL, an application id, or asks "why is my Spark job slow".
---

# Spark UI profiler

Read a slow Spark application the way an experienced data engineer reads the Spark UI, and
write it up so another engineer can check every claim in the UI. The bundled script collects
the facts from the Spark REST API; you reason over them and write the analysis; the script
renders one self-contained HTML page with your analysis above the measured tables.

## Inputs

Ask for whichever is missing:

1. A Spark UI base URL: a History Server (`http://localhost:18080`, or a server behind a
   path prefix such as `http://localhost:8080/spark/history-server`) or a live driver UI
   (`http://localhost:4040`). If the server sits in Kubernetes, suggest
   `kubectl port-forward svc/<history-server-svc> 18080:18080 -n <ns>`.
2. The application id (`spark-…` or `app-…`). Optional for a live UI (the first app is used).
3. Optional: the job's code or SQL, and the instance type of the executors (vCPU, memory,
   local disk, network). Code lets you quote the line a fix changes.

## Step 1: Collect the facts

```bash
python3 <skill-dir>/profile_spark_app.py --url <base-url> --app <app-id> --json facts.json
```

Standard library only, Spark 3.x REST API (`/api/v1`), a few seconds per application. Read
`facts.json` in full before writing anything. It holds:

| Key | What it is | Spark UI tab it mirrors |
|---|---|---|
| `app`, `cluster` | duration, Spark version, executors × cores = task slots, memory per executor and per task slot, dynamic allocation, native engine | Environment, Executors |
| `timeline` | time with at least one stage running vs time with none (driver work), and the longest gaps with the stage before and after | Jobs → Event Timeline |
| `jobs` | each job with its description, duration, start offset, the stages it ran and skipped, and the SQL execution it belongs to | Jobs |
| `stages` | every stage that ran, longest active time first (`wall_ms` runs from the first task launched to completion; `queued_ms` is the time the stage waited for a slot or for executors after submission, which the Stages tab's Duration includes). The top ones (`profiled: true`) carry task-duration, bytes-per-task and peak-execution-memory-per-task quantiles (min / p25 / median / p75 / max), CPU share, spill, GC, fetch wait, scheduler delay, per-executor balance, the operators that ran in the stage, `signals`, and `slowest_tasks` (task id, executor, host, duration, bytes, spill, GC, locality) for the three longest stages and every flagged stage | Stages → stage page |
| `sql` | every SQL execution with its jobs and stages (`wrapper: true` marks a parent execution whose child holds the jobs); the slowest ones carry `patterns` (join types, exchanges, broadcasts, Python UDF, window, AQEShuffleRead, copy-on-write vs merge-on-read writes, native engine and JVM fallbacks), `operators` (each operator with the stage it ran in, output rows, main time metric total / median task / slowest task, peak memory, spill, shuffle bytes written plus the in-memory `data_size_bytes`, partitions, files) and the physical plan text for the top 3. Iceberg `BatchScan` metrics are N/A on a History Server; `scan_metrics_unavailable` lists them | SQL → execution page |
| `executors` | tasks, task time, GC, input, shuffle, peak heap / execution / storage memory, when each joined or left, remove reason | Executors |
| `signals` | application-level observations: DRIVER_TIME, EXECUTOR_STARTUP, LATE_EXECUTORS, EXECUTORS_LOST, GC_HEAVY_EXECUTORS, STORAGE_FULL, UNEVEN_EXECUTOR_LOAD | |
| `configs` | the settings that shape parallelism and memory | Environment |

Every signal carries `evidence` (the measured numbers), `possible_causes` and
`check_in_ui`. The stage signals are:

| Signal | What the numbers show | Tells you to look at |
|---|---|---|
| `SKEW` | slowest task ≥ 3× the median and ≥ 30 s. The evidence quotes the slowest task's own bytes against the median task's, and notes when the task with the most data was not the slowest | data skew when the slow task also read several times the median; a straggler executor when it did not (see `slowest_tasks`: same executor on every slow task → the node) |
| `TOO_BIG` | spill to memory or disk, or median bytes per task above the memory per task slot. The evidence quotes peak execution memory per task and disk spill per task | rows are far larger in memory than the compressed shuffle bytes suggest (compare `data_size_bytes` with `shuffle bytes written` on the Exchange); a hot key when the spill sits in the slowest tasks; shuffle partition count vs data volume; large per-row aggregation state |
| `TOO_SMALL` | ≥ 200 tasks, median < 16 MB per task, median task < 1 s; scheduler-delay share when it is high | small files; too many shuffle partitions; per-file cost on object storage |
| `GC` | GC ≥ 10% of executor run time | heap per task slot; object-heavy code; cached rows on the heap |
| `IDLE` | fewer tasks than task slots on a stage whose slowest task ran ≥ 30 s | window / sort without PARTITION BY, coalesce(1), few write partitions; more executors would not help |
| `LOW_CPU` | CPU time < 50% of executor run time on a stage with ≥ 2 min of task time | waiting on storage (object store latency, small files), shuffle, or Python workers |
| `FETCH_WAIT` | shuffle fetch wait ≥ 10% of run time | network or the serving executors' disks; lost executors |
| `UNEVEN_EXECUTORS`, `SAME_EXECUTOR`, `TASK_FAILURES` | per-executor task time imbalance in the stage; the slowest tasks on one executor; failed tasks | late executors, locality, a slow node; retries |

Thresholds are listed in `facts.json` under `thresholds`. A signal is an observation, not a
verdict: a stage can carry `SKEW` because of one slow node, and a stage with no signal can
still be the bottleneck simply because it does the most work (a copy-on-write MERGE that
rewrites most of the table, a scan of every column to build a cache).

For an application-level cost breakdown,
[`spark-analyzer`](https://pypi.org/project/spark-analyzer/) is an extra source.

## Step 2: Reason from the timeline down

Work in this order, and keep the numbers from `facts.json` next to every statement:

1. **Where did the wall-clock time go?** `timeline.busy_ms` vs `timeline.no_stage_ms`. Time
   with no stage running is driver work (planning, file listing, table commits, `collect()`
   handling, the user's Python between actions) or waiting for executors; the longest gaps say
   which. If that share is large, no stage-level fix will recover it.
2. **Which jobs and stages hold the critical path?** Walk `stages` longest active time
   first and tie each to its job and SQL execution (`job_id`, `sql_id`). Stages of one job
   can overlap, and stages of parallel jobs too, so when you add up time, group overlapping
   stages into one phase (use `start_s` and `wall_ms`) rather than summing them. A stage
   with a large `queued_ms` was waiting, not working; say so rather than calling it slow.
3. **For each long stage, what were the tasks doing?** Compare task duration with bytes per
   task, then CPU share, spill, GC, fetch wait:

   | Resource | Shows up as | Exhausted when |
   |---|---|---|
   | CPU (cores = task slots) | CPU share near 100%, all slots busy | the stage does that much work; only less work or more cores help |
   | Memory | spill (memory then disk), GC time, peak execution memory | bytes per task (plus hash tables, sort buffers) > memory per task slot |
   | Local disk | spill (disk), shuffle write time | large spill, slow shuffle write |
   | Network / storage | fetch wait, low CPU share on scans, `io wait time` in native scans | tasks wait for bytes |

4. **Which operator did the time go to?** In `sql[].operators`, find the operators that ran
   in that stage (`stages`) and compare their slowest-task time with the stage's slowest task.
   Totals are summed over tasks (CPU-like budgets), so use the max-task column for wall-clock
   reasoning. `WholeStageCodegen` durations and `time in aggregation build` are inclusive of
   the pipeline below them (a scan waiting for S3 shows up as aggregation time), so do not
   name the aggregate as the cost when the scan under it has low CPU share. Check
   `patterns`: a SortMergeJoin against a small table, `SortMergeJoin(skew=true)` means AQE
   split that join (and the plain `SortMergeJoin` next to it was not split), many exchanges,
   a Python UDF, no `AQEShuffleRead` (AQE did not act), copy-on-write rewrites, JVM
   fallbacks on a native engine.
5. **Is it the data or the cluster?** `slowest_tasks` separates a hot key (the slow task also
   moved several times the data) from a slow node (the slow tasks share an executor and read
   ordinary amounts of data) from work that grows inside the task (ordinary bytes, different
   executors: a join that multiplies rows, a UDF, a regex).

Where the facts do not decide between two causes, say so, name both, and say which
measurement (or which re-run) would decide it. Arithmetic on measured numbers is fine
(a share of the run, bytes per task from a total) when you mark it "about".

## Step 3: Recommend changes: configs and code

For every bottleneck give the config change and the code/SQL change when both exist, say
which you would try first and why, and say what the next run must show. Prefer the change
that attacks the cause (less data, fewer files, a broadcast) over the one that gives the
symptom more room (more memory, more partitions). The table below lists the usual pairs.

| Symptom | Config option | Code / SQL option |
|---|---|---|
| `TOO_BIG` after a shuffle (spill) | `spark.sql.adaptive.coalescePartitions.initialPartitionNum` (3-10× slots) with `spark.sql.adaptive.advisoryPartitionSizeInBytes` (64-256m); or `spark.sql.shuffle.partitions` when AQE is off | filter and project before the shuffle; drop unused columns; pre-aggregate |
| SortMergeJoin against a small table | raise `spark.sql.autoBroadcastJoinThreshold` above the small side's size | `/*+ BROADCAST(t) */` or `F.broadcast(df)`; select only the needed columns of the small side |
| `SKEW` in a join that AQE did not split | `spark.sql.adaptive.skewJoin.skewedPartitionFactor` / `skewedPartitionThresholdInBytes`; `spark.sql.adaptive.forceOptimizeSkewedJoin=true` when a following aggregate needs the same partitioning | handle the hot key on its own path (filter it out, broadcast it, union back), or salt the key |
| `SKEW` in an aggregate or window | none: AQE cannot split it | two-phase aggregation with a salt column; pre-aggregate before the window |
| `IDLE` with 1 task (window or sort with no PARTITION BY) | none | partition the window; range-partition and number rows per partition; `monotonically_increasing_id()` when gaps are fine |
| `SKEW` without a data ratio (straggler) | `spark.speculation=true` | nothing in the code; check the node, disk, and network |
| `TOO_SMALL` from many small files (Iceberg) | read options `split-size` / `file-open-cost` (`spark.sql.files.*` does not apply to Iceberg). If the cost is per file (object-store GETs), fewer tasks alone will not help | compact (`CALL <catalog>.system.rewrite_data_files`); fix the writer (`write.distribution-mode=hash`, no fan-out from many tasks) |
| `TOO_SMALL` from too many shuffle partitions | lower `spark.sql.shuffle.partitions` or raise the advisory size | remove an unneeded `repartition(n)` |
| Iceberg write with few busy write tasks | `spark.sql.iceberg.advisory-partition-size`, the table's `write.distribution-mode` | a finer sort / cluster column for the busy partition |
| Copy-on-write MERGE / UPDATE rewriting much of the table | table properties `write.merge.mode=merge-on-read` (`write.update.mode`, `write.delete.mode`) | partition predicates in the `ON` / `WHERE` clause so untouched partitions are pruned |
| `GC` or `GC_HEAVY_EXECUTORS` | more heap per core (fewer `spark.executor.cores` or more `spark.executor.memory`), G1GC | DataFrame expressions instead of RDD or object-heavy UDF code; Pandas UDF instead of row UDF |
| `STORAGE_FULL` | `spark.memory.storageFraction`, `MEMORY_AND_DISK` or serialized storage level | cache only the columns and rows the later queries need, or do not cache when it is read once |
| `LOW_CPU` on scans | more tasks in flight per core is not possible in Spark; `spark.sql.files.maxPartitionBytes` (non-Iceberg) only changes task size | fewer, larger files; column pruning and partition filters so fewer files are opened |
| `DRIVER_TIME` | `spark.sql.iceberg.planning.*` / metadata caching where it applies; `spark.driver.cores` and memory if the driver is GC-bound | fewer actions (one write instead of `count()` + write); avoid `collect()` of large results; batch small commits |
| `EXECUTORS_LOST` with exit code 137 | `spark.executor.memoryOverhead` / `spark.executor.memoryOverheadFactor`; check node DiskPressure evictions | Python UDF → Pandas UDF or built-ins; lower per-task memory |

Only recommend more or bigger instances when a stage is busy on every slot with CPU share
near 100% and none of the rows above applies. Never claim a fix worked without a re-run: say
what the next run must show (the stage, the metric, the expected value).

## Step 4: Write the analysis and render the page

Write the analysis as an HTML fragment (no `<html>` / `<body>`) to `analysis.html`, then:

```bash
python3 <skill-dir>/profile_spark_app.py --facts facts.json --html spark-profile.html --analysis analysis.html
open spark-profile.html     # xdg-open on Linux
```

The page is short on purpose: about 1,200 words in all. The script contributes about 800
(the timeline with queued time and driver gaps, jobs → stages → SQL, the critical-path
stages with their signals and slowest task, the operators that ran in those stages). Your
fragment gets **at most 400 words**. Everything the Spark UI already shows (physical plans,
every stage, every executor, the full operator list, configs) stays out of the page and in
`facts.json`; the page links to the UI pages instead.

Use only `<h3>`, `<p>`, `<ul>`, `<table>`, `<pre>`, `<code>` and `<a>` in the fragment.
Link stages, jobs and SQL executions to their Spark UI pages with the `ui_url` values in
`facts.json`. Four short sections:

1. `<h3>What this job does and where the time went</h3>`: one or two sentences on the job,
   then a three-to-five-row table: phase (a job, a long stage, or overlapping stages),
   active time, share of the run, one-line reason. Include executor start-up or driver gaps
   as a row when they are more than a few percent.
2. `<h3>Likely bottleneck</h3>`: at most two. For each: the numbers (quoted), what is
   probably happening and why, a confidence word (likely / possible / cannot tell from the
   UI), the alternative you cannot exclude, and the UI page and column that shows it.
3. `<h3>What to change</h3>`: one `<pre>` block with the exact `--conf` lines, table
   properties (`ALTER TABLE … SET TBLPROPERTIES`) or read options, one comment per line
   naming what it targets; then the code / SQL change as a short before / after snippet
   when you have the code; then one sentence on the order to try them and what the next
   run must show (stage, metric, expected value).
4. `<h3>What would not help</h3>`: one or two sentences on the obvious change the numbers
   argue against (more executors for an `IDLE` or `SKEW` stage, more memory for
   `TOO_SMALL`), and what you could not tell from the UI alone.

### Tone rules

- Explain, do not pronounce. Write "the slowest task read 13× the median, which points at
  one hot key" rather than "one task decides the run time". No headlines, no superlatives,
  no "verdict".
- Hedge in proportion to the evidence. The UI shows symptoms; the cause is an inference.
  Use "likely", "possible", "consistent with", and say what would confirm it.
- Give possibilities when you are not sure: name the two or three causes the numbers allow
  and the measurement that separates them. The catalogue below lists the usual ones.
- Quote the measured numbers from `facts.json`; do not estimate what the profile measured.
  Round to the precision the reader needs (297 s, 12×, 1.4 GiB).
- Keep every finding verifiable: name the Spark UI tab and the column that shows it.
- Plain page: no emoji, no dashboard tiles, no colored verdict banners.
- On a History Server the Storage tab is empty after the application ends; for cache
  questions point the reader at the Executors tab's peak storage memory instead.
- When the configs show the run was not what the user called it (a "defaults" run with
  tuned settings, a different partition count), say so before analysing.

### Possible causes per signal

`facts.json` carries these under each signal's `possible_causes`; the page does not print
them, so pick the one or two the numbers support and name them in the analysis.

| Signal | Usual causes, in the order to check |
|---|---|
| `SKEW` with a high data ratio | one key (or a few) holds a large share of rows: a default key such as `customer_id = 0`, a busy partition value, a null key |
| `SKEW` with an ordinary data ratio | the slowest tasks share an executor (slow node, GC, disk); a key that multiplies rows inside a join; a UDF or regex on a few long rows; slow storage for those files |
| `TOO_BIG` | rows far larger in memory than the compressed shuffle bytes (compare `data_size_bytes` with `shuffle bytes written`); the hot key when the spill sits in the slowest tasks; too few partitions for the volume (`spark.sql.shuffle.partitions` = 200 whatever the size, AQE merges but does not split); large per-row state (`collect_list`, wide structs, `approx_count_distinct`) |
| `TOO_SMALL` | tens of thousands of small files (each file or split is a task); too many shuffle partitions for a small volume; a `repartition(n)` with a large n. When the cost is per file on object storage, fewer tasks alone will not help; fewer files will |
| `GC`, `GC_HEAVY_EXECUTORS` | small heap per task slot for the objects created (wide rows, `collect_list`, UDFs); many cores per executor sharing one heap; cached rows on the heap |
| `IDLE` | window or sort with no `PARTITION BY`, `coalesce(1)`, `collect()`; a write grouped by few partition values; a small input; AQE coalesced to few partitions |
| `LOW_CPU` | tasks waiting on object storage (latency per file, small files), on shuffle reads, on Python workers (their CPU is not in this metric), or on the writer committing files |
| `FETCH_WAIT` | network or the serving executors' disks; executors that were lost, so shuffle files were re-fetched or recomputed |
| `UNEVEN_EXECUTORS`, `UNEVEN_EXECUTOR_LOAD` | executors that joined late; locality preferences (`spark.locality.wait`); the uneven tasks landed on one executor |
| `SAME_EXECUTOR` | a slow or overloaded node; locality that sent one set of files to one executor |
| `TASK_FAILURES`, `EXECUTORS_LOST` | memory limit exceeded (exit code 137), node eviction or spot interruption, fetch failures, an exception in the task; dynamic-allocation idle removal is harmless |
| `DRIVER_TIME` | planning and file listing (Iceberg/Hudi metadata), table commits, `collect()` results handled in Python, many small actions |
| `EXECUTOR_STARTUP`, `LATE_EXECUTORS` | pod or container scheduling, image pull, node provisioning, dynamic-allocation ramp-up |
| `STORAGE_FULL` | `cache()` of more than the storage share, so blocks are evicted and recomputed and execution memory is squeezed; caching data that is read once |

Finish by giving the user the path of the page and a three-line summary in chat: where the
time went, the most likely bottleneck, and the first change to try.
