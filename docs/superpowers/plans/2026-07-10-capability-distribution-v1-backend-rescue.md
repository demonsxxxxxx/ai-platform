# Capability Distribution V1 Backend Rescue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the current-main backend authority that lets AI administrators distribute Skills and MCP servers by department and role while enforcing the same decision at discovery, enqueue, worker execution, and MCP registration.

**Architecture:** Add one pure capability resolver over a sole `tenant_capability_distributions` repository authority, then adapt current Skill, Marketplace, MCP, run, and worker seams to consume it. Persist principal department, normalized roles, and auth source on the run so the worker can re-fetch current distribution state and fail closed without trusting queue payload fields.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, psycopg async repository functions, PostgreSQL schema SQL, pytest, existing Redis queue and worker contracts.

## Global Constraints

- Base is `origin/main` commit `124a09c39290bb3bf39d9b13bd2fa1bd632a5040` plus the approved design commit.
- Use the historical `097d839..6a77c37` range as behavioral reference only; do not merge or broadly cherry-pick it.
- `tenant_capability_distributions` is the only post-cutover department and role distribution authority.
- Legacy Skill and MCP scope fields are idempotent backfill inputs only and never read fallbacks.
- `mcp_tool` inherits its parent `mcp_server` distribution.
- Queue payload schemas remain unchanged; worker authorization context comes from the locked run record.
- Preserve ordinary-user Marketplace install and update behavior unless the operation explicitly manages shared distribution.
- Preserve existing MCP lifecycle and risk/write policy as additional gates.
- Role comparisons use normalized role names.
- Missing, hidden, disabled, wrong-department, and wrong-role distributions fail closed.
- Do not modify sandbox, Release Authority, B1, B2 readiness, B3, frontend, deploy, compose, Ruleset, or 211 paths.
- Every local pytest command uses a fresh child under `--basetemp .pytest-tmp`.

## File Map

- Create `app/capability_distribution.py`: pure access context, subject, decision, normalization, resolver, and audit projection.
- Create `app/routes/capability_distributions.py`: AI-admin-only management API.
- Modify `app/schema.sql`: distribution table plus persisted run department field.
- Modify `app/models.py`: Admin API request and response models only; do not add authorization fields to `QueueRunPayload`.
- Modify `app/repositories.py`: backfill, distribution CRUD, MCP lookup, run snapshot persistence, locked-run projection, and child inheritance.
- Modify `app/main.py`: register the Admin Capability Distribution router.
- Modify `app/routes/skills_marketplace.py`: Skill and Marketplace visibility/detail/write cutover.
- Modify `app/routes/role_governance.py`: projected Skill availability reads the unified distribution authority.
- Modify `app/routes/mcp.py`: MCP list/detail/tools cutover, administrator bypass audit, and shared-write guard.
- Modify `app/routes/chat.py`: enqueue-time Skill/MCP authorization and run snapshot persistence.
- Modify `app/routes/runs.py`: create/copy/retry/dispatch authorization and snapshot persistence.
- Modify `app/worker.py`: locked-run Skill/MCP reauthorization, denial events/audit, and authorized MCP registration input.
- Create `tests/test_capability_distribution.py`: pure resolver coverage.
- Create `tests/test_capability_distribution_routes.py`: Admin API coverage.
- Modify focused existing test modules named in the tasks below.

---

### Task 1: Distribution Authority Foundation

**Files:**
- Create: `app/capability_distribution.py`
- Modify: `app/schema.sql`
- Modify: `app/repositories.py`
- Create: `tests/test_capability_distribution.py`
- Modify: `tests/test_schema.py`
- Modify: `tests/test_repositories.py`

**Interfaces:**
- Produces: `CapabilityAccessContext`, `CapabilityDistributionSubject`, `CapabilityAccessDecision`, `resolve_capability_access(...)`, and `capability_distribution_audit_payload(...)`.
- Produces: `ensure_tenant_capability_distribution_backfill(...)`, `list_capability_distribution_rows(...)`, `get_capability_distribution_row(...)`, `upsert_capability_distribution_row(...)`, and `toggle_capability_distribution_row(...)`.
- Consumes: existing `AsyncConnection`, JSON helpers, ID generation, and `RepositoryNotFoundError` patterns from `app/repositories.py`.

