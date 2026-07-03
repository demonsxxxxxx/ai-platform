from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from app.control_plane_contracts import sanitize_public_payload

CONTEXT_MANIFEST_SCHEMA_VERSION = "ai-platform.context-manifest.v1"
EXECUTOR_CONTEXT_PACK_SCHEMA_VERSION = "ai-platform.executor-context-pack.v1"
DEFAULT_CONTEXT_MANIFEST_VERSION = "v1"
CONTEXT_RETRIEVAL_TOOLS = (
    "read_session_messages",
    "read_context_file",
    "read_run_artifact",
    "stage_context_file_to_workspace",
    "search_memory",
)
_PRIVATE_KEY_MARKERS = (
    "storage_key",
    "raw_storage_key",
    "executor_private_payload",
    "runtime_private_payload",
    "sandbox_workdir",
    "private_payload",
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_iso_timestamp(value: object) -> str:
    text = _safe_text(value)
    if not text:
        return _utc_now_iso()
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return _utc_now_iso()
    return text


def _safe_text(value: object, *, limit: int | None = None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    sanitized = sanitize_public_payload(text)
    text = sanitized if isinstance(sanitized, str) else ""
    lowered = text.lower()
    if any(marker in lowered for marker in _PRIVATE_KEY_MARKERS):
        return ""
    if limit is not None and len(text) > limit:
        return text[: max(0, limit)].rstrip()
    return text


def _safe_id(value: object) -> str:
    return str(value or "").strip()


def _sanitize_manifest_value(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(marker in key_text.lower() for marker in _PRIVATE_KEY_MARKERS):
                continue
            cleaned_item = _sanitize_manifest_value(item)
            if cleaned_item is not None:
                cleaned[key_text] = cleaned_item
        return cleaned
    if isinstance(value, list):
        return [
            item
            for item in (_sanitize_manifest_value(entry) for entry in value)
            if item is not None
        ]
    if isinstance(value, str):
        return _safe_text(value)
    return sanitize_public_payload(value)


def sanitize_context_manifest_payload(value: Any) -> dict[str, Any]:
    """Return a manifest payload with private executor/storage fields removed."""
    sanitized = _sanitize_manifest_value(value)
    return sanitized if isinstance(sanitized, dict) else {}


def public_context_manifest_projection(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return a counts-and-flags projection without material IDs or tool internals."""
    safe_manifest = sanitize_context_manifest_payload(manifest)
    available_tools = [
        str(tool)
        for tool in safe_manifest.get("available_retrieval_tools") or []
        if str(tool) in CONTEXT_RETRIEVAL_TOOLS
    ]
    return {
        "schema_version": CONTEXT_MANIFEST_SCHEMA_VERSION,
        "context_manifest_version": str(
            safe_manifest.get("context_manifest_version") or DEFAULT_CONTEXT_MANIFEST_VERSION
        ),
        "generated_at": _safe_iso_timestamp(safe_manifest.get("generated_at")),
        "referenced_materials": {
            "message_count": len(safe_manifest.get("recent_messages") or []),
            "file_count": len(safe_manifest.get("files") or []),
            "artifact_count": len(safe_manifest.get("artifacts") or []),
            "memory_record_count": len(safe_manifest.get("memory_records") or []),
            "source_run_count": len(safe_manifest.get("source_runs") or []),
        },
        "retrieval": {
            "available": bool(available_tools),
            "tool_count": len(available_tools),
            "workspace_staging_available": "stage_context_file_to_workspace" in available_tools,
        },
        "redaction": {
            "private_payloads_removed": True,
            "object_locator_refs_removed": True,
            "deleted_memory_excluded": True,
        },
        "audit": {
            "retrieval_required_for_full_content": True,
            "scope_bound": "tenant/workspace/user/session/run",
        },
    }


def _display_name_from_row(row: dict[str, Any]) -> str:
    label = _safe_text(row.get("original_name") or row.get("label") or row.get("name"))
    if label:
        return PurePosixPath(label).name
    return _safe_id(row.get("id") or row.get("file_id") or row.get("artifact_id"))


def _message_token_count(value: str) -> int:
    return len(value.split())


def _can_inline_file_preview(content_type: str) -> bool:
    return content_type.startswith("text/") or content_type in {
        "application/json",
        "application/yaml",
        "application/x-yaml",
    }


class ContextPlanner:
    """Build bounded context manifests for executor prompts and retrieval tools."""

    def __init__(
        self,
        *,
        max_inline_message_chars: int = 640,
        max_inline_file_preview_chars: int = 1024,
        max_inline_file_bytes: int = 8192,
        recent_message_limit: int = 8,
        token_budget: int = 1200,
        context_manifest_version: str = DEFAULT_CONTEXT_MANIFEST_VERSION,
    ) -> None:
        self.max_inline_message_chars = max(0, int(max_inline_message_chars))
        self.max_inline_file_preview_chars = max(0, int(max_inline_file_preview_chars))
        self.max_inline_file_bytes = max(0, int(max_inline_file_bytes))
        self.recent_message_limit = max(0, int(recent_message_limit))
        self.token_budget = max(1, int(token_budget))
        self.context_manifest_version = context_manifest_version or DEFAULT_CONTEXT_MANIFEST_VERSION

    def plan(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        run_id: str,
        agent_id: str,
        skill_id: str,
        current_message: str,
        recent_messages: list[dict[str, Any]] | None = None,
        context_chips: list[str] | None = None,
        files: list[dict[str, Any]] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        memory_records: list[dict[str, Any]] | None = None,
        source_run_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return the bounded manifest that is safe to include as a prompt index."""
        budget = _InlineBudget(self.token_budget)
        message_refs = self._message_refs(list(recent_messages or []), budget=budget)
        file_refs = self._file_refs(list(files or []), budget=budget)
        artifact_refs = self._artifact_refs(list(artifacts or []))
        memory_refs = self._memory_refs(list(memory_records or []))
        chip_refs = [
            chip for chip in (_safe_text(item, limit=160) for item in context_chips or []) if chip
        ]
        safe_source_runs = [
            {"run_id": run_id}
            for run_id in dict.fromkeys(_safe_id(item) for item in source_run_ids or [])
            if run_id
        ]
        return {
            "schema_version": CONTEXT_MANIFEST_SCHEMA_VERSION,
            "context_manifest_version": self.context_manifest_version,
            "generated_at": _utc_now_iso(),
            "scope": {
                "tenant_id": _safe_id(tenant_id),
                "workspace_id": _safe_id(workspace_id),
                "user_id": _safe_id(user_id),
                "session_id": _safe_id(session_id),
                "run_id": _safe_id(run_id),
                "agent_id": _safe_id(agent_id),
                "skill_id": _safe_id(skill_id),
            },
            "current_message": _safe_text(current_message, limit=self.max_inline_message_chars * 2),
            "recent_messages": message_refs,
            "context_chips": chip_refs,
            "files": file_refs,
            "artifacts": artifact_refs,
            "memory_records": memory_refs,
            "source_runs": safe_source_runs,
            "budget": {
                "max_prompt_tokens": self.token_budget,
                "recent_message_limit": self.recent_message_limit,
                "max_inline_message_chars": self.max_inline_message_chars,
                "max_inline_file_bytes": self.max_inline_file_bytes,
                "inline_tokens_used": budget.used_tokens,
                "inline_budget_exhausted": budget.exhausted,
            },
            "available_retrieval_tools": list(CONTEXT_RETRIEVAL_TOOLS),
            "redaction": {
                "private_payloads_removed": True,
                "object_locator_refs_removed": True,
                "deleted_memory_excluded": True,
            },
            "audit": {
                "retrieval_required_for_full_content": True,
                "scope_bound": "tenant/workspace/user/session/run",
            },
        }

    def executor_context_pack(self, manifest: dict[str, Any]) -> dict[str, Any]:
        """Wrap a manifest in the existing executor context-pack contract."""
        safe_manifest = sanitize_context_manifest_payload(manifest)
        counts = {
            "message_count": len(safe_manifest.get("recent_messages") or []),
            "file_count": len(safe_manifest.get("files") or []),
            "artifact_count": len(safe_manifest.get("artifacts") or []),
            "memory_record_count": len(safe_manifest.get("memory_records") or []),
        }
        prompt_summary = (
            "Context manifest: "
            f"{counts['message_count']} message refs, "
            f"{counts['file_count']} file refs, "
            f"{counts['artifact_count']} artifact refs, "
            f"{counts['memory_record_count']} memory refs. "
            "Use context retrieval tools for full message, file, artifact, or memory content."
        )
        return {
            "schema_version": EXECUTOR_CONTEXT_PACK_SCHEMA_VERSION,
            "source": "context_manifest",
            "context_manifest": safe_manifest,
            "referenced_materials": counts,
            "used_context_summary": {
                "source": "stored_context_snapshot",
                "input_keys": ["context_manifest"],
                "memory_policy_source": "not_recorded",
                "long_term_memory_read": False,
            },
            "latest_artifact_version": None,
            "execution_tier": "sdk_only_writing",
            "context_pack_version": str(safe_manifest.get("context_manifest_version") or DEFAULT_CONTEXT_MANIFEST_VERSION),
            "context_pack_generated_at": str(safe_manifest.get("generated_at") or _utc_now_iso()),
            "prompt_summary": prompt_summary,
        }

    def _message_refs(self, rows: list[dict[str, Any]], *, budget: "_InlineBudget") -> list[dict[str, Any]]:
        selected = rows[-self.recent_message_limit :] if self.recent_message_limit else []
        result: list[dict[str, Any]] = []
        for row in selected:
            if row.get("requires_retrieval") and not row.get("content"):
                result.append(
                    {
                        "message_id": _safe_id(row.get("message_id") or row.get("id")),
                        "requires_retrieval": True,
                    }
                )
                continue
            content = _safe_text(row.get("content"))
            candidate_tokens = _message_token_count(content)
            inline_content = None
            omitted_for_budget = False
            if content and len(content) <= self.max_inline_message_chars:
                if budget.try_consume(candidate_tokens):
                    inline_content = content
                else:
                    omitted_for_budget = True
            result.append(
                {
                    "message_id": _safe_id(row.get("message_id") or row.get("id")),
                    "run_id": _safe_id(row.get("run_id")),
                    "role": _safe_text(row.get("role"), limit=32) or "unknown",
                    "inline_content": inline_content,
                    "summary": None
                    if inline_content
                    else "Content omitted from manifest; use scoped retrieval."
                    if omitted_for_budget
                    else self._summary(content),
                    "approx_tokens": candidate_tokens,
                }
            )
        return result

    def _file_refs(self, rows: list[dict[str, Any]], *, budget: "_InlineBudget") -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in rows:
            if row.get("requires_retrieval") and not row.get("content_type") and not row.get("text_preview"):
                result.append(
                    {
                        "file_id": _safe_id(row.get("file_id") or row.get("id")),
                        "requires_retrieval": True,
                    }
                )
                continue
            size_bytes = int(row.get("size_bytes") or 0)
            content_type = _safe_text(row.get("content_type"), limit=128)
            preview = _safe_text(row.get("text_preview"), limit=self.max_inline_file_preview_chars)
            inline_preview = (
                preview
                if preview
                and size_bytes <= self.max_inline_file_bytes
                and _can_inline_file_preview(content_type)
                and budget.try_consume(_message_token_count(preview))
                else None
            )
            result.append(
                {
                    "file_id": _safe_id(row.get("file_id") or row.get("id")),
                    "name": _display_name_from_row(row),
                    "content_type": content_type,
                    "size_bytes": size_bytes,
                    "inline_preview": inline_preview,
                    "requires_retrieval": inline_preview is None,
                }
            )
        return result

    def _artifact_refs(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in rows:
            if row.get("requires_retrieval") and not row.get("artifact_type") and not row.get("label"):
                result.append(
                    {
                        "artifact_id": _safe_id(row.get("artifact_id") or row.get("id")),
                        "requires_retrieval": True,
                    }
                )
                continue
            result.append(
                {
                    "artifact_id": _safe_id(row.get("artifact_id") or row.get("id")),
                    "run_id": _safe_id(row.get("run_id")),
                    "artifact_type": _safe_text(row.get("artifact_type"), limit=96),
                    "label": _display_name_from_row(row),
                    "size_bytes": int(row.get("size_bytes") or 0),
                    "requires_retrieval": True,
                }
            )
        return result

    def _memory_refs(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in rows:
            status = str(row.get("status") or "active")
            if status != "active" or row.get("deleted_at"):
                continue
            result.append(
                {
                    "memory_record_id": _safe_id(row.get("memory_record_id") or row.get("id")),
                    "record_type": _safe_text(row.get("record_type"), limit=64),
                    "status": "active",
                }
            )
        return result

    def _summary(self, content: str) -> str:
        if not content:
            return "Content omitted from manifest; use scoped retrieval."
        if _message_token_count(content) > self.token_budget:
            return "Content omitted from manifest; use scoped retrieval."
        return content[: self.max_inline_message_chars].rstrip()


class _InlineBudget:
    def __init__(self, max_tokens: int) -> None:
        self.max_tokens = max(1, int(max_tokens))
        self.used_tokens = 0
        self.exhausted = False

    def try_consume(self, tokens: int) -> bool:
        normalized = max(0, int(tokens))
        if normalized == 0:
            return True
        if self.exhausted:
            return False
        if self.used_tokens + normalized > self.max_tokens:
            self.exhausted = True
            return False
        self.used_tokens += normalized
        if self.used_tokens >= self.max_tokens:
            self.exhausted = True
        return True
