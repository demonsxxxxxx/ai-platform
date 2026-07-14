import base64
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

import app.skills.dependencies as dependency_policy
from app.executors.base import ArtifactManifest, ExecutorResult, RunPayload
from app.executors.claude_agent_worker import ClaudeAgentWorkerAdapter, PreparedSdkRun
from app.executors.claude_agent_worker import _allowed_skill_names
from app.executors.claude_agent_worker import _inferred_used_skill_names
from app.executors.claude_agent_worker import _ordinary_run_requires_sandbox
from app.storage import StoredObject
from app.executors.claude_agent_sdk_runner import build_sdk_env, build_skill_prompt, run_claude_agent_sdk
from app.executors.registry import AdapterRegistry
from app.runtime.sandbox.container_provider import (
    DockerContainerProvider,
    FakeContainerProvider,
    OpenSandboxContainerProvider,
)
from app.runtime.kernel_contracts import AgentEvent
from app.skills.pinning import build_skill_manifest_pins
from app.skills.registry import BuiltinSkillRegistry
from app.worker import WorkerRunCancelled


class FakeDelegate:
    async def submit_run(self, payload, event_sink=None):
        return ExecutorResult(
            status="succeeded",
            adapter_version="runtime211-adapter/2",
            executor_type="runtime211",
            executor_version="runtime211-http",
            capabilities={"artifacts": True, "streaming": False, "tools": False},
            result={"message": "done"},
            artifacts=[
                ArtifactManifest(
                    artifact_type="translated_docx",
                    label="翻译 Word",
                    content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    storage_key="tenants/default/runs/run_1/out.docx",
                    size_bytes=10,
                )
            ],
            executor_payload={"runtime_task_id": "task-1"},
        )


class FakeQueryResult:
    used_sdk = True
    message = "hello from sdk"
    session_id = "sdk-session"
    usage = {"input_tokens": 1}
    error = None


class FakeSdkUnavailable:
    used_sdk = False
    message = ""
    session_id = None
    usage = {}
    error = "claude_agent_sdk_unavailable: No module named claude_agent_sdk"


class FakeSdkRuntimeError:
    used_sdk = True
    message = ""
    session_id = None
    usage = {}
    error = "model gateway timeout"


class FakeSdkStopSequence:
    used_sdk = True
    message = "completed at the requested stop sequence"
    session_id = "sdk-session"
    usage = {"input_tokens": 1}
    error = "stop_sequence"


class FakeSdkRuntimeErrorWithSkillUse:
    used_sdk = True
    message = ""
    session_id = "sdk-session"
    usage = {"input_tokens": 1}
    error = "model gateway timeout"
    used_skills = ["qa-file-reviewer"]
    used_skills_source = "executor_hook"


class FakeSdkMaxTurnsWithSkillUse:
    used_sdk = True
    message = ""
    session_id = "sdk-session"
    usage = {"input_tokens": 1}
    error = "Reached maximum number of turns (128)"
    used_skills = ["qa-file-reviewer"]
    used_skills_source = "executor_hook"


class FakeSdkNativeSkillUse:
    used_sdk = True
    message = "reviewed with native skill telemetry"
    session_id = "sdk-session"
    usage = {"input_tokens": 1}
    error = None
    used_skills = ["qa-file-reviewer"]
    used_skills_source = "executor_hook"


RELEASE_DECISION_SCHEMA_VERSION = "ai-platform.skill-release-decision.v1"


@asynccontextmanager
async def fake_transaction():
    yield object()


def _snapshot_hash(files):
    digest = hashlib.sha256()
    for item in sorted(files, key=lambda value: str(value["relative_path"])):
        relative_path = str(item["relative_path"]).replace("\\", "/").encode("utf-8")
        content = base64.b64decode(str(item["content_base64"]))
        digest.update(len(relative_path).to_bytes(8, "big"))
        digest.update(relative_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _test_skill_manifest(skill_id, *, description="Test skill.", dependency_ids=None):
    content = f"---\nname: {skill_id}\ndescription: {description}\n---\n\n# {skill_id}\n".encode("utf-8")
    files = [
        {
            "relative_path": "SKILL.md",
            "content_base64": base64.b64encode(content).decode("ascii"),
            "size_bytes": len(content),
        }
    ]
    version = _snapshot_hash(files)
    return {
        "skill_id": skill_id,
        "description": description,
        "version": version,
        "content_hash": version,
        "source": {"kind": "builtin", "asset_dir": skill_id},
        "files": files,
        "dependency_ids": list(dependency_ids or []),
        "allowed": True,
        "staged": False,
        "used": False,
    }


def _release_decision(version, *, policy_active=False, selected_track="manifest_pin"):
    return {
        "schema_version": RELEASE_DECISION_SCHEMA_VERSION,
        "policy_active": policy_active,
        "selected_version": version,
        "selected_track": selected_track,
    }


def _primary_manifest_version(skill_id, manifests):
    for manifest in manifests or []:
        if manifest.get("skill_id") == skill_id:
            return str(manifest.get("content_hash") or manifest.get("version") or "")
    return ""


def _registry_pins(root, *, skill_id, input_payload=None):
    return build_skill_manifest_pins(
        skill_id=skill_id,
        input_payload=input_payload or {},
        builtin_skills=BuiltinSkillRegistry(root).list_builtin_skills(),
    )


def payload(**overrides):
    data = {
        "tenant_id": "default",
        "workspace_id": "default",
        "user_id": "user-a",
        "session_id": "ses_1",
        "run_id": "run_1",
        "agent_id": "translate",
        "skill_id": "baoyu-translate",
        "file_ids": ["file_1"],
        "input": {},
    }
    data.update(overrides)
    if "skill_manifests" not in data:
        data["skill_manifests"] = [_test_skill_manifest(data["skill_id"])]
    primary_version = _primary_manifest_version(data["skill_id"], data.get("skill_manifests"))
    if "skill_version" not in data and primary_version:
        data["skill_version"] = primary_version
    if "release_decision" not in data and data.get("skill_version"):
        data["release_decision"] = _release_decision(data["skill_version"])
    return RunPayload(**data)


def settings(tmp_path, *, sdk_enabled=True, legacy_fallback=False):
    short_id = hashlib.sha256(str(tmp_path).encode("utf-8")).hexdigest()[:8]
    return type(
        "S",
        (),
        {
            "claude_agent_sdk_enabled": sdk_enabled,
            "claude_agent_workspace_root": str(tmp_path / "workspaces"),
            "sandbox_workspace_root": str(tmp_path.parents[1] / f"s-{short_id}"),
            "sandbox_container_provider": "docker",
            "sandbox_callback_base_url": "http://platform.test",
            "claude_agent_model": "deepseek-v4-flash",
            "platform_skills_root": str(tmp_path / "skills"),
            "skill_staging_subdir": ".claude/skills",
            "enable_legacy_runtime211_fallback": legacy_fallback,
        },
    )()


def sandbox_writing_payload(**overrides):
    tier = str(overrides.pop("execution_tier", "document_worker"))
    overrides.setdefault("context_snapshot", {"execution_tier": tier})
    overrides.setdefault("context_pack", {"execution_tier": tier})
    return payload(**overrides)


def install_sandbox_runtime(monkeypatch, *, executor_response=None, status="accepted", provider="docker"):
    requests = []

    class FakeSandboxRuntime:
        def __init__(self):
            provider_type = {
                "docker": DockerContainerProvider,
                "opensandbox": OpenSandboxContainerProvider,
                "fake": FakeContainerProvider,
            }[provider]
            self.provider = object.__new__(provider_type)

        async def submit(self, request, event_sink=None):
            requests.append(request)
            response = executor_response(request) if callable(executor_response) else executor_response
            if asyncio.iscoroutine(response):
                response = await response
            return types.SimpleNamespace(
                status=status,
                provider=provider,
                session_id=request.session_id,
                run_id=request.run_id,
                executor_response=dict(
                    response
                    or {
                        "status": status,
                        "message": "sandbox completed",
                        "sdk_used": True,
                        "used_skills": [],
                        "used_skills_source": "",
                    }
                ),
                timings={},
            )

    monkeypatch.setattr(
        "app.executors.claude_agent_worker.SandboxRuntime",
        lambda *args, **kwargs: FakeSandboxRuntime(),
    )
    return requests


def sandbox_workspace_path(current_settings, *, run_id="run_1"):
    return (
        Path(current_settings.sandbox_workspace_root)
        / "tenants"
        / "default"
        / "workspaces"
        / "default"
        / "users"
        / "user-a"
        / "sessions"
        / "ses_1"
        / "runs"
        / run_id
        / "workspace"
    )


async def prepare_controlled_runner_case(adapter, run_payload, current_settings, *, event_sink=None):
    prepared, failure = await adapter._prepare_sdk_run(
        run_payload,
        event_sink=event_sink,
        workspace=sandbox_workspace_path(current_settings, run_id=run_payload.run_id),
        workspace_root=current_settings.sandbox_workspace_root,
    )
    assert failure is None
    assert prepared is not None
    return prepared


async def call_controlled_runner_helper(adapter, run_payload, prepared, sdk_result, *, event_sink=None):
    return await adapter._try_controlled_runner(
        run_payload,
        event_sink=event_sink,
        workspace=prepared.workspace,
        file_names=prepared.file_names,
        staged_skill_names=prepared.staged_skill_names,
        selected_skills=prepared.selected_skills,
        pinned_manifests=prepared.pinned_manifests,
        sdk_result=sdk_result,
    )


def write_skill(root, name="qa-file-reviewer", description="Review Word documents."):
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return skill_dir


def write_runner_skill(
    root,
    *,
    name="qa-file-reviewer",
    script_name="run_qa_review.py",
    artifact_name="reviewed.docx",
    description="Review Word documents.",
):
    skill_dir = write_skill(root, name=name, description=description)
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / script_name).write_text(
        "import pathlib\n"
        "import sys\n"
        "out = pathlib.Path(sys.argv[2])\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        f"(out / {artifact_name!r}).write_bytes(b'reviewed artifact')\n"
        "print('deterministic runner completed')\n",
        encoding="utf-8",
    )
    return skill_dir


def write_empty_bash_loop_transcript(workspace, *, count=3):
    transcript_dir = workspace / ".claude-config" / "projects" / "run"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    transcript = transcript_dir / "session.jsonl"
    lines = []
    for index in range(count):
        lines.append(
            json.dumps(
                {
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Bash",
                                "id": f"call-{index}",
                                "input": {},
                            }
                        ]
                    }
                }
            )
        )
    transcript.write_text("\n".join(lines), encoding="utf-8")
    return transcript


def write_bash_command_transcript(workspace, *, command="echo ok", project="run-command"):
    transcript_dir = workspace / ".claude-config" / "projects" / project
    transcript_dir.mkdir(parents=True, exist_ok=True)
    transcript = transcript_dir / "session.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "id": "call-command",
                            "input": {"command": command},
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    return transcript


def symlink_or_skip(target, link):
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation not available: {exc}")


def test_registry_exposes_claude_agent_worker():
    adapter = AdapterRegistry().get("claude-agent-worker")

    assert isinstance(adapter, ClaudeAgentWorkerAdapter)


def test_collect_workspace_artifacts_rejects_symlinked_output(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    output = workspace / "output"
    output.mkdir(parents=True)
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    symlink_or_skip(secret, output / "linked-secret.txt")
    stored = []

    class FakeStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            stored.append(content)
            return StoredObject(storage_key=storage_key, sha256="hash", size_bytes=len(content))

    monkeypatch.setattr("app.executors.claude_agent_worker.ObjectStorage", FakeStorage)
    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())

    with pytest.raises(ValueError, match="symlink"):
        adapter._collect_workspace_artifacts(payload(), workspace)

    assert stored == []


def test_collect_workspace_artifacts_includes_delivery_outputs(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    delivery = workspace / "outputs" / "run-002-ctd-fill" / "delivery"
    delivery.mkdir(parents=True)
    (delivery / "filled.docx").write_bytes(b"docx")
    debug_dir = workspace / "outputs" / "run-002-ctd-fill" / "_debug"
    debug_dir.mkdir()
    (debug_dir / "debug.txt").write_text("debug", encoding="utf-8")
    stored = []

    class FakeStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            stored.append((storage_key, content, content_type))
            return StoredObject(storage_key=storage_key, sha256="hash", size_bytes=len(content))

    monkeypatch.setattr("app.executors.claude_agent_worker.ObjectStorage", FakeStorage)
    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())

    artifacts = adapter._collect_workspace_artifacts(
        payload(skill_id="ctd-32s73-stability-template-fill"),
        workspace,
    )

    assert len(artifacts) == 1
    assert artifacts[0].artifact_type == "result_docx"
    assert artifacts[0].manifest["workspace_output"] == "outputs/run-002-ctd-fill/delivery/filled.docx"
    assert stored == [
        (
            "tenants/default/workspaces/default/sessions/ses_1/runs/run_1/artifacts/1/filled.docx",
            b"docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    ]


@pytest.mark.asyncio
async def test_materialize_files_rejects_symlinked_workspace(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace-link"
    outside = tmp_path / "outside"
    outside.mkdir()
    symlink_or_skip(outside, workspace)

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())

    with pytest.raises(ValueError, match="run workspace"):
        await adapter._materialize_files(payload(file_ids=["file_1"]), workspace)


@pytest.mark.asyncio
async def test_materialize_files_rejects_existing_symlinked_target(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    symlink_or_skip(outside, workspace / "input.docx")

    class FakeStorage:
        def get_bytes(self, *, storage_key):
            return b"doc"

    @asynccontextmanager
    async def fake_transaction():
        yield object()

    async def fake_get_run_file(conn, *, tenant_id, run_id, file_id):
        return {"original_name": "input.docx", "storage_key": "files/input.docx"}

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.ObjectStorage", FakeStorage)
    monkeypatch.setattr("app.executors.claude_agent_worker.repositories.get_run_file", fake_get_run_file)
    monkeypatch.setattr("app.executors.claude_agent_worker.transaction", fake_transaction)

    with pytest.raises(ValueError, match="run workspace"):
        await adapter._materialize_files(payload(file_ids=["file_1"]), workspace)


def test_qa_file_reviewer_includes_minimax_docx_dependency_when_available():
    selected = _allowed_skill_names(
        types.SimpleNamespace(skill_id="qa-file-reviewer", input={}, skill_manifests=[]),
        ["qa-file-reviewer", "minimax-docx", "baoyu-translate"],
    )

    assert selected == ["qa-file-reviewer", "minimax-docx"]


def test_ctd_stability_template_fill_includes_reference_fact_dependency_when_available():
    selected = _allowed_skill_names(
        types.SimpleNamespace(skill_id="ctd-32s73-stability-template-fill", input={}, skill_manifests=[]),
        ["ctd-32s73-stability-template-fill", "reference-fact-extraction", "general-chat"],
    )

    assert selected == ["ctd-32s73-stability-template-fill", "reference-fact-extraction"]


def test_inferred_used_skill_names_uses_shared_dependency_helper(monkeypatch):
    calls = []

    def fake_dependency_ids(skill_id, available):
        calls.append((skill_id, available))
        return ["custom-dependency"] if skill_id == "qa-file-reviewer" else []

    monkeypatch.setattr("app.executors.claude_agent_worker.skill_dependency_ids", fake_dependency_ids)

    used = _inferred_used_skill_names(
        types.SimpleNamespace(skill_id="qa-file-reviewer", input={}, skill_manifests=[]),
        ["qa-file-reviewer", "custom-dependency"],
    )

    assert used == ["qa-file-reviewer", "custom-dependency"]
    assert calls == [("qa-file-reviewer", {"qa-file-reviewer", "custom-dependency"})]


def test_allowed_skill_names_prefers_pinned_manifest_dependency_graph(monkeypatch):
    monkeypatch.setattr(dependency_policy, "SKILL_DEPENDENCIES", {})

    selected = _allowed_skill_names(
        payload(
            skill_id="qa-file-reviewer",
            skill_manifests=[
                _test_skill_manifest("qa-file-reviewer", dependency_ids=["minimax-docx"]),
                _test_skill_manifest("minimax-docx"),
            ],
        ),
        ["qa-file-reviewer", "minimax-docx"],
    )

    assert selected == ["qa-file-reviewer", "minimax-docx"]


@pytest.mark.asyncio
async def test_agent_run_records_pinned_manifest_dependency_graph(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    current_policy_helper = write_skill(tmp_path / "skills", name="minimax-docx", description="Current DOCX helper.")
    async def no_files(payload, workspace):
        return []

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", no_files)
    runtime_requests = install_sandbox_runtime(monkeypatch)

    result = await adapter.submit_run(
        sandbox_writing_payload(
            skill_id="qa-file-reviewer",
            agent_id="qa-word-review",
            skill_manifests=[
                _test_skill_manifest("qa-file-reviewer", dependency_ids=["legacy-helper"]),
                _test_skill_manifest("legacy-helper"),
            ],
        )
    )

    assert current_policy_helper.is_dir()
    assert result.status == "succeeded"
    assert runtime_requests[0].skill_ids == ["qa-file-reviewer", "legacy-helper"]
    assert result.executor_payload["skill_manifests"][0]["dependency_ids"] == ["legacy-helper"]


def test_general_chat_does_not_stage_all_platform_skills_by_default():
    selected = _allowed_skill_names(
        payload(agent_id="general-agent", skill_id="general-chat", input={"message": "hello"}),
        ["qa-file-reviewer", "minimax-docx", "baoyu-translate"],
    )

    assert selected == []


@pytest.mark.asyncio
async def test_general_chat_treats_sdk_stop_sequence_as_normal_completion(monkeypatch):
    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())

    async def sdk_stop_sequence(*args, **kwargs):
        return FakeSdkStopSequence()

    monkeypatch.setattr(adapter, "_try_run_sdk", sdk_stop_sequence)

    result = await adapter._run_general_chat(payload())

    assert result.status == "succeeded"
    assert result.result["message"] == "completed at the requested stop sequence"
    assert result.result["sdk_error"] is None
    assert result.executor_payload["sdk_terminal_reason"] == "stop_sequence"


@pytest.mark.asyncio
async def test_general_chat_keeps_real_sdk_errors_failed(monkeypatch):
    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())

    async def sdk_runtime_error(*args, **kwargs):
        return FakeSdkRuntimeError()

    monkeypatch.setattr(adapter, "_try_run_sdk", sdk_runtime_error)

    result = await adapter._run_general_chat(payload())

    assert result.status == "failed"
    assert result.result["error_code"] == "claude_agent_sdk_runtime_error"


@pytest.mark.asyncio
async def test_legacy_delegate_is_not_used_even_when_flag_enabled(monkeypatch, tmp_path):
    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.get_settings",
        lambda: settings(tmp_path, sdk_enabled=False, legacy_fallback=True),
    )

    result = await adapter.submit_run(sandbox_writing_payload())

    result.validate()
    assert result.executor_type == "claude-agent-worker"
    assert result.status == "failed"
    assert result.result["error_code"] == "claude_agent_sdk_disabled"
    assert result.result["delegate_used"] is False
    assert result.result["sdk_used"] is False
    assert result.executor_payload["worker_boundary"] == "claude-agent-worker"


@pytest.mark.asyncio
async def test_sdk_disabled_fails_without_legacy_delegate(monkeypatch, tmp_path):
    class FailingDelegate:
        async def submit_run(self, payload, event_sink=None):
            raise AssertionError("runtime211 must not be used unless fallback is enabled")

    adapter = ClaudeAgentWorkerAdapter(delegate=FailingDelegate())
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.get_settings",
        lambda: settings(tmp_path, sdk_enabled=False, legacy_fallback=False),
    )

    result = await adapter.submit_run(
        sandbox_writing_payload(skill_id="qa-file-reviewer", agent_id="qa-word-review")
    )

    assert result.status == "failed"
    assert result.result["error_code"] == "claude_agent_sdk_disabled"
    assert result.result["delegate_used"] is False

