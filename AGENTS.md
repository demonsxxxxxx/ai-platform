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
- Run `docker compose` validation, image builds, container restarts, and runtime smoke checks only on a Docker-capable environment, normally the 211 deployment host.
- On the 211 host, invoke repository Python checks with `python3`; bare `python` is Python 2.7 there and will misreport modern type annotations as syntax errors.
- On the 211 host, verifier scripts that need Docker must use `--docker-cmd "sudo -n docker"` because the login user cannot access `/var/run/docker.sock` directly.
- Keep operational recovery commands, runtime-only rebuild details, flat-base
  recovery, Compose environment handling, and entrypoint permissions in
  `docs/operations/211-release-operations-runbook.md`. This entry file keeps
  only the host, source, secret, and verification boundaries.
- When local pytest needs temporary files, use a workspace-local temp directory instead of the default Windows temp path if the default path has permission errors.
- If pytest fails because a stale child under `.pytest-tmp/` is unreadable or cannot be removed, pass a fresh non-existing child path under `.pytest-tmp/`, such as `--basetemp .pytest-tmp\run-verify-211-<timestamp>`, and report the reason.
- Always pass `--basetemp .pytest-tmp` to every local pytest invocation; never rely on the
  system default temp path. Example:
    python -m pytest tests/test_changed_path.py -q --basetemp .pytest-tmp
  The `.pytest-tmp/` directory is workspace-local, git-ignored, and owned entirely by pytest.
  Do not create top-level ad-hoc `--basetemp` variants (e.g. `.pytest-tmp-run-verify-211`);
  consolidate all temporary test artifacts under `.pytest-tmp/`.
## Deploy Config Handling

- Keep `deploy/ai-platform/.env.example` as the committed non-secret template.
- Do not copy, export, commit, or quote a real `deploy/ai-platform/.env` file.
- If deployment variables are needed, read them only from the target runtime environment and report redacted evidence.

## Source Of Truth

- Use the current repository root as the local `ai-platform` source.
- Use `docs/superpowers/specs/2026-06-10-ai-platform-product-prd-v2.md`, `docs/superpowers/specs/2026-06-11-ai-platform-tech-acceptance.md`, `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`, `docs/agent-rules/ai-platform-guardrails.md`, current code, and fresh 211 runtime evidence for ai-platform decisions.
- Treat `/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform` as the target 211 backend source path.
- Treat `/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform` as the target 211 repo-local deploy composition path after sync. If live container labels still point to `/home/xinlin.jiang/ai-platform-phaseb/deploy/ai-platform`, report that as stale runtime evidence that must be reconciled before claiming G0 Source Authority closure.
- Treat `http://10.56.0.211:18001/` as the current 211 frontend entry.
- Treat `ai-platform-api` and `ai-platform-worker` as the target backend/worker containers.
- Do not treat short-term execution notes, old local paths, or historical service layouts as product requirements.

## Current Goal-Driven Priorities

Use only issues explicitly named by the active user goal and confirmed current
from fresh GitHub state. Keep concrete issue numbers, owners, and transient
priority ordering in the roadmap or Controller Current, not this durable entry
file.

Current priority is the company-internal Agent platform baseline, not Docker
compose out-of-the-box delivery. Prioritize, in order:

1. AD/company auth and session behavior, tenant/workspace/user isolation,
   RBAC/redaction, and source-authority parity between local source, 211 source,
   repo-local deploy composition, and runtime labels.
2. Tenant-aware concurrency and fair scheduling: DB connection pool, per-tenant
   and per-user quota/backpressure, bounded queue metadata, and tenant-aware
   worker maintenance.
3. Admin Runtime / Observability: queue depth, run status, sandbox lease state,
   latency/token/cost/error/artifact/event metrics, worker heartbeat, dead
   letters, and per-tenant throttling.
4. Memory / Context management and Tool Permission / Agent Frontend V1 user
   loop, with frontend consuming only ai-platform public/admin projections.
5. Long Task / Multi-Agent Runtime only after the earlier gates pass.

Frontend source is maintained in `frontend/web`. Preserve traceability from the
Git commit through the frontend build and image labels. Backend/worker/frontend
multi-image delivery remains roadmap work. Do not make compose one-command
startup or packaged delivery a current acceptance gate, and do not mount the
Docker socket in the default stack.

## GitHub Issue And PR Workflow

Use GitHub issues and pull requests as the default closure loop for goal-sized
work, gate closures, and newly discovered defects. Keep the detailed procedure
in `docs/agent-rules/github-issue-pr-workflow.md` instead of expanding this
entry file.

