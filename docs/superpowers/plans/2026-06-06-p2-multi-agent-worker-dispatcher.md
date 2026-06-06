# P2 Multi-Agent Worker Dispatcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bounded worker-side dispatcher that can automatically tick one safe ready multi-agent step per parked parent run.

**Architecture:** Add a small `app.multi_agent_dispatcher` module that reuses existing route-level readiness/candidate helpers, repository claim/handoff helpers, copied-run queue preparation, and Redis enqueue. The normal worker parks top-level multi-agent parent runs with a server-owned `awaiting_dispatch` marker before adapter execution; the dispatcher only scans marked parents when explicitly enabled.

Review fix note: the parent dispatcher marker is top-level
`input_json.multi_agent_dispatch`, not user-controlled
`input_json.input.multi_agent_dispatch`. User-controlled `resume` and
`multi_agent_dispatch` metadata is stripped from run/chat create inputs and
ordinary public projections.

Review fix note 2: ordinary public event/step/message projections also strip
dispatch claim/handoff control fields. Non-finite dispatcher interval values
fail closed. Redis enqueue failure after a committed child handoff triggers a
compensating DB update that fails the child, resets the parent step to
`pending`, and records hidden event/audit evidence instead of crashing the
worker loop.

**Tech Stack:** FastAPI helper contracts, Pydantic settings, psycopg async transactions, Redis queue helper, pytest.

---

### Task 1: RED Tests

**Files:**
- Create: `tests/test_multi_agent_dispatcher.py`
- Modify: `tests/test_worker.py`
- Modify: `tests/test_worker_main.py`
- Modify: `tests/test_repositories.py`

- [x] **Step 1: Add dispatcher tests**

Add tests proving:

```python
async def test_worker_dispatcher_skips_when_disabled(monkeypatch): ...
async def test_worker_dispatcher_dispatches_candidate_parent_and_enqueues_child(monkeypatch): ...
async def test_worker_dispatcher_skips_conflicted_candidate_without_enqueue(monkeypatch): ...
async def test_worker_dispatcher_disables_pass_on_invalid_interval_or_limit(monkeypatch): ...
```

- [x] **Step 2: Add worker tests**

Add a worker-main test proving `run_once()` invokes dispatcher maintenance after
memory cleanup and before queue lease when enabled. Add a worker test proving a
leased top-level multi-agent parent parks for dispatch without running the
adapter.

- [x] **Step 3: Add repository SQL tests**

Add a repository test proving candidate listing filters same-tenant running
top-level multi-agent runs with the server-owned `awaiting_dispatch` marker and
uses a bounded limit. Add a marker-write SQL test.

- [x] **Step 4: Verify RED**

Run:

```powershell
python -m pytest tests/test_multi_agent_dispatcher.py tests/test_worker_main.py::test_run_once_dispatches_multi_agent_ready_steps_before_queue_lease tests/test_repositories.py::test_list_multi_agent_dispatch_candidate_runs_filters_running_top_level_multi_agent -q --basetemp .pytest-tmp\p2-worker-dispatcher-red
```

Expected before implementation: import/attribute failures for the new
dispatcher and repository helper.

Result: initial RED failed during collection with missing
`list_multi_agent_dispatch_candidate_run_ids`. Review-driven RED failed with
missing `mark_multi_agent_dispatch_parent_awaiting_dispatch`, proving the
park-marker path was not yet implemented.

### Task 2: Dispatcher Implementation

**Files:**
- Create: `app/multi_agent_dispatcher.py`
- Modify: `app/repositories.py`
- Modify: `app/settings.py`
- Modify: `app/worker.py`
- Modify: `app/worker_main.py`
- Modify: `deploy/ai-platform/.env.example`
- Modify: `deploy/ai-platform/docker-compose.yml`

- [x] **Step 1: Add settings**

Add fail-closed settings:

```python
multi_agent_dispatch_worker_enabled: bool = Field(default=False)
multi_agent_dispatch_worker_interval_seconds: float | str = Field(default=30.0)
multi_agent_dispatch_worker_limit: int | str = Field(default=1)
multi_agent_dispatch_worker_user_id: str = Field(default="system:multi-agent-dispatcher")
```

- [x] **Step 2: Add repository candidate and marker helpers**

