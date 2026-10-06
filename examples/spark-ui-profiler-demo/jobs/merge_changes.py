#!/usr/bin/env python3
"""ETL A: apply a day's changes to the Iceberg fact table with MERGE INTO.

This is a normal CDC upsert, written the way most teams write it. Nothing in the code
is tuned or broken on purpose: run it with Spark defaults and read the Spark UI.

Each run first rolls the table back to the `base` tag created by load_tables.py, so
every run does exactly the same work.

Usage: merge_changes.py <lake-loader output path>
"""
import argparse
import time

from pyspark.sql import SparkSession

MERGE_SQL = """
MERGE INTO demo.sales.fact t
USING changes s
ON t.order_id = s.order_id AND t.event_date = s.event_date
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_path", help="lake-loader output path (contains 0/ and 1/)")
    args = parser.parse_args()

    spark = SparkSession.builder.appName("profiler-demo-merge-changes").getOrCreate()
    # load_tables.py ships with spark.submit.pyFiles, or, where the submitting client cannot
    # read object storage, with spark.demo.pyFiles added here by the driver.
    for path in filter(None, spark.conf.get("spark.demo.pyFiles", "").split(",")):
        spark.sparkContext.addPyFile(path)
    from load_tables import to_sales

    base = spark.sql("SELECT snapshot_id FROM demo.sales.fact.refs WHERE name = 'base'").first()
    spark.sql(f"CALL demo.system.rollback_to_snapshot('sales.fact', {base.snapshot_id})")

    changes = to_sales(spark.read.parquet(f"{args.raw_path.rstrip('/')}/1"))
    changes.createOrReplaceTempView("changes")

    started = time.time()
    spark.sparkContext.setJobDescription(f"merge_changes -> demo.sales.fact: {MERGE_SQL}")
    spark.sql(MERGE_SQL)
    print(f"[merge] MERGE INTO finished in {time.time() - started:.1f}s")
    spark.stop()


if __name__ == "__main__":
    main()
