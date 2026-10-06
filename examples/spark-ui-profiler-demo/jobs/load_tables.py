#!/usr/bin/env python3
"""One-time setup: turn lake-loader output into the Iceberg tables the talk uses.

lake-loader's ChangeDataGenerator writes round 0 (the base snapshot) and round 1 (the
changes) as Parquet. This job derives business-looking columns from the generated ones
and creates three Iceberg tables in the `demo` catalog:

  demo.sales.fact       ~1B rows, partitioned by event_date. One busy day holds ~40%
                        of the rows (lake-loader --partition-distribution).
  demo.sales.customers  20M rows. customer_id 0 is "guest checkout" and ~20% of fact
                        rows point at it, a skew pattern most real fact tables have.
  demo.sales.products   1M rows. The columns the rollup reads come to ~40 MB, above the
                        10 MB broadcast default even after AQE sees the real size.

It tags the fact snapshot as `base`, so every MERGE run in the talk starts from the
same state.

Usage: load_tables.py <lake-loader output path> [--write-tasks N]
"""
import argparse

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

NUM_CUSTOMERS = 20_000_000
NUM_PRODUCTS = 1_000_000
GUEST_SHARE_PCT = 20


def to_sales(raw: DataFrame) -> DataFrame:
    """Map lake-loader's generic columns to sales columns. merge_changes.py uses the same mapping."""
    seed = F.coalesce(F.col("intField19"), F.lit(0))
    return raw.select(
        F.col("key").alias("order_id"),
        F.to_date("partition").alias("event_date"),
        F.col("ts"),
        F.when(F.pmod(seed, 100) < GUEST_SHARE_PCT, F.lit(0))
         .otherwise(F.pmod(seed, NUM_CUSTOMERS) + 1).cast("long").alias("customer_id"),
        F.pmod(F.coalesce(F.col("longField27"), F.lit(0)), NUM_PRODUCTS).alias("product_id"),
        (F.pmod(F.coalesce(F.col("longField38"), F.lit(0)), 10) + 1).cast("int").alias("quantity"),
        F.round(F.coalesce(F.col("decimalField6"), F.lit(0.0)) * 100, 2).cast("decimal(10,2)").alias("unit_price"),
        F.col("textField10").alias("channel"),
        F.col("textField21").alias("shipping_address"),
        F.col("textField32").alias("notes"),
        F.col("longField15").alias("session_id"),
    )


def customers(spark: SparkSession) -> DataFrame:
    return spark.range(0, NUM_CUSTOMERS + 1).select(
        F.col("id").alias("customer_id"),
        F.when(F.col("id") == 0, F.lit("guest"))
         .otherwise(F.element_at(F.array(*[F.lit(s) for s in ("consumer", "smb", "enterprise")]),
                                 (F.pmod(F.col("id"), 3) + 1).cast("int"))).alias("segment"),
        F.concat(F.lit("region-"), F.pmod(F.col("id"), 50)).alias("region"),
        F.sha2(F.col("id").cast("string"), 256).alias("email_hash"),
    )


def products(spark: SparkSession) -> DataFrame:
    return spark.range(0, NUM_PRODUCTS).select(
        F.col("id").alias("product_id"),
        F.concat(F.lit("category-"), F.pmod(F.col("id"), 40)).alias("category"),
        F.concat(F.lit("brand-"), F.pmod(F.col("id"), 900)).alias("brand"),
        F.sha2(F.col("id").cast("string"), 256).alias("sku"),
        F.sha2(F.concat(F.lit("desc"), F.col("id").cast("string")), 512).alias("description"),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_path", help="lake-loader output path (contains 0/ and 1/)")
    parser.add_argument("--write-tasks", type=int, default=2000,
                        help="fact write tasks; aim for ~128 MB per task (2000 for the ~280 GB base)")
    args = parser.parse_args()

    spark = SparkSession.builder.appName("profiler-demo-load-tables").getOrCreate()
    spark.sql("CREATE NAMESPACE IF NOT EXISTS demo.sales")
    for table in ("fact", "customers", "products", "customer_daily_revenue"):
        spark.sql(f"DROP TABLE IF EXISTS demo.sales.{table} PURGE")

    customers(spark).writeTo("demo.sales.customers").create()
    products(spark).coalesce(1).writeTo("demo.sales.products").create()

    # Setup only: range-partition so each write task covers one or two days and writes
    # ~128 MB files, and the busy day spreads over many tasks. The talk's jobs run with
    # the table's default write settings.
    fact = to_sales(spark.read.parquet(f"{args.raw_path.rstrip('/')}/0"))
    (fact.repartitionByRange(args.write_tasks, "event_date", "order_id")
         .writeTo("demo.sales.fact")
         .partitionedBy(F.col("event_date"))
         .option("distribution-mode", "none")
         .create())
    spark.sql("ALTER TABLE demo.sales.fact CREATE TAG base")

    spark.sql("""
      CREATE TABLE demo.sales.customer_daily_revenue (
        customer_id BIGINT, event_date DATE, segment STRING, orders BIGINT,
        units BIGINT, revenue DECIMAL(30,2), categories BIGINT)
      USING iceberg PARTITIONED BY (event_date)""")

    for table in ("fact", "customers", "products"):
        row = spark.sql(f"""SELECT count(*) AS files, sum(file_size_in_bytes) AS bytes,
                                   sum(record_count) AS records
                            FROM demo.sales.{table}.files""").first()
        print(f"[load] demo.sales.{table}: {row.records:,} rows, {row.files:,} files, "
              f"{row.bytes / 2**30:.2f} GiB")
    spark.stop()


if __name__ == "__main__":
    main()