@pytest.mark.asyncio
async def test_agent_run_stages_platform_skills_before_sdk(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    write_skill(tmp_path / "skills")
    write_skill(tmp_path / "skills", name="minimax-docx", description="Manipulate Word documents.")
    pins = _registry_pins(tmp_path / "skills", skill_id="qa-file-reviewer", input_payload={"message": "审核一下"})
    async def no_files(payload, workspace):
        return []

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", no_files)
    runtime_requests = install_sandbox_runtime(monkeypatch)

    result = await adapter.submit_run(
        payload(
            skill_id="qa-file-reviewer",
            agent_id="qa-word-review",
            input={"message": "审核一下"},
            skill_manifests=pins,
            context_snapshot={
                "source": "chat_stream",
                "referenced_materials": {
                    "message_count": 1,
                    "file_count": 1,
                    "artifact_count": 1,
                    "memory_record_count": 1,
                },
                "used_context_summary": {
                    "source": "chat_stream",
                    "input_keys": ["message", "attachments"],
                    "memory_policy_source": "stored",
                    "long_term_memory_read": True,
                },
                "latest_artifact_version": "v4",
                "execution_tier": "sdk_only_writing",
                "context_pack_generated_at": "2026-06-12T01:23:45Z",
                "raw_storage_key": "s3://private/object",
            },
        )
    )

    assert result.status == "succeeded"
    assert result.result["sdk_used"] is True
    assert result.result["delegate_used"] is False
    assert result.result["allowed_skills"] == ["qa-file-reviewer", "minimax-docx"]
    assert result.result["staged_skills"] == ["qa-file-reviewer", "minimax-docx"]
    assert result.result["used_skills"] == []
    assert result.executor_payload["used_skills_source"] == "none"
    assert result.executor_payload["inferred_used_skills"] == ["qa-file-reviewer", "minimax-docx"]
    manifest = result.executor_payload["skill_manifests"][0]
    assert manifest["skill_id"] == "qa-file-reviewer"
    assert manifest["version"]
    assert manifest["content_hash"] == manifest["version"]
    assert manifest["source"]["kind"] == "builtin"
    assert manifest["allowed"] is True
    assert manifest["staged"] is True
    assert manifest["used"] is False
    runtime_request = runtime_requests[0]
    workspace = (
        Path(current_settings.sandbox_workspace_root)
        / "tenants"
        / "default"
        / "workspaces"
        / "default"
        / "users"
        / "user-a"
        / "sessions"
        / "ses_1"
        / "runs"
        / "run_1"
        / "workspace"
    )
    assert runtime_request.skill_ids == ["qa-file-reviewer", "minimax-docx"]
    assert (workspace / ".claude" / "skills" / "qa-file-reviewer" / "SKILL.md").is_file()
    assert (workspace / ".claude" / "skills" / "minimax-docx" / "SKILL.md").is_file()
    assert "Skill: qa-file-reviewer" not in runtime_request.input_message
    assert "Office context pack:" in runtime_request.input_message
    assert "Context pack: 1 message(s), 1 file(s), 1 artifact(s), 0 long-term memory record(s)" in runtime_request.input_message
    assert "Latest artifact version: v4" in runtime_request.input_message
    assert "raw_storage_key" not in runtime_request.input_message
    assert "s3://private" not in runtime_request.input_message


@pytest.mark.asyncio
async def test_agent_run_prefers_worker_context_pack_over_snapshot_reparse(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    write_skill(tmp_path / "skills")
    async def no_files(payload, workspace):
        return []

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", no_files)
    runtime_requests = install_sandbox_runtime(monkeypatch)

    result = await adapter.submit_run(
        payload(
            skill_id="qa-file-reviewer",
            agent_id="qa-word-review",
            input={"message": "审核一下"},
            context_snapshot={
                "source": "stored_context_snapshot",
                "referenced_materials": {
                    "message_count": 99,
                    "file_count": 99,
                    "artifact_count": 99,
                    "memory_record_count": 99,
                },
                "used_context_summary": {
                    "source": "stored_context_snapshot",
                    "input_keys": ["raw_storage_key"],
                    "memory_policy_source": "not_recorded",
                    "long_term_memory_read": True,
                },
                "raw_storage_key": "s3://private/object",
            },
            context_pack={
                "schema_version": "ai-platform.executor-context-pack.v1",
                "prompt_summary": (
                    "Context pack: 1 message(s), 0 file(s), 0 artifact(s), "
                    "0 long-term memory record(s). Inputs: message. "
                    "Execution tier: document_worker. Context pack version: v4."
                ),
                "context_pack_generated_at": "2026-06-12T01:23:45Z",
                "execution_tier": "document_worker",
            },
        )
    )

    assert result.status == "succeeded"
    prompt = runtime_requests[0].input_message
    assert "Context pack: 1 message(s), 0 file(s), 0 artifact(s)" in prompt
    assert "Context pack version: v4" in prompt
    assert "99 message(s)" not in prompt
    assert "raw_storage_key" not in prompt
    assert "s3://private" not in prompt


@pytest.mark.asyncio
async def test_general_chat_routes_heavy_sandbox_runs_to_sandbox_runtime(monkeypatch, tmp_path):
    current_settings = type(
        "S",
        (),
            {
                "claude_agent_sdk_enabled": True,
                "claude_agent_workspace_root": str(tmp_path / "a"),
                "sandbox_workspace_root": str(tmp_path / "s"),
                "sandbox_container_provider": "docker",
                "platform_skills_root": str(tmp_path / "k"),
                "skill_staging_subdir": ".claude/skills",
                "sandbox_callback_base_url": "http://platform.test",
                "claude_agent_model": "deepseek-v4-flash",
            },
    )()
    runtime_calls = []

    class FakeRuntime:
        provider = object.__new__(DockerContainerProvider)

        async def submit(self, request, event_sink=None):
            runtime_calls.append(request)
            return types.SimpleNamespace(
                status="accepted",
                provider="docker",
                session_id=request.session_id,
                run_id=request.run_id,
                executor_response={
                    "status": "accepted",
                    "message": "sandbox completed",
                    "sdk_session_id": "sdk-session-heavy",
                    "sdk_usage": {"input_tokens": 3},
                    "sdk_used": True,
                    "executor_mode": "claude_agent_sdk",
                    "used_skills": [],
                    "used_skills_source": "",
                    "executor_first_token_latency_ms": 5,
                    "executor_tool_call_latency_ms": 0,
                    "executor_model_latency_ms": 8,
                    "document_processing_latency_ms": 0,
                    "artifact_upload_latency_ms": 0,
                },
                timings={
                    "schema_version": "ai-platform.sandbox-latency-split.v1",
                    "sandbox_queue_wait_latency_ms": 0,
                    "sandbox_lease_acquire_latency_ms": 1,
                    "sandbox_container_start_latency_ms": 2,
                    "sandbox_container_cold_start_latency_ms": 2,
                    "sandbox_healthcheck_latency_ms": 3,
                    "sandbox_executor_dispatch_latency_ms": 4,
                    "executor_first_token_latency_ms": 5,
                    "executor_tool_call_latency_ms": 0,
                    "executor_model_latency_ms": 8,
                    "document_processing_latency_ms": 0,
                    "artifact_upload_latency_ms": 0,
                    "sandbox_cleanup_latency_ms": 1,
                    "sandbox_total_latency_ms": 21,
                },
            )

    async def fail_try_run_sdk(*args, **kwargs):
        raise AssertionError("heavy_sandbox ordinary run must not stay on the worker-local SDK path")

    async def no_files(payload, workspace):
        return []

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.SandboxRuntime",
        lambda *args, **kwargs: FakeRuntime(),
        raising=False,
    )
    monkeypatch.setattr(adapter, "_try_run_sdk", fail_try_run_sdk)
    monkeypatch.setattr(adapter, "_materialize_files", no_files)

    result = await adapter.submit_run(
        payload(
            agent_id="general-agent",
            skill_id="general-chat",
            file_ids=[],
            input={"message": "run a shell command in sandbox", "sandbox_mode": "ephemeral"},
            context_snapshot={
                "schema_version": "ai-platform.context-snapshot.v1",
                "context_snapshot_id": "ctx-heavy",
                "source": "test",
                "message_count": 0,
                "file_count": 0,
                "memory_record_count": 0,
                "execution_tier": "heavy_sandbox",
            },
            context_pack={
                "schema_version": "ai-platform.executor-context-pack.v1",
                "source": "runs_api",
                "referenced_materials": {
                    "message_count": 0,
                    "file_count": 0,
                    "artifact_count": 0,
                    "memory_record_count": 0,
                },
                "used_context_summary": {
                    "source": "runs_api",
                    "input_keys": ["message"],
                    "memory_policy_source": "stored",
                    "long_term_memory_read": False,
                },
                "execution_tier": "heavy_sandbox",
                "latest_artifact_version": None,
                "context_pack_version": "v1",
                "context_pack_generated_at": "2026-07-09T00:00:00Z",
                "prompt_summary": "Execution tier: heavy_sandbox.",
            },
        ),
    )

    assert result.status == "succeeded"
    assert runtime_calls
    assert runtime_calls[0].skill_ids == ["general-chat"]
    assert runtime_calls[0].callback_token_id == "cbt_run_1"
    assert runtime_calls[0].sandbox_mode == "ephemeral"
    assert result.executor_payload["sandbox_provider"] == "docker"


@pytest.mark.parametrize(
    ("execution_tier", "skill_id"),
    [
        ("sdk_only_writing", "general-chat"),
        ("document_worker", "qa-file-reviewer"),
        ("sdk_only_writing", "tenant-selected-writing-skill"),
    ],
)
def test_single_run_claude_writing_tiers_require_real_sandbox(execution_tier, skill_id):
    assert _ordinary_run_requires_sandbox(
        payload(
            agent_id="general-agent",
            skill_id=skill_id,
            input={"message": "write the requested result"},
            context_snapshot={"execution_tier": execution_tier},
            context_pack={"execution_tier": execution_tier},
        )
    ) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("execution_tier", "agent_id", "skill_id"),
    [
        ("sdk_only_writing", "general-agent", "general-chat"),
        ("document_worker", "qa-word-review", "qa-file-reviewer"),
        ("sdk_only_writing", "general-agent", "tenant-selected-writing-skill"),
    ],
)
async def test_single_run_writing_entrypoint_never_calls_worker_local_helpers(
    monkeypatch,
    tmp_path,
    execution_tier,
    agent_id,
    skill_id,
):
    current_settings = settings(tmp_path, sdk_enabled=True)
    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())

    async def fail_local_helper(*args, **kwargs):
        raise AssertionError("ordinary writing entrypoint must not call worker-local execution helpers")

    async def no_files(payload, workspace):
        return []

    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", no_files)
    monkeypatch.setattr(adapter, "_try_run_sdk", fail_local_helper)
    monkeypatch.setattr(adapter, "_try_controlled_runner", fail_local_helper)
    runtime_requests = install_sandbox_runtime(monkeypatch)

    result = await adapter.submit_run(
        sandbox_writing_payload(
            execution_tier=execution_tier,
            agent_id=agent_id,
            skill_id=skill_id,
            file_ids=[],
            input={"message": "write the requested result"},
        )
    )

    assert result.status == "succeeded"
    assert len(runtime_requests) == 1


@pytest.mark.asyncio
async def test_unknown_claude_execution_tier_fails_before_any_execution_helper(monkeypatch):
    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())

    async def fail_execution(*args, **kwargs):
        raise AssertionError("unknown Claude execution tier must fail before execution")

    monkeypatch.setattr(adapter, "_run_with_staged_skills", fail_execution)
    result = await adapter.submit_run(
        payload(
            agent_id="general-agent",
            skill_id="general-chat",
            context_snapshot={"execution_tier": "future_untrusted_tier"},
            context_pack={"execution_tier": "future_untrusted_tier"},
        )
    )

    assert result.status == "failed"
    assert result.result["error_code"] == "untrusted_claude_execution_tier"


def test_sandbox_runtime_fake_provider_result_fails_closed(monkeypatch, tmp_path):
    adapter = ClaudeAgentWorkerAdapter()
    prepared = PreparedSdkRun(
        workspace=tmp_path,
        file_names=[],
        selected_skills=[],
        pinned_manifests={},
        allowed_skill_names=["general-chat"],
        staged_skill_names=["general-chat"],
        prompt="write the requested result",
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.get_settings",
        lambda: type("S", (), {"sandbox_container_provider": "fake"})(),
    )

    result = adapter._executor_result_from_sandbox_runtime(
        payload(agent_id="general-agent", skill_id="general-chat"),
        prepared,
        types.SimpleNamespace(
            status="accepted",
            provider="fake",
            executor_response={"status": "accepted", "message": "fake completed", "sdk_used": True},
            timings={},
        ),
    )

    assert result.status == "failed"
    assert result.result["error_code"] == "sandbox_real_provider_required"


@pytest.mark.asyncio
async def test_fake_provider_fails_before_runtime_or_worker_local_side_effects(monkeypatch):
    adapter = ClaudeAgentWorkerAdapter()
    calls = {"prepare": 0, "runtime": 0}

    async def fail_prepare(*args, **kwargs):
        calls["prepare"] += 1
        raise AssertionError("fake provider must fail before workspace or SDK preparation")

    class FailRuntime:
        def __init__(self, *args, **kwargs):
            calls["runtime"] += 1
            raise AssertionError("fake provider must fail before SandboxRuntime construction")

    monkeypatch.setattr(
        "app.executors.claude_agent_worker.get_settings",
        lambda: type("S", (), {"sandbox_container_provider": "fake"})(),
    )
    monkeypatch.setattr(adapter, "_run_with_staged_skills", fail_prepare)
    monkeypatch.setattr("app.executors.claude_agent_worker.SandboxRuntime", FailRuntime)

    result = await adapter.submit_run(
        payload(
            agent_id="general-agent",
            skill_id="general-chat",
            context_snapshot={"execution_tier": "sdk_only_writing"},
            context_pack={"execution_tier": "sdk_only_writing"},
        )
    )

    assert result.status == "failed"
    assert result.result["error_code"] == "sandbox_real_provider_required"
    assert calls == {"prepare": 0, "runtime": 0}


@pytest.mark.asyncio
async def test_actual_runtime_provider_mismatch_fails_before_workspace_preparation(monkeypatch, tmp_path):
    adapter = ClaudeAgentWorkerAdapter()
    calls = {"prepare": 0, "submit": 0}

    async def fail_prepare(*args, **kwargs):
        calls["prepare"] += 1
        raise AssertionError("actual provider mismatch must fail before workspace preparation")

    class MismatchedRuntime:
        provider = object.__new__(FakeContainerProvider)

        async def submit(self, request, event_sink=None):
            calls["submit"] += 1
            raise AssertionError("actual provider mismatch must fail before runtime.submit")

    monkeypatch.setattr(
        "app.executors.claude_agent_worker.get_settings",
        lambda: type(
            "S",
            (),
            {
                "claude_agent_sdk_enabled": True,
                "sandbox_container_provider": "docker",
                "sandbox_workspace_root": str(tmp_path),
            },
        )(),
    )
    monkeypatch.setattr(adapter, "_run_with_staged_skills", fail_prepare)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.SandboxRuntime",
        lambda *args, **kwargs: MismatchedRuntime(),
    )

    result = await adapter.submit_run(
        sandbox_writing_payload(
            execution_tier="sdk_only_writing",
            agent_id="general-agent",
            skill_id="general-chat",
        )
    )

    assert result.status == "failed"
    assert result.result["error_code"] == "sandbox_real_provider_required"
    assert result.executor_payload["sandbox_provider"] == "fake"
    assert calls == {"prepare": 0, "submit": 0}


def test_sandbox_runtime_missing_provider_does_not_fallback_to_settings(monkeypatch, tmp_path):
    adapter = ClaudeAgentWorkerAdapter()
    prepared = PreparedSdkRun(
        workspace=tmp_path,
        file_names=[],
        selected_skills=[],
        pinned_manifests={},
        allowed_skill_names=["general-chat"],
        staged_skill_names=["general-chat"],
        prompt="write the requested result",
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.get_settings",
        lambda: type("S", (), {"sandbox_container_provider": "docker"})(),
    )

    result = adapter._executor_result_from_sandbox_runtime(
        payload(
            agent_id="general-agent",
            skill_id="general-chat",
            context_snapshot={"execution_tier": "sdk_only_writing"},
            context_pack={"execution_tier": "sdk_only_writing"},
        ),
        prepared,
        types.SimpleNamespace(
            status="accepted",
            executor_response={"status": "accepted", "message": "completed", "sdk_used": True},
            timings={},
        ),
    )

    assert result.status == "failed"
    assert result.result["error_code"] == "sandbox_real_provider_required"
    assert result.executor_payload["sandbox_provider"] == ""


@pytest.mark.parametrize("runtime_status", ["error", "timeout", "future_unknown_status"])
def test_sandbox_runtime_unknown_or_error_terminal_status_fails_closed(runtime_status, tmp_path):
    adapter = ClaudeAgentWorkerAdapter()
    prepared = PreparedSdkRun(
        workspace=tmp_path,
        file_names=[],
        selected_skills=[],
        pinned_manifests={},
        allowed_skill_names=["general-chat"],
        staged_skill_names=["general-chat"],
        prompt="write the requested result",
    )

    result = adapter._executor_result_from_sandbox_runtime(
        sandbox_writing_payload(agent_id="general-agent", skill_id="general-chat"),
        prepared,
        types.SimpleNamespace(
            status=runtime_status,
            provider="docker",
            executor_response={"status": runtime_status, "message": "runtime did not complete"},
            timings={},
        ),
    )

    assert result.status == "failed"
    assert result.result["error_code"] == "executor_reported_failure"
    assert result.executor_payload["runtime_terminal_status"] == runtime_status


@pytest.mark.asyncio
async def test_general_chat_preserves_cancelled_runtime_terminal_status(monkeypatch, tmp_path):
    current_settings = type(
        "S",
        (),
        {
            "claude_agent_sdk_enabled": True,
            "claude_agent_workspace_root": str(tmp_path / "a"),
            "sandbox_workspace_root": str(tmp_path / "s"),
            "sandbox_container_provider": "docker",
            "platform_skills_root": str(tmp_path / "k"),
            "skill_staging_subdir": ".claude/skills",
            "sandbox_callback_base_url": "http://platform.test",
            "claude_agent_model": "deepseek-v4-flash",
        },
    )()

    class FakeRuntime:
        provider = object.__new__(DockerContainerProvider)

        async def submit(self, request, event_sink=None):
            return types.SimpleNamespace(
                status="cancelled",
                provider="docker",
                session_id=request.session_id,
                run_id=request.run_id,
                executor_response={
                    "status": "cancelled",
                    "message": "任务已取消",
                    "sdk_session_id": "sdk-session-heavy",
                    "sdk_usage": {},
                    "sdk_used": True,
                },
                timings={
                    "schema_version": "ai-platform.sandbox-latency-split.v1",
                    "sandbox_queue_wait_latency_ms": 0,
                    "sandbox_lease_acquire_latency_ms": 1,
                    "sandbox_container_start_latency_ms": 2,
                    "sandbox_container_cold_start_latency_ms": 2,
                    "sandbox_healthcheck_latency_ms": 3,
                    "sandbox_executor_dispatch_latency_ms": 4,
                    "executor_first_token_latency_ms": 0,
                    "executor_tool_call_latency_ms": 0,
                    "executor_model_latency_ms": 0,
                    "document_processing_latency_ms": 0,
                    "artifact_upload_latency_ms": 0,
                    "sandbox_cleanup_latency_ms": 1,
                    "sandbox_total_latency_ms": 13,
                },
            )

    async def no_files(payload, workspace):
        return []

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.SandboxRuntime",
        lambda *args, **kwargs: FakeRuntime(),
        raising=False,
    )
    monkeypatch.setattr(adapter, "_materialize_files", no_files)

    result = await adapter.submit_run(
        payload(
            agent_id="general-agent",
            skill_id="general-chat",
            file_ids=[],
            input={"message": "cancel sandbox run", "sandbox_mode": "ephemeral"},
            context_snapshot={
                "schema_version": "ai-platform.context-snapshot.v1",
                "context_snapshot_id": "ctx-heavy",
                "source": "test",
                "message_count": 0,
                "file_count": 0,
                "memory_record_count": 0,
                "execution_tier": "heavy_sandbox",
            },
            context_pack={
                "schema_version": "ai-platform.executor-context-pack.v1",
                "source": "runs_api",
                "referenced_materials": {
                    "message_count": 0,
                    "file_count": 0,
                    "artifact_count": 0,
                    "memory_record_count": 0,
                },
                "used_context_summary": {
                    "source": "runs_api",
                    "input_keys": ["message"],
                    "memory_policy_source": "stored",
                    "long_term_memory_read": False,
                },
                "execution_tier": "heavy_sandbox",
                "latest_artifact_version": None,
                "context_pack_version": "v1",
                "context_pack_generated_at": "2026-07-09T00:00:00Z",
                "prompt_summary": "Execution tier: heavy_sandbox.",
            },
        ),
    )

    assert result.status == "failed"
    assert result.result["error_code"] == "executor_cancelled"
    assert result.executor_payload["runtime_terminal_status"] == "cancelled"


@pytest.mark.asyncio
async def test_general_chat_heavy_sandbox_request_carries_context_retrieval_scope(monkeypatch, tmp_path):
    current_settings = type(
        "S",
        (),
        {
            "claude_agent_sdk_enabled": True,
            "claude_agent_workspace_root": str(tmp_path / "a"),
            "sandbox_workspace_root": str(tmp_path / "s"),
            "sandbox_container_provider": "docker",
            "platform_skills_root": str(tmp_path / "k"),
            "skill_staging_subdir": ".claude/skills",
            "sandbox_callback_base_url": "http://platform.test",
            "claude_agent_model": "deepseek-v4-flash",
        },
    )()
    runtime_calls = []

    class FakeRuntime:
        provider = object.__new__(DockerContainerProvider)

        async def submit(self, request, event_sink=None):
            runtime_calls.append(request)
            return types.SimpleNamespace(
                status="accepted",
                provider="docker",
                session_id=request.session_id,
                run_id=request.run_id,
                executor_response={"status": "accepted", "message": "sandbox completed", "sdk_used": True},
                timings={"schema_version": "ai-platform.sandbox-latency-split.v1"},
            )

    async def no_files(payload, workspace):
        return []

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.SandboxRuntime",
        lambda *args, **kwargs: FakeRuntime(),
        raising=False,
    )
    monkeypatch.setattr(adapter, "_materialize_files", no_files)

    await adapter.submit_run(
        payload(
            agent_id="general-agent",
            skill_id="general-chat",
            file_ids=[],
            input={"message": "review context file in sandbox", "sandbox_mode": "ephemeral"},
            context_snapshot={
                "schema_version": "ai-platform.context-snapshot.v1",
                "context_snapshot_id": "ctx-heavy",
                "source": "test",
                "message_count": 0,
                "file_count": 0,
                "memory_record_count": 0,
                "execution_tier": "heavy_sandbox",
            },
            context_pack={
                "schema_version": "ai-platform.executor-context-pack.v1",
                "context_manifest": {
                    "schema_version": "ai-platform.context-manifest.v1",
                    "available_retrieval_tools": ["read_context_file"],
                },
                "execution_tier": "heavy_sandbox",
            },
            trace_id="trace-sdk",
        )
    )

    assert runtime_calls[0].context_manifest["available_retrieval_tools"] == ["read_context_file"]
    assert runtime_calls[0].context_retrieval_scope.user_id == "user-a"
    assert runtime_calls[0].trace_id == "trace-sdk"


