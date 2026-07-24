import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import hashlib
import json
import types
from pathlib import Path

import pytest

from app.runtime.sandbox.container_provider import NativeToolAdmissionError

import app.worker as worker_module
from app import repositories as repository_module
from app.executors.base import (
    ArtifactManifest,
    ExecutorResult,
    RunExecutionOwner,
    RunPayload,
)
from app.executors.claude_agent_worker import ClaudeAgentWorkerAdapter
from app.executors.fake import FakeFailureAdapter, FakeSuccessAdapter
from app.executors.registry import AdapterRegistry
from app.models import QueueRunPayload
from app.repositories import RepositoryConflictError, RepositoryNotFoundError, ToolPermissionTerminalizationProgress
from app.worker import (
    WorkerOutcome,
    _locked_run_principal,
    _multi_agent_result_summary,
    _payload_from_locked_run,
    _record_run_step_from_event,
    parse_queue_payload,
    process_run_payload,
)
from app.auth import AuthPrincipal, is_ai_admin
from app.runtime.sandbox import container_provider
from app.skills.execution_profiles import resolve_skill_execution_profile


RELEASE_DECISION_SCHEMA_VERSION = "ai-platform.skill-release-decision.v1"
_CURRENT_QUEUE_PAYLOAD = None
_ORIGINAL_ENSURE_MCP_TOOL_ACTIVE = repository_module.ensure_mcp_tool_active


def test_worker_preserves_only_the_fixed_native_tool_admission_failure():
    private_token = "private-native-token"
    private_path = "/home/private/workspace/native-tool.sock"
    native_error = NativeToolAdmissionError()
    native_error.__context__ = RuntimeError(f"{private_token} at {private_path}")

    assert worker_module._executor_exception_failure(native_error) == (
        "native_tool_admission_failed",
        "Native tool sandbox admission failed",
    )
    assert worker_module._executor_exception_failure(
        RuntimeError("ordinary executor failure")
    ) == ("executor_failure", "ordinary executor failure")
    assert private_token not in str(worker_module._executor_exception_failure(native_error))
    assert private_path not in str(worker_module._executor_exception_failure(native_error))


@pytest.mark.asyncio
async def test_worker_submit_monitor_preserves_normal_terminal_result():
    expected = FakeSuccessAdapter()
    skill_version = "hash-general-chat"
    payload = RunPayload(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        attempt_id="qat-test-attempt",
        agent_id="general-agent",
        skill_id="general-chat",
        file_ids=[],
        input={},
        skill_version=skill_version,
        release_decision=release_decision(skill_version),
        skill_manifests=[primary_manifest("general-chat", skill_version)],
    )
    cancel_checks = 0

    async def cancel_requested():
        nonlocal cancel_checks
        cancel_checks += 1
        return False

    result = await worker_module._submit_run_until_cancelled(
        expected,
        payload,
        event_sink=None,
        cancel_requested=cancel_requested,
        poll_interval_seconds=0.01,
    )

    assert result.status == "succeeded"
    assert cancel_checks <= 1


@pytest.mark.asyncio
async def test_worker_submit_monitor_external_cancellation_stops_registered_owner():
    started = asyncio.Event()
    calls: list[tuple[str, str] | tuple[str]] = []

    class OwnedAdapter:
        async def submit_run(self, payload, event_sink=None, execution_owner=None):
            async def stop_remote(reason: str):
                calls.append(("stop", reason))
                return True

            execution_owner.register_stop(stop_remote)
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                calls.append(("adapter_cancelled",))
                raise

    skill_version = "hash-general-chat"
    payload = RunPayload(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        attempt_id="qat-test-attempt",
        agent_id="general-agent",
        skill_id="general-chat",
        file_ids=[],
        input={},
        skill_version=skill_version,
        release_decision=release_decision(skill_version),
        skill_manifests=[primary_manifest("general-chat", skill_version)],
    )

    task = asyncio.create_task(
        worker_module._submit_run_until_cancelled(
            OwnedAdapter(),
            payload,
            event_sink=None,
            cancel_requested=lambda: _async_false(),
            poll_interval_seconds=0.01,
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls == [("stop", "worker_interrupted"), ("adapter_cancelled",)]


async def _async_false() -> bool:
    return False


def test_authoritative_leased_envelope_extracts_attempt_before_extra_forbid():
    raw = base_payload()
    raw["_queue_attempt_id"] = "qat-test-attempt"

    envelope = worker_module.parse_leased_queue_envelope(raw)

    assert envelope.attempt_id == "qat-test-attempt"
    assert envelope.payload.run_id == "run-a"
    assert "_queue_attempt_id" not in envelope.payload.model_dump()
    with pytest.raises(worker_module.InvalidLeasedQueueEnvelope):
        worker_module.parse_leased_queue_envelope({key: value for key, value in raw.items() if key != "_queue_attempt_id"})
    with pytest.raises(worker_module.InvalidLeasedQueueEnvelope):
        worker_module.parse_leased_queue_envelope({**raw, "_queue_attempt_id": ""})


@pytest.mark.asyncio
async def test_run_execution_owner_bounds_non_cooperative_stop_without_detaching_writer():
    release_writer = asyncio.Event()
    writer_stopped = asyncio.Event()

    async def non_cooperative_writer():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release_writer.wait()
            writer_stopped.set()
            raise

    owner = RunExecutionOwner("run-a")
    owner.start(non_cooperative_writer())
    await asyncio.sleep(0)

    first = await owner.stop(reason="cancel_requested", timeout_seconds=0.01)

    assert first.status == "timed_out"
    assert first.quiescent is False
    assert owner.done is False
    assert writer_stopped.is_set() is False

    release_writer.set()
    second = await owner.stop(reason="cancel_requested", timeout_seconds=0.2)

    assert second.status == "quiescent"
    assert second.quiescent is True
    assert owner.done is True
    assert writer_stopped.is_set() is True


@pytest.mark.asyncio
async def test_run_execution_owner_reports_registered_stop_failure_until_retry_quiesces():
    attempts = 0

    async def live_writer():
        await asyncio.Event().wait()

    async def stop_remote(reason: str):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return False
        return True

    owner = RunExecutionOwner("run-a")
    owner.start(live_writer())
    owner.register_stop(stop_remote)
    await asyncio.sleep(0)

    first = await owner.stop(reason="cancel_requested", timeout_seconds=0.1)
    second = await owner.stop(reason="cancel_requested", timeout_seconds=0.1)

    assert first.status == "failed"
    assert first.quiescent is False
    assert second.status == "quiescent"
    assert second.quiescent is True
    assert attempts == 2


@pytest.mark.asyncio
async def test_run_execution_owner_bounds_non_cooperative_provider_stop_without_duplicate_attempts():
    stop_started = asyncio.Event()
    allow_stop = asyncio.Event()
    attempts = 0

    async def live_writer():
        await asyncio.Event().wait()

    async def non_cooperative_stop(reason: str):
        nonlocal attempts
        attempts += 1
        stop_started.set()
        try:
            await allow_stop.wait()
        except asyncio.CancelledError:
            await allow_stop.wait()
        return True

    owner = RunExecutionOwner("run-a")
    owner.start(live_writer())
    owner.register_stop(non_cooperative_stop)

    first = await asyncio.wait_for(
        owner.stop(reason="cancel_requested", timeout_seconds=0.01),
        timeout=0.1,
    )
    await stop_started.wait()
    second = await asyncio.wait_for(
        owner.stop(reason="cancel_requested", timeout_seconds=0.01),
        timeout=0.1,
    )

    assert first.status == "timed_out"
    assert second.status == "timed_out"
    assert attempts == 1

    allow_stop.set()
    final = await owner.stop(reason="cancel_requested", timeout_seconds=0.2)

    assert final.quiescent is True
    assert owner.done is True


@pytest.mark.asyncio
async def test_worker_submit_monitor_emits_truthful_silent_progress_without_assistant_tokens():
    finish = asyncio.Event()
    events = []

    class SilentAdapter:
        async def submit_run(self, payload, event_sink=None):
            await finish.wait()
            return ExecutorResult(
                status="succeeded",
                adapter_version="silent/1",
                executor_type="silent",
                executor_version="silent/1",
                capabilities={"streaming": True},
                result={"message": "done"},
            )

    async def event_sink(**event):
        events.append(event)
        finish.set()

    async def cancel_requested():
        return False

    skill_version = "hash-general-chat"
    payload = RunPayload(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        attempt_id="qat-test-attempt",
        agent_id="general-agent",
        skill_id="general-chat",
        file_ids=[],
        input={},
        skill_version=skill_version,
        release_decision=release_decision(skill_version),
        skill_manifests=[primary_manifest("general-chat", skill_version)],
    )

    result = await worker_module._submit_run_until_cancelled(
        SilentAdapter(),
        payload,
        event_sink=event_sink,
        cancel_requested=cancel_requested,
        poll_interval_seconds=0.005,
        progress_interval_seconds=0.005,
    )

    assert result.status == "succeeded"
    assert [event["event_type"] for event in events] == ["run_started"]
    assert events[0]["payload"]["progress_kind"] == "active"
    assert events[0]["payload"]["heartbeat"] is True
    assert "delta" not in events[0]["payload"]


@pytest.mark.parametrize(
    ("stored_role", "expected_admin"),
    [
        (" PLATFORM_ADMIN ", True),
        ("platform-admin", False),
        ("platform admin", False),
    ],
)
def test_worker_role_identity_matches_shared_normalization(stored_role, expected_admin):
    principal = _locked_run_principal(
        {
            "principal_roles": [stored_role],
            "principal_department_id": "qa",
            "auth_source": "session-token",
        },
        {
            "tenant_id": "tenant-a",
            "user_id": "user-a",
        },
    )

    assert principal.roles == [stored_role.strip().lower()]
    assert is_ai_admin(principal) is expected_admin


def release_decision(version: str) -> dict:
    return {
        "schema_version": RELEASE_DECISION_SCHEMA_VERSION,
        "policy_active": False,
        "selected_version": version,
        "selected_track": "manifest_pin",
    }


def primary_manifest(skill_id: str, version: str) -> dict:
    return {
        "skill_id": skill_id,
        "version": version,
        "content_hash": version,
        "source": {"kind": "builtin", "asset_dir": skill_id},
        "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
        "dependency_ids": [],
        "builtin_tool_identities": ["Bash", "Write"] if skill_id == "qa-file-reviewer" else [],
        "mcp_tool_ids": [skill_id] if skill_id == "ragflow-knowledge-search" else [],
        "snapshot_governance": snapshot_governance(version),
        "allowed": True,
        "staged": False,
        "used": False,
    }


def test_worker_projects_reviewed_uploaded_skill_local_tools_from_server_profile():
    profile = resolve_skill_execution_profile(
        skill_id="native-review",
        source_kind="uploaded",
        lifecycle_status="released",
    )
    payload = parse_queue_payload(
        base_payload(
            _leased=False,
            skill_id="native-review",
            skill_version="hash-native",
            skill_manifests=[
                {
                    "skill_id": "native-review",
                    "version": "hash-native",
                    "content_hash": "hash-native",
                    "source": {"kind": "uploaded"},
                    "files": [
                        {
                            "relative_path": "SKILL.md",
                            "content_base64": "c2tpbGw=",
                            "size_bytes": 5,
                        }
                    ],
                    "dependency_ids": [],
                    "lifecycle_status": "released",
                    "execution_profile": profile,
                    "builtin_tool_identities": profile["builtin_tool_identities"],
                    "mcp_tool_ids": [],
                    "allowed": True,
                    "staged": False,
                    "used": False,
                }
            ],
        )
    )

    subjects = worker_module._builtin_capability_subjects(
        payload=payload,
        run_identity={"skill_id": "native-review"},
        skill={"skill_id": "native-review", "skill_status": "active"},
        skill_decision=types.SimpleNamespace(usable=True),
    )
    by_identity = {subject["identity"]: subject for subject in subjects}

    assert set(by_identity) == {"Skill", "Read", "Glob", "LS", "Bash", "Write", "Edit"}
    assert all(
        subject["declared_identities"] == [subject["identity"]]
        for subject in subjects
    )
    assert container_provider._native_tool_required(
        types.SimpleNamespace(tool_policy_subjects=subjects)
    )
    assert by_identity["Bash"]["command_isolation"] == "sibling-tool-sandbox-v1"
    assert by_identity["Bash"]["execution_strategy"] == "sdk_native"
    assert by_identity["Read"]["required_parameter_keys"] == ["file_path"]
    assert by_identity["Skill"]["allowed_skill_names"] == ["native-review"]


def test_general_chat_catalog_aggregation_drives_mount_and_native_bash_admission():
    primary = primary_manifest("general-chat", "hash-general")
    catalog_profile = resolve_skill_execution_profile(
        skill_id="minimax-docx",
        source_kind="builtin",
        lifecycle_status="released",
    )
    catalog_manifest = primary_manifest("minimax-docx", "hash-minimax")
    catalog_manifest.update(
        {
            "lifecycle_status": "released",
            "execution_profile": catalog_profile,
            "builtin_tool_identities": catalog_profile["builtin_tool_identities"],
        }
    )
    payload = parse_queue_payload(
        base_payload(
            _leased=False,
            skill_id="general-chat",
            skill_version="hash-general",
            skill_manifests=[primary],
        )
    )

    subjects = worker_module._builtin_capability_subjects(
        payload=payload,
        run_identity={"skill_id": "general-chat"},
        skill={"skill_id": "general-chat", "skill_status": "active"},
        skill_decision=types.SimpleNamespace(usable=True),
        authorized_skill_manifests=[catalog_manifest],
        authorized_skill_names=["minimax-docx"],
    )
    by_identity = {subject["identity"]: subject for subject in subjects}
    runtime_request = types.SimpleNamespace(tool_policy_subjects=subjects)

    assert by_identity["Skill"]["execution_strategy"] == "sdk_restricted"
    assert by_identity["Skill"]["allowed_skill_names"] == ["minimax-docx"]
    assert by_identity["Bash"]["execution_strategy"] == "sdk_native"
    assert by_identity["Bash"]["command_isolation"] == "sibling-tool-sandbox-v1"
    assert container_provider._staged_skill_mount_required(runtime_request)
    assert container_provider._native_tool_required(runtime_request)


def test_worker_keeps_legacy_uploaded_skill_restricted_to_skill_loader():
    manifest = primary_manifest("native-review", "hash-native")
    manifest["source"] = {"kind": "uploaded"}
    manifest["builtin_tool_identities"] = []
    manifest["dependency_ids"] = ["minimax-docx"]
    dependency = primary_manifest("minimax-docx", "hash-minimax")
    dependency["builtin_tool_identities"] = ["Bash", "Write"]
    payload = parse_queue_payload(
        base_payload(
            _leased=False,
            skill_id="native-review",
            skill_version="hash-native",
            skill_manifests=[manifest, dependency],
        )
    )

    subjects = worker_module._builtin_capability_subjects(
        payload=payload,
        run_identity={"skill_id": "native-review"},
        skill={"skill_id": "native-review", "skill_status": "active"},
        skill_decision=types.SimpleNamespace(usable=True),
    )

    assert [subject["identity"] for subject in subjects] == ["Skill"]
    assert subjects[0]["execution_strategy"] == "sdk_restricted"
    assert not container_provider._native_tool_required(
        types.SimpleNamespace(tool_policy_subjects=subjects)
    )


def snapshot_governance(digest: str = "hash-a") -> dict:
    return {
        "schema_version": "ai-platform.skill-pinned-snapshot-governance.v1",
        "snapshot_source": "platform_release_lock",
        "release_lock": {"schema_version": RELEASE_DECISION_SCHEMA_VERSION, "mode": "manifest_pin"},
        "manifest": {"source_kind": "builtin", "selected_file_count": 1},
        "selected_files": [
            {
                "relative_path": "SKILL.md",
                "size_bytes": 5,
                "sha256": "9c53c074d7ac6a2728b638ac1f376c5fa9eb8f71603017c3ea638c2fd40548df",
            }
        ],
        "dependency_evidence": {
            "status": "review_required",
            "ref": "skill_dependency_policy",
            "dependency_count": 1,
        },
        "does_not_close_b4_or_211": True,
    }


def primary_manifest_version(skill_id: str, manifests: list[dict]) -> str:
    for manifest in manifests:
        if manifest.get("skill_id") == skill_id:
            return str(manifest.get("content_hash") or manifest.get("version") or "")
    return ""


def reviewed_docx_artifact() -> ArtifactManifest:
    return ArtifactManifest(
        artifact_type="reviewed_docx",
        label="Reviewed Word",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        storage_key="tenants/tenant-a/runs/run-a/artifacts/reviewed.docx",
        size_bytes=1,
    )


@asynccontextmanager
async def fake_transaction():
    yield object()


async def fake_append_message(*args, **kwargs):
    return "msg-a"


def _test_current_principal(
    *,
    user_id: str,
    tenant_id: str,
    department_id: str = "",
    roles: list[str] | None = None,
    source: str = "test-current-principal",
) -> AuthPrincipal:
    return AuthPrincipal(
        user_id=user_id,
        display_name=user_id,
        tenant_id=tenant_id,
        department_id=department_id,
        roles=list(["user"] if roles is None else roles),
        permissions=["agent:use"],
        source=source,
    )


@pytest.fixture(autouse=True)
def default_cancel_not_requested(monkeypatch):
    global _CURRENT_QUEUE_PAYLOAD
    _CURRENT_QUEUE_PAYLOAD = None

    def capture_queue_payload(raw):
        global _CURRENT_QUEUE_PAYLOAD
        parsed = parse_queue_payload(raw)
        _CURRENT_QUEUE_PAYLOAD = parsed.model_dump(mode="json")
        return parsed

    def materialize_legacy_locked_run(locked_run, *, run_identity):
        if locked_run is True:
            locked_run = locked_run_from_payload(_CURRENT_QUEUE_PAYLOAD)
        return _payload_from_locked_run(locked_run, run_identity=run_identity)

    monkeypatch.setattr("app.worker.parse_queue_payload", capture_queue_payload)
    monkeypatch.setattr("app.worker._payload_from_locked_run", materialize_legacy_locked_run)

    async def resolve_test_current_principal(*, user_id, tenant_id):
        return _test_current_principal(user_id=user_id, tenant_id=tenant_id)

    monkeypatch.setattr(
        "app.worker.resolve_current_principal",
        resolve_test_current_principal,
        raising=False,
    )

    async def is_cancel_requested(conn, *, tenant_id, run_id):
        return False

    monkeypatch.setattr("app.worker.repositories.is_cancel_requested", is_cancel_requested, raising=False)

    async def complete_run(conn, **kwargs):
        return True

    async def fail_run(conn, **kwargs):
        return ToolPermissionTerminalizationProgress(
            completed=True,
            status="failed",
            did_transition=True,
        )

    async def classify_success_commit_block(conn, **kwargs):
        return "stale_terminal_state"

    async def drain_run_tool_permission_terminalization(**kwargs):
        return None

    async def create_artifact(conn, **kwargs):
        return None

    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run, raising=False)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run, raising=False)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact, raising=False)
    monkeypatch.setattr(
        "app.worker.repositories.classify_success_commit_block",
        classify_success_commit_block,
        raising=False,
    )
    monkeypatch.setattr(
        "app.worker.drain_run_tool_permission_terminalization",
        drain_run_tool_permission_terminalization,
        raising=False,
    )

    async def has_pending_tool_permission_requests(conn, *, tenant_id, run_id):
        return False

    monkeypatch.setattr(
        "app.worker.repositories.has_pending_tool_permission_requests",
        has_pending_tool_permission_requests,
        raising=False,
    )

    async def validate_run_skill_snapshots_for_dispatch(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "app.worker.repositories.validate_run_skill_snapshots_for_dispatch",
        validate_run_skill_snapshots_for_dispatch,
        raising=False,
    )

    async def validate_replay_skill_manifests(*_args, **kwargs):
        manifests = kwargs.get("skill_manifests") or []
        primary = next(
            (item for item in manifests if item.get("skill_id") == kwargs.get("skill_id")),
            {},
        )
        return list(primary.get("mcp_tool_ids") or [])

    monkeypatch.setattr(
        "app.worker.repositories.validate_replay_skill_manifests",
        validate_replay_skill_manifests,
        raising=False,
    )

    async def get_context_snapshot_for_worker(conn, **kwargs):
        return {
            "id": kwargs["context_snapshot_id"],
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "session_id": kwargs["session_id"],
            "run_id": kwargs["run_id"],
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": "executor",
            "included_message_ids": [],
            "included_file_ids": ["file-a"],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "redaction_summary_json": {},
            "payload_json": {
                "schema_version": "ai-platform.context-snapshot.v1",
                "source": "test",
                "message_count": 0,
                "file_count": 1,
                "memory_record_count": 0,
            },
            "created_at": None,
        }

    monkeypatch.setattr(
        "app.worker.repositories.get_context_snapshot_for_worker",
        get_context_snapshot_for_worker,
        raising=False,
    )

    async def reconcile_multi_agent_child_run_terminal_state(conn, **kwargs):
        return None

    monkeypatch.setattr(
        "app.worker.repositories.reconcile_multi_agent_child_run_terminal_state",
        reconcile_multi_agent_child_run_terminal_state,
        raising=False,
    )

    async def finalize_multi_agent_parent_run_if_ready(conn, **kwargs):
        return None

    monkeypatch.setattr(
        "app.worker.repositories.finalize_multi_agent_parent_run_if_ready",
        finalize_multi_agent_parent_run_if_ready,
        raising=False,
    )

    async def reconcile_terminalized_permission_run(*, tenant_id, run_id, progress, transaction_factory):
        if not progress.did_transition or not progress.needs_reconcile:
            return None
        async with transaction_factory() as conn:
            return await repository_module.reconcile_multi_agent_child_run_terminal_state(
                conn,
                tenant_id=tenant_id,
                child_run_id=run_id,
                child_status=str(progress.status or ""),
            )

    monkeypatch.setattr(
        "app.worker.reconcile_terminalized_permission_run",
        reconcile_terminalized_permission_run,
        raising=False,
    )

    async def create_sandbox_lease(conn, **kwargs):
        return {
            "id": "lease-test-default",
            **kwargs,
        }

    async def release_sandbox_lease(conn, **kwargs):
        return {
            "id": kwargs["lease_id"],
            "status": "released",
            **kwargs,
        }

    monkeypatch.setattr("app.worker.repositories.create_sandbox_lease", create_sandbox_lease, raising=False)
    monkeypatch.setattr("app.worker.repositories.release_sandbox_lease", release_sandbox_lease, raising=False)
    monkeypatch.setattr("app.worker._PARENT_ROLLUP_RETRY_DELAY_SECONDS", 0, raising=False)

    async def resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        executor_type = "ragflow" if skill_id == "ragflow-knowledge-search" else "fake"
        return {
            "agent_id": agent_id,
            "agent_status": "active",
            "skill_id": skill_id,
            "skill_status": "active",
            "executor_type": executor_type,
            "backing_mcp_tool_id": skill_id if executor_type == "ragflow" else None,
        }

    async def get_capability_distribution_row(conn, *, tenant_id, capability_kind, capability_id):
        return {
            "tenant_id": tenant_id,
            "capability_kind": capability_kind,
            "capability_id": capability_id,
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": [],
            "allowed_roles": [],
        }

    async def get_mcp_tool_registry_entry(conn, *, tenant_id, tool_id):
        if (
            tool_id == "ragflow-knowledge-search"
            and repository_module.ensure_mcp_tool_active is not _ORIGINAL_ENSURE_MCP_TOOL_ACTIVE
        ):
            try:
                policy = await repository_module.ensure_mcp_tool_active(
                    conn,
                    tenant_id=tenant_id,
                    tool_id=tool_id,
                )
            except RepositoryConflictError:
                return {
                    "tool_id": tool_id,
                    "server_id": "default-server",
                    "effective_status": "disabled",
                    "server_status": "active",
                    "visible_to_user": True,
                    "write_capable": False,
                    "risk_level": "low",
                }
            return {
                "tool_id": str(policy.get("tool_id") or policy.get("id") or tool_id),
                "server_id": str(policy.get("server_id") or "default-server"),
                "registry_status": str(policy.get("registry_status") or policy.get("status") or "active"),
                "policy_status": str(policy.get("policy_status") or policy.get("status") or "active"),
                "effective_status": str(policy.get("effective_status") or policy.get("status") or "active"),
                "server_status": str(policy.get("server_status") or "active"),
                "visible_to_user": bool(policy.get("visible_to_user", True)),
                "write_capable": bool(policy.get("write_capable")),
                "risk_level": str(policy.get("risk_level") or "low"),
                "allowed_tools": ["query"],
                "transport_type": "streamable_http",
                "endpoint": "https://mcp.example.test/v1",
                "auth_mode": "none",
            }
        return {
            "tool_id": tool_id,
            "server_id": "default-server",
            "registry_status": "active",
            "policy_status": "active",
            "effective_status": "active",
            "server_status": "active",
            "visible_to_user": True,
            "write_capable": False,
            "risk_level": "low",
            "allowed_tools": ["query"],
            "transport_type": "streamable_http",
            "endpoint": "https://mcp.example.test/v1",
            "auth_mode": "none",
        }

    async def append_audit_log(conn, **kwargs):
        return "audit-default"

    monkeypatch.setattr("app.worker.repositories.resolve_agent_skill", resolve_agent_skill, raising=False)
    monkeypatch.setattr("app.worker.repositories.resolve_selected_skill", resolve_agent_skill, raising=False)
    monkeypatch.setattr(
        "app.worker.repositories.get_capability_distribution_row",
        get_capability_distribution_row,
        raising=False,
    )
    monkeypatch.setattr(
        "app.worker.repositories.get_mcp_tool_registry_entry",
        get_mcp_tool_registry_entry,
        raising=False,
    )
    monkeypatch.setattr("app.worker.repositories.append_audit_log", append_audit_log, raising=False)

    class _DefaultCatalogSnapshot:
        def __init__(self, skill_id, materialized_skill_ids):
            self.available_skill_ids = (skill_id,)
            self.materialized_skill_ids = materialized_skill_ids
            self._skill_id = skill_id

        def entry(self, skill_id):
            if skill_id != self._skill_id:
                return None
            return types.SimpleNamespace(available=True)

    class _DefaultCatalogResolution:
        def __init__(self, skill_id, manifests):
            self.manifests = list(manifests)
            materialized_skill_ids = (
                tuple(
                    str(manifest.get("skill_id") or "")
                    for manifest in self.manifests
                    if isinstance(manifest, dict) and str(manifest.get("skill_id") or "")
                )
                if skill_id != "general-chat"
                else ()
            )
            self.snapshot = _DefaultCatalogSnapshot(skill_id, materialized_skill_ids)

        def runtime_input_updates(self):
            return {}

    async def resolve_authorized_skill_catalog(*_args, **kwargs):
        binding = kwargs["binding"]
        return _DefaultCatalogResolution(
            binding.selected_skill_id,
            kwargs.get("pinned_manifests") or [],
        )

    monkeypatch.setattr(
        "app.worker.resolve_authorized_skill_catalog",
        resolve_authorized_skill_catalog,
        raising=False,
    )


@pytest.mark.asyncio
async def test_reused_step_event_clears_checkpoint_reuse_pending(monkeypatch):
    calls = []

    async def upsert_run_step(conn, **kwargs):
        calls.append(kwargs)
        return "step-a"

    monkeypatch.setattr("app.worker.repositories.upsert_run_step", upsert_run_step, raising=False)

    await _record_run_step_from_event(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        event_type="agent_step_reused",
        message="coding agent reused checkpoint",
        payload={
            "role": "coding",
            "step_key": "code",
            "step_index": 1,
            "checkpoint_reused": True,
            "output": "code output",
        },
    )

    assert calls[0]["payload_json"]["checkpoint_reused"] is True
    assert calls[0]["payload_json"]["checkpoint_reuse_pending"] is False


@pytest.mark.asyncio
async def test_completed_step_event_materializes_source_step_id_for_checkpoint(monkeypatch):
    calls = []

    async def upsert_run_step(conn, **kwargs):
        calls.append(kwargs)
        return "step-created"

    monkeypatch.setattr("app.worker.repositories.upsert_run_step", upsert_run_step, raising=False)

    await _record_run_step_from_event(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        event_type="agent_step_completed",
        message="coding agent completed",
        payload={
            "role": "coding",
            "step_key": "code",
            "step_index": 1,
            "checkpoint_id": "checkpoint-run-a-code",
            "output": "code output",
        },
    )

    assert calls[0]["payload_json"]["checkpoint_id"] == "checkpoint-run-a-code"
    assert "source_step_id" not in calls[0]["payload_json"]
    assert calls[1]["payload_json"] == {"source_step_id": "step-created"}


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["agent_step_started", "agent_step_completed", "agent_step_failed", "agent_step_blocked"])
async def test_non_pending_step_event_clears_checkpoint_reuse_pending(monkeypatch, event_type):
    calls = []

    async def upsert_run_step(conn, **kwargs):
        calls.append(kwargs)
        return "step-a"

    monkeypatch.setattr("app.worker.repositories.upsert_run_step", upsert_run_step, raising=False)

    await _record_run_step_from_event(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        event_type=event_type,
        message="agent step progressed",
        payload={
            "role": "coding",
            "step_key": "code",
            "step_index": 1,
            "checkpoint_reuse_pending": True,
        },
    )

    assert calls[0]["payload_json"]["checkpoint_reuse_pending"] is False


