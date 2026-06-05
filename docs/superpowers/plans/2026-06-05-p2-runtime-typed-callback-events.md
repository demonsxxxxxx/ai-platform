# P2 Runtime Typed Callback Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let internal sandbox runtime callbacks persist standard typed checkpoint, subagent, and agent-step events through the existing event bridge.

**Architecture:** Extend `ExecutorCallbackEvent` with validated `AgentEvent` items, append those items in the existing normalizer, and route every normalized event through `agent_event_to_executor_event()` before persistence. Existing callback fields remain compatible.

**Tech Stack:** FastAPI, Pydantic, pytest, existing ai-platform runtime callback route and event bridge.

---

### Task 1: Add Typed Callback Event Contract

**Files:**
- Modify: `app/runtime/sandbox/contracts.py`
- Modify: `app/runtime/sandbox/event_normalizer.py`
- Test: `tests/test_sandbox_contracts.py`
- Test: `tests/test_sandbox_executor_client.py`

- [ ] **Step 1: Write failing contract tests**

Add tests that validate `ExecutorCallbackEvent.events` accepts supported
`AgentEvent` payloads and rejects unsupported event types.

Run:

```powershell
python -m pytest tests/test_sandbox_contracts.py tests/test_sandbox_executor_client.py -q --basetemp .pytest-tmp
```

Expected: fail because `ExecutorCallbackEvent` currently forbids the `events`
field.

- [ ] **Step 2: Implement the contract**

Import `AgentEvent` in `contracts.py` and add:

```python
events: list[AgentEvent] = Field(default_factory=list)
```

In `callback_event_to_run_events()`, append `callback.events` after the
existing compatibility-derived events.

- [ ] **Step 3: Verify contract tests pass**

Run:

```powershell
python -m pytest tests/test_sandbox_contracts.py tests/test_sandbox_executor_client.py -q --basetemp .pytest-tmp
```

Expected: all selected tests pass.

### Task 2: Persist Typed Events Through The Stage Map

**Files:**
- Modify: `app/routes/runtime_callbacks.py`
- Test: `tests/test_runtime_callbacks.py`
- Reference: `app/runtime/event_bridge.py`

- [ ] **Step 1: Write failing persistence tests**

Add route-level tests proving that a callback with `checkpoint_created`,
`subagent_started`, and `agent_step_completed` writes stages
`checkpoint`, `subagent`, and `agent`; add a test proving `admin_only=true`
forces `visible_to_user=false`.

Run:

```powershell
python -m pytest tests/test_runtime_callbacks.py -q --basetemp .pytest-tmp
```

Expected: fail because runtime callback persistence currently writes
normalized events with `stage="executor"` and does not accept `events`.

- [ ] **Step 2: Implement stage-map persistence**

Import `agent_event_to_executor_event` in `runtime_callbacks.py`. For every
normalized `AgentEvent`, call it and persist the returned `event_type`, `stage`,
`message`, and `payload`, adding `source="executor_callback"` to the payload.

- [ ] **Step 3: Verify persistence tests pass**

Run:

```powershell
python -m pytest tests/test_runtime_callbacks.py -q --basetemp .pytest-tmp
```

Expected: all route callback tests pass.

### Task 3: Source Authority And Roadmap Evidence

**Files:**
- Modify: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`
- Test: `tests/test_source_authority_docs.py`

- [ ] **Step 1: Add started status**

Add a P2 roadmap section recording this as a typed callback event contract.
Do not claim 211 deployment until it is deployed and smoked.

- [ ] **Step 2: Verify docs tests**

Run:

```powershell
python -m pytest tests/test_source_authority_docs.py -q --basetemp .pytest-tmp
```

Expected: `8 passed`.

### Task 4: Review, Full Verification, Deployment

**Files:**
- Review current branch diff against `main`.

- [ ] **Step 1: Compile**

Run:

```powershell
python -m compileall -q app tools scripts
```

Expected: exit 0.

- [ ] **Step 2: Focused tests**

Run:

```powershell
python -m pytest tests/test_sandbox_contracts.py tests/test_sandbox_executor_client.py tests/test_runtime_callbacks.py tests/test_embedded_poco_adapter.py tests/test_source_authority_docs.py -q --basetemp .pytest-tmp
```

Expected: all selected tests pass.

- [ ] **Step 3: Full tests**

Run:

```powershell
python -m pytest -q --basetemp .pytest-tmp
```

Expected: full suite passes.

- [ ] **Step 4: Inherited-configuration review**

Dispatch a review agent if the available tool can inherit the current
filesystem/network/approval posture. Record that no explicit model gate is
externally asserted if model and reasoning fields are unavailable.

- [ ] **Step 5: Commit, push, deploy, smoke**

Commit the feature and docs, push the feature branch or `main` per current repo
workflow, deploy on 211 only, and smoke typed callback event persistence plus
cleanup.
