import argparse
import ast
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _function(path: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse((ROOT / path).read_text())
    matches = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    if len(matches) != 1:
        raise RuntimeError(f"{path}:{name}:missing_or_ambiguous")
    return matches[0]


def _method(
    path: str,
    class_name: str,
    name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse((ROOT / path).read_text())
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    ]
    if len(classes) != 1:
        raise RuntimeError(f"{path}:{class_name}:missing_or_ambiguous")
    matches = [
        node
        for node in classes[0].body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    if len(matches) != 1:
        raise RuntimeError(f"{path}:{class_name}.{name}:missing_or_ambiguous")
    return matches[0]


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _dotted_name(node.value)
        return f"{owner}.{node.attr}" if owner else node.attr
    return None


class _DirectCallVisitor(ast.NodeVisitor):
    def __init__(self, root: ast.AST) -> None:
        self.root = root
        self.calls: list[tuple[str, int]] = []

    def visit_Call(self, node: ast.Call) -> None:
        name = _dotted_name(node.func)
        if name is not None:
            self.calls.append((name, node.lineno))
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node is self.root:
            self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node is self.root:
            self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


def _calls(node: ast.AST) -> list[tuple[str, int]]:
    visitor = _DirectCallVisitor(node)
    visitor.visit(node)
    return visitor.calls


def _all_calls(node: ast.AST) -> list[tuple[str, int]]:
    return [
        (name, candidate.lineno)
        for candidate in ast.walk(node)
        if isinstance(candidate, ast.Call)
        and (name := _dotted_name(candidate.func)) is not None
    ]


def _unique_call_line(
    calls: list[tuple[str, int]],
    *,
    qualified_name: str,
) -> int | None:
    lines = [line for name, line in calls if name == qualified_name]
    return lines[0] if len(lines) == 1 else None


def _nested_function(
    node: ast.AST,
    name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    matches = [
        candidate
        for candidate in ast.walk(node)
        if candidate is not node
        and isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef))
        and candidate.name == name
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"{getattr(node, 'name', '<node>')}:{name}:missing_or_ambiguous"
        )
    return matches[0]


def _redis_append_inside_transaction(node: ast.AST) -> list[int]:
    bridge_names = {"bridge", "stream_bridge"}
    for candidate in ast.walk(node):
        if not isinstance(candidate, (ast.Assign, ast.AnnAssign)):
            continue
        value = candidate.value
        if not isinstance(value, ast.Call) or not (
            _dotted_name(value.func) or ""
        ).endswith("RedisStreamBridge"):
            continue
        targets = (
            candidate.targets
            if isinstance(candidate, ast.Assign)
            else [candidate.target]
        )
        bridge_names.update(
            target.id for target in targets if isinstance(target, ast.Name)
        )
    failures: list[int] = []
    for candidate in ast.walk(node):
        if not isinstance(candidate, ast.AsyncWith):
            continue
        context_calls = [
            _dotted_name(item.context_expr.func)
            for item in candidate.items
            if isinstance(item.context_expr, ast.Call)
        ]
        if not any(
            (name or "").rsplit(".", 1)[-1] in {"transaction", "transaction_factory"}
            for name in context_calls
        ):
            continue
        for call in (
            item for item in ast.walk(candidate) if isinstance(item, ast.Call)
        ):
            name = _dotted_name(call.func)
            if name is None:
                continue
            parts = name.split(".")
            direct_bridge_append = (
                len(parts) >= 2 and parts[-1] == "append" and parts[-2] in bridge_names
            )
            publisher_redis_call = (
                len(parts) >= 2
                and parts[-2] == "stream_publisher"
                and parts[-1]
                in {"open", "publish_committed_event"}
            )
            if direct_bridge_append or publisher_redis_call:
                failures.append(call.lineno)
    return failures


def _assistant_delta_ownership_failures(
    *,
    worker_source: str,
    redis_source: str,
    callback_source: str,
    executor_source: str,
    adr_source: str,
) -> list[str]:
    failures: list[str] = []
    if "publish_assistant_delta" in worker_source or "publish_assistant_delta" in redis_source:
        failures.append("worker direct assistant-delta publisher exists")
    if "raise WorkerDirectAssistantDeltaError" not in worker_source:
        failures.append("worker assistant-delta ingress does not fail closed")
    if "canonical_assistant_delta_event" not in callback_source or "await bridge.append(" not in callback_source:
        failures.append("runtime callback is not the declared assistant-delta ingress")
    forbidden_executor_dependencies = (
        "RedisStreamBridge",
        "redis.asyncio",
        "psycopg",
        "app.db",
        "app.repositories",
    )
    if any(value in executor_source for value in forbidden_executor_dependencies):
        failures.append("sandbox executor has a direct database or Redis transport dependency")
    required_adr_statements = (
        "assistant_text_delta` has one ingress",
        "Adding any second assistant-text ingress requires a new accepted ADR",
        "one logical delta cannot be published by both owners",
    )
    if any(value not in adr_source for value in required_adr_statements):
        failures.append("ADR 0009 does not freeze assistant-delta producer ownership")
    return failures


def _strip_typescript_comments(source: str) -> str:
    result: list[str] = []
    index = 0
    quote: str | None = None
    while index < len(source):
        char = source[index]
        pair = source[index : index + 2]
        if quote is not None:
            result.append(char)
            if char == "\\" and index + 1 < len(source):
                index += 1
                result.append(source[index])
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            result.append(char)
            index += 1
            continue
        if pair == "//":
            end = source.find("\n", index)
            if end < 0:
                break
            result.append("\n")
            index = end + 1
            continue
        if pair == "/*":
            end = source.find("*/", index + 2)
            if end < 0:
                raise RuntimeError("typescript:unterminated_comment")
            result.extend("\n" for char in source[index : end + 2] if char == "\n")
            index = end + 2
            continue
        result.append(char)
        index += 1
    return "".join(result)


def _balanced_region(source: str, start: int, opening: str, closing: str) -> str:
    if start >= len(source) or source[start] != opening:
        raise RuntimeError("typescript:balanced_region_missing")
    depth = 0
    quote: str | None = None
    index = start
    while index < len(source):
        char = source[index]
        if quote is not None:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return source[start + 1 : index]
        index += 1
    raise RuntimeError("typescript:unbalanced_region")


def _typescript_function_body(source: str, declaration: str) -> str:
    positions = [match.start() for match in re.finditer(re.escape(declaration), source)]
    if len(positions) != 1:
        raise RuntimeError(f"sseConnection.ts:{declaration}:missing_or_ambiguous")
    tail = source[positions[0] + len(declaration) :]
    if declaration.startswith("export async function"):
        body_match = re.search(r"\)\s*:\s*Promise<[^>]+>\s*\{", tail)
    else:
        body_match = re.search(r"=>\s*\{", tail)
    if body_match is None:
        raise RuntimeError(f"sseConnection.ts:{declaration}:body_missing")
    opening = positions[0] + len(declaration) + body_match.end() - 1
    return _balanced_region(source, opening, "{", "}")


def _typescript_call_arguments(source: str, callee: str) -> list[str]:
    pattern = re.compile(rf"\b{re.escape(callee)}\s*\(")
    return [
        _balanced_region(source, match.end() - 1, "(", ")")
        for match in pattern.finditer(source)
    ]


def _nginx_sse_contract_failures(source: str) -> list[str]:
    marker = "location ~ ^/api/chat/sessions/[A-Za-z0-9_-]+/stream$ {"
    if marker not in source:
        return ["nginx.conf.template:sse_location_missing"]
    block = source.split(marker, 1)[1].split("\n    }", 1)[0]
    required = (
        'proxy_set_header Connection "";',
        'proxy_set_header Accept-Encoding "";',
        "proxy_buffering off;",
        "proxy_request_buffering off;",
        "proxy_cache off;",
        "gzip off;",
        'add_header Cache-Control "no-cache, no-transform" always;',
        "proxy_read_timeout ${AI_PLATFORM_FRONTEND_PROXY_READ_TIMEOUT};",
        "proxy_send_timeout ${AI_PLATFORM_FRONTEND_PROXY_SEND_TIMEOUT};",
    )
    return [
        f"nginx.conf.template:sse_directive_missing:{directive}"
        for directive in required
        if directive not in block
    ]


def check() -> list[str]:
    failures: list[str] = []
    chat_stream = _function("app/routes/lambchat_compat.py", "chat_session_stream")
    for name, line in _all_calls(chat_stream):
        final_name = name.rsplit(".", 1)[-1]
        if final_name in {"list_run_events", "event_page", "sleep", "read", "xread"}:
            failures.append(f"lambchat_compat.py:{line}:retired_live_call:{final_name}")
    chat_calls = _calls(chat_stream)
    live_order = [
        _unique_call_line(chat_calls, qualified_name=name)
        for name in (
            "runtime.hub.subscribe",
            "bridge.resolve_resume",
            "bridge.retained_bounds",
        )
    ]
    if (
        any(line is None for line in live_order)
        or live_order != sorted(live_order)  # type: ignore[arg-type]
    ):
        failures.append("lambchat_compat.py:subscribe_before_replay_unproven")

    redis_source = (ROOT / "app/streaming/redis.py").read_text(encoding="utf-8")
    if "async def read(" in redis_source or ".xread(" in redis_source:
        failures.append("redis.py:retired_xread_live_path_present")
    if "XADD" not in redis_source or "PUBLISH" not in redis_source:
        failures.append("redis.py:atomic_stream_publish_script_missing")
    for retired_marker in (
        "ai-platform-stream-open-v2.1",
        "ai-platform-stream-terminal-v2.1",
        "ai-platform:sse:v2.1",
        "ai-platform.stream-event.v2.1",
    ):
        if retired_marker in redis_source:
            failures.append(f"redis.py:retired_v21_marker:{retired_marker}")
    for required_marker in (
        "ai-platform-stream-open-v3",
        "ai-platform-stream-terminal-v3",
    ):
        if required_marker not in redis_source:
            failures.append(f"redis.py:v3_semantic_id_marker_missing:{required_marker}")

    failures.extend(
        _assistant_delta_ownership_failures(
            worker_source=(ROOT / "app/worker.py").read_text(encoding="utf-8"),
            redis_source=redis_source,
            callback_source=(ROOT / "app/routes/runtime_callbacks.py").read_text(
                encoding="utf-8"
            ),
            executor_source=(
                ROOT / "app/runtime/sandbox/executor_app.py"
            ).read_text(encoding="utf-8"),
            adr_source=(
                ROOT / "docs/adr/0009-redis-streams-sse-v3-live-fanout.md"
            ).read_text(encoding="utf-8"),
        )
    )

    worker = _function("app/worker.py", "process_run_payload")
    worker_calls = _calls(worker)
    required = {
        "prepare": "stream_publisher.prepare",
        "open": "stream_publisher.open",
        "confirm": "stream_publisher.confirm",
        "dispatch": "_submit_run_until_cancelled",
    }
    positions = {
        label: _unique_call_line(worker_calls, qualified_name=qualified_name)
        for label, qualified_name in required.items()
    }
    ordered = [positions[label] for label in ("prepare", "open", "confirm", "dispatch")]
    prepare_calls = {
        name
        for name, _ in _calls(
            _method("app/streaming/redis.py", "RunStreamPublisher", "prepare")
        )
    }
    confirm_calls = {
        name
        for name, _ in _calls(
            _method("app/streaming/redis.py", "RunStreamPublisher", "confirm")
        )
    }
    if (
        any(line is None for line in ordered)
        or ordered != sorted(ordered)  # type: ignore[arg-type]
        or "create_or_get_stream_admission" not in prepare_calls
        or "confirm_stream_admission" not in confirm_calls
    ):
        failures.append("worker.py:sse_admission_not_before_sdk_dispatch")

    callback = _function("app/routes/runtime_callbacks.py", "record_executor_callback")
    for line in _redis_append_inside_transaction(callback):
        failures.append(
            f"runtime_callbacks.py:{line}:redis_append_inside_pg_transaction"
        )
    for line in _redis_append_inside_transaction(worker):
        failures.append(f"worker.py:{line}:redis_append_inside_pg_transaction")

    event_sink = _nested_function(worker, "event_sink")
    event_sink_calls = _calls(event_sink)
    producer = _function(
        "app/streaming/worker_projection.py",
        "persist_and_publish_worker_event",
    )
    publisher = _function(
        "app/streaming/worker_projection.py",
        "publish_committed_run_event",
    )
    for line in _redis_append_inside_transaction(producer):
        failures.append(
            f"worker_projection.py:{line}:redis_append_inside_pg_transaction"
        )
    producer_calls = _calls(producer)
    publisher_calls = _calls(publisher)
    producer_positions = {
        "persist": _unique_call_line(
            producer_calls,
            qualified_name="repositories.append_event",
        ),
        "cancel": _unique_call_line(
            producer_calls,
            qualified_name="repositories.is_cancel_requested",
        ),
        "handoff": _unique_call_line(
            producer_calls,
            qualified_name="publish_committed_run_event",
        ),
    }
    publisher_positions = {
        "run": _unique_call_line(
            publisher_calls,
            qualified_name="repositories.get_run_identity",
        ),
        "refresh": _unique_call_line(
            publisher_calls,
            qualified_name="stream_publisher.refresh",
        ),
        "publish": _unique_call_line(
            publisher_calls,
            qualified_name="stream_publisher.publish_committed_event",
        ),
    }
    sink_handoff = _unique_call_line(
        event_sink_calls,
        qualified_name="persist_and_publish_worker_event",
    )
    producer_order = [
        producer_positions[label] for label in ("persist", "cancel", "handoff")
    ]
    publisher_order = [
        publisher_positions[label] for label in ("run", "refresh", "publish")
    ]
    if (
        sink_handoff is None
        or any(line is None for line in producer_order + publisher_order)
        or producer_order != sorted(producer_order)  # type: ignore[arg-type]
        or publisher_order != sorted(publisher_order)  # type: ignore[arg-type]
    ):
        failures.append("worker.py:committed_semantic_producer_unwired")

    frontend = _strip_typescript_comments(
        (ROOT / "frontend/web/src/hooks/useAgent/sseConnection.ts").read_text()
    )
    connect = _typescript_function_body(frontend, "export async function connectToSSE")
    reconnect = _typescript_function_body(
        frontend, "export async function reconnectSSE"
    )
    for retired in ("event.id ||", "uuid()"):
        if retired in connect or retired in reconnect:
            failures.append(f"sseConnection.ts:retired_live_cursor:{retired}")
    if connect.count('headers["Last-Event-ID"] = acceptedCursor.eventId') != 1:
        failures.append("sseConnection.ts:last_event_id_not_from_accepted_cursor")
    commit = _typescript_function_body(connect, "const commitAcceptedStreamEvent")
    handle_calls = _typescript_call_arguments(connect, "handleStreamEvent")
    cursor_assignment = "ctx.acceptedStreamCursorRef.current ="
    if (
        len(handle_calls) != 1
        or not re.search(r",\s*commitAcceptedStreamEvent\s*,?\s*$", handle_calls[0])
        or commit.count(cursor_assignment) != 1
        or connect.count(cursor_assignment) != 1
    ):
        failures.append("sseConnection.ts:cursor_not_bound_to_reducer_commit")
    if connect.count("adaptPublicRunStreamEventV3(") != 1:
        failures.append("sseConnection.ts:v3_generated_adapter_not_unique")
    event_handlers = (
        ROOT / "frontend/web/src/hooks/useAgent/eventHandlers.ts"
    ).read_text(encoding="utf-8")
    if "approval_required" in event_handlers:
        failures.append("eventHandlers.ts:retired_runtime_approval_present")
    nginx = (ROOT / "frontend/web/nginx.conf.template").read_text(encoding="utf-8")
    failures.extend(_nginx_sse_contract_failures(nginx))
    return failures


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", default="full", choices=("full",))
    return parser.parse_args(argv)


if __name__ == "__main__":
    _parse_args(sys.argv[1:])
    errors = check()
    print("\n".join(errors) if errors else "SSE v3 cutover check passed")
    sys.exit(bool(errors))