- [ ] **Step 1: Add failing resolver tests**

```python
def test_resolver_normalizes_roles_and_requires_department_and_role():
    context = CapabilityAccessContext(
        tenant_id="default",
        department_id="qa",
        roles=["QA_OPERATOR"],
    )
    subject = CapabilityDistributionSubject(
        capability_kind="skill",
        capability_id="qa-review",
        lifecycle_status="active",
        distribution={
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["qa"],
            "allowed_roles": ["qa_operator"],
        },
    )

    assert resolve_capability_access(context, subject, "use").usable is True


@pytest.mark.parametrize(
    ("distribution", "reason"),
    [
        (None, "distribution_missing"),
        ({"status": "disabled", "visible_to_user": True}, "distribution_disabled"),
        ({"status": "active", "visible_to_user": False}, "distribution_hidden"),
        ({"status": "active", "visible_to_user": True, "department_ids": ["rd"]}, "department_not_allowed"),
        ({"status": "active", "visible_to_user": True, "allowed_roles": ["manager"]}, "role_not_allowed"),
    ],
)
def test_resolver_fails_closed(distribution, reason):
    decision = resolve_capability_access(
        CapabilityAccessContext("default", "qa", ["user"]),
        CapabilityDistributionSubject("skill", "qa-review", distribution=distribution),
        "use",
    )
    assert decision.usable is False
    assert decision.decision_reason == reason
```

- [ ] **Step 2: Run the resolver tests red**

Run: `python -m pytest tests/test_capability_distribution.py -q -p no:cacheprovider --basetemp .pytest-tmp\capdist-task1-red`

Expected: FAIL because `app.capability_distribution` does not exist.

- [ ] **Step 3: Implement the pure normalized resolver**

```python
CapabilityAccessIntent = Literal["discover", "use", "manage"]


def normalize_capability_roles(roles: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(role).strip().lower() for role in roles if str(role).strip()))


def resolve_capability_access(
    context: CapabilityAccessContext,
    subject: CapabilityDistributionSubject,
    intent: CapabilityAccessIntent,
) -> CapabilityAccessDecision:
    if subject.distribution is None:
        return _deny(subject, "distribution_missing")
    if subject.lifecycle_status != "active":
        return _deny(subject, "lifecycle_denied")
    if context.is_admin:
        return _allow(subject, "admin_bypass", admin_bypass=True)
    if intent == "manage":
        return _deny(subject, "manage_admin_required")
    if not subject.visible_to_user:
        return _deny(subject, "distribution_hidden")
    if subject.status != "active":
        return _deny(subject, "distribution_disabled")
    if subject.department_ids and context.department_id not in subject.department_ids:
        return _deny(subject, "department_not_allowed")
    if subject.allowed_roles and not set(normalize_capability_roles(context.roles)).intersection(subject.allowed_roles):
        return _deny(subject, "role_not_allowed")
    return _allow(subject, "allowed", admin_bypass=False)
```

- [ ] **Step 4: Add failing schema and repository tests**

```python
def test_schema_defines_capability_distribution_authority():
    schema = read_schema().lower()
    assert "create table if not exists tenant_capability_distributions" in schema
    assert "unique (tenant_id, capability_kind, capability_id)" in schema
    assert "check (capability_kind in ('skill', 'mcp_server'))" in schema


async def test_distribution_backfill_is_insert_only_and_binds_every_placeholder():
    conn = RecordingConnection()
    await repositories.ensure_tenant_capability_distribution_backfill(conn, tenant_id="default")
    assert len(conn.calls) == 2
    for sql, params in conn.calls:
        assert "on conflict (tenant_id, capability_kind, capability_id) do nothing" in sql.lower()
        assert sql.count("%s") == len(params)
```

- [ ] **Step 5: Run repository and schema tests red**

Run: `python -m pytest tests/test_schema.py tests/test_repositories.py -q -p no:cacheprovider -k "capability_distribution or principal_department" --basetemp .pytest-tmp\capdist-task1-repo-red`

Expected: FAIL because the schema and repository APIs do not exist.

