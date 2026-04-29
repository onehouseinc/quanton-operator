# v2.0.0 Documentation Updates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update all documentation, values.yaml default, and skills to reflect the Quanton Operator v2.0.0 release — covering breaking changes, enhanced security, AI agent support, and v1 deprecation.

**Architecture:** Coordinated file-by-file updates with a cross-reference audit. Each task modifies one file (or a small related group), and a final task validates consistency across all docs.

**Tech Stack:** Markdown docs, Helm values.yaml, Claude skills (Markdown)

---

### Task 1: Change enableAIAgent default to false in values.yaml

**Files:**
- Modify: `charts/quanton-operator-chart/values.yaml:14-15`

- [ ] **Step 1: Change enableAIAgent default from true to false**

In `charts/quanton-operator-chart/values.yaml`, change line 15:

```yaml
  # Enable AI agent plugin for Spark applications
  enableAIAgent: false
```

- [ ] **Step 2: Verify the change**

Run: `grep -n "enableAIAgent" charts/quanton-operator-chart/values.yaml`

Expected output:
```
15:  enableAIAgent: false
```

- [ ] **Step 3: Commit**

```bash
git add charts/quanton-operator-chart/values.yaml
git commit -m "Change enableAIAgent default to false in values.yaml"
```

---

### Task 2: Update versioning.md with v2.0.0 release notes and v1 deprecation

**Files:**
- Modify: `docs/versioning.md:1-18`

- [ ] **Step 1: Add v2.0.0 section and deprecate v1.0.0**

Replace the content of `docs/versioning.md` from lines 1-18 (everything before the Spark Image Versions table) with:

```markdown
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
```

Lines 20 onwards (Quanton Spark Image Versions table) remain unchanged.

- [ ] **Step 2: Verify the file renders correctly**

Run: `head -50 docs/versioning.md`

Confirm v2.0.0 is at the top, v1.0.0 has the deprecation warning, and the Spark Image Versions table is intact below.

- [ ] **Step 3: Commit**

```bash
git add docs/versioning.md
git commit -m "Add v2.0.0 release notes and mark v1.0.0 as deprecated"
```

---

### Task 3: Update configurations.md

**Files:**
- Modify: `docs/configurations.md`

- [ ] **Step 1: Update the Onehouse Configuration section**

Replace lines 13-27 (the Onehouse Configuration section table) with:

```markdown
These values are typically pre-populated in the `onehouse-values.yaml` provided by Onehouse. Do not modify them unless instructed by Onehouse support.

> **Note:** Connection parameters (`projectId`, `linkId`, `endpoint`, `metricsEndpoint`, `authToken`) are no longer required in v2.0.0. These are now automatically derived from the mTLS certificate.

| Parameter | Description | Default |
|---|---|---|
| `onehouseConfig.mtls.clientCert` | Client certificate in PEM format for mTLS | `""` |
| `onehouseConfig.mtls.clientKey` | Client private key in PEM format for mTLS | `""` |
| `onehouseConfig.imagePullSecrets.accessToken` | Docker registry access token for pulling Onehouse images | `""` |
| `onehouseConfig.quantonSparkImage` | Quanton Spark runtime image | `dist.onehouse.ai/onehouseai/quanton-spark:release-v1.29.0-al2023` |
| `onehouseConfig.enableAIAgent` | Enable AI agent plugin for Spark applications | `false` |
```

- [ ] **Step 2: Update operator image version**

In the Operator Configuration table (line 35), change:

```markdown
| `quantonOperator.image` | Operator container image | `onehouseai/quanton-controller:2.0.0` |
```

- [ ] **Step 3: Update the Full Example section**

Replace lines 119-155 (the Full Example YAML) with:

```markdown
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
  quantonSparkImage: "dist.onehouse.ai/onehouseai/quanton-spark:release-v1.29.0-al2023"
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
```

- [ ] **Step 4: Verify the file**

Run: `grep -n "projectId\|linkId\|endpoint\|metricsEndpoint\|authToken" docs/configurations.md`

