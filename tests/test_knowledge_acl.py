from app.knowledge.domain import KnowledgeAcl


def test_enterprise_source_contains_every_agent_scope() -> None:
    source = KnowledgeAcl.create(visibility="enterprise")
    restricted_agent = KnowledgeAcl.create(
        visibility="restricted",
        department_ids=["dept-finance"],
    )

    assert source.contains(restricted_agent)
    assert source.contains(KnowledgeAcl.create(visibility="tenant"))


def test_restricted_source_cannot_back_a_company_wide_agent() -> None:
    source = KnowledgeAcl.create(
        visibility="restricted",
        department_ids=["dept-finance"],
    )

    assert not source.contains(KnowledgeAcl.create(visibility="tenant"))


def test_restricted_source_contains_only_narrower_department_role_and_user_scope() -> None:
    source = KnowledgeAcl.create(
        visibility="restricted",
        department_ids=["dept-finance", "dept-legal"],
        roles=["Reviewer", "Operator"],
        user_ids=["user-a", "user-b"],
    )

    assert source.contains(
        KnowledgeAcl.create(
            visibility="restricted",
            department_ids=["dept-finance"],
            roles=["reviewer"],
            user_ids=["user-a"],
        )
    )
    assert not source.contains(
        KnowledgeAcl.create(
            visibility="restricted",
            department_ids=["dept-engineering"],
            roles=["reviewer"],
            user_ids=["user-a"],
        )
    )
    assert not source.contains(
        KnowledgeAcl.create(
            visibility="restricted",
            department_ids=["dept-finance"],
            roles=["reviewer"],
            user_ids=["user-c"],
        )
    )


def test_restricted_acl_evaluation_matches_department_role_and_explicit_user_rules() -> None:
    acl = KnowledgeAcl.create(
        visibility="restricted",
        department_ids=["dept-finance"],
        roles=["reviewer"],
        user_ids=["user-exception"],
    )

    assert acl.allows(
        user_id="user-a",
        department_id="dept-finance",
        roles=["Reviewer"],
    )
    assert not acl.allows(
        user_id="user-a",
        department_id="dept-finance",
        roles=["operator"],
    )
    assert acl.allows(
        user_id="user-exception",
        department_id="dept-engineering",
        roles=[],
    )
