"""Full-schema platform entry fixture; no Run, Attempt or Context rows are seeded."""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
import os
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest_asyncio
from starlette.requests import Request

from app.auth import AuthPrincipal
from app.schema_migrations import schema_sql
from app.bootstrap.context import configure_context_services
from app.bootstrap.model_services import configure_model_services
from app.bootstrap.run_attempt_lifecycle import build_run_attempt_lifecycle_service
from app.bootstrap import run_lifecycle
from app.bootstrap.run_delivery_intents import build_run_delivery_intents
from app.bootstrap.streaming import build_worker_v4_capabilities
from app.models import ChatStreamRequest, QueueRunPayload
from app.queue import QueueAdmissionMetadata, message_id_for_raw
from app.routes import chat
from app.settings import Settings


class PublicationSink:
    """Only Redis delivery is substituted; authority and events remain PostgreSQL-owned."""

    async def publish(self, _payload):
        return "1-0"


class SyntheticQueueTransport:
    """In-memory Redis boundary used beneath the production delivery-intent adapter."""

    def __init__(self):
        self._messages: dict[str, tuple[str, QueueAdmissionMetadata]] = {}

    @staticmethod
    def _identity(payload):
        validated = QueueRunPayload.model_validate(payload)
        payload_json = validated.model_dump_json()
        return validated, payload_json, message_id_for_raw(payload_json)

    async def enqueue_run_with_metadata(self, payload):
        _validated, payload_json, message_id = self._identity(payload)
        existing = self._messages.get(message_id)
        if existing is not None:
            return existing[1]
        ordinal = len(self._messages) + 1
        metadata = QueueAdmissionMetadata(
            queue_position=ordinal,
            queue_admission_ordinal=ordinal,
            message_id=message_id,
            source="synthetic-transport",
        )
        self._messages[message_id] = (payload_json, metadata)
        return metadata

    async def read_queue_admission(self, payload):
        _validated, _payload_json, message_id = self._identity(payload)
        existing = self._messages.get(message_id)
        return existing[1] if existing is not None else None

    def message_for_run(self, run_id: str) -> tuple[dict, str, QueueAdmissionMetadata]:
        matches = [
            (payload_json, metadata)
            for payload_json, metadata in self._messages.values()
            if QueueRunPayload.model_validate_json(payload_json).run_id == run_id
        ]
        if len(matches) != 1:
            raise AssertionError(f"synthetic_queue_run_count:{run_id}:{len(matches)}")
        payload_json, metadata = matches[0]
        payload = QueueRunPayload.model_validate_json(payload_json).model_dump(mode="json")
        return payload, payload_json, metadata