Add `list_multi_agent_dispatch_candidate_run_ids(conn, *, tenant_id: str, limit: int = 10) -> list[str]` selecting running, top-level, same-tenant multi-agent runs with the server-owned `awaiting_dispatch` marker and a bounded limit. Add `mark_multi_agent_dispatch_parent_awaiting_dispatch(...)` to write the marker.

- [x] **Step 3: Add dispatcher module**

Add:

```python
async def dispatch_multi_agent_ready_steps_for_worker(settings: object | None = None, *, now: float | None = None) -> list[dict[str, object]]:
    ...
```

It should enforce enabled/interval/limit gates, treat malformed interval/limit
values as disabled for that pass, build the synthetic admin
principal, list candidates, run each dispatch in its own transaction, enqueue
only successful child payloads after commit, and skip known fail-closed
conflicts without leaking private payloads.

- [x] **Step 4: Wire worker park, worker loop, and compose env**

When enabled, make `process_run_payload()` park top-level multi-agent parents
before adapter execution. Call the dispatcher in `run_once()` after memory
cleanup and before `queue.reclaim_expired_leases()`. Pass the new env vars
through compose and document non-secret defaults in `.env.example`.

- [x] **Step 5: Verify GREEN**

Run:

```powershell
python -m pytest tests/test_multi_agent_dispatcher.py tests/test_worker_main.py tests/test_repositories.py -q --basetemp .pytest-tmp\p2-worker-dispatcher-green
```

Results:

- Initial focused target after implementation:
  `python -m pytest tests/test_multi_agent_dispatcher.py tests/test_worker_main.py::test_run_once_dispatches_multi_agent_ready_steps_before_queue_lease tests/test_repositories.py::test_list_multi_agent_dispatch_candidate_runs_filters_running_top_level_multi_agent -q --basetemp .pytest-tmp\p2-worker-dispatcher-green1`: `5 passed`.
- Broader green before review fixes:
  `python -m pytest tests/test_multi_agent_dispatcher.py tests/test_worker_main.py tests/test_repositories.py -q --basetemp .pytest-tmp\p2-worker-dispatcher-green`: `105 passed`.
- Review-fix target:
  `python -m pytest tests/test_multi_agent_dispatcher.py::test_worker_dispatcher_disables_pass_on_invalid_interval_or_limit tests/test_worker.py::test_worker_parks_top_level_multi_agent_parent_for_dispatcher_without_running_adapter tests/test_repositories.py::test_list_multi_agent_dispatch_candidate_runs_filters_running_top_level_multi_agent tests/test_repositories.py::test_mark_multi_agent_dispatch_parent_awaiting_dispatch_sets_server_owned_marker -q --basetemp .pytest-tmp\p2-worker-dispatcher-review-green2`: `5 passed`.
- Worker/repository broader after review fixes:
  `python -m pytest tests/test_multi_agent_dispatcher.py tests/test_worker.py tests/test_worker_main.py tests/test_repositories.py -q --basetemp .pytest-tmp\p2-worker-dispatcher-review-broader`: `173 passed`.
- Second review-fix RED:
  `python -m pytest tests/test_repositories.py::test_list_multi_agent_dispatch_candidate_runs_filters_running_top_level_multi_agent tests/test_repositories.py::test_mark_multi_agent_dispatch_parent_awaiting_dispatch_uses_top_level_server_marker_only tests/test_projection_redaction.py::test_sanitize_user_control_input_removes_server_owned_multi_agent_dispatch_metadata tests/test_routes.py::test_create_run_strips_user_controlled_server_owned_metadata tests/test_routes.py::test_get_run_redacts_raw_skill_references_for_ordinary_user tests/test_chat_routes.py::test_chat_stream_strips_user_controlled_server_owned_metadata -q --basetemp .pytest-tmp\p2-dispatch-review-red`: `6 failed` before the fix, covering nested marker acceptance, nested marker writes, user-control metadata persistence, and ordinary projection leakage.
- Second review-fix GREEN:
  `python -m pytest tests/test_repositories.py::test_list_multi_agent_dispatch_candidate_runs_filters_running_top_level_multi_agent tests/test_repositories.py::test_mark_multi_agent_dispatch_parent_awaiting_dispatch_uses_top_level_server_marker_only tests/test_projection_redaction.py::test_sanitize_user_control_input_removes_server_owned_multi_agent_dispatch_metadata tests/test_routes.py::test_create_run_strips_user_controlled_server_owned_metadata tests/test_routes.py::test_get_run_redacts_raw_skill_references_for_ordinary_user tests/test_chat_routes.py::test_chat_stream_strips_user_controlled_server_owned_metadata -q --basetemp .pytest-tmp\p2-dispatch-review-green`: `6 passed`.
