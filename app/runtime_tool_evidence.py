"""Validate sandbox tool evidence before it crosses into worker results."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.required_tool_contract import (
    REQUIRED_CAPABILITY_EVIDENCE_KEY,
    TOOL_INVOCATION_EVIDENCE_KEY,
    RequiredCapabilityEvidence,
    RequiredToolContractError,
    validate_tool_invocation_evidence,
)

_EVIDENCE_MISMATCH = "tool_invocation_evidence_mismatch"


@dataclass(frozen=True)
class RuntimeToolEvidenceValidation:
    tool_invocation_evidence: list[dict[str, str]]
    required_capability_evidence: dict[str, object] | None
    error_code: str | None

    def private_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            TOOL_INVOCATION_EVIDENCE_KEY: self.tool_invocation_evidence,
        }
        if self.required_capability_evidence is not None:
            payload[REQUIRED_CAPABILITY_EVIDENCE_KEY] = self.required_capability_evidence
        return payload


def validate_runtime_tool_evidence(
    executor_response: Mapping[str, object],
    *,
    binding: Mapping[str, object],
    capability_evidence: object,
    capability_error: str | None,
) -> RuntimeToolEvidenceValidation:
    """Normalize local-tool evidence and enforce one owner for every call ID."""

    validation_error: str | None = None
    try:
        tool_invocations = validate_tool_invocation_evidence(
            executor_response.get(TOOL_INVOCATION_EVIDENCE_KEY),
            binding=binding,
        )
    except RequiredToolContractError:
        tool_invocations = []
        validation_error = _EVIDENCE_MISMATCH

    required_evidence = executor_response.get(REQUIRED_CAPABILITY_EVIDENCE_KEY)
    owner_error = _evidence_owner_error(
        capability_evidence=capability_evidence,
        tool_invocation_evidence=tool_invocations,
        required_capability_evidence=required_evidence,
    )
    return RuntimeToolEvidenceValidation(
        tool_invocation_evidence=tool_invocations,
        required_capability_evidence=(
            dict(required_evidence) if isinstance(required_evidence, dict) else None
        ),
        error_code=validation_error or capability_error or owner_error,
    )


def _evidence_owner_error(
    *,
    capability_evidence: object,
    tool_invocation_evidence: list[dict[str, str]],
    required_capability_evidence: object,
) -> str | None:
    owners: dict[str, tuple[str, str]] = {}

    def claim(call_id: str, owner: tuple[str, str]) -> bool:
        return owners.setdefault(call_id, owner) == owner

    try:
        if not isinstance(capability_evidence, list):
            raise RequiredToolContractError(_EVIDENCE_MISMATCH)
        for raw in capability_evidence:
            record = RequiredCapabilityEvidence.from_payload(raw)
            if not claim(
                record.tool_call_id,
                (record.capability_kind, record.canonical_identity),
            ):
                return _EVIDENCE_MISMATCH

        completed_local_calls: set[tuple[str, str]] = set()
        for raw in tool_invocation_evidence:
            call_id = str(raw.get("tool_call_id") or "")
            identity = str(raw.get("canonical_identity") or "")
            if not claim(call_id, ("builtin", identity)):
                return _EVIDENCE_MISMATCH
            if raw.get("lifecycle_phase") == "completed":
                completed_local_calls.add((identity, call_id))

        if required_capability_evidence is not None:
            record = RequiredCapabilityEvidence.from_payload(required_capability_evidence)
            if (
                record.capability_kind != "builtin"
                or record.lifecycle_phase != "completed"
                or record.lifecycle_status != "succeeded"
                or not claim(
                    record.tool_call_id,
                    (record.capability_kind, record.canonical_identity),
                )
                or (record.canonical_identity, record.tool_call_id)
                not in completed_local_calls
            ):
                return _EVIDENCE_MISMATCH
    except RequiredToolContractError:
        return _EVIDENCE_MISMATCH
    return None