- [ ] **Step 6: Implement table, backfill, projection, CRUD, and indexes**

```python
async def get_capability_distribution_row(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    capability_kind: str,
    capability_id: str,
) -> dict[str, Any] | None:
    await ensure_tenant_capability_distribution_backfill(conn, tenant_id=tenant_id)
    cursor = await conn.execute(
        """
        select id, tenant_id, capability_kind, capability_id, status,
               visible_to_user, scope_mode, department_ids, allowed_roles,
               metadata_json, updated_by, created_at, updated_at
        from tenant_capability_distributions
        where tenant_id = %s and capability_kind = %s and capability_id = %s
        """,
        (tenant_id, capability_kind, capability_id),
    )
    row = await cursor.fetchone()
    return _capability_distribution_projection(dict(row)) if row else None
```

The two backfill inserts copy Skill visibility/status and MCP status/department/role scope, use deterministic IDs, and use `ON CONFLICT ... DO NOTHING`. CRUD reads only the new table after ensuring backfill.

- [ ] **Step 7: Run Task 1 tests green**

Run: `python -m pytest tests/test_capability_distribution.py tests/test_schema.py tests/test_repositories.py -q -p no:cacheprovider -k "capability_distribution or principal_department" --basetemp .pytest-tmp\capdist-task1-green`

Expected: all selected tests PASS.

- [ ] **Step 8: Commit the authority foundation**

```powershell
git add app/capability_distribution.py app/schema.sql app/repositories.py tests/test_capability_distribution.py tests/test_schema.py tests/test_repositories.py
git commit -m "feat: add capability distribution authority"
```

---

### Task 2: AI Admin Management API

**Files:**
- Create: `app/routes/capability_distributions.py`
- Modify: `app/models.py`
- Modify: `app/main.py`
- Create: `tests/test_capability_distribution_routes.py`

**Interfaces:**
- Consumes: Task 1 repository CRUD, resolver, `AuthPrincipal`, `is_ai_admin`, `assert_safe_id`, `transaction`, and `append_audit_log`.
- Produces: `GET /api/admin/capability-distributions`, detail GET, PUT, and PATCH toggle.

- [ ] **Step 1: Add failing Admin route tests**

```python
def test_admin_updates_department_distribution_and_audits_target_scope(monkeypatch):
    calls = install_distribution_fakes(monkeypatch)
    response = TestClient(create_app()).put(
        "/api/admin/capability-distributions/skill/qa-review",
        headers=admin_headers(department_id="rd"),
        json={
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["qa", "qa"],
            "allowed_roles": ["QA_OPERATOR", "qa_operator"],
            "metadata_json": {},
        },
    )
    assert response.status_code == 200
    assert response.json()["distribution"]["department_ids"] == ["qa"]
    assert response.json()["distribution"]["allowed_roles"] == ["qa_operator"]
    assert calls.audit["payload_json"]["actor_department_id"] == "rd"
    assert calls.audit["payload_json"]["department_scope_ids"] == ["qa"]


def test_ordinary_user_cannot_manage_distribution(monkeypatch):
    response = TestClient(create_app()).put(
        "/api/admin/capability-distributions/skill/qa-review",
        headers=user_headers(),
        json=valid_distribution_payload(),
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "capability_distribution_admin_required"
```

- [ ] **Step 2: Run Admin route tests red**

Run: `python -m pytest tests/test_capability_distribution_routes.py -q -p no:cacheprovider --basetemp .pytest-tmp\capdist-task2-red`

Expected: FAIL with missing route or missing model symbols.

- [ ] **Step 3: Add strict request and response models**

```python
class CapabilityDistributionUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["active", "disabled"] = "active"
    visible_to_user: bool = True
    scope_mode: Literal["allowlist"] = "allowlist"
    department_ids: list[str] = Field(default_factory=list)
    allowed_roles: list[str] = Field(default_factory=list)
    metadata_json: dict[str, Any] = Field(default_factory=dict)

    @field_validator("department_ids", "allowed_roles")
    @classmethod
    def normalize_scope_lists(cls, value: list[str], info):
        normalized = [assert_safe_id(str(item).strip().lower(), info.field_name) for item in value]
        return list(dict.fromkeys(normalized))
```

