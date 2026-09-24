#!/usr/bin/env python3
"""Lake-loader merge benchmark: repeated incremental upserts into Hudi or Iceberg.

The query benchmark measures reads. This measures the other half of a lakehouse workload:
an ingestion loop that keeps merging change batches into a large table. Each round builds a
batch of updates to existing rows plus a batch of brand-new rows, stages it as Parquet, and
then merges it into the target table. Only the merge is timed.

Hudi is merged through its ``upsert`` write operation and Iceberg through ``MERGE INTO``,
because those are each format's native loader path. Both run identically on OSS Spark and on
Quanton, so the per-round times are directly comparable.

Every round is validated: the row count must grow by exactly the number of inserted rows,
and a probe of updated rows must come back carrying the new values. A round that fails
validation is reported as such rather than counted as a fast merge.

Usage
-----
    merge_benchmark.py --format iceberg --table store_sales \
        --source-uri s3a://my-bucket/tpcds/parquet/1TB \
        --stage-uri s3a://my-bucket/tpcds/merge-batches \
        --rounds 5
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pyspark.sql import SparkSession  # noqa: E402
from pyspark.sql import functions as F  # noqa: E402
from load_tables import (  # noqa: E402
    PRECOMBINE_COLUMN,
    RECORD_KEYS,
    hudi_options,
    load_hudi,
    load_iceberg,
    partition_column,
)

# Per table: the bigint key column whose value is shifted to mint new rows, and the measure
# columns an update rewrites. Restricted to the three sales fact tables, which are the only
# TPC-DS tables large enough for a merge benchmark to say anything.
MERGE_TABLES = {
    "store_sales": {
        "shift_column": "ss_ticket_number",
        "update_columns": ["ss_quantity", "ss_sales_price", "ss_net_profit"],
    },
    "catalog_sales": {
        "shift_column": "cs_order_number",
        "update_columns": ["cs_quantity", "cs_sales_price", "cs_net_profit"],
    },
    "web_sales": {
        "shift_column": "ws_order_number",
        "update_columns": ["ws_quantity", "ws_sales_price", "ws_net_profit"],
    },
}

# Added to a staged batch to tell updates from inserts, then dropped before the merge.
OP_COLUMN = "_bench_op"

# New rows get their key column shifted by round * KEY_SHIFT. TPC-DS transaction numbers are
# far below this, so a shifted key can never collide with a real one or with another round.
KEY_SHIFT = 10**11

PROBE_ROWS = 20


def tpcds_columns(frame):
    """Strip Hudi's bookkeeping columns so a frame carries the plain TPC-DS schema."""
    extra = [c for c in frame.columns if c.startswith("_hoodie_") or c == PRECOMBINE_COLUMN]
    return frame.drop(*extra) if extra else frame


def build_batch(spark, target_frame, source_frame, config, round_index, args):
    """Create one round's change batch: sampled updates plus brand-new inserts.

    Updates are sampled from the *target*, so every update row is guaranteed to match an
    existing key even when the target was prepared from a sample of the source. Inserts come
    from the source with a shifted key, so they are guaranteed not to match anything.
    """
    types = dict(source_frame.dtypes)
    shift_column = config["shift_column"]

    updates = target_frame.sample(False, args.update_fraction, seed=args.seed + round_index)
    for column in config["update_columns"]:
        if column not in types:
            continue
        # Nudge each measure so an updated row is distinguishable, then cast back to the
        # column's declared type so the batch schema still matches the target exactly.
        if types[column].startswith("decimal"):
            updates = updates.withColumn(
                column, (F.col(column) * F.lit(1.05)).cast(types[column])
            )
        else:
            updates = updates.withColumn(column, (F.col(column) + F.lit(1)).cast(types[column]))
    updates = updates.withColumn(OP_COLUMN, F.lit("U"))

    inserts = source_frame.sample(False, args.insert_fraction, seed=args.seed + 5000 + round_index)
    inserts = inserts.withColumn(
        shift_column, (F.col(shift_column) + F.lit(KEY_SHIFT * round_index)).cast(types[shift_column])
    )
    inserts = inserts.withColumn(OP_COLUMN, F.lit("I"))

    return updates.unionByName(inserts)


