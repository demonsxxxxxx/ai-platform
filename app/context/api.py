from __future__ import annotations

import secrets

from app.context.application.provider_sessions import (
    ProviderSessionOperationResult,
    execute_provider_session_callback,
    provider_session_has_main_transcript,
)
from app.context.application.worker_snapshot import (
    materialize_worker_context_snapshot as _materialize_worker_context_snapshot,
)
from app.context.domain.conversation import (
    MAX_CONVERSATION_CONTEXT_CANDIDATES,
    ConversationContextError,
    build_executor_conversation_context,
    empty_executor_conversation_context,
)
from app.context.domain.provider_sessions import (
    MAX_PROVIDER_SESSION_BATCH_BYTES,
    MAX_PROVIDER_SESSION_BATCH_COUNT,
    MAX_PROVIDER_SESSION_ENTRIES,
    MAX_PROVIDER_SESSION_ENTRY_BYTES,
    MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES,
    PROVIDER_SESSION_CONTEXT_EPOCH,
    PROVIDER_SESSION_ENGINE_CLAUDE,
    PROVIDER_SESSION_RESUME_CONTEXT_KEY,
    ProviderSessionConflictError,
    ProviderSessionContinuityError,
    ProviderSessionEntry,
    ProviderSessionNotFoundError,
    ProviderSessionScope,
    claude_provider_session_id_for_session,
    normalize_provider_entry,
    normalize_provider_entry_batch,
    normalize_provider_subpath,
    provider_session_id_for_scope,
    provider_session_id_for_session,
    select_provider_conversation_context,
)


CONTEXT_FILE_FAILURE_SCHEMA_VERSION = "ai-platform.context-file-failure.v1"
# Persisted legacy codes remain valid for historical terminal projection.
CONTEXT_FILE_ERROR_CODES = frozenset(
    {
        "attachment_materialized_fact_invalid",
        "attachment_parser_file_mapping_invalid",
        "attachment_parser_file_too_large",
        "attachment_parser_prompt_too_large",
        "attachment_parser_staged_file_invalid",
        "attachment_parser_staged_file_mismatch",
        "attachment_parser_unsupported",
        "attachment_preprocessing_contract_invalid",
        "context_file_docx_archive_entry_limit_exceeded",
        "context_file_docx_archive_invalid",
        "context_file_docx_archive_structure_invalid",
        "context_file_docx_archive_too_large",
        "context_file_docx_embedded_content_unsupported",
        "context_file_docx_encrypted",
        "context_file_docx_external_relationship_unsupported",
        "context_file_docx_macros_unsupported",
        "context_file_docx_parse_failed",
        "context_file_docx_relationship_invalid",
        "context_file_docx_required_part_missing",
        "context_file_identity_mismatch",
        "context_file_json_invalid",
        "context_file_name_conflict",
        "context_file_pdf_active_content_unsupported",
        "context_file_pdf_page_limit_exceeded",
        "context_file_pdf_parse_failed",
        "context_file_pdf_password_required",
        "context_file_preprocessing_failed",
        "context_file_staging_write_failed",
        "context_file_storage_unavailable",
        "context_file_text_encoding_unsupported",
        "context_file_too_large",
        "context_file_type_unsupported",
        "context_file_unavailable",
        "context_file_xlsx_archive_invalid",
        "xlsx_archive_too_large",
        "xlsx_cell_limit_exceeded",
        "xlsx_content_types_structure_unsupported",
        "xlsx_encrypted_unsupported",
        "xlsx_macros_unsupported",
        "xlsx_parse_failed",
        "xlsx_relationship_structure_unsupported",
        "xlsx_workbook_part_unsupported",
        "xlsx_workbook_structure_unsupported",
        "xlsx_worksheet_structure_unsupported",
        "xlsx_xml_encoding_unsupported",
        "xlsx_xml_entities_unsupported",
    }
)


def normalize_context_file_error_code(value: object) -> str:
    code = str(value or "").strip()
    return code if code in CONTEXT_FILE_ERROR_CODES else "context_file_preprocessing_failed"


def _context_file_error_phase(code: str) -> str:
    if code == "context_file_unavailable":
        return "authorization"
    if code == "context_file_identity_mismatch":
        return "identity"
    if code in {"context_file_name_conflict", "context_file_staging_write_failed"}:
        return "staging"
    if code == "context_file_storage_unavailable":
        return "storage"
    if code == "context_file_too_large" or code.endswith("_too_large"):
        return "limits"
    if code == "context_file_type_unsupported":
        return "classification"
    return "parser"


