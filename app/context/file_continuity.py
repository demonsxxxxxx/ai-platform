from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Any

from app.context.api import ContextFileContentError
from app.context.file_content import (
    MAX_CONTEXT_FILE_STAGE_BYTES,
    validate_context_file_for_stage,
)
from app.path_safety import ensure_creatable_inside
from app.storage import ObjectStorageSizeLimitError


_FILE_INPUT_MODES = frozenset({"csv", "docx", "json", "markdown", "md", "pdf", "text", "txt", "xlsx"})
MAX_PRIMARY_FILE_IDS = 8
_MAX_CONTEXT_FILE_STAGE_TOTAL_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class ContextFileMetadata:
    file_id: str
    file_name: str
    content_type: str
    size_bytes: int


@dataclass(frozen=True)
class ContextFileMaterialization:
    file_names: tuple[str, ...]
    materialized_file_names: tuple[str, ...]
    attachment_metadata: tuple[ContextFileMetadata, ...]


@dataclass(frozen=True)
class RunFileSelection:
    primary_file_ids: tuple[str, ...]
    reusable_primary_file_ids: tuple[str, ...]
    file_required: bool


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
    selected_names: set[str] = set()
    for row in rows:
        name = str(row.get("original_name") or "").replace("\\", "/").casefold()
        basename = name.rsplit("/", 1)[-1]
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
        if (
            matches
            and file_id
            and file_id not in compatible
            and basename
            and basename not in selected_names
        ):
            compatible.append(file_id)
            selected_names.add(basename)
    return compatible


def snapshot_file_ids(
    *,
    current_file_ids: list[str],
    historical_file_ids: list[str],
    history_limit: int = MAX_PRIMARY_FILE_IDS,
) -> list[str]:
    """Keep every current attachment plus the newest bounded historical files."""

    current = list(dict.fromkeys(str(file_id) for file_id in current_file_ids if file_id))
    current_set = set(current)
    historical = list(
        dict.fromkeys(
            str(file_id)
            for file_id in historical_file_ids
            if file_id and str(file_id) not in current_set
        )
    )
    remaining_history_slots = max(0, int(history_limit) - len(current))
    if remaining_history_slots == 0:
        return current
    return historical[:remaining_history_slots] + current


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


def select_run_file_snapshot(
    *,
    requested_file_ids: list[str],
    reusable_rows: list[dict[str, Any]],
    input_modes: list[object],
    preserve_agent_history: bool,
) -> RunFileSelection:
    """Select immutable run files without treating Agent Skill capabilities as requirements."""

    if preserve_agent_history:
        primary = snapshot_file_ids(
            current_file_ids=requested_file_ids,
            historical_file_ids=[str(row.get("id") or "") for row in reversed(reusable_rows)],
        )
    else:
        primary = primary_file_ids_for_run(
            requested_file_ids=requested_file_ids,
            reusable_rows=reusable_rows,
            input_modes=input_modes,
        )
    reusable_ids = {str(row.get("id") or "") for row in reusable_rows}
    return RunFileSelection(
        primary_file_ids=tuple(primary),
        reusable_primary_file_ids=tuple(file_id for file_id in primary if file_id in reusable_ids),
        file_required=not preserve_agent_history and has_file_input_mode(input_modes) and not primary,
    )


async def select_authorized_run_file_snapshot(
    *,
    conn: Any,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str | None,
    requested_file_ids: list[str],
    input_modes: list[object],
    preserve_agent_history: bool,
    load_session_files: Callable[..., Awaitable[list[dict[str, Any]]]],
) -> RunFileSelection:
    """Load history only when it can participate in the immutable run snapshot."""

    rows: list[dict[str, Any]] = []
    should_load = session_id and (
        preserve_agent_history or (not requested_file_ids and has_file_input_mode(input_modes))
    )
    if should_load:
        rows = [
            dict(row)
            for row in await load_session_files(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=session_id,
            )
        ]
    return select_run_file_snapshot(
        requested_file_ids=requested_file_ids,
        reusable_rows=rows,
        input_modes=input_modes,
        preserve_agent_history=preserve_agent_history,
    )


