"""Shared projection of an admitted model selection into Run-owned fields."""

from __future__ import annotations

from typing import Any

from app.execution.application.model_selection import RunModelSelection


def restored_session_model_metadata(selection: object) -> dict[str, Any]:
    """Expose only model identity; a resumed conversation keeps its frozen choice."""
    if not isinstance(selection, dict):
        return {}
    options = {key: selection[key] for key in ("model_id", "model", "group_id")
               if isinstance(selection.get(key), str) and selection[key]}
    if not options.get("model_id") or not options.get("model"):
        return {}
    return {"agent_options": {**options, "selection_mode": "explicit"}}


def group_snapshot_fields(selected_model: RunModelSelection | None) -> dict[str, Any]:
    group_id = getattr(selected_model, "group_id", None) if selected_model is not None else None
    if not group_id:
        return {}
    return {
        "model_group_id": group_id,
        "model_group_code": getattr(selected_model, "group_code", None),
        "model_group_type": getattr(selected_model, "group_type", None),
        "model_group_revision": getattr(selected_model, "group_revision", None),
        "model_selection_mode": getattr(selected_model, "selection_mode", "explicit"),
        "usage_scene": getattr(selected_model, "usage_scene", "independent"),
        "model_policy_version": getattr(selected_model, "policy_version", None),
    }
