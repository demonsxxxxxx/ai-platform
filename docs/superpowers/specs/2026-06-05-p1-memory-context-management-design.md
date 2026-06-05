# P1 Memory / Context Management Design

## Goal

Close the next P1 Memory / Context backend management slice so ai-platform can
move from P0 storage contracts toward an operable Agent platform. The slice
adds ordinary-user memory policy self-management and an admin policy inventory
projection while preserving the existing fail-closed stance for cross-session
long-term memory.

This is not the full Memory UI and it does not open Long Task / Multi-Agent
Runtime work. It creates the public/admin API projections that the later Agent
Frontend V1 stage can consume.

## Source Constraints

Use these sources as authority:

- Current `main` baseline and feature branch
  `codex/p1-memory-context-management`.
- `docs/superpowers/specs/2026-05-29-ai-platform-final-product-prd.md`.
- `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`.
- `docs/agent-rules/ai-platform-guardrails.md`.
- Current `app/routes/context.py`, `app/repositories.py`, `app/models.py`,
  `app/schema.sql`, and focused tests.
- Fresh 211 runtime evidence before stage completion.

The Windows workstation does not run Docker. Local validation uses Python
compile and pytest with `--basetemp .pytest-tmp`. Docker compose, image build,
container restart, and runtime smoke checks happen only on 211 or another
Docker-capable host.

## Current Baseline

The current code already provides P0-level Memory / Context contracts:

- Context snapshot create/list and worker-scoped snapshot loading.
- Session-bound memory record create/list/delete.
- Admin memory policy set by target user.
- Admin retention cleanup.
- Admin memory record operational projection.
- Redaction for context payload, memory content, memory metadata, and audit
  reason fields.
- Schema constraints that require `memory_records.session_id` and `agent_id`.
- Schema and repository clamps that keep `long_term_memory_enabled = false`.

The P1 management gap is that ordinary users can read but cannot update their
own memory policy, and admins can set a single user's policy but cannot list
same-tenant policy state for operational management.

## API Contract

Add:

```text
PUT /api/ai/memory/policy
```

Request body reuses `MemoryPolicyRequest`.

Access rules:

- Requires `require_principal`.
- Only updates `principal.user_id` in `principal.tenant_id`.
- `long_term_memory_enabled = true` returns `409 long_term_memory_not_available`.
- If `agent_id` is supplied, it must resolve to an agent in the same tenant.
- Public agent ids such as `document-review` are normalized to the internal
  agent id for storage and lookup, then projected back to the public id in
  responses and audit metadata.
- Existing session-scoped memory record endpoints must follow the same
  public-to-internal routing before session/policy lookup and must keep record
  responses plus audit metadata projected back to public agent ids.
- The response uses the existing public `_memory_policy_response` projection.
- The audit event is public-safe and contains only tenant/workspace/user/agent
  policy metadata, not memory content or executor private payload.

Response shape:

```json
{
  "memory_policy": {
    "tenant_id": "default",
    "workspace_id": "default",
    "user_id": "user-a",
    "agent_id": "general-agent",
    "memory_enabled": false,
    "long_term_memory_enabled": false,
    "retention_days": 30,
    "source": "stored",
    "reason": "user opt-out",
    "updated_by": "user-a",
    "updated_at": "2026-06-05T00:00:00Z"
  }
}
```

Add:

```text
GET /api/ai/admin/memory/policies
```

Query parameters:

- `workspace_id`: safe id, default `default`.
- `user_id`: optional safe id filter.
- `agent_id`: optional safe id filter.
- `limit`: `1..500`, default `50`.

Access rules:

- Requires `require_principal`.
- Requires the existing memory admin role check.
- Non-admin callers receive `403 not_ai_memory_admin`.
- All rows are scoped to `principal.tenant_id`.
- The response uses the same public policy projection used by the ordinary-user
  route; no secret-like payload or executor private payload is returned.
- Optional `agent_id` filters accept public ids and query the corresponding
  internal agent id.

Response shape:

