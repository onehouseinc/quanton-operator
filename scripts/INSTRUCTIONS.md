# SparkApplication to QuantonSparkApplication Transform

CLI utilities (Go and Python) that convert a Kubeflow `SparkApplication` CRD YAML into a `QuantonSparkApplication` CRD YAML.

## What It Does

| Field | Input (SparkApplication) | Output (QuantonSparkApplication) |
|-------|--------------------------|----------------------------------|
| `apiVersion` | `sparkoperator.k8s.io/v1beta2` | `onehouse.ai/v1beta2` |
| `kind` | `SparkApplication` | `QuantonSparkApplication` |
| `metadata` | Preserved as-is | Preserved as-is |
| `spec` | Direct Spark config | Moved under `spec.sparkApplicationSpec` |

## Input Validation

Both implementations validate the input YAML before transformation:

- `apiVersion` must be `sparkoperator.k8s.io/v1beta2`
- `kind` must be `SparkApplication`
- `metadata.name` must be present
- `spec` must exist and be non-empty
- `spec.type` must be one of: `Java`, `Scala`, `Python`, `R`
- `spec.mode` must be one of: `cluster`, `client`

## Python

### Prerequisites

- Python 3.9+
- PyYAML (`pip install pyyaml`)
- pytest (for tests only: `pip install pytest`)

### Run

```bash
cd scripts

# Output to stdout
python transform.py -input test/input.yaml

# Output to a file
python transform.py -input test/input.yaml -output result.yaml
```

### Tests

```bash
cd scripts
pytest test_transform.py -v
```

## Test Fixtures

- `test/input.yaml` — Sample SparkApplication input
- `test/output.yaml` — Expected QuantonSparkApplication output
