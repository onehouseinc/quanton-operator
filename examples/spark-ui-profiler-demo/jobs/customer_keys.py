#!/usr/bin/env python3
"""Idle slots: assign sequential surrogate keys to every customer.

row_number() over a window with no PARTITION BY is the usual way to number rows. Run it
with Spark defaults and read the event timeline: how many tasks run, against how many slots.
spark.demo.keys.method=parallel selects the fix: the same keys, computed in parallel.

Usage: customer_keys.py
"""
import time

from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F

KEYS_SQL = """
SELECT *, row_number() OVER (ORDER BY customer_id) AS customer_sk
FROM demo.sales.customers
"""


def main() -> None:
    spark = SparkSession.builder.appName("profiler-demo-customer-keys").getOrCreate()
    customers = spark.table("demo.sales.customers")
    if spark.conf.get("spark.demo.keys.method", "window") == "parallel":
        # Range-partition by customer_id, number rows inside each partition, then shift each
        # partition by the row count of the partitions before it. Same keys, every slot busy.
        # cache(): the counts and the numbering must see the same range boundaries.
        ranged = (customers.repartitionByRange("customer_id")
                           .withColumn("pid", F.spark_partition_id())
                           .cache())
        numbered = ranged.withColumn(
            "rn", F.row_number().over(Window.partitionBy("pid").orderBy("customer_id")))
        counts = sorted(ranged.groupBy("pid").count().collect())
        offsets, total = [], 0
        for row in counts:
            offsets.append((row["pid"], total))
            total += row["count"]
        offset_df = spark.createDataFrame(offsets, "pid INT, offset LONG")
        keyed = (numbered.join(F.broadcast(offset_df), "pid")
                         .withColumn("customer_sk", F.col("offset") + F.col("rn"))
                         .drop("pid", "rn", "offset"))
        description = ("customer_keys (parallel) -> demo.sales.customer_dim: repartitionByRange(customer_id), "
                       "row_number() per range, plus the row count of the ranges before it")
    else:
        keyed = spark.sql(KEYS_SQL)
        description = f"customer_keys -> demo.sales.customer_dim: {KEYS_SQL}"
    started = time.time()
    spark.sparkContext.setJobDescription(description)
    keyed.writeTo("demo.sales.customer_dim").createOrReplace()
    print(f"[keys] write finished in {time.time() - started:.1f}s")
    spark.stop()


if __name__ == "__main__":
    main()
