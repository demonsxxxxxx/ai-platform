"""Upstream OpenAI-compatible model gateway access (platform layer).

This module is the only place that calls the configured model gateway's
``/v1/models`` endpoint. It lives in the platform package so it may use
third-party HTTP clients (httpx) without expanding legacy app modules.
"""

from __future__ import annotations

import time
from typing import Any

from app.validation import assert_safe_id

_UPSTREAM_MODEL_CACHE_TTL_SECONDS = 60.0
_upstream_model_cache: dict[str, Any] = {
    "fetched_at": 0.0,
    "models": [],
    "error": None,
}


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
        return None
    if not isinstance(raw_id_value, str):
        return None
    raw_id = raw_id_value.strip()
    if not raw_id:
        return None
    model_id = assert_safe_id(raw_id, "model_id")
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
    return {
        "id": model_id,
        "value": model_id,
        "provider": provider,
        "label": label,
        "description": description,
        "profile": {"max_input_tokens": 128000},
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
