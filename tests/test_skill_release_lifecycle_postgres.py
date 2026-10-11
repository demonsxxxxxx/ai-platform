"""Shared Skill release invariants and lifecycle contention on real PostgreSQL."""

import asyncio
import hashlib
import io
import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import zipfile

from fastapi import HTTPException
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app.auth import AuthPrincipal
from app.models import AdminSkillPromoteRequest, AdminSkillRollbackRequest, AdminSkillVersionStatusRequest
from app.routes import admin_skills
from app.skills import api as skill_api
from app.skills.application import skill_markdown
from app.skills.application.run_admission import SkillRunAdmissionPorts, SkillRunAdmissionService
from app.skills.catalog import is_current_skill_dependency_usable
from app.skills.infrastructure import catalog_postgres, postgres, versions_postgres
from app.skills.infrastructure.skill_markdown_yaml import load_skill_markdown_metadata
from app.skills.lifecycle import is_user_runnable_status
from app.skills.packages import build_skill_package_contract, parse_skill_package_zip
from app.skills.pinning import (
    SkillVersionMaterializationError, attach_skill_snapshot_governance,
    build_skill_version_policy_manifest_pins, governed_locked_skill_version,
)
from app.skills.release_policy import release_decision_payload_for_locked_version, resolve_rollout_skill_decision


SKILL_ID = "shared-review"


def _uploaded_version(label):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("SKILL.md", f"---\nname: {SKILL_ID}\ndescription: Shared review {label}\n---\n{label}\n")
    parsed = parse_skill_package_zip(content.getvalue())
    package_sha256 = hashlib.sha256(content.getvalue()).hexdigest()
    storage_key = f"skills/{SKILL_ID}/versions/{parsed.content_hash}/package.zip"
    return parsed, {
        "kind": "uploaded", "files": parsed.files, "storage_key": storage_key,
        "package_sha256": package_sha256,
        "package_contract": build_skill_package_contract(
            parsed, package_sha256, storage_key, uploaded_by="admin",
        ),
    }


@pytest.fixture
async def release_db(monkeypatch):
    dsn = os.getenv("AI_PLATFORM_S0A_SCHEMA_TEST_DSN")
    if not dsn:
        pytest.skip("AI_PLATFORM_S0A_SCHEMA_TEST_DSN is not configured")
    schema = "skill_release_" + uuid4().hex
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))

    @asynccontextmanager
    async def connect():
        async with await psycopg.AsyncConnection.connect(
            dsn, options=f"-c search_path={schema}", row_factory=dict_row,
        ) as conn:
            yield conn

    # Audit and distribution writes are outside this database slice. Skill SQL,
    # materialization, trust review and route transaction/lock order stay real.
    async def unrelated_write(conn, **kwargs):
        return None

    monkeypatch.setattr(skill_markdown, "_yaml_metadata_loader", load_skill_markdown_metadata)
    monkeypatch.setattr(skill_api, "_skill_version_persistence", postgres)
    monkeypatch.setattr(admin_skills, "transaction", connect)
    monkeypatch.setattr(admin_skills.identity_audit, "append_audit_log", unrelated_write)
    monkeypatch.setattr(catalog_postgres, "set_uploaded_workbench_skill_status", unrelated_write)
    v1, source1 = _uploaded_version("first")
    v2, source2 = _uploaded_version("second")
    principal = AuthPrincipal(user_id="admin", display_name="Admin", tenant_id="tenant-a", roles=["admin"])
    try:
        async with connect() as conn:
            await conn.execute("create table tenants(id text primary key)")
            source = (Path(__file__).resolve().parents[1] / "app/schema.sql").read_text()
            start = source.index("create table if not exists skills (")
            end = source.index("create table if not exists user_skill_files", start)
            await conn.execute(source[start:end])
            await conn.execute("insert into tenants values ('tenant-a'), ('tenant-b')")
            await versions_postgres.create_skill_catalog(
                conn, skill_id=SKILL_ID, name=SKILL_ID, version=v1.content_hash,
                description=v1.description, input_modes=["chat"], output_modes=["answer"],
                executor_type="claude-agent-worker",
            )
            for parsed, package_source in ((v1, source1), (v2, source2)):
                await versions_postgres.upsert_skill_version(
                    conn, skill_id=SKILL_ID, version=parsed.content_hash,
                    content_hash=parsed.content_hash, description=parsed.description,
                    source_json=package_source, dependency_ids=[], status="released", created_by="admin",
                )
            for tenant in ("tenant-a", "tenant-b"):
                await versions_postgres.set_skill_release_policy(
                    conn, tenant_id=tenant, skill_id=SKILL_ID, version=v1.content_hash,
                    previous_version=None, promoted_by="admin",
                )
        yield SimpleNamespace(
            connect=connect, admin=admin, principal=principal,
            v1=v1.content_hash, v2=v2.content_hash,
        )
    finally:
        await admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))
        await admin.close()


