# P2 Resume Manifest Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `ai-platform.run-resume-manifest.v1` projection that describes checkpoint reuse intent for copied runs.

**Architecture:** Reuse the existing run authorization, playback summary, run-step projection, and readiness redaction helpers in `app/routes/runs.py`. Add a single route and pure projection helpers; do not change copy execution, worker scheduling, retry policy, sandbox behavior, or tool permission behavior.

**Tech Stack:** FastAPI route handlers, existing repository helpers, pytest route tests with monkeypatched repositories.

---

## File Structure

- Modify `app/routes/runs.py`: add `RUN_RESUME_MANIFEST_CONTRACT_VERSION`, projection helpers, and `GET /runs/{run_id}/resume/manifest`.
- Modify `tests/test_run_control_routes.py`: add focused route tests for copied-run manifest, redaction, disabled normal-run state, and 404 behavior.
- Modify `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`: record P2 resume manifest status after local verification and later append 211 deployment evidence.

## Task 1: Failing Route Tests

**Files:**
- Modify: `tests/test_run_control_routes.py`

- [ ] **Step 1: Add copied-run manifest test**

Append these tests after the existing run control readiness tests:

```python
def resume_manifest_run_row(*, status="queued", error_message=None):
    return {
        "id": "run-resume",
        "session_id": "ses-resume",
        "workspace_id": "default",
        "agent_id": "qa-word-review",
        "skill_id": "qa-file-reviewer",
        "schema_version": "ai-platform.run.v1",
        "executor_schema_version": "ai-platform.executor-result.v1",
        "status": status,
        "trace_id": "trace-resume",
        "input_json": {"message": "resume", "skill_id": "qa-file-reviewer"},
        "result_json": {},
        "error_code": None,
        "error_message": error_message,
        "cancel_requested_at": None,
        "cancel_requested_by": None,
    }


def test_run_resume_manifest_projects_copied_reuse_intent(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("default", "user-a", "run-resume")
        return resume_manifest_run_row()

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-code",
                "run_id": run_id,
                "step_key": "code",
                "step_kind": "agent",
                "status": "pending",
                "title": "Code",
                "role": "coding",
                "sequence": 1,
                "payload_json": {
                    "checkpoint_reuse_pending": True,
                    "copied_from_run_id": "run-old",
                    "depends_on": [],
                    "output": "raw source output must not leak",
                    "skill_ids": ["qa-file-reviewer"],
                    "resource_limits": {"max_seconds": 60},
                    "sandbox_mode": "ephemeral",
                    "private_payload": {"token": "secret-token"},
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": "step-test",
                "run_id": run_id,
                "step_key": "test",
                "step_kind": "agent",
                "status": "pending",
                "title": "Test",
                "role": "verifier",
                "sequence": 2,
                "payload_json": {"depends_on": ["code"]},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-resume/resume/manifest", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "ai-platform.run-resume-manifest.v1"
    assert body["run"]["run_id"] == "run-resume"
    assert body["run"]["skill_id"] is None
    assert body["source_run_id"] == "run-old"
    assert body["resume_enabled"] is True
    assert body["reason"] == "reuse_pending"
    assert body["counts"] == {
        "total": 2,
        "reuse_pending": 1,
        "rerun": 1,
        "pending": 2,
        "running": 0,
        "succeeded": 0,
        "failed": 0,
        "cancelled": 0,
    }
    assert body["steps"] == [
        {
            "step_id": "step-code",
            "step_key": "code",
            "status": "pending",
            "title": "Code",
            "role": "coding",
            "sequence": 1,
            "depends_on": [],
            "reuse_intent": "reuse_pending",
            "source_run_id": "run-old",
        },
        {
            "step_id": "step-test",
            "step_key": "test",
            "status": "pending",
            "title": "Test",
            "role": "verifier",
            "sequence": 2,
            "depends_on": ["code"],
            "reuse_intent": "rerun",
            "source_run_id": None,
        },
    ]
    public_dump = str(body)
    assert "raw source output" not in public_dump
    assert "qa-file-reviewer" not in public_dump
    assert "resource_limits" not in public_dump
    assert "sandbox_mode" not in public_dump
    assert "private_payload" not in public_dump
    assert "secret-token" not in public_dump
```

