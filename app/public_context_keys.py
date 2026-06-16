from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, unquote_plus

from app.control_plane_contracts import sanitize_public_payload


PUBLIC_CONTEXT_PROVENANCE_KEYS = {
    "provenance",
    "referenced_materials",
    "used_context_summary",
    "latest_artifact_version",
    "execution_tier",
    "context_pack_version",
    "context_pack_generated_at",
    "source",
}

PUBLIC_CONTEXT_SUMMARY_KEYS = {
    "agent_id",
    "artifact_count",
    "capability_id",
    "context_snapshot_id",
    "file_count",
    "included_artifact_ids",
    "included_file_ids",
    "included_memory_record_ids",
    "included_message_ids",
    "input_keys",
    "memory_policy",
    "memory_record_count",
    "message_count",
    "schema_version",
    "skill_id",
    "summary",
}

PUBLIC_CONTEXT_FORBIDDEN_KEY_ALIASES = {
    "absoluteruntimepaths",
    "executorprivatepayload",
    "privatepayload",
    "rawstoragekey",
    "sandboxworkdir",
    "secretlikevalues",
    "storagekey",
}
PUBLIC_CONTEXT_FORBIDDEN_ID_KEY_ALIASES = {
    "artifactid",
    "artifactids",
    "copiedfromrunid",
    "fileid",
    "fileids",
    "materialid",
    "materialids",
    "memoryrecordid",
    "memoryrecordids",
    "messageid",
    "messageids",
    "parentrunid",
    "rawmaterialid",
    "rawmaterialids",
    "runid",
    "runids",
    "sourcefileid",
    "sourcefileids",
    "sourcerunid",
    "sourcerunids",
}
PUBLIC_CONTEXT_FORBIDDEN_ID_TOKEN_SEQUENCES = (
    ("artifact", "id"),
    ("artifact", "ids"),
    ("copied", "from", "run", "id"),
    ("file", "id"),
    ("file", "ids"),
    ("material", "id"),
    ("material", "ids"),
    ("memory", "record", "id"),
    ("memory", "record", "ids"),
    ("message", "id"),
    ("message", "ids"),
    ("parent", "run", "id"),
    ("raw", "material", "id"),
    ("raw", "material", "ids"),
    ("run", "id"),
    ("run", "ids"),
    ("source", "file", "id"),
    ("source", "file", "ids"),
    ("source", "run", "id"),
    ("source", "run", "ids"),
)

PUBLIC_CONTEXT_MATERIAL_COUNT_KEYS = {
    "message_count",
    "file_count",
    "artifact_count",
    "memory_record_count",
}
PUBLIC_CONTEXT_PACK_VERSION_RE = re.compile(r"^v\d+(?:[._:-]\d+){0,3}$", re.IGNORECASE)
PUBLIC_CONTEXT_HASH_LIKE_VALUE_RE = re.compile(r"^[a-f0-9]{32,}$", re.IGNORECASE)
PUBLIC_CONTEXT_KEY_DECODE_DEPTH = 8
PUBLIC_CONTEXT_CAMEL_BOUNDARY_RE = re.compile(r"([a-z0-9])([A-Z])")
PUBLIC_CONTEXT_ACRONYM_BOUNDARY_RE = re.compile(r"([A-Z]+)([A-Z][a-z])")
PUBLIC_CONTEXT_TOKEN_SEPARATOR_RE = re.compile(r"[^A-Za-z0-9]+")


def normalized_public_context_key(value: object) -> str:
    return "".join(ch for ch in str(value) if ch.isalnum()).lower()


def decoded_public_context_key_candidates(value: object) -> tuple[tuple[str, ...], bool]:
    raw = str(value)
    candidates: list[str] = []
    pending: list[tuple[str, int]] = [(raw, 0)]
    decode_budget_exhausted = False
    while pending:
        current, depth = pending.pop(0)
        if current and current not in candidates:
            candidates.append(current)
        if depth >= PUBLIC_CONTEXT_KEY_DECODE_DEPTH:
            if any(decoded != current for decoded in {unquote(current), unquote_plus(current)}):
                decode_budget_exhausted = True
            continue
        for decoded in {unquote(current), unquote_plus(current)}:
            if decoded != current:
                pending.append((decoded, depth + 1))
    return tuple(candidates), decode_budget_exhausted


def normalized_public_context_key_candidates(value: object) -> tuple[tuple[str, ...], bool]:
    decoded_candidates, decode_budget_exhausted = decoded_public_context_key_candidates(value)
    candidates: list[str] = []
    for decoded in decoded_candidates:
        normalized = normalized_public_context_key(decoded)
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return tuple(candidates), decode_budget_exhausted


