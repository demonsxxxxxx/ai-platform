import pytest

from app.platform.postgres.errors import RepositoryConflictError
from app.skills.domain.version_labels import (
    next_uploaded_skill_display_version,
    resolve_uploaded_skill_display_versions,
)
from app.skills.infrastructure import postgres as skill_postgres


class _Cursor:
    def __init__(self, *, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, *, lock_row=None):
        self.lock_row = lock_row
        self.calls = []

    async def execute(self, query, params):
        self.calls.append((" ".join(query.split()), params))
        if "for update" in query.lower():
            return _Cursor(row=self.lock_row)
        return _Cursor(
            rows=[
                {
                    "skill_id": "review",
                    "version": "hash-a",
                    "display_version": None,
                }
            ]
        )


@pytest.mark.asyncio
async def test_display_version_adapter_locks_skill_before_ordered_history_read():
    conn = _Connection(lock_row={"id": "review"})

    await skill_postgres.lock_skill_for_version_upload(conn, skill_id="review")
    rows = await skill_postgres.list_uploaded_skill_display_version_rows(
        conn,
        skill_ids=["review"],
    )

    assert conn.calls[0] == (
        "select id from skills where id = %s for update",
        ("review",),
    )
    assert "skill_id = any(%s)" in conn.calls[1][0]
    assert "order by skill_id asc, created_at asc, version asc" in conn.calls[1][0]
    assert conn.calls[1][1] == (["review"],)
    assert rows[0]["version"] == "hash-a"


@pytest.mark.asyncio
async def test_display_version_adapter_rejects_a_missing_skill_lock_target():
    with pytest.raises(RepositoryConflictError, match="skill_not_found"):
        await skill_postgres.lock_skill_for_version_upload(
            _Connection(),
            skill_id="missing",
        )


def test_uploaded_skill_display_versions_start_at_one_and_increment_by_patch():
    rows = [
        {"skill_id": "review", "version": "hash-a", "display_version": None},
        {"skill_id": "review", "version": "hash-b", "display_version": "1.0.1"},
        {"skill_id": "review", "version": "hash-c", "display_version": None},
        {"skill_id": "translate", "version": "hash-z", "display_version": None},
    ]

    assert resolve_uploaded_skill_display_versions(rows) == {
        ("review", "hash-a"): "1.0.0",
        ("review", "hash-b"): "1.0.1",
        ("review", "hash-c"): "1.0.2",
        ("translate", "hash-z"): "1.0.0",
    }
    assert next_uploaded_skill_display_version("review", rows) == "1.0.3"
    assert next_uploaded_skill_display_version("new-skill", rows) == "1.0.0"