async def test_tenant_rollout_retries_preserve_shared_version_and_rollback(release_db):
    db = release_db
    for percentage in (10, 20, 100, 100):
        promoted = await admin_skills.admin_promote_skill_version(
            SKILL_ID, AdminSkillPromoteRequest(version=db.v2, rollout_percent=percentage), db.principal,
        )
        assert promoted.previous_version == db.v1
        async with db.connect() as conn:
            policy_b = await versions_postgres.get_skill_release_policy(conn, tenant_id="tenant-b", skill_id=SKILL_ID)
            assert policy_b["current_version"] == db.v1
            assert (await postgres.get_skill_version(conn, skill_id=SKILL_ID, version=db.v1))["status"] == "released"

    service = SkillRunAdmissionService(SkillRunAdmissionPorts(
        catalog=catalog_postgres, versions=versions_postgres,
        resolve_release_decision=resolve_rollout_skill_decision,
        release_decision_payload=release_decision_payload_for_locked_version,
        is_user_runnable_status=is_user_runnable_status,
        dependency_is_usable=is_current_skill_dependency_usable,
        build_manifest_pins=build_skill_version_policy_manifest_pins,
        lock_skill_version=governed_locked_skill_version,
        attach_snapshot_governance=attach_skill_snapshot_governance,
        materialization_error=SkillVersionMaterializationError,
    ))
    async with db.connect() as conn:
        admission = await service.admit(
            conn, skill={"skill_version": db.v1, "release_policy_version": db.v1,
                         "release_policy_rollout_percent": 100},
            skill_id=SKILL_ID, input_payload={}, tenant_id="tenant-b", rollout_key="user-b",
        )
        assert admission.skill_version == db.v1
    rolled_back = await admin_skills.admin_rollback_skill_version(
        SKILL_ID, AdminSkillRollbackRequest(version=db.v1), db.principal,
    )
    assert rolled_back.previous_version == db.v2
    async with db.connect() as conn:
        assert (await postgres.get_skill_version(conn, skill_id=SKILL_ID, version=db.v2))["status"] == "released"


@pytest.mark.parametrize("kind,expected", [
    ("other_tenant_current", True), ("gray_zero", True), ("gray_half", True),
    ("full_previous", False), ("inactive_policy", False), ("catalog_fallback", True), ("unreferenced", False),
])
async def test_global_release_reference_query_uses_real_policy_tracks(release_db, kind, expected):
    db = release_db
    async with db.connect() as conn:
        await postgres.lock_skill_version_lifecycle(conn, skill_id=SKILL_ID)
        await conn.execute("delete from skill_release_policies")
        target = db.v1 if kind == "catalog_fallback" else db.v2
        if kind not in {"catalog_fallback", "unreferenced"}:
            await versions_postgres.set_skill_release_policy(
                conn, tenant_id="tenant-b", skill_id=SKILL_ID,
                version=db.v2 if kind in {"other_tenant_current", "inactive_policy"} else db.v1,
                previous_version=db.v2, promoted_by="admin",
                rollout_percent={"gray_zero": 0, "gray_half": 50}.get(kind, 100),
                status="disabled" if kind == "inactive_policy" else "active",
            )
        assert await versions_postgres.has_active_skill_version_release_references(
            conn, skill_id=SKILL_ID, version=target,
        ) is expected


@pytest.mark.parametrize("first_action", ["promote", "disable"])
async def test_concurrent_promote_and_disable_recheck_after_real_skill_lock(release_db, monkeypatch, first_action):
    db = release_db
    original_lock = postgres.lock_skill_version_lifecycle
    first_locked = asyncio.Event()
    second_entered = asyncio.Event()
    release_first = asyncio.Event()
    pids = {}

    async def observed_lock(conn, *, skill_id):
        task_name = asyncio.current_task().get_name()
        pids[task_name] = conn.info.backend_pid
        if task_name == "skill-second":
            second_entered.set()
        await original_lock(conn, skill_id=skill_id)
        if task_name == "skill-first":
            first_locked.set()
            await release_first.wait()

    monkeypatch.setattr(postgres, "lock_skill_version_lifecycle", observed_lock)

    async def action(name):
        try:
            if name == "promote":
                await admin_skills.admin_promote_skill_version(
                    SKILL_ID, AdminSkillPromoteRequest(version=db.v2), db.principal,
                )
            else:
                await admin_skills.admin_update_skill_version_status(
                    SKILL_ID, db.v2, AdminSkillVersionStatusRequest(status="disabled"), db.principal,
                )
            return "success"
        except HTTPException as exc:
            return exc.detail

    async def wait_until_database_confirms_blocking():
        while True:
            row = await (await db.admin.execute(
                "select pg_blocking_pids(%s) as blockers", (pids["skill-second"],),
            )).fetchone()
            if pids["skill-first"] in row["blockers"]:
                return
            await asyncio.sleep(0.01)

    tasks = []
    try:
        tasks.append(asyncio.create_task(action(first_action), name="skill-first"))
        await asyncio.wait_for(first_locked.wait(), timeout=5)
        tasks.append(asyncio.create_task(
            action("disable" if first_action == "promote" else "promote"), name="skill-second",
        ))
        await asyncio.wait_for(second_entered.wait(), timeout=5)
        await asyncio.wait_for(wait_until_database_confirms_blocking(), timeout=5)
        assert not tasks[1].done()
        release_first.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
        assert results == ["success", (
            "skill_version_has_active_release_policy" if first_action == "promote" else "skill_version_inactive"
        )]
    finally:
        release_first.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    async with db.connect() as conn:
        current = await versions_postgres.get_skill_release_policy(conn, tenant_id="tenant-a", skill_id=SKILL_ID)
        version = await postgres.get_skill_version(conn, skill_id=SKILL_ID, version=db.v2)
        assert (current["current_version"], version["status"]) == (
            (db.v2, "released") if first_action == "promote" else (db.v1, "disabled")
        )
