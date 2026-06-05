# P2 Checkpoint Materialization Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `ai-platform.run-checkpoint-audit.v1` projection that audits checkpoint reusable-output and artifact-materialization state for an authorized run.

**Architecture:** Reuse existing run authorization, `list_run_steps`, `list_run_artifacts`, public run summaries, artifact cards, and safe graph id helpers in `app/routes/runs.py`. Add pure projection helpers and a single route; do not change copy-run execution, retry policy, worker scheduling, sandbox behavior, tool permission behavior, or persistence schema.

**Tech Stack:** FastAPI, Python, pytest, existing `app.routes.runs` projection helpers, existing repository read methods.

## Completion Evidence

Status: completed on `main` at
`2a268dedcd835d001928b24dc5e1f8fb8782855a`, then deployed and smoked on 211.

- Local focused route verification:
  `python -m pytest tests/test_run_control_routes.py` -> `49 passed`.
- Local source-authority verification:
  `python -m pytest tests/test_run_control_routes.py tests/test_source_authority_docs.py -q --basetemp .pytest-tmp\p2-checkpoint-audit-focused` -> `57 passed`.
- Local compile:
  `python -m compileall -q app tools scripts` -> exit 0.
- Local full verification:
  `python -m pytest -q --basetemp .pytest-tmp\p2-checkpoint-audit-full` -> `895 passed, 6 skipped, 2 warnings`.
- Inherited-configuration review:
  reviewer reported no issues on owner scoping, tenant scoping, ordinary-user
  redaction, checkpoint/artifact aggregation, and no accidental
  retry/resume/subagent/sandbox/tool behavior; reviewer focused tests passed
  with `2 passed`.
- 211 deployment:
  `ai-platform-api` and `ai-platform-worker` run image
  `sha256:32ae0a52d7176745686b2afe75dce497cc20159eb0585b8b05d29a12af1f1720`
  with label `ai-platform.source-revision=2a268dedcd835d001928b24dc5e1f8fb8782855a`.
- 211 smoke:
  `/api/ai/health` returned `{"status":"ok"}`;
  `/openapi.json` includes `/api/ai/runs/{run_id}/checkpoints/audit`;
  seeded ordinary-user smoke returned contract
  `ai-platform.run-checkpoint-audit.v1`, redacted raw skill/private/runtime
  data, and counted one public materialized checkpoint plus one redacted
  uncheckpointed reusable step; same-owner admin saw both checkpoints;
  another user received 404; smoke rows were cleaned up.

---

## File Structure

- Modify `app/routes/runs.py`: add `RUN_CHECKPOINT_AUDIT_CONTRACT_VERSION`, checkpoint audit projection helpers, and `GET /runs/{run_id}/checkpoints/audit`.
- Modify `tests/test_run_control_routes.py`: add focused route tests for checkpoint materialization, artifact-only gaps, step-only/incomplete states, producer checkpoint mismatch, missing or unsafe artifact source steps, uncheckpointed reusable steps, redaction, and 404 behavior.
- Modify `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`: record the P2 checkpoint audit deployment only after local tests, review, and 211 smoke pass.

## Task 1: Checkpoint Audit Route Tests

**Files:**
- Modify: `tests/test_run_control_routes.py`

- [x] **Step 1: Write the failing materialization/redaction test**

Add a test near the existing resume manifest route tests:

