import ast

from tools import check_sse_runtime_cutover as cutover


def test_release_atomic_cutover_has_no_pg_live_reader_or_predispatch_sdk():
    assert cutover.check() == []


def test_release_atomic_cutover_rejects_generated_v4_contract_drift(monkeypatch):
    monkeypatch.setattr(
        cutover.generate_sse_v4_contracts,
        "generate",
        lambda *, check: ["generated/publicRunStreamV4.ts differs"] if check else [],
    )

    assert "public_run_stream_v4:generated/publicRunStreamV4.ts differs" in (
        cutover.check()
    )


def test_release_atomic_cutover_rejects_optional_v3_runtime_markers():
    assert cutover._retired_v3_runtime_failures(
        {"sseConnection.ts": 'import { connect } from "./publicRunStreamV3";'}
    ) == [
        "sseConnection.ts:retired_v3_runtime_marker:publicRunStreamV3"
    ]


def test_assistant_delta_ownership_guard_rejects_a_second_worker_ingress():
    valid = {
        "worker_source": "raise WorkerDirectAssistantDeltaError",
        "redis_source": "class RunStreamPublisher: pass",
        "callback_source": "canonical_assistant_delta_event(); await bridge.append(event)",
        "executor_source": "async def run(): pass",
        "adr_source": (
            "assistant_text_delta` has one ingress\n"
            "Adding any second assistant-text ingress requires a new accepted ADR\n"
            "one logical delta cannot be published by both owners"
        ),
    }
    assert cutover._assistant_delta_ownership_failures(**valid) == []

    failures = cutover._assistant_delta_ownership_failures(
        **{
            **valid,
            "worker_source": (
                "raise WorkerDirectAssistantDeltaError\n"
                "await stream_publisher.publish_assistant_delta(delta)"
            ),
        }
    )

    assert failures == ["worker direct assistant-delta publisher exists"]


def test_assistant_delta_ownership_guard_rejects_direct_redis_bridge_append():
    source = """
from app.streaming.redis import RedisStreamBridge
from app.streaming.sse_contract import canonical_assistant_delta_event

async def publish(redis_client):
    bridge = RedisStreamBridge(redis_client)
    event = canonical_assistant_delta_event(delta="blocked")
    await bridge.append(event)
"""

    assert cutover._worker_assistant_delta_ingress_exists(source) is True


def test_assistant_delta_ownership_guard_rejects_aliased_bridge_import():
    source = """
from app.streaming.redis import RedisStreamBridge as Bridge

async def publish(redis_client, event):
    bridge = Bridge(redis_client)
    await bridge.append(event)
"""

    assert cutover._worker_assistant_delta_ingress_exists(source) is True


def test_assistant_delta_ownership_guard_rejects_public_event_literal():
    source = """
async def publish(bridge, payload):
    await bridge.append({"event": "assistant_text_delta", "payload": payload})
"""

    assert cutover._worker_assistant_delta_ingress_exists(source) is True


def test_assistant_delta_ownership_guard_requires_an_adr_for_a_second_ingress():
    failures = cutover._assistant_delta_ownership_failures(
        worker_source="raise WorkerDirectAssistantDeltaError",
        redis_source="class RunStreamPublisher: pass",
        callback_source="canonical_assistant_delta_event(); await bridge.append(event)",
        executor_source="async def run(): pass",
        adr_source="producer ownership is unspecified",
    )

    assert failures == ["ADR 0009 does not freeze assistant-delta producer ownership"]


def test_checker_detects_a_retired_pg_live_call(monkeypatch):
    original = cutover._all_calls

    def all_calls(node: ast.AST):
        values = original(node)
        return (
            [*values, ("list_run_events", 99)]
            if getattr(node, "name", "") == "chat_session_stream"
            else values
        )

    monkeypatch.setattr(cutover, "_all_calls", all_calls)
    assert "lambchat_compat.py:99:retired_live_call:list_run_events" in cutover.check()


def test_checker_scans_retired_calls_inside_nested_live_generator():
    node = ast.parse(
        """
async def chat_session_stream():
    async def stream():
        await repositories.list_run_events()
    return stream()
"""
    ).body[0]

    assert ("repositories.list_run_events", 4) in cutover._all_calls(node)
    assert ("repositories.list_run_events", 4) not in cutover._calls(node)


def test_checker_detects_redis_append_hidden_in_nested_transaction_helper():
    node = ast.parse(
        """
async def callback():
    async with db.transaction():
        async def nested_publish():
            await stream_bridge.append(envelope)
        await nested_publish()
"""
    ).body[0]

    assert cutover._redis_append_inside_transaction(node) == [5]


def test_checker_detects_redis_append_inside_transaction_factory_wrapper():
    node = ast.parse(
        """
async def producer(transaction_factory):
    async with transaction_factory():
        await stream_publisher.publish_committed_event(event)
"""
    ).body[0]

    assert cutover._redis_append_inside_transaction(node) == [4]


def test_checker_detects_active_v4_transport_inside_transaction():
    node = ast.parse(
        """
async def admit(transaction):
    async with transaction():
        await capabilities.publication_transport.publish(payload)
"""
    ).body[0]

    assert cutover._redis_append_inside_transaction(node) == [4]


def test_checker_detects_active_v4_application_publisher_inside_transaction():
    node = ast.parse(
        """
async def callback(transaction):
    async with transaction():
        await publish_pending_v4_events(capabilities)
"""
    ).body[0]

    assert cutover._redis_append_inside_transaction(node) == [4]


