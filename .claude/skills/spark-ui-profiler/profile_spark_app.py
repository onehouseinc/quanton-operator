#!/usr/bin/env python3
"""Profile one Spark application from the Spark History Server (or a live Spark UI).

The script reads the public Spark REST API (/api/v1, Spark 3.x) and writes the facts a data
engineer needs to find out where the run time went:

  * a wall-clock timeline: which jobs and stages ran when, and the gaps in between where no
    stage ran (driver work: planning, commits, collect(), Python code)
  * the jobs -> stages -> SQL execution mapping, as the Jobs and SQL tabs show it
  * per stage: task duration and data per task quantiles, CPU share, spill, GC, fetch wait,
    scheduler delay, executor balance, and the slowest tasks with the executor they ran on
  * per SQL execution: the operator split (each operator with the stage it ran in, rows,
    time, peak memory, spill, shuffle bytes), the join/exchange/UDF patterns, and the
    physical plan of the slowest executions
  * per executor: tasks, task time, GC, shuffle, peak heap, and when it joined or left
  * the configs that shape parallelism and memory

For each stage the script attaches *signals*:

  SKEW         the slowest task is far slower than the median task (with the data ratio, so
               data skew can be told from a straggler executor)
  TOO_BIG      tasks spill, or each task moves more data than its share of memory
  TOO_SMALL    tasks are so small that scheduling overhead dominates
  GC           JVM garbage collection eats a large share of task time
  IDLE         a long stage ran fewer tasks than the cluster had task slots
  FETCH_WAIT   tasks waited for shuffle blocks from other executors
  LOW_CPU      tasks spent most of their run time off the CPU (storage, network, Python)
  UNEVEN_EXECUTORS, SAME_EXECUTOR, TASK_FAILURES

and at the application level DRIVER_TIME, LATE_EXECUTORS, EXECUTORS_LOST,
GC_HEAVY_EXECUTORS, STORAGE_FULL, UNEVEN_EXECUTOR_LOAD. A signal names what the numbers show, the
evidence, the possible causes, and where to verify it in the Spark UI. Signals are
observations, not verdicts: the analysis that interprets them is written separately and
placed above the tables.

Outputs:
  --json facts.json                        the facts, for an LLM or a human to reason over
  --html report.html [--analysis a.html]   a short self-contained report page (analysis,
                                           timeline, jobs -> stages -> SQL, critical-path
                                           stages with signals, operators in those stages);
                                           everything else stays in the facts and the UI
  --facts facts.json                       re-render from saved facts instead of fetching

Only the Python standard library is used.

Usage:
  profile_spark_app.py --url http://localhost:18080 --app spark-1234 --json facts.json
  profile_spark_app.py --facts facts.json --html report.html --analysis analysis.html
"""
import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

QUANTILES = "0.0,0.25,0.5,0.75,1.0"
KB = 1024
MB = 1024 * KB
GB = 1024 * MB

# ------------------------------------------------------------------ thresholds
# The defaults follow common Spark guidance. They decide when a signal is attached, not what
# the signal means; the evidence is always printed next to it.
UNEVEN_RATIO = 3.0            # slowest task / median task
UNEVEN_MIN_MAX_MS = 30_000    # ignore uneven tasks when the slowest task is under 30 s
TINY_TASK_BYTES = 16 * MB     # median data per task below this is "tiny"
TINY_TASK_MS = 1_000          # ...and the median task finishes in under 1 s
TINY_MIN_TASKS = 200          # ...and the stage has many tasks
GC_SHARE = 0.10               # GC time / executor run time
LOW_CPU_SHARE = 0.50          # executor CPU time / executor run time
FETCH_WAIT_SHARE = 0.10       # shuffle fetch wait / executor run time
SCHED_DELAY_SHARE = 0.20      # scheduler delay / task duration (median)
LONG_STAGE_MS = 20_000        # a stage short enough to ignore for parallelism signals
GAP_MIN_MS = 2_000            # report gaps between stages longer than this
EXEC_IMBALANCE = 2.0          # busiest executor task time / median executor task time
TOP_STAGES = 20               # stages profiled in detail (by wall time)
TOP_SQL = 6                   # SQL executions with an operator split
PLANS_WITH_TEXT = 3           # ...of which these get the physical plan text
PLAN_CHARS = 14_000
SLOW_TASKS = 5                # slowest tasks listed per flagged stage
MAX_TASK_LISTS = 8            # stages for which the slowest tasks are fetched
STAGE_DETAIL_MAX_TASKS = 4000 # per-executor balance needs the full stage detail; skip huge stages

INTERESTING_CONFS = (
    "spark.sql.shuffle.partitions", "spark.sql.adaptive.", "spark.sql.autoBroadcastJoinThreshold",
    "spark.sql.files.", "spark.executor.", "spark.driver.memory", "spark.driver.cores", "spark.memory.",
    "spark.sql.iceberg.", "spark.default.parallelism", "spark.dynamicAllocation.", "spark.speculation",
    "spark.locality.wait", "spark.serializer", "spark.shuffle.", "spark.plugins", "spark.task.cpus",
    "spark.sql.sources.partitionOverwriteMode", "spark.sql.parquet.", "spark.sql.execution.arrow",
    "spark.python.worker", "spark.kubernetes.executor.request.cores", "spark.kubernetes.executor.limit.cores",
    "spark.executorEnv", "spark.sql.extensions", "spark.sql.catalog.",
)
# Operator names that mean a native engine (Gluten/Velox, Quanton) ran the operator.
NATIVE_MARKERS = ("Transformer", "ColumnarExchange", "ColumnarBroadcastExchange", "Velox", "Gluten", "Native")
NATIVE_OK_FALLBACKS = ("LocalTableScan", "AdaptiveSparkPlan", "AQEShuffleRead", "ReusedExchange",
                       "ShuffleQueryStage", "BroadcastQueryStage", "Subquery", "Scan parquet",
                       "Scan", "CommandResult", "Execute", "WriteFiles", "AppendData", "OverwriteByExpression",
                       "ReplaceData", "WriteDelta", "ColumnarToRow", "RowToColumnar", "InputAdapter",
                       "WholeStageCodegen", "Project", "Filter")


class ProfilerError(Exception):
    pass


# ------------------------------------------------------------------ HTTP

TIMEOUT = 180


def get(base: str, path: str) -> Any:
    url = f"{base}/api/v1/{path}"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise ProfilerError(f"GET {url} -> HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise ProfilerError(f"GET {url} -> {e.reason}") from e


def get_or(base: str, path: str, default: Any) -> Any:
    try:
        return get(base, path)
    except ProfilerError as e:
        print(f"note: {e} (skipped)", file=sys.stderr)
        return default


def url_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return resp.status == 200
    except Exception:
        return False


# ------------------------------------------------------------------ parsing

def parse_ts(value: Optional[str]) -> Optional[float]:
    """Spark REST timestamps ('2026-10-06T04:18:36.513GMT') -> epoch ms."""
    if not value:
        return None
    text = value.replace("GMT", "").replace("UTC", "").replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).timestamp() * 1000
        except ValueError:
            continue
    return None


def quantiles(summary: Dict[str, Any], *path: str) -> List[float]:
    node: Any = summary
    for key in path:
        node = node.get(key, {}) if isinstance(node, dict) else {}
    return [float(x) for x in node] if isinstance(node, list) and len(node) == 5 else [0.0] * 5


TIME_UNITS = {"ms": 1.0, "s": 1000.0, "m": 60_000.0, "min": 60_000.0, "h": 3_600_000.0}
BYTE_UNITS = {"B": 1.0, "KiB": KB, "MiB": MB, "GiB": GB, "TiB": 1024.0 * GB, "KB": KB, "MB": MB, "GB": GB}
VALUE_RE = re.compile(r"^\s*([-\d.,E+]+)\s*([A-Za-z]*)\s*$")
MULTI_RE = re.compile(r"^(.*?)\s*\((.*?),\s*(.*?),\s*(.*?)\s*\(stage\s+(\d+)\.(\d+):\s*task\s+(\d+)\)\)\s*$")


def parse_value(text: str) -> Tuple[Optional[float], str]:
    """'1.2 m' -> (72000, 'ms'); '26.0 MiB' -> (bytes, 'bytes'); '48,998,632' -> (n, 'count')."""
    m = VALUE_RE.match(text or "")
    if not m:
        return None, ""
    try:
        number = float(m.group(1).replace(",", ""))
    except ValueError:
        return None, ""
    unit = m.group(2)
    if unit in TIME_UNITS:
        return number * TIME_UNITS[unit], "ms"
    if unit in BYTE_UNITS:
        return number * BYTE_UNITS[unit], "bytes"
    if unit == "":
        return number, "count"
    return None, ""


def parse_metric(name: str, raw: str) -> Dict[str, Any]:
    """Parse one SQL node metric string into numbers.

    Formats seen in the REST API:
      '48,998,632'
      '653 ms'
      'total (min, med, max (stageId: taskId))\\n3.21 h (2.3 s, 24.4 s, 1.2 m (stage 0.0: task 280))'
      '(min, med, max (stageId: taskId)):\\n(1, 1, 1 (stage 0.0: task 2))'
    """
    out: Dict[str, Any] = {"name": name, "raw": raw}
    if raw is None or raw.strip() in ("", "N/A"):
        return out
    last = raw.strip().splitlines()[-1].strip()
    m = MULTI_RE.match(last)
    if m:
        total_txt = m.group(1).strip().lstrip("(")
        total, unit = parse_value(total_txt) if total_txt else (None, "")
        lo, u1 = parse_value(m.group(2))
        med, u2 = parse_value(m.group(3))
        hi, u3 = parse_value(m.group(4))
        out.update({"total": total, "min": lo, "med": med, "max": hi, "unit": unit or u2 or u3 or u1,
                    "max_stage": int(m.group(5)), "max_attempt": int(m.group(6)), "max_task": int(m.group(7))})
        return out
    value, unit = parse_value(last)
    if value is not None:
        out.update({"total": value, "unit": unit})
    return out