- [ ] **Step 2: Add redaction, disabled-state, and 404 tests**

```python
def test_run_resume_manifest_redacts_raw_skill_ids_from_public_scalars(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return resume_manifest_run_row(error_message="qa-file-reviewer failed in qa-word-review")

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-skill",
                "run_id": run_id,
                "step_key": "qa-file-reviewer",
                "step_kind": "agent",
                "status": "pending",
                "title": "qa-file-reviewer",
                "role": "qa-word-review",
                "sequence": 1,
                "payload_json": {
                    "checkpoint_reuse_pending": True,
                    "copied_from_run_id": "run-old",
                    "depends_on": ["qa-file-reviewer"],
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-resume/resume/manifest", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["run"]["error_message"] == "run_failed"
    assert body["steps"] == [
        {
            "step_id": "step-skill",
            "step_key": "step-skill",
            "status": "pending",
            "title": "step-skill",
            "role": None,
            "sequence": 1,
            "depends_on": [],
            "reuse_intent": "reuse_pending",
            "source_run_id": "run-old",
        }
    ]
    public_dump = str(body)
    assert "qa-file-reviewer" not in public_dump
    assert "qa-word-review" not in public_dump


def test_run_resume_manifest_returns_disabled_state_for_normal_run(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return resume_manifest_run_row(status="running")

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-normal",
                "run_id": run_id,
                "step_key": "normal",
                "step_kind": "agent",
                "status": "running",
                "title": "Normal",
                "role": "worker",
                "sequence": 1,
                "payload_json": {"depends_on": []},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-resume/resume/manifest", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["source_run_id"] is None
    assert body["resume_enabled"] is False
    assert body["reason"] == "no_reuse_pending"
    assert body["counts"]["total"] == 1
    assert body["counts"]["reuse_pending"] == 0
    assert body["counts"]["rerun"] == 1


def test_run_resume_manifest_returns_not_found_without_loading_steps(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("default", "user-a", "missing-run")
        return None

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        raise AssertionError("steps must not be listed for missing run")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/missing-run/resume/manifest", headers=headers())

    assert response.status_code == 404
    assert response.json() == {"detail": "run_not_found"}
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py::test_run_resume_manifest_projects_copied_reuse_intent tests/test_run_control_routes.py::test_run_resume_manifest_redacts_raw_skill_ids_from_public_scalars tests/test_run_control_routes.py::test_run_resume_manifest_returns_disabled_state_for_normal_run tests/test_run_control_routes.py::test_run_resume_manifest_returns_not_found_without_loading_steps -q --basetemp .pytest-tmp\p2-resume-manifest-red
```

Expected: tests fail with `404 Not Found` because the route does not exist.

## Task 2: Manifest Projection Implementation

**Files:**
- Modify: `app/routes/runs.py`

- [ ] **Step 1: Add contract constant and helper functions**

Add the contract constant near the other run projection constants:

```python
RUN_RESUME_MANIFEST_CONTRACT_VERSION = "ai-platform.run-resume-manifest.v1"
```

Add these helpers near `run_control_readiness_snapshot`:

