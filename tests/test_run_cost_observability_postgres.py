"""Real-PostgreSQL coverage for privately marked and legacy Run costs."""

from typing import Any

from psycopg.types.json import Jsonb
import pytest

from app.control_plane_contracts import standard_trace_id
from app.identity.infrastructure.audit_postgres import append_audit_log
from app.platform.public_payload import sanitize_public_payload, sanitize_public_text
from app.runs.application.lifecycle import RunLifecycleService
from app.runs.infrastructure.admin_queries_postgres import (
    get_admin_runtime_observability_summary,
)
from app.runs.infrastructure.diagnostics_postgres import (
    PostgresRunDiagnosticsRepository,
)
from app.runs.infrastructure.lifecycle_postgres import (
    PostgresRunLifecyclePersistence,
    require_run_result_size,
)
from app.streaming.infrastructure.run_events_postgres import append_event
from tests.support.db_transactions import event_loop_policy as event_loop_policy
from tests.test_run_diagnostics_postgres import _isolated_postgres_run


def _observed_cost(amount: Any, coverage: str = "complete") -> dict[str, Any]:
    return {
        "cost": {"estimated_cost_minor": amount},
        "runtime_private_payload": {"sdk_usage": {"cost_coverage": coverage}},
    }


# These cases must agree in SQL aggregates and individual monitor metadata.
# A cost value alone never promotes an old result into the new contract.
_COST_AUTHORITY_CASES = [
    ("legacy-conflicting-null", {"cost": {"estimated_cost_minor": None}}, 97),
    ("legacy-conflicting-zero", {"cost": {"estimated_cost_minor": 0}}, 97),
    ("legacy-conflicting-positive", {"cost": {"estimated_cost_minor": 7}}, 97),
    ("legacy-empty-carrier", {"runtime_private_payload": {"sdk_usage": {}}}, 97),
    ("legacy-unrecognized-coverage", _observed_cost(None, "future"), 97),
    ("partial-positive", _observed_cost(7, "partial"), None),
    ("unavailable-positive", _observed_cost(7, "unavailable"), None),
    ("complete-invalid-null", _observed_cost(None), None),
    ("complete-invalid-bool", _observed_cost(True), None),
    ("complete-invalid-string", _observed_cost("7"), None),
    ("complete-invalid-negative", _observed_cost(-1), None),
    ("complete-invalid-overflow", _observed_cost(2**31), None),
    ("complete-invalid-fraction", _observed_cost(1.5), None),
    ("complete-max-int", _observed_cost(2**31 - 1), 2**31 - 1),
    (
        "complete-missing-estimate",
        {"cost": {}, "runtime_private_payload": {"sdk_usage": {"cost_coverage": "complete"}}},
        None,
    ),
]


async def _insert_run(
    conn,
    *,
    run_id: str,
    stored_cost: int,
    result: dict[str, Any],
    tenant_suffix: str = "a",
) -> None:
    await conn.execute(
        """
        insert into runs(
          id, tenant_id, workspace_id, session_id, user_id, agent_id,
          skill_id, status, estimated_cost_minor, result_json
        ) values (%s, %s, %s, %s, %s, %s, 'skill-a', 'succeeded', %s, %s)
        """,
        (
            run_id,
            f"tenant-{tenant_suffix}",
            f"workspace-{tenant_suffix}",
            f"session-{tenant_suffix}",
            f"user-{tenant_suffix}",
            f"agent-{tenant_suffix}",
            stored_cost,
            Jsonb(result),
        ),
    )


