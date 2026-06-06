# G5 Tenant-Aware Queue Lease Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bounded tenant/user-aware worker queue leasing and throttling observability without migrating the current Redis queue.

**Architecture:** Keep the existing global Redis queued/processing lists. Preserve the current `brpoplpush` fast path when quota limits are disabled, and add a bounded scan path only when tenant or user processing limits are configured. Admin queue insight reports limit, scan, and same-tenant throttling state without exposing queue payloads.

**Tech Stack:** Python, FastAPI support modules, Pydantic settings, Redis async client, pytest.

---

## File Structure

- Modify `app/settings.py`: add queue tenant/user processing limit and scan limit settings.
- Modify `app/queue.py`: add quota helpers, bounded scan lease path, metadata `user_id`, and throttling projection.
- Modify `app/worker_main.py`: pass the new settings into `queue.lease_run()`.
- Modify `deploy/ai-platform/.env.example`: document non-secret queue quota knobs.
- Modify `deploy/ai-platform/docker-compose.yml`: forward quota env vars to API and worker containers.
- Modify `tests/test_queue.py`: add RED/GREEN queue quota and insight tests.
- Modify `tests/test_worker_main.py`: add settings propagation coverage and update existing lease mocks.
- Modify `tests/test_admin_runtime_routes.py` only if existing assertions require queue insight shape updates.
- Modify `tests/test_runtime_launch_script.py` only if compose/env forwarding assertions require updates.
- Modify `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`: record the implemented G5 queue lease slice after verification.

## Task 1: RED Queue Quota Tests

**Files:**
- Modify: `tests/test_queue.py`

- [ ] **Step 1: Add bounded-scan test support**

Update `FakeRedis` with an `lindex` method and make `lrem` usable for queued
candidate removal. If implementation avoids `lindex`, no support change is
needed beyond current `lrange` and `lrem`.

Expected helper if needed:

```python
async def lindex(self, key, index):
    target = self.queued if key == queue.QUEUE_KEY else self.processing
    try:
        return target[index]
    except IndexError:
        return None
```

- [ ] **Step 2: Add RED tests**

Add these tests near existing `lease_run` tests:

```python
@pytest.mark.asyncio
async def test_lease_run_skips_saturated_tenant_and_leases_next_candidate(monkeypatch):
    blocked = queue_payload(run_id="run-blocked", tenant_id="tenant-a", user_id="user-a").model_dump_json()
    allowed = queue_payload(run_id="run-allowed", tenant_id="tenant-b", user_id="user-b").model_dump_json()
    fake = FakeRedis(
        queued=[allowed, blocked],
        meta={"processing-a": json.dumps({"tenant_id": "tenant-a", "user_id": "user-active"})},
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    message = await queue.lease_run(
        timeout_seconds=1,
        worker_id="worker-a",
        max_processing_runs=3,
        tenant_processing_limit=1,
        user_processing_limit=0,
        lease_scan_limit=2,
    )

    assert message is not None
    assert message.payload["run_id"] == "run-allowed"
    assert blocked in fake.queued
    assert allowed not in fake.queued
    assert allowed in fake.processing
```

```python
@pytest.mark.asyncio
async def test_lease_run_skips_saturated_user_and_leases_next_candidate(monkeypatch):
    blocked = queue_payload(run_id="run-blocked", tenant_id="tenant-a", user_id="user-a").model_dump_json()
    allowed = queue_payload(run_id="run-allowed", tenant_id="tenant-a", user_id="user-b").model_dump_json()
    fake = FakeRedis(
        queued=[allowed, blocked],
        meta={"processing-a": json.dumps({"tenant_id": "tenant-a", "user_id": "user-a"})},
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    message = await queue.lease_run(
        timeout_seconds=1,
        worker_id="worker-a",
        max_processing_runs=3,
        tenant_processing_limit=0,
        user_processing_limit=1,
        lease_scan_limit=2,
    )

    assert message is not None
    assert message.payload["run_id"] == "run-allowed"
    assert blocked in fake.queued
    assert allowed in fake.processing
```