@pytest_asyncio.fixture
async def platform_entry(monkeypatch, tmp_path):
    import pytest
    from app.bootstrap import run_delivery_intents as run_delivery_intents_bootstrap
    from app.bootstrap import worker_dispatch_admission, worker_dispatch_binding, streaming
    from app.execution.application import model_control_plane
    from app.runs.application import model_snapshot, execution_spec
    from app.context.application import provider_sessions

    dsn = os.getenv("AI_PLATFORM_S0A_SCHEMA_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("AI_PLATFORM_S0A_SCHEMA_TEST_DSN is not configured")
    schema = "lifecycle_entry_" + uuid.uuid4().hex
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)

    @asynccontextmanager
    async def transaction():
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
            await conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema)))
            async with conn.transaction():
                yield conn

    # Configure process-owned services normally, and restore their prior bindings on teardown.
    for module, name in ((model_control_plane, "_service"),
                         (model_snapshot, "_service"),
                         (execution_spec, "_dispatch_run_facts_loader"),
                         (provider_sessions, "_use_cases")):
        if hasattr(module, name):
            monkeypatch.setattr(module, name, getattr(module, name))
    settings = Settings(_env_file=None, ai_session_secret="synthetic-entry-authority-secret",
                        sandbox_callback_token="synthetic-entry-callback-secret",
                        sandbox_workspace_root=str(tmp_path / "host-workspaces"),
                        run_delivery_intents_enabled=True)
    for module in (chat, run_lifecycle, worker_dispatch_admission, worker_dispatch_binding, streaming):
        if hasattr(module, "get_settings"):
            monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(chat, "transaction", transaction)
    monkeypatch.setattr(run_lifecycle, "transaction", transaction)
    configure_context_services()
    configure_model_services()
    lifecycle = run_lifecycle.build_run_lifecycle_service()
    attempts = build_run_attempt_lifecycle_service()
    capabilities = build_worker_v4_capabilities(None, transaction, lifecycle)
    capabilities = type(capabilities)(capabilities.pending_admissions, capabilities.event_persistence, PublicationSink())
    queue_transport = SyntheticQueueTransport()
    monkeypatch.setattr(run_delivery_intents_bootstrap, "enqueue_run_with_metadata",
                        queue_transport.enqueue_run_with_metadata)
    monkeypatch.setattr(run_delivery_intents_bootstrap, "read_queue_admission",
                        queue_transport.read_queue_admission)
    delivery_intents = build_run_delivery_intents(
        settings, transaction, lifecycle=lifecycle, capabilities=capabilities,
    )
    principal = AuthPrincipal(user_id="synthetic-user", tenant_id="default", display_name="Synthetic User")

    # Substitute only the current company authorization lookup, never the locked Run check.
    async def current_principal(**_kwargs):
        return principal
    monkeypatch.setattr(worker_dispatch_admission, "resolve_current_principal", current_principal)
    app = SimpleNamespace(state=SimpleNamespace(run_lifecycle=lifecycle,
                          run_stream_runtime=SimpleNamespace(worker_capabilities=capabilities),
                          run_delivery_intents=delivery_intents))

    async def read_delivery_intent(run_id):
        async with transaction() as conn:
            return await (await conn.execute(
                "select * from run_delivery_intents where tenant_id=%s and run_id=%s",
                (principal.tenant_id, run_id),
            )).fetchone()

    async def admit(message, session_id=None):
        response = await chat.chat_stream(
            ChatStreamRequest(message=message, workspace_id="synthetic-workspace", session_id=session_id,
                              submission_id=str(uuid.uuid4())),
            Request({"type": "http", "app": app}), agent_id=None, principal=principal,
        )
        assert response.status == "queued", response
        payload, payload_json, metadata = queue_transport.message_for_run(response.run_id)
        assert payload["run_id"] == response.run_id
        intent = await read_delivery_intent(response.run_id)
        assert intent is not None
        assert intent["state"] == "delivered"
        assert intent["queue_payload_json"] == payload_json
        assert intent["queue_message_id"] == metadata.message_id == intent["payload_sha256"]
        return response, payload

    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
        await admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema)))
        await admin.execute(schema_sql())
        await admin.execute("insert into workspaces(id,tenant_id,name) values ('synthetic-workspace','default','Synthetic')")
        await admin.execute("insert into users(id,tenant_id,display_name) values ('synthetic-user','default','Synthetic')")
        await admin.execute("""insert into model_gateway_revisions(revision,base_url,api_key_ciphertext,key_fingerprint,active,created_by)
            values (1,'http://127.0.0.1','\\x00','0123456789abcdef',true,'synthetic-user')""")
        await admin.execute("""insert into model_catalog_entries(model_id,upstream_model_id,display_name,provider,enabled,
            upstream_available,is_default,display_order,auto_compact_window,max_output_tokens,first_seen_revision,last_seen_revision)
            values ('synthetic-model','claude-sonnet-4-5-20250929','Synthetic','compatible',true,true,true,1,100000,2048,1,1)""")
        await admin.execute("""insert into model_groups(id,tenant_id,code,name,type,default_model_id)
            values ('synthetic-chat-group','default','synthetic-chat','Synthetic Chat','business','synthetic-model')""")
        await admin.execute("""insert into model_group_members(tenant_id,group_id,user_id,is_primary)
            values ('default','synthetic-chat-group','synthetic-user',true)""")
        await admin.execute("""insert into model_group_models(group_id,model_id,enabled)
            values ('synthetic-chat-group','synthetic-model',true)""")
        yield SimpleNamespace(transaction=transaction, admit=admit, delivery_intent=read_delivery_intent,
                              principal=principal, capabilities=capabilities, lifecycle=lifecycle,
                              attempts=attempts, settings=settings)
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema)))
        await admin.close()
