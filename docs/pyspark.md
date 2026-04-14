# PySpark on Quanton

The Quanton Spark image ships with multiple Python versions so you can run PySpark jobs against any supported interpreter without building a custom image.

## Supported Python versions

| Version | Path |
|---|---|
| Python 3.9 | `/usr/bin/python3.9` |
| Python 3.11 | `/usr/bin/python3.11` *(default)* |
| Python 3.12 | `/usr/bin/python3.12` |

The default interpreter (used when no `PYSPARK_PYTHON` is set) is Python 3.11, configured by the image's `spark-env.sh`.

## Selecting a Python version

Set `PYSPARK_PYTHON` on the executor and `PYSPARK_DRIVER_PYTHON` on the driver to choose a specific interpreter. No `PYTHONPATH` override is needed — Spark's launcher injects the correct PySpark path automatically.

### Python 3.9

```yaml
driver:
  envVars:
    PYSPARK_PYTHON: "/usr/bin/python3.9"
    PYSPARK_DRIVER_PYTHON: "/usr/bin/python3.9"
executor:
  envVars:
    PYSPARK_PYTHON: "/usr/bin/python3.9"
```

### Python 3.12

```yaml
driver:
  envVars:
    PYSPARK_PYTHON: "/usr/bin/python3.12"
    PYSPARK_DRIVER_PYTHON: "/usr/bin/python3.12"
executor:
  envVars:
    PYSPARK_PYTHON: "/usr/bin/python3.12"
```

## Full example

The following `QuantonSparkApplication` runs a PySpark script from a ConfigMap using Python 3.12:

```yaml
apiVersion: quantonsparkoperator.onehouse.ai/v1beta2
kind: QuantonSparkApplication
metadata:
  name: my-pyspark-job
  namespace: default
spec:
  sparkApplicationSpec:
    type: Python
    mode: cluster
    image: "dist.onehouse.ai/onehouseai/quanton-spark:release-v1.29.0-al2023"
    imagePullPolicy: IfNotPresent
    mainApplicationFile: "local:///mnt/scripts/my_job.py"
    sparkVersion: "3.5.0"
    restartPolicy:
      type: Never
    volumes:
      - name: scripts
        configMap:
          name: my-job-scripts
    driver:
      cores: 2
      memory: "4096m"
      serviceAccount: spark-operator-spark
      envVars:
        PYSPARK_PYTHON: "/usr/bin/python3.12"
        PYSPARK_DRIVER_PYTHON: "/usr/bin/python3.12"
      volumeMounts:
        - name: scripts
          mountPath: /mnt/scripts
    executor:
      cores: 2
      instances: 2
      memory: "4096m"
      envVars:
        PYSPARK_PYTHON: "/usr/bin/python3.12"
      volumeMounts:
        - name: scripts
          mountPath: /mnt/scripts
```

Omit the `envVars` block entirely to use the default Python 3.11 interpreter.

## Smoke test

A ready-to-run smoke test is provided in `examples/quanton-pyspark-smoke-test.yaml`. It runs two assertions (DataFrame aggregation and SparkSQL) and prints `ALL TESTS PASSED` on success.

```bash
kubectl apply -f examples/quanton-pyspark-smoke-test.yaml
kubectl logs quanton-pyspark-smoke-test-driver -n default | grep -E "PASSED|FAILED|ALL TESTS"
```
