"""Run-model selection policy owned by Execution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from app.runs.api import bind_run_model


@dataclass(frozen=True)
class RunModelSelection:
    model_id: str
    model_value: str
    connection_revision: int | None
    auto_compact_window: int | None = None
    max_output_tokens: int | None = None
    group_id: str | None = None
    group_code: str | None = None
    group_type: str | None = None
    group_revision: int | None = None
    selection_mode: str = "auto"
    usage_scene: str = "independent"
    policy_version: str | None = None


class GovernedModelResolver(Protocol):
    async def __call__(
        self,
        conn: Any,
        *,
        model_id: str | None,
        model_value: str | None,
        user_id: str,
    ) -> RunModelSelection | None: ...


async def bind_selected_run_model(
    conn: Any, *, tenant_id: str, run_id: str, selected_model: RunModelSelection,
) -> None:
    snapshot_kwargs = {}
    if selected_model.group_id:
        snapshot_kwargs = {
            "group_id": selected_model.group_id,
            "group_code": selected_model.group_code,
            "group_type": selected_model.group_type,
            "group_revision": selected_model.group_revision,
            "selection_mode": selected_model.selection_mode,
            "usage_scene": selected_model.usage_scene,
            "policy_version": selected_model.policy_version,
        }
    await bind_run_model(
        conn, tenant_id=tenant_id, run_id=run_id,
        model_id=selected_model.model_id, model_value=selected_model.model_value,
        connection_revision=selected_model.connection_revision,
        auto_compact_window=selected_model.auto_compact_window,
        max_output_tokens=selected_model.max_output_tokens,
        **snapshot_kwargs,
    )


def parse_requested_model_selection(agent_options: object) -> dict[str, str] | None:
    """Validate the public model/group selector before persistence side effects."""

    options = agent_options if isinstance(agent_options, dict) else {}
    raw_model_id = options.get("model_id")
    raw_model_value = options.get("model")
    raw_group_id = options.get("group_id")
    raw_selection_mode = options.get("selection_mode")
    if raw_group_id is not None and (
        not isinstance(raw_group_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", raw_group_id)
    ):
        raise ValueError("group_not_available")
    if raw_selection_mode is not None and raw_selection_mode not in {"auto", "explicit"}:
        raise ValueError("model_selection_conflict")
    if raw_model_id is None and raw_model_value is None and raw_group_id is None and raw_selection_mode is None:
        return None
    if raw_model_id is not None and (
        not isinstance(raw_model_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", raw_model_id)
    ):
        raise ValueError("model_id_not_available")
    if raw_model_value is not None and (
        not isinstance(raw_model_value, str)
        or not raw_model_value
        or raw_model_value != raw_model_value.strip()
        or len(raw_model_value.encode("utf-8")) > 512
        or any(ord(char) < 32 or ord(char) == 127 for char in raw_model_value)
    ):
        raise ValueError("model_id_not_available")
    if raw_model_id is None and raw_model_value is None and raw_group_id is None and raw_selection_mode is None:
        return None
    inferred_mode = "explicit" if raw_model_id is not None or raw_model_value is not None else "auto"
    if raw_selection_mode == "auto" and inferred_mode == "explicit":
        raise ValueError("model_selection_conflict")
    return {
        **({"id": raw_model_id} if raw_model_id is not None else {}),
        **({"value": raw_model_value} if raw_model_value is not None else {}),
        **({"group_id": raw_group_id} if raw_group_id is not None else {}),
        **({"selection_mode": raw_selection_mode} if raw_selection_mode is not None else {}),
    }


async def resolve_chat_model_selection(
    conn: Any,
    *,
    selection: dict[str, str] | None,
    user_id: str,
    resolve_governed_model: GovernedModelResolver,
) -> RunModelSelection:
    """Resolve only the configured, Run-budgeted model for new execution."""

    model_id = selection.get("id") if selection else None
    model_value = selection.get("value") if selection else None
    governed = await resolve_governed_model(
        conn,
        model_id=model_id,
        model_value=model_value,
        user_id=user_id,
    )
    if governed is None:
        raise ValueError("model_connection_not_configured")
    if (
        governed.connection_revision is None
        or governed.auto_compact_window is None
        or governed.max_output_tokens is None
    ):
        raise ValueError("model_capacity_missing")
    return governed
