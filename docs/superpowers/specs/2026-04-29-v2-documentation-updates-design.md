# Quanton Operator v2.0.0 Documentation Updates

## Overview

Quanton Operator v2.0.0 introduces breaking changes (Spark Operator 2.x.x requirement), enhanced security (direct mTLS, simplified JWT), and AI agent support. The documentation must be updated to reflect these changes, ensure cross-referencing consistency, and clearly communicate the v1 deprecation to the developer community.

## Approach

Coordinated file-by-file updates with a cross-reference audit to ensure consistency across all docs.

## Changes

### 1. values.yaml

- Change `enableAIAgent` default from `true` to `false`

### 2. versioning.md

- Add **v2.0.0** section at the top with:
  - Breaking change: requires Spark Operator 2.x.x or later
  - Key features: direct mTLS (dp-proxy removed), simplified JWT (managed via mTLS flow), AI agent support, simplified OTel config, conditional cluster-wide secret permissions
  - Removed: dp-proxy, standalone JWT secret, manual `projectId`/`linkId`/`endpoint`/`metricsEndpoint`/`authToken` (now derived from mTLS cert)
  - Updated component version: operator image `2.0.0`
- Mark **v1.0.0** as deprecated with warning not to use it

### 3. configurations.md

- Remove `projectId`, `linkId`, `endpoint`, `metricsEndpoint`, `authToken` from configuration table
- Add `enableAIAgent` setting (boolean, default `false`)
- Update operator image version from `1.0.0` to `2.0.0`
- Add note that connection parameters are now derived from the mTLS certificate

### 4. security.md

- Remove dp-proxy references
- Update JWT section: JWT is now handled through the mTLS flow, not via a separate Kubernetes secret
- Remove `jwt-token-secret` and JWT-specific RBAC references
- Update RBAC table:
  - Add `secrets` permissions for all-namespaces mode
  - Add `namespaces` get permission
  - Remove dp-proxy RBAC entries
- Maintain defense-in-depth narrative

### 5. data-collection.md

- Add **AI Agent** section:
  - No usage data is collected for the Spark AI agent
  - Link to https://quanton.dev/docs/agent-ai/ for more information

### 6. getting-started.md

- Add prerequisite: Spark Operator 2.x.x or later
- Remove dp-proxy references from installation/validation flow
- Add migration note for v1 users pointing to versioning.md

### 7. Cross-Reference Audit

- **configurations.md** <-> **security.md**: ensure RBAC and auth descriptions match
- **configurations.md** <-> **data-collection.md**: parameter masking references still accurate
- **getting-started.md** <-> **configurations.md**: install commands and config references consistent
- **airflow.md**: update prerequisite from `>= 1.0.0` to `>= 2.0.0`
- **Scripts/skills**: pin Spark Operator to `2.5.0` in install commands

## Out of Scope

- No changes to memory-configurations.md, pyspark.md, hudi-smoke-test.md, or metrics.md (unaffected by v2 changes)
- No doc restructuring — incremental updates only
- No changes to benchmark scripts or examples (separate effort if needed)
