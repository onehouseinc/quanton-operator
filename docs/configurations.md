# Configuration Reference

All configuration is managed through Helm values. When installing the chart, supply a `onehouse-values.yaml` file (provided by Onehouse) and override any additional parameters as needed.

```bash
helm upgrade --install quanton-operator oci://registry-1.docker.io/onehouseai/quanton-operator \
    --namespace quanton-operator \
    --create-namespace \
    --set "quantonOperator.jobNamespaces={default}" \
    -f onehouse-values.yaml 
```

## Onehouse Configuration
These values are typically pre-populated in the `onehouse-values.yaml` provided by Onehouse. Do not modify them unless instructed by Onehouse support.

> **Note:** Connection parameters (`projectId`, `linkId`, `endpoint`, `metricsEndpoint`, `authToken`) are no longer required in v2.0.0. These are now automatically derived from the mTLS certificate.

| Parameter | Description | Default |
|---|---|---|
| `onehouseConfig.mtls.clientCert` | Client certificate in PEM format for mTLS | `""` |
| `onehouseConfig.mtls.clientKey` | Client private key in PEM format for mTLS | `""` |
| `onehouseConfig.imagePullSecrets.accessToken` | Docker registry access token for pulling Onehouse images | `""` |
| `onehouseConfig.quantonSparkImage` | Quanton Spark runtime image | `dist.onehouse.ai/onehouseai/quanton-spark:quanton-operator-release-v0.2.0-al2023-quanton-operator` |
| `onehouseConfig.enableAIAgent` | Enable AI agent plugin for Spark applications | `false` |


## Operator Configuration

These values control the behavior and resource allocation of the Quanton Operator.

| Parameter | Description | Default |
|---|---|---|
| `quantonOperator.image` | Operator container image | `dist.onehouse.ai/onehouseai/quanton-controller:2.0.0` |
| `quantonOperator.pullPolicy` | Image pull policy | `IfNotPresent` |
| `quantonOperator.replicas` | Number of operator replicas | `1` |
| `quantonOperator.serviceAccount.name` | Service account name for the operator | `quanton-operator` |

### Resource Limits

| Parameter | Description | Default |
|---|---|---|
| `quantonOperator.resources.requests.cpu` | CPU request for operator pod | `100m` |
| `quantonOperator.resources.requests.memory` | Memory request for operator pod | `256Mi` |
| `quantonOperator.resources.limits.cpu` | CPU limit for operator pod | `500m` |
| `quantonOperator.resources.limits.memory` | Memory limit for operator pod | `512Mi` |

## Job Namespace Configuration

The `jobNamespaces` parameter controls where Spark jobs are allowed to run.

| Parameter | Description | Default |
|---|---|---|
| `quantonOperator.jobNamespaces` | List of namespaces where Spark jobs are permitted | `["default"]` |

**Namespace-restricted mode** (recommended): Specify a list of namespaces. The operator only processes `QuantonSparkApplication` resources in these namespaces and creates the required secrets and RBAC in each.

```yaml
quantonOperator:
  jobNamespaces:
    - data-jobs
    - analytics
```

> **Important:** These namespaces must exist before installing the chart. The chart does not create them automatically. Create them with:
> ```bash
> kubectl create namespace data-jobs
> kubectl create namespace analytics
> ```

**All-namespaces mode**: Set `jobNamespaces` to an empty list. The operator watches all namespaces and auto-creates secrets as needed.

```yaml
quantonOperator:
  jobNamespaces: []
```

## Spark Parameter Masking

The operator sends Spark configuration parameters to the Onehouse control plane for observability. By default, parameters whose keys contain `secret`, `password`, `token`, or `access.key` are automatically masked before being sent. Use `additionalSparkParamsToMask` to mask additional custom parameters by exact key name.

| Parameter | Description | Default |
|---|---|---|
| `quantonOperator.additionalSparkParamsToMask` | List of additional Spark conf parameter names to mask before uploading to the control plane | `[]` |

```yaml
quantonOperator:
  additionalSparkParamsToMask:
    - "spark.hadoop.fs.s3a.session.token"
    - "spark.my.custom.credential"
```

## Pod Annotations and Node Selection

These values are applied globally to all Spark driver and executor pods created by the operator.

| Parameter | Description | Default |
|---|---|---|
| `quantonOperator.annotations` | Annotations applied to Spark application pods | `{}` |
| `quantonOperator.nodeSelector` | Node selector labels for Spark driver and executor pods | `{}` |

Example:

```yaml
quantonOperator:
  annotations:
    environment: "production"
    team: "data-engineering"
  nodeSelector:
    node-type: "compute"
```

## PySpark

For running PySpark workloads and selecting a specific Python version (3.9, 3.11, or 3.12), see the [PySpark guide](pyspark.md).

## Full Example

```yaml
onehouseConfig:
  mtls:
    clientCert: |
      -----BEGIN CERTIFICATE-----
      ...
      -----END CERTIFICATE-----
    clientKey: |
      -----BEGIN PRIVATE KEY-----
      ...
      -----END PRIVATE KEY-----
  imagePullSecrets:
    accessToken: "your-docker-access-token"
  quantonSparkImage: "dist.onehouse.ai/onehouseai/quanton-spark:quanton-operator-release-v0.2.0-al2023-quanton-operator"
  enableAIAgent: false

quantonOperator:
  jobNamespaces:
    - data-jobs
  replicas: 1
  resources:
    requests:
      cpu: 100m
      memory: 256Mi
    limits:
      cpu: 500m
      memory: 512Mi
  annotations:
    team: "data-engineering"
  nodeSelector:
    workload: "batch"
```
