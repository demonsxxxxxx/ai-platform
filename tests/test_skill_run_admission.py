from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.skills.application.run_admission import (
    SkillRunAdmissionPorts,
    SkillRunAdmissionService,
    SkillRunVersionMismatch,
)
from app.skills.catalog import is_current_skill_dependency_usable
from app.skills.lifecycle import is_user_runnable_status
from app.skills.pinning import (
    SkillVersionMaterializationError,
    attach_skill_snapshot_governance,
    build_skill_version_policy_manifest_pins,
    governed_locked_skill_version,
)
from app.skills.release_policy import (
    release_decision_payload_for_locked_version,
    resolve_rollout_skill_decision,
)


@pytest.mark.asyncio
async def test_admission_selects_rollout_version_and_locks_matching_manifest():
    requested_versions = []
    skill_id = "qa-file-reviewer"
    files = [
        {"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}
    ]

    async def get_effective_skill_version(_conn, *, skill_id, version):
        requested_versions.append((skill_id, version))
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "description": "Review Word documents.",
            "source": {"kind": "builtin", "asset_dir": skill_id, "files": files},
            "dependency_ids": [],
            "status": "active",
        }

    service = SkillRunAdmissionService(
        SkillRunAdmissionPorts(
            catalog=SimpleNamespace(list_public_skill_catalog=lambda *_args, **_kwargs: []),
            versions=SimpleNamespace(
                get_effective_skill_version_for_policy=get_effective_skill_version
            ),
            resolve_release_decision=resolve_rollout_skill_decision,
            release_decision_payload=release_decision_payload_for_locked_version,
            is_user_runnable_status=is_user_runnable_status,
            dependency_is_usable=is_current_skill_dependency_usable,
            build_manifest_pins=build_skill_version_policy_manifest_pins,
            lock_skill_version=governed_locked_skill_version,
            attach_snapshot_governance=attach_skill_snapshot_governance,
            materialization_error=SkillVersionMaterializationError,
        )
    )
    admission = await service.admit(
        object(),
        skill={
            "skill_version": "hash-current",
            "release_policy_version": "hash-current",
            "release_policy_previous_version": "hash-previous",
            "release_policy_rollout_percent": 0,
        },
        skill_id=skill_id,
        input_payload={},
        tenant_id="tenant-a",
        rollout_key="user-a",
    )

    assert requested_versions == [(skill_id, "hash-previous")]
    assert admission.skill_version == "hash-previous"
    assert admission.release_decision["selected_track"] == "previous"
    assert admission.release_decision["selected_version"] == "hash-previous"
    assert len(admission.skill_manifests) == 1
    assert admission.skill_manifests[0]["version"] == "hash-previous"
    assert admission.skill_manifests[0]["content_hash"] == "hash-previous"
    assert admission.skill_manifests[0]["files"] == files
    assert "mcp_tool_ids" not in admission.skill_manifests[0]

    def fail_governance(*_args, **_kwargs):
        raise AssertionError("stale Skill version must fail before snapshot governance")

    stale_service = SkillRunAdmissionService(
        replace(
            service._ports,
            attach_snapshot_governance=fail_governance,
        )
    )
    with pytest.raises(SkillRunVersionMismatch):
        await stale_service.admit(
            object(),
            skill={
                "skill_version": "hash-current",
                "release_policy_version": "hash-current",
            },
            skill_id=skill_id,
            input_payload={},
            tenant_id="tenant-a",
            rollout_key="user-a",
            expected_version="hash-previous",
        )