- Settings RED/GREEN:
  `python -m pytest tests/test_multi_agent_dispatcher.py::test_settings_accept_malformed_dispatcher_numeric_env_for_pass_level_fail_closed -q --basetemp .pytest-tmp\p2-dispatch-settings-red`: failed before settings type relaxation because malformed interval/limit env values caused startup validation errors.
  `python -m pytest tests/test_multi_agent_dispatcher.py::test_settings_accept_malformed_dispatcher_numeric_env_for_pass_level_fail_closed tests/test_multi_agent_dispatcher.py::test_worker_dispatcher_disables_pass_on_invalid_interval_or_limit -q --basetemp .pytest-tmp\p2-dispatch-settings-green`: `3 passed`.
- Affected regression after the second review fix:
  `python -m pytest tests/test_multi_agent_dispatcher.py tests/test_worker.py tests/test_worker_main.py tests/test_repositories.py tests/test_projection_redaction.py tests/test_routes.py tests/test_chat_routes.py tests/test_run_control_routes.py -q -k "not stream_timeout" --basetemp .pytest-tmp\p2-dispatch-review-affected2`: `398 passed`.
- Third review-fix RED:
  `python -m pytest tests/test_routes.py::test_run_event_response_redacts_dispatch_control_metadata_for_ordinary_user tests/test_routes.py::test_run_step_response_redacts_dispatch_control_metadata_for_ordinary_user tests/test_projection_redaction.py::test_sanitize_user_control_input_removes_server_owned_multi_agent_dispatch_metadata tests/test_chat_routes.py::test_list_messages_redacts_raw_skill_metadata_for_ordinary_user tests/test_multi_agent_dispatcher.py::test_worker_dispatcher_disables_pass_on_invalid_interval_or_limit tests/test_multi_agent_dispatcher.py::test_worker_dispatcher_compensates_child_handoff_when_enqueue_fails -q --basetemp .pytest-tmp\p2-dispatch-review2-red`: `7 failed, 2 passed` before the fix, covering dispatch control projection leakage, non-finite interval handling, and enqueue-failure compensation.
- Third review-fix GREEN:
  `python -m pytest tests/test_routes.py::test_run_event_response_redacts_dispatch_control_metadata_for_ordinary_user tests/test_routes.py::test_run_step_response_redacts_dispatch_control_metadata_for_ordinary_user tests/test_projection_redaction.py::test_sanitize_user_control_input_removes_server_owned_multi_agent_dispatch_metadata tests/test_chat_routes.py::test_list_messages_redacts_raw_skill_metadata_for_ordinary_user tests/test_multi_agent_dispatcher.py::test_worker_dispatcher_disables_pass_on_invalid_interval_or_limit tests/test_multi_agent_dispatcher.py::test_worker_dispatcher_compensates_child_handoff_when_enqueue_fails -q --basetemp .pytest-tmp\p2-dispatch-review2-green`: `9 passed`.
- Compensation helper coverage:
  `python -m pytest tests/test_repositories.py::test_mark_multi_agent_dispatch_enqueue_failed_resets_parent_step_and_fails_child -q --basetemp .pytest-tmp\p2-dispatch-compensation-helper`: `1 passed`.
- Affected regression after the third review fix:
  `python -m pytest tests/test_multi_agent_dispatcher.py tests/test_repositories.py tests/test_projection_redaction.py tests/test_routes.py tests/test_chat_routes.py tests/test_run_control_routes.py tests/test_worker.py tests/test_worker_main.py -q --basetemp .pytest-tmp\p2-dispatch-review2-affected`: `404 passed`.

### Task 3: Review, Docs, And Deployment

