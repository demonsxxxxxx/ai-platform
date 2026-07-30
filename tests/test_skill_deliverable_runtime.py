from dataclasses import asdict
import io
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from app.executors.base import ArtifactManifest, ExecutorResult
from app.required_tool_contract import RequiredCapabilityDeclaration, RequiredCapabilityEvidence
import app.skills.deliverable_runtime as deliverable_runtime
from app.skills.deliverable_runtime import (
    collect_workspace_artifacts,
    enforce_pinned_deliverable_result,
    persisted_required_artifact_types,
    stage_adapter_delivery,
)
from app.storage import StoredObject
from tests.test_skill_deliverables import usable_xlsx_bytes, xlsx_contract


class FakeStorage:
    def __init__(self, objects: dict[str, bytes] | None = None):
        self.stored: list[tuple[str, bytes, str]] = []
        self.objects = dict(objects or {})
        self.reads: list[tuple[str, int]] = []

    def put_bytes(self, *, storage_key, content, content_type):
        self.stored.append((storage_key, content, content_type))
        return StoredObject(storage_key=storage_key, sha256="hash", size_bytes=len(content))

    def get_bytes_bounded(self, *, storage_key, max_bytes):
        self.reads.append((storage_key, max_bytes))
        content = self.objects[storage_key]
        if len(content) > max_bytes:
            raise ValueError("object_size_limit_exceeded")
        return content


def payload(*, contract=None, file_ids=None, skill_id="audit-finding-rca"):
    return SimpleNamespace(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        skill_id=skill_id,
        file_ids=file_ids or ["file-a"],
        skill_manifests=(
            [{"skill_id": skill_id, "deliverable_contract": contract}] if contract is not None else []
        ),
    )


def artifact_dirs(workspace: Path) -> list[Path]:
    return [workspace / "outputs" / "audit-rca" / "delivery"]


def current_delivery_storage_key(*, filename: str = "audit-result.xlsx") -> str:
    """Return the deterministic storage namespace for the runtime fixture run."""

    return (
        "tenants/tenant-a/workspaces/workspace-a/sessions/session-a/runs/run-a/"
        f"artifacts/1/{filename}"
    )


def legacy_artifact_dirs(workspace: Path) -> list[Path]:
    """Mirror the adapter's historical output roots for runtime admission tests."""

    roots = [workspace / "output"] if (workspace / "output").is_dir() else []
    outputs_root = workspace / "outputs"
    if outputs_root.is_dir():
        roots.extend(sorted(outputs_root.rglob("delivery")))
    return roots


def collect_legacy_artifacts(run, workspace: Path, storage: FakeStorage):
    """Collect one legacy Skill output through the runtime-owned admission seam."""

    return collect_workspace_artifacts(
        payload=run,
        workspace=workspace,
        source_executor="claude-agent-worker",
        artifact_dirs=legacy_artifact_dirs,
        deliverable_contract=None,
        storage=storage,
    )


