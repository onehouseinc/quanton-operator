# Security

The Quanton Operator is designed with defense-in-depth principles. This document describes the key aspects of security for the operator.

## RBAC

The operator uses a dedicated `quanton-operator` ServiceAccount with a ClusterRole scoped to the minimum permissions required.

### Cluster-Scoped Permissions

| Resource | API Group | Verbs | Purpose |
|---|---|---|---|
| `quantonsparkapplications` | `quantonsparkoperator.onehouse.ai` | create, delete, get, list, patch, update, watch | Manage the QuantonSparkApplication CRD |
| `quantonsparkapplications/finalizers` | `quantonsparkoperator.onehouse.ai` | update | Manage resource finalizers |
| `quantonsparkapplications/status` | `quantonsparkoperator.onehouse.ai` | get, patch, update | Update CRD status |
| `sparkapplications` | `sparkoperator.k8s.io` | create, delete, get, list, patch, update, watch | Create and manage underlying SparkApplication resources |
| `configmaps` | core | create, delete, get, list, patch, update, watch | Store operator configuration |
| `pods` | core | get, list, watch | Monitor Spark driver and executor pods |
| `events` | core | create, patch | Emit Kubernetes events |
| `leases` | `coordination.k8s.io` | create, delete, get, list, patch, update, watch | Leader election for high availability |

### Namespace-Scoped Permissions

| Resource | API Group | Verbs | Purpose | Scope |
|---|---|---|---|---|
| `deployments` | `apps` | get, list, patch, update, watch | Manage operator deployments on certificate refresh | quanton-operator namespace only |

### Secret Access

The operator can also create, list, and watch secrets globally to provision credentials in job namespaces.
Secret access is scoped to specific named secrets that are created by and managed by operator.

## Network Security

All external communication is encrypted. No inbound network access is required.

## Recommendations

- Use **namespace-restricted mode** (`quantonOperator.jobNamespaces` with explicit namespaces) to limit the operator's scope.
- Avoid reusing namespaces managed by other operators to prevent RBAC conflicts.
- Store `onehouse-values.yaml` securely — it contains credentials for the Onehouse control plane and Docker registry.

<img referrerpolicy="no-referrer-when-downgrade" src="https://static.scarf.sh/a.png?x-pxid=9d354525-edac-41b0-bca6-a37ae2d24852" />