@pytest.mark.asyncio
async def test_general_chat_heavy_sandbox_fails_when_runtime_reports_sdk_disabled(monkeypatch, tmp_path):
    current_settings = type(
        "S",
        (),
        {
            "claude_agent_sdk_enabled": True,
            "claude_agent_workspace_root": str(tmp_path / "a"),
            "sandbox_workspace_root": str(tmp_path / "s"),
            "sandbox_container_provider": "docker",
            "platform_skills_root": str(tmp_path / "k"),
            "skill_staging_subdir": ".claude/skills",
            "sandbox_callback_base_url": "http://platform.test",
            "claude_agent_model": "deepseek-v4-flash",
        },
    )()

    class FakeRuntime:
        provider = object.__new__(DockerContainerProvider)

        async def submit(self, request, event_sink=None):
            return types.SimpleNamespace(
                status="failed",
                provider="docker",
                session_id=request.session_id,
                run_id=request.run_id,
                executor_response={
                    "status": "failed",
                    "message": "Claude Agent SDK is disabled",
                    "error_code": "claude_agent_sdk_disabled",
                    "error_message": "Claude Agent SDK is disabled",
                    "sdk_used": False,
                    "executor_mode": "claude_agent_sdk_disabled",
                },
                timings={"schema_version": "ai-platform.sandbox-latency-split.v1"},
            )

    async def no_files(payload, workspace):
        return []

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.SandboxRuntime",
        lambda *args, **kwargs: FakeRuntime(),
        raising=False,
    )
    monkeypatch.setattr(adapter, "_materialize_files", no_files)

    result = await adapter.submit_run(
        payload(
            agent_id="general-agent",
            skill_id="general-chat",
            file_ids=[],
            input={"message": "run a shell command in sandbox", "sandbox_mode": "ephemeral"},
            context_snapshot={
                "schema_version": "ai-platform.context-snapshot.v1",
                "context_snapshot_id": "ctx-heavy",
                "source": "test",
                "message_count": 0,
                "file_count": 0,
                "memory_record_count": 0,
                "execution_tier": "heavy_sandbox",
            },
            context_pack={
                "schema_version": "ai-platform.executor-context-pack.v1",
                "execution_tier": "heavy_sandbox",
            },
        ),
    )

    assert result.status == "failed"
    assert result.result["error_code"] == "claude_agent_sdk_disabled"


