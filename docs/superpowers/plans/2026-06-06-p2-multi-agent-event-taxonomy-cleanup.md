# P2 Multi-Agent Event Taxonomy Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the deployed multi-agent dispatch runtime event names part of the
standard event taxonomy and keep ordinary-user projections public-safe.

**Architecture:** Preserve persisted event names for compatibility. Extend
`STANDARD_EVENT_TYPES` and add one ordinary-user event alias for the visible
child-run creation event. Hidden dispatch control events stay hidden and admin
projections keep raw operational event names.

**Tech Stack:** Python, FastAPI route projection helpers, pytest.

---

## File Structure

- Modify `app/control_plane_contracts.py`: add missing multi-agent dispatch
  event names to `STANDARD_EVENT_TYPES`.
- Modify `app/projection_redaction.py`: redact `parent_run_id` aliases from
  ordinary-user server-owned control metadata.
- Modify `app/routes/runs.py`: add ordinary-user alias
  `run_multi_agent_child_created -> run_child_created`.
- Modify `tests/test_control_plane_contracts.py`: assert the dispatch event
  names are standard.
- Modify `tests/test_routes.py`: assert ordinary/admin public projection
  behavior for child-created events.
- Modify `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`:
  record completion only after review and 211 smoke.

---

### Task 1: RED Contract Tests

**Files:**
- Modify: `tests/test_control_plane_contracts.py`
- Modify: `tests/test_routes.py`

- [ ] **Step 1: Extend taxonomy test**

In `tests/test_control_plane_contracts.py`, extend
`test_standard_event_taxonomy_covers_g2_lifecycle_events` with:

```python
    assert "multi_agent_dispatch_handoff" in STANDARD_EVENT_TYPES
    assert "run_multi_agent_child_created" in STANDARD_EVENT_TYPES
    assert "multi_agent_dispatch_enqueue_failed" in STANDARD_EVENT_TYPES
    assert "multi_agent_dispatch_reconciled" in STANDARD_EVENT_TYPES
    assert "multi_agent_dispatch_parent_parked" in STANDARD_EVENT_TYPES
    assert is_standard_event_type("multi_agent_dispatch_handoff") is True
    assert is_standard_event_type("run_multi_agent_child_created") is True
    assert is_standard_event_type("multi_agent_dispatch_enqueue_failed") is True
    assert is_standard_event_type("multi_agent_dispatch_reconciled") is True
    assert is_standard_event_type("multi_agent_dispatch_parent_parked") is True
```

- [ ] **Step 2: Add projection alias test**

In `tests/test_routes.py`, add a test near
`test_run_event_response_redacts_dispatch_control_metadata_for_ordinary_user`:

```python
def test_run_event_response_aliases_multi_agent_child_created_for_ordinary_user():
    row = {
        "id": "evt-child",
        "trace_id": "trace_child",
        "schema_version": "ai-platform.event-envelope.v1",
        "sequence": 1,
        "event_type": "run_multi_agent_child_created",
        "stage": "control",
        "message": "Multi-agent child run created",
        "severity": "info",
        "visible_to_user": True,
        "error_code": None,
        "latency_ms": None,
        "input_token_count": 0,
        "output_token_count": 0,
        "total_token_count": 0,
        "estimated_cost_minor": 0,
        "payload_json": {
            "visible_to_user": True,
            "copied_from_run_id": "run-parent",
            "parent_run_id": "run-parent-root",
            "parent_step_id": "step-code",
            "step_key": "code",
            "dispatch_id": "dispatch-code",
        },
        "created_at": None,
    }

    event = run_event_response("run-child", row, principal=principal())
    admin_event = run_event_response("run-child", row, principal=principal(roles=["admin"]))

    assert event["event_type"] == "run_child_created"
    assert event["type"] == "run_child_created"
    assert admin_event["event_type"] == "run_multi_agent_child_created"
    assert event["payload"] == {"visible_to_user": True, "step_key": "code"}
    assert "dispatch-code" not in str(event)
    assert "run-parent" not in str(event)
    assert "run-parent-root" not in str(event)
    assert "step-code" not in str(event)
```

- [ ] **Step 3: Run RED tests**

Run:

```powershell
python -m pytest tests/test_control_plane_contracts.py::test_standard_event_taxonomy_covers_g2_lifecycle_events tests/test_routes.py::test_run_event_response_aliases_multi_agent_child_created_for_ordinary_user -q --basetemp .pytest-tmp
```

Expected: fails because the dispatch event names and public alias do not exist.

---

### Task 2: GREEN Minimal Implementation

**Files:**
- Modify: `app/control_plane_contracts.py`
- Modify: `app/projection_redaction.py`
- Modify: `app/routes/runs.py`

- [ ] **Step 1: Add standard event names**

