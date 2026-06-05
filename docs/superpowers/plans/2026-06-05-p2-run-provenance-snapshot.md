# P2 Run Provenance Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `ai-platform.run-provenance.v1` snapshot contract that exposes checkpoint, subagent, and artifact lineage relationships for an authorized run without opening new long-task execution paths.

**Architecture:** Reuse existing `runs`, `run_steps`, `artifacts`, and public projection helpers instead of adding a new persistence table. The route is owner-scoped under `/api/ai/runs/{run_id}/provenance`; Admin can keep using existing Admin Run Detail until a later admin-specific projection slice. The snapshot builds a deterministic graph from already-sanitized `run_step_response()` and `artifact_card()` outputs.

**Tech Stack:** FastAPI, Python, pytest, existing `app.routes.runs` projection helpers, existing repository read methods.

---

## File Structure

- Modify `app/routes/runs.py`: add `RUN_PROVENANCE_CONTRACT_VERSION`, projection helpers, and `GET /runs/{run_id}/provenance`.
- Modify `tests/test_event_playback_routes.py`: add focused route tests using the existing fake transaction/repository pattern.
- Modify `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`: record that P2 has started with a read-only provenance slice after local tests; update deployment status only after review and 211 smoke pass.

## Task 1: Public Run Provenance Snapshot

**Files:**
- Modify: `app/routes/runs.py`
- Test: `tests/test_event_playback_routes.py`

- [ ] **Step 1: Write the failing ordinary-user provenance test**

Add this test to `tests/test_event_playback_routes.py` after `test_run_playback_projection_redacts_ordinary_user_timeline`:

```python
def test_run_provenance_snapshot_links_steps_checkpoints_and_artifacts(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("tenant-a", "user-a", "run-a")
        return run_row()

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return [artifact_row()]

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        row = step_row()
        row["payload_json"] = {
            **row["payload_json"],
            "checkpoint_id": "checkpoint-a",
            "checkpoint_reused": True,
            "subagent_id": "subagent-a",
            "depends_on": ["plan"],
            "output": "sanitized reviewer output",
        }
        row["status"] = "succeeded"
        return [row]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-a/provenance", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "ai-platform.run-provenance.v1"
    assert body["run"]["run_id"] == "run-a"
    assert body["run"]["skill_id"] is None
    assert body["graph"]["counts"] == {
        "steps": 1,
        "artifacts": 1,
        "checkpoints": 1,
        "subagents": 1,
    }
    assert body["subagents"] == [
        {
            "subagent_id": "subagent-a",
            "role": "reviewer",
            "step_ids": ["step-a"],
            "statuses": ["succeeded"],
            "checkpoint_ids": ["checkpoint-a"],
            "artifact_ids": ["artifact-a"],
        }
    ]
    assert body["checkpoints"] == [
        {
            "checkpoint_id": "checkpoint-a",
            "step_ids": ["step-a"],
            "artifact_ids": ["artifact-a"],
            "reused": True,
        }
    ]
    assert body["artifact_tree"][0]["artifact_id"] == "artifact-a"
    assert body["artifact_tree"][0]["produced_by_step_id"] == "step-a"
    assert body["artifact_tree"][0]["checkpoint_id"] == "checkpoint-a"
    assert body["artifact_tree"][0]["subagent_id"] == "subagent-a"
    public_dump = str(body)
    assert "storage_key" not in public_dump
    assert "command_sha256" not in public_dump
    assert "resource_limits" not in public_dump
    assert "sandbox_mode" not in public_dump
    assert "/tmp/" not in public_dump
    assert "qa-file-reviewer" not in public_dump
```

- [ ] **Step 2: Verify the test fails before implementation**

Run:

```powershell
python -m pytest tests/test_event_playback_routes.py::test_run_provenance_snapshot_links_steps_checkpoints_and_artifacts -q --basetemp .pytest-tmp\p2-provenance-red
```

Expected: `404 Not Found` because `/api/ai/runs/{run_id}/provenance` does not exist.

- [ ] **Step 3: Implement the route and projection helpers**

In `app/routes/runs.py`, add:

