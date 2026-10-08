import pytest

from app.skills.api import persist_worker_skill_snapshots


@pytest.mark.asyncio
async def test_worker_skill_snapshot_keeps_only_admitted_fields_on_the_same_connection():
    observed = []
    conn = object()

    async def upsert(received_conn, **fields):
        assert received_conn is conn
        observed.append(fields)

    await persist_worker_skill_snapshots(
        conn,
        [{"skill_id": "", "version": "invalid"}, {
            "skill_id": "synthetic", "version": "locked", "content_hash": "hash",
            "dependency_ids": ["dependent"], "allowed": True, "staged": True,
            "used": False, "used_skills_source": "",
        }],
        tenant_id="tenant", run_id="run", release_decision={"status": "admitted"},
        source_json=lambda item, *, release_decision: {"version": item["version"], "release": release_decision["status"]},
        upsert_snapshot=upsert,
    )
    assert len(observed) == 1
    assert observed[0]["source_json"] == {"version": "locked", "release": "admitted"}
    assert observed[0]["skill_version"] == "locked"
    assert observed[0]["dependency_ids"] == ["dependent"]
    assert observed[0]["used"] is False
