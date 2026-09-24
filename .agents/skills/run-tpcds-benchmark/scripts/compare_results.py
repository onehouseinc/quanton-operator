#!/usr/bin/env python3
"""Compare the OSS Spark and Quanton TPC-DS result files and print a plain summary.

Usage:
  compare_results.py --results-dir benchmarks/results/sf_1 [--png]

Reads oss-spark-parquet.json and quanton-parquet.json from the directory (names can be
overridden). Each file has the shape written by benchmarks/scripts/run_queries.py:

  {"total_time_seconds": float, "query_count": int, "successful": int, "failed": int,
   "results": [{"query": "q1", "status": "success|failed|skipped", "time_seconds": float, ...}]}

Speedup is oss_time / quanton_time. Only queries that succeeded on both engines enter the
comparison; every excluded query is named. The summary states which engine was faster
overall. With --png and matplotlib installed, comparison.png is written next to the inputs.
"""
import argparse
import json
import math
import os
import re
import sys


def qkey(q):
    m = re.match(r"q(\d+)([a-z]?)$", q)
    return (int(m.group(1)), m.group(2)) if m else (10**6, q)


def load(path):
    if not os.path.exists(path):
        sys.exit(f"missing result file: {path}")
    with open(path) as f:
        return json.load(f)


def by_status(doc, status):
    return {r["query"]: r["time_seconds"] for r in doc.get("results", []) if r.get("status") == status}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-dir", required=True)
    ap.add_argument("--oss", default="oss-spark-parquet.json")
    ap.add_argument("--quanton", default="quanton-parquet.json")
    ap.add_argument("--png", action="store_true", help="also write comparison.png if matplotlib is available")
    ap.add_argument("--bar-width", type=int, default=50)
    a = ap.parse_args()

    oss = load(os.path.join(a.results_dir, a.oss))
    qt = load(os.path.join(a.results_dir, a.quanton))
    oss_ok, qt_ok = by_status(oss, "success"), by_status(qt, "success")
    oss_fail = sorted(by_status(oss, "failed"), key=qkey)
    qt_fail = sorted(by_status(qt, "failed"), key=qkey)

    common = sorted(set(oss_ok) & set(qt_ok), key=qkey)
    if not common:
        sys.exit("no query succeeded on both engines; nothing to compare")
    everything = set(oss_ok) | set(qt_ok) | set(oss_fail) | set(qt_fail)
    excluded = sorted(everything - set(common), key=qkey)

    rows = []
    for q in common:
        o, t = oss_ok[q], qt_ok[q]
        sp = (o / t) if t > 0 else float("inf")
        rows.append((q, o, t, sp))

    def faster(sp):
        if not math.isfinite(sp) or sp > 1:
            return "Quanton"
        return "OSS Spark" if sp < 1 else "tie"

    print(f"TPC-DS results from {a.results_dir}")
    print(f"  OSS Spark file: {a.oss}  (reported total {oss.get('total_time_seconds')}s, "
          f"success {oss.get('successful')}, failed {oss.get('failed')})")
    print(f"  Quanton file:   {a.quanton}  (reported total {qt.get('total_time_seconds')}s, "
          f"success {qt.get('successful')}, failed {qt.get('failed')})")
    print()
    header = f"{'Query':<8}{'OSS Spark (s)':>15}{'Quanton (s)':>13}{'Speedup':>10}  Faster"
    print(header)
    print("-" * len(header))
    for q, o, t, sp in rows:
        sp_txt = f"{sp:.2f}x" if math.isfinite(sp) else "inf"
        print(f"{q:<8}{o:>15.2f}{t:>13.2f}{sp_txt:>10}  {faster(sp)}")
    print("-" * len(header))
    total_o = sum(r[1] for r in rows)
    total_t = sum(r[2] for r in rows)
    total_sp = (total_o / total_t) if total_t > 0 else float("inf")
    total_txt = f"{total_sp:.2f}x" if math.isfinite(total_sp) else "inf"
    print(f"{'TOTAL':<8}{total_o:>15.2f}{total_t:>13.2f}{total_txt:>10}  {faster(total_sp)}")

    finite = [r[3] for r in rows if math.isfinite(r[3]) and r[3] > 0]
    geo = math.exp(sum(math.log(s) for s in finite) / len(finite)) if finite else float("nan")
    q_wins = sum(1 for r in rows if r[3] > 1)
    o_wins = sum(1 for r in rows if r[3] < 1)
    best = max(rows, key=lambda r: r[3])
    worst = min(rows, key=lambda r: r[3])

    print()
    print("Summary (only queries that succeeded on both engines are compared)")
    print(f"  Queries compared:        {len(rows)}")
    print(f"  Excluded:                {len(excluded)}" + (f"  ({', '.join(excluded)})" if excluded else ""))
    print(f"  Failed on OSS Spark:     {', '.join(oss_fail) if oss_fail else 'none'}")
    print(f"  Failed on Quanton:       {', '.join(qt_fail) if qt_fail else 'none'}")
    print(f"  Quanton faster on:       {q_wins} of {len(rows)}")
    print(f"  OSS Spark faster on:     {o_wins} of {len(rows)}")
    print(f"  Total-time speedup:      {total_txt}  ({faster(total_sp)} faster overall)")
    print(f"  Geometric-mean speedup:  {geo:.2f}x")
    print(f"  Best query for Quanton:  {best[0]} at {best[3]:.2f}x")
    print(f"  Worst query for Quanton: {worst[0]} at {worst[3]:.2f}x")

    print()
    print("Speedup by query on a log scale. '>' means Quanton faster, '<' means OSS Spark faster.")
    max_log = max((abs(math.log2(s)) for s in finite), default=1.0) or 1.0
    for q, o, t, sp in sorted(rows, key=lambda r: -r[3]):
        if not math.isfinite(sp):
            bar = ">" * a.bar_width + "!"
            sp_txt = "  inf"
        else:
            n = 0 if sp == 1 else int(round(abs(math.log2(sp)) / max_log * a.bar_width))
            bar = (">" if sp > 1 else "<") * n
            sp_txt = f"{sp:5.2f}x"
        print(f"{q:<6}{sp_txt}  {bar}#")

    if a.png:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            print()
            print("PNG skipped: matplotlib is not installed. The text output above is complete.")
            return
        labels = [r[0] for r in rows]
        x = list(range(len(rows)))
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(20, 12), gridspec_kw={"height_ratios": [3, 1]})
        fig.suptitle(f"TPC-DS: OSS Spark vs Quanton ({os.path.basename(a.results_dir.rstrip('/'))})", fontsize=16)
        w = 0.4
        ax1.bar([i - w / 2 for i in x], [r[1] for r in rows], w, label="OSS Spark")
        ax1.bar([i + w / 2 for i in x], [r[2] for r in rows], w, label="Quanton")
        ax1.set_ylabel("seconds")
        ax1.set_xticks(x)
        ax1.set_xticklabels(labels, rotation=90, fontsize=6)
        ax1.legend()
        ax1.grid(axis="y", alpha=0.3)
        sps = [r[3] if math.isfinite(r[3]) else max(finite, default=1.0) for r in rows]
        ax2.bar(x, sps, color=["#2a7f62" if s >= 1 else "#b3261e" for s in sps])
        ax2.axhline(1.0, color="black", linewidth=0.6, linestyle="--")
        ax2.set_yscale("log")
        ax2.set_ylabel("speedup (log)")
        ax2.set_xticks(x)
        ax2.set_xticklabels(labels, rotation=90, fontsize=6)
        ax2.grid(axis="y", alpha=0.3)
        plt.tight_layout()
        out = os.path.join(a.results_dir, "comparison.png")
        plt.savefig(out, dpi=150, bbox_inches="tight")
        print()
        print(f"PNG written: {out}")


if __name__ == "__main__":
    main()
