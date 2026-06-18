import json
from typing import Any

from app.validation import assert_safe_id


DEFAULT_MODEL_ID = "deepseek-v4-flash"
DEFAULT_MODEL_CATALOG = [
    {
        "id": "deepseek-v4-flash",
        "value": "deepseek-v4-flash",
        "provider": "new-api",
        "label": "DeepSeek V4 Flash",
        "description": "211 new-api",
        "profile": {"max_input_tokens": 128000},
    },
    {
        "id": "deepseek-v4-pro",
        "value": "deepseek-v4-pro",
        "provider": "new-api",
        "label": "DeepSeek V4 Pro",
        "description": "211 new-api",
        "profile": {"max_input_tokens": 128000},
    },
]


def _coerce_positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _model_from_item(item: object) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    raw_id = str(item.get("id") or item.get("value") or "").strip()
    if not raw_id:
        return None
    model_id = assert_safe_id(raw_id, "model_id")
    value = str(item.get("value") or model_id).strip()
    value = assert_safe_id(value, "model_value")
    provider = str(item.get("provider") or "new-api").strip() or "new-api"
    label = str(item.get("label") or model_id).strip() or model_id
    description = str(item.get("description") or "211 new-api").strip() or "211 new-api"
    profile = item.get("profile") if isinstance(item.get("profile"), dict) else {}
    max_input_tokens = _coerce_positive_int(
        profile.get("max_input_tokens") if isinstance(profile, dict) else item.get("max_input_tokens"),
        128000,
    )
    return {
        "id": model_id,
        "value": value,
        "provider": provider,
        "label": label,
        "description": description,
        "profile": {"max_input_tokens": max_input_tokens},
    }


def _models_from_json(raw_catalog: str) -> list[dict[str, Any]]:
    if not raw_catalog.strip():
        return []
    decoded = json.loads(raw_catalog)
    if not isinstance(decoded, list):
        raise ValueError("model_catalog_json must be a JSON list")
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in decoded:
        model = _model_from_item(item)
        if model is None:
            continue
        if model["id"] in seen:
            continue
        seen.add(str(model["id"]))
        models.append(model)
    return models


def build_model_catalog(settings: object) -> dict[str, Any]:
    """Build the operator-configured model catalog exposed to the frontend."""
    raw_catalog = str(getattr(settings, "model_catalog_json", "") or "")
    models = _models_from_json(raw_catalog) if raw_catalog.strip() else [dict(item) for item in DEFAULT_MODEL_CATALOG]
    if not models:
        fallback_id = str(
            getattr(settings, "claude_agent_model", "")
            or getattr(settings, "anthropic_model", "")
            or getattr(settings, "openai_model", "")
            or DEFAULT_MODEL_ID
        ).strip()
        model = _model_from_item({"id": fallback_id, "label": fallback_id})
        if model is not None:
            models = [model]
    configured_default = str(getattr(settings, "default_model_id", "") or "").strip()
    runtime_default = str(
        getattr(settings, "claude_agent_model", "")
        or getattr(settings, "anthropic_model", "")
        or getattr(settings, "openai_model", "")
        or ""
    ).strip()
    preferred_default = configured_default or runtime_default or DEFAULT_MODEL_ID
    model_ids = {str(model["id"]) for model in models}
    default_model_id = preferred_default if preferred_default in model_ids else str(models[0]["id"])
    return {
        "models": models,
        "count": len(models),
        "enabled_count": len(models),
        "default_model_id": default_model_id,
    }


def resolve_model_selection(model_id: str | None, settings: object) -> dict[str, str] | None:
    """Resolve a frontend catalog id to the runtime model value used by providers."""
    if model_id is None:
        return None
    normalized = assert_safe_id(str(model_id).strip(), "model_id")
    catalog = build_model_catalog(settings)
    for model in catalog["models"]:
        if str(model["id"]) == normalized:
            return {"id": normalized, "value": str(model["value"])}
    raise ValueError("model_id_not_available")


def validate_model_id(model_id: str | None, settings: object) -> str | None:
    """Return a catalog-approved model id or raise when the request is invalid."""
    selection = resolve_model_selection(model_id, settings)
    return selection["id"] if selection is not None else None
