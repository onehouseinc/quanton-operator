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
| `onehouseConfig.quantonSparkImage` | Quanton Spark runtime image, Spark 3 line | `dist.onehouse.ai/onehouseai/quanton-spark:quanton-operator-release-v0.2.0-al2023` |
| `onehouseConfig.quantonOperatorOpenSourceGlutenImage` | OSS Gluten image, Spark 3 line | `""` |
| `onehouseConfig.quantonOperatorOpenSourceCometImage` | OSS Comet image, Spark 3 line | `""` |
| `onehouseConfig.quantonSpark4Image` | Quanton Spark runtime image, Spark 4 line | `""` |
| `onehouseConfig.quantonOperatorOpenSourceCometSpark4Image` | OSS Comet image, Spark 4 line | `""` |
| `onehouseConfig.enableAIAgent` | Enable AI agent plugin for Spark applications | `true` |
| `onehouseConfig.useJobImage` | Run each job on its own `sparkApplicationSpec.image` instead of the image the operator selects. See [Using your own image per job](#using-your-own-image-per-job). | `false` |

See [Engine and Spark Version Selection](#engine-and-spark-version-selection) for how a job picks one of these images.


## Operator Configuration

These values control the behavior and resource allocation of the Quanton Operator.

| Parameter | Description | Default |
|---|---|---|
| `quantonOperator.image` | Operator container image | `dist.onehouse.ai/onehouseai/quanton-controller:2.0.7` |
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

## Engine and Spark Version Selection

By default each job runs on an operator-controlled image, and any `image` you set in the job spec is overridden. To run each job on its own image instead, see [Using your own image per job](#using-your-own-image-per-job). The operator picks the image from two fields in the job:

- `spec.sparkApplicationSpec.sparkVersion` — its major version selects the line. Quanton ships `3.5.x` on the Spark 3 line and `4.1.x` on the Spark 4 line.
- `spark.quanton.accelerator` in `sparkConf` — `native` (the default), `gluten`, or `comet`.

| `sparkVersion` | `spark.quanton.accelerator` | Image value |
|---|---|---|
| `3.x` | `native` (default) | `onehouseConfig.quantonSparkImage` |
| `3.x` | `gluten` | `onehouseConfig.quantonOperatorOpenSourceGlutenImage` |
| `3.x` | `comet` | `onehouseConfig.quantonOperatorOpenSourceCometImage` |
| `4.x` | `native` (default) | `onehouseConfig.quantonSpark4Image` |
| `4.x` | `comet` | `onehouseConfig.quantonOperatorOpenSourceCometSpark4Image` |
| `4.x` | `gluten` | Unsupported. Gluten has no Spark 4 image. |

Declare the version Quanton ships, `3.5.x` or `4.1.x`. Another minor of the same major still runs, on that line's image, and the operator logs the mismatch. A different major, such as `2.4.8` or `5.0.0`, is rejected.

Example of a Spark 4 job that runs the OSS Comet engine:

```yaml
apiVersion: quantonsparkoperator.onehouse.ai/v1beta2
kind: QuantonSparkApplication
metadata:
  name: spark4-comet-example
spec:
  sparkApplicationSpec:
    sparkVersion: "4.1.0"
    sparkConf:
      spark.quanton.accelerator: "comet"
```

Quote the version. An unquoted `sparkVersion: 4.1` is a YAML number, and the job is rejected.

The operator never falls back to another image. A job fails at submission in these cases:

- `sparkVersion` is missing, or its major version is neither `3` nor `4`.
- The accelerator value is not `native`, `gluten`, or `comet`.
- The combination does not exist, such as `gluten` on Spark 4.1.
- The image for that combination is empty, which means Onehouse does not offer that engine in your deployment. Contact Onehouse support.

Each failure is reported on the job. Run `kubectl describe quantonsparkapplication <name>` and read the `SubmissionFailed` event, which names the field to fix:

```
sparkVersion is required in spec.sparkApplicationSpec: set it to 3.5.x for Spark 3, or 4.1.x for Spark 4
unsupported sparkVersion "5.0.0": supported versions are 3.5.x (Spark 3) and 4.1.x (Spark 4)
spark.quanton.accelerator=gluten is not supported on Spark 4.1 (supported on Spark 4.1: native, comet)
```

## Using your own image per job

By default the operator sets the image for every job, as described in [Engine and Spark Version Selection](#engine-and-spark-version-selection), and ignores `sparkApplicationSpec.image`. Set `onehouseConfig.useJobImage` to `true` to run each job on its own image instead. Use this when you build a separate image per job on top of the Quanton Spark image.

When enabled:

- Each job runs on its `sparkApplicationSpec.image`. If `driver.image` or `executor.image` is set, that pod uses it.
- `spark.quanton.accelerator` no longer selects the image. `sparkVersion` is still required and must be `3.x` or `4.x`.
- A job with an empty `image` fails at submission.
- Build every job image `FROM` the Quanton Spark image. A job on any other image fails at startup.
- Set `sparkVersion` to the Spark line of the image you build from: `3.5.x` for `quantonSparkImage`, `4.1.x` for `quantonSpark4Image`. The operator still uses it to set Spark configurations, and a mismatch can fail the driver at startup.
- The operator still sets `imagePullSecrets` to the Onehouse registry secret. Nodes must be able to pull job images without a pull secret, for example ECR with node IAM, or images in the Onehouse registry.

```bash
helm upgrade --install quanton-operator \
  oci://registry-1.docker.io/onehouseai/quanton-operator \
  --version <chart-version> \
  --namespace quanton-operator \
  --set "onehouseConfig.useJobImage=true" \
  -f onehouse-values.yaml
```

**Restart the controller after changing only this value.** The operator reads its configuration once at startup. If `useJobImage` is the only change in a `helm upgrade`, restart the controller so it takes effect:

```bash
kubectl rollout restart deploy/quanton-controller -n quanton-operator
```

An upgrade that also changes the controller image restarts it automatically.

**Keep the flag on every upgrade.** If you re-run `helm upgrade` with a fresh `onehouse-values.yaml`, for example when credentials expire, pass `--set onehouseConfig.useJobImage=true` again. Otherwise the operator goes back to setting the image itself.

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
  quantonSparkImage: "dist.onehouse.ai/onehouseai/quanton-spark:quanton-operator-release-v0.2.0-al2023"
  enableAIAgent: true

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
