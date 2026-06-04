from __future__ import annotations

from dataclasses import dataclass
from typing import Any


RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
ALLOW_DECISIONS = {"allow_once", "allow_for_run"}


@dataclass(frozen=True)
class ToolPolicyDecision:
    allowed: bool
    reason: str
    risk_level: str
    write_capable: bool
    decision: str = ""
    permission_request_id: str = ""
    auto_allowed: bool = False


def max_risk(left: str, right: str) -> str:
    left_value = str(left or "low")
    right_value = str(right or "low")
    return left_value if RISK_ORDER.get(left_value, 0) >= RISK_ORDER.get(right_value, 0) else right_value


def evaluate_tool_policy(
    *,
    tool: dict[str, Any],
    permission_decision: dict[str, Any] | None = None,
    requested_risk_level: str = "low",
    requested_write_capable: bool = False,
) -> ToolPolicyDecision:
    risk_level = max_risk(str(tool.get("risk_level") or "low"), requested_risk_level)
    write_capable = bool(tool.get("write_capable")) or bool(requested_write_capable)
    requires_decision = write_capable or RISK_ORDER.get(risk_level, 0) >= RISK_ORDER["medium"]
    if not requires_decision:
        return ToolPolicyDecision(
            allowed=True,
            reason="read_only_low_risk_auto_allowed",
            risk_level=risk_level,
            write_capable=write_capable,
            auto_allowed=True,
        )

    if permission_decision is None:
        return ToolPolicyDecision(
            allowed=False,
            reason="tool_permission_required",
            risk_level=risk_level,
            write_capable=write_capable,
        )

    decision = str(permission_decision.get("decision") or "")
    request_id = str(permission_decision.get("id") or "")
    if decision in ALLOW_DECISIONS:
        return ToolPolicyDecision(
            allowed=True,
            reason="tool_permission_allowed",
            risk_level=risk_level,
            write_capable=write_capable,
            decision=decision,
            permission_request_id=request_id,
        )
    return ToolPolicyDecision(
        allowed=False,
        reason="tool_permission_denied",
        risk_level=risk_level,
        write_capable=write_capable,
        decision=decision,
        permission_request_id=request_id,
    )