def base_payload(**overrides):
    leased = overrides.pop("_leased", True)
    skill_id = overrides.get("skill_id", "qa-file-reviewer")
    default_version = f"hash-{skill_id}"
    payload = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "agent_id": "qa-word-review",
        "skill_id": skill_id,
        "file_ids": ["file-a"],
        "input": {"mode": "file"},
        "executor_type": "fake",
        "skill_version": default_version,
        "release_decision": release_decision(default_version),
        "skill_manifests": [primary_manifest(skill_id, default_version)],
        "context_snapshot_id": "ctx-existing",
        "context_snapshot": {
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_snapshot_id": "ctx-existing",
            "source": "test",
            "message_count": 0,
            "file_count": 1,
            "memory_record_count": 0,
        },
    }
    if leased:
        payload["_queue_attempt_id"] = "qat-test-attempt"
    payload.update(overrides)
    manifests = payload.get("skill_manifests") or []
    if "skill_version" not in overrides:
        locked_version = primary_manifest_version(payload["skill_id"], manifests) or payload.get("skill_version")
    else:
        locked_version = payload.get("skill_version")
    if locked_version:
        payload["skill_version"] = locked_version
        if "release_decision" not in overrides:
            payload["release_decision"] = release_decision(locked_version)
    return payload


def test_worker_propagates_exact_authorized_mcp_subject_without_permission_lookup_or_consume():
    payload = QueueRunPayload.model_validate(
        {key: value for key, value in base_payload(input={"mode": "file", "mcp_tool_ids": ["corp-search"]}).items() if key != "_queue_attempt_id"}
    )
    tool = {
        "tool_id": "corp-search",
        "server_id": "corp:search",
        "name": "query",
        "registry_status": "active",
        "policy_status": "active",
        "server_status": "active",
        "risk_level": "high",
        "write_capable": True,
        "transport_type": "streamable_http",
        "endpoint": "https://mcp.example.test/v1",
        "auth_mode": "none",
        "allowed_tools": ["query"],
    }
    subject = worker_module._mcp_capability_subject(
        tool,
        types.SimpleNamespace(usable=True),
    )
    authorized = worker_module._payload_with_authorized_mcp_registration(
        payload,
        allowed_entries=[tool],
        tool_policy_subjects=[subject],
    )

    assert authorized.input["mcp_tool_ids"] == ["corp-search"]
    assert authorized.input["_runtime_tool_policy_subjects"] == [
        {
            **subject,
            "identity": "mcp__corp:search__query",
        }
    ]
    assert subject["mcp_server_config"] == {
        "type": "http",
        "url": "https://mcp.example.test/v1",
    }
    assert worker_module._mcp_capability_subject(
        {**tool, "endpoint": "https://token@example.test/v1"},
        types.SimpleNamespace(usable=True),
    ) is None
    assert worker_module._mcp_capability_subject(
        {**tool, "auth_mode": "api-key"},
        types.SimpleNamespace(usable=True),
    ) is None
    assert worker_module._mcp_capability_subject(
        {**tool, "endpoint": "https://mcp.example.test/v1?api_key=redacted"},
        types.SimpleNamespace(usable=True),
    ) is None
    assert worker_module._mcp_capability_subject(
        {**tool, "endpoint": "https://mcp.example.test/v1?token=redacted"},
        types.SimpleNamespace(usable=True),
    ) is None
    assert worker_module._mcp_capability_subject(
        {**tool, "endpoint": "https://mcp.example.test/v1#fragment"},
        types.SimpleNamespace(usable=True),
    ) is None
    source = (Path(__file__).parents[1] / "app" / "worker.py").read_text(encoding="utf-8")
    assert "get_exact_tool_permission_decision(" not in source
    assert "consume_tool_permission_decision(" not in source


@pytest.mark.asyncio
async def test_registry_entry_returns_tenant_scoped_external_mcp_runtime_metadata(monkeypatch):
    monkeypatch.undo()
    class Cursor:
        async def fetchone(self):
            return {
                "tool_id": "corp-search",
                "server_id": "corp:search",
                "name": "中文展示名",
                "description": "search",
                "transport_type": "streamable_http",
                "endpoint": "https://mcp.example.test/v1",
                "auth_mode": "none",
                "allowed_tools": ["query"],
                "registry_status": "active",
                "server_status": "active",
                "registry_write_capable": False,
                "registry_risk_level": "low",
                "registry_visible_to_user": True,
                "policy_status": "active",
                "policy_write_capable": False,
                "policy_risk_level": "low",
                "policy_visible_to_user": True,
            }

    class Connection:
        async def execute(self, query, params):
            assert params == ("tenant-a", "corp-search")
            assert "mcp_tools.endpoint" in query
            assert "mcp_tools.auth_mode" in query
            assert "mcp_tools.allowed_tools" in query
            return Cursor()

    entry = await repository_module.get_mcp_tool_registry_entry(
        Connection(), tenant_id="tenant-a", tool_id="corp-search"
    )

    assert entry is not None
    assert entry["allowed_tools"] == ["query"]
    assert entry["transport_type"] == "streamable_http"
    assert entry["endpoint"] == "https://mcp.example.test/v1"
    assert entry["auth_mode"] == "none"
    assert entry["name"] == "中文展示名"


def locked_run_from_payload(payload):
    validated = QueueRunPayload.model_validate(
        {key: value for key, value in payload.items() if key != "_queue_attempt_id"}
    ).model_dump(mode="json")
    return {
        "id": validated["run_id"],
        "tenant_id": validated["tenant_id"],
        "workspace_id": validated["workspace_id"],
        "user_id": validated["user_id"],
        "session_id": validated["session_id"],
        "agent_id": validated["agent_id"],
        "skill_id": validated["skill_id"],
        "trace_id": f"trace_{validated['run_id']}",
        "principal_roles": [],
        "principal_department_id": "",
        "auth_source": "test",
        "input_json": {
            key: value
            for key, value in validated.items()
            if key
            not in {
                "tenant_id",
                "workspace_id",
                "user_id",
                "session_id",
                "run_id",
                "agent_id",
                "skill_id",
            }
        },
    }


def test_multi_agent_result_summary_counts_pending_and_cancelled_steps_like_sse_snapshot():
    summary = _multi_agent_result_summary(
        [
            {
                "step_key": "code",
                "status": "pending",
                "role": "coding",
                "sequence": 1,
                "payload_json": {},
            },
            {
                "step_key": "verify",
                "status": "cancelled",
                "role": "test",
                "sequence": 2,
                "payload_json": {},
            },
        ]
    )

    assert summary["counts"] == {
        "total": 2,
        "pending": 1,
        "succeeded": 0,
        "failed": 0,
        "running": 0,
        "cancelled": 1,
        "reused": 0,
        "blocked": 0,
    }


def test_multi_agent_result_summary_normalizes_legacy_canceled_step_status():
    summary = _multi_agent_result_summary(
        [
            {
                "step_key": "verify",
                "status": "canceled",
                "role": "test",
                "sequence": 1,
                "payload_json": {},
            },
        ]
    )

    assert summary["steps"][0]["status"] == "cancelled"
    assert summary["counts"]["cancelled"] == 1


def test_multi_agent_result_summary_preserves_step_governance_context():
    summary = _multi_agent_result_summary(
        [
            {
                "step_key": "verify",
                "status": "succeeded",
                "role": "test",
                "sequence": 2,
                "payload_json": {
                    "depends_on": ["code"],
                    "output": "verify output",
                    "skill_ids": ["qa-file-reviewer"],
                    "mcp_tool_ids": ["ragflow-knowledge-search"],
                    "resource_limits": {"max_tool_calls": 3},
                    "sandbox_mode": "ephemeral",
                    "browser_enabled": True,
                },
            }
        ]
    )

    assert summary["steps"][0]["skill_ids"] == ["qa-file-reviewer"]
    assert summary["steps"][0]["mcp_tool_ids"] == ["ragflow-knowledge-search"]
    assert summary["steps"][0]["resource_limits"] == {"max_tool_calls": 3}
    assert summary["steps"][0]["sandbox_mode"] == "ephemeral"
    assert summary["steps"][0]["browser_enabled"] is True


@pytest.mark.asyncio
async def test_worker_completes_successful_adapter_run(monkeypatch):
    calls = []
    diagnostics = {
        "schema_version": "ai-platform.sdk-turn-diagnostics.v1",
        "terminal_class": "completed",
        "error_code": None,
        "action": "none",
        "retryable": False,
        "counters": {
            "max_turns": 128,
            "turns_observed": 3,
            "assistant_messages": 2,
            "text_blocks": 2,
            "result_messages": 1,
            "tool_admission_denials": 0,
            "skill_invocations": 0,
        },
        "last_public_stage": "message",
        "selected_skill": None,
        "used_skills": [],
    }

    class DiagnosticSuccessAdapter(FakeSuccessAdapter):
        async def submit_run(self, payload, event_sink=None):
            result = await super().submit_run(payload, event_sink=event_sink)
            return replace(
                result,
                result={**result.result, "sdk_turn_diagnostics": diagnostics},
                executor_payload={
                    "sdk_turn_diagnostics": diagnostics,
                    "private_raw_error": "private-token=must-not-persist",
                },
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return True

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage, message))

    async def create_artifact(conn, **kwargs):
        calls.append(("artifact", kwargs["artifact_type"], kwargs["storage_key"]))

    async def complete_run(conn, *, tenant_id, run_id, result_json):
        assert "payload" not in result_json["executor"]
        assert result_json["skills"] == {
            "allowed_skills": [],
            "staged_skills": [],
            "used_skills": [],
        }
        assert result_json["sdk_turn_diagnostics"] == diagnostics
        assert "private-token" not in str(result_json)
        calls.append(("complete", result_json["executor"]["adapter_version"]))
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(file_ids=[], skill_id="general-chat", agent_id="general-agent"),
        AdapterRegistry({"fake": DiagnosticSuccessAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert ("running", "tenant-a", "run-a") in calls
    assert any(item[0] == "artifact" for item in calls)
    assert ("complete", "fake-adapter/1") in calls
    assert calls[-1] == ("event", "status", "worker", "Run succeeded")
    assert sum(1 for item in calls if item[0] == "complete") == 1
    assert not any(
        item[0] == "event" and item[1] in {"run_failed", "run_cancelled"}
        for item in calls
    )


@pytest.mark.asyncio
async def test_worker_fails_and_terminalizes_when_a_pending_permission_would_bypass_success(monkeypatch):
    calls = []

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def has_pending(conn, *, tenant_id, run_id):
        assert (tenant_id, run_id) == ("tenant-a", "run-a")
        return True

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", error_code, error_message))
        return True

    async def complete_run(conn, **kwargs):
        raise AssertionError("a pending permission must prevent complete_run")

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs["stage"]))
        return "evt-a"

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.has_pending_tool_permission_requests", has_pending)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(base_payload(file_ids=[], skill_id="general-chat", agent_id="general-agent"), AdapterRegistry({"fake": FakeSuccessAdapter()}))

    assert outcome.status == "failed"
    assert ("fail", "tool_permission_pending", "A pending tool-permission request blocks successful completion.") in calls
    assert not any(event_type == "run_succeeded" for kind, event_type, *_ in calls if kind == "event")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "artifact_types", "required_artifact_types", "skill_id", "expected_status"),
    [
        ("correct_type", ["reviewed_docx"], [], "qa-file-reviewer", "succeeded"),
        ("wrong_type_only", ["execution_log"], [], "qa-file-reviewer", "failed"),
        ("mixed_types", ["execution_log", "reviewed_docx"], [], "qa-file-reviewer", "succeeded"),
        ("non_required_non_claude", [], [], "general-chat", "succeeded"),
    ],
)
async def test_worker_enforces_declared_required_artifact_types(
    monkeypatch,
    case,
    artifact_types,
    required_artifact_types,
    skill_id,
    expected_status,
):
    calls = []

    class ArtifactContractAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="succeeded",
                adapter_version="artifact-contract/1",
                executor_type="fake",
                executor_version="test",
                capabilities={},
                result={"message": "done"},
                artifacts=[
                    ArtifactManifest(
                        artifact_type=artifact_type,
                        label=artifact_type,
                        content_type="text/plain",
                        storage_key=f"artifacts/{artifact_type}.txt",
                        size_bytes=1,
                    )
                    for artifact_type in artifact_types
                ],
                executor_payload={"required_artifact_types": required_artifact_types},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def has_pending(conn, *, tenant_id, run_id):
        return False

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", error_code, error_message))
        return True

    async def complete_run(conn, **kwargs):
        calls.append(("complete", kwargs["run_id"]))
        return True

    async def create_artifact(conn, **kwargs):
        calls.append(("artifact", kwargs["artifact_type"]))

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs["stage"]))
        return "evt-a"

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.has_pending_tool_permission_requests", has_pending)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(
            skill_id=skill_id,
            agent_id="general-agent" if skill_id == "general-chat" else "qa-word-review",
            file_ids=[] if skill_id == "general-chat" else ["file-a"],
        ),
        AdapterRegistry({"fake": ArtifactContractAdapter()}),
    )

    assert outcome.status == expected_status, case
    if expected_status == "failed":
        assert (
            "fail",
            "required_artifact_missing",
            "The file-required Skill did not produce every required artifact type.",
        ) in calls
        assert not any(call[0] == "complete" for call in calls)
    else:
        assert ("complete", "run-a") in calls
        assert {call[1] for call in calls if call[0] == "artifact"} == set(artifact_types)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "skill_id", "agent_id", "file_ids", "artifacts", "expected_status"),
    [
        ("document_resume_without_artifact", "qa-file-reviewer", "qa-word-review", ["file-a"], [], "failed"),
        (
            "document_resume_with_reviewed_docx",
            "qa-file-reviewer",
            "qa-word-review",
            ["file-a"],
            [
                ArtifactManifest(
                    artifact_type="reviewed_docx",
                    label="Reviewed Word",
                    content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    storage_key="tenants/tenant-a/runs/run-a/artifacts/reviewed.docx",
                    size_bytes=1024,
                )
            ],
            "succeeded",
        ),
        ("general_resume_without_artifact", "general-chat", "general-agent", [], [], "succeeded"),
    ],
)
async def test_worker_enforces_capability_artifact_contract_for_real_checkpoint_resume(
    monkeypatch,
    case,
    skill_id,
    agent_id,
    file_ids,
    artifacts,
    expected_status,
):
    calls = []
    claude_adapter = ClaudeAgentWorkerAdapter()

    async def allow_resume_preflight(*_args, **_kwargs):
        return None

    class CheckpointResumeAdapter:
        async def submit_run(self, payload, event_sink=None):
            # This contract test exercises the real resume result path. Its
            # lightweight transaction double intentionally does not model
            # streamed run-step persistence, so resume output is collected
            # without an event sink here.
            resumed = await claude_adapter._run_multi_agent_file_skill(payload, event_sink=None)
            # The real resume result omits this field. Explicitly supplying an
            # empty list here proves the worker cannot treat executor metadata
            # as authority over the selected capability's required artifact.
            return replace(
                resumed,
                artifacts=artifacts,
                executor_payload={**resumed.executor_payload, "required_artifact_types": []},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("repository_terminal", error_code, error_message))
        return ToolPermissionTerminalizationProgress(
            completed=True,
            status="failed",
            did_transition=True,
            needs_reconcile=False,
        )

    async def complete_run(conn, *, tenant_id, run_id, result_json):
        calls.append(("complete", run_id))
        return True

    async def create_artifact(conn, **kwargs):
        calls.append(("artifact", kwargs["artifact_type"]))

    async def list_run_steps(conn, *, tenant_id, run_id):
        return []

    async def append_event(conn, **kwargs):
        calls.append(("worker_event", kwargs["event_type"], kwargs["stage"]))
        return "evt-a"

    monkeypatch.setattr(claude_adapter, "_preflight_resume_pinned_skills", allow_resume_preflight)
    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.list_run_steps", list_run_steps)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(
            skill_id=skill_id,
            agent_id=agent_id,
            file_ids=file_ids,
            input={
                "execution_mode": "multi_agent",
                "multi_agent_steps": [{"step_key": "review", "role": "review"}],
                "resume": {"completed_step_outputs": {"review": "checkpointed output"}},
            },
        ),
        AdapterRegistry({"fake": CheckpointResumeAdapter()}),
    )

    assert outcome.status == expected_status, case
    if expected_status == "failed":
        assert calls.count(
            (
                "repository_terminal",
                "required_artifact_missing",
                "The file-required Skill did not produce every required artifact type.",
            )
        ) == 1
        assert not any(call[0] == "complete" for call in calls)
        assert not any(call[1] == "run_failed" for call in calls if call[0] == "worker_event")
    else:
        assert ("complete", "run-a") in calls
        assert {call[1] for call in calls if call[0] == "artifact"} == {
            artifact.artifact_type for artifact in artifacts
        }
        assert not any(call[0] == "repository_terminal" for call in calls)


@pytest.mark.asyncio
async def test_worker_does_not_append_success_terminal_events_when_run_is_already_terminal(monkeypatch):
    calls = []

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs["stage"]))
        return "evt-stale"

    async def create_artifact(conn, **kwargs):
        calls.append(("artifact", kwargs["artifact_type"]))

    async def complete_run(conn, *, tenant_id, run_id, result_json):
        calls.append(("complete", run_id))
        return False

    async def fail_run(conn, **kwargs):
        return ToolPermissionTerminalizationProgress(completed=False, status=None, did_transition=False)

    async def classify_success_commit_block(conn, *, tenant_id, run_id):
        return "stale_terminal_state"

    async def drain_terminalization(**kwargs):
        return None

    async def release_sandbox_lease(conn, **kwargs):
        calls.append(("release", kwargs["reason"]))
        return {"id": kwargs["lease_id"], "status": "released", **kwargs}

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.classify_success_commit_block", classify_success_commit_block, raising=False)
    monkeypatch.setattr("app.worker.drain_run_tool_permission_terminalization", drain_terminalization)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.repositories.release_sandbox_lease", release_sandbox_lease)

    outcome = await process_run_payload(base_payload(file_ids=[], skill_id="general-chat", agent_id="general-agent"), AdapterRegistry({"fake": FakeSuccessAdapter()}))

    assert outcome.status == "skipped"
    assert outcome.error_code == "stale_terminal_state"
    assert ("complete", "run-a") in calls
    assert not any(item[0] == "event" and item[1] in {"run_succeeded", "status"} for item in calls)
    assert not any(item == ("release", "run_succeeded") for item in calls)


@pytest.mark.asyncio
async def test_worker_rolls_back_success_visible_writes_when_a_permission_arrives_before_final_completion(monkeypatch):
    visible_writes = []
    initial_permission_check = asyncio.Event()
    permission_inserted = asyncio.Event()

    class TransactionConnection:
        def __init__(self):
            self.pending_writes = []

    @asynccontextmanager
    async def transactional_connection():
        conn = TransactionConnection()
        try:
            yield conn
        except Exception:
            # A real database transaction drops these writes before recovery.
            raise
        else:
            visible_writes.extend(conn.pending_writes)

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def has_pending(conn, *, tenant_id, run_id):
        initial_permission_check.set()
        return False

    async def insert_permission_after_initial_check():
        await initial_permission_check.wait()
        permission_inserted.set()

    async def create_artifact(conn, **kwargs):
        conn.pending_writes.append(("artifact", kwargs["artifact_type"]))

    async def append_message(conn, **kwargs):
        conn.pending_writes.append(("message", kwargs["role"]))
        return "msg-a"

    async def append_event(conn, **kwargs):
        conn.pending_writes.append(("event", kwargs["event_type"]))
        return "evt-a"

    async def complete_run(conn, **kwargs):
        await permission_inserted.wait()
        return False

    async def fail_run(conn, **kwargs):
        conn.pending_writes.append(("fail", kwargs["error_code"]))
        return True

    async def classify_success_commit_block(conn, *, tenant_id, run_id):
        return "tool_permission_pending"

    monkeypatch.setattr("app.worker.transaction", transactional_connection)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.has_pending_tool_permission_requests", has_pending)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.append_message", append_message)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.classify_success_commit_block", classify_success_commit_block, raising=False)

    injector = asyncio.create_task(insert_permission_after_initial_check())
    outcome = await process_run_payload(
        base_payload(file_ids=[], skill_id="general-chat", agent_id="general-agent"),
        AdapterRegistry({"fake": FakeSuccessAdapter()}),
    )
    await injector

    assert outcome == WorkerOutcome(
        "failed",
        "run-a",
        "tool_permission_pending",
        "A pending tool-permission request blocked successful completion.",
    )
    assert ("fail", "tool_permission_pending") in visible_writes
    assert not any(kind in {"artifact", "message"} for kind, *_ in visible_writes)
    assert not any(
        kind == "event" and event_type in {"artifact_created", "assistant_message_created", "run_succeeded", "status"}
        for kind, event_type in visible_writes
    )


@pytest.mark.asyncio
async def test_worker_classifies_success_commit_cancel_race_without_permission_failure(monkeypatch):
    committed = []

    @asynccontextmanager
    async def transactional_connection():
        pending = []
        try:
            yield types.SimpleNamespace(pending=pending)
        except Exception:
            raise
        else:
            committed.extend(pending)

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def has_pending(conn, *, tenant_id, run_id):
        return False

    async def complete_run(conn, **kwargs):
        return False

    async def classify_success_commit_block(conn, *, tenant_id, run_id):
        return "cancel_requested"

    async def cancel_run(conn, *, tenant_id, run_id, result_json=None):
        conn.pending.append(("cancel", result_json))
        return ToolPermissionTerminalizationProgress(completed=True, status="cancelled", did_transition=True)

    async def fail_run(conn, **kwargs):
        raise AssertionError("accepted cancellation must not be reported as tool_permission_pending")

    async def append_event(conn, **kwargs):
        conn.pending.append(("event", kwargs["event_type"]))
        return "evt-a"

    async def append_message(conn, **kwargs):
        conn.pending.append(("message", kwargs["role"]))
        return "msg-a"

    async def create_artifact(conn, **kwargs):
        conn.pending.append(("artifact", kwargs["artifact_type"]))

    async def upsert_run_skill_snapshot(conn, **kwargs):
        return None

    monkeypatch.setattr("app.worker.transaction", transactional_connection)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.has_pending_tool_permission_requests", has_pending)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.classify_success_commit_block", classify_success_commit_block, raising=False)
    monkeypatch.setattr("app.worker.repositories.cancel_run", cancel_run)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.append_message", append_message)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.upsert_run_skill_snapshot", upsert_run_skill_snapshot)

    outcome = await process_run_payload(base_payload(file_ids=[], skill_id="general-chat", agent_id="general-agent"), AdapterRegistry({"fake": FakeSuccessAdapter()}))

    assert outcome == WorkerOutcome("cancelled", "run-a")
    assert ("cancel", {"message": "任务已取消"}) in committed
    assert not any(item == ("event", "run_failed") for item in committed)


@pytest.mark.asyncio
async def test_worker_passes_locked_run_model_id_to_adapter(monkeypatch):
    calls = []
    locked_run = locked_run_from_payload(
        base_payload(
            executor_type="capture",
            model_id="pro-tier",
            model_value="deepseek-v4-pro",
        )
    )
    locked_run["trace_id"] = "trace-run-a"

    class CaptureAdapter:
        async def submit_run(self, payload, event_sink=None):
            calls.append(("model", payload.model_id, payload.model_value, payload.attempt_id))
            return ExecutorResult(
                status="succeeded",
                adapter_version="capture/1",
                executor_type="capture",
                executor_version="capture",
                capabilities={"artifacts": False, "streaming": False, "tools": False},
                result={"message": "done"},
                artifacts=[reviewed_docx_artifact()],
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return locked_run

    async def append_event(conn, **kwargs):
        return None

    async def create_artifact(conn, **kwargs):
        return None

    async def complete_run(conn, **kwargs):
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(executor_type="capture"),
        AdapterRegistry({"capture": CaptureAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert calls == [("model", "pro-tier", "deepseek-v4-pro", "qat-test-attempt")]


@pytest.mark.asyncio
async def test_worker_records_runtime_sandbox_lease_around_successful_executor_run(monkeypatch):
    calls = []
    locked_run = locked_run_from_payload(
        base_payload(
            workspace_id="workspace-locked",
            file_ids=[],
            skill_id="general-chat",
            agent_id="general-agent",
        )
    )
    locked_run["trace_id"] = "trace-run-a"

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return locked_run

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs["stage"], kwargs.get("payload") or {}))
        return "evt-a"

    async def get_context_snapshot_for_worker(conn, **kwargs):
        return {
            "id": kwargs["context_snapshot_id"],
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "session_id": kwargs["session_id"],
            "run_id": kwargs["run_id"],
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": "executor",
            "included_message_ids": [],
            "included_file_ids": [],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "redaction_summary_json": {},
            "payload_json": {
                "schema_version": "ai-platform.context-snapshot.v1",
                "source": "test",
                "message_count": 0,
                "file_count": 0,
                "memory_record_count": 0,
            },
            "created_at": None,
        }

    async def is_cancel_requested(conn, *, tenant_id, run_id):
        return False

    async def create_artifact(conn, **kwargs):
        return None

    async def complete_run(conn, **kwargs):
        calls.append(("complete", kwargs["run_id"]))
        return True

    async def fail_run(conn, **kwargs):
        raise AssertionError(
            f"unexpected fail_run: {kwargs.get('error_code')} {kwargs.get('error_message')}"
        )

    async def create_sandbox_lease(conn, **kwargs):
        calls.append(("lease_create", kwargs))
        return {"id": "lease-runtime-a", **kwargs}

    async def release_sandbox_lease(conn, **kwargs):
        calls.append(("lease_release", kwargs))
        return {"id": kwargs["lease_id"], "status": "released", **kwargs}

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr(
        "app.worker.repositories.get_context_snapshot_for_worker",
        get_context_snapshot_for_worker,
    )
    monkeypatch.setattr("app.worker.repositories.is_cancel_requested", is_cancel_requested)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.repositories.create_sandbox_lease", create_sandbox_lease)
    monkeypatch.setattr("app.worker.repositories.release_sandbox_lease", release_sandbox_lease)

    outcome = await process_run_payload(
        base_payload(
            workspace_id="workspace-locked",
            file_ids=[],
            skill_id="general-chat",
            agent_id="general-agent",
        ),
        AdapterRegistry({"fake": FakeSuccessAdapter()}),
        worker_id="worker-a",
    )

    assert outcome.status == "succeeded"
    create_call = next(item[1] for item in calls if item[0] == "lease_create")
    assert create_call["tenant_id"] == "tenant-a"
    assert create_call["workspace_id"] == "workspace-locked"
    assert create_call["user_id"] == "user-a"
    assert create_call["session_id"] == "session-a"
    assert create_call["run_id"] == "run-a"
    assert create_call["trace_id"] == "trace-run-a"
    assert create_call["sandbox_mode"] == "ephemeral"
    assert create_call["provider"] == "fake"
    assert create_call["browser_enabled"] is False
    assert create_call["resource_limits_json"] == {}
    assert create_call["user_visible_payload_json"] == {
        "workspace": "/workspace",
        "inputs": "/workspace/inputs",
    }
    assert create_call["lease_payload_json"] == {
        "source": "sdk_only_lifecycle_placeholder",
        "evidence_class": "sdk_only_lifecycle_placeholder",
        "executor_type": "fake",
        "worker_id": "worker-a",
    }
    assert create_call["lease_payload_json"].get("probe") != "foundation_runtime"

    release_call = next(item[1] for item in calls if item[0] == "lease_release")
    assert release_call == {
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "run_id": "run-a",
        "lease_id": "lease-runtime-a",
        "reason": "run_succeeded",
    }
    assert next(index for index, item in enumerate(calls) if item[0] == "lease_create") < next(
        index for index, item in enumerate(calls) if item[0] == "complete"
    )
    assert next(index for index, item in enumerate(calls) if item[0] == "complete") < next(
        index for index, item in enumerate(calls) if item[0] == "lease_release"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("execution_tier", "agent_id", "skill_id"),
    [
        ("sdk_only_writing", "general-agent", "general-chat"),
        ("document_worker", "qa-word-review", "qa-file-reviewer"),
        ("sdk_only_writing", "general-agent", "tenant-selected-writing-skill"),
    ],
)
async def test_worker_does_not_record_placeholder_lease_for_sandbox_required_ordinary_run(
    monkeypatch,
    execution_tier,
    agent_id,
    skill_id,
):
    calls = []

    class CaptureAdapter:
        async def submit_run(self, payload, event_sink=None):
            calls.append(("adapter", payload.run_id, payload.context_snapshot.get("execution_tier")))
            return ExecutorResult(
                status="succeeded",
                adapter_version="capture-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="capture/1",
                capabilities={},
                result={"message": "done"},
                artifacts=[reviewed_docx_artifact()] if skill_id == "qa-file-reviewer" else [],
                executor_payload={"sandbox_provider": "docker"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs["stage"]))
        return "evt-heavy"

    async def get_context_snapshot_for_worker(conn, **kwargs):
        return {
            "id": "ctx-heavy",
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "session_id": kwargs["session_id"],
            "run_id": kwargs["run_id"],
            "trace_id": "trace-run-a",
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": "executor",
            "included_message_ids": [],
            "included_file_ids": [],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "redaction_summary_json": {},
            "payload_json": {
                "schema_version": "ai-platform.context-snapshot.v1",
                "source": "test",
                "message_count": 0,
                "file_count": 0,
                "memory_record_count": 0,
                "execution_tier": execution_tier,
            },
            "created_at": None,
        }

    async def create_artifact(conn, **kwargs):
        return None

    async def complete_run(conn, **kwargs):
        calls.append(("complete", kwargs["run_id"]))
        return True

    async def fail_create_sandbox_lease(*args, **kwargs):
        raise AssertionError("sandbox-required ordinary runs must rely on SandboxRuntime leases, not worker placeholders")

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.get_context_snapshot_for_worker", get_context_snapshot_for_worker)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.repositories.create_sandbox_lease", fail_create_sandbox_lease)

    outcome = await process_run_payload(
        base_payload(
            executor_type="claude-agent-worker",
            agent_id=agent_id,
            skill_id=skill_id,
            file_ids=[],
            input={"message": "run code in sandbox", "sandbox_mode": "ephemeral"},
            context_snapshot_id="ctx-heavy",
        ),
        AdapterRegistry({"claude-agent-worker": CaptureAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert ("adapter", "run-a", execution_tier) in calls


@pytest.mark.asyncio
async def test_worker_does_not_record_runtime_sandbox_lease_when_cancelled_before_executor(monkeypatch):
    calls = []

    class ShouldNotRunAdapter:
        async def submit_run(self, payload, event_sink=None):
            calls.append(("adapter", payload.run_id))
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={},
                result={"message": "should not run"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def is_cancel_requested(conn, *, tenant_id, run_id):
        return True

    async def cancel_run(conn, *, tenant_id, run_id, result_json=None):
        calls.append(("cancel", result_json))
        return ToolPermissionTerminalizationProgress(completed=True, status="cancelled", did_transition=True)

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs["stage"]))
        return "evt-a"

    async def fail_create_sandbox_lease(*args, **kwargs):
        raise AssertionError("cancelled run that never reaches executor setup must not create runtime sandbox leases")

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.is_cancel_requested", is_cancel_requested)
    monkeypatch.setattr("app.worker.repositories.cancel_run", cancel_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.create_sandbox_lease", fail_create_sandbox_lease)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": ShouldNotRunAdapter()}))

    assert outcome.status == "cancelled"
    assert ("adapter", "run-a") not in calls
    assert any(item[0] == "cancel" for item in calls)


