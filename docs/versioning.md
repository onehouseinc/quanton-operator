# Release Notes

## Quanton Operator Helm Chart

### v2.0.0

Major release of the Quanton Operator with enhanced security, simplified configuration, and AI agent support.

**Breaking Changes:**

- Requires **Spark Operator 2.x.x or later**. Spark Operator 1.x.x is not compatible with this release.
- Removed `projectId`, `linkId`, `endpoint`, `metricsEndpoint`, and `authToken` from Helm values. These parameters are now automatically derived from the mTLS certificate.
- Removed dp-proxy. The operator now communicates directly with the Onehouse control plane via mTLS.

**Highlights:**

- Direct mTLS communication with the Onehouse control plane (dp-proxy removed)
- Simplified JWT handling — authentication tokens are now managed through the mTLS flow rather than as a separate Kubernetes secret
- AI agent support for Spark applications (`enableAIAgent` configuration option)
- Simplified OpenTelemetry collector configuration
- Conditional cluster-wide secret permissions when running in all-namespaces mode
- Helm chart version is now included in operator configuration

### v1.0.0 (Deprecated)

> **Warning:** v1.0.0 is fully deprecated and should not be used. Please upgrade to v2.0.0.

First public release of the Quanton Operator. This release provides a Kubernetes operator that extends [kubeflow/spark-operator](https://github.com/kubeflow/spark-operator) to run Apache Spark jobs using the Quanton compute engine by Onehouse.

**Highlights:**

- `QuantonSparkApplication` CRD (`apiVersion: quantonsparkoperator.onehouse.ai/v1beta2`) for declarative Spark job submission
- Automatic Quanton image injection and lifecycle management
- mTLS and JWT-based security
- Namespace isolation with scoped RBAC and secret management
- Built-in metrics collection via OpenTelemetry
- Airflow provider for orchestrating Quanton Spark jobs
- CLI migration tool to convert `SparkApplication` resources to `QuantonSparkApplication`


## Quanton Spark Image Versions

The table below maps Quanton image tags to the underlying open-source component versions they are built on.

| Component        | `release-v1.29.0-al2023` |
| ---------------- | ------------------------- |
| [Spark](https://spark.apache.org/) [^1] | 3.5.2                     |
| Python           | 3.9, 3.11 (default), or 3.12 |
| Java JDK         | 17                        |
| Scala            | 2.12.18                   |
| [Hudi](https://hudi.apache.org/) [^2]       | 0.14.1                    |
| [Iceberg](https://iceberg.apache.org/) [^2] | 1.5.2                     |
| [Delta](https://delta.io/) [^2]             | 3.3.2                     |

[^1]: Quanton's Spark APIs are fully compatible with Apache Spark.
[^2]: We have made Onehouse-specific changes to enhance performance, but the APIs and storage formats are completely OSS compatible.

> **Note:** Quanton images are compiled for AWS Graviton (ARM with SVE2). They may not work on Apple Silicon — see the [benchmarks section](../README.md#benchmarks) for details.

### Upgrading the Quanton Spark Image

To upgrade to a newer Quanton Spark image, use `helm upgrade` with the `--set` flag:

```bash
helm upgrade quanton-operator oci://registry-1.docker.io/onehouseai/quanton-operator \
    --namespace quanton-operator \
    --reuse-values \
    --set onehouseConfig.quantonSparkImage="dist.onehouse.ai/onehouseai/quanton-spark:<new-image-tag>"
```

After upgrading, any new `QuantonSparkApplication` submissions will use the updated image. Already-running jobs are not affected.
