from app.skills.release_policy import resolve_rollout_skill_decision, resolve_rollout_skill_version, skill_rollout_bucket


def test_skill_rollout_bucket_is_stable_and_bounded():
    first = skill_rollout_bucket(tenant_id="tenant-a", skill_id="qa-file-reviewer", rollout_key="user-a")
    second = skill_rollout_bucket(tenant_id="tenant-a", skill_id="qa-file-reviewer", rollout_key="user-a")

    assert first == second
    assert 0 <= first <= 99


def test_resolve_rollout_skill_version_uses_current_for_full_rollout():
    skill = {
        "skill_version": "hash-new",
        "release_policy_version": "hash-new",
        "release_policy_previous_version": "hash-old",
        "release_policy_rollout_percent": 100,
    }

    assert (
        resolve_rollout_skill_version(
            skill,
            tenant_id="tenant-a",
            skill_id="qa-file-reviewer",
            rollout_key="user-a",
        )
        == "hash-new"
    )


def test_resolve_rollout_skill_version_uses_previous_for_zero_rollout():
    skill = {
        "skill_version": "hash-new",
        "release_policy_version": "hash-new",
        "release_policy_previous_version": "hash-old",
        "release_policy_rollout_percent": 0,
    }

    assert (
        resolve_rollout_skill_version(
            skill,
            tenant_id="tenant-a",
            skill_id="qa-file-reviewer",
            rollout_key="user-a",
        )
        == "hash-old"
    )


def test_resolve_rollout_skill_version_keeps_fallback_without_policy():
    skill = {"skill_version": "0.1.0", "release_policy_version": None}

    assert (
        resolve_rollout_skill_version(
            skill,
            tenant_id="tenant-a",
            skill_id="general-chat",
            rollout_key="user-a",
        )
        == "0.1.0"
    )


def test_resolve_rollout_skill_version_splits_stable_mid_percent_cohorts():
    skill = {
        "skill_version": "hash-new",
        "release_policy_version": "hash-new",
        "release_policy_previous_version": "hash-old",
        "release_policy_rollout_percent": 50,
    }
    current_user = next(
        f"user-{index}"
        for index in range(200)
        if skill_rollout_bucket(tenant_id="tenant-a", skill_id="qa-file-reviewer", rollout_key=f"user-{index}") < 50
    )
    previous_user = next(
        f"user-{index}"
        for index in range(200)
        if skill_rollout_bucket(tenant_id="tenant-a", skill_id="qa-file-reviewer", rollout_key=f"user-{index}") >= 50
    )

    assert (
        resolve_rollout_skill_version(
            skill,
            tenant_id="tenant-a",
            skill_id="qa-file-reviewer",
            rollout_key=current_user,
        )
        == "hash-new"
    )
    assert (
        resolve_rollout_skill_version(
            skill,
            tenant_id="tenant-a",
            skill_id="qa-file-reviewer",
            rollout_key=previous_user,
        )
        == "hash-old"
    )


def test_resolve_rollout_skill_decision_records_mid_rollout_cohort_context():
    skill = {
        "skill_version": "hash-new",
        "release_policy_version": "hash-new",
        "release_policy_previous_version": "hash-old",
        "release_policy_rollout_percent": 50,
    }
    current_user = next(
        f"user-{index}"
        for index in range(200)
        if skill_rollout_bucket(tenant_id="tenant-a", skill_id="qa-file-reviewer", rollout_key=f"user-{index}") < 50
    )
    bucket = skill_rollout_bucket(tenant_id="tenant-a", skill_id="qa-file-reviewer", rollout_key=current_user)

    decision = resolve_rollout_skill_decision(
        skill,
        tenant_id="tenant-a",
        skill_id="qa-file-reviewer",
        rollout_key=current_user,
    )

    assert decision.selected_version == "hash-new"
    assert decision.to_payload() == {
        "schema_version": "ai-platform.skill-release-decision.v1",
        "policy_active": True,
        "channel": "stable",
        "selected_version": "hash-new",
        "selected_track": "current",
        "fallback_version": "hash-new",
        "current_version": "hash-new",
        "previous_version": "hash-old",
        "rollout_percent": 50,
        "bucket": bucket,
        "cohort_basis": "tenant_id:skill_id:user_id",
    }


def test_resolve_rollout_skill_decision_records_catalog_fallback_without_policy():
    decision = resolve_rollout_skill_decision(
        {"skill_version": "0.1.0", "release_policy_version": None},
        tenant_id="tenant-a",
        skill_id="general-chat",
        rollout_key="user-a",
    )

    assert decision.selected_version == "0.1.0"
    assert decision.to_payload()["policy_active"] is False
    assert decision.to_payload()["selected_track"] == "catalog"
    assert decision.to_payload()["bucket"] is None


def test_resolve_rollout_skill_version_uses_current_for_gray_policy_without_previous():
    skill = {
        "skill_version": "hash-new",
        "release_policy_version": "hash-new",
        "release_policy_previous_version": None,
        "release_policy_rollout_percent": 50,
    }

    assert (
        resolve_rollout_skill_version(
            skill,
            tenant_id="tenant-a",
            skill_id="qa-file-reviewer",
            rollout_key="user-a",
        )
        == "hash-new"
    )


def test_resolve_rollout_skill_version_clamps_or_defaults_invalid_percent_values():
    base_skill = {
        "skill_version": "hash-new",
        "release_policy_version": "hash-new",
        "release_policy_previous_version": "hash-old",
    }

    assert (
        resolve_rollout_skill_version(
            {**base_skill, "release_policy_rollout_percent": -1},
            tenant_id="tenant-a",
            skill_id="qa-file-reviewer",
            rollout_key="user-a",
        )
        == "hash-old"
    )
    assert (
        resolve_rollout_skill_version(
            {**base_skill, "release_policy_rollout_percent": 101},
            tenant_id="tenant-a",
            skill_id="qa-file-reviewer",
            rollout_key="user-a",
        )
        == "hash-new"
    )
    assert (
        resolve_rollout_skill_version(
            {**base_skill, "release_policy_rollout_percent": "not-a-number"},
            tenant_id="tenant-a",
            skill_id="qa-file-reviewer",
            rollout_key="user-a",
        )
        == "hash-new"
    )
