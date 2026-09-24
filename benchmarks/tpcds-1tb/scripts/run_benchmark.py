#!/usr/bin/env python3
"""Run the 99 TPC-DS queries against Parquet, Hudi, or Iceberg tables.

This is the large-scale counterpart to ``benchmarks/scripts/run_queries.py``. It reuses
that module's query timing and its table list, and adds three things the 1 TB run needs:
reading from object storage instead of a PVC, registering Hudi and Iceberg tables as well
as Parquet, and running several rounds so a cold first round does not decide the result.

The same script runs on OSS Spark and on Quanton. Nothing in it is engine-specific: the
engine is chosen by the manifest that submits it, so both sides execute identical SQL.

Usage
-----
    run_benchmark.py --format parquet --data-uri s3a://my-bucket/tpcds/parquet/1TB \
        --sql-dir /mnt/sql/tpcds --engine oss --rounds 2
"""

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pyspark.sql import SparkSession  # noqa: E402
from run_queries import TPCDS_TABLES, run_query  # noqa: E402  (shared query timing)


def natural_key(name):
    """Sort q2 before q10, and q24a before q24b."""
    match = re.match(r"^q(\d+)([a-z]*)$", name)
    if match:
        return (int(match.group(1)), match.group(2))
    return (10**6, name)


def register_tables(spark, args):
    """Expose the 24 TPC-DS tables as temporary views, whatever the storage format.

    Registering views rather than switching the session catalog keeps the 99 query files
    byte-identical across all three formats, so the comparison stays honest.
    """
    registered = []
    base = args.data_uri.rstrip("/")
    for table in TPCDS_TABLES:
        try:
            if args.format == "parquet":
                frame = spark.read.parquet("%s/%s" % (base, table))
            elif args.format == "hudi":
                frame = spark.read.format("hudi").load("%s/%s" % (base, table))
                # Drop Hudi's bookkeeping columns and the loader's precombine column so the
                # view exposes exactly the TPC-DS schema.
                frame = frame.drop(*[c for c in frame.columns
                                     if c.startswith("_hoodie_") or c == "_bench_ts"])
            else:
                frame = spark.table("%s.%s.%s" % (args.catalog, args.database, table))
            frame.createOrReplaceTempView(table)
            registered.append(table)
        except Exception as exc:  # noqa: BLE001 - report and keep going
            print("  WARNING: could not register %s: %s" % (table, exc))
    print("Registered %d/%d tables as %s" % (len(registered), len(TPCDS_TABLES), args.format))
    return registered


def select_queries(sql_dir, wanted):
    """Return the SQL files to run, in natural query order."""
    names = [f[:-4] for f in os.listdir(sql_dir) if f.endswith(".sql")]
    if wanted:
        requested = set()
        for item in wanted.split(","):
            item = item.strip().lower()
            if not item:
                continue
            requested.add(item if item.startswith("q") else "q" + item)
        missing = sorted(requested - set(names))
        if missing:
            raise SystemExit("No SQL file for: %s" % ", ".join(missing))
        names = [n for n in names if n in requested]
    names.sort(key=natural_key)
    return [(n, os.path.join(sql_dir, n + ".sql")) for n in names]


def main():
    parser = argparse.ArgumentParser(description="Run TPC-DS queries at scale")
    parser.add_argument("--format", required=True, choices=["parquet", "hudi", "iceberg"])
    parser.add_argument("--data-uri", default="", help="Base URI for parquet or hudi tables")
    parser.add_argument("--catalog", default="lakehouse", help="Iceberg catalog name")
    parser.add_argument("--database", default="tpcds", help="Iceberg namespace")
    parser.add_argument("--sql-dir", default="/mnt/sql/tpcds", help="Directory of .sql files")
    parser.add_argument("--query-numbers", default="", help="Subset, e.g. 1,23,67 or q23a")
    parser.add_argument("--rounds", type=int, default=1, help="Timed rounds over the query set")
    parser.add_argument("--warmup", action="store_true", help="Run one untimed round first")
    parser.add_argument("--engine", default="unknown", help="Label for this run, e.g. oss or quanton")
    parser.add_argument("--output-json", default="")
    args = parser.parse_args()

    if args.format in ("parquet", "hudi") and not args.data_uri:
        raise SystemExit("--data-uri is required for the %s format" % args.format)

    spark = SparkSession.builder.appName(
        "TPC-DS %s %s" % (args.engine, args.format)
    ).getOrCreate()
    spark.sparkContext.setLogLevel("WARN")

    register_tables(spark, args)
    queries = select_queries(args.sql_dir, args.query_numbers)
    print("Running %d queries, %d round(s), warmup=%s" % (len(queries), args.rounds, args.warmup))

    if args.warmup:
        print("--- warmup round (not timed) ---")
        for name, path in queries:
            run_query(spark, path)

    # per_query[name] collects one entry per round; the headline time is the best round,
    # which is the convention TPC-DS reporting uses to exclude one-off cluster noise.
    per_query = {name: [] for name, _ in queries}
    run_start = time.time()
    for round_index in range(1, args.rounds + 1):
        print("--- timed round %d/%d ---" % (round_index, args.rounds))
        for name, path in queries:
            per_query[name].append(run_query(spark, path))
    total_elapsed = time.time() - run_start

    results = []
    for name, _ in queries:
        attempts = per_query[name]
        successes = [a for a in attempts if a["status"] == "success"]
        if successes:
            best = min(successes, key=lambda a: a["time_seconds"])
            results.append(
                {
                    "query": name,
                    "status": "success",
                    "time_seconds": best["time_seconds"],
                    "row_count": best.get("row_count"),
                    "rounds": [a["time_seconds"] for a in attempts],
                }
            )
        else:
            results.append(
                {
                    "query": name,
                    "status": "failed",
                    "time_seconds": attempts[-1]["time_seconds"] if attempts else 0,
                    "error": attempts[-1].get("error", "unknown") if attempts else "not run",
                    "rounds": [a["time_seconds"] for a in attempts],
                }
            )

    summary = {
        "phase": "query",
        "engine": args.engine,
        "format": args.format,
        "rounds": args.rounds,
        "warmup": args.warmup,
        "total_time_seconds": round(total_elapsed, 3),
        "query_count": len(results),
        "successful": sum(1 for r in results if r["status"] == "success"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "sum_best_seconds": round(sum(r["time_seconds"] for r in results
                                      if r["status"] == "success"), 3),
        "results": results,
    }

    payload = json.dumps(summary, indent=2)
    if args.output_json:
        os.makedirs(os.path.dirname(args.output_json) or ".", exist_ok=True)
        with open(args.output_json, "w") as handle:
            handle.write(payload)

    print("===QUANTON_BENCH_RESULT_BEGIN===")
    print(payload)
    print("===QUANTON_BENCH_RESULT_END===")
    print("Total %.1fs | success %d | failed %d"
          % (total_elapsed, summary["successful"], summary["failed"]))

    spark.stop()


if __name__ == "__main__":
    main()