@pytest.mark.asyncio
async def test_file_skill_uses_controlled_runner_when_sdk_tool_schema_loops(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    write_runner_skill(tmp_path / "skills")
    write_skill(tmp_path / "skills", name="minimax-docx", description="Manipulate Word documents.")
    pins = _registry_pins(tmp_path / "skills", skill_id="qa-file-reviewer", input_payload={"message": "审核一下"})
    stored = []
    events = []

    async def materialize_file(payload, workspace):
        (workspace / "sample.docx").write_bytes(b"docx")
        return ["sample.docx"]

    async def sdk_turn_exhausted(payload, event_sink=None, **kwargs):
        write_empty_bash_loop_transcript(kwargs["workspace"])
        return FakeSdkMaxTurnsWithSkillUse()

    async def event_sink(**event):
        events.append(event)

    class FakeStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            stored.append((storage_key, content, content_type))
            return StoredObject(storage_key=storage_key, sha256="hash", size_bytes=len(content))

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr("app.executors.claude_agent_worker.ObjectStorage", FakeStorage)
    monkeypatch.setattr(adapter, "_materialize_files", materialize_file)
    monkeypatch.setattr(adapter, "_try_run_sdk", sdk_turn_exhausted)

    run_payload = sandbox_writing_payload(
            skill_id="qa-file-reviewer",
            agent_id="qa-word-review",
            input={"message": "审核一下"},
            skill_manifests=pins,
    )
    prepared = await prepare_controlled_runner_case(
        adapter, run_payload, current_settings, event_sink=event_sink
    )
    write_empty_bash_loop_transcript(prepared.workspace)
    result = await call_controlled_runner_helper(
        adapter,
        run_payload,
        prepared,
        FakeSdkMaxTurnsWithSkillUse(),
        event_sink=event_sink,
    )

    assert result.status == "succeeded"
    assert result.result["controlled_runner_used"] is True
    assert result.result["controlled_runner_reason"] == "empty_bash_tool_input_loop"
    assert result.result["sdk_error"] == "Reached maximum number of turns (128)"
    assert result.artifacts[0].artifact_type == "reviewed_docx"
    assert stored[0][1] == b"reviewed artifact"
    assert result.result["used_skills"] == ["qa-file-reviewer"]
    assert result.executor_payload["used_skills"] == ["qa-file-reviewer"]
    assert result.executor_payload["inferred_used_skills"] == ["qa-file-reviewer", "minimax-docx"]
    manifests = {item["skill_id"]: item for item in result.executor_payload["skill_manifests"]}
    assert manifests["qa-file-reviewer"]["used"] is True
    assert manifests["minimax-docx"]["used"] is False
    assert any(event["event_type"] == "controlled_runner_completed" for event in events)


@pytest.mark.asyncio
async def test_baoyu_translate_uses_controlled_runner_when_sdk_tool_schema_loops(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    write_runner_skill(
        tmp_path / "skills",
        name="baoyu-translate",
        script_name="run_translation.py",
        artifact_name="translated.docx",
        description="Translate Word documents.",
    )
    pins = _registry_pins(tmp_path / "skills", skill_id="baoyu-translate", input_payload={"message": "翻译一下"})
    stored = []

    async def materialize_file(payload, workspace):
        (workspace / "sample.docx").write_bytes(b"docx")
        return ["sample.docx"]

    async def sdk_turn_exhausted(payload, event_sink=None, **kwargs):
        write_empty_bash_loop_transcript(kwargs["workspace"])
        return FakeSdkMaxTurnsWithSkillUse()

    class FakeStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            stored.append((storage_key, content, content_type))
            return StoredObject(storage_key=storage_key, sha256="hash", size_bytes=len(content))

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr("app.executors.claude_agent_worker.ObjectStorage", FakeStorage)
    monkeypatch.setattr(adapter, "_materialize_files", materialize_file)
    monkeypatch.setattr(adapter, "_try_run_sdk", sdk_turn_exhausted)

    run_payload = sandbox_writing_payload(
            skill_id="baoyu-translate",
            agent_id="baoyu-translate",
            input={"message": "翻译一下"},
            skill_manifests=pins,
    )
    prepared = await prepare_controlled_runner_case(adapter, run_payload, current_settings)
    write_empty_bash_loop_transcript(prepared.workspace)
    result = await call_controlled_runner_helper(
        adapter, run_payload, prepared, FakeSdkMaxTurnsWithSkillUse()
    )

    assert result.status == "succeeded"
    assert result.result["controlled_runner_used"] is True
    assert result.artifacts[0].artifact_type == "translated_docx"
    assert stored[0][1] == b"reviewed artifact"


@pytest.mark.asyncio
async def test_controlled_runner_failure_keeps_sdk_failure(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    skill_dir = write_skill(tmp_path / "skills")
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "run_qa_review.py").write_text("import sys\nsys.exit(7)\n", encoding="utf-8")
    write_skill(tmp_path / "skills", name="minimax-docx", description="Manipulate Word documents.")
    pins = _registry_pins(tmp_path / "skills", skill_id="qa-file-reviewer", input_payload={"message": "审核一下"})
    events = []

    async def materialize_file(payload, workspace):
        (workspace / "sample.docx").write_bytes(b"docx")
        return ["sample.docx"]

    async def sdk_turn_exhausted(payload, event_sink=None, **kwargs):
        write_empty_bash_loop_transcript(kwargs["workspace"])
        return FakeSdkMaxTurnsWithSkillUse()

    async def event_sink(**event):
        events.append(event)

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", materialize_file)
    monkeypatch.setattr(adapter, "_try_run_sdk", sdk_turn_exhausted)

    run_payload = sandbox_writing_payload(
            skill_id="qa-file-reviewer",
            agent_id="qa-word-review",
            input={"message": "审核一下"},
            skill_manifests=pins,
    )
    prepared = await prepare_controlled_runner_case(
        adapter, run_payload, current_settings, event_sink=event_sink
    )
    write_empty_bash_loop_transcript(prepared.workspace)
    result = await call_controlled_runner_helper(
        adapter,
        run_payload,
        prepared,
        FakeSdkMaxTurnsWithSkillUse(),
        event_sink=event_sink,
    )

    assert result is None
    assert any(event["event_type"] == "controlled_runner_failed" for event in events)


@pytest.mark.asyncio
async def test_controlled_runner_success_without_artifacts_keeps_sdk_failure(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    skill_dir = write_skill(tmp_path / "skills")
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "run_qa_review.py").write_text(
        "import pathlib\n"
        "import sys\n"
        "pathlib.Path(sys.argv[2]).mkdir(parents=True, exist_ok=True)\n"
        "print('runner completed without artifacts')\n",
        encoding="utf-8",
    )
    write_skill(tmp_path / "skills", name="minimax-docx", description="Manipulate Word documents.")
    pins = _registry_pins(tmp_path / "skills", skill_id="qa-file-reviewer", input_payload={"message": "审核一下"})
    events = []

    async def materialize_file(payload, workspace):
        (workspace / "sample.docx").write_bytes(b"docx")
        return ["sample.docx"]

    async def sdk_turn_exhausted(payload, event_sink=None, **kwargs):
        write_empty_bash_loop_transcript(kwargs["workspace"])
        return FakeSdkMaxTurnsWithSkillUse()

    async def event_sink(**event):
        events.append(event)

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", materialize_file)
    monkeypatch.setattr(adapter, "_try_run_sdk", sdk_turn_exhausted)

    run_payload = sandbox_writing_payload(
            skill_id="qa-file-reviewer",
            agent_id="qa-word-review",
            input={"message": "审核一下"},
            skill_manifests=pins,
    )
    prepared = await prepare_controlled_runner_case(
        adapter, run_payload, current_settings, event_sink=event_sink
    )
    write_empty_bash_loop_transcript(prepared.workspace)
    result = await call_controlled_runner_helper(
        adapter,
        run_payload,
        prepared,
        FakeSdkMaxTurnsWithSkillUse(),
        event_sink=event_sink,
    )

    assert result is None
    assert any(event["event_type"] == "controlled_runner_failed" for event in events)


@pytest.mark.asyncio
async def test_controlled_runner_launch_timeout_keeps_sdk_failure(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    write_runner_skill(tmp_path / "skills")
    write_skill(tmp_path / "skills", name="minimax-docx", description="Manipulate Word documents.")
    pins = _registry_pins(tmp_path / "skills", skill_id="qa-file-reviewer", input_payload={"message": "审核一下"})
    events = []

    async def materialize_file(payload, workspace):
        (workspace / "sample.docx").write_bytes(b"docx")
        return ["sample.docx"]

    async def sdk_turn_exhausted(payload, event_sink=None, **kwargs):
        write_empty_bash_loop_transcript(kwargs["workspace"])
        return FakeSdkMaxTurnsWithSkillUse()

    async def event_sink(**event):
        events.append(event)

    def timeout_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=kwargs.get("args") or "runner", timeout=kwargs.get("timeout"))

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr("app.executors.claude_agent_worker.subprocess.run", timeout_run)
    monkeypatch.setattr(adapter, "_materialize_files", materialize_file)
    monkeypatch.setattr(adapter, "_try_run_sdk", sdk_turn_exhausted)

    run_payload = sandbox_writing_payload(
            skill_id="qa-file-reviewer",
            agent_id="qa-word-review",
            input={"message": "审核一下"},
            skill_manifests=pins,
    )
    prepared = await prepare_controlled_runner_case(
        adapter, run_payload, current_settings, event_sink=event_sink
    )
    write_empty_bash_loop_transcript(prepared.workspace)
    result = await call_controlled_runner_helper(
        adapter,
        run_payload,
        prepared,
        FakeSdkMaxTurnsWithSkillUse(),
        event_sink=event_sink,
    )

    assert result is None
    assert any(event["event_type"] == "controlled_runner_failed" for event in events)


@pytest.mark.asyncio
async def test_controlled_runner_launch_oserror_keeps_sdk_failure(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    write_runner_skill(tmp_path / "skills")
    write_skill(tmp_path / "skills", name="minimax-docx", description="Manipulate Word documents.")
    pins = _registry_pins(tmp_path / "skills", skill_id="qa-file-reviewer", input_payload={"message": "审核一下"})
    events = []

    async def materialize_file(payload, workspace):
        (workspace / "sample.docx").write_bytes(b"docx")
        return ["sample.docx"]

    async def sdk_turn_exhausted(payload, event_sink=None, **kwargs):
        write_empty_bash_loop_transcript(kwargs["workspace"])
        return FakeSdkMaxTurnsWithSkillUse()

    async def event_sink(**event):
        events.append(event)

    def broken_run(*args, **kwargs):
        raise OSError("runner launch failed")

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr("app.executors.claude_agent_worker.subprocess.run", broken_run)
    monkeypatch.setattr(adapter, "_materialize_files", materialize_file)
    monkeypatch.setattr(adapter, "_try_run_sdk", sdk_turn_exhausted)

    run_payload = sandbox_writing_payload(
            skill_id="qa-file-reviewer",
            agent_id="qa-word-review",
            input={"message": "审核一下"},
            skill_manifests=pins,
    )
    prepared = await prepare_controlled_runner_case(
        adapter, run_payload, current_settings, event_sink=event_sink
    )
    write_empty_bash_loop_transcript(prepared.workspace)
    result = await call_controlled_runner_helper(
        adapter,
        run_payload,
        prepared,
        FakeSdkMaxTurnsWithSkillUse(),
        event_sink=event_sink,
    )

    assert result is None
    assert any(event["event_type"] == "controlled_runner_failed" for event in events)


@pytest.mark.asyncio
async def test_controlled_runner_does_not_mask_gateway_failures(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    write_runner_skill(tmp_path / "skills")
    write_skill(tmp_path / "skills", name="minimax-docx", description="Manipulate Word documents.")
    pins = _registry_pins(tmp_path / "skills", skill_id="qa-file-reviewer", input_payload={"message": "审核一下"})

    async def materialize_file(payload, workspace):
        (workspace / "sample.docx").write_bytes(b"docx")
        return ["sample.docx"]

    async def sdk_gateway_failed(payload, event_sink=None, **kwargs):
        return FakeSdkRuntimeErrorWithSkillUse()

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", materialize_file)
    monkeypatch.setattr(adapter, "_try_run_sdk", sdk_gateway_failed)

    run_payload = sandbox_writing_payload(
            skill_id="qa-file-reviewer",
            agent_id="qa-word-review",
            input={"message": "审核一下"},
            skill_manifests=pins,
    )
    prepared = await prepare_controlled_runner_case(adapter, run_payload, current_settings)
    result = await call_controlled_runner_helper(
        adapter, run_payload, prepared, FakeSdkRuntimeErrorWithSkillUse()
    )

    assert result is None


@pytest.mark.asyncio
async def test_controlled_runner_does_not_mask_max_turns_without_transcript(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    write_runner_skill(tmp_path / "skills")
    write_skill(tmp_path / "skills", name="minimax-docx", description="Manipulate Word documents.")
    pins = _registry_pins(tmp_path / "skills", skill_id="qa-file-reviewer", input_payload={"message": "审核一下"})

    async def materialize_file(payload, workspace):
        (workspace / "sample.docx").write_bytes(b"docx")
        return ["sample.docx"]

    async def sdk_turn_exhausted(payload, event_sink=None, **kwargs):
        return FakeSdkMaxTurnsWithSkillUse()

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", materialize_file)
    monkeypatch.setattr(adapter, "_try_run_sdk", sdk_turn_exhausted)

    run_payload = sandbox_writing_payload(
            skill_id="qa-file-reviewer",
            agent_id="qa-word-review",
            input={"message": "审核一下"},
            skill_manifests=pins,
    )
    prepared = await prepare_controlled_runner_case(adapter, run_payload, current_settings)
    result = await call_controlled_runner_helper(
        adapter, run_payload, prepared, FakeSdkMaxTurnsWithSkillUse()
    )

    assert result is None


@pytest.mark.asyncio
async def test_controlled_runner_does_not_mask_max_turns_with_bash_command(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    write_runner_skill(tmp_path / "skills")
    write_skill(tmp_path / "skills", name="minimax-docx", description="Manipulate Word documents.")
    pins = _registry_pins(tmp_path / "skills", skill_id="qa-file-reviewer", input_payload={"message": "审核一下"})

    async def materialize_file(payload, workspace):
        (workspace / "sample.docx").write_bytes(b"docx")
        return ["sample.docx"]

    async def sdk_turn_exhausted(payload, event_sink=None, **kwargs):
        write_empty_bash_loop_transcript(kwargs["workspace"])
        write_bash_command_transcript(kwargs["workspace"])
        return FakeSdkMaxTurnsWithSkillUse()

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", materialize_file)
    monkeypatch.setattr(adapter, "_try_run_sdk", sdk_turn_exhausted)

    run_payload = sandbox_writing_payload(
            skill_id="qa-file-reviewer",
            agent_id="qa-word-review",
            input={"message": "审核一下"},
            skill_manifests=pins,
    )
    prepared = await prepare_controlled_runner_case(adapter, run_payload, current_settings)
    write_empty_bash_loop_transcript(prepared.workspace)
    write_bash_command_transcript(prepared.workspace)
    result = await call_controlled_runner_helper(
        adapter, run_payload, prepared, FakeSdkMaxTurnsWithSkillUse()
    )

    assert result is None


@pytest.mark.asyncio
async def test_agent_run_clears_stale_workspace_before_sdk(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    write_skill(tmp_path / "skills")
    write_skill(tmp_path / "skills", name="minimax-docx", description="Manipulate Word documents.")
    pins = _registry_pins(tmp_path / "skills", skill_id="qa-file-reviewer")
    stale_workspace = sandbox_workspace_path(current_settings)
    stale_output = stale_workspace / "output"
    stale_output.mkdir(parents=True)
    (stale_output / "stale.txt").write_text("old artifact", encoding="utf-8")

    async def no_files(payload, workspace):
        return []

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", no_files)
    runtime_requests = install_sandbox_runtime(monkeypatch)

    result = await adapter.submit_run(
        sandbox_writing_payload(skill_id="qa-file-reviewer", agent_id="qa-word-review", skill_manifests=pins)
    )

    assert result.status == "succeeded"
    assert runtime_requests
    assert not (stale_workspace / "output" / "stale.txt").exists()
    assert (stale_workspace / ".claude" / "skills" / "qa-file-reviewer" / "SKILL.md").is_file()
    assert (stale_workspace / ".claude" / "skills" / "minimax-docx" / "SKILL.md").is_file()
    assert result.result["artifact_count"] == 0


@pytest.mark.asyncio
async def test_qa_file_reviewer_manifest_records_available_dependency(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    write_skill(tmp_path / "skills", name="qa-file-reviewer")
    write_skill(tmp_path / "skills", name="minimax-docx", description="Manipulate Word documents.")
    input_payload = {"message": "审核一下"}
    pins = _registry_pins(tmp_path / "skills", skill_id="qa-file-reviewer", input_payload=input_payload)

    async def no_files(payload, workspace):
        return []

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", no_files)
    install_sandbox_runtime(monkeypatch)

    result = await adapter.submit_run(
        sandbox_writing_payload(
            skill_id="qa-file-reviewer",
            agent_id="qa-word-review",
            input=input_payload,
            skill_manifests=pins,
        )
    )

    assert "skill_manifests" not in result.result
    assert result.result["allowed_skills"] == ["qa-file-reviewer", "minimax-docx"]
    manifests = {item["skill_id"]: item for item in result.executor_payload["skill_manifests"]}
    assert manifests["qa-file-reviewer"]["dependency_ids"] == ["minimax-docx"]
    assert result.result["used_skills"] == []
    assert result.executor_payload["used_skills_source"] == "none"
    assert result.executor_payload["inferred_used_skills"] == ["qa-file-reviewer", "minimax-docx"]
    assert manifests["qa-file-reviewer"]["used"] is False
    assert manifests["minimax-docx"]["dependency_ids"] == []
    assert manifests["minimax-docx"]["used"] is False


@pytest.mark.asyncio
async def test_agent_run_prefers_sdk_reported_used_skills_over_inference(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    write_skill(tmp_path / "skills", name="qa-file-reviewer")
    write_skill(tmp_path / "skills", name="minimax-docx", description="Manipulate Word documents.")
    input_payload = {"message": "审核一下"}
    pins = _registry_pins(tmp_path / "skills", skill_id="qa-file-reviewer", input_payload=input_payload)

    async def no_files(payload, workspace):
        return []

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", no_files)
    install_sandbox_runtime(
        monkeypatch,
        executor_response={
            "status": "accepted",
            "message": "reviewed with native skill telemetry",
            "sdk_used": True,
            "used_skills": ["qa-file-reviewer"],
            "used_skills_source": "executor_hook",
        },
    )

    result = await adapter.submit_run(
        sandbox_writing_payload(
            skill_id="qa-file-reviewer",
            agent_id="qa-word-review",
            input=input_payload,
            skill_manifests=pins,
        )
    )

    manifests = {item["skill_id"]: item for item in result.executor_payload["skill_manifests"]}
    assert result.result["used_skills"] == ["qa-file-reviewer"]
    assert "used_skills_source" not in result.result
    assert result.executor_payload["used_skills_source"] == "executor_hook"
    assert result.executor_payload["inferred_used_skills"] == ["qa-file-reviewer", "minimax-docx"]
    assert manifests["qa-file-reviewer"]["used"] is True
    assert manifests["minimax-docx"]["used"] is False


@pytest.mark.asyncio
async def test_agent_run_preserves_sdk_reported_used_skills_on_sdk_error(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    write_skill(tmp_path / "skills", name="qa-file-reviewer")
    write_skill(tmp_path / "skills", name="minimax-docx", description="Manipulate Word documents.")
    input_payload = {"message": "审核一下"}
    pins = _registry_pins(tmp_path / "skills", skill_id="qa-file-reviewer", input_payload=input_payload)

    async def no_files(payload, workspace):
        return []

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", no_files)
    install_sandbox_runtime(
        monkeypatch,
        status="failed",
        executor_response={
            "status": "failed",
            "message": "model gateway timeout",
            "error_code": "model_gateway_timeout",
            "error_message": "model gateway timeout",
            "sdk_used": True,
            "used_skills": ["qa-file-reviewer"],
            "used_skills_source": "executor_hook",
        },
    )

    result = await adapter.submit_run(
        sandbox_writing_payload(
            skill_id="qa-file-reviewer",
            agent_id="qa-word-review",
            input=input_payload,
            skill_manifests=pins,
        )
    )

    manifests = {item["skill_id"]: item for item in result.executor_payload["skill_manifests"]}
    assert result.status == "failed"
    assert result.result["used_skills"] == ["qa-file-reviewer"]
    assert "used_skills_source" not in result.result
    assert result.executor_payload["used_skills_source"] == "executor_hook"
    assert manifests["qa-file-reviewer"]["used"] is True
    assert manifests["minimax-docx"]["used"] is False


@pytest.mark.asyncio
async def test_agent_run_stages_pinned_skill_snapshot_after_filesystem_drift(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    skill_dir = write_skill(tmp_path / "skills", name="qa-file-reviewer", description="Review Word documents.")
    write_skill(tmp_path / "skills", name="minimax-docx", description="Manipulate Word documents.")
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "guide.md").write_text("review guide", encoding="utf-8")
    pins = build_skill_manifest_pins(
        skill_id="qa-file-reviewer",
        input_payload={},
        builtin_skills=BuiltinSkillRegistry(tmp_path / "skills").list_builtin_skills(),
    )
    (skill_dir / "SKILL.md").write_text(
        "---\nname: qa-file-reviewer\ndescription: Changed.\n---\n\n# changed\n",
        encoding="utf-8",
    )
    async def no_files(payload, workspace):
        return []

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", no_files)
    runtime_requests = install_sandbox_runtime(monkeypatch)

    result = await adapter.submit_run(
        sandbox_writing_payload(
            skill_id="qa-file-reviewer", agent_id="qa-word-review", input={}, skill_manifests=pins
        )
    )

    staged_skill = sandbox_workspace_path(current_settings) / ".claude" / "skills" / "qa-file-reviewer"
    assert result.status == "succeeded"
    assert runtime_requests[0].skill_ids == ["qa-file-reviewer", "minimax-docx"]
    assert "Review Word documents." in (staged_skill / "SKILL.md").read_text(encoding="utf-8")
    assert (staged_skill / "references" / "guide.md").read_text(encoding="utf-8") == "review guide"
    assert result.executor_payload["skill_manifests"][0]["content_hash"] == pins[0]["content_hash"]


@pytest.mark.asyncio
async def test_agent_run_fails_closed_when_dependency_pin_is_missing(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    write_skill(tmp_path / "skills", name="qa-file-reviewer", description="Review Word documents.")
    write_skill(tmp_path / "skills", name="minimax-docx", description="Manipulate Word documents.")
    pins = build_skill_manifest_pins(
        skill_id="qa-file-reviewer",
        input_payload={},
        builtin_skills=BuiltinSkillRegistry(tmp_path / "skills").list_builtin_skills(),
    )
    primary_pin = [item for item in pins if item["skill_id"] == "qa-file-reviewer"]
    async def no_files(payload, workspace):
        return []

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", no_files)
    runtime_requests = install_sandbox_runtime(monkeypatch)

    result = await adapter.submit_run(
        sandbox_writing_payload(
            skill_id="qa-file-reviewer",
            agent_id="qa-word-review",
            input={},
            skill_version=primary_pin[0]["content_hash"],
            release_decision={
                "schema_version": "ai-platform.skill-release-decision.v1",
                "policy_active": False,
                "selected_version": primary_pin[0]["content_hash"],
                "selected_track": "manifest_pin",
            },
            skill_manifests=primary_pin,
        )
    )

    assert result.status == "failed"
    assert result.result["error_code"] == "skill_version_pin_mismatch"
    assert result.executor_payload["pin_mismatches"][0]["skill_id"] == "minimax-docx"
    assert result.executor_payload["pin_mismatches"][0]["reason"] == "missing_pinned_manifest"
    assert runtime_requests == []


@pytest.mark.asyncio
async def test_agent_run_fails_closed_when_snapshotless_pin_hash_drifted(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    write_skill(tmp_path / "skills", name="qa-file-reviewer", description="Review Word documents.")
    write_skill(tmp_path / "skills", name="minimax-docx", description="Manipulate Word documents.")
    async def no_files(payload, workspace):
        return []

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", no_files)
    runtime_requests = install_sandbox_runtime(monkeypatch)

    result = await adapter.submit_run(
        sandbox_writing_payload(
            skill_id="qa-file-reviewer",
            agent_id="qa-word-review",
            input={},
            skill_manifests=[
                {
                    "skill_id": "qa-file-reviewer",
                    "version": "old-hash",
                    "content_hash": "old-hash",
                    "source": {"kind": "builtin", "asset_dir": "qa-file-reviewer"},
                    "dependency_ids": [],
                    "allowed": True,
                }
            ],
        )
    )

    assert result.status == "failed"
    assert result.result["error_code"] == "skill_version_pin_mismatch"
    assert runtime_requests == []
    assert not (sandbox_workspace_path(current_settings) / ".claude" / "skills" / "qa-file-reviewer").exists()


@pytest.mark.asyncio
async def test_agent_run_fails_closed_when_snapshotless_pin_missing_hash(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    write_skill(tmp_path / "skills", name="qa-file-reviewer", description="Review Word documents.")
    async def no_files(payload, workspace):
        return []

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", no_files)

    with pytest.raises(ValueError, match="release_decision_primary_manifest_mismatch"):
        payload(
            skill_id="qa-file-reviewer",
            agent_id="qa-word-review",
            input={},
            skill_version="hash-primary",
            release_decision=_release_decision("hash-primary"),
            skill_manifests=[
                {
                    "skill_id": "qa-file-reviewer",
                    "source": {"kind": "builtin", "asset_dir": "qa-file-reviewer"},
                    "dependency_ids": [],
                    "allowed": True,
                }
            ],
        )


@pytest.mark.asyncio
async def test_agent_run_rejects_tampered_pinned_skill_snapshot_hash(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    write_skill(tmp_path / "skills", name="qa-file-reviewer", description="Review Word documents.")
    write_skill(tmp_path / "skills", name="minimax-docx", description="Manipulate Word documents.")
    pins = build_skill_manifest_pins(
        skill_id="qa-file-reviewer",
        input_payload={},
        builtin_skills=BuiltinSkillRegistry(tmp_path / "skills").list_builtin_skills(),
    )
    pins[0]["files"][0]["content_base64"] = base64.b64encode(
        b"---\nname: qa-file-reviewer\ndescription: Tampered.\n---\n\n# tampered\n"
    ).decode("ascii")
    pins[0]["files"][0]["size_bytes"] = len(
        base64.b64decode(pins[0]["files"][0]["content_base64"])
    )
    called = False

    async def fail_if_called(payload, event_sink=None, **kwargs):
        nonlocal called
        called = True
        return FakeQueryResult()

    async def no_files(payload, workspace):
        return []

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", no_files)
    runtime_requests = install_sandbox_runtime(monkeypatch)

    result = await adapter.submit_run(
        sandbox_writing_payload(
            skill_id="qa-file-reviewer", agent_id="qa-word-review", input={}, skill_manifests=pins
        )
    )

    assert result.status == "failed"
    assert result.result["error_code"] == "skill_version_pin_mismatch"
    assert result.executor_payload["pin_mismatches"][0]["expected_content_hash"] == pins[0]["content_hash"]
    assert result.executor_payload["pin_mismatches"][0]["actual_content_hash"]
    assert runtime_requests == []
    assert not (sandbox_workspace_path(current_settings) / ".claude" / "skills" / "qa-file-reviewer").exists()


@pytest.mark.asyncio
async def test_agent_run_rejects_pinned_skill_snapshot_size_mismatch(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    write_skill(tmp_path / "skills", name="qa-file-reviewer", description="Review Word documents.")
    write_skill(tmp_path / "skills", name="minimax-docx", description="Manipulate Word documents.")
    pins = build_skill_manifest_pins(
        skill_id="qa-file-reviewer",
        input_payload={},
        builtin_skills=BuiltinSkillRegistry(tmp_path / "skills").list_builtin_skills(),
    )
    pins[0]["files"][0]["size_bytes"] = int(pins[0]["files"][0]["size_bytes"]) + 1
    async def no_files(payload, workspace):
        return []

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", no_files)
    runtime_requests = install_sandbox_runtime(monkeypatch)

    result = await adapter.submit_run(
        sandbox_writing_payload(
            skill_id="qa-file-reviewer", agent_id="qa-word-review", input={}, skill_manifests=pins
        )
    )

    assert result.status == "failed"
    assert result.result["error_code"] == "skill_version_pin_mismatch"
    assert "size" in result.executor_payload["pin_mismatches"][0]["reason"]
    assert runtime_requests == []


@pytest.mark.asyncio
async def test_agent_run_rejects_pinned_skill_snapshot_file_over_worker_cap(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    write_skill(tmp_path / "skills", name="qa-file-reviewer", description="Review Word documents.")
    write_skill(tmp_path / "skills", name="minimax-docx", description="Manipulate Word documents.")
    pins = build_skill_manifest_pins(
        skill_id="qa-file-reviewer",
        input_payload={},
        builtin_skills=BuiltinSkillRegistry(tmp_path / "skills").list_builtin_skills(),
    )
    monkeypatch.setattr("app.executors.claude_agent_worker.MAX_SKILL_SNAPSHOT_FILE_BYTES", 8)
    async def no_files(payload, workspace):
        return []

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", no_files)
    runtime_requests = install_sandbox_runtime(monkeypatch)

    result = await adapter.submit_run(
        sandbox_writing_payload(
            skill_id="qa-file-reviewer", agent_id="qa-word-review", input={}, skill_manifests=pins
        )
    )

    assert result.status == "failed"
    assert result.result["error_code"] == "skill_version_pin_mismatch"
    assert "too large" in result.executor_payload["pin_mismatches"][0]["reason"]
    assert runtime_requests == []


@pytest.mark.asyncio
async def test_general_chat_with_files_stays_on_sdk_path(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)

    class FailingDelegate:
        async def submit_run(self, payload, event_sink=None):
            raise AssertionError("general chat files must not delegate to runtime211")

    async def one_file(payload, workspace):
        return ["sample.docx"]

    adapter = ClaudeAgentWorkerAdapter(delegate=FailingDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", one_file)
    runtime_requests = install_sandbox_runtime(
        monkeypatch,
        executor_response={
            "status": "accepted",
            "message": "hello from sdk",
            "sdk_used": True,
            "used_skills": [],
            "used_skills_source": "",
        },
    )

    result = await adapter.submit_run(
        sandbox_writing_payload(
            agent_id="general-agent",
            skill_id="general-chat",
            file_ids=["file_1"],
            input={"message": "summarize file"},
        )
    )

    assert result.status == "succeeded"
    assert result.result["message"] == "hello from sdk"
    assert result.result["delegate_used"] is False
    assert result.result["allowed_skills"] == ["general-chat"]
    assert runtime_requests[0].skill_ids == ["general-chat"]
    assert result.result["staged_skills"] == ["general-chat"]
    assert result.result["used_skills"] == []


@pytest.mark.asyncio
async def test_sandbox_required_general_chat_bridges_agent_event_to_keyword_worker_sink(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    current_settings.sandbox_workspace_root = str(tmp_path / "sandbox")
    received_events = []

    async def no_files(payload, workspace):
        return []

    async def event_sink(*, event_type, stage, message, payload):
        received_events.append(
            {
                "event_type": event_type,
                "stage": stage,
                "message": message,
                "payload": payload,
            }
        )

    class PositionalAgentEventRuntime:
        provider = object.__new__(DockerContainerProvider)

        async def submit(self, request, event_sink=None):
            await event_sink(
                AgentEvent(
                    type="runtime_container_started",
                    message="Sandbox executor container started",
                    admin_only=True,
                    payload={"container_id": "exec-run-1", "provider": "docker"},
                )
            )
            return types.SimpleNamespace(
                status="accepted",
                provider="docker",
                session_id=request.session_id,
                run_id=request.run_id,
                executor_response={
                    "status": "accepted",
                    "message": "sandbox completed",
                    "sdk_used": True,
                    "used_skills": [],
                    "used_skills_source": "",
                },
                timings={},
            )

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", no_files)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.SandboxRuntime",
        lambda *args, **kwargs: PositionalAgentEventRuntime(),
    )

    result = await adapter.submit_run(
        sandbox_writing_payload(
            agent_id="general-agent",
            skill_id="general-chat",
            file_ids=[],
            input={"message": "hello"},
        ),
        event_sink=event_sink,
    )

    assert result.status == "succeeded"
    assert [event["event_type"] for event in received_events] == [
        "skills_staged",
        "runtime_container_started",
    ]
    assert received_events[-1] == {
        "event_type": "runtime_container_started",
        "stage": "runtime",
        "message": "Sandbox executor container started",
        "payload": {
            "container_id": "exec-run-1",
            "provider": "docker",
            "visible_to_user": False,
            "admin_only": True,
        },
    }


@pytest.mark.asyncio
async def test_sdk_runtime_error_is_reported_without_delegate(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)

    async def no_files(payload, workspace):
        return []

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", no_files)
    install_sandbox_runtime(
        monkeypatch,
        status="failed",
        executor_response={
            "status": "failed",
            "message": "model gateway timeout",
            "error_code": "claude_agent_sdk_runtime_error",
            "error_message": "model gateway timeout",
            "sdk_used": True,
        },
    )

    result = await adapter.submit_run(
        sandbox_writing_payload(
            agent_id="general-agent", skill_id="general-chat", file_ids=[], input={"message": "hello"}
        )
    )

    assert result.status == "failed"
    assert result.result["error_code"] == "claude_agent_sdk_runtime_error"
    assert result.result["sdk_used"] is True
    assert result.result["delegate_used"] is False


@pytest.mark.asyncio
async def test_general_chat_propagates_worker_cancel_from_sdk_stream(monkeypatch, tmp_path):
    runtime_submit_calls = 0
    runtime_continued = False
    received_event_types = []
    cancellation = WorkerRunCancelled("platform cancel requested")

    async def event_sink(*, event_type, stage, message, payload):
        received_event_types.append(event_type)
        if event_type == "assistant_delta":
            raise cancellation

    class CancellingRuntime:
        provider = object.__new__(DockerContainerProvider)

        async def submit(self, request, event_sink=None):
            nonlocal runtime_submit_calls, runtime_continued
            runtime_submit_calls += 1
            await event_sink(
                AgentEvent(
                    type="assistant_delta",
                    message="partial",
                    payload={"visible_to_user": True},
                )
            )
            runtime_continued = True
            raise AssertionError("cancel must propagate before runtime result mapping")

    current_settings = settings(tmp_path, sdk_enabled=True)
    current_settings.sandbox_workspace_root = str(tmp_path / "sandbox")
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.SandboxRuntime",
        lambda *args, **kwargs: CancellingRuntime(),
    )
    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())

    with pytest.raises(WorkerRunCancelled) as exc_info:
        await adapter.submit_run(
            sandbox_writing_payload(
                agent_id="general-agent", skill_id="general-chat", file_ids=[], input={"message": "hello"}
            ),
            event_sink=event_sink,
        )

    assert exc_info.value is cancellation
    assert runtime_submit_calls == 1
    assert runtime_continued is False
    assert received_event_types == ["skills_staged", "assistant_delta"]


@pytest.mark.asyncio
async def test_worker_passes_session_continuity_resume_key_to_sdk_runner(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    write_skill(tmp_path / "skills", name="qa-file-reviewer")
    async def no_files(payload, workspace):
        return []

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", no_files)
    runtime_requests = install_sandbox_runtime(monkeypatch)

    base_payload = sandbox_writing_payload(
        agent_id="qa-word-review",
        skill_id="qa-file-reviewer",
        file_ids=[],
        input={"message": "review"},
    )
    second_payload = sandbox_writing_payload(
        agent_id="qa-word-review",
        skill_id="qa-file-reviewer",
        file_ids=[],
        input={"message": "continue"},
        run_id="run_2",
    )
    fork_payload = sandbox_writing_payload(
        agent_id="qa-word-review",
        skill_id="qa-file-reviewer",
        file_ids=[],
        input={"message": "explore", "context_fork_reason": "parallel_exploration"},
        run_id="run_3",
    )

    await adapter.submit_run(base_payload)
    await adapter.submit_run(second_payload)
    await adapter.submit_run(fork_payload)

    captured_session_ids = [request.sdk_session_id for request in runtime_requests]
    assert captured_session_ids[0]
    assert captured_session_ids[0] == captured_session_ids[1]
    assert captured_session_ids[2] != captured_session_ids[0]


@pytest.mark.asyncio
async def test_worker_passes_scoped_context_retrieval_to_sdk_runner_for_manifest(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    write_skill(tmp_path / "skills", name="qa-file-reviewer")
    async def no_files(payload, workspace):
        return []

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_materialize_files", no_files)
    runtime_requests = install_sandbox_runtime(monkeypatch)

    await adapter.submit_run(
        sandbox_writing_payload(
            agent_id="qa-word-review",
            skill_id="qa-file-reviewer",
            file_ids=[],
            input={"message": "review"},
            context_pack={
                "schema_version": "ai-platform.executor-context-pack.v1",
                "execution_tier": "document_worker",
                "context_manifest": {
                    "schema_version": "ai-platform.context-manifest.v1",
                    "available_retrieval_tools": ["read_context_file"],
                },
            },
        )
    )

    scope = runtime_requests[0].context_retrieval_scope
    assert scope is not None
    assert scope.tenant_id == "default"
    assert scope.workspace_id == "default"
    assert scope.user_id == "user-a"
    assert scope.session_id == "ses_1"


@pytest.mark.asyncio
async def test_qa_file_reviewer_multi_agent_plan_emits_steps_and_runs_staged_sdk_once(monkeypatch):
    review_calls = []
    received_event_sinks = []
    events = []

    async def fake_run_with_staged_skills(payload, event_sink=None):
        review_calls.append(payload.run_id)
        received_event_sinks.append(event_sink)
        return ExecutorResult(
            status="succeeded",
            adapter_version="claude-agent-worker-adapter/1",
            executor_type="claude-agent-worker",
            executor_version="claude-agent-sdk-poc",
            capabilities={"artifacts": True, "streaming": True, "tools": True, "skills": True, "platform_skills": True},
            result={
                "message": "reviewed",
                "artifact_count": 1,
                "sdk_used": True,
                "sdk_session_id": "sdk-session",
                "sdk_error": None,
                "delegate_used": False,
            },
            artifacts=[
                ArtifactManifest(
                    artifact_type="reviewed_docx",
                    label="审核 Word",
                    content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    storage_key="tenants/default/runs/run_1/reviewed.docx",
                    size_bytes=10,
                )
            ],
            executor_payload={
                "sdk_used": True,
                "sdk_session_id": "sdk-session",
                "sdk_usage": {"input_tokens": 1},
                "delegate_used": False,
                "worker_boundary": "claude-agent-worker",
                "staged_skills": ["qa-file-reviewer"],
            },
        )

    async def event_sink(**event):
        events.append(event)

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr(adapter, "_run_with_staged_skills", fake_run_with_staged_skills)
    qa_payload = payload(
        agent_id="qa-word-review",
        skill_id="qa-file-reviewer",
        input={
            "message": "审核一下",
            "execution_mode": "multi_agent",
            "multi_agent_steps": [
                {"step_key": "inspect", "role": "inspect"},
                {"step_key": "review", "role": "review", "depends_on": ["inspect"]},
                {"step_key": "verify", "role": "verify", "depends_on": ["review"]},
            ],
            "skill_ids": ["qa-file-reviewer"],
        },
    )

    result = await adapter._run_multi_agent_file_skill(qa_payload, event_sink=event_sink)

    assert result.status == "succeeded"
    assert result.capabilities["multi_agent"] is True
    assert result.capabilities["platform_skills"] is True
    assert review_calls == ["run_1"]
    assert received_event_sinks == [event_sink]
    assert result.result["sdk_used"] is True
    assert result.result["delegate_used"] is False
    assert result.result["sdk_session_id"] == "sdk-session"
    assert result.executor_payload["sdk_used"] is True
    assert result.executor_payload["delegate_used"] is False
    assert result.executor_payload["sdk_usage"] == {"input_tokens": 1}
    step_events = [event for event in events if event["event_type"].startswith("agent_step_")]
    assert [event["event_type"] for event in step_events] == [
        "agent_step_started",
        "agent_step_completed",
        "agent_step_started",
        "agent_step_completed",
        "agent_step_started",
        "agent_step_completed",
    ]
    assert step_events[1]["payload"]["checkpoint_id"] == "checkpoint-run_1-step-1"
    assert step_events[3]["payload"]["output"] == "reviewed"
    assert step_events[3]["payload"]["artifact_count"] == 1
    assert step_events[3]["payload"]["checkpoint_id"] == "checkpoint-run_1-step-2"
    assert step_events[5]["payload"]["checkpoint_id"] == "checkpoint-run_1-step-3"


@pytest.mark.asyncio
async def test_multi_agent_product_entrypoint_fails_closed_before_feature_gated_helper(monkeypatch):
    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())

    async def fail_helper(*args, **kwargs):
        raise AssertionError("multi-agent product entrypoint must not enter the feature-gated helper")

    monkeypatch.setattr(adapter, "_run_multi_agent_file_skill", fail_helper)
    result = await adapter.submit_run(
        payload(
            agent_id="qa-word-review",
            skill_id="qa-file-reviewer",
            input={"message": "review", "execution_mode": "multi_agent"},
        )
    )

    assert result.status == "failed"
    assert result.result["error_code"] == "multi_agent_adapter_execution_disabled"


@pytest.mark.asyncio
async def test_multi_agent_file_skill_resume_reuses_completed_steps_without_rerunning_skill(monkeypatch, tmp_path):
    events = []
    current_settings = settings(tmp_path, sdk_enabled=True)

    async def fail_staged_sdk(payload, event_sink=None):
        raise AssertionError("checkpointed file skill step must not run again")

    async def event_sink(**event):
        events.append(event)

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_run_with_staged_skills", fail_staged_sdk)
    qa_payload = payload(
        tenant_id="default",
        workspace_id="default",
        user_id="user-a",
        session_id="ses_1",
        run_id="run_retry",
        agent_id="qa-word-review",
        skill_id="qa-file-reviewer",
        file_ids=["file_1"],
        skill_manifests=[
            _test_skill_manifest("qa-file-reviewer", dependency_ids=["minimax-docx"]),
            _test_skill_manifest("minimax-docx"),
        ],
        input={
            "message": "继续审核",
            "execution_mode": "multi_agent",
            "multi_agent_steps": [
                {"step_key": "inspect", "role": "inspect"},
                {"step_key": "review", "role": "review", "depends_on": ["inspect"]},
                {"step_key": "verify", "role": "verify", "depends_on": ["review"]},
            ],
            "resume": {
                "copied_from_run_id": "run_mid",
                "completed_step_outputs": {
                    "inspect": "Input inspected: 1 file(s).",
                    "review": "reviewed Word artifact ready",
                },
                "completed_step_checkpoints": {
                    "inspect": {
                        "checkpoint_id": "checkpoint-inspect",
                        "source_step_id": "step-inspect-source",
                        "copied_from_run_id": "run_original",
                    },
                    "review": {
                        "checkpoint_id": "checkpoint-review",
                        "source_step_id": "step-review-source",
                        "copied_from_run_id": "run_original",
                    },
                },
            },
        },
    )

    result = await adapter._run_multi_agent_file_skill(qa_payload, event_sink=event_sink)

    assert result.status == "succeeded"
    assert result.capabilities["multi_agent"] is True
    assert result.result["checkpoint_reused"] is True
    assert result.result["delegate_executor_type"] == "multi-agent-resume"
    step_events = [event for event in events if event["event_type"].startswith("agent_step_")]
    assert [event["event_type"] for event in step_events] == [
        "agent_step_reused",
        "agent_step_reused",
        "agent_step_started",
        "agent_step_completed",
    ]
    assert step_events[0]["payload"]["copied_from_run_id"] == "run_original"
    assert step_events[0]["payload"]["checkpoint_id"] == "checkpoint-inspect"
    assert step_events[0]["payload"]["source_step_id"] == "step-inspect-source"
    assert step_events[1]["payload"]["output"] == "reviewed Word artifact ready"
    assert step_events[1]["payload"]["checkpoint_id"] == "checkpoint-review"
    assert step_events[1]["payload"]["source_step_id"] == "step-review-source"
    assert step_events[1]["payload"]["copied_from_run_id"] == "run_original"
    assert step_events[3]["payload"]["output"] == "Verification completed: 0 artifact(s) prepared."


@pytest.mark.asyncio
async def test_multi_agent_resume_validates_pinned_snapshot_before_reuse(monkeypatch, tmp_path):
    events = []
    current_settings = settings(tmp_path, sdk_enabled=True)
    write_skill(tmp_path / "skills", name="qa-file-reviewer", description="Review Word documents.")
    write_skill(tmp_path / "skills", name="minimax-docx", description="Manipulate Word documents.")
    pins = _registry_pins(tmp_path / "skills", skill_id="qa-file-reviewer")
    pins[0]["files"][0]["content_base64"] = base64.b64encode(
        b"---\nname: qa-file-reviewer\ndescription: Tampered.\n---\n\n# tampered\n"
    ).decode("ascii")
    pins[0]["files"][0]["size_bytes"] = len(base64.b64decode(pins[0]["files"][0]["content_base64"]))

    async def fail_staged_sdk(payload, event_sink=None):
        raise AssertionError("checkpointed file skill step must not run SDK")

    async def event_sink(**event):
        events.append(event)

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr(adapter, "_run_with_staged_skills", fail_staged_sdk)

    result = await adapter._run_multi_agent_file_skill(
        payload(
            tenant_id="default",
            workspace_id="default",
            user_id="user-a",
            session_id="ses_1",
            run_id="run_retry",
            agent_id="qa-word-review",
            skill_id="qa-file-reviewer",
            file_ids=["file_1"],
            input={
                "message": "继续审核",
                "execution_mode": "multi_agent",
                "multi_agent_steps": [
                    {"step_key": "inspect", "role": "inspect"},
                    {"step_key": "review", "role": "review", "depends_on": ["inspect"]},
                    {"step_key": "verify", "role": "verify", "depends_on": ["review"]},
                ],
                "resume": {
                    "copied_from_run_id": "run_original",
                    "completed_step_outputs": {
                        "inspect": "Input inspected: 1 file(s).",
                        "review": "reviewed Word artifact ready",
                    },
                },
            },
            skill_manifests=pins,
        ),
        event_sink=event_sink,
    )

    assert result.status == "failed"
    assert result.result["error_code"] == "skill_version_pin_mismatch"
    assert result.executor_payload["pin_mismatches"][0]["skill_id"] == "qa-file-reviewer"
    assert [event["event_type"] for event in events if event["event_type"].startswith("agent_step_")] == []


def test_build_sdk_env_maps_anthropic_gateway(monkeypatch):
    current_settings = type(
        "S",
        (),
        {
            "anthropic_base_url": "http://10.56.0.211:3002",
            "anthropic_auth_token": "token",
            "anthropic_model": "deepseek-v4-flash",
            "openai_api_key": "",
        },
    )()
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", lambda: current_settings)

    env = build_sdk_env()

    assert env["ANTHROPIC_BASE_URL"] == "http://10.56.0.211:3002"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "token"
    assert env["ANTHROPIC_MODEL"] == "deepseek-v4-flash"


def test_build_sdk_env_overrides_untrusted_inherited_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", "/tmp/user-home")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/tmp/user-claude-config")
    monkeypatch.setenv("AI_PLATFORM_SECRET", "host-secret")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "ambient-token")
    current_settings = type(
        "S",
        (),
        {
            "anthropic_base_url": "http://10.56.0.211:3002",
            "anthropic_auth_token": "settings-token",
            "anthropic_model": "deepseek-v4-flash",
            "openai_api_key": "",
        },
    )()
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", lambda: current_settings)

    env = build_sdk_env(cwd=tmp_path / "run-workspace")

    assert env["ANTHROPIC_AUTH_TOKEN"] == "settings-token"
    assert env["HOME"] == str(tmp_path / "run-workspace" / ".home")
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "run-workspace" / ".claude-config")
    assert env["AI_PLATFORM_SECRET"] == ""


def test_build_skill_prompt_uses_backend_managed_skills_without_forced_selector():
    prompt = build_skill_prompt(
        skill_id="qa-file-reviewer",
        user_message="review this",
        file_names=["sample.docx"],
    )

    assert "Skill: qa-file-reviewer" not in prompt
    assert "sample.docx" in prompt
    assert "backend-managed skills" in prompt
    assert "staged Skill" in prompt


def test_build_skill_prompt_includes_bounded_executor_context_pack():
    prompt = build_skill_prompt(
        skill_id="general-chat",
        user_message="continue the proposal",
        file_names=["proposal.docx"],
        context_pack={
            "schema_version": "ai-platform.executor-context-pack.v1",
            "prompt_summary": (
                "Context pack: 2 message(s), 1 file(s), 1 artifact(s), "
                "0 long-term memory record(s). Inputs: attachments, message. "
                "Execution tier: sdk_only_writing. Latest artifact version: v3."
            ),
            "referenced_materials": {
                "message_count": 2,
                "file_count": 1,
                "artifact_count": 1,
                "memory_record_count": 0,
            },
            "used_context_summary": {
                "source": "chat_stream",
                "input_keys": ["attachments", "message"],
                "memory_policy_source": "stored",
                "long_term_memory_read": False,
            },
            "context_pack_generated_at": "2026-06-12T01:23:45Z",
            "raw_storage_key": "s3://private/object",
            "sandbox_workdir": "/tmp/private",
        },
    )

    assert "Office context pack:" in prompt
    assert "Context pack: 2 message(s), 1 file(s), 1 artifact(s)" in prompt
    assert "Context pack generated at: 2026-06-12T01:23:45Z" in prompt
    assert "Use this bounded context only as background" in prompt
    assert "raw_storage_key" not in prompt
    assert "s3://private" not in prompt
    assert "sandbox_workdir" not in prompt


def test_build_skill_prompt_ignores_unknown_context_pack_schema():
    prompt = build_skill_prompt(
        skill_id="general-chat",
        user_message="continue the proposal",
        file_names=[],
        context_pack={
            "schema_version": "private.unbounded.v1",
            "prompt_summary": "raw_storage_key=s3://private/object",
        },
    )

    assert "Office context pack:" not in prompt
    assert "raw_storage_key" not in prompt
    assert "s3://private" not in prompt


def test_build_skill_prompt_rejects_leaky_context_pack_summary():
    prompt = build_skill_prompt(
        skill_id="general-chat",
        user_message="continue the proposal",
        file_names=[],
        context_pack={
            "schema_version": "ai-platform.executor-context-pack.v1",
            "prompt_summary": "raw_storage_key=s3://private/object sandbox_workdir=/tmp/private",
            "context_pack_version": "v4",
            "context_pack_generated_at": "2026-06-12T01:23:45Z",
        },
    )

    assert "Office context pack:" not in prompt
    assert "raw_storage_key" not in prompt
    assert "s3://private" not in prompt
    assert "sandbox_workdir" not in prompt
    assert "/tmp/private" not in prompt


def test_build_skill_prompt_sanitizes_context_pack_metadata():
    prompt = build_skill_prompt(
        skill_id="general-chat",
        user_message="continue the proposal",
        file_names=[],
        context_pack={
            "schema_version": "ai-platform.executor-context-pack.v1",
            "prompt_summary": "Context pack: 1 message(s), 0 file(s), 0 artifact(s).",
            "context_pack_version": "/tmp/private-version",
            "context_pack_generated_at": "C:\\private\\generated-at",
        },
    )

    assert "Office context pack:" in prompt
    assert "Context pack: 1 message(s), 0 file(s), 0 artifact(s)." in prompt
    assert "Context pack version:" not in prompt
    assert "Context pack generated at:" not in prompt
    assert "/tmp/private-version" not in prompt
    assert "C:\\private\\generated-at" not in prompt


def test_build_skill_prompt_rejects_semantically_private_context_pack_metadata():
    prompt = build_skill_prompt(
        skill_id="general-chat",
        user_message="continue the proposal",
        file_names=[],
        context_pack={
            "schema_version": "ai-platform.executor-context-pack.v1",
            "prompt_summary": "Context pack: 1 message(s), 0 file(s), 0 artifact(s).",
            "context_pack_version": "raw_storage_key=tenant/private/object",
            "context_pack_generated_at": "run_id=run-a raw_memory_content=customer-note",
        },
    )

    assert "Office context pack:" in prompt
    assert "Context pack: 1 message(s), 0 file(s), 0 artifact(s)." in prompt
    assert "Context pack version:" not in prompt
    assert "Context pack generated at:" not in prompt
    assert "raw_storage_key" not in prompt
    assert "raw_memory_content" not in prompt


def test_build_skill_prompt_frontloads_qa_review_fast_path():
    prompt = build_skill_prompt(
        skill_id="qa-file-reviewer",
        user_message="review this",
        file_names=["sample.docx"],
    )

    assert (
        'mkdir -p output && python .claude/skills/qa-file-reviewer/scripts/run_qa_review.py '
        '"sample.docx" output --with-comments --original-filename "sample.docx"'
    ) in prompt
    assert "Do not list or read staged skill files before running this command." in prompt


def test_build_skill_prompt_frontloads_baoyu_translate_fast_path():
    prompt = build_skill_prompt(
        skill_id="baoyu-translate",
        user_message="translate this to English",
        file_names=["sample.docx"],
    )

    assert (
        'mkdir -p output && python .claude/skills/baoyu-translate/scripts/run_translation.py '
        '"sample.docx" output --target-language "English" --original-filename "sample.docx"'
    ) in prompt
    assert "Do not list or read staged skill files before running this command." in prompt


@pytest.mark.asyncio
async def test_sdk_runner_deduplicates_result_message(monkeypatch, tmp_path):
    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class ResultMessage:
        session_id = "sdk-session"
        usage = {}
        model_usage = {}
        result = "hello from sdk"
        is_error = False
        errors = []
        stop_reason = None

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    async def query(prompt, options):
        yield AssistantMessage([TextBlock("hello from sdk")])
        yield ResultMessage()

    current_settings = type(
        "S",
        (),
        {
            "claude_agent_sdk_enabled": True,
            "anthropic_base_url": "",
            "anthropic_auth_token": "",
            "anthropic_model": "",
            "openai_api_key": "",
            "claude_agent_model": "deepseek-v4-flash",
            "claude_agent_sdk_skills": "",
            "claude_agent_sdk_timeout_seconds": 5,
        },
    )()
    fake_sdk = types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        ResultMessage=ResultMessage,
        TextBlock=TextBlock,
        query=query,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", lambda: current_settings)
    result = await run_claude_agent_sdk(prompt="hello", cwd=tmp_path, skill_id="general-chat")

    assert result.message == "hello from sdk"


@pytest.mark.asyncio
async def test_sdk_runner_passes_staged_skill_names(monkeypatch, tmp_path):
    captured = {}

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class ResultMessage:
        session_id = "sdk-session"
        usage = {}
        model_usage = {}
        result = "ok"
        is_error = False
        errors = []
        stop_reason = None

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            captured.update(kwargs)

    async def query(prompt, options):
        yield AssistantMessage([TextBlock("ok")])
        yield ResultMessage()

    current_settings = type(
        "S",
        (),
        {
            "claude_agent_sdk_enabled": True,
            "anthropic_base_url": "",
            "anthropic_auth_token": "",
            "anthropic_model": "",
            "openai_api_key": "",
            "claude_agent_model": "deepseek-v4-flash",
            "claude_agent_sdk_skills": "legacy-skill",
            "claude_agent_sdk_timeout_seconds": 5,
        },
    )()
    fake_sdk = types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        ResultMessage=ResultMessage,
        TextBlock=TextBlock,
        query=query,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", lambda: current_settings)
    result = await run_claude_agent_sdk(
        prompt="hello",
        cwd=tmp_path,
        skill_id="general-chat",
        skills=["qa-file-reviewer"],
    )

    assert result.message == "ok"
    assert captured["skills"] == ["qa-file-reviewer"]
    assert captured["permission_mode"] == "dontAsk"
    assert captured["tools"] == ["Read", "Glob", "LS", "Bash"]
    assert captured["allowed_tools"] == ["Read", "Glob", "LS"]
    assert captured["disallowed_tools"] == ["Write", "Edit", "NotebookEdit"]
    assert callable(captured["can_use_tool"])


@pytest.mark.asyncio
async def test_sdk_runner_uses_run_model_override(monkeypatch, tmp_path):
    captured = {}

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class ResultMessage:
        session_id = "sdk-session"
        usage = {}
        model_usage = {}
        result = "ok"
        is_error = False
        errors = []
        stop_reason = None

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            captured.update(kwargs)

    async def query(prompt, options):
        yield AssistantMessage([TextBlock("ok")])
        yield ResultMessage()

    current_settings = type(
        "S",
        (),
        {
            "claude_agent_sdk_enabled": True,
            "anthropic_base_url": "",
            "anthropic_auth_token": "",
            "anthropic_model": "deepseek-v4-flash",
            "openai_api_key": "",
            "claude_agent_model": "deepseek-v4-flash",
            "claude_agent_sdk_skills": "",
            "claude_agent_sdk_timeout_seconds": 5,
        },
    )()
    fake_sdk = types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        ResultMessage=ResultMessage,
        TextBlock=TextBlock,
        query=query,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", lambda: current_settings)

    result = await run_claude_agent_sdk(
        prompt="hello",
        cwd=tmp_path,
        skill_id="general-chat",
        model_id="deepseek-v4-pro",
    )

    assert result.message == "ok"
    assert captured["model"] == "deepseek-v4-pro"


@pytest.mark.asyncio
async def test_sdk_runner_uses_streaming_prompt_for_permission_callback(monkeypatch, tmp_path):
    captured = {}

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class ResultMessage:
        session_id = "sdk-session"
        usage = {}
        model_usage = {}
        result = "ok"
        is_error = False
        errors = []
        stop_reason = None

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    async def query(prompt, options):
        captured["prompt_is_stream"] = hasattr(prompt, "__aiter__") and not isinstance(prompt, str)
        captured["prompt_messages"] = []
        if captured["prompt_is_stream"]:
            async for message in prompt:
                captured["prompt_messages"].append(message)
        yield AssistantMessage([TextBlock("ok")])
        yield ResultMessage()

    current_settings = type(
        "S",
        (),
        {
            "claude_agent_sdk_enabled": True,
            "anthropic_base_url": "",
            "anthropic_auth_token": "",
            "anthropic_model": "",
            "openai_api_key": "",
            "claude_agent_model": "deepseek-v4-flash",
            "claude_agent_sdk_skills": "",
            "claude_agent_sdk_timeout_seconds": 5,
            "claude_agent_sdk_max_turns": 12,
            "claude_agent_sdk_effort": "xhigh",
            "claude_agent_sdk_max_thinking_tokens": 16384,
        },
    )()
    fake_sdk = types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        ResultMessage=ResultMessage,
        TextBlock=TextBlock,
        query=query,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", lambda: current_settings)

    result = await run_claude_agent_sdk(
        prompt="hello",
        cwd=tmp_path,
        skill_id="qa-file-reviewer",
        skills=["qa-file-reviewer"],
    )

    assert result.message == "ok"
    assert captured["max_turns"] == 12
    assert captured["effort"] == "xhigh"
    assert captured["max_thinking_tokens"] == 16384
    assert captured["prompt_is_stream"] is True
    assert captured["prompt_messages"] == [
        {
            "type": "user",
            "message": {"role": "user", "content": "hello"},
            "parent_tool_use_id": None,
            "session_id": "default",
        }
    ]


@pytest.mark.asyncio
async def test_sdk_runner_allows_only_platform_file_skill_bash_fast_paths(monkeypatch, tmp_path):
    captured = {}

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class ResultMessage:
        session_id = "sdk-session"
        usage = {}
        model_usage = {}
        result = "ok"
        is_error = False
        errors = []
        stop_reason = None

    class PermissionResultAllow:
        def __init__(self, behavior="allow", updated_input=None, updated_permissions=None):
            self.behavior = behavior
            self.updated_input = updated_input
            self.updated_permissions = updated_permissions

    class PermissionResultDeny:
        def __init__(self, behavior="deny", message="", interrupt=False):
            self.behavior = behavior
            self.message = message
            self.interrupt = interrupt

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            captured.update(kwargs)

    async def query(prompt, options):
        yield AssistantMessage([TextBlock("ok")])
        yield ResultMessage()

    current_settings = type(
        "S",
        (),
        {
            "claude_agent_sdk_enabled": True,
            "anthropic_base_url": "",
            "anthropic_auth_token": "",
            "anthropic_model": "",
            "openai_api_key": "",
            "claude_agent_model": "deepseek-v4-flash",
            "claude_agent_sdk_skills": "",
            "claude_agent_sdk_timeout_seconds": 5,
            "claude_agent_sdk_max_turns": 12,
        },
    )()
    fake_sdk = types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        PermissionResultAllow=PermissionResultAllow,
        PermissionResultDeny=PermissionResultDeny,
        ResultMessage=ResultMessage,
        TextBlock=TextBlock,
        query=query,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", lambda: current_settings)

    await run_claude_agent_sdk(
        prompt="hello",
        cwd=tmp_path,
        skill_id="qa-file-reviewer",
        skills=["qa-file-reviewer", "minimax-docx"],
    )

    can_use_tool = captured["can_use_tool"]
    allowed = await can_use_tool(
        "Bash",
        {
            "command": (
                "mkdir -p output && python "
                ".claude/skills/qa-file-reviewer/scripts/run_qa_review.py "
                '"sample.docx" output --with-comments --original-filename "sample.docx"'
            )
        },
        None,
    )
    workspace = tmp_path.as_posix()
    allowed_absolute = await can_use_tool(
        "Bash",
        {
            "command": (
                f'mkdir -p "{workspace}/output" && python '
                f'"{workspace}/.claude/skills/qa-file-reviewer/scripts/run_qa_review.py" '
                f'"{workspace}/sample.docx" "{workspace}/output" '
                '--with-comments --original-filename "sample.docx"'
            )
        },
        None,
    )
    allowed_redirect = await can_use_tool(
        "Bash",
        {
            "command": (
                "python3 "
                ".claude/skills/qa-file-reviewer/scripts/run_qa_review.py "
                '"sample.docx" output --with-comments --original-filename "sample.docx" 2>&1'
            )
        },
        None,
    )
    allowed_preflight_ls = await can_use_tool(
        "Bash",
        {
            "command": (
                "ls -la .claude/skills/minimax-docx/docx_engine.py "
                ".claude/skills/qa-file-reviewer/scripts/run_qa_review.py"
            )
        },
        None,
    )
    allowed_baoyu_translate = await can_use_tool(
        "Bash",
        {
            "command": (
                "mkdir -p output && python "
                ".claude/skills/baoyu-translate/scripts/run_translation.py "
                '"sample.docx" output --target-language "English" --original-filename "sample.docx"'
            )
        },
        None,
    )
    allowed_baoyu_translate_parenthesized_filename = await can_use_tool(
        "Bash",
        {
            "command": (
                "mkdir -p output && python "
                ".claude/skills/baoyu-translate/scripts/run_translation.py "
                '"TP(G)-AD-IP166E-1-026 IP166E PPQ_-_ -_ - _-hy.docx" output '
                '--target-language "English" --original-filename '
                '"TP(G)-AD-IP166E-1-026 IP166E PPQ_-_ -_ - _-hy.docx"'
            )
        },
        None,
    )
    unsafe_baoyu_target = await can_use_tool(
        "Bash",
        {
            "command": (
                "mkdir -p output && python "
                ".claude/skills/baoyu-translate/scripts/run_translation.py "
                '"sample.docx" output --target-language "Klingon" --original-filename "sample.docx"'
            )
        },
        None,
    )
    unsafe = await can_use_tool("Bash", {"command": "cat /etc/passwd"}, None)
    unsafe_preflight_ls = await can_use_tool(
        "Bash",
        {
            "command": (
                "ls -la .claude/skills/qa-file-reviewer/scripts/run_qa_review.py "
                "/etc/passwd"
            )
        },
        None,
    )
    expansion = await can_use_tool(
        "Bash",
        {
            "command": (
                "mkdir -p output && python "
                ".claude/skills/qa-file-reviewer/scripts/run_qa_review.py "
                '"$(touch pwned).docx" output --with-comments --original-filename "sample.docx"'
            )
        },
        None,
    )
    separator = await can_use_tool(
        "Bash",
        {
            "command": (
                "mkdir -p output && python "
                ".claude/skills/qa-file-reviewer/scripts/run_qa_review.py "
                '"sample;touch-pwned.docx" output --with-comments --original-filename "sample.docx"'
            )
        },
        None,
    )
    outside = await can_use_tool(
        "Bash",
        {
            "command": (
                "python .claude/skills/qa-file-reviewer/scripts/run_qa_review.py "
                '"/tmp/outside.docx" output --with-comments --original-filename "outside.docx"'
            )
        },
        None,
    )

    assert allowed.behavior == "allow"
    assert "$(touch" not in allowed.updated_input["command"]
    assert allowed_absolute.behavior == "allow"
    assert allowed_absolute.updated_input["command"].startswith("mkdir -p ")
    assert allowed_redirect.behavior == "allow"
    assert ">" not in allowed_redirect.updated_input["command"]
    assert allowed_preflight_ls.behavior == "allow"
    assert allowed_preflight_ls.updated_input["command"].startswith("ls -la ")
    assert "minimax-docx" in allowed_preflight_ls.updated_input["command"]
    assert "qa-file-reviewer" in allowed_preflight_ls.updated_input["command"]
    assert allowed_baoyu_translate.behavior == "allow"
    assert "baoyu-translate" in allowed_baoyu_translate.updated_input["command"]
    assert allowed_baoyu_translate_parenthesized_filename.behavior == "allow"
    assert "TP(G)-AD-IP166E" in allowed_baoyu_translate_parenthesized_filename.updated_input["command"]
    assert unsafe_baoyu_target.behavior == "deny"
    assert unsafe.behavior == "deny"
    assert unsafe_preflight_ls.behavior == "deny"
    assert expansion.behavior == "deny"
    assert separator.behavior == "deny"
    assert "not permitted" in unsafe.message
    assert outside.behavior == "deny"


@pytest.mark.asyncio
async def test_sdk_runner_pre_tool_hook_gates_bash_before_permission_rules(monkeypatch, tmp_path):
    captured = {}

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class ResultMessage:
        session_id = "sdk-session"
        usage = {}
        model_usage = {}
        result = "ok"
        is_error = False
        errors = []
        stop_reason = None

    class HookMatcher:
        def __init__(self, matcher=None, hooks=None, timeout=None):
            self.matcher = matcher
            self.hooks = hooks or []
            self.timeout = timeout

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            captured.update(kwargs)

    async def query(prompt, options):
        yield AssistantMessage([TextBlock("ok")])
        yield ResultMessage()

    current_settings = type(
        "S",
        (),
        {
            "claude_agent_sdk_enabled": True,
            "anthropic_base_url": "",
            "anthropic_auth_token": "",
            "anthropic_model": "",
            "openai_api_key": "",
            "claude_agent_model": "deepseek-v4-flash",
            "claude_agent_sdk_skills": "",
            "claude_agent_sdk_timeout_seconds": 5,
            "claude_agent_sdk_max_turns": 12,
        },
    )()
    fake_sdk = types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        HookMatcher=HookMatcher,
        ResultMessage=ResultMessage,
        TextBlock=TextBlock,
        query=query,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", lambda: current_settings)

    await run_claude_agent_sdk(
        prompt="hello",
        cwd=tmp_path,
        skill_id="qa-file-reviewer",
        skills=["qa-file-reviewer", "minimax-docx"],
    )

    pre_tool_matcher = captured["hooks"]["PreToolUse"][0]
    assert pre_tool_matcher.matcher == "Bash"
    pre_tool_hook = pre_tool_matcher.hooks[0]

    allowed = await pre_tool_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    "mkdir -p output && python "
                    ".claude/skills/qa-file-reviewer/scripts/run_qa_review.py "
                    '"sample.docx" output --with-comments --original-filename "sample.docx"'
                )
            },
            "tool_use_id": "tool-safe",
        },
        "tool-safe",
        {},
    )
    denied_probe = await pre_tool_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "which python3"},
            "tool_use_id": "tool-probe",
        },
        "tool-probe",
        {},
    )
    allowed_preflight_ls = await pre_tool_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    "ls -la .claude/skills/minimax-docx/docx_engine.py "
                    ".claude/skills/qa-file-reviewer/scripts/run_qa_review.py"
                )
            },
            "tool_use_id": "tool-preflight",
        },
        "tool-preflight",
        {},
    )
    denied_expansion = await pre_tool_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    "python .claude/skills/qa-file-reviewer/scripts/run_qa_review.py "
                    '"$(touch pwned).docx" output --with-comments --original-filename "sample.docx"'
                )
            },
            "tool_use_id": "tool-expansion",
        },
        "tool-expansion",
        {},
    )

    assert allowed["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert allowed["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "updatedInput" in allowed["hookSpecificOutput"]
    assert allowed["hookSpecificOutput"]["updatedInput"]["command"].startswith("mkdir -p ")
    assert allowed_preflight_ls["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "preflight" in allowed_preflight_ls["hookSpecificOutput"]["permissionDecisionReason"]
    assert allowed_preflight_ls["hookSpecificOutput"]["updatedInput"]["command"].startswith("ls -la ")
    assert "minimax-docx" in allowed_preflight_ls["hookSpecificOutput"]["updatedInput"]["command"]
    assert "qa-file-reviewer" in allowed_preflight_ls["hookSpecificOutput"]["updatedInput"]["command"]
    assert denied_probe["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert denied_expansion["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "not permitted" in denied_probe["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.asyncio
async def test_sdk_runner_pre_tool_hook_routes_unsafe_bash_to_platform_permission_callback(monkeypatch, tmp_path):
    captured = {}
    permission_calls = []
    permission_results = [
        {
            "allowed": False,
            "reason": "tool_permission_required",
            "risk_level": "high",
            "write_capable": True,
            "permission_request_id": "tpr-deny",
        },
        {
            "allowed": True,
            "reason": "tool_permission_allowed",
            "risk_level": "high",
            "write_capable": True,
            "decision": "allow_for_run",
            "permission_request_id": "tpr-allow",
        },
    ]

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class ResultMessage:
        session_id = "sdk-session"
        usage = {}
        model_usage = {}
        result = "ok"
        is_error = False
        errors = []
        stop_reason = None

    class HookMatcher:
        def __init__(self, matcher=None, hooks=None, timeout=None):
            self.matcher = matcher
            self.hooks = hooks or []
            self.timeout = timeout

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            captured.update(kwargs)

    async def query(prompt, options):
        yield AssistantMessage([TextBlock("ok")])
        yield ResultMessage()

    async def on_tool_permission(request):
        permission_calls.append(request)
        return permission_results[len(permission_calls) - 1]

    current_settings = type(
        "S",
        (),
        {
            "claude_agent_sdk_enabled": True,
            "anthropic_base_url": "",
            "anthropic_auth_token": "",
            "anthropic_model": "",
            "openai_api_key": "",
            "claude_agent_model": "deepseek-v4-flash",
            "claude_agent_sdk_skills": "",
            "claude_agent_sdk_timeout_seconds": 5,
            "claude_agent_sdk_max_turns": 12,
        },
    )()
    fake_sdk = types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        HookMatcher=HookMatcher,
        ResultMessage=ResultMessage,
        TextBlock=TextBlock,
        query=query,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", lambda: current_settings)

    await run_claude_agent_sdk(
        prompt="hello",
        cwd=tmp_path,
        skill_id="general-chat",
        skills=[],
        on_tool_permission=on_tool_permission,
    )

    pre_tool_hook = captured["hooks"]["PreToolUse"][0].hooks[0]
    denied = await pre_tool_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "python write_business_system.py --id 123"},
            "tool_use_id": "tool-write-deny",
        },
        "tool-write-deny",
        {},
    )
    allowed = await pre_tool_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "python write_business_system.py --id 456"},
            "tool_use_id": "tool-write-allow",
        },
        "tool-write-allow",
        {},
    )

    assert permission_calls[0]["tool_name"] == "Bash"
    assert permission_calls[0]["tool_call_id"] == "tool-write-deny"
    assert permission_calls[0]["risk_level"] == "high"
    assert permission_calls[0]["write_capable"] is True
    assert "command" in permission_calls[0]["tool_input_keys"]
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert denied["hookSpecificOutput"]["permissionDecisionReason"] == "tool_permission_required"
    assert denied["hookSpecificOutput"]["permission_request_id"] == "tpr-deny"
    assert permission_calls[1]["tool_call_id"] == "tool-write-allow"
    assert allowed["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert allowed["hookSpecificOutput"]["permissionDecisionReason"] == "tool_permission_allowed"
    assert allowed["hookSpecificOutput"]["permission_request_id"] == "tpr-allow"


@pytest.mark.asyncio
async def test_claude_worker_sdk_permission_hook_creates_request_event_and_audit(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    calls = []

    async def fake_run_claude_agent_sdk(
        *,
        prompt,
        cwd,
        skill_id,
        skills,
        model_id=None,
        session_id=None,
        on_text,
        on_skill_use,
        on_tool_permission,
    ):
        gate = await on_tool_permission(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "python write_business_system.py --id 123"},
                "tool_call_id": "tool-write",
                "risk_level": "high",
                "write_capable": True,
                "reason": "Claude SDK requested Bash",
            }
        )
        calls.append(("gate", gate))
        return types.SimpleNamespace(
            used_sdk=True,
            message="allowed",
            session_id="sdk-session",
            usage={},
            error=None if gate["allowed"] else gate["reason"],
            used_skills=[],
            used_skills_source="",
        )

    async def get_exact_tool_permission_decision(conn, **kwargs):
        calls.append(("decision_lookup", kwargs))
        return None

    async def create_tool_permission_request(conn, **kwargs):
        calls.append(("request", kwargs))
        return {
            "id": "tpr-sdk",
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "session_id": kwargs["session_id"],
            "run_id": kwargs["run_id"],
            "trace_id": kwargs["trace_id"],
            "tool_id": kwargs["tool_id"],
            "tool_call_id": kwargs["tool_call_id"],
            "action": kwargs["action"],
            "risk_level": kwargs["risk_level"],
            "write_capable": kwargs["write_capable"],
            "status": "pending",
            "reason": kwargs["reason"],
            "request_payload_json": kwargs["request_payload_json"],
        }

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-sdk"

    async def append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-sdk"

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    workspace = tmp_path / "workspaces" / "default" / "run_1"
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr("app.executors.claude_agent_worker.run_claude_agent_sdk", fake_run_claude_agent_sdk)
    monkeypatch.setattr("app.executors.claude_agent_worker.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.get_exact_tool_permission_decision",
        get_exact_tool_permission_decision,
        raising=False,
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.create_tool_permission_request",
        create_tool_permission_request,
        raising=False,
    )
    monkeypatch.setattr("app.executors.claude_agent_worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.executors.claude_agent_worker.repositories.append_audit_log", append_audit_log)

    result = await adapter._try_run_sdk(
        payload(trace_id="trace-sdk"),
        workspace=workspace,
        file_names=[],
        prompt="hello",
        staged_skill_names=[],
    )

    assert result.error == "tool_permission_required"
    command = "python write_business_system.py --id 123"
    lookup_call = next(item[1] for item in calls if item[0] == "decision_lookup")
    assert lookup_call["tenant_id"] == "default"
    assert lookup_call["user_id"] == "user-a"
    assert lookup_call["run_id"] == "run_1"
    assert lookup_call["tool_id"] == "claude-sdk:Bash"
    assert lookup_call["action"] == "execute"
    assert lookup_call["tool_call_id"] == "tool-write"
    assert lookup_call["request_payload_json"]["command_sha256"] == hashlib.sha256(command.encode("utf-8")).hexdigest()
    request_call = next(item[1] for item in calls if item[0] == "request")
    assert request_call["tool_id"] == "claude-sdk:Bash"
    assert request_call["tool_call_id"] == "tool-write"
    assert request_call["risk_level"] == "high"
    assert request_call["write_capable"] is True
    assert request_call["request_payload_json"] == {
        "source": "claude_agent_sdk_hook",
        "tool_name": "Bash",
        "tool_input_keys": ["command"],
        "command_length": len(command),
        "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
    }
    event_call = next(item[1] for item in calls if item[0] == "event")
    assert event_call["event_type"] == "tool_permission_requested"
    assert event_call["payload"] == {
        "visible_to_user": True,
        "permission_request_id": "tpr-sdk",
        "tool_id": "claude-sdk:Bash",
        "tool_call_id": "tool-write",
        "action": "execute",
        "risk_level": "high",
        "write_capable": True,
        "reason": "Claude SDK requested Bash",
        "status": "pending",
    }
    audit_call = next(item[1] for item in calls if item[0] == "audit")
    assert audit_call["action"] == "claude_sdk_tool_policy_denied"
    assert audit_call["payload_json"]["reason"] == "tool_permission_required"
    assert calls[-1][0] == "gate"
    assert calls[-1][1]["permission_request_id"] == "tpr-sdk"


