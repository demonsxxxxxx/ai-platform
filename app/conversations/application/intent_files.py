"""Conversation intent file metadata enrichment, independent of HTTP and storage."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FileSummary:
    file_id: str
    name: str = ""
    content_type: str = ""


_MISSING = object()


def _row_value(row: dict[str, Any], key: str, default: Any = None) -> Any:
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, TypeError):
        return default


def _file_ids_for_intent_lookup(file_ids: list[str], attachments: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for value in file_ids:
        if value not in result:
            result.append(value)
    for attachment in attachments:
        value = attachment.get("file_id") or attachment.get("key") or attachment.get("id")
        if isinstance(value, str) and value.startswith("file_") and value not in result:
            result.append(value)
    return result


def _file_row_matches_scope(
    row: dict[str, Any],
    *,
    tenant_id: str,
    user_id: str,
    session_id: str | None,
    workspace_id: str,
) -> bool:
    row_tenant_id = _row_value(row, "tenant_id", _MISSING)
    if row_tenant_id != tenant_id:
        return False
    row_workspace_id = _row_value(row, "workspace_id", _MISSING)
    if row_workspace_id != workspace_id:
        return False
    row_user_id = _row_value(row, "user_id", _MISSING)
    if row_user_id != user_id:
        return False
    row_session_id = _row_value(row, "session_id", _MISSING)
    if row_session_id is _MISSING:
        return False
    if row_session_id and row_session_id != session_id:
        return False
    run_id = _row_value(row, "run_id", _MISSING)
    return not (run_id is _MISSING or run_id)


def _file_summaries_from_attachments(attachments: list[dict[str, Any]]) -> list[FileSummary]:
    summaries: list[FileSummary] = []
    for attachment in attachments:
        value = attachment.get("file_id") or attachment.get("key") or attachment.get("id") or ""
        summaries.append(
            FileSummary(
                file_id=str(value),
                name=str(attachment.get("name") or attachment.get("filename") or ""),
                content_type=str(attachment.get("mimeType") or attachment.get("mime_type") or ""),
            )
        )
    return summaries


def _merge_file_summary(existing: FileSummary, incoming: FileSummary) -> FileSummary:
    return FileSummary(
        file_id=existing.file_id or incoming.file_id,
        name=existing.name or incoming.name,
        content_type=existing.content_type or incoming.content_type,
    )


def _merge_file_summaries(summaries: list[FileSummary], incoming: FileSummary) -> list[FileSummary]:
    if not incoming.file_id:
        return [*summaries, incoming]
    merged: list[FileSummary] = []
    replaced = False
    for item in summaries:
        if item.file_id == incoming.file_id:
            merged.append(_merge_file_summary(item, incoming))
            replaced = True
        else:
            merged.append(item)
    if not replaced:
        merged.append(incoming)
    return merged


def _file_summary_from_row(file_id: str, row: dict[str, Any]) -> FileSummary:
    return FileSummary(
        file_id=str(_row_value(row, "id") or file_id),
        name=str(_row_value(row, "original_name") or _row_value(row, "name") or ""),
        content_type=str(_row_value(row, "content_type") or _row_value(row, "mime_type") or ""),
    )


async def resolve_intent_files(
    conn: Any,
    *,
    file_ids: list[str],
    attachments: list[dict[str, Any]],
    tenant_id: str,
    user_id: str,
    session_id: str | None,
    workspace_id: str,
    get_file: Callable[..., Awaitable[dict[str, Any] | None]],
) -> list[FileSummary]:
    """Enrich intent metadata in the caller's transaction and effective scope.

    Client metadata keeps its existing precedence. Only missing summaries are
    read, in stable deduplicated order, and only exact-scope unbound rows enrich
    them. This does not admit files or bind them to a Run.
    """

    summaries = _file_summaries_from_attachments(attachments)
    for file_id in _file_ids_for_intent_lookup(file_ids, attachments):
        existing = next((item for item in summaries if item.file_id == file_id), None)
        if existing and (existing.name or existing.content_type):
            continue
        row = await get_file(conn, tenant_id=tenant_id, file_id=file_id)
        if not row or not _file_row_matches_scope(
            row, tenant_id=tenant_id, user_id=user_id,
            session_id=session_id, workspace_id=workspace_id,
        ):
            continue
        summaries = _merge_file_summaries(summaries, _file_summary_from_row(file_id, row))
    return summaries
