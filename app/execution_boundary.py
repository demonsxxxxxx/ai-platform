from __future__ import annotations

from dataclasses import dataclass
from typing import Any


CLAUDE_WORKER_EXECUTOR = "claude-agent-worker"
REAL_SANDBOX_PROVIDERS = frozenset({"docker", "opensandbox"})
REAL_SANDBOX_EVIDENCE_SOURCE = "sandbox_runtime"
REAL_SANDBOX_EVIDENCE_CLASS = "runtime_lease_projection"
SANDBOX_BROKERED_PERMISSION_POLICY = "sandbox_brokered"
SINGLE_RUN_WRITING_TIERS = frozenset({"sdk_only_writing", "document_worker", "heavy_sandbox"})


@dataclass(frozen=True)
class ExecutionBoundaryDecision:
    """Describe the trusted execution and evidence contract for one run."""

    requires_real_sandbox: bool
    accepted_providers: frozenset[str]
    permission_policy: str
    evidence_source: str
    evidence_class: str
    local_sdk_allowed: bool
    fail_closed: bool
    reason: str


def decide_execution_boundary(
    *,
    executor_type: str,
    execution_mode: str,
    execution_tier: str,
) -> ExecutionBoundaryDecision:
    """Resolve one execution authority decision without inspecting user input modes."""
    if executor_type != CLAUDE_WORKER_EXECUTOR:
        return ExecutionBoundaryDecision(
            requires_real_sandbox=False,
            accepted_providers=frozenset(),
            permission_policy="adapter_managed",
            evidence_source="",
            evidence_class="",
            local_sdk_allowed=False,
            fail_closed=False,
            reason="non_claude_adapter",
        )

    common = {
        "requires_real_sandbox": True,
        "accepted_providers": REAL_SANDBOX_PROVIDERS,
        "permission_policy": SANDBOX_BROKERED_PERMISSION_POLICY,
        "evidence_source": REAL_SANDBOX_EVIDENCE_SOURCE,
        "evidence_class": REAL_SANDBOX_EVIDENCE_CLASS,
        "local_sdk_allowed": False,
    }
    if execution_mode == "multi_agent":
        return ExecutionBoundaryDecision(
            **common,
            fail_closed=True,
            reason="multi_agent_adapter_execution_disabled",
        )
    if execution_tier not in SINGLE_RUN_WRITING_TIERS:
        return ExecutionBoundaryDecision(
            **common,
            fail_closed=True,
            reason="untrusted_claude_execution_tier",
        )
    return ExecutionBoundaryDecision(
        **common,
        fail_closed=False,
        reason="ordinary_claude_writing_requires_real_sandbox",
    )


def is_accepted_runtime_lease(row: dict[str, Any]) -> bool:
    """Return whether a persisted lease row is admissible as real runtime proof."""
    provider = str(row.get("provider") or "")
    payload = row.get("lease_payload_json")
    if not isinstance(payload, dict):
        payload = row.get("lease_payload")
    if not isinstance(payload, dict):
        return False
    return (
        provider in REAL_SANDBOX_PROVIDERS
        and str(payload.get("source") or "") == REAL_SANDBOX_EVIDENCE_SOURCE
        and str(payload.get("evidence_class") or "") == REAL_SANDBOX_EVIDENCE_CLASS
    )
