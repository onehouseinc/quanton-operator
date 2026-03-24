# Hudi Smoke Test

This guide walks through running the PySpark Hudi smoke test on a Quanton cluster. The test writes a 10-row COW table, reads it back, and runs filter, aggregation, partition-pruning, and upsert assertions.

## What the test does

| Step | Operation | Assertion |
|------|-----------|-----------|
| 1 | `bulk_insert` — write 10 customer rows partitioned by `country` | read-back count == 10 |
| 2 | Filter `is_active = true` | count == 8 |
| 3 | Aggregate `avg(spend_amount)` | result > 0 |
| 4 | Filter `country = 'US'` (partition pruning) | count == 2 |
| 5 | `upsert` — update Alice's `spend_amount` to 9999.99 | count still 10, value updated |

Files:
- `examples/hudi_test.py` — the PySpark script
- `examples/quanton-pyspark-hudi-test.yaml` — ConfigMap + PVC + QuantonSparkApplication

## Prerequisites

- Quanton Operator installed (see [Getting Started](getting-started.md))
- Spark Operator installed with `spark-operator-spark` service account in the target namespace
- Pods have outbound internet access — the Hudi bundle JAR (~60MB) is downloaded from Maven Central at job startup via `spark.jars.packages`

## Running the test

```bash
kubectl apply -f examples/quanton-pyspark-hudi-test.yaml
```

This creates three resources:

1. **ConfigMap** `pyspark-hudi-test-script` — mounts `hudi_test.py` into the driver and executor at `/mnt/scripts/`
2. **PersistentVolumeClaim** `hudi-test-pvc` — 2Gi shared volume for Hudi table output
3. **QuantonSparkApplication** `quanton-pyspark-hudi-test` — runs the test

Wait for completion (typically ~60s after the Hudi JAR downloads):

```bash
kubectl wait sparkapplication quanton-pyspark-hudi-test \
  --for=jsonpath='{.status.applicationState.state}'=COMPLETED \
  --timeout=300s
```

Check results:

```bash
kubectl logs quanton-pyspark-hudi-test-driver | grep -E "\[hudi_test\]|PASSED"
```

Expected output:

```
[hudi_test] Writing 10 rows to Hudi table at /data/hudi-output/pyspark_hudi_customers
[hudi_test] PASS — read back 10 rows from /data/hudi-output/pyspark_hudi_customers
[hudi_test] PASS — filter: 8 active customers
[hudi_test] PASS — avg spend_amount = 1737.80
[hudi_test] PASS — partition filter: 2 US customers
[hudi_test] PASS — upsert: row count still 10, Alice spend_amount = 9999.99

  All PySpark Hudi tests PASSED
```

## Cleanup

```bash
kubectl delete -f examples/quanton-pyspark-hudi-test.yaml
```

## Troubleshooting

### Volume mount: executor can't find the Hudi table path

**Symptom:** Job fails with `FileNotFoundException` on the table path, typically during the read-back or metadata init step:

```
java.io.FileNotFoundException: File /data/hudi-output/pyspark_hudi_customers does not exist
org.apache.hudi.exception.HoodieException: Failed to instantiate Metadata table
```

**Cause:** The driver and executor are separate pods. An `emptyDir` volume is not shared between them — each pod gets its own independent directory. The driver writes the Hudi table but the executor cannot see it.

**Fix:** Use a `PersistentVolumeClaim` with `ReadWriteOnce` (or `ReadWriteMany` for multi-node clusters) and mount it on both driver and executor. The YAML in this repo already does this via `hudi-test-pvc`. If you're adapting the manifest for another cluster, ensure:

```yaml
volumes:
  - name: hudi-output
    persistentVolumeClaim:
      claimName: hudi-test-pvc   # NOT emptyDir

driver:
  volumeMounts:
    - name: hudi-output
      mountPath: /data/hudi-output

executor:
  volumeMounts:
    - name: hudi-output
      mountPath: /data/hudi-output   # same path on both pods
```

On multi-node clusters, the PVC access mode must be `ReadWriteMany` (e.g. EFS on EKS, NFS) so the executor pod can be scheduled on a different node than the driver and still access the same volume. On single-node clusters (minikube), `ReadWriteOnce` works because both pods land on the same node.

### Hudi JAR download fails or times out

**Symptom:** Job hangs or fails during startup with a Maven resolution error.

**Cause:** Pods don't have outbound internet access to `repo1.maven.org`.

**Fix:** Either open egress to Maven Central, or pre-stage the JAR. To pre-stage:

```bash
# Download the JAR on a machine with internet access
mvn dependency:get \
  -Dartifact=org.apache.hudi:hudi-spark3.5-bundle_2.12:0.15.0 \
  -Ddest=hudi-spark3.5-bundle_2.12-0.15.0.jar

# Upload to S3 (or another accessible location) and reference via spark.jars
```

Then replace `spark.jars.packages` in the manifest with:

```yaml
sparkConf:
  spark.jars: "s3a://your-bucket/jars/hudi-spark3.5-bundle_2.12-0.15.0.jar"
```

### Job fails with SIGILL on Apple Silicon

Quanton's Velox engine is compiled for x86_64 and AWS Graviton (SVE2). Running on Apple Silicon (arm64) causes `SIGILL` (illegal instruction). This is a known limitation — use an x86_64 or Graviton node for Quanton jobs.