def usable_docx_bytes(
    *,
    document: bytes | None = None,
    content_types: bytes | None = None,
    relationships: bytes | None = None,
    include_relationships: bool = True,
    extra_entries: dict[str, bytes] | None = None,
) -> bytes:
    """Build a bounded OOXML DOCX fixture with deliberately adjustable parts."""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            content_types
            if content_types is not None
            else (
                b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                b'<Override PartName="/word/document.xml" '
                b'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                b"</Types>"
            ),
        )
        if include_relationships:
            archive.writestr(
                "_rels/.rels",
                relationships
                if relationships is not None
                else (
                    b'<?xml version="1.0"?><Relationships '
                    b'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    b'<Relationship Id="rId1" '
                    b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                    b'Target="word/document.xml"/>'
                    b"</Relationships>"
                ),
            )
        if document is not None:
            archive.writestr("word/document.xml", document)
        for name, content in (extra_entries or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def valid_docx_bytes() -> bytes:
    """Build one valid nonempty DOCX fixture."""

    return usable_docx_bytes(
        document=(
            b'<?xml version="1.0"?><w:document '
            b'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            b"<w:body><w:p/></w:body></w:document>"
        )
    )


def test_runtime_stages_only_verified_xlsx_and_never_stores_private_intermediates(tmp_path):
    contract = xlsx_contract()
    run = payload(contract=contract)
    delivery = artifact_dirs(tmp_path)[0]
    delivery.mkdir(parents=True)
    workbook = usable_xlsx_bytes()
    (delivery / "audit-result.xlsx").write_bytes(workbook)
    (delivery / "generate_filled_excel.py").write_text("private", encoding="utf-8")
    (delivery / "intermediate.json").write_text("{}", encoding="utf-8")
    storage = FakeStorage()

    outcome = stage_adapter_delivery(
        payload=run,
        pinned_manifests={"audit-finding-rca": run.skill_manifests[0]},
        workspace=tmp_path,
        executor_payload={"executor_mode": "claude_agent_sdk", "capability_evidence": []},
        source_executor="claude-agent-worker",
        artifact_dirs=artifact_dirs,
        storage=storage,
    )

    assert outcome.error_code is None
    assert [(artifact.artifact_type, artifact.label) for artifact in outcome.artifacts] == [
        ("xlsx", "Excel 文件")
    ]
    assert storage.stored == [
        (
            "tenants/tenant-a/workspaces/workspace-a/sessions/session-a/runs/run-a/artifacts/1/audit-result.xlsx",
            workbook,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    ]


def test_runtime_fails_closed_for_py_and_json_without_required_xlsx(tmp_path):
    contract = xlsx_contract()
    run = payload(contract=contract)
    delivery = artifact_dirs(tmp_path)[0]
    delivery.mkdir(parents=True)
    (delivery / "generate_filled_excel.py").write_text("private", encoding="utf-8")
    (delivery / "intermediate.json").write_text("{}", encoding="utf-8")
    storage = FakeStorage()

    outcome = stage_adapter_delivery(
        payload=run,
        pinned_manifests={"audit-finding-rca": run.skill_manifests[0]},
        workspace=tmp_path,
        executor_payload={"executor_mode": "claude_agent_sdk", "capability_evidence": []},
        source_executor="claude-agent-worker",
        artifact_dirs=artifact_dirs,
        storage=storage,
    )

    assert outcome.error_code == "required_artifact_missing"
    assert outcome.artifacts == ()
    assert storage.stored == []


def test_runtime_rejects_duplicate_required_xlsx_before_any_storage_write(tmp_path):
    contract = xlsx_contract()
    run = payload(contract=contract)
    delivery = artifact_dirs(tmp_path)[0]
    delivery.mkdir(parents=True)
    (delivery / "audit-result.xlsx").write_bytes(usable_xlsx_bytes())
    (delivery / "audit-result-copy.xlsx").write_bytes(usable_xlsx_bytes())
    (delivery / "generate_filled_excel.py").write_text("private", encoding="utf-8")
    storage = FakeStorage()

    outcome = stage_adapter_delivery(
        payload=run,
        pinned_manifests={"audit-finding-rca": run.skill_manifests[0]},
        workspace=tmp_path,
        executor_payload={"executor_mode": "claude_agent_sdk", "capability_evidence": []},
        source_executor="claude-agent-worker",
        artifact_dirs=artifact_dirs,
        storage=storage,
    )

    assert outcome.error_code == "required_artifact_cardinality_invalid"
    assert outcome.artifacts == ()
    assert storage.stored == []


def test_runtime_enforcer_requires_exact_attempt_evidence_and_clears_internal_artifacts():
    contract = xlsx_contract(requires_process_evidence=True)
    run = payload(contract=contract)
    workbook = usable_xlsx_bytes()
    storage = FakeStorage(
        {
            current_delivery_storage_key(): workbook,
            current_delivery_storage_key(filename="audit-result-copy.xlsx").replace("artifacts/1/", "artifacts/2/"): workbook,
        }
    )
    declaration = RequiredCapabilityDeclaration.from_authorized_subject(
        capability_kind="skill",
        canonical_identity="audit-finding-rca",
    )

    def result_for(attempt_id):
        binding = {
            "tenant_id": run.tenant_id,
            "workspace_id": run.workspace_id,
            "user_id": run.user_id,
            "session_id": run.session_id,
            "run_id": run.run_id,
            "attempt_id": attempt_id,
        }
        evidence = [
            asdict(
                RequiredCapabilityEvidence.from_controlled_runner(
                    declaration=declaration,
                    binding=binding,
                    tool_call_id="controlled-audit",
                    lifecycle_phase=phase,
                )
            )
            for phase in ("invocation_requested", "completed")
        ]
        return ExecutorResult(
            status="succeeded",
            adapter_version="test/1",
            executor_type="fake",
            executor_version="test",
            capabilities={},
            result={"message": "generate_filled_excel.py"},
            artifacts=[
                ArtifactManifest(
                    artifact_type="xlsx",
                    label="Excel 文件",
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    storage_key=current_delivery_storage_key(),
                    size_bytes=len(workbook),
                    manifest={
                        "deliverable_type": "xlsx",
                        "workspace_output": "outputs/audit-rca/delivery/audit-result.xlsx",
                    },
                ),
                ArtifactManifest(
                    artifact_type="runtime_file",
                    label="intermediate.json",
                    content_type="application/json",
                    storage_key="tenants/tenant-a/runs/run-a/intermediate.json",
                    size_bytes=1,
                ),
            ],
            executor_payload={
                "executor_mode": "platform_controlled_runner",
                "capability_evidence": evidence,
            },
        )

    accepted = enforce_pinned_deliverable_result(
        result_for("attempt-a"), payload=run, attempt_id="attempt-a", storage=storage
    )
    rejected = enforce_pinned_deliverable_result(
        result_for("stale-attempt"), payload=run, attempt_id="attempt-a", storage=storage
    )
    duplicate = result_for("attempt-a")
    duplicate.artifacts.insert(
        1,
        ArtifactManifest(
            artifact_type="xlsx",
            label="Excel 文件",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            storage_key=current_delivery_storage_key(filename="audit-result-copy.xlsx").replace("artifacts/1/", "artifacts/2/"),
            size_bytes=len(workbook),
            manifest={
                "deliverable_type": "xlsx",
                "workspace_output": "outputs/audit-rca/delivery/audit-result-copy.xlsx",
            },
        ),
    )
    duplicate_rejected = enforce_pinned_deliverable_result(
        duplicate, payload=run, attempt_id="attempt-a", storage=storage
    )

    assert [artifact.artifact_type for artifact in accepted.artifacts] == ["xlsx"]
    assert accepted.result["message"] == "已生成结果文件。"
    assert rejected.result["error_code"] == "skill_deliverable_process_evidence_missing"
    assert rejected.artifacts == []
    assert duplicate_rejected.status == "failed"
    assert duplicate_rejected.result["error_code"] == "required_artifact_cardinality_invalid"
    assert duplicate_rejected.artifacts == []


def test_runtime_enforcer_requires_current_namespace_and_verified_storage_bytes():
    contract = xlsx_contract(requires_process_evidence=True)
    run = payload(contract=contract)
    workbook = usable_xlsx_bytes()
    declaration = RequiredCapabilityDeclaration.from_authorized_subject(
        capability_kind="skill",
        canonical_identity="audit-finding-rca",
    )

    def result_for(*, storage_key: str, size_bytes: int) -> ExecutorResult:
        binding = {
            "tenant_id": run.tenant_id,
            "workspace_id": run.workspace_id,
            "user_id": run.user_id,
            "session_id": run.session_id,
            "run_id": run.run_id,
            "attempt_id": run.attempt_id,
        }
        evidence = [
            asdict(
                RequiredCapabilityEvidence.from_controlled_runner(
                    declaration=declaration,
                    binding=binding,
                    tool_call_id="storage-checked-audit",
                    lifecycle_phase=phase,
                )
            )
            for phase in ("invocation_requested", "completed")
        ]
        return ExecutorResult(
            status="succeeded",
            adapter_version="test/1",
            executor_type="fake",
            executor_version="test",
            capabilities={},
            result={"message": "internal output"},
            artifacts=[
                ArtifactManifest(
                    artifact_type="xlsx",
                    label="Excel 文件",
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    storage_key=storage_key,
                    size_bytes=size_bytes,
                    manifest={
                        "deliverable_type": "xlsx",
                        "workspace_output": "outputs/audit-rca/delivery/audit-result.xlsx",
                    },
                )
            ],
            executor_payload={
                "executor_mode": "platform_controlled_runner",
                "capability_evidence": evidence,
            },
        )

    current_key = current_delivery_storage_key()
    accepted_storage = FakeStorage({current_key: workbook})
    accepted = enforce_pinned_deliverable_result(
        result_for(storage_key=current_key, size_bytes=len(workbook)),
        payload=run,
        attempt_id=run.attempt_id,
        storage=accepted_storage,
    )
    cross_run_key = current_key.replace("runs/run-a/", "runs/run-other/")
    cross_run_storage = FakeStorage({cross_run_key: workbook})
    cross_run = enforce_pinned_deliverable_result(
        result_for(storage_key=cross_run_key, size_bytes=len(workbook)),
        payload=run,
        attempt_id=run.attempt_id,
        storage=cross_run_storage,
    )
    missing_storage = FakeStorage()
    missing = enforce_pinned_deliverable_result(
        result_for(storage_key=current_key, size_bytes=len(workbook)),
        payload=run,
        attempt_id=run.attempt_id,
        storage=missing_storage,
    )
    mismatch_storage = FakeStorage({current_key: workbook})
    mismatch = enforce_pinned_deliverable_result(
        result_for(storage_key=current_key, size_bytes=len(workbook) - 1),
        payload=run,
        attempt_id=run.attempt_id,
        storage=mismatch_storage,
    )
    invalid_storage = FakeStorage({current_key: b"not an OOXML workbook"})
    invalid = enforce_pinned_deliverable_result(
        result_for(storage_key=current_key, size_bytes=len(b"not an OOXML workbook")),
        payload=run,
        attempt_id=run.attempt_id,
        storage=invalid_storage,
    )

    assert [artifact.artifact_type for artifact in accepted.artifacts] == ["xlsx"]
    assert accepted_storage.reads == [(current_key, len(workbook))]
    assert accepted_storage.stored == []
    for rejected in (cross_run, missing, mismatch, invalid):
        assert rejected.status == "failed"
        assert rejected.result["error_code"] == "skill_deliverable_artifact_invalid"
        assert rejected.artifacts == []
    assert cross_run_storage.reads == []
    assert cross_run_storage.stored == []
    assert missing_storage.reads == [(current_key, len(workbook))]
    assert mismatch_storage.reads == [(current_key, len(workbook) - 1)]
    assert invalid_storage.reads == [(current_key, len(b"not an OOXML workbook"))]
    assert all(storage.stored == [] for storage in (missing_storage, mismatch_storage, invalid_storage))


def test_runtime_rejects_uncontracted_skill_private_artifacts_with_upgrade_packet(tmp_path):
    run = payload(contract=None)
    delivery = artifact_dirs(tmp_path)[0]
    delivery.mkdir(parents=True)
    (delivery / "generate_filled_excel.py").write_text("private", encoding="utf-8")
    (delivery / "intermediate.json").write_text("{}", encoding="utf-8")
    storage = FakeStorage()

    outcome = stage_adapter_delivery(
        payload=run,
        pinned_manifests={"audit-finding-rca": {"skill_id": "audit-finding-rca", "version": "v1"}},
        workspace=tmp_path,
        executor_payload={"executor_mode": "claude_agent_sdk", "capability_evidence": []},
        source_executor="claude-agent-worker",
        artifact_dirs=artifact_dirs,
        storage=storage,
    )

    assert outcome.error_code == "skill_deliverable_contract_upgrade_required"
    assert outcome.upgrade_packet == {
        "schema_version": "ai-platform.skill-deliverable-upgrade-packet.v1",
        "status": "package_upgrade_required",
        "skill_id": "audit-finding-rca",
        "version": "v1",
        "required_front_matter_fields": [
            "deliverable-public-types",
            "deliverable-required-types",
            "deliverable-process-evidence",
        ],
        "supported_public_deliverable_types": ["xlsx"],
        "process_evidence_values": ["required", "not_required"],
    }
    assert storage.stored == []


def test_runtime_keeps_upgrade_packet_out_of_ordinary_result_projection():
    run = payload(contract=None)
    result = ExecutorResult(
        status="succeeded",
        adapter_version="test/1",
        executor_type="fake",
        executor_version="test",
        capabilities={},
        result={"message": "internal output"},
        artifacts=[
            ArtifactManifest(
                artifact_type="runtime_file",
                label="intermediate.json",
                content_type="application/json",
                storage_key="tenants/tenant-a/runs/run-a/intermediate.json",
                size_bytes=2,
            )
        ],
    )

    failed = enforce_pinned_deliverable_result(result, payload=run, attempt_id=run.attempt_id)

    assert failed.status == "failed"
    assert failed.artifacts == []
    assert failed.result["error_code"] == "skill_deliverable_contract_upgrade_required"
    assert "deliverable_contract_upgrade" not in failed.result
    assert failed.executor_payload["deliverable_contract_upgrade"]["status"] == "package_upgrade_required"


def test_runtime_preserves_general_chat_artifacts_without_a_skill_delivery_contract():
    run = payload(contract=None, file_ids=[], skill_id="general-chat")
    result = ExecutorResult(
        status="succeeded",
        adapter_version="test/1",
        executor_type="fake",
        executor_version="test",
        capabilities={},
        result={"message": "general chat completed"},
        artifacts=[
            ArtifactManifest(
                artifact_type="test_json",
                label="Test JSON",
                content_type="application/json",
                storage_key="tenants/tenant-a/runs/run-a/result.json",
                size_bytes=2,
            )
        ],
    )

    accepted = enforce_pinned_deliverable_result(result, payload=run, attempt_id="attempt-a")

    assert accepted.status == "succeeded"
    assert [artifact.artifact_type for artifact in accepted.artifacts] == ["test_json"]


def test_runtime_collection_rejects_invalid_xlsx_before_storage(tmp_path):
    contract = xlsx_contract()
    run = payload(contract=contract)
    delivery = artifact_dirs(tmp_path)[0]
    delivery.mkdir(parents=True)
    (delivery / "audit-result.xlsx").write_bytes(b"not an OOXML workbook")
    storage = FakeStorage()

    artifacts = collect_workspace_artifacts(
        payload=run,
        workspace=tmp_path,
        source_executor="claude-agent-worker",
        artifact_dirs=artifact_dirs,
        deliverable_contract=contract,
        storage=storage,
    )

    assert artifacts == []
    assert storage.stored == []


def test_runtime_persistence_combines_legacy_tuple_requirements(monkeypatch):
    run = payload(contract=None, skill_id="qa-file-reviewer")
    monkeypatch.setattr(
        deliverable_runtime,
        "required_artifact_types_for_skill",
        lambda _skill_id: ("reviewed_docx",),
    )

    required_types = persisted_required_artifact_types(
        run,
        {"required_artifact_types": ["execution_log", "reviewed_docx"]},
    )

    assert required_types == {"reviewed_docx", "execution_log"}


def test_runtime_collection_rejects_symlinked_output_before_storage(tmp_path):
    workspace = tmp_path / "workspace"
    output = workspace / "output"
    output.mkdir(parents=True)
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    try:
        (output / "linked-secret.txt").symlink_to(secret)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation not available: {exc}")
    storage = FakeStorage()

    with pytest.raises(ValueError, match="symlinks"):
        collect_legacy_artifacts(payload(skill_id="qa-file-reviewer"), workspace, storage)

    assert storage.stored == []


def test_runtime_collection_keeps_only_delivery_roots_and_safe_mime_types(tmp_path):
    workspace = tmp_path / "workspace"
    delivery = workspace / "outputs" / "run-002-ctd-fill" / "delivery"
    delivery.mkdir(parents=True)
    (delivery / "report.pdf").write_bytes(b"pdf")
    (delivery / "chart.png").write_bytes(b"png")
    (delivery / "page.html").write_bytes(b"html")
    debug_dir = workspace / "outputs" / "run-002-ctd-fill" / "_debug"
    debug_dir.mkdir()
    (debug_dir / "debug.txt").write_text("debug", encoding="utf-8")
    storage = FakeStorage()

    artifacts = collect_legacy_artifacts(
        payload(skill_id="ctd-32s73-stability-template-fill"), workspace, storage
    )

    assert [artifact.content_type for artifact in artifacts] == [
        "image/png",
        "application/octet-stream",
        "application/pdf",
    ]
    assert [artifact.manifest["workspace_output"] for artifact in artifacts] == [
        "outputs/run-002-ctd-fill/delivery/chart.png",
        "outputs/run-002-ctd-fill/delivery/page.html",
        "outputs/run-002-ctd-fill/delivery/report.pdf",
    ]
    assert [content_type for _key, _content, content_type in storage.stored] == [
        "image/png",
        "application/octet-stream",
        "application/pdf",
    ]


@pytest.mark.parametrize(
    ("limit_name", "limit_value", "files", "expected_error"),
    [
        ("_MAX_WORKSPACE_ARTIFACT_FILES", 1, {"one.txt": b"1", "two.txt": b"2"}, "file count"),
        ("_MAX_WORKSPACE_ARTIFACT_FILE_BYTES", 3, {"large.txt": b"1234"}, "per-file"),
        ("_MAX_WORKSPACE_ARTIFACT_TOTAL_BYTES", 3, {"one.txt": b"12", "two.txt": b"34"}, "total"),
    ],
)
def test_runtime_collection_enforces_delivery_limits_before_storage(
    monkeypatch, tmp_path, limit_name, limit_value, files, expected_error
):
    workspace = tmp_path / "workspace"
    delivery = workspace / "outputs" / "delivery"
    delivery.mkdir(parents=True)
    for name, content in files.items():
        (delivery / name).write_bytes(content)
    monkeypatch.setattr(deliverable_runtime, limit_name, limit_value)
    storage = FakeStorage()

    with pytest.raises(ValueError, match=expected_error):
        collect_legacy_artifacts(payload(), workspace, storage)

    assert storage.stored == []


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"not-a-zip",
        usable_docx_bytes(document=None),
        usable_docx_bytes(document=b""),
        usable_docx_bytes(document=b"<document/>"),
        usable_docx_bytes(document=b"<w:document>not valid XML</w:document>"),
        usable_docx_bytes(document=b"<document><body><p/></body></document>", content_types=b"not XML"),
        usable_docx_bytes(document=b"<document><body><p/></body></document>", include_relationships=False),
        usable_docx_bytes(
            document=b"<document><body><p/></body></document>",
            relationships=(
                b'<Relationships><Relationship '
                b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                b'Target="../word/document.xml"/></Relationships>'
            ),
        ),
        usable_docx_bytes(
            document=(
                b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                b"<w:body><w:p/></w:body></w:document>"
            ),
            relationships=(
                b'<Relationships><Relationship Id="rId1" '
                b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                b'Target="word/document.xml"/></Relationships>'
            ),
        ),
        usable_docx_bytes(
            document=(
                b'<w:document xmlns:w="urn:wrong-wordprocessingml">'
                b"<w:body><w:p/></w:body></w:document>"
            ),
        ),
    ],
)
@pytest.mark.parametrize("skill_id", ["qa-file-reviewer", "baoyu-translate"])
def test_runtime_collection_rejects_unusable_required_docx(tmp_path, content, skill_id):
    workspace = tmp_path / "workspace"
    output = workspace / "output"
    output.mkdir(parents=True)
    (output / "review.docx").write_bytes(content)
    storage = FakeStorage()

    artifacts = collect_legacy_artifacts(payload(skill_id=skill_id), workspace, storage)

    assert artifacts == []
    assert storage.stored == []


