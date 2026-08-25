"""Immutable, canonical Run execution specification values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any

EXECUTION_SPEC_SCHEMA_VERSION = "ai-platform.execution-spec.v1"
_RUN_PAYLOAD_SCHEMA_VERSION_V1 = "ai-platform.run-payload.v1"
_RUN_PAYLOAD_SCHEMA_VERSION_V2 = "ai-platform.run-payload.v2"
_SUPPORTED_RUN_PAYLOAD_SCHEMA_VERSIONS = frozenset(
    {_RUN_PAYLOAD_SCHEMA_VERSION_V1, _RUN_PAYLOAD_SCHEMA_VERSION_V2}
)
_RUN_EXECUTION_KIND_SKILL = "skill"
_RUN_EXECUTION_KIND_HARNESS_CHAT = "harness_chat"
_RELEASE_DECISION_SCHEMA_VERSION = "ai-platform.skill-release-decision.v1"
_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_PRINCIPAL_USER_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$"
)
_SENSITIVE_JSON_KEY_ALIASES = frozenset(
    {
        "apikey",
        "accesskey",
        "accesstoken",
        "anthropicauthtoken",
        "auth",
        "authorization",
        "bearertoken",
        "clientsecret",
        "credential",
        "credentials",
        "idtoken",
        "openaikey",
        "openaiapikey",
        "password",
        "privatekey",
        "refreshtoken",
        "secret",
        "token",
        "xapikey",
    }
)
_SAFE_SECRET_LIKE_JSON_KEYS = frozenset(
    {
        "authorizationstatus",
        "authsource",
        "authstatus",
        "redactionsummary",
        "tokenbudget",
        "tokenbudgets",
        "tokencount",
        "tokencounts",
        "tokenstatus",
        "tokentotal",
        "tokenusage",
    }
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?key|access[_-]?token|auth[_-]?token|"
    r"authorization|aws[_-]?secret[_-]?access[_-]?key|aws[_-]?session[_-]?token|"
    r"client[_-]?secret|credential|openai_api_key|anthropic_auth_token|password|"
    r"private[_-]?key|refresh[_-]?token|secret[_-]?(?:access[_-]?)?key|"
    r"session[_-]?token)"
    r"\s*[:=]\s*(?!\[redacted)[\"']?(?:bearer\s+)?[^\s,;}\"']{12,}"
)
_HIGH_CONFIDENCE_SECRET_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)

_EXECUTION_SPEC_FIELDS = frozenset(
    {
        "schema_version",
        "run_payload_schema_version",
        "tenant_id",
        "workspace_id",
        "user_id",
        "session_id",
        "run_id",
        "agent_id",
        "execution_kind",
        "skill_id",
        "file_ids",
        "input",
        "executor_type",
        "trace_id",
        "skill_version",
        "release_decision",
        "skill_manifests",
        "context_snapshot_id",
        "context_snapshot",
        "context_pack",
        "model_id",
        "model_value",
        "agent_profile",
    }
)


class ExecutionSpecError(ValueError):
    """Stable fail-closed error raised by the Runs-owned specification codec."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _reject_json_constant(_value: str) -> None:
    raise ExecutionSpecError("execution_spec_json_value_invalid")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ExecutionSpecError("execution_spec_duplicate_field")
        value[key] = item
    return value


def _normalized_json_key(value: str) -> str:
    return "".join(character for character in value if character.isalnum()).lower()


def _is_sensitive_json_key(value: str) -> bool:
    normalized = _normalized_json_key(value)
    if normalized in _SAFE_SECRET_LIKE_JSON_KEYS:
        return False
    return (
        normalized in _SENSITIVE_JSON_KEY_ALIASES
        or "apikey" in normalized
        or "credential" in normalized
        or "password" in normalized
        or "privatekey" in normalized
        or "clientsecret" in normalized
        or "secretkey" in normalized
        or ("secret" in normalized and "secretary" not in normalized)
        or "connectionstring" in normalized
        or "authorizationheader" in normalized
        or normalized.endswith("token")
    )


def _contains_high_confidence_secret(value: str) -> bool:
    return _SECRET_ASSIGNMENT_PATTERN.search(value) is not None or any(
        pattern.search(value) is not None
        for pattern in _HIGH_CONFIDENCE_SECRET_PATTERNS
    )


