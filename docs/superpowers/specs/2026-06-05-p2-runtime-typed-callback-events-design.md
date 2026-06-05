# P2 Runtime Typed Callback Events Design

Add a small internal runtime callback contract so sandbox executors can submit
standard `AgentEvent` entries for checkpoint, subagent, and agent-step progress.
This advances P2 Long Task / Multi-Agent auditability without enabling
autonomous dispatch, risky tools, retry scheduling, or new sandbox behavior.

## Contract

`ExecutorCallbackEvent` keeps the existing compatibility fields:

- `status`
- `progress`
- `new_message`
- `state_patch`
- `sdk_session_id`
- `error_message`

It also accepts:

```json
{
  "events": [
    {
      "type": "subagent_started",
      "message": "reviewer started",
      "payload": {
        "subagent_id": "reviewer-1",
        "step_key": "review",
        "step_index": 2
      },
      "admin_only": false
    }
  ]
}
```

Each item is validated as `AgentEvent`, so unsupported event types or malformed
event payloads fail closed at request validation.

## Persistence

`record_executor_callback()` continues to write one admin-only
`executor_callback` envelope event per accepted callback. For normalized
runtime events it uses `agent_event_to_executor_event()`, so stage mapping and
visibility are centralized:

- `checkpoint_created` -> `checkpoint`
- `subagent_started`, `subagent_completed`, `subagent_failed` -> `subagent`
- `agent_step_*` -> `agent`
- `run_completed`, `run_failed` -> `runtime`
- `run_cancelled` -> `control`

`admin_only=true` on an event forces `visible_to_user=false` and records
`admin_only=true` in the event payload. Non-admin events default to
`visible_to_user=true`.

## Non-Goals

- No autonomous subagent dispatch.
- No multi-agent scheduler.
- No retry/dead-letter scheduler.
- No new sandbox provider behavior.
- No ordinary-user writable event endpoint.
- No exposure of real `.env`, callback tokens, runtime private payload, storage
  keys, or worker paths.

## Verification

Local focused verification:

```powershell
python -m pytest tests/test_sandbox_contracts.py tests/test_sandbox_executor_client.py tests/test_runtime_callbacks.py tests/test_embedded_poco_adapter.py -q --basetemp .pytest-tmp
python -m pytest tests/test_source_authority_docs.py -q --basetemp .pytest-tmp
```

Full verification before commit:

```powershell
python -m compileall -q app tools scripts
python -m pytest -q --basetemp .pytest-tmp
```

211 smoke after deployment should verify health, image labels, OpenAPI
availability, a typed callback containing `checkpoint_created`,
`subagent_started`, and `agent_step_completed`, expected persisted stages,
ordinary-user redaction, and DB/Redis smoke cleanup.
