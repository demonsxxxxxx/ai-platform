import hashlib
from dataclasses import dataclass
from typing import Any

RELEASE_DECISION_SCHEMA_VERSION = "ai-platform.skill-release-decision.v1"


@dataclass(frozen=True)
class SkillReleaseDecision:
    selected_version: str
    policy_active: bool
    fallback_version: str
    current_version: str
    previous_version: str
    rollout_percent: int | None
    selected_track: str
    bucket: int | None
    channel: str = "stable"
    cohort_basis: str = "tenant_id:skill_id:user_id"

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": RELEASE_DECISION_SCHEMA_VERSION,
            "policy_active": self.policy_active,
            "channel": self.channel,
            "selected_version": self.selected_version,
            "selected_track": self.selected_track,
            "fallback_version": self.fallback_version,
            "current_version": self.current_version,
            "previous_version": self.previous_version,
            "rollout_percent": self.rollout_percent,
            "bucket": self.bucket,
            "cohort_basis": self.cohort_basis,
        }


def skill_rollout_bucket(*, tenant_id: str, skill_id: str, rollout_key: str) -> int:
    digest = hashlib.sha256(f"{tenant_id}:{skill_id}:{rollout_key}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100


def resolve_rollout_skill_decision(
    skill: dict[str, Any],
    *,
    tenant_id: str,
    skill_id: str,
    rollout_key: str,
) -> SkillReleaseDecision:
    fallback_version = str(skill.get("skill_version") or "")
    policy_version = str(skill.get("release_policy_version") or "")
    if not policy_version:
        return SkillReleaseDecision(
            selected_version=fallback_version,
            policy_active=False,
            fallback_version=fallback_version,
            current_version="",
            previous_version="",
            rollout_percent=None,
            selected_track="catalog",
            bucket=None,
        )

    previous_version = str(skill.get("release_policy_previous_version") or "")
    current_version = policy_version or fallback_version
    rollout_percent = _rollout_percent(skill.get("release_policy_rollout_percent"))
    if rollout_percent >= 100:
        return SkillReleaseDecision(
            selected_version=current_version,
            policy_active=True,
            fallback_version=fallback_version,
            current_version=current_version,
            previous_version=previous_version,
            rollout_percent=rollout_percent,
            selected_track="current",
            bucket=None,
        )
    if rollout_percent <= 0:
        selected_version = previous_version or current_version
        return SkillReleaseDecision(
            selected_version=selected_version,
            policy_active=True,
            fallback_version=fallback_version,
            current_version=current_version,
            previous_version=previous_version,
            rollout_percent=rollout_percent,
            selected_track="previous" if previous_version else "current",
            bucket=None,
        )
    if not previous_version:
        return SkillReleaseDecision(
            selected_version=current_version,
            policy_active=True,
            fallback_version=fallback_version,
            current_version=current_version,
            previous_version=previous_version,
            rollout_percent=rollout_percent,
            selected_track="current",
            bucket=None,
        )
    bucket = skill_rollout_bucket(tenant_id=tenant_id, skill_id=skill_id, rollout_key=rollout_key)
    selected_track = "current" if bucket < rollout_percent else "previous"
    return SkillReleaseDecision(
        selected_version=current_version if selected_track == "current" else previous_version,
        policy_active=True,
        fallback_version=fallback_version,
        current_version=current_version,
        previous_version=previous_version,
        rollout_percent=rollout_percent,
        selected_track=selected_track,
        bucket=bucket,
    )


def resolve_rollout_skill_version(
    skill: dict[str, Any],
    *,
    tenant_id: str,
    skill_id: str,
    rollout_key: str,
) -> str:
    return resolve_rollout_skill_decision(
        skill,
        tenant_id=tenant_id,
        skill_id=skill_id,
        rollout_key=rollout_key,
    ).selected_version


def release_decision_payload_for_locked_version(
    decision: SkillReleaseDecision | dict[str, Any],
    *,
    locked_version: str,
) -> dict[str, Any]:
    payload = decision.to_payload() if isinstance(decision, SkillReleaseDecision) else dict(decision or {})
    if not payload:
        return {}
    selected_version = str(payload.get("selected_version") or "")
    if locked_version and selected_version != locked_version and not bool(payload.get("policy_active")):
        payload["selected_version"] = locked_version
        payload["selected_track"] = "manifest_pin"
    return payload


def validate_release_decision_payload(release_decision: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(release_decision or {})
    if not payload:
        return {}
    if payload.get("schema_version") != RELEASE_DECISION_SCHEMA_VERSION:
        raise ValueError("release_decision_schema_version_invalid")
    if not str(payload.get("selected_version") or ""):
        raise ValueError("release_decision_selected_version_required")
    return payload


def validate_release_decision_lock(
    *,
    release_decision: dict[str, Any] | None,
    skill_version: str | None,
    skill_id: str,
    skill_manifests: list[dict[str, Any]] | None = None,
) -> None:
    payload = validate_release_decision_payload(release_decision)
    if not payload:
        raise ValueError("release_decision_required")

    locked_version = str(skill_version or "")
    if not locked_version:
        raise ValueError("release_decision_skill_version_required")
    selected_version = str(payload.get("selected_version") or "")
    if selected_version != locked_version:
        raise ValueError("release_decision_selected_version_mismatch")

    manifests = list(skill_manifests or [])
    seen_manifest_ids: set[str] = set()
    for manifest in manifests:
        if not isinstance(manifest, dict):
            continue
        manifest_skill_id = str(manifest.get("skill_id") or "").strip()
        if not manifest_skill_id:
            continue
        if manifest_skill_id in seen_manifest_ids:
            raise ValueError("release_decision_duplicate_skill_manifest")
        seen_manifest_ids.add(manifest_skill_id)
    normalized_skill_id = str(skill_id).strip()
    primary_manifest = next(
        (
            manifest
            for manifest in manifests
            if isinstance(manifest, dict) and str(manifest.get("skill_id") or "").strip() == normalized_skill_id
        ),
        None,
    )
    if primary_manifest is None:
        raise ValueError("release_decision_primary_manifest_missing")
    primary_version = str(primary_manifest.get("content_hash") or primary_manifest.get("version") or "")
    if primary_version != locked_version:
        raise ValueError("release_decision_primary_manifest_mismatch")


def _rollout_percent(value: object) -> int:
    try:
        percent = int(value if value is not None else 100)
    except (TypeError, ValueError):
        return 100
    return max(0, min(100, percent))