@pytest.mark.asyncio
async def test_worker_releases_runtime_sandbox_lease_when_executor_raises(monkeypatch):
    calls = []

    class RaisingAdapter:
        async def submit_run(self, payload, event_sink=None):
            raise RuntimeError("executor crashed")

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs["stage"]))
        return "evt-a"

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", run_id, error_code, error_message))
        return ToolPermissionTerminalizationProgress(
            completed=True,
            status="failed",
            did_transition=True,
        )

    async def create_sandbox_lease(conn, **kwargs):
        calls.append(("lease_create", kwargs["run_id"]))
        return {"id": "lease-failed-a", **kwargs}

    async def release_sandbox_lease(conn, **kwargs):
        calls.append(("lease_release", kwargs))
        return {"id": kwargs["lease_id"], "status": "released", **kwargs}

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.create_sandbox_lease", create_sandbox_lease)
    monkeypatch.setattr("app.worker.repositories.release_sandbox_lease", release_sandbox_lease)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": RaisingAdapter()}))

    assert outcome.status == "failed"
    assert ("lease_create", "run-a") in calls
    release_call = next(item[1] for item in calls if item[0] == "lease_release")
    assert release_call["tenant_id"] == "tenant-a"
    assert release_call["user_id"] == "user-a"
    assert release_call["run_id"] == "run-a"
    assert release_call["lease_id"] == "lease-failed-a"
    assert release_call["reason"] == "run_failed"
    assert next(index for index, item in enumerate(calls) if item[0] == "fail") < next(
        index for index, item in enumerate(calls) if item[0] == "lease_release"
    )


@pytest.mark.asyncio
async def test_worker_persists_native_tool_admission_failure_as_safe_stage_code(monkeypatch):
    calls = []

    class RaisingAdapter:
        async def submit_run(self, payload, event_sink=None):
            raise NativeToolAdmissionError()

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs["stage"], kwargs.get("payload")))
        return "evt-native-admission"

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", run_id, error_code, error_message))
        return ToolPermissionTerminalizationProgress(
            completed=True,
            status="failed",
            did_transition=True,
        )

    async def create_sandbox_lease(conn, **kwargs):
        return {"id": "lease-native-admission", **kwargs}

    async def release_sandbox_lease(conn, **kwargs):
        calls.append(("lease_release", kwargs["reason"]))
        return {"id": kwargs["lease_id"], "status": "released", **kwargs}

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.create_sandbox_lease", create_sandbox_lease)
    monkeypatch.setattr("app.worker.repositories.release_sandbox_lease", release_sandbox_lease)

    outcome = await process_run_payload(
        base_payload(),
        AdapterRegistry({"fake": RaisingAdapter()}),
    )

    assert outcome == WorkerOutcome(
        "failed",
        "run-a",
        "native_tool_admission_failed",
        "Native tool sandbox admission failed",
    )
    assert (
        "fail",
        "run-a",
        "native_tool_admission_failed",
        "Native tool sandbox admission failed",
    ) in calls
    hidden_error = next(item for item in calls if item[:3] == ("event", "error", "executor"))
    assert hidden_error[3]["visible_to_user"] is False
    assert hidden_error[3]["error"] == "Native tool sandbox admission failed"
    assert ("lease_release", "run_failed") in calls


@pytest.mark.asyncio
async def test_worker_releases_runtime_sandbox_lease_when_adapter_reports_failure(monkeypatch):
    calls = []

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs["stage"]))
        return "evt-a"

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", run_id, error_code, error_message))
        return ToolPermissionTerminalizationProgress(
            completed=True,
            status="failed",
            did_transition=True,
        )

    async def create_sandbox_lease(conn, **kwargs):
        calls.append(("lease_create", kwargs["run_id"]))
        return {"id": "lease-reported-failed-a", **kwargs}

    async def release_sandbox_lease(conn, **kwargs):
        calls.append(("lease_release", kwargs))
        return {"id": kwargs["lease_id"], "status": "released", **kwargs}

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.create_sandbox_lease", create_sandbox_lease)
    monkeypatch.setattr("app.worker.repositories.release_sandbox_lease", release_sandbox_lease)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": FakeFailureAdapter()}))

    assert outcome.status == "failed"
    release_call = next(item[1] for item in calls if item[0] == "lease_release")
    assert release_call["tenant_id"] == "tenant-a"
    assert release_call["user_id"] == "user-a"
    assert release_call["run_id"] == "run-a"
    assert release_call["lease_id"] == "lease-reported-failed-a"
    assert release_call["reason"] == "run_failed"
    assert next(index for index, item in enumerate(calls) if item[0] == "fail") < next(
        index for index, item in enumerate(calls) if item[0] == "lease_release"
    )


@pytest.mark.asyncio
async def test_worker_does_not_append_failure_terminal_events_when_run_is_already_terminal(monkeypatch):
    calls = []

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs["stage"]))
        return "evt-stale"

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", run_id, error_code))
        return False

    async def release_sandbox_lease(conn, **kwargs):
        calls.append(("release", kwargs["reason"]))
        return {"id": kwargs["lease_id"], "status": "released", **kwargs}

    async def drain_terminalization(**kwargs):
        return None

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.release_sandbox_lease", release_sandbox_lease)
    monkeypatch.setattr("app.worker.drain_run_tool_permission_terminalization", drain_terminalization)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": FakeFailureAdapter()}))

    assert outcome.status == "skipped"
    assert outcome.error_code == "stale_terminal_state"
    assert ("fail", "run-a", "fake_failure") in calls
    assert not any(item[0] == "event" and item[1] in {"run_failed", "error"} for item in calls)
    assert not any(item == ("release", "run_failed") for item in calls)


@pytest.mark.asyncio
async def test_worker_releases_runtime_sandbox_lease_when_cancelled_on_event_boundary(monkeypatch):
    calls = []
    cancel_checks = 0

    class StreamingAdapter:
        async def submit_run(self, payload, event_sink=None):
            await event_sink(
                event_type="assistant_delta",
                stage="message",
                message="partial",
                payload={"delta": "partial"},
            )
            calls.append(("adapter", "continued_after_cancel"))
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"streaming": True},
                result={"message": "should not complete"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def is_cancel_requested(conn, *, tenant_id, run_id):
        nonlocal cancel_checks
        cancel_checks += 1
        return cancel_checks >= 2

    async def cancel_run(conn, *, tenant_id, run_id, result_json=None):
        calls.append(("cancel", result_json))
        return ToolPermissionTerminalizationProgress(completed=True, status="cancelled", did_transition=True)

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs["stage"]))
        return "evt-a"

    async def create_sandbox_lease(conn, **kwargs):
        calls.append(("lease_create", kwargs["run_id"]))
        return {"id": "lease-event-cancel-a", **kwargs}

    async def release_sandbox_lease(conn, **kwargs):
        calls.append(("lease_release", kwargs))
        return {"id": kwargs["lease_id"], "status": "released", **kwargs}

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.is_cancel_requested", is_cancel_requested)
    monkeypatch.setattr("app.worker.repositories.cancel_run", cancel_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.create_sandbox_lease", create_sandbox_lease)
    monkeypatch.setattr("app.worker.repositories.release_sandbox_lease", release_sandbox_lease)

    outcome = await process_run_payload(base_payload(file_ids=[], skill_id="general-chat", agent_id="general-agent"), AdapterRegistry({"fake": StreamingAdapter()}))

    assert outcome.status == "cancelled"
    assert ("adapter", "continued_after_cancel") not in calls
    release_call = next(item[1] for item in calls if item[0] == "lease_release")
    assert release_call["tenant_id"] == "tenant-a"
    assert release_call["user_id"] == "user-a"
    assert release_call["run_id"] == "run-a"
    assert release_call["lease_id"] == "lease-event-cancel-a"
    assert release_call["reason"] == "run_cancelled"
    assert next(index for index, item in enumerate(calls) if item[0] == "cancel") < next(
        index for index, item in enumerate(calls) if item[0] == "lease_release"
    )


@pytest.mark.asyncio
async def test_worker_prefers_cancelled_after_executor_failure_when_cancel_requested(monkeypatch):
    calls = []
    cancel_checks = 0
    diagnostics = {
        "schema_version": "ai-platform.sdk-turn-diagnostics.v1",
        "terminal_class": "cancelled",
        "error_code": "executor_cancelled",
        "action": "none",
        "retryable": False,
        "counters": {
            "max_turns": 128,
            "turns_observed": 1,
            "assistant_messages": 0,
            "text_blocks": 0,
            "result_messages": 0,
            "tool_admission_denials": 0,
            "skill_invocations": 0,
        },
        "last_public_stage": "runtime",
        "selected_skill": None,
        "used_skills": [],
    }

    class CancelAwareFailureAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="failed",
                adapter_version="adapter/1",
                executor_type="claude-agent-worker",
                executor_version="sandbox-runtime/1",
                capabilities={},
                result={
                    "message": "任务已取消",
                    "error_code": "executor_cancelled",
                    "sdk_turn_diagnostics": diagnostics,
                },
                executor_payload={
                    "sandbox_provider": "docker",
                    "runtime_terminal_status": "cancelled",
                    "sdk_turn_diagnostics": diagnostics,
                    "private_raw_error": "private-token=must-not-persist",
                },
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def is_cancel_requested(conn, *, tenant_id, run_id):
        nonlocal cancel_checks
        cancel_checks += 1
        return cancel_checks >= 2

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs["stage"]))
        return "evt-cancel"

    async def cancel_run(conn, *, tenant_id, run_id, result_json=None):
        calls.append(("cancel", run_id, result_json))
        return ToolPermissionTerminalizationProgress(completed=True, status="cancelled", did_transition=True)

    async def fail_run(conn, **kwargs):
        raise AssertionError("cancel-requested runtime failures must prefer cancelled over failed")

    async def get_context_snapshot_for_worker(conn, **kwargs):
        return {
            "id": "ctx-heavy",
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "session_id": kwargs["session_id"],
            "run_id": kwargs["run_id"],
            "trace_id": "trace-run-a",
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": "executor",
            "included_message_ids": [],
            "included_file_ids": [],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "redaction_summary_json": {},
            "payload_json": {
                "schema_version": "ai-platform.context-snapshot.v1",
                "source": "test",
                "message_count": 0,
                "file_count": 0,
                "memory_record_count": 0,
                "execution_tier": "heavy_sandbox",
            },
            "created_at": None,
        }

    async def fail_create_sandbox_lease(*args, **kwargs):
        raise AssertionError("heavy_sandbox runtime path must not record worker placeholder leases")

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.is_cancel_requested", is_cancel_requested)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.cancel_run", cancel_run)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.get_context_snapshot_for_worker", get_context_snapshot_for_worker)
    monkeypatch.setattr("app.worker.repositories.create_sandbox_lease", fail_create_sandbox_lease)

    outcome = await process_run_payload(
        base_payload(
            executor_type="claude-agent-worker",
            agent_id="general-agent",
            skill_id="general-chat",
            file_ids=[],
            input={"message": "run code in sandbox", "sandbox_mode": "ephemeral"},
            context_snapshot_id="ctx-heavy",
        ),
        AdapterRegistry({"claude-agent-worker": CancelAwareFailureAdapter()}),
    )

    assert outcome.status == "cancelled"
    cancel_calls = [item for item in calls if item[0] == "cancel"]
    assert cancel_calls == [("cancel", "run-a", {"message": "任务已取消"})]
    assert "private-token" not in str(cancel_calls)
    assert not any(
        item[0] == "event"
        and item[1] in {"run_succeeded", "run_failed", "run_cancelled"}
        for item in calls
    )


@pytest.mark.asyncio
async def test_worker_prefers_cancelled_when_executor_raises_after_cancel_request(monkeypatch):
    calls = []
    cancel_checks = 0

    class RaisingAdapter:
        async def submit_run(self, payload, event_sink=None):
            raise RuntimeError("executor failed after accepted cancel")

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def is_cancel_requested(conn, *, tenant_id, run_id):
        nonlocal cancel_checks
        cancel_checks += 1
        return cancel_checks >= 2

    async def cancel_run(conn, *, tenant_id, run_id, result_json=None):
        calls.append(("cancel", run_id, result_json))
        return ToolPermissionTerminalizationProgress(completed=True, status="cancelled", did_transition=True)

    async def fail_run(conn, **kwargs):
        raise AssertionError("accepted cancel must not be overwritten by executor_failure")

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs["stage"]))
        return "evt-cancelled"

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.is_cancel_requested", is_cancel_requested)
    monkeypatch.setattr("app.worker.repositories.cancel_run", cancel_run)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": RaisingAdapter()}))

    assert outcome.status == "cancelled"
    assert any(item[0] == "cancel" for item in calls)
    assert not any(item[0] == "event" and item[1] == "run_cancelled" for item in calls)


@pytest.mark.asyncio
async def test_worker_does_not_append_cancel_terminal_event_when_cancel_update_is_stale(monkeypatch):
    calls = []
    cancel_checks = 0

    class RaisingAdapter:
        async def submit_run(self, payload, event_sink=None):
            raise RuntimeError("executor failed after accepted cancel")

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def is_cancel_requested(conn, *, tenant_id, run_id):
        nonlocal cancel_checks
        cancel_checks += 1
        return cancel_checks >= 2

    async def cancel_run(conn, *, tenant_id, run_id, result_json=None):
        calls.append(("cancel", run_id, result_json))
        return False

    async def fail_run(conn, **kwargs):
        raise AssertionError("accepted cancel must not be overwritten by executor_failure")

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs["stage"]))
        return "evt-stale-cancel"

    async def release_sandbox_lease(conn, **kwargs):
        calls.append(("release", kwargs["reason"]))
        return {"id": kwargs["lease_id"], "status": "released", **kwargs}

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.is_cancel_requested", is_cancel_requested)
    monkeypatch.setattr("app.worker.repositories.cancel_run", cancel_run)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.release_sandbox_lease", release_sandbox_lease)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": RaisingAdapter()}))

    assert outcome.status == "skipped"
    assert outcome.error_code == "stale_terminal_state"
    assert any(item[0] == "cancel" for item in calls)
    assert ("event", "run_cancelled", "control") not in calls
    assert not any(item == ("release", "run_cancelled") for item in calls)


@pytest.mark.asyncio
async def test_worker_keeps_runtime_failure_when_cancel_requested_but_runtime_failed(monkeypatch):
    calls = []
    cancel_checks = 0

    class LateFailureAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="failed",
                adapter_version="adapter/1",
                executor_type="claude-agent-worker",
                executor_version="sandbox-runtime/1",
                capabilities={},
                result={"message": "runtime failed after cancel request", "error_code": "executor_failure"},
                executor_payload={"sandbox_provider": "docker", "runtime_terminal_status": "failed"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def is_cancel_requested(conn, *, tenant_id, run_id):
        nonlocal cancel_checks
        cancel_checks += 1
        return cancel_checks >= 2

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs["stage"]))
        return "evt-failed"

    async def cancel_run(conn, **kwargs):
        calls.append(("cancel", kwargs["run_id"]))
        return ToolPermissionTerminalizationProgress(completed=True, status="cancelled", did_transition=True)

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", run_id, error_code, error_message, result_json))
        return ToolPermissionTerminalizationProgress(completed=True, status="failed", did_transition=True)

    async def get_context_snapshot_for_worker(conn, **kwargs):
        return {
            "id": "ctx-heavy",
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "session_id": kwargs["session_id"],
            "run_id": kwargs["run_id"],
            "trace_id": "trace-run-a",
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": "executor",
            "included_message_ids": [],
            "included_file_ids": [],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "redaction_summary_json": {},
            "payload_json": {
                "schema_version": "ai-platform.context-snapshot.v1",
                "source": "test",
                "message_count": 0,
                "file_count": 0,
                "memory_record_count": 0,
                "execution_tier": "heavy_sandbox",
            },
            "created_at": None,
        }

    async def fail_create_sandbox_lease(*args, **kwargs):
        raise AssertionError("heavy_sandbox runtime path must not record worker placeholder leases")

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.is_cancel_requested", is_cancel_requested)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.cancel_run", cancel_run)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.get_context_snapshot_for_worker", get_context_snapshot_for_worker)
    monkeypatch.setattr("app.worker.repositories.create_sandbox_lease", fail_create_sandbox_lease)

    outcome = await process_run_payload(
        base_payload(
            executor_type="claude-agent-worker",
            agent_id="general-agent",
            skill_id="general-chat",
            file_ids=[],
            input={"message": "run code in sandbox", "sandbox_mode": "ephemeral"},
            context_snapshot_id="ctx-heavy",
        ),
        AdapterRegistry({"claude-agent-worker": LateFailureAdapter()}),
    )

    assert outcome.status == "failed"
    assert not any(item[0] == "cancel" for item in calls)
    assert any(item[0] == "fail" and item[2] == "executor_failure" for item in calls)
    assert not any(item[0] == "event" and item[1] == "run_failed" for item in calls)
    assert ("event", "error", "worker") in calls


@pytest.mark.asyncio
async def test_worker_releases_runtime_sandbox_lease_when_terminal_persistence_raises(monkeypatch):
    calls = []
    tx_counter = 0

    @asynccontextmanager
    async def recording_transaction():
        nonlocal tx_counter
        tx_counter += 1
        tx_label = f"tx-{tx_counter}"
        calls.append(("tx_enter", tx_label))
        try:
            yield tx_label
        except BaseException:
            calls.append(("tx_rollback", tx_label))
            raise
        else:
            calls.append(("tx_commit", tx_label))
        finally:
            calls.append(("tx_exit", tx_label))

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", conn, kwargs["event_type"], kwargs["stage"]))
        return "evt-a"

    async def create_artifact(conn, **kwargs):
        return None

    async def complete_run(conn, **kwargs):
        calls.append(("complete", conn, kwargs["run_id"]))
        raise RuntimeError("terminal write failed")

    async def fail_run(conn, **kwargs):
        raise RuntimeError("terminal write failed")

    async def create_sandbox_lease(conn, **kwargs):
        calls.append(("lease_create", conn, kwargs["run_id"]))
        return {"id": "lease-terminal-error-a", **kwargs}

    async def release_sandbox_lease(conn, **kwargs):
        calls.append(("lease_release", conn, kwargs))
        return {"id": kwargs["lease_id"], "status": "released", **kwargs}

    monkeypatch.setattr("app.worker.transaction", recording_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.repositories.create_sandbox_lease", create_sandbox_lease)
    monkeypatch.setattr("app.worker.repositories.release_sandbox_lease", release_sandbox_lease)

    with pytest.raises(RuntimeError, match="terminal write failed"):
        await process_run_payload(base_payload(file_ids=[], skill_id="general-chat", agent_id="general-agent"), AdapterRegistry({"fake": FakeSuccessAdapter()}))

    complete_call = next(item for item in calls if item[0] == "complete")
    release_call = next(item for item in calls if item[0] == "lease_release")
    assert complete_call[1] != release_call[1]
    assert release_call[2] == {
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "run_id": "run-a",
        "lease_id": "lease-terminal-error-a",
        "reason": "run_terminal_interrupted",
    }
    assert calls.index(complete_call) < calls.index(release_call)


@pytest.mark.asyncio
async def test_worker_reconciles_multi_agent_child_after_success(monkeypatch):
    calls = []

    child_input = {
        "mode": "file",
        "multi_agent_dispatch": {
            "parent_run_id": "run-parent",
            "parent_step_id": "step-code",
            "dispatch_id": "dispatch-code",
            "step_key": "code",
        },
    }

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"]))
        return "evt-a"

    async def create_artifact(conn, **kwargs):
        return None

    async def complete_run(conn, *, tenant_id, run_id, result_json):
        calls.append(("complete", run_id, result_json["message"]))
        return True

    async def reconcile(*, tenant_id, run_id, progress, transaction_factory):
        calls.append(("reconcile", {"tenant_id": tenant_id, "run_id": run_id, "progress": progress}))
        return {"parent_run_id": "run-parent"}

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.reconcile_terminalized_permission_run", reconcile)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(run_id="run-child", skill_id="general-chat", agent_id="general-agent", input=child_input),
        AdapterRegistry({"fake": FakeSuccessAdapter()}),
    )

    assert outcome.status == "succeeded"
    complete_index = next(index for index, item in enumerate(calls) if item[0] == "complete")
    reconcile_index = next(index for index, item in enumerate(calls) if item[0] == "reconcile")
    assert complete_index < reconcile_index
    reconcile_call = calls[reconcile_index][1]
    assert reconcile_call["tenant_id"] == "tenant-a"
    assert reconcile_call["run_id"] == "run-child"
    assert reconcile_call["progress"].status == "succeeded"
    assert reconcile_call["progress"].did_transition is True
    assert reconcile_call["progress"].needs_reconcile is True


@pytest.mark.asyncio
async def test_worker_retries_multi_agent_parent_rollup_after_child_transaction_commit(monkeypatch):
    calls = []
    tx_counter = 0
    tx_events = []

    @asynccontextmanager
    async def recording_transaction():
        nonlocal tx_counter
        tx_counter += 1
        tx_label = f"tx-{tx_counter}"
        tx_events.append(("enter", tx_label))
        try:
            yield tx_label
        except BaseException:
            tx_events.append(("rollback", tx_label))
            raise
        else:
            tx_events.append(("commit", tx_label))
        finally:
            tx_events.append(("exit", tx_label))

    child_input = {
        "mode": "file",
        "multi_agent_dispatch": {
            "parent_run_id": "run-parent",
            "parent_step_id": "step-code",
            "dispatch_id": "dispatch-code",
            "step_key": "code",
        },
    }

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", conn, kwargs["event_type"]))
        return "evt-a"

    async def create_artifact(conn, **kwargs):
        return None

    async def complete_run(conn, *, tenant_id, run_id, result_json):
        calls.append(("complete", conn, run_id))
        return True

    async def reconcile(*, tenant_id, run_id, progress, transaction_factory):
        calls.append(("reconcile", {"tenant_id": tenant_id, "run_id": run_id, "progress": progress}))
        return {"parent_run_id": "run-parent"}

    monkeypatch.setattr("app.worker.transaction", recording_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.reconcile_terminalized_permission_run", reconcile)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(run_id="run-child", skill_id="general-chat", agent_id="general-agent", input=child_input),
        AdapterRegistry({"fake": FakeSuccessAdapter()}),
    )

    assert outcome.status == "succeeded"
    reconcile_call = next(item for item in calls if item[0] == "reconcile")
    assert reconcile_call[1]["run_id"] == "run-child"
    assert reconcile_call[1]["progress"].status == "succeeded"
    first_commit = next(index for index, item in enumerate(tx_events) if item[0] == "commit")
    assert first_commit < len(tx_events)


@pytest.mark.asyncio
async def test_worker_reconciles_multi_agent_child_after_failure(monkeypatch):
    calls = []

    child_input = {
        "mode": "file",
        "multi_agent_dispatch": {
            "parent_run_id": "run-parent",
            "parent_step_id": "step-code",
            "dispatch_id": "dispatch-code",
            "step_key": "code",
        },
    }

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"]))
        return "evt-a"

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", run_id, error_code, error_message, result_json))
        return ToolPermissionTerminalizationProgress(True, "failed", True, True)

    async def reconcile(*, tenant_id, run_id, progress, transaction_factory):
        calls.append(("reconcile", {"tenant_id": tenant_id, "run_id": run_id, "progress": progress}))
        return {"parent_run_id": "run-parent"}

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.reconcile_terminalized_permission_run", reconcile)

    outcome = await process_run_payload(
        base_payload(run_id="run-child", input=child_input),
        AdapterRegistry({"fake": FakeFailureAdapter()}),
    )

    assert outcome.status == "failed"
    fail_index = next(index for index, item in enumerate(calls) if item[0] == "fail")
    reconcile_index = next(index for index, item in enumerate(calls) if item[0] == "reconcile")
    assert fail_index < reconcile_index
    reconcile_call = calls[reconcile_index][1]
    assert reconcile_call["run_id"] == "run-child"
    assert reconcile_call["progress"].status == "failed"
    assert reconcile_call["progress"].did_transition is True


@pytest.mark.asyncio
async def test_worker_reconciles_multi_agent_child_after_cancel(monkeypatch):
    calls = []
    cancel_checks = 0

    child_input = {
        "mode": "file",
        "multi_agent_dispatch": {
            "parent_run_id": "run-parent",
            "parent_step_id": "step-code",
            "dispatch_id": "dispatch-code",
            "step_key": "code",
        },
    }

    class StreamingAdapter:
        async def submit_run(self, payload, event_sink=None):
            await event_sink(
                event_type="assistant_delta",
                stage="message",
                message="partial",
                payload={"delta": "partial"},
            )
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"streaming": True},
                result={"message": "should not complete"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def is_cancel_requested(conn, *, tenant_id, run_id):
        nonlocal cancel_checks
        cancel_checks += 1
        return cancel_checks >= 2

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"]))
        return "evt-a"

    async def cancel_run(conn, *, tenant_id, run_id, result_json=None):
        calls.append(("cancel", run_id, result_json))
        return ToolPermissionTerminalizationProgress(completed=True, status="cancelled", did_transition=True, needs_reconcile=True)

    async def reconcile(*, tenant_id, run_id, progress, transaction_factory):
        calls.append(("reconcile", {"tenant_id": tenant_id, "run_id": run_id, "progress": progress}))
        return {"parent_run_id": "run-parent"}

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.is_cancel_requested", is_cancel_requested)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.cancel_run", cancel_run)
    monkeypatch.setattr("app.worker.reconcile_terminalized_permission_run", reconcile)

    outcome = await process_run_payload(
        base_payload(run_id="run-child", input=child_input),
        AdapterRegistry({"fake": StreamingAdapter()}),
    )

    assert outcome.status == "cancelled"
    cancel_index = next(index for index, item in enumerate(calls) if item[0] == "cancel")
    reconcile_index = next(index for index, item in enumerate(calls) if item[0] == "reconcile")
    assert cancel_index < reconcile_index
    reconcile_call = calls[reconcile_index][1]
    assert reconcile_call["tenant_id"] == "tenant-a"
    assert reconcile_call["run_id"] == "run-child"
    assert reconcile_call["progress"].status == "cancelled"
    assert reconcile_call["progress"].did_transition is True
    assert reconcile_call["progress"].needs_reconcile is True


