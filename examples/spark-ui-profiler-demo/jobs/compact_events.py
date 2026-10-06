#!/usr/bin/env python3
"""Maintenance fix for small files: compact demo.sales.events with rewrite_data_files.

Tags the small-file snapshot as `small_files` first, so the slow events_rollup run can be
repeated later by reading that tag (spark.demo.read.tag=small_files).

Usage: compact_events.py
"""
import time

from pyspark.sql import SparkSession


def files(spark: SparkSession) -> str:
    row = spark.sql("""SELECT count(*) AS files, sum(file_size_in_bytes) AS bytes
                       FROM demo.sales.events.files""").first()
    return f"{row.files:,} files, {row.bytes / max(row.files, 1) / 2**20:.1f} MiB per file"


def main() -> None:
    spark = SparkSession.builder.appName("profiler-demo-compact-events").getOrCreate()
    tags = {r.name for r in spark.sql("SELECT name FROM demo.sales.events.refs").collect()}
    if "small_files" not in tags:
        spark.sql("ALTER TABLE demo.sales.events CREATE TAG small_files")
    print(f"[compact] before: {files(spark)}")
    started = time.time()
    spark.sql("""CALL demo.system.rewrite_data_files(
                   table => 'sales.events',
                   options => map('target-file-size-bytes', '134217728', 'min-input-files', '2'))""").show()
    print(f"[compact] after: {files(spark)} in {time.time() - started:.1f}s")
    spark.stop()


if __name__ == "__main__":
    main()
