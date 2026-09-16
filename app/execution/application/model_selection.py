"""Run-model selection policy owned by Execution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class RunModelSelection:
    model_id: str
    model_value: str
    connection_revision: int | None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None


class GovernedModelResolver(Protocol):
    async def __call__(
        self,
        conn: Any,
        *,
        model_id: str | None,
        model_value: str | None,
    ) -> RunModelSelection | None: ...


def parse_requested_model_selection(agent_options: object) -> dict[str, str] | None:
    """Validate the public Chat model selector before persistence side effects."""

    options = agent_options if isinstance(agent_options, dict) else {}
    raw_model_id = options.get("model_id")
    raw_model_value = options.get("model")
    if raw_model_id is None and raw_model_value is None:
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
    return {
        **({"id": raw_model_id} if raw_model_id is not None else {}),
        **({"value": raw_model_value} if raw_model_value is not None else {}),
    }


async def resolve_chat_model_selection(
    conn: Any,
    *,
    selection: dict[str, str] | None,
    resolve_governed_model: GovernedModelResolver,
) -> RunModelSelection:
    """Resolve only the configured, Run-budgeted model for new execution."""

    model_id = selection.get("id") if selection else None
    model_value = selection.get("value") if selection else None
    governed = await resolve_governed_model(
        conn,
        model_id=model_id,
        model_value=model_value,
    )
    if governed is None:
        raise ValueError("model_connection_not_configured")
    if (
        governed.connection_revision is None
        or governed.max_input_tokens is None
        or governed.max_output_tokens is None
    ):
        raise ValueError("model_capacity_missing")
    return governed
