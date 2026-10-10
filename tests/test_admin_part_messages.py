"""Part facts -> owning persisted reader -> admin detail (shared mounted fixture)."""

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.runs.infrastructure import admin_queries_postgres as queries
from app.streaming.api import project_persisted_assistant_text_messages
from app.runs.application.admin_run_monitor import build_admin_worker_execution
from tests.test_admin_run_detail import (
    auth_settings,
    create_app,
    headers,
    _install_admin_monitor_metadata,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/admin-part-message.json").read_text()
)


def _rows():
    rows = deepcopy(FIXTURE["rows"])
    for row in rows:
        row["created_at"] = datetime.fromisoformat(
            row["created_at"].replace("Z", "+00:00")
        )
    return rows


def _execution(rows):
    return build_admin_worker_execution(
        [
            {
                "event_id": r["id"],
                "type": r["event_type"],
                "sequence": r["sequence"],
                "visible_to_user": r["visible_to_user"],
                "payload": r["payload_json"],
            }
            for r in rows
        ],
        part_messages=project_persisted_assistant_text_messages(
            rows, tenant_id="default", run_id="run_parts"
        ),
        sanitize_text=lambda text: text.replace("token", "[redacted]"),
    )


def test_final_answer_excludes_work_and_matches_mounted_contract():
    assert _execution(_rows()) == FIXTURE["worker_execution"]


@pytest.mark.parametrize(
    "damage",
    [
        "missing_role",
        "count",
        "length",
        "causation",
        "unauthorized",
        "attempt",
        "incarnation",
        "epoch",
        "private_classification",
        "mixed",
        "after_completion",
        "duplicate_source",
    ],
)
def test_invalid_or_incomplete_part_ledger_never_supplies_an_answer(damage):
    rows = _rows()
    if damage == "missing_role":
        rows.pop(4)
    elif damage in {"count", "length"}:
        rows[-1]["payload_json"][
            "delta_count" if damage == "count" else "text_length"
        ] += 1
    elif damage == "causation":
        rows[-1]["payload_json"]["__stream_v4"]["causation_event_id"] = "foreign_source"
    elif damage == "unauthorized":
        rows[4]["v4_attempt_authorized"] = False
    elif damage in {"attempt", "incarnation", "epoch"}:
        key = {
            "attempt": "attempt_id",
            "incarnation": "stream_incarnation",
            "epoch": "authorization_epoch",
        }[damage]
        rows[4]["payload_json"]["__stream_v4"][key] = (
            "attempt_b" if damage == "attempt" else 2
        )
    elif damage == "private_classification":
        rows[4]["visible_to_user"] = False
    elif damage == "mixed":
        rows[3]["event_type"] = "message.delta"
        rows[3]["payload_json"] = {
            "delta": "legacy_must_not_leak",
            "__stream_v4": rows[3]["payload_json"]["__stream_v4"],
        }
    elif damage == "after_completion":
        extra = deepcopy(rows[3])
        extra["sequence"] = 8
        extra["id"] = "evt4_after"
        rows.append(extra)
    elif damage == "duplicate_source":
        rows[5]["payload_json"]["__stream_v4"]["source_event_id"] = "source_4"
    result = _execution(rows)
    assert result["response"] == ""
    assert result["messages"] == []
    assert result["answer_projection"]["status"] == "invalid"


def test_pending_and_work_only_are_incomplete_without_private_result_fallback():
    rows = _rows()[:-1]
    for row in rows:
        if row["event_type"] == "message.part.classified":
            row["payload_json"]["role"] = "work"
    result = _execution(rows)
    assert result["response"] == ""
    assert result["messages"] == []
    assert result["answer_projection"] == {
        "status": "incomplete",
        "incomplete_messages": 1,
        "invalid_messages": 0,
    }
    assert _execution(_rows()[:4])["answer_projection"]["status"] == "incomplete"
    assert _execution([])["answer_projection"]["status"] == "unknown"


def test_private_and_foreign_rows_do_not_join_or_override_a_valid_message():
    rows = _rows()
    for field, value in [
        ("visible_to_user", False),
        ("tenant_id", "other"),
        ("run_id", "run_other"),
    ]:
        extra = deepcopy(rows[3])
        extra[field] = value
        extra["payload_json"]["delta"] = "PRIVATE_TEXT_MUST_NOT_LEAK"
        rows.append(extra)
    result = _execution(rows)
    assert result == FIXTURE["worker_execution"]


def test_whole_answer_redaction_spans_deltas_and_latest_empty_keeps_answer():
    rows = _rows()
    rows[3]["payload_json"]["delta"] = "tok"
    rows[5]["payload_json"]["delta"] = "en"
    rows[-1]["payload_json"]["text_length"] = 5
    empty = deepcopy(rows[0])
    empty["id"] = "evt4_empty"
    empty["sequence"] = 8
    empty["payload_json"]["__stream_v4"]["message_id"] = "msg_empty"
    rows.append(empty)
    result = _execution(rows)
    assert result["response"] == "[redacted]"
    assert result["messages"][0]["text"] == "[redacted]"