# ------------------------------------------------------------------ formatting

def fmt_bytes(n: Optional[float]) -> str:
    n = n or 0
    if n >= GB:
        return f"{n / GB:.1f} GiB"
    if n >= MB:
        return f"{n / MB:.0f} MiB"
    if n >= KB:
        return f"{n / KB:.0f} KiB"
    return f"{n:.0f} B"


def fmt_ms(ms: Optional[float]) -> str:
    ms = ms or 0
    if ms >= 3_600_000:
        return f"{ms / 3_600_000:.1f} h"
    if ms >= 60_000:
        return f"{ms / 60_000:.1f} min"
    if ms >= 1000:
        return f"{ms / 1000:.1f} s"
    return f"{ms:.0f} ms"


def fmt_num(n: Optional[float]) -> str:
    if n is None:
        return "-"
    return f"{int(n):,}" if float(n).is_integer() else f"{n:,.1f}"


def fmt_s(seconds: Optional[float], missing: str = "-") -> str:
    """Offset from application start, e.g. '164 s'."""
    return f"{seconds:.0f} s" if seconds is not None else missing


def plural(n: Any, word: str) -> str:
    n = n or 0
    return f"{fmt_num(n)} {word}" if n == 1 else f"{fmt_num(n)} {word}s"


def fmt_pct(x: Optional[float]) -> str:
    return f"{100 * (x or 0):.0f}%"


def ratio(a: float, b: float) -> Optional[float]:
    return a / b if b else None


# ------------------------------------------------------------------ signals

def signal(kind: str, severity: str, summary: str, evidence: str, causes: List[str], check: str) -> Dict[str, Any]:
    return {"kind": kind, "severity": severity, "summary": summary, "evidence": evidence,
            "possible_causes": causes, "check_in_ui": check}


def stage_signals(st: Dict[str, Any], slots: int, mem_per_slot: Optional[float]) -> List[Dict[str, Any]]:
    """Observations about one stage, each with evidence and possible causes."""
    out: List[Dict[str, Any]] = []
    d = st["task_duration_ms"]
    b = st["bytes_per_task"]
    tasks = st["tasks"]
    run_ms = st["executor_run_ms"] or 0
    wall = st["wall_ms"]
    sid = st["stage_id"]
    stage_page = f"Stages tab, stage {sid}"

    # Uneven task durations: data skew, a straggler executor, or work that grows inside the task.
    slowest = (st.get("slowest_tasks") or [None])[0]
    if d[2] > 0 and d[4] / d[2] >= UNEVEN_RATIO and d[4] >= UNEVEN_MIN_MAX_MS:
        dur_x = d[4] / d[2]
        slow_bytes = slowest["bytes"] if slowest else b[4]      # the slowest task's own data, not the max-bytes task
        bytes_x = ratio(slow_bytes, b[2])
        evidence = f"slowest task {fmt_ms(d[4])} vs median {fmt_ms(d[2])} ({dur_x:.0f}x)"
        if bytes_x:
            evidence += f"; the slowest task read {fmt_bytes(slow_bytes)} vs a median of {fmt_bytes(b[2])} ({bytes_x:.1f}x)"
        if slowest and b[4] > 2 * slow_bytes:
            evidence += f"; the task with the most data ({fmt_bytes(b[4])}) was not the slowest"
        causes = []
        if bytes_x and bytes_x >= 2:
            causes.append("data skew: one partition (one key, or a few) holds much more data than the "
                          "others, so one task does that share of the work while other slots wait")
        else:
            causes.append("the slow task read about as much data as the others, so the extra time came from "
                          "inside the task: a key that expands in a join, a slow executor or node, "
                          "GC pauses, or slow storage for that task's files")
            causes.append("if the slowest tasks share an executor or host (see the slowest-tasks table), a "
                          "straggler node is more likely than data skew")
        out.append(signal("SKEW", "high" if wall > LONG_STAGE_MS else "medium",
                          "Task durations are uneven", evidence, causes,
                          f"{stage_page}: Summary Metrics (Duration, Shuffle Read Size / Input Size) and the "
                          f"Event Timeline; sort the task table by Duration"))

    # Spill: the task's share of execution memory did not hold its data.
    if (st["spill_disk_bytes"] or 0) > 0 or (st["spill_memory_bytes"] or 0) > 0:
        pk = st.get("peak_execution_memory_per_task") or [0] * 5
        spill_t = st.get("spill_disk_per_task") or [0] * 5
        evidence = (f"{fmt_bytes(st['spill_memory_bytes'])} spilled from memory, {fmt_bytes(st['spill_disk_bytes'])} "
                    f"written to disk; data per task median {fmt_bytes(b[2])}, max {fmt_bytes(b[4])}")
        if pk[2] or pk[4]:
            evidence += f"; peak execution memory per task median {fmt_bytes(pk[2])}, max {fmt_bytes(pk[4])}"
        if mem_per_slot:
            evidence += f", against about {fmt_bytes(mem_per_slot)} per task slot"
        if spill_t[4]:
            evidence += f"; disk spill per task median {fmt_bytes(spill_t[2])}, max {fmt_bytes(spill_t[4])}"
        skewed = d[2] > 0 and d[4] / d[2] >= UNEVEN_RATIO
        causes = ["the rows are much larger in memory than on the wire (compressed shuffle bytes are a fraction of the "
                  "in-memory size), so a task's hash table or sort buffer outgrows its share of execution memory "
                  "even when the shuffle read per task looks small",
                  f"{'the hot key: the spill sits mostly in the slowest tasks (see disk spill per task)' if skewed else 'a hot key, if the spill sits in the slowest tasks only'}",
                  f"too few partitions for the data volume ({fmt_num(st['tasks'])} tasks in this stage; "
                  "spark.sql.shuffle.partitions defaults to 200 whatever the size, and AQE merges small partitions "
                  "but does not split large ones outside skew joins)",
                  "aggregation or sort state that is large per row (collect_list, wide structs, approx_count_distinct)"]
        if skewed:
            causes = [causes[1], causes[0], causes[2], causes[3]]
        sev = "high" if (st["spill_disk_bytes"] or 0) > 100 * MB else "medium"
        out.append(signal("TOO_BIG", sev, "Tasks spilled: more data per task than its share of memory", evidence, causes,
                          f"{stage_page}: Summary Metrics (Spill (memory), Spill (disk)); Aggregated Metrics by Executor"))
    elif mem_per_slot and b[2] > mem_per_slot:
        out.append(signal("TOO_BIG", "medium", "Data per task is larger than a task slot's memory",
                          f"median {fmt_bytes(b[2])} per task vs about {fmt_bytes(mem_per_slot)} per slot; no spill recorded",
                          ["the stage streams the data (a scan with a filter, for example), so it did not spill; "
                           "a later aggregate, sort or join on the same partitioning may"],
                          f"{stage_page}: Summary Metrics (Input Size / Shuffle Read Size)"))

    # Tiny tasks: per-task overhead dominates.
    if tasks >= TINY_MIN_TASKS and b[2] < TINY_TASK_BYTES and d[2] < TINY_TASK_MS:
        sched = st.get("scheduler_delay_ms", [0] * 5)
        evidence = f"{fmt_num(tasks)} tasks, median {fmt_bytes(b[2])} per task in {fmt_ms(d[2])}"
        if d[2] and sched[2] / d[2] >= SCHED_DELAY_SHARE:
            evidence += f"; scheduler delay is {fmt_pct(sched[2] / d[2])} of the median task"
        out.append(signal("TOO_SMALL", "medium" if wall > LONG_STAGE_MS else "low",
                          "Tasks are tiny; per-task overhead dominates", evidence,
                          ["many small files in the table (each file or split becomes a task)",
                           "too many shuffle partitions for a small data volume, or a repartition(n) with a large n",
                           "if the cost is per file on object storage (one GET per file), fewer tasks "
                           "alone will not make the stage much faster; fewer files will"],
                          f"{stage_page}: number of tasks, Summary Metrics (Input Size / Records), Scheduler Delay"))

    # GC share.
    gc_ms = st["gc_ms"] or 0
    if run_ms > 0 and gc_ms / run_ms >= GC_SHARE:
        out.append(signal("GC", "medium", "Garbage collection takes a large share of task time",
                          f"GC {fmt_ms(gc_ms)} of {fmt_ms(run_ms)} executor run time ({fmt_pct(gc_ms / run_ms)})",
                          ["the heap per task slot is small for the objects the tasks create (wide rows, "
                           "collect_list, UDFs, cached rows in deserialized form)",
                           "many tasks per executor sharing one heap (high spark.executor.cores)"],
                          f"{stage_page}: Summary Metrics (GC Time); Executors tab, GC Time column"))

    # Fetch wait: waiting for shuffle blocks from other executors.
    fw = st.get("fetch_wait_ms") or 0
    if run_ms > 0 and fw / run_ms >= FETCH_WAIT_SHARE:
        out.append(signal("FETCH_WAIT", "medium", "Tasks waited for shuffle data from other executors",
                          f"fetch wait {fmt_ms(fw)} of {fmt_ms(run_ms)} run time ({fmt_pct(fw / run_ms)})",
                          ["the network or the serving executors' disks are the limit for this shuffle",
                           "executors that were removed, so their shuffle files were re-fetched or recomputed"],
                          f"{stage_page}: Summary Metrics (Shuffle Read Blocked Time)"))

    # CPU share: are the tasks computing or waiting?
    cpu_share = st.get("cpu_share")
    if cpu_share is not None and wall > LONG_STAGE_MS and tasks >= 2 and run_ms >= 60_000:
        if cpu_share < LOW_CPU_SHARE and not any(s["kind"] == "FETCH_WAIT" for s in out):
            waiting = "storage reads (object storage latency, many small files)" if (st["input_bytes"] or 0) > 0 \
                else "shuffle reads, Python workers, or the writer committing files"
            out.append(signal("LOW_CPU", "medium", "Tasks spent most of their time off the CPU",
                              f"CPU time is {fmt_pct(cpu_share)} of executor run time",
                              [f"tasks waited on I/O: {waiting}",
                               "Python UDF time counts as wait here (the Python worker's CPU is not in this metric)"],
                              f"{stage_page}: compare Task Time with the task table's Executor CPU Time; Executors tab"))

    # Parallelism: fewer tasks than slots on a long stage.
    if slots and 0 < tasks < slots and wall > LONG_STAGE_MS and d[4] >= UNEVEN_MIN_MAX_MS:
        causes = ["a window or sort over the whole dataset with no PARTITION BY, or coalesce(1) / collect()"] \
            if tasks == 1 else \
            ["the number of partitions after a shuffle or a write (AQE coalesced them, the table has few "
             "partitions, or the writer groups rows by partition value)",
             "a small input (few files or splits)"]
        out.append(signal("IDLE", "high" if tasks == 1 else "medium",
                          "Fewer tasks than task slots on a long stage",
                          f"{tasks} tasks for {slots} task slots; the stage took {fmt_ms(wall)} and its slowest task {fmt_ms(d[4])}",
                          causes + ["more executors would not shorten this stage; more tasks would"],
                          f"{stage_page}: number of tasks vs Executors tab cores; Event Timeline"))

    # Executor imbalance inside the stage.
    bal = st.get("executor_balance") or {}
    skew_fired = any(x["kind"] == "SKEW" for x in out)
    if bal.get("executors", 0) >= 3 and bal.get("imbalance") and bal["imbalance"] >= EXEC_IMBALANCE \
            and wall > LONG_STAGE_MS and not skew_fired:
        out.append(signal("UNEVEN_EXECUTORS", "low", "Executors did uneven amounts of work in this stage",
                          f"busiest executor {bal['busiest_id']} did {fmt_ms(bal['max_task_ms'])} of task time vs "
                          f"a median executor's {fmt_ms(bal['median_task_ms'])} ({bal['imbalance']:.1f}x)",
                          ["executors that joined late (dynamic allocation, slow pod scheduling)",
                           "locality: tasks waited for a preferred executor (spark.locality.wait)",
                           "the uneven tasks above landed on that executor"],
                          f"{stage_page}: Aggregated Metrics by Executor"))

    # Failures.
    if (st.get("failed_tasks") or 0) > 0:
        out.append(signal("TASK_FAILURES", "medium", "Some tasks failed and were retried",
                          f"{st['failed_tasks']} failed tasks",
                          ["executor loss (memory limit, node eviction), fetch failures, or an exception in the task"],
                          f"{stage_page}: task table filtered by status FAILED; Executors tab, Failed Tasks"))
    return out


