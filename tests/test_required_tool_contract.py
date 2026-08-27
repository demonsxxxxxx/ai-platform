from dataclasses import asdict, replace

import pytest

from app.execution_boundary import ExecutionBoundaryDecision
from app.required_tool_contract import (
    REQUIRED_CAPABILITY_DECLARATION_INPUT_KEY,
    RequiredCapabilityDecision,
    RequiredCapabilityDeclaration,
    RequiredCapabilityEvidence,
    RequiredToolContractError,
    ToolInvocationEvidence,
    completion_decision,
    declaration_from_payload,
    parse_required_tool_declaration,
    replay_required_tool_authorization,
    required_builtin_capability_subjects,
    required_tool_authorization_for_run,
    selected_capability_completion_decision,
    validate_tool_invocation_evidence,
    with_boundary_sandbox_local_tool_subjects,
    with_sandbox_local_tool_capability_subjects,
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


def test_required_builtin_subject_preserves_existing_server_declaration():
    declaration = _declaration()
    declared_subjects = [{"identity": "Bash", "declared": True}]
    subjects = required_builtin_capability_subjects(
        declaration=declaration,
        existing_subjects=declared_subjects,
        active=True,
        distributed=True,
    )

    assert subjects is declared_subjects


def test_required_builtin_subject_never_mints_undeclared_authority_and_rejects_forgery():
    declaration = _declaration()

    assert required_builtin_capability_subjects(
        declaration=declaration,
        existing_subjects=[],
        active=True,
        distributed=True,
    ) == []
    with pytest.raises(RequiredToolContractError, match="required_tool_declaration_mismatch"):
        required_builtin_capability_subjects(
            declaration=replace(declaration, declaration_sha256="0" * 64),
            existing_subjects=[],
            active=True,
            distributed=True,
        )


def test_real_sandbox_replaces_local_tool_authority_once():
    subjects = with_sandbox_local_tool_capability_subjects(
        [
            {"identity": "Read", "registered": True},
            {"identity": "Bash", "command_isolation": "sibling-tool-sandbox-v1"},
            {"identity": "Bash", "command_isolation": "minimal-environment-v1"},
        ],
        sandbox_provider="opensandbox",
    )

    assert [subject["identity"] for subject in subjects] == [
        "Read",
        "Glob",
        "Grep",
        "LS",
        "Bash",
        "Write",
        "Edit",
        "NotebookEdit",
    ]
    assert all(
        subject["execution_strategy"] == "sandbox_full_local"
        for subject in subjects
    )
    assert not {"Agent", "WebFetch", "WebSearch"} & {
        subject["identity"] for subject in subjects
    }
    bash_subject = next(subject for subject in subjects if subject["identity"] == "Bash")
    assert bash_subject["registered"] is True
    assert bash_subject["active"] is True
    assert bash_subject["distributed"] is True
    assert bash_subject["command_isolation"] == "opensandbox-workspace-v1"
    assert bash_subject["workspace_contract"] == "ai-platform.skill-workspace.v1"
    grep_subject = next(subject for subject in subjects if subject["identity"] == "Grep")
    assert grep_subject["risk_level"] == "low"
    assert grep_subject["write_capable"] is False
    assert grep_subject["allowed_parameter_keys"] == [
        "pattern",
        "path",
        "glob",
        "output_mode",
        "-i",
        "multiline",
        "head_limit",
        "offset",
        "context",
    ]
    assert grep_subject["required_parameter_keys"] == ["pattern"]


def test_sandbox_local_tools_use_credential_free_docker_sibling_for_bash():
    subjects = with_sandbox_local_tool_capability_subjects(
        [],
        sandbox_provider="docker",
    )
    bash_subject = next(subject for subject in subjects if subject["identity"] == "Bash")

    assert bash_subject["command_isolation"] == "sibling-tool-sandbox-v1"
    assert bash_subject["execution_strategy"] == "sandbox_full_local"


def test_sandbox_local_tool_subjects_reject_non_real_provider():
    with pytest.raises(RequiredToolContractError, match="sandbox_bash_provider_invalid"):
        with_sandbox_local_tool_capability_subjects([], sandbox_provider="fake")


def _boundary(*, real: bool, fail_closed: bool = False) -> ExecutionBoundaryDecision:
    return ExecutionBoundaryDecision(
        requires_real_sandbox=real,
        accepted_providers=frozenset({"docker", "opensandbox"}) if real else frozenset(),
        permission_policy="sandbox_brokered" if real else "adapter_managed",
        evidence_source="sandbox_runtime" if real else "",
        evidence_class="runtime_lease_projection" if real else "",
        local_sdk_allowed=False,
        fail_closed=fail_closed,
        reason="test",
    )


@pytest.mark.parametrize(
    "decision",
    [_boundary(real=False), _boundary(real=True, fail_closed=True)],
)
def test_non_real_or_fail_closed_boundary_strips_all_sandbox_local_capabilities(decision):
    subjects = with_boundary_sandbox_local_tool_subjects(
        [
            {"identity": "Read", "write_capable": False},
            {"identity": "Bash", "write_capable": True},
            {"identity": "custom-write", "write_capable": True},
            {"identity": "Write", "write_capable": False},
        ],
        decision=decision,
        sandbox_provider="opensandbox",
    )

    assert subjects == []


def test_real_boundary_adds_all_local_tools_after_sanitizing_existing_authority():
    subjects = with_boundary_sandbox_local_tool_subjects(
        [
            {"identity": "mcp__catalog__search", "write_capable": False},
            {"identity": "custom-write", "write_capable": True},
        ],
        decision=_boundary(real=True),
        sandbox_provider="opensandbox",
    )

    assert [subject["identity"] for subject in subjects] == [
        "mcp__catalog__search",
        "Read",
        "Glob",
        "Grep",
        "LS",
        "Bash",
        "Write",
        "Edit",
        "NotebookEdit",
    ]


def test_tool_invocation_evidence_requires_attempt_bound_started_to_terminal_sequence():
    started = ToolInvocationEvidence.from_executor_private_payload(
        binding=_binding(),
        tool_call_id="call-a",
        canonical_identity="Bash",
        lifecycle_phase="started",
    )
    completed = ToolInvocationEvidence.from_executor_private_payload(
        binding=_binding(),
        tool_call_id="call-a",
        canonical_identity="Bash",
        lifecycle_phase="completed",
    )
    failed = ToolInvocationEvidence.from_executor_private_payload(
        binding=_binding(),
        tool_call_id="call-a",
        canonical_identity="Bash",
        lifecycle_phase="failed",
    )

    assert validate_tool_invocation_evidence(
        [asdict(started), asdict(completed)], binding=_binding()
    ) == [asdict(started), asdict(completed)]
    assert validate_tool_invocation_evidence(
        [asdict(started), asdict(failed)], binding=_binding()
    ) == [asdict(started), asdict(failed)]
    with pytest.raises(RequiredToolContractError, match="tool_invocation_evidence_mismatch"):
        validate_tool_invocation_evidence([asdict(started)], binding=_binding())
    with pytest.raises(RequiredToolContractError, match="tool_invocation_evidence_mismatch"):
        validate_tool_invocation_evidence(
            [asdict(started), asdict(completed)],
            binding=_binding(attempt_id="other-attempt"),
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


@pytest.mark.parametrize(
    ("admin_non_bypass_authorized", "allowed", "reason"),
    [
        (False, False, "required_tool_admin_bypass_forbidden"),
        (True, True, "required_tool_currently_authorized"),
    ],
)
def test_run_authorization_requires_explicit_non_bypass_proof_for_admin(
    admin_non_bypass_authorized,
    allowed,
    reason,
):
    declaration = _declaration()
    payload = type(
        "Payload",
        (),
        {
            "input": {REQUIRED_CAPABILITY_DECLARATION_INPUT_KEY: declaration.to_payload()},
            **{field: value for field, value in _binding().items() if field != "attempt_id"},
        },
    )()
    subject = {
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
    }

    decision = required_tool_authorization_for_run(
        payload=payload,
        run_identity={key: value for key, value in _binding().items() if key != "attempt_id"},
        attempt_id="qat-a",
        subjects=[subject],
        admin_bypass=True,
        admin_non_bypass_authorized=admin_non_bypass_authorized,
    )

    assert decision.allowed is allowed
    assert decision.reason == reason


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
        "tool_call_id": "bash-call-a",
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
        evidence={**valid_evidence, "tool_call_id": None},
    ).reason == "required_tool_completion_evidence_mismatch"
    assert completion_decision(
        declaration=declaration,
        authorization=authorized,
        binding=_binding(),
        evidence={
            **valid_evidence,
            "lifecycle_phase": "invocation_requested",
            "lifecycle_status": "invoking",
            "public_status": "invoking",
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


def test_authorized_skill_and_mcp_declarations_keep_distinct_canonical_identities():
    skill = RequiredCapabilityDeclaration.from_authorized_subject(
        capability_kind="skill",
        canonical_identity="document-reviewer",
    )
    mcp = RequiredCapabilityDeclaration.from_authorized_subject(
        capability_kind="mcp",
        canonical_identity="mcp__github__search_issues",
    )

    assert skill.canonical_identity == "document-reviewer"
    assert mcp.canonical_identity == "mcp__github__search_issues"
    assert skill.declaration_sha256 != mcp.declaration_sha256
    assert declaration_from_payload(skill.to_payload()) == skill
    assert declaration_from_payload(mcp.to_payload()) == mcp


def test_executor_private_payload_binds_completed_builtin_to_exact_attempt():
    declaration = _declaration()

    evidence = RequiredCapabilityEvidence.from_executor_private_payload(
        declaration=declaration,
        binding=_binding(),
        tool_call_id="bash-call-a",
    )

    assert evidence.capability_kind == "builtin"
    assert evidence.canonical_identity == "Bash"
    assert evidence.lifecycle_phase == "completed"
    assert evidence.lifecycle_status == "succeeded"
    assert evidence.evidence_source == "executor_private_payload"
    assert evidence.trust_basis == "attempt_bound_tool_invocation"
    assert completion_decision(
        declaration=declaration,
        authorization=RequiredCapabilityDecision(
            True,
            "required_tool_currently_authorized",
            "builtin",
            "Bash",
        ),
        binding=_binding(),
        evidence=evidence.__dict__,
    ).allowed is True


@pytest.mark.parametrize(
    ("succeeded", "phase", "status"),
    [(True, "completed", "succeeded"), (False, "failed", "failed")],
)
def test_sdk_hook_evidence_is_exactly_bound_and_contains_no_private_payload(
    succeeded,
    phase,
    status,
):
    declaration = RequiredCapabilityDeclaration.from_authorized_subject(
        capability_kind="mcp",
        canonical_identity="mcp__github__search_issues",
    )

    evidence = RequiredCapabilityEvidence.from_sdk_hook(
        declaration=declaration,
        binding=_binding(),
        tool_call_id="tool-call-a",
        succeeded=succeeded,
    )
    payload = evidence.__dict__

    assert evidence.lifecycle_phase == phase
    assert evidence.lifecycle_status == status
    assert evidence.evidence_source == "claude_agent_sdk_hook"
    assert evidence.trust_basis == "tool_call_bound_invocation"
    assert RequiredCapabilityEvidence.from_payload(payload) == evidence
    assert not ({"arguments", "result", "endpoint", "token", "error"} & payload.keys())


def test_sdk_hook_evidence_rejects_missing_tool_call_or_attempt_binding():
    declaration = RequiredCapabilityDeclaration.from_authorized_subject(
        capability_kind="skill",
        canonical_identity="document-reviewer",
    )

    with pytest.raises(RequiredToolContractError, match="required_tool_completion_evidence_mismatch"):
        RequiredCapabilityEvidence.from_sdk_hook(
            declaration=declaration,
            binding=_binding(),
            tool_call_id="",
            succeeded=True,
        )
    with pytest.raises(RequiredToolContractError, match="required_tool_completion_evidence_mismatch"):
        RequiredCapabilityEvidence.from_sdk_hook(
            declaration=declaration,
            binding=_binding(attempt_id=""),
            tool_call_id="tool-call-a",
            succeeded=True,
        )


def test_sdk_pre_tool_hook_evidence_is_exactly_bound_and_private():
    declaration = RequiredCapabilityDeclaration.from_authorized_subject(
        capability_kind="skill",
        canonical_identity="document-reviewer",
    )

    evidence = RequiredCapabilityEvidence.from_sdk_hook(
        declaration=declaration,
        binding=_binding(),
        tool_call_id="tool-call-started",
        lifecycle_phase="invocation_requested",
    )
    payload = evidence.__dict__

    assert evidence.lifecycle_phase == "invocation_requested"
    assert evidence.lifecycle_status == "invoking"
    assert evidence.public_status == "invoking"
    assert RequiredCapabilityEvidence.from_payload(payload) == evidence
    assert not ({"arguments", "result", "endpoint", "token", "error"} & payload.keys())


def test_unbound_sdk_hook_payload_uses_the_same_bounded_lifecycle_vocabulary():
    declaration = RequiredCapabilityDeclaration.from_authorized_subject(
        capability_kind="mcp",
        canonical_identity="mcp__github__search_issues",
    )

    payload = RequiredCapabilityEvidence.sdk_hook_payload(
        declaration=declaration,
        tool_call_id="tool-call-started",
        lifecycle_phase="invocation_requested",
    )

    assert payload == {
        "schema_version": "ai-platform.required-capability-evidence.v1",
        "capability_kind": "mcp",
        "canonical_identity": "mcp__github__search_issues",
        "tool_call_id": "tool-call-started",
        "lifecycle_phase": "invocation_requested",
        "lifecycle_status": "invoking",
        "evidence_source": "claude_agent_sdk_hook",
        "trust_basis": "tool_call_bound_invocation",
        "public_label": "controlled_execution_capability",
        "public_status": "invoking",
        "declaration_sha256": declaration.declaration_sha256,
    }


@pytest.mark.parametrize("tool_call_id", ["", None])
def test_sdk_pre_tool_hook_evidence_rejects_empty_tool_call_identity(tool_call_id):
    declaration = RequiredCapabilityDeclaration.from_authorized_subject(
        capability_kind="mcp",
        canonical_identity="mcp__github__search_issues",
    )

    with pytest.raises(RequiredToolContractError, match="required_tool_completion_evidence_mismatch"):
        RequiredCapabilityEvidence.from_sdk_hook(
            declaration=declaration,
            binding=_binding(),
            tool_call_id=tool_call_id,
            lifecycle_phase="invocation_requested",
        )


@pytest.mark.parametrize(
    ("phase", "status"),
    [("invocation_requested", "invoking"), ("completed", "succeeded"), ("failed", "failed")],
)
def test_controlled_runner_skill_evidence_uses_process_bound_trust(phase, status):
    declaration = RequiredCapabilityDeclaration.from_authorized_subject(
        capability_kind="skill",
        canonical_identity="document-reviewer",
    )

    evidence = RequiredCapabilityEvidence.from_controlled_runner(
        declaration=declaration,
        binding=_binding(),
        tool_call_id="process-a",
        lifecycle_phase=phase,
    )

    assert evidence.lifecycle_status == status
    assert evidence.evidence_source == "controlled_skill_runner"
    assert evidence.trust_basis == "process_bound_invocation"
    assert RequiredCapabilityEvidence.from_payload(evidence.__dict__) == evidence


def test_controlled_runner_evidence_rejects_mcp_identity():
    declaration = RequiredCapabilityDeclaration.from_authorized_subject(
        capability_kind="mcp",
        canonical_identity="mcp__github__search_issues",
    )

    with pytest.raises(RequiredToolContractError, match="required_tool_completion_evidence_mismatch"):
        RequiredCapabilityEvidence.from_controlled_runner(
            declaration=declaration,
            binding=_binding(),
            tool_call_id="process-a",
            lifecycle_phase="invocation_requested",
        )


@pytest.mark.parametrize(
    ("capability_kind", "source", "trust"),
    [
        ("builtin", "claude_agent_sdk_hook", "tool_call_bound_invocation"),
        ("builtin", "executor_private_payload", "tool_call_bound_invocation"),
        ("mcp", "controlled_skill_runner", "process_bound_invocation"),
        ("mcp", "claude_agent_sdk_hook", "process_bound_invocation"),
        ("skill", "arbitrary_source", "arbitrary_trust"),
        ("skill", "controlled_skill_runner", "tool_call_bound_invocation"),
        ("skill", "claude_agent_sdk_hook", "process_bound_invocation"),
    ],
)
def test_evidence_source_and_trust_matrix_rejects_forged_pairs(
    capability_kind,
    source,
    trust,
):
    declaration = RequiredCapabilityDeclaration.from_authorized_subject(
        capability_kind="skill" if capability_kind == "builtin" else capability_kind,
        canonical_identity=(
            "document-reviewer"
            if capability_kind != "mcp"
            else "mcp__github__search_issues"
        ),
    )
    evidence = {
        "schema_version": "ai-platform.required-capability-evidence.v1",
        **_binding(),
        "tool_call_id": "invocation-a",
        "capability_kind": capability_kind,
        "canonical_identity": "Bash" if capability_kind == "builtin" else declaration.identity,
        "lifecycle_phase": "invocation_requested",
        "lifecycle_status": "invoking",
        "evidence_source": source,
        "trust_basis": trust,
        "public_label": "controlled_execution_capability",
        "public_status": "invoking",
        "declaration_sha256": declaration.declaration_sha256,
    }

    with pytest.raises(RequiredToolContractError, match="required_tool_completion_evidence_mismatch"):
        RequiredCapabilityEvidence.from_payload(evidence)


def _selected_evidence(declaration, phase, *, call_id="call-a", **binding_overrides):
    return RequiredCapabilityEvidence.from_sdk_hook(
        declaration=declaration,
        binding=_binding(**binding_overrides),
        tool_call_id=call_id,
        lifecycle_phase=phase,
    ).__dict__


@pytest.mark.parametrize(
    "records",
    [
        ["completed"],
        ["invocation_requested"],
        ["invocation_requested", "failed"],
        ["completed", "invocation_requested"],
        ["invocation_requested", "invocation_requested", "completed"],
        ["invocation_requested", "completed", "completed"],
    ],
)
def test_selected_capability_sequence_rejects_incomplete_failed_reversed_or_duplicate(records):
    declaration = RequiredCapabilityDeclaration.from_authorized_subject(
        capability_kind="skill",
        canonical_identity="document-reviewer",
    )

    decision = selected_capability_completion_decision(
        declarations=[declaration],
        binding=_binding(),
        evidence=[_selected_evidence(declaration, phase) for phase in records],
    )

    assert decision.allowed is False
    assert decision.reason == "required_tool_completion_evidence_mismatch"


def test_selected_capability_sequence_requires_same_call_binding_and_every_identity():
    skill = RequiredCapabilityDeclaration.from_authorized_subject(
        capability_kind="skill",
        canonical_identity="document-reviewer",
    )
    mcp = RequiredCapabilityDeclaration.from_authorized_subject(
        capability_kind="mcp",
        canonical_identity="mcp__github__search_issues",
    )
    valid = [
        _selected_evidence(skill, "invocation_requested"),
        _selected_evidence(skill, "completed"),
        _selected_evidence(mcp, "invocation_requested", call_id="call-b"),
        _selected_evidence(mcp, "completed", call_id="call-b"),
    ]

    assert selected_capability_completion_decision(
        declarations=[skill, mcp], binding=_binding(), evidence=valid
    ).allowed
    assert not selected_capability_completion_decision(
        declarations=[skill, mcp], binding=_binding(), evidence=valid[:-1]
    ).allowed
    assert not selected_capability_completion_decision(
        declarations=[skill],
        binding=_binding(),
        evidence=[valid[0], _selected_evidence(skill, "completed", call_id="other")],
    ).allowed
    assert not selected_capability_completion_decision(
        declarations=[skill],
        binding=_binding(),
        evidence=[valid[0], _selected_evidence(skill, "completed", attempt_id="qat-stale")],
    ).allowed
    assert not selected_capability_completion_decision(
        declarations=[skill], binding=_binding(), evidence=valid
    ).allowed
    assert not selected_capability_completion_decision(
        declarations=[skill, skill], binding=_binding(), evidence=valid[:2]
    ).allowed

    shared_call = [
        _selected_evidence(skill, "invocation_requested", call_id="shared-call"),
        _selected_evidence(skill, "completed", call_id="shared-call"),
        _selected_evidence(mcp, "invocation_requested", call_id="shared-call"),
        _selected_evidence(mcp, "completed", call_id="shared-call"),
    ]
    assert not selected_capability_completion_decision(
        declarations=[skill, mcp], binding=_binding(), evidence=shared_call
    ).allowed


@pytest.mark.parametrize(
    "call_id",
    [
        "   ",
        " call-with-padding ",
        "x" * 513,
        "call\nwith-control",
        "call\x7fwith-del",
        "call\x85with-c1",
        "call\u202ewith-bidi",
        "call-with-unicode-\u8c03\u7528",
    ],
)
def test_capability_and_local_tool_evidence_reject_noncanonical_call_ids(call_id):
    declaration = RequiredCapabilityDeclaration.from_authorized_subject(
        capability_kind="mcp",
        canonical_identity="mcp__github__search_issues",
    )

    with pytest.raises(RequiredToolContractError):
        RequiredCapabilityEvidence.from_sdk_hook(
            declaration=declaration,
            binding=_binding(),
            tool_call_id=call_id,
            lifecycle_phase="invocation_requested",
        )
    with pytest.raises(RequiredToolContractError):
        ToolInvocationEvidence.from_executor_private_payload(
            binding=_binding(),
            tool_call_id=call_id,
            canonical_identity="Bash",
            lifecycle_phase="started",
        )