@pytest.mark.asyncio
async def test_claude_worker_uses_runtime_model_value_for_sdk(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    captured = {}

    async def fake_run_claude_agent_sdk(
        *,
        prompt,
        cwd,
        skill_id,
        skills,
        model_id=None,
        session_id=None,
        on_text,
        on_skill_use,
        on_tool_permission,
    ):
        captured["model_id"] = model_id
        return FakeQueryResult()

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    workspace = tmp_path / "workspaces" / "default" / "run_1"
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr("app.executors.claude_agent_worker.run_claude_agent_sdk", fake_run_claude_agent_sdk)

    result = await adapter._try_run_sdk(
        payload(
            trace_id="trace-sdk",
            model_id="pro-tier",
            model_value="deepseek-v4-pro",
        ),
        workspace=workspace,
        file_names=[],
        prompt="hello",
        staged_skill_names=[],
    )

    assert result.error is None
    assert captured["model_id"] == "deepseek-v4-pro"


@pytest.mark.asyncio
async def test_claude_worker_sdk_permission_hook_allows_existing_decision(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    calls = []

    async def fake_run_claude_agent_sdk(
        *,
        prompt,
        cwd,
        skill_id,
        skills,
        model_id=None,
        session_id=None,
        on_text,
        on_skill_use,
        on_tool_permission,
    ):
        gate = await on_tool_permission(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "python write_business_system.py --id 456"},
                "tool_call_id": "tool-write",
                "risk_level": "high",
                "write_capable": True,
                "reason": "Claude SDK requested Bash",
            }
        )
        calls.append(("gate", gate))
        return types.SimpleNamespace(
            used_sdk=True,
            message="allowed",
            session_id="sdk-session",
            usage={},
            error=None if gate["allowed"] else gate["reason"],
            used_skills=[],
            used_skills_source="",
        )

    async def get_exact_tool_permission_decision(conn, **kwargs):
        calls.append(("decision_lookup", kwargs))
        command_hash = hashlib.sha256("python write_business_system.py --id 456".encode("utf-8")).hexdigest()
        return {
            "id": "tpr-allow",
            "decision": "allow_for_run",
            "tool_call_id": "tool-write",
            "request_payload_json": {"command_sha256": command_hash},
        }

    async def create_tool_permission_request(conn, **kwargs):
        raise AssertionError("existing allow decision must not create another request")

    async def append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-sdk"

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    workspace = tmp_path / "workspaces" / "default" / "run_1"
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr("app.executors.claude_agent_worker.run_claude_agent_sdk", fake_run_claude_agent_sdk)
    monkeypatch.setattr("app.executors.claude_agent_worker.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.get_exact_tool_permission_decision",
        get_exact_tool_permission_decision,
        raising=False,
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.create_tool_permission_request",
        create_tool_permission_request,
        raising=False,
    )
    monkeypatch.setattr("app.executors.claude_agent_worker.repositories.append_audit_log", append_audit_log)

    result = await adapter._try_run_sdk(
        payload(trace_id="trace-sdk"),
        workspace=workspace,
        file_names=[],
        prompt="hello",
        staged_skill_names=[],
    )

    assert result.error is None
    assert calls[-1] == (
        "gate",
        {
            "allowed": True,
            "reason": "tool_permission_allowed",
            "risk_level": "high",
            "write_capable": True,
            "decision": "allow_for_run",
            "permission_request_id": "tpr-allow",
        },
    )
    audit_call = next(item[1] for item in calls if item[0] == "audit")
    assert audit_call["action"] == "claude_sdk_tool_policy_allowed"
    assert audit_call["payload_json"]["permission_request_id"] == "tpr-allow"


