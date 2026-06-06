# G5 Tenant-Aware Scheduling Admission Metadata Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close GitHub issue #20 by making queue leasing fair within a bounded horizon, queue lookup/removal metadata-backed, and multi-agent child-run creation subject to active-run admission.

**Architecture:** Keep the current Redis global queued list as the worker source of truth, add derived Redis metadata keys for indexed queue lookup/removal, and change quota leasing from tail-window-only to bounded multi-window scanning. Apply existing Postgres advisory-lock user admission to multi-agent child-run fanout before child insertion. Keep public/admin projections redacted and backward-compatible.

**Tech Stack:** Python, FastAPI route handlers, async Redis, async Postgres repository functions, pytest, existing ai-platform queue/run models.

---

## Files

- Modify: `app/queue.py`
  - Add queued metadata keys.
  - Add enqueue metadata write.
  - Add indexed queue position and indexed removal.
  - Add bounded multi-window quota lease.
  - Delete queue metadata when queue items leave the queued list.
  - Rebuild queue metadata when rollback/reclaim paths push raw payloads back
    into the queued list.
- Modify: `app/settings.py`
  - Add `queue_metadata_fallback_scan_limit` for old unindexed queued items.
- Modify: `app/repositories.py`
- Add required active-run admission to `create_multi_agent_dispatch_child_run()`
  and release a claimed dispatch step if admission rejects before child insert.
- Modify: `app/routes/runs.py`
  - Pass active-run admission limit into admin handoff/tick child-run creation.
- Modify: `app/multi_agent_dispatcher.py`
  - Pass active-run admission limit into worker-side child-run creation.
- Modify: `AGENTS.md`
  - Clarify high-risk review and explicit model/reasoning claims.
- Modify: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`
  - Record #20 gate implementation after local verification.
- Test: `tests/test_queue.py`
- Test: `tests/test_run_control_routes.py`
- Test: `tests/test_multi_agent_dispatcher.py`
- Test: `tests/test_admin_run_detail.py`

## Task 1: Queue Metadata And Indexed Lookup

- [x] **Step 1: Write failing queue metadata tests**

Add tests in `tests/test_queue.py`:

```python
@pytest.mark.asyncio
async def test_enqueue_run_writes_indexed_queue_metadata(monkeypatch):
    raw_payload = queue_payload(run_id="run-indexed", tenant_id="tenant-a").model_dump()
    fake = FakeRedis()

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    position = await queue.enqueue_run(raw_payload)

    assert position == 1
    assert fake.metadata_by_message_id
    message_id = next(iter(fake.metadata_by_message_id))
    metadata = json.loads(fake.metadata_by_message_id[message_id])
    assert metadata["run_id"] == "run-indexed"
    assert metadata["tenant_id"] == "tenant-a"
    assert fake.run_index["tenant-a:run-indexed"] == message_id
    assert fake.order_scores[message_id] == 1
```

Add:

```python
@pytest.mark.asyncio
async def test_get_run_queue_position_uses_index_without_full_lrange(monkeypatch):
    raw = queue_payload(run_id="run-indexed", tenant_id="tenant-a").model_dump_json()
    message_id = queue.message_id_for_raw(raw)
    fake = FakeRedis(queued=[raw])
    fake.metadata_by_message_id[message_id] = json.dumps({
        "run_id": "run-indexed",
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "raw": raw,
        "sequence": 1,
    })
    fake.run_index["tenant-a:run-indexed"] = message_id
    fake.order_scores[message_id] = 1

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    position = await queue.get_run_queue_position(tenant_id="tenant-a", run_id="run-indexed")

    assert position == 1
    assert (queue.QUEUE_KEY, 0, -1) not in fake.lrange_calls
```

- [x] **Step 2: Run RED tests**

Run:

```powershell
python -m pytest tests/test_queue.py::test_enqueue_run_writes_indexed_queue_metadata tests/test_queue.py::test_get_run_queue_position_uses_index_without_full_lrange -q --basetemp .pytest-tmp\g5-metadata-red
```

Expected: fail because metadata keys and indexed lookup do not exist.

- [x] **Step 3: Implement queue metadata**

In `app/queue.py`:

- Extend `QueueKeys` with `queued_meta`, `queued_run_index`, `queued_order`, and `queued_sequence`.
- Add `queued_run_index_field(tenant_id, run_id)`.
- Add `ENQUEUE_WITH_METADATA_SCRIPT` that increments sequence, appends raw payload, writes metadata hash, writes run index, writes sorted order, and returns queue position.
- Make `enqueue_run()` validate payload and call the script.
- Make `get_run_queue_position()` use `hget`, `zrank`, and metadata validation.

- [x] **Step 4: Run GREEN metadata tests**

Run the same command from Step 2. Expected: pass.

## Task 2: Indexed Removal And Metadata Cleanup

- [x] **Step 1: Write failing indexed removal tests**

Add in `tests/test_queue.py`:

```python
@pytest.mark.asyncio
async def test_remove_queued_run_uses_indexed_metadata_without_full_lrange(monkeypatch):
    raw = queue_payload(run_id="run-remove", tenant_id="tenant-a").model_dump_json()
    message_id = queue.message_id_for_raw(raw)
    fake = FakeRedis(queued=[raw])
    fake.metadata_by_message_id[message_id] = json.dumps({
        "run_id": "run-remove",
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "raw": raw,
        "sequence": 1,
    })
    fake.run_index["tenant-a:run-remove"] = message_id
    fake.order_scores[message_id] = 1

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    removed = await queue.remove_queued_run(tenant_id="tenant-a", run_id="run-remove")

    assert removed == 1
    assert raw not in fake.queued
    assert message_id not in fake.metadata_by_message_id
    assert "tenant-a:run-remove" not in fake.run_index
    assert message_id not in fake.order_scores
    assert (queue.QUEUE_KEY, 0, -1) not in fake.lrange_calls