def _skill_version(skill_id, version, dependency_ids=(), *, dependency_manifests=None):
    source = {
        "kind": "builtin",
        "asset_dir": skill_id,
        "files": [
            {"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}
        ],
    }
    if dependency_manifests is not None:
        source["dependency_manifests"] = dependency_manifests
    return {
        "skill_id": skill_id,
        "version": version,
        "content_hash": version,
        "description": f"{skill_id} description",
        "source": source,
        "dependency_ids": list(dependency_ids),
        "status": "active",
    }


def _catalog_row(version):
    return {
        "skill_id": version["skill_id"],
        "name": version["skill_id"],
        "version": version["version"],
        "expected_version": version["content_hash"],
        "description": version["description"],
        "source": version["source"],
        "dependency_ids": list(version["dependency_ids"]),
        "lifecycle_status": "active",
        "version_status": version["status"],
        "status": "active",
        "visible_to_user": True,
        "department_ids": [],
        "allowed_roles": [],
    }


def _current_dependency_service(version_rows):
    catalog_rows = {
        skill_id: _catalog_row(version)
        for (skill_id, _version), version in version_rows.items()
        if skill_id != "root"
    }
    catalog_queries = []
    version_lookups = []
    manifest_builds = []
    governance_calls = []

    async def list_public_skill_catalog(
        _conn,
        *,
        tenant_id,
        include_disabled,
        rollout_key,
        skill_ids,
    ):
        catalog_queries.append(tuple(skill_ids))
        assert tenant_id == "tenant-a"
        assert include_disabled is True
        assert rollout_key == "user-a"
        return [catalog_rows[item] for item in skill_ids if item in catalog_rows]

    async def get_effective_skill_version(_conn, *, skill_id, version):
        version_lookups.append((skill_id, version))
        return version_rows.get((skill_id, version))

    def build_manifest(version, *, available_skill_ids):
        manifest_builds.append(version["skill_id"])
        return build_skill_version_policy_manifest_pins(
            version,
            available_skill_ids=available_skill_ids,
        )

    def attach_governance(manifests, *, release_decision=None):
        governance_calls.append(tuple(item["skill_id"] for item in manifests))
        return attach_skill_snapshot_governance(
            manifests,
            release_decision=release_decision,
        )

    service = SkillRunAdmissionService(
        SkillRunAdmissionPorts(
            catalog=SimpleNamespace(list_public_skill_catalog=list_public_skill_catalog),
            versions=SimpleNamespace(
                get_effective_skill_version_for_policy=get_effective_skill_version
            ),
            resolve_release_decision=resolve_rollout_skill_decision,
            release_decision_payload=release_decision_payload_for_locked_version,
            is_user_runnable_status=is_user_runnable_status,
            dependency_is_usable=is_current_skill_dependency_usable,
            build_manifest_pins=build_manifest,
            lock_skill_version=governed_locked_skill_version,
            attach_snapshot_governance=attach_governance,
            materialization_error=SkillVersionMaterializationError,
        )
    )
    evidence = {
        "catalog_queries": catalog_queries,
        "version_lookups": version_lookups,
        "manifest_builds": manifest_builds,
        "governance_calls": governance_calls,
    }
    return service, evidence


def _root(skill_id, version):
    return {"skill_id": skill_id, "skill_version": version}


@pytest.mark.asyncio
async def test_admission_resolves_entire_dependency_chain_to_current_published_versions():
    stale_d1 = _skill_version("dependency-one", "d1-stored-old")
    stale_d2 = _skill_version("dependency-two", "d2-stored-old")
    root = _skill_version(
        "root",
        "root-v1",
        ["dependency-one"],
        dependency_manifests=[stale_d1],
    )
    dependency_one = _skill_version(
        "dependency-one",
        "d1-v2",
        ["dependency-two"],
        dependency_manifests=[stale_d2],
    )
    dependency_two = _skill_version("dependency-two", "d2-v3")
    service, evidence = _current_dependency_service(
        {
            ("root", "root-v1"): root,
            ("dependency-one", "d1-v2"): dependency_one,
            ("dependency-two", "d2-v3"): dependency_two,
        }
    )

    admission = await service.admit(
        object(),
        skill=_root("root", "root-v1"),
        skill_id="root",
        input_payload={},
        tenant_id="tenant-a",
        rollout_key="user-a",
    )

    assert [(item["skill_id"], item["version"]) for item in admission.skill_manifests] == [
        ("root", "root-v1"),
        ("dependency-one", "d1-v2"),
        ("dependency-two", "d2-v3"),
    ]
    assert evidence["version_lookups"] == [
        ("root", "root-v1"),
        ("dependency-one", "d1-v2"),
        ("dependency-two", "d2-v3"),
    ]
    assert evidence["catalog_queries"] == [("dependency-one",), ("dependency-two",)]
    assert all("dependency_manifests" not in item["source"] for item in admission.skill_manifests)


@pytest.mark.asyncio
async def test_admission_resolves_shared_dependency_once_for_multiple_authorized_roots():
    root_one = _skill_version("root-one", "root-one-v1", ["shared"])
    root_two = _skill_version("root-two", "root-two-v1", ["shared"])
    shared = _skill_version("shared", "shared-current")
    service, evidence = _current_dependency_service(
        {
            ("root-one", "root-one-v1"): root_one,
            ("root-two", "root-two-v1"): root_two,
            ("shared", "shared-current"): shared,
        }
    )

    admissions = await service.admit_set(
        object(),
        roots=[
            ("root-one", _root("root-one", "root-one-v1"), "root-one-v1"),
            ("root-two", _root("root-two", "root-two-v1"), "root-two-v1"),
        ],
        input_payload={},
        tenant_id="tenant-a",
        rollout_key="user-a",
    )

    assert evidence["catalog_queries"] == [("shared",)]
    assert evidence["version_lookups"].count(("shared", "shared-current")) == 1
    assert [
        [(item["skill_id"], item["version"]) for item in admission.skill_manifests]
        for admission in admissions
    ] == [
        [("root-one", "root-one-v1"), ("shared", "shared-current")],
        [("root-two", "root-two-v1"), ("shared", "shared-current")],
    ]


@pytest.mark.asyncio
async def test_admission_rejects_more_than_64_roots_and_dependencies_before_pinning():
    root = _skill_version("root", "root-v1", ["dependency-0"])
    rows = {("root", "root-v1"): root}
    for index in range(64):
        dependency_id = f"dependency-{index}"
        dependencies = [f"dependency-{index + 1}"] if index < 63 else []
        version = _skill_version(dependency_id, f"v{index}", dependencies)
        rows[(dependency_id, f"v{index}")] = version
    service, evidence = _current_dependency_service(rows)

    with pytest.raises(SkillVersionMaterializationError, match="skill_version_not_materializable"):
        await service.admit(
            object(),
            skill=_root("root", "root-v1"),
            skill_id="root",
            input_payload={},
            tenant_id="tenant-a",
            rollout_key="user-a",
        )

    assert evidence["manifest_builds"] == []
    assert evidence["governance_calls"] == []


@pytest.mark.asyncio
async def test_admission_rejects_more_than_64_roots_before_pinning():
    roots = []
    versions = {}
    for index in range(65):
        skill_id = f"root-{index}"
        version = _skill_version(skill_id, f"v{index}")
        roots.append((skill_id, _root(skill_id, f"v{index}"), f"v{index}"))
        versions[(skill_id, f"v{index}")] = version
    service, evidence = _current_dependency_service(versions)

    with pytest.raises(SkillVersionMaterializationError, match="skill_version_not_materializable"):
        await service.admit_set(
            object(),
            roots=roots,
            input_payload={},
            tenant_id="tenant-a",
            rollout_key="user-a",
        )

    assert evidence["version_lookups"] == []
    assert evidence["manifest_builds"] == []
    assert evidence["governance_calls"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "cycle", "scoped_dependency"])