```python
RUN_PROVENANCE_CONTRACT_VERSION = "ai-platform.run-provenance.v1"
```

Add helper functions near `multi_agent_snapshot_from_steps()`:

```python
def _unique_sorted(values: list[object]) -> list[str]:
    return sorted({str(item) for item in values if item})


def run_provenance_snapshot(
    *,
    run: dict[str, object],
    steps: list[dict[str, object]],
    artifacts: list[dict[str, object]],
    principal: AuthPrincipal,
) -> dict[str, object]:
    run_id = str(run["id"])
    step_cards = [run_step_response(row, principal=principal) for row in steps]
    artifact_cards = [artifact_card(row, principal=principal) for row in artifacts]
    step_by_id = {str(item["step_id"]): item for item in step_cards}

    artifact_tree = []
    artifacts_by_checkpoint: dict[str, list[str]] = {}
    artifacts_by_subagent: dict[str, list[str]] = {}
    for artifact in artifact_cards:
        lineage = artifact.get("lineage") if isinstance(artifact.get("lineage"), dict) else {}
        checkpoint_id = lineage.get("checkpoint_id")
        subagent_id = lineage.get("subagent_id")
        source_step_id = lineage.get("source_step_id")
        artifact_id = str(artifact["artifact_id"])
        if checkpoint_id:
            artifacts_by_checkpoint.setdefault(str(checkpoint_id), []).append(artifact_id)
        if subagent_id:
            artifacts_by_subagent.setdefault(str(subagent_id), []).append(artifact_id)
        artifact_tree.append(
            {
                "artifact_id": artifact_id,
                "artifact_type": artifact.get("artifact_type"),
                "label": artifact.get("label"),
                "produced_by_step_id": str(source_step_id) if source_step_id in step_by_id else None,
                "producer_kind": lineage.get("producer_kind"),
                "producer_role": lineage.get("producer_role"),
                "checkpoint_id": str(checkpoint_id) if checkpoint_id else None,
                "subagent_id": str(subagent_id) if subagent_id else None,
                "lineage": lineage,
            }
        )

    checkpoints: dict[str, dict[str, object]] = {}
    subagents: dict[str, dict[str, object]] = {}
    for step in step_cards:
        payload = step.get("payload") if isinstance(step.get("payload"), dict) else {}
        step_id = str(step["step_id"])
        checkpoint_id = payload.get("checkpoint_id")
        subagent_id = payload.get("subagent_id")
        if checkpoint_id:
            checkpoints.setdefault(
                str(checkpoint_id),
                {"checkpoint_id": str(checkpoint_id), "step_ids": [], "artifact_ids": [], "reused": False},
            )
            checkpoints[str(checkpoint_id)]["step_ids"].append(step_id)
            checkpoints[str(checkpoint_id)]["reused"] = bool(checkpoints[str(checkpoint_id)]["reused"]) or bool(
                payload.get("checkpoint_reused")
            )
        if subagent_id:
            subagents.setdefault(
                str(subagent_id),
                {
                    "subagent_id": str(subagent_id),
                    "role": step.get("role"),
                    "step_ids": [],
                    "statuses": [],
                    "checkpoint_ids": [],
                    "artifact_ids": [],
                },
            )
            subagents[str(subagent_id)]["step_ids"].append(step_id)
            subagents[str(subagent_id)]["statuses"].append(step.get("status"))
            if checkpoint_id:
                subagents[str(subagent_id)]["checkpoint_ids"].append(str(checkpoint_id))

    for checkpoint_id, artifact_ids in artifacts_by_checkpoint.items():
        checkpoints.setdefault(
            checkpoint_id,
            {"checkpoint_id": checkpoint_id, "step_ids": [], "artifact_ids": [], "reused": False},
        )
        checkpoints[checkpoint_id]["artifact_ids"].extend(artifact_ids)
    for subagent_id, artifact_ids in artifacts_by_subagent.items():
        subagents.setdefault(
            subagent_id,
            {
                "subagent_id": subagent_id,
                "role": None,
                "step_ids": [],
                "statuses": [],
                "checkpoint_ids": [],
                "artifact_ids": [],
            },
        )
        subagents[subagent_id]["artifact_ids"].extend(artifact_ids)

    checkpoint_items = [
        {
            "checkpoint_id": str(item["checkpoint_id"]),
            "step_ids": _unique_sorted(item["step_ids"]),
            "artifact_ids": _unique_sorted(item["artifact_ids"]),
            "reused": bool(item["reused"]),
        }
        for item in checkpoints.values()
    ]
    subagent_items = [
        {
            "subagent_id": str(item["subagent_id"]),
            "role": item.get("role"),
            "step_ids": _unique_sorted(item["step_ids"]),
            "statuses": _unique_sorted(item["statuses"]),
            "checkpoint_ids": _unique_sorted(item["checkpoint_ids"]),
            "artifact_ids": _unique_sorted(item["artifact_ids"]),
        }
        for item in subagents.values()
    ]
    return {
        "contract_version": RUN_PROVENANCE_CONTRACT_VERSION,
        "run": run_playback_summary(run, principal),
        "steps": step_cards,
        "artifact_tree": artifact_tree,
        "checkpoints": sorted(checkpoint_items, key=lambda item: item["checkpoint_id"]),
        "subagents": sorted(subagent_items, key=lambda item: item["subagent_id"]),
        "graph": {
            "counts": {
                "steps": len(step_cards),
                "artifacts": len(artifact_cards),
                "checkpoints": len(checkpoint_items),
                "subagents": len(subagent_items),
            }
        },
    }
```