```

- [x] **Step 2: Run RED removal test**

Run:

```powershell
python -m pytest tests/test_queue.py::test_remove_queued_run_uses_indexed_metadata_without_full_lrange -q --basetemp .pytest-tmp\g5-remove-red
```

Expected: fail because removal still scans the full list.

- [x] **Step 3: Implement indexed removal and cleanup**

In `app/queue.py`:

- Add `REMOVE_QUEUED_WITH_METADATA_SCRIPT`.
- Make `remove_queued_run()` resolve run index and atomically remove raw plus metadata.
- Add `_delete_queued_metadata()` and call it when quota lease, invalid dead-letter, and legacy lease remove items from queued.

- [x] **Step 4: Run GREEN removal tests**

Run the same command from Step 2. Expected: pass.

## Task 3: Fair Bounded Quota Leasing

- [x] **Step 1: Rewrite starvation test as RED**

Replace `test_lease_run_returns_idle_when_bounded_scan_candidates_are_saturated` in `tests/test_queue.py` with:

```python
@pytest.mark.asyncio
async def test_lease_run_scans_next_window_when_tail_candidates_are_saturated(monkeypatch):
    blocked = queue_payload(run_id="run-blocked", tenant_id="tenant-a", user_id="user-a").model_dump_json()
    allowed = queue_payload(run_id="run-allowed", tenant_id="tenant-b", user_id="user-b").model_dump_json()
    active = queue_payload(run_id="run-active", tenant_id="tenant-a", user_id="user-active").model_dump_json()
    fake = FakeRedis(queued=[allowed, blocked], processing=[active])

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    message = await queue.lease_run(
        timeout_seconds=1,
        worker_id="worker-a",
        max_processing_runs=3,
        tenant_processing_limit=1,
        user_processing_limit=0,
        lease_scan_limit=1,
    )

    assert message is not None
    assert message.payload["run_id"] == "run-allowed"
    assert blocked in fake.queued
    assert allowed in fake.processing
```

- [x] **Step 2: Run RED starvation test**

Run:

```powershell
python -m pytest tests/test_queue.py::test_lease_run_scans_next_window_when_tail_candidates_are_saturated -q --basetemp .pytest-tmp\g5-fair-red
```

Expected: fail because current quota leasing returns idle.

- [x] **Step 3: Implement bounded multi-window lease**

In `_lease_run_with_quota()`:

- Keep global capacity check.
- Treat `lease_scan_limit` as per-window size.
- Add `fairness_horizon = min(queued_depth, max(lease_scan_limit * 4, lease_scan_limit))`.
- Scan newest-first in windows until horizon is exhausted.
- Continue on `quota_blocked` and `conflict`.
- Continue after invalid dead-letter cleanup.
- Stop on `capacity_full`.

- [x] **Step 4: Run GREEN queue fairness tests**

Run:

```powershell
python -m pytest tests/test_queue.py::test_lease_run_scans_next_window_when_tail_candidates_are_saturated tests/test_queue.py::test_lease_run_skips_saturated_tenant_and_leases_next_candidate tests/test_queue.py::test_lease_run_skips_saturated_user_and_leases_next_candidate tests/test_queue.py::test_lease_run_continues_after_invalid_payload_shrinks_scan_window -q --basetemp .pytest-tmp\g5-fair-green
```

Expected: pass.

## Task 4: Multi-Agent Child-Run Admission

- [x] **Step 1: Write failing repository admission test**

Add in `tests/test_run_control_routes.py` near existing `create_multi_agent_dispatch_child_run` repository tests:

```python
@pytest.mark.asyncio
async def test_create_multi_agent_dispatch_child_run_enforces_owner_admission(monkeypatch):
    calls = []

    async def fake_enforce(conn, *, tenant_id, user_id, limit):
        calls.append((tenant_id, user_id, limit))
        raise repositories.RepositoryConflictError("user_active_run_limit_exceeded")

    monkeypatch.setattr("app.repositories.enforce_user_active_run_admission", fake_enforce)

    conn = ExistingFakeConnectionConfiguredLikeChildRunHappyPath()

    with pytest.raises(repositories.RepositoryConflictError, match="user_active_run_limit_exceeded"):
        await repositories.create_multi_agent_dispatch_child_run(
            conn,
            tenant_id="tenant-a",
            parent_run_id="run-parent",
            dispatch_id="dispatch-code",
            handed_off_by="admin-a",
            active_run_admission_limit=3,
        )

    assert calls == [("tenant-a", "user-a", 3)]
    assert not conn.inserted_runs