For an ordinary implementation slice, the linked issue and PR are the plan and
ongoing status record. Create a separate design only when the slice changes a
schema or public contract, persistence, concurrency, infrastructure, or leaves
an unresolved cross-module decision. Medium or long work may keep one concise
Phase status document; do not create a spec/plan/status trio by default.
Create a separate design for security, auth or authorization, tenant isolation,
release or deployment, runtime, and other high-risk changes even when the slice
is otherwise small.
Historical evidence remains historical. Risk-proportionate machine evidence,
including exact authorization and route checks plus the relevant smoke, remains
required before making a status claim.

## Multi-Agent Delegation

- Use `docs/agent-rules/multi-agent-context-workflow.md` for the working
  pattern, including the main-agent 120k-token context target, sub-agent output
  summarization, and context checkpoint rules.
- Do not require per-agent `model` or `reasoning_effort` fields for `spawn_agent`.
- When the delegation tool exposes per-agent `model` or `reasoning_effort`
  fields, set them deliberately according to task complexity.
- Default the controller and every new, resumed, or re-chartered task to a
  reasoning effort no higher than `xhigh`. `max` requires explicit user
  authorization for the exact task and a recorded reason; do not use `ultra` as
  a routine project setting. A current user instruction imposing a stricter cap
  always wins.
- When the tool contract exposes the disposable `default` agent role as
  Luna-low, actively consider it for simple, one-shot, read-only
  context-isolation work such as wide search, log compression, baseline
  comparison, checklist extraction, state refresh, and peripheral evidence
  reduction. Use `fork_turns = "none"`, a self-contained prompt, and a ten-minute
  stop; do not dispatch work merely to fill capacity.
- Luna-low disposable sub-agents must not implement, own a persistent test or
  review generation, mutate GitHub or 211, deploy, receive credentials, or make
  a final high-risk decision.
- When the delegation tool does not expose those fields, use the tool's default
  or inherited configuration and state that model-specific settings were not
  externally asserted.
- Do not claim that a model-specific or reasoning-specific review gate is
  complete unless the model and reasoning level are directly configurable or
  otherwise explicitly confirmed.
- If a user or goal explicitly requires an explicit model/reasoning gate and the
  available delegation path cannot expose or confirm those fields, record the
  review as inherited/default only; do not mark that explicit gate closed until
  the requirement is revised or a suitable review path is available.
- Main-session authority and task ownership are separate. User authorization
  permits an action but does not turn a disposable child into a writer or make
  the controller the routine deployment executor.
- Implementation, complex test/review generations, browser acceptance, and
  release/deployment work use project-bound persistent tasks when the thread
  path has the required permission posture. Every 211 mutation has one release
  owner and one lease after readiness passes. The controller may perform only
  the few decisive read-only preflight or final-parity checks.
- Standing phrases such as `主线程全部授权`, `主线程有权限操作`, or `执行`
  authorize the current main session for the active task; they do not grant
  disposable sub-agents write, GitHub, Docker, deployment, or remote authority,
  and they do not waive the persistent release-owner contract.
- A direct controller mutation is break-glass only: the user must authorize the
  exact mutation after the normal task path is unavailable, and the same single
  lease, source, rollback, and parity invariants still apply. Broad standing
  authorization is not a break-glass grant.
- Do not delegate write, deployment, remote runtime, Docker, GitHub write, or
  long-running operational tasks to sub-agents unless the delegation path is
  confirmed to inherit the same filesystem, network, approval, and permission
  posture as the main session. Keep those tasks in the main session when
  inheritance cannot be proven.
- Complex or high-risk coding should use multi-agent collaboration and review
  when the active user request and available delegation path permit it.
  Lightweight documentation, wording, and single-point fixes do not require
  multi-agent review.

## Verification Strategy

Use layered verification during normal coding:

- Small/local changes: run targeted tests for the touched module, contract, or
  source-authority rule plus `git diff --check` when relevant.
- Medium changes: run related module tests and key-path tests.
- High-risk areas require elevated verification: auth/session, tenant
  isolation, queue, worker maintenance, run lifecycle, sandbox, schema, shared
  contracts, multi-agent runtime, frontend-backend auth/session contracts, and
  211 deployment paths.
- Before PR, deployment, merge, or stage-gate closure: run targeted tests for
  the changed or affected modules plus the relevant integration or smoke checks,
  then record evidence. Do not substitute full pytest for focused verification.
- Do not claim tests, review, 211 smoke, or deployment passed unless the command
  was actually run and the result was observed.

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
