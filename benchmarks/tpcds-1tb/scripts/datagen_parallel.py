#!/usr/bin/env python3
"""Distributed TPC-DS data generation for large scale factors (SF=1000 / 1 TB and up).

The single-node generator in ``benchmarks/scripts/datagen.py`` runs ``dsdgen`` on the
driver and writes to a PVC. That does not scale past a few tens of gigabytes. This
script keeps the *same table schemas* (it imports them from ``datagen.py``) but runs
``dsdgen`` on every executor in parallel and writes Parquet straight to object storage.

How it works
------------
``dsdgen -PARALLEL P -CHILD c`` generates chunk ``c`` of ``P`` for one table. We build an
RDD of the chunk numbers, run ``dsdgen`` inside each task on executor-local disk, stream
the generated rows out as text, and let Spark split and cast them into the target schema.
Nothing is staged twice: the rows go from ``dsdgen`` to Parquet in one pass.

The three "returns" tables are children of a sales table in TPC-DS and ``dsdgen`` refuses
to generate them on their own, so we generate the parent and keep only the child rows.

Usage
-----
    datagen_parallel.py --scale-factor 1000 \
        --output-uri s3a://my-bucket/tpcds/parquet/1TB \
        --parallel 400
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

# The scripts ConfigMap mounts benchmarks/scripts/ and benchmarks/tpcds-1tb/scripts/ into
# the same directory, so the single-node generator is importable and stays the one source
# of truth for the TPC-DS schemas.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datagen import TABLES, parse_schema  # noqa: E402  (single source of truth for schemas)
from pyspark.sql import SparkSession  # noqa: E402
from pyspark.sql import functions as F  # noqa: E402

# TPC-DS child tables: dsdgen only emits these while building their parent.
PARENT_OF = {
    "store_returns": "store_sales",
    "catalog_returns": "catalog_sales",
    "web_returns": "web_sales",
}

# Tables whose row count grows with the scale factor. Everything else is small enough
# that a single dsdgen invocation is faster than coordinating parallel chunks.
SCALING_TABLES = {
    "store_sales", "store_returns",
    "catalog_sales", "catalog_returns",
    "web_sales", "web_returns",
    "inventory", "customer", "customer_address", "customer_demographics", "item",
}

# Generated as a side effect of every dsdgen run and never referenced by the 99 queries.
EXCLUDED_TABLES = {"dbgen_version"}


def local_scratch_dir() -> str:
    """Pick a work directory with room for a dsdgen chunk.

    Spark sets SPARK_LOCAL_DIRS on executors to the shuffle volume, which is the only
    place with meaningful free space in a Spark container. Fall back to the system
    temp dir when the variable is absent (local runs).
    """
    dirs = os.environ.get("SPARK_LOCAL_DIRS", "").split(",")
    for d in dirs:
        if d and os.path.isdir(d):
            return d
    return tempfile.gettempdir()


def make_chunk_generator(table, scale_factor, parallel, dsdgen_dir):
    """Return a function that generates one dsdgen chunk and yields its raw lines.

    The returned closure runs on the executor. It is a generator, so a chunk is streamed
    to Spark line by line instead of being held in the Python worker's memory.
    """
    gen_table = PARENT_OF.get(table, table)
    # dsdgen names parallel output "<table>_<child>_<parallel>.dat" and serial output
    # "<table>.dat". Match only the table we asked for; a parent run also drops its
    # child table's file into the same directory.
    pattern = re.compile(r"^%s(_\d+_\d+)?\.dat$" % re.escape(table))

    def _generate(child):
        workdir = tempfile.mkdtemp(prefix="dsdgen-%s-%s-" % (table, child), dir=local_scratch_dir())
        try:
            cmd = [
                os.path.join(dsdgen_dir, "dsdgen"),
                "-TABLE", gen_table,
                "-SCALE", str(scale_factor),
                "-DIR", workdir,
                "-SUFFIX", ".dat",
                "-DELIMITER", "|",
                "-TERMINATE", "N",
                "-FORCE", "Y",
                "-DISTRIBUTIONS", os.path.join(dsdgen_dir, "tpcds.idx"),
            ]
            if parallel > 1:
                cmd += ["-PARALLEL", str(parallel), "-CHILD", str(child)]
            proc = subprocess.run(
                cmd, cwd=dsdgen_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    "dsdgen failed for table=%s child=%s rc=%s: %s"
                    % (table, child, proc.returncode, proc.stderr.decode("utf-8", "replace")[-2000:])
                )
            for name in sorted(os.listdir(workdir)):
                if not pattern.match(name):
                    continue  # the parent table's own rows, when we only want the child
                with open(os.path.join(workdir, name), "r", errors="replace") as handle:
                    for line in handle:
                        yield line.rstrip("\r\n")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    return _generate


def typed_dataframe(spark, table, lines_rdd):
    """Split the pipe-delimited lines and cast every column to its TPC-DS type.

    Splitting and casting happen in the JVM, so the Python workers only ever touch raw
    strings. Empty fields become NULL, which matches how ``datagen.py`` reads the same
    files through the CSV reader (its default ``nullValue`` is the empty string).
    """
    schema = parse_schema(TABLES[table])
    raw = spark.createDataFrame(lines_rdd, "string")  # single column named "value"
    parts = F.split(F.col("value"), r"\|", -1)
    columns = []
    for index, field in enumerate(schema.fields):
        cell = parts.getItem(index)
        cell = F.when(cell.isNull() | (F.length(cell) == 0), F.lit(None).cast("string")).otherwise(cell)
        columns.append(cell.cast(field.dataType).alias(field.name))
    return raw.select(*columns)


def already_generated(spark, uri):
    """True when a previous run left a committed Parquet directory at this URI."""
    try:
        jvm = spark.sparkContext._jvm
        hadoop_conf = spark.sparkContext._jsc.hadoopConfiguration()
        path = jvm.org.apache.hadoop.fs.Path(uri)
        fs = path.getFileSystem(hadoop_conf)
        return fs.exists(jvm.org.apache.hadoop.fs.Path(uri.rstrip("/") + "/_SUCCESS"))
    except Exception as exc:  # noqa: BLE001 - never let a probe fail the run
        print("  could not probe %s (%s); assuming it is absent" % (uri, exc))
        return False


def main():
    parser = argparse.ArgumentParser(description="Generate TPC-DS Parquet at scale")
    parser.add_argument("--scale-factor", type=int, default=1000, help="TPC-DS scale factor in GB")
    parser.add_argument("--output-uri", required=True, help="Base URI for the Parquet output")
    parser.add_argument("--parallel", type=int, default=400, help="dsdgen chunks for scaling tables")
    parser.add_argument("--dsdgen-dir", default="/opt/tpcds-kit/tools", help="Directory holding dsdgen")
    parser.add_argument("--tables", default="", help="Comma-separated subset of tables to generate")
    parser.add_argument("--overwrite", action="store_true", help="Regenerate tables that already exist")
    parser.add_argument("--output-json", default="", help="Write the run summary to this local path")
    args = parser.parse_args()

    base_uri = args.output_uri.rstrip("/")
    requested = [t.strip() for t in args.tables.split(",") if t.strip()]
    all_tables = [t for t in TABLES if t not in EXCLUDED_TABLES]
    if requested:
        unknown = sorted(set(requested) - set(all_tables))
        if unknown:
            raise SystemExit("Unknown tables: %s" % ", ".join(unknown))
        selected = [t for t in all_tables if t in requested]
    else:
        selected = all_tables

    # Small tables first: they finish in seconds and surface a broken image or a bad
    # object-storage URI before the multi-hour fact tables start.
    selected.sort(key=lambda t: (t in SCALING_TABLES, t))

    spark = SparkSession.builder.appName("TPC-DS Parallel Datagen SF=%d" % args.scale_factor).getOrCreate()
    spark.sparkContext.setLogLevel("WARN")

    summary = {
        "phase": "datagen",
        "scale_factor": args.scale_factor,
        "output_uri": base_uri,
        "parallel": args.parallel,
        "tables": [],
    }
    run_start = time.time()

    for table in selected:
        target = "%s/%s" % (base_uri, table)
        if not args.overwrite and already_generated(spark, target):
            print("Skipping %s: %s already has a _SUCCESS marker" % (table, target))
            summary["tables"].append({"table": table, "status": "skipped", "seconds": 0})
            continue

        parallel = args.parallel if table in SCALING_TABLES else 1
        print("Generating %s (parallel=%d) -> %s" % (table, parallel, target))
        started = time.time()
        generator = make_chunk_generator(table, args.scale_factor, parallel, args.dsdgen_dir)
        chunks = spark.sparkContext.parallelize(range(1, parallel + 1), parallel)
        frame = typed_dataframe(spark, table, chunks.flatMap(generator))
        frame.write.mode("overwrite").parquet(target)
        elapsed = time.time() - started

        rows = spark.read.parquet(target).count()
        print("  %s: %d rows in %.1fs" % (table, rows, elapsed))
        summary["tables"].append(
            {"table": table, "status": "generated", "rows": rows, "seconds": round(elapsed, 3)}
        )

    summary["total_seconds"] = round(time.time() - run_start, 3)
    summary["total_rows"] = sum(t.get("rows", 0) for t in summary["tables"])

    payload = json.dumps(summary, indent=2)
    if args.output_json:
        os.makedirs(os.path.dirname(args.output_json) or ".", exist_ok=True)
        with open(args.output_json, "w") as handle:
            handle.write(payload)

    # The orchestrator scrapes this block out of the driver log, so it never needs
    # object-storage credentials on the machine that launched the run.
    print("===QUANTON_BENCH_RESULT_BEGIN===")
    print(payload)
    print("===QUANTON_BENCH_RESULT_END===")

    spark.stop()


if __name__ == "__main__":
    main()