```

Use the existing fake connection pattern from the neighboring repository tests
instead of introducing a new database test harness.

- [x] **Step 2: Run RED repository test**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py::test_create_multi_agent_dispatch_child_run_enforces_owner_admission -q --basetemp .pytest-tmp\g5-child-admission-red
```

Expected: fail because the function does not accept or call admission.

- [x] **Step 3: Implement admission in repository and callers**

In `app/repositories.py`:

- Add required keyword-only parameter `active_run_admission_limit: int`.
- After parent row is loaded and status is validated, call
  `enforce_user_active_run_admission(conn, tenant_id=tenant_id, user_id=str(parent["user_id"]), limit=active_run_admission_limit)`.
- Keep the call before child run insert.
- If admission rejects, release the claimed dispatch step back to `pending`
  before re-raising the conflict so the claim is not stranded until lease expiry.

In `app/routes/runs.py` and `app/multi_agent_dispatcher.py`:

- Pass `active_run_admission_limit=int(get_settings().max_active_runs_per_user)`.

- [x] **Step 4: Run GREEN multi-agent admission tests**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py::test_create_multi_agent_dispatch_child_run_enforces_owner_admission tests/test_run_control_routes.py::test_admin_multi_agent_dispatch_handoff_creates_owner_child_run_and_enqueues tests/test_run_control_routes.py::test_multi_agent_dispatch_tick_claims_handoffs_and_enqueues_next_ready_step tests/test_multi_agent_dispatcher.py -q --basetemp .pytest-tmp\g5-child-admission-green
```

Expected: pass after updating existing fakes for the new keyword.

## Task 5: Rules And Roadmap Sync

- [x] **Step 1: Update `AGENTS.md`**

Clarify that high-risk work requires independent review and that inherited
review is not an explicit model/reasoning gate.

- [x] **Step 2: Update roadmap gate summary**

Update `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`
under the current G5 status, linking this implementation plan and #20 without
adding long release logs.

- [x] **Step 3: Run docs/rules check**

Run:

```powershell
rg -n "high-risk|independent review|inherited/default|explicit model|model-specific|reasoning-specific|#20|bounded queue metadata|fair" AGENTS.md docs\superpowers\plans\2026-06-02-ai-platform-foundation-roadmap.md
```

Expected: shows the updated rule and gate references.

## Task 6: Verification And Review

- [x] **Step 1: Focused tests**

Run:

```powershell
python -m pytest tests/test_queue.py tests/test_run_control_routes.py::test_create_multi_agent_dispatch_child_run_records_parent_child_events_and_audit tests/test_run_control_routes.py::test_create_multi_agent_dispatch_child_run_builds_single_step_resume_input tests/test_run_control_routes.py::test_create_multi_agent_dispatch_child_run_rejects_malformed_claim_lease_without_insert tests/test_multi_agent_dispatcher.py tests/test_admin_run_detail.py -q --basetemp .pytest-tmp\g5-focused
```

- [x] **Step 2: Compile**

Run:

```powershell
python -m compileall -q app tools scripts
```

- [x] **Step 3: Diff hygiene**

Run:

```powershell
git diff --check
```

- [x] **Step 4: Independent review**

Use a subagent or review agent if the available delegation path can inspect the
diff. Record whether the review used inherited/default configuration or an
explicit model/reasoning setting.

- [x] **Step 5: Full local test gate before PR/deploy**

Run:

```powershell
python -m pytest -q --basetemp .pytest-tmp\g5-full
```

- [ ] **Step 6: Commit, push, PR, and 211 smoke**

After local verification and valid review feedback are resolved:

```powershell
git add app/queue.py app/settings.py app/repositories.py app/routes/runs.py app/multi_agent_dispatcher.py AGENTS.md docs/superpowers/specs/2026-06-06-g5-tenant-aware-scheduling-admission-metadata-design.md docs/superpowers/plans/2026-06-06-g5-tenant-aware-scheduling-admission-metadata.md docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md tests/test_queue.py tests/test_run_control_routes.py tests/test_multi_agent_dispatcher.py tests/test_admin_run_detail.py
git commit -m "feat: close g5 tenant scheduling gaps"
git push
```

Run 211 deployment and smoke only on the Docker-capable 211 host. Verify API and
frontend health, source label parity, queue metadata behavior, child-run
admission rejection, no Docker socket expansion, and redacted admin/public
projections.

## Plan Self-Review

- Spec coverage: queue fair bounded leasing, metadata lookup/removal,
  multi-agent admission, rules sync, tests, review, and 211 smoke are covered.
- Placeholder scan: no unresolved markers are left.
- Type consistency: metadata keys use `message_id`, run index uses
  `tenant_id:run_id -> JSON message_id list`, and child-run admission uses
  `active_run_admission_limit`.
