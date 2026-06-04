import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote

import httpx

from app import repositories
from app.db import transaction
from app.executors.base import ArtifactManifest, ExecutorEventSink, ExecutorResult, RunPayload
from app.settings import get_settings
from app.storage import ObjectStorage


Runtime211RunPayload = RunPayload
FILE_MARKER_PATTERN = re.compile(r"\[\[FILE:([^\]]+)\]\]")


@dataclass(frozen=True)
class Runtime211FileRef:
    path: str
    name: str | None = None


@dataclass(frozen=True)
class Runtime211StreamResult:
    deltas: list[str]
    file_refs: list[Runtime211FileRef]
    task_id: str | None = None
    error_message: str | None = None

    @property
    def text(self) -> str:
        return "".join(self.deltas)


class Runtime211BridgeError(ValueError):
    def __init__(self, error_code: str, message: str, *, stage: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.stage = stage
        self.retryable = retryable


def parse_sse_deltas(raw: str) -> list[str]:
    return parse_sse_stream(raw).deltas


def parse_sse_stream(raw: str) -> Runtime211StreamResult:
    deltas: list[str] = []
    file_refs: list[Runtime211FileRef] = []
    seen_files: set[str] = set()
    task_id: str | None = None
    error_message: str | None = None

    def add_file_ref(ref: Runtime211FileRef | None) -> None:
        if ref is None:
            return
        path = ref.path.strip()
        if not path or path in seen_files:
            return
        seen_files.add(path)
        file_refs.append(Runtime211FileRef(path=path, name=ref.name))

    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            deltas.append(data)
            continue
        if not isinstance(payload, dict):
            continue
        current_task_id = payload.get("task_id") or payload.get("taskId")
        if current_task_id and task_id is None:
            task_id = str(current_task_id)
        current_error = payload.get("error") or payload.get("error_message") or payload.get("errorMessage")
        if current_error and error_message is None:
            error_message = str(current_error)
        delta = _payload_delta(payload)
        if delta:
            deltas.append(delta)
        for ref in _payload_file_refs(payload):
            add_file_ref(ref)
    for path in extract_file_markers("".join(deltas)):
        add_file_ref(Runtime211FileRef(path=path))
    return Runtime211StreamResult(
        deltas=deltas,
        file_refs=file_refs,
        task_id=task_id,
        error_message=error_message,
    )


def _payload_delta(payload: dict[str, Any]) -> str:
    for key in ("delta", "content", "text", "message"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return ""


def _payload_file_refs(payload: dict[str, Any]) -> list[Runtime211FileRef]:
    refs: list[Runtime211FileRef] = []
    for key in ("files", "result_files", "resultFiles", "artifacts"):
        value = payload.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            ref = _file_ref_from_payload(item)
            if ref is not None:
                refs.append(ref)
    return refs


def _file_ref_from_payload(value: Any) -> Runtime211FileRef | None:
    if isinstance(value, str):
        path = value.strip()
        return Runtime211FileRef(path=path) if path else None
    if not isinstance(value, dict):
        return None
    path = ""
    for key in ("path", "file_path", "filePath", "url", "file_key", "fileKey", "key"):
        if value.get(key):
            path = str(value[key]).strip()
            break
    if not path:
        return None
    name = None
    for key in ("name", "filename", "file_name", "fileName"):
        if value.get(key):
            name = str(value[key]).strip()
            break
    return Runtime211FileRef(path=path, name=name or None)


def extract_file_markers(text: str) -> list[str]:
    seen: set[str] = set()
    paths: list[str] = []
    for match in FILE_MARKER_PATTERN.finditer(text or ""):
        path = match.group(1).strip()
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def strip_file_markers(text: str) -> str:
    cleaned = FILE_MARKER_PATTERN.sub("", text or "")
    return re.sub(r"[ \t]+\n", "\n", cleaned).strip()


def _message_for_skill(skill_id: str, input_payload: dict[str, Any]) -> str:
    explicit = str(input_payload.get("message") or input_payload.get("prompt") or "").strip()
    if explicit:
        return explicit
    if skill_id == "baoyu-translate":
        target = str(
            input_payload.get("target_language")
            or input_payload.get("target_lang")
            or input_payload.get("language")
            or ""
        ).strip()
        if target:
            return f"请将上传的 Word 文档翻译为{target}，并输出 Word 翻译版。"
        return "请将上传的 Word 文档翻译为目标语言，并输出 Word 翻译版。"
    if skill_id == "ragflow-knowledge-search":
        return "请基于公司知识库回答用户问题。"
    return "请审核上传的 Word 文档，输出带批注的 Word 文件。"


def _artifact_content_type(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if lower.endswith(".txt"):
        return "text/plain; charset=utf-8"
    if lower.endswith(".md"):
        return "text/markdown; charset=utf-8"
    return "application/octet-stream"


def _artifact_type(filename: str, skill_id: str | None = None) -> str:
    lower = filename.lower()
    if skill_id == "baoyu-translate" and lower.endswith(".docx"):
        return "translated_docx"
    if lower.endswith("_reviewed.docx"):
        return "reviewed_docx"
    if lower.endswith("_translated.docx") or "翻译" in filename:
        return "translated_docx"
    if lower.endswith(".docx"):
        return "result_docx"
    if lower.endswith(".txt"):
        return "report_txt"
    return "runtime_file"


def _artifact_label(filename: str, skill_id: str | None = None) -> str:
    artifact_type = _artifact_type(filename, skill_id)
    if artifact_type == "reviewed_docx":
        return "批注 Word"
    if artifact_type == "translated_docx":
        return "翻译 Word"
    if artifact_type == "report_txt":
        return "详细报告"
    if artifact_type == "result_docx":
        return "Word 文件"
    return filename


def _filename_from_path(path: str) -> str:
    clean = unquote(path).replace("\\", "/").rstrip("/")
    return PurePosixPath(clean).name or "runtime-artifact.bin"


def _filename_from_file_ref(ref: Runtime211FileRef) -> str:
    if ref.name:
        return _filename_from_path(ref.name)
    return _filename_from_path(ref.path)


def is_failure_text(text: str) -> bool:
    failure_prefixes = (
        "调用失败",
        "技能执行失败",
        "技能执行部分完成",
    )
    for line in (text or "").splitlines()[:100]:
        normalized = line.strip()
        if not normalized or normalized.startswith("[进度]"):
            continue
        if normalized.startswith(failure_prefixes):
            return True
    return False


class Runtime211Adapter:
    adapter_version = "runtime211-adapter/2"
    executor_type = "runtime211"
    executor_version = "runtime211-http"
    capabilities = {
        "artifacts": True,
        "streaming": True,
        "tools": False,
    }

    def __init__(self) -> None:
        self.base_url = get_settings().runtime_211_base_url.rstrip("/")

    async def submit_run(self, payload: RunPayload, event_sink: ExecutorEventSink | None = None) -> ExecutorResult:
        runtime_file_ids: list[str] = []
        try:
            runtime_file_ids = await self._upload_platform_files(payload)
            request_body = {
                "tenant_id": payload.tenant_id,
                "workspace_id": payload.workspace_id,
                "session_id": payload.session_id,
                "run_id": payload.run_id,
                "agent_id": payload.agent_id,
                "skill_id": payload.skill_id,
                "preferred_skill_id": payload.skill_id,
                "work_id": str(payload.input.get("work_id") or payload.session_id or payload.run_id),
                "message": _message_for_skill(payload.skill_id, payload.input),
                "history": payload.input.get("history") if isinstance(payload.input.get("history"), list) else [],
                "file_id": runtime_file_ids[0] if runtime_file_ids else None,
                "file_ids": runtime_file_ids,
                "input": payload.input,
                "queue_mode": str(payload.input.get("queue_mode") or payload.input.get("execution_mode") or "queued"),
            }
            async with httpx.AsyncClient(timeout=None) as client:
                stream = await self._read_chat_stream(client, request_body, event_sink=event_sink)
                artifacts = await self._collect_artifacts(client, payload, stream.file_refs)
        except Runtime211BridgeError as exc:
            return self._failure_result(
                payload,
                error_code=exc.error_code,
                message=str(exc),
                error_stage=exc.stage,
                retryable=exc.retryable,
                runtime_file_ids=runtime_file_ids,
            )
        except httpx.HTTPError as exc:
            return self._failure_result(
                payload,
                error_code="runtime211_http_error",
                message=str(exc),
                error_stage="chat",
                retryable=True,
                runtime_file_ids=runtime_file_ids,
            )

        result_text = stream.text
        public_message = strip_file_markers(result_text) or "任务完成"
        if stream.error_message:
            return self._failure_result(
                payload,
                error_code="runtime211_stream_error",
                message=stream.error_message,
                error_stage="stream",
                retryable=False,
                runtime_file_ids=runtime_file_ids,
                runtime_task_id=stream.task_id,
                artifacts=artifacts,
            )
        if is_failure_text(result_text):
            return self._failure_result(
                payload,
                error_code="runtime211_reported_failure",
                message=public_message,
                error_stage="stream",
                retryable=False,
                runtime_file_ids=runtime_file_ids,
                runtime_task_id=stream.task_id,
                artifacts=artifacts,
            )
        if payload.skill_id == "baoyu-translate" and not any(
            artifact.artifact_type == "translated_docx" for artifact in artifacts
        ):
            return self._failure_result(
                payload,
                error_code="runtime211_missing_translated_docx",
                message="runtime211 completed baoyu-translate without a translated docx artifact",
                error_stage="artifact",
                retryable=False,
                runtime_file_ids=runtime_file_ids,
                runtime_task_id=stream.task_id,
                artifacts=artifacts,
            )
        return ExecutorResult(
            status="succeeded",
            adapter_version=self.adapter_version,
            executor_type=self.executor_type,
            executor_version=self.executor_version,
            capabilities=self.capabilities,
            result={
                "message": public_message,
                "artifact_count": len(artifacts),
            },
            artifacts=artifacts,
            executor_payload=self._executor_payload(runtime_file_ids, runtime_task_id=stream.task_id),
        )

    def _failure_result(
        self,
        payload: RunPayload,
        *,
        error_code: str,
        message: str,
        error_stage: str,
        retryable: bool,
        runtime_file_ids: list[str] | None = None,
        runtime_task_id: str | None = None,
        artifacts: list[ArtifactManifest] | None = None,
    ) -> ExecutorResult:
        collected_artifacts = artifacts or []
        return ExecutorResult(
            status="failed",
            adapter_version=self.adapter_version,
            executor_type=self.executor_type,
            executor_version=self.executor_version,
            capabilities=self.capabilities,
            result={
                "message": message or "runtime211 execution failed",
                "error_code": error_code,
                "error_stage": error_stage,
                "retryable": retryable,
                "artifact_count": len(collected_artifacts),
                "skill_id": payload.skill_id,
            },
            artifacts=collected_artifacts,
            executor_payload=self._executor_payload(runtime_file_ids or [], runtime_task_id=runtime_task_id),
        )

    async def _read_chat_stream(
        self,
        client: httpx.AsyncClient,
        request_body: dict[str, Any],
        *,
        event_sink: ExecutorEventSink | None,
    ) -> Runtime211StreamResult:
        raw_lines: list[str] = []
        async with client.stream("POST", f"{self.base_url}/api/chat/stream", json=request_body) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                raw_lines.append(line)
                if not line.startswith("data:"):
                    continue
                current = parse_sse_stream(line)
                for delta in current.deltas:
                    if event_sink is None:
                        continue
                    await event_sink(
                        event_type="assistant_delta",
                        stage="assistant",
                        message=delta,
                        payload={"delta": delta, "visible_to_user": True, "severity": "info"},
                    )
        return parse_sse_stream("\n".join(raw_lines))

    def _executor_payload(self, runtime_file_ids: list[str], *, runtime_task_id: str | None = None) -> dict[str, Any]:
        payload = {
            "runtime_upload_endpoint": "/api/upload",
            "runtime_endpoint": "/api/chat/stream",
            "runtime_base_url": self.base_url,
            "runtime_file_ids": runtime_file_ids,
        }
        if runtime_task_id:
            payload["runtime_task_id"] = runtime_task_id
        return payload

    async def _upload_platform_files(self, payload: RunPayload) -> list[str]:
        if not payload.file_ids:
            return []
        storage = ObjectStorage()
        uploaded: list[str] = []
        async with httpx.AsyncClient(timeout=120.0) as client:
            for file_id in payload.file_ids:
                async with transaction() as conn:
                    file_row = await repositories.get_run_file(
                        conn,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        file_id=file_id,
                )
                if file_row is None:
                    raise Runtime211BridgeError(
                        "runtime211_platform_file_not_bound",
                        f"Platform file is not bound to this run: {file_id}",
                        stage="upload",
                    )
                content = storage.get_bytes(storage_key=file_row["storage_key"])
                files = {
                    "file": (
                        file_row["original_name"],
                        content,
                        file_row["content_type"] or "application/octet-stream",
                    )
                }
                data = {
                    "tenant_id": payload.tenant_id,
                    "workspace_id": payload.workspace_id,
                    "agent_id": payload.agent_id,
                    "session_id": payload.session_id,
                }
                response = await client.post(f"{self.base_url}/api/upload", data=data, files=files)
                try:
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    raise Runtime211BridgeError(
                        "runtime211_upload_http_error",
                        str(exc),
                        stage="upload",
                        retryable=True,
                    ) from exc
                runtime_file_id = str(response.json().get("file_id") or "").strip()
                if not runtime_file_id:
                    raise Runtime211BridgeError(
                        "runtime211_upload_missing_file_id",
                        f"Runtime upload did not return file_id for {file_id}",
                        stage="upload",
                    )
                uploaded.append(runtime_file_id)
        return uploaded

    async def _collect_artifacts(
        self,
        client: httpx.AsyncClient,
        payload: RunPayload,
        runtime_files: list[Runtime211FileRef],
    ) -> list[ArtifactManifest]:
        artifacts: list[ArtifactManifest] = []
        storage = ObjectStorage()
        for index, runtime_file in enumerate(runtime_files, start=1):
            response = await client.get(f"{self.base_url}/api/file/download", params={"path": runtime_file.path})
            try:
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise Runtime211BridgeError(
                    "runtime211_download_http_error",
                    str(exc),
                    stage="download",
                    retryable=True,
                ) from exc
            filename = _filename_from_file_ref(runtime_file)
            content = response.content
            content_type = response.headers.get("content-type") or _artifact_content_type(filename)
            storage_key = (
                f"tenants/{payload.tenant_id}/workspaces/{payload.workspace_id}/"
                f"sessions/{payload.session_id}/runs/{payload.run_id}/artifacts/{index}/{filename}"
            )
            stored = storage.put_bytes(
                storage_key=storage_key,
                content=content,
                content_type=content_type,
            )
            artifacts.append(
                ArtifactManifest(
                    artifact_type=_artifact_type(filename, payload.skill_id),
                    label=_artifact_label(filename, payload.skill_id),
                    content_type=content_type,
                    storage_key=stored.storage_key,
                    size_bytes=stored.size_bytes,
                    manifest={
                        "source_executor": self.executor_type,
                        "runtime_path": runtime_file.path,
                        "runtime_name": runtime_file.name,
                    },
                )
            )
        return artifacts
