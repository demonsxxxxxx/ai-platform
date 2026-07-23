from types import SimpleNamespace

import pytest

from app.executors.base import RunPayload
from app.executors.runtime211 import (
    Runtime211Adapter,
    _artifact_label,
    _artifact_type,
    extract_file_markers,
    is_failure_text,
    parse_sse_deltas,
    strip_file_markers,
)


RELEASE_DECISION_SCHEMA_VERSION = "ai-platform.skill-release-decision.v1"


def release_decision(version: str) -> dict:
    return {
        "schema_version": RELEASE_DECISION_SCHEMA_VERSION,
        "policy_active": False,
        "selected_version": version,
        "selected_track": "manifest_pin",
    }


def primary_manifest(skill_id: str, version: str) -> dict:
    return {"skill_id": skill_id, "content_hash": version}


def run_payload(**overrides) -> RunPayload:
    skill_id = overrides.get("skill_id", "qa-file-reviewer")
    version = overrides.get("skill_version") or f"hash-{skill_id}"
    values = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "agent_id": "qa-word-review",
        "skill_id": skill_id,
        "file_ids": ["file-a"],
        "input": {},
        "skill_version": version,
        "release_decision": release_decision(version),
        "skill_manifests": [primary_manifest(skill_id, version)],
    }
    values.update(overrides)
    if "release_decision" not in overrides:
        values["release_decision"] = release_decision(values["skill_version"])
    return RunPayload(**values)


def test_parse_sse_deltas_extracts_json_delta_and_ignores_done():
    raw = (
        'data: {"delta":"[进度] start\\n","task_id":"t1"}\n\n'
        'data: {"delta":"result text"}\n\n'
        "data: [DONE]\n\n"
    )

    assert parse_sse_deltas(raw) == ["[进度] start\n", "result text"]


def test_baoyu_translate_message_includes_target_language():
    from app.executors.runtime211 import _message_for_skill

    message = _message_for_skill(
        "baoyu-translate",
        {
            "target_language": "英文",
        },
    )

    assert "英文" in message


def test_baoyu_translate_docx_is_always_translated_docx():
    assert _artifact_type("result.docx", "baoyu-translate") == "translated_docx"
    assert _artifact_label("result.docx", "baoyu-translate") == "翻译 Word"


def test_extract_file_markers_returns_unique_paths_in_order():
    text = (
        "审核完成 [[FILE:/tmp/out/a_reviewed.docx]] "
        "报告 [[FILE:/tmp/out/report.txt]] "
        "重复 [[FILE:/tmp/out/a_reviewed.docx]]"
    )

    assert extract_file_markers(text) == ["/tmp/out/a_reviewed.docx", "/tmp/out/report.txt"]


def test_strip_file_markers_removes_runtime_paths_from_public_message():
    text = "审核完成 [[FILE:/tmp/out/a_reviewed.docx]]\n报告见附件 [[FILE:/tmp/out/report.txt]]"

    cleaned = strip_file_markers(text)

    assert "[[FILE:" not in cleaned
    assert "/tmp/out" not in cleaned
    assert "审核完成" in cleaned


def test_is_failure_text_detects_runtime_failure_after_progress_lines():
    text = "[进度] 执行完成，正在输出结果...\n技能执行部分完成，但存在错误。\n[[FILE:/tmp/report.txt]]"

    assert is_failure_text(text) is True


def test_runtime211_executor_payload_keeps_runtime_details():
    from app.executors.base import ExecutorResult

    result = ExecutorResult(
        status="succeeded",
        adapter_version="runtime211-adapter/2",
        executor_type="runtime211",
        executor_version="runtime211-http",
        capabilities={"artifacts": True, "streaming": False, "tools": False},
        result={"message": "ok"},
        executor_payload={"runtime_task_id": "task-1"},
    )
    result.validate()
    assert "runtime_task_id" in result.executor_payload
    assert "runtime_task_id" not in result.result


@pytest.mark.asyncio
async def test_runtime211_upload_requires_file_bound_to_current_run(monkeypatch):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_transaction():
        yield object()

    async def fake_get_run_file(conn, *, tenant_id, run_id, file_id):
        assert run_id == "run-a"
        return None

    adapter = Runtime211Adapter.__new__(Runtime211Adapter)
    adapter.base_url = "http://runtime.example"

    monkeypatch.setattr("app.executors.runtime211.transaction", fake_transaction)
    monkeypatch.setattr("app.executors.runtime211.repositories.get_run_file", fake_get_run_file)

    payload = run_payload(file_ids=["file-b"])

    with pytest.raises(ValueError, match="not bound to this run"):
        await adapter._upload_platform_files(payload)


class FakeRuntimeResponse:
    def __init__(self, *, text="", content=b"", headers=None, json_data=None):
        self.text = text
        self.content = content
        self.headers = headers or {}
        self._json_data = json_data or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._json_data


class FakeRuntimeStreamResponse(FakeRuntimeResponse):
    def __init__(self, *, text=""):
        super().__init__(text=text)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def aiter_lines(self):
        for line in self.text.splitlines():
            yield line


class FakeRuntimeClient:
    def __init__(self, *, stream_text, artifact_content=b"docx-bytes"):
        self.stream_text = stream_text
        self.artifact_content = artifact_content
        self.post_json = None
        self.download_paths = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url, json=None, **kwargs):
        self.post_json = json
        return FakeRuntimeResponse(text=self.stream_text)

    def stream(self, method, url, json=None, **kwargs):
        self.post_json = json
        return FakeRuntimeStreamResponse(text=self.stream_text)

    async def get(self, url, params=None, **kwargs):
        self.download_paths.append((params or {}).get("path"))
        return FakeRuntimeResponse(
            content=self.artifact_content,
            headers={"content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
        )


def baoyu_payload(**overrides):
    values = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "agent_id": "baoyu-translate",
        "skill_id": "baoyu-translate",
        "file_ids": ["file-a"],
        "input": {"message": "请翻译为中文", "work_id": "work-a"},
    }
    values.update(overrides)
    return run_payload(**values)