def test_checker_requires_dedicated_sse_nginx_contract():
    source = """
location ~ ^/api/chat/sessions/[A-Za-z0-9_-]+/stream$ {
    proxy_set_header Connection "";
}
"""

    failures = cutover._nginx_sse_contract_failures(source)

    assert any("Accept-Encoding" in failure for failure in failures)
    assert any("proxy_buffering off" in failure for failure in failures)
    assert any("proxy_cache off" in failure for failure in failures)


def test_checker_rejects_commented_sse_nginx_contract():
    source = """
# location ~ ^/api/chat/sessions/[A-Za-z0-9_-]+/stream$ {
#     proxy_set_header Connection "";
#     proxy_set_header Accept-Encoding "";
#     proxy_buffering off;
#     proxy_request_buffering off;
#     proxy_cache off;
#     gzip off;
#     add_header Cache-Control "no-cache, no-transform" always;
#     proxy_read_timeout ${AI_PLATFORM_FRONTEND_PROXY_READ_TIMEOUT};
#     proxy_send_timeout ${AI_PLATFORM_FRONTEND_PROXY_SEND_TIMEOUT};
# }
"""

    assert cutover._nginx_sse_contract_failures(source) == [
        "nginx.conf.template:sse_location_missing"
    ]


def test_checker_rejects_the_retired_run_id_path_sse_location():
    source = """
location ~ ^/api/chat/sessions/[A-Za-z0-9_-]+/runs/[A-Za-z0-9_-]+/stream$ {
    proxy_set_header Connection "";
    proxy_set_header Accept-Encoding "";
    proxy_buffering off;
    proxy_request_buffering off;
    proxy_cache off;
    gzip off;
    add_header Cache-Control "no-cache, no-transform" always;
    proxy_read_timeout ${AI_PLATFORM_FRONTEND_PROXY_READ_TIMEOUT};
    proxy_send_timeout ${AI_PLATFORM_FRONTEND_PROXY_SEND_TIMEOUT};
}
"""

    assert cutover._nginx_sse_contract_failures(source) == [
        "nginx.conf.template:sse_location_missing"
    ]


def test_worker_admission_guard_accepts_multiple_terminal_branches_with_predispatch_admission():
    worker = ast.parse(
        """
async def process_run_payload():
    if terminal_before_dispatch:
        await admit_v4_stream()
        return
    await admit_v4_stream()
    await _submit_run_until_cancelled()
    if terminal_after_dispatch:
        await admit_v4_stream()
"""
    ).body[0]

    assert cutover._worker_admission_failures(worker) == []


def test_worker_admission_guard_rejects_terminal_only_admission_before_dispatch():
    worker = ast.parse(
        """
async def process_run_payload():
    if terminal_before_dispatch:
        await admit_v4_stream()
        return
    await _submit_run_until_cancelled()
"""
    ).body[0]

    assert cutover._worker_admission_failures(worker) == [
        "worker.py:v4_admission_not_before_sdk_dispatch"
    ]


def test_worker_admission_guard_rejects_only_postdispatch_admission():
    worker = ast.parse(
        """
async def process_run_payload():
    await _submit_run_until_cancelled()
    await admit_v4_stream()
"""
    ).body[0]

    assert cutover._worker_admission_failures(worker) == [
        "worker.py:v4_admission_not_before_sdk_dispatch"
    ]


def test_frontend_structure_ignores_noop_markers_outside_the_real_connect_body():
    source = """
const commitTransportCursor = () => {
  fake.acceptedStreamCursorRef.current = null;
};
export async function connectToSSE(): Promise<void> {
  const commitTransportCursor = (semanticApplied: boolean) => {
    ctx.acceptedStreamCursorRef.current = accepted;
  };
  const commitAcceptedStreamEvent = (semanticApplied: boolean) => {
    commitTransportCursor(semanticApplied);
  };
  handlePublicRunStreamFrameV4({
    frame,
    onCommitted: commitAcceptedStreamEvent,
  });
}
"""

    connect = cutover._typescript_function_body(
        source,
        "export async function connectToSSE",
    )
    transport_commit = cutover._typescript_function_body(
        connect,
        "const commitTransportCursor",
    )
    calls = cutover._typescript_call_arguments(
        connect,
        "handlePublicRunStreamFrameV4",
    )

    assert "fake.acceptedStreamCursorRef" not in connect
    assert transport_commit.count("ctx.acceptedStreamCursorRef.current =") == 1
    assert "onCommitted: commitAcceptedStreamEvent" in calls[0]
    assert cutover._frontend_cursor_commit_failures(source) == []


def test_frontend_structure_rejects_cursor_commit_outside_reducer_callback():
    source = """
export async function connectToSSE(): Promise<void> {
  const commitTransportCursor = (semanticApplied: boolean) => {
    ctx.acceptedStreamCursorRef.current = accepted;
  };
  const commitAcceptedStreamEvent = (semanticApplied: boolean) => {};
  handlePublicRunStreamFrameV4({
    frame,
    onCommitted: commitAcceptedStreamEvent,
  });
  commitTransportCursor(true);
}
"""

    assert cutover._frontend_cursor_commit_failures(source) == [
        "sseConnection.ts:cursor_not_bound_to_reducer_commit"
    ]