```json
{
  "memory_policies": [],
  "summary": {
    "workspace_id": "default",
    "returned_count": 0,
    "limit": 50
  }
}
```

## Data Flow

1. Ordinary user policy update validates workspace, optional agent, and
   long-term memory flag before writing.
2. The route calls existing `repositories.set_memory_policy` with
   `user_id = principal.user_id` and `updated_by = principal.user_id`.
3. The route writes `memory.policy.updated` audit metadata with redacted
   `reason`.
4. Admin policy inventory validates admin role and safe query ids.
5. The route calls a new repository helper,
   `list_admin_memory_policies(conn, *, tenant_id, workspace_id, user_id,
   agent_id, limit)`.
6. The helper returns stored policy rows only. Missing policies continue to be
   represented by `GET /memory/policy` effective defaults; the inventory is for
   operational state, not implicit default expansion.

## Repository Helper

Add:

```python
async def list_admin_memory_policies(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str | None = None,
    agent_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    # Return stored, same-tenant policy rows as public-safe policy objects.
```

The SQL must:

- Filter by `tenant_id` and `workspace_id`.
- Apply optional `user_id` and `agent_id` filters.
- Clamp `limit` to `1..500`.
- Order by `updated_at desc, created_at desc`.
- Return only policy columns needed by `_memory_policy_response`.
- Pass rows through `_memory_policy_from_row`, which already clamps
  `long_term_memory_enabled` to `False`.

## Error Handling

- Unsafe ids return `422` before repository access.
- Missing workspace returns `404 workspace_not_found`.
- Missing or foreign optional agent returns `404 agent_not_found`.
- Long-term memory enable attempts return `409 long_term_memory_not_available`
  before persistence.
- Repository conflicts remain mapped to `409` where existing routes already do
  so.

## Testing

Focused tests cover:

- Ordinary user `PUT /memory/policy` updates only the caller's policy and writes
  redacted audit metadata.
- Ordinary user cannot enable long-term memory.
- Ordinary user policy read/update returns `404 workspace_not_found` for a
  missing or foreign workspace before policy repository access.
- Ordinary user update returns `404` for missing or foreign optional agent.
- Ordinary user update rejects unsafe body ids with `422` before repository
  writes.
- Session-scoped memory record create/list/delete accepts public agent ids,
  maps them to internal agent ids for session/policy/repository operations, and
  does not expose internal agent ids in responses or audit metadata.
- Admin `GET /admin/memory/policies` requires memory-admin role.
- Admin policy inventory calls the repository with `principal.tenant_id` and
  returns sanitized public policy projections.
- Admin policy inventory returns `404 workspace_not_found` for a missing or
  foreign workspace before inventory repository access.
- Admin policy inventory rejects unsafe query ids before repository access.
- Repository `list_admin_memory_policies` is tenant-scoped, clamps limit, and
  clamps legacy long-term rows to `False`.

Local verification before commit:

```powershell
python -m compileall -q app tools scripts
python -m pytest tests/test_context_routes.py tests/test_repositories.py tests/test_schema.py -q --basetemp .pytest-tmp\run-p1-memory-focused
python -m pytest -q --basetemp .pytest-tmp\run-p1-memory-full
```

211 verification after review and deployment:

- `GET /api/ai/health` returns `200`.
- `PUT /api/ai/memory/policy` returns `200` for an ordinary user and the
  response keeps `long_term_memory_enabled = false`.
- `GET /api/ai/admin/memory/policies` returns `403` for an ordinary user.
- `GET /api/ai/admin/memory/policies` returns `200` for an admin principal.
- Smoke responses contain no real `.env` values, secrets, raw memory content,
  executor private payload, storage keys, or runtime paths.

## Out Of Scope

- No cross-session long-term memory reads.
- No background scheduler for retention cleanup. Existing admin-triggered
  cleanup remains the P1 backend control.
- No new frontend route or parallel frontend entry in this slice.
- No configurable redaction policy engine; existing redaction is preserved.
- No Long Task / Multi-Agent Runtime implementation.
