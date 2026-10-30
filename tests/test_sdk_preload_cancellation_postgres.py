"""Preload cancellation receipt settles Context lineage and admits the next run."""
# ruff: noqa: F401,F811 -- shared PostgreSQL fixture is injected by name.
from types import SimpleNamespace

import pytest

from app.bootstrap.context import configure_context_services
from app.bootstrap.run_attempt_lifecycle import build_run_attempt_lifecycle_service
from app.context.application.provider_sessions import settle_provider_continuation
from app.context.infrastructure import provider_epochs
from tests.test_provider_unclaimed_continuation_postgres import (
    callback,
    prepare,
    seed_source,
    unclaimed_db,
)
from tests.test_sdk_preload_cancellation import (
    _UNACCEPTED_SDK_SETTLEMENT,
    _cancel_preload_through_executor,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("with_source", [False, True], ids=["first-input", "existing-source"])
async def test_preload_cancel_terminal_settlement_releases_context_lineage_and_allows_next_run(
    unclaimed_db, monkeypatch, tmp_path, with_source,
):
    async with unclaimed_db() as conn:
        source = await seed_source(conn) if with_source else None
        session_generation = 2 if with_source else 1
        frozen = await prepare(conn, "target", session_generation)

        terminal = await _cancel_preload_through_executor(
            monkeypatch,
            tmp_path,
            tenant_id="tenant",
            workspace_id="workspace",
            user_id="user",
            session_id="session",
            agent_id="agent",
            run_id="target",
            attempt_id="attempt-target",
            sdk_session_id=frozen["provider_session_id"],
            provider_epoch_id=frozen["provider_epoch_id"],
            provider_session_resume_required=with_source,
        )
        assert terminal["status"] == "cancelled"
        assert terminal["sdk_settlement"] == _UNACCEPTED_SDK_SETTLEMENT

        await conn.execute("update runs set cancel_requested_at=now() where id='target'")
        await conn.execute(
            "update run_attempts set status='cancel_requested',owner_generation=3 where id='attempt-target'"
        )
        sdk_settlement = {**terminal["sdk_settlement"], "owner_generation": 3}
        configure_context_services()
        closing = await settle_provider_continuation(
            conn,
            tenant_id="tenant",
            run_id="target",
            attempt_id="attempt-target",
            final_sequence=None,
            workspace_receipt=None,
            stop_evidence="provider_not_created",
            sdk_settlement=sdk_settlement,
        )
        assert closing["settlement_kind"] == "unclaimed_unaccepted"
        assert closing["input_accepted"] is False and closing["ack_unknown"] is False

        await conn.execute("update runs set status='cancelled' where id='target'")
        monkeypatch.setattr(
            "app.bootstrap.run_attempt_lifecycle.get_settings",
            lambda: SimpleNamespace(run_attempt_settlement_shadow_enabled=True),
        )
        transitioned = await build_run_attempt_lifecycle_service().terminalize(
            conn,
            tenant_id="tenant",
            run_id="target",
            attempt_id="attempt-target",
            status="cancelled",
            terminal_reason="executor_cancelled",
        )
        assert transitioned["owner_generation"] == 4
        await provider_epochs.release_provider_lineage(conn, tenant_id="tenant", run_id="target")
        head = await (await conn.execute(
            "select active_run_id,active_attempt_id from provider_session_heads"
        )).fetchone()
        assert head == {"active_run_id": None, "active_attempt_id": None}

        next_frozen = await prepare(conn, "next", session_generation + 1)
        assert next_frozen["execution_mode"] == ("native_resume" if with_source else "empty_start")
        if source is not None:
            assert next_frozen["provider_session_id"] == source["provider_session_id"]
            assert (await callback(conn, "next", next_frozen, "load"))["entries"] == [
                {"type": "user", "uuid": "source-user"}
            ]
