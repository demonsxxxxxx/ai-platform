"""Real PG/ACK/HTTP composition for the production public-summary producer.

Requires AI_PLATFORM_S0A_SCHEMA_TEST_DSN; missing DSN is not passing evidence.
"""
# ruff: noqa: F401,F811 -- shared isolated PostgreSQL fixture.
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from psycopg.types.json import Jsonb

from app.auth import AuthPrincipal, require_principal
from app.bootstrap.mcp import configure_mcp_runtime
from app.bootstrap.recovery_operations import build_recovery_operations
from app.context.infrastructure import provider_epochs
from app.context.infrastructure.provider_continuation import settle_provider_continuation
from app.mcp import api as mcp_api
from app.mcp.infrastructure import postgres as mcp_postgres
from app.runtime.sandbox.callback_tokens import derive_callback_token
from tests.test_mcp_public_operation_summary import DECLARATION
from tests.test_provider_unclaimed_continuation_postgres import unclaimed_db, prepare, callback, SCOPE_MAP


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["native_query", "host_startup", "host_unresolved", "host_metadata_revoked", "host_native_mismatch", "host_lease_expired"])
async def test_two_native_ack_objects_http_questions_authenticated_answers_and_allow_once(unclaimed_db, monkeypatch, entry):
    from app.routes import runtime_callbacks, runs
    from app.settings import Settings
    from app import principal_authority
    from app.context.application.provider_sessions import configure_provider_session_use_cases, ProviderSessionUseCases
    # Compose real repositories; company identity is the only external authority port.
    configure_provider_session_use_cases(ProviderSessionUseCases(provider_epochs.PostgresProviderEpochRepository()))
    configure_mcp_runtime()
    principal = AuthPrincipal(tenant_id="tenant", user_id="user", display_name="Synthetic", department_id="110", direct_department_ids=("110",), department_ids=("100", "110"),
                              department_authority_status="available", roles=["user"], permissions=["mcp:read"], source="test")
    async def current_principal(**kwargs):
        assert kwargs == {"tenant_id": "tenant", "user_id": "user"}
        return principal
    monkeypatch.setattr(principal_authority, "resolve_current_principal", current_principal)
    settings = Settings(sandbox_callback_token="synthetic-callback-secret-only")
    monkeypatch.setattr(runtime_callbacks, "get_settings", lambda: settings)
    @asynccontextmanager
    async def transaction():
        async with unclaimed_db() as conn, conn.transaction():
            yield conn
    monkeypatch.setattr(runtime_callbacks, "transaction", transaction)
    monkeypatch.setattr(runs, "transaction", transaction)
    app = FastAPI()
    app.include_router(runtime_callbacks.router, prefix="/api")
    app.include_router(runs.router, prefix="/api")
    app.dependency_overrides[require_principal] = lambda: principal
    _, app.state.run_inputs_service = build_recovery_operations()
    shared = Path("app/schema.sql").read_text(encoding="utf-8")
    async with transaction() as conn:
        await conn.execute("""
            create table tenants(id text primary key);
            insert into tenants values('tenant');
            alter table run_attempts add unique(id);
            alter table sandbox_leases add column workspace_id text default 'workspace';
            alter table sandbox_leases add column user_id text default 'user';
            alter table sandbox_leases add column session_id text default 'session';
        """)
        await conn.execute(shared[shared.index("create table if not exists mcp_servers"):shared.index("create table if not exists mcp_tools")])
        start = shared.index("create table if not exists run_input_sessions")
        await conn.execute(shared[start:shared.index("create or replace function", start)])
        await conn.execute(Path("app/execution/infrastructure/recovery_operations_schema.sql").read_text())
        await conn.execute("insert into tenant_capability_distribution_backfills values('tenant',now())")
        await conn.execute("insert into mcp_servers(id,tenant_id,name) values('server','tenant','vendor')")
        await mcp_postgres.upsert_mcp_server_distribution(conn, tenant_id="tenant", server_name="vendor", status="active", visible_to_user=True,
            scope_mode="allowlist", department_ids=["100"], allowed_roles=["user"], metadata_json={"public_operation_summaries": {"create_project": DECLARATION}}, updated_by="user")
        # Fail at the real distribution decision rather than a later recovery projection.
        authorized = await mcp_api.authorize_selected_chat_mcp_tools(
            conn, tenant_id=principal.tenant_id, tool_ids=["vendor::create_project"],
            principal_department_id=principal.department_id, principal_department_ids=principal.department_ids,
            principal_roles=principal.roles, is_admin=False, permissions=principal.permissions,
        )
        assert [tool["tool_id"] for tool in authorized] == ["vendor::create_project"]
        tool = await mcp_api.get_mcp_tool_registry_entry(conn, tenant_id="tenant", tool_id="vendor::create_project")
        subject = mcp_api.mcp_capability_subject(tool, distribution_usable=True, sanitize_label=str)
        assert subject["public_operation_summary"] == DECLARATION
        frozen = await prepare(conn, "source", 1)
        await conn.execute("update run_attempts set execution_spec_json=jsonb_set(execution_spec_json,'{input}',%s) where id='attempt-source'", (Jsonb({"_runtime_tool_policy_subjects": [subject]}),))
        await conn.execute("update sandbox_leases set lease_payload_json=lease_payload_json || %s where run_id='source'", (Jsonb({"attempt_id": "attempt-source", "callback_token_id": "cbt:source:attempt-source:g2"}),))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        async def effect(run, native, operation):
            token_id = f"cbt:{run}:attempt-{run}:g2"
            return await client.post("/api/runtime/callbacks/recovery-operation", headers={"X-AI-Platform-Callback-Token": derive_callback_token(settings.sandbox_callback_token, token_id)}, json={
                "run_id": run, "attempt_id": f"attempt-{run}", "callback_token_id": token_id,
                "tool_identity": subject["identity"], "invocation_id": native, "operation": operation,
            })
        # Started/completed facts are legal before the later native ACK exists.
        for native in ("private-native-a", "private-native-b"):
            assert (await effect("source", native, "started")).status_code == 200
            assert (await effect("source", native, "completed")).status_code == 200
        async with transaction() as conn:
            await callback(conn, "source", frozen, "load")
            await callback(conn, "source", frozen, "append", [{"type": "assistant", "uuid": "synthetic-assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": native + ("-mismatch" if entry == "host_native_mismatch" else ""), "name": subject["identity"], "input": {"project": {"name": name}, "password": "unselected-secret", "path": "/tmp/unselected"}}
                for native, name in (("private-native-a", "华东项目"), ("private-native-b", "华南项目"))
            ]}}], 1)
            # Completed error results leave no pending native call for the SDK to replay.
            await callback(conn, "source", frozen, "append", [{"type": "user", "uuid": "synthetic-error", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": native, "is_error": True, "content": "synthetic timeout"}
                for native in ("private-native-a", "private-native-b")
            ]}}], 2)
            receipt = dict(schema_version="ai-platform.workspace-version.v1", state="saved", version_id="synthetic-version",
                scope={**{k: v for k, v in SCOPE_MAP.items() if k != "agent_id"}, "run_id": "source", "attempt_id": "attempt-source"}, file_count=0, total_bytes=0, source_stop_evidence="provider_stopped")
            await settle_provider_continuation(conn, tenant_id="tenant", run_id="source", attempt_id="attempt-source", final_sequence=2,
                workspace_receipt=receipt, stop_evidence="provider_stopped", sdk_settlement=dict(owner_generation=2, input_accepted=True, mirror_complete=True), external_effect_unknown=True)
            await conn.execute("update runs set status='failed' where id='source'")
            await provider_epochs.release_provider_lineage(conn, tenant_id="tenant", run_id="source")
            target = await prepare(conn, "target", 2)
            await conn.execute("update run_attempts set execution_spec_json=jsonb_set(execution_spec_json,'{input}',%s) where id='attempt-target'", (Jsonb({"_runtime_tool_policy_subjects": [subject]}),))
            await conn.execute("update sandbox_leases set lease_payload_json=lease_payload_json || %s where run_id='target'", (Jsonb({"attempt_id": "attempt-target", "callback_token_id": "cbt:target:attempt-target:g2"}),))
            await conn.execute("insert into run_input_sessions(tenant_id,run_id,attempt_id) values('tenant','target','attempt-target')")
        async def host_start(run="target"):
            token_id = f"cbt:{run}:attempt-{run}:g2"
            return await client.post("/api/runtime/callbacks/inputs", headers={"X-AI-Platform-Callback-Token": derive_callback_token(settings.sandbox_callback_token, token_id)}, json={
                "run_id": run, "attempt_id": f"attempt-{run}", "callback_token_id": token_id, "operation": "open",
            })
        if entry == "host_metadata_revoked":
            async with transaction() as conn:
                await conn.execute("update tenant_capability_distributions set metadata_json='{}'")
        questions = []
        if entry == "native_query":
            for native in ("private-native-a", "private-native-b"):
                response = await effect("target", native, "query")
                assert response.status_code == 200, response.text
                assert response.json()["decision"] == "wait", response.text
                questions.append(response.json()["question_id"])
        else:
            response = await host_start()
            assert response.status_code == 200, response.text
            if entry in {"host_metadata_revoked", "host_native_mismatch"}:
                assert not response.json()["recovery_confirmation_pending"]
                assert response.json()["recovery_confirmation_reason"] == (
                    "recovery_operation_public_object_metadata_changed" if entry == "host_metadata_revoked" else "recovery_operation_native_evidence_invalid")
                async with transaction() as conn:
                    assert (await (await conn.execute("select count(*) as n from run_input_questions")).fetchone())["n"] == 0
                    rows = await (await conn.execute("select verified_outcome from execution_recovery_operations")).fetchall()
                    assert [row["verified_outcome"] for row in rows] == ["unknown", "unknown"]
                return
            assert response.json()["recovery_confirmation_pending"]
            assert (await host_start()).json()["recovery_confirmation_pending"]
            async with transaction() as conn:
                rows = await (await conn.execute("select question_id from run_input_questions order by created_at")).fetchall()
                questions = [row["question_id"] for row in rows]
                assert len(questions) == 2
                bindings = await (await conn.execute("select * from execution_recovery_invocations")).fetchall()
                assert all(row["binding_kind"] == "source_operation" and row["invocation_id"] == row["operation_key"] for row in bindings)
            projection = await client.get("/api/runs/target/inputs")
            assert projection.status_code == 200, projection.text
            assert len(projection.json()["questions"]) == 2
        if entry == "host_lease_expired":
            async with transaction() as conn:
                await conn.execute("update sandbox_leases set expires_at=clock_timestamp()-interval '1 second' where run_id='target'")
            response = await client.post("/api/runs/target/inputs", json={"input_id": str(uuid4()), "question_id": questions[0], "answers": {"q0": "o1"}})
            assert response.status_code == 409, response.text
            async with transaction() as conn:
                rows = await (await conn.execute("select verified_outcome from execution_recovery_operations")).fetchall()
                assert all(row["verified_outcome"] == "unknown" for row in rows)
            return
        async with transaction() as conn:
            rows = await (await conn.execute("select questions from run_input_questions order by created_at")).fetchall()
            texts = [row["questions"][0]["question"] for row in rows]
            assert sum("华东项目" in text and "华南项目" not in text for text in texts) == 1
            assert sum("华南项目" in text and "华东项目" not in text for text in texts) == 1
            assert not any(value in repr(texts) for value in ("private-native", "rop_", "unselected", "/tmp/", "vendor", "password"))
        if entry == "host_unresolved":
            from app.bootstrap.conversation_continuation import _settle_stopped_continuation
            from app.bootstrap.run_attempt_lifecycle import build_run_attempt_lifecycle_service
            async with transaction() as conn:
                await callback(conn, "target", target, "load")
                await callback(conn, "target", target, "append", [{"type": "assistant", "uuid": "no-write", "message": {"role": "assistant", "content": [{"type": "text", "text": "No external call performed"}]}}], 3)
                await conn.execute("alter table messages add column created_at timestamptz not null default now()")
                await conn.execute("insert into messages(id,tenant_id,session_id,run_id,role,content,created_at) values('assistant-target','tenant','session','target','assistant','No external call performed',now()+interval '1 second')")
                await provider_epochs.commit_provider_turn(conn, tenant_id="tenant", run_id="target", attempt_id="attempt-target",
                    assistant_message_id="assistant-target", final_sequence=3)
                result = await _settle_stopped_continuation(conn, tenant_id="tenant", run_id="target", attempt_id="attempt-target", final_sequence=3,
                    workspace_receipt={**receipt, "scope": {**receipt["scope"], "run_id": "target", "attempt_id": "attempt-target"}},
                    stop_evidence="provider_stopped", sdk_settlement=dict(owner_generation=2, input_accepted=True, mirror_complete=True), external_effect_unknown=False)
                assert result["state"] == "recovery_candidate" and result["reason_code"] == "external_effect_unknown"
                assert result["operation_sources"] == [{"source_run_id": "source", "source_attempt_id": "attempt-source", "provider_epoch_id": frozen["provider_epoch_id"], "final_sequence": 2}]
                await conn.execute("update runs set status='succeeded' where id='target'")
                terminal = await build_run_attempt_lifecycle_service().terminalize(
                    conn, tenant_id="tenant", run_id="target", attempt_id="attempt-target",
                    status="succeeded", terminal_reason="synthetic_no_external_call",
                )
                assert terminal["status"] == "succeeded" and terminal["owner_generation"] == 3
                next_frozen = await prepare(conn, "next", 3)
                assert next_frozen["recovery"]["state"] == "recovery_candidate"
                assert next_frozen["recovery"]["operation_sources"] == result["operation_sources"]
                await conn.execute("update run_attempts set execution_spec_json=jsonb_set(execution_spec_json,'{input}',%s) where id='attempt-next'", (Jsonb({"_runtime_tool_policy_subjects": [subject]}),))
                await conn.execute("update sandbox_leases set lease_payload_json=lease_payload_json || %s where run_id='next'", (Jsonb({"attempt_id": "attempt-next", "callback_token_id": "cbt:next:attempt-next:g2"}),))
            response = await host_start("next")
            assert response.status_code == 200 and response.json()["recovery_confirmation_pending"], response.text
            async with transaction() as conn:
                bindings = await (await conn.execute("select operation_key from execution_recovery_invocations where run_id='next'")).fetchall()
                old = await (await conn.execute("select operation_key from execution_recovery_invocations where run_id='target'")).fetchall()
                assert {row["operation_key"] for row in bindings} == {row["operation_key"] for row in old}
            return
        # Foreign owner cannot answer; the exact authenticated answer changes only A.
        app.dependency_overrides[require_principal] = lambda: AuthPrincipal(tenant_id="tenant", user_id="foreign", display_name="Foreign", source="test")
        assert (await client.post("/api/runs/target/inputs", json={"input_id": str(uuid4()), "question_id": questions[0], "answers": {"q0": "o2"}})).status_code == 404
        app.dependency_overrides[require_principal] = lambda: principal
        assert (await client.post("/api/runs/target/inputs", json={"input_id": str(uuid4()), "question_id": questions[0], "answers": {"q0": "o2"}})).status_code == 200
        if entry == "native_query":
            assert (await effect("target", "private-native-a", "query")).json()["decision"] == "allow"
            assert (await effect("target", "private-native-b", "query")).json()["decision"] == "wait"
            assert (await effect("target", "private-native-a", "started")).status_code == 200
            assert (await effect("target", "private-native-a", "query")).json()["reason_code"] == "operation_already_started"
        else:
            partial = (await host_start()).json()
            assert partial["recovery_confirmation_pending"]
            assert len(partial["recovery_operation_facts"]) == 1
            assert partial["recovery_operation_facts"][0]["outcome"] == "not_effective"
            assert (await effect("target", "brand-new-native", "started")).status_code == 409
        assert (await client.post("/api/runs/target/inputs", json={"input_id": str(uuid4()), "question_id": questions[1], "answers": {"q0": "o1"}})).status_code == 200
        if entry == "native_query":
            assert (await effect("target", "private-native-b", "query")).json()["reason_code"] == "operation_already_completed"
            assert (await effect("target", "private-native-b", "started")).status_code == 409
        else:
            resolved = (await host_start()).json()
            assert not resolved["recovery_confirmation_pending"]
            facts = resolved["recovery_operation_facts"]
            assert sorted(fact["outcome"] for fact in facts) == ["not_effective", "succeeded"]
            assert all(set(fact) == {"public_summary", "outcome", "provenance"}
                       and fact["provenance"] == "authenticated_owner_confirmation" for fact in facts)
            assert all(any(name in fact["public_summary"] for name in ("华东项目", "华南项目")) for fact in facts)
            assert not any(value in repr(facts) for value in ("private-native", "rop_", "unselected", "/tmp/", "vendor", "password"))
            assert (await effect("target", "brand-new-native", "query")).json()["decision"] == "deny"
            assert (await effect("target", "brand-new-native", "started")).status_code == 409
            async with transaction() as conn:
                rows = await (await conn.execute("select verified_outcome from execution_recovery_operations")).fetchall()
                assert sorted(row["verified_outcome"] for row in rows) == ["not_effective", "succeeded"]
                rows = await (await conn.execute("select started, \"authorization\" from execution_recovery_invocations where binding_kind='source_operation'")).fetchall()
                assert all(not row["started"] and row["authorization"] == "hold" for row in rows)
        # Manual history was acknowledged by the host and is never model/tool_result input.
        async with transaction() as conn:
            rows = await (await conn.execute("select status from run_inputs")).fetchall()
            assert [row["status"] for row in rows] == ["applied", "applied"]
            await conn.execute("update tenant_capability_distributions set metadata_json='{}'")
        if entry == "host_startup":
            assert (await host_start()).json()["recovery_operation_facts"] == []
        denied = await effect("target", "private-native-a", "query")
        assert denied.json()["decision"] == "deny" and "question_id" not in denied.json()
