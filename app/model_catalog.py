import json
from typing import Any

from app.validation import assert_safe_id


MODEL_CATALOG_NOT_CONFIGURED = "model_catalog_not_configured"
DEFAULT_MAX_INPUT_TOKENS = 128000


def _coerce_positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _model_from_item(
    item: object,
    *,
    default_provider: str,
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    raw_id = str(item.get("id") or item.get("value") or "").strip()
    if not raw_id:
        return None
    model_id = assert_safe_id(raw_id, "model_id")
    value = str(item.get("value") or "").strip() or model_id
    value = assert_safe_id(value, "model_value")
    provider = str(item.get("provider") or "").strip() or default_provider
    label = str(item.get("label") or model_id).strip() or model_id
    description = str(item.get("description") or "").strip()
    profile = item.get("profile") if isinstance(item.get("profile"), dict) else {}
    raw_max_input_tokens = profile.get("max_input_tokens")
    if raw_max_input_tokens is None:
        raw_max_input_tokens = item.get("max_input_tokens")
    max_input_tokens = _coerce_positive_int(
        raw_max_input_tokens,
        DEFAULT_MAX_INPUT_TOKENS,
    )
    return {
        "id": model_id,
        "value": value,
        "provider": provider,
        "label": label,
        "description": description,
        "profile": {"max_input_tokens": max_input_tokens},
    }


def _models_from_json(
    raw_catalog: str,
    *,
    default_provider: str,
) -> list[dict[str, Any]]:
    if not raw_catalog.strip():
        return []
    decoded = json.loads(raw_catalog)
    if not isinstance(decoded, list):
        raise ValueError("model_catalog_json must be a JSON list")
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in decoded:
        model = _model_from_item(item, default_provider=default_provider)
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
    default_provider = str(getattr(settings, "llm_gateway_provider", "") or "").strip()
    has_explicit_catalog = bool(raw_catalog.strip())
    models = (
        _models_from_json(raw_catalog, default_provider=default_provider)
        if has_explicit_catalog
        else []
    )
    runtime_default = ""
    for field_name in ("claude_agent_model", "anthropic_model", "openai_model"):
        candidate = str(getattr(settings, field_name, "") or "").strip()
        if candidate:
            runtime_default = candidate
            break
    if not models:
        if has_explicit_catalog or not runtime_default:
            raise ValueError(MODEL_CATALOG_NOT_CONFIGURED)
        model = _model_from_item(
            {"id": runtime_default, "label": runtime_default},
            default_provider=default_provider,
        )
        if model is not None:
            models = [model]
    if not models:
        raise ValueError(MODEL_CATALOG_NOT_CONFIGURED)
    configured_default = str(getattr(settings, "default_model_id", "") or "").strip()
    preferred_default = configured_default or runtime_default
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