@pytest.mark.asyncio
async def test_claude_worker_sdk_permission_hook_uses_exact_decision_lookup(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    calls = []

    async def fake_run_claude_agent_sdk(
        *,
        prompt,
        cwd,
        skill_id,
        skills,
        model_id=None,
        session_id=None,
        on_text,
        on_skill_use,
        on_tool_permission,
    ):
        gate = await on_tool_permission(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "python write_business_system.py --id 456"},
                "tool_call_id": "tool-write",
                "risk_level": "high",
                "write_capable": True,
                "reason": "Claude SDK requested Bash",
            }
        )
        calls.append(("gate", gate))
        return types.SimpleNamespace(
            used_sdk=True,
            message="allowed",
            session_id="sdk-session",
            usage={},
            error=None if gate["allowed"] else gate["reason"],
            used_skills=[],
            used_skills_source="",
        )

    async def get_exact_tool_permission_decision(conn, **kwargs):
        calls.append(("exact_decision_lookup", kwargs))
        command_hash = hashlib.sha256("python write_business_system.py --id 456".encode("utf-8")).hexdigest()
        assert kwargs["tool_call_id"] == "tool-write"
        assert kwargs["request_payload_json"]["command_sha256"] == command_hash
        return {
            "id": "tpr-allow",
            "decision": "allow_for_run",
            "tool_call_id": "tool-write",
            "request_payload_json": {"command_sha256": command_hash},
        }

    async def get_latest_tool_permission_decision(conn, **kwargs):
        raise AssertionError("Claude SDK tool permission must use exact decision lookup")

    async def create_tool_permission_request(conn, **kwargs):
        raise AssertionError("exact allow decision must not create another request")

    async def append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-sdk"

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    workspace = tmp_path / "workspaces" / "default" / "run_1"
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr("app.executors.claude_agent_worker.run_claude_agent_sdk", fake_run_claude_agent_sdk)
    monkeypatch.setattr("app.executors.claude_agent_worker.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.get_exact_tool_permission_decision",
        get_exact_tool_permission_decision,
        raising=False,
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.get_latest_tool_permission_decision",
        get_latest_tool_permission_decision,
        raising=False,
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.create_tool_permission_request",
        create_tool_permission_request,
        raising=False,
    )
    monkeypatch.setattr("app.executors.claude_agent_worker.repositories.append_audit_log", append_audit_log)

    result = await adapter._try_run_sdk(
        payload(trace_id="trace-sdk"),
        workspace=workspace,
        file_names=[],
        prompt="hello",
        staged_skill_names=[],
    )

    assert result.error is None
    assert any(item[0] == "exact_decision_lookup" for item in calls)
    assert calls[-1][0] == "gate"
    assert calls[-1][1]["allowed"] is True


