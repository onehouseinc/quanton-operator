#!/usr/bin/env python3
"""One-time setup: build demo.sales.events the way a micro-batch ingester would.

Each micro-batch appends a small slice of the day's changes, spread over every day it
touches, so the table ends up with tens of thousands of small files. This is the input
for events_rollup.py.

Usage: prepare_events.py <lake-loader output path> [--write-tasks N]
"""
import argparse

from pyspark.sql import SparkSession

from load_tables import to_sales


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_path", help="lake-loader output path (contains 0/ and 1/)")
    parser.add_argument("--write-tasks", type=int, default=200,
                        help="write tasks; each writes one small file per day it holds")
    args = parser.parse_args()

    spark = SparkSession.builder.appName("profiler-demo-prepare-events").getOrCreate()
    spark.sql("DROP TABLE IF EXISTS demo.sales.events PURGE")
    events = to_sales(spark.read.parquet(f"{args.raw_path.rstrip('/')}/1"))
    (events.repartition(args.write_tasks)
           .writeTo("demo.sales.events")
           .partitionedBy(events.event_date)
           .option("distribution-mode", "none")
           .option("fanout-enabled", "true")
           .create())
    row = spark.sql("""SELECT count(*) AS files, sum(file_size_in_bytes) AS bytes,
                              sum(record_count) AS records FROM demo.sales.events.files""").first()
    print(f"[events] demo.sales.events: {row.records:,} rows, {row.files:,} files, "
          f"{row.bytes / 2**30:.2f} GiB, {row.bytes / max(row.files, 1) / 1024:.0f} KiB per file")
    spark.stop()


if __name__ == "__main__":
    main()
