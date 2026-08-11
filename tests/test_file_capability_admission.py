from __future__ import annotations

import hashlib
import inspect
import io
from dataclasses import replace

import pytest
from openpyxl import Workbook

from app.attachments.capability_admission import (
    ADMISSION_REJECTED,
    ADMISSION_REQUIRED,
    FILE_CAPABILITY_REGISTRY,
    FILE_CAPABILITY_REGISTRY_POLICY_DIGEST,
    FILE_CAPABILITY_REGISTRY_VERSION,
    AgentSkillBinding,
    AuthorizedSkillPin,
    FileCapabilityAdmissionRequest,
    RuntimeDependencyIdentity,
    RuntimeImageInventory,
    RuntimeInventoryResolution,
    SkillAuthorizationResolution,
    SkillSelection,
    WorkspaceSkillPin,
    admit_file_capability,
    registry_policy_digest,
)
from app.attachments.classification import AttachmentBytesForClassification
from app.attachments.file_capabilities import (
    FILE_CAPABILITY_CONTRACT_SCOPE,
    FILE_CAPABILITY_ENFORCEMENT_STATE,
    AgentFileAuthorizationResolution,
    authorization_scope_sha256,
)
from app.auth import AuthPrincipal
from app.file_parser_contracts import XLSX_CONTENT_TYPE


IMAGE_DIGEST = "sha256:" + "b" * 64
PROFILE_SHA256 = "c" * 64


def _principal() -> AuthPrincipal:
    return AuthPrincipal(
        user_id="user-reviewed",
        display_name="Reviewed User",
        tenant_id="tenant-reviewed",
        department_id="department-reviewed",
        roles=["user"],
        permissions=["agent:use"],
        source="trusted-header",
        authority_source="trusted-gateway",
        authority_checked_at="2026-08-11T00:00:00+00:00",
    )


class FakeSkillAuthorizationPort:
    def __init__(self, resolution: object) -> None:
        self.resolution = resolution
        self.calls: list[dict[str, object]] = []

    async def resolve_exactly_one(
        self,
        *,
        principal,
        workspace_id,
        logical_skill_ids,
        selection,
        binding,
    ):
        self.calls.append(
            {
                "principal": principal,
                "workspace_id": workspace_id,
                "logical_skill_ids": logical_skill_ids,
                "selection": selection,
                "binding": binding,
            }
        )
        if isinstance(self.resolution, Exception):
            raise self.resolution
        return self.resolution


class FakeRuntimeInventoryPort:
    def __init__(self, resolution: object) -> None:
        self.resolution = resolution
        self.calls: list[dict[str, object]] = []

    async def resolve_authorized_inventory(
        self,
        *,
        principal,
        workspace_id,
        run_id,
        attempt_id,
        expected_workspace_skill,
    ):
        self.calls.append(
            {
                "principal": principal,
                "workspace_id": workspace_id,
                "run_id": run_id,
                "attempt_id": attempt_id,
                "expected_workspace_skill": expected_workspace_skill,
            }
        )
        if isinstance(self.resolution, Exception):
            raise self.resolution
        return self.resolution


class FakeAgentAuthorizationPort:
    def __init__(self, resolution: object) -> None:
        self.resolution = resolution
        self.calls: list[dict[str, object]] = []

    async def resolve_authorized_revision(
        self,
        *,
        principal,
        workspace_id,
        agent_id,
        expected_revision,
        expected_profile_sha256,
    ):
        self.calls.append(
            {
                "principal": principal,
                "workspace_id": workspace_id,
                "agent_id": agent_id,
                "expected_revision": expected_revision,
                "expected_profile_sha256": expected_profile_sha256,
            }
        )
        if isinstance(self.resolution, Exception):
            raise self.resolution
        return self.resolution


