"""Pure policy for the shared compatible-model catalog."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


_SAFE_PLATFORM_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_GENERATED_PLATFORM_ID_PREFIX = "mdl_"
_MAX_UPSTREAM_MODEL_ID_BYTES = 512
_MAX_MODEL_TOKEN_LIMIT = 10_000_000


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


@dataclass(frozen=True)
class CatalogPatch:
    display_name: str
    enabled: bool
    is_default: bool
    max_input_tokens: int | None
    max_output_tokens: int | None


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


def normalize_catalog_patch(
    row: Mapping[str, Any],
    *,
    display_name: str | None,
    enabled: bool | None,
    is_default: bool | None,
    max_input_tokens: int | None,
    max_output_tokens: int | None,
) -> CatalogPatch:
    next_name = str(row["display_name"]) if display_name is None else display_name.strip()
    if not next_name or len(next_name) > 160 or any(ord(char) < 32 for char in next_name):
        raise ValueError("model_display_name_invalid")
    next_enabled = bool(row["enabled"]) if enabled is None else enabled
    next_default = bool(row["is_default"]) if is_default is None else is_default
    if next_default and (not next_enabled or not bool(row["upstream_available"])):
        raise ValueError("model_default_must_be_available")
    if not next_enabled:
        next_default = False
    next_input, next_output = normalize_model_token_limits(
        row.get("max_input_tokens") if max_input_tokens is None else max_input_tokens,
        row.get("max_output_tokens") if max_output_tokens is None else max_output_tokens,
    )
    return CatalogPatch(
        display_name=next_name,
        enabled=next_enabled,
        is_default=next_default,
        max_input_tokens=next_input,
        max_output_tokens=next_output,
    )


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
    default = next(
        (
            model["id"]
            for model, row in zip(models, rows, strict=True)
            if row["is_default"]
        ),
        None,
    )
    return {
        "models": models,
        "count": len(models),
        "enabled_count": len(models),
        "default_model_id": default,
    }
