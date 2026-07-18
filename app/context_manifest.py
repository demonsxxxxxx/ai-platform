from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from app.control_plane_contracts import sanitize_public_payload

CONTEXT_MANIFEST_SCHEMA_VERSION = "ai-platform.context-manifest.v1"
EXECUTOR_CONTEXT_PACK_SCHEMA_VERSION = "ai-platform.executor-context-pack.v1"
DEFAULT_CONTEXT_MANIFEST_VERSION = "v1"
DEFAULT_CONTEXT_SELECTION_VERSION = "session-context-v1"
DEFAULT_MAX_INLINE_HISTORY_BYTES = 8192
DEFAULT_MAX_CURRENT_MESSAGE_BYTES = 16384
CONTEXT_RETRIEVAL_TOOLS = (
    "read_session_messages",
    "read_context_file",
    "read_run_artifact",
    "stage_context_file_to_workspace",
    "stage_run_artifact_to_workspace",
    "search_memory",
)


def available_context_retrieval_tools(manifest: dict[str, Any] | None) -> list[str]:
    """Return only advertised retrieval actions backed by non-empty manifest refs."""

    if not manifest or manifest.get("schema_version") != CONTEXT_MANIFEST_SCHEMA_VERSION:
        return []
    raw_advertised = manifest.get("available_retrieval_tools")
    if not isinstance(raw_advertised, list):
        return []
    advertised = {
        str(tool_name)
        for tool_name in raw_advertised
        if isinstance(tool_name, str) and tool_name in CONTEXT_RETRIEVAL_TOOLS
    }
    selected: list[str] = []
    for refs_key, tool_names in (
        ("recent_messages", ("read_session_messages",)),
        ("files", ("read_context_file", "stage_context_file_to_workspace")),
        ("artifacts", ("read_run_artifact", "stage_run_artifact_to_workspace")),
        ("memory_records", ("search_memory",)),
    ):
        refs = manifest.get(refs_key)
        if not isinstance(refs, list) or not refs:
            continue
        selected.extend(tool_name for tool_name in tool_names if tool_name in advertised)
    return selected
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


def utf8_token_estimate(value: object) -> int:
    """Conservatively estimate prompt tokens from UTF-8 bytes.

    A byte-per-token bound is deliberately pessimistic, but it is stable for
    whitespace-free CJK text and emoji where word-counting is unsafe.
    """

    return len(str(value or "").encode("utf-8"))


