#!/usr/bin/env python3
"""GC pressure: three reports over the last 90 days of sales, computed from one cache().

Caching a DataFrame that several queries reuse is the textbook move. Run it with Spark
defaults and read GC time in the Executors and Stages tabs, and the Storage tab.

Usage: sales_report.py
"""
import time

from pyspark.sql import SparkSession
from pyspark.sql import functions as F


def main() -> None:
    spark = SparkSession.builder.appName("profiler-demo-sales-report").getOrCreate()
    fact = spark.table("demo.sales.fact")
    latest = fact.agg(F.max("event_date")).first()[0]
    recent = fact.where(F.col("event_date") > F.date_sub(F.lit(latest), 90)).cache()

    started = time.time()
    revenue = F.sum(F.col("quantity") * F.col("unit_price")).alias("revenue")

    spark.sparkContext.setJobDescription("report 1: daily revenue by channel")
    daily = recent.groupBy("event_date", "channel").agg(F.count("*").alias("orders"), revenue)
    daily.writeTo("demo.sales.report_daily").createOrReplace()

    spark.sparkContext.setJobDescription("report 2: revenue per product")
    per_product = recent.groupBy("product_id").agg(F.count("*").alias("orders"), revenue,
                                                   F.approx_count_distinct("customer_id").alias("buyers"))
    per_product.writeTo("demo.sales.report_products").createOrReplace()

    spark.sparkContext.setJobDescription("report 3: revenue per customer")
    per_customer = recent.groupBy("customer_id").agg(F.count("*").alias("orders"), revenue,
                                                     F.max("event_date").alias("last_order"))
    per_customer.writeTo("demo.sales.report_customers").createOrReplace()

    print(f"[report] 3 reports finished in {time.time() - started:.1f}s")
    spark.stop()


if __name__ == "__main__":
    main()