- [ ] **Step 4: Implement and register the Admin router**

```python
@router.put(
    "/admin/capability-distributions/{capability_kind}/{capability_id}",
    response_model=CapabilityDistributionWriteResponse,
)
async def update_capability_distribution(
    capability_kind: str,
    capability_id: str,
    request: CapabilityDistributionUpdateRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> CapabilityDistributionWriteResponse:
    _require_admin(principal)
    # Validate kind, ID, and capability existence; upsert and append audit in one transaction.
```

Use dotted audit actions `capability_distribution.updated` and `capability_distribution.toggled`. Unknown capabilities and missing toggle rows return controlled `404` responses.

- [ ] **Step 5: Run Admin route tests green**

Run: `python -m pytest tests/test_capability_distribution_routes.py -q -p no:cacheprovider --basetemp .pytest-tmp\capdist-task2-green`

Expected: all tests PASS.

- [ ] **Step 6: Commit the Admin API**

```powershell
git add app/routes/capability_distributions.py app/models.py app/main.py tests/test_capability_distribution_routes.py
git commit -m "feat: add capability distribution admin API"
```

---

### Task 3: Skill and Marketplace Cutover

**Files:**
- Modify: `app/routes/skills_marketplace.py`
- Modify: `app/routes/role_governance.py`
- Modify: `tests/test_skills_marketplace_routes.py`
- Modify: `tests/test_role_governance_routes.py`

**Interfaces:**
- Consumes: Task 1 resolver and distribution list/detail repository APIs.
- Produces: fail-closed Skill/Marketplace projections and role-governance availability derived from the same authority.

- [ ] **Step 1: Add failing visibility and write-boundary tests**

```python
def test_marketplace_allows_same_department_and_hides_cross_department(monkeypatch):
    install_skill_catalog(monkeypatch, skill_id="qa-review")
    install_distribution(monkeypatch, capability_id="qa-review", department_ids=["qa"])
    qa = TestClient(create_app()).get("/api/marketplace", headers=user_headers("qa"))
    rd = TestClient(create_app()).get("/api/marketplace", headers=user_headers("rd"))
    assert [item["skill_id"] for item in qa.json()["items"]] == ["qa-review"]
    assert rd.json()["items"] == []


def test_marketplace_role_deny_and_disabled_deny(monkeypatch):
    install_distribution(monkeypatch, allowed_roles=["qa_operator"], status="disabled")
    response = TestClient(create_app()).get("/api/marketplace", headers=user_headers("qa", roles="user"))
    assert response.json()["items"] == []


def test_ordinary_marketplace_install_path_remains_available(monkeypatch):
    response = TestClient(create_app()).post(
        "/api/marketplace/qa-review/install",
        headers=user_headers("qa"),
        json=valid_install_request(),
    )
    assert response.status_code != 403
```

- [ ] **Step 2: Run Skill/Marketplace tests red**

Run: `python -m pytest tests/test_skills_marketplace_routes.py tests/test_role_governance_routes.py -q -p no:cacheprovider -k "distribution or department or marketplace" --basetemp .pytest-tmp\capdist-task3-red`

Expected: new distribution tests FAIL while existing ordinary install behavior remains the baseline.

- [ ] **Step 3: Add a route-local decision adapter and cut reads over**

```python
def _skill_decision(
    principal: AuthPrincipal,
    skill: dict[str, Any],
    distribution: dict[str, Any] | None,
    *,
    intent: CapabilityAccessIntent,
) -> CapabilityAccessDecision:
    return resolve_capability_access(
        capability_access_context(principal),
        CapabilityDistributionSubject(
            capability_kind="skill",
            capability_id=str(skill.get("skill_id") or ""),
            lifecycle_status=str(skill.get("status") or "disabled"),
            distribution=distribution,
        ),
        intent,
    )
```

List routes filter unauthorized rows; known unauthorized detail/file reads return `404`. Role governance reads Skill rows from `list_capability_distribution_rows(..., capability_kind="skill")` and never from legacy department scope.

- [ ] **Step 4: Guard only shared tenant lifecycle writes**

