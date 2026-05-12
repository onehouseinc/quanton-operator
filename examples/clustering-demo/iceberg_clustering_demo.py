"""
iceberg_clustering_demo.py — Iceberg clustering demo with complex nested schema

End-to-end Iceberg clustering demo on a small (100-row) dataset:
  1. Creates an Iceberg table (Hadoop catalog) with a complex schema
     (Struct / Array / Map), populated via repeated INSERT batches so the
     table has many small data files.
  2. Calls rewrite_data_files(strategy='sort', sort_order='region ASC, ts ASC')
     to compact and sort the small files.
  3. Verifies the file count decreased (clustering actually ran) and the data
     comes back intact.

On the Quanton image with spark.quanton.clustering.accelerate=true, the sort
inside the rewrite_data_files procedure runs via Quanton-native execution.

Usage:
  spark-submit iceberg_clustering_demo.py <warehouseDir>
"""

import sys

from pyspark.sql import SparkSession
from pyspark.sql import functions as F


SORT_ORDER = "region ASC NULLS LAST, ts ASC NULLS LAST"
ROWS_PER_BATCH = 5
BATCHES = 20  # 20 × 5 = 100 rows, written as 20 separate snapshots → 20+ data files
EVENT_TYPES = [f"event_{i:02d}" for i in range(8)]
REGIONS = ["us-east-1", "us-west-2", "eu-west-1", "ap-south-1"]


def file_count(spark, table: str) -> int:
    return int(spark.sql(f"SELECT count(*) AS c FROM {table}.files").collect()[0]["c"])


def main():
    if len(sys.argv) != 2:
        print("Usage: iceberg_clustering_demo.py <warehouseDir>")
        sys.exit(1)

    warehouse = sys.argv[1].rstrip("/")
    table = "default.events_iceberg"

    spark = (SparkSession.builder
             .appName("IcebergClusteringDemo")
             .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
             .config("spark.sql.catalog.spark_catalog", "org.apache.iceberg.spark.SparkSessionCatalog")
             .config("spark.sql.catalog.spark_catalog.type", "hadoop")
             .config("spark.sql.catalog.spark_catalog.warehouse", warehouse)
             .getOrCreate())
    spark.sparkContext.setLogLevel("WARN")

    print(f"[iceberg-clustering] Creating {table} in {warehouse}")
    spark.sql(f"DROP TABLE IF EXISTS {table}")
    spark.sql(f"""
      CREATE TABLE {table} (
        id BIGINT,
        ts TIMESTAMP,
        region STRING,
        customer_id STRING,
        amount DECIMAL(15,2),
        event_type STRING,
        address STRUCT<street: STRING, city: STRING, zip: STRING>,
        tags ARRAY<STRING>,
        attributes MAP<STRING, STRING>
      )
      USING iceberg
      TBLPROPERTIES ('write.parquet.compression-codec' = 'zstd')
    """)

    # 20 sequential inserts of 5 rows each → 20+ small files
    for b in range(BATCHES):
        batch = (spark.range(b * ROWS_PER_BATCH, (b + 1) * ROWS_PER_BATCH)
                 .withColumn("ts", F.expr("from_unixtime(unix_timestamp(current_timestamp()) - cast(rand(42) * 86400 * 90 as bigint))").cast("timestamp"))
                 .withColumn("region", F.element_at(F.array(*[F.lit(r) for r in REGIONS]),
                                                    (F.col("id") % len(REGIONS) + 1).cast("int")))
                 .withColumn("customer_id", F.concat(F.lit("cust-"), F.lpad((F.col("id") % 50).cast("string"), 4, "0")))
                 .withColumn("amount", (F.rand(7) * 1000.0).cast("decimal(15,2)"))
                 .withColumn("event_type", F.element_at(F.array(*[F.lit(e) for e in EVENT_TYPES]),
                                                        (F.col("id") % len(EVENT_TYPES) + 1).cast("int")))
                 .withColumn("address", F.struct(
                     F.concat(F.lit("street-"), (F.col("id") % 99).cast("string")).alias("street"),
                     F.concat(F.lit("city-"), (F.col("id") % 20).cast("string")).alias("city"),
                     F.lpad((F.col("id") % 99999).cast("string"), 5, "0").alias("zip"),
                 ))
                 .withColumn("tags", F.array(F.concat(F.lit("tag-"), (F.col("id") % 5).cast("string")),
                                             F.concat(F.lit("tag-"), (F.col("id") % 7).cast("string"))))
                 .withColumn("attributes", F.map_from_arrays(
                     F.array(F.lit("k1"), F.lit("k2")),
                     F.array(F.concat(F.lit("v-"), (F.col("id") % 3).cast("string")),
                             F.concat(F.lit("v-"), (F.col("id") % 5).cast("string"))))))
        batch.writeTo(table).append()

    files_before = file_count(spark, table)
    rows_before = int(spark.sql(f"SELECT count(*) AS c FROM {table}").collect()[0]["c"])
    print(f"[iceberg-clustering] Wrote {rows_before} rows across {files_before} data files (target: {BATCHES})")

    print(f"[iceberg-clustering] CALL rewrite_data_files(strategy='sort', sort_order='{SORT_ORDER}')")
    spark.sql(f"""
        CALL spark_catalog.system.rewrite_data_files(
          table => '{table}',
          strategy => 'sort',
          sort_order => '{SORT_ORDER}'
        )
    """).collect()
    files_after = file_count(spark, table)

    print(f"[iceberg-clustering] Files: {files_before} -> {files_after}")
    assert files_after < files_before, \
        f"Expected file count to decrease after clustering ({files_before} -> {files_after})"

    rows = int(spark.sql(f"SELECT count(*) AS c FROM {table}").collect()[0]["c"])
    assert rows == rows_before, f"Row count changed during clustering: {rows_before} -> {rows}"
    print(f"[iceberg-clustering] PASS — {rows} rows preserved, files compacted {files_before} -> {files_after}")

    spark.stop()


if __name__ == "__main__":
    main()
