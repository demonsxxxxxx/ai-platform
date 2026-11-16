from __future__ import annotations

import pytest

from app.execution.domain.model_groups import (
    ModelGroupPolicyError,
    normalize_group_code,
    normalize_group_name,
)
from app.execution.application.model_selection import RunModelSelection, parse_requested_model_selection
from app.runs.api import EXECUTION_SPEC_SCHEMA_VERSION_V4, ExecutionSpecError, compile_execution_spec


def test_group_names_and_codes_are_normalized_and_reserved_default_is_not_a_business_group():
    assert normalize_group_name("  Sales  ") == "Sales"
    assert normalize_group_code(" sales-team ") == "sales-team"
    with pytest.raises(ModelGroupPolicyError):
        normalize_group_code("sales/team")
    with pytest.raises(ValueError):
        parse_requested_model_selection({"group_id": "bad/id"})


def test_migration_mapping_rejects_multiple_user_groups():
    from tools.model_group_migration import VERSION, _mapping
    with pytest.raises(ValueError, match="model_group_single_membership_required"):
        _mapping({"version": VERSION, "groups": [], "users": [{"tenant_id": "tenant-a", "user_id": "user-a", "group_codes": ["sales", "support"], "primary_group_code": "sales"}]})


def test_new_run_model_selection_defaults_to_explicit_mode():
    selected = RunModelSelection(
        model_id="model-a",
        model_value="provider/model-a",
        connection_revision=1,
    )
    assert selected.selection_mode == "explicit"


@pytest.mark.asyncio
async def test_unavailable_group_default_never_substitutes_another_model(monkeypatch):
    from app.execution.infrastructure import model_groups

    async def group(*args, **kwargs):
        return {"id": "group-a", "default_model_id": "model-a"}

    async def models(*args, **kwargs):
        return [{"model_id": "model-b", "upstream_model_id": "provider/b", "display_name": "B", "provider": "test", "auto_compact_window": 100, "max_output_tokens": 10}]

    monkeypatch.setattr(model_groups, "_effective_group", group)
    monkeypatch.setattr(model_groups, "_available_group_models", models)
    public = await model_groups.list_public_group_models(None, tenant_id="t", user_id="u", group_id="group-a", usage_scene="chat")
    assert public["default_model_id"] is None
    with pytest.raises(ModelGroupPolicyError, match="model_not_available"):
        await model_groups.resolve_group_model(None, tenant_id="t", user_id="u", group_id="group-a", model_id=None, model_value=None, selection_mode=None, usage_scene="chat")


def test_model_selector_accepts_matching_id_and_value_and_rejects_retired_auto_mode():
    assert parse_requested_model_selection({
        "model_id": "model-a", "model": "provider/model-a",
    }) == {"id": "model-a", "value": "provider/model-a"}
    assert parse_requested_model_selection({"group_id": "group-a"}) == {"group_id": "group-a"}
    for selection in (
        {"selection_mode": "auto"},
        {"group_id": "group-a", "selection_mode": "auto"},
        {"model_id": "model-a", "selection_mode": "auto"},
    ):
        with pytest.raises(ValueError, match="model_selection_conflict"):
            parse_requested_model_selection(selection)


def test_continuation_inherits_frozen_model_only_without_a_new_selection():
    from app.execution.application.model_selection_bridge import inherit_explicit_selection
    previous = {"model_id": "m", "model_value": "provider/m", "model_group_id": "a"}
    expected = {"id": "m", "value": "provider/m", "group_id": "a", "selection_mode": "explicit"}
    assert inherit_explicit_selection(None, previous) == expected
    assert inherit_explicit_selection({"group_id": "a"}, previous) == expected
    changed = {"group_id": "b"}
    assert inherit_explicit_selection(changed, previous) == changed
    auto = {"group_id": "a", "selection_mode": "auto"}
    assert inherit_explicit_selection(auto, previous) == auto
    explicit = {"id": "other", "selection_mode": "explicit"}
    assert inherit_explicit_selection(explicit, previous) == explicit


