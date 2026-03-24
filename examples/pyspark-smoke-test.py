#!/usr/bin/env python3
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, sum as spark_sum

spark = SparkSession.builder.appName("PySpark-Smoke-Test").getOrCreate()

# Test 1: basic DataFrame
data = [("alice", 10), ("bob", 20), ("carol", 30)]
df = spark.createDataFrame(data, ["name", "value"])
total = df.agg(spark_sum("value")).collect()[0][0]
assert total == 60, f"Expected 60, got {total}"
print(f"Test 1 PASSED: sum = {total}")

# Test 2: SparkSQL
df.createOrReplaceTempView("people")
result = spark.sql("SELECT name, value * 2 AS doubled FROM people ORDER BY name").collect()
assert len(result) == 3
print(f"Test 2 PASSED: sql rows = {[r.name for r in result]}")

print("ALL TESTS PASSED")
spark.stop()