# ------------------------------------------------------------------ collection

def executor_profile(executors: List[Dict[str, Any]], app_start: Optional[float]) -> List[Dict[str, Any]]:
    out = []
    for e in executors:
        added = parse_ts(e.get("addTime"))
        removed = parse_ts(e.get("removeTime"))
        task_ms = e.get("totalDuration", 0) or 0
        gc_ms = e.get("totalGCTime", 0) or 0
        peak = e.get("peakMemoryMetrics") or {}
        out.append({
            "id": e.get("id"), "host": (e.get("hostPort") or "").split(":")[0], "is_driver": e.get("id") == "driver",
            "cores": e.get("totalCores", 0), "max_memory_bytes": e.get("maxMemory", 0),
            "active": e.get("isActive", True), "tasks": e.get("totalTasks", 0), "failed_tasks": e.get("failedTasks", 0),
            "task_time_ms": task_ms, "gc_ms": gc_ms, "gc_share": ratio(gc_ms, task_ms),
            "input_bytes": e.get("totalInputBytes", 0), "shuffle_read_bytes": e.get("totalShuffleRead", 0),
            "shuffle_write_bytes": e.get("totalShuffleWrite", 0),
            "peak_heap_bytes": peak.get("JVMHeapMemory"), "peak_offheap_bytes": peak.get("JVMOffHeapMemory"),
            "peak_storage_bytes": (peak.get("OnHeapStorageMemory") or 0) + (peak.get("OffHeapStorageMemory") or 0),
            "major_gc_count": peak.get("MajorGCCount"), "minor_gc_count": peak.get("MinorGCCount"),
            "peak_execution_bytes": (peak.get("OnHeapExecutionMemory") or 0) + (peak.get("OffHeapExecutionMemory") or 0),
            "added_s": (added - app_start) / 1000 if added and app_start else None,
            "removed_s": (removed - app_start) / 1000 if removed and app_start else None,
            "remove_reason": e.get("removeReason"),
        })
    return out


