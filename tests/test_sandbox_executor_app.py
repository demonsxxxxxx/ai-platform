import asyncio
import functools
import gc
import hashlib
import json
import shutil
import threading
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.executors.claude_agent_sdk_runner import build_skill_prompt
from app.file_parser_contracts import MaterializedAttachmentFact, build_attachment_preprocessing_contract
from app.runtime.kernel_contracts import AgentEvent
from app.runtime.sandbox.contracts import ExecutorTaskRequest
from app.runtime.sandbox import executor_app
from app.runtime.sandbox.executor_app import _default_callback_sender, _default_executor_runner, create_executor_app
from app.tool_permission_lifecycle import tool_permission_budget


EXECUTOR_AUTH_TOKEN = "executor-secret"
TRUSTED_CALLBACK_BASE_URL = "http://ai-platform.test"
TRUSTED_CALLBACK_URL = f"{TRUSTED_CALLBACK_BASE_URL}/api/ai/runtime/callbacks/executor"


def task_payload(
    callback_url: str = TRUSTED_CALLBACK_URL,
    *,
    callback_base_url: str = TRUSTED_CALLBACK_BASE_URL,
) -> dict[str, object]:
    return {
        "session_id": "session-a",
        "run_id": "run-a",
        "prompt": "hello executor",
        "callback_url": callback_url,
        "callback_token_id": "cbt_run-a",
        "callback_token": "secret",
        "callback_base_url": callback_base_url,
        "sdk_session_id": None,
        "permission_mode": "default",
        "config": {
            "model": "deepseek-v4-flash",
            "browser_enabled": False,
            "resource_limits": {"max_seconds": 60},
            "skill_ids": [],
            "mcp_tool_ids": [],
            "input_files": [],
            "materialized_file_names": [],
        },
    }


def sensitive_task_payload(callback_url: str = TRUSTED_CALLBACK_URL) -> dict[str, object]:
    payload = task_payload(callback_url)
    payload["config"] = {
        "model": "deepseek-v4-flash",
        "browser_enabled": False,
        "resource_limits": {
            "max_seconds": 60,
            "headers": {"Authorization": "Bearer nested-secret"},
            "host_path": "/runtime/tenants/nested",
        },
        "skill_ids": ["safe-skill"],
        "mcp_tool_ids": [],
        "input_files": ["file-a", "/runtime/tenants/input-path"],
        "env_overrides": {"OPENAI_API_KEY": "secret-key"},
        "headers": {"Authorization": "Bearer secret"},
        "host_path": "/runtime/tenants/tenant-a/workspaces/workspace-a",
    }
    return payload


def auth_headers(token: str = EXECUTOR_AUTH_TOKEN) -> dict[str, str]:
    return {"X-AI-Platform-Executor-Credential": token}


def create_test_client(tmp_path, **kwargs) -> TestClient:
    return TestClient(
        create_executor_app(
            workspace_root=tmp_path,
            executor_auth_token=EXECUTOR_AUTH_TOKEN,
            expected_session_id="session-a",
            expected_run_id="run-a",
            trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
            **kwargs,
        )
    )


def write_minimal_docx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">
  <Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>
  <Default Extension=\"xml\" ContentType=\"application/xml\"/>
  <Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/>
</Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">
  <Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"word/document.xml\"/>
</Relationships>""",
        )
        archive.writestr(
            "word/document.xml",
            """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">
  <w:body><w:p><w:r><w:t>translated</w:t></w:r></w:p></w:body>
</w:document>""",
        )


def write_minimal_xlsx(path: Path, *, formula: str = "=1+2") -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet["A1"] = "metric"
    sheet["B1"] = "value"
    sheet["A2"] = "total"
    sheet["B2"] = formula
    workbook.save(path)
    workbook.close()


def selected_baoyu_skill_policy() -> list[dict[str, object]]:
    return [
        {
            "identity": identity,
            "registered": True,
            "declared": True,
            "active": True,
            "distributed": True,
            "identity_authorized": True,
            "object_authorized": True,
            "parameters_authorized": True,
            "allowed_skill_names": ["baoyu-translate"] if identity == "Skill" else [],
        }
        for identity in ("Bash", "Write", "Skill")
    ]


def skill_only_baoyu_policy() -> list[dict[str, object]]:
    return [subject for subject in selected_baoyu_skill_policy() if subject["identity"] == "Skill"]


def context_stage_policy() -> list[dict[str, object]]:
    return [
        {
            "identity": "mcp__ai-platform-context__stage_context_file_to_workspace",
            "registered": True,
            "declared": True,
            "active": True,
            "distributed": True,
            "identity_authorized": True,
            "object_authorized": True,
            "parameters_authorized": True,
            "risk_level": "medium",
            "write_capable": True,
            "allowed_parameter_keys": ["file_id", "max_bytes"],
            "required_parameter_keys": ["file_id"],
        }
    ]


def test_executor_health_returns_ready(tmp_path):
    client = create_test_client(tmp_path)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


@pytest.mark.asyncio
async def test_default_non_permission_callback_fails_fast(monkeypatch):
    observed = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"accepted": True}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, *, json, headers):
            return FakeResponse()

    def build_client(*, timeout):
        observed["timeout"] = timeout
        return FakeClient()

    monkeypatch.setattr("app.runtime.sandbox.executor_app.httpx.AsyncClient", build_client)

    assert await _default_callback_sender("https://control-plane.test/event", {"status": "running"}, "token-a") == {
        "accepted": True
    }
    assert observed["timeout"] == tool_permission_budget(120.0).non_permission_callback_timeout_seconds


def test_executor_runtime_identity_requires_lease_credential_and_returns_only_effective_ids(tmp_path, monkeypatch):
    monkeypatch.setattr("app.runtime.sandbox.executor_app.os.geteuid", lambda: 10001, raising=False)
    monkeypatch.setattr("app.runtime.sandbox.executor_app.os.getegid", lambda: 10001, raising=False)
    client = TestClient(create_executor_app(workspace_root=tmp_path, executor_auth_token="lease-secret"))

    assert client.get("/health/runtime-identity").status_code == 401
    assert client.get(
        "/health/runtime-identity",
        headers={"X-AI-Platform-Executor-Credential": "wrong"},
    ).status_code == 401
    response = client.get(
        "/health/runtime-identity",
        headers={"X-AI-Platform-Executor-Credential": "lease-secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"uid": 10001, "gid": 10001}


def test_executor_execute_posts_only_non_terminal_execution_callbacks(tmp_path, monkeypatch):
    callbacks = []

    class StubSettings:
        claude_agent_sdk_enabled = True

    async def fake_run_claude_agent_sdk(**kwargs):
        return type(
            "SdkResult",
            (),
            {
                "used_sdk": True,
                "message": "sdk final",
                "session_id": "sdk-session-a",
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "error": None,
                "used_skills": [],
                "used_skills_source": "",
            },
        )()

    def callback_sender(url, payload, token):
        callbacks.append((url, payload, token))
        return {"accepted": True}

    # keep this focused on the default happy path instead of the disabled fail-closed branch
    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())
    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", fake_run_claude_agent_sdk)
    client = create_test_client(tmp_path, callback_sender=callback_sender)

    response = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["run_id"] == "run-a"
    assert isinstance(body["executor_model_latency_ms"], int)
    assert isinstance(body["document_processing_latency_ms"], int)
    assert [item[1]["status"] for item in callbacks] == ["running", "running"]
    assert {item[2] for item in callbacks} == {"secret"}
    assert {item[1]["callback_token_id"] for item in callbacks} == {"cbt_run-a"}
    assert callbacks[0][1]["progress"] == 5
    assert callbacks[1][1]["progress"] == 99
    assert callbacks[1][1]["state_patch"]["stage"] == "executor_finished"


def test_executor_execute_streams_runner_events_and_phase_timings(tmp_path):
    callbacks = []

    async def executor_runner(request, workspace_root, emit_event):
        assert request.run_id == "run-a"
        assert workspace_root == Path(tmp_path)
        await emit_event(
            AgentEvent(type="assistant_delta", message="partial", payload={"delta": "partial"})
        )
        await emit_event(
            AgentEvent(
                type="tool_call_started",
                message="Bash started",
                payload={"tool_name": "Bash", "tool_call_id": "tool-a"},
                admin_only=True,
            )
        )
        await emit_event(
            AgentEvent(
                type="artifact_created",
                message="Artifact uploaded",
                payload={"artifact_id": "artifact-a", "label": "result.txt"},
            )
        )
        return {
            "status": "completed",
            "message": "done",
            "sdk_session_id": "sdk-session-a",
            "sdk_usage": {"input_tokens": 2, "output_tokens": 3},
        }

    def callback_sender(url, payload, token):
        callbacks.append((url, payload, token))
        return {"accepted": True}

    client = create_test_client(
        tmp_path,
        callback_sender=callback_sender,
        executor_runner=executor_runner,
    )

    response = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["sdk_session_id"] == "sdk-session-a"
    assert body["sdk_usage"] == {"input_tokens": 2, "output_tokens": 3}
    assert isinstance(body["executor_first_token_latency_ms"], int)
    assert isinstance(body["executor_tool_call_latency_ms"], int)
    assert isinstance(body["artifact_upload_latency_ms"], int)
    assert [item[1]["status"] for item in callbacks] == [
        "running",
        "running",
        "running",
        "running",
        "running",
    ]
    assert callbacks[1][1]["events"][0]["type"] == "assistant_delta"
    assert callbacks[2][1]["events"][0]["type"] == "tool_call_started"
    assert callbacks[3][1]["events"][0]["type"] == "artifact_created"
    assert callbacks[-1][1]["sdk_session_id"] == "sdk-session-a"


def test_executor_execute_uses_claude_sdk_runner_when_enabled(tmp_path, monkeypatch):
    callbacks = []
    calls = {}

    class StubSettings:
        claude_agent_sdk_enabled = True

    async def fake_run_claude_agent_sdk(**kwargs):
        calls["cwd"] = kwargs["cwd"]
        calls["skill_id"] = kwargs["skill_id"]
        calls["model_id"] = kwargs["model_id"]
        calls["skills"] = kwargs["skills"]
        calls["subjects"] = kwargs["tool_policy_subjects"]
        assert "on_tool_permission" not in kwargs
        await kwargs["on_text"]("sdk partial")
        return type(
            "SdkResult",
            (),
            {
                "used_sdk": True,
                "message": "sdk final",
                "session_id": "sdk-session-a",
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "error": None,
                "used_skills": [],
                "used_skills_source": "",
            },
        )()

    def callback_sender(url, payload, token):
        callbacks.append(payload)
        return {"accepted": True}

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())
    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", fake_run_claude_agent_sdk)

    payload = task_payload()
    payload["config"]["tool_policy_subjects"] = [
        {
            "identity": "Bash",
            "registered": True,
            "declared": True,
            "active": True,
            "distributed": True,
            "identity_authorized": True,
            "object_authorized": True,
            "parameters_authorized": True,
            "risk_level": "high",
            "write_capable": True,
            "allowed_parameter_keys": ["command"],
            "required_parameter_keys": ["command"],
        }
    ]
    client = create_test_client(tmp_path, callback_sender=callback_sender)

    response = client.post("/v1/tasks/execute", json=payload, headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["sdk_session_id"] == "sdk-session-a"
    assert calls["cwd"] == Path(tmp_path)
    assert calls["skill_id"] == "general-chat"
    assert calls["model_id"] == "deepseek-v4-flash"
    assert calls["skills"] == ["general-chat"]
    assert calls["subjects"][0]["identity"] == "Bash"
    assert any(
        event["type"] == "assistant_delta"
        for callback in callbacks
        for event in callback.get("events", [])
    )
    assert not any("tool-permission" in str(callback) for callback in callbacks)


def test_executor_runs_selected_authorized_baoyu_docx_skill_without_sdk_discretion(tmp_path, monkeypatch):
    class StubSettings:
        claude_agent_sdk_enabled = True

    workspace = Path(tmp_path)
    write_minimal_docx(workspace / "source.docx")
    script = workspace / ".claude" / "skills" / "baoyu-translate" / "scripts" / "run_translation.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        """import shutil
