#!/usr/bin/env python3
"""Execute TPC-DS SQL queries with timing, output results as JSON."""

import argparse
import json
import os
import time
import traceback
from pyspark.sql import SparkSession

# TPC-DS tables expected in the parquet directory
TPCDS_TABLES = [
    "call_center", "catalog_page", "catalog_returns", "catalog_sales",
    "customer", "customer_address", "customer_demographics", "date_dim",
    "household_demographics", "income_band", "inventory", "item",
    "promotion", "reason", "ship_mode", "store", "store_returns",
    "store_sales", "time_dim", "warehouse", "web_page", "web_returns",
    "web_sales", "web_site",
]


def register_parquet_tables(spark, parquet_dir: str):
    """Register Parquet directories as temporary views, skipping missing ones."""
    print(f"Registering Parquet tables from {parquet_dir}")
    for table in TPCDS_TABLES:
        table_path = os.path.join(parquet_dir, table)
        if not os.path.isdir(table_path):
            print(f"  Warning: {table_path} not found, skipping")
            continue
        try:
            spark.read.parquet(table_path).createOrReplaceTempView(table)
            print(f"  Registered {table}")
        except Exception as e:
            print(f"  Warning: could not register {table}: {e}")


def execute_ddl(spark, ddl_file: str):
    """Execute a DDL file containing multiple SQL statements."""
    if not ddl_file or not os.path.exists(ddl_file):
        return
    print(f"Executing DDL: {ddl_file}")
    with open(ddl_file) as f:
        content = f.read()
    for stmt in content.split(";"):
        stmt = stmt.strip()
        if stmt and not stmt.startswith("--"):
            spark.sql(stmt)


def run_query(spark, sql_file: str) -> dict:
    """Run a single SQL file and return timing info."""
    query_name = os.path.splitext(os.path.basename(sql_file))[0]
    with open(sql_file) as f:
        sql = f.read().strip()
    if not sql:
        return {"query": query_name, "status": "skipped", "time_seconds": 0}

    print(f"Running {query_name}...")
    start = time.time()
    try:
        df = spark.sql(sql)
        row_count = df.count()  # force materialization
        elapsed = time.time() - start
        print(f"  {query_name}: {elapsed:.2f}s ({row_count} rows)")
        return {
            "query": query_name,
            "status": "success",
            "time_seconds": round(elapsed, 3),
            "row_count": row_count,
        }
    except Exception as e:
        elapsed = time.time() - start
        print(f"  {query_name}: FAILED ({elapsed:.2f}s) - {e}")
        traceback.print_exc()
        return {
            "query": query_name,
            "status": "failed",
            "time_seconds": round(elapsed, 3),
            "error": str(e),
        }


def main():
    parser = argparse.ArgumentParser(description="Run TPC-DS benchmark queries")
    parser.add_argument("--sql-dir", required=True, help="Directory containing .sql files")
    parser.add_argument("--parquet-dir", default=None, help="Parquet data directory to register tables from")
    parser.add_argument("--ddl-file", default=None, help="DDL file to execute before queries")
    parser.add_argument("--views-file", default=None, help="Views file to execute before queries")
    parser.add_argument("--output-file", required=True, help="Output JSON file path")
    args = parser.parse_args()

    spark = SparkSession.builder \
        .appName("TPC-DS Benchmark") \
        .getOrCreate()

    # Register tables from Parquet directory (preferred)
    if args.parquet_dir:
        register_parquet_tables(spark, args.parquet_dir)
    elif args.ddl_file:
        execute_ddl(spark, args.ddl_file)

    # Execute additional views (e.g., Iceberg aliases)
    if args.views_file:
        execute_ddl(spark, args.views_file)

    # Collect and sort SQL files
    sql_files = sorted(
        os.path.join(args.sql_dir, f)
        for f in os.listdir(args.sql_dir)
        if f.endswith(".sql")
    )
    print(f"Found {len(sql_files)} queries in {args.sql_dir}")

    # Run queries
    results = []
    total_start = time.time()
    for sql_file in sql_files:
        results.append(run_query(spark, sql_file))
    total_elapsed = time.time() - total_start

    # Write results
    output = {
        "total_time_seconds": round(total_elapsed, 3),
        "query_count": len(results),
        "successful": sum(1 for r in results if r["status"] == "success"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "results": results,
    }
    os.makedirs(os.path.dirname(args.output_file), exist_ok=True)
    with open(args.output_file, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults written to {args.output_file}")
    print(f"Total: {total_elapsed:.2f}s | Success: {output['successful']} | Failed: {output['failed']}")

    spark.stop()


if __name__ == "__main__":
    main()