def test_legacy_latest_empty_and_duplicate_mirror_use_one_message_selection():
    events = [
        {
            "type": "message.delta",
            "sequence": 1,
            "visible_to_user": True,
            "payload": {
                "delta": "旧协议答案",
                "__stream_v4": {"message_id": "msg_old"},
            },
        },
        {
            "type": "message.completed",
            "sequence": 2,
            "visible_to_user": True,
            "payload": {"__stream_v4": {"message_id": "msg_empty"}},
        },
        {
            "type": "assistant_delta",
            "sequence": 3,
            "visible_to_user": True,
            "payload": {"delta": "MIRROR"},
        },
    ]
    result = build_admin_worker_execution(events, sanitize_text=lambda text: text)
    assert result["response"] == "旧协议答案"
    assert [m["text"] for m in result["messages"]] == ["旧协议答案"]


def test_real_admin_query_and_detail_serialize_shared_part_fixture(monkeypatch):
    class Cursor:
        async def fetchall(self):
            return []

    class Connection:
        async def execute(self, _sql, _params):
            return Cursor()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def transaction():
        yield Connection()

    async def get_run(_conn, *, tenant_id, run_id):
        assert (tenant_id, run_id) == ("default", "run_parts")
        return {
            "id": run_id,
            "session_id": "ses_a",
            "user_id": "user_a",
            "workspace_id": "default",
            "status": "succeeded",
            "agent_id": "agent_a",
            "skill_id": None,
            "created_at": None,
            "started_at": None,
            "finished_at": None,
            "input_json": {},
            "result_json": {"message": "PRIVATE_RESULT_MUST_NOT_BE_ANSWER"},
            "schema_version": "ai-platform.run.v1",
            "executor_schema_version": "ai-platform.executor-result.v1",
        }

    async def list_events(_conn, *, tenant_id, run_id):
        assert (tenant_id, run_id) == ("default", "run_parts")
        return _rows()

    async def empty(*_args, **_kwargs):
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", transaction)
    monkeypatch.setattr(queries, "get_run", get_run)
    monkeypatch.setattr(queries, "list_run_events", list_events)
    for name in (
        "list_run_steps",
        "list_run_artifacts",
        "list_sandbox_leases_for_run",
        "list_run_skill_snapshots",
    ):
        monkeypatch.setattr(queries, name, empty)
    _install_admin_monitor_metadata(monkeypatch, {})
    client = TestClient(create_app())
    response = client.get("/api/ai/admin/runs/run_parts", headers=headers())
    client.close()
    assert response.status_code == 200
    body = response.json()
    assert body["worker_execution"] == FIXTURE["worker_execution"]
    assert body["run"]["model_output"] == "正常公开回答"
    assert "_assistant_text_messages" not in body
    assert "__stream_v4" not in response.text
    assert "attempt_a" not in response.text


def test_legacy_v4_same_message_id_in_distinct_attempts_never_concatenates():
    events = [
        {
            "type": "message.delta",
            "sequence": sequence,
            "visible_to_user": True,
            "payload": {
                "delta": text,
                "__stream_v4": {"message_id": "msg_shared", "attempt_id": attempt},
            },
        }
        for sequence, text, attempt in [
            (1, "first", "attempt_a"),
            (2, "second", "attempt_b"),
        ]
    ]
    result = build_admin_worker_execution(events, sanitize_text=lambda text: text)
    assert [message["text"] for message in result["messages"]] == ["first", "second"]
    assert result["response"] == "second"


def test_invalid_role_and_completed_work_only_remain_unconfirmed():
    rows = _rows()
    rows[4]["payload_json"]["role"] = "private"
    assert _execution(rows)["answer_projection"]["status"] == "invalid"
    rows = _rows()
    rows[4]["payload_json"]["role"] = "work"
    result = _execution(rows)
    assert result["messages"] == []
    assert result["response"] == ""
    assert result["answer_projection"]["status"] == "invalid"


def test_commentary_only_is_visible_but_does_not_become_a_final_answer():
    result = build_admin_worker_execution(
        [
            {
                "type": "commentary.delta",
                "sequence": 1,
                "visible_to_user": True,
                "payload": {
                    "delta": "工具过程说明",
                    "summary_id": "summary_a",
                    "__stream_v4": {"message_id": "msg_a"},
                },
            },
        ],
        sanitize_text=lambda text: text,
    )
    assert result["response"] == ""
    assert result["messages"][0]["kind"] == "commentary"
    assert result["answer_projection"]["status"] == "unknown"