async def test_admission_rejects_unavailable_or_cyclic_current_dependencies(case):
    root = _skill_version(
        "root",
        "root-v1",
        ["dependency-one"],
        dependency_manifests=(
            [_skill_version("dependency-one", "stored-old")] if case == "missing" else None
        ),
    )
    versions = {("root", "root-v1"): root}
    if case == "cycle":
        dependency_one = _skill_version("dependency-one", "d1", ["dependency-two"])
        dependency_two = _skill_version("dependency-two", "d2", ["dependency-one"])
        versions.update({("dependency-one", "d1"): dependency_one, ("dependency-two", "d2"): dependency_two})
    elif case == "scoped_dependency":
        versions[("dependency-one", "d1")] = _skill_version("dependency-one", "d1")
    service, _evidence = _current_dependency_service(versions)
    if case == "scoped_dependency":
        row = _catalog_row(versions[("dependency-one", "d1")])
        row["department_ids"] = ["qa"]
        service._ports.catalog.list_public_skill_catalog = None

        async def list_scoped_dependency(*_args, **_kwargs):
            return [row]

        service._ports.catalog.list_public_skill_catalog = list_scoped_dependency

    with pytest.raises(SkillVersionMaterializationError, match="skill_version_not_materializable"):
        await service.admit(
            object(),
            skill=_root("root", "root-v1"),
            skill_id="root",
            input_payload={},
            tenant_id="tenant-a",
            rollout_key="user-a",
        )