def stage_batch(spark, batch, stage_uri, table, round_index, args):
    """Materialise the batch so the timed merge reads real files, like a real loader does."""
    path = "%s/%s/round=%d" % (stage_uri.rstrip("/"), table, round_index)
    batch.repartition(args.batch_partitions).write.mode("overwrite").parquet(path)
    staged = spark.read.parquet(path)
    counts = {
        row[OP_COLUMN]: row["n"]
        for row in staged.groupBy(OP_COLUMN).agg(F.count(F.lit(1)).alias("n")).collect()
    }
    return path, counts.get("U", 0), counts.get("I", 0)


def probe_expectations(staged, key_columns, update_columns):
    """Take a sample of the updates so the merge result can be checked afterwards."""
    columns = key_columns + [c for c in update_columns if c not in key_columns]
    rows = staged.filter(F.col(OP_COLUMN) == "U").select(*columns).limit(PROBE_ROWS).collect()
    return [row.asDict() for row in rows]


def verify_probe(target_frame, probes, key_columns, update_columns):
    """Confirm every probed key came back from the target carrying its new values."""
    mismatches = 0
    for probe in probes:
        condition = None
        for column in key_columns:
            clause = F.col(column) == F.lit(probe[column])
            condition = clause if condition is None else (condition & clause)
        found = target_frame.filter(condition).select(*update_columns).limit(1).collect()
        if not found:
            mismatches += 1
            continue
        actual = found[0].asDict()
        for column in update_columns:
            if str(actual[column]) != str(probe[column]):
                mismatches += 1
                break
    return mismatches


