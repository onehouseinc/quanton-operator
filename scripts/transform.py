#!/usr/bin/env python3
"""Convert a SparkApplication CRD YAML to a QuantonSparkApplication CRD YAML.

Usage:
    python transform.py -input <sparkapplication.yaml> [-output <output.yaml>]
"""

import argparse
import sys
from collections import OrderedDict

import yaml

INPUT_API_VERSION = "sparkoperator.k8s.io/v1beta2"
INPUT_KIND = "SparkApplication"
OUTPUT_API_VERSION = "onehouse.ai/v1beta2"
OUTPUT_KIND = "QuantonSparkApplication"

VALID_TYPES = {"Java", "Scala", "Python", "R"}
VALID_MODES = {"cluster", "client"}


class ValidationError(Exception):
    pass


def validate(doc: dict) -> None:
    """Validate that doc is a proper SparkApplication CRD."""
    api_version = doc.get("apiVersion", "")
    if api_version != INPUT_API_VERSION:
        raise ValidationError(
            f'invalid apiVersion "{api_version}": expected "{INPUT_API_VERSION}"'
        )

    kind = doc.get("kind", "")
    if kind != INPUT_KIND:
        raise ValidationError(f'invalid kind "{kind}": expected "{INPUT_KIND}"')

    metadata = doc.get("metadata")
    if not isinstance(metadata, dict):
        raise ValidationError("missing 'metadata'")
    if not metadata.get("name"):
        raise ValidationError("missing 'metadata.name'")

    spec = doc.get("spec")
    if not isinstance(spec, dict) or not spec:
        raise ValidationError("missing or empty 'spec'")

    app_type = spec.get("type", "")
    if app_type not in VALID_TYPES:
        raise ValidationError(
            f'invalid spec.type "{app_type}": must be one of {", ".join(sorted(VALID_TYPES))}'
        )

    mode = spec.get("mode", "")
    if mode not in VALID_MODES:
        raise ValidationError(
            f'invalid spec.mode "{mode}": must be one of {", ".join(sorted(VALID_MODES))}'
        )


def transform(doc: dict) -> OrderedDict:
    """Convert a SparkApplication dict to a QuantonSparkApplication dict."""
    validate(doc)

    out = OrderedDict()
    out["apiVersion"] = OUTPUT_API_VERSION
    out["kind"] = OUTPUT_KIND
    if "metadata" in doc:
        out["metadata"] = doc["metadata"]
    out["spec"] = {"sparkApplicationSpec": doc["spec"]}
    return out


# --- YAML representers to preserve key order and style ---


def _represent_ordereddict(dumper, data):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())


def _represent_str(dumper, data):
    # Use quoted style for strings that look like numbers or contain special chars.
    if data and (
        data.replace(".", "", 1).isdigit()
        or ":" in data
        or "?" in data
        or "&" in data
        or "=" in data
    ):
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


class TransformDumper(yaml.SafeDumper):
    pass


TransformDumper.add_representer(OrderedDict, _represent_ordereddict)
TransformDumper.add_representer(str, _represent_str)


def main():
    parser = argparse.ArgumentParser(
        description="Convert SparkApplication CRD to QuantonSparkApplication CRD"
    )
    parser.add_argument(
        "-input", required=True, dest="input_file", help="Source SparkApplication YAML"
    )
    parser.add_argument(
        "-output",
        dest="output_file",
        default=None,
        help="Output file (default: stdout)",
    )
    args = parser.parse_args()

    try:
        with open(args.input_file) as f:
            doc = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"Error: file not found: {args.input_file}", file=sys.stderr)
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"Error parsing YAML: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        result = transform(doc)
    except ValidationError as e:
        print(f"Error: validation failed: {e}", file=sys.stderr)
        sys.exit(1)

    output = yaml.dump(
        result, Dumper=TransformDumper, default_flow_style=False, sort_keys=False
    )

    if args.output_file:
        with open(args.output_file, "w") as f:
            f.write(output)
        print(f"Output written to {args.output_file}", file=sys.stderr)
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