```python
@pytest.mark.asyncio
async def test_lease_run_returns_idle_when_bounded_scan_candidates_are_saturated(monkeypatch):
    blocked = queue_payload(run_id="run-blocked", tenant_id="tenant-a", user_id="user-a").model_dump_json()
    later = queue_payload(run_id="run-later", tenant_id="tenant-b", user_id="user-b").model_dump_json()
    fake = FakeRedis(
        queued=[later, blocked],
        meta={"processing-a": json.dumps({"tenant_id": "tenant-a", "user_id": "user-active"})},
    )

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

    assert message is None
    assert fake.queued == [later, blocked]
    assert fake.processing == []
```

```python
@pytest.mark.asyncio
async def test_lease_run_dead_letters_invalid_payload_during_bounded_scan(monkeypatch):
    invalid = '{"run_id": "../bad"}'
    allowed = queue_payload(run_id="run-allowed", tenant_id="tenant-b", user_id="user-b").model_dump_json()
    fake = FakeRedis(queued=[allowed, invalid])

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_redis", get_redis)

    message = await queue.lease_run(
        timeout_seconds=1,
        worker_id="worker-a",
        max_processing_runs=3,
        tenant_processing_limit=1,
        user_processing_limit=1,
        lease_scan_limit=2,
    )

    assert message is not None
    assert message.payload["run_id"] == "run-allowed"
    assert invalid not in fake.queued
    assert fake.pushed[0][0] == queue.DEAD_LETTER_KEY
    assert json.loads(fake.pushed[0][1])["error_code"] == "invalid_queue_payload"
```

- [ ] **Step 3: Run RED command**

Run:

```powershell
python -m pytest tests/test_queue.py::test_lease_run_skips_saturated_tenant_and_leases_next_candidate tests/test_queue.py::test_lease_run_skips_saturated_user_and_leases_next_candidate tests/test_queue.py::test_lease_run_returns_idle_when_bounded_scan_candidates_are_saturated tests/test_queue.py::test_lease_run_dead_letters_invalid_payload_during_bounded_scan -q --basetemp .pytest-tmp\g5-tenant-queue-red
```

Expected: tests fail because `lease_run()` does not accept the quota parameters
or does not implement bounded scan.

## Task 2: GREEN Queue Quota Implementation

**Files:**
- Modify: `app/settings.py`
- Modify: `app/queue.py`

- [ ] **Step 1: Add settings**

Add fields to `Settings`:

```python
queue_tenant_processing_limit: int = Field(default=0)
queue_user_processing_limit: int = Field(default=0)
queue_lease_scan_limit: int = Field(default=50)
```

- [ ] **Step 2: Add quota helpers in `app/queue.py`**

Add focused helpers:

```python
def _processing_quota_counts(meta_items: dict[str, str]) -> tuple[dict[str, int], dict[tuple[str, str], int]]:
    tenant_counts: dict[str, int] = {}
    user_counts: dict[tuple[str, str], int] = {}
    for raw_meta in meta_items.values():
        try:
            meta = json.loads(raw_meta)
        except (TypeError, json.JSONDecodeError):
            continue
        tenant_id = str(meta.get("tenant_id") or "")
        user_id = str(meta.get("user_id") or "")
        if tenant_id:
            tenant_counts[tenant_id] = tenant_counts.get(tenant_id, 0) + 1
        if tenant_id and user_id:
            key = (tenant_id, user_id)
            user_counts[key] = user_counts.get(key, 0) + 1
    return tenant_counts, user_counts
```

Add an admissibility helper that returns a public-safe snapshot:

