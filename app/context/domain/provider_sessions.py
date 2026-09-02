from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from app.context.domain.conversation import empty_executor_conversation_context


PROVIDER_SESSION_ENGINE_CLAUDE = "claude"
PROVIDER_SESSION_CONTEXT_EPOCH = 1
PROVIDER_SESSION_RESUME_CONTEXT_KEY = "provider_session_resume_required"
MAX_PROVIDER_SESSION_BATCH_COUNT = 128
MAX_PROVIDER_SESSION_ENTRY_BYTES = 256 * 1024
MAX_PROVIDER_SESSION_BATCH_BYTES = 2 * 1024 * 1024
MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES = 8 * 1024 * 1024
MAX_PROVIDER_SESSION_ENTRIES = 4096
MAX_PROVIDER_SESSION_SUBPATH_LENGTH = 512
MAX_PROVIDER_SESSION_ENTRY_UUID_LENGTH = 128


class ProviderSessionContinuityError(ValueError):
    """Raised when provider-session state cannot satisfy the continuity contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProviderSessionConflictError(ProviderSessionContinuityError):
    """The requested write or identity conflicts with durable provider state."""


class ProviderSessionNotFoundError(ProviderSessionContinuityError):
    """The requested provider binding does not exist in the authorized scope."""


@dataclass(frozen=True)
class ProviderSessionScope:
    tenant_id: str
    workspace_id: str
    user_id: str
    session_id: str
    agent_id: str
    engine: str = PROVIDER_SESSION_ENGINE_CLAUDE

    def __post_init__(self) -> None:
        for name in (
            "tenant_id",
            "workspace_id",
            "user_id",
            "session_id",
            "agent_id",
            "engine",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ProviderSessionContinuityError("provider_session_scope_invalid")
        if self.engine != PROVIDER_SESSION_ENGINE_CLAUDE:
            raise ProviderSessionContinuityError("provider_session_engine_unsupported")


@dataclass(frozen=True)
class ProviderSessionEntry:
    """A validated opaque SDK entry and its optional SDK id."""

    entry: dict[str, Any]
    sdk_entry_uuid: str | None = None
    subpath: str | None = None


def normalize_provider_subpath(subpath: object) -> str | None:
    if subpath is None:
        return None
    if not isinstance(subpath, str):
        raise ProviderSessionContinuityError("provider_session_subpath_invalid")
    normalized = subpath.strip()
    if not normalized:
        return None
    if len(normalized) > MAX_PROVIDER_SESSION_SUBPATH_LENGTH:
        raise ProviderSessionContinuityError("provider_session_subpath_too_long")
    return normalized


def _json_bytes(value: object, *, code: str) -> int:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProviderSessionContinuityError(code) from exc
    return len(encoded)


def provider_entry_json_bytes(entry: Mapping[str, Any]) -> int:
    if not isinstance(entry, Mapping):
        raise ProviderSessionContinuityError("provider_session_entry_shape_invalid")
    return _json_bytes(dict(entry), code="provider_session_entry_shape_invalid")


def normalize_provider_entry(
    entry: Mapping[str, Any], *, subpath: object = None
) -> ProviderSessionEntry:
    if not isinstance(entry, Mapping):
        raise ProviderSessionContinuityError("provider_session_entry_shape_invalid")
    normalized_entry = dict(entry)
    entry_bytes = provider_entry_json_bytes(normalized_entry)
    if entry_bytes > MAX_PROVIDER_SESSION_ENTRY_BYTES:
        raise ProviderSessionContinuityError("provider_session_entry_too_large")
    raw_uuid = normalized_entry.get("uuid")
    sdk_entry_uuid: str | None
    if raw_uuid is None or raw_uuid == "":
        sdk_entry_uuid = None
    elif isinstance(raw_uuid, str) and raw_uuid.strip():
        sdk_entry_uuid = raw_uuid.strip()
        if len(sdk_entry_uuid) > MAX_PROVIDER_SESSION_ENTRY_UUID_LENGTH:
            raise ProviderSessionContinuityError("provider_session_entry_uuid_too_long")
    else:
        raise ProviderSessionContinuityError("provider_session_entry_uuid_invalid")
    if sdk_entry_uuid is not None:
        normalized_entry["uuid"] = sdk_entry_uuid
    return ProviderSessionEntry(
        entry=normalized_entry,
        sdk_entry_uuid=sdk_entry_uuid,
        subpath=normalize_provider_subpath(subpath),
    )


def normalize_provider_entry_batch(
    entries: list[Mapping[str, Any]], *, subpath: object = None
) -> tuple[list[ProviderSessionEntry], int]:
    if not isinstance(entries, list) or not entries:
        raise ProviderSessionContinuityError("provider_session_entry_batch_invalid")
    if len(entries) > MAX_PROVIDER_SESSION_BATCH_COUNT:
        raise ProviderSessionContinuityError("provider_session_entry_batch_too_large")
    normalized_subpath = normalize_provider_subpath(subpath)
    normalized: list[ProviderSessionEntry] = []
    total_bytes = 0
    for entry in entries:
        item = normalize_provider_entry(entry, subpath=normalized_subpath)
        total_bytes += provider_entry_json_bytes(item.entry)
        if total_bytes > MAX_PROVIDER_SESSION_BATCH_BYTES:
            raise ProviderSessionContinuityError("provider_session_entry_batch_too_large")
        normalized.append(item)
    return normalized, total_bytes


def provider_session_id_for_scope(
    scope: ProviderSessionScope, *, context_epoch: int = PROVIDER_SESSION_CONTEXT_EPOCH
) -> str:
    if type(context_epoch) is not int or context_epoch < 1:
        raise ProviderSessionContinuityError("provider_session_context_epoch_invalid")
    identity = "\x1f".join(
        (
            scope.engine,
            scope.tenant_id,
            scope.workspace_id,
            scope.user_id,
            scope.session_id,
            scope.agent_id,
            str(context_epoch),
        )
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ai-platform-provider-session:{identity}"))


def claude_provider_session_id_for_session(
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    agent_id: str,
    context_epoch: int = PROVIDER_SESSION_CONTEXT_EPOCH,
) -> str:
    return provider_session_id_for_scope(
        ProviderSessionScope(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
        ),
        context_epoch=context_epoch,
    )


# Short aliases keep the identity rule discoverable to adapters without making
# the engine-specific UUID derivation a second implementation.
provider_session_id_for_session = claude_provider_session_id_for_session
stable_provider_session_id = claude_provider_session_id_for_session


def has_main_provider_transcript(entries: list[Mapping[str, Any]]) -> bool:
    """Return whether a loaded set contains at least one committed main entry."""
    return any(normalize_provider_subpath(row.get("subpath")) is None for row in entries)


def select_provider_conversation_context(
    reconstructed_context: dict[str, Any], *, has_main_transcript: bool
) -> dict[str, Any]:
    """Use native provider history only after a committed main transcript exists."""
    if not has_main_transcript:
        return reconstructed_context
    return empty_executor_conversation_context()


__all__ = [
    "MAX_PROVIDER_SESSION_BATCH_BYTES",
    "MAX_PROVIDER_SESSION_BATCH_COUNT",
    "MAX_PROVIDER_SESSION_ENTRIES",
    "MAX_PROVIDER_SESSION_ENTRY_BYTES",
    "MAX_PROVIDER_SESSION_ENTRY_UUID_LENGTH",
    "MAX_PROVIDER_SESSION_SUBPATH_LENGTH",
    "MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES",
    "PROVIDER_SESSION_CONTEXT_EPOCH",
    "PROVIDER_SESSION_ENGINE_CLAUDE",
    "PROVIDER_SESSION_RESUME_CONTEXT_KEY",
    "ProviderSessionConflictError",
    "ProviderSessionContinuityError",
    "ProviderSessionEntry",
    "ProviderSessionNotFoundError",
    "ProviderSessionScope",
    "claude_provider_session_id_for_session",
    "has_main_provider_transcript",
    "normalize_provider_entry",
    "normalize_provider_entry_batch",
    "normalize_provider_subpath",
    "provider_entry_json_bytes",
    "provider_session_id_for_scope",
    "provider_session_id_for_session",
    "select_provider_conversation_context",
    "stable_provider_session_id",
]
