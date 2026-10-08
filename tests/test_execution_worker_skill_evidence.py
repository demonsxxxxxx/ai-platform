from types import SimpleNamespace

from app.agent_apps.capability_state import exact_invoked_skills
from app.execution.api import skill_manifests_for_persistence, skill_snapshot_from_result


def test_skill_evidence_requires_exact_native_hook_and_admitted_manifest():
    result = SimpleNamespace(
        result={"used_skills": ["admitted", "invented"]},
        executor_payload={
            "allowed_skills": ["admitted"],
            "staged_skills": ["admitted"],
            "used_skills": ["admitted", "invented"],
            "used_skills_source": "executor_hook",
            "skill_manifests": [
                {"skill_id": "admitted", "version": "injected", "source": {"kind": "untrusted"}},
                {"skill_id": "invented", "version": "injected"},
            ],
        },
    )
    admitted = [{"skill_id": "admitted", "version": "locked-version", "content_hash": "locked-hash"}]

    assert skill_snapshot_from_result(result, invoked_skill_ids=exact_invoked_skills)["used_skills"] == ["admitted"]
    persisted = skill_manifests_for_persistence(result, admitted, invoked_skill_ids=exact_invoked_skills)
    assert [manifest["skill_id"] for manifest in persisted] == ["admitted"]
    assert persisted[0]["version"] == "locked-version"
    assert persisted[0]["content_hash"] == "locked-hash"


def test_untrusted_usage_source_cannot_claim_invocation():
    result = SimpleNamespace(
        result={"used_skills": ["skill"]},
        executor_payload={"staged_skills": ["skill"], "used_skills_source": "inferred"},
    )

    assert skill_snapshot_from_result(result, invoked_skill_ids=exact_invoked_skills)["used_skills"] == []
