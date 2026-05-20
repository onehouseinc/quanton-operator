"""
hudi_merge_into_demo.py — Hudi CREATE / INSERT / MERGE INTO demo (COW).

End-to-end MERGE INTO demo:
  1. CREATE TABLE customers (Hudi COW, primary key = id, precombine = ts).
  2. INSERT 10 starting rows.
  3. Build a source DataFrame with 6 rows: 3 updates + 3 inserts.
  4. MERGE INTO ... WHEN MATCHED ... WHEN NOT MATCHED ... using SQL.
  5. Verify the result (10 -> 13 rows, 3 updated to 'vip').

Usage:
  spark-submit hudi_merge_into_demo.py <warehouseDir>
"""

import sys

from pyspark.sql import SparkSession, Row


warehouse = sys.argv[1].rstrip("/")
table_path = f"{warehouse}/customers"
table = "default.customers"

spark = (SparkSession.builder
         .appName("HudiMergeIntoDemo")
         .config("spark.serializer", "org.apache.spark.serializer.KryoSerializer")
         .config("spark.kryo.registrator", "org.apache.spark.HoodieSparkKryoRegistrar")
         .config("spark.sql.extensions", "org.apache.spark.sql.hudi.HoodieSparkSessionExtension")
         .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.hudi.catalog.HoodieCatalog")
         .getOrCreate())
spark.sparkContext.setLogLevel("WARN")

print(f"[hudi-merge] Creating {table} at {table_path}")
spark.sql(f"DROP TABLE IF EXISTS {table}")
spark.sql(f"""
  CREATE TABLE {table} (
    id      BIGINT,
    ts      BIGINT,
    name    STRING,
    region  STRING,
    amount  DECIMAL(15,2),
    status  STRING
  )
  USING hudi
  TBLPROPERTIES (
    type             = 'cow',
    primaryKey       = 'id',
    preCombineField  = 'ts'
  )
  LOCATION '{table_path}'
""")

print(f"[hudi-merge] INSERT 10 initial rows")
spark.sql(f"""
  INSERT INTO {table} VALUES
    (1,  1, 'Alice',   'us-east-1',  100.00, 'active'),
    (2,  1, 'Bob',     'us-west-2',  250.50, 'active'),
    (3,  1, 'Carol',   'eu-west-1',   75.25, 'active'),
    (4,  1, 'Dave',    'ap-south-1', 500.00, 'active'),
    (5,  1, 'Eve',     'us-east-1',  150.75, 'active'),
    (6,  1, 'Frank',   'us-west-2',  300.00, 'active'),
    (7,  1, 'Grace',   'eu-west-1',  220.00, 'active'),
    (8,  1, 'Heidi',   'ap-south-1', 410.00, 'active'),
    (9,  1, 'Ivan',    'us-east-1',  180.00, 'active'),
    (10, 1, 'Judy',    'us-west-2',  275.00, 'active')
""")
rows_before = int(spark.sql(f"SELECT count(*) AS c FROM {table}").collect()[0]["c"])
print(f"[hudi-merge] After INSERT: {rows_before} rows")

# Source dataset: 3 updates (id 2, 5, 8) and 3 new inserts (id 11, 12, 13)
# Bump ts so updates win precombine.
source = spark.createDataFrame([
    Row(id=2,  ts=2, name='Bob',    region='us-west-2',  amount=999.99, status='vip'),
    Row(id=5,  ts=2, name='Eve',    region='us-east-1',  amount=888.88, status='vip'),
    Row(id=8,  ts=2, name='Heidi',  region='ap-south-1', amount=777.77, status='vip'),
    Row(id=11, ts=2, name='Kevin',  region='us-east-1',  amount=120.00, status='active'),
    Row(id=12, ts=2, name='Laura',  region='us-west-2',  amount=140.00, status='active'),
    Row(id=13, ts=2, name='Mallory',region='eu-west-1',  amount=160.00, status='active'),
])
source.createOrReplaceTempView("source_updates")

print(f"[hudi-merge] MERGE INTO {table} (3 updates + 3 inserts)")
spark.sql(f"""
  MERGE INTO {table} t
  USING source_updates s
  ON t.id = s.id
  WHEN MATCHED THEN UPDATE SET
    t.ts     = s.ts,
    t.amount = s.amount,
    t.status = s.status
  WHEN NOT MATCHED THEN INSERT (id, ts, name, region, amount, status)
    VALUES (s.id, s.ts, s.name, s.region, s.amount, s.status)
""")

rows_after = int(spark.sql(f"SELECT count(*) AS c FROM {table}").collect()[0]["c"])
vip_count = int(spark.sql(f"SELECT count(*) AS c FROM {table} WHERE status = 'vip'").collect()[0]["c"])
print(f"[hudi-merge] After MERGE: {rows_after} rows, {vip_count} 'vip'")
print(f"[hudi-merge] Final table:")
spark.sql(f"SELECT id, ts, name, region, amount, status FROM {table} ORDER BY id").show(50, truncate=False)

assert rows_after == 13, f"Expected 13 rows after merge, got {rows_after}"
assert vip_count == 3,   f"Expected 3 vip rows after merge, got {vip_count}"
print(f"[hudi-merge] PASS — {rows_before} -> {rows_after} rows, 3 updated to 'vip'")
spark.stop()
