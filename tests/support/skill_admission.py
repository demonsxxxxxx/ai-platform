"""Build public Skill admission results for route isolation tests."""

def admitted_skill(skill, tenant_id, rollout_key, manifests):
    from app.skills.application.run_admission import SkillRunAdmission
    from app.skills.pinning import attach_skill_snapshot_governance, governed_locked_skill_version
    from app.skills.release_policy import resolve_rollout_skill_decision, release_decision_payload_for_locked_version
    decision = resolve_rollout_skill_decision(skill, tenant_id=tenant_id, skill_id=manifests[0]["skill_id"], rollout_key=rollout_key)
    version = governed_locked_skill_version(skill_id=manifests[0]["skill_id"], skill_manifests=manifests, fallback_version=str(skill.get("skill_version") or ""), release_policy_version=decision.selected_version if decision.policy_active else None)
    release = release_decision_payload_for_locked_version(decision, locked_version=version)
    return SkillRunAdmission(attach_skill_snapshot_governance(manifests, release_decision=release), version, release)