Use `is_ai_admin(principal)` for endpoints that activate, disable, toggle, or delete shared tenant capability state. Do not add this guard to an ordinary-user install/update operation whose current contract only changes that user's installation state.

- [ ] **Step 5: Run full focused Skill/Marketplace tests green**

Run: `python -m pytest tests/test_skills_marketplace_routes.py tests/test_role_governance_routes.py -q -p no:cacheprovider --basetemp .pytest-tmp\capdist-task3-green`

Expected: all tests PASS.

- [ ] **Step 6: Commit Skill and Marketplace cutover**

```powershell
git add app/routes/skills_marketplace.py app/routes/role_governance.py tests/test_skills_marketplace_routes.py tests/test_role_governance_routes.py
git commit -m "feat: enforce skill capability distribution"
```

---

### Task 4: MCP Read, Write, and Registration Inputs

**Files:**
- Modify: `app/repositories.py`
- Modify: `app/routes/mcp.py`
- Modify: `tests/test_repositories.py`
- Modify: `tests/test_mcp_routes.py`

**Interfaces:**
- Consumes: Task 1 resolver and MCP server distribution rows.
- Produces: `get_mcp_tool_registry_entry(...)`, an unfiltered administrative registry read, and public MCP list/detail/tools filtered through inherited server distribution.

- [ ] **Step 1: Add failing MCP inheritance tests**

```python
def test_mcp_tools_inherit_parent_server_distribution(monkeypatch):
    install_mcp_server(monkeypatch, server_id="qa-mcp", tool_ids=["qa.search"])
    install_distribution(monkeypatch, capability_id="qa-mcp", department_ids=["qa"])
    qa = TestClient(create_app()).get("/api/mcp/qa-mcp/tools", headers=user_headers("qa"))
    rd = TestClient(create_app()).get("/api/mcp/qa-mcp/tools", headers=user_headers("rd"))
    assert [item["id"] for item in qa.json()["items"]] == ["qa.search"]
    assert rd.status_code == 404


def test_mcp_registered_tools_exclude_unauthorized_server(monkeypatch):
    servers = authorized_mcp_servers_for_principal(
        principal=user_principal("rd"),
        registry=[mcp_server("qa-mcp")],
        distributions=[distribution("qa-mcp", departments=["qa"])],
    )
    assert servers == []
```

- [ ] **Step 2: Run MCP tests red**

Run: `python -m pytest tests/test_mcp_routes.py tests/test_repositories.py -q -p no:cacheprovider -k "mcp and (distribution or department or registry)" --basetemp .pytest-tmp\capdist-task4-red`

Expected: new inherited-distribution tests FAIL.

- [ ] **Step 3: Implement MCP repository lookups and route filtering**

```python
async def get_mcp_tool_registry_entry(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    tool_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select tools.id, tools.server_id, tools.status, tools.write_capable,
               tools.risk_level
        from mcp_tools tools
        join mcp_servers servers on servers.id = tools.server_id
        where servers.tenant_id = %s and tools.id = %s
        """,
        (tenant_id, tool_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None
```

Public list/detail/tools resolve parent `mcp_server` distribution. Administrator bypass reads emit `capability_distribution.admin_bypass` audit. Shared server create/update/enablement remains AI-admin-only. Existing lifecycle and risk/write policy checks stay after distribution allow.

- [ ] **Step 4: Run MCP tests green**

Run: `python -m pytest tests/test_mcp_routes.py tests/test_repositories.py -q -p no:cacheprovider -k "mcp or capability_distribution" --basetemp .pytest-tmp\capdist-task4-green`

Expected: all selected tests PASS.

- [ ] **Step 5: Commit MCP cutover**

```powershell
git add app/repositories.py app/routes/mcp.py tests/test_repositories.py tests/test_mcp_routes.py
git commit -m "feat: enforce MCP capability distribution"
```

---

### Task 5: Enqueue Authorization and Run Snapshot

**Files:**
- Modify: `app/schema.sql`
- Modify: `app/repositories.py`
- Modify: `app/routes/chat.py`
- Modify: `app/routes/runs.py`
- Modify: `tests/test_schema.py`
- Modify: `tests/test_repositories.py`
- Modify: `tests/test_chat_routes.py`
- Modify: `tests/test_routes.py`
- Modify: `tests/test_run_control_routes.py`