def main():
    parser = argparse.ArgumentParser(description="Lake-loader merge benchmark")
    parser.add_argument("--format", required=True, choices=["hudi", "iceberg"])
    parser.add_argument("--table", default="store_sales", choices=sorted(MERGE_TABLES))
    parser.add_argument("--source-uri", required=True, help="Base URI of the Parquet dataset")
    parser.add_argument("--target-uri", default="", help="Base URI of the Hudi tables")
    parser.add_argument("--stage-uri", required=True, help="Where change batches are staged")
    parser.add_argument("--catalog", default="lakehouse", help="Iceberg catalog name")
    parser.add_argument("--database", default="tpcds", help="Iceberg namespace")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--update-fraction", type=float, default=0.01)
    parser.add_argument("--insert-fraction", type=float, default=0.002)
    parser.add_argument("--batch-partitions", type=int, default=200)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--engine", default="unknown", help="Label for this run, e.g. oss or quanton")
    parser.add_argument("--target-suffix", default="",
                        help="Suffix for this run's private copy of the merge target")
    parser.add_argument("--source-fraction", type=float, default=1.0,
                        help="Fraction of the source table to build the target from")
    parser.add_argument("--skip-prepare", action="store_true",
                        help="Reuse an existing target instead of rebuilding it")
    parser.add_argument("--partition-facts", default="none", choices=["none", "date"])
    parser.add_argument("--iceberg-write-mode", default="copy-on-write",
                        choices=["copy-on-write", "merge-on-read"])
    parser.add_argument("--hudi-table-type", default="COPY_ON_WRITE",
                        choices=["COPY_ON_WRITE", "MERGE_ON_READ"])
    parser.add_argument("--hudi-index-type", default="BLOOM",
                        choices=["BLOOM", "SIMPLE", "GLOBAL_SIMPLE", "BUCKET", "RECORD_INDEX"])
    parser.add_argument("--shuffle-parallelism", type=int, default=2000)
    parser.add_argument("--output-json", default="")
    args = parser.parse_args()

    if args.format == "hudi" and not args.target_uri:
        raise SystemExit("--target-uri is required for the hudi format")

    table = args.table
    config = MERGE_TABLES[table]
    key_columns = RECORD_KEYS[table].split(",")
    update_columns = config["update_columns"]

    spark = SparkSession.builder.appName(
        "TPC-DS Merge %s %s" % (args.engine, args.format)
    ).getOrCreate()
    spark.sparkContext.setLogLevel("WARN")

    # Each engine merges into its own copy of the target. Merging is destructive, so sharing
    # one table would mean the second engine starts from a table the first one already grew.
    physical = table + args.target_suffix
    source = "%s/%s" % (args.source_uri.rstrip("/"), table)
    hudi_path = "%s/%s" % (args.target_uri.rstrip("/"), physical)
    iceberg_table = "%s.%s.%s" % (args.catalog, args.database, physical)

    def read_target():
        if args.format == "hudi":
            return spark.read.format("hudi").load(hudi_path)
        return spark.table(iceberg_table)

    prepare_seconds = 0.0
    if not args.skip_prepare:
        print("Preparing merge target %s from %s (fraction %.3f)"
              % (physical, source, args.source_fraction))
        prepare_start = time.time()
        if args.format == "hudi":
            load_hudi(spark, table, source, args.target_uri.rstrip("/"), args,
                      fraction=args.source_fraction, table_name=physical)
        else:
            spark.sql("CREATE NAMESPACE IF NOT EXISTS %s.%s" % (args.catalog, args.database))
            load_iceberg(spark, table, source, iceberg_table, args,
                         fraction=args.source_fraction)
        prepare_seconds = time.time() - prepare_start
        print("  prepared in %.1fs" % prepare_seconds)

    rows_before_run = read_target().count()
    print("Target %s starts with %d rows" % (physical, rows_before_run))

    summary = {
        "phase": "merge",
        "engine": args.engine,
        "format": args.format,
        "table": physical,
        "base_table": table,
        "prepare_seconds": round(prepare_seconds, 3),
        "source_fraction": args.source_fraction,
        "rounds": args.rounds,
        "update_fraction": args.update_fraction,
        "insert_fraction": args.insert_fraction,
        "initial_rows": rows_before_run,
        "results": [],
    }

    expected_rows = rows_before_run
    run_start = time.time()

    for round_index in range(1, args.rounds + 1):
        print("--- merge round %d/%d ---" % (round_index, args.rounds))

        batch = build_batch(
            spark, tpcds_columns(read_target()), spark.read.parquet(source),
            config, round_index, args,
        )
        # Stage under the physical target name, so the two engines never share a batch path.
        stage_path, update_rows, insert_rows = stage_batch(
            spark, batch, args.stage_uri, physical, round_index, args
        )
        staged = spark.read.parquet(stage_path)
        probes = probe_expectations(staged, key_columns, update_columns)
        print("  staged %d updates + %d inserts at %s" % (update_rows, insert_rows, stage_path))

        merge_source = staged.drop(OP_COLUMN)

        started = time.time()
        if args.format == "hudi":
            options = hudi_options(
                table, args, partition_column(table, args.partition_facts), table_name=physical
            )
            options["hoodie.datasource.write.operation"] = "upsert"
            (
                merge_source.withColumn(PRECOMBINE_COLUMN, F.lit(round_index).cast("bigint"))
                .write.format("hudi")
                .options(**options)
                .mode("append")
                .save(hudi_path)
            )
        else:
            merge_source.createOrReplaceTempView("_bench_batch")
            on_clause = " AND ".join("t.%s = s.%s" % (c, c) for c in key_columns)
            spark.sql(
                "MERGE INTO %s t USING _bench_batch s ON %s "
                "WHEN MATCHED THEN UPDATE SET * "
                "WHEN NOT MATCHED THEN INSERT *" % (iceberg_table, on_clause)
            )
        merge_seconds = time.time() - started

        validate_start = time.time()
        target = read_target()
        actual_rows = target.count()
        expected_rows += insert_rows
        mismatches = verify_probe(target, probes, key_columns, update_columns)
        validate_seconds = time.time() - validate_start

        valid = actual_rows == expected_rows and mismatches == 0
        print(
            "  merge %.1fs | rows %d (expected %d) | probe mismatches %d | %s"
            % (merge_seconds, actual_rows, expected_rows, mismatches, "PASS" if valid else "FAIL")
        )

        summary["results"].append(
            {
                "round": round_index,
                "update_rows": update_rows,
                "insert_rows": insert_rows,
                "merge_seconds": round(merge_seconds, 3),
                "validate_seconds": round(validate_seconds, 3),
                "rows_after": actual_rows,
                "expected_rows": expected_rows,
                "probe_mismatches": mismatches,
                "status": "success" if valid else "failed",
            }
        )

    merge_times = [r["merge_seconds"] for r in summary["results"] if r["status"] == "success"]
    summary["total_seconds"] = round(time.time() - run_start, 3)
    summary["total_merge_seconds"] = round(sum(r["merge_seconds"] for r in summary["results"]), 3)
    summary["mean_merge_seconds"] = round(sum(merge_times) / len(merge_times), 3) if merge_times else None
    summary["rounds_passed"] = sum(1 for r in summary["results"] if r["status"] == "success")
    summary["rounds_failed"] = sum(1 for r in summary["results"] if r["status"] == "failed")

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