```python
def test_run_checkpoint_audit_projects_materialization_without_private_payload(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("default", "user-a", "run-a")
        return {
            "id": "run-a",
            "session_id": "ses-a",
            "schema_version": "ai-platform.run.v1",
            "executor_schema_version": "ai-platform.executor-result.v1",
            "user_id": "user-a",
            "workspace_id": "default",
            "status": "failed",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "trace_id": "trace-a",
            "input_json": {},
            "result_json": {},
            "cancel_requested_at": None,
            "cancel_requested_by": None,
            "error_code": None,
            "error_message": "qa-file-reviewer wrote /tmp/private-output",
        }

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-code",
                "run_id": run_id,
                "step_key": "qa-file-reviewer-step",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "qa-file-reviewer produced C:/runtime/private",
                "role": "reviewer",
                "sequence": 1,
                "payload_json": {
                    "checkpoint_id": "checkpoint-a",
                    "checkpoint_reused": True,
                    "output": "raw checkpoint output must not leak",
                    "resource_limits": {"max_tool_calls": 99},
                    "sandbox_mode": "ephemeral",
                    "command_sha256": "a" * 64,
                    "private_payload": {"storage_key": "tenants/default/private"},
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return [
            {
                "id": "artifact-report",
                "trace_id": "trace-a",
                "artifact_type": "reviewed_docx",
                "label": "Reviewed report",
                "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "storage_key": "tenants/default/runs/run-a/artifacts/report.docx",
                "size_bytes": 12,
                "manifest_version": "ai-platform.artifact-manifest.v1",
                "manifest_json": {
                    "schema_version": "ai-platform.artifact-manifest.v1",
                    "artifact_type": "reviewed_docx",
                    "source_step_id": "step-code",
                    "checkpoint_id": "checkpoint-a",
                    "producer_kind": "agent",
                    "producer_role": "reviewer",
                    "local_path": "/tmp/private/report.docx",
                    "skill_id": "qa-file-reviewer",
                },
                "created_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-a/checkpoints/audit", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "ai-platform.run-checkpoint-audit.v1"
    assert body["run"]["run_id"] == "run-a"
    assert body["run"]["skill_id"] is None
    assert body["counts"] == {
        "checkpoints": 1,
        "resume_reusable": 1,
        "artifact_materialized": 1,
        "step_only": 0,
        "artifact_only": 0,
        "incomplete": 0,
        "gaps": 0,
        "uncheckpointed_reusable_steps": 0,
    }
    assert body["checkpoints"] == [
        {
            "checkpoint_id": "checkpoint-a",
            "audit_state": "materialized",
            "resume_reusable": True,
            "artifact_materialized": True,
            "step_ids": ["step-code"],
            "artifact_ids": ["artifact-report"],
            "reuse": {"pending": 0, "reused": 1},
            "gaps": [],
        }
    ]
    public_dump = str(body)
    assert "raw checkpoint output" not in public_dump
    assert "storage_key" not in public_dump
    assert "command_sha256" not in public_dump
    assert "resource_limits" not in public_dump
    assert "sandbox_mode" not in public_dump
    assert "private_payload" not in public_dump
    assert "/tmp/" not in public_dump
    assert "C:/runtime" not in public_dump
    assert "qa-file-reviewer" not in public_dump
```