**Interfaces:**
- Consumes: Task 1 resolver and Task 4 tool-to-server lookup.
- Produces: persisted `principal_department_id`, normalized `principal_roles`, and `auth_source` on runs; enqueue guards shared by chat and run creation; child-run snapshot inheritance.

- [ ] **Step 1: Add failing enqueue and persistence tests**

```python
def test_enqueue_rejects_known_cross_department_skill_before_run_creation(monkeypatch):
    calls = install_run_fakes(monkeypatch, skill_department_ids=["qa"])
    response = TestClient(create_app()).post(
        "/api/ai/runs",
        headers=user_headers("rd"),
        json=run_request(skill_id="qa-review"),
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "capability_not_authorized"
    assert calls.create_run == 0


def test_create_run_persists_authorization_snapshot():
    await repositories.create_run(
        conn,
        tenant_id="default",
        user_id="qa-user",
        skill_id="qa-review",
        input_json={},
        principal_roles=["QA_OPERATOR"],
        principal_department_id="qa",
        auth_source="trusted_headers",
    )
    assert recorded_params.principal_roles == ["qa_operator"]
    assert recorded_params.principal_department_id == "qa"
    assert recorded_params.auth_source == "trusted_headers"
```

- [ ] **Step 2: Run enqueue/snapshot tests red**

Run: `python -m pytest tests/test_schema.py tests/test_repositories.py tests/test_chat_routes.py tests/test_routes.py tests/test_run_control_routes.py -q -p no:cacheprovider -k "capability_distribution or auth_snapshot or principal_department or child_run" --basetemp .pytest-tmp\capdist-task5-red`

Expected: new snapshot and enqueue tests FAIL.

- [ ] **Step 3: Persist run authorization context**

```python
async def update_run_auth_snapshot(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    principal_roles: list[str] | None,
    principal_department_id: str,
    auth_source: str | None,
) -> None:
    await conn.execute(
        """
        update runs
        set principal_roles = %s::jsonb,
            principal_department_id = %s,
            auth_source = %s
        where tenant_id = %s and id = %s
        """,
        (
            dumps_json(normalize_capability_roles(principal_roles or [])),
            principal_department_id,
            auth_source,
            tenant_id,
            run_id,
        ),
    )
```

Add `principal_department_id text not null default ''` idempotently. Extend create, lock, copy, retry, and multi-agent child projections without modifying `QueueRunPayload`.

- [ ] **Step 4: Add shared enqueue helpers and call them before run creation**

```python
async def require_requested_capabilities_authorized(
    conn,
    *,
    principal: AuthPrincipal,
    skill: dict[str, Any],
    skill_id: str,
    raw_input: dict[str, Any],
) -> None:
    await require_skill_authorized(conn, principal=principal, skill=skill, skill_id=skill_id)
    for tool_id in string_list(raw_input.get("mcp_tool_ids")):
        await require_mcp_tool_authorized(conn, principal=principal, tool_id=tool_id)
```

Call this helper from chat-stream and explicit run creation before `repositories.create_run`. Copied/retried/child runs restore department, roles, and source from the persisted parent row before queue preparation.

- [ ] **Step 5: Run enqueue/snapshot tests green**

Run: `python -m pytest tests/test_schema.py tests/test_repositories.py tests/test_chat_routes.py tests/test_routes.py tests/test_run_control_routes.py -q -p no:cacheprovider -k "capability_distribution or auth_snapshot or principal_department or child_run" --basetemp .pytest-tmp\capdist-task5-green`

Expected: all selected tests PASS; the known unrelated sandbox cancel baseline remains excluded by the expression.

- [ ] **Step 6: Commit enqueue and snapshot support**

```powershell
git add app/schema.sql app/repositories.py app/routes/chat.py app/routes/runs.py tests/test_schema.py tests/test_repositories.py tests/test_chat_routes.py tests/test_routes.py tests/test_run_control_routes.py
git commit -m "feat: snapshot capability authorization at enqueue"
```

---

### Task 6: Worker Reauthorization and MCP Registration