def public_context_key_token_candidates(value: object) -> tuple[tuple[str, ...], ...]:
    decoded_candidates, _ = decoded_public_context_key_candidates(value)
    token_candidates: list[tuple[str, ...]] = []
    for decoded in decoded_candidates:
        spaced = PUBLIC_CONTEXT_ACRONYM_BOUNDARY_RE.sub(r"\1 \2", decoded)
        spaced = PUBLIC_CONTEXT_CAMEL_BOUNDARY_RE.sub(r"\1 \2", spaced)
        tokens = tuple(
            token.lower()
            for token in PUBLIC_CONTEXT_TOKEN_SEPARATOR_RE.sub(" ", spaced).split()
            if token
        )
        if tokens and tokens not in token_candidates:
            token_candidates.append(tokens)
    return tuple(token_candidates)


def has_public_context_forbidden_id_tokens(tokens: tuple[str, ...]) -> bool:
    return any(
        tokens[index : index + len(sequence)] == sequence
        for sequence in PUBLIC_CONTEXT_FORBIDDEN_ID_TOKEN_SEQUENCES
        for index in range(0, len(tokens) - len(sequence) + 1)
    )


PUBLIC_CONTEXT_PROVENANCE_KEY_ALIASES = {
    normalized_public_context_key(key)
    for key in PUBLIC_CONTEXT_PROVENANCE_KEYS
}

PUBLIC_CONTEXT_SUMMARY_KEY_ALIASES = {
    normalized_public_context_key(key)
    for key in PUBLIC_CONTEXT_SUMMARY_KEYS
}

PUBLIC_CONTEXT_SUMMARY_PREFIX_ALIASES = {
    "provenance",
    "summary",
}
PUBLIC_CONTEXT_MEMORY_POLICY_SOURCE_VALUES = {"default", "not_recorded", "stored"}
PUBLIC_CONTEXT_SOURCE_VALUES = {
    "chat_stream",
    "copy_run",
    "manual_context_snapshot",
    "multi_agent_dispatch_handoff",
    "multi_agent_dispatch_tick",
    "resume_run",
    "retry_run",
    "runs_api",
    "stored_context_snapshot",
    "worker_refresh",
}


def is_public_context_input_key(value: object) -> bool:
    key_text = str(value).strip() if isinstance(value, str) else ""
    if not key_text:
        return False
    normalized_keys, decode_budget_exhausted = normalized_public_context_key_candidates(key_text)
    if decode_budget_exhausted:
        return False
    if any(normalized_key in PUBLIC_CONTEXT_PROVENANCE_KEY_ALIASES for normalized_key in normalized_keys):
        return False
    if any(normalized_key in PUBLIC_CONTEXT_SUMMARY_KEY_ALIASES for normalized_key in normalized_keys):
        return False
    if any(
        normalized_key.startswith(prefix)
        for normalized_key in normalized_keys
        for prefix in PUBLIC_CONTEXT_SUMMARY_PREFIX_ALIASES
    ):
        return False
    if any(normalized_key in PUBLIC_CONTEXT_FORBIDDEN_KEY_ALIASES for normalized_key in normalized_keys):
        return False
    if any(normalized_key in PUBLIC_CONTEXT_FORBIDDEN_ID_KEY_ALIASES for normalized_key in normalized_keys):
        return False
    if any(
        has_public_context_forbidden_id_tokens(token_candidate)
        for token_candidate in public_context_key_token_candidates(key_text)
    ):
        return False
    preview = sanitize_public_payload({key_text: True})
    return isinstance(preview, dict) and key_text in preview


def safe_public_context_input_keys(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    keys: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        key = item.strip()
        if key and is_public_context_input_key(key):
            keys.append(key)
    return sorted(set(keys))


def public_context_input_key_findings(value: object) -> tuple[list[str], list[str]]:
    """Classify public context input keys with the same predicate used by producers."""
    if not isinstance(value, list):
        return [], []
    safe_keys: list[str] = []
    unsafe_keys: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return [], []
        key = item.strip()
        if not key:
            return [], []
        if is_public_context_input_key(key):
            safe_keys.append(key)
        else:
            unsafe_keys.append(key)
    return sorted(set(safe_keys)), sorted(set(unsafe_keys))


def safe_public_context_pack_version(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if sanitize_public_payload(candidate) != candidate:
        return None
    normalized_candidates, decode_budget_exhausted = normalized_public_context_key_candidates(candidate)
    if decode_budget_exhausted:
        return None
    forbidden_aliases = PUBLIC_CONTEXT_FORBIDDEN_KEY_ALIASES | PUBLIC_CONTEXT_FORBIDDEN_ID_KEY_ALIASES
    if any(alias in normalized_key for normalized_key in normalized_candidates for alias in forbidden_aliases):
        return None
    if any(
        has_public_context_forbidden_id_tokens(token_candidate)
        for token_candidate in public_context_key_token_candidates(candidate)
    ):
        return None
    if PUBLIC_CONTEXT_HASH_LIKE_VALUE_RE.fullmatch(candidate):
        return None
    return candidate if PUBLIC_CONTEXT_PACK_VERSION_RE.fullmatch(candidate) else None
