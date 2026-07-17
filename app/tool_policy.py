"""The single, fail-closed runtime policy for model tool invocation.

Adapters must present a canonical, run-scoped capability subject here.  This
module deliberately has no approval, persistence, polling, or callback API:
an invocation is either allowed now or denied now.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.validation import SAFE_ID_PATTERN

RISK_ORDER = {"low": 0, "medium": 1, "high": 2}

# Exact spellings accepted from the Claude SDK adapter.  Do not case-fold or
# trim these names: doing so would turn a lookalike invocation into authority.
BUILTIN_TOOL_IDENTITIES = frozenset(
    {
        "Read",
        "Glob",
        "LS",
        "Bash",
        "Write",
        "Edit",
        "NotebookEdit",
        "Agent",
        "WebFetch",
        "WebSearch",
        "Skill",
    }
)
@dataclass(frozen=True)
class ToolPolicyDecision:
    """One synchronous policy result; ``outcome`` is always allow or deny."""

    outcome: str
    reason: str
    canonical_identity: str
    risk_level: str
    write_capable: bool

    @property
    def allowed(self) -> bool:
        """Compatibility view for adapters; it introduces no third outcome."""

        return self.outcome == "allow"


def max_risk(left: str, right: str) -> str:
    """Retain deterministic risk metadata aggregation for read-only projections."""

    left_value = str(left or "low")
    right_value = str(right or "low")
    return left_value if RISK_ORDER.get(left_value, 0) >= RISK_ORDER.get(right_value, 0) else right_value


def _canonical_identity(value: object) -> str:
    """Accept only exact built-ins or an exact SDK MCP identity."""

    if not isinstance(value, str) or not value:
        return ""
    if value in BUILTIN_TOOL_IDENTITIES:
        return value
    if not value.startswith("mcp__"):
        return ""
    parts = value[5:].split("__", 1)
    if len(parts) != 2:
        return ""
    server, tool = parts
    if not SAFE_ID_PATTERN.fullmatch(server) or not SAFE_ID_PATTERN.fullmatch(tool):
        return ""
    return value


def _mcp_identity(server: object, tool: object) -> str:
    """Build a canonical MCP identity only after both exact segments validate."""

    if not isinstance(server, str) or not isinstance(tool, str) or not server or not tool:
        return ""
    return _canonical_identity(f"mcp__{server}__{tool}")


def _declared_identities(value: object) -> set[str]:
    if not isinstance(value, list | tuple | set | frozenset):
        return set()
    return {identity for item in value if (identity := _canonical_identity(item))}


def _risk_level(tool: dict[str, Any]) -> str:
    value = str(tool.get("risk_level") or "low")
    return value if value in RISK_ORDER else "low"


def _decision(
    outcome: str,
    reason: str,
    identity: str,
    *,
    risk_level: str,
    write_capable: bool,
) -> ToolPolicyDecision:
    return ToolPolicyDecision(
        outcome=outcome,
        reason=reason,
        canonical_identity=identity,
        risk_level=risk_level,
        write_capable=write_capable,
    )


def evaluate_tool_policy(*, tool: dict[str, Any]) -> ToolPolicyDecision:
    """Allow only one exact, already-authorized capability for this run.

    Required adapter fields are ``requested_identity``, ``declared_identities``,
    ``registered``, ``declared``, ``active`` and ``distributed``.  Identity,
    object and parameter authorization signals are intentionally independent:
    this result never substitutes for application or sandbox authorization.
    """

    risk_level = _risk_level(tool)
    write_capable = tool.get("write_capable") is True
    identity = _canonical_identity(tool.get("requested_identity")) or _mcp_identity(
        tool.get("mcp_server"), tool.get("mcp_tool")
    )
    if not identity:
        return _decision("deny", "tool_identity_malformed", "", risk_level=risk_level, write_capable=write_capable)
    if tool.get("registered") is not True:
        return _decision("deny", "tool_not_registered", identity, risk_level=risk_level, write_capable=write_capable)
    declared_identities = _declared_identities(tool.get("declared_identities"))
    if tool.get("declared") is True and not declared_identities and tool.get("mcp_server") is not None:
        # The worker fetched this exact selected MCP registration; preserve that
        # declaration as a canonical policy subject rather than comparing IDs.
        declared_identities = {identity}
    if tool.get("declared") is not True or identity not in declared_identities:
        return _decision("deny", "tool_identity_undeclared", identity, risk_level=risk_level, write_capable=write_capable)
    if tool.get("active") is not True:
        return _decision("deny", "tool_not_active", identity, risk_level=risk_level, write_capable=write_capable)
    if tool.get("distributed") is not True:
        return _decision("deny", "tool_not_distributed", identity, risk_level=risk_level, write_capable=write_capable)
    if tool.get("identity_authorized") is not True:
        return _decision("deny", "tool_identity_not_authorized", identity, risk_level=risk_level, write_capable=write_capable)
    if tool.get("object_authorized") is not True:
        return _decision("deny", "tool_object_not_authorized", identity, risk_level=risk_level, write_capable=write_capable)
    if tool.get("parameters_authorized") is not True:
        return _decision("deny", "tool_parameters_not_authorized", identity, risk_level=risk_level, write_capable=write_capable)
    return _decision("allow", "tool_policy_allowed", identity, risk_level=risk_level, write_capable=write_capable)
