from __future__ import annotations

from typing import Any


_FILE_INPUT_MODES = frozenset({"csv", "docx", "json", "markdown", "md", "pdf", "text", "txt", "xlsx"})
MAX_PRIMARY_FILE_IDS = 8


def has_file_input_mode(input_modes: list[object]) -> bool:
    normalized_modes = {str(mode or "").strip().casefold() for mode in input_modes}
    return bool(normalized_modes & _FILE_INPUT_MODES)


def compatible_reusable_file_ids(
    rows: list[dict[str, Any]],
    *,
    input_modes: list[object],
) -> list[str]:
    """Select newest-first session files compatible with declared Skill inputs."""

    normalized_modes = {str(mode or "").strip().casefold() for mode in input_modes}
    if not normalized_modes:
        return []
    compatible: list[str] = []
    for row in rows:
        name = str(row.get("original_name") or "").replace("\\", "/").casefold()
        content_type = str(row.get("content_type") or "").split(";", 1)[0].strip().casefold()
        matches = (
            "docx" in normalized_modes
            and name.endswith(".docx")
            and content_type
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ) or (
            "xlsx" in normalized_modes
            and name.endswith(".xlsx")
            and content_type
            == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ) or (
            "pdf" in normalized_modes
            and name.endswith(".pdf")
            and content_type == "application/pdf"
        ) or (
            bool(normalized_modes & {"text", "txt"})
            and name.endswith(".txt")
            and content_type == "text/plain"
        ) or (
            bool(normalized_modes & {"markdown", "md"})
            and name.endswith((".md", ".markdown"))
            and content_type == "text/markdown"
        ) or (
            "csv" in normalized_modes
            and name.endswith(".csv")
            and content_type == "text/csv"
        ) or (
            "json" in normalized_modes
            and name.endswith(".json")
            and content_type == "application/json"
        )
        file_id = str(row.get("id") or "")
        if matches and file_id and file_id not in compatible:
            compatible.append(file_id)
    return compatible


def primary_file_ids_for_run(
    *,
    requested_file_ids: list[str],
    reusable_rows: list[dict[str, Any]],
    input_modes: list[object],
    limit: int = MAX_PRIMARY_FILE_IDS,
) -> list[str]:
    """Prefer this turn's uploads, otherwise reuse newest compatible session files."""

    requested = list(dict.fromkeys(str(file_id) for file_id in requested_file_ids if file_id))
    if requested:
        return requested
    if not has_file_input_mode(input_modes):
        return []
    newest_first = sorted(
        reusable_rows,
        key=lambda row: (str(row.get("created_at") or ""), str(row.get("id") or "")),
        reverse=True,
    )
    return compatible_reusable_file_ids(newest_first, input_modes=input_modes)[: max(0, int(limit))]