@pytest.mark.parametrize("skill_id", ["qa-file-reviewer", "baoyu-translate"])
@pytest.mark.parametrize(
    ("limit_name", "limit_value", "content"),
    [
        (
            "_REQUIRED_DOCX_MAX_ENTRY_COUNT",
            3,
            usable_docx_bytes(document=valid_docx_bytes(), extra_entries={"extra.txt": b"x"}),
        ),
        ("_REQUIRED_DOCX_MAX_COMPRESSED_BYTES", 1, valid_docx_bytes()),
        ("_REQUIRED_DOCX_MAX_UNCOMPRESSED_BYTES", 1, valid_docx_bytes()),
    ],
)
def test_runtime_collection_rejects_required_docx_zip_bounds_before_read(
    monkeypatch, tmp_path, skill_id, limit_name, limit_value, content
):
    workspace = tmp_path / "workspace"
    output = workspace / "output"
    output.mkdir(parents=True)
    (output / "review.docx").write_bytes(content)
    monkeypatch.setattr(deliverable_runtime, limit_name, limit_value)

    def fail_read(*_args, **_kwargs):
        raise AssertionError("bounded metadata rejection must happen before archive.read")

    monkeypatch.setattr(zipfile.ZipFile, "read", fail_read)
    artifacts = collect_legacy_artifacts(payload(skill_id=skill_id), workspace, FakeStorage())

    assert artifacts == []


