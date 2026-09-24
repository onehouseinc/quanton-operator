#!/usr/bin/env bash
# Poll one SparkApplication or QuantonSparkApplication until it reaches a terminal state
# or the deadline passes. Prints one status line per interval, so an agent can quote it.
#
# Usage: scripts/agent/wait-for-app.sh --kind sparkapplication|quantonsparkapplication \
#            --name NAME [--namespace NS] [--max-seconds N] [--interval S] \
#            [--progress-regex REGEX] [--log-prefix PREFIX]
#
# Defaults: --namespace default, --max-seconds 480, --interval 20.
# --progress-regex counts matching driver-log lines and prints progress=<n> last=<line>.
# --log-prefix prints the last driver-log line that starts with [PREFIX].
# Exit codes: 0 terminal state reached, 2 deadline passed, 64 usage error.
set -euo pipefail

kind=""
name=""
ns="default"
max_seconds=480
interval=20
progress_regex=""
log_prefix=""

usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --kind)           kind="${2:-}";           shift 2 ;;
    --name)           name="${2:-}";           shift 2 ;;
    --namespace|-n)   ns="${2:-}";             shift 2 ;;
    --max-seconds)    max_seconds="${2:-}";    shift 2 ;;
    --interval)       interval="${2:-}";       shift 2 ;;
    --progress-regex) progress_regex="${2:-}"; shift 2 ;;
    --log-prefix)     log_prefix="${2:-}";     shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 64 ;;
  esac
done

kind=$(printf '%s' "$kind" | tr '[:upper:]' '[:lower:]')
case "$kind" in
  sparkapplication)        jsonpath='{.status.applicationState.state}' ;;
  quantonsparkapplication) jsonpath='{.status.phase}' ;;
  *) echo "--kind must be sparkapplication or quantonsparkapplication" >&2; exit 64 ;;
esac
if [ -z "$name" ]; then echo "--name is required" >&2; exit 64; fi

driver="${name}-driver"
deadline=$((SECONDS + max_seconds))
state=""

is_terminal() {
  case "$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')" in
    COMPLETED|FAILED|SUBMISSION_FAILED) return 0 ;;
    *) return 1 ;;
  esac
}

while :; do
  state=$(kubectl get "$kind" "$name" -n "$ns" -o jsonpath="$jsonpath" 2>/dev/null || true)
  pod=$(kubectl get pod "$driver" -n "$ns" --no-headers 2>/dev/null | awk '{print $3}' || true)
  line="$(date +%T) kind=$kind name=$name phase=${state:-<none>} driver=${pod:-<none>}"

  if [ -n "$progress_regex" ]; then
    done_n=$(kubectl logs "$driver" -n "$ns" 2>/dev/null | grep -cE -- "$progress_regex" || true)
    last=$(kubectl logs "$driver" -n "$ns" --tail=1 2>/dev/null || true)
    line="$line progress=${done_n:-0} last=${last}"
  fi
  if [ -n "$log_prefix" ]; then
    marker=$(kubectl logs "$driver" -n "$ns" --tail=200 2>/dev/null | grep -F -- "[${log_prefix}]" | tail -n 1 || true)
    line="$line marker=${marker:-<none>}"
  fi
  echo "$line"

  if is_terminal "$state"; then
    echo "result: terminal phase=$state"
    exit 0
  fi
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "result: deadline of ${max_seconds}s passed, last phase=${state:-<none>}. Run again to keep waiting."
    exit 2
  fi
  sleep "$interval"
done
