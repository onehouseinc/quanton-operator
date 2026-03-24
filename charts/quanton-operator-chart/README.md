# Quanton Operator Helm Chart

Helm chart for deploying the Quanton Operator on Kubernetes.

## Prerequisites

- Kubernetes >= 1.28
- Helm >= 3.x
- [Spark Operator](https://github.com/kubeflow/spark-operator) (v1.x or v2.x) installed on the cluster
- An `onehouse-values.yaml` file from your [Onehouse account](https://www.onehouse.ai)

## Installation

### 1. Create Job Namespaces

The chart does not create job namespaces automatically. Create them before installing:

```bash
kubectl create namespace <your-job-namespace>
```

> **Note:** Do not reuse namespaces managed by other Helm releases (e.g., `spark-operator`) to avoid RBAC conflicts. Use dedicated namespaces such as `data-jobs`, `analytics`, or `spark-jobs`.

### 2. Install the Chart

```bash
helm upgrade --install quanton-operator oci://registry-1.docker.io/onehouseai/quanton-operator \
    --version 1.0.0 \
    --namespace quanton-operator \
    --create-namespace \
    -f onehouse-values.yaml
```

### 3. Verify the Installation

```bash
kubectl get pods -n quanton-operator
kubectl get secret onehouse-token -n quanton-operator
```

## Upgrading

```bash
helm upgrade quanton-operator oci://registry-1.docker.io/onehouseai/quanton-operator \
    --version <new-version> \
    --namespace quanton-operator \
    -f onehouse-values.yaml
```

## Uninstalling

```bash
helm uninstall quanton-operator --namespace quanton-operator
```

> **Note:** Uninstalling the chart does not remove the `QuantonSparkApplication` CRD or any existing `QuantonSparkApplication` resources. To remove the CRD:
> ```bash
> kubectl delete crd quantonsparkapplications.quantonsparkoperator.onehouse.ai
> ```

## Configuration

See the full [Configuration Reference](../../docs/configurations.md) for all available parameters.

Key parameters:

| Parameter | Description | Default |
|---|---|---|
| `onehouseConfig.projectId` | Onehouse project ID | `""` |
| `onehouseConfig.linkId` | Cluster link ID | `""` |
| `onehouseConfig.authToken` | JWT authentication token | `""` |
| `quantonOperator.jobNamespaces` | Namespaces where Spark jobs run | `["default"]` |
| `quantonOperator.replicas` | Number of operator replicas | `1` |
| `quantonOperator.image` | Operator container image | `onehouseai/quanton-controller:1.0.0` |

## Chart Components

The chart deploys the following resources:

| Resource | Description |
|---|---|
| Deployment (`quanton-controller`) | Operator pod with sidecars |
| ServiceAccount (`quanton-operator`) | Identity for the operator with scoped RBAC |
| ClusterRole / ClusterRoleBinding | Permissions for managing CRDs, pods, secrets, and leases |
| Service (`quanton-operator`) | ClusterIP service exposing metrics (`:8080`) and health (`:8081`) endpoints |
| CRD (`QuantonSparkApplication`) | Custom resource definition for submitting Quanton Spark jobs |
| Secrets | JWT token, mTLS certificates, and Docker registry credentials |

## Health Checks

The operator exposes health endpoints:

- **Liveness**: `GET :8081/healthz`
- **Readiness**: `GET :8081/readyz`

## Source Code

The chart source is located at [`charts/quanton-operator-chart/`](.) in the repository.