In `app/control_plane_contracts.py`, add these names inside
`STANDARD_EVENT_TYPES` near other multi-agent/control events:

```python
        "multi_agent_dispatch_enqueue_failed",
        "multi_agent_dispatch_handoff",
        "multi_agent_dispatch_parent_parked",
        "multi_agent_dispatch_reconciled",
        "run_multi_agent_child_created",
```

- [ ] **Step 2: Add ordinary-user public alias**

In `app/routes/runs.py`, add this entry to `PUBLIC_EVENT_TYPE_ALIASES`:

```python
    "run_multi_agent_child_created": "run_child_created",
```

- [ ] **Step 3: Redact parent run linkage aliases**

In `app/projection_redaction.py`, add this normalized key to
`SERVER_OWNED_CONTROL_KEYS`:

```python
    "parentrunid",
```

- [ ] **Step 4: Run GREEN tests**

Run:

```powershell
python -m pytest tests/test_control_plane_contracts.py::test_standard_event_taxonomy_covers_g2_lifecycle_events tests/test_routes.py::test_run_event_response_aliases_multi_agent_child_created_for_ordinary_user -q --basetemp .pytest-tmp
```

Expected: passes.

---

### Task 3: Focused Regression Verification

**Files:**
- Verify: `tests/test_control_plane_contracts.py`
- Verify: `tests/test_routes.py`
- Verify: `tests/test_projection_redaction.py`
- Verify: `tests/test_worker.py`
- Verify: `tests/test_run_control_routes.py`
- Verify: `tests/test_repositories.py`
- Verify: `tests/test_multi_agent_dispatcher.py`

- [ ] **Step 1: Run focused event and projection tests**

Run:

```powershell
python -m pytest tests/test_control_plane_contracts.py tests/test_projection_redaction.py tests/test_routes.py::test_run_event_response_redacts_dispatch_control_metadata_for_ordinary_user tests/test_routes.py::test_run_event_response_aliases_multi_agent_child_created_for_ordinary_user -q --basetemp .pytest-tmp
```

Expected: passes.

- [ ] **Step 2: Run affected multi-agent dispatch tests**

Run:

```powershell
python -m pytest tests/test_worker.py::test_worker_parks_top_level_multi_agent_parent_for_dispatcher_without_running_adapter tests/test_run_control_routes.py::test_admin_multi_agent_dispatch_handoff_creates_owner_child_run_and_enqueues tests/test_run_control_routes.py::test_reconcile_multi_agent_child_success_updates_parent_step_event_and_audit tests/test_repositories.py::test_mark_multi_agent_dispatch_enqueue_failed_resets_parent_step_and_fails_child tests/test_multi_agent_dispatcher.py -q --basetemp .pytest-tmp
```

Expected: passes.

---

### Task 4: Review, Full Verification, Roadmap, Deploy

**Files:**
- Modify after successful gates:
  `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`

- [ ] **Step 1: Request inherited-configuration code review**

Review scope:

```text
P2 Multi-Agent Event Taxonomy Cleanup: STANDARD_EVENT_TYPES now includes the
deployed multi-agent dispatch event names; ordinary users see
run_child_created instead of run_multi_agent_child_created; parent run linkage
is stripped from ordinary-user payloads; hidden control events remain hidden
and admin projections keep raw operational event names.
```

- [ ] **Step 2: Fix validated review feedback**

Only fix feedback that is valid against current PRD, roadmap, guardrails, code,
tests, and 211 evidence.

- [ ] **Step 3: Run pre-commit verification**

Run:

```powershell
python -m compileall -q app tools scripts
python -m pytest -q --basetemp .pytest-tmp
git diff --check
```

Expected: compile exits 0; full pytest passes; diff check exits 0.

- [ ] **Step 4: Update roadmap after local verification and review**

Append a `P2 Multi-Agent Event Taxonomy Cleanup` section after
`P2 Multi-Agent Worker Dispatcher` with actual commit, review, local
verification, deployment, and 211 smoke evidence. Do not claim deployment until
it is done.

- [ ] **Step 5: Commit and push**

Run:

```powershell
git status --short
git add app/control_plane_contracts.py app/projection_redaction.py app/routes/runs.py tests/test_control_plane_contracts.py tests/test_projection_redaction.py tests/test_routes.py docs/superpowers/specs/2026-06-06-p2-multi-agent-event-taxonomy-cleanup-design.md docs/superpowers/plans/2026-06-06-p2-multi-agent-event-taxonomy-cleanup.md docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md
git commit -m "test: cover multi-agent event taxonomy cleanup"
git push -u origin codex/p2-multi-agent-event-taxonomy-cleanup
```

- [ ] **Step 6: 211 smoke after deployment**

On 211, verify health, labels, source markers, in-container taxonomy, in-container
projection alias behavior, and recent API/worker logs. Do not print or copy the
real runtime `.env`.