Expected: no output (all removed references should be gone, except the note explaining they're derived from mTLS).

Run: `grep -n "enableAIAgent\|2.0.0" docs/configurations.md`

Expected: both `enableAIAgent` and `2.0.0` should appear.

- [ ] **Step 5: Commit**

```bash
git add docs/configurations.md
git commit -m "Update configurations.md for v2.0.0: remove deprecated params, add enableAIAgent"
```

---

### Task 4: Update security.md

**Files:**
- Modify: `docs/security.md`

- [ ] **Step 1: Replace the entire security.md content**

Replace the full content of `docs/security.md` with:

```markdown
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
```

- [ ] **Step 2: Verify no dp-proxy or jwt-token-secret references remain**

Run: `grep -in "dp-proxy\|jwt-token-secret\|jwt-token-protection" docs/security.md`

Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add docs/security.md
git commit -m "Update security.md for v2.0.0: mTLS-first auth, updated RBAC, remove dp-proxy"
```

---

### Task 5: Add AI Agent section to data-collection.md

**Files:**
- Modify: `docs/data-collection.md:9`

- [ ] **Step 1: Add AI Agent section before the Scarf section**

Insert a new item 4 (AI Agent) before the current item 4 (Scarf), renumbering Scarf to 5. Replace lines 1-9 with:

```markdown
# Data Collection

1. The operator sends your entire QuantonSparkApplication yaml to the Onehouse control plane for ease of use and debugging. However, it makes sure that sensitive parameters are masked before sending. Checkout `Spark Parameter Masking` in [configurations](/docs/configurations.md) for more information and controls.

2. The operator collects operational metrics to monitor operator health. Metrics are collected using [OpenTelemetry](https://opentelemetry.io/) and forwarded to the Onehouse control plane. More information about this metrics is available [here](/docs/metrics.md)

3. The operator also collects resource usage metrics to know track how much CPU is used to run drivers/executors spawned by QuantonSparkApplications. More information about this metrics is available [here](/docs/metrics.md)

4. AI Agent: No usage data is collected for the Spark AI agent. The AI agent runs entirely within your Kubernetes cluster. For more information about the AI agent, visit [Quanton AI Agent documentation](https://quanton.dev/docs/agent-ai/).

5. Scarf: The operator uses Scarf to collect anonymous usage data (pixel and package tracking) to better understand how users use the system, the website, and the docs and where to focus improvements next. Scarf fully supports the GDPR. The privacy policy of Scarf is available at https://about.scarf.sh/privacy-policy.
```

- [ ] **Step 2: Verify the file**

Run: `grep -n "AI Agent" docs/data-collection.md`

Expected: line with "AI Agent: No usage data is collected"

- [ ] **Step 3: Commit**

```bash
git add docs/data-collection.md
git commit -m "Add AI Agent data collection section: no usage data collected"
```

---

### Task 6: Update getting-started.md

**Files:**
- Modify: `docs/getting-started.md`

- [ ] **Step 1: Add Spark Operator version prerequisite and migration note**

After line 3 ("This guide walks through..."), insert a migration notice:

```markdown
> **Upgrading from v1.x?** This guide covers Quanton Operator v2.0.0. If upgrading from v1.x, see the [release notes](versioning.md) for breaking changes.
```

- [ ] **Step 2: Add Spark Operator version requirement to prerequisites**

In the Prerequisites section (line 6-14), add after the Helm line:

```markdown
- [Spark Operator](https://github.com/kubeflow/spark-operator) 2.x.x or later
```

- [ ] **Step 3: Verify the file**

Run: `grep -n "2.x.x\|Upgrading from v1" docs/getting-started.md`

Expected: both the version requirement and migration note appear.

- [ ] **Step 4: Commit**

```bash
git add docs/getting-started.md
git commit -m "Add Spark Operator 2.x.x prerequisite and v1 migration note to getting-started"
```

---

### Task 7: Update airflow.md prerequisite

**Files:**
- Modify: `docs/airflow.md:12`

- [ ] **Step 1: Update Quanton Operator version requirement**

Change line 12 from:

```markdown
- Quanton Operator >= 1.0.0 installed on the target Kubernetes cluster
```

to:

```markdown
- Quanton Operator >= 2.0.0 installed on the target Kubernetes cluster
```

- [ ] **Step 2: Verify**

Run: `grep -n "Quanton Operator >=" docs/airflow.md`

Expected: `12:- Quanton Operator >= 2.0.0 installed on the target Kubernetes cluster`

- [ ] **Step 3: Commit**

```bash
git add docs/airflow.md
git commit -m "Update Airflow provider prerequisite to Quanton Operator >= 2.0.0"
```

---

### Task 8: Pin Spark Operator to 2.5.0 in Claude skills

**Files:**
- Modify: `.claude/skills/setup-and-run-example/SKILL.md:46-51`
- Modify: `.claude/skills/run-tpcds-benchmark/SKILL.md:87`

- [ ] **Step 1: Update setup-and-run-example skill**

In `.claude/skills/setup-and-run-example/SKILL.md`, replace the Spark Operator install block (lines 46-51):

```markdown
  ```
  helm repo add spark-operator https://kubeflow.github.io/spark-operator
  helm repo update
  helm install spark-operator spark-operator/spark-operator \
    --namespace spark-operator \
    --create-namespace \
    --version 2.5.0 \
    --set "spark.jobNamespaces={default}"
  ```
```

Also update the summary section (line 101). Change:

```
  Spark Operator:   Installed (vX.Y.Z)
```

to:

```
  Spark Operator:   Installed (v2.5.0)
```

And change line 103:

```
  Quanton Operator: Installed (v1.0.0)
```

to:

```
  Quanton Operator: Installed (v2.0.0)
```

- [ ] **Step 2: Update run-tpcds-benchmark skill**

In `.claude/skills/run-tpcds-benchmark/SKILL.md`, the benchmark skill references the setup-and-run-example skill on line 87:

```markdown
- If either is missing, tell the user what's missing and that they should run `/setup-and-run-example` first, then stop.
```

This is fine — it delegates to the setup skill which now pins to 2.5.0. No changes needed to the benchmark skill.

- [ ] **Step 3: Verify**

Run: `grep -n "version 2.5.0\|v2.0.0\|v2.5.0" .claude/skills/setup-and-run-example/SKILL.md`

Expected: version 2.5.0 in the helm install command, v2.5.0 and v2.0.0 in the summary.

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/setup-and-run-example/SKILL.md
git commit -m "Pin Spark Operator to 2.5.0 and update versions in setup skill"
```

---

### Task 9: Cross-reference audit and final verification

**Files:**
- Read: all modified docs files for consistency check

- [ ] **Step 1: Check configurations.md references in other docs**

Run: `grep -rn "configurations.md\|configurations\.md" docs/`

Verify all cross-references still point to valid sections.

- [ ] **Step 2: Check security.md references in other docs**

Run: `grep -rn "security.md\|security\.md" docs/`

Verify all cross-references still point to valid sections.

- [ ] **Step 3: Check for any remaining stale references**

Run: `grep -rn "dp-proxy\|jwt-token-secret\|authToken\|projectId.*linkId\|1\.0\.0" docs/`

Expected: `1.0.0` should only appear in the deprecated v1.0.0 section of versioning.md. No dp-proxy, jwt-token-secret, or authToken references should remain outside of versioning.md's historical notes.

- [ ] **Step 4: Verify enableAIAgent consistency**

Run: `grep -rn "enableAIAgent\|AI [Aa]gent" docs/ charts/quanton-operator-chart/values.yaml`

Verify default is `false` in values.yaml and documented as `false` in configurations.md.

- [ ] **Step 5: Fix any issues found**

If any stale references or inconsistencies are found in Steps 1-4, fix them and commit:

```bash
git add -A docs/
git commit -m "Fix cross-reference inconsistencies found during audit"
```

If no issues found, skip this step.