def executor_balance(stage_detail: Dict[str, Any]) -> Dict[str, Any]:
    summ = stage_detail.get("executorSummary") or {}
    if not summ:
        return {}
    times = sorted(((v.get("taskTime", 0) or 0), k) for k, v in summ.items())
    if not times:
        return {}
    median = times[len(times) // 2][0]
    max_ms, busiest = times[-1]
    return {"executors": len(times), "max_task_ms": max_ms, "median_task_ms": median, "busiest_id": busiest,
            "imbalance": ratio(max_ms, median), "min_task_ms": times[0][0]}


def timeline(stages: List[Dict[str, Any]], app_start: Optional[float], app_end: Optional[float]) -> Dict[str, Any]:
    """Union of stage intervals, and the gaps where no stage was running."""
    ivs = sorted((s["start_ms"], s["end_ms"], s) for s in stages if s.get("start_ms") and s.get("end_ms"))
    merged: List[List[Any]] = []
    for a, b, s in ivs:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
            merged[-1][3] = s
        else:
            merged.append([a, b, s, s])
    gaps = []
    if app_start and merged and merged[0][0] - app_start >= GAP_MIN_MS:
        gaps.append({"start_s": 0.0, "duration_ms": merged[0][0] - app_start, "after": "application start",
                     "before": f"stage {merged[0][2]['stage_id']}"})
    for (a1, b1, _, last), (a2, b2, first, _) in zip(merged, merged[1:]):
        if a2 - b1 >= GAP_MIN_MS:
            gaps.append({"start_s": (b1 - app_start) / 1000 if app_start else None, "duration_ms": a2 - b1,
                         "after": f"stage {last['stage_id']} ({last['description'] or last['name']})",
                         "before": f"stage {first['stage_id']} ({first['description'] or first['name']})"})
    if app_end and merged and app_end - merged[-1][1] >= GAP_MIN_MS:
        last = merged[-1][3]
        gaps.append({"start_s": (merged[-1][1] - app_start) / 1000 if app_start else None,
                     "duration_ms": app_end - merged[-1][1],
                     "after": f"stage {last['stage_id']} ({last['description'] or last['name']})",
                     "before": "application end (table commit, driver shutdown)"})
    busy = sum(b - a for a, b, _, _ in merged)
    total = (app_end - app_start) if app_start and app_end else busy
    return {"busy_ms": busy, "no_stage_ms": max(total - busy, 0), "no_stage_share": ratio(max(total - busy, 0), total),
            "gaps": sorted(gaps, key=lambda g: -g["duration_ms"])[:15]}


def classify_sql(nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
    names = [n.get("nodeName", "") for n in nodes]
    native = any(any(m in n for m in NATIVE_MARKERS) for n in names)
    fallbacks = []
    if native:
        for n in nodes:
            name = n.get("nodeName", "")
            if any(m in name for m in NATIVE_MARKERS) or any(name.startswith(ok) for ok in NATIVE_OK_FALLBACKS):
                continue
            if n.get("metrics"):
                fallbacks.append(name)
    return {
        "joins": sorted({n.split(" ")[0] for n in names if "Join" in n or "CartesianProduct" in n}),
        "exchanges": sum(1 for n in names if n.startswith(("Exchange", "ColumnarExchange"))),
        "broadcasts": sum(1 for n in names if "BroadcastExchange" in n),
        "python_udf": any(n.startswith(("BatchEvalPython", "ArrowEvalPython", "MapInPandas", "FlatMapGroupsInPandas")) for n in names),
        "aqe_shuffle_read": any(n.startswith("AQEShuffleRead") for n in names),
        "window": any(n.startswith("Window") or "WindowExec" in n for n in names),
        "sort": sum(1 for n in names if n.startswith("Sort") or "SortExec" in n),
        "native_engine": native, "native_fallbacks": sorted(set(fallbacks)),
        "write_mode": ("copy-on-write rewrite (ReplaceData: whole files are rewritten)" if any(n.startswith("ReplaceData") for n in names)
                       else "merge-on-read (WriteDelta: delete files are written)" if any(n.startswith("WriteDelta") for n in names)
                       else None),
    }


TIME_METRICS = ("duration", "time", "wall")
SECONDARY_TIME_METRICS = ("remote reqs duration", "fetch wait time", "remote merged reqs duration", "shuffle write time",
                          "time to compress", "time to split", "time of input iterator")
MEMORY_METRICS = ("peak memory", "peak mem", "memory bytes")


def operator_rows(nodes: List[Dict[str, Any]], edges: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """One row per operator: the stage it ran in, rows, the main time metric, memory, spill, bytes."""
    rows = []
    for n in nodes:
        parsed = [parse_metric(m.get("name", ""), m.get("value", "")) for m in n.get("metrics", [])]
        parsed = [p for p in parsed if p.get("total") is not None]
        stages = sorted({p["max_stage"] for p in parsed if "max_stage" in p})
        time_candidates = [p for p in parsed if p.get("unit") == "ms" and any(k in p["name"] for k in TIME_METRICS)
                           and p["name"] not in SECONDARY_TIME_METRICS]
        if not time_candidates:
            time_candidates = [p for p in parsed if p.get("unit") == "ms" and p["name"] in SECONDARY_TIME_METRICS]
        time_m = max(time_candidates, key=lambda p: p.get("total") or 0) if time_candidates else None
        rows_out = next((p["total"] for p in parsed if p["name"] == "number of output rows"), None)
        mem = max((p for p in parsed if p.get("unit") == "bytes" and any(k in p["name"] for k in MEMORY_METRICS)),
                  key=lambda p: p.get("max") or p.get("total") or 0, default=None)
        spill = next((p for p in parsed if p["name"].startswith("spill size") or p["name"] == "spilled bytes"), None)
        bytes_m = next((p for p in parsed if p["name"] in ("shuffle bytes written", "shuffle write bytes")), None)
        if bytes_m is None:
            bytes_candidates = [p for p in parsed if p.get("unit") == "bytes" and p["name"] in
                                ("number of output bytes", "size of files read", "total data file size (bytes)", "written output")]
            bytes_m = max(bytes_candidates, key=lambda p: p.get("total") or 0) if bytes_candidates else None
        data_size = next((p for p in parsed if p["name"] == "data size"), None)   # in-memory size before compression
        fetch_wait = next((p for p in parsed if p["name"] == "fetch wait time"), None)
        if n.get("nodeName", "").startswith(("Exchange", "ColumnarExchange")):
            preferred = next((p for p in parsed if p["name"] in ("shuffle write time", "shuffle wall time")), None)
            time_m = preferred or time_m
        partitions = next((p["total"] for p in parsed if p["name"] in ("number of partitions", "number of coalesced partitions")), None)
        files = next((p["total"] for p in parsed if p["name"] in ("number of files read", "number of file splits read", "number of result data files")), None)
        rows.append({
            "node_id": n.get("nodeId"), "operator": n.get("nodeName", ""), "wscg_id": n.get("wholeStageCodegenId"),
            "stages": stages, "rows_out": rows_out,
            "time": {"name": time_m["name"], "total_ms": time_m.get("total"), "med_ms": time_m.get("med"),
                     "max_ms": time_m.get("max"), "max_task": time_m.get("max_task")} if time_m else None,
            "peak_memory": {"name": mem["name"], "max_bytes": mem.get("max") or mem.get("total"), "total_bytes": mem.get("total")} if mem else None,
            "spill_bytes": spill.get("total") if spill else None,
            "bytes": {"name": bytes_m["name"], "total_bytes": bytes_m.get("total"), "max_bytes": bytes_m.get("max")} if bytes_m else None,
            "data_size_bytes": data_size.get("total") if data_size else None,
            "fetch_wait": {"total_ms": fetch_wait.get("total"), "max_ms": fetch_wait.get("max")} if fetch_wait else None,
            "partitions": partitions, "files": files,
            "metrics": {p["name"]: {k: p[k] for k in ("total", "min", "med", "max", "unit", "max_stage") if k in p}
                        for p in parsed},
        })
    # Operators whose metrics carry no "(stage N: task M)" (scans, filters, projects) ran in the same
    # stage as the first operator above them in the plan that does. An exchange spans two stages:
    # its map side is the stage of its shuffle write.
    by_id = {r["node_id"]: r for r in rows}
    parent = {e["fromId"]: e["toId"] for e in (edges or []) if "fromId" in e and "toId" in e}

    def write_stage(r: Dict[str, Any]) -> List[int]:
        for name in ("shuffle bytes written", "shuffle write time", "data size", "number of output bytes"):
            m = r["metrics"].get(name)
            if m and "max_stage" in m:
                return [m["max_stage"]]
        return r["stages"]

    for r in rows:
        if r["stages"]:
            continue
        node, hops = r["node_id"], 0
        while node in parent and hops < 12:
            node, hops = parent[node], hops + 1
            up = by_id.get(node)
            if up and up["stages"]:
                r["stages"] = write_stage(up) if "Exchange" in up["operator"] else up["stages"]
                r["stage_inherited"] = True
                break
    return rows


def physical_plan(text: str) -> str:
    marker = text.find("== Physical Plan ==")
    text = text[marker:] if marker >= 0 else text
    return text[:PLAN_CHARS] + ("\n... (truncated)" if len(text) > PLAN_CHARS else "")


def collect(base: str, app: Optional[str], ui_base: Optional[str], top_stages: int, top_sql: int) -> Dict[str, Any]:
    app = app or get(base, "applications?limit=1")[0]["id"]
    info = get(base, f"applications/{app}")
    attempts = info.get("attempts", [])
    attempt = attempts[-1] if attempts else {}
    attempt_path = f"{app}/{attempt['attemptId']}" if attempt.get("attemptId") else app
    app_start = parse_ts(attempt.get("startTime"))
    app_end = parse_ts(attempt.get("endTime")) if attempt.get("completed") else None
    duration = attempt.get("duration", 0) or ((app_end - app_start) if app_start and app_end else 0)

    env = get_or(base, f"applications/{attempt_path}/environment", {})
    executors = get_or(base, f"applications/{attempt_path}/allexecutors", [])
    jobs = get_or(base, f"applications/{attempt_path}/jobs", [])
    all_stages = get_or(base, f"applications/{attempt_path}/stages", [])
    sql_list = get_or(base, f"applications/{attempt_path}/sql?length=2000", [])

    if not ui_base:
        candidate = f"{base}/history/{app}"
        ui_base = candidate if url_ok(f"{candidate}/jobs/") else base

    # --- cluster
    workers = [e for e in executors if e.get("id") != "driver"] or executors
    slots = sum(e.get("totalCores", 0) for e in workers if e.get("isActive", True)) or \
        sum(e.get("totalCores", 0) for e in workers)
    mem_per_slot = None
    sized = [e for e in workers if e.get("maxMemory") and e.get("totalCores")]
    if sized:
        mem_per_slot = sized[0]["maxMemory"] / sized[0]["totalCores"]
    props = dict(env.get("sparkProperties", []))
    runtime = env.get("runtime", {})
    exec_rows = executor_profile(executors, app_start)
    worker_rows = [e for e in exec_rows if not e["is_driver"]] or exec_rows
    first_added = min((e["added_s"] for e in worker_rows if e["added_s"] is not None), default=None)
    last_added = max((e["added_s"] for e in worker_rows if e["added_s"] is not None), default=None)

    # --- jobs, stages, sql mapping
    stage_to_job: Dict[int, int] = {}
    for j in jobs:
        for sid in j.get("stageIds", []):
            stage_to_job[sid] = j["jobId"]
    job_to_sql: Dict[int, int] = {}
    for x in sql_list:
        for jid in (x.get("successJobIds") or []) + (x.get("failedJobIds") or []) + (x.get("runningJobIds") or []):
            job_to_sql[jid] = x["id"]

    latest: Dict[int, Dict[str, Any]] = {}
    for s in all_stages:
        sid = s["stageId"]
        if sid not in latest or (s.get("attemptId", 0) >= latest[sid].get("attemptId", 0)):
            latest[sid] = s
    ran = [s for s in latest.values() if s.get("status") in ("COMPLETE", "FAILED") and s.get("submissionTime")]

    def stage_base(s: Dict[str, Any]) -> Dict[str, Any]:
        submitted = parse_ts(s.get("submissionTime"))
        start = parse_ts(s.get("firstTaskLaunchedTime")) or submitted
        end = parse_ts(s.get("completionTime")) or (app_end if s.get("status") == "COMPLETE" else None)
        jid = stage_to_job.get(s["stageId"])
        run_ms = s.get("executorRunTime", 0) or 0
        cpu_ms = (s.get("executorCpuTime", 0) or 0) / 1e6
        return {
            "stage_id": s["stageId"], "attempt": s.get("attemptId", 0), "status": s.get("status"),
            "name": (s.get("name") or "")[:120], "description": (s.get("description") or "")[:160],
            "job_id": jid, "sql_id": job_to_sql.get(jid) if jid is not None else None,
            "start_ms": start, "end_ms": end, "submitted_ms": submitted,
            "start_s": (start - app_start) / 1000 if start and app_start else None,
            "submitted_s": (submitted - app_start) / 1000 if submitted and app_start else None,
            # wall_ms is the active time: first task launched -> completion. The time between
            # submission and the first task (waiting for a free slot or for executors) is queued_ms.
            "wall_ms": (end - start) if start and end else 0.0,
            "queued_ms": (start - submitted) if start and submitted else 0.0,
            "tasks": s.get("numTasks", 0), "failed_tasks": s.get("numFailedTasks", 0),
            "input_bytes": s.get("inputBytes", 0), "input_records": s.get("inputRecords", 0),
            "output_bytes": s.get("outputBytes", 0), "output_records": s.get("outputRecords", 0),
            "shuffle_read_bytes": s.get("shuffleReadBytes", 0), "shuffle_read_records": s.get("shuffleReadRecords", 0),
            "shuffle_write_bytes": s.get("shuffleWriteBytes", 0), "shuffle_write_records": s.get("shuffleWriteRecords", 0),
            "spill_memory_bytes": s.get("memoryBytesSpilled", 0), "spill_disk_bytes": s.get("diskBytesSpilled", 0),
            "executor_run_ms": run_ms, "executor_cpu_ms": cpu_ms, "cpu_share": ratio(cpu_ms, run_ms) if run_ms else None,
            "gc_ms": s.get("jvmGcTime", 0), "fetch_wait_ms": s.get("shuffleFetchWaitTime", 0),
            "peak_execution_memory_bytes": s.get("peakExecutionMemory", 0),
            "ui_url": f"{ui_base}/stages/stage/?id={s['stageId']}&attempt={s.get('attemptId', 0)}",
        }

    stage_rows = [stage_base(s) for s in ran]
    stage_rows.sort(key=lambda r: r["wall_ms"], reverse=True)
    total_stage_wall = sum(r["wall_ms"] for r in stage_rows) or 1.0
    for r in stage_rows:
        r["share_of_app"] = ratio(r["wall_ms"], duration) if duration else None

    # Detailed profile of the longest stages.
    detailed = stage_rows[:top_stages]
    for r in detailed:
        path = f"applications/{attempt_path}/stages/{r['stage_id']}/{r['attempt']}"
        summary = get_or(base, f"{path}/taskSummary?quantiles={QUANTILES}", {})
        dur = quantiles(summary, "duration") if "duration" in summary else quantiles(summary, "executorRunTime")
        in_q = quantiles(summary, "inputMetrics", "bytesRead")
        sr_q = quantiles(summary, "shuffleReadMetrics", "readBytes")
        r.update({
            "task_duration_ms": dur,
            "task_cpu_ms": [x / 1e6 for x in quantiles(summary, "executorCpuTime")],
            "input_bytes_per_task": in_q, "shuffle_read_per_task": sr_q,
            "bytes_per_task": [max(a, b) for a, b in zip(in_q, sr_q)],
            "shuffle_write_per_task": quantiles(summary, "shuffleWriteMetrics", "writeBytes"),
            "spill_disk_per_task": quantiles(summary, "diskBytesSpilled"),
            "gc_per_task_ms": quantiles(summary, "jvmGcTime"),
            "scheduler_delay_ms": quantiles(summary, "schedulerDelay"),
            "fetch_wait_per_task_ms": quantiles(summary, "shuffleReadMetrics", "fetchWaitTime"),
            "peak_execution_memory_per_task": quantiles(summary, "peakExecutionMemory"),
        })
        r["executor_balance"] = {}
        if r["tasks"] <= STAGE_DETAIL_MAX_TASKS and r["wall_ms"] > LONG_STAGE_MS:
            detail = get_or(base, path, {})   # includes every task; the only place executorSummary is served
            detail = detail[0] if isinstance(detail, list) and detail else detail
            r["executor_balance"] = executor_balance(detail if isinstance(detail, dict) else {})
        r["signals"] = stage_signals(r, slots, mem_per_slot)   # first pass: decides which task lists to fetch
        r["profiled"] = True
    for r in stage_rows[top_stages:]:
        r["profiled"] = False
        r["signals"] = []

    # Slowest tasks for the flagged stages: tells data skew from a straggler executor.
    flagged = detailed[:3] + [r for r in detailed[3:] if any(s["kind"] in ("SKEW", "TOO_BIG", "IDLE", "LOW_CPU", "GC")
                                                               for s in r["signals"])]
    flagged = [r for r in flagged if r["tasks"] > 0][:MAX_TASK_LISTS]
    for r in flagged:
        path = f"applications/{attempt_path}/stages/{r['stage_id']}/{r['attempt']}/taskList?sortBy=-runtime&length={SLOW_TASKS}"
        tasks = get_or(base, path, [])
        rows = []
        for t in tasks:
            tm = t.get("taskMetrics") or {}
            in_b = (tm.get("inputMetrics") or {}).get("bytesRead", 0) or 0
            sr = tm.get("shuffleReadMetrics") or {}
            sr_b = (sr.get("localBytesRead", 0) or 0) + (sr.get("remoteBytesRead", 0) or 0)
            rows.append({"task_id": t.get("taskId"), "executor": t.get("executorId"), "host": t.get("host"),
                         "locality": t.get("taskLocality"), "status": t.get("status"),
                         "duration_ms": t.get("duration", 0), "input_bytes": in_b, "shuffle_read_bytes": sr_b,
                         "bytes": max(in_b, sr_b), "spill_disk_bytes": tm.get("diskBytesSpilled", 0),
                         "gc_ms": tm.get("jvmGcTime", 0), "cpu_ms": (tm.get("executorCpuTime", 0) or 0) / 1e6,
                         "run_ms": tm.get("executorRunTime", 0), "fetch_wait_ms": sr.get("fetchWaitTime", 0),
                         "launched_s": ((parse_ts(t.get("launchTime")) or 0) - app_start) / 1000 if app_start and t.get("launchTime") else None})
        r["slowest_tasks"] = rows
        r["signals"] = stage_signals(r, slots, mem_per_slot)   # second pass with the slowest task's own bytes
        hosts = {x["executor"] for x in rows}
        if len(rows) >= 3 and len(hosts) == 1:
            r["signals"].append(signal("SAME_EXECUTOR", "medium", "The slowest tasks all ran on one executor",
                                       f"the {len(rows)} slowest tasks ran on executor {rows[0]['executor']} ({rows[0]['host']})",
                                       ["a slow or overloaded node (noisy neighbour, disk, network), or that executor's GC",
                                        "locality: tasks for one set of files all preferred that executor"],
                                       f"Stages tab, stage {r['stage_id']}: Aggregated Metrics by Executor; Executors tab"))

    # --- jobs
    job_rows = []
    stage_by_id = {r["stage_id"]: r for r in stage_rows}
    for j in sorted(jobs, key=lambda j: j["jobId"]):
        start = parse_ts(j.get("submissionTime"))
        end = parse_ts(j.get("completionTime"))
        sids = sorted(j.get("stageIds", []))
        job_rows.append({
            "job_id": j["jobId"], "status": j.get("status"), "description": (j.get("description") or j.get("name") or "")[:160],
            "sql_id": job_to_sql.get(j["jobId"]),
            "start_s": (start - app_start) / 1000 if start and app_start else None,
            "duration_ms": (end - start) if start and end else None,
            "stage_ids": sids, "stages_run": [s for s in sids if s in stage_by_id],
            "stages_skipped": [s for s in sids if s not in stage_by_id],
            "tasks": j.get("numTasks", 0), "failed_tasks": j.get("numFailedTasks", 0),
            "ui_url": f"{ui_base}/jobs/job/?id={j['jobId']}",
        })

    # --- sql executions
    sql_rows = []
    def has_jobs(x: Dict[str, Any]) -> bool:
        return bool((x.get("successJobIds") or []) + (x.get("failedJobIds") or []) + (x.get("runningJobIds") or []))
    ranked = sorted((x for x in sql_list if has_jobs(x)), key=lambda x: x.get("duration", 0) or 0, reverse=True)
    detailed_ids = {x["id"] for x in ranked[:top_sql]}
    for x in sorted(sql_list, key=lambda x: x["id"]):
        jids = sorted((x.get("successJobIds") or []) + (x.get("failedJobIds") or []) + (x.get("runningJobIds") or []))
        sids = sorted({sid for jid in jids for sid in next((j["stageIds"] for j in jobs if j["jobId"] == jid), [])})
        row = {"id": x["id"], "status": x.get("status"), "duration_ms": x.get("duration", 0),
               "description": (x.get("description") or "").replace("\n", " ")[:200],
               "start_s": ((parse_ts(x.get("submissionTime")) or 0) - app_start) / 1000 if app_start and x.get("submissionTime") else None,
               "job_ids": jids, "stage_ids": sids, "stages_run": [s for s in sids if s in stage_by_id],
               "ui_url": f"{ui_base}/SQL/execution/?id={x['id']}", "detailed": x["id"] in detailed_ids,
               "wrapper": not has_jobs(x)}
        if x["id"] in detailed_ids:
            rank = next(i for i, y in enumerate(ranked) if y["id"] == x["id"])
            ex = get_or(base, f"applications/{attempt_path}/sql/{x['id']}?details=true&planDescription="
                              f"{'true' if rank < PLANS_WITH_TEXT else 'false'}", {})
            nodes = ex.get("nodes", []) if isinstance(ex, dict) else []
            row["patterns"] = classify_sql(nodes)
            row["operators"] = operator_rows(nodes, ex.get("edges", []) if isinstance(ex, dict) else [])
            row["scan_metrics_unavailable"] = sorted({n.get("nodeName", "") for n in nodes if n.get("nodeName", "").startswith("BatchScan")
                                                      and n.get("metrics") and all((m.get("value") or "N/A") == "N/A" for m in n["metrics"])})
            row["physical_plan"] = physical_plan(ex.get("planDescription") or "") if rank < PLANS_WITH_TEXT else ""
        sql_rows.append(row)

    # --- name each stage by the operators that ran in it (from the detailed SQL executions)
    skip_ops = ("WholeStageCodegen", "InputAdapter", "InputIterator", "ColumnarToRow", "RowToColumnar", "VeloxColumnarToRow",
                "VeloxResizeBatches", "AdaptiveSparkPlan", "AQEShuffleRead", "ReusedExchange", "Project", "Filter", "Subquery")
    stage_ops: Dict[int, List[str]] = {}
    for x in sql_rows:
        for o in x.get("operators", []):
            name = o["operator"]
            if name.startswith(skip_ops) or "ProjectExec" in name or "FilterExec" in name:
                continue
            short = re.sub(r"ExecTransformer|Transformer|Exec\b", "", name.split(" (")[0])[:48]
            for sid in o["stages"]:
                lst = stage_ops.setdefault(sid, [])
                if short not in lst and len(lst) < 8:
                    lst.append(short)
    for r in stage_rows:
        r["operators"] = stage_ops.get(r["stage_id"], [])

    # --- app-level signals
    app_signals = []
    tl = timeline(stage_rows, app_start, app_end)
    if tl["no_stage_share"] and tl["no_stage_share"] >= 0.2 and duration > 60_000:
        biggest = tl["gaps"][0] if tl["gaps"] else None
        app_signals.append(signal("DRIVER_TIME", "high" if tl["no_stage_share"] >= 0.4 else "medium",
                                  "A large share of the run had no stage running",
                                  f"{fmt_ms(tl['no_stage_ms'])} of {fmt_ms(duration)} ({fmt_pct(tl['no_stage_share'])}) with no stage running"
                                  + (f"; the longest gap is {fmt_ms(biggest['duration_ms'])} after {biggest['after']}" if biggest else ""),
                                  ["driver-side work: query planning and file listing (Iceberg/Hudi metadata), table commits, "
                                   "collect() results processed in Python, or waiting for executors to start",
                                   "on a History Server the gap before the first stage includes executor start-up"],
                                  "Jobs tab, Event Timeline: the spaces between job bars; driver logs for the same timestamps"))
    first_submit = min((r["submitted_s"] for r in stage_rows if r["submitted_s"] is not None), default=None)
    first_launch = min((r["start_s"] for r in stage_rows if r["start_s"] is not None), default=None)
    if first_added is not None and first_submit is not None and first_added - first_submit >= 10:
        app_signals.append(signal("EXECUTOR_STARTUP", "medium", "The first stage waited for executors to start",
                                  f"the first stage was submitted at {first_submit:.0f} s, the first executor joined at {first_added:.0f} s"
                                  + (f", the first task launched at {first_launch:.0f} s" if first_launch is not None else ""),
                                  ["executor pods or containers took that long to be scheduled, pulled and started",
                                   "this time counts as queued time on the first stages, not as task time"],
                                  "Jobs tab Event Timeline: executor added events vs the first stage; Executors tab Add Time"))
    if first_added is not None and last_added is not None and last_added - first_added >= 20:
        app_signals.append(signal("LATE_EXECUTORS", "low", "Executors joined over a long period",
                                  f"first executor at {first_added:.0f} s, last at {last_added:.0f} s after application start",
                                  ["dynamic allocation ramp-up, or slow pod scheduling / node provisioning",
                                   "early stages ran on fewer slots than the later ones"],
                                  "Executors tab: Add Time column; Jobs tab Event Timeline (executor added events)"))
    lost = [e for e in worker_rows if e["remove_reason"]]
    if lost:
        app_signals.append(signal("EXECUTORS_LOST", "high", "Executors were removed during the run",
                                  f"{len(lost)} executors removed; first reason: {lost[0]['remove_reason'][:160]}",
                                  ["memory limit exceeded (exit code 137 / OOMKilled), node eviction or spot interruption, "
                                   "or dynamic allocation idle timeout (harmless if idle)",
                                   "lost executors force shuffle files to be recomputed"],
                                  "Executors tab: Remove Reason column"))
    gc_execs = [e for e in worker_rows if e["gc_share"] and e["gc_share"] >= GC_SHARE and e["task_time_ms"] > 60_000]
    if gc_execs:
        app_signals.append(signal("GC_HEAVY_EXECUTORS", "medium", "Some executors spent a large share of task time in GC",
                                  f"{len(gc_execs)} of {len(worker_rows)} executors with GC above {fmt_pct(GC_SHARE)} of task time "
                                  f"(highest {fmt_pct(max(e['gc_share'] for e in gc_execs))})",
                                  ["the heap per task slot is small for the objects the tasks create",
                                   "cached data in deserialized form on the heap"],
                                  "Executors tab: GC Time vs Task Time"))
    full = [e for e in worker_rows if e["max_memory_bytes"] and e["peak_storage_bytes"] >= 0.9 * e["max_memory_bytes"]]
    if full:
        app_signals.append(signal("STORAGE_FULL", "medium", "Cached data filled the unified memory pool on some executors",
                                  f"{len(full)} of {len(worker_rows)} executors reached a peak storage memory of "
                                  f"{fmt_bytes(max(e['peak_storage_bytes'] for e in full))} out of {fmt_bytes(full[0]['max_memory_bytes'])}",
                                  ["cache() / persist() of a dataset larger than the storage share, so blocks were evicted and "
                                   "recomputed, and execution memory for joins, sorts and aggregates was squeezed (more spill, more GC)",
                                   "if the cached data is only read once or twice, caching may cost more than it saves"],
                                  "Storage tab (while the app runs): Fraction Cached, Size in Memory / on Disk; Executors tab: Storage Memory"))
    task_times = sorted(e["task_time_ms"] for e in worker_rows if e["task_time_ms"])
    if len(task_times) >= 3 and task_times[-1] >= EXEC_IMBALANCE * task_times[len(task_times) // 2]:
        app_signals.append(signal("UNEVEN_EXECUTOR_LOAD", "low", "Executors did uneven amounts of work over the run",
                                  f"busiest executor {fmt_ms(task_times[-1])} of task time vs median {fmt_ms(task_times[len(task_times) // 2])}",
                                  ["executors that joined late, or locality preferences",
                                   "uneven tasks that landed on one executor"],
                                  "Executors tab: Task Time column"))

    return {
        "app": {"id": app, "name": info.get("name"), "user": attempt.get("sparkUser"), "spark_version": attempt.get("appSparkVersion"),
                "start_time": attempt.get("startTime"), "end_time": attempt.get("endTime"), "duration_ms": duration,
                "completed": attempt.get("completed"), "attempt_id": attempt.get("attemptId"), "ui_url": f"{ui_base}/jobs/",
                "rest_url": f"{base}/api/v1/applications/{attempt_path}", "java": runtime.get("javaVersion")},
        "cluster": {"executors": len(worker_rows), "task_slots": slots,
                    "cores_per_executor": sized[0]["totalCores"] if sized else None,
                    "memory_per_executor_bytes": sized[0]["maxMemory"] if sized else None,
                    "memory_per_task_slot_bytes": mem_per_slot,
                    "executor_memory_conf": props.get("spark.executor.memory"),
                    "executor_memory_overhead_conf": props.get("spark.executor.memoryOverhead") or props.get("spark.executor.memoryOverheadFactor"),
                    "offheap_conf": props.get("spark.memory.offHeap.size") if props.get("spark.memory.offHeap.enabled") == "true" else None,
                    "dynamic_allocation": props.get("spark.dynamicAllocation.enabled") == "true",
                    "first_executor_added_s": first_added, "last_executor_added_s": last_added,
                    "native_engine": any(x.get("patterns", {}).get("native_engine") for x in sql_rows)},
        "timeline": tl,
        "signals": app_signals,
        "jobs": job_rows,
        "stages": stage_rows,
        "stage_wall_total_ms": total_stage_wall,
        "sql": sql_rows,
        "executors": exec_rows,
        "configs": {k: v for k, v in sorted(props.items()) if k.startswith(INTERESTING_CONFS)},
        "thresholds": {"uneven_ratio": UNEVEN_RATIO, "uneven_min_max_ms": UNEVEN_MIN_MAX_MS, "tiny_task_bytes": TINY_TASK_BYTES,
                       "tiny_task_ms": TINY_TASK_MS, "gc_share": GC_SHARE, "low_cpu_share": LOW_CPU_SHARE,
                       "fetch_wait_share": FETCH_WAIT_SHARE, "gap_min_ms": GAP_MIN_MS, "long_stage_ms": LONG_STAGE_MS},
    }


# ------------------------------------------------------------------ HTML
# The page holds only what the Spark UI does not show in one place: the analysis, the timeline
# with queued time and driver gaps, jobs -> stages -> SQL, the critical-path stages with their
# signals, and the operators that ran in those stages. Everything else is a link into the UI.

CSS = """
:root { --bg:#fafafa; --fg:#222; --muted:#666; --line:#e0e0e0; --head:#2d2d2d; --code:#f0f0f0; --link:#0b5fa5;
        --callout:#eef4fb; --callout-line:#4a7fb5; --high:#f8d7da; --high-line:#dc3545; --med:#fff3cd; --med-line:#d9a400;
        --low:#e9ecef; --low-line:#8a949e; --bar:#4a7fb5; --gap:#d9d9d9; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#16181c; --fg:#e6e6e6; --muted:#9aa0a6; --line:#2c3036; --head:#0d0f12; --code:#23272e; --link:#7cb4ea;
          --callout:#1f2a38; --callout-line:#5b8fc7; --high:#3d2326; --high-line:#e5534b; --med:#3a3220; --med-line:#c9a227;
          --low:#2a2e33; --low-line:#6c757d; --bar:#5b8fc7; --gap:#3a3f46; } }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; background: var(--bg); color: var(--fg); }
main { max-width: 1180px; margin: 0 auto; padding: 20px 16px 40px; }
a { color: var(--link); }
h1 { font-size: 21px; margin-bottom: 4px; }
h2 { font-size: 17px; margin-top: 30px; padding-top: 8px; border-top: 1px solid var(--line); }
h3 { font-size: 15px; margin-top: 18px; }
h4 { font-size: 14px; margin: 14px 0 4px; }
.subtitle { color: var(--muted); font-size: 13px; margin-bottom: 12px; line-height: 1.6; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13px; margin-bottom: 14px; }
th { background: var(--head); color: #fff; padding: 6px 8px; text-align: left; font-weight: 600; font-size: 12px;
     text-transform: uppercase; letter-spacing: 0.4px; white-space: nowrap; vertical-align: bottom; }
td { padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
td.num { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
.main { font-size: 14px; font-weight: 700; } .sub { font-size: 11px; color: var(--muted); margin-top: 2px; }
.sig { display: inline-block; font-size: 11px; font-weight: 700; padding: 1px 6px; border-radius: 3px; margin: 0 4px 3px 0; }
.sig.high { background: var(--high); border-left: 3px solid var(--high-line); }
.sig.medium { background: var(--med); border-left: 3px solid var(--med-line); }
.sig.low { background: var(--low); border-left: 3px solid var(--low-line); }
.ok { color: var(--muted); }
.analysis { max-width: 900px; line-height: 1.6; } .analysis table { max-width: 100%; }
.callout { background: var(--callout); border-left: 4px solid var(--callout-line); padding: 8px 12px; margin: 12px 0; font-size: 13px; line-height: 1.5; }
code { background: var(--code); padding: 1px 5px; border-radius: 3px; font-size: 12px; }
pre { background: var(--code); border: 1px solid var(--line); border-radius: 4px; padding: 10px 14px; font-size: 12px; overflow-x: auto; line-height: 1.4; }
.tl { position: relative; width: 100%; height: 18px; background: var(--gap); border-radius: 2px; margin: 2px 0; }
.tl .bar { position: absolute; top: 2px; height: 14px; background: var(--bar); border-radius: 2px; min-width: 2px; opacity: 0.9; }
.tl .bar.hot { background: var(--high-line); } .tl .bar.warm { background: var(--med-line); }
.axis { display: flex; justify-content: space-between; font-size: 11px; color: var(--muted); margin-top: 2px; }
.links a { margin-right: 14px; }
"""

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
NO_SIGNAL = "<span class='ok'>no signal</span>"
CRITICAL_SHARE = 0.8          # stages shown: the longest ones until they cover this share of stage time
MAX_CRITICAL_STAGES = 6
MAX_OPERATORS = 14


def heat(value: Optional[float], top: Optional[float], color: str = "220, 53, 69") -> str:
    if not top or not value or value <= 0:
        return ""
    alpha = min(0.55, 0.55 * value / top)
    return f' style="background: rgba({color}, {alpha:.2f})"'


def sorted_signals(sigs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(sigs, key=lambda x: SEVERITY_ORDER[x["severity"]])


def sig_html(s: Dict[str, Any]) -> str:
    """The signal tag and its evidence; the possible causes stay in facts.json and SKILL.md."""
    e = html.escape
    return (f"<span class='sig {e(s['severity'])}' title='Verify: {e(s['check_in_ui'])}'>{e(s['kind'])}</span>"
            f"<span class='sub'>{e(s['evidence'])}</span>")


def critical_stages(facts: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The longest stages that together cover most of the stage time, plus any high-severity stage."""
    stages = [s for s in facts["stages"] if s.get("profiled")]
    total = sum(s["wall_ms"] for s in facts["stages"]) or 1.0
    out, acc = [], 0.0
    for s in stages:
        if (acc >= CRITICAL_SHARE * total or len(out) >= MAX_CRITICAL_STAGES) and \
                not any(x["severity"] == "high" for x in s["signals"]):
            continue
        out.append(s)
        acc += s["wall_ms"]
    return out


def render_timeline(facts: Dict[str, Any]) -> str:
    e = html.escape
    dur = facts["app"]["duration_ms"] or 1
    stages = facts["stages"]
    top_wall = max((s["wall_ms"] for s in stages), default=0)
    out = ["<div class='scroll'><table><tr><th>Job</th><th style='width:70%'>Stages over the run</th><th>Duration</th></tr>"]
    for j in facts["jobs"]:
        bars = []
        for sid in j["stages_run"]:
            s = next((x for x in stages if x["stage_id"] == sid), None)
            if not s or s["start_s"] is None:
                continue
            left = 100 * (s["start_s"] * 1000) / dur
            width = max(100 * s["wall_ms"] / dur, 0.15)
            cls = "hot" if top_wall and s["wall_ms"] >= 0.5 * top_wall else ("warm" if top_wall and s["wall_ms"] >= 0.2 * top_wall else "")
            title = f"stage {sid}: {fmt_ms(s['wall_ms'])} active, {fmt_ms(s['queued_ms'])} queued, {plural(s['tasks'], 'task')}, " \
                    + (", ".join(s.get("operators") or []) or s["name"][:60])
            bars.append(f"<a class='bar {cls}' href='{e(s['ui_url'])}' style='left:{left:.2f}%;width:{width:.2f}%' title='{e(title)}'></a>")
        sub = e(j["description"][:70]) + (f" &middot; SQL {j['sql_id']}" if j["sql_id"] is not None else "")
        out.append(f"<tr><td><a href='{e(j['ui_url'])}'>job {j['job_id']}</a><div class='sub'>{sub}</div></td>"
                   f"<td><div class='tl'>{''.join(bars)}</div></td>"
                   f"<td class='num'>{fmt_ms(j['duration_ms']) if j['duration_ms'] is not None else '-'}</td></tr>")
    out.append("</table></div>")
    out.append(f"<div class='axis'><span>0 s</span><span>{fmt_ms(dur / 2)}</span><span>{fmt_ms(dur)}</span></div>")
    return "".join(out)


def render_html(facts: Dict[str, Any], analysis: str) -> str:
    e = html.escape
    app, cl, tl = facts["app"], facts["cluster"], facts["timeline"]
    stages = facts["stages"]
    sby = {s["stage_id"]: s for s in stages}
    ui = app["ui_url"].rsplit("/jobs/", 1)[0]
    conf = facts["configs"]
    conf_line = ", ".join(f"{k.split('.')[-1]}={v}" for k, v in conf.items()
                          if k in ("spark.sql.shuffle.partitions", "spark.sql.adaptive.enabled", "spark.sql.autoBroadcastJoinThreshold",
                                   "spark.executor.memory", "spark.executor.cores", "spark.executor.instances", "spark.dynamicAllocation.enabled"))
    out = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
           f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
           f"<title>Spark profile: {e(str(app['name']))}</title><style>{CSS}</style></head><body><main>",
           f"<h1>Spark profile: {e(str(app['name']))}</h1>",
           f"<div class='subtitle'><a href='{e(app['ui_url'])}'>{e(app['id'])}</a> &middot; {fmt_ms(app['duration_ms'])} &middot; "
           f"Spark {e(str(app['spark_version']))} &middot; started {e(str(app['start_time']))}<br>"
           f"{cl['executors']} executors &times; {cl['cores_per_executor'] or '?'} cores = {cl['task_slots']} task slots &middot; "
           f"{fmt_bytes(cl['memory_per_executor_bytes'])} unified memory per executor, about {fmt_bytes(cl['memory_per_task_slot_bytes'])} per task slot"
           + (f" &middot; off-heap {e(str(cl['offheap_conf']))}" if cl["offheap_conf"] else "")
           + (" &middot; native engine" if cl["native_engine"] else "")
           + (f"<br><code>{e(conf_line)}</code>" if conf_line else "")
           + f"<br><span class='links'>Spark UI: <a href='{e(ui)}/jobs/'>Jobs</a><a href='{e(ui)}/stages/'>Stages</a>"
             f"<a href='{e(ui)}/SQL/'>SQL</a><a href='{e(ui)}/executors/'>Executors</a><a href='{e(ui)}/environment/'>Environment</a></span></div>"]

    # ---- analysis
    out.append("<h2 id='analysis'>Analysis</h2>")
    out.append(f"<section class='analysis'>{analysis}</section>" if analysis else
               "<p class='ok'>No analysis fragment was supplied (pass --analysis).</p>")

    # ---- timeline
    out.append("<h2 id='timeline'>Where the wall-clock time went</h2>")
    out.append(f"<p>Stages were active for {fmt_ms(tl['busy_ms'])} of the {fmt_ms(app['duration_ms'])} run. For {fmt_ms(tl['no_stage_ms'])} "
               f"({fmt_pct(tl['no_stage_share'])}) no stage was running: executor start-up, driver work (planning, metadata, commits) "
               f"or code between actions. Red bars are the longest stages; hover a bar for its stage.</p>")
    out.append(render_timeline(facts))
    gaps = [g for g in tl["gaps"] if g["duration_ms"] >= 0.03 * (app["duration_ms"] or 1)][:5]
    if gaps:
        out.append("<div class='scroll'><table><tr><th>At</th><th>No stage for</th><th>After</th><th>Before</th></tr>")
        out += [f"<tr><td class='num'>{fmt_s(g['start_s'])}</td><td class='num'>{fmt_ms(g['duration_ms'])}</td>"
                f"<td>{e(g['after'][:90])}</td><td>{e(g['before'][:90])}</td></tr>" for g in gaps]
        out.append("</table></div>")
    if facts["signals"]:
        out += [f"<div>{sig_html(s)}</div>" for s in sorted_signals(facts["signals"])]

    # ---- jobs -> stages -> sql
    out.append("<h2 id='jobs'>Jobs, their stages, and the SQL execution they belong to</h2>")
    out.append("<div class='scroll'><table><tr><th>Job</th><th>Description</th><th>SQL</th><th>At</th><th>Duration</th>"
               "<th>Stages run (active time, tasks)</th><th>Skipped</th></tr>")
    for j in facts["jobs"]:
        parts = []
        for sid in j["stages_run"]:
            s = sby[sid]
            flag = " <span class='sig high'>!</span>" if any(x["severity"] == "high" for x in s.get("signals", [])) else ""
            parts.append(f"<a href='{e(s['ui_url'])}'>{sid}</a> ({fmt_ms(s['wall_ms'])}, {plural(s['tasks'], 'task')}){flag}")
        sql = f"<a href='{e(next((x['ui_url'] for x in facts['sql'] if x['id'] == j['sql_id']), '#'))}'>{j['sql_id']}</a>" \
            if j["sql_id"] is not None else "-"
        status = "" if j["status"] == "SUCCEEDED" else f" <span class='sig high'>{e(str(j['status']))}</span>"
        out.append(f"<tr><td><a href='{e(j['ui_url'])}'>{j['job_id']}</a>{status}</td><td>{e(j['description'][:90])}</td>"
                   f"<td>{sql}</td><td class='num'>{fmt_s(j['start_s'])}</td><td class='num'>{fmt_ms(j['duration_ms'])}</td>"
                   f"<td>{', '.join(parts) or '-'}</td><td class='num'>{len(j['stages_skipped']) or ''}</td></tr>")
    out.append("</table></div>")

    # ---- critical-path stages
    crit = critical_stages(facts)
    out.append("<h2 id='stages'>Stages on the critical path</h2>")
    out.append("<div class='callout'>Active time runs from the first task to completion; queued time (waiting for a slot or for executors) "
               "is shown separately. Compare the slowest task with the median, the data per task with the memory per task slot "
               f"(about {fmt_bytes(cl['memory_per_task_slot_bytes'])}), and the CPU share. Hover a signal for where to verify it in the UI.</div>")
    top = {"wall": max((s["wall_ms"] for s in crit), default=0), "max_task": max((s["task_duration_ms"][4] for s in crit), default=0),
           "spill": max((s["spill_disk_bytes"] or 0 for s in crit), default=0)}
    out.append("<div class='scroll'><table><tr><th>Stage</th><th>Active</th><th>Tasks</th><th>Task duration<br>median / max</th><th>CPU</th>"
               "<th>Data per task<br>median / max</th><th>Peak exec memory<br>per task, max</th><th>Shuffle write</th><th>Spill (disk)</th><th>Signals</th></tr>")
    for s in crit:
        d, b = s["task_duration_ms"], s["bytes_per_task"]
        pk = s.get("peak_execution_memory_per_task") or [0] * 5
        ops_line = ", ".join(s.get("operators") or []) or s["name"][:60]
        where = f"job {s['job_id']}" + (f", SQL {s['sql_id']}" if s["sql_id"] is not None else "")
        sigs = "".join(f"<div>{sig_html(x)}</div>" for x in sorted_signals(s["signals"]))
        slow = (s.get("slowest_tasks") or [None])[0]
        if slow and s["signals"]:
            dx, bx = ratio(slow["duration_ms"], d[2]), ratio(slow["bytes"], b[2])
            sigs += (f"<div class='sub'>slowest task {slow['task_id']} on executor {e(str(slow['executor']))} ({e(str(slow['host']))}): "
                     f"{fmt_ms(slow['duration_ms'])}" + (f", {dx:.1f}x the median duration" if dx else "")
                     + (f", {bx:.1f}x the median data" if bx else "") + (f", {fmt_bytes(slow['spill_disk_bytes'])} spilled" if slow["spill_disk_bytes"] else "") + "</div>")
        output = f"<div class='sub'>wrote {fmt_bytes(s['output_bytes'])}</div>" if s.get("output_bytes") else ""
        out.append(
            f"<tr><td><div class='main'><a href='{e(s['ui_url'])}'>{s['stage_id']}</a></div><div class='sub'>{e(ops_line)}</div><div class='sub'>{where}</div></td>"
            f"<td class='num'{heat(s['wall_ms'], top['wall'])}><div class='main'>{fmt_ms(s['wall_ms'])}</div><div class='sub'>{fmt_pct(s['share_of_app'])} of app, from {fmt_s(s['start_s'])}</div>"
            + (f"<div class='sub'>queued {fmt_ms(s['queued_ms'])}</div>" if (s.get("queued_ms") or 0) >= GAP_MIN_MS else "") + "</td>"
            f"<td class='num'>{fmt_num(s['tasks'])}" + (f"<div class='sub'>{s['failed_tasks']} failed</div>" if s["failed_tasks"] else "") + "</td>"
            f"<td class='num'{heat(d[4], top['max_task'])}>{fmt_ms(d[2])} / {fmt_ms(d[4])}</td>"
            f"<td class='num'>{fmt_pct(s['cpu_share']) if s['cpu_share'] is not None else '-'}</td>"
            f"<td class='num'>{fmt_bytes(b[2])} / {fmt_bytes(b[4])}{output}</td>"
            f"<td class='num'{heat(pk[4], cl['memory_per_task_slot_bytes'] or 0, '111, 66, 193')}>{fmt_bytes(pk[4]) if pk[4] else '-'}</td>"
            f"<td class='num'>{fmt_bytes(s['shuffle_write_bytes'])}</td>"
            f"<td class='num'{heat(s['spill_disk_bytes'], top['spill'])}>{fmt_bytes(s['spill_disk_bytes'])}</td>"
            f"<td style='min-width:240px'>{sigs or NO_SIGNAL}</td></tr>")
    out.append("</table></div>")
    rest = len(stages) - len(crit)
    if rest > 0:
        out.append(f"<p class='sub'>{rest} shorter stages ({fmt_ms(sum(s['wall_ms'] for s in stages if s not in crit))} together) are in "
                   f"<a href='{e(ui)}/stages/'>the Stages tab</a> and in facts.json.</p>")

    # ---- operators that ran in the critical-path stages
    crit_ids = {s["stage_id"] for s in crit}
    rows = []
    for x in facts["sql"]:
        for o in x.get("operators", []):
            if not (set(o["stages"]) & crit_ids) or not (o["time"] or o["spill_bytes"] or o["bytes"]):
                continue
            if o["operator"].startswith(("WholeStageCodegen", "InputAdapter", "InputIterator", "VeloxResizeBatches", "ColumnarToRow")):
                continue
            rows.append((x, o))
    rows.sort(key=lambda r: -((r[1]["time"] or {}).get("max_ms") or 0))
    rows = rows[:MAX_OPERATORS]
    if rows:
        out.append("<h2 id='sql'>Operators in those stages</h2>")
        out.append("<div class='callout'>Which operator the slow tasks spent their time in. The slowest-task time is per task; the total is summed "
                   "over all tasks. 'time in aggregation build' includes the scan below it in the same pipeline. Shuffle bytes are compressed; "
                   "the in-memory size shows how much larger the rows are once read. The full plan is on the SQL execution page.</div>")
        out.append("<div class='scroll'><table><tr><th>Operator</th><th>SQL</th><th>Stage</th><th>Output rows</th><th>Time metric</th>"
                   "<th>Slowest task</th><th>Total over tasks</th><th>Peak memory, max task</th><th>Spill</th><th>Shuffle / bytes</th><th>Partitions</th></tr>")
        top_mx = max(((o["time"] or {}).get("max_ms") or 0 for _, o in rows), default=0)
        for x, o in rows:
            t, m, b = o["time"], o["peak_memory"], o["bytes"]
            stage_max = max((sby[s]["task_duration_ms"][4] for s in o["stages"] if s in sby and sby[s].get("task_duration_ms")), default=None)
            over = t and t.get("max_ms") and stage_max and t["max_ms"] > 1.1 * stage_max
            stg = ", ".join(f"<a href='{e(sby[s]['ui_url'])}'>{s}</a>" if s in sby else str(s) for s in o["stages"])
            out.append(f"<tr><td>{e(o['operator'][:60])}</td><td><a href='{e(x['ui_url'])}'>{x['id']}</a></td><td>{stg}</td>"
                       f"<td class='num'>{fmt_num(o['rows_out']) if o['rows_out'] is not None else '-'}</td><td class='sub'>{e(t['name']) if t else '-'}</td>"
                       f"<td class='num'{heat(t['max_ms'] if t else 0, top_mx)}>{fmt_ms(t['max_ms']) if t and t['max_ms'] is not None else '-'}"
                       + ("<div class='sub'>cumulative: above the stage's slowest task</div>" if over else "") + "</td>"
                       f"<td class='num'>{fmt_ms(t['total_ms']) if t else '-'}</td>"
                       f"<td class='num'>{fmt_bytes(m['max_bytes']) if m else '-'}</td><td class='num'>{fmt_bytes(o['spill_bytes']) if o['spill_bytes'] else '-'}</td>"
                       f"<td class='num'>{fmt_bytes(b['total_bytes']) if b else '-'}"
                       + (f"<div class='sub'>{fmt_bytes(o['data_size_bytes'])} in memory</div>" if o.get("data_size_bytes") else "") + "</td>"
                       f"<td class='num'>{fmt_num(o['partitions']) if o['partitions'] is not None else ''}</td></tr>")
        out.append("</table></div>")
    patterns = [(x["id"], x["patterns"]) for x in facts["sql"] if x.get("patterns") and (set(x["stages_run"]) & crit_ids)]
    notes = []
    for sid, p in patterns:
        bits = [", ".join(p["joins"]) if p["joins"] else "no join", f"{p['exchanges']} exchanges"]
        bits += [b for b, on in (("Python UDF", p["python_udf"]), ("window", p["window"]), (p.get("write_mode"), p.get("write_mode")),
                                 ("AQE did not change any shuffle", not p["aqe_shuffle_read"]),
                                 (f"JVM fallbacks: {', '.join(p['native_fallbacks'])}", p["native_engine"] and p["native_fallbacks"])) if on]
        notes.append(f"SQL {sid}: " + "; ".join(bits))
    if notes:
        out.append("<p class='sub'>" + "<br>".join(e(n) for n in notes) + "</p>")

    out.append(f"<p class='subtitle'>Generated by the spark-ui-profiler skill from <code>{e(app['rest_url'])}</code>. "
               f"Executors, configs, every stage and every operator are in facts.json.</p>")
    out.append("</main></body></html>")
    return "\n".join(out)


# ------------------------------------------------------------------ main

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", help="History Server or live Spark UI base URL (the part before /api/v1)")
    parser.add_argument("--app", help="application id (default: the first app at --url)")
    parser.add_argument("--ui-base", help="base URL for links into the Spark UI (default: <url>/history/<app> when it exists, else <url>)")
    parser.add_argument("--json", help="write the collected facts to this file")
    parser.add_argument("--html", help="write the report page to this file")
    parser.add_argument("--analysis", help="HTML fragment with the written analysis, placed above the tables")
    parser.add_argument("--facts", help="render from a facts JSON written earlier instead of fetching")
    parser.add_argument("--top-stages", type=int, default=TOP_STAGES, help=f"stages profiled in detail (default {TOP_STAGES})")
    parser.add_argument("--top-sql", type=int, default=TOP_SQL, help=f"SQL executions with an operator split (default {TOP_SQL})")
    args = parser.parse_args()
    if not args.url and not args.facts:
        parser.error("pass --url, or --facts to re-render saved facts")

    try:
        facts = json.loads(Path(args.facts).read_text()) if args.facts else \
            collect(args.url.rstrip("/"), args.app, args.ui_base, args.top_stages, args.top_sql)
    except ProfilerError as e:
        sys.exit(f"profile_spark_app.py: {e}")
    if args.json:
        Path(args.json).write_text(json.dumps(facts, indent=2))
    if args.html:
        analysis = Path(args.analysis).read_text() if args.analysis else ""
        Path(args.html).write_text(render_html(facts, analysis))
    if not args.json and not args.html:
        json.dump(facts, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
