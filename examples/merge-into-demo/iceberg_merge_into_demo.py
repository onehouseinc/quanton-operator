"""
iceberg_merge_into_demo.py — Iceberg CREATE / INSERT / MERGE INTO demo on a Hadoop catalog.

End-to-end MERGE INTO demo:
  1. CREATE TABLE customers (Iceberg, Hadoop catalog).
  2. INSERT 10 starting rows.
  3. Build a source DataFrame with 6 rows: 3 updates + 3 inserts.
  4. MERGE INTO ... WHEN MATCHED ... WHEN NOT MATCHED ... using SQL.
  5. Verify the result (10 -> 13 rows, 3 updated to 'vip').

Usage:
  spark-submit iceberg_merge_into_demo.py <warehouseDir>
"""

import sys

from pyspark.sql import SparkSession, Row


warehouse = sys.argv[1].rstrip("/")
table = "default.customers"

spark = (SparkSession.builder
         .appName("IcebergMergeIntoDemo")
         .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
         .config("spark.sql.catalog.spark_catalog", "org.apache.iceberg.spark.SparkSessionCatalog")
         .config("spark.sql.catalog.spark_catalog.type", "hadoop")
         .config("spark.sql.catalog.spark_catalog.warehouse", warehouse)
         .getOrCreate())
spark.sparkContext.setLogLevel("WARN")

print(f"[iceberg-merge] Creating {table} in {warehouse}")
spark.sql(f"DROP TABLE IF EXISTS {table}")
spark.sql(f"""
  CREATE TABLE {table} (
    id BIGINT,
    name STRING,
    region STRING,
    amount DECIMAL(15,2),
    status STRING
  )
  USING iceberg
  TBLPROPERTIES (
    'write.parquet.compression-codec' = 'zstd',
    'format-version'    = '2',
    'write.merge.mode'  = 'copy-on-write',
    'write.update.mode' = 'copy-on-write',
    'write.delete.mode' = 'copy-on-write'
  )
""")
spark.sql(f"ALTER TABLE {table} SET TBLPROPERTIES ('write.distribution-mode' = 'hash')")

print(f"[iceberg-merge] INSERT 10 initial rows")
spark.sql(f"""
  INSERT INTO {table} VALUES
    (1,  'Alice',   'us-east-1',  100.00, 'active'),
    (2,  'Bob',     'us-west-2',  250.50, 'active'),
    (3,  'Carol',   'eu-west-1',   75.25, 'active'),
    (4,  'Dave',    'ap-south-1', 500.00, 'active'),
    (5,  'Eve',     'us-east-1',  150.75, 'active'),
    (6,  'Frank',   'us-west-2',  300.00, 'active'),
    (7,  'Grace',   'eu-west-1',  220.00, 'active'),
    (8,  'Heidi',   'ap-south-1', 410.00, 'active'),
    (9,  'Ivan',    'us-east-1',  180.00, 'active'),
    (10, 'Judy',    'us-west-2',  275.00, 'active')
""")
rows_before = int(spark.sql(f"SELECT count(*) AS c FROM {table}").collect()[0]["c"])
print(f"[iceberg-merge] After INSERT: {rows_before} rows")

# Source dataset: 3 updates (id 2, 5, 8) and 3 new inserts (id 11, 12, 13)
source = spark.createDataFrame([
    Row(id=2,  name='Bob',    region='us-west-2',  amount=999.99, status='vip'),
    Row(id=5,  name='Eve',    region='us-east-1',  amount=888.88, status='vip'),
    Row(id=8,  name='Heidi',  region='ap-south-1', amount=777.77, status='vip'),
    Row(id=11, name='Kevin',  region='us-east-1',  amount=120.00, status='active'),
    Row(id=12, name='Laura',  region='us-west-2',  amount=140.00, status='active'),
    Row(id=13, name='Mallory',region='eu-west-1',  amount=160.00, status='active'),
])
source.createOrReplaceTempView("source_updates")

print(f"[iceberg-merge] MERGE INTO {table} (3 updates + 3 inserts)")
spark.sql(f"""
  MERGE INTO {table} t
  USING source_updates s
  ON t.id = s.id
  WHEN MATCHED THEN UPDATE SET
    t.amount = s.amount,
    t.status = s.status
  WHEN NOT MATCHED THEN INSERT (id, name, region, amount, status)
    VALUES (s.id, s.name, s.region, s.amount, s.status)
""")

rows_after = int(spark.sql(f"SELECT count(*) AS c FROM {table}").collect()[0]["c"])
vip_count = int(spark.sql(f"SELECT count(*) AS c FROM {table} WHERE status = 'vip'").collect()[0]["c"])
print(f"[iceberg-merge] After MERGE: {rows_after} rows, {vip_count} 'vip'")
print(f"[iceberg-merge] Final table:")
spark.sql(f"SELECT * FROM {table} ORDER BY id").show(50, truncate=False)

assert rows_after == 13, f"Expected 13 rows after merge, got {rows_after}"
assert vip_count == 3,   f"Expected 3 vip rows after merge, got {vip_count}"
print(f"[iceberg-merge] PASS — {rows_before} -> {rows_after} rows, 3 updated to 'vip'")
spark.stop()