```python
def _quota_snapshot(
    payload: QueueRunPayload,
    *,
    tenant_counts: dict[str, int],
    user_counts: dict[tuple[str, str], int],
    tenant_processing_limit: int,
    user_processing_limit: int,
) -> dict[str, Any]:
    tenant_processing = tenant_counts.get(payload.tenant_id, 0)
    user_processing = user_counts.get((payload.tenant_id, payload.user_id), 0)
    return {
        "tenant_processing": tenant_processing,
        "tenant_processing_limit": tenant_processing_limit,
        "tenant_processing_saturated": tenant_processing_limit > 0 and tenant_processing >= tenant_processing_limit,
        "user_processing": user_processing,
        "user_processing_limit": user_processing_limit,
        "user_processing_saturated": user_processing_limit > 0 and user_processing >= user_processing_limit,
    }
```

- [ ] **Step 3: Preserve the legacy fast path**

Keep the existing `brpoplpush` logic when both quota limits are disabled:

```python
quota_mode = (tenant_processing_limit or 0) > 0 or (user_processing_limit or 0) > 0
if not quota_mode:
    return await _lease_run_legacy(...)
```

Use a private helper for the existing logic to keep the public signature small.

- [ ] **Step 4: Implement bounded scan path**

For quota mode:

1. Check global processing capacity.
2. If `lease_scan_limit <= 0`, return idle.
3. Read `lrange(keys.queued, 0, lease_scan_limit - 1)`.
4. Load processing metadata and compute counts.
5. Iterate candidates from newest lease order to oldest compatible with current
   `brpoplpush` behavior. With current list semantics, inspect
   `reversed(scanned_items)`.
6. Invalid candidate: remove from queued with `lrem(keys.queued, 1, raw)`,
   dead-letter it, clear retry meta, and continue.
7. Blocked candidate: leave it in queued and continue.
8. Allowed candidate: remove one occurrence from queued, push it to processing,
   write processing/retry metadata including `user_id` and `quota_snapshot`,
   update worker heartbeat, and return `QueueMessage`.
9. If no candidate is leased, return idle.

- [ ] **Step 5: Run GREEN command**

Run:

```powershell
python -m pytest tests/test_queue.py::test_lease_run_skips_saturated_tenant_and_leases_next_candidate tests/test_queue.py::test_lease_run_skips_saturated_user_and_leases_next_candidate tests/test_queue.py::test_lease_run_returns_idle_when_bounded_scan_candidates_are_saturated tests/test_queue.py::test_lease_run_dead_letters_invalid_payload_during_bounded_scan -q --basetemp .pytest-tmp\g5-tenant-queue-green1
```

Expected: 4 passed.

## Task 3: Queue Insight Projection Tests And Implementation

**Files:**
- Modify: `tests/test_queue.py`
- Modify: `app/queue.py`

- [ ] **Step 1: Add RED insight test**

Add a test proving queue insight exposes quota and throttling state:

```python
@pytest.mark.asyncio
async def test_get_queue_insight_reports_quota_throttling(monkeypatch):
    class Settings:
        queue_key_prefix = "ai-platform:runs"
        max_active_worker_runs = 3
        worker_heartbeat_ttl_seconds = 30.0
        queue_tenant_processing_limit = 1
        queue_user_processing_limit = 1
        queue_lease_scan_limit = 25

    raw = queue_payload(run_id="run-a", tenant_id="tenant-a", user_id="user-a").model_dump_json()
    fake = FakeRedis(
        lengths={queue.QUEUE_KEY: 1, queue.PROCESSING_KEY: 1, queue.DEAD_LETTER_KEY: 0},
        queued=[raw],
        meta={"msg-a": json.dumps({"tenant_id": "tenant-a", "user_id": "user-a", "worker_id": "worker-a"})},
        workers={"worker-a": "100.0"},
    )

    async def get_redis():
        return fake

    monkeypatch.setattr("app.queue.get_settings", lambda: Settings())
    monkeypatch.setattr("app.queue.get_redis", get_redis)
    monkeypatch.setattr("app.queue._now", lambda: 120.0)

    insight = await queue.get_queue_insight("tenant-a")

    assert insight["capacity"]["queue_tenant_processing_limit"] == 1
    assert insight["capacity"]["queue_user_processing_limit"] == 1
    assert insight["capacity"]["queue_lease_scan_limit"] == 25
    assert insight["throttling"]["tenant_processing"] == 1
    assert insight["throttling"]["tenant_processing_saturated"] is True
    assert insight["throttling"]["users"]["user-a"]["processing"] == 1
    assert insight["throttling"]["users"]["user-a"]["processing_saturated"] is True
```