async def list_authorized_session_input_files(
    conn: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
) -> list[dict[str, Any]]:
    """Return files authorized by each source run's immutable snapshot."""

    cursor = await conn.execute(
        """
        select files.id, files.run_id, files.original_name, files.content_type,
               files.size_bytes, files.created_at
        from files
        join sessions on sessions.id = files.session_id
          and sessions.tenant_id = files.tenant_id
          and sessions.workspace_id = files.workspace_id
          and sessions.user_id = files.user_id
          and sessions.status = 'active'
        join runs on runs.id = files.run_id
          and runs.tenant_id = files.tenant_id
          and runs.workspace_id = files.workspace_id
          and runs.user_id = files.user_id
          and runs.session_id = files.session_id
          and runs.input_json->>'context_snapshot_id' = runs.context_snapshot_id
          and runs.input_json->'context_snapshot'->>'context_snapshot_id' = runs.context_snapshot_id
        join run_context_snapshots authorized_snapshot
          on authorized_snapshot.id = runs.context_snapshot_id
          and authorized_snapshot.tenant_id = files.tenant_id
          and authorized_snapshot.workspace_id = files.workspace_id
          and authorized_snapshot.user_id = files.user_id
          and authorized_snapshot.session_id = files.session_id
          and authorized_snapshot.run_id = files.run_id
          and authorized_snapshot.context_kind = 'executor'
          and authorized_snapshot.included_file_ids ? files.id
        where files.tenant_id = %s
          and files.workspace_id = %s
          and files.user_id = %s
          and files.session_id = %s
        order by files.created_at asc, files.id asc
        """,
        (tenant_id, workspace_id, user_id, session_id),
    )
    return list(await cursor.fetchall())