@pytest.mark.asyncio
async def test_claude_worker_sdk_permission_hook_consumes_allow_once_decision(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    calls = []
    command = "python write_business_system.py --id 456"

    async def fake_run_claude_agent_sdk(
        *,
        prompt,
        cwd,
        skill_id,
        skills,
        model_id=None,
        session_id=None,
        on_text,
        on_skill_use,
        on_tool_permission,
    ):
        gate = await on_tool_permission(
            {
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "tool_call_id": "tool-write",
                "risk_level": "high",
                "write_capable": True,
                "reason": "Claude SDK requested Bash",
            }
        )
        calls.append(("gate", gate))
        return types.SimpleNamespace(
            used_sdk=True,
            message="allowed",
            session_id="sdk-session",
            usage={},
            error=None if gate["allowed"] else gate["reason"],
            used_skills=[],
            used_skills_source="",
        )

    async def get_exact_tool_permission_decision(conn, **kwargs):
        calls.append(("decision_lookup", kwargs))
        return {
            "id": "tpr-once",
            "decision": "allow_once",
            "tool_call_id": "tool-write",
            "request_payload_json": {},
        }

    async def consume_tool_permission_decision(conn, **kwargs):
        calls.append(("consume", kwargs))
        return {"id": kwargs["request_id"], "decision": "allow_once", "status": "consumed"}

    async def create_tool_permission_request(conn, **kwargs):
        raise AssertionError("existing allow_once decision must not create another request")

    async def append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-sdk"

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    workspace = tmp_path / "workspaces" / "default" / "run_1"
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr("app.executors.claude_agent_worker.run_claude_agent_sdk", fake_run_claude_agent_sdk)
    monkeypatch.setattr("app.executors.claude_agent_worker.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.get_exact_tool_permission_decision",
        get_exact_tool_permission_decision,
        raising=False,
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.consume_tool_permission_decision",
        consume_tool_permission_decision,
        raising=False,
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.create_tool_permission_request",
        create_tool_permission_request,
        raising=False,
    )
    monkeypatch.setattr("app.executors.claude_agent_worker.repositories.append_audit_log", append_audit_log)

    result = await adapter._try_run_sdk(
        payload(trace_id="trace-sdk"),
        workspace=workspace,
        file_names=[],
        prompt="hello",
        staged_skill_names=[],
    )

    assert result.error is None
    consume_calls = [item for item in calls if item[0] == "consume"]
    assert consume_calls, "allow_once Claude SDK decision must be consumed before returning allow"
    consume_call = consume_calls[0]
    gate_call = next(item for item in calls if item[0] == "gate")
    assert calls.index(consume_call) < calls.index(gate_call)
    assert consume_call[1] == {
        "tenant_id": "default",
        "user_id": "user-a",
        "run_id": "run_1",
        "request_id": "tpr-once",
    }
    assert gate_call[1] == {
        "allowed": True,
        "reason": "tool_permission_allowed",
        "risk_level": "high",
        "write_capable": True,
        "decision": "allow_once",
        "permission_request_id": "tpr-once",
    }
    audit_call = next(item[1] for item in calls if item[0] == "audit")
    assert audit_call["action"] == "claude_sdk_tool_policy_allowed"
    assert audit_call["payload_json"]["decision"] == "allow_once"
    assert audit_call["payload_json"]["permission_request_id"] == "tpr-once"


@pytest.mark.asyncio
async def test_claude_worker_sdk_permission_hook_fails_closed_when_allow_once_consumption_fails(
    monkeypatch,
    tmp_path,
):
    current_settings = settings(tmp_path, sdk_enabled=True)
    calls = []
    command = "python write_business_system.py --id 456"

    async def fake_run_claude_agent_sdk(
        *,
        prompt,
        cwd,
        skill_id,
        skills,
        model_id=None,
        session_id=None,
        on_text,
        on_skill_use,
        on_tool_permission,
    ):
        gate = await on_tool_permission(
            {
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "tool_call_id": "tool-write",
                "risk_level": "high",
                "write_capable": True,
                "reason": "Claude SDK requested Bash",
            }
        )
        calls.append(("gate", gate))
        return types.SimpleNamespace(
            used_sdk=True,
            message="",
            session_id="sdk-session",
            usage={},
            error=None if gate["allowed"] else gate["reason"],
            used_skills=[],
            used_skills_source="",
        )

    async def get_exact_tool_permission_decision(conn, **kwargs):
        calls.append(("decision_lookup", kwargs))
        return {
            "id": "tpr-once",
            "decision": "allow_once",
            "tool_call_id": "tool-write",
            "request_payload_json": {},
        }

    async def consume_tool_permission_decision(conn, **kwargs):
        calls.append(("consume", kwargs))
        return None

    async def create_tool_permission_request(conn, **kwargs):
        raise AssertionError("consumed allow_once decision must not create another request")

    async def append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-sdk"

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    workspace = tmp_path / "workspaces" / "default" / "run_1"
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr("app.executors.claude_agent_worker.run_claude_agent_sdk", fake_run_claude_agent_sdk)
    monkeypatch.setattr("app.executors.claude_agent_worker.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.get_exact_tool_permission_decision",
        get_exact_tool_permission_decision,
        raising=False,
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.consume_tool_permission_decision",
        consume_tool_permission_decision,
        raising=False,
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.create_tool_permission_request",
        create_tool_permission_request,
        raising=False,
    )
    monkeypatch.setattr("app.executors.claude_agent_worker.repositories.append_audit_log", append_audit_log)

    result = await adapter._try_run_sdk(
        payload(trace_id="trace-sdk"),
        workspace=workspace,
        file_names=[],
        prompt="hello",
        staged_skill_names=[],
    )

    assert result.error == "tool_permission_consumed_or_expired"
    gate_call = next(item for item in calls if item[0] == "gate")
    assert gate_call[1] == {
        "allowed": False,
        "reason": "tool_permission_consumed_or_expired",
        "risk_level": "high",
        "write_capable": True,
        "decision": "allow_once",
        "permission_request_id": "tpr-once",
    }
    denied_audit = next(item[1] for item in calls if item[0] == "audit")
    assert denied_audit["action"] == "claude_sdk_tool_policy_denied"
    assert denied_audit["payload_json"]["reason"] == "tool_permission_consumed_or_expired"
    assert denied_audit["payload_json"]["permission_request_id"] == "tpr-once"


@pytest.mark.asyncio
async def test_claude_worker_sdk_permission_hook_allows_run_decision_for_same_bash_command(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    calls = []
    command = "python write_business_system.py --id 789"
    command_hash = hashlib.sha256(command.encode("utf-8")).hexdigest()

    async def fake_run_claude_agent_sdk(
        *,
        prompt,
        cwd,
        skill_id,
        skills,
        model_id=None,
        session_id=None,
        on_text,
        on_skill_use,
        on_tool_permission,
    ):
        gate = await on_tool_permission(
            {
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "tool_call_id": "tool-current",
                "risk_level": "high",
                "write_capable": True,
                "reason": "Claude SDK requested Bash",
            }
        )
        calls.append(("gate", gate))
        return types.SimpleNamespace(
            used_sdk=True,
            message="allowed",
            session_id="sdk-session",
            usage={},
            error=None if gate["allowed"] else gate["reason"],
            used_skills=[],
            used_skills_source="",
        )

    async def get_exact_tool_permission_decision(conn, **kwargs):
        calls.append(("decision_lookup", kwargs))
        if kwargs.get("request_payload_json", {}).get("command_sha256") != command_hash:
            return None
        return {
            "id": "tpr-run",
            "decision": "allow_for_run",
            "tool_call_id": "tool-original",
            "request_payload_json": {"command_sha256": command_hash},
        }

    async def create_tool_permission_request(conn, **kwargs):
        raise AssertionError("same-command allow_for_run must not create another request")

    async def append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-sdk"

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    workspace = tmp_path / "workspaces" / "default" / "run_1"
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr("app.executors.claude_agent_worker.run_claude_agent_sdk", fake_run_claude_agent_sdk)
    monkeypatch.setattr("app.executors.claude_agent_worker.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.get_exact_tool_permission_decision",
        get_exact_tool_permission_decision,
        raising=False,
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.create_tool_permission_request",
        create_tool_permission_request,
        raising=False,
    )
    monkeypatch.setattr("app.executors.claude_agent_worker.repositories.append_audit_log", append_audit_log)

    result = await adapter._try_run_sdk(
        payload(trace_id="trace-sdk"),
        workspace=workspace,
        file_names=[],
        prompt="hello",
        staged_skill_names=[],
    )

    assert result.error is None
    assert calls[-1] == (
        "gate",
        {
            "allowed": True,
            "reason": "tool_permission_allowed",
            "risk_level": "high",
            "write_capable": True,
            "decision": "allow_for_run",
            "permission_request_id": "tpr-run",
        },
    )
    audit_call = next(item[1] for item in calls if item[0] == "audit")
    assert audit_call["payload_json"]["permission_request_id"] == "tpr-run"


@pytest.mark.asyncio
async def test_claude_worker_sdk_permission_hook_does_not_reuse_bash_decision_for_other_command(monkeypatch, tmp_path):
    current_settings = settings(tmp_path, sdk_enabled=True)
    calls = []

    async def fake_run_claude_agent_sdk(
        *,
        prompt,
        cwd,
        skill_id,
        skills,
        model_id=None,
        session_id=None,
        on_text,
        on_skill_use,
        on_tool_permission,
    ):
        gate = await on_tool_permission(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "python write_business_system.py --id 789"},
                "tool_call_id": "tool-current",
                "risk_level": "high",
                "write_capable": True,
                "reason": "Claude SDK requested Bash",
            }
        )
        calls.append(("gate", gate))
        return types.SimpleNamespace(
            used_sdk=True,
            message="",
            session_id="sdk-session",
            usage={},
            error=gate["reason"],
            used_skills=[],
            used_skills_source="",
        )

    async def get_exact_tool_permission_decision(conn, **kwargs):
        calls.append(("decision_lookup", kwargs))
        other_command_hash = hashlib.sha256("python write_business_system.py --id 456".encode("utf-8")).hexdigest()
        return {
            "id": "tpr-other",
            "decision": "allow_for_run",
            "tool_call_id": "tool-other",
            "request_payload_json": {"command_sha256": other_command_hash},
        }

    async def create_tool_permission_request(conn, **kwargs):
        calls.append(("request", kwargs))
        return {
            "id": "tpr-current",
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "session_id": kwargs["session_id"],
            "run_id": kwargs["run_id"],
            "trace_id": kwargs["trace_id"],
            "tool_id": kwargs["tool_id"],
            "tool_call_id": kwargs["tool_call_id"],
            "action": kwargs["action"],
            "risk_level": kwargs["risk_level"],
            "write_capable": kwargs["write_capable"],
            "status": "pending",
            "reason": kwargs["reason"],
            "request_payload_json": kwargs["request_payload_json"],
        }

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-sdk"

    async def append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-sdk"

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    workspace = tmp_path / "workspaces" / "default" / "run_1"
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr("app.executors.claude_agent_worker.run_claude_agent_sdk", fake_run_claude_agent_sdk)
    monkeypatch.setattr("app.executors.claude_agent_worker.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.get_exact_tool_permission_decision",
        get_exact_tool_permission_decision,
        raising=False,
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.create_tool_permission_request",
        create_tool_permission_request,
        raising=False,
    )
    monkeypatch.setattr("app.executors.claude_agent_worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.executors.claude_agent_worker.repositories.append_audit_log", append_audit_log)

    result = await adapter._try_run_sdk(
        payload(trace_id="trace-sdk"),
        workspace=workspace,
        file_names=[],
        prompt="hello",
        staged_skill_names=[],
    )

    assert result.error == "tool_permission_required"
    request_call = next(item[1] for item in calls if item[0] == "request")
    assert request_call["tool_call_id"] == "tool-current"
    assert calls[-1][0] == "gate"
    assert calls[-1][1]["allowed"] is False
    assert calls[-1][1]["permission_request_id"] == "tpr-current"


@pytest.mark.asyncio
async def test_claude_worker_sdk_permission_hook_does_not_reuse_bash_deny_for_other_tool_call(
    monkeypatch,
    tmp_path,
):
    current_settings = settings(tmp_path, sdk_enabled=True)
    calls = []
    command = "python write_business_system.py --id 999"

    async def fake_run_claude_agent_sdk(
        *,
        prompt,
        cwd,
        skill_id,
        skills,
        model_id=None,
        session_id=None,
        on_text,
        on_skill_use,
        on_tool_permission,
    ):
        gate = await on_tool_permission(
            {
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "tool_call_id": "tool-current",
                "risk_level": "high",
                "write_capable": True,
                "reason": "Claude SDK requested Bash",
            }
        )
        calls.append(("gate", gate))
        return types.SimpleNamespace(
            used_sdk=True,
            message="",
            session_id="sdk-session",
            usage={},
            error=gate["reason"],
            used_skills=[],
            used_skills_source="",
        )

    async def get_exact_tool_permission_decision(conn, **kwargs):
        calls.append(("decision_lookup", kwargs))
        return {
            "id": "tpr-denied-other",
            "decision": "deny",
            "tool_call_id": "tool-other",
            "request_payload_json": {"command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest()},
        }

    async def create_tool_permission_request(conn, **kwargs):
        calls.append(("request", kwargs))
        return {
            "id": "tpr-current",
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "session_id": kwargs["session_id"],
            "run_id": kwargs["run_id"],
            "trace_id": kwargs["trace_id"],
            "tool_id": kwargs["tool_id"],
            "tool_call_id": kwargs["tool_call_id"],
            "action": kwargs["action"],
            "risk_level": kwargs["risk_level"],
            "write_capable": kwargs["write_capable"],
            "status": "pending",
            "reason": kwargs["reason"],
            "request_payload_json": kwargs["request_payload_json"],
        }

    async def append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-sdk"

    async def append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-sdk"

    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    workspace = tmp_path / "workspaces" / "default" / "run_1"
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: current_settings)
    monkeypatch.setattr("app.executors.claude_agent_worker.run_claude_agent_sdk", fake_run_claude_agent_sdk)
    monkeypatch.setattr("app.executors.claude_agent_worker.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.get_exact_tool_permission_decision",
        get_exact_tool_permission_decision,
        raising=False,
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.create_tool_permission_request",
        create_tool_permission_request,
        raising=False,
    )
    monkeypatch.setattr("app.executors.claude_agent_worker.repositories.append_event", append_event)
    monkeypatch.setattr("app.executors.claude_agent_worker.repositories.append_audit_log", append_audit_log)

    result = await adapter._try_run_sdk(
        payload(trace_id="trace-sdk"),
        workspace=workspace,
        file_names=[],
        prompt="hello",
        staged_skill_names=[],
    )

    assert result.error == "tool_permission_required"
    request_call = next(item[1] for item in calls if item[0] == "request")
    assert request_call["tool_call_id"] == "tool-current"
    assert calls[-1][0] == "gate"
    assert calls[-1][1] == {
        "allowed": False,
        "reason": "tool_permission_required",
        "risk_level": "high",
        "write_capable": True,
        "decision": "",
        "permission_request_id": "tpr-current",
    }


@pytest.mark.asyncio
async def test_sdk_runner_records_qa_skill_use_from_allowed_bash_fast_path(monkeypatch, tmp_path):
    captured = {}
    reported = []

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class ResultMessage:
        session_id = "sdk-session"
        usage = {}
        model_usage = {}
        result = "ok"
        is_error = False
        errors = []
        stop_reason = None

    class HookMatcher:
        def __init__(self, matcher=None, hooks=None, timeout=None):
            self.matcher = matcher
            self.hooks = hooks or []
            self.timeout = timeout

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            captured.update(kwargs)

    async def query(prompt, options):
        pre_tool_hook = options.kwargs["hooks"]["PreToolUse"][0].hooks[0]
        await pre_tool_hook(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "ls -la .claude/skills/minimax-docx/docx_engine.py "
                        ".claude/skills/qa-file-reviewer/scripts/run_qa_review.py"
                    )
                },
                "tool_use_id": "tool-preflight",
            },
            "tool-preflight",
            {},
        )
        await pre_tool_hook(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "python .claude/skills/qa-file-reviewer/scripts/run_qa_review.py "
                        '"sample.docx" output --with-comments --original-filename "sample.docx"'
                    )
                },
                "tool_use_id": "tool-safe",
            },
            "tool-safe",
            {},
        )
        yield AssistantMessage([TextBlock("ok")])
        yield ResultMessage()

    async def on_skill_use(skill_name, metadata):
        reported.append(
            (
                skill_name,
                metadata["tool_name"],
                metadata["hook_event_name"],
                metadata["source"],
                metadata["tool_use_id"],
            )
        )

    current_settings = type(
        "S",
        (),
        {
            "claude_agent_sdk_enabled": True,
            "anthropic_base_url": "",
            "anthropic_auth_token": "",
            "anthropic_model": "",
            "openai_api_key": "",
            "claude_agent_model": "deepseek-v4-flash",
            "claude_agent_sdk_skills": "",
            "claude_agent_sdk_timeout_seconds": 5,
            "claude_agent_sdk_max_turns": 12,
        },
    )()
    fake_sdk = types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        HookMatcher=HookMatcher,
        ResultMessage=ResultMessage,
        TextBlock=TextBlock,
        query=query,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", lambda: current_settings)

    result = await run_claude_agent_sdk(
        prompt="hello",
        cwd=tmp_path,
        skill_id="qa-file-reviewer",
        skills=["qa-file-reviewer", "minimax-docx"],
        on_skill_use=on_skill_use,
    )

    assert result.used_skills == ["qa-file-reviewer"]
    assert result.used_skills_source == "executor_hook"
    assert reported == [("qa-file-reviewer", "Bash", "PreToolUse", "claude_agent_sdk_hook", "tool-safe")]


@pytest.mark.asyncio
async def test_sdk_runner_records_baoyu_skill_use_from_allowed_bash_fast_path(monkeypatch, tmp_path):
    captured = {}
    reported = []

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class ResultMessage:
        session_id = "sdk-session"
        usage = {}
        model_usage = {}
        result = "ok"
        is_error = False
        errors = []
        stop_reason = None

    class HookMatcher:
        def __init__(self, matcher=None, hooks=None, timeout=None):
            self.matcher = matcher
            self.hooks = hooks or []
            self.timeout = timeout

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            captured.update(kwargs)

    async def query(prompt, options):
        pre_tool_hook = options.kwargs["hooks"]["PreToolUse"][0].hooks[0]
        await pre_tool_hook(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "python .claude/skills/baoyu-translate/scripts/run_translation.py "
                        '"TP(G)-AD-IP166E-1-026 IP166E PPQ_-_ -_ - _-hy.docx" output '
                        '--target-language "English" --original-filename '
                        '"TP(G)-AD-IP166E-1-026 IP166E PPQ_-_ -_ - _-hy.docx"'
                    )
                },
                "tool_use_id": "tool-translate",
            },
            "tool-translate",
            {},
        )
        yield AssistantMessage([TextBlock("ok")])
        yield ResultMessage()

    async def on_skill_use(skill_name, metadata):
        reported.append(
            (
                skill_name,
                metadata["tool_name"],
                metadata["hook_event_name"],
                metadata["source"],
                metadata["tool_use_id"],
            )
        )

    current_settings = type(
        "S",
        (),
        {
            "claude_agent_sdk_enabled": True,
            "anthropic_base_url": "",
            "anthropic_auth_token": "",
            "anthropic_model": "",
            "openai_api_key": "",
            "claude_agent_model": "deepseek-v4-flash",
            "claude_agent_sdk_skills": "",
            "claude_agent_sdk_timeout_seconds": 5,
            "claude_agent_sdk_max_turns": 12,
        },
    )()
    fake_sdk = types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        HookMatcher=HookMatcher,
        ResultMessage=ResultMessage,
        TextBlock=TextBlock,
        query=query,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", lambda: current_settings)

    result = await run_claude_agent_sdk(
        prompt="hello",
        cwd=tmp_path,
        skill_id="baoyu-translate",
        skills=["baoyu-translate"],
        on_skill_use=on_skill_use,
    )

    assert result.used_skills == ["baoyu-translate"]
    assert result.used_skills_source == "executor_hook"
    assert reported == [("baoyu-translate", "Bash", "PreToolUse", "claude_agent_sdk_hook", "tool-translate")]