**Files:**
- Modify: `app/worker.py`
- Modify: `tests/test_worker.py`
- Modify: `tests/test_claude_agent_worker_adapter.py`

**Interfaces:**
- Consumes: locked run fields from Task 5, Task 1 resolver, and Task 4 MCP tool-to-server lookup.
- Produces: worker-time Skill and MCP denial before executor invocation, denial audit/events, and an MCP registration set limited to currently authorized servers/tools.

- [ ] **Step 1: Add failing post-enqueue revocation tests**

```python
async def test_worker_rejects_skill_disabled_after_enqueue(monkeypatch):
    harness = worker_harness(
        locked_run=locked_run(department_id="qa", roles=["qa_operator"]),
        skill_distribution=distribution(status="disabled", departments=["qa"]),
    )
    outcome = await process_run_payload(harness.queue_payload, harness.executor)
    assert outcome.status == "failed"
    assert outcome.error_code == "capability_not_authorized"
    assert harness.executor.calls == []
    assert harness.audit.action == "capability_distribution.denied"


async def test_worker_rejects_mcp_redistributed_after_enqueue(monkeypatch):
    harness = worker_harness(
        locked_run=locked_run(department_id="qa", roles=["qa_operator"]),
        mcp_distribution=distribution(departments=["rd"]),
        requested_tool="qa.search",
    )
    outcome = await process_run_payload(harness.queue_payload, harness.executor)
    assert outcome.error_code == "capability_not_authorized"
    assert harness.registered_tools == []
```

- [ ] **Step 2: Run worker tests red**

Run: `python -m pytest tests/test_worker.py tests/test_claude_agent_worker_adapter.py -q -p no:cacheprovider -k "capability_distribution or authorization_snapshot or registered_tools" --basetemp .pytest-tmp\capdist-task6-red`

Expected: new worker reauthorization tests FAIL.

- [ ] **Step 3: Reconstruct context only from the locked run**

```python
def capability_access_context_from_locked_run(payload: QueueRunPayload, locked_run: object) -> CapabilityAccessContext:
    row = locked_run if isinstance(locked_run, dict) else {}
    return CapabilityAccessContext(
        tenant_id=payload.tenant_id,
        department_id=str(row.get("principal_department_id") or ""),
        roles=normalize_capability_roles(row.get("principal_roles") or []),
        is_admin=is_ai_admin(
            AuthPrincipal(
                user_id=payload.user_id,
                display_name=payload.user_id,
                tenant_id=payload.tenant_id,
                department_id=str(row.get("principal_department_id") or ""),
                roles=list(row.get("principal_roles") or []),
                permissions=[],
                source=str(row.get("auth_source") or "worker-queue"),
            )
        ),
    )
```

- [ ] **Step 4: Re-fetch and authorize Skill and MCP before executor setup**

```python
async def authorize_locked_run_capabilities(conn, *, payload, locked_run):
    context = capability_access_context_from_locked_run(payload, locked_run)
    skill = await repositories.get_skill(conn, skill_id=payload.skill_id)
    skill_distribution = await repositories.get_capability_distribution_row(
        conn,
        tenant_id=payload.tenant_id,
        capability_kind="skill",
        capability_id=payload.skill_id,
    )
    skill_decision = resolve_capability_access(
        context,
        CapabilityDistributionSubject(
            "skill",
            payload.skill_id,
            lifecycle_status=str((skill or {}).get("status") or "disabled"),
            distribution=skill_distribution,
        ),
        "use",
    )
    # Resolve each requested MCP tool to its server and apply the inherited server distribution.
    return skill_decision, authorized_mcp_tools
```

On deny, mark the run failed with `capability_not_authorized`, append a sanitized authorization event, and audit `capability_distribution.denied`. Do not call executor setup or MCP registration.

- [ ] **Step 5: Preserve existing MCP tool policy after distribution allow**

The worker passes only resolver-authorized tools into the current registration path. Existing write-capable, risk-level, and deny-by-default checks execute afterward and retain their current error/audit semantics.

- [ ] **Step 6: Run worker and adapter tests green**

