#!/usr/bin/env bash
# Print the active kubectl context, a node summary, and the operator Helm releases.
# Exit non-zero when a requirement fails, so a skill can stop on it.
#
# Usage: scripts/agent/check-cluster.sh [--require-context NAME] [--require-charts a,b]
#                                       [--require-tools t1,t2]
#
# Defaults: --require-tools kubectl,helm. Chart names match as substrings of the
# installed chart, e.g. "spark-operator" matches "spark-operator-2.5.0".
# Exit codes: 0 all requirements met, 1 a requirement failed, 64 usage error.
set -euo pipefail

require_context=""
require_charts=""
require_tools="kubectl,helm"

usage() { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --require-context) require_context="${2:-}"; shift 2 ;;
    --require-charts)  require_charts="${2:-}";  shift 2 ;;
    --require-tools)   require_tools="${2:-}";   shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 64 ;;
  esac
done

fail=0

check_tools() {
  local t
  IFS=, read -ra tools <<<"$require_tools"
  for t in "${tools[@]}"; do
    [ -n "$t" ] || continue
    if command -v "$t" >/dev/null 2>&1; then
      echo "tool $t: present"
    else
      echo "tool $t: MISSING"
      fail=1
    fi
  done
}

check_context() {
  local ctx
  ctx=$(kubectl config current-context 2>/dev/null || echo "<none>")
  echo "context: $ctx"
  if [ -n "$require_context" ] && [ "$ctx" != "$require_context" ]; then
    echo "context check: FAIL (required $require_context, active $ctx)"
    fail=1
  fi
}

show_nodes() {
  echo "nodes:"
  local out
  out=$(kubectl get nodes --no-headers 2>&1 | head -5 || true)
  if [ -n "$out" ]; then printf '%s\n' "$out" | sed 's/^/  /'; else echo "  <none>"; fi
}

check_charts() {
  echo "operator releases:"
  local releases
  releases=$(helm list -A -o json 2>/dev/null | python3 -c '
import json, sys
try:
    rows = json.load(sys.stdin)
except Exception:
    rows = []
for r in rows:
    chart = r.get("chart", "")
    if "spark-operator" in chart or "quanton-operator" in chart:
        print("  " + " ".join([r.get("name", "?"), r.get("namespace", "?"), chart, r.get("status", "?")]))
' || true)
  if [ -n "$releases" ]; then printf '%s\n' "$releases"; else echo "  <none>"; fi

  [ -n "$require_charts" ] || return 0
  local c
  IFS=, read -ra charts <<<"$require_charts"
  for c in "${charts[@]}"; do
    [ -n "$c" ] || continue
    # "spark-operator" must not be satisfied by the "quanton-operator" chart and vice versa.
    if printf '%s\n' "$releases" | awk '{print $3}' | grep -q -- "$c"; then
      echo "chart $c: present"
    else
      echo "chart $c: MISSING"
      fail=1
    fi
  done
}

check_tools
if [ "$fail" -ne 0 ]; then
  echo "result: FAIL (missing tools)"
  exit 1
fi
check_context
show_nodes
check_charts

if [ "$fail" -ne 0 ]; then
  echo "result: FAIL"
  exit 1
fi
echo "result: OK"