Add route before `/runs/{run_id}/events`:

```python
@router.get("/runs/{run_id}/provenance")
async def get_run_provenance(
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    async with transaction() as conn:
        run = await repositories.get_authorized_run(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
        if run is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        artifacts = await repositories.list_run_artifacts(conn, tenant_id=principal.tenant_id, run_id=run_id)
        steps = await repositories.list_run_steps(conn, tenant_id=principal.tenant_id, run_id=run_id)
    return run_provenance_snapshot(run=run, steps=steps, artifacts=artifacts, principal=principal)
```

- [ ] **Step 4: Verify the provenance route test passes**

Run:

```powershell
python -m pytest tests/test_event_playback_routes.py::test_run_provenance_snapshot_links_steps_checkpoints_and_artifacts -q --basetemp .pytest-tmp\p2-provenance-green
```

Expected: `1 passed`.

- [ ] **Step 5: Run focused playback/provenance tests**

Run:

```powershell
python -m pytest tests/test_event_playback_routes.py tests/test_routes.py::test_run_playback_projection_redacts_ordinary_user_timeline -q --basetemp .pytest-tmp\p2-provenance-focused
```

Expected: focused tests pass; if a selected test name does not exist, rerun the existing route file tests and report the exact reason.

## Task 2: Roadmap Sync After Verified Implementation

**Files:**
- Modify: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`
- Test: `tests/test_source_authority_docs.py`

- [ ] **Step 1: Add a P2 status paragraph**

After the P1 Tool Permission section, add:

```markdown
### P2 Run Provenance Snapshot

Status: started as a read-only P2 foundation slice. This adds the
`ai-platform.run-provenance.v1` owner-scoped projection for existing run steps,
checkpoint ids, subagent ids, and artifact lineage. It does not start
high-risk tool execution, Docker sandbox expansion, or autonomous multi-agent
scheduling.
```

- [ ] **Step 2: Verify docs**

Run:

```powershell
python -m pytest tests/test_source_authority_docs.py -q --basetemp .pytest-tmp\p2-provenance-docs
```

Expected: `8 passed`.

## Final Verification

- [ ] Run compile:

```powershell
python -m compileall -q app tools scripts
```

- [ ] Run focused tests:

```powershell
python -m pytest tests/test_event_playback_routes.py tests/test_source_authority_docs.py -q --basetemp .pytest-tmp\p2-provenance-final-focused
```

- [ ] Run full suite before commit:

```powershell
python -m pytest -q --basetemp .pytest-tmp\p2-provenance-full
```

- [ ] Request code review after implementation and before merging.
- [ ] Deploy to 211 only after review feedback is handled and local full suite passes.