@pytest.mark.asyncio
async def test_runtime211_collects_structured_translation_file_as_translated_docx(monkeypatch):
    stream_text = (
        'data: {"delta":"[进度] 翻译完成\\n","task_id":"task-1"}\n\n'
        'data: {"files":[{"path":"/tmp/runtime/demo.docx","name":"demo.docx"}]}\n\n'
        "data: [DONE]\n\n"
    )
    runtime_client = FakeRuntimeClient(stream_text=stream_text)

    async def fake_upload(self, payload):
        return ["runtime-file-a"]

    class FakeStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            return SimpleNamespace(storage_key=storage_key, size_bytes=len(content))

    monkeypatch.setattr(Runtime211Adapter, "_upload_platform_files", fake_upload)
    monkeypatch.setattr("app.executors.runtime211.httpx.AsyncClient", lambda **kwargs: runtime_client)
    monkeypatch.setattr("app.executors.runtime211.ObjectStorage", FakeStorage)

    adapter = Runtime211Adapter.__new__(Runtime211Adapter)
    adapter.base_url = "http://runtime.example"

    result = await adapter.submit_run(baoyu_payload())

    assert result.status == "succeeded"
    assert result.result["artifact_count"] == 1
    assert result.artifacts[0].artifact_type == "translated_docx"
    assert result.artifacts[0].label == "翻译 Word"
    assert result.artifacts[0].storage_key.endswith("/demo.docx")
    assert runtime_client.download_paths == ["/tmp/runtime/demo.docx"]
    assert runtime_client.post_json["preferred_skill_id"] == "baoyu-translate"
    assert runtime_client.post_json["queue_mode"] == "queued"
    assert runtime_client.post_json["file_ids"] == ["runtime-file-a"]


@pytest.mark.asyncio
async def test_runtime211_emits_assistant_delta_events_from_sse(monkeypatch):
    stream_text = (
        'data: {"delta":"第一段","task_id":"task-stream"}\n\n'
        'data: {"delta":"第二段"}\n\n'
        "data: [DONE]\n\n"
    )
    runtime_client = FakeRuntimeClient(stream_text=stream_text)
    events = []

    async def fake_upload(self, payload):
        return []

    async def event_sink(**event):
        events.append(event)

    monkeypatch.setattr(Runtime211Adapter, "_upload_platform_files", fake_upload)
    monkeypatch.setattr("app.executors.runtime211.httpx.AsyncClient", lambda **kwargs: runtime_client)

    adapter = Runtime211Adapter.__new__(Runtime211Adapter)
    adapter.base_url = "http://runtime.example"

    result = await adapter.submit_run(
        baoyu_payload(
            agent_id="general-agent",
            skill_id="general-chat",
            file_ids=[],
            input={"message": "普通聊天", "work_id": "work-a"},
        ),
        event_sink=event_sink,
    )

    assert result.result["message"] == "第一段第二段"
    assert [
        (event["event_type"], event["stage"], event["message"], event["payload"])
        for event in events
    ] == [
        ("assistant_delta", "assistant", "第一段", {"delta": "第一段", "visible_to_user": True, "severity": "info"}),
        ("assistant_delta", "assistant", "第二段", {"delta": "第二段", "visible_to_user": True, "severity": "info"}),
    ]


@pytest.mark.asyncio
async def test_runtime211_maps_sse_error_to_failed_result(monkeypatch):
    stream_text = 'data: {"error":"技能执行失败：翻译服务超时","task_id":"task-err"}\n\ndata: [DONE]\n\n'
    runtime_client = FakeRuntimeClient(stream_text=stream_text)

    async def fake_upload(self, payload):
        return ["runtime-file-a"]

    monkeypatch.setattr(Runtime211Adapter, "_upload_platform_files", fake_upload)
    monkeypatch.setattr("app.executors.runtime211.httpx.AsyncClient", lambda **kwargs: runtime_client)

    adapter = Runtime211Adapter.__new__(Runtime211Adapter)
    adapter.base_url = "http://runtime.example"

    result = await adapter.submit_run(baoyu_payload())

    assert result.status == "failed"
    assert result.result["error_code"] == "runtime211_stream_error"
    assert "翻译服务超时" in result.result["message"]
    assert result.executor_payload["runtime_task_id"] == "task-err"


@pytest.mark.asyncio
async def test_runtime211_fails_baoyu_run_without_translated_docx(monkeypatch):
    stream_text = 'data: {"delta":"翻译完成，但没有文件。","task_id":"task-no-file"}\n\ndata: [DONE]\n\n'
    runtime_client = FakeRuntimeClient(stream_text=stream_text)

    async def fake_upload(self, payload):
        return ["runtime-file-a"]

    monkeypatch.setattr(Runtime211Adapter, "_upload_platform_files", fake_upload)
    monkeypatch.setattr("app.executors.runtime211.httpx.AsyncClient", lambda **kwargs: runtime_client)

    adapter = Runtime211Adapter.__new__(Runtime211Adapter)
    adapter.base_url = "http://runtime.example"

    result = await adapter.submit_run(baoyu_payload())

    assert result.status == "failed"
    assert result.result["error_code"] == "runtime211_missing_translated_docx"
    assert "translated docx" in result.result["message"]