def _xlsx_bytes(cell_value: str = "name") -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    sheet["A1"] = cell_value
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _attachment(
    file_id: str = "file-xlsx",
    *,
    declared_media_type: str = XLSX_CONTENT_TYPE,
    cell_value: str = "name",
) -> AttachmentBytesForClassification:
    raw = _xlsx_bytes(cell_value)
    return AttachmentBytesForClassification(
        file_id=file_id,
        raw_bytes=raw,
        source_filename="report.xlsx",
        declared_media_type=declared_media_type,
        expected_size_bytes=len(raw),
        expected_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _pin(
    *,
    skill_id: str = "qa-rag-skill",
    version: str = "v2026-07-29",
    manifest_sha256: str | None = None,
) -> AuthorizedSkillPin:
    return AuthorizedSkillPin(
        logical_skill_id="qa-rag-skill",
        skill_id=skill_id,
        expected_version=version,
        manifest_sha256=manifest_sha256
        or hashlib.sha256(f"{skill_id}:{version}".encode()).hexdigest(),
        public_label="QA spreadsheet analysis",
    )


def _skill_port(
    status: str = "authorized",
    *,
    pins: tuple[AuthorizedSkillPin, ...] | None = None,
    principal: AuthPrincipal | None = None,
    workspace_id: str = "workspace-reviewed",
) -> FakeSkillAuthorizationPort:
    scoped_principal = principal or _principal()
    if status != "authorized":
        return FakeSkillAuthorizationPort(SkillAuthorizationResolution(status=status))
    return FakeSkillAuthorizationPort(
        SkillAuthorizationResolution(
            status=status,
            pins=(_pin(),) if pins is None else pins,
            tenant_id=scoped_principal.tenant_id,
            principal_user_id=scoped_principal.user_id,
            workspace_id=workspace_id,
            authorization_scope_sha256=authorization_scope_sha256(
                scoped_principal, workspace_id
            ),
            authority_source="published_skill_authority",
        )
    )


def _inventory(
    *,
    pin: AuthorizedSkillPin | None = None,
    dependencies: tuple[RuntimeDependencyIdentity, ...] | None = None,
    artifact_types: frozenset[str] = frozenset({"xlsx"}),
) -> RuntimeImageInventory:
    selected = pin or _pin()
    return RuntimeImageInventory(
        image_digest=IMAGE_DIGEST,
        python_version="3.11.9",
        runs_as_non_root=True,
        dependencies=(
            RuntimeDependencyIdentity("prebuilt_python", "openpyxl", "3.1.5"),
        )
        if dependencies is None
        else dependencies,
        workspace_skills=(
            WorkspaceSkillPin(
                selected.skill_id,
                selected.expected_version,
                selected.manifest_sha256,
            ),
        ),
        artifact_types=artifact_types,
        node_version=None,
        npm_source_install_allowed=False,
        public_package_registry_egress=False,
    )


def _runtime_resolution(
    *,
    principal: AuthPrincipal | None = None,
    workspace_id: str = "workspace-reviewed",
    run_id: str = "run-reviewed",
    attempt_id: str = "attempt-reviewed",
    inventory: RuntimeImageInventory | None = None,
) -> RuntimeInventoryResolution:
    scoped_principal = principal or _principal()
    return RuntimeInventoryResolution(
        status="authorized",
        tenant_id=scoped_principal.tenant_id,
        principal_user_id=scoped_principal.user_id,
        workspace_id=workspace_id,
        run_id=run_id,
        attempt_id=attempt_id,
        authorization_scope_sha256=authorization_scope_sha256(
            scoped_principal, workspace_id
        ),
        authority_source="runtime_lease_projection",
        inventory=inventory or _inventory(),
    )


def _runtime_port(**kwargs) -> FakeRuntimeInventoryPort:
    return FakeRuntimeInventoryPort(_runtime_resolution(**kwargs))


def _agent_resolution(
    *,
    principal: AuthPrincipal | None = None,
    workspace_id: str = "workspace-reviewed",
    agent_revision: int = 4,
    profile_sha256: str = PROFILE_SHA256,
    supported_input_types: tuple[str, ...] = ("text", "file"),
    supported_file_types: tuple[str, ...] = ("xlsx",),
    selected_skill_version: str = "agent-version",
) -> AgentFileAuthorizationResolution:
    scoped_principal = principal or _principal()
    return AgentFileAuthorizationResolution(
        status="authorized",
        tenant_id=scoped_principal.tenant_id,
        principal_user_id=scoped_principal.user_id,
        workspace_id=workspace_id,
        authorization_scope_sha256=authorization_scope_sha256(
            scoped_principal, workspace_id
        ),
        agent_id="agent-reviewed",
        agent_revision=agent_revision,
        profile_sha256=profile_sha256,
        supported_input_types=supported_input_types,
        supported_file_types=supported_file_types,
        selected_skill_id="qa-rag-skill",
        selected_skill_version=selected_skill_version,
    )


def _request(
    *attachments: AttachmentBytesForClassification,
    principal: AuthPrincipal | None = None,
    task_intent: str = "analyze",
    explicit_selection: SkillSelection | None = None,
    agent_binding: AgentSkillBinding | None = None,
) -> FileCapabilityAdmissionRequest:
    return FileCapabilityAdmissionRequest(
        attachments=attachments,
        task_intent=task_intent,
        principal=principal or _principal(),
        workspace_id="workspace-reviewed",
        run_id="run-reviewed",
        attempt_id="attempt-reviewed",
        explicit_selection=explicit_selection,
        agent_binding=agent_binding,
    )


@pytest.mark.asyncio
async def test_happy_path_requires_skill_and_runtime_authority_ports():
    principal = _principal()
    skill_port = _skill_port()
    runtime_port = _runtime_port(principal=principal)

    result = await admit_file_capability(
        _request(_attachment(), principal=principal),
        authorization_port=skill_port,
        runtime_authorization_port=runtime_port,
    )

    assert result.state == ADMISSION_REQUIRED
    assert result.fallback_prohibited is True
    assert result.selected_skill == SkillSelection("qa-rag-skill", "v2026-07-29")
    assert result.runtime_image_digest == IMAGE_DIGEST
    assert result.registry_policy_digest == FILE_CAPABILITY_REGISTRY_POLICY_DIGEST
    assert result.registry_digest == result.registry_policy_digest
    assert result.contract_scope == FILE_CAPABILITY_CONTRACT_SCOPE
    assert result.enforcement_state == FILE_CAPABILITY_ENFORCEMENT_STATE
    assert result.admission_fingerprint
    assert result.admission_fingerprint.startswith("sha256:")
    assert runtime_port.calls == [
        {
            "principal": principal,
            "workspace_id": "workspace-reviewed",
            "run_id": "run-reviewed",
            "attempt_id": "attempt-reviewed",
            "expected_workspace_skill": _pin().workspace_pin(),
        }
    ]


@pytest.mark.asyncio
async def test_admission_fingerprint_changes_with_each_authoritative_fact_group():
    base = await admit_file_capability(
        _request(_attachment()),
        authorization_port=_skill_port(),
        runtime_authorization_port=_runtime_port(),
    )
    changed_file = await admit_file_capability(
        _request(_attachment(cell_value="different workbook")),
        authorization_port=_skill_port(),
        runtime_authorization_port=_runtime_port(),
    )
    changed_pin = _pin(manifest_sha256="d" * 64)
    changed_skill = await admit_file_capability(
        _request(_attachment()),
        authorization_port=_skill_port(pins=(changed_pin,)),
        runtime_authorization_port=_runtime_port(
            inventory=_inventory(pin=changed_pin)
        ),
    )
    changed_runtime = await admit_file_capability(
        _request(_attachment()),
        authorization_port=_skill_port(),
        runtime_authorization_port=_runtime_port(
            inventory=replace(
                _inventory(), image_digest="sha256:" + "e" * 64
            )
        ),
    )
    changed_intent = await admit_file_capability(
        _request(_attachment(), task_intent="generate_artifact"),
        authorization_port=_skill_port(),
        runtime_authorization_port=_runtime_port(),
    )

    results = (base, changed_file, changed_skill, changed_runtime, changed_intent)
    assert all(result.state == ADMISSION_REQUIRED for result in results)
    assert len({result.admission_fingerprint for result in results}) == len(results)
    assert (
        base.parser_requirements[0].expected_sha256
        != changed_file.parser_requirements[0].expected_sha256
    )


@pytest.mark.asyncio
async def test_admission_fingerprint_covers_agent_revision_and_profile():
    principal = _principal()
    pin = _pin(version="agent-version")
    first_profile = "c" * 64
    second_profile = "d" * 64

    async def admitted(revision: int, profile_sha256: str):
        return await admit_file_capability(
            _request(
                _attachment(),
                principal=principal,
                agent_binding=AgentSkillBinding(
                    "agent-reviewed", revision, profile_sha256
                ),
            ),
            authorization_port=_skill_port(pins=(pin,), principal=principal),
            agent_authorization_port=FakeAgentAuthorizationPort(
                _agent_resolution(
                    principal=principal,
                    agent_revision=revision,
                    profile_sha256=profile_sha256,
                )
            ),
            runtime_authorization_port=_runtime_port(
                principal=principal,
                inventory=_inventory(pin=pin),
            ),
        )

    first = await admitted(4, first_profile)
    second = await admitted(5, second_profile)

    assert first.state == ADMISSION_REQUIRED
    assert second.state == ADMISSION_REQUIRED
    assert first.admission_fingerprint != second.admission_fingerprint


def test_request_has_no_runtime_inventory_self_attestation_field():
    parameters = inspect.signature(FileCapabilityAdmissionRequest).parameters

    assert "runtime_inventory" not in parameters
    assert {
        "principal",
        "workspace_id",
        "run_id",
        "attempt_id",
    } <= set(parameters)
    with pytest.raises(TypeError):
        FileCapabilityAdmissionRequest(  # type: ignore[call-arg]
            attachments=(_attachment(),),
            task_intent="analyze",
            principal=_principal(),
            workspace_id="workspace-reviewed",
            run_id="run-reviewed",
            attempt_id="attempt-reviewed",
            runtime_inventory=_inventory(),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resolution", "expected_code"),
    [
        (TimeoutError(), "file_capability_runtime_binding_unavailable"),
        (
            RuntimeInventoryResolution(status="unavailable"),
            "file_capability_runtime_binding_unavailable",
        ),
        (
            RuntimeInventoryResolution(status="unauthorized"),
            "file_capability_runtime_not_authorized",
        ),
        (
            RuntimeInventoryResolution(status="stale"),
            "file_capability_runtime_revision_stale",
        ),
    ],
)
async def test_runtime_authority_failures_are_bounded(resolution, expected_code):
    result = await admit_file_capability(
        _request(_attachment()),
        authorization_port=_skill_port(),
        runtime_authorization_port=FakeRuntimeInventoryPort(resolution),
    )

    assert result.state == ADMISSION_REJECTED
    assert result.rejection_code == expected_code
    assert result.fallback_prohibited is True


@pytest.mark.asyncio
async def test_runtime_scope_mismatch_is_terminal():
    result = await admit_file_capability(
        _request(_attachment()),
        authorization_port=_skill_port(),
        runtime_authorization_port=_runtime_port(workspace_id="workspace-other"),
    )

    assert result.rejection_code == "file_capability_runtime_scope_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("inventory", "expected_code"),
    [
        (
            _inventory(dependencies=()),
            "file_capability_runtime_dependency_unavailable",
        ),
        (
            _inventory(artifact_types=frozenset()),
            "file_capability_required_artifact_incompatible",
        ),
        (
            replace(_inventory(), workspace_skills=()),
            "file_capability_workspace_skill_mismatch",
        ),
    ],
)
async def test_authorized_runtime_observation_still_requires_exact_facts(
    inventory, expected_code
):
    task_intent = "generate_artifact" if "artifact" in expected_code else "analyze"
    result = await admit_file_capability(
        _request(_attachment(), task_intent=task_intent),
        authorization_port=_skill_port(),
        runtime_authorization_port=_runtime_port(inventory=inventory),
    )

    assert result.rejection_code == expected_code


