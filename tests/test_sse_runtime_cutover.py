import ast

from tools import check_sse_runtime_cutover as cutover


def test_release_atomic_cutover_has_no_pg_live_reader_or_predispatch_sdk():
    assert cutover.check() == []


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


def test_frontend_structure_ignores_noop_markers_outside_the_real_connect_body():
    source = """
const commitAcceptedStreamEvent = () => {
  fake.acceptedStreamCursorRef.current = null;
};
export async function connectToSSE(): Promise<void> {
  const commitAcceptedStreamEvent = (semanticApplied: boolean) => {
    ctx.acceptedStreamCursorRef.current = accepted;
  };
  handleStreamEvent(event, commitAcceptedStreamEvent);
}
"""

    connect = cutover._typescript_function_body(
        source,
        "export async function connectToSSE",
    )
    commit = cutover._typescript_function_body(
        connect,
        "const commitAcceptedStreamEvent",
    )
    calls = cutover._typescript_call_arguments(connect, "handleStreamEvent")

    assert "fake.acceptedStreamCursorRef" not in connect
    assert commit.count("ctx.acceptedStreamCursorRef.current =") == 1
    assert calls == ["event, commitAcceptedStreamEvent"]
