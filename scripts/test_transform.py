#!/usr/bin/env python3
"""Tests for transform.py."""

import os
import pytest
import yaml

from transform import ValidationError, transform, validate

TEST_DIR = os.path.join(os.path.dirname(__file__), "test")


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)


def valid_doc():
    return {
        "apiVersion": "sparkoperator.k8s.io/v1beta2",
        "kind": "SparkApplication",
        "metadata": {"name": "test"},
        "spec": {"type": "Java", "mode": "cluster"},
    }


class TestTransformSuccess:
    def test_apiversion_and_kind(self):
        inp = load_yaml(os.path.join(TEST_DIR, "input.yaml"))
        exp = load_yaml(os.path.join(TEST_DIR, "output.yaml"))
        got = transform(inp)

        assert got["apiVersion"] == exp["apiVersion"]
        assert got["kind"] == exp["kind"]

    def test_metadata_preserved(self):
        inp = load_yaml(os.path.join(TEST_DIR, "input.yaml"))
        exp = load_yaml(os.path.join(TEST_DIR, "output.yaml"))
        got = transform(inp)

        assert got["metadata"]["name"] == exp["metadata"]["name"]
        assert got["metadata"]["namespace"] == exp["metadata"]["namespace"]

    def test_spec_wrapped(self):
        inp = load_yaml(os.path.join(TEST_DIR, "input.yaml"))
        exp = load_yaml(os.path.join(TEST_DIR, "output.yaml"))
        got = transform(inp)

        got_spec = got["spec"]["sparkApplicationSpec"]
        exp_spec = exp["spec"]["sparkApplicationSpec"]

        for field in ("type", "mode", "image", "imagePullPolicy", "mainClass", "sparkVersion"):
            assert got_spec[field] == exp_spec[field], f"mismatch on {field}"

    def test_driver_config(self):
        inp = load_yaml(os.path.join(TEST_DIR, "input.yaml"))
        exp = load_yaml(os.path.join(TEST_DIR, "output.yaml"))
        got = transform(inp)

        got_drv = got["spec"]["sparkApplicationSpec"]["driver"]
        exp_drv = exp["spec"]["sparkApplicationSpec"]["driver"]
        assert got_drv["cores"] == exp_drv["cores"]
        assert got_drv["memory"] == exp_drv["memory"]

    def test_executor_config(self):
        inp = load_yaml(os.path.join(TEST_DIR, "input.yaml"))
        exp = load_yaml(os.path.join(TEST_DIR, "output.yaml"))
        got = transform(inp)

        got_exec = got["spec"]["sparkApplicationSpec"]["executor"]
        exp_exec = exp["spec"]["sparkApplicationSpec"]["executor"]
        assert got_exec["instances"] == exp_exec["instances"]


class TestValidation:
    def test_wrong_api_version(self):
        doc = valid_doc()
        doc["apiVersion"] = "wrong/v1"
        with pytest.raises(ValidationError, match="apiVersion"):
            validate(doc)

    def test_wrong_kind(self):
        doc = valid_doc()
        doc["kind"] = "WrongKind"
        with pytest.raises(ValidationError, match="kind"):
            validate(doc)

    def test_missing_spec(self):
        doc = valid_doc()
        del doc["spec"]
        with pytest.raises(ValidationError, match="spec"):
            validate(doc)

    def test_invalid_type(self):
        doc = valid_doc()
        doc["spec"]["type"] = "Ruby"
        with pytest.raises(ValidationError, match="spec.type"):
            validate(doc)

    def test_invalid_mode(self):
        doc = valid_doc()
        doc["spec"]["mode"] = "local"
        with pytest.raises(ValidationError, match="spec.mode"):
            validate(doc)

    def test_missing_metadata_name(self):
        doc = valid_doc()
        doc["metadata"] = {}
        with pytest.raises(ValidationError, match="metadata.name"):
            validate(doc)
