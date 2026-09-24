# Skills in this repository

Each directory under `.agents/skills/` holds one skill: a `SKILL.md` file, and optionally
`scripts/` and `references/`. The format is the open Agent Skills layout (YAML frontmatter with
`name` and `description`, then Markdown instructions).

Two runtimes load these skills from one copy:

| Runtime | Scans | Invoke by name |
|---|---|---|
| Codex | `.agents/skills/` from the working directory up to the repo root | `$skill-name` |
| Claude Code | `.claude/skills/`, where each entry is a symlink to `../../.agents/skills/<name>` | `/skill-name` |

Both runtimes also pick a skill from its `description` when a request matches it. Frontmatter
fields beyond the open spec (`allowed-tools`, `argument-hint`, `disable-model-invocation`) are
Claude Code extensions; Codex ignores them.

## What lives where

| Content | Location | Why |
|---|---|---|
| Action mapping (Run, Read, Ask, Wait) and the ground rules | `AGENTS.md` | Codex loads it on every turn. Claude Code loads it through `@AGENTS.md` in `CLAUDE.md`. So it is in context whenever a skill runs, and no skill repeats it. |
| Cluster check, bounded wait, verdict | `scripts/agent/` | A skill runs a tested script instead of retyping a shell loop. |
| Failure cases that appear in more than one skill | `docs/troubleshooting.md` | Humans need them too. A skill keeps only its own failures. |
| Facts, steps, report template, skill-specific failures | `SKILL.md` | The part that differs per skill. |
| Long reference material | `<skill>/references/` | Loaded only when a step says to read it. |
| Skill-specific helpers | `<skill>/scripts/` | Called by repo-relative path, e.g. `.agents/skills/run-tpcds-benchmark/scripts/patch_manifest.py`. |

## Conventions every skill follows

**A fact table, verified before use.** Every path, resource name, and expected log line the
skill relies on sits in one table. The procedure verifies each fact with a command before it
acts on it. If a command disagrees with the table, the command wins and the user hears about it.

**Bounded waits, never polling in conversation turns.** Long-running work goes through
`scripts/agent/wait-for-app.sh`, which exits at a terminal state or a deadline, or runs in the
background with its output in a log file the skill reads back. Keep the deadline below the
runtime's command timeout.

**Cluster context is checked first.** Every skill that touches Kubernetes runs
`scripts/agent/check-cluster.sh` before its first `kubectl apply` or `helm install`. The demo
skills require `minikube`. Other skills ask the user to confirm the cluster by name.

**Deterministic scripts for fragile steps.** Manifest patching and result comparison live in
`scripts/` and are called, not retyped. A model transcribing forty lines of Python is a
transcription risk; a model running `python3 scripts/x.py --flag` is not.

**One line of tone guidance at most.** Do not ask the model to be enthusiastic; that pushes it
to describe results before the command that proves them has run.

## Adding or changing a skill

1. Create `.agents/skills/<name>/SKILL.md` and the symlink
   `ln -s ../../.agents/skills/<name> .claude/skills/<name>`.
2. Start the body with the one-line pointer to `AGENTS.md`, `scripts/agent/`, and
   `docs/troubleshooting.md`. Do not paste the ground rules.
3. Put every path and name into the fact table, then verify each one against the repository
   with `ls`, `grep`, or `kubectl explain`.
4. For each step, write the command, the output that means success, and what to do on every
   other outcome.
5. Keep `SKILL.md` under 500 lines. Move long reference material to `references/` and say
   when to read it.
6. Run `python3 scripts/agent/validate_skills.py`. CI runs the same check on every pull
   request that touches a skill.
