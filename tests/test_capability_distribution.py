import importlib
import importlib.util

import pytest


def _module():
    spec = importlib.util.find_spec("app.capability_distribution")
    assert spec is not None, "app.capability_distribution module missing"
    return importlib.import_module("app.capability_distribution")


def _context(module, *, department_id="qa", roles=None, is_admin=False):
    return module.CapabilityAccessContext(
        tenant_id="tenant-a",
        department_id=department_id,
        roles=roles or ["qa_operator"],
        is_admin=is_admin,
    )


def _subject(module, **overrides):
    distribution = {
        "status": "active",
        "visible_to_user": True,
        "scope_mode": "allowlist",
        "department_ids": ["qa"],
        "allowed_roles": ["qa_operator"],
    }
    distribution.update(overrides.pop("distribution", {}))
    capability_kind = overrides.pop("capability_kind", "skill")
    capability_id = overrides.pop("capability_id", "qa-file-reviewer")
    return module.CapabilityDistributionSubject(
        capability_kind=capability_kind,
        capability_id=capability_id,
        distribution=distribution,
        **overrides,
    )


def test_resolver_allows_case_insensitive_exact_role_without_punctuation_aliases():
    module = _module()

    decision = module.resolve_capability_access(
        _context(module, roles=[" QA-Operator "]),
        _subject(module, distribution={"allowed_roles": ["qa-operator"]}),
        intent="use",
    )
    denied = module.resolve_capability_access(
        _context(module, roles=["qa_operator"]),
        _subject(module, distribution={"allowed_roles": ["qa-operator"]}),
        intent="use",
    )

    assert module.normalize_capability_roles([" QA-Operator ", "qa-operator"]) == ["qa-operator"]
    assert decision.decision_reason == "allowed"
    assert decision.visible is True
    assert decision.usable is True
    assert decision.manageable is True
    assert denied.decision_reason == "role_not_allowed"


@pytest.mark.parametrize(
    ("context_kwargs", "subject_kwargs", "intent", "reason"),
    [
        ({"department_id": "rd"}, {}, "discover", "department_not_allowed"),
        ({"roles": ["reader"]}, {}, "use", "role_not_allowed"),
        ({}, {"distribution": {"status": "disabled"}}, "use", "distribution_disabled"),
        ({}, {"distribution": {"visible_to_user": False}}, "discover", "distribution_hidden"),
        ({}, {"distribution": None}, "use", "distribution_missing"),
        ({}, {"lifecycle_status": "archived"}, "use", "lifecycle_denied"),
        ({}, {}, "manage", "manage_admin_required"),
    ],
)
def test_resolver_denies_required_access_cases(context_kwargs, subject_kwargs, intent, reason):
    module = _module()
    subject = (
        module.CapabilityDistributionSubject(
            capability_kind="skill",
            capability_id="qa-file-reviewer",
            lifecycle_status=subject_kwargs.get("lifecycle_status", "active"),
            distribution=None,
        )
        if subject_kwargs.get("distribution", object()) is None
        else _subject(module, **subject_kwargs)
    )

    decision = module.resolve_capability_access(_context(module, **context_kwargs), subject, intent=intent)

    assert decision.decision_reason == reason
    assert decision.visible is False
    assert decision.usable is False
    assert decision.manageable is False
    assert decision.admin_bypass is False


def test_resolver_admin_bypass_precedes_distribution_checks_and_audit_payload_is_stable():
    module = _module()
    subject = _subject(
        module,
        capability_kind="mcp_server",
        capability_id="qa-mcp",
        distribution={"status": "disabled", "visible_to_user": False},
    )

    decision = module.resolve_capability_access(
        _context(module, department_id="rd", roles=["employee"], is_admin=True), subject, intent="manage"
    )

    assert decision.decision_reason == "admin_bypass"
    assert decision.admin_bypass is True
    assert (decision.visible, decision.usable, decision.manageable) == (True, True, True)
    assert module.capability_distribution_audit_payload(
        decision=decision,
        actor_department_id="rd",
        actor_roles=[" Employee ", "QA-Lead", "employee"],
        capability_kind="mcp_server",
        capability_id="qa-mcp",
    ) == {
        "capability_kind": "mcp_server",
        "capability_id": "qa-mcp",
        "actor_department_id": "rd",
        "actor_roles": ["employee", "qa-lead"],
        "department_scope_ids": ["qa"],
        "role_scope_ids": ["qa_operator"],
        "scope_mode": "allowlist",
        "decision_reason": "admin_bypass",
        "admin_bypass": True,
    }


@pytest.mark.parametrize("capability_kind", ["skill", "mcp_server", "mcp_tool"])
@pytest.mark.parametrize("is_admin", [False, True])
def test_resolver_denies_archived_distribution_before_admin_bypass(capability_kind, is_admin):
    module = _module()
    subject = _subject(
        module,
        capability_kind=capability_kind,
        capability_id="qa-mcp:search" if capability_kind == "mcp_tool" else "qa-file-reviewer",
        inherited_distribution_source="mcp_server:qa-mcp" if capability_kind == "mcp_tool" else None,
        distribution={"metadata_json": '{"archived_at":"2026-07-15T00:00:00.000Z"}'},
    )

    decision = module.resolve_capability_access(
        _context(module, is_admin=is_admin),
        subject,
        intent="use",
    )

    assert subject.is_archived is True
    assert decision.decision_reason == "distribution_archived"
    assert (decision.visible, decision.usable, decision.manageable, decision.admin_bypass) == (False, False, False, False)


def test_resolver_reads_legacy_metadata_safely_without_treating_malformed_json_as_archived():
    module = _module()

    archived = _subject(module, distribution={"metadata": {"archived_at": "2026-07-15T00:00:00.000Z"}})
    malformed = _subject(module, distribution={"metadata_json": "{not-json"})

    assert archived.is_archived is True
    assert malformed.is_archived is False


def test_mcp_tool_uses_its_inherited_mcp_server_distribution():
    module = _module()
    subject = _subject(
        module,
        capability_kind="mcp_tool",
        capability_id="qa-mcp:search",
        inherited_distribution_source="mcp_server:qa-mcp",
    )

    decision = module.resolve_capability_access(_context(module), subject, intent="use")

    assert subject.inherited_distribution_source == "mcp_server:qa-mcp"
    assert decision.decision_reason == "allowed"


@pytest.mark.parametrize("inherited_source", [None, "", "  ", "skill:qa-mcp"])
def test_mcp_tool_fails_closed_without_explicit_parent_server_distribution(inherited_source):
    module = _module()
    subject = _subject(
        module,
        capability_kind="mcp_tool",
        capability_id="qa-mcp:search",
        inherited_distribution_source=inherited_source,
    )

    decision = module.resolve_capability_access(_context(module), subject, intent="use")

    assert decision.decision_reason == "distribution_inheritance_missing"
    assert (decision.visible, decision.usable, decision.manageable) == (False, False, False)