- [ ] **Step 2: Run RED command**

Run:

```powershell
python -m pytest tests/test_queue.py::test_get_queue_insight_reports_quota_throttling -q --basetemp .pytest-tmp\g5-queue-insight-red
```

Expected: fail because `throttling` and quota capacity keys do not exist.

- [ ] **Step 3: Implement projection**

Extend `get_queue_insight()` by using `_processing_quota_counts()` and settings
values. Add `capacity` keys and a `throttling` object. Do not remove existing
keys.

- [ ] **Step 4: Run GREEN command**

Run:

```powershell
python -m pytest tests/test_queue.py::test_get_queue_insight_reports_quota_throttling tests/test_queue.py::test_get_queue_insight_counts_tenant_queued_and_processing tests/test_queue.py::test_get_queue_insight_reports_worker_capacity_full -q --basetemp .pytest-tmp\g5-queue-insight-green
```

Expected: 3 passed.

## Task 4: Worker Settings Propagation

**Files:**
- Modify: `tests/test_worker_main.py`
- Modify: `app/worker_main.py`

- [ ] **Step 1: Add RED worker test**

Update the lease mock signature in touched tests to accept:

```python
tenant_processing_limit=None,
user_processing_limit=None,
lease_scan_limit=None,
```

Add:

```python
@pytest.mark.asyncio
async def test_run_once_passes_queue_quota_settings_to_queue(monkeypatch):
    calls = []

    class Settings:
        max_active_worker_runs = 3
        queue_tenant_processing_limit = 2
        queue_user_processing_limit = 1
        queue_lease_scan_limit = 25

    async def reclaim_expired_leases():
        calls.append(("reclaim",))

    async def lease_run(
        timeout_seconds=5,
        worker_id="worker",
        max_processing_runs=None,
        tenant_processing_limit=None,
        user_processing_limit=None,
        lease_scan_limit=None,
    ):
        calls.append(
            (
                "lease",
                timeout_seconds,
                worker_id,
                max_processing_runs,
                tenant_processing_limit,
                user_processing_limit,
                lease_scan_limit,
            )
        )
        return None

    monkeypatch.setattr("app.worker_main.get_settings", lambda: Settings())
    monkeypatch.setattr("app.worker_main.queue.reclaim_expired_leases", reclaim_expired_leases)
    monkeypatch.setattr("app.worker_main.queue.lease_run", lease_run)

    outcome = await run_once(timeout_seconds=1, worker_id="worker-a")

    assert outcome.status == "idle"
    assert calls == [("reclaim",), ("lease", 1, "worker-a", 3, 2, 1, 25)]
```

- [ ] **Step 2: Run RED command**

Run:

```powershell
python -m pytest tests/test_worker_main.py::test_run_once_passes_queue_quota_settings_to_queue -q --basetemp .pytest-tmp\g5-worker-settings-red
```

Expected: fail because `run_once()` does not pass new arguments.

- [ ] **Step 3: Update worker call**

Pass settings to `queue.lease_run()`:

```python
tenant_processing_limit=settings.queue_tenant_processing_limit,
user_processing_limit=settings.queue_user_processing_limit,
lease_scan_limit=settings.queue_lease_scan_limit,
```

- [ ] **Step 4: Run GREEN command**