def truncate_utf8_text(value: object, *, max_bytes: int) -> str:
    """Return a UTF-8-safe prefix constrained by an independent byte cap."""

    text = str(value or "")
    encoded = text.encode("utf-8")
    if len(encoded) <= max(0, int(max_bytes)):
        return text
    return encoded[: max(0, int(max_bytes))].decode("utf-8", errors="ignore")


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
    selection = safe_manifest.get("selection")
    selection = selection if isinstance(selection, dict) else {}
    window_status = str(selection.get("status") or "complete")
    if window_status not in {"complete", "trimmed", "degraded"}:
        window_status = "degraded"
    # File rows are authorized before they reach the manifest.  Extract a
    # basename from the original value before generic payload sanitization
    # discards a Windows/absolute-looking path wholesale.
    raw_file_names = manifest.get("files") if isinstance(manifest, dict) else []
    selected_file_names: list[str] = []
    if isinstance(raw_file_names, list):
        for row in raw_file_names:
            if not isinstance(row, dict):
                continue
            raw_name = str(row.get("name") or "").replace("\\", "/")
            name = _safe_text(PurePosixPath(raw_name).name, limit=160)
            if not name:
                continue
            if name and name not in selected_file_names:
                selected_file_names.append(name)
            if len(selected_file_names) >= 8:
                break
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
        "context_window": {
            "status": window_status,
            "selection_version": _safe_text(
                selection.get("selection_version"), limit=64
            )
            or DEFAULT_CONTEXT_SELECTION_VERSION,
            "history_candidate_count": _safe_nonnegative_int(selection.get("history_candidate_count")),
            "history_inline_count": _safe_nonnegative_int(selection.get("history_inline_count")),
            "history_trimmed_count": _safe_nonnegative_int(selection.get("history_trimmed_count")),
            "legacy_history_excluded": bool(selection.get("legacy_history_excluded")),
            "selected_file_names": selected_file_names,
        },
        "retrieval": {
            "available": bool(available_tools),
            "tool_count": len(available_tools),
            "workspace_staging_available": any(
                tool in available_tools
                for tool in ("stage_context_file_to_workspace", "stage_run_artifact_to_workspace")
            ),
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


def _safe_nonnegative_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _display_name_from_row(row: dict[str, Any]) -> str:
    label = _safe_text(row.get("original_name") or row.get("label") or row.get("name"))
    if label:
        return PurePosixPath(label.replace("\\", "/")).name
    return _safe_id(row.get("id") or row.get("file_id") or row.get("artifact_id"))


def _message_token_count(value: str) -> int:
    return utf8_token_estimate(value)


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
        max_inline_message_bytes: int | None = None,
        max_inline_history_bytes: int = DEFAULT_MAX_INLINE_HISTORY_BYTES,
        context_manifest_version: str = DEFAULT_CONTEXT_MANIFEST_VERSION,
    ) -> None:
        self.max_inline_message_chars = max(0, int(max_inline_message_chars))
        self.max_inline_file_preview_chars = max(0, int(max_inline_file_preview_chars))
        self.max_inline_file_bytes = max(0, int(max_inline_file_bytes))
        self.recent_message_limit = max(0, int(recent_message_limit))
        self.token_budget = max(1, int(token_budget))
        self.max_inline_message_bytes = max(
            0,
            int(max_inline_message_bytes)
            if max_inline_message_bytes is not None
            else self.max_inline_message_chars * 4,
        )
        self.max_inline_history_bytes = max(0, int(max_inline_history_bytes))
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
        legacy_history_excluded: bool = False,
    ) -> dict[str, Any]:
        """Return the bounded manifest that is safe to include as a prompt index."""
        budget = _InlineBudget(self.token_budget, self.max_inline_history_bytes)
        # The current turn is authoritative prompt material. Reserve it once
        # before selecting historical messages, using the same UTF-8-safe
        # estimator as every other inline candidate.
        budget.reserve_current(current_message)
        message_refs, history_selection = self._message_refs(list(recent_messages or []), budget=budget)
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
            "current_message": truncate_utf8_text(
                _safe_text(current_message, limit=self.max_inline_message_chars * 2),
                max_bytes=min(
                    DEFAULT_MAX_CURRENT_MESSAGE_BYTES,
                    max(self.max_inline_message_bytes * 2, self.max_inline_message_bytes),
                ),
            ),
            "recent_messages": message_refs,
            "context_chips": chip_refs,
            "files": file_refs,
            "artifacts": artifact_refs,
            "memory_records": memory_refs,
            "source_runs": safe_source_runs,
            "selection": {
                "selection_version": DEFAULT_CONTEXT_SELECTION_VERSION,
                "status": "degraded"
                if legacy_history_excluded
                else "trimmed"
                if history_selection["trimmed_count"]
                else "complete",
                "history_candidate_count": history_selection["candidate_count"],
                "history_inline_count": history_selection["inline_count"],
                "history_trimmed_count": history_selection["trimmed_count"],
                "legacy_history_excluded": bool(legacy_history_excluded),
                "selection_order": "newest_first",
                "render_order": "chronological",
            },
            "budget": {
                "max_prompt_tokens": self.token_budget,
                "recent_message_limit": self.recent_message_limit,
                "max_inline_message_chars": self.max_inline_message_chars,
                "max_inline_message_bytes": self.max_inline_message_bytes,
                "max_inline_history_bytes": self.max_inline_history_bytes,
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

    def _message_refs(
        self,
        rows: list[dict[str, Any]],
        *,
        budget: "_InlineBudget",
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """Select newest eligible history first, then return it chronologically."""

        candidate_count = len(rows)
        newest_first = sorted(rows, key=_message_sort_key, reverse=True)[: self.recent_message_limit]
        result: list[tuple[tuple[int, str, str], dict[str, Any]]] = []
        inline_count = 0
        # Count-limit exclusions are material omissions too; otherwise a
        # public "complete" summary could claim full history when it was not
        # considered for inclusion.
        trimmed_count = candidate_count - len(newest_first)
        for row in newest_first:
            if row.get("requires_retrieval") and not row.get("content"):
                trimmed_count += 1
                result.append(
                    (_message_sort_key(row), {
                        "message_id": _safe_id(row.get("message_id") or row.get("id")),
                        "requires_retrieval": True,
                    })
                )
                continue
            content = _safe_text(row.get("content"))
            candidate_tokens = _message_token_count(content)
            inline_content = None
            omitted_from_inline = bool(content)
            if (
                content
                and len(content) <= self.max_inline_message_chars
                and utf8_token_estimate(content) <= self.max_inline_message_bytes
            ):
                if budget.try_consume(content):
                    inline_content = truncate_utf8_text(
                        content,
                        max_bytes=self.max_inline_message_bytes,
                    )
                    inline_count += 1
                    omitted_from_inline = False
            if omitted_from_inline:
                trimmed_count += 1
            result.append(
                (_message_sort_key(row), {
                    "message_id": _safe_id(row.get("message_id") or row.get("id")),
                    "run_id": _safe_id(row.get("run_id")),
                    "role": _safe_text(row.get("role"), limit=32) or "unknown",
                    "inline_content": inline_content,
                    "summary": None if inline_content else "Content omitted from manifest; use scoped retrieval.",
                    "approx_tokens": candidate_tokens,
                    "requires_retrieval": inline_content is None,
                })
            )
        result.sort(key=lambda item: item[0])
        return (
            [item[1] for item in result],
            {
                "candidate_count": candidate_count,
                "inline_count": inline_count,
                "trimmed_count": trimmed_count,
            },
        )

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
            preview = truncate_utf8_text(
                _safe_text(row.get("text_preview"), limit=self.max_inline_file_preview_chars),
                max_bytes=min(self.max_inline_file_bytes, self.max_inline_file_preview_chars * 4),
            )
            inline_preview = (
                preview
                if preview
                and size_bytes <= self.max_inline_file_bytes
                and _can_inline_file_preview(content_type)
                and budget.try_consume(preview)
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
        return truncate_utf8_text(
            content[: self.max_inline_message_chars].rstrip(),
            max_bytes=self.max_inline_message_bytes,
        )


class _InlineBudget:
    def __init__(self, max_tokens: int, max_bytes: int) -> None:
        self.max_tokens = max(1, int(max_tokens))
        self.max_bytes = max(0, int(max_bytes))
        self.used_tokens = 0
        self.used_bytes = 0
        self.exhausted = False

    def try_consume(self, value: str) -> bool:
        normalized = _message_token_count(value)
        byte_count = utf8_token_estimate(value)
        if normalized == 0:
            return True
        if (
            self.used_tokens + normalized > self.max_tokens
            or self.used_bytes + byte_count > self.max_bytes
        ):
            self.exhausted = True
            return False
        self.used_tokens += normalized
        self.used_bytes += byte_count
        if self.used_tokens >= self.max_tokens or self.used_bytes >= self.max_bytes:
            self.exhausted = True
        return True

    def reserve_current(self, value: str) -> None:
        """Reserve the current user turn once, without allowing it to be omitted."""

        normalized = _message_token_count(value)
        byte_count = utf8_token_estimate(value)
        if normalized == 0:
            return
        if normalized > self.max_tokens or byte_count > self.max_bytes:
            self.used_tokens = self.max_tokens
            self.used_bytes = self.max_bytes
            self.exhausted = True
            return
        self.used_tokens += normalized
        self.used_bytes += byte_count
        if self.used_tokens >= self.max_tokens or self.used_bytes >= self.max_bytes:
            self.exhausted = True


def _message_sort_key(row: dict[str, Any]) -> tuple[int, str, str]:
    """Return a stable run-generation and message-order key for manifest rendering."""

    generation = row.get("session_generation")
    safe_generation = generation if isinstance(generation, int) and not isinstance(generation, bool) else -1
    created_at = _safe_text(row.get("created_at"), limit=64)
    message_id = _safe_id(row.get("message_id") or row.get("id"))
    return safe_generation, created_at, message_id
