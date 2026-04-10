# Airflow Provider for Quanton Spark

The `airflow-provider-quanton-spark-k8s` package provides an Apache Airflow operator for submitting and monitoring Quanton Spark jobs on Kubernetes.

## How It Works

The `QuantonSparkApplicationOperator` creates a `QuantonSparkApplication` Custom Resource on your Kubernetes cluster. The Quanton Operator then reconciles it into a `SparkApplication`, which generates the driver and executor pods with the provided configuration. The Airflow operator polls the job status until it succeeds or fails.

## Prerequisites

- Apache Airflow >= 2.7.0
- Quanton Operator >= 1.0.0 installed on the target Kubernetes cluster
- Airflow configured with a Kubernetes connection to the cluster

## Installation

```bash
pip install airflow-provider-quanton-spark-k8s --extra-index-url https://dist.onehouse.ai/simple/
```

## Usage

### Basic Example

```python
from datetime import datetime

from airflow import DAG
from quanton_provider.operators.quanton_spark_application import (
    QuantonSparkApplicationOperator,
)

with DAG(
    dag_id="quanton_spark_pi",
    start_date=datetime(2024, 1, 1),
    schedule=None,
    catchup=False,
    tags=["quanton", "spark"],
) as dag:
    run_spark_pi = QuantonSparkApplicationOperator(
        task_id="run_spark_pi",
        name="quanton-airflow-spark-pi",
        namespace="default",
        timeout=600,
        delete_on_success=True,
        spark_application_spec={
            "type": "Java",
            "mode": "cluster",
            "image": "apache/spark:3.5.0",
            "imagePullPolicy": "IfNotPresent",
            "mainClass": "org.apache.spark.examples.JavaSparkPi",
            "mainApplicationFile": "local:///opt/spark/examples/jars/spark-examples_2.12-3.5.0.jar",
            "arguments": ["100"],
            "sparkVersion": "3.5.0",
            "restartPolicy": {"type": "Never"},
            "driver": {
                "cores": 1,
                "coreLimit": "1200m",
                "memory": "1024m",
                "labels": {"version": "3.5.0"},
                "serviceAccount": "spark-operator-spark",
            },
            "executor": {
                "cores": 1,
                "instances": 2,
                "memory": "1024m",
                "labels": {"version": "3.5.0"},
            },
        },
    )
```

### Operator Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `name` | `str` | Yes | Name of the `QuantonSparkApplication` resource created on Kubernetes |
| `namespace` | `str` | Yes | Kubernetes namespace for the Spark job. Must be listed in `quantonOperator.jobNamespaces` |
| `spark_application_spec` | `dict` | Yes | Full `sparkApplicationSpec` matching the [QuantonSparkApplication CRD](../charts/quanton-operator-chart/crds/) |
| `timeout` | `int` | No | Maximum wait time in seconds before the task is marked as failed. Default: `600` |
| `delete_on_success` | `bool` | No | Delete the `QuantonSparkApplication` resource after successful completion. Default: `True` |

### Spark Application Spec

The `spark_application_spec` dictionary follows the same schema as the `sparkApplicationSpec` field in the `QuantonSparkApplication` CRD. It accepts any valid [kubeflow SparkApplication](https://github.com/kubeflow/spark-operator/blob/master/docs/api-docs.md) spec fields, including:

- `type` — `Java`, `Scala`, `Python`, or `R`
- `mode` — `cluster` (recommended) or `client`
- `image` — Spark base image (the operator substitutes the Quanton runtime image automatically)
- `mainClass` / `mainApplicationFile` — Entry point for the Spark job
- `driver` / `executor` — Resource configuration for driver and executor pods
- `sparkVersion` — Spark version string
- `restartPolicy` — Restart behavior on failure

For a complete field reference, see the [CRD definition](../charts/quanton-operator-chart/crds/quantonsparkoperator.onehouse.ai_quantonsparkapplications.yaml).
