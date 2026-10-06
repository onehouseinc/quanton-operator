#!/usr/bin/env python3
"""Small files: daily totals per channel over demo.sales.events.

The table holds tens of thousands of small files (see prepare_events.py). Run it with
Spark defaults and read the scan stage: how many tasks, and how much data each one moves.

Usage: events_rollup.py
"""
import time

from pyspark.sql import SparkSession

EVENTS_SQL = """
SELECT event_date,
       count(*)                           AS orders,
       sum(quantity * unit_price)         AS revenue,
       approx_count_distinct(customer_id) AS customers
FROM events
GROUP BY event_date
"""


def main() -> None:
    spark = SparkSession.builder.appName("profiler-demo-events-rollup").getOrCreate()
    started = time.time()
    spark.sparkContext.setJobDescription(f"events_rollup <- demo.sales.events: {EVENTS_SQL}")
    # Iceberg read options (split-size, file-open-cost, ...) come from spark.demo.read.* so a
    # run can change them through config alone. Spark's own spark.sql.files.* settings do
    # not apply to Iceberg tables.
    options = {key[len("spark.demo.read."):]: value
               for key, value in spark.sparkContext.getConf().getAll()
               if key.startswith("spark.demo.read.")}
    # compact_events.py tags the small-file snapshot before it compacts the table. Read that
    # tag unless the run asks for another ref (for example spark.demo.read.branch=main).
    refs = {r.name for r in spark.sql("SELECT name FROM demo.sales.events.refs").collect()}
    if "small_files" in refs and not {"tag", "branch", "snapshot-id"} & options.keys():
        options["tag"] = "small_files"
    print(f"[events] read options: {options}")
    reader = spark.read.format("iceberg").options(**options)
    reader.load("demo.sales.events").createOrReplaceTempView("events")
    rows = spark.sql(EVENTS_SQL).collect()
    print(f"[events] {len(rows)} days aggregated in {time.time() - started:.1f}s")
    spark.stop()


if __name__ == "__main__":
    main()
