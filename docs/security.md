# Security

The Quanton Operator is designed with defense-in-depth principles. This document describes the key aspects of security for the operator.

## Authentication

### mTLS

All communication between the operator and the Onehouse control plane is secured using mutual TLS (mTLS). The operator uses a client certificate and key provided via the `onehouseConfig.mtls` Helm values.

In v2.0.0, mTLS is the primary authentication mechanism. Connection parameters (project ID, endpoint, etc.) are automatically derived from the mTLS certificate, simplifying configuration.

### JWT

JWT authentication is still used for control plane communication but is now managed internally through the mTLS flow. In v1.0.0, a separate JWT token had to be provided as a Kubernetes secret. In v2.0.0, the operator handles JWT token lifecycle automatically — no separate secret or configuration is required.

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
| `namespaces` | core | get | Read namespace metadata |
| `pods` | core | get, list, watch | Monitor Spark driver and executor pods |
| `events` | core | create, patch | Emit Kubernetes events |
| `leases` | `coordination.k8s.io` | create, delete, get, list, patch, update, watch | Leader election for high availability |
| `deployments` | `apps` | get, list, watch | Cache sync for controller-runtime informer |

### Namespace-Scoped Permissions

| Resource | API Group | Verbs | Purpose | Scope |
|---|---|---|---|---|
| `deployments` | `apps` | get, list, patch, update, watch | Restart operator on certificate refresh | quanton-operator namespace only |

### Secret Access

Secret permissions vary depending on the deployment mode:

**Namespace-restricted mode** (recommended): When `jobNamespaces` lists specific namespaces, the operator has:
- Cluster-scoped: create, list, watch on secrets (required by Kubernetes API), plus get, update, patch scoped to operator-managed secrets (`quanton-operator-docker-secret`, `quanton-operator-mtls-secret`, `quanton-operator-cert`)
- Per-namespace: delete, get, patch, update on secrets in each listed job namespace (for per-job token lifecycle and secret syncing)

**All-namespaces mode**: When `jobNamespaces` is empty, the operator has cluster-wide secret access (create, delete, get, list, patch, update, watch) to provision credentials in any namespace.

## Network Security

All external communication is encrypted via mTLS. No inbound network access is required — the operator only makes outbound connections to the Onehouse control plane.

## Recommendations

- Use **namespace-restricted mode** (`quantonOperator.jobNamespaces` with explicit namespaces) to limit the operator's scope.
- Avoid reusing namespaces managed by other operators to prevent RBAC conflicts.
- Store `onehouse-values.yaml` securely — it contains credentials for the Onehouse control plane and Docker registry.

<img referrerpolicy="no-referrer-when-downgrade" src="https://static.scarf.sh/a.png?x-pxid=9d354525-edac-41b0-bca6-a37ae2d24852" />
