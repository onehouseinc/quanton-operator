#!/usr/bin/env python3
"""Load the TPC-DS Parquet dataset into Hudi or Iceberg tables.

The Parquet dataset produced by ``datagen_parallel.py`` is the common source. This script
writes the same 24 tables into a lakehouse format so the query benchmark and the merge
benchmark can run against Hudi and Iceberg as well as raw Parquet.

The initial load uses each format's bulk path (Hudi ``bulk_insert``, Iceberg CTAS). It is
timed and reported, but it is a *load* number, not a merge number. Merge performance is
measured separately by ``merge_benchmark.py``.

Usage
-----
    load_tables.py --format hudi \
        --source-uri s3a://my-bucket/tpcds/parquet/1TB \
        --target-uri s3a://my-bucket/tpcds/hudi/1TB
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pyspark.sql import SparkSession  # noqa: E402
from pyspark.sql import functions as F  # noqa: E402
from run_queries import TPCDS_TABLES  # noqa: E402  (the 24 tables the 99 queries read)

# Hudi needs a record key per table. TPC-DS surrogate keys are unique except on the
# slowly-changing dimensions, where a row is versioned by its record start date, so those
# tables take a composite key. The fact tables key on (item, transaction number), which is
# also the join key the merge benchmark matches on.
RECORD_KEYS = {
    "call_center": "cc_call_center_sk,cc_rec_start_date",
    "catalog_page": "cp_catalog_page_sk",
    "catalog_returns": "cr_item_sk,cr_order_number",
    "catalog_sales": "cs_item_sk,cs_order_number",
    "customer": "c_customer_sk",
    "customer_address": "ca_address_sk",
    "customer_demographics": "cd_demo_sk",
    "date_dim": "d_date_sk",
    "household_demographics": "hd_demo_sk",
    "income_band": "ib_income_band_sk",
    "inventory": "inv_date_sk,inv_item_sk,inv_warehouse_sk",
    "item": "i_item_sk,i_rec_start_date",
    "promotion": "p_promo_sk",
    "reason": "r_reason_sk",
    "ship_mode": "sm_ship_mode_sk",
    "store": "s_store_sk,s_rec_start_date",
    "store_returns": "sr_item_sk,sr_ticket_number",
    "store_sales": "ss_item_sk,ss_ticket_number",
    "time_dim": "t_time_sk",
    "warehouse": "w_warehouse_sk",
    "web_page": "wp_web_page_sk,wp_rec_start_date",
    "web_returns": "wr_item_sk,wr_order_number",
    "web_sales": "ws_item_sk,ws_order_number",
    "web_site": "web_site_sk,web_rec_start_date",
}

# Optional date partitioning for the fact tables, enabled with --partition-facts date.
FACT_PARTITION_COLUMNS = {
    "store_sales": "ss_sold_date_sk",
    "store_returns": "sr_returned_date_sk",
    "catalog_sales": "cs_sold_date_sk",
    "catalog_returns": "cr_returned_date_sk",
    "web_sales": "ws_sold_date_sk",
    "web_returns": "wr_returned_date_sk",
    "inventory": "inv_date_sk",
}

# Hudi orders concurrent versions of a record by a precombine column. Every natural
# candidate in TPC-DS is nullable, so the loader adds an explicit one. The merge benchmark
# raises it per round, which makes each round's update the winning version.
PRECOMBINE_COLUMN = "_bench_ts"


def partition_column(table, mode):
    if mode == "date":
        return FACT_PARTITION_COLUMNS.get(table, "")
    return ""


def hudi_options(table, args, partition_col, table_name=None):
    """Build the Hudi write options for one table.

    ``table`` selects the record key and must be a real TPC-DS table. ``table_name`` is the
    physical Hudi table name, which differs when the merge benchmark works on its own copy.
    """
    physical = table_name or table
    options = {
        "hoodie.table.name": physical,
        "hoodie.datasource.write.table.name": physical,
        "hoodie.datasource.write.recordkey.field": RECORD_KEYS[table],
        "hoodie.datasource.write.precombine.field": PRECOMBINE_COLUMN,
        "hoodie.datasource.write.table.type": args.hudi_table_type,
        "hoodie.datasource.write.hive_style_partitioning": "true",
        "hoodie.datasource.write.row.writer.enable": "true",
        "hoodie.combine.before.insert": "false",
        "hoodie.metadata.enable": "true",
        "hoodie.index.type": args.hudi_index_type,
        "hoodie.parquet.compression.codec": "snappy",
        "hoodie.bulkinsert.shuffle.parallelism": str(args.shuffle_parallelism),
        "hoodie.upsert.shuffle.parallelism": str(args.shuffle_parallelism),
    }
    if partition_col:
        options["hoodie.datasource.write.partitionpath.field"] = partition_col
        options["hoodie.datasource.write.keygenerator.class"] = (
            "org.apache.hudi.keygen.ComplexKeyGenerator"
        )
    else:
        options["hoodie.datasource.write.partitionpath.field"] = ""
        options["hoodie.datasource.write.keygenerator.class"] = (
            "org.apache.hudi.keygen.NonpartitionedKeyGenerator"
        )
    if args.hudi_index_type == "RECORD_INDEX":
        options["hoodie.metadata.record.index.enable"] = "true"
    return options


def load_hudi(spark, table, source, target, args, fraction=1.0, table_name=None):
    """Bulk-load one Parquet table into Hudi.

    ``fraction`` samples the source, which the merge benchmark uses to prepare a smaller
    target when a full copy of a fact table would cost more than the measurement is worth.
    """
    physical = table_name or table
    frame = spark.read.parquet(source)
    if fraction < 1.0:
        frame = frame.sample(False, fraction, seed=11)
    frame = frame.withColumn(PRECOMBINE_COLUMN, F.lit(0).cast("bigint"))
    partition_col = partition_column(table, args.partition_facts)
    options = hudi_options(table, args, partition_col, table_name=physical)
    options["hoodie.datasource.write.operation"] = "bulk_insert"
    (
        frame.write.format("hudi")
        .options(**options)
        .mode("overwrite")
        .save("%s/%s" % (target, physical))
    )


def load_iceberg(spark, table, source, target_table, args, fraction=1.0):
    frame = spark.read.parquet(source)
    if fraction < 1.0:
        frame = frame.sample(False, fraction, seed=11)
    frame.createOrReplaceTempView("_bench_source")
    partition_col = partition_column(table, args.partition_facts)
    partition_clause = " PARTITIONED BY (%s)" % partition_col if partition_col else ""
    properties = ", ".join(
        [
            "'format-version' = '2'",
            "'write.parquet.compression-codec' = 'snappy'",
            "'write.merge.mode' = '%s'" % args.iceberg_write_mode,
            "'write.update.mode' = '%s'" % args.iceberg_write_mode,
            "'write.delete.mode' = '%s'" % args.iceberg_write_mode,
        ]
    )
    spark.sql("DROP TABLE IF EXISTS %s" % target_table)
    spark.sql(
        "CREATE TABLE %s USING iceberg%s TBLPROPERTIES (%s) AS SELECT * FROM _bench_source"
        % (target_table, partition_clause, properties)
    )
    spark.sql(
        "ALTER TABLE %s SET TBLPROPERTIES ('write.distribution-mode' = 'hash')" % target_table
    )


def main():
    parser = argparse.ArgumentParser(description="Load TPC-DS Parquet into Hudi or Iceberg")
    parser.add_argument("--format", required=True, choices=["hudi", "iceberg"])
    parser.add_argument("--source-uri", required=True, help="Base URI of the Parquet dataset")
    parser.add_argument("--target-uri", default="", help="Base URI for Hudi tables")
    parser.add_argument("--catalog", default="lakehouse", help="Iceberg catalog name")
    parser.add_argument("--database", default="tpcds", help="Iceberg namespace")
    parser.add_argument("--tables", default="", help="Comma-separated subset of tables to load")
    parser.add_argument("--partition-facts", default="none", choices=["none", "date"])
    parser.add_argument("--hudi-table-type", default="COPY_ON_WRITE",
                        choices=["COPY_ON_WRITE", "MERGE_ON_READ"])
    parser.add_argument("--hudi-index-type", default="BLOOM",
                        choices=["BLOOM", "SIMPLE", "GLOBAL_SIMPLE", "BUCKET", "RECORD_INDEX"])
    parser.add_argument("--iceberg-write-mode", default="copy-on-write",
                        choices=["copy-on-write", "merge-on-read"])
    parser.add_argument("--shuffle-parallelism", type=int, default=2000)
    parser.add_argument("--output-json", default="")
    args = parser.parse_args()

    if args.format == "hudi" and not args.target_uri:
        raise SystemExit("--target-uri is required for the hudi format")

    requested = [t.strip() for t in args.tables.split(",") if t.strip()]
    selected = [t for t in TPCDS_TABLES if not requested or t in requested]
    unknown = sorted(set(requested) - set(TPCDS_TABLES))
    if unknown:
        raise SystemExit("Unknown tables: %s" % ", ".join(unknown))

    source_base = args.source_uri.rstrip("/")
    target_base = args.target_uri.rstrip("/")

    spark = SparkSession.builder.appName("TPC-DS Load %s" % args.format).getOrCreate()
    spark.sparkContext.setLogLevel("WARN")

    if args.format == "iceberg":
        spark.sql("CREATE NAMESPACE IF NOT EXISTS %s.%s" % (args.catalog, args.database))

    summary = {
        "phase": "load",
        "format": args.format,
        "source_uri": source_base,
        "target": target_base or "%s.%s" % (args.catalog, args.database),
        "partition_facts": args.partition_facts,
        "tables": [],
    }
    run_start = time.time()

    for table in selected:
        source = "%s/%s" % (source_base, table)
        print("Loading %s into %s" % (table, args.format))
        started = time.time()
        if args.format == "hudi":
            load_hudi(spark, table, source, target_base, args)
            rows = spark.read.format("hudi").load("%s/%s" % (target_base, table)).count()
        else:
            target_table = "%s.%s.%s" % (args.catalog, args.database, table)
            load_iceberg(spark, table, source, target_table, args)
            rows = spark.table(target_table).count()
        elapsed = time.time() - started
        print("  %s: %d rows in %.1fs" % (table, rows, elapsed))
        summary["tables"].append({"table": table, "rows": rows, "seconds": round(elapsed, 3)})

    summary["total_seconds"] = round(time.time() - run_start, 3)
    summary["total_rows"] = sum(t["rows"] for t in summary["tables"])

    payload = json.dumps(summary, indent=2)
    if args.output_json:
        os.makedirs(os.path.dirname(args.output_json) or ".", exist_ok=True)
        with open(args.output_json, "w") as handle:
            handle.write(payload)

    print("===QUANTON_BENCH_RESULT_BEGIN===")
    print(payload)
    print("===QUANTON_BENCH_RESULT_END===")

    spark.stop()


if __name__ == "__main__":
    main()
