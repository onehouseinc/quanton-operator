#!/usr/bin/env python3
"""Tests for the helper scripts in scripts/agent/.

kubectl and helm are replaced by small shell fakes on PATH. Each fake reads canned output
from the directory in $FAKE_STATE, so a test controls what the cluster "says".
"""
from __future__ import annotations

import json
import os
import pathlib
import stat
import subprocess
import textwrap

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
AGENT = REPO / "scripts" / "agent"

FAKE_KUBECTL = r"""#!/usr/bin/env bash
state="$FAKE_STATE"
case "$1 $2" in
  "config current-context") cat "$state/context" ;;
  "get nodes") cat "$state/nodes" 2>/dev/null ;;
  "get pod") echo "$3   1/1   $(cat "$state/pod_status" 2>/dev/null || echo Running)   0   1m" ;;
  "get quantonsparkapplication"|"get sparkapplication")
    f="$state/phases"
    if [ -s "$f" ]; then
      head -n1 "$f"; tail -n +2 "$f" > "$f.tmp"; mv "$f.tmp" "$f"
    else
      cat "$state/final_phase" 2>/dev/null
    fi ;;
  "logs "*) cat "$state/log" 2>/dev/null ;;
  *) echo "fake kubectl: unhandled: $*" >&2; exit 1 ;;
esac
"""

FAKE_HELM = r"""#!/usr/bin/env bash
state="$FAKE_STATE"
case "$1" in
  list) cat "$state/helm.json" ;;
  *) echo "fake helm: unhandled: $*" >&2; exit 1 ;;
esac
"""

BOTH_CHARTS = [
    {"name": "spark-operator", "namespace": "spark-operator", "chart": "spark-operator-2.5.0", "status": "deployed"},
    {"name": "quanton-operator", "namespace": "quanton-operator", "chart": "quanton-operator-2.0.0", "status": "deployed"},
]


@pytest.fixture()
def fake(tmp_path: pathlib.Path) -> dict:
    bin_dir = tmp_path / "bin"
    state = tmp_path / "state"
    bin_dir.mkdir()
    state.mkdir()
    for name, body in (("kubectl", FAKE_KUBECTL), ("helm", FAKE_HELM)):
        f = bin_dir / name
        f.write_text(body)
        f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (state / "context").write_text("minikube\n")
    (state / "nodes").write_text("minikube   Ready   control-plane   1d   v1.33.1\n")
    (state / "helm.json").write_text(json.dumps(BOTH_CHARTS))
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", FAKE_STATE=str(state))
    return {"env": env, "state": state}


def run(script: str, *args: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(AGENT / script), *args], env=env, capture_output=True, text=True, timeout=60
    )


# --- check-cluster.sh -------------------------------------------------------------------

