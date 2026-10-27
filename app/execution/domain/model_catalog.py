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
# Claude Code's documented --autocompact window bounds. The platform default is
# the lower bound; the engine may compact earlier than this window.
AUTO_COMPACT_WINDOW_MIN_TOKENS = 100_000
AUTO_COMPACT_WINDOW_MAX_TOKENS = 1_000_000
AUTO_COMPACT_WINDOW_DEFAULT_TOKENS = 100_000


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


def normalize_auto_compact_window(value: object) -> int:
    """Return the engine-owned automatic-compaction window for one model.

    An omitted value takes the platform default. A configured value outside
    Claude Code's supported range is rejected rather than silently clamped, so
    the administrator sees exactly the window the engine receives.
    """

    if value is None:
        return AUTO_COMPACT_WINDOW_DEFAULT_TOKENS
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < AUTO_COMPACT_WINDOW_MIN_TOKENS
        or value > AUTO_COMPACT_WINDOW_MAX_TOKENS
    ):
        raise ValueError("auto_compact_window_invalid")
    return value


def legacy_input_capacity_to_auto_compact_window(value: object) -> int | None:
    """Map a retired max-input capacity onto the window it actually produced.

    Historical catalog rows and frozen `ai-platform.execution-spec.v2` attempts
    stored an input capacity that the runner clamped into the CLI range before
    passing it as `--autocompact`. Reading those values back through the same
    mapping preserves the engine behavior they were admitted with. Only the
    historical readers call this; new writes store the window itself.
    """

    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    return min(
        AUTO_COMPACT_WINDOW_MAX_TOKENS,
        max(AUTO_COMPACT_WINDOW_MIN_TOKENS, value),
    )


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


def normalize_model_output_capacity(
    max_output_tokens: object,
) -> int | None:
    return normalize_model_token_limit(
        max_output_tokens, field_name="max_output_tokens"
    )


def normalize_model_token_limits(
    auto_compact_window: object,
    max_output_tokens: object,
) -> tuple[int, int | None]:
    """Normalize the two independent per-model execution limits.

    The compaction window always resolves to a value (the platform default when
    omitted); the output cap stays optional so a disabled catalog entry can be
    saved without one.
    """

    return (
        normalize_auto_compact_window(auto_compact_window),
        normalize_model_output_capacity(max_output_tokens),
    )


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


def admin_model_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    projection = {
        "id": str(row["model_id"]),
        "value": str(row["upstream_model_id"]),
        "label": str(row["display_name"]),
        "provider": str(row["provider"]),
        "enabled": bool(row["enabled"]),
        "available": bool(row["upstream_available"]),
        "order": int(row["display_order"]),
        "last_seen_revision": int(row["last_seen_revision"]),
        "last_seen_at": row["last_seen_at"].isoformat(),
    }
    window = row.get("auto_compact_window")
    output_limit = row.get("max_output_tokens")
    if window is not None:
        projection["auto_compact_window"] = int(window)
    if output_limit is not None:
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
        window = row.get("auto_compact_window")
        output_limit = row.get("max_output_tokens")
        if window is not None or output_limit is not None:
            item["profile"] = {
                **({"auto_compact_window": int(window)} if window is not None else {}),
                **(
                    {"max_output_tokens": int(output_limit)}
                    if output_limit is not None
                    else {}
                ),
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