import sys
from pathlib import Path

source = Path(sys.argv[1])
output = Path(sys.argv[2])
output.mkdir(parents=True, exist_ok=True)
shutil.copyfile(source, output / \"translated.docx\")
(output / \"target-language.txt\").write_text(
    sys.argv[sys.argv.index(\"--target-language\") + 1], encoding=\"utf-8\"
)
""",
        encoding="utf-8",
    )

    async def sdk_must_not_run(**_kwargs):
        raise AssertionError("selected file Skill must not be left to SDK discretion")

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())
    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", sdk_must_not_run)

    payload = task_payload()
    payload["prompt"] = build_skill_prompt(
        skill_id="baoyu-translate",
        user_message="请将此文档翻译为中文",
        file_names=["source.docx"],
    )
    payload["config"]["skill_ids"] = ["baoyu-translate"]
    payload["config"]["materialized_file_names"] = ["source.docx"]
    payload["config"]["tool_policy_subjects"] = selected_baoyu_skill_policy()
    client = create_test_client(tmp_path, callback_sender=lambda url, payload, token: {"accepted": True})

    response = client.post("/v1/tasks/execute", json=payload, headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["sdk_used"] is False
    assert body["executor_mode"] == "platform_controlled_runner"
    assert body["used_skills"] == ["baoyu-translate"]
    assert body["used_skills_source"] == "platform_controlled_runner"
    assert (workspace / "output" / "translated.docx").is_file()
    assert "Controlled fast path" not in payload["prompt"]
    assert (workspace / "output" / "target-language.txt").read_text(encoding="utf-8") == "Chinese"


def test_executor_fails_closed_for_skill_only_authorization(tmp_path, monkeypatch):
    workspace = Path(tmp_path)
    write_minimal_docx(workspace / "source.docx")
    script = workspace / ".claude" / "skills" / "baoyu-translate" / "scripts" / "run_translation.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        """from pathlib import Path

Path("untrusted-runner-executed").write_text("unexpected", encoding="utf-8")
""",
        encoding="utf-8",
    )

    async def sdk_must_not_run(**_kwargs):
        raise AssertionError("incomplete controlled authorization must not fall back to SDK")

    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", sdk_must_not_run)
    payload = task_payload()
    payload["config"]["skill_ids"] = ["baoyu-translate"]
    payload["config"]["materialized_file_names"] = ["source.docx"]
    payload["config"]["tool_policy_subjects"] = skill_only_baoyu_policy()
    client = create_test_client(tmp_path, callback_sender=lambda url, payload, token: {"accepted": True})

    response = client.post("/v1/tasks/execute", json=payload, headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "controlled_skill_authorization_incomplete"
    assert body["used_skills"] == []
    assert not (workspace / "untrusted-runner-executed").exists()


def test_executor_uses_minimal_secret_free_environment_for_controlled_runner(tmp_path, monkeypatch):
    workspace = Path(tmp_path)
    write_minimal_docx(workspace / "source.docx")
    script = workspace / ".claude" / "skills" / "baoyu-translate" / "scripts" / "run_translation.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        """import json
import os
import shutil
import sys
from pathlib import Path

output = Path(sys.argv[2])
output.mkdir(parents=True, exist_ok=True)
json.dump(
    {key: os.environ.get(key) for key in ("ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "AI_PLATFORM_EXECUTOR_AUTH_TOKEN", "UNRELATED_SECRET")},
    (output / "child-env.json").open("w", encoding="utf-8"),
)
shutil.copyfile(sys.argv[1], output / "translated.docx")
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "model-token")
    monkeypatch.setenv("OPENAI_API_KEY", "api-key")
    monkeypatch.setenv("AI_PLATFORM_EXECUTOR_AUTH_TOKEN", "executor-token")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-inherit")
    payload = task_payload()
    payload["config"]["skill_ids"] = ["baoyu-translate"]
    payload["config"]["materialized_file_names"] = ["source.docx"]
    payload["config"]["tool_policy_subjects"] = selected_baoyu_skill_policy()
    client = create_test_client(tmp_path, callback_sender=lambda url, payload, token: {"accepted": True})

    response = client.post("/v1/tasks/execute", json=payload, headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert json.loads((workspace / "output" / "child-env.json").read_text(encoding="utf-8")) == {
        "ANTHROPIC_AUTH_TOKEN": None,
        "OPENAI_API_KEY": None,
        "AI_PLATFORM_EXECUTOR_AUTH_TOKEN": None,
        "UNRELATED_SECRET": None,
    }


def test_executor_uses_worker_materialized_docx_order_without_sorting(tmp_path):
    workspace = Path(tmp_path)
    write_minimal_docx(workspace / "z.docx")
    write_minimal_docx(workspace / "a.docx")
    script = workspace / ".claude" / "skills" / "baoyu-translate" / "scripts" / "run_translation.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        """import shutil
import sys
from pathlib import Path

output = Path(sys.argv[2])
output.mkdir(parents=True, exist_ok=True)
(output / "selected-input.txt").write_text(Path(sys.argv[1]).name, encoding="utf-8")
shutil.copyfile(sys.argv[1], output / "translated.docx")
""",
        encoding="utf-8",
    )
    payload = task_payload()
    payload["config"]["skill_ids"] = ["baoyu-translate"]
    payload["config"]["materialized_file_names"] = ["z.docx", "a.docx"]
    payload["config"]["tool_policy_subjects"] = selected_baoyu_skill_policy()
    client = create_test_client(tmp_path, callback_sender=lambda url, payload, token: {"accepted": True})

    response = client.post("/v1/tasks/execute", json=payload, headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert (workspace / "output" / "selected-input.txt").read_text(encoding="utf-8") == "z.docx"


def test_executor_rejects_unsafe_materialized_file_name_without_executing(tmp_path, monkeypatch):
    workspace = Path(tmp_path)
    write_minimal_docx(workspace / "source.docx")
    script = workspace / ".claude" / "skills" / "baoyu-translate" / "scripts" / "run_translation.py"
    script.parent.mkdir(parents=True)
    script.write_text("from pathlib import Path\nPath('unexpected').write_text('ran')\n", encoding="utf-8")

    async def sdk_must_not_run(**_kwargs):
        raise AssertionError("invalid materialized filename must fail closed")

    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", sdk_must_not_run)
    payload = task_payload()
    payload["config"]["skill_ids"] = ["baoyu-translate"]
    payload["config"]["materialized_file_names"] = ["../escape.docx"]
    payload["config"]["tool_policy_subjects"] = selected_baoyu_skill_policy()
    client = create_test_client(tmp_path, callback_sender=lambda url, payload, token: {"accepted": True})

    response = client.post("/v1/tasks/execute", json=payload, headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["error_code"] == "controlled_skill_input_name_invalid"
    assert not (workspace / "unexpected").exists()


def test_executor_runs_real_staged_baoyu_entrypoint_and_produces_translated_docx(tmp_path, monkeypatch):
    class StubSettings:
        claude_agent_sdk_enabled = True

    workspace = Path(tmp_path)
    write_minimal_docx(workspace / "source.docx")
    source_script = Path(__file__).parents[1] / "skills" / "baoyu-translate" / "scripts" / "run_translation.py"
    staged_script = workspace / ".claude" / "skills" / "baoyu-translate" / "scripts" / "run_translation.py"
    staged_script.parent.mkdir(parents=True)
    staged_script.write_bytes(source_script.read_bytes())

    async def sdk_must_not_run(**_kwargs):
        raise AssertionError("the real staged file Skill must not be left to SDK discretion")

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())
    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", sdk_must_not_run)

    payload = task_payload()
    payload["prompt"] = build_skill_prompt(
        skill_id="baoyu-translate",
        user_message="translate this document to English",
        file_names=["source.docx"],
    )
    payload["config"]["skill_ids"] = ["baoyu-translate"]
    payload["config"]["materialized_file_names"] = ["source.docx"]
    payload["config"]["tool_policy_subjects"] = selected_baoyu_skill_policy()
    client = create_test_client(tmp_path, callback_sender=lambda url, payload, token: {"accepted": True})

    response = client.post("/v1/tasks/execute", json=payload, headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    output_docx = workspace / "output" / "source_translated.docx"
    assert output_docx.is_file()
    with zipfile.ZipFile(output_docx) as archive:
        assert "word/document.xml" in archive.namelist()


def test_executor_runs_real_staged_qa_entrypoint_with_minimal_environment(tmp_path, monkeypatch):
    workspace = Path(tmp_path)
    write_minimal_docx(workspace / "source.docx")
    skills_root = Path(__file__).parents[1] / "skills"
    staged_skills = workspace / ".claude" / "skills"
    shutil.copytree(skills_root / "qa-file-reviewer", staged_skills / "qa-file-reviewer")
    shutil.copytree(skills_root / "minimax-docx", staged_skills / "minimax-docx")

    async def sdk_must_not_run(**_kwargs):
        raise AssertionError("the real staged QA Skill must not be left to SDK discretion")

    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", sdk_must_not_run)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "model-token")
    payload = task_payload()
    payload["prompt"] = build_skill_prompt(
        skill_id="qa-file-reviewer",
        user_message="review this document",
        file_names=["source.docx"],
    )
    payload["config"]["skill_ids"] = ["qa-file-reviewer", "minimax-docx"]
    payload["config"]["materialized_file_names"] = ["source.docx"]
    qa_policy = selected_baoyu_skill_policy()
    next(subject for subject in qa_policy if subject["identity"] == "Skill")["allowed_skill_names"] = [
        "qa-file-reviewer"
    ]
    payload["config"]["tool_policy_subjects"] = qa_policy
    client = create_test_client(tmp_path, callback_sender=lambda url, payload, token: {"accepted": True})

    response = client.post("/v1/tasks/execute", json=payload, headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    output_docx = workspace / "output" / "source_reviewed.docx"
    assert output_docx.is_file()
    with zipfile.ZipFile(output_docx) as archive:
        assert "word/document.xml" in archive.namelist()


def test_executor_fails_closed_when_selected_file_skill_runner_fails(tmp_path, monkeypatch):
    class StubSettings:
        claude_agent_sdk_enabled = True

    workspace = Path(tmp_path)
    write_minimal_docx(workspace / "source.docx")
    script = workspace / ".claude" / "skills" / "baoyu-translate" / "scripts" / "run_translation.py"
    script.parent.mkdir(parents=True)
    script.write_text("raise SystemExit(7)\n", encoding="utf-8")

    async def sdk_must_not_run(**_kwargs):
        raise AssertionError("failed controlled Skill must not fall back to SDK discretion")

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())
    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", sdk_must_not_run)

    payload = task_payload()
    payload["config"]["skill_ids"] = ["baoyu-translate"]
    payload["config"]["materialized_file_names"] = ["source.docx"]
    payload["config"]["tool_policy_subjects"] = selected_baoyu_skill_policy()
    client = create_test_client(tmp_path, callback_sender=lambda url, payload, token: {"accepted": True})

    response = client.post("/v1/tasks/execute", json=payload, headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "controlled_skill_execution_failed"
    assert body["sdk_used"] is False
    assert body["used_skills"] == []
    assert not (workspace / "output" / "translated.docx").exists()


def test_executor_fails_closed_when_selected_file_skill_runner_is_not_staged(tmp_path, monkeypatch):
    class StubSettings:
        claude_agent_sdk_enabled = True

    workspace = Path(tmp_path)
    write_minimal_docx(workspace / "source.docx")

    async def sdk_must_not_run(**_kwargs):
        raise AssertionError("missing staged Skill runner must not fall back to SDK discretion")

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())
    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", sdk_must_not_run)

    payload = task_payload()
    payload["config"]["skill_ids"] = ["baoyu-translate"]
    payload["config"]["materialized_file_names"] = ["source.docx"]
    payload["config"]["tool_policy_subjects"] = selected_baoyu_skill_policy()
    client = create_test_client(tmp_path, callback_sender=lambda url, payload, token: {"accepted": True})

    response = client.post("/v1/tasks/execute", json=payload, headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "controlled_skill_runner_missing"
    assert body["sdk_used"] is False
    assert body["used_skills"] == []


@pytest.mark.asyncio
async def test_selected_file_skill_cancellation_terminates_the_controlled_process(tmp_path):
    workspace = Path(tmp_path)
    write_minimal_docx(workspace / "source.docx")
    script = workspace / ".claude" / "skills" / "baoyu-translate" / "scripts" / "run_translation.py"
    script.parent.mkdir(parents=True)
    child = script.with_name("late_child.py")
    child.write_text(
        """import sys
import time
from pathlib import Path

time.sleep(0.15)
output = Path(sys.argv[1])
output.mkdir(parents=True, exist_ok=True)
(output / \"translated.docx\").write_bytes(b\"late artifact\")
""",
        encoding="utf-8",
    )
    script.write_text(
        """import subprocess
import sys
import time
from pathlib import Path

Path(\"runner-started\").write_text(\"started\", encoding=\"utf-8\")
subprocess.Popen([sys.executable, str(Path(__file__).with_name(\"late_child.py\")), sys.argv[2]])
time.sleep(10)
""",
        encoding="utf-8",
    )
    payload = task_payload()
    payload["config"]["skill_ids"] = ["baoyu-translate"]
    payload["config"]["materialized_file_names"] = ["source.docx"]
    payload["config"]["tool_policy_subjects"] = selected_baoyu_skill_policy()
    request = ExecutorTaskRequest.model_validate(payload)

    async def emit_event(_event):
        return None

    task = asyncio.create_task(_default_executor_runner(request, workspace, emit_event))
    for _ in range(50):
        if (workspace / "runner-started").is_file():
            break
        await asyncio.sleep(0.01)
    assert (workspace / "runner-started").is_file()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.3)
    assert not (workspace / "output" / "translated.docx").exists()


@pytest.mark.asyncio
async def test_executor_deadline_stops_controlled_runner_descendants_before_terminal_response(tmp_path):
    workspace = Path(tmp_path)
    write_minimal_docx(workspace / "source.docx")
    script = workspace / ".claude" / "skills" / "baoyu-translate" / "scripts" / "run_translation.py"
    script.parent.mkdir(parents=True)
    child = script.with_name("late_child.py")
    child.write_text(
        """import sys
import time
from pathlib import Path

time.sleep(0.3)
output = Path(sys.argv[1])
output.mkdir(parents=True, exist_ok=True)
(output / \"translated.docx\").write_bytes(b\"late artifact\")
""",
        encoding="utf-8",
    )
    script.write_text(
        """import subprocess
import sys
import time
from pathlib import Path

Path(\"runner-started\").write_text(\"started\", encoding=\"utf-8\")
subprocess.Popen([sys.executable, str(Path(__file__).with_name(\"late_child.py\")), sys.argv[2]])
time.sleep(10)
""",
        encoding="utf-8",
    )
    payload = task_payload()
    payload["config"]["resource_limits"] = {"max_seconds": 0.15}
    payload["config"]["skill_ids"] = ["baoyu-translate"]
    payload["config"]["materialized_file_names"] = ["source.docx"]
    payload["config"]["tool_policy_subjects"] = selected_baoyu_skill_policy()
    app = create_executor_app(
        workspace_root=workspace,
        callback_sender=lambda url, callback_payload, token: {"accepted": True},
        executor_auth_token=EXECUTOR_AUTH_TOKEN,
        expected_session_id="session-a",
        expected_run_id="run-a",
        trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
    )
    endpoint = next(route.endpoint for route in app.routes if route.path == "/v1/tasks/execute")
    request = ExecutorTaskRequest.model_validate(payload)

    result = await endpoint(request, executor_credential=EXECUTOR_AUTH_TOKEN)

    assert (workspace / "runner-started").is_file()
    assert result["status"] == "failed"
    assert result["error_code"] == "executor_deadline_exceeded"
    await asyncio.sleep(0.45)
    assert not (workspace / "output" / "translated.docx").exists()


def test_executor_fails_closed_without_matching_skill_authorization(tmp_path, monkeypatch):
    workspace = Path(tmp_path)
    write_minimal_docx(workspace / "source.docx")
    script = workspace / ".claude" / "skills" / "baoyu-translate" / "scripts" / "run_translation.py"
    script.parent.mkdir(parents=True)
    script.write_text("raise AssertionError('unauthorized script executed')\n", encoding="utf-8")
    async def sdk_must_not_run(**_kwargs):
        raise AssertionError("denied controlled Skill must not fall back to SDK")

    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", sdk_must_not_run)

    payload = task_payload()
    payload["config"]["skill_ids"] = ["baoyu-translate"]
    payload["config"]["materialized_file_names"] = ["source.docx"]
    denied_policy = selected_baoyu_skill_policy()
    next(subject for subject in denied_policy if subject["identity"] == "Skill")["allowed_skill_names"] = [
        "qa-file-reviewer"
    ]
    payload["config"]["tool_policy_subjects"] = denied_policy
    client = create_test_client(tmp_path, callback_sender=lambda url, payload, token: {"accepted": True})

    response = client.post("/v1/tasks/execute", json=payload, headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error_code"] == "controlled_skill_authorization_incomplete"
    assert not (workspace / "output" / "translated.docx").exists()


def test_executor_execute_fails_when_claude_sdk_disabled(tmp_path, monkeypatch):
    class StubSettings:
        claude_agent_sdk_enabled = False

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())

    client = create_test_client(tmp_path)

    response = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "claude_agent_sdk_disabled"
    assert body["executor_mode"] == "claude_agent_sdk_disabled"


def test_executor_execute_rehydrates_context_retrieval_for_manifest(tmp_path, monkeypatch):
    captured = {}

    class StubSettings:
        claude_agent_sdk_enabled = True

    async def fake_run_claude_agent_sdk(**kwargs):
        captured["context_retrieval"] = kwargs["context_retrieval"]
        captured["context_retrieval_identity"] = kwargs["context_retrieval_identity"]
        return type(
            "SdkResult",
            (),
            {
                "used_sdk": True,
                "message": "sdk final",
                "session_id": "sdk-session-a",
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "error": None,
                "used_skills": [],
                "used_skills_source": "",
            },
        )()

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())
    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", fake_run_claude_agent_sdk)

    payload = task_payload()
    payload["config"]["context_manifest"] = {
        "schema_version": "ai-platform.context-manifest.v1",
        "available_retrieval_tools": ["read_context_file"],
    }
    payload["config"]["context_retrieval_scope"] = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "agent_id": "general-agent",
    }

    client = create_test_client(tmp_path)

    response = client.post("/v1/tasks/execute", json=payload, headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert captured["context_retrieval"] is not None
    assert (
        captured["context_retrieval"]._callback_url
        == "http://ai-platform.test/api/ai/runtime/callbacks/context-retrieval"
    )
    assert captured["context_retrieval_identity"].tenant_id == "tenant-a"
    assert captured["context_retrieval_identity"].workspace_id == "workspace-a"
    assert captured["context_retrieval_identity"].user_id == "user-a"


@pytest.mark.asyncio
async def test_default_executor_preparses_brokered_xlsx_and_forwards_typed_context(tmp_path, monkeypatch):
    source = tmp_path / "source.xlsx"
    write_minimal_xlsx(source)
    raw = source.read_bytes()
    source.unlink()
    captured = {}

    class StubSettings:
        claude_agent_sdk_enabled = True

    async def fake_stage(_self, *, file_id, workspace_root, max_bytes, **scope):
        assert file_id == "file-a"
        assert max_bytes == 1024 * 1024
        assert scope == {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
        }
        target = Path(workspace_root) / "context" / "file-a" / "book.xlsx"
        target.parent.mkdir(parents=True)
        target.write_bytes(raw)
        return {
            "file_id": file_id,
            "workspace_path": "context/file-a/book.xlsx",
            "bytes_staged": len(raw),
            "max_bytes": max_bytes,
        }

    async def fake_sdk(**kwargs):
        captured["attachment_contexts"] = kwargs["attachment_contexts"]
        return type(
            "SdkResult",
            (),
            {
                "used_sdk": True,
                "message": "xlsx answer",
                "session_id": "sdk-session-a",
                "usage": {},
                "error": None,
                "used_skills": ["qa-rag-skill"],
                "used_skills_source": "executor_hook",
            },
        )()

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())
    monkeypatch.setattr(
        "app.runtime.sandbox.executor_app.PlatformContextRetrievalClient.stage_context_file_to_workspace",
        fake_stage,
    )
    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", fake_sdk)
    payload = task_payload()
    payload["config"].update(
        {
            "skill_ids": ["qa-rag-skill"],
            "input_files": ["file-a"],
            "materialized_file_names": ["book.xlsx"],
            "tool_policy_subjects": context_stage_policy(),
            "context_manifest": {
                "schema_version": "ai-platform.context-manifest.v1",
                "available_retrieval_tools": ["stage_context_file_to_workspace"],
                "files": [{"file_id": "file-a"}],
                "attachment_preprocessing": build_attachment_preprocessing_contract(
                    file_ids=["file-a"],
                    file_names=["book.xlsx"],
                ),
            },
            "context_retrieval_scope": {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-a",
                "agent_id": "general-agent",
            },
        }
    )
    request = ExecutorTaskRequest.model_validate(payload)

    async def emit_event(_event):
        return None

    result = await _default_executor_runner(request, tmp_path, emit_event)

    assert result["status"] == "completed"
    assert result["attachment_parser_evidence"][0]["status"] == "parsed"
    assert result["attachment_parser_evidence"][0]["file_id"] == "file-a"
    typed_context = captured["attachment_contexts"][0]
    formula = typed_context.content["workbook"]["sheets"][0]["rows"][1]["cells"][1]
    assert formula["kind"] == "formula"
    assert formula["value"] == "=1+2"


@pytest.mark.asyncio
async def test_default_executor_keeps_duplicate_xlsx_basenames_bound_to_distinct_file_ids(
    tmp_path,
    monkeypatch,
):
    first_path = tmp_path / "first.xlsx"
    second_path = tmp_path / "second.xlsx"
    write_minimal_xlsx(first_path, formula="=1+2")
    write_minimal_xlsx(second_path, formula="=3+4")
    raw_by_file = {
        "file-a": first_path.read_bytes(),
        "file-b": second_path.read_bytes(),
    }
    first_path.unlink()
    second_path.unlink()
    captured = {}

    class StubSettings:
        claude_agent_sdk_enabled = True

    async def fake_stage(_self, *, file_id, workspace_root, max_bytes, **_scope):
        raw = raw_by_file[file_id]
        target = Path(workspace_root) / "context" / file_id / "book.xlsx"
        target.parent.mkdir(parents=True)
        target.write_bytes(raw)
        return {
            "file_id": file_id,
            "workspace_path": f"context/{file_id}/book.xlsx",
            "bytes_staged": len(raw),
            "max_bytes": max_bytes,
        }

    async def fake_sdk(**kwargs):
        captured["attachment_contexts"] = kwargs["attachment_contexts"]
        return type(
            "SdkResult",
            (),
            {
                "used_sdk": True,
                "message": "two workbook answer",
                "session_id": "sdk-session-a",
                "usage": {},
                "error": None,
                "used_skills": ["qa-rag-skill"],
                "used_skills_source": "executor_hook",
            },
        )()

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())
    monkeypatch.setattr(
        "app.runtime.sandbox.executor_app.PlatformContextRetrievalClient.stage_context_file_to_workspace",
        fake_stage,
    )
    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", fake_sdk)
    facts = [
        MaterializedAttachmentFact(
            file_id=file_id,
            file_name="book.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            byte_count=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
        )
        for file_id, raw in raw_by_file.items()
    ]
    payload = task_payload()
    payload["config"].update(
        {
            "skill_ids": ["qa-rag-skill"],
            "input_files": ["file-a", "file-b"],
            "materialized_file_names": ["book.xlsx", "book.xlsx"],
            "tool_policy_subjects": context_stage_policy(),
            "context_manifest": {
                "schema_version": "ai-platform.context-manifest.v1",
                "files": [{"file_id": "file-a"}, {"file_id": "file-b"}],
                "attachment_preprocessing": build_attachment_preprocessing_contract(
                    attachment_facts=facts,
                ),
            },
            "context_retrieval_scope": {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-a",
                "agent_id": "general-agent",
            },
        }
    )
    request = ExecutorTaskRequest.model_validate(payload)

    async def emit_event(_event):
        return None

    result = await _default_executor_runner(request, tmp_path, emit_event)

    assert result["status"] == "completed"
    assert [row["file_id"] for row in result["attachment_parser_evidence"]] == [
        "file-a",
        "file-b",
    ]
    assert result["attachment_parser_evidence"][0]["sha256"] != result[
        "attachment_parser_evidence"
    ][1]["sha256"]
    formulas = [
        context.content["workbook"]["sheets"][0]["rows"][1]["cells"][1]["value"]
        for context in captured["attachment_contexts"]
    ]
    assert formulas == ["=1+2", "=3+4"]


@pytest.mark.asyncio
async def test_default_executor_fails_before_sdk_for_malformed_xlsx(tmp_path, monkeypatch):
    class StubSettings:
        claude_agent_sdk_enabled = True

    async def fake_stage(_self, *, file_id, workspace_root, max_bytes, **_scope):
        target = Path(workspace_root) / "context" / file_id / "book.xlsx"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"not-a-workbook")
        return {
            "file_id": file_id,
            "workspace_path": f"context/{file_id}/book.xlsx",
            "bytes_staged": len(b"not-a-workbook"),
            "max_bytes": max_bytes,
        }

    async def fail_sdk(**_kwargs):
        raise AssertionError("SDK must not run without positive XLSX parser evidence")

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())
    monkeypatch.setattr(
        "app.runtime.sandbox.executor_app.PlatformContextRetrievalClient.stage_context_file_to_workspace",
        fake_stage,
    )
    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", fail_sdk)
    payload = task_payload()
    payload["config"].update(
        {
            "skill_ids": ["qa-rag-skill"],
            "input_files": ["file-a"],
            "materialized_file_names": ["book.xlsx"],
            "tool_policy_subjects": context_stage_policy(),
            "context_manifest": {
                "schema_version": "ai-platform.context-manifest.v1",
                "files": [{"file_id": "file-a"}],
                "attachment_preprocessing": build_attachment_preprocessing_contract(
                    file_ids=["file-a"],
                    file_names=["book.xlsx"],
                ),
            },
            "context_retrieval_scope": {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-a",
                "agent_id": "general-agent",
            },
        }
    )
    request = ExecutorTaskRequest.model_validate(payload)

    async def emit_event(_event):
        return None

    result = await _default_executor_runner(request, tmp_path, emit_event)

    assert result["status"] == "failed"
    assert result["error_code"] == "xlsx_parse_failed"
    assert result["sdk_used"] is False


@pytest.mark.asyncio
async def test_default_executor_requires_server_context_stage_subject_for_xlsx(tmp_path, monkeypatch):
    class StubSettings:
        claude_agent_sdk_enabled = True

    async def fail_stage(*_args, **_kwargs):
        raise AssertionError("staging must not start without the exact server-owned subject")

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())
    monkeypatch.setattr(
        "app.runtime.sandbox.executor_app.PlatformContextRetrievalClient.stage_context_file_to_workspace",
        fail_stage,
    )
    payload = task_payload()
    payload["config"].update(
        {
            "skill_ids": ["qa-rag-skill"],
            "input_files": ["file-a"],
            "materialized_file_names": ["book.xlsx"],
            "context_manifest": {
                "schema_version": "ai-platform.context-manifest.v1",
                "files": [{"file_id": "file-a"}],
                "attachment_preprocessing": build_attachment_preprocessing_contract(
                    file_ids=["file-a"],
                    file_names=["book.xlsx"],
                ),
            },
            "context_retrieval_scope": {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-a",
                "agent_id": "general-agent",
            },
        }
    )
    request = ExecutorTaskRequest.model_validate(payload)

    async def emit_event(_event):
        return None

    result = await _default_executor_runner(request, tmp_path, emit_event)

    assert result["status"] == "failed"
    assert result["error_code"] == "attachment_parser_staging_not_authorized"


@pytest.mark.asyncio
async def test_default_executor_rejects_parser_file_absent_from_dispatched_manifest(tmp_path, monkeypatch):
    class StubSettings:
        claude_agent_sdk_enabled = True

    async def fail_stage(*_args, **_kwargs):
        raise AssertionError("staging must not expand beyond dispatched manifest file IDs")

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())
    monkeypatch.setattr(
        "app.runtime.sandbox.executor_app.PlatformContextRetrievalClient.stage_context_file_to_workspace",
        fail_stage,
    )
    payload = task_payload()
    payload["config"].update(
        {
            "skill_ids": ["qa-rag-skill"],
            "input_files": ["file-a"],
            "materialized_file_names": ["book.xlsx"],
            "tool_policy_subjects": context_stage_policy(),
            "context_manifest": {
                "schema_version": "ai-platform.context-manifest.v1",
                "files": [{"file_id": "file-other"}],
                "available_retrieval_tools": ["stage_context_file_to_workspace"],
                "attachment_preprocessing": build_attachment_preprocessing_contract(
                    file_ids=["file-a"],
                    file_names=["book.xlsx"],
                ),
            },
            "context_retrieval_scope": {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-a",
                "agent_id": "general-agent",
            },
        }
    )
    request = ExecutorTaskRequest.model_validate(payload)

    async def emit_event(_event):
        return None

    result = await _default_executor_runner(request, tmp_path, emit_event)

    assert result["status"] == "failed"
    assert result["error_code"] == "attachment_parser_manifest_file_mismatch"


def test_executor_execute_fails_closed_for_manifest_without_valid_scope(tmp_path, monkeypatch):
    class StubSettings:
        claude_agent_sdk_enabled = True

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())

    payload = task_payload()
    payload["config"]["context_manifest"] = {
        "schema_version": "ai-platform.context-manifest.v1",
        "available_retrieval_tools": ["read_context_file"],
    }
    payload["config"]["context_retrieval_scope"] = {"tenant_id": "tenant-a"}

    client = create_test_client(tmp_path)

    response = client.post("/v1/tasks/execute", json=payload, headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "context_retrieval_scope_invalid"


def test_executor_execute_rejects_context_scope_for_different_run(tmp_path, monkeypatch):
    class StubSettings:
        claude_agent_sdk_enabled = True

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())

    payload = task_payload()
    payload["config"]["context_manifest"] = {
        "schema_version": "ai-platform.context-manifest.v1",
        "available_retrieval_tools": ["read_context_file"],
    }
    payload["config"]["context_retrieval_scope"] = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-b",
        "agent_id": "general-agent",
    }

    response = create_test_client(tmp_path).post(
        "/v1/tasks/execute",
        json=payload,
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["error_code"] == "context_retrieval_scope_invalid"


def test_executor_execute_reports_platform_timeout_probe_as_nonterminal_observation(tmp_path):
    callbacks = []
    payload = task_payload()
    payload["config"]["resource_limits"] = {"max_seconds": 0}

    def callback_sender(url, payload, token):
        callbacks.append((url, payload, token))
        return {"accepted": True}

    client = create_test_client(tmp_path, callback_sender=callback_sender)

    response = client.post("/v1/tasks/execute", json=payload, headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["run_id"] == "run-a"
    assert body["error_code"] == "executor_health_timeout"
    assert body["error_message"] == "Executor health timeout"
    assert body["requested_max_seconds"] == 0
    assert isinstance(body["timeout_elapsed_ms"], int)
    assert [item[1]["status"] for item in callbacks] == ["running", "running"]
    assert callbacks[-1][1]["error_message"] == "Executor health timeout"
    assert callbacks[-1][1]["state_patch"] == {
        "stage": "executor_finished",
        "error_code": "executor_health_timeout",
        "requested_max_seconds": 0,
        "timeout_elapsed_ms": body["timeout_elapsed_ms"],
    }
    assert str(tmp_path) not in str(body)


def test_executor_execute_enforces_fractional_positive_timeout_and_cancels_runner(tmp_path):
    callbacks = []
    runner_cancelled = threading.Event()
    late_side_effect = threading.Event()
    payload = task_payload()
    payload["config"]["resource_limits"] = {"max_seconds": 0.03}

    async def executor_runner(request, workspace_root, emit_event):
        try:
            await asyncio.sleep(0.2)
            late_side_effect.set()
            return {"status": "completed"}
        except asyncio.CancelledError:
            runner_cancelled.set()
            raise

    def callback_sender(url, payload, token):
        callbacks.append((url, payload, token))
        return {"accepted": True}

    client = create_test_client(
        tmp_path,
        callback_sender=callback_sender,
        executor_runner=executor_runner,
    )

    started_at = time.monotonic()
    response = client.post("/v1/tasks/execute", json=payload, headers=auth_headers())
    elapsed = time.monotonic() - started_at

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "executor_deadline_exceeded"
    assert body["error_message"] == "Executor deadline exceeded"
    assert body["requested_max_seconds"] == 0.03
    assert 0 <= body["timeout_elapsed_ms"] < 250
    assert elapsed < 0.25
    assert runner_cancelled.wait(timeout=0.1)
    time.sleep(0.1)
    assert not late_side_effect.is_set()
    assert [item[1]["status"] for item in callbacks] == ["running", "running"]
    assert callbacks[-1][1]["state_patch"] == {
        "stage": "executor_finished",
        "error_code": "executor_deadline_exceeded",
        "requested_max_seconds": 0.03,
        "timeout_elapsed_ms": body["timeout_elapsed_ms"],
    }
    assert str(tmp_path) not in str(body)


@pytest.mark.asyncio
async def test_executor_deadline_waits_for_runner_cleanup_before_terminal_response(tmp_path):
    callbacks = []
    runner_cancelled = asyncio.Event()
    runner_finished = asyncio.Event()
    late_event_attempted = asyncio.Event()
    release_runner = asyncio.Event()
    loop_exception_contexts = []
    payload = task_payload()
    payload["config"]["resource_limits"] = {"max_seconds": 0.01}

    async def executor_runner(request, workspace_root, emit_event):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            runner_cancelled.set()
            await release_runner.wait()
            try:
                late_event_attempted.set()
                await emit_event(AgentEvent(type="assistant_delta", message="late", payload={"delta": "late"}))
                raise RuntimeError("deterministic runner cleanup failure")
            finally:
                runner_finished.set()

    async def callback_sender(url, callback_payload, token):
        callbacks.append(callback_payload)
        return {"accepted": True}

    app = create_executor_app(
        workspace_root=tmp_path,
        callback_sender=callback_sender,
        executor_runner=executor_runner,
        executor_auth_token=EXECUTOR_AUTH_TOKEN,
        expected_session_id="session-a",
        expected_run_id="run-a",
        trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
    )
    endpoint = next(route.endpoint for route in app.routes if route.path == "/v1/tasks/execute")
    request = ExecutorTaskRequest.model_validate(payload)
    loop = asyncio.get_running_loop()
    previous_exception_handler = loop.get_exception_handler()
    initial_tasks = asyncio.all_tasks()
    endpoint_task = None

    def capture_loop_exception(loop, context):
        loop_exception_contexts.append(context)

    loop.set_exception_handler(capture_loop_exception)
    try:
        endpoint_task = asyncio.create_task(endpoint(request, executor_credential=EXECUTOR_AUTH_TOKEN))

        await asyncio.wait_for(runner_cancelled.wait(), timeout=0.15)
        await asyncio.sleep(0.02)
        assert not endpoint_task.done()

        release_runner.set()
        await asyncio.wait_for(runner_finished.wait(), timeout=0.5)
        result = await asyncio.wait_for(endpoint_task, timeout=0.5)
        assert result["status"] == "failed"
        assert result["error_code"] == "executor_cleanup_failed"
        await asyncio.sleep(0)
        gc.collect()
        await asyncio.sleep(0)

        assert late_event_attempted.is_set()
        assert [callback["status"] for callback in callbacks] == ["running", "running"]
        assert all(
            event.get("message") != "late"
            for callback in callbacks
            for event in callback.get("events", [])
        )
        assert loop_exception_contexts == []
        assert [task for task in asyncio.all_tasks() - initial_tasks if not task.done()] == []
    finally:
        release_runner.set()
        if endpoint_task is not None and not endpoint_task.done():
            endpoint_task.cancel()
            await asyncio.gather(endpoint_task, return_exceptions=True)
        if runner_cancelled.is_set() and not runner_finished.is_set():
            await asyncio.wait_for(runner_finished.wait(), timeout=0.5)
        loop.set_exception_handler(previous_exception_handler)


@pytest.mark.asyncio
async def test_executor_deadline_reports_cleanup_timeout_without_waiting_forever(tmp_path, monkeypatch):
    runner_cancelled = asyncio.Event()
    runner_finished = asyncio.Event()
    release_runner = asyncio.Event()
    payload = task_payload()
    payload["config"]["resource_limits"] = {"max_seconds": 0.01}

    async def executor_runner(request, workspace_root, emit_event):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            runner_cancelled.set()
            try:
                await release_runner.wait()
            finally:
                runner_finished.set()
            return {"status": "completed"}

    monkeypatch.setattr("app.runtime.sandbox.executor_app._EXECUTOR_CLEANUP_TIMEOUT_SECONDS", 0.02)
    app = create_executor_app(
        workspace_root=tmp_path,
        callback_sender=lambda url, payload, token: {"accepted": True},
        executor_runner=executor_runner,
        executor_auth_token=EXECUTOR_AUTH_TOKEN,
        expected_session_id="session-a",
        expected_run_id="run-a",
        trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
    )
    endpoint = next(route.endpoint for route in app.routes if route.path == "/v1/tasks/execute")
    request = ExecutorTaskRequest.model_validate(payload)
    endpoint_task = asyncio.create_task(endpoint(request, executor_credential=EXECUTOR_AUTH_TOKEN))

    try:
        await asyncio.wait_for(runner_cancelled.wait(), timeout=0.15)
        done, _ = await asyncio.wait({endpoint_task}, timeout=0.15)
        assert endpoint_task in done
        result = endpoint_task.result()
        assert result["status"] == "failed"
        assert result["error_code"] == "executor_cleanup_timeout"
        assert "requested_max_seconds" not in result
    finally:
        release_runner.set()
        if not endpoint_task.done():
            await asyncio.wait({endpoint_task}, timeout=0.5)
        await asyncio.wait_for(runner_finished.wait(), timeout=0.5)


@pytest.mark.asyncio
async def test_stop_controlled_process_bounds_post_kill_wait(monkeypatch):
    release_waiters = asyncio.Event()
    signals = []

    class StuckProcess:
        pid = 4242
        returncode = None

        async def wait(self):
            await release_waiters.wait()

        def send_signal(self, signal_value):
            signals.append(("graceful", signal_value))

        def terminate(self):
            signals.append(("terminate", None))

        def kill(self):
            signals.append(("kill", None))

    if executor_app.os.name == "nt":
        interrupt = getattr(executor_app.signal, "CTRL_BREAK_EVENT", None)
        expected_signals = [
            ("graceful", interrupt) if interrupt is not None else ("terminate", None),
            ("kill", None),
        ]
    else:
        monkeypatch.setattr(
            executor_app.os,
            "killpg",
            lambda pid, signal_value: signals.append((pid, signal_value)),
        )
        expected_signals = [
            (StuckProcess.pid, executor_app.signal.SIGTERM),
            (StuckProcess.pid, executor_app.signal.SIGKILL),
        ]
    monkeypatch.setattr("app.runtime.sandbox.executor_app._CONTROLLED_RUNNER_TERMINATION_GRACE_SECONDS", 0.01)
    process = StuckProcess()

    try:
        with pytest.raises(TimeoutError, match="Controlled process"):
            await executor_app._stop_controlled_process(process)
        assert signals == expected_signals
    finally:
        release_waiters.set()
        await asyncio.sleep(0)


def test_executor_execute_allows_runner_with_larger_fractional_deadline(tmp_path):
    callbacks = []
    payload = task_payload()
    payload["config"]["resource_limits"] = {"max_seconds": 0.2}

    async def executor_runner(request, workspace_root, emit_event):
        await asyncio.sleep(0.01)
        return {"status": "completed", "message": "done"}

    def callback_sender(url, payload, token):
        callbacks.append(payload)
        return {"accepted": True}

    client = create_test_client(tmp_path, callback_sender=callback_sender, executor_runner=executor_runner)

    response = client.post("/v1/tasks/execute", json=payload, headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert [item["status"] for item in callbacks] == ["running", "running"]
    assert callbacks[-1]["state_patch"]["stage"] == "executor_finished"


def test_executor_execute_does_not_rewrite_runner_timeout_error_as_deadline(tmp_path):
    async def executor_runner(request, workspace_root, emit_event):
        raise TimeoutError("runner dependency timed out")

    client = create_test_client(
        tmp_path,
        callback_sender=lambda url, payload, token: {"accepted": True},
        executor_runner=executor_runner,
    )

    response = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error_code"] == "executor_runner_failed"
    assert response.json()["error_message"] == "runner dependency timed out"
    assert "requested_max_seconds" not in response.json()
    assert "timeout_elapsed_ms" not in response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_max_seconds", [True, "0.05", float("nan"), float("inf"), float("-inf")])
async def test_executor_execute_rejects_invalid_deadline_without_invoking_runner(
    tmp_path,
    invalid_max_seconds,
):
    runner_called = False

    async def executor_runner(request, workspace_root, emit_event):
        nonlocal runner_called
        runner_called = True
        return {"status": "completed"}

    app = create_executor_app(
        workspace_root=tmp_path,
        callback_sender=lambda url, payload, token: {"accepted": True},
        executor_runner=executor_runner,
        executor_auth_token=EXECUTOR_AUTH_TOKEN,
        expected_session_id="session-a",
        expected_run_id="run-a",
        trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
    )
    endpoint = next(route.endpoint for route in app.routes if route.path == "/v1/tasks/execute")
    payload = task_payload()
    payload["config"]["resource_limits"] = {"max_seconds": invalid_max_seconds}
    request = ExecutorTaskRequest.model_validate(payload)

    result = await endpoint(request, executor_credential=EXECUTOR_AUTH_TOKEN)

    assert result["status"] == "failed"
    assert result["error_code"] == "executor_invalid_max_seconds"
    assert "requested_max_seconds" not in result
    assert "timeout_elapsed_ms" not in result
    assert runner_called is False


@pytest.mark.parametrize("runner_kind", ["partial", "callable", "decorated"])
def test_executor_execute_accepts_supported_async_callable_forms(tmp_path, runner_kind):
    async def async_runner(request, workspace_root, emit_event):
        await asyncio.sleep(0)
        return {"status": "completed", "message": runner_kind}

    if runner_kind == "partial":
        executor_runner = functools.partial(async_runner)
    elif runner_kind == "callable":
        class AsyncRunner:
            async def __call__(self, request, workspace_root, emit_event):
                return await async_runner(request, workspace_root, emit_event)

        executor_runner = AsyncRunner()
    else:
        @functools.wraps(async_runner)
        async def decorated_runner(request, workspace_root, emit_event):
            return await async_runner(request, workspace_root, emit_event)

        executor_runner = decorated_runner

    client = create_test_client(
        tmp_path,
        callback_sender=lambda url, payload, token: {"accepted": True},
        executor_runner=executor_runner,
    )

    response = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


def test_executor_execute_rejects_sync_wrapper_before_positive_deadline_control(tmp_path):
    wrapper_called = False

    async def async_runner(request, workspace_root, emit_event):
        return {"status": "completed"}

    @functools.wraps(async_runner)
    def sync_wrapper(request, workspace_root, emit_event):
        nonlocal wrapper_called
        wrapper_called = True
        return async_runner(request, workspace_root, emit_event)

    client = create_test_client(
        tmp_path,
        callback_sender=lambda url, payload, token: {"accepted": True},
        executor_runner=sync_wrapper,
    )

    response = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error_code"] == "executor_deadline_requires_async_runner"
    assert wrapper_called is False


def test_executor_execute_classifies_decorated_runner_timeout_as_internal_failure(tmp_path):
    async def async_runner(request, workspace_root, emit_event):
        raise TimeoutError("decorated runner dependency timed out")

    @functools.wraps(async_runner)
    async def decorated_runner(request, workspace_root, emit_event):
        return await async_runner(request, workspace_root, emit_event)

    client = create_test_client(
        tmp_path,
        callback_sender=lambda url, payload, token: {"accepted": True},
        executor_runner=decorated_runner,
    )

    response = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error_code"] == "executor_runner_failed"
    assert response.json()["error_message"] == "decorated runner dependency timed out"
    assert "requested_max_seconds" not in response.json()
    assert "timeout_elapsed_ms" not in response.json()


@pytest.mark.asyncio
async def test_executor_execute_preserves_caller_cancellation(tmp_path):
    runner_started = asyncio.Event()
    runner_cancelled = asyncio.Event()

    async def executor_runner(request, workspace_root, emit_event):
        runner_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            runner_cancelled.set()
            raise

    app = create_executor_app(
        workspace_root=tmp_path,
        callback_sender=lambda url, payload, token: {"accepted": True},
        executor_runner=executor_runner,
        executor_auth_token=EXECUTOR_AUTH_TOKEN,
        expected_session_id="session-a",
        expected_run_id="run-a",
        trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
    )
    endpoint = next(route.endpoint for route in app.routes if route.path == "/v1/tasks/execute")
    request = ExecutorTaskRequest.model_validate(task_payload())

    execute_task = asyncio.create_task(endpoint(request, executor_credential=EXECUTOR_AUTH_TOKEN))
    await asyncio.wait_for(runner_started.wait(), timeout=0.2)
    execute_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await execute_task
    assert runner_cancelled.is_set()


@pytest.mark.asyncio
async def test_executor_execute_reports_cleanup_failure_when_caller_cancellation_cleanup_fails(tmp_path):
    runner_started = asyncio.Event()

    async def executor_runner(request, workspace_root, emit_event):
        runner_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            raise RuntimeError("runner cancellation cleanup failed") from exc

    app = create_executor_app(
        workspace_root=tmp_path,
        callback_sender=lambda url, payload, token: {"accepted": True},
        executor_runner=executor_runner,
        executor_auth_token=EXECUTOR_AUTH_TOKEN,
        expected_session_id="session-a",
        expected_run_id="run-a",
        trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
    )
    endpoint = next(route.endpoint for route in app.routes if route.path == "/v1/tasks/execute")
    request = ExecutorTaskRequest.model_validate(task_payload())

    execute_task = asyncio.create_task(endpoint(request, executor_credential=EXECUTOR_AUTH_TOKEN))
    await asyncio.wait_for(runner_started.wait(), timeout=0.2)
    execute_task.cancel()

    result = await execute_task

    assert result["status"] == "failed"
    assert result["error_code"] == "executor_cleanup_failed"


def test_executor_execute_fails_closed_for_sync_runner_with_positive_deadline(tmp_path):
    invoked = False

    def executor_runner(request, workspace_root, emit_event):
        nonlocal invoked
        invoked = True
        return {"status": "completed"}

    client = create_test_client(
        tmp_path,
        callback_sender=lambda url, payload, token: {"accepted": True},
        executor_runner=executor_runner,
    )

    response = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error_code"] == "executor_deadline_requires_async_runner"
    assert invoked is False


def test_executor_execute_writes_runtime_marker_without_host_path(tmp_path):
    client = create_test_client(
        tmp_path,
        callback_sender=lambda url, payload, token: {},
    )

    response = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert response.status_code == 200
    marker = Path(tmp_path) / "runtime" / "run-a.json"
    content = marker.read_text(encoding="utf-8")
    assert "prompt_length" in content
    assert "hello executor" not in content
    assert str(tmp_path) not in content


def test_executor_marker_redacts_unapproved_config_and_tokens(tmp_path):
    client = create_test_client(
        tmp_path,
        callback_sender=lambda url, payload, token: {},
    )

    response = client.post("/v1/tasks/execute", json=sensitive_task_payload(), headers=auth_headers())

    assert response.status_code == 200
    content = (Path(tmp_path) / "runtime" / "run-a.json").read_text(encoding="utf-8")
    assert "secret-key" not in content
    assert "Authorization" not in content
    assert "/runtime/tenants" not in content
    assert "nested-secret" not in content
    assert "safe-skill" in content
    assert "deepseek-v4-flash" in content
    assert "secret" not in content


def test_executor_execute_reports_callback_errors_without_raising(tmp_path, monkeypatch):
    callbacks = []

    class StubSettings:
        claude_agent_sdk_enabled = True

    async def fake_run_claude_agent_sdk(**kwargs):
        return type(
            "SdkResult",
            (),
            {
                "used_sdk": True,
                "message": "sdk final",
                "session_id": "sdk-session-a",
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "error": None,
                "used_skills": [],
                "used_skills_source": "",
            },
        )()

    def callback_sender(url, payload, token):
        callbacks.append((payload["status"], payload.get("state_patch", {}).get("stage")))
        if payload.get("state_patch", {}).get("stage") == "executor_finished":
            raise RuntimeError("callback failed")
        return {"accepted": True}

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())
    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", fake_run_claude_agent_sdk)
    client = create_test_client(tmp_path, callback_sender=callback_sender)

    response = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["run_id"] == "run-a"
    assert body["callback_errors"] == ["running"]
    assert isinstance(body["executor_model_latency_ms"], int)
    assert isinstance(body["document_processing_latency_ms"], int)
    assert callbacks == [("running", "accepted"), ("running", "executor_finished")]


def test_executor_finished_observation_marker_path_is_container_path(tmp_path, monkeypatch):
    callbacks = []

    class StubSettings:
        claude_agent_sdk_enabled = True

    async def fake_run_claude_agent_sdk(**kwargs):
        return type(
            "SdkResult",
            (),
            {
                "used_sdk": True,
                "message": "sdk final",
                "session_id": "sdk-session-a",
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "error": None,
                "used_skills": [],
                "used_skills_source": "",
            },
        )()

    def callback_sender(url, payload, token):
        callbacks.append(payload)
        return {"accepted": True}

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())
    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", fake_run_claude_agent_sdk)
    client = create_test_client(tmp_path, callback_sender=callback_sender)

    response = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert response.status_code == 200
    assert callbacks[-1]["status"] == "running"
    assert callbacks[-1]["state_patch"]["stage"] == "executor_finished"
    marker_path = callbacks[-1]["state_patch"]["marker_path"]
    assert marker_path == "/workspace/runtime/run-a.json"
    assert str(tmp_path) not in marker_path


def test_executor_execute_rejects_missing_executor_credential(tmp_path):
    client = create_test_client(tmp_path)

    response = client.post("/v1/tasks/execute", json=task_payload())

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid_executor_credential"}


def test_executor_execute_rejects_wrong_executor_credential(tmp_path):
    client = create_test_client(tmp_path)

    response = client.post(
        "/v1/tasks/execute",
        json=task_payload(),
        headers=auth_headers("wrong-token"),
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid_executor_credential"}


def test_executor_execute_rejects_replay_after_first_dispatch(tmp_path, monkeypatch):
    class StubSettings:
        claude_agent_sdk_enabled = True

    async def fake_run_claude_agent_sdk(**kwargs):
        return type(
            "SdkResult",
            (),
            {
                "used_sdk": True,
                "message": "sdk final",
                "session_id": "sdk-session-a",
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "error": None,
                "used_skills": [],
                "used_skills_source": "",
            },
        )()

    monkeypatch.setattr("app.runtime.sandbox.executor_app.get_settings", lambda: StubSettings())
    monkeypatch.setattr("app.runtime.sandbox.executor_app.run_claude_agent_sdk", fake_run_claude_agent_sdk)
    client = create_test_client(tmp_path, callback_sender=lambda url, payload, token: {"accepted": True})

    first = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())
    second = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json() == {"detail": "executor_request_replayed"}


def test_executor_execute_rejects_untrusted_callback_target(tmp_path):
    client = create_test_client(tmp_path)

    response = client.post(
        "/v1/tasks/execute",
        json=task_payload(
            "http://169.254.169.254/latest/meta-data",
            callback_base_url="http://169.254.169.254",
        ),
        headers=auth_headers(),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "invalid_callback_target"}


def test_executor_execute_rejects_missing_executor_scope_binding(tmp_path):
    client = TestClient(
        create_executor_app(
            workspace_root=tmp_path,
            executor_auth_token=EXECUTOR_AUTH_TOKEN,
            trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
        )
    )

    response = client.post("/v1/tasks/execute", json=task_payload(), headers=auth_headers())

    assert response.status_code == 503
    assert response.json() == {"detail": "executor_scope_not_configured"}


def test_executor_execute_rejects_wrong_executor_scope(tmp_path):
    client = create_test_client(tmp_path)

    response = client.post(
        "/v1/tasks/execute",
        json=task_payload(callback_url=TRUSTED_CALLBACK_URL) | {"session_id": "session-b"},
        headers=auth_headers(),
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid_executor_scope"}
