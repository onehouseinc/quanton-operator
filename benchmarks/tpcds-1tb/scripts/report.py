#!/usr/bin/env python3
"""Turn the collected benchmark JSON into comparison tables.

Reads every ``*.json`` the orchestrator scraped out of the driver logs and prints an ASCII
report, then writes the same content as Markdown next to it. Runs locally with the standard
library only, so it needs neither Spark nor object-storage credentials.

Expected file names, all optional:
    datagen.json
    load-<format>.json
    query-<engine>-<format>.json
    merge-<engine>-<format>.json

Usage
-----
    report.py --results-dir benchmarks/tpcds-1tb/results/<run-id>
"""

import argparse
import glob
import json
import math
import os
import re

FORMATS = ["parquet", "hudi", "iceberg"]
BASELINE = "oss"
CANDIDATE = "quanton"


def load_json(path):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        print("  skipping %s: %s" % (os.path.basename(path), exc))
        return None


def collect(results_dir):
    data = {"datagen": None, "load": {}, "query": {}, "merge": {}}
    for path in sorted(glob.glob(os.path.join(results_dir, "*.json"))):
        name = os.path.basename(path)[:-5]
        payload = load_json(path)
        if payload is None:
            continue
        if name == "datagen":
            data["datagen"] = payload
        elif name.startswith("load-"):
            data["load"][name[len("load-"):]] = payload
        else:
            match = re.match(r"^(query|merge)-([^-]+)-(.+)$", name)
            if match:
                kind, engine, fmt = match.groups()
                data[kind][(engine, fmt)] = payload
    return data


def natural_key(name):
    match = re.match(r"^q(\d+)([a-z]*)$", name)
    return (int(match.group(1)), match.group(2)) if match else (10**6, name)


def format_datagen(datagen, lines):
    if not datagen:
        return
    lines.append("## Data generation")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append("| Scale factor | %s |" % datagen.get("scale_factor"))
    lines.append("| Tables | %d |" % len(datagen.get("tables", [])))
    lines.append("| Total rows | %s |" % f"{datagen.get('total_rows', 0):,}")
    lines.append("| Wall clock | %.1f min |" % (datagen.get("total_seconds", 0) / 60.0))
    lines.append("")


def format_load(load, lines):
    if not load:
        return
    lines.append("## Table load")
    lines.append("")
    lines.append("| Format | Tables | Rows | Load time |")
    lines.append("|---|---|---|---|")
    for fmt in FORMATS:
        payload = load.get(fmt)
        if not payload:
            continue
        lines.append(
            "| %s | %d | %s | %.1f min |"
            % (fmt, len(payload.get("tables", [])), f"{payload.get('total_rows', 0):,}",
               payload.get("total_seconds", 0) / 60.0)
        )
    lines.append("")


def query_times(payload):
    return {
        r["query"]: r["time_seconds"]
        for r in payload.get("results", [])
        if r.get("status") == "success"
    }


def format_query(query, lines):
    if not query:
        return
    lines.append("## TPC-DS query performance")
    lines.append("")

    for fmt in FORMATS:
        base = query.get((BASELINE, fmt))
        cand = query.get((CANDIDATE, fmt))
        if not base and not cand:
            continue

        lines.append("### %s" % fmt)
        lines.append("")
        if not (base and cand):
            only = base or cand
            engine = BASELINE if base else CANDIDATE
            lines.append("Only the %s run is present, so there is no comparison." % engine)
            lines.append("")
            lines.append("| Metric | Value |")
            lines.append("|---|---|")
            lines.append("| Queries succeeded | %d |" % only.get("successful", 0))
            lines.append("| Queries failed | %d |" % only.get("failed", 0))
            lines.append("| Sum of best times | %.1f s |" % only.get("sum_best_seconds", 0))
            lines.append("")
            continue

        base_times = query_times(base)
        cand_times = query_times(cand)
        common = sorted(set(base_times) & set(cand_times), key=natural_key)
        speedups = {q: base_times[q] / cand_times[q] for q in common if cand_times[q] > 0}

        base_total = sum(base_times[q] for q in common)
        cand_total = sum(cand_times[q] for q in common)
        wins = sum(1 for s in speedups.values() if s >= 1.0)
        ordered = sorted(speedups.items(), key=lambda kv: kv[1], reverse=True)

        lines.append("| Metric | Value |")
        lines.append("|---|---|")
        lines.append("| Queries compared | %d |" % len(common))
        lines.append("| OSS Spark total | %.1f s |" % base_total)
        lines.append("| Quanton total | %.1f s |" % cand_total)
        if cand_total > 0:
            lines.append("| Total speedup | %.2fx |" % (base_total / cand_total))
        if speedups:
            geo = math.exp(sum(math.log(s) for s in speedups.values()) / len(speedups))
            lines.append("| Geometric mean speedup | %.2fx |" % geo)
            lines.append("| Queries where Quanton wins | %d / %d |" % (wins, len(speedups)))
            lines.append("| Best query | %s at %.2fx |" % (ordered[0][0], ordered[0][1]))
            lines.append("| Worst query | %s at %.2fx |" % (ordered[-1][0], ordered[-1][1]))
        lines.append("| OSS failures | %d |" % base.get("failed", 0))
        lines.append("| Quanton failures | %d |" % cand.get("failed", 0))
        lines.append("")

        lines.append("| Query | OSS Spark (s) | Quanton (s) | Speedup |")
        lines.append("|---|---|---|---|")
        for q in common:
            speed = speedups.get(q)
            lines.append(
                "| %s | %.2f | %.2f | %s |"
                % (q, base_times[q], cand_times[q], "%.2fx" % speed if speed else "n/a")
            )
        lines.append("")


