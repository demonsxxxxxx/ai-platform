from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from app.skills.api import (
    SkillRunVersionMismatch,
    admit_skill_run,
    pin_skill_run_mcp_tools,
)


async def pin_agent_skill_set(
    conn: Any,
    skills: Sequence[dict[str, Any]],
    *,
    input_payload: dict[str, Any],
    tenant_id: str,
    rollout_key: str,
    mcp_tool_ids_for_skill: Callable[[dict[str, Any], dict[str, Any]], list[str]],
    conflict_error: Callable[[str], Exception],
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Admit each Skill in an Agent Profile and preserve its primary identity."""

    manifests_by_id: dict[str, dict[str, Any]] = {}
    primary_version = ""
    primary_decision: dict[str, Any] = {}
    for index, skill in enumerate(skills):
        skill_id = str(skill.get("skill_id") or "")
        expected_version = str(skill.get("skill_version") or "")
        if not skill_id or not expected_version:
            raise conflict_error("agent_profile_skill_set_invalid")

        try:
            admission = await admit_skill_run(
                conn,
                skill=skill,
                skill_id=skill_id,
                input_payload=input_payload,
                tenant_id=tenant_id,
                rollout_key=rollout_key,
                expected_version=expected_version,
            )
        except SkillRunVersionMismatch as exc:
            raise conflict_error("agent_profile_skill_set_stale") from exc

        pinned_manifests = pin_skill_run_mcp_tools(
            admission.skill_manifests,
            skill_id=skill_id,
            mcp_tool_ids=mcp_tool_ids_for_skill(skill, input_payload),
        )

        for manifest in pinned_manifests:
            manifest = {
                **manifest,
                "release_decision": dict(admission.release_decision),
            }
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
            primary_version = admission.skill_version
            primary_decision = admission.release_decision

    if not manifests_by_id or not primary_version:
        raise conflict_error("agent_profile_skill_set_invalid")
    return list(manifests_by_id.values()), primary_version, primary_decision