Run:

```powershell
python -m pytest tests/test_worker_main.py -q --basetemp .pytest-tmp\g5-worker-settings-green
```

Expected: worker tests pass.

## Task 5: Deployment Config And Roadmap

**Files:**
- Modify: `deploy/ai-platform/.env.example`
- Modify: `deploy/ai-platform/docker-compose.yml`
- Modify: `tests/test_runtime_launch_script.py`
- Modify: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`

- [ ] **Step 1: Add non-secret env template values**

Add:

```text
QUEUE_TENANT_PROCESSING_LIMIT=0
QUEUE_USER_PROCESSING_LIMIT=0
QUEUE_LEASE_SCAN_LIMIT=50
```

- [ ] **Step 2: Forward env vars in compose**

Add the three vars to both API and worker service environments using the
existing `${NAME:-default}` pattern.

- [ ] **Step 3: Run config-related focused tests**

Run:

```powershell
python -m pytest tests/test_runtime_launch_script.py tests/test_admin_runtime_routes.py -q --basetemp .pytest-tmp\g5-config-admin
```

Expected: all selected tests pass, or update tests only for intentional config
shape changes.

- [ ] **Step 4: Update roadmap after verification**

Append a concise `### G5 Tenant-Aware Queue Lease` section recording local
verification, review, PR, and 211 smoke evidence after those gates complete.

## Task 6: Verification, Review, Commit, Push, 211 Smoke

**Files:**
- Review all touched files.

- [ ] **Step 1: Focused verification**

Run:

```powershell
python -m pytest tests/test_queue.py tests/test_worker_main.py tests/test_admin_runtime_routes.py tests/test_runtime_launch_script.py -q --basetemp .pytest-tmp\g5-tenant-queue-focused
python -m compileall -q app tools scripts
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 2: Multi-agent review**

Ask an inherited-configuration reviewer to inspect tenant/user quota semantics,
Redis queue ordering, bounded scan behavior, metadata redaction, and admin
projection. If the delegation tool does not expose explicit model or
reasoning-effort fields, record it as inherited-configuration review only.

- [ ] **Step 3: Full local verification**

Run:

```powershell
python -m pytest -q --basetemp .pytest-tmp\g5-tenant-queue-full
```

Expected: full test suite passes.

- [ ] **Step 4: Commit and push**

Run:

```powershell
git status --short
git add app/settings.py app/queue.py app/worker_main.py app/routes/admin_runs.py app/routes/admin_runtime.py app/routes/chat.py app/routes/runs.py deploy/ai-platform/.env.example deploy/ai-platform/docker-compose.yml tests/test_queue.py tests/test_worker_main.py tests/test_admin_runtime_routes.py tests/test_runtime_launch_script.py tests/test_chat_routes.py tests/test_routes.py tests/test_run_control_routes.py tests/test_admin_run_detail.py docs/superpowers/specs/2026-06-06-g5-tenant-aware-queue-lease-design.md docs/superpowers/plans/2026-06-06-g5-tenant-aware-queue-lease.md docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md
git commit -m "feat: add tenant-aware queue leasing"
git push -u origin feat/g5-tenant-aware-queue-lease
```

Stage only files that actually changed.

- [ ] **Step 5: 211 deploy and smoke**

On 211, sync source to `/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform`,
record `.codex-source-revision` and `.codex-source-note`, build or runtime-rebase
only on 211, restart API/worker with the repo-local compose file, then smoke:

- API and frontend health.
- API/worker runtime labels and source marker.
- Admin overview queue insight includes quota and throttling keys.
- In-container temporary queue-prefix quota probe leases a later allowed tenant
  while preserving a saturated tenant item.
- No secret, `.env`, runtime private payload, raw queue payload, storage key, or
  command fingerprint leakage.
- Recent API/worker logs clean and restart count stable.
- Temporary Redis keys and DB smoke rows cleaned.
