#!/usr/bin/env python3
"""ETL B: daily revenue per customer, written to an Iceberg table.

Reads the fact table, joins products and customers, aggregates per customer per day and
overwrites demo.sales.customer_daily_revenue. Plain, idiomatic SQL: run it with
Spark defaults and read the Spark UI.

Usage: daily_rollup.py
"""
import time

from pyspark.sql import SparkSession

ROLLUP_SQL = """
SELECT customer_id, event_date, segment,
       count(*)                   AS orders,
       sum(quantity)              AS units,
       sum(quantity * unit_price) AS revenue,
       count(DISTINCT category)   AS categories
FROM demo.sales.fact
JOIN demo.sales.products  USING (product_id)
JOIN demo.sales.customers USING (customer_id)
GROUP BY customer_id, event_date, segment
"""


def main() -> None:
    spark = SparkSession.builder.appName("profiler-demo-daily-rollup").getOrCreate()

    revenue = spark.sql(ROLLUP_SQL)

    started = time.time()
    # The SQL tab shows the job description; without one it shows only the call site.
    spark.sparkContext.setJobDescription(f"daily_rollup -> demo.sales.customer_daily_revenue: {ROLLUP_SQL}")
    revenue.writeTo("demo.sales.customer_daily_revenue").overwritePartitions()
    print(f"[rollup] write finished in {time.time() - started:.1f}s")
    spark.stop()


if __name__ == "__main__":
    main()
