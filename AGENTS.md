# AI Platform Agent Rules

## Scope

This file applies to the current `ai-platform` repository root.

## Local Execution Constraints

- This Windows workstation currently does not provide a local `docker` command. If `docker` is not recognized, do not repeatedly retry local `docker compose` checks.
- For local readiness, prefer repository-native checks such as:
  - `python -m compileall -q app tools scripts`
  - `python -m pytest tests/test_runtime_launch_script.py -q --basetemp .pytest-tmp`
  - `python -m pytest <changed-or-affected-tests> -q --basetemp .pytest-tmp`
  - relevant integration or smoke checks for the changed path
- Do not run or require full-repository pytest by default. Full pytest is
  prohibited as a routine gate because it wastes time; run it only if the user
  explicitly requests it for a specific risk decision.
- Run Docker validation, builds, restarts, and runtime smoke only on a
  Docker-capable environment. The authoritative 211 commands and recovery paths
  live in `docs/operations/211-release-operations-runbook.md`.
- When local pytest needs temporary files, use a workspace-local temp directory instead of the default Windows temp path if the default path has permission errors.
- If pytest fails because a stale child under `.pytest-tmp/` is unreadable or cannot be removed, pass a fresh non-existing child path under `.pytest-tmp/`, such as `--basetemp .pytest-tmp\run-verify-211-<timestamp>`, and report the reason.
- Always pass `--basetemp .pytest-tmp` to every local pytest invocation; never rely on the
  system default temp path. Example:
    python -m pytest tests/test_changed_path.py -q --basetemp .pytest-tmp
  The `.pytest-tmp/` directory is workspace-local, git-ignored, and owned entirely by pytest.
  Do not create top-level ad-hoc `--basetemp` variants (e.g. `.pytest-tmp-run-verify-211`);
  consolidate all temporary test artifacts under `.pytest-tmp/`.
## Source Of Truth

- Use the current repository root as the local `ai-platform` source.
- Use the current PRD, technical acceptance, roadmap,
  `docs/agent-rules/ai-platform-guardrails.md`, current code, and fresh runtime
  evidence for ai-platform decisions. The guardrails file is the single source
  for current 211 paths, services, and product/security boundaries.
- Do not treat short-term execution notes, old local paths, or historical service layouts as product requirements.

Use only issues named by the active goal and confirmed from fresh GitHub state.
Keep concrete issue numbers, owners, ordering, and current gate state in the
roadmap or Controller Current rather than this durable entry file. Product
priorities, frontend ownership, and runtime boundaries live in
`docs/agent-rules/ai-platform-guardrails.md`.

## GitHub Issue And PR Workflow

Use GitHub issues and pull requests as the default closure loop for goal-sized
work, gate closures, and newly discovered defects. Keep the detailed procedure
in `docs/agent-rules/github-issue-pr-workflow.md` instead of expanding this
entry file.

## Multi-Agent Delegation

`docs/agent-rules/multi-agent-context-workflow.md` is the single source for
task lifetimes, ownership, authority, model ceilings, disposable probes,
release readiness, repair budgets, and context checkpoints. Do not restate
those rules here.

## Large Feature Workflow
A change is treated as a **large feature** if it meets any of the following:
- Introduces a new package / sub-module (new directory with `__init__.py`)
- Adds or modifies a public API route, schema, or database migration
- Touches more than 3 existing files in a single logical change
- Introduces a new background task, worker job, or scheduled process

### Pre-commit review gate
Before committing a large feature, the agent must complete all of the following and
report results inline:

1. **Compile check** – `python -m compileall -q app tools scripts` exits 0.
2. **Changed-scope tests plus integration check** – targeted pytest for the
   changed or affected modules exits 0, and the relevant integration or smoke
   check exits 0.
3. **Self-review checklist** (confirm each item explicitly):
   - [ ] No secrets, real `.env` values, or personal paths in staged files.
   - [ ] New public functions/classes have docstrings.
   - [ ] Test coverage exists for the new happy path and at least one error path.
   - [ ] `CHANGELOG.md` or the relevant roadmap doc updated if the feature closes a milestone.
4. **Diff summary** – Output a one-paragraph plain-English summary of what changed and why.

Only after all four steps pass does the agent proceed to `git add` + `git commit`.
