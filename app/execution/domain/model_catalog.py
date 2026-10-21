"""Pure policy for the shared compatible-model catalog."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping, Sequence


_SAFE_PLATFORM_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_GENERATED_PLATFORM_ID_PREFIX = "mdl_"
_MAX_UPSTREAM_MODEL_ID_BYTES = 512
_MAX_MODEL_TOKEN_LIMIT = 10_000_000
_MAX_MODEL_DISPLAY_NAME_CHARS = 160
_SAFE_PRINCIPAL_USER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}\Z")


def normalize_model_token_limit(value: object, *, field_name: str) -> int | None:
    if value is None:
        return None
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 1
        or value > _MAX_MODEL_TOKEN_LIMIT
    ):
        raise ValueError(f"{field_name}_invalid")
    return value


def normalize_model_token_limits(
    max_input_tokens: object,
    max_output_tokens: object,
) -> tuple[int | None, int | None]:
    input_limit = normalize_model_token_limit(
        max_input_tokens, field_name="max_input_tokens"
    )
    output_limit = normalize_model_token_limit(
        max_output_tokens, field_name="max_output_tokens"
    )
    if (input_limit is None) != (output_limit is None):
        raise ValueError("model_capacity_pair_required")
    return input_limit, output_limit


def validate_upstream_model_id(value: str) -> str:
    if (
        not value
        or value != value.strip()
        or len(value.encode("utf-8")) > _MAX_UPSTREAM_MODEL_ID_BYTES
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("upstream model ID contains unsupported characters")
    return value


def platform_model_id(upstream_model_id: str) -> str:
    if (
        _SAFE_PLATFORM_ID.fullmatch(upstream_model_id)
        and not upstream_model_id.startswith(_GENERATED_PLATFORM_ID_PREFIX)
    ):
        return upstream_model_id
    digest = hashlib.sha256(upstream_model_id.encode("utf-8")).hexdigest()[:32]
    return f"mdl_{digest}"


def discovered_model_mapping(upstream_model_ids: Sequence[str]) -> dict[str, str]:
    discovered: dict[str, str] = {}
    for upstream_model_id in upstream_model_ids:
        model_id = platform_model_id(upstream_model_id)
        previous = discovered.setdefault(model_id, upstream_model_id)
        if previous != upstream_model_id:
            raise ValueError("model_catalog_identity_collision")
    return discovered


def normalize_model_display_name(
    value: object, *, fallback: str, current: str | None = None
) -> str:
    """Return the platform display name.

    An omitted value keeps the saved display name so an older client cannot
    silently erase it. An explicitly empty value clears back to the upstream
    model ID.
    """

    if value is None:
        if isinstance(current, str) and current.strip():
            return current.strip()
        return fallback
    if not isinstance(value, str):
        raise ValueError("model_display_name_invalid")
    display_name = value.strip()
    if not display_name:
        return fallback
    if len(display_name) > _MAX_MODEL_DISPLAY_NAME_CHARS or any(
        ord(character) < 32 or ord(character) == 127 for character in display_name
    ):
        raise ValueError("model_display_name_invalid")
    return display_name


def normalize_model_user_access(
    access_mode: object,
    allowed_user_ids: object,
    *,
    current: tuple[str, list[str]] | None = None,
) -> tuple[str, list[str]]:
    """Normalize per-model user access.

    A model that never configured access is available to every authenticated
    user. An update that omits the fields keeps the saved restriction, so an
    older client cannot silently widen a restricted model. Omitted mode with a
    user list is read as an explicit restricted selection rather than "everyone".
    """

    if access_mode is None:
        if allowed_user_ids is None:
            return current if current is not None else ("all", [])
        if isinstance(allowed_user_ids, (list, tuple)) and not allowed_user_ids:
            # Fields omitted together on a restricted model keep the saved list.
            return current if current is not None else ("all", [])
        if isinstance(allowed_user_ids, (list, tuple)):
            return _normalize_selected_users(allowed_user_ids)
        raise ValueError("model_user_access_invalid")
    if access_mode == "all":
        return "all", []
    if access_mode != "selected" or not isinstance(allowed_user_ids, (list, tuple)):
        raise ValueError("model_user_access_invalid")
    return _normalize_selected_users(allowed_user_ids)


def _normalize_selected_users(allowed_user_ids: Sequence[object]) -> tuple[str, list[str]]:
    normalized: list[str] = []
    for user_id in allowed_user_ids:
        if (
            not isinstance(user_id, str)
            or not _SAFE_PRINCIPAL_USER_ID.fullmatch(user_id)
            or ".." in user_id
        ):
            raise ValueError("model_user_access_invalid")
        if user_id not in normalized:
            normalized.append(user_id)
    # An empty selection would otherwise silently mean "everyone".
    if not normalized:
        raise ValueError("model_user_access_requires_users")
    return "selected", normalized


def current_model_user_access(row: Mapping[str, Any]) -> tuple[str, list[str]]:
    """Read the saved access pair for inheritance across an update."""

    mode = str(row.get("access_mode") or "all")
    if mode != "selected":
        return "all", []
    allowed = row.get("allowed_user_ids")
    return "selected", [str(item) for item in allowed] if isinstance(allowed, (list, tuple)) else []


def model_visible_to_user(row: Mapping[str, Any], *, user_id: str) -> bool:
    access_mode = str(row.get("access_mode") or "all")
    if access_mode == "all":
        return True
    if access_mode != "selected":
        return False
    allowed = row.get("allowed_user_ids")
    if not isinstance(allowed, (list, tuple)):
        return False
    return user_id in [str(item) for item in allowed]


def admin_model_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    projection = {
        "id": str(row["model_id"]),
        "value": str(row["upstream_model_id"]),
        "label": str(row["display_name"]),
        "provider": str(row["provider"]),
        "enabled": bool(row["enabled"]),
        "available": bool(row["upstream_available"]),
        "is_default": bool(row["is_default"]),
        "order": int(row["display_order"]),
        "access_mode": str(row.get("access_mode") or "all"),
        "allowed_user_ids": [
            str(item) for item in (row.get("allowed_user_ids") or [])
        ],
        "last_seen_revision": int(row["last_seen_revision"]),
        "last_seen_at": row["last_seen_at"].isoformat(),
    }
    input_limit = row.get("max_input_tokens")
    output_limit = row.get("max_output_tokens")
    if input_limit is not None and output_limit is not None:
        projection["max_input_tokens"] = int(input_limit)
        projection["max_output_tokens"] = int(output_limit)
    return projection


def public_model_projection(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    models: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {
            "id": str(row["model_id"]),
            "value": str(row["upstream_model_id"]),
            "label": str(row["display_name"]),
            "provider": str(row["provider"]),
            "description": "",
            "profile": {},
        }
        input_limit = row.get("max_input_tokens")
        output_limit = row.get("max_output_tokens")
        if input_limit is not None and output_limit is not None:
            item["profile"] = {
                "max_input_tokens": int(input_limit),
                "max_output_tokens": int(output_limit),
            }
        models.append(item)
    # The default must be usable by this principal: fall back to the first
    # visible model when the global default is excluded from the filtered rows.
    default = next(
        (
            model["id"]
            for model, row in zip(models, rows, strict=True)
            if row["is_default"]
        ),
        models[0]["id"] if models else None,
    )
    return {
        "models": models,
        "count": len(models),
        "enabled_count": len(models),
        "default_model_id": default,
    }