@pytest.mark.asyncio
async def test_agent_admission_rechecks_acl_scope_declaration_and_selected_skill():
    principal = _principal()
    selected = _pin(version="agent-version")
    skill_port = _skill_port(pins=(selected,))
    agent_port = FakeAgentAuthorizationPort(_agent_resolution(principal=principal))
    runtime_port = _runtime_port(
        principal=principal,
        inventory=_inventory(pin=selected),
    )

    result = await admit_file_capability(
        _request(
            _attachment(),
            principal=principal,
            agent_binding=AgentSkillBinding(
                "agent-reviewed", 4, PROFILE_SHA256
            ),
        ),
        authorization_port=skill_port,
        agent_authorization_port=agent_port,
        runtime_authorization_port=runtime_port,
    )

    assert result.state == ADMISSION_REQUIRED
    assert result.selected_skill == SkillSelection("qa-rag-skill", "agent-version")
    assert agent_port.calls[0]["principal"] is principal
    binding = skill_port.calls[0]["binding"]
    assert binding.authority == "server_authorized_agent_revision"
    assert binding.workspace_id == "workspace-reviewed"
    assert binding.principal_user_id == principal.user_id


@pytest.mark.asyncio
async def test_agent_scope_or_empty_declaration_never_reaches_skill_authority():
    principal = _principal()
    request = _request(
        _attachment(),
        principal=principal,
        agent_binding=AgentSkillBinding("agent-reviewed", 4, PROFILE_SHA256),
    )
    skill_port = _skill_port()
    scope_mismatch = replace(
        _agent_resolution(principal=principal), workspace_id="workspace-other"
    )

    wrong_scope = await admit_file_capability(
        request,
        authorization_port=skill_port,
        agent_authorization_port=FakeAgentAuthorizationPort(scope_mismatch),
        runtime_authorization_port=_runtime_port(principal=principal),
    )
    empty = await admit_file_capability(
        request,
        authorization_port=skill_port,
        agent_authorization_port=FakeAgentAuthorizationPort(
            _agent_resolution(
                principal=principal,
                supported_input_types=("text",),
                supported_file_types=(),
            )
        ),
        runtime_authorization_port=_runtime_port(principal=principal),
    )

    assert wrong_scope.rejection_code == "file_capability_agent_scope_mismatch"
    assert empty.rejection_code == "file_capability_agent_declaration_empty"
    assert skill_port.calls == []


