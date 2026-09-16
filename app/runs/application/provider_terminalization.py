"""Runs terminal composition for assistant persistence and provider coverage."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.context.api import commit_provider_turn, load_checkpoint_usage_for_run


_update_checkpoint_counts: Callable[..., Awaitable[None]] | None = None
_validate_terminal_result: Callable[[dict[str, Any]], None] | None = None


def configure_terminal_checkpoint_dependencies(
    *, update_counts: Callable[..., Awaitable[None]],
    validate_result: Callable[[dict[str, Any]], None],
) -> None:
    global _update_checkpoint_counts, _validate_terminal_result
    _update_checkpoint_counts = update_counts
    _validate_terminal_result = validate_result


def _configured_checkpoint_dependencies() -> tuple[Callable[..., Awaitable[None]], Callable[[dict[str, Any]], None]]:
    if _update_checkpoint_counts is None or _validate_terminal_result is None:
        raise RuntimeError("run_terminal_checkpoint_dependencies_not_configured")
    return _update_checkpoint_counts, _validate_terminal_result


async def result_with_checkpoint_usage(
    conn: Any, *, tenant_id: str, run_id: str, result_json: dict[str, Any],
) -> dict[str, Any]:
    usage = await load_checkpoint_usage_for_run(conn, tenant_id=tenant_id, run_id=run_id)
    added_input, added_output = usage["input_tokens"], usage["output_tokens"]
    if (type(added_input) is not int or type(added_output) is not int
        or added_input < 0 or added_output < 0):
        raise ValueError("conversation_checkpoint_usage_invalid")
    if added_input + added_output == 0:
        return result_json
    counts = result_json.get("token_counts") if isinstance(result_json.get("token_counts"), dict) else {}
    original_input = int(counts.get("input") or 0)
    original_output = int(counts.get("output") or 0)
    original_total = int(counts.get("total") or (original_input + original_output))
    input_total, output_total = original_input + added_input, original_output + added_output
    total = original_total + added_input + added_output
    if min(input_total, output_total, total) < 0 or max(input_total, output_total, total) > 2**31 - 1:
        raise ValueError("conversation_checkpoint_usage_overflow")
    merged = {**result_json, "token_counts": {**counts, "input": input_total,
                                              "output": output_total, "total": total}}
    _configured_checkpoint_dependencies()[1](merged)
    return merged


async def commit_terminal_checkpoint_usage(
    conn: Any, *, tenant_id: str, run_id: str, result_json: dict[str, Any],
) -> dict[str, Any]:
    merged = await result_with_checkpoint_usage(
        conn, tenant_id=tenant_id, run_id=run_id, result_json=result_json,
    )
    if merged is not result_json:
        counts = merged["token_counts"]
        await _configured_checkpoint_dependencies()[0](
            conn, tenant_id=tenant_id, run_id=run_id, result_json=merged,
            input_tokens=counts["input"], output_tokens=counts["output"],
            total_tokens=counts["total"],
        )
    return merged


async def persist_assistant_with_provider_coverage(
    conn: Any, *, append_message: Callable[..., Awaitable[str]],
    tenant_id: str, session_id: str, run_id: str, attempt_id: str,
    executor_type: str, content: str, metadata_json: dict[str, Any],
    provider_final_sequence: int | None,
) -> str:
    message_id = await append_message(
        conn, tenant_id=tenant_id, session_id=session_id, run_id=run_id,
        role="assistant", content=content, metadata_json=metadata_json,
    )
    if executor_type == "claude-agent-worker":
        await commit_provider_turn(
            conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
            assistant_message_id=message_id, final_sequence=provider_final_sequence,
        )
    return message_id