def test_v4_execution_spec_requires_group_snapshot_fields():
    payload = {
        "schema_version": EXECUTION_SPEC_SCHEMA_VERSION_V4,
        "run_payload_schema_version": "ai-platform.run-payload.v1",
        "tenant_id": "tenant-a", "workspace_id": "workspace-a", "user_id": "user-a",
        "session_id": "session-a", "run_id": "run-a", "agent_id": "agent-a",
        "execution_kind": "skill", "skill_id": "skill-a", "file_ids": [],
        "input": {}, "executor_type": "worker", "trace_id": "trace-a",
        "skill_version": "sha-a",
        "release_decision": {
            "schema_version": "ai-platform.skill-release-decision.v1",
            "policy_active": False, "selected_version": "sha-a", "selected_track": "manifest_pin",
        },
        "skill_manifests": [{"skill_id": "skill-a", "content_hash": "sha-a"}],
        "context_snapshot_id": "context-a", "context_snapshot": {"context_snapshot_id": "context-a"},
        "context_pack": {}, "model_id": "model-a", "model_value": "model-a",
        "model_gateway_revision": 1, "model_auto_compact_window": 100000,
        "model_max_output_tokens": 1000, "agent_profile": {},
        "model_group_id": "group-a", "model_group_code": "sales",
        "model_group_type": "business", "model_group_revision": 2,
        "model_selection_mode": "auto", "usage_scene": "skill", "model_policy_version": "model-groups.v1",
    }
    assert compile_execution_spec(payload).to_mapping()["model_group_id"] == "group-a"
    invalid = dict(payload)
    invalid.pop("usage_scene")
    with pytest.raises(ExecutionSpecError, match="execution_spec_fields_invalid"):
        compile_execution_spec(invalid)


@pytest.mark.asyncio
async def test_employee_preassignment_checks_directory_before_transaction_and_provisions_atomically():
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from app.execution.application.model_control_plane import ModelControlPlaneService
    from app.identity.api import CompanyEmployee, CompanyEmployeeDirectoryService, CompanyEmployeeProvisioningService
    events = []
    active = False
    fail = False
    async def read():
        assert not active
        events.append("directory")
        if fail:
            raise RuntimeError("company_employee_directory_unavailable")
        return [CompanyEmployee("NEW", "Trusted name")]
    class Store:
        async def ensure_employee(self, conn, **kwargs):
            assert active and conn == "conn"
            assert kwargs == {"tenant_id":"tenant-a","work_id":"NEW","display_name":"Trusted name"}
            events.append("provision")
            return "NEW"
    class Repository:
        async def require_group_member_edit(self, conn, **kwargs):
            assert active
            events.append("group-lock")
        async def set_group_members(self, conn, **kwargs):
            assert active and kwargs["members"] == [{"user_id":"NEW","is_primary":True}]
            events.append("members")
            return {"revision":2}
    @asynccontextmanager
    async def transaction():
        nonlocal active
        active = True
        events.append("transaction")
        try:
            yield "conn"
        finally:
            active = False
    service = ModelControlPlaneService(transaction_factory=transaction, settings_provider=lambda: None,
        repository=Repository(),security=SimpleNamespace(),upstream=SimpleNamespace(),attempt_capability_verifier=lambda **_:False,
        employee_identities=CompanyEmployeeProvisioningService(CompanyEmployeeDirectoryService(read,tenant_id="tenant-a"),Store()))
    async def save():
        return await service.set_model_group_members(tenant_id="tenant-a",group_id="g",revision=1,members=[{"work_id":"NEW","is_primary":True}])
    assert await save() == {"revision":2}
    assert events == ["directory","transaction","group-lock","provision","members"]
    fail = True
    events.clear()
    with pytest.raises(RuntimeError, match="company_employee_directory_unavailable"):
        await save()
    assert events == ["directory"]