@pytest.mark.asyncio
async def test_sdk_runner_removes_project_settings_before_sdk_launch(monkeypatch, tmp_path):
    captured = {}
    project_claude_dir = tmp_path / ".claude"
    skills_dir = project_claude_dir / "skills" / "qa-file-reviewer"
    skills_dir.mkdir(parents=True)
    (project_claude_dir / "settings.json").write_text('{"permissions":{"allow":["Bash"]}}')
    (project_claude_dir / "settings.local.json").write_text('{"permissions":{"allow":["Bash"]}}')

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class ResultMessage:
        session_id = "sdk-session"
        usage = {}
        model_usage = {}
        result = "ok"
        is_error = False
        errors = []
        stop_reason = None

    class HookMatcher:
        def __init__(self, matcher=None, hooks=None, timeout=None):
            self.matcher = matcher
            self.hooks = hooks or []
            self.timeout = timeout

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    async def query(prompt, options):
        assert not (project_claude_dir / "settings.json").exists()
        assert not (project_claude_dir / "settings.local.json").exists()
        assert skills_dir.is_dir()
        yield AssistantMessage([TextBlock("ok")])
        yield ResultMessage()

    current_settings = type(
        "S",
        (),
        {
            "claude_agent_sdk_enabled": True,
            "anthropic_base_url": "",
            "anthropic_auth_token": "",
            "anthropic_model": "",
            "openai_api_key": "",
            "claude_agent_model": "deepseek-v4-flash",
            "claude_agent_sdk_skills": "",
            "claude_agent_sdk_timeout_seconds": 5,
            "claude_agent_sdk_max_turns": 12,
        },
    )()
    fake_sdk = types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        HookMatcher=HookMatcher,
        ResultMessage=ResultMessage,
        TextBlock=TextBlock,
        query=query,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", lambda: current_settings)

    result = await run_claude_agent_sdk(
        prompt="hello",
        cwd=tmp_path,
        skill_id="qa-file-reviewer",
        skills=["qa-file-reviewer"],
    )

    assert result.message == "ok"
    assert captured["setting_sources"] == ["project"]


@pytest.mark.asyncio
async def test_sdk_runner_records_skill_use_from_sdk_hook(monkeypatch, tmp_path):
    captured = {}
    reported = []

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class ResultMessage:
        session_id = "sdk-session"
        usage = {}
        model_usage = {}
        result = "ok"
        is_error = False
        errors = []
        stop_reason = None

    class HookMatcher:
        def __init__(self, matcher=None, hooks=None, timeout=None):
            self.matcher = matcher
            self.hooks = hooks or []
            self.timeout = timeout

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            captured.update(kwargs)

    async def query(prompt, options):
        hook = options.kwargs["hooks"]["PostToolUse"][0].hooks[0]
        await hook(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Skill",
                "tool_input": {"skill": "unstaged-skill"},
                "tool_use_id": "tool-0",
            },
            "tool-0",
            {},
        )
        await hook(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Skill",
                "tool_input": {"skill": "qa-file-reviewer"},
                "tool_use_id": "tool-1",
            },
            "tool-1",
            {},
        )
        await hook(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Skill",
                "tool_input": {"skill": "qa-file-reviewer"},
                "tool_use_id": "tool-2",
            },
            "tool-2",
            {},
        )
        yield AssistantMessage([TextBlock("ok")])
        yield ResultMessage()

    async def on_skill_use(skill_name, metadata):
        reported.append((skill_name, metadata["tool_use_id"], metadata["source"]))

    current_settings = type(
        "S",
        (),
        {
            "claude_agent_sdk_enabled": True,
            "anthropic_base_url": "",
            "anthropic_auth_token": "",
            "anthropic_model": "",
            "openai_api_key": "",
            "claude_agent_model": "deepseek-v4-flash",
            "claude_agent_sdk_skills": "",
            "claude_agent_sdk_timeout_seconds": 5,
            "claude_agent_sdk_max_turns": 12,
        },
    )()
    fake_sdk = types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        HookMatcher=HookMatcher,
        ResultMessage=ResultMessage,
        TextBlock=TextBlock,
        query=query,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", lambda: current_settings)

    result = await run_claude_agent_sdk(
        prompt="hello",
        cwd=tmp_path,
        skill_id="general-chat",
        skills=["qa-file-reviewer", "minimax-docx"],
        on_skill_use=on_skill_use,
    )

    assert captured["hooks"]["PostToolUse"][0].matcher == "Skill"
    assert captured["setting_sources"] == ["project"]
    assert result.used_skills == ["qa-file-reviewer"]
    assert result.used_skills_source == "executor_hook"
    assert reported == [("qa-file-reviewer", "tool-1", "claude_agent_sdk_hook")]


@pytest.mark.asyncio
async def test_sdk_runner_preserves_skill_use_when_query_raises_after_hook(monkeypatch, tmp_path):
    captured = {}

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class ResultMessage:
        session_id = "sdk-session"
        usage = {}
        model_usage = {}
        result = "ok"
        is_error = False
        errors = []
        stop_reason = None

    class HookMatcher:
        def __init__(self, matcher=None, hooks=None, timeout=None):
            self.matcher = matcher
            self.hooks = hooks or []
            self.timeout = timeout

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            captured.update(kwargs)

    async def query(prompt, options):
        hook = options.kwargs["hooks"]["PostToolUse"][0].hooks[0]
        await hook(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Skill",
                "tool_input": {"skill": "qa-file-reviewer"},
                "tool_use_id": "tool-1",
            },
            "tool-1",
            {},
        )
        raise RuntimeError("sdk stream disconnected")
        yield ResultMessage()

    current_settings = type(
        "S",
        (),
        {
            "claude_agent_sdk_enabled": True,
            "anthropic_base_url": "",
            "anthropic_auth_token": "",
            "anthropic_model": "",
            "openai_api_key": "",
            "claude_agent_model": "deepseek-v4-flash",
            "claude_agent_sdk_skills": "",
            "claude_agent_sdk_timeout_seconds": 5,
            "claude_agent_sdk_max_turns": 12,
        },
    )()
    fake_sdk = types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        HookMatcher=HookMatcher,
        ResultMessage=ResultMessage,
        TextBlock=TextBlock,
        query=query,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", lambda: current_settings)

    result = await run_claude_agent_sdk(
        prompt="hello",
        cwd=tmp_path,
        skill_id="general-chat",
        skills=["qa-file-reviewer"],
    )

    assert result.used_sdk is True
    assert result.error == "sdk stream disconnected"
    assert result.used_skills == ["qa-file-reviewer"]
    assert result.used_skills_source == "executor_hook"


@pytest.mark.asyncio
async def test_sdk_runner_preserves_skill_use_when_timeout_fires_after_hook(monkeypatch, tmp_path):
    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class ResultMessage:
        session_id = "sdk-session"
        usage = {}
        model_usage = {}
        result = "ok"
        is_error = False
        errors = []
        stop_reason = None

    class HookMatcher:
        def __init__(self, matcher=None, hooks=None, timeout=None):
            self.matcher = matcher
            self.hooks = hooks or []
            self.timeout = timeout

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    async def query(prompt, options):
        hook = options.kwargs["hooks"]["PostToolUse"][0].hooks[0]
        await hook(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Skill",
                "tool_input": {"skill": "qa-file-reviewer"},
                "tool_use_id": "tool-1",
            },
            "tool-1",
            {},
        )
        await asyncio.sleep(1)
        yield ResultMessage()

    current_settings = type(
        "S",
        (),
        {
            "claude_agent_sdk_enabled": True,
            "anthropic_base_url": "",
            "anthropic_auth_token": "",
            "anthropic_model": "",
            "openai_api_key": "",
            "claude_agent_model": "deepseek-v4-flash",
            "claude_agent_sdk_skills": "",
            "claude_agent_sdk_timeout_seconds": 0.01,
            "claude_agent_sdk_max_turns": 12,
        },
    )()
    fake_sdk = types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        HookMatcher=HookMatcher,
        ResultMessage=ResultMessage,
        TextBlock=TextBlock,
        query=query,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", lambda: current_settings)

    result = await run_claude_agent_sdk(
        prompt="hello",
        cwd=tmp_path,
        skill_id="general-chat",
        skills=["qa-file-reviewer"],
    )

    assert result.used_sdk is True
    assert result.error == "claude_agent_sdk_timeout"
    assert result.used_skills == ["qa-file-reviewer"]
    assert result.used_skills_source == "executor_hook"


@pytest.mark.asyncio
async def test_sdk_runner_honors_explicit_full_access_tool_policy_override(monkeypatch, tmp_path):
    captured = {}
    permission_calls = []

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class ResultMessage:
        session_id = "sdk-session"
        usage = {}
        model_usage = {}
        result = "ok"
        is_error = False
        errors = []
        stop_reason = None

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            captured.update(kwargs)

    class HookMatcher:
        def __init__(self, matcher=None, hooks=None, timeout=None):
            self.matcher = matcher
            self.hooks = hooks or []
            self.timeout = timeout

    class PermissionResultAllow:
        def __init__(self, behavior="allow", updated_input=None, updated_permissions=None):
            self.behavior = behavior
            self.updated_input = updated_input
            self.updated_permissions = updated_permissions

    async def query(prompt, options):
        yield AssistantMessage([TextBlock("ok")])
        yield ResultMessage()

    async def on_tool_permission(request):
        permission_calls.append(request)
        return {
            "allowed": False,
            "reason": "tool_permission_required",
            "risk_level": "high",
            "write_capable": True,
            "permission_request_id": "unexpected",
        }

    current_settings = type(
        "S",
        (),
        {
            "claude_agent_sdk_enabled": True,
            "anthropic_base_url": "",
            "anthropic_auth_token": "",
            "anthropic_model": "",
            "openai_api_key": "",
            "claude_agent_model": "deepseek-v4-flash",
            "claude_agent_sdk_skills": "",
            "claude_agent_sdk_timeout_seconds": 5,
            "claude_agent_sdk_max_turns": 12,
            "claude_agent_permission_mode": "bypassPermissions",
            "claude_agent_allowed_tools": "Read,Write,Bash",
            "claude_agent_disallowed_tools": "Edit",
        },
    )()
    fake_sdk = types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        HookMatcher=HookMatcher,
        PermissionResultAllow=PermissionResultAllow,
        ResultMessage=ResultMessage,
        TextBlock=TextBlock,
        query=query,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", lambda: current_settings)

    result = await run_claude_agent_sdk(
        prompt="hello",
        cwd=tmp_path,
        skill_id="general-chat",
        skills=["qa-file-reviewer"],
        on_tool_permission=on_tool_permission,
    )

    assert result.message == "ok"
    assert captured["permission_mode"] == "dontAsk"
    assert captured["tools"] == ["Read", "Glob", "LS", "Bash", "Agent"]
    assert captured["allowed_tools"] == ["Read", "Glob", "LS", "Bash", "Agent"]
    assert captured["disallowed_tools"] == []
    assert callable(captured["can_use_tool"])
    can_use_tool = captured["can_use_tool"]
    allowed = await can_use_tool("Bash", {"command": "python custom_translate.py"}, None)
    assert allowed.behavior == "allow"
    agent_allowed = await can_use_tool("Agent", {"agent": "reference-fact-extraction"}, None)
    assert agent_allowed.behavior == "allow"
    pre_tool_hook = captured["hooks"]["PreToolUse"][0].hooks[0]
    pre_tool_result = await pre_tool_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "python custom_translate.py"},
            "tool_use_id": "tool-full-access",
        },
        "tool-full-access",
        {},
    )
    assert pre_tool_result["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "full access" in pre_tool_result["hookSpecificOutput"]["permissionDecisionReason"]
    assert permission_calls == []


@pytest.mark.asyncio
async def test_sandbox_brokered_policy_preserves_governed_tools_and_brokers_other_tools(monkeypatch, tmp_path):
    captured = {}
    permission_calls = []
    used_skill_events = []

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class ResultMessage:
        session_id = "sdk-session"
        usage = {}
        model_usage = {}
        result = "ok"
        is_error = False
        errors = []
        stop_reason = None

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class HookMatcher:
        def __init__(self, matcher=None, hooks=None, timeout=None):
            self.matcher = matcher
            self.hooks = hooks or []
            self.timeout = timeout

    class PermissionResultDeny:
        def __init__(self, behavior="deny", message="", interrupt=False):
            self.behavior = behavior
            self.message = message
            self.interrupt = interrupt

    async def query(prompt, options):
        yield AssistantMessage([TextBlock("ok")])
        yield ResultMessage()

    async def on_tool_permission(request):
        permission_calls.append(request)
        if request["tool_name"] == "WebFetch":
            raise TimeoutError("callback timed out")
        if request["tool_name"] == "WebSearch":
            return {"allowed": "false", "reason": "malformed truthy scalar"}
        return {
            "allowed": request["tool_name"] in {"Bash", "mcp__knowledge__search"},
            "reason": f"broker_{request['tool_name']}",
        }

    async def on_skill_use(skill_name, metadata):
        used_skill_events.append((skill_name, metadata["tool_use_id"]))

    current_settings = type(
        "S",
        (),
        {
            "claude_agent_sdk_enabled": True,
            "anthropic_base_url": "",
            "anthropic_auth_token": "",
            "anthropic_model": "",
            "openai_api_key": "",
            "claude_agent_model": "deepseek-v4-flash",
            "claude_agent_sdk_skills": "",
            "claude_agent_sdk_timeout_seconds": 5,
            "claude_agent_sdk_max_turns": 12,
            "claude_agent_permission_mode": "bypassPermissions",
            "claude_agent_allowed_tools": "Read,Write,Bash",
            "claude_agent_disallowed_tools": "Edit",
        },
    )()
    fake_sdk = types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        HookMatcher=HookMatcher,
        PermissionResultDeny=PermissionResultDeny,
        ResultMessage=ResultMessage,
        TextBlock=TextBlock,
        query=query,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", lambda: current_settings)
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner._build_context_retrieval_mcp_server",
        lambda *args, **kwargs: object(),
    )

    result = await run_claude_agent_sdk(
        prompt="hello",
        cwd=tmp_path,
        skill_id="general-chat",
        skills=["qa-file-reviewer"],
        on_skill_use=on_skill_use,
        on_tool_permission=on_tool_permission,
        execution_policy="sandbox_brokered",
    )

    assert result.message == "ok"
    assert captured["permission_mode"] == "dontAsk"
    internal_context_tools = [
        "read_session_messages",
        "read_context_file",
        "read_run_artifact",
        "stage_context_file_to_workspace",
        "search_memory",
    ]
    assert captured["allowed_tools"] == ["Read", "Glob", "LS", *internal_context_tools]
    assert captured["disallowed_tools"] == []
    side_effect_tools = ["Bash", "Write", "Edit", "NotebookEdit", "Agent", "WebFetch", "WebSearch"]
    assert captured["tools"] == ["Read", "Glob", "LS", *side_effect_tools]
    pre_tool_matchers = captured["hooks"]["PreToolUse"]
    assert len(pre_tool_matchers) == 1
    assert pre_tool_matchers[0].matcher is None
    assert set(captured["hooks"]) == {"PreToolUse", "PostToolUse", "PostToolUseFailure"}
    assert len(captured["hooks"]["PostToolUse"]) == 1
    assert captured["hooks"]["PostToolUse"][0].matcher == "Skill"
    broker_hook = pre_tool_matchers[0].hooks[0]

    decisions = {}
    brokered_tools = [*side_effect_tools, "mcp__knowledge__search"]
    for tool_name in brokered_tools:
        tool_input = {"command": "python .claude/skills/qa-file-reviewer/scripts/run_qa_review.py inputs/a.docx"} if tool_name == "Bash" else {"value": "x"}
        decisions[tool_name] = await broker_hook(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_use_id": f"tool-{tool_name}",
            },
            f"tool-{tool_name}",
            {},
        )

    assert [request["tool_name"] for request in permission_calls] == brokered_tools
    mcp_request = permission_calls[-1]
    assert mcp_request["tool_name"] == "mcp__knowledge__search"
    assert mcp_request["tool_call_id"] == "tool-mcp__knowledge__search"
    assert mcp_request["tool_input"] == {"value": "x"}
    assert sum(1 for request in permission_calls if request["tool_call_id"] == mcp_request["tool_call_id"]) == 1
    assert decisions["Bash"]["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert decisions["mcp__knowledge__search"]["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert decisions["Write"]["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert decisions["WebFetch"]["hookSpecificOutput"]["permissionDecisionReason"] == "tool_permission_broker_failed"
    assert decisions["WebSearch"]["hookSpecificOutput"]["permissionDecisionReason"] == "tool_permission_malformed_response"

    permission_count_before_governed_tools = len(permission_calls)
    allowed_skill = await broker_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Skill",
            "tool_input": {"skill": "qa-file-reviewer"},
            "tool_use_id": "tool-Skill-allow",
        },
        "tool-Skill-allow",
        {},
    )
    unknown_skill = await broker_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Skill",
            "tool_input": {"skill": "unknown-skill"},
            "tool_use_id": "tool-Skill-unknown",
        },
        "tool-Skill-unknown",
        {},
    )
    internal_context_read = await broker_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "read_context_file",
            "tool_input": {"file_id": "file-a"},
            "tool_use_id": "tool-context-read",
        },
        "tool-context-read",
        {},
    )
    assert allowed_skill["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert unknown_skill["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert internal_context_read == {}
    assert used_skill_events == []
    assert len(permission_calls) == permission_count_before_governed_tools
    post_skill_hook = captured["hooks"]["PostToolUse"][0].hooks[0]
    await post_skill_hook(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Skill",
            "tool_input": {"skill": "qa-file-reviewer"},
            "tool_use_id": "tool-Skill-allow",
        },
        "tool-Skill-allow",
        {},
    )
    assert used_skill_events == [("qa-file-reviewer", "tool-Skill-allow")]
    denied = await captured["can_use_tool"]("Bash", {"command": "echo local"}, None)
    assert denied.behavior == "deny"
    context_allowed = await captured["can_use_tool"]("read_context_file", {"file_id": "file-a"}, None)
    assert context_allowed.behavior == "allow"
    pinned_skill_allowed = await captured["can_use_tool"](
        "Skill", {"skill": "qa-file-reviewer"}, None
    )
    assert pinned_skill_allowed.behavior == "allow"
    unknown_skill_denied = await captured["can_use_tool"]("Skill", {"skill": "unknown-skill"}, None)
    assert unknown_skill_denied.behavior == "deny"

    captured.clear()
    await run_claude_agent_sdk(
        prompt="hello",
        cwd=tmp_path,
        skill_id="general-chat",
        skills=[],
        execution_policy="sandbox_brokered",
    )
    missing_broker_hook = captured["hooks"]["PreToolUse"][0].hooks[0]
    missing_broker = await missing_broker_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "mcp__knowledge__search",
            "tool_input": {"value": "x"},
            "tool_use_id": "tool-mcp-missing",
        },
        "tool-mcp-missing",
        {},
    )
    assert missing_broker["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.asyncio
async def test_legacy_delegate_does_not_emit_fallback_marker_when_enabled(monkeypatch, tmp_path):
    events = []
    adapter = ClaudeAgentWorkerAdapter(delegate=FakeDelegate())
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.get_settings",
        lambda: settings(tmp_path, sdk_enabled=False, legacy_fallback=True),
    )

    async def event_sink(**event):
        events.append(event)

    result = await adapter.submit_run(sandbox_writing_payload(), event_sink=event_sink)

    assert result.status == "failed"
    assert result.result["error_code"] == "claude_agent_sdk_disabled"
    assert result.result["delegate_used"] is False
    assert events == []


@pytest.mark.asyncio
async def test_sdk_runner_propagates_cancelled_error_from_stream_callback(monkeypatch, tmp_path):
    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class ResultMessage:
        session_id = "sdk-session"
        usage = {}
        model_usage = {}
        result = "done"
        is_error = False
        errors = []
        stop_reason = None

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    async def query(prompt, options):
        yield AssistantMessage([TextBlock("partial")])
        yield ResultMessage()

    async def on_text(delta):
        raise WorkerRunCancelled("platform cancel requested")

    current_settings = type(
        "S",
        (),
        {
            "claude_agent_sdk_enabled": True,
            "anthropic_base_url": "",
            "anthropic_auth_token": "",
            "anthropic_model": "",
            "openai_api_key": "",
            "claude_agent_model": "deepseek-v4-flash",
            "claude_agent_sdk_skills": "",
            "claude_agent_sdk_timeout_seconds": 5,
        },
    )()
    fake_sdk = types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        ResultMessage=ResultMessage,
        TextBlock=TextBlock,
        query=query,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", lambda: current_settings)

    with pytest.raises(WorkerRunCancelled, match="platform cancel requested"):
        await run_claude_agent_sdk(
            prompt="hello",
            cwd=tmp_path,
            skill_id="general-chat",
            on_text=on_text,
        )
