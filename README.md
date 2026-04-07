# Quanton Operator

Quanton Operator is a Kubernetes operator that extends [kubeflow/spark-operator](https://github.com/kubeflow/spark-operator) to run Apache Spark jobs using the [Quanton](https://www.onehouse.ai/blog/announcing-spark-and-sql-on-the-onehouse-compute-runtime-with-quanton) compute engine by Onehouse. Quanton is a purpose-built query execution engine that delivers 2-3x better price-performance for ETL workloads.

The operator provides a seamless migration path — submit your existing Spark jobs as `QuantonSparkApplication` resources and the operator handles the rest.

## Prerequisites

- Kubernetes >= 1.28
- Helm >= 3.x
- kubectl configured for your cluster
- [Spark Operator](https://github.com/kubeflow/spark-operator) (v1.x or v2.x) installed on the cluster
- An [Onehouse](https://www.onehouse.ai) account with Quanton access
- Network access to `*.onehouse.ai` and `*.docker.io`

## Quick Start

1. Obtain your `onehouse-values.yaml` from the [Onehouse console](https://www.cloud.onehouse.ai).
2. Install the operator:

```bash
helm upgrade --install quanton-operator oci://registry-1.docker.io/onehouseai/quanton-operator \
    --version 1.0.0 \
    --namespace quanton-operator \
    --create-namespace \
    -f onehouse-values.yaml
```

3. Submit a sample Spark job:

```bash
kubectl apply -f examples/quanton-application.yaml
```

4. Verify the job output:

```bash
kubectl logs -f quanton-spark-pi-java-example-driver | grep -i "pi is"
```

Expected output:

```
Pi is roughly 3.1416568
```

For a step-by-step walkthrough including local setup with minikube, see the [Getting Started](docs/getting-started.md) guide.

## Features

- **QuantonSparkApplication CRD** — Declarative API for submitting Spark jobs to Quanton. Wraps the standard `SparkApplication` spec with automatic image injection, JWT token management, and lifecycle tracking.
- **Airflow Integration** — Native [Airflow provider](docs/airflow.md) for orchestrating Quanton Spark jobs from your DAGs.
- **Namespace Isolation** — Run Spark jobs in dedicated namespaces with scoped RBAC and secret management. See [Configuration](docs/configurations.md).
- **Observability** — Built-in metrics collection via OpenTelemetry. See [Metrics](docs/metrics.md).
- **Security** — mTLS, JWT token protection, and least-privilege RBAC. See [Security](docs/security.md).

## Documentation


| Document                                               | Description                                          |
| ------------------------------------------------------ | ---------------------------------------------------- |
| [Getting Started](docs/getting-started.md)             | Local setup with minikube and first job submission   |
| [Configuration Reference](docs/configurations.md)      | All Helm chart parameters                            |
| [Airflow Provider](docs/airflow.md)                    | Orchestrating Quanton jobs from Apache Airflow       |
| [Metrics](docs/metrics.md)                             | Telemetry and metrics collection                     |
| [Security](docs/security.md)                           | All information about network security               |
| [Helm Chart](charts/quanton-operator-chart/README.md)  | Chart-specific installation and upgrade instructions |
| [Memory configurations](docs/memory-configurations.md) | Understanding and configuring memory for Quanton     |


## Example

A minimal `QuantonSparkApplication` resource:

```yaml
apiVersion: onehouse.ai/v1beta2
kind: QuantonSparkApplication
metadata:
  name: my-spark-job
  namespace: default
spec:
  sparkApplicationSpec:
    type: Java
    mode: cluster
    image: "apache/spark:3.5.0"
    mainClass: org.apache.spark.examples.JavaSparkPi
    mainApplicationFile: "local:///opt/spark/examples/jars/calculate-pi-example_2.12-3.5.0.jar"
    sparkVersion: "3.5.0"
    driver:
      cores: 1
      memory: "1024m"
      serviceAccount: spark-operator-spark
    executor:
      cores: 1
      instances: 2
      memory: "1024m"
```

See [examples/](examples/) for more samples.

## Migration Tool

The `scripts/` directory contains a CLI utility (Python) to convert existing `SparkApplication` CRDs to `QuantonSparkApplication` format:

```bash
# Python
python scripts/transform.py -input my-spark-app.yaml -output my-quanton-app.yaml
```

The tool validates the input and rewrites `apiVersion`, `kind`, and nests `spec` under `spec.sparkApplicationSpec`. See [scripts/INSTRUCTIONS.md](scripts/INSTRUCTIONS.md) for details.

## Claude Code

If you have [Claude Code](https://claude.com/claude-code) installed, you can set up and demo Quanton interactively from the terminal. The repo ships with two skills (slash commands) that automate the full setup and benchmarking workflow.

Start Claude Code in the repo root:

```bash
claude
```

Then use either skill:


| Skill                    | What it does                                                                                                                                                                                                          |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/setup-and-run-example` | Sets up minikube, installs Spark Operator + Quanton Operator, and runs a sample SparkPi job end-to-end. Walks you through each step interactively.                                                                    |
| `/run-tpcds-benchmark`   | Runs the TPC-DS read benchmark (99 queries on Parquet) comparing OSS Spark vs Quanton. Asks you for scale factor and configuration, gives live progress updates, and produces a per-query comparison table and chart. |


Both skills check prerequisites, handle errors, and give you live progress updates as jobs run on your local minikube cluster. You will need `onehouse-values.yaml` (from the [Onehouse console](https://www.cloud.onehouse.ai)) to install the Quanton Operator.

## Benchmarks

A local benchmark setup is provided [here](benchmarks/README.md), to try Quanton locally on your mac or linux developement machine.

Note on Apple Silicon: The default ARM build for Quanton is optimized for latest ARM architectures like AWS Graviton. It may fail on older macs that don't support latest instruction sets. We suggest using a real Spark setup in those cases.

For industry standard benchmarks, please refer to the following resources.

- [Onehouse Quanton vs the latest AWS EMR for Apache Spark Workloads](https://www.onehouse.ai/blog/onehouse-quanton-vs-the-latest-aws-emr-for-apache-spark-workloads)
- [Apache Iceberg on Quanton: 3x Faster Apache Spark Workloads](https://www.onehouse.ai/blog/apache-iceberg-on-quanton-3x-faster-apache-spark-workloads)

## Community

Join the [Onehouse Community Slack](https://onehouse-community.slack.com/join/shared_invite/zt-3s323tl8w-HKVMu~JirERmsp2Jl3beZg#/shared-invite/email) to connect directly with engineers building Quanton.

## Resources

- [Announcing Apache Spark and SQL on the Onehouse Compute Runtime with Quanton](https://www.onehouse.ai/blog/announcing-spark-and-sql-on-the-onehouse-compute-runtime-with-quanton)
- [Quanton Linkedin Live Event](https://www.linkedin.com/events/7336071337095942147?viewAsMember=true)

## Release Notes

For information about versions of quanton-operator and quanton images, please checkout [versioning](/docs/versioning.md).

## Data Collection

See [data collection](/docs/data-collection.md).

## License

Copyright Onehouse, Inc. All rights reserved.

