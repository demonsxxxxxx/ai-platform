from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from app.settings import get_settings


CLAUDE_WORKER_EXECUTOR = "claude-agent-worker"
REAL_SANDBOX_PROVIDERS = frozenset({"docker", "opensandbox"})
REAL_SANDBOX_EVIDENCE_SOURCE = "sandbox_runtime"
REAL_SANDBOX_EVIDENCE_CLASS = "runtime_lease_projection"
GOVERNED_EGRESS_PROOF_SCHEMA = "ai-platform.governed-egress-proof.v2"
GOVERNED_EGRESS_PROOF_LABEL = "ai-platform.governed_egress.proof"
GOVERNED_EGRESS_PROOF_MAX_TTL_SECONDS = 900
GOVERNED_EGRESS_PROOF_MIN_SIGNING_KEY_BYTES = 32
GOVERNED_EGRESS_PROOF_DEFAULT_KEY_ID = "current"
GOVERNED_EGRESS_PROOF_MAX_PREVIOUS_KEYS = 4
_GOVERNED_EGRESS_PROOF_KEYS = frozenset(
    {
        "schema_version",
        "key_id",
        "provider",
        "source",
        "evidence_class",
        "issued_at",
        "expires_at",
        "default_deny_outbound",
        "governed_callback_exception",
        "policy_bound_enforcement",
        "network_internal",
        "runtime_subject_sha256",
        "policy_subject_sha256",
        "callback_subject_sha256",
        "denial_subject_sha256",
        "network_id_sha256",
        "network_name_sha256",
        "tenant_id_sha256",
        "workspace_id_sha256",
        "user_id_sha256",
        "session_id_sha256",
        "run_id_sha256",
        "image_subject_sha256",
        "image_digest_sha256",
        "authorized_skill_scope_sha256",
        "authorized_native_tool_scope_sha256",
        "lease_identity_sha256",
        "signature",
    }
)
_GOVERNED_EGRESS_SUBJECT_FIELDS = (
    "runtime_subject",
    "policy_subject",
    "callback_subject",
    "denial_subject",
    "network_id",
    "network_name",
    "tenant_id",
    "workspace_id",
    "user_id",
    "session_id",
    "run_id",
    "image_subject",
    "image_digest",
    "authorized_skill_scope",
    "authorized_native_tool_scope",
    "lease_identity",
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
    """Return a bounded irreversible subject projection for a durable proof."""
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 4096:
        raise ValueError("governed_egress_subject_invalid")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _canonical_scope_subject(value: object) -> str:
    """Canonicalize one authorized scope before it becomes an irreversible subject."""
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("governed_egress_scope_invalid") from exc
    if not encoded or len(encoded) > 4096:
        raise ValueError("governed_egress_scope_invalid")
    return encoded


def governed_egress_authorized_skill_scope(*, skill_ids: object, mcp_tool_ids: object) -> str:
    """Build the canonical authorized Skill/MCP scope subject for one runtime request."""
    return _canonical_scope_subject({"mcp_tool_ids": mcp_tool_ids or [], "skill_ids": skill_ids or []})


def governed_egress_authorized_native_tool_scope(tool_policy_subjects: object) -> str:
    """Build the canonical authorized native-tool scope subject for one runtime request."""
    return _canonical_scope_subject({"tool_policy_subjects": tool_policy_subjects or []})


def _valid_signing_key(signing_key: object) -> bytes | None:
    if not isinstance(signing_key, str):
        return None
    encoded = signing_key.encode("utf-8")
    normalized = signing_key.strip().lower()
    if (
        len(encoded) < GOVERNED_EGRESS_PROOF_MIN_SIGNING_KEY_BYTES
        or signing_key != signing_key.strip()
        or len(set(signing_key)) < 8
        or any(marker in normalized for marker in ("change_me", "replace_me", "placeholder", "example"))
    ):
        return None
    return encoded


def has_governed_egress_signing_key(signing_key: object) -> bool:
    """Return whether the dedicated proof key is strong enough to authenticate evidence."""
    return _valid_signing_key(signing_key) is not None


def _valid_governed_egress_key_id(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", normalized) else None


def governed_egress_previous_signing_keys(value: object) -> dict[str, str]:
    """Decode a bounded read-only previous-key map without projecting its secrets."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    if not isinstance(value, Mapping) or len(value) > GOVERNED_EGRESS_PROOF_MAX_PREVIOUS_KEYS:
        return {}
    keys: dict[str, str] = {}
    for key_id, key in value.items():
        normalized_id = _valid_governed_egress_key_id(key_id)
        if normalized_id is None or _valid_signing_key(key) is None:
            return {}
        keys[normalized_id] = str(key)
    return keys


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _canonical_proof_payload(proof: Mapping[str, object]) -> bytes:
    unsigned = {key: value for key, value in proof.items() if key != "signature"}
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _proof_signature(proof: Mapping[str, object], signing_key: bytes) -> str:
    return hmac.new(signing_key, _canonical_proof_payload(proof), hashlib.sha256).hexdigest()


def build_governed_egress_proof(
    *,
    signing_key: object,
    provider: str,
    runtime_subject: object,
    policy_subject: object,
    callback_subject: object,
    denial_subject: object,
    network_id: object,
    network_name: object,
    network_internal: object,
    tenant_id: object,
    workspace_id: object,
    user_id: object,
    session_id: object,
    run_id: object,
    image_subject: object,
    image_digest: object,
    authorized_skill_scope: object,
    authorized_native_tool_scope: object,
    lease_identity: object,
    key_id: object = GOVERNED_EGRESS_PROOF_DEFAULT_KEY_ID,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> dict[str, object]:
    """Seal one provider-neutral, redacted, expiry-bounded egress admission proof."""
    key = _valid_signing_key(signing_key)
    normalized_key_id = _valid_governed_egress_key_id(key_id)
    if (
        provider not in REAL_SANDBOX_PROVIDERS
        or key is None
        or normalized_key_id is None
        or not isinstance(network_internal, bool)
    ):
        raise ValueError("governed_egress_proof_invalid")
    issued = (issued_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expiry = (expires_at or issued + timedelta(seconds=GOVERNED_EGRESS_PROOF_MAX_TTL_SECONDS)).astimezone(timezone.utc)
    if expiry <= issued or expiry - issued > timedelta(seconds=GOVERNED_EGRESS_PROOF_MAX_TTL_SECONDS):
        raise ValueError("governed_egress_proof_expiry_invalid")
    subjects = {
        "runtime_subject": runtime_subject,
        "policy_subject": policy_subject,
        "callback_subject": callback_subject,
        "denial_subject": denial_subject,
        "network_id": network_id,
        "network_name": network_name,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "session_id": session_id,
        "run_id": run_id,
        "image_subject": image_subject,
        "image_digest": image_digest,
        "authorized_skill_scope": authorized_skill_scope,
        "authorized_native_tool_scope": authorized_native_tool_scope,
        "lease_identity": lease_identity,
    }
    proof: dict[str, object] = {
        "schema_version": GOVERNED_EGRESS_PROOF_SCHEMA,
        "key_id": normalized_key_id,
        "provider": provider,
        "source": REAL_SANDBOX_EVIDENCE_SOURCE,
        "evidence_class": REAL_SANDBOX_EVIDENCE_CLASS,
        "issued_at": _format_timestamp(issued),
        "expires_at": _format_timestamp(expiry),
        "default_deny_outbound": True,
        "governed_callback_exception": True,
        "policy_bound_enforcement": True,
        "network_internal": network_internal,
        **{f"{name}_sha256": _governed_egress_subject_digest(value) for name, value in subjects.items()},
    }
    proof["signature"] = _proof_signature(proof, key)
    return proof


def _proof_matches_expected_binding(proof: Mapping[str, object], expected_binding: Mapping[str, object]) -> bool:
    for field, value in expected_binding.items():
        if field not in _GOVERNED_EGRESS_SUBJECT_FIELDS:
            return False
        try:
            expected_digest = _governed_egress_subject_digest(value)
        except ValueError:
            return False
        if not hmac.compare_digest(str(proof.get(f"{field}_sha256") or ""), expected_digest):
            return False
    return True


def is_governed_egress_proof(
    proof: object,
    *,
    provider: str,
    signing_key: object,
    signing_key_id: object = GOVERNED_EGRESS_PROOF_DEFAULT_KEY_ID,
    previous_signing_keys: Mapping[str, str] | None = None,
    allow_previous_keys: bool = False,
    expected_binding: Mapping[str, object] | None = None,
    now: datetime | None = None,
    require_fresh: bool = True,
) -> bool:
    """Verify a sealed proof, optionally requiring it remains admissible now.

    ``require_fresh=False`` is for signed terminal audit evidence only. Runtime
    acquisition, reuse, dispatch, and active-lease admission must retain the
    default fresh mode and therefore reject an expired proof.
    """
    current_key = _valid_signing_key(signing_key)
    current_key_id = _valid_governed_egress_key_id(signing_key_id)
    if provider not in REAL_SANDBOX_PROVIDERS or current_key is None or current_key_id is None or not isinstance(proof, dict):
        return False
    if set(proof) != _GOVERNED_EGRESS_PROOF_KEYS:
        return False
    if (
        proof.get("schema_version") != GOVERNED_EGRESS_PROOF_SCHEMA
        or not isinstance(proof.get("key_id"), str)
        or proof.get("provider") != provider
        or proof.get("source") != REAL_SANDBOX_EVIDENCE_SOURCE
        or proof.get("evidence_class") != REAL_SANDBOX_EVIDENCE_CLASS
        or proof.get("default_deny_outbound") is not True
        or proof.get("governed_callback_exception") is not True
        or proof.get("policy_bound_enforcement") is not True
        or proof.get("network_internal") is not (provider == "docker")
    ):
        return False
    issued_at = _parse_timestamp(proof.get("issued_at"))
    expires_at = _parse_timestamp(proof.get("expires_at"))
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if (
        issued_at is None
        or expires_at is None
        or expires_at <= issued_at
        or expires_at - issued_at > timedelta(seconds=GOVERNED_EGRESS_PROOF_MAX_TTL_SECONDS)
        or (require_fresh and expires_at <= current_time)
        or issued_at > current_time + timedelta(seconds=30)
    ):
        return False
    for field in _GOVERNED_EGRESS_SUBJECT_FIELDS:
        digest = proof.get(f"{field}_sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            return False
    signature = proof.get("signature")
    if not isinstance(signature, str) or len(signature) != 64 or any(char not in "0123456789abcdef" for char in signature):
        return False
    proof_key_id = _valid_governed_egress_key_id(proof.get("key_id"))
    verification_key = current_key if proof_key_id == current_key_id else None
    if verification_key is None and allow_previous_keys and proof_key_id is not None:
        verification_key = _valid_signing_key((previous_signing_keys or {}).get(proof_key_id))
    if verification_key is None or not hmac.compare_digest(signature, _proof_signature(proof, verification_key)):
        return False
    return expected_binding is None or _proof_matches_expected_binding(proof, expected_binding)


def governed_egress_proof_label(proof: object) -> str:
    """Encode an already sealed proof without writing its secret or source subjects."""
    if not isinstance(proof, dict) or set(proof) != _GOVERNED_EGRESS_PROOF_KEYS:
        raise ValueError("governed_egress_proof_invalid")
    return json.dumps(proof, sort_keys=True, separators=(",", ":"))


def governed_egress_proof_from_labels(
    provider: str,
    labels: object,
    *,
    signing_key: object,
    signing_key_id: object = GOVERNED_EGRESS_PROOF_DEFAULT_KEY_ID,
    previous_signing_keys: Mapping[str, str] | None = None,
    allow_previous_keys: bool = False,
    expected_binding: Mapping[str, object] | None = None,
    now: datetime | None = None,
) -> dict[str, object] | None:
    """Recover only a currently valid sealed provider proof from runtime metadata."""
    if provider not in REAL_SANDBOX_PROVIDERS or not isinstance(labels, dict):
        return None
    encoded = labels.get(GOVERNED_EGRESS_PROOF_LABEL)
    if not isinstance(encoded, str) or len(encoded) > 8192:
        return None
    try:
        proof = json.loads(encoded)
    except (TypeError, ValueError):
        return None
    return (
        proof
        if is_governed_egress_proof(
            proof,
            provider=provider,
            signing_key=signing_key,
            signing_key_id=signing_key_id,
            previous_signing_keys=previous_signing_keys,
            allow_previous_keys=allow_previous_keys,
            expected_binding=expected_binding,
            now=now,
        )
        else None
    )


def _runtime_lease_expected_binding(row: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, object] | None:
    labels = payload.get("labels")
    container_id = payload.get("container_id")
    container_name = payload.get("container_name")
    if not isinstance(labels, dict) or not all(
        isinstance(value, str) and value for value in (container_id, container_name)
    ):
        return None
    provider = str(row.get("provider") or "")
    lease_identity = f"{provider}:{container_name}:{container_id}"
    return {
        "tenant_id": row.get("tenant_id"),
        "workspace_id": row.get("workspace_id"),
        "user_id": row.get("user_id"),
        "session_id": row.get("session_id"),
        "run_id": row.get("run_id"),
        "lease_identity": lease_identity,
    }


def _payload_matches_signed_projection(payload: Mapping[str, Any], proof: Mapping[str, object]) -> bool:
    for proof_field in (
        "image_subject_sha256",
        "image_digest_sha256",
        "authorized_skill_scope_sha256",
        "authorized_native_tool_scope_sha256",
    ):
        projected = payload.get(f"governed_egress_{proof_field}")
        signed = proof.get(proof_field)
        if (
            not isinstance(projected, str)
            or len(projected) != 64
            or any(char not in "0123456789abcdef" for char in projected)
            or not isinstance(signed, str)
            or not hmac.compare_digest(projected, signed)
        ):
            return False
    return True


def is_accepted_runtime_lease(
    row: dict[str, Any],
    *,
    signing_key: object | None = None,
    signing_key_id: object | None = None,
    previous_signing_keys: Mapping[str, str] | None = None,
    now: datetime | None = None,
    verification_mode: str = "active",
) -> bool:
    """Verify active admission or signed historical evidence for one lease.

    ``active`` is the only mode for runtime admission and requires a proof that
    has not expired. ``historical`` is limited to terminal audit projections:
    it still requires exact shape, signature, and scope binding, but permits a
    naturally expired proof without extending its runtime authority.
    """
    if verification_mode not in {"active", "historical"}:
        return False
    if verification_mode == "historical" and str(row.get("status") or "") not in {"released", "expired"}:
        return False
    provider = str(row.get("provider") or "")
    payload = row.get("lease_payload_json")
    if not isinstance(payload, dict):
        payload = row.get("lease_payload")
    expected_binding = _runtime_lease_expected_binding(row, payload) if isinstance(payload, dict) else None
    settings = get_settings()
    key = signing_key if signing_key is not None else settings.sandbox_egress_proof_signing_key
    current_key_id = (
        signing_key_id
        if signing_key_id is not None
        else getattr(settings, "sandbox_egress_proof_key_id", GOVERNED_EGRESS_PROOF_DEFAULT_KEY_ID)
    )
    historical_keys = (
        previous_signing_keys
        if previous_signing_keys is not None
        else governed_egress_previous_signing_keys(
            getattr(settings, "sandbox_egress_proof_previous_keys_json", "")
        )
    )
    proof = payload.get("governed_egress_proof") if isinstance(payload, dict) else None
    return (
        isinstance(payload, dict)
        and provider in REAL_SANDBOX_PROVIDERS
        and str(payload.get("source") or "") == REAL_SANDBOX_EVIDENCE_SOURCE
        and str(payload.get("evidence_class") or "") == REAL_SANDBOX_EVIDENCE_CLASS
        and expected_binding is not None
        and is_governed_egress_proof(
            proof,
            provider=provider,
            signing_key=key,
            signing_key_id=current_key_id,
            previous_signing_keys=historical_keys if verification_mode == "historical" else None,
            allow_previous_keys=verification_mode == "historical",
            expected_binding=expected_binding,
            now=now,
            require_fresh=verification_mode == "active",
        )
        and isinstance(proof, dict)
        and _payload_matches_signed_projection(payload, proof)
    )