```python
def _resume_manifest_public_depends_on(values: object, *, raw_terms: set[str]) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        text = _resume_manifest_public_text(value, raw_terms=raw_terms)
        if text:
            result.append(text)
    return result


def _resume_manifest_has_fingerprint(text: str) -> bool:
    if HASH_LIKE_VALUE_PATTERN.fullmatch(text.strip()):
        return True
    return any(HASH_LIKE_VALUE_PATTERN.fullmatch(token) for token in re.split(r"[^A-Fa-f0-9]+", text))


def _resume_manifest_public_text(value: object, *, fallback: object = "", raw_terms: set[str]) -> str:
    text = _readiness_public_text(value, raw_terms=raw_terms)
    if text and not _resume_manifest_has_fingerprint(text):
        return text
    fallback_text = _readiness_public_text(fallback, raw_terms=raw_terms)
    if fallback_text and not _resume_manifest_has_fingerprint(fallback_text):
        return fallback_text
    return ""


def _resume_manifest_step(
    row: dict[str, object],
    principal: AuthPrincipal,
    *,
    raw_terms: set[str],
    authorized_source_run_ids: set[str],
) -> dict[str, object]:
    public_step = run_step_response(row, principal=principal)
    payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
    step_id = str(public_step["step_id"])
    step_key = str(public_step["step_key"])
    title = public_step.get("title")
    role = public_step.get("role")
    source_run_id = (
        _safe_provenance_graph_id("source_run_id", payload.get("copied_from_run_id"))
        if payload.get("checkpoint_reuse_pending")
        else None
    )
    if source_run_id and source_run_id not in authorized_source_run_ids:
        source_run_id = None
    depends_on = payload.get("depends_on")
    public_raw_terms = raw_terms if not is_ai_admin(principal) else set()
    step_key = _resume_manifest_public_text(step_key, fallback=step_id, raw_terms=public_raw_terms) or step_id
    title = _resume_manifest_public_text(title, fallback=step_key, raw_terms=public_raw_terms) or step_key
    role = _resume_manifest_public_text(role, raw_terms=public_raw_terms) if role is not None else None
    role = role or None
    depends_on = _resume_manifest_public_depends_on(depends_on, raw_terms=public_raw_terms)
    return {
        "step_id": step_id,
        "step_key": step_key,
        "status": str(public_step["status"]),
        "title": title,
        "role": role,
        "sequence": int(public_step.get("sequence") or 0),
        "depends_on": depends_on,
        "reuse_intent": "reuse_pending" if payload.get("checkpoint_reuse_pending") else "rerun",
        "source_run_id": str(source_run_id) if source_run_id else None,
    }
```

- [ ] **Step 2: Add snapshot function**

```python
def run_resume_manifest_snapshot(
    *,
    run: dict[str, object],
    steps: list[dict[str, object]],
    principal: AuthPrincipal,
    authorized_source_run_ids: set[str] | None = None,
) -> dict[str, object]:
    """Return read-only checkpoint reuse intent for a copied run."""
    raw_terms = _readiness_raw_projection_terms(run)
    manifest_steps = [
        _resume_manifest_step(
            row,
            principal,
            raw_terms=raw_terms,
            authorized_source_run_ids=authorized_source_run_ids or set(),
        )
        for row in steps
    ]
    source_run_ids = sorted({str(item["source_run_id"]) for item in manifest_steps if item.get("source_run_id")})
    source_run_id = source_run_ids[0] if len(source_run_ids) == 1 else None
    counts = {
        "total": len(manifest_steps),
        "reuse_pending": sum(1 for item in manifest_steps if item["reuse_intent"] == "reuse_pending"),
        "rerun": sum(1 for item in manifest_steps if item["reuse_intent"] == "rerun"),
        "pending": sum(1 for item in manifest_steps if item["status"] == "pending"),
        "running": sum(1 for item in manifest_steps if item["status"] == "running"),
        "succeeded": sum(1 for item in manifest_steps if item["status"] == "succeeded"),
        "failed": sum(1 for item in manifest_steps if item["status"] == "failed"),
        "cancelled": sum(1 for item in manifest_steps if item["status"] == "cancelled"),
    }
    run_summary = run_playback_summary(run, principal)
    if not is_ai_admin(principal):
        raw_error_message = run_summary.get("error_message")
        error_fallback = "run_failed" if raw_error_message and normalize_run_status(str(run["status"])) == "failed" else ""
        run_summary["error_message"] = _readiness_public_text(
            raw_error_message,
            fallback=error_fallback,
            raw_terms=raw_terms,
        )
    resume_enabled = counts["reuse_pending"] > 0
    return {
        "contract_version": RUN_RESUME_MANIFEST_CONTRACT_VERSION,
        "run": run_summary,
        "source_run_id": source_run_id,
        "resume_enabled": resume_enabled,
        "reason": "reuse_pending" if resume_enabled else "no_reuse_pending",
        "counts": counts,
        "steps": manifest_steps,
    }


def _resume_manifest_source_run_candidates(steps: list[dict[str, object]]) -> list[str]:
    source_run_ids: set[str] = set()
    for row in steps:
        payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
        if not payload.get("checkpoint_reuse_pending"):
            continue
        source_run_id = _safe_provenance_graph_id("source_run_id", payload.get("copied_from_run_id"))
        if source_run_id:
            source_run_ids.add(source_run_id)
    return sorted(source_run_ids)
```