def _validated_json_copy(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        if _contains_high_confidence_secret(value):
            raise ExecutionSpecError("execution_spec_credential_material_forbidden")
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ExecutionSpecError("execution_spec_json_value_invalid")
        return value
    if isinstance(value, list):
        return [_validated_json_copy(item) for item in value]
    if isinstance(value, dict):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ExecutionSpecError("execution_spec_json_key_invalid")
            if _is_sensitive_json_key(key):
                raise ExecutionSpecError("execution_spec_credential_material_forbidden")
            copied[key] = _validated_json_copy(item)
        return copied
    raise ExecutionSpecError("execution_spec_json_value_invalid")


def _required_string(payload: Mapping[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value:
        raise ExecutionSpecError(f"execution_spec_{field_name}_invalid")
    return value


def _optional_string(payload: Mapping[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str):
        raise ExecutionSpecError(f"execution_spec_{field_name}_invalid")
    return value


def _required_json_object(
    payload: Mapping[str, Any], field_name: str
) -> dict[str, Any]:
    value = payload.get(field_name)
    if not isinstance(value, dict):
        raise ExecutionSpecError(f"execution_spec_{field_name}_invalid")
    return _validated_json_copy(value)


def _required_json_object_list(
    payload: Mapping[str, Any], field_name: str
) -> list[dict[str, Any]]:
    value = payload.get(field_name)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ExecutionSpecError(f"execution_spec_{field_name}_invalid")
    return [_validated_json_copy(item) for item in value]


def _safe_id(value: str, field_name: str) -> str:
    if _SAFE_ID_PATTERN.fullmatch(value) is None:
        raise ExecutionSpecError(f"execution_spec_{field_name}_invalid")
    return value


def _upstream_model_id(value: str) -> str:
    if (
        value != value.strip()
        or len(value.encode("utf-8")) > 512
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ExecutionSpecError("execution_spec_model_value_invalid")
    return value


def _safe_principal_user_id(value: str) -> str:
    if (
        _SAFE_PRINCIPAL_USER_ID_PATTERN.fullmatch(value) is None
        or ".." in value
    ):
        raise ExecutionSpecError("execution_spec_user_id_invalid")
    return value


def _validate_skill_authority(
    *,
    skill_id: str,
    skill_version: str,
    release_decision: dict[str, Any],
    skill_manifests: list[dict[str, Any]],
) -> None:
    if release_decision.get("schema_version") != _RELEASE_DECISION_SCHEMA_VERSION:
        raise ExecutionSpecError("execution_spec_skill_authority_invalid")
    selected_version = release_decision.get("selected_version")
    if not isinstance(selected_version, str) or selected_version != skill_version:
        raise ExecutionSpecError("execution_spec_skill_authority_invalid")
    if not skill_version:
        raise ExecutionSpecError("execution_spec_skill_authority_invalid")
    seen_manifest_ids: set[str] = set()
    primary_version: str | None = None
    for manifest in skill_manifests:
        manifest_skill_id = manifest.get("skill_id")
        if not isinstance(manifest_skill_id, str) or not manifest_skill_id.strip():
            continue
        manifest_skill_id = manifest_skill_id.strip()
        if manifest_skill_id in seen_manifest_ids:
            raise ExecutionSpecError("execution_spec_skill_authority_invalid")
        seen_manifest_ids.add(manifest_skill_id)
        if manifest_skill_id == skill_id:
            version = manifest.get("content_hash") or manifest.get("version")
            primary_version = version if isinstance(version, str) else None
    if primary_version != skill_version:
        raise ExecutionSpecError("execution_spec_skill_authority_invalid")


def _normalize_execution_spec(payload: Mapping[str, Any]) -> dict[str, Any]:
    if set(payload) != _EXECUTION_SPEC_FIELDS:
        raise ExecutionSpecError("execution_spec_fields_invalid")
    if payload.get("schema_version") != EXECUTION_SPEC_SCHEMA_VERSION:
        raise ExecutionSpecError("execution_spec_schema_version_invalid")

    run_payload_schema_version = _required_string(payload, "run_payload_schema_version")
    if run_payload_schema_version not in _SUPPORTED_RUN_PAYLOAD_SCHEMA_VERSIONS:
        raise ExecutionSpecError("execution_spec_run_payload_schema_version_invalid")

    normalized: dict[str, Any] = {
        "schema_version": EXECUTION_SPEC_SCHEMA_VERSION,
        "run_payload_schema_version": run_payload_schema_version,
    }
    for field_name in (
        "tenant_id",
        "workspace_id",
        "session_id",
        "run_id",
        "agent_id",
        "executor_type",
    ):
        normalized[field_name] = _safe_id(
            _required_string(payload, field_name), field_name
        )
    normalized["user_id"] = _safe_principal_user_id(
        _required_string(payload, "user_id")
    )

    execution_kind = _required_string(payload, "execution_kind")
    if execution_kind not in {
        _RUN_EXECUTION_KIND_HARNESS_CHAT,
        _RUN_EXECUTION_KIND_SKILL,
    }:
        raise ExecutionSpecError("execution_spec_execution_kind_invalid")
    normalized["execution_kind"] = execution_kind

    skill_id = payload.get("skill_id")
    if skill_id == "":
        skill_id = None
    elif skill_id is not None:
        if not isinstance(skill_id, str):
            raise ExecutionSpecError("execution_spec_skill_id_invalid")
        skill_id = _safe_id(skill_id, "skill_id")
    normalized["skill_id"] = skill_id

    file_ids = payload.get("file_ids")
    if not isinstance(file_ids, list):
        raise ExecutionSpecError("execution_spec_file_ids_invalid")
    normalized_file_ids: list[str] = []
    for file_id in file_ids:
        if not isinstance(file_id, str):
            raise ExecutionSpecError("execution_spec_file_ids_invalid")
        normalized_file_ids.append(_safe_id(file_id, "file_ids"))
    normalized["file_ids"] = normalized_file_ids

    normalized["input"] = _required_json_object(payload, "input")
    normalized["trace_id"] = _optional_string(payload, "trace_id")
    normalized["skill_version"] = _optional_string(payload, "skill_version")
    normalized["release_decision"] = _required_json_object(payload, "release_decision")
    normalized["skill_manifests"] = _required_json_object_list(
        payload, "skill_manifests"
    )
    normalized["context_snapshot"] = _required_json_object(payload, "context_snapshot")
    normalized["context_pack"] = _required_json_object(payload, "context_pack")
    normalized["agent_profile"] = _required_json_object(payload, "agent_profile")

    context_snapshot_id = _required_string(payload, "context_snapshot_id")
    context_snapshot_id = _safe_id(context_snapshot_id, "context_snapshot_id")
    if normalized["context_snapshot"].get("context_snapshot_id") != context_snapshot_id:
        raise ExecutionSpecError("execution_spec_context_snapshot_identity_mismatch")
    normalized["context_snapshot_id"] = context_snapshot_id

    model_id = _optional_string(payload, "model_id")
    if model_id:
        normalized["model_id"] = _safe_id(model_id, "model_id")
    model_value = _optional_string(payload, "model_value")
    if model_value:
        normalized["model_value"] = _upstream_model_id(model_value)

    if execution_kind == _RUN_EXECUTION_KIND_HARNESS_CHAT:
        if run_payload_schema_version != _RUN_PAYLOAD_SCHEMA_VERSION_V2:
            raise ExecutionSpecError("execution_spec_harness_schema_invalid")
        if skill_id is not None:
            raise ExecutionSpecError("execution_spec_harness_skill_id_forbidden")
        if (
            normalized["skill_version"]
            or normalized["release_decision"]
            or normalized["skill_manifests"]
        ):
            raise ExecutionSpecError("execution_spec_harness_skill_authority_forbidden")
    else:
        if skill_id is None:
            raise ExecutionSpecError("execution_spec_skill_identity_invalid")
        _validate_skill_authority(
            release_decision=normalized["release_decision"],
            skill_version=normalized["skill_version"],
            skill_id=skill_id,
            skill_manifests=normalized["skill_manifests"],
        )

    return normalized


def _canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ExecutionSpecError("execution_spec_json_value_invalid") from exc


def _decode_canonical_json(canonical_json: bytes) -> dict[str, Any]:
    if not isinstance(canonical_json, bytes):
        raise ExecutionSpecError("execution_spec_canonical_json_invalid")
    try:
        decoded = json.loads(
            canonical_json.decode("utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except ExecutionSpecError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExecutionSpecError("execution_spec_canonical_json_invalid") from exc
    if not isinstance(decoded, dict):
        raise ExecutionSpecError("execution_spec_canonical_json_invalid")
    return decoded


@dataclass(frozen=True, slots=True)
class ExecutionSpec:
    """Canonical immutable bytes and digest for one admitted Run execution."""

    canonical_json: bytes
    spec_sha256: str

    def __post_init__(self) -> None:
        decoded = _decode_canonical_json(self.canonical_json)
        normalized = _normalize_execution_spec(decoded)
        if _canonical_json_bytes(normalized) != self.canonical_json:
            raise ExecutionSpecError("execution_spec_json_not_canonical")
        if hashlib.sha256(self.canonical_json).hexdigest() != self.spec_sha256:
            raise ExecutionSpecError("execution_spec_digest_mismatch")

    @classmethod
    def compile(cls, payload: Mapping[str, Any]) -> "ExecutionSpec":
        normalized = _normalize_execution_spec(payload)
        canonical_json = _canonical_json_bytes(normalized)
        return cls(
            canonical_json=canonical_json,
            spec_sha256=hashlib.sha256(canonical_json).hexdigest(),
        )

    @classmethod
    def from_canonical_json(
        cls,
        canonical_json: bytes,
        *,
        expected_sha256: str | None = None,
    ) -> "ExecutionSpec":
        if not isinstance(canonical_json, bytes):
            raise ExecutionSpecError("execution_spec_canonical_json_invalid")
        spec = cls(
            canonical_json=canonical_json,
            spec_sha256=hashlib.sha256(canonical_json).hexdigest(),
        )
        if expected_sha256 is not None and spec.spec_sha256 != expected_sha256:
            raise ExecutionSpecError("execution_spec_digest_mismatch")
        return spec

    def to_mapping(self) -> dict[str, Any]:
        """Return a fresh JSON projection without exposing internal mutable state."""

        decoded = json.loads(self.canonical_json.decode("utf-8"))
        if not isinstance(decoded, dict):  # pragma: no cover - constructor invariant
            raise ExecutionSpecError("execution_spec_canonical_json_invalid")
        return decoded


def compile_execution_spec(payload: Mapping[str, Any]) -> ExecutionSpec:
    """Public Runs-domain compiler for one exact admitted execution mapping."""

    return ExecutionSpec.compile(payload)
