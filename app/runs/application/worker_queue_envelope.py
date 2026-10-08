"""Validate one leased queue envelope before Run admission."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, Self


class WorkerDispatchPayload(Protocol):
    """Validated queue data shared by Runs admission, binding, and commit.

    The queue transport supplies its validated model. This contract describes
    the data used by these operations without depending on that legacy model.
    """

    tenant_id: str
    workspace_id: str
    user_id: str
    session_id: str
    run_id: str
    agent_id: str
    skill_id: str | None
    executor_type: str
    context_snapshot_id: str | None
    file_ids: list[str]
    input: dict[str, Any]
    skill_manifests: list[dict[str, Any]]
    release_decision: dict[str, Any]

    def model_copy(self, *, update: dict[str, Any]) -> Self: ...


class InvalidLeasedQueueEnvelope(ValueError):
    """The queue-private lease identity is missing or cannot be trusted."""


@dataclass(frozen=True)
class LeasedQueueEnvelope:
    payload: Any
    attempt_id: str


def parse_leased_queue_envelope(
    raw: dict[str, Any],
    *,
    attempt_field: str,
    validate_attempt_id: Callable[[str, str], Any],
    parse_payload: Callable[[dict[str, Any]], Any],
) -> LeasedQueueEnvelope:
    attempt_id = raw.get(attempt_field)
    if not isinstance(attempt_id, str) or not attempt_id:
        raise InvalidLeasedQueueEnvelope("Queue lease attempt identity is required.")
    try:
        validate_attempt_id(attempt_id, "attempt_id")
    except ValueError as exc:
        raise InvalidLeasedQueueEnvelope("Queue lease attempt identity is invalid.") from exc
    parseable_raw = dict(raw)
    parseable_raw.pop(attempt_field)
    return LeasedQueueEnvelope(payload=parse_payload(parseable_raw), attempt_id=attempt_id)
