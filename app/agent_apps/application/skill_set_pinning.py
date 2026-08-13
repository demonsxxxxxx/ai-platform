from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any


async def pin_agent_skill_set(
    skills: Sequence[dict[str, Any]],
    *,
    manifest_scope: Any,
    input_payload: dict[str, Any],
    tenant_id: str,
    rollout_key: str,
    resolve_release_decision: Callable[..., Any],
    governed_manifest_pins: Callable[..., Awaitable[list[dict[str, Any]]]],
    locked_skill_version: Callable[..., str],
    decision_payload_for_version: Callable[..., dict[str, Any]],
    attach_snapshot_governance: Callable[..., list[dict[str, Any]]],
    pin_mcp_tool_ids: Callable[..., list[dict[str, Any]]],
    mcp_tool_ids_for_skill: Callable[..., list[str]],
    conflict_error: Callable[[str], Exception],
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Lock an immutable Agent Skill Set while preserving its legacy primary identity."""

    manifests_by_id: dict[str, dict[str, Any]] = {}
    primary_version = ""
    primary_decision: dict[str, Any] = {}
    for index, skill in enumerate(skills):
        skill_id = str(skill.get("skill_id") or "")
        expected_version = str(skill.get("skill_version") or "")
        if not skill_id or not expected_version:
            raise conflict_error("agent_profile_skill_set_invalid")
        decision = resolve_release_decision(
            skill, tenant_id=tenant_id, skill_id=skill_id, rollout_key=rollout_key
        )
        selected_version = decision.selected_version
        policy_version = selected_version if decision.policy_active else None
        manifests = await governed_manifest_pins(
            manifest_scope,
            skill_id=skill_id,
            input_payload=input_payload,
            release_policy_version=policy_version,
        )
        version = locked_skill_version(
            skill_id=skill_id,
            skill_manifests=manifests,
            fallback_version=selected_version,
            release_policy_version=policy_version,
        )
        if version != expected_version:
            raise conflict_error("agent_profile_skill_set_stale")
        decision_payload = decision_payload_for_version(decision, locked_version=version)
        manifests = attach_snapshot_governance(manifests, release_decision=decision_payload)
        manifests = [
            {**manifest, "release_decision": dict(decision_payload)}
            for manifest in manifests
        ]
        manifests = pin_mcp_tool_ids(
            manifests,
            skill_id=skill_id,
            mcp_tool_ids=mcp_tool_ids_for_skill(skill, input_payload),
        )
        for manifest in manifests:
            manifest_id = str(manifest.get("skill_id") or "")
            existing = manifests_by_id.get(manifest_id)
            if existing is not None and str(existing.get("content_hash") or "") != str(
                manifest.get("content_hash") or ""
            ):
                raise conflict_error("agent_profile_skill_set_conflict")
            if manifest_id == skill_id:
                manifests_by_id[manifest_id] = manifest
            else:
                manifests_by_id.setdefault(manifest_id, manifest)
        if index == 0:
            primary_version = version
            primary_decision = decision_payload
    if not manifests_by_id or not primary_version or not primary_decision:
        raise conflict_error("agent_profile_skill_set_invalid")
    return list(manifests_by_id.values()), primary_version, primary_decision