- [x] **Step 2: Run the new test and verify RED**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py::test_run_checkpoint_audit_projects_materialization_without_private_payload -q --basetemp .pytest-tmp\p2-checkpoint-audit-red
```

Expected: fail with `404 Not Found` because the route does not exist yet.

- [x] **Step 3: Write the failing gap tests**

Add these tests near the first checkpoint audit test:

```python
def test_run_checkpoint_audit_reports_artifact_only_and_uncheckpointed_step_gaps(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return resume_manifest_run_row(status="failed")

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-uncheckpointed",
                "run_id": run_id,
                "step_key": "review",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "Review",
                "role": "reviewer",
                "sequence": 1,
                "payload_json": {"output": "reusable but no checkpoint id"},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return [
            {
                "id": "artifact-orphan",
                "trace_id": "trace-resume",
                "artifact_type": "reviewed_docx",
                "label": "Orphan artifact",
                "content_type": "application/octet-stream",
                "storage_key": "tenants/default/runs/run-resume/artifacts/orphan.docx",
                "size_bytes": 1,
                "manifest_version": "ai-platform.artifact-manifest.v1",
                "manifest_json": {
                    "schema_version": "ai-platform.artifact-manifest.v1",
                    "artifact_type": "reviewed_docx",
                    "checkpoint_id": "checkpoint-orphan",
                    "source_step_id": "step-missing",
                    "producer_kind": "agent",
                },
                "created_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-resume/checkpoints/audit", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["counts"]["artifact_only"] == 1
    assert body["counts"]["gaps"] == 2
    assert body["checkpoints"] == [
        {
            "checkpoint_id": "checkpoint-orphan",
            "audit_state": "artifact_only",
            "resume_reusable": False,
            "artifact_materialized": True,
            "step_ids": [],
            "artifact_ids": ["artifact-orphan"],
            "reuse": {"pending": 0, "reused": 0},
            "gaps": ["producer_step_missing"],
        }
    ]
    assert body["uncheckpointed_reusable_steps"] == [
        {
            "step_id": "step-uncheckpointed",
            "step_key": "review",
            "status": "succeeded",
            "reason": "missing_checkpoint_id",
        }
    ]
```

Run:

```powershell
python -m pytest tests/test_run_control_routes.py::test_run_checkpoint_audit_reports_artifact_only_and_uncheckpointed_step_gaps -q --basetemp .pytest-tmp\p2-checkpoint-audit-gap-red
```

Expected: fail with `404 Not Found`.

- [x] **Step 4: Write the missing-run test**

Add:

```python
def test_run_checkpoint_audit_returns_not_found_without_loading_steps_or_artifacts(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return None

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        raise AssertionError("steps must not load for missing run")

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        raise AssertionError("artifacts must not load for missing run")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/missing-run/checkpoints/audit", headers=headers())

    assert response.status_code == 404
    assert response.json()["detail"] == "run_not_found"
```

Run:

```powershell
python -m pytest tests/test_run_control_routes.py::test_run_checkpoint_audit_returns_not_found_without_loading_steps_or_artifacts -q --basetemp .pytest-tmp\p2-checkpoint-audit-404-red
```

Expected: fail with `404 Not Found` for the wrong reason only if the route is missing; after implementation, it must still return the same detail without loading child rows.

## Task 2: Checkpoint Audit Projection

**Files:**
- Modify: `app/routes/runs.py`
- Test: `tests/test_run_control_routes.py`

- [x] **Step 1: Add the contract version and helpers**

In `app/routes/runs.py`, add:

```python
RUN_CHECKPOINT_AUDIT_CONTRACT_VERSION = "ai-platform.run-checkpoint-audit.v1"
```

Add helpers near `run_resume_manifest_snapshot()`:

```python
def _checkpoint_audit_step_label(
    row: dict[str, object],
    principal: AuthPrincipal,
    *,
    raw_terms: set[str],
) -> str:
    public_step = run_step_response(row, principal=principal)
    step_id = str(public_step["step_id"])
    step_key = str(public_step["step_key"])
    if is_ai_admin(principal):
        return step_key
    return _resume_manifest_public_text(step_key, fallback=step_id, raw_terms=raw_terms) or step_id


def _checkpoint_audit_state(
    *,
    has_steps: bool,
    resume_reusable: bool,
    artifact_materialized: bool,
) -> str:
    if resume_reusable and artifact_materialized:
        return "materialized"
    if has_steps and not artifact_materialized:
        return "step_only" if resume_reusable else "incomplete"
    if artifact_materialized and not has_steps:
        return "artifact_only"
    return "incomplete"


def _checkpoint_audit_safe_checkpoint_id(
    value: object,
    principal: AuthPrincipal,
    *,
    raw_terms: set[str],
) -> str | None:
    checkpoint_id = _safe_provenance_graph_id("checkpoint_id", value)
    if checkpoint_id is None:
        return None
    if not is_ai_admin(principal) and _contains_raw_projection_term(checkpoint_id, raw_terms):
        return None
    return checkpoint_id
```

- [x] **Step 2: Add `run_checkpoint_audit_snapshot()`**

Add:

```python
def run_checkpoint_audit_snapshot(
    *,
    run: dict[str, object],
    steps: list[dict[str, object]],
    artifacts: list[dict[str, object]],
    principal: AuthPrincipal,
) -> dict[str, object]:
    raw_terms = _readiness_raw_projection_terms(run)
    checkpoints: dict[str, dict[str, object]] = {}
    step_ids = {str(row["id"]) for row in steps}
    step_checkpoint_ids: dict[str, str] = {}
    uncheckpointed: list[dict[str, object]] = []

    for row in steps:
        payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
        status = normalize_step_status(row.get("status"))
        output_available = status == "succeeded" and payload.get("output") is not None
        checkpoint_id = _checkpoint_audit_safe_checkpoint_id(payload.get("checkpoint_id"), principal, raw_terms=raw_terms)
        if checkpoint_id:
            step_checkpoint_ids[str(row["id"])] = checkpoint_id
            item = checkpoints.setdefault(
                checkpoint_id,
                {
                    "checkpoint_id": checkpoint_id,
                    "step_ids": [],
                    "artifact_ids": [],
                    "resume_reusable": False,
                    "artifact_materialized": False,
                    "reuse_pending": 0,
                    "reused": 0,
                    "gaps": set(),
                },
            )
            item["step_ids"].append(str(row["id"]))
            item["resume_reusable"] = bool(item["resume_reusable"]) or output_available
            item["reuse_pending"] = int(item["reuse_pending"]) + (1 if payload.get("checkpoint_reuse_pending") else 0)
            item["reused"] = int(item["reused"]) + (1 if payload.get("checkpoint_reused") else 0)
        elif output_available:
            uncheckpointed.append(
                {
                    "step_id": str(row["id"]),
                    "step_key": _checkpoint_audit_step_label(row, principal, raw_terms=raw_terms),
                    "status": status,
                    "reason": "missing_checkpoint_id",
                }
            )

    artifact_cards = [artifact_card(row, principal=principal) for row in artifacts]
    for row, artifact in zip(artifacts, artifact_cards):
        lineage = artifact.get("lineage") if isinstance(artifact.get("lineage"), dict) else {}
        checkpoint_id = _checkpoint_audit_safe_checkpoint_id(lineage.get("checkpoint_id"), principal, raw_terms=raw_terms)
        if not checkpoint_id:
            continue
        item = checkpoints.setdefault(
            checkpoint_id,
            {
                "checkpoint_id": checkpoint_id,
                "step_ids": [],
                "artifact_ids": [],
                "resume_reusable": False,
                "artifact_materialized": False,
                "reuse_pending": 0,
                "reused": 0,
                "gaps": set(),
            },
        )
        item["artifact_ids"].append(str(artifact["artifact_id"]))
        manifest = row.get("manifest_json") if isinstance(row.get("manifest_json"), dict) else {}
        raw_source_step_id = manifest.get("source_step_id") if isinstance(manifest, dict) else None
        source_step_id = _safe_provenance_graph_id("source_step_id", raw_source_step_id)
        source_step_checkpoint_id = step_checkpoint_ids.get(str(source_step_id)) if source_step_id else None
        if raw_source_step_id is None:
            item["gaps"].add("artifact_source_step_missing")
        elif source_step_id is None:
            item["gaps"].add("artifact_source_step_unsafe")
        elif str(source_step_id) not in step_ids:
            item["gaps"].add("producer_step_missing")
            if not item["step_ids"]:
                item["artifact_materialized"] = True
        elif source_step_checkpoint_id != checkpoint_id:
            item["gaps"].add("producer_checkpoint_mismatch")
        else:
            item["artifact_materialized"] = True

    checkpoint_items = []
    for item in checkpoints.values():
        step_ids_for_checkpoint = _unique_sorted(item["step_ids"])
        artifact_ids = _unique_sorted(item["artifact_ids"])
        resume_reusable = bool(item["resume_reusable"])
        artifact_materialized = bool(item["artifact_materialized"])
        state = _checkpoint_audit_state(
            has_steps=bool(step_ids_for_checkpoint),
            resume_reusable=resume_reusable,
            artifact_materialized=artifact_materialized,
        )
        gaps = set(item["gaps"])
        if bool(step_ids_for_checkpoint) and not resume_reusable:
            gaps.add("no_reusable_output")
        if state == "step_only" and not artifact_ids:
            gaps.add("no_artifact_lineage")
        if state == "artifact_only" and not gaps:
            gaps.add("producer_step_missing")
        checkpoint_items.append(
            {
                "checkpoint_id": str(item["checkpoint_id"]),
                "audit_state": state,
                "resume_reusable": resume_reusable,
                "artifact_materialized": artifact_materialized,
                "step_ids": step_ids_for_checkpoint,
                "artifact_ids": artifact_ids,
                "reuse": {
                    "pending": int(item["reuse_pending"]),
                    "reused": int(item["reused"]),
                },
                "gaps": sorted(gaps),
            }
        )

    checkpoint_items = sorted(checkpoint_items, key=lambda entry: entry["checkpoint_id"])
    counts = {
        "checkpoints": len(checkpoint_items),
        "resume_reusable": sum(1 for item in checkpoint_items if item["resume_reusable"]),
        "artifact_materialized": sum(1 for item in checkpoint_items if item["artifact_materialized"]),
        "step_only": sum(1 for item in checkpoint_items if item["audit_state"] == "step_only"),
        "artifact_only": sum(1 for item in checkpoint_items if item["audit_state"] == "artifact_only"),
        "incomplete": sum(1 for item in checkpoint_items if item["audit_state"] == "incomplete"),
        "gaps": sum(len(item["gaps"]) for item in checkpoint_items) + len(uncheckpointed),
        "uncheckpointed_reusable_steps": len(uncheckpointed),
    }
    run_summary = run_playback_summary(run, principal)
    if not is_ai_admin(principal):
        raw_error_message = run_summary.get("error_message")
        error_fallback = (
            "run_failed"
            if raw_error_message and normalize_run_status(str(run["status"])) == "failed"
            else ""
        )
        run_summary["error_message"] = _readiness_public_text(
            raw_error_message,
            fallback=error_fallback,
            raw_terms=raw_terms,
        )
    return {
        "contract_version": RUN_CHECKPOINT_AUDIT_CONTRACT_VERSION,
        "run": run_summary,
        "counts": counts,
        "checkpoints": checkpoint_items,
        "uncheckpointed_reusable_steps": uncheckpointed,
    }
```

- [x] **Step 3: Add the route**

Add before `@router.post("/runs/{run_id}/cancel", ...)`:

```python
@router.get("/runs/{run_id}/checkpoints/audit")
async def get_run_checkpoint_audit(
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Return read-only checkpoint materialization audit for an authorized run."""
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
        artifacts = await repositories.list_run_artifacts(conn, tenant_id=tenant_id, run_id=run_id)
    return run_checkpoint_audit_snapshot(run=run, steps=steps, artifacts=artifacts, principal=principal)
```

- [x] **Step 4: Run focused tests and fix only this slice**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py -q --basetemp .pytest-tmp\p2-checkpoint-audit-routes
```

Expected: all route tests pass.

## Task 3: Docs, Review, and Verification

**Files:**
- Modify after successful implementation: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`

- [x] **Step 1: Run focused source-authority tests**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py tests/test_source_authority_docs.py -q --basetemp .pytest-tmp\p2-checkpoint-audit-focused
```

Expected: all selected tests pass.

- [x] **Step 2: Run compile and full local verification**

Run:

```powershell
python -m compileall -q app tools scripts
python -m pytest -q --basetemp .pytest-tmp\p2-checkpoint-audit-full
```

Expected: compile exits 0 and full pytest passes.

- [x] **Step 3: Request inherited-config multi-agent review**

Dispatch review only if the available tool inherits current filesystem/network/approval permissions. If no explicit `model` or `reasoning_effort` fields are exposed, do not claim an explicit model gate; record inherited/default configuration review.

Review focus:

```text
Review the P2 Checkpoint Materialization Audit Snapshot for ai-platform.
Check PRD/roadmap/guardrail fit, owner scoping, ordinary-user redaction,
checkpoint/artifact aggregation correctness, and whether the slice accidentally
opens retry, resume scheduler, subagent dispatch, sandbox, or tool behavior.
```

- [x] **Step 4: Update roadmap after review fixes**

Add a P2 section recording the route, contract version, local verification, review, and 211 deployment status. Do not claim 211 deployment before it is done.

- [x] **Step 5: Commit, push, deploy, and smoke**

Commit the implementation branch after pre-commit verification passes. Deploy to 211 from the current branch/source revision. Smoke with `python3` and `sudo -n docker` as required by `AGENTS.md`.

211 smoke must verify:

```text
/api/ai/health == 200
/openapi.json contains /api/ai/runs/{run_id}/checkpoints/audit
seeded same-tenant ordinary-user response contract_version == ai-platform.run-checkpoint-audit.v1
seeded response redacts raw output, storage keys, runtime paths, command fingerprints, resource limits, sandbox fields, raw skill ids, and private payloads
seeded smoke rows are cleaned up
container labels and restart count are reported
```