def test_runtime_collection_rejects_duplicate_case_colliding_docx_before_read(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    output = workspace / "output"
    output.mkdir(parents=True)
    path = output / "review.docx"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            b'<Override PartName="/word/document.xml" '
            b'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            b"</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            b'<Relationship Id="rId1" '
            b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            b'Target="word/document.xml"/></Relationships>',
        )
        archive.writestr(
            "word/document.xml",
            b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            b"<w:body><w:p/></w:body></w:document>",
        )
        archive.writestr("WORD/DOCUMENT.XML", b"duplicate")

    def fail_read(*_args, **_kwargs):
        raise AssertionError("unsafe archive metadata must fail before archive.read")

    monkeypatch.setattr(zipfile.ZipFile, "read", fail_read)
    artifacts = collect_legacy_artifacts(payload(skill_id="qa-file-reviewer"), workspace, FakeStorage())

    assert artifacts == []


@pytest.mark.parametrize(
    ("skill_id", "expected_type"),
    [("qa-file-reviewer", "reviewed_docx"), ("baoyu-translate", "translated_docx")],
)
def test_runtime_collection_accepts_usable_required_docx(tmp_path, skill_id, expected_type):
    workspace = tmp_path / "workspace"
    output = workspace / "output"
    output.mkdir(parents=True)
    content = valid_docx_bytes()
    (output / "review.docx").write_bytes(content)
    storage = FakeStorage()

    artifacts = collect_legacy_artifacts(payload(skill_id=skill_id), workspace, storage)

    assert [artifact.artifact_type for artifact in artifacts] == [expected_type]
    assert storage.stored[0][1] == content