@pytest.mark.asyncio
async def test_worker_reconciliation_uses_repository_for_ordinary_run(monkeypatch):
    calls = []

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def create_artifact(conn, **kwargs):
        return None

    async def complete_run(conn, *, tenant_id, run_id, result_json):
        calls.append(("complete", run_id))
        return True

    async def reconcile(conn, **kwargs):
        calls.append(("reconcile", kwargs))
        return None

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.reconcile_multi_agent_child_run_terminal_state", reconcile)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    child_input = {
        "mode": "chat",
        "multi_agent_dispatch": {
            "parent_run_id": "run-parent",
            "parent_step_id": "step-code",
            "dispatch_id": "dispatch-code",
            "step_key": "code",
        },
    }
    outcome = await process_run_payload(
        base_payload(
            file_ids=[],
            skill_id="general-chat",
            agent_id="general-agent",
            input=child_input,
        ),
        AdapterRegistry({"fake": FakeSuccessAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert ("complete", "run-a") in calls
    reconcile_call = next(item[1] for item in calls if item[0] == "reconcile")
    assert reconcile_call["tenant_id"] == "tenant-a"
    assert reconcile_call["child_run_id"] == "run-a"
    assert reconcile_call["child_status"] == "succeeded"


@pytest.mark.asyncio
async def test_worker_reconciles_multi_agent_child_after_executor_exception(monkeypatch):
    calls = []

    child_input = {
        "mode": "file",
        "multi_agent_dispatch": {
            "parent_run_id": "run-parent",
            "parent_step_id": "step-code",
            "dispatch_id": "dispatch-code",
            "step_key": "code",
        },
    }

    class RaisingAdapter:
        async def submit_run(self, payload, event_sink=None):
            raise RuntimeError("executor crashed")

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"]))
        return "evt-a"

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", run_id, error_code, error_message, result_json))
        return ToolPermissionTerminalizationProgress(True, "failed", True, True)

    async def reconcile(*, tenant_id, run_id, progress, transaction_factory):
        calls.append(("reconcile", {"tenant_id": tenant_id, "run_id": run_id, "progress": progress}))
        return {"parent_run_id": "run-parent"}

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.reconcile_terminalized_permission_run", reconcile)

    outcome = await process_run_payload(
        base_payload(run_id="run-child", input=child_input),
        AdapterRegistry({"fake": RaisingAdapter()}),
    )

    assert outcome.status == "failed"
    fail_index = next(index for index, item in enumerate(calls) if item[0] == "fail")
    reconcile_index = next(index for index, item in enumerate(calls) if item[0] == "reconcile")
    assert fail_index < reconcile_index
    reconcile_call = calls[reconcile_index][1]
    assert reconcile_call["run_id"] == "run-child"
    assert reconcile_call["progress"].status == "failed"


@pytest.mark.asyncio
async def test_worker_reconciles_multi_agent_child_after_unknown_executor(monkeypatch):
    calls = []

    child_input = {
        "mode": "file",
        "multi_agent_dispatch": {
            "parent_run_id": "run-parent",
            "parent_step_id": "step-code",
            "dispatch_id": "dispatch-code",
            "step_key": "code",
        },
    }

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"]))
        return "evt-a"

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", run_id, error_code, error_message, result_json))
        return ToolPermissionTerminalizationProgress(True, "failed", True, True)

    async def reconcile(*, tenant_id, run_id, progress, transaction_factory):
        calls.append(("reconcile", {"tenant_id": tenant_id, "run_id": run_id, "progress": progress}))
        return {"parent_run_id": "run-parent"}

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.reconcile_terminalized_permission_run", reconcile)

    outcome = await process_run_payload(
        base_payload(run_id="run-child", executor_type="missing", input=child_input),
        AdapterRegistry({"fake": FakeSuccessAdapter()}),
    )

    assert outcome.status == "failed"
    fail_index = next(index for index, item in enumerate(calls) if item[0] == "fail")
    reconcile_index = next(index for index, item in enumerate(calls) if item[0] == "reconcile")
    assert fail_index < reconcile_index
    reconcile_call = calls[reconcile_index][1]
    assert reconcile_call["run_id"] == "run-child"
    assert reconcile_call["progress"].status == "failed"


@pytest.mark.asyncio
async def test_worker_retries_parent_rollup_after_early_unknown_executor_reconciliation(monkeypatch):
    calls = []
    tx_counter = 0
    tx_events = []

    @asynccontextmanager
    async def recording_transaction():
        nonlocal tx_counter
        tx_counter += 1
        tx_label = f"tx-{tx_counter}"
        tx_events.append(("enter", tx_label))
        try:
            yield tx_label
        except BaseException:
            tx_events.append(("rollback", tx_label))
            raise
        else:
            tx_events.append(("commit", tx_label))
        finally:
            tx_events.append(("exit", tx_label))

    child_input = {
        "mode": "file",
        "multi_agent_dispatch": {
            "parent_run_id": "run-parent",
            "parent_step_id": "step-code",
            "dispatch_id": "dispatch-code",
            "step_key": "code",
        },
    }

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", conn, kwargs["event_type"]))
        return "evt-a"

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", conn, error_code))
        return ToolPermissionTerminalizationProgress(True, "failed", True, True)

    async def reconcile(*, tenant_id, run_id, progress, transaction_factory):
        calls.append(("reconcile", {"tenant_id": tenant_id, "run_id": run_id, "progress": progress}))
        return {"parent_run_id": "run-parent"}

    monkeypatch.setattr("app.worker.transaction", recording_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.reconcile_terminalized_permission_run", reconcile)

    outcome = await process_run_payload(
        base_payload(run_id="run-child", executor_type="missing", input=child_input),
        AdapterRegistry({"fake": FakeSuccessAdapter()}),
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "unknown_executor_type"
    reconcile_call = next(item for item in calls if item[0] == "reconcile")
    assert reconcile_call[1]["run_id"] == "run-child"
    assert reconcile_call[1]["progress"].status == "failed"
    assert any(item[0] == "commit" for item in tx_events)


@pytest.mark.asyncio
async def test_worker_passes_skill_manifest_pins_to_executor(monkeypatch):
    captured = {}

    class CaptureAdapter:
        async def submit_run(self, payload, event_sink=None):
            captured["payload"] = payload
            return ExecutorResult(
                status="succeeded",
                adapter_version="capture-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="capture/1",
                capabilities={},
                result={"message": "done"},
                artifacts=[reviewed_docx_artifact()],
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def complete_run(conn, **kwargs):
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(
            executor_type="claude-agent-worker",
            skill_version="hash-primary",
            skill_manifests=[{"skill_id": "qa-file-reviewer", "content_hash": "hash-primary"}],
        ),
        AdapterRegistry({"claude-agent-worker": CaptureAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert captured["payload"].skill_version == "hash-primary"
    assert captured["payload"].skill_manifests == [{"skill_id": "qa-file-reviewer", "content_hash": "hash-primary"}]


@pytest.mark.asyncio
async def test_worker_fails_missing_physical_context_snapshot_before_adapter(monkeypatch):
    calls = []

    class ForbiddenAdapter:
        async def submit_run(self, payload, event_sink=None):
            raise AssertionError("missing physical context binding must not reach the adapter")

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs["stage"]))
        return "evt-a"

    async def missing_snapshot(conn, **kwargs):
        calls.append(("lookup", kwargs["context_snapshot_id"]))
        return None

    async def fail_run(conn, **kwargs):
        calls.append(("fail", kwargs["error_code"], kwargs["error_message"]))
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.get_context_snapshot_for_worker", missing_snapshot)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)

    outcome = await process_run_payload(
        base_payload(
            executor_type="claude-agent-worker",
            context_snapshot_id="ctx-missing",
            context_snapshot={},
        ),
        AdapterRegistry({"claude-agent-worker": ForbiddenAdapter()}),
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "context_snapshot_unavailable"
    assert ("lookup", "ctx-missing") in calls
    assert ("fail", "context_snapshot_unavailable", "Run context snapshot is unavailable") in calls
    assert not any(item[0] == "complete" for item in calls)


@pytest.mark.asyncio
async def test_worker_uses_scoped_db_context_snapshot_instead_of_queue_copy(monkeypatch):
    captured = {}

    class CaptureAdapter:
        async def submit_run(self, payload, event_sink=None):
            captured["payload"] = payload
            return ExecutorResult(
                status="succeeded",
                adapter_version="capture-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="capture/1",
                capabilities={},
                result={"message": "done"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def get_context_snapshot_for_worker(conn, **kwargs):
        assert kwargs == {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "context_snapshot_id": "ctx-existing",
        }
        return {
            "id": "ctx-existing",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": "executor",
            "included_message_ids": [],
            "included_file_ids": ["file-a"],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "redaction_summary_json": {},
            "payload_json": {
                "schema_version": "ai-platform.context-snapshot.v1",
                "source": "db_scoped",
                "message_count": 0,
                "file_count": 1,
                "memory_record_count": 0,
            },
            "created_at": None,
        }

    async def fail_record_context(*args, **kwargs):
        raise AssertionError("verified queue snapshots must be reconstructed from DB scope")

    async def complete_run(conn, **kwargs):
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.get_context_snapshot_for_worker", get_context_snapshot_for_worker)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(
            executor_type="claude-agent-worker",
            agent_id="general-agent",
            skill_id="general-chat",
            context_snapshot={"source": "tampered_queue_copy"},
        ),
        AdapterRegistry({"claude-agent-worker": CaptureAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert captured["payload"].context_snapshot_id == "ctx-existing"
    assert captured["payload"].context_snapshot["source"] == "stored_context_snapshot"
    assert captured["payload"].context_snapshot["used_context_summary"]["source"] == "stored_context_snapshot"
    assert "tampered_queue_copy" not in json.dumps(captured["payload"].context_snapshot, ensure_ascii=False)


@pytest.mark.asyncio
async def test_worker_uses_private_context_manifest_from_scoped_db_snapshot(monkeypatch):
    captured = {}

    class CaptureAdapter:
        async def submit_run(self, payload, event_sink=None):
            captured["payload"] = payload
            return ExecutorResult(
                status="succeeded",
                adapter_version="capture-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="capture/1",
                capabilities={},
                result={"message": "done"},
                artifacts=[
                    ArtifactManifest(
                        artifact_type="reviewed_docx",
                        label="Reviewed Word",
                        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        storage_key="tenants/tenant-a/runs/run-a/artifacts/reviewed.docx",
                        size_bytes=1,
                    )
                ],
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-context-manifest"

    async def get_context_snapshot_for_worker(conn, **kwargs):
        return {
            "id": kwargs["context_snapshot_id"],
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "session_id": kwargs["session_id"],
            "run_id": kwargs["run_id"],
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": "executor",
            "included_message_ids": ["msg-a"],
            "included_file_ids": ["file-a"],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "redaction_summary_json": {},
            "payload_json": {
                "schema_version": "ai-platform.context-snapshot.v1",
                "source": "chat_stream",
                "execution_tier": "sdk_only_writing",
                "context_manifest": {
                    "schema_version": "ai-platform.context-manifest.v1",
                    "context_manifest_version": "v1",
                    "generated_at": "2026-07-02T01:02:03Z",
                    "scope": {
                        "tenant_id": kwargs["tenant_id"],
                        "workspace_id": kwargs["workspace_id"],
                        "user_id": kwargs["user_id"],
                        "session_id": kwargs["session_id"],
                        "run_id": kwargs["run_id"],
                        "agent_id": "qa-word-review",
                        "skill_id": "qa-file-reviewer",
                    },
                    "current_message": "review the scoped file",
                    "recent_messages": [],
                    "files": [
                        {
                            "file_id": "file-a",
                            "requires_retrieval": True,
                            "storage_key": "tenants/tenant-a/private/source.docx",
                        }
                    ],
                    "available_retrieval_tools": ["read_context_file"],
                },
            },
            "created_at": None,
        }

    async def fail_record_context(*args, **kwargs):
        raise AssertionError("verified queue snapshots must be reconstructed from DB scope")

    async def complete_run(conn, **kwargs):
        return True

    async def create_artifact(conn, **kwargs):
        return None

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.get_context_snapshot_for_worker", get_context_snapshot_for_worker)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(
            executor_type="claude-agent-worker",
            context_snapshot={"source": "tampered_queue_copy"},
        ),
        AdapterRegistry({"claude-agent-worker": CaptureAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert captured["payload"].context_pack["source"] == "context_manifest"
    assert captured["payload"].context_pack["context_manifest"]["files"] == [
        {"file_id": "file-a", "requires_retrieval": True}
    ]
    serialized = json.dumps(captured["payload"].context_pack, ensure_ascii=False).lower()
    assert "storage_key" not in serialized
    assert "tenants/tenant-a/private" not in serialized
    assert "tampered_queue_copy" not in serialized


@pytest.mark.asyncio
async def test_worker_uses_scoped_db_context_snapshot_when_queue_copy_missing(monkeypatch):
    captured = {}

    class CaptureAdapter:
        async def submit_run(self, payload, event_sink=None):
            captured["payload"] = payload
            return ExecutorResult(
                status="succeeded",
                adapter_version="capture-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="capture/1",
                capabilities={},
                result={"message": "done"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-context-id-only"

    async def get_context_snapshot_for_worker(conn, **kwargs):
        assert kwargs["context_snapshot_id"] == "ctx-existing"
        return {
            "id": "ctx-existing",
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "session_id": kwargs["session_id"],
            "run_id": kwargs["run_id"],
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": "executor",
            "included_message_ids": ["msg-a"],
            "included_file_ids": ["file-a"],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "redaction_summary_json": {},
            "payload_json": {"window": "current"},
            "created_at": None,
        }

    async def fail_record_context(*args, **kwargs):
        raise AssertionError("context_snapshot_id-only payload must resolve scoped DB snapshot")

    async def complete_run(conn, **kwargs):
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.get_context_snapshot_for_worker", get_context_snapshot_for_worker)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(
            executor_type="claude-agent-worker",
            agent_id="general-agent",
            skill_id="general-chat",
            context_snapshot_id="ctx-existing",
            context_snapshot={},
        ),
        AdapterRegistry({"claude-agent-worker": CaptureAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert captured["payload"].context_snapshot_id == "ctx-existing"
    assert captured["payload"].context_snapshot["used_context_summary"] == {
        "source": "stored_context_snapshot",
        "input_keys": ["attachments", "window"],
        "memory_policy_source": "not_recorded",
        "long_term_memory_read": False,
    }


@pytest.mark.asyncio
async def test_worker_preserves_stored_safe_summary_metadata_when_payload_has_only_provenance(monkeypatch):
    captured = {}

    class CaptureAdapter:
        async def submit_run(self, payload, event_sink=None):
            captured["payload"] = payload
            return ExecutorResult(
                status="succeeded",
                adapter_version="capture-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="capture/1",
                capabilities={},
                result={"message": "done"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-context"

    async def get_context_snapshot_for_worker(conn, **kwargs):
        return {
            "id": "ctx-existing",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": "executor",
            "included_message_ids": ["msg-a"],
            "included_file_ids": ["file-a"],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "redaction_summary_json": {},
            "payload_json": {
                "memory_policy": {
                    "source": "stored",
                    "memory_enabled": False,
                    "long_term_memory_enabled": False,
                    "retention_days": 30,
                },
                "used_context_summary": {
                    "source": "runs_api",
                    "input_keys": ["message", "attachments", "raw_storage_key"],
                    "memory_policy_source": "stored",
                    "long_term_memory_read": True,
                },
                "execution_tier": "document_worker",
                "latest_artifact_version": "v7",
                "context_pack_version": "v9",
                "context_pack_generated_at": "2026-06-12T01:23:45Z",
            },
            "created_at": None,
        }

    async def fail_record_context(*args, **kwargs):
        raise AssertionError("context_snapshot_id-only payload must resolve scoped DB snapshot")

    async def complete_run(conn, **kwargs):
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.get_context_snapshot_for_worker", get_context_snapshot_for_worker)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(
            executor_type="claude-agent-worker",
            agent_id="general-agent",
            skill_id="general-chat",
            context_snapshot_id="ctx-existing",
            context_snapshot={},
        ),
        AdapterRegistry({"claude-agent-worker": CaptureAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert captured["payload"].context_snapshot["used_context_summary"] == {
        "source": "runs_api",
        "input_keys": ["attachments", "message"],
        "memory_policy_source": "stored",
        "long_term_memory_read": True,
    }
    assert captured["payload"].context_snapshot["execution_tier"] == "document_worker"
    assert captured["payload"].context_snapshot["latest_artifact_version"] == "v7"
    assert captured["payload"].context_snapshot["context_pack_version"] == "v9"
    assert captured["payload"].context_snapshot["context_pack_generated_at"] == "2026-06-12T01:23:45Z"
    serialized = json.dumps(captured["payload"].context_snapshot, ensure_ascii=False).lower()
    assert "raw_storage_key" not in serialized


@pytest.mark.asyncio
async def test_worker_preserves_safe_top_level_legacy_context_source(monkeypatch):
    captured = {}

    class CaptureAdapter:
        async def submit_run(self, payload, event_sink=None):
            captured["payload"] = payload
            return ExecutorResult(
                status="succeeded",
                adapter_version="capture-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="capture/1",
                capabilities={},
                result={"message": "done"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-context"

    async def get_context_snapshot_for_worker(conn, **kwargs):
        return {
            "id": "ctx-existing",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": "executor",
            "included_message_ids": ["msg-a"],
            "included_file_ids": [],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "redaction_summary_json": {},
            "payload_json": {
                "source": "chat_stream",
                "message": "hello",
                "context_pack_generated_at": "2026-06-12T01:23:45Z",
            },
            "created_at": None,
        }

    async def fail_record_context(*args, **kwargs):
        raise AssertionError("context_snapshot_id-only payload must resolve scoped DB snapshot")

    async def complete_run(conn, **kwargs):
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.get_context_snapshot_for_worker", get_context_snapshot_for_worker)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(
            executor_type="claude-agent-worker",
            agent_id="general-agent",
            skill_id="general-chat",
            context_snapshot_id="ctx-existing",
            context_snapshot={},
        ),
        AdapterRegistry({"claude-agent-worker": CaptureAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert captured["payload"].context_snapshot["source"] == "chat_stream"
    assert captured["payload"].context_snapshot["used_context_summary"] == {
        "source": "chat_stream",
        "input_keys": ["message"],
        "memory_policy_source": "not_recorded",
        "long_term_memory_read": False,
    }
    assert captured["payload"].context_snapshot["context_pack_generated_at"] == "2026-06-12T01:23:45Z"
    serialized = json.dumps(captured["payload"].context_snapshot, ensure_ascii=False).lower()
    assert "stored_context_snapshot" not in serialized


@pytest.mark.asyncio
async def test_worker_rebuilds_db_context_snapshot_with_public_provenance(monkeypatch):
    captured = {}

    class CaptureAdapter:
        async def submit_run(self, payload, event_sink=None):
            captured["payload"] = payload
            return ExecutorResult(
                status="succeeded",
                adapter_version="capture-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="capture/1",
                capabilities={},
                result={"message": "done"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-context"

    async def get_context_snapshot_for_worker(conn, **kwargs):
        return {
            "id": "ctx-existing",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": "executor",
            "included_message_ids": ["msg-a"],
            "included_file_ids": ["file-a"],
            "included_artifact_ids": ["artifact-a"],
            "included_memory_record_ids": [],
            "redaction_summary_json": {},
            "payload_json": {
                "schema_version": "ai-platform.context-snapshot.v1",
                "source": "forged_db_source",
                "agent_id": "general-agent",
                "input_keys": ["raw_storage_key"],
                "message_count": 99,
                "file_count": 99,
                "artifact_count": 99,
                "memory_record_count": 99,
                "memory_policy": {
                    "source": "stored forged-source",
                    "memory_enabled": False,
                    "long_term_memory_enabled": True,
                    "retention_days": "bad",
                },
                "memoryPolicy": {
                    "source": "forged-camel-memory-policy",
                    "memory_enabled": True,
                    "retention_days": 1,
                },
                "window": "current",
                "used_context_summary": {
                    "source": "forged_nested_source",
                    "input_keys": ["storage_key"],
                    "long_term_memory_read": True,
                },
                "provenance": {"source": "forged-provenance"},
                "Provenance": {"source": "forged-title-provenance"},
                "provenance%5Fsummary": {"source": "forged-encoded-provenance"},
                "summary": "legacy summary",
                "Summary": "legacy title summary",
                "summary%5Fpayload": {"source": "forged-encoded-summary"},
                "raw_storage_key": "tenant/private/object",
                "raw%5Fstorage%5Fkey": "s3://encoded/private",
                "sandbox%5Fworkdir": "/tmp/encoded-private",
                "executor%5Fprivate%5Fpayload": {"token": "encoded-private"},
                "used%5Fcontext%5Fsummary": {"source": "forged-encoded"},
            },
            "created_at": None,
        }

    async def fail_record_context(*args, **kwargs):
        raise AssertionError("verified queue snapshots must be reconstructed from DB scope")

    async def complete_run(conn, **kwargs):
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.get_context_snapshot_for_worker", get_context_snapshot_for_worker)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(executor_type="claude-agent-worker", agent_id="general-agent", skill_id="general-chat"),
        AdapterRegistry({"claude-agent-worker": CaptureAdapter()}),
    )

    assert outcome.status == "succeeded"
    context_snapshot = captured["payload"].context_snapshot
    assert context_snapshot["source"] == "stored_context_snapshot"
    assert context_snapshot["referenced_materials"] == {
        "message_count": 1,
        "file_count": 1,
        "artifact_count": 1,
        "memory_record_count": 0,
    }
    assert context_snapshot["used_context_summary"] == {
        "source": "stored_context_snapshot",
        "input_keys": ["attachments", "window"],
        "memory_policy_source": "not_recorded",
        "long_term_memory_read": False,
    }
    assert context_snapshot["latest_artifact_version"] is None
    assert context_snapshot["execution_tier"] == "sdk_only_writing"
    assert context_snapshot["context_pack_generated_at"]
    assert context_snapshot["memory_policy"] == {
        "source": "stored",
        "memory_enabled": False,
        "long_term_memory_enabled": False,
        "retention_days": 90,
    }
    serialized = json.dumps(context_snapshot, ensure_ascii=False)
    assert "forged_db_source" not in serialized
    assert "forged_nested_source" not in serialized
    assert "forged-encoded" not in serialized
    assert "forged-provenance" not in serialized
    assert "legacy summary" not in serialized
    assert "raw_storage_key" not in serialized
    assert "tenant/private/object" not in serialized
    assert "raw%5Fstorage%5Fkey" not in serialized
    assert "s3://encoded/private" not in serialized
    assert "sandbox%5Fworkdir" not in serialized
    assert "encoded-private" not in serialized


@pytest.mark.asyncio
async def test_worker_payload_includes_bounded_context_pack_from_scoped_db_snapshot(monkeypatch):
    captured = {}

    class CaptureAdapter:
        async def submit_run(self, payload, event_sink=None):
            captured["payload"] = payload
            return ExecutorResult(
                status="succeeded",
                adapter_version="capture-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="capture/1",
                capabilities={},
                result={"message": "done"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-context-pack"

    async def get_context_snapshot_for_worker(conn, **kwargs):
        return {
            "id": "ctx-existing",
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "session_id": kwargs["session_id"],
            "run_id": kwargs["run_id"],
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": "executor",
            "included_message_ids": ["msg-a", "msg-b"],
            "included_file_ids": ["file-a"],
            "included_artifact_ids": ["artifact-a"],
            "included_memory_record_ids": ["mem-a"],
            "redaction_summary_json": {},
            "payload_json": {
                "window": "current",
                "message": "review this file",
                "raw_storage_key": "tenant/private/object",
                "sandbox_workdir": "/tmp/private-workdir",
                "used_context_summary": {
                    "source": "runs_api",
                    "input_keys": ["message", "raw_storage_key", "sandbox_workdir"],
                    "memory_policy_source": "stored",
                    "long_term_memory_read": True,
                },
                "execution_tier": "document_worker",
                "latest_artifact_version": "v3",
                "context_pack_version": "v4",
                "context_pack_generated_at": "2026-06-12T01:23:45Z",
            },
            "created_at": None,
        }

    async def fail_record_context(*args, **kwargs):
        raise AssertionError("worker must derive context pack from the scoped DB snapshot")

    async def complete_run(conn, **kwargs):
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.get_context_snapshot_for_worker", get_context_snapshot_for_worker)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(executor_type="claude-agent-worker", agent_id="general-agent", skill_id="general-chat"),
        AdapterRegistry({"claude-agent-worker": CaptureAdapter()}),
    )

    assert outcome.status == "succeeded"
    context_pack = getattr(captured["payload"], "context_pack", None)
    assert isinstance(context_pack, dict)
    assert context_pack["schema_version"] == "ai-platform.executor-context-pack.v1"
    assert context_pack["source"] == "runs_api"
    assert context_pack["referenced_materials"] == {
        "message_count": 2,
        "file_count": 1,
        "artifact_count": 1,
        "memory_record_count": 1,
    }
    assert context_pack["used_context_summary"] == {
        "source": "runs_api",
        "input_keys": ["attachments", "message"],
        "memory_policy_source": "stored",
        "long_term_memory_read": False,
    }
    assert context_pack["execution_tier"] == "document_worker"
    assert context_pack["latest_artifact_version"] == "v3"
    assert context_pack["context_pack_version"] == "v4"
    assert context_pack["context_pack_generated_at"] == "2026-06-12T01:23:45Z"
    assert "1 long-term memory record(s)" not in context_pack["prompt_summary"]
    serialized = json.dumps(context_pack, ensure_ascii=False).lower()
    assert "raw_storage_key" not in serialized
    assert "tenant/private/object" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "/tmp/private-workdir" not in serialized


@pytest.mark.asyncio
async def test_worker_fails_invalid_physical_context_binding_before_adapter(monkeypatch):
    calls = []

    class ForbiddenAdapter:
        async def submit_run(self, payload, event_sink=None):
            raise AssertionError("invalid physical context binding must not reach the adapter")

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"]))
        return "evt-a"

    async def get_context_snapshot_for_worker(conn, **kwargs):
        calls.append(("lookup", kwargs["context_snapshot_id"]))
        return None

    async def fail_run(conn, **kwargs):
        calls.append(("fail", kwargs["error_code"]))
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.get_context_snapshot_for_worker", get_context_snapshot_for_worker)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)

    outcome = await process_run_payload(
        base_payload(
            executor_type="claude-agent-worker",
            context_snapshot_id="ctx-cross-tenant",
            context_snapshot={"source": "cross_tenant_queue_copy"},
        ),
        AdapterRegistry({"claude-agent-worker": ForbiddenAdapter()}),
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "context_snapshot_unavailable"
    assert ("lookup", "ctx-cross-tenant") in calls
    assert ("fail", "context_snapshot_unavailable") in calls


@pytest.mark.asyncio
async def test_worker_rejects_queue_payload_identity_mismatch_before_context_or_executor(monkeypatch):
    calls = []

    class ForbiddenAdapter:
        async def submit_run(self, payload, event_sink=None):
            raise AssertionError("identity-mismatched queue payload must not reach executor")

    async def mark_run_running(conn, *, tenant_id, run_id):
        return {
            "id": run_id,
            "tenant_id": tenant_id,
            "workspace_id": "workspace-db",
            "user_id": "user-db",
            "session_id": "session-db",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
        }

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs.get("payload") or {}))
        return "evt-a"

    async def fail_run(conn, **kwargs):
        calls.append(("fail", kwargs["error_code"], kwargs["error_message"]))
        return ToolPermissionTerminalizationProgress(completed=True, status="failed", did_transition=True)

    async def fail_record_context(*args, **kwargs):
        raise AssertionError("identity-mismatched queue payload must not refresh context snapshot")

    async def fail_create_sandbox_lease(*args, **kwargs):
        raise AssertionError("identity-mismatched queue payload must not create runtime sandbox leases")

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.create_sandbox_lease", fail_create_sandbox_lease)

    outcome = await process_run_payload(
        base_payload(
            executor_type="claude-agent-worker",
            workspace_id="workspace-queue",
            user_id="user-queue",
            session_id="session-queue",
        ),
        AdapterRegistry({"claude-agent-worker": ForbiddenAdapter()}),
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "queue_payload_identity_mismatch"
    assert ("fail", "queue_payload_identity_mismatch", "Queue payload identity does not match run record") in calls
    assert any(item[0] == "event" and item[1] == "error" for item in calls)


@pytest.mark.asyncio
async def test_worker_rejects_missing_db_identity_fields_before_context_or_executor(monkeypatch):
    calls = []

    class ForbiddenAdapter:
        async def submit_run(self, payload, event_sink=None):
            raise AssertionError("DB identity with missing user_id must not reach executor")

    async def mark_run_running(conn, *, tenant_id, run_id):
        return {
            "id": run_id,
            "tenant_id": tenant_id,
            "workspace_id": "workspace-a",
            "user_id": None,
            "session_id": "session-a",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
        }

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs.get("payload") or {}))
        return "evt-a"

    async def fail_run(conn, **kwargs):
        calls.append(("fail", kwargs["error_code"]))
        return ToolPermissionTerminalizationProgress(completed=True, status="failed", did_transition=True)

    async def fail_record_context(*args, **kwargs):
        raise AssertionError("missing DB identity must not refresh context snapshot")

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)

    outcome = await process_run_payload(
        base_payload(executor_type="claude-agent-worker"),
        AdapterRegistry({"claude-agent-worker": ForbiddenAdapter()}),
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "queue_payload_identity_mismatch"
    assert ("fail", "queue_payload_identity_mismatch") in calls
    assert any("user_id" in item[2].get("mismatch_fields", []) for item in calls if item[0] == "event")


@pytest.mark.asyncio
async def test_worker_fails_queued_run_when_scope_guard_rejects_running_lock(monkeypatch):
    calls = []

    class ForbiddenAdapter:
        async def submit_run(self, payload, event_sink=None):
            raise AssertionError("scope-invalid queued run must not reach executor")

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("lock", tenant_id, run_id))
        return None

    async def get_run(conn, *, tenant_id, run_id):
        calls.append(("get_run", tenant_id, run_id))
        return {
            "id": run_id,
            "tenant_id": tenant_id,
            "workspace_id": "workspace-a",
            "user_id": None,
            "session_id": "session-a",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "status": "queued",
        }

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs.get("payload") or {}))
        return "evt-a"

    async def fail_run(conn, **kwargs):
        calls.append(("fail", kwargs["error_code"], kwargs["error_message"]))
        return ToolPermissionTerminalizationProgress(completed=True, status="failed", did_transition=True)

    async def fail_record_context(*args, **kwargs):
        raise AssertionError("scope-invalid queued run must not refresh context snapshot")

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.get_run", get_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)

    outcome = await process_run_payload(
        base_payload(executor_type="claude-agent-worker"),
        AdapterRegistry({"claude-agent-worker": ForbiddenAdapter()}),
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "queue_payload_identity_mismatch"
    assert ("fail", "queue_payload_identity_mismatch", "Queued run identity is invalid") in calls
    assert any(item[0] == "event" and item[1] == "error" for item in calls)


@pytest.mark.asyncio
async def test_worker_uses_db_run_input_when_queue_execution_fields_are_tampered(monkeypatch):
    captured = {}
    calls = []
    version = "hash-qa-file-reviewer"

    class CaptureAdapter:
        async def submit_run(self, payload, event_sink=None):
            captured["payload"] = payload
            return ExecutorResult(
                status="succeeded",
                adapter_version="capture-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="capture/1",
                capabilities={},
                result={"message": "done"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return {
            "id": run_id,
            "tenant_id": tenant_id,
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "trace_id": "trace_run_a",
            "input_json": {
                "input": {"mode": "db", "message": "authoritative"},
                "file_ids": ["file-db"],
                    "executor_type": "claude-agent-worker",
                "skill_version": version,
                "release_decision": release_decision(version),
                "skill_manifests": [primary_manifest("general-chat", version)],
                "context_snapshot_id": "ctx-db",
                "context_snapshot": {"context_snapshot_id": "ctx-db"},
            },
        }

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs.get("payload") or {}))
        return "evt-a"

    async def get_context_snapshot_for_worker(conn, **kwargs):
        return {
            "id": "ctx-db",
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "session_id": kwargs["session_id"],
            "run_id": kwargs["run_id"],
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": "executor",
            "included_message_ids": [],
            "included_file_ids": ["file-db"],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "redaction_summary_json": {},
            "payload_json": {"source": "stored_context_snapshot", "input_keys": ["mode", "message"]},
            "created_at": None,
        }

    async def complete_run(conn, **kwargs):
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.get_context_snapshot_for_worker", get_context_snapshot_for_worker)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(
            agent_id="general-agent",
            skill_id="general-chat",
            executor_type="fake",
            input={"mode": "queue-tampered"},
            file_ids=["file-queue"],
            context_snapshot_id="ctx-cross-scope",
            context_snapshot={"source": "queue-tampered"},
        ),
        AdapterRegistry({"claude-agent-worker": CaptureAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert {
        key: value
        for key, value in captured["payload"].input.items()
        if key != "_runtime_tool_policy_subjects"
    } == {"mode": "db", "message": "authoritative"}
    assert captured["payload"].file_ids == ["file-db"]
    assert captured["payload"].skill_version == version
    assert captured["payload"].release_decision == release_decision(version)
    assert captured["payload"].context_snapshot_id == "ctx-db"


@pytest.mark.asyncio
async def test_worker_does_not_refresh_missing_context_for_unknown_executor(monkeypatch):
    calls = []

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs["stage"]))
        return "evt-a"

    async def fail_record_context(*args, **kwargs):
        raise AssertionError("unknown executor must fail before refreshing context")

    async def fail_create_sandbox_lease(*args, **kwargs):
        raise AssertionError("unknown executor must fail before creating runtime sandbox leases")

    async def fail_run(conn, **kwargs):
        calls.append(("fail", kwargs["error_code"]))
        return ToolPermissionTerminalizationProgress(completed=True, status="failed", did_transition=True)

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.create_sandbox_lease", fail_create_sandbox_lease)

    outcome = await process_run_payload(
        base_payload(
            executor_type="missing-executor",
            context_snapshot_id="",
            context_snapshot={},
        ),
        AdapterRegistry({}),
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "unknown_executor_type"
    assert ("fail", "unknown_executor_type") in calls
    assert not any(item[0] == "context" for item in calls)


@pytest.mark.asyncio
async def test_worker_persists_run_skill_snapshots(monkeypatch):
    snapshots = []

    class SkillSnapshotAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="succeeded",
                adapter_version="test-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="test-executor/1",
                capabilities={"skills": True},
                result={
                    "message": "done",
                    "allowed_skills": ["qa-file-reviewer"],
                    "staged_skills": ["qa-file-reviewer"],
                },
                artifacts=[reviewed_docx_artifact()],
                executor_payload={
                    "used_skills": ["qa-file-reviewer"],
                    "used_skills_source": "executor_hook",
                    "skill_manifests": [
                        {
                            "skill_id": "qa-file-reviewer",
                            "version": "hash-evil",
                            "content_hash": "hash-evil",
                            "source": {
                                "kind": "builtin",
                                "storage_key": "tenants/default/private/package.zip",
                                "files": [
                                    {
                                        "relative_path": "SKILL.md",
                                        "size_bytes": 5,
                                        "content_base64": "c2tpbGw=",
                                    }
                                ],
                                "content_hash": "hash-a",
                            },
                            "dependency_ids": ["minimax-docx"],
                            "allowed": True,
                            "staged": True,
                            "used": True,
                        }
                    ],
                },
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def complete_run(conn, **kwargs):
        assert "skill_manifests" not in kwargs["result_json"]
        return True

    async def upsert_run_skill_snapshot(conn, **kwargs):
        snapshots.append(kwargs)

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.repositories.upsert_run_skill_snapshot", upsert_run_skill_snapshot)

    outcome = await process_run_payload(
        base_payload(
            skill_manifests=[
                {
                    **primary_manifest("qa-file-reviewer", "hash-a"),
                    "dependency_ids": ["minimax-docx"],
                }
            ]
        ),
        AdapterRegistry({"fake": SkillSnapshotAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert snapshots == [
        {
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "skill_id": "qa-file-reviewer",
            "skill_version": "hash-a",
            "content_hash": "hash-a",
            "source_json": repository_module.run_skill_snapshot_source_json(
                {
                    **primary_manifest("qa-file-reviewer", "hash-a"),
                    "dependency_ids": ["minimax-docx"],
                },
                release_decision=release_decision("hash-a"),
            ),
            "dependency_ids": ["minimax-docx"],
            "allowed": True,
            "staged": True,
            "used": True,
            "used_skills_source": "executor_hook",
            "inferred_used": False,
        }
    ]


@pytest.mark.asyncio
async def test_worker_persists_reviewed_uploaded_skill_with_complete_governance_identity(monkeypatch):
    snapshots = []
    version = "hash-native-review"
    profile = resolve_skill_execution_profile(
        skill_id="native-review",
        source_kind="uploaded",
        lifecycle_status="released",
    )
    locked_manifest = {
        "skill_id": "native-review",
        "version": version,
        "content_hash": version,
        "source": {"kind": "uploaded"},
        "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
        "dependency_ids": [],
        "lifecycle_status": "released",
        "execution_profile": profile,
        "builtin_tool_identities": profile["builtin_tool_identities"],
        "mcp_tool_ids": [],
        "snapshot_governance": snapshot_governance(version),
        "allowed": True,
        "staged": False,
        "used": False,
    }
    expected_source = repository_module.run_skill_snapshot_source_json(
        locked_manifest,
        release_decision=release_decision(version),
    )

    class ReviewedUploadedSkillAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="succeeded",
                adapter_version="test-adapter/1",
                executor_type="fake",
                executor_version="test-executor/1",
                capabilities={"skills": True},
                result={"message": "done"},
                executor_payload={
                    "used_skills": ["native-review"],
                    "used_skills_source": "executor_hook",
                    "skill_manifests": [
                        {
                            "skill_id": "native-review",
                            "version": "executor-version",
                            "content_hash": "executor-version",
                            "source": {"kind": "uploaded"},
                            "allowed": True,
                            "staged": True,
                            "used": True,
                        }
                    ],
                },
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def upsert_run_skill_snapshot(conn, **kwargs):
        if kwargs["source_json"] != expected_source:
            raise RepositoryConflictError("run_skill_snapshot_identity_mismatch")
        snapshots.append(kwargs)

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.repositories.upsert_run_skill_snapshot", upsert_run_skill_snapshot)

    outcome = await process_run_payload(
        base_payload(
            skill_id="native-review",
            skill_version=version,
            skill_manifests=[locked_manifest],
        ),
        AdapterRegistry({"fake": ReviewedUploadedSkillAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert len(snapshots) == 1
    assert snapshots[0]["source_json"] == expected_source
    assert snapshots[0]["source_json"]["execution_profile"] == profile
    assert snapshots[0]["source_json"]["builtin_tool_identities"] == profile["builtin_tool_identities"]


@pytest.mark.asyncio
async def test_worker_drops_executor_returned_snapshot_governance_without_payload_match(monkeypatch):
    snapshots = []

    class UntrustedGovernanceAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="succeeded",
                adapter_version="test-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="test-executor/1",
                capabilities={"skills": True},
                result={"message": "done"},
                artifacts=[reviewed_docx_artifact()],
                executor_payload={
                    "skill_manifests": [
                        {
                            "skill_id": "qa-file-reviewer",
                            "version": "hash-evil",
                            "content_hash": "hash-evil",
                            "source": {"kind": "builtin"},
                            "snapshot_governance": {
                                "schema_version": "ai-platform.skill-pinned-snapshot-governance.v1",
                                "release_decision": {"selected_version": "hash-a"},
                                "storage_key": "tenants/default/private/package.zip",
                                "content_hash": "hash-a",
                                "selected_files": [
                                    {"relative_path": "SKILL.md", "content_base64": "c2tpbGw="}
                                ],
                            },
                            "allowed": True,
                            "staged": True,
                            "used": True,
                        }
                    ],
                },
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def complete_run(conn, **kwargs):
        return True

    async def upsert_run_skill_snapshot(conn, **kwargs):
        snapshots.append(kwargs)

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.repositories.upsert_run_skill_snapshot", upsert_run_skill_snapshot)

    outcome = await process_run_payload(
        base_payload(skill_manifests=[primary_manifest("qa-file-reviewer", "hash-a")]),
        AdapterRegistry({"fake": UntrustedGovernanceAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert snapshots[0]["skill_version"] == "hash-a"
    assert snapshots[0]["content_hash"] == "hash-a"
    assert snapshots[0]["source_json"] == repository_module.run_skill_snapshot_source_json(
        primary_manifest("qa-file-reviewer", "hash-a"),
        release_decision=release_decision("hash-a"),
    )
    serialized = json.dumps(snapshots[0]["source_json"], ensure_ascii=False)
    assert snapshots[0]["source_json"]["snapshot_governance"] == repository_module.run_skill_snapshot_source_json(
        primary_manifest("qa-file-reviewer", "hash-a"),
        release_decision=release_decision("hash-a"),
    )["snapshot_governance"]
    assert "selected_version" not in serialized
    assert "hash-evil" not in serialized
    assert "storage_key" not in serialized
    assert "content_hash" not in serialized
    assert "content_base64" not in serialized


@pytest.mark.asyncio
async def test_worker_uses_payload_source_instead_of_executor_returned_source(monkeypatch):
    snapshots = []

    class UntrustedSourceAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="succeeded",
                adapter_version="test-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="test-executor/1",
                capabilities={"skills": True},
                result={"message": "done"},
                artifacts=[reviewed_docx_artifact()],
                executor_payload={
                    "skill_manifests": [
                        {
                            "skill_id": "qa-file-reviewer",
                            "version": "hash-evil",
                            "content_hash": "hash-evil",
                            "source": {
                                "kind": "uploaded",
                                "asset_dir": "executor-controlled",
                                "storage_key": "tenants/default/private/package.zip",
                            },
                            "allowed": True,
                            "staged": True,
                            "used": True,
                        }
                    ],
                },
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def complete_run(conn, **kwargs):
        return True

    async def upsert_run_skill_snapshot(conn, **kwargs):
        snapshots.append(kwargs)

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.repositories.upsert_run_skill_snapshot", upsert_run_skill_snapshot)

    outcome = await process_run_payload(
        base_payload(
            skill_manifests=[
                    {
                        **primary_manifest("qa-file-reviewer", "hash-a"),
                        "source": {"kind": "builtin", "asset_dir": "qa-file-reviewer", "version": "hash-a"},
                    }
            ]
        ),
        AdapterRegistry({"fake": UntrustedSourceAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert snapshots[0]["skill_version"] == "hash-a"
    assert snapshots[0]["content_hash"] == "hash-a"
    assert snapshots[0]["source_json"] == repository_module.run_skill_snapshot_source_json(
        {
            **primary_manifest("qa-file-reviewer", "hash-a"),
            "source": {"kind": "builtin", "asset_dir": "qa-file-reviewer", "version": "hash-a"},
        },
        release_decision=release_decision("hash-a"),
    )
    serialized = json.dumps(snapshots[0]["source_json"], ensure_ascii=False)
    assert "uploaded" not in serialized
    assert "executor-controlled" not in serialized
    assert "storage_key" not in serialized
    assert "hash-a" not in serialized


@pytest.mark.asyncio
async def test_worker_drops_executor_skill_manifest_without_payload_match(monkeypatch):
    snapshots = []

    class UnmatchedSkillAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="succeeded",
                adapter_version="test-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="test-executor/1",
                capabilities={"skills": True},
                result={"message": "done"},
                artifacts=[reviewed_docx_artifact()],
                executor_payload={
                    "used_skills": ["qa-file-reviewer", "unlisted-skill"],
                    "used_skills_source": "executor_hook",
                    "skill_manifests": [
                        {
                            "skill_id": "qa-file-reviewer",
                            "version": "hash-a",
                            "content_hash": "hash-a",
                            "allowed": True,
                            "staged": True,
                            "used": True,
                        },
                        {
                            "skill_id": "unlisted-skill",
                            "version": "hash-unlisted",
                            "content_hash": "hash-unlisted",
                            "source": {"kind": "builtin", "asset_dir": "unlisted-skill"},
                            "dependency_ids": ["hidden-dep"],
                            "allowed": True,
                            "staged": True,
                            "used": True,
                        },
                    ],
                },
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def complete_run(conn, **kwargs):
        return True

    async def upsert_run_skill_snapshot(conn, **kwargs):
        snapshots.append(kwargs)

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.repositories.upsert_run_skill_snapshot", upsert_run_skill_snapshot)

    outcome = await process_run_payload(
        base_payload(skill_manifests=[primary_manifest("qa-file-reviewer", "hash-a")]),
        AdapterRegistry({"fake": UnmatchedSkillAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert [item["skill_id"] for item in snapshots] == ["qa-file-reviewer"]


@pytest.mark.asyncio
async def test_worker_persists_platform_controlled_runner_skill_as_used(monkeypatch):
    snapshots = []

    class ControlledRunnerSkillAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="succeeded",
                adapter_version="test-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="test-executor/1",
                capabilities={"skills": True},
                result={
                    "message": "controlled runner completed",
                    "allowed_skills": ["qa-file-reviewer", "minimax-docx"],
                    "staged_skills": ["qa-file-reviewer", "minimax-docx"],
                },
                artifacts=[reviewed_docx_artifact()],
                executor_payload={
                    "used_skills": ["qa-file-reviewer"],
                    "used_skills_source": "platform_controlled_runner",
                    "inferred_used_skills": ["qa-file-reviewer", "minimax-docx"],
                    "skill_manifests": [
                        {
                            "skill_id": "qa-file-reviewer",
                            "version": "hash-reviewer",
                            "content_hash": "hash-reviewer",
                            "source": {"kind": "builtin"},
                            "dependency_ids": ["minimax-docx"],
                            "allowed": True,
                            "staged": True,
                            "used": True,
                        },
                        {
                            "skill_id": "minimax-docx",
                            "version": "hash-docx",
                            "content_hash": "hash-docx",
                            "source": {"kind": "builtin"},
                            "dependency_ids": [],
                            "allowed": True,
                            "staged": True,
                            "used": False,
                        },
                    ],
                },
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def complete_run(conn, **kwargs):
        return True

    async def upsert_run_skill_snapshot(conn, **kwargs):
        snapshots.append(kwargs)

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.repositories.upsert_run_skill_snapshot", upsert_run_skill_snapshot)

    outcome = await process_run_payload(
        base_payload(
            skill_manifests=[
                {
                    **primary_manifest("qa-file-reviewer", "hash-reviewer"),
                    "dependency_ids": ["minimax-docx"],
                },
                {
                    **primary_manifest("minimax-docx", "hash-docx"),
                    "builtin_tool_identities": ["Bash", "Write"],
                },
            ]
        ),
        AdapterRegistry({"fake": ControlledRunnerSkillAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert snapshots[0]["skill_id"] == "qa-file-reviewer"
    assert snapshots[0]["used"] is True
    assert snapshots[0]["used_skills_source"] == "platform_controlled_runner"
    assert snapshots[0]["inferred_used"] is False
    assert snapshots[1]["skill_id"] == "minimax-docx"
    assert snapshots[1]["used"] is False
    assert snapshots[1]["used_skills_source"] == "inferred"
    assert snapshots[1]["inferred_used"] is True


@pytest.mark.asyncio
async def test_worker_rejects_used_skill_without_native_provenance(monkeypatch):
    snapshots = []
    completed = {}

    class InferredSkillAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="succeeded",
                adapter_version="test-adapter/1",
                executor_type="claude-agent-worker",
                executor_version="test-executor/1",
                capabilities={"skills": True},
                result={
                    "message": "done",
                    "allowed_skills": ["qa-file-reviewer"],
                    "staged_skills": ["qa-file-reviewer"],
                    "used_skills": ["qa-file-reviewer"],
                    "used_skills_source": "inferred",
                    "inferred_used_skills": ["qa-file-reviewer"],
                    "skill_manifests": [
                        {
                            "skill_id": "qa-file-reviewer",
                            "version": "hash-a",
                            "content_hash": "hash-a",
                            "source": {"kind": "builtin"},
                            "dependency_ids": [],
                            "allowed": True,
                            "staged": True,
                            "used": True,
                        }
                    ],
                },
                artifacts=[reviewed_docx_artifact()],
                executor_payload={"inferred_used_skills": ["qa-file-reviewer"]},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def complete_run(conn, *, tenant_id, run_id, result_json):
        completed["result_json"] = result_json
        return True

    async def upsert_run_skill_snapshot(conn, **kwargs):
        snapshots.append(kwargs)

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.repositories.upsert_run_skill_snapshot", upsert_run_skill_snapshot)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": InferredSkillAdapter()}))

    assert outcome.status == "succeeded"
    assert completed["result_json"]["used_skills"] == []
    assert "used_skills_source" not in completed["result_json"]
    assert "inferred_used_skills" not in completed["result_json"]
    assert completed["result_json"]["skills"]["used_skills"] == []
    assert snapshots[0]["used"] is False
    assert snapshots[0]["used_skills_source"] == "inferred"
    assert snapshots[0]["inferred_used"] is True


@pytest.mark.asyncio
async def test_worker_persists_g2_executor_contract_latency_and_token_placeholders(monkeypatch):
    calls = []

    class G2Adapter:
        async def submit_run(self, payload, event_sink=None):
            calls.append(("payload_trace", payload.trace_id, payload.schema_version))
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"streaming": False},
                result={"message": "done"},
                executor_payload={
                    "input_token_count": 11,
                    "output_token_count": 13,
                    "estimated_cost_minor": 17,
                },
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        calls.append(
            (
                "event",
                kwargs["event_type"],
                kwargs.get("latency_ms"),
                kwargs.get("input_token_count"),
                kwargs.get("output_token_count"),
                kwargs.get("estimated_cost_minor"),
                kwargs.get("trace_id"),
            )
        )
        return "evt-a"

    async def complete_run(conn, **kwargs):
        result = kwargs["result_json"]
        calls.append(("complete", result["latency_ms"], result["token_counts"], result["cost"]))
        return True

    monotonic_values = iter([10.0])

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr(
        "app.worker.time.monotonic",
        lambda: next(monotonic_values, 10.25),
        raising=False,
    )

    outcome = await process_run_payload(
        base_payload(
            file_ids=[],
            skill_id="general-chat",
            agent_id="general-agent",
            context_snapshot={},
        ),
        AdapterRegistry({"fake": G2Adapter()}),
    )

    assert outcome.status == "succeeded"
    assert ("payload_trace", "trace_run_a", "ai-platform.run-payload.v1") in calls
    assert ("complete", 250, {"input": 11, "output": 13, "total": 24}, {"estimated_cost_minor": 17}) in calls
    succeeded_event = next(item for item in calls if item[0] == "event" and item[1] == "run_succeeded")
    assert succeeded_event[2:] == (250, 11, 13, 17, "trace_run_a")


@pytest.mark.asyncio
async def test_worker_persists_sdk_usage_as_run_observability(monkeypatch):
    calls = []

    class SdkUsageAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="claude-agent-worker",
                executor_version="claude-agent-worker/1",
                capabilities={"streaming": False},
                result={"message": "done"},
                executor_payload={
                    "sdk_used": True,
                    "sdk_session_id": "sdk-session-a",
                    "sdk_usage": {
                        "input_tokens": 101,
                        "output_tokens": 37,
                        "total_tokens": 138,
                        "total_cost_usd": 0.0123,
                    },
                },
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        calls.append(
            (
                "event",
                kwargs["event_type"],
                kwargs.get("latency_ms"),
                kwargs.get("input_token_count"),
                kwargs.get("output_token_count"),
                kwargs.get("total_token_count"),
                kwargs.get("estimated_cost_minor"),
            )
        )
        return "evt-a"

    async def complete_run(conn, **kwargs):
        result = kwargs["result_json"]
        calls.append(("complete", result["latency_ms"], result["token_counts"], result["cost"]))
        return True

    monotonic_values = iter([20.0])

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr(
        "app.worker.time.monotonic",
        lambda: next(monotonic_values, 20.5),
        raising=False,
    )

    outcome = await process_run_payload(
        base_payload(file_ids=[], skill_id="general-chat", agent_id="general-agent"),
        AdapterRegistry({"fake": SdkUsageAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert ("complete", 500, {"input": 101, "output": 37, "total": 138}, {"estimated_cost_minor": 1}) in calls
    succeeded_event = next(item for item in calls if item[0] == "event" and item[1] == "run_succeeded")
    assert succeeded_event[2:] == (500, 101, 37, 138, 1)


@pytest.mark.asyncio
async def test_worker_persists_artifact_manifest_contract(monkeypatch):
    created = []
    events = []

    class ArtifactAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"streaming": False},
                result={"message": "done"},
                artifacts=[
                    ArtifactManifest(
                        artifact_type="reviewed_docx",
                        label="批注 Word",
                        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        storage_key="tenants/tenant-a/runs/run-a/artifacts/1/reviewed.docx",
                        size_bytes=10,
                        manifest={
                            "local_path": "/tmp/worker/output.docx",
                            "source_file_id": "file-a",
                            "source_step_id": "step-a",
                            "producer_kind": "subagent",
                            "producer_role": "reviewer",
                            "checkpoint_id": "checkpoint-a",
                            "subagent_id": "subagent-a",
                            "skill_id": "qa-file-reviewer",
                            "command_sha256": "b" * 64,
                        },
                    )
                ],
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def create_artifact(conn, **kwargs):
        created.append(kwargs)

    async def append_event(conn, **kwargs):
        events.append(kwargs)
        return "evt-a"

    async def complete_run(conn, **kwargs):
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": ArtifactAdapter()}))

    assert outcome.status == "succeeded"
    assert created[0]["trace_id"] == "trace_run_a"
    assert created[0]["manifest_json"]["schema_version"] == "ai-platform.artifact-manifest.v1"
    assert created[0]["manifest_json"]["artifact_type"] == "reviewed_docx"
    assert created[0]["manifest_json"]["source_file_id"] == "file-a"
    assert "local_path" not in created[0]["manifest_json"]
    artifact_event = next(item for item in events if item["event_type"] == "artifact_created")
    assert artifact_event["payload"]["artifact_id"] == created[0]["artifact_id"]
    assert artifact_event["payload"]["artifact_type"] == "reviewed_docx"
    assert artifact_event["payload"]["download_url"] == f"/api/ai/artifacts/{created[0]['artifact_id']}/download"
    assert artifact_event["payload"]["lineage"] == {
        "source_run_id": "run-a",
        "source_file_id": "file-a",
        "source_step_id": "step-a",
        "producer_kind": "subagent",
        "producer_role": "reviewer",
        "checkpoint_id": "checkpoint-a",
        "subagent_id": "subagent-a",
    }
    assert "skill_id" not in str(artifact_event["payload"])
    assert "command_sha256" not in str(artifact_event["payload"])
    assert "/tmp/" not in str(artifact_event["payload"])


@pytest.mark.asyncio
async def test_worker_marks_adapter_reported_failure(monkeypatch):
    calls = []

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage, message))

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", error_code, error_message))
        return ToolPermissionTerminalizationProgress(completed=True, status="failed", did_transition=True)

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": FakeFailureAdapter()}))

    assert outcome.status == "failed"
    assert outcome.error_code == "fake_failure"
    assert outcome.error_message == "fake run failed for run-a"
    assert any(item[0] == "fail" and item[1] == "fake_failure" for item in calls)


@pytest.mark.asyncio
async def test_worker_preserves_canonical_sdk_failure_diagnostics_without_raw_error(monkeypatch):
    calls = []
    diagnostics = {
        "schema_version": "ai-platform.sdk-turn-diagnostics.v1",
        "terminal_class": "upstream_error",
        "error_code": "claude_agent_sdk_upstream_error",
        "action": "retry_later",
        "retryable": True,
        "counters": {
            "max_turns": 128,
            "turns_observed": 2,
            "assistant_messages": 1,
            "text_blocks": 1,
            "result_messages": 0,
            "tool_admission_denials": 0,
            "skill_invocations": 0,
        },
        "last_public_stage": "message",
        "selected_skill": None,
        "used_skills": [],
    }

    class SdkFailureAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="failed",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={},
                result={
                    "error_code": "claude_agent_sdk_upstream_error",
                    "message": "The execution service failed. Please retry later.",
                    "sdk_error": "claude_agent_sdk_upstream_error",
                    "sdk_turn_diagnostics": diagnostics,
                },
                executor_payload={
                    "sdk_error": "claude_agent_sdk_upstream_error",
                    "sdk_turn_diagnostics": diagnostics,
                    "private_raw_error": "API Error: 529 upstream overloaded request id: req_abc123",
                },
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage, message))

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", error_code, error_message, result_json))
        return ToolPermissionTerminalizationProgress(completed=True, status="failed", did_transition=True)

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": SdkFailureAdapter()}))

    assert outcome.status == "failed"
    assert outcome.error_code == "claude_agent_sdk_upstream_error"
    assert outcome.error_message == "The execution service failed. Please retry later."
    fail_call = next(item for item in calls if item[0] == "fail")
    assert fail_call[2] == "The execution service failed. Please retry later."
    assert fail_call[3]["sdk_error"] == "claude_agent_sdk_upstream_error"
    assert fail_call[3]["sdk_turn_diagnostics"] == diagnostics
    assert "req_abc123" not in str(fail_call[3])
    assert sum(1 for item in calls if item[0] == "fail") == 1
    assert not any(
        item[0] == "event" and item[1] in {"run_succeeded", "run_cancelled"}
        for item in calls
    )


@pytest.mark.asyncio
async def test_worker_records_non_secret_runtime_evidence(monkeypatch):
    events = []

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        events.append({"event_type": event_type, "stage": stage, "message": message, "payload": payload or {}})

    async def complete_run(conn, *, tenant_id, run_id, result_json):
        return True

    async def create_artifact(conn, **kwargs):
        return None

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.repositories.new_id", lambda prefix: "art_runtime_evidence")

    outcome = await process_run_payload(
        base_payload(file_ids=[], skill_id="general-chat", agent_id="general-agent"),
        AdapterRegistry({"fake": FakeSuccessAdapter()}),
        worker_id="worker-test-1",
    )

    assert outcome.status == "succeeded"
    started = next(item for item in events if item["message"] == "Run started")
    assert started["payload"]["worker_id"] == "worker-test-1"
    assert started["payload"]["executor_type"] == "fake"
    assert "claude_agent_sdk_enabled" in started["payload"]
    assert "claude_agent_model" in started["payload"]
    payload_text = str(started["payload"]).lower()
    assert "token" not in payload_text
    assert "secret" not in payload_text
    assert "api_key" not in payload_text


@pytest.mark.asyncio
async def test_worker_rejects_bad_queue_payload_without_touching_database(monkeypatch):
    touched = False

    async def mark_run_running(conn, *, tenant_id, run_id):
        nonlocal touched
        touched = True
        return True

    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)

    outcome = await process_run_payload(
        {"run_id": "../bad", "_queue_attempt_id": "qat-test-attempt"},
        AdapterRegistry({"fake": FakeSuccessAdapter()}),
    )

    assert outcome.status == "dead_letter"
    assert outcome.error_code == "invalid_queue_payload"
    assert touched is False


@pytest.mark.asyncio
async def test_worker_skips_stale_queue_payload_when_run_row_is_missing(monkeypatch):
    calls = []

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return False

    async def get_run(conn, *, tenant_id, run_id):
        calls.append(("get_run", tenant_id, run_id))
        return None

    async def append_event(conn, **kwargs):
        raise AssertionError("stale queue payload without a run row must not write run_events")

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.get_run", get_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": FakeSuccessAdapter()}))

    assert outcome.status == "skipped"
    assert outcome.error_code == "stale_queue_payload"
    assert calls == [
        ("running", "tenant-a", "run-a"),
        ("get_run", "tenant-a", "run-a"),
    ]


@pytest.mark.asyncio
async def test_worker_honors_cancel_before_executor_start(monkeypatch):
    calls = []

    class ShouldNotRunAdapter:
        async def submit_run(self, payload, event_sink=None):
            calls.append(("adapter", payload.run_id))
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={},
                result={"message": "should not run"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def is_cancel_requested(conn, *, tenant_id, run_id):
        return True

    async def cancel_run(conn, *, tenant_id, run_id, result_json=None):
        calls.append(("cancel", result_json))
        return ToolPermissionTerminalizationProgress(completed=True, status="cancelled", did_transition=True)

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage, message))

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.is_cancel_requested", is_cancel_requested)
    monkeypatch.setattr("app.worker.repositories.cancel_run", cancel_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": ShouldNotRunAdapter()}))

    assert outcome.status == "cancelled"
    assert ("adapter", "run-a") not in calls
    assert any(item[0] == "cancel" for item in calls)


@pytest.mark.asyncio
async def test_worker_does_not_report_soft_cancel_intent_as_cancelled(monkeypatch):
    """A soft route intent cannot produce a cancelled worker outcome or terminal side effect."""

    calls = []

    class ShouldNotRunAdapter:
        async def submit_run(self, payload, event_sink=None):
            raise AssertionError("soft cancellation must stop before adapter execution")

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def is_cancel_requested(conn, *, tenant_id, run_id):
        return True

    async def cancel_run(conn, *, tenant_id, run_id, result_json=None):
        calls.append(("cancel", result_json))
        return ToolPermissionTerminalizationProgress(completed=True, status="cancel_requested")

    async def append_event(_conn, **kwargs):
        calls.append(("event", kwargs["event_type"]))

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.is_cancel_requested", is_cancel_requested)
    monkeypatch.setattr("app.worker.repositories.cancel_run", cancel_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": ShouldNotRunAdapter()}))

    assert outcome.status == "skipped"
    assert outcome.error_code == "stale_terminal_state"
    assert any(call[0] == "cancel" for call in calls)
    assert [call[1] for call in calls if call[0] == "event"] == ["worker_started"]


@pytest.mark.asyncio
async def test_worker_parks_top_level_multi_agent_parent_for_dispatcher_without_running_adapter(monkeypatch):
    calls = []

    class Settings:
        multi_agent_dispatch_worker_enabled = True
        claude_agent_sdk_enabled = False
        claude_agent_model = "test-model"

    class ShouldNotRunAdapter:
        async def submit_run(self, payload, event_sink=None):
            raise AssertionError("parked multi-agent parent must not execute adapter steps")

    locked_run = locked_run_from_payload(
        base_payload(
            file_ids=[],
            agent_id="general-agent",
            skill_id="general-chat",
            input={
                "message": "build feature",
                "execution_mode": "multi_agent",
                "multi_agent_steps": [{"step_key": "code", "depends_on": []}],
            },
            executor_type="fake",
            skill_manifests=[primary_manifest("general-chat", "hash-general-chat")],
        )
    )
    locked_run["trace_id"] = "trace-run-a"

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return locked_run

    async def mark_parent_awaiting_dispatch(conn, *, tenant_id, run_id, worker_id):
        calls.append(("park", tenant_id, run_id, worker_id))
        return True

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs["stage"], kwargs.get("payload") or {}))
        return "evt-a"

    async def fail_create_sandbox_lease(*args, **kwargs):
        raise AssertionError("parked multi-agent parent must not create runtime sandbox leases")

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.get_settings", lambda: Settings(), raising=False)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr(
        "app.worker.repositories.mark_multi_agent_dispatch_parent_awaiting_dispatch",
        mark_parent_awaiting_dispatch,
        raising=False,
    )
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.create_sandbox_lease", fail_create_sandbox_lease)

    outcome = await process_run_payload(
        base_payload(
            file_ids=[],
            agent_id="general-agent",
            skill_id="general-chat",
            input={"message": "build feature"},
            executor_type="fake",
            skill_manifests=[primary_manifest("general-chat", "hash-general-chat")],
        ),
        AdapterRegistry({"fake": ShouldNotRunAdapter()}),
        worker_id="worker-a",
    )

    assert outcome.status == "skipped"
    assert calls[0] == ("running", "tenant-a", "run-a")
    assert ("park", "tenant-a", "run-a", "worker-a") in calls
    parked_events = [item for item in calls if item[0] == "event" and item[1] == "multi_agent_dispatch_parent_parked"]
    assert parked_events
    assert parked_events[0][3]["visible_to_user"] is False
    assert parked_events[0][3]["orchestration_state"] == "awaiting_dispatch"


@pytest.mark.asyncio
async def test_worker_stops_running_executor_after_cancel_requested_on_event_boundary(monkeypatch):
    calls = []
    cancel_checks = 0

    class StreamingAdapter:
        async def submit_run(self, payload, event_sink=None):
            await event_sink(
                event_type="assistant_delta",
                stage="message",
                message="partial",
                payload={"delta": "partial"},
            )
            calls.append(("adapter", "continued_after_cancel"))
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"streaming": True},
                result={"message": "should not complete"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def is_cancel_requested(conn, *, tenant_id, run_id):
        nonlocal cancel_checks
        cancel_checks += 1
        return cancel_checks >= 2

    async def cancel_run(conn, *, tenant_id, run_id, result_json=None):
        calls.append(("cancel", result_json))
        return ToolPermissionTerminalizationProgress(completed=True, status="cancelled", did_transition=True)

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage, message))

    async def complete_run(conn, **kwargs):
        calls.append(("complete", kwargs["result_json"]))
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.is_cancel_requested", is_cancel_requested)
    monkeypatch.setattr("app.worker.repositories.cancel_run", cancel_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": StreamingAdapter()}))

    assert outcome.status == "cancelled"
    assert ("adapter", "continued_after_cancel") not in calls
    assert any(item[0] == "cancel" for item in calls)
    assert not any(item[0] == "complete" for item in calls)
    assert not any(item[0] == "event" and item[1] == "run_cancelled" for item in calls)


@pytest.mark.asyncio
async def test_worker_stops_silent_executor_after_cancel_requested(monkeypatch):
    calls = []
    cancel_checks = 0

    class SilentAdapter:
        async def submit_run(self, payload, event_sink=None):
            calls.append(("adapter", "started"))
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                calls.append(("adapter", "cancelled"))
                raise

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def is_cancel_requested(conn, *, tenant_id, run_id):
        nonlocal cancel_checks
        cancel_checks += 1
        return cancel_checks >= 3

    async def cancel_run(conn, *, tenant_id, run_id, result_json=None):
        calls.append(("cancel", result_json))
        return ToolPermissionTerminalizationProgress(
            completed=True,
            status="cancelled",
            did_transition=True,
        )

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs["stage"]))

    async def complete_run(conn, **kwargs):
        raise AssertionError("cancelled silent execution must not complete successfully")

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.is_cancel_requested", is_cancel_requested)
    monkeypatch.setattr("app.worker.repositories.cancel_run", cancel_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    original_submit_until_cancelled = worker_module._submit_run_until_cancelled

    async def submit_until_cancelled(adapter, run_payload, *, event_sink, cancel_requested):
        return await original_submit_until_cancelled(
            adapter,
            run_payload,
            event_sink=event_sink,
            cancel_requested=cancel_requested,
            poll_interval_seconds=0.01,
        )

    monkeypatch.setattr("app.worker._submit_run_until_cancelled", submit_until_cancelled)

    outcome = await asyncio.wait_for(
        process_run_payload(base_payload(), AdapterRegistry({"fake": SilentAdapter()})),
        timeout=1.0,
    )

    assert outcome.status == "cancelled"
    assert ("adapter", "cancelled") in calls
    assert any(item[0] == "cancel" for item in calls)


@pytest.mark.asyncio
async def test_worker_waits_for_non_cooperative_adapter_before_cancel_terminal_and_lease_release(monkeypatch):
    calls = []
    cancel_checks = 0
    adapter_started = asyncio.Event()
    allow_quiescence = asyncio.Event()
    stop_waiting = asyncio.Event()

    class NonCooperativeAdapter:
        async def submit_run(self, payload, event_sink=None):
            adapter_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                stop_waiting.set()
                await allow_quiescence.wait()
                calls.append(("adapter", "quiescent"))
                raise

    async def mark_run_running(conn, *, tenant_id, run_id):
        locked = locked_run_from_payload(
            base_payload(file_ids=[], skill_id="general-chat", agent_id="general-agent")
        )
        return locked

    async def is_cancel_requested(conn, *, tenant_id, run_id):
        nonlocal cancel_checks
        cancel_checks += 1
        return adapter_started.is_set() and cancel_checks >= 3

    async def cancel_run(conn, *, tenant_id, run_id, result_json=None):
        calls.append(("cancel", run_id))
        return ToolPermissionTerminalizationProgress(
            completed=True,
            status="cancelled",
            did_transition=True,
        )

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs["event_type"], kwargs["stage"], kwargs.get("payload") or {}))

    async def get_context_snapshot_for_worker(conn, **kwargs):
        return {
            "id": kwargs["context_snapshot_id"],
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "session_id": kwargs["session_id"],
            "run_id": kwargs["run_id"],
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": "executor",
            "included_message_ids": [],
            "included_file_ids": [],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "redaction_summary_json": {},
            "payload_json": {
                "schema_version": "ai-platform.context-snapshot.v1",
                "source": "test",
                "message_count": 0,
                "file_count": 0,
                "memory_record_count": 0,
            },
            "created_at": None,
        }

    async def create_sandbox_lease(conn, **kwargs):
        return {"id": "lease-cancel-a", **kwargs}

    async def release_sandbox_lease(conn, **kwargs):
        calls.append(("lease_release", kwargs["reason"]))

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.is_cancel_requested", is_cancel_requested)
    monkeypatch.setattr("app.worker.repositories.cancel_run", cancel_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr(
        "app.worker.repositories.get_context_snapshot_for_worker",
        get_context_snapshot_for_worker,
    )
    monkeypatch.setattr("app.worker.repositories.create_sandbox_lease", create_sandbox_lease)
    monkeypatch.setattr("app.worker.repositories.release_sandbox_lease", release_sandbox_lease)

    original_submit_until_cancelled = worker_module._submit_run_until_cancelled

    async def submit_until_cancelled(adapter, run_payload, *, event_sink, cancel_requested):
        return await original_submit_until_cancelled(
            adapter,
            run_payload,
            event_sink=event_sink,
            cancel_requested=cancel_requested,
            poll_interval_seconds=0.005,
            stop_timeout_seconds=0.01,
            progress_interval_seconds=60,
        )

    monkeypatch.setattr("app.worker._submit_run_until_cancelled", submit_until_cancelled)

    task = asyncio.create_task(
        process_run_payload(
            base_payload(file_ids=[], skill_id="general-chat", agent_id="general-agent"),
            AdapterRegistry({"fake": NonCooperativeAdapter()}),
        )
    )
    started_done, _ = await asyncio.wait(
        {asyncio.create_task(adapter_started.wait()), task}, timeout=0.5
    )
    assert adapter_started.is_set(), (
        task.exception() if task.done() and not task.cancelled() else calls
    )
    done, _ = await asyncio.wait({asyncio.create_task(stop_waiting.wait()), task}, timeout=0.5)
    assert stop_waiting.is_set(), (
        (
            task.exception()
            if task.done() and not task.cancelled() and task.exception() is not None
            else task.result()
            if task.done() and not task.cancelled()
            else calls
        )
    )
    await asyncio.sleep(0.03)

    assert not any(item[0] in {"cancel", "lease_release"} for item in calls)
    waiting_events = [
        item for item in calls if item[0] == "event" and item[3].get("wait_reason") == "cancellation"
    ]
    assert waiting_events

    allow_quiescence.set()
    outcome = await asyncio.wait_for(task, timeout=0.5)

    assert outcome.status == "cancelled"
    assert calls.index(("adapter", "quiescent")) < calls.index(("cancel", "run-a"))
    assert calls.index(("cancel", "run-a")) < calls.index(("lease_release", "run_cancelled"))


@pytest.mark.asyncio
async def test_worker_records_unknown_executor_as_failed(monkeypatch):
    calls = []

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage))

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", error_code, error_message))
        return ToolPermissionTerminalizationProgress(completed=True, status="failed", did_transition=True)

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)

    outcome = await process_run_payload(base_payload(executor_type="missing"), AdapterRegistry({"fake": FakeSuccessAdapter()}))

    assert outcome.status == "failed"
    assert outcome.error_code == "unknown_executor_type"
    assert any(item[0] == "fail" and item[1] == "unknown_executor_type" for item in calls)


@pytest.mark.asyncio
async def test_worker_honors_explicit_empty_registry(monkeypatch):
    calls = []

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage))

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", error_code, error_message))
        return ToolPermissionTerminalizationProgress(completed=True, status="failed", did_transition=True)

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)

    outcome = await process_run_payload(
        base_payload(executor_type="claude-agent-worker"),
        AdapterRegistry({}),
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "unknown_executor_type"
    assert any(item[0] == "fail" and item[1] == "unknown_executor_type" for item in calls)


@pytest.mark.asyncio
async def test_worker_honors_falsy_registry_double(monkeypatch):
    calls = []

    class FalsyRegistry:
        def __bool__(self):
            return False

        def get(self, executor_type):
            calls.append(("get", executor_type))
            return FakeSuccessAdapter()

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return True

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage))

    async def create_artifact(conn, **kwargs):
        calls.append(("artifact", kwargs["artifact_type"]))

    async def complete_run(conn, *, tenant_id, run_id, result_json):
        calls.append(("complete", result_json["executor"]["adapter_version"]))
        return True

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", error_code))
        return ToolPermissionTerminalizationProgress(completed=True, status="failed", did_transition=True)

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(file_ids=[], skill_id="general-chat", agent_id="general-agent", executor_type="fake"),
        FalsyRegistry(),
    )

    assert outcome.status == "succeeded"
    assert ("get", "fake") in calls
    assert ("complete", "fake-adapter/1") in calls


@pytest.mark.asyncio
async def test_worker_skips_unknown_executor_payload_for_terminal_run(monkeypatch):
    calls = []

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return False

    async def get_run(conn, *, tenant_id, run_id):
        calls.append(("get_run", tenant_id, run_id))
        return {"id": run_id, "status": "succeeded"}

    async def fail_run(conn, **kwargs):
        calls.append(("fail", kwargs["error_code"]))
        raise AssertionError("terminal run must not be overwritten by stale unknown executor payload")

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage))

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.get_run", get_run)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)

    outcome = await process_run_payload(
        base_payload(executor_type="missing"),
        AdapterRegistry({"fake": FakeSuccessAdapter()}),
    )

    assert outcome.status == "skipped"
    assert not any(item[0] == "fail" for item in calls)
    assert calls == [
        ("running", "tenant-a", "run-a"),
        ("get_run", "tenant-a", "run-a"),
        ("event", "skip", "worker"),
    ]


@pytest.mark.asyncio
async def test_worker_blocks_direct_runtime211_queue_payload(monkeypatch):
    calls = []

    class DirectRuntime211Adapter:
        async def submit_run(self, payload, event_sink=None):
            calls.append(("adapter", payload.run_id))
            raise AssertionError("direct runtime211 queue payload must not reach adapter")

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", error_code, error_message))
        return ToolPermissionTerminalizationProgress(completed=True, status="failed", did_transition=True)

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage, payload or {}))

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)

    outcome = await process_run_payload(
        base_payload(executor_type="runtime211"),
        AdapterRegistry({"runtime211": DirectRuntime211Adapter()}),
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "legacy_runtime211_direct_executor_disabled"
    assert ("running", "tenant-a", "run-a") in calls
    assert not any(item[0] == "adapter" for item in calls)
    assert any(
        item[0] == "fail" and item[1] == "legacy_runtime211_direct_executor_disabled"
        for item in calls
    )
    denied_event = next(item for item in calls if item[0] == "event" and item[1] == "legacy_runtime211_direct_executor_denied")
    assert denied_event[1] == "legacy_runtime211_direct_executor_denied"
    assert denied_event[2] == "policy"
    assert denied_event[3]["visible_to_user"] is False


@pytest.mark.asyncio
async def test_worker_skips_direct_runtime211_payload_for_terminal_run(monkeypatch):
    calls = []

    class DirectRuntime211Adapter:
        async def submit_run(self, payload, event_sink=None):
            calls.append(("adapter", payload.run_id))
            raise AssertionError("terminal direct runtime211 payload must not reach adapter")

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return False

    async def get_run(conn, *, tenant_id, run_id):
        calls.append(("get_run", tenant_id, run_id))
        return {"id": run_id, "status": "succeeded"}

    async def fail_run(conn, **kwargs):
        calls.append(("fail", kwargs["error_code"]))
        raise AssertionError("terminal run must not be overwritten by stale runtime211 payload")

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage))

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.get_run", get_run)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)

    outcome = await process_run_payload(
        base_payload(executor_type="runtime211"),
        AdapterRegistry({"runtime211": DirectRuntime211Adapter()}),
    )

    assert outcome.status == "skipped"
    assert not any(item[0] in {"adapter", "fail"} for item in calls)
    assert calls == [
        ("running", "tenant-a", "run-a"),
        ("get_run", "tenant-a", "run-a"),
        ("event", "skip", "worker"),
    ]


@pytest.mark.asyncio
async def test_worker_passes_user_id_to_executor_payload(monkeypatch):
    seen = {}

    class IdentityAdapter:
        async def submit_run(self, payload, event_sink=None):
            seen["user_id"] = payload.user_id
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"streaming": False},
                result={"message": "done"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def complete_run(conn, **kwargs):
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(
            user_id="user-a",
            file_ids=[],
            skill_id="general-chat",
            agent_id="general-agent",
        ),
        AdapterRegistry({"fake": IdentityAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert seen["user_id"] == "user-a"


@pytest.mark.asyncio
async def test_worker_records_multi_agent_step_events(monkeypatch):
    step_calls = []

    class StepAdapter:
        async def submit_run(self, payload, event_sink=None):
            await event_sink(
                event_type="agent_step_started",
                stage="agent",
                message="coding agent started",
                payload={"role": "coding", "step_key": "code", "step_index": 1, "depends_on": []},
            )
            await event_sink(
                event_type="agent_step_completed",
                stage="agent",
                message="coding agent completed",
                payload={
                    "role": "coding",
                    "step_key": "code",
                    "step_index": 1,
                    "depends_on": [],
                    "output": "code output",
                },
            )
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"multi_agent": True},
                result={"message": "done"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def upsert_run_step(conn, **kwargs):
        step_calls.append(kwargs)
        return "step-a"

    async def complete_run(conn, **kwargs):
        return True

    async def list_run_steps(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.upsert_run_step", upsert_run_step, raising=False)
    monkeypatch.setattr("app.worker.repositories.list_run_steps", list_run_steps)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(file_ids=[], skill_id="general-chat", agent_id="general-agent"),
        AdapterRegistry({"fake": StepAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert [item["status"] for item in step_calls] == ["running", "succeeded"]
    assert step_calls[0]["step_key"] == "code"
    assert step_calls[0]["step_kind"] == "agent"
    assert step_calls[0]["role"] == "coding"
    assert step_calls[1]["payload_json"]["output"] == "code output"


@pytest.mark.asyncio
async def test_worker_records_multi_agent_blocked_step_events(monkeypatch):
    step_calls = []
    failed_result = {}

    class BlockedStepAdapter:
        async def submit_run(self, payload, event_sink=None):
            await event_sink(
                event_type="agent_step_blocked",
                stage="agent",
                message="test agent blocked by unresolved dependencies",
                payload={
                    "role": "test",
                    "step_key": "verify",
                    "step_index": 2,
                    "depends_on": ["unknown"],
                    "missing_dependencies": ["unknown"],
                    "error_code": "multi_agent_dependency_blocked",
                },
            )
            return ExecutorResult(
                status="failed",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"multi_agent": True},
                result={"error_code": "multi_agent_dependency_blocked", "message": "blocked"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def upsert_run_step(conn, **kwargs):
        step_calls.append(kwargs)
        return "step-a"

    async def list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-verify",
                "run_id": run_id,
                "step_key": "verify",
                "step_kind": "agent",
                "status": "failed",
                "title": "test agent blocked by unresolved dependencies",
                "role": "test",
                "sequence": 2,
                "payload_json": {
                    "depends_on": ["unknown"],
                    "missing_dependencies": ["unknown"],
                    "error_code": "multi_agent_dependency_blocked",
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
        ]

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        failed_result.update(result_json or {})
        return ToolPermissionTerminalizationProgress(completed=True, status="failed", did_transition=True)

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.upsert_run_step", upsert_run_step, raising=False)
    monkeypatch.setattr("app.worker.repositories.list_run_steps", list_run_steps)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)

    outcome = await process_run_payload(
        base_payload(file_ids=[], skill_id="general-chat", agent_id="general-agent"),
        AdapterRegistry({"fake": BlockedStepAdapter()}),
    )

    assert outcome.status == "failed"
    assert len(step_calls) == 1
    assert step_calls[0]["status"] == "failed"
    assert step_calls[0]["step_key"] == "verify"
    assert step_calls[0]["role"] == "test"
    assert step_calls[0]["payload_json"]["missing_dependencies"] == ["unknown"]
    assert step_calls[0]["payload_json"]["error_code"] == "multi_agent_dependency_blocked"
    assert failed_result["multi_agent"]["steps"][0]["error_code"] == "multi_agent_dependency_blocked"
    assert failed_result["multi_agent"]["steps"][0]["error"] is None
    assert failed_result["multi_agent"]["steps"][0]["missing_dependencies"] == ["unknown"]
    assert failed_result["multi_agent"]["counts"] == {
        "total": 1,
        "pending": 0,
        "succeeded": 0,
        "failed": 1,
        "running": 0,
        "cancelled": 0,
        "reused": 0,
        "blocked": 1,
    }


@pytest.mark.asyncio
async def test_worker_includes_multi_agent_step_summary_in_success_result(monkeypatch):
    completed_result = {}

    class MultiAgentAdapter:
        async def submit_run(self, payload, event_sink=None):
            await event_sink(
                event_type="agent_step_reused",
                stage="agent",
                message="coding agent reused checkpoint",
                payload={
                    "role": "coding",
                    "step_key": "code",
                    "step_index": 1,
                    "depends_on": [],
                    "output": "checkpointed code output",
                    "checkpoint_reused": True,
                },
            )
            await event_sink(
                event_type="agent_step_completed",
                stage="agent",
                message="test agent completed",
                payload={
                    "role": "test",
                    "step_key": "verify",
                    "step_index": 2,
                    "depends_on": ["code"],
                    "output": "verify output",
                },
            )
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"multi_agent": True},
                result={"message": "verify output"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def upsert_run_step(conn, **kwargs):
        return "step-a"

    async def list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-code",
                "run_id": run_id,
                "step_key": "code",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "coding agent reused checkpoint",
                "role": "coding",
                "sequence": 1,
                "payload_json": {
                    "depends_on": [],
                    "output": "checkpointed code output",
                    "checkpoint_reused": True,
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": "step-verify",
                "run_id": run_id,
                "step_key": "verify",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "test agent completed",
                "role": "test",
                "sequence": 2,
                "payload_json": {
                    "depends_on": ["code"],
                    "output": "verify output",
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
        ]

    async def complete_run(conn, *, tenant_id, run_id, result_json):
        completed_result.update(result_json)
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.upsert_run_step", upsert_run_step, raising=False)
    monkeypatch.setattr("app.worker.repositories.list_run_steps", list_run_steps)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(file_ids=[], skill_id="general-chat", agent_id="general-agent"),
        AdapterRegistry({"fake": MultiAgentAdapter()}),
    )

    assert outcome.status == "succeeded"
    assert completed_result["multi_agent"] == {
        "steps": [
            {
                "step_key": "code",
                "status": "succeeded",
                "role": "coding",
                "sequence": 1,
                "depends_on": [],
                "checkpoint_reused": True,
                "output": "checkpointed code output",
                "error_code": None,
                "error": None,
                "missing_dependencies": [],
            },
            {
                "step_key": "verify",
                "status": "succeeded",
                "role": "test",
                "sequence": 2,
                "depends_on": ["code"],
                "checkpoint_reused": False,
                "output": "verify output",
                "error_code": None,
                "error": None,
                "missing_dependencies": [],
            },
        ],
        "reused_step_keys": ["code"],
        "completed_step_outputs": {
            "code": "checkpointed code output",
            "verify": "verify output",
        },
        "counts": {
            "total": 2,
            "pending": 0,
            "succeeded": 2,
            "failed": 0,
            "running": 0,
            "cancelled": 0,
            "reused": 1,
            "blocked": 0,
        },
    }


@pytest.mark.asyncio
async def test_worker_includes_multi_agent_step_summary_in_failed_result(monkeypatch):
    failed_result = {}

    class FailingMultiAgentAdapter:
        async def submit_run(self, payload, event_sink=None):
            await event_sink(
                event_type="agent_step_completed",
                stage="agent",
                message="coding agent completed",
                payload={
                    "role": "coding",
                    "step_key": "code",
                    "step_index": 1,
                    "depends_on": [],
                    "output": "code output",
                },
            )
            await event_sink(
                event_type="agent_step_failed",
                stage="agent",
                message="test agent failed",
                payload={
                    "role": "test",
                    "step_key": "verify",
                    "step_index": 2,
                    "depends_on": ["code"],
                    "error_code": "multi_agent_step_failed",
                    "error": "tests failed",
                },
            )
            return ExecutorResult(
                status="failed",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"multi_agent": True},
                result={"error_code": "multi_agent_step_failed", "message": "tests failed"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def upsert_run_step(conn, **kwargs):
        return "step-a"

    async def list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-code",
                "run_id": run_id,
                "step_key": "code",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "coding agent completed",
                "role": "coding",
                "sequence": 1,
                "payload_json": {
                    "depends_on": [],
                    "output": "code output",
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": "step-verify",
                "run_id": run_id,
                "step_key": "verify",
                "step_kind": "agent",
                "status": "failed",
                "title": "test agent failed",
                "role": "test",
                "sequence": 2,
                "payload_json": {
                    "depends_on": ["code"],
                    "error_code": "multi_agent_step_failed",
                    "error": "tests failed",
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
        ]

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        failed_result.update(result_json or {})
        return ToolPermissionTerminalizationProgress(completed=True, status="failed", did_transition=True)

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.upsert_run_step", upsert_run_step, raising=False)
    monkeypatch.setattr("app.worker.repositories.list_run_steps", list_run_steps)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)

    outcome = await process_run_payload(
        base_payload(file_ids=[], skill_id="general-chat", agent_id="general-agent"),
        AdapterRegistry({"fake": FailingMultiAgentAdapter()}),
    )

    assert outcome.status == "failed"
    assert failed_result["multi_agent"] == {
        "steps": [
            {
                "step_key": "code",
                "status": "succeeded",
                "role": "coding",
                "sequence": 1,
                "depends_on": [],
                "checkpoint_reused": False,
                "output": "code output",
                "error_code": None,
                "error": None,
                "missing_dependencies": [],
            },
            {
                "step_key": "verify",
                "status": "failed",
                "role": "test",
                "sequence": 2,
                "depends_on": ["code"],
                "checkpoint_reused": False,
                "output": None,
                "error_code": "multi_agent_step_failed",
                "error": "tests failed",
                "missing_dependencies": [],
            },
        ],
        "reused_step_keys": [],
        "completed_step_outputs": {"code": "code output"},
        "counts": {
            "total": 2,
            "pending": 0,
            "succeeded": 1,
            "failed": 1,
            "running": 0,
            "cancelled": 0,
            "reused": 0,
            "blocked": 0,
        },
    }


def test_executor_result_schema_validation_blocks_unstable_adapter_output():
    result = ExecutorResult(
        status="completed",
        adapter_version="fake-adapter/1",
        executor_type="fake",
        executor_version="fake-executor/1",
        capabilities={},
    )

    with pytest.raises(ValueError, match="Unsupported executor status"):
        result.validate()


def test_default_adapter_registry_does_not_expose_embedded_poco_kernel():
    with pytest.raises(KeyError, match="Unknown executor_type: embedded-poco-kernel"):
        AdapterRegistry().get("embedded-poco-kernel")


def test_explicit_empty_adapter_registry_does_not_fall_back_to_defaults():
    with pytest.raises(KeyError, match="Unknown executor_type: claude-agent-worker"):
        AdapterRegistry({}).get("claude-agent-worker")


@pytest.mark.asyncio
async def test_worker_adds_artifact_links_to_success_result_message(monkeypatch):
    calls = []

    class LocalPathAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="qa-file-reviewer-local",
                executor_version="runner/1",
                capabilities={"artifacts": True},
                result={
                    "message": (
                        "文件审核\n"
                        "详细报告: /tmp/workspace/report.txt\n"
                        "批注文档: /tmp/workspace/reviewed.docx"
                    )
                },
                artifacts=[
                    ArtifactManifest(
                        artifact_type="reviewed_docx",
                        label="审核 Word",
                        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        storage_key="tenants/tenant-a/workspaces/workspace-a/sessions/session-a/runs/run-a/artifacts/1/reviewed.docx",
                        size_bytes=123,
                        manifest={},
                    )
                ],
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage, payload))

    async def create_artifact(conn, **kwargs):
        calls.append(("artifact", kwargs["artifact_id"], kwargs["storage_key"]))

    async def complete_run(conn, *, tenant_id, run_id, result_json):
        calls.append(("complete", result_json))
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    generated_ids = iter(["art_reviewed"])
    monkeypatch.setattr("app.worker.repositories.new_id", lambda prefix: next(generated_ids))

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": LocalPathAdapter()}))

    assert outcome.status == "succeeded"
    complete_payload = next(item[1] for item in calls if item[0] == "complete")
    assert "/tmp/workspace" not in complete_payload["message"]
    assert "审核 Word: /api/ai/artifacts/art_reviewed/download" in complete_payload["message"]
    assert complete_payload["artifacts"][0]["id"] == "art_reviewed"
    assert complete_payload["artifacts"][0]["download_url"] == "/api/ai/artifacts/art_reviewed/download"
    assert "storage_key" not in complete_payload["artifacts"][0]
    assert "tenants/" not in str(complete_payload)


@pytest.mark.asyncio
async def test_worker_sanitizes_artifact_manifest_paths_before_persisting(monkeypatch):
    created = []

    class PathManifestAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"artifacts": True},
                result={"message": "done"},
                artifacts=[
                    ArtifactManifest(
                        artifact_type="reviewed_docx",
                        label="审核 Word",
                        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        storage_key="tenants/tenant-a/workspaces/workspace-a/sessions/session-a/runs/run-a/artifacts/1/reviewed.docx",
                        size_bytes=12,
                        manifest={
                            "review_result": "/tmp/workspace/output/review_result.json",
                            "runner": r"C:\Users\Xinlin.jiang\.codex\skills\qa-file-reviewer\scripts\run_qa_review.py",
                            "cwd": "/tmp/workspace/output",
                            "source_executor": "qa-file-reviewer-local",
                        },
                    )
                ],
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def create_artifact(conn, **kwargs):
        created.append(kwargs)

    async def complete_run(conn, **kwargs):
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.repositories.new_id", lambda prefix: "art-a")

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": PathManifestAdapter()}))

    assert outcome.status == "succeeded"
    manifest = created[0]["manifest_json"]
    assert manifest == {
        "schema_version": "ai-platform.artifact-manifest.v1",
        "artifact_type": "reviewed_docx",
        "source_executor": "qa-file-reviewer-local",
    }
    assert "/tmp/" not in str(manifest)
    assert "C:" not in str(manifest)


@pytest.mark.asyncio
async def test_worker_appends_user_visible_execution_timeline(monkeypatch):
    events = []

    class ArtifactAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"artifacts": True, "streaming": False, "skills": True},
                result={"message": "done"},
                artifacts=[
                    ArtifactManifest(
                        artifact_type="reviewed_docx",
                        label="批注 Word",
                        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        storage_key=(
                            "tenants/tenant-a/workspaces/workspace-a/sessions/session-a/"
                            "runs/run-a/artifacts/1/reviewed.docx"
                        ),
                        size_bytes=12,
                        manifest={},
                    )
                ],
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        events.append({"event_type": event_type, "stage": stage, "message": message, "payload": payload or {}})

    async def create_artifact(conn, **kwargs):
        return None

    async def complete_run(conn, *, tenant_id, run_id, result_json):
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.repositories.new_id", lambda prefix: "art-a")

    outcome = await process_run_payload(base_payload(), AdapterRegistry({"fake": ArtifactAdapter()}))

    assert outcome.status == "succeeded"
    event_types = [item["event_type"] for item in events]
    assert "worker_started" in event_types
    assert "artifact_created" in event_types
    assert "assistant_message_created" in event_types
    assert "run_succeeded" in event_types
    user_visible_types = {"worker_started", "artifact_created", "assistant_message_created", "run_succeeded"}
    assert all(
        item["payload"].get("visible_to_user") is True
        for item in events
        if item["event_type"] in user_visible_types
    )


@pytest.mark.asyncio
async def test_worker_records_general_chat_token_events(monkeypatch):
    events = []

    class StreamingAdapter:
        async def submit_run(self, payload, event_sink=None):
            if event_sink:
                await event_sink(
                    event_type="assistant_delta",
                    stage="thinking",
                    message="private reasoning",
                    payload={"delta": "private reasoning", "visible_to_user": True},
                )
                await event_sink(
                    event_type="assistant_delta",
                    stage="message",
                    message="raw sdk fallback",
                    payload={"content": "raw sdk fallback", "visible_to_user": True},
                )
                await event_sink(
                    event_type="assistant_delta",
                    stage="message",
                    message="executor text must not persist",
                    payload={
                        "delta": "你好",
                        "visible_to_user": True,
                        "tool_args": {"path": "/var/lib/private"},
                        "raw_sdk_event": {"type": "content_block_delta"},
                    },
                )
            return ExecutorResult(
                status="succeeded",
                adapter_version="streaming-test/1",
                executor_type="claude-agent-worker",
                executor_version="test",
                capabilities={"streaming": True},
                result={"message": "你好"},
            )

    class Registry:
        def get(self, executor_type):
            return StreamingAdapter()

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        events.append(kwargs)
        return f"evt_{len(events)}"

    async def complete_run(conn, **kwargs):
        events.append({"event_type": "complete_run", "result_json": kwargs["result_json"]})
        return True

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.fail_run", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    payload = base_payload(skill_id="general-chat", executor_type="claude-agent-worker")
    outcome = await process_run_payload(payload, registry=Registry(), worker_id="worker-stream")

    assert outcome.status == "succeeded"
    assistant_deltas = [event for event in events if event["event_type"] == "assistant_delta"]
    assert len(assistant_deltas) == 1
    assert assistant_deltas[0]["stage"] == "answer"
    assert assistant_deltas[0]["message"] == ""
    assert assistant_deltas[0]["payload"] == {
        "delta": "你好",
        "source": "worker_answer_delta_v1",
        "visible_to_user": True,
        "severity": "info",
    }


@pytest.mark.asyncio
async def test_worker_processes_embedded_poco_kernel_and_persists_stream_events(monkeypatch):
    from app.executors.embedded_poco import EmbeddedPocoAdapter

    events = []
    messages = []

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        events.append(kwargs)
        return f"evt_{len(events)}"

    async def complete_run(conn, **kwargs):
        events.append({"event_type": "complete_run", "result_json": kwargs["result_json"]})
        return True

    async def append_message(conn, **kwargs):
        messages.append(kwargs)
        return "msg-a"

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", append_message)

    outcome = await process_run_payload(
        base_payload(
            agent_id="general-agent",
            skill_id="general-chat",
            file_ids=[],
            input={"message": "hello"},
            executor_type="embedded-poco-kernel",
        ),
        registry=AdapterRegistry({"embedded-poco-kernel": EmbeddedPocoAdapter()}),
        worker_id="worker-embedded",
    )

    assert outcome.status == "succeeded"
    event_types = [event["event_type"] for event in events]
    assert "run_started" in event_types
    assert "assistant_delta" in event_types
    assert "run_completed" in event_types
    assert "assistant_message_created" in event_types
    assert messages[0]["role"] == "assistant"
    assert messages[0]["content"] == "hello"


@pytest.mark.asyncio
async def test_worker_persists_terminal_assistant_message(monkeypatch):
    calls = []

    class MessageAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="succeeded",
                adapter_version="adapter/1",
                executor_type="fake",
                executor_version="fake/1",
                capabilities={"streaming": False},
                result={"message": "最终回答"},
            )

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def append_event(conn, **kwargs):
        return "evt-a"

    async def complete_run(conn, **kwargs):
        calls.append(("complete", kwargs["result_json"]["message"]))
        return True

    async def append_message(conn, **kwargs):
        calls.append(("message", kwargs["role"], kwargs["content"], kwargs["run_id"]))
        return "msg-a"

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", append_message)

    outcome = await process_run_payload(base_payload(file_ids=[], skill_id="general-chat", agent_id="general-agent"), AdapterRegistry({"fake": MessageAdapter()}))

    assert outcome.status == "succeeded"
    assert ("complete", "最终回答") in calls
    assert ("message", "assistant", "最终回答", "run-a") in calls


@pytest.mark.asyncio
async def test_worker_follow_up_terminalization_reconciles_one_final_drain_only(monkeypatch):
    calls = []

    class FailingAdapter:
        async def submit_run(self, _payload, event_sink=None):
            return ExecutorResult(
                status="failed", adapter_version="test", executor_type="fake", executor_version="test",
                capabilities={}, result={"message": "failed", "error_code": "executor_failure"},
            )

    async def mark_run_running(_conn, **_kwargs):
        return True

    async def fail_run(_conn, **_kwargs):
        return ToolPermissionTerminalizationProgress(False, "failed")

    final = ToolPermissionTerminalizationProgress(True, "failed", True, True)

    async def drain(**_kwargs):
        return final

    async def reconcile(**kwargs):
        calls.append((kwargs["tenant_id"], kwargs["run_id"], kwargs["progress"].did_transition))

    async def append_event(_conn, **kwargs):
        calls.append(("event", kwargs["event_type"]))
        return "evt-a"

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.drain_run_tool_permission_terminalization", drain)
    monkeypatch.setattr("app.worker.reconcile_terminalized_permission_run", reconcile)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)

    outcome = await process_run_payload(
        base_payload(file_ids=[], skill_id="general-chat", agent_id="general-agent"),
        AdapterRegistry({"fake": FailingAdapter()}),
    )

    assert outcome.status == "failed"
    assert calls.count(("tenant-a", "run-a", True)) == 1
    assert not any(item == ("event", "run_failed") or item == ("event", "run_cancelled") for item in calls)


@pytest.mark.asyncio
async def test_worker_audits_read_only_ragflow_tool_call(monkeypatch):
    audits = []
    events = []
    snapshots = []

    class RagflowAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="succeeded",
                adapter_version="ragflow-adapter/1",
                executor_type="ragflow",
                executor_version="ragflow-retrieval-http",
                capabilities={"tools": True, "streaming": False},
                result={"message": "answer"},
                executor_payload={
                    "dataset_ids": ["dataset-a"],
                    "reference_ids": [
                        {"index": 1, "dataset_id": "dataset-a", "document_id": "doc-a", "chunk_id": "chunk-a"}
                    ],
                },
            )

    class Registry:
        def get(self, executor_type):
            return RagflowAdapter()

    async def fake_mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def fake_append_event(conn, **kwargs):
        events.append(kwargs["event_type"])
        return f"evt_{len(events)}"

    async def fake_append_audit_log(conn, **kwargs):
        audits.append(kwargs)
        return f"aud_{len(audits)}"

    async def fake_ensure_mcp_tool_active(conn, *, tenant_id, tool_id):
        return {"id": tool_id, "status": "active", "write_capable": False, "risk_level": "low"}

    async def fake_complete_run(conn, **kwargs):
        return True

    async def fake_upsert_run_skill_snapshot(conn, **kwargs):
        snapshots.append(kwargs)

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", fake_mark_run_running)
    monkeypatch.setattr("app.worker.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.worker.repositories.append_audit_log", fake_append_audit_log)
    monkeypatch.setattr("app.worker.repositories.ensure_mcp_tool_active", fake_ensure_mcp_tool_active, raising=False)
    monkeypatch.setattr("app.worker.repositories.complete_run", fake_complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.repositories.upsert_run_skill_snapshot", fake_upsert_run_skill_snapshot)

    outcome = await process_run_payload(
        base_payload(
            skill_id="ragflow-knowledge-search",
            executor_type="ragflow",
            skill_version="hash-ragflow",
            skill_manifests=[primary_manifest("ragflow-knowledge-search", "hash-ragflow")],
        ),
        registry=Registry(),
        worker_id="worker-ragflow",
    )

    assert outcome.status == "succeeded"
    assert "mcp_tool_call_started" in events
    assert "mcp_tool_call_completed" in events
    completion_audit = next(item for item in audits if item["action"] == "mcp_tool_call_completed")
    assert completion_audit["payload_json"]["dataset_ids"] == ["dataset-a"]
    assert audits[0]["payload_json"]["risk_level"] == "low"
    assert audits[0]["payload_json"]["write_capable"] is False
    assert audits[1]["action"] == "mcp_tool_call_completed"
    assert audits[1]["trace_id"] == "trace_run_a"
    assert audits[1]["payload_json"]["dataset_ids"] == ["dataset-a"]
    assert audits[1]["payload_json"]["reference_ids"] == [
        {"index": 1, "dataset_id": "dataset-a", "document_id": "doc-a", "chunk_id": "chunk-a"}
    ]
    assert snapshots == [
        {
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "skill_id": "ragflow-knowledge-search",
            "skill_version": "hash-ragflow",
            "content_hash": "hash-ragflow",
            "source_json": repository_module.run_skill_snapshot_source_json(
                primary_manifest("ragflow-knowledge-search", "hash-ragflow"),
                release_decision=release_decision("hash-ragflow"),
            ),
            "dependency_ids": [],
            "allowed": True,
            "staged": True,
            "used": True,
            "used_skills_source": "executor_native",
            "inferred_used": False,
        }
    ]


@pytest.mark.asyncio
async def test_worker_does_not_publish_ragflow_completion_for_failed_result(monkeypatch):
    events = []
    audits = []

    class FailedRagflowAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="failed",
                adapter_version="ragflow-adapter/1",
                executor_type="ragflow",
                executor_version="ragflow-retrieval-http",
                capabilities={"tools": True, "streaming": False},
                result={"message": "RAGFlow retrieval failed.", "error_code": "ragflow_api_error"},
                executor_payload={"dataset_ids": ["dataset-a"]},
            )

    class Registry:
        def get(self, executor_type):
            return FailedRagflowAdapter()

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def ensure_mcp_tool_active(conn, *, tenant_id, tool_id):
        return {"id": tool_id, "status": "active", "write_capable": False, "risk_level": "low"}

    async def append_event(conn, **kwargs):
        events.append(kwargs["event_type"])
        return "evt-a"

    async def append_audit_log(conn, **kwargs):
        audits.append(kwargs["action"])
        return "audit-a"

    async def fail_run(conn, **kwargs):
        return True

    async def upsert_run_skill_snapshot(conn, **kwargs):
        return None

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.ensure_mcp_tool_active", ensure_mcp_tool_active, raising=False)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.append_audit_log", append_audit_log)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.upsert_run_skill_snapshot", upsert_run_skill_snapshot)

    outcome = await process_run_payload(
        base_payload(skill_id="ragflow-knowledge-search", executor_type="ragflow"),
        registry=Registry(),
        worker_id="worker-ragflow",
    )

    assert outcome.status == "failed"
    assert "mcp_tool_call_completed" not in events
    assert "mcp_tool_call_completed" not in audits


@pytest.mark.asyncio
async def test_worker_rolls_back_ragflow_completion_when_final_success_guard_loses(monkeypatch):
    committed = []

    class TransactionConnection:
        def __init__(self):
            self.pending = []

    @asynccontextmanager
    async def transactional_connection():
        conn = TransactionConnection()
        try:
            yield conn
        except Exception:
            raise
        else:
            committed.extend(conn.pending)

    class RagflowAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="succeeded",
                adapter_version="ragflow-adapter/1",
                executor_type="ragflow",
                executor_version="ragflow-retrieval-http",
                capabilities={"tools": True, "streaming": False},
                result={"message": "answer"},
                executor_payload={"dataset_ids": ["dataset-a"]},
            )

    class Registry:
        def get(self, executor_type):
            return RagflowAdapter()

    async def mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def ensure_mcp_tool_active(conn, *, tenant_id, tool_id):
        return {"id": tool_id, "status": "active", "write_capable": False, "risk_level": "low"}

    async def append_event(conn, **kwargs):
        conn.pending.append(("event", kwargs["event_type"]))
        return "evt-a"

    async def append_audit_log(conn, **kwargs):
        conn.pending.append(("audit", kwargs["action"]))
        return "audit-a"

    async def append_message(conn, **kwargs):
        conn.pending.append(("message", kwargs["role"]))
        return "msg-a"

    async def complete_run(conn, **kwargs):
        return False

    async def fail_run(conn, **kwargs):
        conn.pending.append(("fail", kwargs["error_code"]))
        return True

    async def classify_success_commit_block(conn, *, tenant_id, run_id):
        return "tool_permission_pending"

    async def upsert_run_skill_snapshot(conn, **kwargs):
        return None

    monkeypatch.setattr("app.worker.transaction", transactional_connection)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.ensure_mcp_tool_active", ensure_mcp_tool_active, raising=False)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.append_audit_log", append_audit_log)
    monkeypatch.setattr("app.worker.repositories.append_message", append_message)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.classify_success_commit_block", classify_success_commit_block, raising=False)
    monkeypatch.setattr("app.worker.repositories.upsert_run_skill_snapshot", upsert_run_skill_snapshot)

    outcome = await process_run_payload(
        base_payload(skill_id="ragflow-knowledge-search", executor_type="ragflow"),
        registry=Registry(),
        worker_id="worker-ragflow",
    )

    assert outcome.status == "failed"
    assert ("fail", "tool_permission_pending") in committed
    assert ("event", "mcp_tool_call_completed") not in committed
    assert ("audit", "mcp_tool_call_completed") not in committed


@pytest.mark.asyncio
async def test_worker_does_not_mark_failed_ragflow_result_as_native_used(monkeypatch):
    snapshots = []
    failures = []

    class FailedRagflowAdapter:
        async def submit_run(self, payload, event_sink=None):
            return ExecutorResult(
                status="failed",
                adapter_version="ragflow-adapter/1",
                executor_type="ragflow",
                executor_version="ragflow-retrieval-http",
                capabilities={"tools": True, "streaming": False},
                result={"message": "RAGFlow retrieval failed.", "error_code": "ragflow_api_error"},
                executor_payload={"dataset_ids": ["dataset-a"]},
            )

    class Registry:
        def get(self, executor_type):
            return FailedRagflowAdapter()

    async def fake_mark_run_running(conn, *, tenant_id, run_id):
        return True

    async def fake_ensure_mcp_tool_active(conn, *, tenant_id, tool_id):
        return {"id": tool_id, "status": "active", "write_capable": False, "risk_level": "low"}

    async def fake_append_event(conn, **kwargs):
        return "evt-a"

    async def fake_append_audit_log(conn, **kwargs):
        return "audit-a"

    async def fake_fail_run(conn, **kwargs):
        failures.append(kwargs)
        return ToolPermissionTerminalizationProgress(completed=True, status="failed", did_transition=True)

    async def fake_upsert_run_skill_snapshot(conn, **kwargs):
        snapshots.append(kwargs)

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", fake_mark_run_running)
    monkeypatch.setattr("app.worker.repositories.ensure_mcp_tool_active", fake_ensure_mcp_tool_active, raising=False)
    monkeypatch.setattr("app.worker.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.worker.repositories.append_audit_log", fake_append_audit_log)
    monkeypatch.setattr("app.worker.repositories.fail_run", fake_fail_run)
    monkeypatch.setattr("app.worker.repositories.upsert_run_skill_snapshot", fake_upsert_run_skill_snapshot)

    outcome = await process_run_payload(
        base_payload(
            skill_id="ragflow-knowledge-search",
            executor_type="ragflow",
            skill_version="hash-ragflow",
            skill_manifests=[primary_manifest("ragflow-knowledge-search", "hash-ragflow")],
        ),
        registry=Registry(),
        worker_id="worker-ragflow",
    )

    assert outcome.status == "failed"
    assert failures[0]["error_code"] == "ragflow_api_error"
    assert snapshots == [
        {
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "skill_id": "ragflow-knowledge-search",
            "skill_version": "hash-ragflow",
            "content_hash": "hash-ragflow",
            "source_json": repository_module.run_skill_snapshot_source_json(
                primary_manifest("ragflow-knowledge-search", "hash-ragflow"),
                release_decision=release_decision("hash-ragflow"),
            ),
            "dependency_ids": [],
            "allowed": True,
            "staged": True,
            "used": False,
            "used_skills_source": "",
            "inferred_used": False,
        }
    ]


@pytest.mark.asyncio
async def test_worker_blocks_disabled_mcp_tool_before_dispatch(monkeypatch):
    calls = []

    class RagflowAdapterMustNotRun:
        async def submit_run(self, payload, event_sink=None):
            calls.append(("adapter", payload.run_id))
            raise AssertionError("disabled MCP tool must not reach adapter dispatch")

    class Registry:
        def get(self, executor_type):
            return RagflowAdapterMustNotRun()

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("running", tenant_id, run_id))
        return True

    async def ensure_mcp_tool_active(conn, *, tenant_id, tool_id):
        calls.append(("policy", tenant_id, tool_id))
        raise RepositoryConflictError("mcp_tool_disabled")

    async def fail_run(conn, *, tenant_id, run_id, error_code, error_message, result_json=None):
        calls.append(("fail", error_code, error_message))
        return ToolPermissionTerminalizationProgress(completed=True, status="failed", did_transition=True)

    async def append_event(conn, *, tenant_id, run_id, event_type, stage, message, payload=None):
        calls.append(("event", event_type, stage, payload or {}))

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.ensure_mcp_tool_active", ensure_mcp_tool_active, raising=False)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)

    outcome = await process_run_payload(
        base_payload(skill_id="ragflow-knowledge-search", executor_type="ragflow"),
        registry=Registry(),
        worker_id="worker-ragflow",
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "capability_not_authorized"
    assert ("policy", "tenant-a", "ragflow-knowledge-search") in calls
    assert not any(item[0] == "adapter" for item in calls)
    assert any(item[0] == "fail" and item[1] == "capability_not_authorized" for item in calls)
    denied_event = next(item for item in calls if item[0] == "event" and item[1] == "capability_not_authorized")
    assert denied_event[2] == "authorization"
    assert denied_event[3]["capability_id"] == "ragflow-knowledge-search"
    assert denied_event[3]["reason"] == "lifecycle_denied"
    assert denied_event[3]["visible_to_user"] is True


def _task6_distribution(
    capability_kind,
    capability_id,
    *,
    status="active",
    visible_to_user=True,
    department_ids=None,
    allowed_roles=None,
):
    return {
        "tenant_id": "tenant-a",
        "capability_kind": capability_kind,
        "capability_id": capability_id,
        "status": status,
        "visible_to_user": visible_to_user,
        "scope_mode": "allowlist",
        "department_ids": list(department_ids or []),
        "allowed_roles": list(allowed_roles or []),
    }


def _task6_tool(tool_id, server_id, *, server_status="active", write_capable=False, risk_level="low"):
    return {
        "tool_id": tool_id,
        "server_id": server_id,
        "allowed_tools": ["query"],
        "effective_status": "active",
        "registry_status": "active",
        "policy_status": "active",
        "server_status": server_status,
        "transport_type": "streamable_http",
        "endpoint": "https://mcp.example.test/v1",
        "auth_mode": "none",
        "visible_to_user": True,
        "write_capable": write_capable,
        "risk_level": risk_level,
    }


def _install_task6_worker_fakes(
    monkeypatch,
    *,
    locked_input=None,
    queue_input=None,
    principal_roles=None,
    principal_department_id="qa",
    auth_source="session-token",
    current_principal: AuthPrincipal | None = None,
):
    calls = []
    skill_id = "qa-file-reviewer"
    state = {
        "skill": {
            "agent_id": "qa-word-review",
            "agent_status": "active",
            "skill_id": skill_id,
            "skill_status": "active",
            "executor_type": "capture",
        },
        "skill_error": None,
        "distribution_errors": {},
        "distributions": {
            ("skill", skill_id): _task6_distribution(
                "skill",
                skill_id,
                department_ids=["qa"],
                allowed_roles=["qa_operator"],
            )
        },
        "tools": {},
    }
    persisted_input = dict(locked_input or {"mode": "file"})
    locked_run = {
        "id": "run-a",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "agent_id": "qa-word-review",
        "skill_id": skill_id,
        "trace_id": "trace-run-a",
        "principal_roles": list(principal_roles or ["qa_operator"]),
        "principal_department_id": principal_department_id,
        "auth_source": auth_source,
        "input_json": {
            "input": persisted_input,
            "file_ids": ["file-a"],
            "executor_type": "capture",
            "skill_version": "hash-qa-file-reviewer",
            "release_decision": release_decision("hash-qa-file-reviewer"),
            "skill_manifests": [primary_manifest("qa-file-reviewer", "hash-qa-file-reviewer")],
            "context_snapshot_id": "ctx-existing",
            "context_snapshot": {
                "schema_version": "ai-platform.context-snapshot.v1",
                "context_snapshot_id": "ctx-existing",
                "source": "test",
                "message_count": 0,
                "file_count": 1,
                "memory_record_count": 0,
            },
        },
    }
    state["locked_run"] = locked_run
    resolved_current_principal = current_principal or _test_current_principal(
        user_id="user-a",
        tenant_id="tenant-a",
        department_id="qa",
        roles=["qa_operator"],
    )

    class CaptureAdapter:
        async def submit_run(self, payload, event_sink=None):
            calls.append(("adapter", payload.input))
            return ExecutorResult(
                status="succeeded",
                adapter_version="capture/1",
                executor_type="capture",
                executor_version="capture",
                capabilities={"artifacts": True, "streaming": False, "tools": True},
                result={"message": "done"},
                artifacts=[
                    ArtifactManifest(
                        artifact_type="reviewed_docx",
                        label="Reviewed Word",
                        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        storage_key="tenants/tenant-a/runs/run-a/artifacts/reviewed.docx",
                        size_bytes=1024,
                    )
                ],
            )

    class CaptureRegistry:
        def get(self, executor_type):
            calls.append(("registry_get", executor_type))
            return CaptureAdapter()

    async def mark_run_running(conn, *, tenant_id, run_id):
        calls.append(("lock", tenant_id, run_id))
        return locked_run

    async def resolve_task6_current_principal(*, user_id, tenant_id):
        assert (user_id, tenant_id) == ("user-a", "tenant-a")
        return resolved_current_principal

    async def resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        calls.append(("skill_lookup", tenant_id, agent_id, skill_id))
        if state["skill_error"] is not None:
            raise state["skill_error"]
        return dict(state["skill"])

    async def get_capability_distribution_row(conn, *, tenant_id, capability_kind, capability_id):
        calls.append(("distribution", tenant_id, capability_kind, capability_id))
        error = state["distribution_errors"].get((capability_kind, capability_id))
        if error is not None:
            raise error
        return state["distributions"].get((capability_kind, capability_id))

    async def get_mcp_tool_registry_entry(conn, *, tenant_id, tool_id):
        calls.append(("tool_lookup", tenant_id, tool_id))
        entry = state["tools"].get(tool_id)
        return dict(entry) if entry is not None else None

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "event-task6"

    async def append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-task6"

    async def fail_run(conn, **kwargs):
        calls.append(("fail", kwargs))
        return ToolPermissionTerminalizationProgress(completed=True, status="failed", did_transition=True)

    async def complete_run(conn, **kwargs):
        calls.append(("complete", kwargs))
        return True

    async def upsert_run_skill_snapshot(conn, **kwargs):
        calls.append(("skill_snapshot", kwargs))

    async def create_artifact(conn, **kwargs):
        calls.append(("artifact", kwargs))

    async def create_sandbox_lease(conn, **kwargs):
        calls.append(("sandbox_create", kwargs))
        return {"id": "lease-task6", **kwargs}

    async def release_sandbox_lease(conn, **kwargs):
        calls.append(("sandbox_release", kwargs))
        return {"id": kwargs["lease_id"], "status": "released", **kwargs}

    monkeypatch.setattr("app.worker.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.worker.resolve_current_principal",
        resolve_task6_current_principal,
        raising=False,
    )
    monkeypatch.setattr("app.worker.repositories.mark_run_running", mark_run_running)
    monkeypatch.setattr("app.worker.repositories.resolve_agent_skill", resolve_agent_skill, raising=False)
    monkeypatch.setattr("app.worker.repositories.resolve_selected_skill", resolve_agent_skill, raising=False)
    monkeypatch.setattr(
        "app.worker.repositories.get_capability_distribution_row",
        get_capability_distribution_row,
        raising=False,
    )
    monkeypatch.setattr(
        "app.worker.repositories.get_mcp_tool_registry_entry",
        get_mcp_tool_registry_entry,
        raising=False,
    )
    monkeypatch.setattr("app.worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.worker.repositories.append_audit_log", append_audit_log)
    monkeypatch.setattr("app.worker.repositories.fail_run", fail_run)
    monkeypatch.setattr("app.worker.repositories.complete_run", complete_run)
    monkeypatch.setattr("app.worker.repositories.append_message", fake_append_message)
    monkeypatch.setattr("app.worker.repositories.upsert_run_skill_snapshot", upsert_run_skill_snapshot)
    monkeypatch.setattr("app.worker.repositories.create_artifact", create_artifact)
    monkeypatch.setattr("app.worker.repositories.create_sandbox_lease", create_sandbox_lease)
    monkeypatch.setattr("app.worker.repositories.release_sandbox_lease", release_sandbox_lease)

    raw = base_payload(
        input=dict(queue_input or {"mode": "queue"}),
        executor_type="capture",
    )
    return raw, CaptureRegistry(), state, calls


def _task6_assert_no_executor_calls(calls):
    assert not any(call[0] in {"registry_get", "sandbox_create", "adapter"} for call in calls)


@pytest.mark.asyncio
async def test_worker_current_principal_denial_cannot_be_restored_by_queued_snapshot(monkeypatch):
    raw, registry, _, calls = _install_task6_worker_fakes(
        monkeypatch,
        locked_input={
            "mode": "file",
            "principal_roles": ["admin"],
            "principal_department_id": "rd",
            "auth_source": "caller-input",
        },
        queue_input={
            "principal_roles": ["admin"],
            "principal_department_id": "finance",
            "auth_source": "queue-input",
        },
        principal_roles=["admin"],
        principal_department_id="finance",
        auth_source="locked-session-token",
        current_principal=_test_current_principal(
            user_id="user-a",
            tenant_id="tenant-a",
            department_id="rd",
            roles=["user"],
        ),
    )

    outcome = await process_run_payload(raw, registry=registry)

    assert outcome.status == "failed"
    assert outcome.error_code == "capability_not_authorized"
    _task6_assert_no_executor_calls(calls)
    denied_audit = next(
        call[1]
        for call in calls
        if call[0] == "audit" and call[1]["action"] == "capability_distribution.denied"
    )
    assert denied_audit["payload_json"]["actor_department_id"] == "rd"
    assert denied_audit["payload_json"]["actor_roles"] == ["user"]
    assert denied_audit["payload_json"]["decision_reason"] == "department_not_allowed"


@pytest.mark.asyncio
async def test_worker_capability_distribution_allows_current_skill_after_enqueue(monkeypatch):
    raw, registry, _, calls = _install_task6_worker_fakes(
        monkeypatch,
        principal_roles=["user"],
        principal_department_id="rd",
        current_principal=_test_current_principal(
            user_id="user-a",
            tenant_id="tenant-a",
            department_id="qa",
            roles=["qa_operator"],
        ),
    )

    outcome = await process_run_payload(raw, registry=registry)

    assert outcome.status == "succeeded"
    assert ("skill_lookup", "tenant-a", "qa-word-review", "qa-file-reviewer") in calls
    assert ("distribution", "tenant-a", "skill", "qa-file-reviewer") in calls
    assert any(call[0] == "adapter" for call in calls)


@pytest.mark.asyncio
async def test_worker_immutable_skill_snapshot_mismatch_blocks_before_stage_or_adapter(monkeypatch):
    raw, registry, _, calls = _install_task6_worker_fakes(monkeypatch)

    async def mismatch(*args, **kwargs):
        raise RepositoryConflictError("run_skill_snapshot_identity_mismatch")

    monkeypatch.setattr(
        "app.worker.repositories.validate_run_skill_snapshots_for_dispatch",
        mismatch,
        raising=False,
    )

    outcome = await process_run_payload(raw, registry=registry)

    assert outcome.status == "failed"
    assert outcome.error_code == "capability_not_authorized"
    _task6_assert_no_executor_calls(calls)
    assert not any(call[0] == "skill_lookup" for call in calls)
    denied_event = next(
        call[1]
        for call in calls
        if call[0] == "event" and call[1]["event_type"] == "capability_not_authorized"
    )
    assert denied_event["payload"]["reason"] == "skill_snapshot_identity_mismatch"


@pytest.mark.parametrize(
    "projection",
    [
        None,
        ["Bash", "Write", "Agent"],
        ["Bash"],
    ],
)
@pytest.mark.asyncio
async def test_worker_rejects_unlocked_builtin_identity_queue_projection_before_adapter(monkeypatch, projection):
    raw, registry, state, calls = _install_task6_worker_fakes(monkeypatch)
    locked_manifest = state["locked_run"]["input_json"]["skill_manifests"][0]
    expected_source = repository_module.run_skill_snapshot_source_json(
        locked_manifest,
        release_decision=state["locked_run"]["input_json"]["release_decision"],
    )
    if projection is None:
        locked_manifest.pop("builtin_tool_identities")
    else:
        locked_manifest["builtin_tool_identities"] = projection

    async def validate_snapshot(_conn, *, skill_manifests, release_decision, **_kwargs):
        actual_source = repository_module.run_skill_snapshot_source_json(
            skill_manifests[0],
            release_decision=release_decision,
        )
        if actual_source != expected_source:
            raise RepositoryConflictError("run_skill_snapshot_identity_mismatch")

    monkeypatch.setattr(
        "app.worker.repositories.validate_run_skill_snapshots_for_dispatch",
        validate_snapshot,
    )

    outcome = await process_run_payload(raw, registry=registry)

    assert outcome.status == "failed"
    assert outcome.error_code == "capability_not_authorized"
    _task6_assert_no_executor_calls(calls)


@pytest.mark.asyncio
async def test_worker_revoked_historical_pin_blocks_before_stage_or_adapter(monkeypatch):
    raw, registry, _, calls = _install_task6_worker_fakes(monkeypatch)

    async def revoked(*args, **kwargs):
        raise repository_module.RepositoryAuthorizationError("capability_not_authorized")

    monkeypatch.setattr(
        "app.worker.repositories.validate_replay_skill_manifests",
        revoked,
        raising=False,
    )

    outcome = await process_run_payload(raw, registry=registry)

    assert outcome.status == "failed"
    assert outcome.error_code == "capability_not_authorized"
    _task6_assert_no_executor_calls(calls)
    assert not any(call[0] == "skill_lookup" for call in calls)
    denied_event = next(
        call[1]
        for call in calls
        if call[0] == "event" and call[1]["event_type"] == "capability_not_authorized"
    )
    assert denied_event["payload"]["reason"] == "skill_historical_pin_revoked"


@pytest.mark.parametrize(
    ("change", "expected_reason"),
    [
        ("hidden", "distribution_hidden"),
        ("disabled", "distribution_disabled"),
        ("department", "department_not_allowed"),
        ("role", "role_not_allowed"),
        ("distribution_missing", "distribution_missing"),
        ("lifecycle_disabled", "lifecycle_denied"),
        ("registry_missing", "lifecycle_denied"),
    ],
)
@pytest.mark.asyncio
async def test_worker_capability_distribution_rechecks_skill_changes_after_enqueue(
    monkeypatch,
    change,
    expected_reason,
):
    raw, registry, state, calls = _install_task6_worker_fakes(
        monkeypatch,
        current_principal=_test_current_principal(
            user_id="user-a",
            tenant_id="tenant-a",
            department_id="qa",
            roles=["qa_operator"],
        ),
    )
    distribution = state["distributions"][("skill", "qa-file-reviewer")]
    if change == "hidden":
        distribution["visible_to_user"] = False
    elif change == "disabled":
        distribution["status"] = "disabled"
    elif change == "department":
        distribution["department_ids"] = ["rd"]
    elif change == "role":
        distribution["allowed_roles"] = ["reviewer"]
    elif change == "distribution_missing":
        state["distributions"].pop(("skill", "qa-file-reviewer"))
    elif change == "lifecycle_disabled":
        state["skill_error"] = RepositoryConflictError("skill_inactive")
    elif change == "registry_missing":
        state["skill_error"] = RepositoryNotFoundError("agent_or_skill_not_found")

    outcome = await process_run_payload(raw, registry=registry)

    assert outcome.status == "failed"
    assert outcome.error_code == "capability_not_authorized"
    _task6_assert_no_executor_calls(calls)
    failed = next(call[1] for call in calls if call[0] == "fail")
    assert failed["error_code"] == "capability_not_authorized"
    denied_event = next(
        call[1]
        for call in calls
        if call[0] == "event" and call[1]["event_type"] == "capability_not_authorized"
    )
    assert denied_event["stage"] == "authorization"
    assert denied_event["payload"]["capability_kind"] == "skill"
    assert denied_event["payload"]["capability_id"] == "qa-file-reviewer"
    assert denied_event["payload"]["policy"] == "capability_distribution"
    assert denied_event["payload"]["reason"] == expected_reason


@pytest.mark.asyncio
async def test_worker_registered_tools_use_only_current_allowed_mcp_entries(monkeypatch):
    locked_input = {
        "mode": "file",
        "mcp_tool_ids": ["tool-global"],
        "multi_agent_steps": [
            {
                "step_key": "review",
                "role": "review",
                "mcp_tool_ids": ["tool-step"],
            }
        ],
    }
    raw, registry, state, calls = _install_task6_worker_fakes(monkeypatch, locked_input=locked_input)
    state["tools"].update(
        {
            "tool-global": _task6_tool("tool-global", "server-global"),
            "tool-step": _task6_tool("tool-step", "server-step"),
        }
    )
    state["distributions"].update(
        {
            ("mcp_server", "server-global"): _task6_distribution("mcp_server", "server-global"),
            ("mcp_server", "server-step"): _task6_distribution("mcp_server", "server-step"),
        }
    )

    outcome = await process_run_payload(raw, registry=registry)

    assert outcome.status == "succeeded"
    assert ("tool_lookup", "tenant-a", "tool-global") in calls
    assert ("tool_lookup", "tenant-a", "tool-step") in calls
    registered_input = next(call[1] for call in calls if call[0] == "adapter")
    assert registered_input["mcp_tool_ids"] == ["tool-global"]
    assert registered_input["multi_agent_steps"][0]["mcp_tool_ids"] == ["tool-step"]
    assert "mcpToolIds" not in registered_input


@pytest.mark.asyncio
async def test_worker_reauthorizes_historical_ragflow_mcp_after_current_skill_changes_executor(monkeypatch):
    raw, registry, state, calls = _install_task6_worker_fakes(monkeypatch, locked_input={"mode": "file"})
    historical_manifest = primary_manifest("qa-file-reviewer", "hash-qa-file-reviewer")
    historical_manifest["mcp_tool_ids"] = ["historical-search"]
    state["locked_run"]["input_json"]["executor_type"] = "ragflow"
    state["locked_run"]["input_json"]["skill_manifests"] = [historical_manifest]
    state["skill"].update(executor_type="capture", backing_mcp_tool_id=None)
    state["tools"]["historical-search"] = _task6_tool(
        "historical-search",
        "historical-server",
    )
    state["distributions"][("mcp_server", "historical-server")] = _task6_distribution(
        "mcp_server",
        "historical-server",
        status="disabled",
    )

    outcome = await process_run_payload(raw, registry=registry)

    assert outcome.status == "failed"
    assert outcome.error_code == "capability_not_authorized"
    assert ("tool_lookup", "tenant-a", "historical-search") in calls
    _task6_assert_no_executor_calls(calls)
    denied_event = next(
        call[1]
        for call in calls
        if call[0] == "event" and call[1]["event_type"] == "capability_not_authorized"
    )
    assert denied_event["payload"]["reason"] == "distribution_disabled"


@pytest.mark.parametrize(
    ("change", "expected_reason"),
    [
        ("hidden", "distribution_hidden"),
        ("disabled", "distribution_disabled"),
        ("department", "department_not_allowed"),
        ("role", "role_not_allowed"),
        ("distribution_missing", "distribution_missing"),
        ("parent_disabled", "lifecycle_denied"),
        ("tool_missing", "distribution_missing"),
    ],
)
@pytest.mark.asyncio
async def test_worker_capability_distribution_rechecks_mcp_parent_changes_after_enqueue(
    monkeypatch,
    change,
    expected_reason,
):
    raw, registry, state, calls = _install_task6_worker_fakes(
        monkeypatch,
        locked_input={"mode": "file", "mcp_tool_ids": ["tool-a"]},
        current_principal=_test_current_principal(
            user_id="user-a",
            tenant_id="tenant-a",
            department_id="qa",
            roles=["qa_operator"],
        ),
    )
    state["tools"]["tool-a"] = _task6_tool("tool-a", "server-a")
    state["distributions"][("mcp_server", "server-a")] = _task6_distribution(
        "mcp_server",
        "server-a",
        department_ids=["qa"],
        allowed_roles=["qa_operator"],
    )
    distribution = state["distributions"][("mcp_server", "server-a")]
    if change == "hidden":
        distribution["visible_to_user"] = False
    elif change == "disabled":
        distribution["status"] = "disabled"
    elif change == "department":
        distribution["department_ids"] = ["rd"]
    elif change == "role":
        distribution["allowed_roles"] = ["reviewer"]
    elif change == "distribution_missing":
        state["distributions"].pop(("mcp_server", "server-a"))
    elif change == "parent_disabled":
        state["tools"]["tool-a"]["server_status"] = "disabled"
    elif change == "tool_missing":
        state["tools"].pop("tool-a")

    outcome = await process_run_payload(raw, registry=registry)

    assert outcome.status == "failed"
    assert outcome.error_code == "capability_not_authorized"
    _task6_assert_no_executor_calls(calls)
    denied_event = next(
        call[1]
        for call in calls
        if call[0] == "event" and call[1]["event_type"] == "capability_not_authorized"
    )
    assert denied_event["payload"]["capability_kind"] == "mcp_tool"
    assert denied_event["payload"]["capability_id"] == "tool-a"
    assert denied_event["payload"]["reason"] == expected_reason


@pytest.mark.asyncio
async def test_worker_registered_tools_never_receive_partially_authorized_mcp_set(monkeypatch):
    raw, registry, state, calls = _install_task6_worker_fakes(
        monkeypatch,
        locked_input={"mode": "file", "mcp_tool_ids": ["tool-allowed", "tool-denied"]},
    )
    state["tools"].update(
        {
            "tool-allowed": _task6_tool("tool-allowed", "server-allowed"),
            "tool-denied": _task6_tool("tool-denied", "server-denied"),
        }
    )
    state["distributions"].update(
        {
            ("mcp_server", "server-allowed"): _task6_distribution("mcp_server", "server-allowed"),
            ("mcp_server", "server-denied"): _task6_distribution(
                "mcp_server",
                "server-denied",
                visible_to_user=False,
            ),
        }
    )

    outcome = await process_run_payload(raw, registry=registry)

    assert outcome.error_code == "capability_not_authorized"
    _task6_assert_no_executor_calls(calls)


@pytest.mark.asyncio
async def test_worker_reauthorization_denies_archived_skill_before_admin_bypass(monkeypatch):
    raw, registry, state, calls = _install_task6_worker_fakes(
        monkeypatch,
        principal_roles=["user"],
        principal_department_id="rd",
        current_principal=_test_current_principal(
            user_id="user-a",
            tenant_id="tenant-a",
            department_id="platform",
            roles=["admin"],
        ),
    )
    state["distributions"][("skill", "qa-file-reviewer")]["metadata_json"] = {
        "archived_at": "2026-07-15T00:00:00.000Z",
        "archived_by": "admin-a",
    }

    outcome = await process_run_payload(raw, registry=registry)

    assert outcome.status == "failed"
    assert outcome.error_code == "capability_not_authorized"
    _task6_assert_no_executor_calls(calls)
    denied_event = next(
        call[1]
        for call in calls
        if call[0] == "event" and call[1]["event_type"] == "capability_not_authorized"
    )
    assert denied_event["payload"]["reason"] == "distribution_archived"
    assert not any(
        call[0] == "audit" and call[1]["action"] == "capability_distribution.admin_bypass"
        for call in calls
    )


@pytest.mark.asyncio
async def test_worker_capability_distribution_admin_bypass_is_auditable(monkeypatch):
    raw, registry, state, calls = _install_task6_worker_fakes(
        monkeypatch,
        locked_input={"mode": "file", "mcp_tool_ids": ["tool-admin"]},
        principal_roles=["user"],
        principal_department_id="rd",
        current_principal=_test_current_principal(
            user_id="user-a",
            tenant_id="tenant-a",
            department_id="platform",
            roles=["admin"],
        ),
    )
    state["distributions"][("skill", "qa-file-reviewer")]["visible_to_user"] = False
    state["tools"]["tool-admin"] = _task6_tool("tool-admin", "server-admin")
    state["distributions"][("mcp_server", "server-admin")] = _task6_distribution(
        "mcp_server",
        "server-admin",
        visible_to_user=False,
    )

    outcome = await process_run_payload(raw, registry=registry)

    assert outcome.status == "succeeded"
    bypass_audits = [
        call[1]
        for call in calls
        if call[0] == "audit" and call[1]["action"] == "capability_distribution.admin_bypass"
    ]
    assert len(bypass_audits) == 2
    assert {(audit["target_type"], audit["target_id"]) for audit in bypass_audits} == {
        ("skill", "qa-file-reviewer"),
        ("mcp_tool", "tool-admin"),
    }
    for audit in bypass_audits:
        assert audit["payload_json"]["admin_bypass"] is True
        assert audit["payload_json"]["decision_reason"] == "admin_bypass"
        assert audit["payload_json"]["run_id"] == "run-a"
        assert audit["payload_json"]["session_id"] == "session-a"
        assert audit["payload_json"]["agent_id"] == "qa-word-review"


@pytest.mark.asyncio
async def test_worker_capability_distribution_denial_event_and_dotted_audit_are_sanitized(monkeypatch):
    raw, registry, state, calls = _install_task6_worker_fakes(
        monkeypatch,
        locked_input={"mode": "file", "private_payload": "private-marker"},
    )
    distribution = state["distributions"][("skill", "qa-file-reviewer")]
    distribution["visible_to_user"] = False
    distribution["metadata_json"] = {"credential": "private-marker"}
    state["skill"]["private_payload"] = "private-marker"

    outcome = await process_run_payload(raw, registry=registry)

    assert outcome.error_code == "capability_not_authorized"
    denied_event = next(
        call[1]
        for call in calls
        if call[0] == "event" and call[1]["event_type"] == "capability_not_authorized"
    )
    denied_audit = next(
        call[1]
        for call in calls
        if call[0] == "audit" and call[1]["action"] == "capability_distribution.denied"
    )
    assert set(denied_event["payload"]) == {
        "capability_kind",
        "capability_id",
        "policy",
        "reason",
        "visible_to_user",
        "severity",
    }
    assert set(denied_audit["payload_json"]) == {
        "capability_kind",
        "capability_id",
        "actor_department_id",
        "actor_roles",
        "department_scope_ids",
        "role_scope_ids",
        "scope_mode",
        "decision_reason",
        "admin_bypass",
        "run_id",
        "session_id",
        "agent_id",
        "skill_id",
    }
    assert denied_audit["payload_json"]["actor_roles"] == ["qa_operator"]
    assert "private-marker" not in json.dumps(
        {"event": denied_event, "audit": denied_audit},
        ensure_ascii=False,
        sort_keys=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_scope", ["skill", "mcp_server"])
async def test_worker_malformed_distribution_scope_becomes_terminal_audited_denial(
    monkeypatch,
    invalid_scope,
):
    locked_input = {"mode": "file"}
    if invalid_scope == "mcp_server":
        locked_input["mcp_tool_ids"] = ["tool-malformed"]
    raw, registry, state, calls = _install_task6_worker_fakes(
        monkeypatch,
        locked_input=locked_input,
    )
    if invalid_scope == "skill":
        key = ("skill", "qa-file-reviewer")
        expected_target = ("skill", "qa-file-reviewer")
    else:
        state["tools"]["tool-malformed"] = _task6_tool("tool-malformed", "server-malformed")
        key = ("mcp_server", "server-malformed")
        expected_target = ("mcp_tool", "tool-malformed")
    state["distribution_errors"][key] = RepositoryConflictError(
        "capability_distribution_scope_invalid"
    )

    outcome = await process_run_payload(raw, registry=registry)

    assert outcome.status == "failed"
    assert outcome.error_code == "capability_not_authorized"
    denied_audit = next(
        call[1]
        for call in calls
        if call[0] == "audit" and call[1]["action"] == "capability_distribution.denied"
    )
    assert (denied_audit["target_type"], denied_audit["target_id"]) == expected_target
    assert denied_audit["payload_json"]["decision_reason"] == "distribution_scope_invalid"
    assert any(call[0] == "fail" for call in calls)
    _task6_assert_no_executor_calls(calls)


@pytest.mark.asyncio
async def test_worker_capability_distribution_audits_synchronous_mcp_risk_write_policy(monkeypatch):
    raw, registry, state, calls = _install_task6_worker_fakes(
        monkeypatch,
        locked_input={"mode": "file", "mcp_tool_ids": ["tool-write"]},
    )
    state["tools"]["tool-write"] = _task6_tool(
        "tool-write",
        "server-write",
        write_capable=True,
        risk_level="high",
    )
    state["distributions"][("mcp_server", "server-write")] = _task6_distribution(
        "mcp_server",
        "server-write",
    )

    outcome = await process_run_payload(raw, registry=registry)

    assert outcome.status == "succeeded"
    policy_audit = next(call[1] for call in calls if call[0] == "audit")
    assert policy_audit["action"] == "mcp_tool_policy_allowed"
    assert policy_audit["target_id"] == "tool-write"
    assert policy_audit["payload_json"]["risk_level"] == "high"
    assert policy_audit["payload_json"]["write_capable"] is True


@pytest.mark.parametrize(
    "snapshot_change",
    [
        "missing_input_json",
        "non_mapping_input_json",
        "missing_input",
        "non_mapping_input",
        "invalid_complete_payload",
    ],
)
@pytest.mark.asyncio
async def test_worker_locked_snapshot_invalid_never_falls_back_to_queue_mcp_input(
    monkeypatch,
    snapshot_change,
):
    raw, registry, state, calls = _install_task6_worker_fakes(
        monkeypatch,
        queue_input={
            "mode": "queue",
            "mcp_tool_ids": ["queue-only-tool"],
            "private_payload": "queue-private-marker",
        },
    )
    state["tools"]["queue-only-tool"] = _task6_tool("queue-only-tool", "queue-server")
    state["distributions"][("mcp_server", "queue-server")] = _task6_distribution(
        "mcp_server",
        "queue-server",
    )
    locked_run = state["locked_run"]
    if snapshot_change == "missing_input_json":
        locked_run.pop("input_json")
    elif snapshot_change == "non_mapping_input_json":
        locked_run["input_json"] = "invalid"
    elif snapshot_change == "missing_input":
        locked_run["input_json"].pop("input")
    elif snapshot_change == "non_mapping_input":
        locked_run["input_json"]["input"] = ["invalid"]
    elif snapshot_change == "invalid_complete_payload":
        locked_run["input_json"]["skill_manifests"] = []

    outcome = await process_run_payload(raw, registry=registry)

    assert outcome.status == "failed"
    assert outcome.error_code == "capability_not_authorized"
    assert not any(
        call[0]
        in {
            "skill_lookup",
            "distribution",
            "tool_lookup",
            "registry_get",
            "sandbox_create",
            "adapter",
        }
        for call in calls
    )
    denied_event = next(
        call[1]
        for call in calls
        if call[0] == "event" and call[1]["event_type"] == "capability_not_authorized"
    )
    denied_audit = next(
        call[1]
        for call in calls
        if call[0] == "audit" and call[1]["action"] == "capability_distribution.denied"
    )
    assert denied_event["stage"] == "authorization"
    assert denied_event["payload"]["policy"] == "locked_run_snapshot"
    assert denied_event["payload"]["reason"] == "locked_snapshot_invalid"
    evidence = json.dumps(
        {"event": denied_event, "audit": denied_audit},
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "queue-only-tool" not in evidence
    assert "queue-private-marker" not in evidence


@pytest.mark.asyncio
async def test_worker_invalid_locked_child_snapshot_reconciles_parent_after_commit(monkeypatch):
    dispatch_input = {
        "mode": "file",
        "multi_agent_dispatch": {
            "parent_run_id": "run-parent",
            "parent_step_id": "step-code",
            "dispatch_id": "dispatch-code",
            "step_key": "code",
        },
    }
    raw, registry, state, calls = _install_task6_worker_fakes(
        monkeypatch,
        locked_input=dispatch_input,
        queue_input={"mode": "queue-without-dispatch"},
    )
    state["locked_run"]["input_json"]["skill_manifests"] = []

    async def reconcile(*, tenant_id, run_id, progress, transaction_factory):
        calls.append(("reconcile", {"tenant_id": tenant_id, "run_id": run_id, "progress": progress}))
        return {"parent_run_id": "run-parent"}

    monkeypatch.setattr("app.worker.reconcile_terminalized_permission_run", reconcile)

    outcome = await process_run_payload(raw, registry=registry)

    assert outcome.status == "failed"
    assert outcome.error_code == "capability_not_authorized"
    reconcile_call = next(call[1] for call in calls if call[0] == "reconcile")
    assert reconcile_call["run_id"] == "run-a"
    assert reconcile_call["progress"].status == "failed"
    assert reconcile_call["progress"].did_transition is True
    assert next(index for index, call in enumerate(calls) if call[0] == "fail") < next(
        index for index, call in enumerate(calls) if call[0] == "reconcile"
    )
