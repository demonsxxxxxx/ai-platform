"""Compatibility bridge for route-level model resolver adapters."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any


_MODEL_ACCESS_CODES = {"group_not_available", "group_disabled", "group_has_no_models", "model_not_available"}
_POLICY_CODES = {"group_models_unavailable", "model_policy_unavailable"}


def inherit_explicit_selection(
    selection: dict[str, str] | None, input_json: object,
) -> dict[str, str] | None:
    if not isinstance(input_json, dict):
        return selection
    if selection is not None:
        if selection.get("id") or selection.get("value") or selection.get("selection_mode") is not None:
            return selection
        requested_group_id = selection.get("group_id")
        if requested_group_id is not None and requested_group_id != input_json.get("model_group_id"):
            return selection
    model_id = input_json.get("model_id")
    model_value = input_json.get("model_value")
    group_id = input_json.get("model_group_id")
    if not isinstance(model_id, str) and not isinstance(model_value, str):
        return selection
    return {
        **({"id": model_id} if isinstance(model_id, str) else {}),
        **({"value": model_value} if isinstance(model_value, str) else {}),
        **({"group_id": group_id} if isinstance(group_id, str) else {}),
        "selection_mode": "explicit",
    }


def model_selection_error_status(code: str) -> int:
    if code in {"model_policy_changed", "model_group_type_conflict"}:
        return 409
    return 403 if code in _MODEL_ACCESS_CODES else 503 if code in _POLICY_CODES else 400


async def select_chat_route_model(
    conn: Any, *, resolver: Callable[..., Any], selection: dict[str, str] | None,
    continuation_input: object, user_id: str, tenant_id: str, usage_scene: str,
) -> tuple[dict[str, str] | None, Any]:
    selection, selected = await resolve_chat_route_model(
        conn, resolver=resolver, selection=selection,
        continuation_input=continuation_input, user_id=user_id,
        tenant_id=tenant_id, usage_scene=usage_scene,
    )
    return selection, selected


async def resolve_chat_route_model(
    conn: Any, *, resolver: Callable[..., Any], selection: dict[str, str] | None,
    continuation_input: object, user_id: str, tenant_id: str, usage_scene: str,
) -> tuple[dict[str, str] | None, Any]:
    selection = inherit_explicit_selection(selection, continuation_input)
    selected = await resolve_model_for_route(
        conn, resolver=resolver, selection=selection, user_id=user_id,
        tenant_id=tenant_id, usage_scene=usage_scene,
    )
    return selection, selected


async def resolve_model_for_route(
    conn: Any, *, resolver: Callable[..., Any], selection: dict[str, str] | None,
    user_id: str, tenant_id: str, usage_scene: str,
) -> Any:
    kwargs: dict[str, Any] = {
        "selection": selection,
        "user_id": user_id,
        "tenant_id": tenant_id,
        "usage_scene": usage_scene,
    }
    parameters = inspect.signature(resolver).parameters
    if not any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        kwargs = {key: value for key, value in kwargs.items() if key in parameters}
    return await resolver(conn, **kwargs)
