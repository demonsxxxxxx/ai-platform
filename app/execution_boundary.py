from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


CLAUDE_WORKER_EXECUTOR = "claude-agent-worker"
REAL_SANDBOX_PROVIDERS = frozenset({"docker", "opensandbox"})
REAL_SANDBOX_EVIDENCE_SOURCE = "sandbox_runtime"
REAL_SANDBOX_EVIDENCE_CLASS = "runtime_lease_projection"
GOVERNED_EGRESS_PROOF_SCHEMA = "ai-platform.governed-egress-proof.v1"
GOVERNED_EGRESS_PROOF_LABEL = "ai-platform.governed_egress.proof"
_GOVERNED_EGRESS_PROOF_KEYS = frozenset(
    {
        "schema_version",
        "provider",
        "default_deny_outbound",
        "governed_callback_exception",
        "policy_bound_enforcement",
        "runtime_subject_sha256",
        "policy_subject_sha256",
        "callback_subject_sha256",
        "denial_subject_sha256",
    }
)
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


def _governed_egress_subject_digest(value: object) -> str:
    """Return a bounded irreversible subject projection for durable egress proof."""
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 512:
        raise ValueError("governed_egress_subject_invalid")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_governed_egress_proof(
    *,
    provider: str,
    runtime_subject: object,
    policy_subject: object,
    callback_subject: object,
    denial_subject: object,
) -> dict[str, object]:
    """Build the one redacted, provider-neutral proof admitted by real sandboxes."""
    if provider not in REAL_SANDBOX_PROVIDERS:
        raise ValueError("governed_egress_provider_invalid")
    return {
        "schema_version": GOVERNED_EGRESS_PROOF_SCHEMA,
        "provider": provider,
        "default_deny_outbound": True,
        "governed_callback_exception": True,
        "policy_bound_enforcement": True,
        "runtime_subject_sha256": _governed_egress_subject_digest(runtime_subject),
        "policy_subject_sha256": _governed_egress_subject_digest(policy_subject),
        "callback_subject_sha256": _governed_egress_subject_digest(callback_subject),
        "denial_subject_sha256": _governed_egress_subject_digest(denial_subject),
    }


def is_governed_egress_proof(proof: object, *, provider: str) -> bool:
    """Accept only the exact bounded proof shape emitted after provider admission."""
    if provider not in REAL_SANDBOX_PROVIDERS or not isinstance(proof, dict):
        return False
    if set(proof) != _GOVERNED_EGRESS_PROOF_KEYS:
        return False
    if (
        proof.get("schema_version") != GOVERNED_EGRESS_PROOF_SCHEMA
        or proof.get("provider") != provider
        or proof.get("default_deny_outbound") is not True
        or proof.get("governed_callback_exception") is not True
        or proof.get("policy_bound_enforcement") is not True
    ):
        return False
    digests = (
        proof.get("runtime_subject_sha256"),
        proof.get("policy_subject_sha256"),
        proof.get("callback_subject_sha256"),
        proof.get("denial_subject_sha256"),
    )
    return all(isinstance(digest, str) and len(digest) == 64 and all(char in "0123456789abcdef" for char in digest) for digest in digests)


def governed_egress_proof_label(proof: object) -> str:
    """Encode a validated proof for provider metadata without durable private fields."""
    if not isinstance(proof, dict) or not is_governed_egress_proof(proof, provider=str(proof.get("provider") or "")):
        raise ValueError("governed_egress_proof_invalid")
    return json.dumps(proof, sort_keys=True, separators=(",", ":"))


def governed_egress_proof_from_labels(provider: str, labels: object) -> dict[str, object] | None:
    """Recover only the canonical provider proof; labels alone are never acceptance evidence."""
    if provider not in REAL_SANDBOX_PROVIDERS or not isinstance(labels, dict):
        return None
    encoded = labels.get(GOVERNED_EGRESS_PROOF_LABEL)
    if not isinstance(encoded, str) or len(encoded) > 2048:
        return None
    try:
        proof = json.loads(encoded)
    except (TypeError, ValueError):
        return None
    return proof if is_governed_egress_proof(proof, provider=provider) else None


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
        and is_governed_egress_proof(payload.get("governed_egress_proof"), provider=provider)
    )