async def _insert_foreign_unknown_run(conn) -> None:
    """Create a fully separate synthetic tenant with an explicit unknown cost."""
    await conn.execute(
        """
        insert into tenants(id, name) values ('tenant-b', 'Tenant B');
        insert into workspaces(id, tenant_id, name)
          values ('workspace-b', 'tenant-b', 'B');
        insert into users(id, tenant_id, display_name)
          values ('user-b', 'tenant-b', 'B');
        insert into agents(id, tenant_id, name, agent_type)
          values ('agent-b', 'tenant-b', 'B', 'chat');
        insert into sessions(
          id, tenant_id, workspace_id, user_id, agent_id, title, status
        ) values (
          'session-b', 'tenant-b', 'workspace-b', 'user-b', 'agent-b', 'B', 'active'
        );
        """
    )
    await _insert_run(
        conn,
        run_id="foreign-unknown",
        stored_cost=101,
        result=_observed_cost(None, "unavailable"),
        tenant_suffix="b",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows,foreign_unknown,expected",
    [
        pytest.param([], False, 0, id="empty"),
        pytest.param(
            [(97, _observed_cost(0))],
            False,
            0,
            id="complete-zero",
        ),
        pytest.param(
            [(97, _observed_cost(7)), (13, {})],
            False,
            20,
            id="known-and-legacy",
        ),
        pytest.param(
            [(97, _observed_cost(None, "unavailable"))],
            False,
            None,
            id="explicit-null",
        ),
        pytest.param(
            [
                (97, _observed_cost(None, "partial")),
                (83, _observed_cost(7)),
                (13, {}),
            ],
            False,
            None,
            id="null-with-known-and-legacy",
        ),
        pytest.param(
            [
                (3, {}),
                (5, {"cost": {}}),
                (7, {"cost": {"currency": "CNY"}}),
                (11, {"cost": None}),
            ],
            False,
            26,
            id="legacy-missing-estimate",
        ),
        pytest.param(
            [(97, _observed_cost(7)), (13, {})],
            True,
            20,
            id="foreign-unknown",
        ),
        *[
            pytest.param([(97, result)], False, expected, id=case_id)
            for case_id, result, expected in _COST_AUTHORITY_CASES
        ],
    ],
)
async def test_postgres_cost_summary_preserves_null_zero_and_legacy(
    rows, foreign_unknown, expected
):
    async with _isolated_postgres_run() as (_dsn, _schema, admin, conn):
        await admin.execute("delete from runs where id = 'run-a'")
        for index, (stored_cost, result) in enumerate(rows):
            await _insert_run(
                admin,
                run_id=f"cost-run-{index}",
                stored_cost=stored_cost,
                result=result,
            )
        if foreign_unknown:
            await _insert_foreign_unknown_run(admin)

        async with conn.transaction():
            summary = await get_admin_runtime_observability_summary(
                conn, tenant_id="tenant-a"
            )
            if foreign_unknown:
                foreign = await get_admin_runtime_observability_summary(
                    conn, tenant_id="tenant-b"
                )
                assert foreign["estimated_cost_minor"] is None

        assert summary["estimated_cost_minor"] == expected
        if expected is not None:
            assert type(summary["estimated_cost_minor"]) is int
        assert summary["token_counts"] == {"input": 0, "output": 0, "total": 0}


