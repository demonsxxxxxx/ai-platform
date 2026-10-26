"""Host-owned identities and decisions for uncertain execution effects."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

RECOVERY_CONFIRMATION_TIMEOUT_SECONDS = 300


class RecoveryOperationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _identity(value: str, maximum: int = 128) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum or not value.isascii() or any(ord(c) < 32 for c in value):
        raise RecoveryOperationError("recovery_operation_identity_invalid")


@dataclass(frozen=True)
class RecoveryOperationScope:
    tenant_id: str
    workspace_id: str
    user_id: str
    session_id: str

    def __post_init__(self) -> None:
        for value in asdict(self).values():
            _identity(value)

    def mapping(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class RecoveryInvocation:
    scope: RecoveryOperationScope
    run_id: str
    attempt_id: str
    owner_generation: int
    invocation_id: str
    tool_identity: str

    def __post_init__(self) -> None:
        for value in (self.run_id, self.attempt_id):
            _identity(value)
        _identity(self.invocation_id, 512)
        if not isinstance(self.tool_identity, str) or not self.tool_identity or len(self.tool_identity) > 512:
            raise RecoveryOperationError("recovery_operation_identity_invalid")
        if type(self.owner_generation) is not int or self.owner_generation < 1:
            raise RecoveryOperationError("recovery_operation_generation_invalid")

    def mapping(self) -> dict[str, Any]:
        return {**self.scope.mapping(), "run_id": self.run_id, "attempt_id": self.attempt_id,
                "owner_generation": self.owner_generation, "invocation_id": self.invocation_id,
                "tool_identity": self.tool_identity, "binding_kind": "native_invocation"}


@dataclass(frozen=True)
class RecoverySourceConfirmation(RecoveryInvocation):
    """Host question identity; invocation_id is an operation key, never an SDK call."""
    binding_kind: str = field(default="source_operation", init=False)

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.invocation_id.startswith("rop_") or len(self.invocation_id) != 36:
            raise RecoveryOperationError("recovery_operation_identity_invalid")

    def mapping(self) -> dict[str, Any]:
        return {**super().mapping(), "binding_kind": self.binding_kind}


def source_operation_question(summary: str) -> list[dict[str, Any]]:
    return [{
        "key": "q0", "header": "核实操作结果", "multiSelect": False,
        "question": f"上轮操作：{summary}。请核实原操作的实际结果；此确认不会执行或重试操作。",
        "options": [
            {"key": "o0", "label": "尚未确认，保持只读", "description": "保留原操作结果未知", "recommended": False},
            {"key": "o1", "label": "确认原操作已完成", "description": "记录已完成，禁止重做", "recommended": False},
            {"key": "o2", "label": "确认原操作未生效", "description": "仅记录核实结果，不授权重试", "recommended": False},
        ],
    }]


@dataclass(frozen=True)
class RecoveryOperationFact:
    public_summary: str
    outcome: str
    provenance: str = "authenticated_owner_confirmation"

    def __post_init__(self) -> None:
        if (not isinstance(self.public_summary, str) or not self.public_summary.strip()
            or len(self.public_summary) > 1000 or any(ord(c) < 32 for c in self.public_summary)
            or not isinstance(self.outcome, str) or self.outcome not in {"succeeded", "not_effective"}
            or self.provenance != "authenticated_owner_confirmation"):
            raise RecoveryOperationError("recovery_operation_facts_invalid")

    def mapping(self) -> dict[str, str]:
        return asdict(self)


def validate_recovery_operation_facts(value: object) -> tuple[RecoveryOperationFact, ...]:
    if not isinstance(value, list) or len(value) > 128:
        raise RecoveryOperationError("recovery_operation_facts_invalid")
    facts = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"public_summary", "outcome", "provenance"}:
            raise RecoveryOperationError("recovery_operation_facts_invalid")
        facts.append(RecoveryOperationFact(**item))
    if len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 128 * 1000:
        raise RecoveryOperationError("recovery_operation_facts_invalid")
    return tuple(facts)


def operation_permission(operation: dict[str, Any], binding: dict[str, Any]) -> tuple[str, str]:
    # A completed source operation is never permission to perform it again.
    if operation["verified_outcome"] == "succeeded":
        return "deny", "operation_already_completed"
    if binding["started"]:
        return "deny", "operation_already_started"
    if binding["closed"]:
        return "deny", "operation_confirmation_closed"
    if binding["authorization"] == "retry" and operation["verified_outcome"] == "not_effective":
        return "allow", "retry_not_effective"
    if binding["authorization"] == "current_invocation":
        return "allow", "current_invocation_authorized"
    if binding["authorization"] == "hold":
        return "deny", "operation_confirmation_declined"
    return "wait", "operation_confirmation_required"


def operation_question(source_summary: str, current_summary: str) -> list[dict[str, Any]]:
    return [{
        "key": "q0", "header": "核实操作结果", "multiSelect": False,
        "question": f"上轮操作：{source_summary}。当前暂停的调用：{current_summary}。请核实后选择；已完成的操作不会重做。",
        "options": [
            {"key": "o0", "label": "尚未确认，保持只读", "description": "不执行这次调用", "recommended": False},
            {"key": "o1", "label": "确认原操作已完成", "description": "记录已完成，禁止重做", "recommended": False},
            {"key": "o2", "label": "确认原操作未生效，并重试本次调用", "description": "仅允许当前这一次调用", "recommended": False},
            {"key": "o3", "label": "明确授权当前这一次调用", "description": "原操作结果仍可能未知；仅授权当前调用", "recommended": False},
        ],
    }]