async def materialize_run_context_files(
    *,
    transaction_factory: Callable[[], AbstractAsyncContextManager[Any]],
    repository: Any,
    storage: Any | None,
    workspace: Path,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    file_ids: list[str],
) -> ContextFileMaterialization:
    """Authorize, validate, then atomically stage one run's context-file set."""

    file_names: list[str] = []
    attachment_metadata: list[ContextFileMetadata] = []
    authorized_files: list[tuple[int, str, dict[str, Any], str, int]] = []
    materialized_name_keys: set[str] = set()
    async with transaction_factory() as conn:
        for attachment_index, file_id in enumerate(file_ids, start=1):
            row = await repository.get_scoped_context_file(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=session_id,
                run_id=run_id,
                file_id=file_id,
            )
            if row is None:
                raise ContextFileContentError(
                    "context_file_unavailable",
                    attachment_index=attachment_index,
                )
            normalized_row = dict(row)
            original_name = str(normalized_row.get("original_name") or file_id).replace("\\", "/")
            filename = Path(original_name).name or file_id
            file_kind = Path(filename).suffix.casefold().lstrip(".")
            content_type = str(normalized_row.get("content_type") or "")
            try:
                size_bytes = int(normalized_row.get("size_bytes"))
            except (TypeError, ValueError) as exc:
                raise ContextFileContentError(
                    "context_file_identity_mismatch",
                    file_kind=file_kind,
                    attachment_index=attachment_index,
                ) from exc
            if size_bytes < 0:
                raise ContextFileContentError(
                    "context_file_identity_mismatch",
                    file_kind=file_kind,
                    attachment_index=attachment_index,
                )
            attachment_metadata.append(
                ContextFileMetadata(file_id, filename, content_type, size_bytes)
            )
            file_names.append(filename)
            name_key = filename.casefold()
            if name_key in materialized_name_keys:
                raise ContextFileContentError(
                    "context_file_name_conflict",
                    file_kind=file_kind,
                    attachment_index=attachment_index,
                )
            materialized_name_keys.add(name_key)
            authorized_files.append(
                (attachment_index, file_id, normalized_row, filename, size_bytes)
            )

    if storage is None:
        raise ContextFileContentError(
            "context_file_storage_unavailable",
            phase="storage",
        )
    if sum(item[4] for item in authorized_files) > _MAX_CONTEXT_FILE_STAGE_TOTAL_BYTES:
        raise ContextFileContentError("context_file_too_large")

    inputs_dir = workspace / "inputs"
    materialized_file_names: list[str] = []
    written_paths: list[Path] = []
    created_inputs_dir = False
    try:
        if authorized_files:
            created_inputs_dir = not inputs_dir.exists()
            inputs_dir.mkdir(parents=True, exist_ok=True)
        for attachment_index, _file_id, row, filename, size_bytes in authorized_files:
            file_kind = Path(filename).suffix.casefold().lstrip(".")
            target = inputs_dir / filename
            ensure_creatable_inside(
                inputs_dir,
                target,
                "uploaded file target must stay inside the run inputs directory",
            )
            if target.exists() or target.is_symlink():
                raise ContextFileContentError(
                    "context_file_name_conflict",
                    file_kind=file_kind,
                    attachment_index=attachment_index,
                )
            if size_bytes > MAX_CONTEXT_FILE_STAGE_BYTES:
                raise ContextFileContentError(
                    "context_file_too_large",
                    file_kind=file_kind,
                    attachment_index=attachment_index,
                )
            storage_key = str(row.get("storage_key") or "")
            if not storage_key:
                raise ContextFileContentError(
                    "context_file_identity_mismatch",
                    file_kind=file_kind,
                    attachment_index=attachment_index,
                )
            written_paths.append(target)
            temporary_path: Path | None = None
            try:
                if hasattr(storage, "download_to_tempfile"):
                    downloaded = storage.download_to_tempfile(
                        storage_key=storage_key,
                        max_bytes=size_bytes,
                    )
                    temporary_path = Path(downloaded.path)
                    if downloaded.size_bytes != size_bytes or (
                        row.get("sha256")
                        and downloaded.sha256.casefold() != str(row["sha256"]).casefold()
                    ):
                        raise ContextFileContentError(
                            "context_file_identity_mismatch",
                            file_kind=file_kind,
                            attachment_index=attachment_index,
                        )
                    shutil.copyfile(temporary_path, target)
                    if filename.casefold().endswith(".xlsx") or str(row.get("content_type") or "").split(";", 1)[0].casefold() == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
                        validate_context_file_for_stage(row, target.read_bytes())
                else:
                    content = storage.get_bytes_bounded(
                        storage_key=storage_key,
                        max_bytes=size_bytes,
                    )
                    validate_context_file_for_stage(row, content)
                    target.write_bytes(content)
            except ObjectStorageSizeLimitError as exc:
                raise ContextFileContentError(
                    "context_file_identity_mismatch",
                    file_kind=file_kind,
                    attachment_index=attachment_index,
                ) from exc
            except ContextFileContentError as exc:
                raise exc.bind_attachment(
                    attachment_index=attachment_index,
                    file_kind=file_kind,
                )
            except Exception as exc:
                raise ContextFileContentError(
                    "context_file_storage_unavailable",
                    file_kind=file_kind,
                    attachment_index=attachment_index,
                ) from exc
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
            materialized_file_names.append(target.name)
    except ContextFileContentError:
        for written_path in reversed(written_paths):
            try:
                written_path.unlink(missing_ok=True)
            except OSError:
                pass
        if created_inputs_dir:
            try:
                inputs_dir.rmdir()
            except OSError:
                pass
        raise
    except BaseException as exc:
        for written_path in reversed(written_paths):
            try:
                written_path.unlink(missing_ok=True)
            except OSError:
                pass
        if created_inputs_dir:
            try:
                inputs_dir.rmdir()
            except OSError:
                pass
        if isinstance(exc, Exception):
            raise ContextFileContentError(
                "context_file_staging_write_failed",
                phase="staging",
            ) from exc
        raise
    return ContextFileMaterialization(
        tuple(file_names),
        tuple(materialized_file_names),
        tuple(attachment_metadata),
    )
