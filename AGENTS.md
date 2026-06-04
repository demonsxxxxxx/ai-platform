# AI Platform Agent Rules

## Scope

This file applies to the current `ai-platform` repository root.

## Local Execution Constraints

- This Windows workstation currently does not provide a local `docker` command. If `docker` is not recognized, do not repeatedly retry local `docker compose` checks.
- For local readiness, prefer repository-native checks such as:
  - `python -m compileall -q app tools scripts`
  - `python -m pytest tests/test_runtime_launch_script.py -q --basetemp .pytest-tmp`
  - `python -m pytest -q --basetemp .pytest-tmp`
- Run `docker compose` validation, image builds, container restarts, and runtime smoke checks only on a Docker-capable environment, normally the 211 deployment host.
- On the 211 host, invoke repository Python checks with `python3`; bare `python` is Python 2.7 there and will misreport modern type annotations as syntax errors.
- On the 211 host, verifier scripts that need Docker must use `--docker-cmd "sudo -n docker"` because the login user cannot access `/var/run/docker.sock` directly.
- For 211 sandbox verifier cancel probes, prefer an already-local image such as `ai-platform:local` via `--cancel-image ai-platform:local`; do not depend on pulling `busybox` from Docker Hub during smoke checks.
- When local pytest needs temporary files, use a workspace-local temp directory instead of the default Windows temp path if the default path has permission errors.
- If pytest fails because a stale child under `.pytest-tmp/` is unreadable or cannot be removed, pass a fresh non-existing child path under `.pytest-tmp/`, such as `--basetemp .pytest-tmp\run-verify-211-<timestamp>`, and report the reason.
- Always pass `--basetemp .pytest-tmp` to every local pytest invocation; never rely on the
  system default temp path. Example:
    python -m pytest -q --basetemp .pytest-tmp
  The `.pytest-tmp/` directory is workspace-local, git-ignored, and owned entirely by pytest.
  Do not create top-level ad-hoc `--basetemp` variants (e.g. `.pytest-tmp-run-verify-211`);
  consolidate all temporary test artifacts under `.pytest-tmp/`.
## Deploy Config Handling

- Keep `deploy/ai-platform/.env.example` as the committed non-secret template.
- Do not copy, export, commit, or quote a real `deploy/ai-platform/.env` file.
- If deployment variables are needed, read them only from the target runtime environment and report redacted evidence.

## Source Of Truth

- Use the current repository root as the local `ai-platform` source.
- Use `docs/superpowers/specs/2026-05-29-ai-platform-final-product-prd.md`, `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`, `docs/agent-rules/ai-platform-guardrails.md`, current code, and fresh 211 runtime evidence for ai-platform decisions.
- Treat `/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform` as the target 211 backend source path.
- Treat `/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform` as the target 211 repo-local deploy composition path after sync. If live container labels still point to `/home/xinlin.jiang/ai-platform-phaseb/deploy/ai-platform`, report that as stale runtime evidence that must be reconciled before claiming G0 Source Authority closure.
- Treat `http://10.56.0.211:18001/` as the current 211 frontend entry.
- Treat `ai-platform-api` and `ai-platform-worker` as the target backend/worker containers.
- Do not treat short-term execution notes, old local paths, or historical service layouts as product requirements.

## Multi-Agent Delegation

- Prefer explicit per-agent `model` and `reasoning_effort` settings whenever the available delegation tool supports those fields.
- If the available delegation tool does not expose per-agent `model` or `reasoning_effort` fields, but its tool contract says sub-agents inherit the current model, inherited configuration may be treated as equivalent only when the main session has been explicitly confirmed to be running the required model and reasoning level.
- Before using inherited configuration for a required review gate, report:
  - the delegation tool does not expose per-agent model or reasoning fields;
  - the tool contract says sub-agents inherit the current model;
  - the current main session model and reasoning level confirmation being relied on;
  - sub-agents cannot self-attest their model or reasoning level from inside their task context.
- If the main session model or reasoning level cannot be confirmed, do not claim that a model-specific or reasoning-specific multi-agent review gate is complete. In that case, sub-agents may still be used for read-only exploration or bounded sidecar checks, but the limitation must be reported.
- Do not delegate write, deployment, remote runtime, or long-running operational tasks unless the delegation path is confirmed to inherit the same filesystem, network, approval, and permission posture as the main session. Keep those tasks in the main session when inheritance cannot be proven.

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
2. **Full local test suite** – `python -m pytest -q --basetemp .pytest-tmp` exits 0.
3. **Self-review checklist** (confirm each item explicitly):
   - [ ] No secrets, real `.env` values, or personal paths in staged files.
   - [ ] New public functions/classes have docstrings.
   - [ ] Test coverage exists for the new happy path and at least one error path.
   - [ ] `CHANGELOG.md` or the relevant roadmap doc updated if the feature closes a milestone.
4. **Diff summary** – Output a one-paragraph plain-English summary of what changed and why.

Only after all four steps pass does the agent proceed to `git add` + `git commit`.

### Commit convention
