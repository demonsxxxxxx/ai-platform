"""Pure policy for the shared compatible-model catalog."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


_SAFE_PLATFORM_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_GENERATED_PLATFORM_ID_PREFIX = "mdl_"


@dataclass(frozen=True)
class CatalogPatch:
    display_name: str
    enabled: bool
    is_default: bool


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
    return CatalogPatch(
        display_name=next_name,
        enabled=next_enabled,
        is_default=next_default,
    )


def admin_model_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
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


def public_model_projection(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    models = [
        {
            "id": str(row["model_id"]),
            "value": str(row["upstream_model_id"]),
            "label": str(row["display_name"]),
            "provider": str(row["provider"]),
            "description": "",
            "profile": {},
        }
        for row in rows
    ]
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
