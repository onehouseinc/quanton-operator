#!/usr/bin/env python3
"""GC pressure: a per-customer feature table with each customer's last 30 days of orders.

collect_list(struct(...)) is the usual way to build an "order history" column. Run it
with Spark defaults and read GC time in the Executors and Stages tabs.

Usage: customer_features.py
"""
import time

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

# Guests (customer_id = 0) have no profile.
FEATURES_SQL = """
SELECT customer_id,
       count(*)                   AS orders,
       sum(quantity * unit_price) AS spend,
       collect_list(struct(order_id, event_date, product_id,
                           quantity, unit_price, channel)) AS recent_orders
FROM demo.sales.fact
WHERE event_date > date_sub(DATE '{latest}', 30)
  AND customer_id != 0
GROUP BY customer_id
"""


def main() -> None:
    spark = SparkSession.builder.appName("profiler-demo-customer-features").getOrCreate()
    latest = spark.table("demo.sales.fact").agg(F.max("event_date")).first()[0]
    sql = FEATURES_SQL.format(latest=latest)
    features = spark.sql(sql)
    started = time.time()
    spark.sparkContext.setJobDescription(f"customer_features -> demo.sales.customer_features: {sql}")
    features.writeTo("demo.sales.customer_features").createOrReplace()
    print(f"[features] write finished in {time.time() - started:.1f}s")
    spark.stop()


if __name__ == "__main__":
    main()
