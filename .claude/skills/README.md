# Skills in this repository

Each directory under `.claude/skills/` holds one skill: a `SKILL.md` file, and optionally
`scripts/` and `references/`. The format follows the open Agent Skills layout (YAML
frontmatter with `name` and `description`, then Markdown instructions), so any runtime that
loads skills can use them. Nothing in a skill body depends on one vendor's tool names.

## Conventions every skill follows

**Runtime-neutral actions.** A skill says *Run*, *Read*, *Ask*, and *Wait*. It never names a
vendor tool. The "How to read this skill" table at the top of each skill maps those four
actions onto whatever tools the runtime has. `allowed-tools` stays in the frontmatter because
the open spec allows it and runtimes that do not know it ignore it.

**A fact table, verified before use.** Every path, resource name, and expected log line the
skill relies on sits in one table. The procedure verifies each fact with a command before it
acts on it. If a command disagrees with the table, the command wins and the user hears about
it.

**Ground rules against invented results.** Each skill carries the same eight rules: quote
command output, one check per claim, bounded waits, ask before destructive or costly actions,
never print secrets, separate environment problems from engine problems, never guess names or
numbers. They are repeated in each file on purpose. Skills load one at a time, so a shared
file would not be in context.

**Bounded waits, never polling in conversation turns.** Long-running work is either wrapped in
a shell loop with a deadline, or started in the background with its output in a log file that
the skill reads back. A wait loop's bound must sit below the runtime's command timeout.

**Deterministic scripts for fragile steps.** Manifest patching and result comparison live in
`scripts/` and are called, not retyped. A model transcribing forty lines of Python is a
hallucination risk; a model running `python3 scripts/x.py --flag` is not.

**Cluster context is checked first.** Every skill that touches Kubernetes prints the current
context and either requires `minikube` or asks the user to confirm the cluster by name.

## Adding or changing a skill

1. Copy the "How to read this skill" and "Ground rules" sections from an existing skill.
2. Put every path and name into the fact table, then verify each one against the repository
   with `ls`, `grep`, or `kubectl explain`.
3. For each step, write the command, the output that means success, and what to do on every
   other outcome.
4. Keep tone instructions to one line. Do not ask the model to be enthusiastic; that pushes it
   to describe results before the command that proves them has run.
5. Keep `SKILL.md` under about 400 lines. Move long reference material to `references/` and
   say when to read it.