@pytest.mark.asyncio
async def test_skill_authority_exception_is_terminal_and_fail_closed():
    result = await admit_file_capability(
        _request(_attachment()),
        authorization_port=FakeSkillAuthorizationPort(TimeoutError()),
        runtime_authorization_port=_runtime_port(),
    )

    assert result.rejection_code == "file_capability_skill_unavailable"
    assert result.fallback_prohibited is True


@pytest.mark.asyncio
async def test_skill_authority_resolution_must_echo_the_exact_acl_scope():
    runtime_port = _runtime_port()

    result = await admit_file_capability(
        _request(_attachment()),
        authorization_port=_skill_port(workspace_id="workspace-other"),
        runtime_authorization_port=runtime_port,
    )

    assert result.rejection_code == "file_capability_skill_scope_mismatch"
    assert result.fallback_prohibited is True
    assert runtime_port.calls == []


@pytest.mark.asyncio
async def test_mime_mismatch_is_reported_before_any_authority_port_call():
    skill_port = _skill_port()
    runtime_port = _runtime_port()

    result = await admit_file_capability(
        _request(_attachment(declared_media_type="application/pdf")),
        authorization_port=skill_port,
        runtime_authorization_port=runtime_port,
    )

    assert (
        result.rejection_code
        == "attachment_classification_media_type_incompatible"
    )
    assert skill_port.calls == []
    assert runtime_port.calls == []