def format_merge(merge, lines):
    if not merge:
        return
    lines.append("## Lake-loader merge performance")
    lines.append("")

    for fmt in ["hudi", "iceberg"]:
        base = merge.get((BASELINE, fmt))
        cand = merge.get((CANDIDATE, fmt))
        if not base and not cand:
            continue

        sample = base or cand
        lines.append("### %s, table %s" % (fmt, sample.get("base_table", sample.get("table"))))
        lines.append("")
        lines.append("| Round | OSS Spark (s) | Quanton (s) | Speedup | Rows merged | Valid |")
        lines.append("|---|---|---|---|---|---|")

        rounds = sorted(
            {r["round"] for payload in (base, cand) if payload for r in payload.get("results", [])}
        )
        for index in rounds:
            base_round = next((r for r in base.get("results", [])
                               if r["round"] == index), None) if base else None
            cand_round = next((r for r in cand.get("results", [])
                               if r["round"] == index), None) if cand else None
            base_secs = base_round["merge_seconds"] if base_round else None
            cand_secs = cand_round["merge_seconds"] if cand_round else None
            speed = (base_secs / cand_secs) if (base_secs and cand_secs) else None
            reference = base_round or cand_round
            merged = (reference["update_rows"] + reference["insert_rows"]) if reference else 0
            statuses = [r["status"] for r in (base_round, cand_round) if r]
            lines.append(
                "| %d | %s | %s | %s | %s | %s |"
                % (
                    index,
                    "%.1f" % base_secs if base_secs is not None else "n/a",
                    "%.1f" % cand_secs if cand_secs is not None else "n/a",
                    "%.2fx" % speed if speed else "n/a",
                    f"{merged:,}",
                    "yes" if statuses and all(s == "success" for s in statuses) else "NO",
                )
            )
        lines.append("")

        if base and cand and base.get("mean_merge_seconds") and cand.get("mean_merge_seconds"):
            lines.append(
                "Mean merge time: OSS Spark %.1f s, Quanton %.1f s, speedup %.2fx."
                % (
                    base["mean_merge_seconds"],
                    cand["mean_merge_seconds"],
                    base["mean_merge_seconds"] / cand["mean_merge_seconds"],
                )
            )
            lines.append("")


def main():
    parser = argparse.ArgumentParser(description="Report on a TPC-DS 1 TB benchmark run")
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--output", default="", help="Markdown output path (default: summary.md)")
    args = parser.parse_args()

    data = collect(args.results_dir)
    lines = ["# TPC-DS benchmark report", "", "Results directory: `%s`" % args.results_dir, ""]
    format_datagen(data["datagen"], lines)
    format_load(data["load"], lines)
    format_query(data["query"], lines)
    format_merge(data["merge"], lines)

    if len(lines) <= 4:
        lines.append("No result files were found. Check that the phases completed.")
        lines.append("")

    report = "\n".join(lines)
    print(report)

    output = args.output or os.path.join(args.results_dir, "summary.md")
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    with open(output, "w") as handle:
        handle.write(report + "\n")
    print("\nWrote %s" % output)


if __name__ == "__main__":
    main()