def test_check_cluster_ok(fake):
    r = run("check-cluster.sh", "--require-context", "minikube",
            "--require-charts", "spark-operator,quanton-operator", env=fake["env"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "context: minikube" in r.stdout
    assert "chart spark-operator: present" in r.stdout
    assert "chart quanton-operator: present" in r.stdout
    assert r.stdout.rstrip().endswith("result: OK")


def test_check_cluster_wrong_context_fails(fake):
    (fake["state"] / "context").write_text("prod-eks\n")
    r = run("check-cluster.sh", "--require-context", "minikube", env=fake["env"])
    assert r.returncode == 1
    assert "context check: FAIL (required minikube, active prod-eks)" in r.stdout
    assert "result: FAIL" in r.stdout


def test_check_cluster_missing_chart_fails(fake):
    (fake["state"] / "helm.json").write_text(json.dumps(BOTH_CHARTS[:1]))
    r = run("check-cluster.sh", "--require-charts", "spark-operator,quanton-operator", env=fake["env"])
    assert r.returncode == 1
    assert "chart spark-operator: present" in r.stdout
    assert "chart quanton-operator: MISSING" in r.stdout


def test_check_cluster_spark_chart_not_satisfied_by_quanton_chart(fake):
    (fake["state"] / "helm.json").write_text(json.dumps(BOTH_CHARTS[1:]))
    r = run("check-cluster.sh", "--require-charts", "spark-operator", env=fake["env"])
    assert r.returncode == 1
    assert "chart spark-operator: MISSING" in r.stdout


def test_check_cluster_missing_tool_fails_before_cluster_calls(fake):
    r = run("check-cluster.sh", "--require-tools", "kubectl,no-such-tool-xyz", env=fake["env"])
    assert r.returncode == 1
    assert "tool no-such-tool-xyz: MISSING" in r.stdout
    assert "context:" not in r.stdout


def test_check_cluster_no_requirements_only_reports(fake):
    (fake["state"] / "context").write_text("anything\n")
    (fake["state"] / "helm.json").write_text("[]")
    r = run("check-cluster.sh", env=fake["env"])
    assert r.returncode == 0
    assert "operator releases:\n  <none>" in r.stdout


def test_check_cluster_unknown_flag_is_usage_error(fake):
    r = run("check-cluster.sh", "--bogus", env=fake["env"])
    assert r.returncode == 64


# --- wait-for-app.sh --------------------------------------------------------------------

def test_wait_reaches_terminal_phase(fake):
    (fake["state"] / "phases").write_text("Pending\nRunning\nCompleted\n")
    r = run("wait-for-app.sh", "--kind", "quantonsparkapplication", "--name", "demo",
            "--interval", "0", env=fake["env"])
    assert r.returncode == 0, r.stdout + r.stderr
    status_lines = [l for l in r.stdout.splitlines() if l.startswith(("0", "1", "2")) and " phase=" in l]
    assert len(status_lines) == 3
    assert "phase=Pending" in status_lines[0]
    assert "phase=Completed driver=Running" in status_lines[2]
    assert r.stdout.rstrip().endswith("result: terminal phase=Completed")


def test_wait_sparkapplication_terminal_states_any_case(fake):
    (fake["state"] / "final_phase").write_text("submission_failed\n")
    r = run("wait-for-app.sh", "--kind", "SparkApplication", "--name", "job",
            "--interval", "0", env=fake["env"])
    assert r.returncode == 0
    assert "result: terminal phase=submission_failed" in r.stdout


def test_wait_stops_at_deadline_with_exit_2(fake):
    (fake["state"] / "final_phase").write_text("Running\n")
    r = run("wait-for-app.sh", "--kind", "quantonsparkapplication", "--name", "demo",
            "--max-seconds", "0", "--interval", "0", env=fake["env"])
    assert r.returncode == 2
    assert "deadline of 0s passed, last phase=Running" in r.stdout


def test_wait_counts_progress_and_shows_marker(fake):
    (fake["state"] / "final_phase").write_text("Completed\n")
    (fake["state"] / "log").write_text(textwrap.dedent("""\
        Found 3 queries in /sql
        Running q1...
          q1: 1.2s (10 rows)
        Running q2...
          q2: 0.8s (5 rows)
        [demo] step 2 done
        """))
    r = run("wait-for-app.sh", "--kind", "sparkapplication", "--name", "job", "--interval", "0",
            "--progress-regex", r"^  q[0-9]+[a-z]?: [0-9.]+s \(", "--log-prefix", "demo", env=fake["env"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "progress=2" in r.stdout
    assert "marker=[demo] step 2 done" in r.stdout


def test_wait_rejects_unknown_kind(fake):
    r = run("wait-for-app.sh", "--kind", "deployment", "--name", "x", env=fake["env"])
    assert r.returncode == 64


def test_wait_requires_name(fake):
    r = run("wait-for-app.sh", "--kind", "sparkapplication", env=fake["env"])
    assert r.returncode == 64


# --- app-verdict.sh ---------------------------------------------------------------------

def test_verdict_pass(fake):
    (fake["state"] / "final_phase").write_text("Completed\n")
    (fake["state"] / "log").write_text(
        "[hudi-merge] step 1: created customers\n"
        "[hudi-merge] step 2: merged\n"
        "[hudi-merge] PASS — 10 -> 13 rows, 3 updated to 'vip'\n"
    )
    r = run("app-verdict.sh", "--name", "quanton-hudi-merge-into-demo", "--prefix", "hudi-merge", env=fake["env"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "phase: Completed" in r.stdout
    assert "  [hudi-merge] PASS — 10 -> 13 rows, 3 updated to 'vip'" in r.stdout
    assert r.stdout.rstrip().endswith("verdict: PASS")


def test_verdict_fail_quotes_error_lines(fake):
    (fake["state"] / "final_phase").write_text("Failed\n")
    (fake["state"] / "log").write_text(
        "[iceberg-merge] step 1: created customers\n"
        "java.lang.RuntimeException: boom\n"
        "Caused by: java.io.IOException: disk\n"
    )
    r = run("app-verdict.sh", "--name", "demo", "--prefix", "iceberg-merge", env=fake["env"])
    assert r.returncode == 1
    assert "verdict: no PASS line found" in r.stdout
    assert "  java.lang.RuntimeException: boom" in r.stdout
    assert "  Caused by: java.io.IOException: disk" in r.stdout


def test_verdict_pass_in_prose_does_not_count(fake):
    (fake["state"] / "log").write_text("[demo] the PASS criteria are 13 rows\n[demo] FAIL — got 10 rows\n")
    r = run("app-verdict.sh", "--name", "demo", "--prefix", "demo", env=fake["env"])
    assert r.returncode == 1
    assert "verdict: no PASS line found" in r.stdout


def test_verdict_without_log(fake):
    r = run("app-verdict.sh", "--name", "demo", "--prefix", "demo", env=fake["env"])
    assert r.returncode == 1
    assert "no driver log" in r.stdout


def test_verdict_requires_name_and_prefix(fake):
    r = run("app-verdict.sh", "--name", "demo", env=fake["env"])
    assert r.returncode == 64


# --- validate_skills.py -----------------------------------------------------------------

def test_validate_skills_passes_on_this_repository():
    r = subprocess.run(["python3", str(AGENT / "validate_skills.py")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("OK ")


def test_validate_skills_reports_problems(tmp_path: pathlib.Path):
    skill = tmp_path / ".agents" / "skills" / "good-name"
    skill.mkdir(parents=True)
    skill.joinpath("SKILL.md").write_text(textwrap.dedent("""\
        ---
        name: other-name
        description: A skill.
        ---
        Ask with AskUserQuestion, then kubectl logs -f the pod <name>-spark-app-driver.
        Run `scripts/does-not-exist.sh`.
        """))
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "stray").mkdir()
    (tmp_path / "CLAUDE.md").write_text("# no import\n")
    r = subprocess.run(["python3", str(AGENT / "validate_skills.py"), "--root", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 1
    out = r.stdout
    assert "name 'other-name' does not match directory 'good-name'" in out
    assert "AskUserQuestion" in out
    assert "kubectl logs -f" in out
    assert "-spark-app-driver" in out
    assert "scripts/does-not-exist.sh" in out
    assert ".claude/skills/good-name: missing symlink" in out
    assert ".claude/skills/stray: not a symlink" in out
    assert "AGENTS.md: missing" in out
    assert "CLAUDE.md: must import AGENTS.md" in out
