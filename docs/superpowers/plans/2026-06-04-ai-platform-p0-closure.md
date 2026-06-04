# ai-platform P0 Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the current ai-platform P0 foundation loop using the latest PRD, foundation roadmap, repository guardrails, current code, focused tests, multi-agent review, and 211 runtime verification.

**Architecture:** Treat `ai-platform` as the only backend control-plane fact source. Keep LambChat as the current thin frontend shell that consumes ai-platform public projections, and keep Docker/sandbox production evidence on 211 rather than the local Windows workstation.

**Tech Stack:** FastAPI, Python, pytest, PostgreSQL schema SQL, Docker Compose on 211, LambChat thin shell served by `tools/serve_lambchat_thin_shell.py`.

---

### Task 1: Source Authority And Guardrails

**Files:**
- Create: `docs/agent-rules/ai-platform-guardrails.md`
- Modify: `AGENTS.md`
- Modify: `docs/superpowers/specs/2026-05-29-ai-platform-final-product-prd.md`
- Modify: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`
- Test: `tests/test_source_authority_docs.py`

- [ ] **Step 1: Write the failing source-authority test**

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRD = ROOT / "docs/superpowers/specs/2026-05-29-ai-platform-final-product-prd.md"
ROADMAP = ROOT / "docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md"
GUARDRAILS = ROOT / "docs/agent-rules/ai-platform-guardrails.md"
AGENTS = ROOT / "AGENTS.md"
COMPOSE = ROOT / "deploy/ai-platform/docker-compose.yml"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_guardrails_document_exists_and_is_named_by_authority_docs():
    assert GUARDRAILS.exists()
    guardrails_text = read(GUARDRAILS)
    assert "ai-platform Guardrails" in guardrails_text
    assert "Current Source Boundaries" in guardrails_text
    assert "P0 Gate Order" in guardrails_text

    assert "docs/agent-rules/ai-platform-guardrails.md" in read(PRD)
    assert "docs/agent-rules/ai-platform-guardrails.md" in read(ROADMAP)
    assert "docs/agent-rules/ai-platform-guardrails.md" in read(AGENTS)


def test_source_authority_docs_keep_current_repo_and_211_deploy_boundary():
    combined = "\n".join([read(PRD), read(ROADMAP), read(GUARDRAILS), read(AGENTS)])
    assert "当前 `ai-platform` 仓库根目录" in combined or "current `ai-platform` repository root" in combined
    assert "/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform" in combined
    assert "/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform" in combined
    assert "http://10.56.0.211:18001/" in combined
    assert "ai-platform-api" in combined
    assert "ai-platform-worker" in combined


def test_default_compose_uses_current_repo_context_and_no_docker_socket():
    compose_text = read(COMPOSE)
    assert compose_text.count("context: ../..") == 2
    assert "/var/run/docker.sock:/var/run/docker.sock" not in compose_text
```

- [ ] **Step 2: Run the focused test to verify it fails before the docs exist**

Run: `python -m pytest tests/test_source_authority_docs.py -q --basetemp .pytest-tmp`

Expected: fails because `docs/agent-rules/ai-platform-guardrails.md` or authority references are missing.

- [ ] **Step 3: Add guardrails and authority references**

Create `docs/agent-rules/ai-platform-guardrails.md` with:

```markdown
# ai-platform Guardrails

## Authority

This file defines repository-level product and engineering guardrails for the
current `ai-platform` control plane.
```

Then include these exact durable sections in that file:

