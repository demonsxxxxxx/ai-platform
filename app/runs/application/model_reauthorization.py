"""Current model-group reauthorization for derived Run admission."""

from __future__ import annotations

from typing import Any

from app.runs.domain.model_group_snapshot import validate_model_group_snapshot

_ALLOWED_SCENES = {"chat", "expert", "skill", "independent"}


def trusted_usage_scene(
    *, execution_kind: str, session: dict[str, Any] | None,
    requested_scene: object, has_session: bool,
) -> str:
    if execution_kind == "harness_chat":
        return "chat"
    if execution_kind == "skill":
        if not has_session:
            return "skill"
        if session is not None and str(session.get("status") or "") == "active":
            return "expert" if str(session.get("purpose") or "conversation") == "conversation" else "skill"
        raise ValueError("model_not_available_to_user")
    if isinstance(requested_scene, str) and requested_scene in _ALLOWED_SCENES:
        return requested_scene
    raise ValueError("model_not_available_to_user")


async def direct_run_usage_scene(
    conn: Any, *, execution_kind: str, session_id: str | None,
    tenant_id: str, user_id: str, workspace_id: str, load_session: Any,
) -> str:
    session = None
    if session_id is not None:
        session = await load_session(conn, tenant_id=tenant_id, session_id=session_id)
        if session is None or session.get("user_id") != user_id or session.get("workspace_id") != workspace_id:
            raise ValueError("group_not_available")
        if session.get("status") != "active":
            raise ValueError("group_not_available")
    return trusted_usage_scene(
        execution_kind=execution_kind, session=session,
        requested_scene=None, has_session=session_id is not None,
    )


async def reauthorize_model(
    conn: Any, *, snapshot: dict[str, Any], tenant_id: str, user_id: str,
    load_session: Any,
) -> Any:
    model_id = snapshot.get("model_id")
    if not isinstance(model_id, str) or not model_id:
        return None
    session_id = snapshot.get("session_id")
    session = await load_session(conn, tenant_id=tenant_id, session_id=session_id) if session_id else None
    if session_id and (session is None or session.get("user_id") != user_id):
        raise ValueError("model_not_available_to_user")
    group_id = snapshot.get("model_group_id")
    if group_id is not None:
        validate_model_group_snapshot(
            group_id=group_id, group_code=snapshot.get("model_group_code"),
            group_type=snapshot.get("model_group_type"), group_revision=snapshot.get("model_group_revision"),
            selection_mode=snapshot.get("model_selection_mode"), usage_scene=snapshot.get("usage_scene"),
            policy_version=snapshot.get("model_policy_version"),
        )
        usage_scene = snapshot["usage_scene"]
    else:
        usage_scene = trusted_usage_scene(
            execution_kind=str(snapshot.get("execution_kind") or "skill"),
            session=session, requested_scene=None, has_session=bool(session_id),
        )
    selection = {
        "id": model_id,
        "value": str(snapshot.get("model_value") or ""),
        "selection_mode": "explicit",
    }
    if isinstance(group_id, str) and group_id:
        selection["group_id"] = group_id
    from app.execution.api import resolve_chat_model_selection

    return await resolve_chat_model_selection(
        conn,
        selection=selection,
        user_id=user_id,
        tenant_id=tenant_id,
        usage_scene=usage_scene,
    )


def requested_run_model_selection(request: Any) -> dict[str, str] | None:
    if request.model_id is None and request.model is None and request.group_id is None:
        return None
    return {
        **({"id": request.model_id} if request.model_id is not None else {}),
        **({"value": request.model} if request.model is not None else {}),
        **({"group_id": request.group_id} if request.group_id is not None else {}),
        **({"selection_mode": request.selection_mode} if request.selection_mode is not None else {}),
    }


def selected_model_binding_fields(selected_model: Any) -> dict[str, Any]:
    return {
        "model_id": selected_model.model_id,
        "model_value": selected_model.model_value,
        "connection_revision": selected_model.connection_revision,
        "auto_compact_window": selected_model.auto_compact_window,
        "max_output_tokens": selected_model.max_output_tokens,
        "group_id": getattr(selected_model, "group_id", None),
        "group_code": getattr(selected_model, "group_code", None),
        "group_type": getattr(selected_model, "group_type", None),
        "group_revision": getattr(selected_model, "group_revision", None),
        "selection_mode": getattr(selected_model, "selection_mode", "auto"),
        "usage_scene": getattr(selected_model, "usage_scene", "independent"),
        "policy_version": getattr(selected_model, "policy_version", None),
    }