@pytest.mark.asyncio
async def test_empty_and_non_execution_requests_do_not_claim_production_admission():
    empty = await admit_file_capability(
        _request(),
        authorization_port=_skill_port(),
        runtime_authorization_port=_runtime_port(),
    )
    non_execution = await admit_file_capability(
        _request(_attachment(), task_intent="non_execution"),
        authorization_port=_skill_port(),
        runtime_authorization_port=_runtime_port(),
    )

    assert empty.state == "not_applicable"
    assert non_execution.state == "not_applicable"
    assert empty.enforcement_state == "not_production_wired"


def test_registry_policy_digest_and_legacy_exports_are_unambiguous():
    from app.attachments.capability_admission import (
        ParserIdentity,
        RuntimeDependencyKind,
    )
    from app.attachments.file_capabilities import (
        ParserIdentity as CanonicalParserIdentity,
    )
    from app.attachments.file_capabilities import (
        RuntimeDependencyKind as CanonicalRuntimeDependencyKind,
    )

    assert len(FILE_CAPABILITY_REGISTRY) == 1
    assert registry_policy_digest(FILE_CAPABILITY_REGISTRY) == (
        FILE_CAPABILITY_REGISTRY_POLICY_DIGEST
    )
    assert ParserIdentity is CanonicalParserIdentity
    assert RuntimeDependencyKind is CanonicalRuntimeDependencyKind
    assert FILE_CAPABILITY_REGISTRY_VERSION.endswith("v4-xlsx-only")
