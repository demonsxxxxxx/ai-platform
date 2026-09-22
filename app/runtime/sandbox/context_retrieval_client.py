from __future__ import annotations

import base64
import binascii
import hashlib
import os
import uuid
from pathlib import Path
from typing import Any

import httpx

from app.context.retrieval import ContextRetrievalDenied
from app.path_safety import ensure_creatable_inside
from app.runtime.sandbox.contracts import (
    PROFILE_DRIVE_STAGE_MAX_BYTES,
    PROFILE_DRIVE_STAGE_TOOL,
    ContextRetrievalScope,
)
from app.validation import assert_safe_id


class PlatformContextRetrievalClient:
    """Retrieve snapshot-authorized context through the platform callback boundary."""

    def __init__(
        self,
        *,
        callback_url: str,
        callback_token_id: str,
        callback_token: str,
        attempt_id: str,
        scope: ContextRetrievalScope,
        workspace_root: str | Path | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._callback_url = callback_url
        self._callback_token_id = callback_token_id
        self._callback_token = callback_token
        self._attempt_id = assert_safe_id(attempt_id, "attempt_id")
        self._scope = scope
        self._workspace_root = Path(workspace_root) if workspace_root is not None else None
        self._timeout_seconds = max(1.0, float(timeout_seconds))

    def _require_scope(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        run_id: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        expected = self._scope
        if (
            tenant_id != expected.tenant_id
            or workspace_id != expected.workspace_id
            or user_id != expected.user_id
            or session_id != expected.session_id
            or (run_id is not None and run_id != expected.run_id)
            or (agent_id is not None and agent_id != expected.agent_id)
        ):
            raise ContextRetrievalDenied("context_scope_denied")

    async def execute(
        self,
        action: str,
        scoped_identity: object,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_scope(
            tenant_id=str(getattr(scoped_identity, "tenant_id", "")),
            workspace_id=str(getattr(scoped_identity, "workspace_id", "")),
            user_id=str(getattr(scoped_identity, "user_id", "")),
            session_id=str(getattr(scoped_identity, "session_id", "")),
            run_id=str(getattr(scoped_identity, "run_id", "")),
            agent_id=str(getattr(scoped_identity, "agent_id", "")),
        )
        if action == PROFILE_DRIVE_STAGE_TOOL:
            if set(arguments) != {"path"} or not isinstance(arguments.get("path"), str):
                raise ContextRetrievalDenied("context_scope_denied")
            return await self.stage_profile_drive_file_to_workspace(
                path=arguments["path"],
                workspace_root=self._required_workspace_root(),
                tenant_id=self._scope.tenant_id,
                workspace_id=self._scope.workspace_id,
                user_id=self._scope.user_id,
                session_id=self._scope.session_id,
                run_id=self._scope.run_id,
                agent_id=self._scope.agent_id,
            )
        result = await self._request(action, arguments)
        if action == "stage_context_file_to_workspace":
            max_bytes = max(1, int(arguments.get("max_bytes", 1048576)))
            return self._stage_result(
                result,
                id_key="file_id",
                expected_id=str(arguments.get("file_id") or ""),
                workspace_root=str(self._required_workspace_root()),
                max_bytes=max_bytes,
            )
        if action == "stage_run_artifact_to_workspace":
            max_bytes = max(1, int(arguments.get("max_bytes", 16777216)))
            return self._stage_result(
                result,
                id_key="artifact_id",
                expected_id=str(arguments.get("artifact_id") or ""),
                workspace_root=str(self._required_workspace_root()),
                max_bytes=max_bytes,
            )
        return result

    def _required_workspace_root(self) -> Path:
        if self._workspace_root is None:
            raise RuntimeError("context_retrieval_workspace_unavailable")
        return self._workspace_root

    async def _request(self, action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "session_id": self._scope.session_id,
            "run_id": self._scope.run_id,
            "attempt_id": self._attempt_id,
            "callback_token_id": self._callback_token_id,
            "action": action,
            "arguments": arguments,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    self._callback_url,
                    json=payload,
                    headers={"X-AI-Platform-Callback-Token": self._callback_token},
                )
        except Exception as exc:
            raise RuntimeError("context_retrieval_callback_failed") from exc
        if response.status_code in {401, 403, 409, 413, 422}:
            raise ContextRetrievalDenied("context_scope_denied")
        if response.status_code != 200:
            raise RuntimeError("context_retrieval_callback_failed")
        body = response.json()
        result = body.get("result") if isinstance(body, dict) else None
        if not isinstance(result, dict):
            raise RuntimeError("context_retrieval_callback_invalid")
        return result

    async def read_run_artifact(
        self,
        *,
        artifact_id: str,
        max_bytes: int = 65536,
        **scope: str,
    ) -> dict[str, Any]:
        self._require_scope(**scope)
        return await self._request(
            "read_run_artifact",
            {"artifact_id": artifact_id, "max_bytes": max_bytes},
        )

    async def stage_context_file_to_workspace(
        self,
        *,
        file_id: str,
        workspace_root: str,
        max_bytes: int = 1048576,
        **scope: str,
    ) -> dict[str, Any]:
        self._require_scope(**scope)
        result = await self._request(
            "stage_context_file_to_workspace",
            {"file_id": file_id, "max_bytes": max_bytes},
        )
        return self._stage_result(
            result,
            id_key="file_id",
            expected_id=file_id,
            workspace_root=workspace_root,
            max_bytes=max_bytes,
        )

    async def stage_run_artifact_to_workspace(
        self,
        *,
        artifact_id: str,
        workspace_root: str,
        max_bytes: int = 16777216,
        **scope: str,
    ) -> dict[str, Any]:
        self._require_scope(**scope)
        result = await self._request(
            "stage_run_artifact_to_workspace",
            {"artifact_id": artifact_id, "max_bytes": max_bytes},
        )
        return self._stage_result(
            result,
            id_key="artifact_id",
            expected_id=artifact_id,
            workspace_root=workspace_root,
            max_bytes=max_bytes,
        )

    async def stage_profile_drive_file_to_workspace(
        self,
        *,
        path: str,
        workspace_root: str | Path,
        **scope: str,
    ) -> dict[str, Any]:
        self._require_scope(**scope)
        normalized_path = path.replace("\\", "/")
        name = normalized_path.rsplit("/", 1)[-1]
        if not path or len(path) > 1024 or "\x00" in path or name in {"", ".", ".."}:
            raise ContextRetrievalDenied("context_scope_denied")
        path_segment = hashlib.sha256(normalized_path.encode("utf-8")).hexdigest()[:16]
        target = Path(workspace_root) / "context" / "profile-drive" / path_segment / name
        ensure_creatable_inside(workspace_root, target, "context_retrieval_workspace_escape")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".staging-{uuid.uuid4().hex}.part")
        bytes_staged = 0
        payload = {
            "session_id": self._scope.session_id,
            "run_id": self._scope.run_id,
            "attempt_id": self._attempt_id,
            "callback_token_id": self._callback_token_id,
            "action": PROFILE_DRIVE_STAGE_TOOL,
            "arguments": {"path": path},
        }
        try:
            timeout = httpx.Timeout(None, connect=self._timeout_seconds)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    self._callback_url,
                    json=payload,
                    headers={"X-AI-Platform-Callback-Token": self._callback_token},
                ) as response:
                    if response.status_code == 413:
                        raise ContextRetrievalDenied("profile_drive_file_too_large")
                    if response.status_code in {401, 403, 409, 422}:
                        raise ContextRetrievalDenied("context_scope_denied")
                    if response.status_code != 200:
                        raise RuntimeError("context_retrieval_callback_failed")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise RuntimeError("context_retrieval_callback_invalid")
                    try:
                        expected_bytes = int(response.headers["content-length"])
                    except (KeyError, TypeError, ValueError) as exc:
                        raise RuntimeError("context_retrieval_callback_invalid") from exc
                    if expected_bytes < 0 or expected_bytes > PROFILE_DRIVE_STAGE_MAX_BYTES:
                        raise ContextRetrievalDenied("profile_drive_file_too_large")
                    with temporary.open("xb") as output:
                        async for chunk in response.aiter_bytes():
                            bytes_staged += len(chunk)
                            if bytes_staged > expected_bytes or bytes_staged > PROFILE_DRIVE_STAGE_MAX_BYTES:
                                raise ContextRetrievalDenied("profile_drive_file_too_large")
                            output.write(chunk)
                    if bytes_staged != expected_bytes:
                        raise RuntimeError("context_retrieval_callback_invalid")
            os.replace(temporary, target)
        except (ContextRetrievalDenied, RuntimeError):
            temporary.unlink(missing_ok=True)
            raise
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            raise RuntimeError("context_retrieval_callback_failed") from exc
        workspace_path = target.relative_to(Path(workspace_root)).as_posix()
        return {
            "workspace_path": workspace_path,
            "bytes_staged": bytes_staged,
            "max_bytes": PROFILE_DRIVE_STAGE_MAX_BYTES,
            "audit": {
                "action": f"context_retrieval.{PROFILE_DRIVE_STAGE_TOOL}",
                "bytes_read": bytes_staged,
                "max_bytes": PROFILE_DRIVE_STAGE_MAX_BYTES,
                "result": "staged",
            },
            "redaction": {"source_path_removed": True},
        }

    async def search_memory(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        query: str,
        limit: int = 10,
        max_tokens: int = 1200,
    ) -> dict[str, Any]:
        self._require_scope(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
        )
        return await self._request(
            "search_memory",
            {"query": query, "limit": limit, "max_tokens": max_tokens},
        )

    def _stage_result(
        self,
        result: dict[str, Any],
        *,
        id_key: str,
        expected_id: str,
        workspace_root: str,
        max_bytes: int,
    ) -> dict[str, Any]:
        if str(result.get(id_key) or "") != expected_id:
            raise ContextRetrievalDenied("context_scope_denied")
        try:
            raw_bytes = base64.b64decode(str(result.get("content_base64") or ""), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RuntimeError("context_retrieval_callback_invalid") from exc
        if len(raw_bytes) > max(1, int(max_bytes)) or int(result.get("bytes_read") or -1) != len(raw_bytes):
            raise ContextRetrievalDenied("context_scope_denied")
        safe_id = "".join(char if char.isalnum() or char in "-_" else "_" for char in expected_id)
        raw_name = str(result.get("name") or "context.bin").replace("\\", "/")
        safe_name = raw_name.rsplit("/", 1)[-1] or "context.bin"
        if safe_name in {".", ".."}:
            safe_name = "context.bin"
        target = Path(workspace_root) / "context" / (safe_id or "context-file") / safe_name
        ensure_creatable_inside(workspace_root, target, "context_retrieval_workspace_escape")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw_bytes)
        workspace_path = f"context/{safe_id or 'context-file'}/{safe_name}"
        return {
            id_key: expected_id,
            "workspace_path": workspace_path,
            "bytes_staged": len(raw_bytes),
            "max_bytes": max(1, int(max_bytes)),
            "audit": {
                "action": f"context_retrieval.{('stage_context_file_to_workspace' if id_key == 'file_id' else 'stage_run_artifact_to_workspace')}",
                "bytes_read": len(raw_bytes),
                "max_bytes": max(1, int(max_bytes)),
                "result": "staged",
            },
            "redaction": {"object_locator_refs_removed": True},
        }
