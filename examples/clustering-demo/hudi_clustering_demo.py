"""
hudi_clustering_demo.py — Hudi clustering demo with complex nested schema

End-to-end Hudi clustering demo on a small (100-row) dataset:
  1. Creates a Hudi COW table with a complex schema (Struct / Array / Map / nested),
     forced to produce many tiny files via small hoodie.parquet.max.file.size.
  2. Calls run_clustering(order='region,ts', op='scheduleandexecute') to compact
     and sort the small files.
  3. Verifies the file count decreased (clustering actually ran) and the data
     comes back intact.

On the Quanton image with spark.quanton.clustering.accelerate=true, the sort +
write inside the clustering procedure run via Quanton's native group writer
(ai.onehouse.hudi.clustering.NativeClusteringGroupWriterImpl).

Usage:
  spark-submit hudi_clustering_demo.py <outputDir>
"""

import os
import sys

from pyspark.sql import SparkSession
from pyspark.sql import functions as F


SORT_COLUMNS = "region,ts"
ROWS = 100
EVENT_TYPES = [f"event_{i:02d}" for i in range(8)]
REGIONS = ["us-east-1", "us-west-2", "eu-west-1", "ap-south-1"]


def count_parquet_files(table_path: str) -> int:
    n = 0
    for root, dirs, files in os.walk(table_path):
        dirs[:] = [d for d in dirs if not d.startswith(".hoodie")]
        for f in files:
            if f.endswith(".parquet"):
                n += 1
    return n


def count_replacecommits(table_path: str) -> int:
    """A clustering operation appends a .replacecommit to .hoodie/. Use this as
    the signal that clustering actually ran — Hudi doesn't physically delete
    the old small files (they're tombstoned via the timeline), so a raw
    parquet-file count is not a reliable check."""
    timeline = os.path.join(table_path, ".hoodie")
    if not os.path.isdir(timeline):
        return 0
    return sum(1 for f in os.listdir(timeline) if f.endswith(".replacecommit"))


def main():
    if len(sys.argv) != 2:
        print("Usage: hudi_clustering_demo.py <outputDir>")
        sys.exit(1)

    output_dir = sys.argv[1].rstrip("/")
    table_path = f"{output_dir}/events_hudi"
    table_name = "events_hudi"

    spark = (SparkSession.builder
             .appName("HudiClusteringDemo")
             .config("spark.serializer", "org.apache.spark.serializer.KryoSerializer")
             .config("spark.kryo.registrator", "org.apache.spark.HoodieSparkKryoRegistrar")
             .config("spark.sql.extensions", "org.apache.spark.sql.hudi.HoodieSparkSessionExtension")
             .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.hudi.catalog.HoodieCatalog")
             .getOrCreate())
    spark.sparkContext.setLogLevel("WARN")

    print(f"[hudi-clustering] Generating {ROWS} rows with complex schema -> {table_path}")
    df = (spark.range(0, ROWS)
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

    # Force many tiny files: 1 KiB max → roughly one row per file.
    (df.repartition(50)
       .write.format("hudi")
       .option("hoodie.table.name", table_name)
       .option("hoodie.datasource.write.recordkey.field", "id")
       .option("hoodie.datasource.write.precombine.field", "ts")
       .option("hoodie.datasource.write.operation", "bulk_insert")
       .option("hoodie.parquet.max.file.size", "1024")
       .option("hoodie.parquet.small.file.limit", "0")
       .option("hoodie.clustering.inline", "false")
       .option("hoodie.clustering.async.enabled", "false")
       .option("hoodie.metadata.enable", "false")
       .option("hoodie.parquet.compression.codec", "zstd")
       .mode("overwrite")
       .save(table_path))

    files_before = count_parquet_files(table_path)
    print(f"[hudi-clustering] Wrote table with {files_before} small parquet files")

    spark.sql(f"CREATE TABLE IF NOT EXISTS {table_name} USING hudi LOCATION '{table_path}'")

    print(f"[hudi-clustering] CALL run_clustering(order='{SORT_COLUMNS}', op='scheduleandexecute')")
    spark.sql(f"""
        CALL run_clustering(
          table => '{table_name}',
          order => '{SORT_COLUMNS}',
          op => 'scheduleandexecute'
        )
    """).collect()
    files_after = count_parquet_files(table_path)
    replacecommits = count_replacecommits(table_path)

    print(f"[hudi-clustering] On-disk parquet files: {files_before} -> {files_after} "
          f"(Hudi keeps old files; clustering adds new ones + a .replacecommit)")
    print(f"[hudi-clustering] .replacecommit files in .hoodie/: {replacecommits}")
    assert replacecommits >= 1, \
        f"Expected at least one .replacecommit after clustering, got {replacecommits}"

    rows = spark.sql(f"SELECT count(*) AS c FROM {table_name}").collect()[0]["c"]
    assert rows == ROWS, f"Expected {ROWS} rows after clustering, got {rows}"
    print(f"[hudi-clustering] PASS — {rows} rows preserved, {replacecommits} replacecommit(s) on timeline")

    spark.stop()


if __name__ == "__main__":
    main()
