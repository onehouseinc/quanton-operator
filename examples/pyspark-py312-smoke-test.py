#!/usr/bin/env python3
# Requires Python 3.12+ — uses PEP 695 type alias syntax (SyntaxError on Python 3.11)
from pyspark.sql import SparkSession
from pyspark.sql.functions import sum as spark_sum

# PEP 695 type alias — new keyword `type` introduced in Python 3.12.
# This line is a SyntaxError on Python 3.11 and earlier.
type Row = tuple[str, int]

spark = SparkSession.builder.appName("PySpark-Py312-Smoke-Test").getOrCreate()

data: list[Row] = [("alice", 10), ("bob", 20), ("carol", 30)]
df = spark.createDataFrame(data, ["name", "value"])

total = df.agg(spark_sum("value")).collect()[0][0]
assert total == 60, f"Expected 60, got {total}"
print(f"Test 1 PASSED: sum = {total}")

df.createOrReplaceTempView("people")
result = spark.sql("SELECT name FROM people ORDER BY name").collect()
assert [r.name for r in result] == ["alice", "bob", "carol"]
print(f"Test 2 PASSED: names = {[r.name for r in result]}")

print("ALL TESTS PASSED")
spark.stop()