@pytest.mark.parametrize(
    ("relationship_id", "accepted"),
    [
        ("关系\u0301", True),
        ("Ångström", True),
        ("", False),
        ("1relationship", False),
        ("relationship:id", False),
        ("relationship id", False),
    ],
)
def test_runtime_collection_validates_docx_relationship_ids(tmp_path, relationship_id, accepted):
    workspace = tmp_path / "workspace"
    output = workspace / "output"
    output.mkdir(parents=True)
    relationships = (
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + f'<Relationship Id="{relationship_id}" '.encode()
        + b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        + b'Target="word/document.xml"/></Relationships>'
    )
    (output / "review.docx").write_bytes(
        usable_docx_bytes(
            document=(
                b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                b"<w:body><w:p/></w:body></w:document>"
            ),
            relationships=relationships,
        )
    )
    storage = FakeStorage()

    artifacts = collect_legacy_artifacts(payload(skill_id="qa-file-reviewer"), workspace, storage)

    assert bool(artifacts) is accepted
    assert bool(storage.stored) is accepted


@pytest.mark.parametrize(
    "relationships",
    [
        (
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            b'<Relationship Id="rId1" Type="urn:example:other" Target="custom.xml"/>'
            b"</Relationships>"
        ),
        (
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            b'<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            b"</Relationships>"
        ),
    ],
)
def test_runtime_collection_rejects_ambiguous_docx_relationships(tmp_path, relationships):
    workspace = tmp_path / "workspace"
    output = workspace / "output"
    output.mkdir(parents=True)
    (output / "review.docx").write_bytes(
        usable_docx_bytes(
            document=(
                b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                b"<w:body><w:p/></w:body></w:document>"
            ),
            relationships=relationships,
        )
    )
    storage = FakeStorage()

    artifacts = collect_legacy_artifacts(payload(skill_id="qa-file-reviewer"), workspace, storage)

    assert artifacts == []
    assert storage.stored == []
