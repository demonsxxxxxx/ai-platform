"""Adapter for the deployment-backed legacy model catalog."""

from __future__ import annotations

from app.execution.application.model_selection import RunModelSelection
from app.model_catalog import build_model_catalog, resolve_model_selection
from app.platform.model_upstream import (
    fetch_upstream_openai_models,
    upstream_model_cache_snapshot,
)
from app.settings import get_settings


class LegacyModelCatalogAdapter:
    async def public_models(self) -> dict[str, object]:
        settings = get_settings()
        upstream_models = await fetch_upstream_openai_models(settings)
        if upstream_models:
            model_ids = {str(model["id"]) for model in upstream_models}
            runtime_default = str(getattr(settings, "default_model_id", "") or "").strip()
            if runtime_default not in model_ids:
                runtime_default = str(upstream_models[0]["id"])
            return {
                "models": upstream_models,
                "count": len(upstream_models),
                "enabled_count": len(upstream_models),
                "default_model_id": runtime_default,
            }
        return build_model_catalog(settings)

    def resolve(self, selection: dict[str, str] | None) -> RunModelSelection:
        model_id = selection.get("id") if selection else None
        settings = get_settings()
        if selection is None:
            try:
                model_id = str(build_model_catalog(settings)["default_model_id"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("model_id_not_available") from exc
        if model_id is None:
            raise ValueError("model_id_not_available")
        try:
            upstream_ids = None
            upstream_models, _ = upstream_model_cache_snapshot()
            if upstream_models:
                upstream_ids = {str(model["id"]) for model in upstream_models}
            legacy = resolve_model_selection(
                model_id,
                settings,
                upstream_ids=upstream_ids,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("model_id_not_available") from exc
        return RunModelSelection(
            model_id=str(legacy["id"]),
            model_value=str(legacy["value"]),
            connection_revision=None,
        )