**Files:**
- Modify: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`
- Modify: `docs/superpowers/plans/2026-06-06-p2-multi-agent-worker-dispatcher.md`

- [x] **Step 1: Run affected and full verification**

Run:

```powershell
python -m compileall -q app tools scripts
python -m pytest tests/test_multi_agent_dispatcher.py tests/test_worker_main.py tests/test_run_control_routes.py tests/test_source_authority_docs.py -q --basetemp .pytest-tmp\p2-worker-dispatcher-affected
python -m pytest -q --basetemp .pytest-tmp\p2-worker-dispatcher-full
git diff --check
```

Results:

- `python -m compileall -q app tools scripts`: exited 0.
- `python -m pytest tests/test_multi_agent_dispatcher.py tests/test_worker_main.py tests/test_run_control_routes.py tests/test_source_authority_docs.py -q --basetemp .pytest-tmp\p2-worker-dispatcher-affected-final`: `149 passed`.
- `python -m pytest -q --basetemp .pytest-tmp\p2-worker-dispatcher-full-final`: `1035 passed, 6 skipped, 2 warnings`.
- `git diff --check`: exited 0 with only CRLF-to-LF normalization warnings for touched files.
- Added-line scan for secret-like values, runtime private payload markers, executor private payload markers, and personal paths found no matches.

- [x] **Step 2: Request inherited-configuration review**

Use available subagent review if the dispatch tool inherits the current
permissions. Do not claim explicit model/reasoning settings unless the tool
exposes or confirms those settings.

Result: inherited-configuration final review reported no Critical, Important,
or Minor findings. The reviewer stayed read-only and did not rerun pytest, so
the fresh local verification above remains the merge evidence.

- [x] **Step 3: Commit, push, deploy, smoke**

Commit to `main`, push, sync source to 211, build or runtime-rebase according
to `AGENTS.md`, recreate API/worker, then smoke one enabled dispatcher pass
against a temporary safe ready parent run. Verify API/worker labels or code hash
parity, ordinary projection redaction, queue payload creation, event/audit
evidence, logs, and cleanup.

Results:

- Initial feature commit:
  `4c0a94a75fac3929aacc0cd7222c7a4851b2bcc7`.
- 211 smoke exposed a runtime schema bug: `runs.updated_at` does not exist in
  the current schema or on 211. RED target
  `python -m pytest tests/test_repositories.py::test_list_multi_agent_dispatch_candidate_runs_filters_running_top_level_multi_agent tests/test_repositories.py::test_mark_multi_agent_dispatch_parent_awaiting_dispatch_sets_server_owned_marker -q --basetemp .pytest-tmp\p2-worker-dispatcher-runs-updated-at-red`
  failed with the expected `updated_at` SQL assertions.
- Fix commit:
  `92bef5c6e196bcbe4bc563e3ad50d1d96a629d7d`.
- GREEN target:
  `python -m pytest tests/test_repositories.py::test_list_multi_agent_dispatch_candidate_runs_filters_running_top_level_multi_agent tests/test_repositories.py::test_mark_multi_agent_dispatch_parent_awaiting_dispatch_sets_server_owned_marker -q --basetemp .pytest-tmp\p2-worker-dispatcher-runs-updated-at-green`:
  `2 passed`.
- Post-fix affected verification:
  `python -m compileall -q app tools scripts`: exited 0.
  `python -m pytest tests/test_multi_agent_dispatcher.py tests/test_repositories.py tests/test_worker.py tests/test_worker_main.py -q --basetemp .pytest-tmp\p2-worker-dispatcher-runtime-schema-affected`:
  `179 passed`.
  `python -m pytest -q --basetemp .pytest-tmp\p2-worker-dispatcher-runtime-schema-full`:
  `1035 passed, 6 skipped, 2 warnings`.
- Final 211 runtime:
  `ai-platform-api` and `ai-platform-worker` both run
  `ai-platform:92bef5c` with image id
  `sha256:31847f637656f0456adcd92a965454cbd05f128ed2e3434cada50162d3af7e9a`,
  `ai-platform.source-revision=92bef5c6e196bcbe4bc563e3ad50d1d96a629d7d`,
  and `ai-platform.source_note=p2-multi-agent-worker-dispatcher`.
- 211 smoke:
  one temporary parked parent dispatched ready `code` step to child
  `run_a953bd41a4a54bdc9bcf8f84d055b08f`, Redis queue payload removal count
  was `1`, audit actions were `run.multi_agent.dispatch.claim` and
  `run.multi_agent.dispatch.handoff`, ordinary public projection checks passed,
  and cleanup counts for run/session/user/audit/event/step/context/artifact
  tables were all `0`.
- Final health/logs:
  `GET /api/ai/health` returned `{"status":"ok"}`; worker import of
  `app.multi_agent_dispatcher` succeeded; deployed default
  `multi_agent_dispatch_worker_enabled` remained `False`; recent API/worker log
  scan found no error markers.