```markdown
## Current Source Boundaries

- Local source is the current `ai-platform` repository root.
- 211 backend source is `/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform`.
- 211 deploy composition target is `/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform`.
- 211 frontend entry is `http://10.56.0.211:18001/`.
```

Modify PRD, roadmap, and `AGENTS.md` so each names `docs/agent-rules/ai-platform-guardrails.md`.

- [ ] **Step 4: Run the focused test to verify it passes**

Run: `python -m pytest tests/test_source_authority_docs.py -q --basetemp .pytest-tmp`

Expected: `3 passed`.

### Task 2: P0 Backend Contract Evidence

**Files:**
- Reference: `app/context_builder.py`
- Reference: `app/routes/context.py`
- Reference: `app/routes/tool_permissions.py`
- Reference: `app/routes/runs.py`
- Reference: `app/routes/sandbox_leases.py`
- Reference: `app/routes/chat.py`
- Reference: `app/intent_router.py`
- Test: `tests/test_context_builder.py`
- Test: `tests/test_context_routes.py`
- Test: `tests/test_tool_permission_routes.py`
- Test: `tests/test_event_playback_routes.py`
- Test: `tests/test_sandbox_lease_routes.py`
- Test: `tests/test_sandbox_container_provider.py`
- Test: `tests/test_intent_router.py`
- Test: `tests/test_chat_routes.py`

- [ ] **Step 1: Re-run focused backend P0 contract tests**

Run:

```powershell
$env:TMP=(Join-Path (Get-Location) '.pytest-tmp')
$env:TEMP=$env:TMP
New-Item -ItemType Directory -Force -Path $env:TMP | Out-Null
python -m pytest tests/test_context_builder.py tests/test_context_routes.py tests/test_tool_permission_routes.py tests/test_event_playback_routes.py tests/test_sandbox_lease_routes.py tests/test_sandbox_container_provider.py tests/test_sandbox_runtime.py tests/test_sandbox_runtime_211_script.py tests/test_run_control_routes.py tests/test_intent_router.py tests/test_chat_routes.py tests/test_routes.py tests/test_lambchat_frontend_compat.py tests/test_admin_run_detail.py tests/test_projection_redaction.py tests/test_worker.py tests/test_repositories.py tests/test_schema.py tests/test_source_authority_docs.py -q --basetemp .pytest-tmp
```

Expected: all selected tests pass. If a failure appears, inspect the current code path named in the traceback before editing.

- [ ] **Step 2: Patch only evidence-backed contract gaps**

If tests reveal missing coverage, add the smallest test that proves the PRD gate and then patch the relevant route/repository/projection. Do not alter unrelated contract behavior.

- [ ] **Step 3: Re-run the focused tests after any patch**

Run the same focused pytest command.

Expected: all selected tests pass.

### Task 3: 211 Runtime Source And Deploy Layout Verification

**Files:**
- Reference: `deploy/ai-platform/docker-compose.yml`
- Reference: `deploy/ai-platform/docker-compose.sandbox.yml`
- Reference: `deploy/ai-platform/.env.example`
- Reference: `tools/serve_lambchat_thin_shell.py`

- [ ] **Step 1: Inspect current 211 runtime state**

Run on `s211`:

```sh
sudo -n docker ps --format "{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}" | grep -E "ai-platform|lambchat" || true
sudo -n docker inspect ai-platform-api ai-platform-worker --format "{{.Name}}|{{.Config.Image}}|{{.Image}}|{{.State.Status}}|{{.State.StartedAt}}|{{index .Config.Labels \"com.docker.compose.project.working_dir\"}}|{{index .Config.Labels \"com.docker.compose.project.config_files\"}}"
curl -sS -i http://127.0.0.1:8020/api/ai/health | head -n 20
```

Expected: `ai-platform-api` and `ai-platform-worker` are running, and `/api/ai/health` returns HTTP 200.

- [ ] **Step 2: Compare 211 compose context against current repo deploy config**

Run on `s211`:

```sh
cd /home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform
grep -n "context:" docker-compose.yml
grep -n "/var/run/docker.sock" docker-compose.yml docker-compose.sandbox.yml
```

Expected after deployment sync: default compose uses the current repository layout and only sandbox overlay mounts Docker socket.

### Task 4: Agent Frontend V1 Projection Verification

**Files:**
- Reference: `tools/serve_lambchat_thin_shell.py`
- Reference: `/home/xinlin.jiang/lambchat-poc/frontend/src/hooks/useAgent/eventProcessor.ts` on 211
- Reference: `/home/xinlin.jiang/lambchat-poc/frontend/src/services/api/toolPermission.ts` on 211
- Reference: `/home/xinlin.jiang/lambchat-poc/frontend/src/services/api/runPlayback.ts` on 211

- [ ] **Step 1: Confirm the 211 frontend shell and source**

Run on `s211`:

```sh
ss -ltnp | grep ":18001" || true
readlink -f /proc/$(pgrep -f "serve_lambchat_thin_shell.py.*18001" | head -n 1)/cwd
ps -fp $(pgrep -f "serve_lambchat_thin_shell.py.*18001" | head -n 1)
```

Expected: 18001 is served by `tools/serve_lambchat_thin_shell.py` and proxies `/api/*` to `http://127.0.0.1:8020`.

- [ ] **Step 2: Inspect frontend projection handlers**

Run on `s211`:

```sh
cd /home/xinlin.jiang/lambchat-poc/frontend
grep -RIn "tool_permission\|permission_request_id\|decision_endpoint\|runPlayback\|artifact_card\|run_event" src | head -n 120
```

Expected: source contains handlers for tool permission cards, run event stream processing, artifact cards, and run playback API.

- [ ] **Step 3: Run frontend tests when package manager is available**

Run on the frontend build host:

```sh
pnpm --version
pnpm test -- src/hooks/useAgent/__tests__/eventProcessor.test.ts src/services/api/__tests__/toolPermission.test.ts src/services/api/__tests__/runPlayback.test.ts
```

Expected: selected frontend contract tests pass. If `pnpm` is not available on 211, record that as an environment limitation and verify with deployed dist plus browser/API smoke instead.

### Task 5: Review, Full Verification, And 211 Deployment

**Files:**
- Modify only files touched by earlier tasks.

- [ ] **Step 1: Run local focused and full verification**

Run:

```powershell
python -m compileall -q app tools scripts
$env:TMP=(Join-Path (Get-Location) '.pytest-tmp')
$env:TEMP=$env:TMP
New-Item -ItemType Directory -Force -Path $env:TMP | Out-Null
python -m pytest tests/test_context_builder.py tests/test_context_routes.py tests/test_tool_permission_routes.py tests/test_event_playback_routes.py tests/test_sandbox_lease_routes.py tests/test_sandbox_container_provider.py tests/test_sandbox_runtime.py tests/test_sandbox_runtime_211_script.py tests/test_run_control_routes.py tests/test_intent_router.py tests/test_chat_routes.py tests/test_routes.py tests/test_lambchat_frontend_compat.py tests/test_admin_run_detail.py tests/test_projection_redaction.py tests/test_worker.py tests/test_repositories.py tests/test_schema.py tests/test_source_authority_docs.py -q --basetemp .pytest-tmp
python -m pytest -q --basetemp .pytest-tmp
```

Expected: compileall exits 0 and focused plus full pytest pass.

- [ ] **Step 2: Request independent multi-agent review**

Dispatch review agents with `model=gpt-5.5` and `reasoning_effort=xhigh` when the delegation tool exposes those fields. If the available tool only supports inherited configuration, report that limitation before dispatch, rely only on explicitly confirmed main-session model/reasoning, and do not claim a model-specific review gate if the main-session model or reasoning level cannot be confirmed. Review scope must include P0 contract boundaries, redaction, source authority, 211 deploy layout, and frontend projection closure.

- [ ] **Step 3: Fix validated review feedback and retest**

Only apply feedback that is proven against PRD, roadmap, guardrails, current code, and tests. Re-run focused tests for touched areas and full local verification after fixes.

- [ ] **Step 4: Deploy to 211 and smoke verify**

Run Docker compose build/recreate and smoke only on 211 or another Docker-capable host. Verify container image identity, restart count, `/api/ai/health`, selected P0 endpoints, and the 18001 frontend shell.

Before claiming the sandbox runtime gate, verify the real 211 Docker path explicitly:

```sh
cd /home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform
sudo -n docker compose -f docker-compose.yml -f docker-compose.sandbox.yml config | grep -E "SANDBOX_CONTAINER_PROVIDER|/var/run/docker.sock|SANDBOX_WORKSPACE_ROOT"
cd /home/xinlin.jiang/ai-platform-phaseb/services/ai-platform
RUN_ID="sandbox-smoke-$(date +%s)"
EXECUTOR_URL="http://127.0.0.1:8091"
EVIDENCE="/tmp/ai-platform-sandbox-runtime-evidence-${RUN_ID}.json"
sudo -n docker rm -f ai-platform-sandbox-smoke-executor >/dev/null 2>&1 || true
sudo -n docker run -d \
  --name ai-platform-sandbox-smoke-executor \
  --label ai-platform.verifier=sandbox-runtime-211 \
  --add-host host.docker.internal:host-gateway \
  -p 127.0.0.1:8091:18000 \
  -v /tmp/ai-platform-sandbox-workspaces:/workspace:rw \
  -e APP_MODULE=app.runtime.sandbox.executor_app:create_executor_app \
  -e APP_PORT=18000 \
  ai-platform:local uvicorn
python3 scripts/generate_sandbox_runtime_evidence_211.py \
  --evidence-file "$EVIDENCE" \
  --run-id "$RUN_ID" \
  --executor-url "$EXECUTOR_URL" \
  --docker-cmd "sudo -n docker" \
  --cancel-image ai-platform:local \
  --callback-host 0.0.0.0 \
  --callback-public-url "http://host.docker.internal:{port}/callback" \
  --callback-timeout 20
python3 scripts/verify_sandbox_runtime_211.py \
  --evidence-file "$EVIDENCE" \
  --run-id "$RUN_ID" \
  --executor-url "$EXECUTOR_URL" \
  --docker-cmd "sudo -n docker"
sudo -n docker rm -f ai-platform-sandbox-smoke-executor >/dev/null 2>&1 || true
```

Then run platform-level user/admin cancel smoke against the deployed API and confirm: the runtime container is stopped/removed, DB active sandbox lease count for the run is `0`, and Admin Runtime projections do not show stale/orphan runtime payload or secret-like values.

Expected: 211 evidence proves the deployed code and frontend entry satisfy the current P0 slice.

## 211 Deployment Evidence

Captured on 2026-06-04 after syncing local commit `31a56bb` to
`/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform`.

- Source backup before overwrite: `/tmp/ai-platform-source-backup-20260604233325.tgz`.
- Image backup before overwrite: `ai-platform:backup-20260604233325`.
- Rebase/no-build fallback was used because the host should not depend on fresh
  package downloads when Python dependencies have not changed; the deployed
  image was rebuilt by copying the current `pyproject.toml`, `app/`, `skills/`,
  and `docker-entrypoint.sh` into the backup base image.
- Real sandbox mode uses the sandbox overlay, not the default compose file:
  `docker-compose.yml` plus `docker-compose.sandbox.yml`. The composed runtime
  sets `SANDBOX_CONTAINER_PROVIDER=docker` and mounts both
  `/var/run/docker.sock` and `/tmp/ai-platform-sandbox-workspaces` only through
  the sandbox overlay.
- Deployed API and worker image identity:
  `sha256:5c37f71c40fedbb145f7ab6c7393dad3e64b079d57aea5e4b135a575c76a4f63`.
- `ai-platform-api` and `ai-platform-worker` were running from
  `/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform`
  with compose config files
  `docker-compose.yml,docker-compose.sandbox.yml`, status `running`, and restart
  count `0`.
- 211 repository check: `python3 -m compileall -q app tools scripts` returned
  `compileall_ok`. Host-level pytest was not available on 211, so pytest
  remained a local verification gate.
- `/api/ai/health` returned HTTP 200 with `{"status":"ok"}`.
- `http://127.0.0.1:18001/` returned HTTP 200. The frontend shell listener was
  `/usr/local/bin/python3 .../tools/serve_lambchat_thin_shell.py --host 0.0.0.0
  --port 18001 --root /home/xinlin.jiang/lambchat-poc/frontend-dist-ai-platform
  --api-base http://127.0.0.1:8020`.
- Post-overlay sandbox runtime verifier passed for run
  `sandbox-smoke-postfix-ready-1780587720`; evidence file:
  `/tmp/ai-platform-sandbox-runtime-evidence-sandbox-smoke-postfix-ready-1780587720.json`.
  A prior verifier attempt submitted before executor health was ready and failed
  with `Connection reset by peer`; rerunning after `/health` was ready passed.
- Platform user cancel smoke run `run-user-1780587901767` returned
  `cancel_requested`, removed `executor-exec-run-user-1780587901767`, set DB
  active sandbox lease count to `0`, and released lease
  `lease-user-1780587901767` with reason `cancel_requested`.
- Platform admin cancel smoke run `run-admin-1780587915476` returned
  `cancel_requested`, removed `executor-exec-run-admin-1780587915476`, set DB
  active sandbox lease count to `0`, and released lease
  `lease-admin-1780587915476` with reason `admin_cancel_requested`.
- Admin Runtime default projection after the cancel smokes returned HTTP 200
  with `total_active=0`, `container_count=0`, `lease_count=0`, and no stale
  smoke run IDs. `include_lease_history=true` returned the released smoke lease
  history without the injected `smoke-secret-token`.
- Tool permission smoke returned HTTP 200 for request, decision, and playback.
  Request `tpr_4da79fd184f64622b5a0f24b609b81d8` ended in `decided`; playback
  contract `ai-platform.run-playback.v1` projected tool permission cards and no
  smoke secret-like values.
- Context snapshot create/list returned HTTP 200 with one snapshot and no smoke
  secret-like values in the public response.
- Memory record create without `session_id` returned HTTP 400
  `memory_session_id_required`; create/list/delete with a bound session returned
  HTTP 200 and redacted smoke secret-like values. Bound smoke record:
  `mem_4f6d011eddd04186b72af667eb3910ec`.
- Chat auto routing for an SOP knowledge question returned HTTP 200, queued run
  `run_ce8124377f7c4921969e1ba40e81589e`, `selected_capability=knowledge_answer`,
  public `agent_id=knowledge-answer`, `skill_id=null`, and no raw
  `ragflow-knowledge-search` string in the public response.
- 211 frontend source contains handlers for tool permission cards, run events,
  artifact cards, and run playback. `pnpm` was not available on 211, so frontend
  verification used deployed HTTP plus source and API projection smoke instead
  of frontend unit tests on that host.

## Post-Review Redaction Fix And Final 211 Evidence

Captured on 2026-06-05 after review feedback commit `b8b2afc` and the
follow-up context redaction fix recorded here.

- `b8b2afc` fixed the validated P0 review findings: stopped sandbox leases are
  released in a committed transaction on partial cleanup failure, ordinary tool
  permission responses hide internal request/decision payloads, unscoped memory
  list/create fails closed with `memory_session_id_required`, and ordinary chat
  routing no longer accepts raw skill-like `agent_id` selectors.
- Local smoke then found a real context projection gap: a free-form
  `smoke-secret-token` value inside a context snapshot payload was returned in
  the public response. The fix extends `redact_memory_text()` to redact
  separator-delimited secret/token-like values while preserving assignment keys
  and already redacted placeholders.
- TDD evidence for the redaction fix:
  `tests/test_context_routes.py::test_create_context_snapshot_redacts_payload_before_persisting`
  failed before the fix because `smoke-secret-token` remained in the persisted
  payload, then passed after the fix. `tests/test_context_routes.py` returned
  `36 passed`; focused affected verification returned `139 passed`. A full
  local verification after the final boundary fix returned
  `823 passed, 6 skipped, 2 warnings`.
- Final 211 backup before deploying the redaction fix:
  `/tmp/ai-platform-source-backup-20260605003640.tgz`; image backup:
  `ai-platform:backup-20260605003640`.
- Final rebase Dockerfile/log:
  `/tmp/ai-platform-redaction-rebase-20260605003640.Dockerfile` and
  `/tmp/ai-platform-redaction-rebase-20260605003640.log`.
- Final deployed API and worker image identity:
  `sha256:ec240f2dcc0fa9e5f45cf35e852f4eb73aa518d04d8d3f9eaafff821199f37fe`.
  Both `ai-platform-api` and `ai-platform-worker` ran this image with restart
  count `0`, compose working dir
  `/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform`,
  and compose files `docker-compose.yml,docker-compose.sandbox.yml`.
- Final 211 readiness: `python3 -m compileall -q app tools scripts` returned
  `compileall_ok`; `/api/ai/health` returned HTTP 200; `http://127.0.0.1:18001/`
  returned HTTP 200.
- Final sandbox runtime verifier passed for run
  `sandbox-smoke-final-1780591097`; evidence file:
  `/tmp/ai-platform-sandbox-runtime-evidence-sandbox-smoke-final-1780591097.json`.
- Final platform cancel smoke passed:
  user run `run-user-1780591168654` returned `cancel_requested`, removed
  `executor-exec-run-user-1780591168654`, active lease count `0`, release reason
  `cancel_requested`; admin run `run-admin-1780591168654` returned
  `cancel_requested`, removed `executor-exec-run-admin-1780591168654`, active
  lease count `0`, release reason `admin_cancel_requested`.
- Final Admin Runtime projection after cancel returned HTTP 200 with
  `total_active=0`, `container_count=0`, `lease_count=0`, and no
  `smoke-secret-token` leak.
- Final P0 API smoke passed for run `run_20c31c45ef9f4534849b4616b9c8857a`:
  tool permission request `tpr_33bf7c580dc9488881166e03b96e201f` requested,
  fetched, decided, and appeared in run playback without internal
  `request_payload`/`decision_payload`; context snapshot
  `ctx_b9bcde8118db42639508c762617511b0` create/list redacted
  `smoke-secret-token` and the JSON-string value `client-secret-json`; memory
  create without `session_id` returned HTTP 400 `memory_session_id_required`;
  bound memory record `mem_d8aa73decdde4f99913463698c642f04`
  create/list/delete returned HTTP 200 without the smoke secret; auto routing
  selected `knowledge_answer`, projected public `agent_id=knowledge-answer`, and
  returned `skill_id=null` without raw `ragflow-knowledge-search` in the chat
  response.
- Final residual sandbox container check was empty:
  `docker ps --filter label=ai-platform.owner=sandbox-runtime` returned no rows.

## Follow-Up Review Feedback Fix Evidence

Captured on 2026-06-05 after inherited read-only reviewer feedback against the
post-`37e37b3` working tree. The delegation tool in this environment does not
expose explicit `model` or `reasoning_effort` fields, so this follow-up is
recorded as an inherited-configuration review and not claimed as an explicit
`gpt-5.5` + `xhigh` gate.

- Validated feedback fixed in this follow-up:
  malformed `tool_permission_*` playback fallback now strips internal
  `request_payload` and `decision_payload`; free-form redaction preserves safe
  public terms such as `token-budget`, `auth-token-status`,
  `password-reset-flow`, and `credential-helper`; punctuated and trailing
  separator secret-like values such as `smoke-secret-token.` and
  `smoke-secret-token-` are redacted.
- TDD evidence: the new punctuation regression in
  `tests/test_control_plane_contracts.py::test_public_payload_sanitizer_redacts_secret_like_executor_values`
  failed before the guard fix, then passed after the implementation changed to
  classify the complete delimiter token before preserving safe public text.
- Focused local verification returned `58 passed` for
  `tests/test_event_playback_routes.py`, `tests/test_tool_permission_routes.py`,
  `tests/test_control_plane_contracts.py`, and `tests/test_context_routes.py`.
- Full local verification returned `824 passed, 6 skipped, 2 warnings`.
  `python -m compileall -q app tools scripts` and `git diff --check` both
  returned exit code `0`; `git diff --check` only emitted CRLF normalization
  warnings.
- Local source hashes before 211 follow-up deployment:
  `app/memory_redaction.py`
  `00C7ECCD4514333884B1B2536C66B23F8612DA40134C21A6CC79DF90F3688358`;
  `app/tool_permission_projection.py`
  `F47535CE38B42C25667C5DA154C6B9CE10633FEC636CF31B27E9C8A9715BDA2B`.

## Self-Review

- Spec coverage: Tasks cover PRD source authority, P0 backend contracts, frontend projection closure, sandbox deploy/runtime boundaries, review, local verification, and 211 smoke.
- Placeholder scan: The plan contains no open-ended placeholders; all commands and files are concrete.
- Type consistency: Test names, route paths, contract names, and compose paths match the current repository and 211 paths observed in this session.