@pytest.mark.asyncio
async def test_postgres_monitor_metadata_preserves_cost_semantics_and_tenant_scope():
    cases = [
        ("unknown", 97, _observed_cost(None, "unavailable"), None),
        ("partial", 97, _observed_cost(None, "partial"), None),
        ("complete-zero", 97, _observed_cost(0), 0),
        ("known", 97, _observed_cost(7), 7),
        ("legacy", 13, {}, 13),
        ("empty-cost", 17, {"cost": {}}, 17),
        ("missing-estimate", 19, {"cost": {"currency": "CNY"}}, 19),
        ("null-cost-container", 23, {"cost": None}, 23),
        *[(case_id, 97, result, expected) for case_id, result, expected in _COST_AUTHORITY_CASES],
    ]
    async with _isolated_postgres_run() as (_dsn, _schema, admin, conn):
        for run_id, stored_cost, result, _expected in cases:
            await _insert_run(
                admin, run_id=run_id, stored_cost=stored_cost, result=result
            )
        await _insert_foreign_unknown_run(admin)
        repository = PostgresRunDiagnosticsRepository()

        async with conn.transaction():
            metadata = await repository.get_admin_monitor_metadata(
                conn,
                tenant_id="tenant-a",
                run_ids=tuple(row[0] for row in cases)
                + ("foreign-unknown", "does-not-exist"),
            )
            foreign = await repository.get_admin_monitor_metadata(
                conn,
                tenant_id="tenant-b",
                run_ids=("unknown", "complete-zero", "foreign-unknown"),
            )

        assert set(metadata) == {row[0] for row in cases}
        for run_id, _stored_cost, _result, expected in cases:
            assert metadata[run_id]["estimated_cost_minor"] == expected
            assert "runtime_private_payload" not in metadata[run_id]
            if expected is not None:
                assert type(metadata[run_id]["estimated_cost_minor"]) is int
        assert set(foreign) == {"foreign-unknown"}
        assert foreign["foreign-unknown"]["estimated_cost_minor"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("target_status", ["failed", "cancelled"])
@pytest.mark.parametrize(
    "estimated_cost,coverage",
    [
        pytest.param(None, "unavailable", id="unavailable"),
        pytest.param(None, "partial", id="partial"),
        pytest.param(0, "complete", id="complete-zero"),
    ],
)
async def test_postgres_terminalization_preserves_explicit_cost_and_public_facts(
    target_status, estimated_cost, coverage
):
    result = {
        "message": "synthetic terminal result",
        **_observed_cost(estimated_cost, coverage),
    }
    persistence = PostgresRunLifecyclePersistence()
    lifecycle = RunLifecycleService(
        persistence=persistence,
        append_event=append_event,
        append_audit_log=append_audit_log,
        validate_result_size=require_run_result_size,
        sanitize_payload=sanitize_public_payload,
        sanitize_text=sanitize_public_text,
        make_trace_id=standard_trace_id,
    )
    async with _isolated_postgres_run() as (_dsn, _schema, admin, conn):
        await admin.execute(
            "update runs set estimated_cost_minor = 97 where id = 'run-a'"
        )
        async with conn.transaction():
            staged = await persistence.stage_run_terminalization(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
                target_status=target_status,
                terminal_reason=f"run_{target_status}",
                result_json=result,
            )
            assert staged is not None

        # A separate connection must observe the JSON null/zero after COMMIT.
        cursor = await admin.execute(
            "select terminalization_result_json from runs where id = 'run-a'"
        )
        assert (await cursor.fetchone())["terminalization_result_json"] == result

        async with conn.transaction():
            progress = await lifecycle.progress_run_terminalization(
                conn, tenant_id="tenant-a", run_id="run-a"
            )
        assert progress is not None
        assert progress.completed and progress.did_transition
        assert progress.status == target_status

        cursor = await admin.execute(
            "select status, result_json from runs where id = 'run-a'"
        )
        durable = await cursor.fetchone()
        assert durable == {"status": target_status, "result_json": result}

        # The legacy non-null integer column can retain its prior value;
        # readers and terminal facts must use the explicit JSON value instead.
        summary = await get_admin_runtime_observability_summary(
            admin, tenant_id="tenant-a"
        )
        metadata = await PostgresRunDiagnosticsRepository().get_admin_monitor_metadata(
            admin, tenant_id="tenant-a", run_ids=("run-a",)
        )
        assert summary["estimated_cost_minor"] == estimated_cost
        assert metadata["run-a"]["estimated_cost_minor"] == estimated_cost
        assert "runtime_private_payload" not in summary
        assert "runtime_private_payload" not in metadata["run-a"]

        cursor = await admin.execute(
            "select payload_json from run_events "
            "where tenant_id = 'tenant-a' and run_id = 'run-a' and event_type = %s",
            (f"run_{target_status}",),
        )
        events = await cursor.fetchall()
        assert len(events) == 1
        # Terminal events carry the cost separately from the generic sanitized
        # result so an explicit unknown survives public-payload sanitization.
        assert "estimated_cost_minor" in events[0]["payload_json"]["cost"]
        assert events[0]["payload_json"]["cost"]["estimated_cost_minor"] == estimated_cost
        assert events[0]["payload_json"]["runtime_private_payload"] == {
            "sdk_usage": {"cost_coverage": coverage}
        }
        assert "runtime_private_payload" not in events[0]["payload_json"]["result"]

        cursor = await admin.execute(
            "select payload_json from audit_logs "
            "where tenant_id = 'tenant-a' and target_id = 'run-a' and action = %s",
            (f"run.{target_status}",),
        )
        audits = await cursor.fetchall()
        assert len(audits) == 1
        assert "estimated_cost_minor" in audits[0]["payload_json"]
        assert audits[0]["payload_json"]["estimated_cost_minor"] == estimated_cost
