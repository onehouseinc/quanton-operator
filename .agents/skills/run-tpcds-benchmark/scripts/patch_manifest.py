#!/usr/bin/env python3
"""Write a patched copy of a TPC-DS benchmark manifest for one run.

The checked-in manifests under benchmarks/k8s/ are never modified. This script reads one,
applies the changes the user asked for, writes the result somewhere else, and prints one line
per change so the caller can show the user exactly what differs.

Usage examples:

  patch_manifest.py --in benchmarks/k8s/datagen-job.yaml --out /tmp/datagen.yaml \
      --scale-factor 10 --executor-instances 4 [--force-datagen]

  patch_manifest.py --in benchmarks/k8s/oss-spark-tpcds.yaml --out /tmp/oss.yaml \
      --scale-factor 10 --executor-instances 1 --executor-cores 2 --executor-memory 6144m

  patch_manifest.py --in benchmarks/k8s/quanton-tpcds-parquet.yaml --out /tmp/quanton.yaml \
      --scale-factor 10 --agent --await-termination --await-timeout 1h

Exit status is non-zero when a requested change cannot be applied, so a silent no-op is
impossible. Requires PyYAML.
"""
import argparse
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is required. Install it with: python3 -m pip install pyyaml")

AGENT_PLUGIN = "ai.quanton.spark.agent.SparkAgentPlugin"


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--in", dest="src", required=True, help="checked-in manifest to read")
    p.add_argument("--out", dest="dst", required=True, help="where to write the patched copy")
    p.add_argument("--scale-factor", type=int, required=True, help="TPC-DS scale factor, e.g. 1 or 10")
    p.add_argument("--executor-instances", type=int)
    p.add_argument("--executor-cores", type=int)
    p.add_argument("--executor-memory", help='e.g. "6144m"')
    p.add_argument("--force-datagen", action="store_true", help="datagen manifest only: regenerate existing tables")
    p.add_argument("--agent", action="store_true", help="QuantonSparkApplication only: enable the in-driver Spark Agent")
    p.add_argument("--await-termination", action="store_true", help="with --agent: keep the Spark UI alive after the job ends")
    p.add_argument("--await-timeout", help='with --await-termination: e.g. "1h" (default is the agent\'s 30m)')
    return p.parse_args()


def main():
    a = parse_args()
    with open(a.src) as f:
        doc = yaml.safe_load(f)

    kind = doc.get("kind")
    if kind == "QuantonSparkApplication":
        spec = doc["spec"]["sparkApplicationSpec"]
    elif kind == "SparkApplication":
        spec = doc["spec"]
    else:
        sys.exit(f"unsupported kind {kind!r} in {a.src}")

    changes = []
    base = f"/data/tpcds/sf_{a.scale_factor}"
    args = spec.get("arguments")
    if not isinstance(args, list):
        sys.exit(f"{a.src}: spec has no 'arguments' list; manifest shape changed")

    if "--scale-factor" in args:
        # Data generation manifest.
        i = args.index("--scale-factor") + 1
        changes.append(f"arguments --scale-factor: {args[i]} -> {a.scale_factor}")
        args[i] = str(a.scale_factor)
        if "--data-dir" not in args:
            sys.exit("datagen manifest has no --data-dir argument; manifest shape changed")
        j = args.index("--data-dir") + 1
        changes.append(f"arguments --data-dir: {args[j]} -> {base}")
        args[j] = base
        if a.force_datagen and "--force-datagen" not in args:
            args.append("--force-datagen")
            changes.append("arguments: appended --force-datagen")
    else:
        # Query manifests: rewrite every /data/tpcds/... path argument. The volume mountPath
        # stays /data/tpcds, exactly as benchmarks/run.sh does it.
        patched = 0
        for i, v in enumerate(args):
            if isinstance(v, str) and v.startswith("/data/tpcds/"):
                new = base + v[len("/data/tpcds"):]
                changes.append(f"arguments[{i}]: {v} -> {new}")
                args[i] = new
                patched += 1
        if patched == 0:
            sys.exit("no /data/tpcds/... argument found to patch; manifest shape changed")
        if a.force_datagen:
            sys.exit("--force-datagen only applies to the datagen manifest")

    executor = spec.setdefault("executor", {})
    for key, val in (
        ("instances", a.executor_instances),
        ("cores", a.executor_cores),
        ("memory", a.executor_memory),
    ):
        if val is not None:
            changes.append(f"executor.{key}: {executor.get(key)} -> {val}")
            executor[key] = val

    if a.agent:
        if kind != "QuantonSparkApplication":
            sys.exit("--agent only applies to a QuantonSparkApplication manifest")
        conf = spec.setdefault("sparkConf", {})
        plugins = str(conf.get("spark.plugins", "")).strip()
        if AGENT_PLUGIN not in plugins.split(","):
            plugins = f"{plugins},{AGENT_PLUGIN}" if plugins else AGENT_PLUGIN
        changes.append(f"sparkConf spark.plugins: {conf.get('spark.plugins')} -> {plugins}")
        conf["spark.plugins"] = plugins
        conf["spark.quanton.agent.enabled"] = "true"
        changes.append("sparkConf spark.quanton.agent.enabled: true")
        if a.await_termination:
            conf["spark.quanton.agent.await.termination"] = "true"
            changes.append("sparkConf spark.quanton.agent.await.termination: true")
            if a.await_timeout:
                conf["spark.quanton.agent.await.termination.timeout"] = a.await_timeout
                changes.append(f"sparkConf spark.quanton.agent.await.termination.timeout: {a.await_timeout}")
    elif a.await_termination or a.await_timeout:
        sys.exit("--await-termination and --await-timeout require --agent")

    with open(a.dst, "w") as f:
        yaml.safe_dump(doc, f, sort_keys=False, default_flow_style=False)

    print(f"wrote {a.dst} from {a.src} ({kind} {doc.get('metadata', {}).get('name')})")
    for c in changes:
        print(f"  {c}")


if __name__ == "__main__":
    main()
