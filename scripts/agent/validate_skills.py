#!/usr/bin/env python3
"""Validate the skills under .agents/skills against the repository conventions.

Checks, per skill:
  - frontmatter parses; name and description present and within the Agent Skills limits
  - name matches the directory name
  - SKILL.md is at most 500 lines
  - the body contains no vendor tool names, wrong pod suffixes, unbounded log follows,
    .claude/skills paths, or the sections that moved to AGENTS.md
  - every scripts/ or references/ path the body mentions exists
  - .claude/skills/<name> is a symlink that resolves to the skill directory

Checks, repository-wide:
  - .claude/skills holds nothing except those symlinks
  - AGENTS.md exists and CLAUDE.md imports it

Usage: python3 scripts/agent/validate_skills.py [--root <repo>]
Exit codes: 0 all checks pass, 1 at least one problem, 2 usage or setup error.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    print("PyYAML is required: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_LINES = 500
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MAX_COMPATIBILITY = 500

FORBIDDEN_IN_BODY: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"AskUserQuestion"), "vendor tool name in the body; say Ask"),
    (re.compile(r"\bthe (Read|Bash|Write|Edit) tool\b"), "vendor tool name in the body; say Run or Read"),
    (re.compile(r"-spark-app-driver"), "the operator names the driver pod <name>-driver"),
    (re.compile(r"kubectl logs -f\b"), "unbounded log follow"),
    (re.compile(r"\.claude/skills/"), "paths must use .agents/skills/"),
    (re.compile(r"hallucinat", re.IGNORECASE), "say 'invented' or 'unverified', not hallucination"),
    (re.compile(r"^## Ground rules", re.MULTILINE), "the ground rules live in AGENTS.md"),
    (re.compile(r"^## How to read this skill", re.MULTILINE), "the action table lives in AGENTS.md"),
]

PATH_RE = re.compile(r"`((?:\.agents/skills/[\w.-]+/)?(?:scripts|references)/[\w./-]+)`")


class Problem(Exception):
    pass


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise Problem("frontmatter must start on line 1 with ---")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise Problem("frontmatter has no closing ---")
    raw = text[4:end]
    body = text[end + 5 :]
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as e:
        raise Problem(f"frontmatter is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise Problem("frontmatter must be a YAML mapping")
    return data, body


def check_skill(skill_dir: pathlib.Path, root: pathlib.Path) -> list[str]:
    problems: list[str] = []
    skill_md = skill_dir / "SKILL.md"
    rel = skill_md.relative_to(root)
    if not skill_md.is_file():
        return [f"{rel}: missing"]

    text = skill_md.read_text(encoding="utf-8")
    line_count = text.count("\n") + (0 if text.endswith("\n") else 1)
    if line_count > MAX_LINES:
        problems.append(f"{rel}: {line_count} lines, limit is {MAX_LINES}")

    try:
        fm, body = parse_frontmatter(text)
    except Problem as e:
        return problems + [f"{rel}: {e}"]

    name = fm.get("name")
    if not isinstance(name, str) or not name:
        problems.append(f"{rel}: frontmatter needs a name")
    else:
        if name != skill_dir.name:
            problems.append(f"{rel}: name '{name}' does not match directory '{skill_dir.name}'")
        if len(name) > MAX_NAME or not NAME_RE.match(name):
            problems.append(f"{rel}: name '{name}' breaks the Agent Skills naming rule")

    desc = fm.get("description")
    if not isinstance(desc, str) or not desc.strip():
        problems.append(f"{rel}: frontmatter needs a description")
    elif len(desc) > MAX_DESCRIPTION:
        problems.append(f"{rel}: description is {len(desc)} characters, limit is {MAX_DESCRIPTION}")

    compat = fm.get("compatibility")
    if compat is not None and (not isinstance(compat, str) or len(compat) > MAX_COMPATIBILITY):
        problems.append(f"{rel}: compatibility must be a string of at most {MAX_COMPATIBILITY} characters")

    for pattern, why in FORBIDDEN_IN_BODY:
        m = pattern.search(body)
        if m:
            line_no = body[: m.start()].count("\n") + text[: len(text) - len(body)].count("\n") + 1
            problems.append(f"{rel}:{line_no}: '{m.group(0)}': {why}")

    for ref in sorted(set(PATH_RE.findall(body))):
        candidates = [skill_dir / ref, root / ref]
        if not any(c.exists() for c in candidates):
            problems.append(f"{rel}: referenced path '{ref}' does not exist in the skill or the repository")

    link = root / ".claude" / "skills" / skill_dir.name
    if not link.is_symlink():
        problems.append(f".claude/skills/{skill_dir.name}: missing symlink to ../../.agents/skills/{skill_dir.name}")
    elif link.resolve() != skill_dir.resolve():
        problems.append(f".claude/skills/{skill_dir.name}: symlink resolves to {link.resolve()}, not {skill_dir}")

    return problems


def check_repo(root: pathlib.Path) -> list[str]:
    problems: list[str] = []
    skills_root = root / ".agents" / "skills"
    if not skills_root.is_dir():
        return [f"{skills_root.relative_to(root)}: directory missing"]

    skill_dirs = sorted(p for p in skills_root.iterdir() if p.is_dir())
    if not skill_dirs:
        problems.append(f"{skills_root.relative_to(root)}: no skills found")
    for skill_dir in skill_dirs:
        problems.extend(check_skill(skill_dir, root))

    claude_root = root / ".claude" / "skills"
    if claude_root.is_dir():
        known = {p.name for p in skill_dirs}
        for entry in sorted(claude_root.iterdir()):
            if entry.name not in known:
                problems.append(f".claude/skills/{entry.name}: not a symlink to a skill in .agents/skills; remove it")

    if not (root / "AGENTS.md").is_file():
        problems.append("AGENTS.md: missing; the shared working rules live there")
    claude_md = root / "CLAUDE.md"
    if not claude_md.is_file():
        problems.append("CLAUDE.md: missing")
    elif "@AGENTS.md" not in claude_md.read_text(encoding="utf-8"):
        problems.append("CLAUDE.md: must import AGENTS.md with a line containing @AGENTS.md")

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--root",
        type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parents[2],
        help="repository root (default: two levels above this script)",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not root.is_dir():
        print(f"root is not a directory: {root}", file=sys.stderr)
        return 2

    problems = check_repo(root)
    if problems:
        for p in problems:
            print(f"FAIL {p}")
        print(f"{len(problems)} problem(s)")
        return 1
    count = len([p for p in (root / ".agents" / "skills").iterdir() if p.is_dir()])
    print(f"OK {count} skill(s) validated under {root / '.agents' / 'skills'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
