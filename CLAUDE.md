# CLAUDE.md

## Project Overview

Quanton Operator is a Kubernetes operator (Helm chart) that extends the Kubeflow Spark Operator to run Apache Spark jobs on the Onehouse Quanton compute engine. It introduces the `QuantonSparkApplication` CRD (`apiVersion: quantonsparkoperator.onehouse.ai/v1beta2`) which wraps a standard `SparkApplication` spec under `spec.sparkApplicationSpec`.

## Repository Structure

- `charts/quanton-operator-chart/` — Helm chart (Chart v2.0.0, App v1.0.0)
  - `crds/` — QuantonSparkApplication CRD definition
  - `templates/` — Deployment, RBAC, services, mTLS, JWT, OTel, monitoring
  - `values.yaml` — Default configuration
- `docs/` — Getting started, configurations, security, metrics, Airflow integration
- `examples/` — Sample SparkApplication and QuantonSparkApplication YAMLs
- `scripts/` — Python transform tool (SparkApplication → QuantonSparkApplication)
- `benchmarks/` — TPC-DS benchmark suite (OSS Spark vs Quanton)
- `.github/workflows/` — Helm chart release to Docker Hub OCI registry

## Commands

### Tests
```bash
cd scripts && pytest test_transform.py -v
```

### Transform Tool
```bash
python scripts/transform.py -input <sparkapplication.yaml> -output <output.yaml>
```

### Helm Install
```bash
helm upgrade --install quanton-operator oci://registry-1.docker.io/onehouseai/quanton-operator \
    --namespace quanton-operator --create-namespace -f onehouse-values.yaml
```

### TPC-DS Benchmark
```bash
./benchmarks/run.sh                    # default SF=1
./benchmarks/run.sh --scale-factor 10  # custom scale factor
```

## Code Conventions

- **Shell scripts**: `set -euo pipefail`, helper functions, phase-based execution
- **Python**: `#!/usr/bin/env python3`, type hints, argparse, custom exceptions, pytest for tests
- **YAML/Helm**: `{{ }}` templating, values-driven configuration, no hardcoded values
- **CRD**: `QuantonSparkApplication` (short name: `qsa`), namespaced scope

## Key Patterns

- QuantonSparkApplication nests SparkApplication spec under `spec.sparkApplicationSpec`
- Benchmark jobs use SparkApplication/QuantonSparkApplication CRDs submitted via `kubectl apply`
- PVC (`ReadWriteOnce`) shared across sequential benchmark phases
- Iceberg JARs are pre-packaged in the Quanton image at `/opt/spark/user-jars/`
- CI releases Helm chart to Docker Hub OCI registry via GitHub Actions

## Important Notes

- `onehouse-values.yaml` contains credentials — gitignored, never commit
- Quanton's native engine is compiled for Graviton (SVE2) — does NOT work on Apple Silicon (SIGILL). TPC-DS reads work on arm64 because they don't trigger the native SQL engine
- The `onehouse-spark-catalog.jar` in the Quanton image bundles old fabric8 classes — use narrow classpath (`iceberg-spark-runtime.jar:iceberg-aws-bundle.jar`) to avoid conflicts