Run: `python -m pytest tests/test_worker.py tests/test_claude_agent_worker_adapter.py -q -p no:cacheprovider -k "capability_distribution or authorization_snapshot or registered_tools or mcp_tool" --basetemp .pytest-tmp\capdist-task6-green`

Expected: all selected tests PASS.

- [ ] **Step 7: Commit worker reauthorization**

```powershell
git add app/worker.py tests/test_worker.py tests/test_claude_agent_worker_adapter.py
git commit -m "feat: reauthorize capabilities in worker"
```

---

### Task 7: Integrated Verification and PR Evidence

**Files:**
- Modify: `docs/operations/capability-distribution-v1-backend-rescue-phase-status.md`
- Modify: focused source or tests only when a failing verification identifies a Capability Distribution defect.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: reviewable evidence, a final focused branch, an independent review result, and a PR with required CI checks.

- [ ] **Step 1: Run compile and focused authority tests**

```powershell
python -m compileall -q app tools scripts
python -m pytest tests/test_capability_distribution.py tests/test_capability_distribution_routes.py tests/test_schema.py tests/test_repositories.py -q -p no:cacheprovider --basetemp .pytest-tmp\capdist-final-foundation
```

Expected: both commands exit 0.

- [ ] **Step 2: Run focused route and lifecycle tests**

```powershell
python -m pytest tests/test_skills_marketplace_routes.py tests/test_role_governance_routes.py tests/test_mcp_routes.py tests/test_chat_routes.py tests/test_routes.py tests/test_run_control_routes.py -q -p no:cacheprovider -k "not test_cancel_run_ignores_user_controlled_sandbox_container_payload" --basetemp .pytest-tmp\capdist-final-routes
```

Expected: all selected tests PASS. The pre-existing sandbox test is named explicitly and remains unmodified.

- [ ] **Step 3: Run focused worker and registration tests**

```powershell
python -m pytest tests/test_worker.py tests/test_claude_agent_worker_adapter.py -q -p no:cacheprovider --basetemp .pytest-tmp\capdist-final-worker
```

Expected: all tests PASS.

- [ ] **Step 4: Verify source scope and formatting**

```powershell
git diff --check origin/main...HEAD
git diff --name-only origin/main...HEAD
git status --short
```

Expected: no whitespace errors, no unknown files, and no sandbox, Release Authority, B1/B2/B3, frontend, deploy, or 211 file paths.

- [ ] **Step 5: Complete the large-feature self-review gate**

Record explicitly in the phase status document:

- no secrets, real `.env` values, or personal paths in staged files
- new public functions and classes have docstrings
- happy-path and error-path tests exist
- the phase status document is current
- a one-paragraph diff summary explaining the sole authority, dual authorization checks, and audit behavior

- [ ] **Step 6: Commit the final evidence update**

```powershell
git add docs/operations/capability-distribution-v1-backend-rescue-phase-status.md
git commit -m "docs: record capability distribution verification"
```

- [ ] **Step 7: Request independent sub-agent review**

Review `origin/main...HEAD` for authorization bypass, tenant leakage, stale legacy fallback, role-normalization gaps, worker TOCTOU gaps, MCP registration leakage, audit omissions, and scope contamination. Fix every Critical or Important finding, rerun focused verification, and request a fresh re-review.

- [ ] **Step 8: Push and open the focused PR**

```powershell
git push -u origin codex/capability-distribution-v1-backend-rescue-20260710
gh pr create --base main --head codex/capability-distribution-v1-backend-rescue-20260710 --title "feat: restore capability distribution backend authority" --body-file .pytest-tmp\capdist-pr-body.md
```

The PR body lists current base/head, scope, exclusions, exact verification results, pre-existing sandbox baseline, independent review status, and `No 211 deployment performed`.

- [ ] **Step 9: Post review evidence and wait for required CI**

Post the independent review result as a PR comment. Observe `backend required` and `frontend required` plus any repository-required checks on the final head. Do not claim PR completion until the final head is current and every required check succeeds.

- [ ] **Step 10: Refresh the phase matrix**

Mark only source, local verification, review, CI, and PR phases complete. Keep deployment, browser acceptance, 211 parity, B1/B2/B3 runtime acceptance, and department rollout explicitly unclaimed.