class ContextFileContentError(ValueError):
    def __init__(
        self,
        code: str,
        *,
        phase: str | None = None,
        file_kind: str | None = None,
        attachment_index: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.phase = phase or _context_file_error_phase(code)
        self.file_kind = str(file_kind or "").strip().casefold()
        self.attachment_index = attachment_index

    def bind_attachment(self, *, attachment_index: int, file_kind: str) -> ContextFileContentError:
        if self.attachment_index is None:
            self.attachment_index = attachment_index
        if not self.file_kind:
            self.file_kind = str(file_kind or "").strip().casefold()
        return self


def context_file_failure_diagnostic(error: ContextFileContentError) -> dict[str, object]:
    chain: list[str] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and len(chain) < 4 and id(current) not in seen:
        seen.add(id(current))
        name = type(current).__name__
        if name.isidentifier():
            chain.append(name[:80])
        current = current.__cause__
    diagnostic: dict[str, object] = {
        "schema_version": CONTEXT_FILE_FAILURE_SCHEMA_VERSION,
        "reason_code": normalize_context_file_error_code(error.code),
        "phase": error.phase,
        "exception_chain": chain,
    }
    if error.file_kind:
        diagnostic["file_kind"] = error.file_kind
    if isinstance(error.attachment_index, int) and error.attachment_index > 0:
        diagnostic["attachment_index"] = error.attachment_index
    return diagnostic


def context_file_executor_failure(
    error: ContextFileContentError,
) -> tuple[str, str, dict[str, object]]:
    diagnostic = context_file_failure_diagnostic(error)
    diagnostic["diagnostic_id"] = secrets.token_hex(8)
    error_code = str(diagnostic["reason_code"])
    message = {
        "context_file_too_large": "Input file exceeds 128 MiB or the input set exceeds 256 MiB.",
        "context_file_pdf_password_required": "The PDF requires a password before it can be processed.",
    }.get(error_code, "The input file could not be prepared for execution.")
    return error_code, message, diagnostic


async def _provider_transcript_state(conn: object, **kwargs: object) -> bool:
    kwargs.pop("run_id", None)
    return await provider_session_has_main_transcript(conn, **kwargs)


async def materialize_worker_context_snapshot(
    conn: object,
    *,
    identity: dict[str, str],
    context_snapshot_id: str,
    snapshot_loader,
    message_loader,
    context_projector,
    provider_transcript_loader=None,
):
    if (
        provider_transcript_loader is None
        and identity.get("agent_id")
        and identity.get("engine") == PROVIDER_SESSION_ENGINE_CLAUDE
    ):
        provider_transcript_loader = _provider_transcript_state
    return await _materialize_worker_context_snapshot(
        conn,
        identity=identity,
        context_snapshot_id=context_snapshot_id,
        snapshot_loader=snapshot_loader,
        message_loader=message_loader,
        context_projector=context_projector,
        provider_transcript_loader=provider_transcript_loader,
    )

__all__ = [
    "CONTEXT_FILE_ERROR_CODES",
    "CONTEXT_FILE_FAILURE_SCHEMA_VERSION",
    "MAX_CONVERSATION_CONTEXT_CANDIDATES",
    "ConversationContextError",
    "ContextFileContentError",
    "build_executor_conversation_context",
    "context_file_executor_failure",
    "context_file_failure_diagnostic",
    "empty_executor_conversation_context",
    "materialize_worker_context_snapshot",
    "normalize_context_file_error_code",
    "MAX_PROVIDER_SESSION_BATCH_BYTES",
    "MAX_PROVIDER_SESSION_BATCH_COUNT",
    "MAX_PROVIDER_SESSION_ENTRIES",
    "MAX_PROVIDER_SESSION_ENTRY_BYTES",
    "MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES",
    "PROVIDER_SESSION_CONTEXT_EPOCH",
    "PROVIDER_SESSION_ENGINE_CLAUDE",
    "PROVIDER_SESSION_RESUME_CONTEXT_KEY",
    "ProviderSessionConflictError",
    "ProviderSessionContinuityError",
    "ProviderSessionEntry",
    "ProviderSessionNotFoundError",
    "ProviderSessionOperationResult",
    "ProviderSessionScope",
    "claude_provider_session_id_for_session",
    "normalize_provider_entry",
    "normalize_provider_entry_batch",
    "normalize_provider_subpath",
    "provider_session_id_for_scope",
    "provider_session_id_for_session",
    "select_provider_conversation_context",
    "execute_provider_session_callback",
]
