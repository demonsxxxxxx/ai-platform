import json
import time
from typing import Any

from app.validation import assert_safe_id


MODEL_CATALOG_NOT_CONFIGURED = "model_catalog_not_configured"
DEFAULT_MAX_INPUT_TOKENS = 128000

_UPSTREAM_MODEL_CACHE_TTL_SECONDS = 60.0
_upstream_model_cache: dict[str, Any] = {
    "fetched_at": 0.0,
    "models": [],
    "error": None,
}


async def fetch_upstream_openai_models(settings: object) -> list[dict[str, Any]]:
    """Return OpenAI-compatible model options from settings.openai_base_url.

    Uses a short TTL in-process cache so the model page does not hit the
    upstream gateway on every request. Returns [] when the gateway is not
    configured or unreachable so callers can fall back to the static catalog.
    """

    base_url = str(getattr(settings, "openai_base_url", "") or "").strip().rstrip("/")
    if not base_url:
        return []
    now = time.monotonic()
    cached = _upstream_model_cache.get("models")
    if cached and (now - float(_upstream_model_cache.get("fetched_at") or 0.0)) < _UPSTREAM_MODEL_CACHE_TTL_SECONDS:
        return list(cached)
    api_key = str(getattr(settings, "openai_api_key", "") or "").strip()
    provider = str(getattr(settings, "llm_gateway_provider", "") or "").strip() or "openai_compatible"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0, headers=headers) as client:
            response = await client.get(f"{base_url}/v1/models")
            if response.status_code >= 400:
                raise RuntimeError(f"upstream_http_{response.status_code}")
            payload = response.json()
    except Exception:
        _upstream_model_cache.update({"fetched_at": now, "models": [], "error": "upstream_unavailable"})
        return []
    raw_models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(raw_models, list):
        _upstream_model_cache.update({"fetched_at": now, "models": [], "error": "invalid_upstream_response"})
        return []
    models: list[dict[str, Any]] = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        model = _model_from_item(
            {
                "id": item.get("id"),
                "value": item.get("id"),
                "label": item.get("id"),
                "provider": provider,
                "description": item.get("description") if isinstance(item.get("description"), str) else "",
            },
            default_provider=provider,
        )
        if model is not None:
            models.append(model)
    _upstream_model_cache.update({"fetched_at": now, "models": list(models), "error": None})
    return models


def upstream_model_cache_snapshot() -> tuple[list[dict[str, Any]], str | None]:
    """Return the last upstream fetch for synchronous model validation."""

    models = _upstream_model_cache.get("models") or []
    return list(models), _upstream_model_cache.get("error")


def _model_from_item(
    item: object,
    *,
    default_provider: str,
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    raw_id_value = item.get("id")
    if raw_id_value is None or (
        isinstance(raw_id_value, str) and not raw_id_value.strip()
    ):
        raw_id_value = item.get("value")
    if not isinstance(raw_id_value, str):
        return None
    raw_id = raw_id_value.strip()
    if not raw_id:
        return None
    model_id = assert_safe_id(raw_id, "model_id")
    raw_value = item.get("value")
    if raw_value is not None and not isinstance(raw_value, str):
        return None
    value = (raw_value or "").strip() or model_id
    value = assert_safe_id(value, "model_value")
    raw_provider = item.get("provider")
    raw_label = item.get("label")
    raw_description = item.get("description")
    if raw_provider is not None and not isinstance(raw_provider, str):
        return None
    if raw_label is not None and not isinstance(raw_label, str):
        return None
    if raw_description is not None and not isinstance(raw_description, str):
        return None
    provider = (raw_provider or "").strip() or default_provider
    label = (raw_label or "").strip() or model_id
    description = (raw_description or "").strip()
    raw_profile = item.get("profile")
    if raw_profile is not None and not isinstance(raw_profile, dict):
        return None
    profile = raw_profile or {}
    raw_max_input_tokens = profile.get("max_input_tokens")
    if raw_max_input_tokens is None:
        raw_max_input_tokens = item.get("max_input_tokens")
    if raw_max_input_tokens is None:
        max_input_tokens = DEFAULT_MAX_INPUT_TOKENS
    elif (
        not isinstance(raw_max_input_tokens, int)
        or isinstance(raw_max_input_tokens, bool)
        or raw_max_input_tokens <= 0
    ):
        return None
    else:
        max_input_tokens = raw_max_input_tokens
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


def resolve_model_selection(
    model_id: str | None,
    settings: object,
    *,
    upstream_ids: set[str] | None = None,
) -> dict[str, str] | None:
    """Resolve a frontend catalog id to the runtime model value used by providers."""
    if model_id is None:
        return None
    if not isinstance(model_id, str):
        raise ValueError("model_id_not_available")
    normalized = assert_safe_id(model_id.strip(), "model_id")
    if upstream_ids and normalized in upstream_ids:
        return {"id": normalized, "value": normalized}
    catalog = build_model_catalog(settings)
    for model in catalog["models"]:
        if str(model["id"]) == normalized:
            return {"id": normalized, "value": str(model["value"])}
    raise ValueError("model_id_not_available")


def validate_model_id(model_id: str | None, settings: object) -> str | None:
    """Return a catalog-approved model id or raise when the request is invalid."""
    selection = resolve_model_selection(model_id, settings)
    return selection["id"] if selection is not None else None
