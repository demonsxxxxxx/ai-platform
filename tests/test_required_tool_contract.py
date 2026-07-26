from dataclasses import replace

import pytest

from app.required_tool_contract import (
    REQUIRED_CAPABILITY_DECLARATION_INPUT_KEY,
    RequiredCapabilityEvidence,
    RequiredToolContractError,
    completion_decision,
    declaration_from_payload,
    parse_required_tool_declaration,
    replay_required_tool_authorization,
    required_builtin_capability_subjects,
)


def _declaration():
    return parse_required_tool_declaration("请执行 Bash 命令 pwd")


def _binding(**overrides):
    values = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "attempt_id": "qat-a",
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    "message",
    [
        "请执行 Bash 命令 pwd",
        "run Bash command pwd",
        "执行工具 Bash",
    ],
)
def test_affirmative_execution_with_exact_bash_declares_server_requirement(message):
    declaration = parse_required_tool_declaration(message)

    assert declaration is not None
    assert declaration.capability_kind == "builtin"
    assert declaration.canonical_identity == "Bash"
    assert declaration.lifecycle_phase == "selected"
    assert declaration.lifecycle_status == "required"
    assert declaration.evidence_source == "server_intent_parser"
    assert declaration.trust_basis == "server_derived_locked_input"
    assert declaration.public_label == "controlled_execution_capability"
    assert declaration.public_status == "required"
    assert declaration.schema_version == "ai-platform.required-capability-declaration.v1"
    assert declaration.declaration_sha256


@pytest.mark.parametrize(
    "message",
    [
        "不要执行 Bash 命令 pwd",
        "解释一下 Bash 命令 pwd",
        "可以执行 Bash 吗？",
        "请执行 bash 命令 pwd",
        "请执行 Bashful 命令 pwd",
        "请执行 Bash.exe 命令 pwd",
        "请执行 Bash_tool 命令 pwd",
        "Bash 是什么？",
        "运行测试",
    ],
)
def test_negative_explanatory_question_and_lookalike_never_create_requirement(message):
    assert parse_required_tool_declaration(message) is None


def test_declaration_validation_rejects_forgery_and_subject_constructor_is_exact():
    declaration = _declaration()
    subjects = required_builtin_capability_subjects(
        declaration=declaration,
        existing_subjects=[],
        active=True,
        distributed=True,
    )

    assert [subject["identity"] for subject in subjects] == ["Bash"]
    assert subjects[0]["required_parameter_keys"] == ["command"]
    assert subjects[0]["command_isolation"] == "sibling-tool-sandbox-v1"
    with pytest.raises(RequiredToolContractError, match="required_tool_declaration_mismatch"):
        required_builtin_capability_subjects(
            declaration=replace(declaration, declaration_sha256="0" * 64),
            existing_subjects=[],
            active=True,
            distributed=True,
        )


def test_explicit_declaration_carrier_is_exact_and_cannot_be_reparsed_from_text():
    declaration = _declaration()
    forged = declaration.to_payload()
    forged["public_status"] = "succeeded"

    with pytest.raises(RequiredToolContractError, match="required_tool_declaration_mismatch"):
        declaration_from_payload(forged)
    assert REQUIRED_CAPABILITY_DECLARATION_INPUT_KEY == "_required_capability_declaration"


def test_current_authorization_replay_has_no_admin_bypass_and_preserves_scope():
    declaration = _declaration()
    allowed = replay_required_tool_authorization(
        declaration=declaration,
        binding=_binding(),
        current_binding=_binding(),
        current_subject={
            "identity": "Bash",
            "declared_identities": ["Bash"],
            "registered": True,
            "declared": True,
            "active": True,
            "distributed": True,
            "identity_authorized": True,
            "object_authorized": True,
            "parameters_authorized": True,
            "risk_level": "high",
            "write_capable": True,
        },
        is_admin=True,
    )
    revoked = replay_required_tool_authorization(
        declaration=declaration,
        binding=_binding(),
        current_binding=_binding(),
        current_subject={
            "identity": "Bash",
            "declared_identities": ["Bash"],
            "registered": True,
            "declared": True,
            "active": False,
            "distributed": True,
            "identity_authorized": True,
            "object_authorized": True,
            "parameters_authorized": True,
            "risk_level": "high",
            "write_capable": True,
        },
        is_admin=True,
    )
    foreign = replay_required_tool_authorization(
        declaration=declaration,
        binding=_binding(),
        current_binding=_binding(run_id="run-foreign"),
        current_subject={},
        is_admin=False,
    )

    assert allowed.allowed is True
    assert allowed.admin_bypass is False
    assert revoked.reason == "required_tool_not_currently_authorized"
    assert foreign.reason == "required_tool_scope_mismatch"


def test_completion_is_run_attempt_bound_and_fails_closed_without_exact_evidence():
    declaration = _declaration()
    authorized = replay_required_tool_authorization(
        declaration=declaration,
        binding=_binding(),
        current_binding=_binding(),
        current_subject={
            "identity": "Bash",
            "declared_identities": ["Bash"],
            "registered": True,
            "declared": True,
            "active": True,
            "distributed": True,
            "identity_authorized": True,
            "object_authorized": True,
            "parameters_authorized": True,
            "risk_level": "high",
            "write_capable": True,
        },
        is_admin=False,
    )
    valid_evidence = {
        "schema_version": "ai-platform.required-capability-evidence.v1",
        **_binding(),
        "tool_call_id": None,
        "capability_kind": "builtin",
        "canonical_identity": "Bash",
        "lifecycle_phase": "completed",
        "lifecycle_status": "succeeded",
        "evidence_source": "executor_private_payload",
        "trust_basis": "attempt_bound_tool_invocation",
        "public_label": "controlled_execution_capability",
        "public_status": "succeeded",
        "declaration_sha256": declaration.declaration_sha256,
    }

    assert RequiredCapabilityEvidence.from_payload(valid_evidence).run_id == "run-a"
    assert completion_decision(
        declaration=declaration,
        authorization=authorized,
        binding=_binding(),
        evidence=valid_evidence,
    ).allowed
    assert completion_decision(
        declaration=declaration,
        authorization=authorized,
        binding=_binding(),
        evidence=None,
    ).reason == "required_tool_completion_evidence_missing"
    assert completion_decision(
        declaration=declaration,
        authorization=authorized,
        binding=_binding(),
        evidence={**valid_evidence, "attempt_id": "qat-stale"},
    ).reason == "required_tool_completion_evidence_mismatch"
    assert completion_decision(
        declaration=declaration,
        authorization=authorized,
        binding=_binding(),
        evidence={
            **valid_evidence,
            "lifecycle_phase": "started",
            "lifecycle_status": "in_progress",
            "public_status": "in_progress",
        },
    ).reason == "required_tool_completion_evidence_mismatch"


def test_mcp_envelope_requires_tool_call_bound_proof_without_adding_mcp_behavior():
    declaration = _declaration()
    evidence = {
        "schema_version": "ai-platform.required-capability-evidence.v1",
        **_binding(),
        "tool_call_id": None,
        "capability_kind": "mcp",
        "canonical_identity": "future-server/future-tool",
        "lifecycle_phase": "completed",
        "lifecycle_status": "succeeded",
        "evidence_source": "future_mcp_adapter",
        "trust_basis": "sandbox_terminal_status",
        "public_label": "controlled_execution_capability",
        "public_status": "succeeded",
        "declaration_sha256": declaration.declaration_sha256,
    }

    with pytest.raises(RequiredToolContractError, match="required_tool_completion_evidence_mismatch"):
        RequiredCapabilityEvidence.from_payload(evidence)
