#!/usr/bin/env bash
# Print the application phase and the marker lines from a driver log, then say whether a
# PASS line exists. On no PASS, print the error lines from the tail of the log.
#
# Usage: scripts/agent/app-verdict.sh --name NAME --prefix PREFIX [--namespace NS] [--tail N]
#
# PREFIX is the bracketed log prefix without brackets, e.g. hudi-merge for "[hudi-merge]".
# Defaults: --namespace default, --tail 50.
# Exit codes: 0 a PASS line exists, 1 no PASS line, 64 usage error.
set -euo pipefail

name=""
prefix=""
ns="default"
tail_n=50

usage() { sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --name)         name="${2:-}";   shift 2 ;;
    --prefix)       prefix="${2:-}"; shift 2 ;;
    --namespace|-n) ns="${2:-}";     shift 2 ;;
    --tail)         tail_n="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 64 ;;
  esac
done
if [ -z "$name" ] || [ -z "$prefix" ]; then echo "--name and --prefix are required" >&2; exit 64; fi

driver="${name}-driver"
phase=$(kubectl get quantonsparkapplication "$name" -n "$ns" -o jsonpath='{.status.phase}' 2>/dev/null || true)
echo "phase: ${phase:-<none>}"

log=$(kubectl logs "$driver" -n "$ns" 2>/dev/null || true)
if [ -z "$log" ]; then
  echo "verdict: no driver log for $driver in namespace $ns"
  exit 1
fi

marker_lines=$(printf '%s\n' "$log" | grep -F -- "[${prefix}]" || true)
echo "marker lines:"
if [ -n "$marker_lines" ]; then printf '%s\n' "$marker_lines" | sed 's/^/  /'; else echo "  <none>"; fi

if printf '%s\n' "$marker_lines" | grep -qE -- "^\[${prefix}\] PASS"; then
  echo "verdict: PASS"
  exit 0
fi

echo "verdict: no PASS line found"
errors=$(printf '%s\n' "$log" | tail -n "$tail_n" | grep -E -- "Exception|Caused by|Error|FAIL" || true)
echo "error lines from the last $tail_n log lines:"
if [ -n "$errors" ]; then printf '%s\n' "$errors" | sed 's/^/  /'; else echo "  <none>"; fi
exit 1