- [ ] **Step 3: Add route**

Add this route near the existing readiness/provenance routes:

```python
@router.get("/runs/{run_id}/resume/manifest")
async def get_run_resume_manifest(
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Return read-only checkpoint reuse intent for an authorized copied run."""
    tenant_id = principal.tenant_id
    async with transaction() as conn:
        run = await repositories.get_authorized_run(
            conn,
            tenant_id=tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
        if run is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        steps = await repositories.list_run_steps(conn, tenant_id=tenant_id, run_id=run_id)
        authorized_source_run_ids: set[str] = set()
        for source_run_id in _resume_manifest_source_run_candidates(steps):
            source_run = await repositories.get_authorized_run(
                conn,
                tenant_id=tenant_id,
                user_id=principal.user_id,
                run_id=source_run_id,
            )
            if source_run is not None:
                authorized_source_run_ids.add(source_run_id)
    return run_resume_manifest_snapshot(
        run=run,
        steps=steps,
        principal=principal,
        authorized_source_run_ids=authorized_source_run_ids,
    )
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py::test_run_resume_manifest_projects_copied_reuse_intent tests/test_run_control_routes.py::test_run_resume_manifest_redacts_raw_skill_ids_from_public_scalars tests/test_run_control_routes.py::test_run_resume_manifest_returns_disabled_state_for_normal_run tests/test_run_control_routes.py::test_run_resume_manifest_returns_not_found_without_loading_steps -q --basetemp .pytest-tmp\p2-resume-manifest-green
```

Expected: all 4 tests pass.

## Task 3: Roadmap And Verification

**Files:**
- Modify: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`

- [ ] **Step 1: Add P2 progress note**

After the P2 Run Control Readiness Snapshot section, add:

```markdown
### P2 Resume Manifest Snapshot

Status: started as a read-only P2 foundation slice. This adds the
`ai-platform.run-resume-manifest.v1` owner-scoped projection for copied-run
checkpoint reuse intent, source run linkage, pending reuse counts, rerun counts,
and public-safe step dependencies. It does not start retry scheduling,
autonomous multi-agent dispatch, high-risk tool execution, or new sandbox
behavior.
```

- [ ] **Step 2: Run focused verification**

Run:

```powershell
python -m compileall -q app tools scripts
python -m pytest tests/test_run_control_routes.py tests/test_source_authority_docs.py -q --basetemp .pytest-tmp\p2-resume-manifest-focused
```

Expected: compile exits 0 and focused tests pass.

- [ ] **Step 3: Run full verification**

Run:

```powershell
python -m pytest -q --basetemp .pytest-tmp\p2-resume-manifest-full
```

Expected: full suite passes, with only existing skips or warnings.

- [ ] **Step 4: Multi-agent review**

Request inherited/default-config code review because the current dispatch path
does not expose externally asserted `model` or `reasoning_effort` controls.
Fix all valid Critical and Important findings, then rerun focused tests.

- [ ] **Step 5: Commit, deploy, and smoke on 211**

Commit the verified code and docs. Deploy to 211 using the repo's current
runtime-only image path when dependencies are unchanged. After deployment,
verify `/api/ai/health`, OpenAPI route exposure, ordinary-user manifest
redaction, image/container labels, restart counts, and seeded smoke data cleanup.
