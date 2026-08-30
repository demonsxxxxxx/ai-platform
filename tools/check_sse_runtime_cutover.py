import argparse
import ast
import re
import sys
from pathlib import Path

if __package__:
    from tools import generate_sse_v4_contracts
else:
    import generate_sse_v4_contracts


ROOT = Path(__file__).resolve().parents[1]

V4_PUBLICATION_CALLS = frozenset(
    {
        "admit_v4_stream",
        "finalize_parent_and_publish",
        "persist_and_publish_worker_event",
        "publish_claimed_v4_events",
        "publish_due_v4_events",
        "publish_pending_admissions",
        "publish_pending_run_terminal",
        "publish_pending_v4_admissions",
        "publish_pending_v4_events",
    }
)
V4_PUBLICATION_OWNER_MANIFEST = frozenset(
    {
        ("app/executor_reconciler.py", "_terminalize_reconciliation_failure"),
        ("app/routes/admin_runs.py", "admin_run_cancel"),
        ("app/routes/runs.py", "cancel_run"),
        ("app/routes/runtime_callbacks.py", "record_executor_callback"),
        ("app/streaming/application/durable_v4.py", "publish_claimed_v4_events"),
        ("app/streaming/application/durable_v4.py", "publish_due_v4_events"),
        ("app/streaming/application/durable_v4.py", "publish_pending_v4_admissions"),
        ("app/streaming/application/worker_publication_v4.py", "admit_v4_stream"),
        (
            "app/streaming/application/worker_publication_v4.py",
            "finalize_parent_and_publish",
        ),
        (
            "app/streaming/application/worker_publication_v4.py",
            "persist_and_publish_worker_event",
        ),
        (
            "app/streaming/application/worker_publication_v4.py",
            "publish_pending_admissions",
        ),
        (
            "app/streaming/application/worker_publication_v4.py",
            "publish_pending_run_terminal",
        ),
        (
            "app/streaming/application/worker_publication_v4.py",
            "publish_pending_v4_events",
        ),
        ("app/worker.py", "process_run_payload"),
        ("app/worker_main.py", "_terminalize_escaped_process_exception"),
        ("app/worker_main.py", "run_worker_maintenance"),
    }
)


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


def _is_v4_publication_call(name: str) -> bool:
    parts = name.split(".")
    return parts[-1] in V4_PUBLICATION_CALLS or (
        len(parts) >= 2
        and parts[-1] == "publish"
        and parts[-2] in {"transport", "publication_transport"}
    )


def _publication_owner_functions(
    root: Path,
) -> dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef]:
    owners: dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for path in sorted((root / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        candidates: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                candidates.append((node.name, node))
            elif isinstance(node, ast.ClassDef):
                candidates.extend(
                    (f"{node.name}.{method.name}", method)
                    for method in node.body
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
        relative_path = path.relative_to(root).as_posix()
        for owner_name, node in candidates:
            if any(_is_v4_publication_call(name) for name, _ in _all_calls(node)):
                owners[(relative_path, owner_name)] = node
    return owners


def _publication_owner_manifest_failures(
    owners: dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef],
) -> list[str]:
    actual = set(owners)
    return [
        f"v4_publication_owner_manifest:unlisted:{path}:{name}"
        for path, name in sorted(actual - V4_PUBLICATION_OWNER_MANIFEST)
    ] + [
        f"v4_publication_owner_manifest:stale:{path}:{name}"
        for path, name in sorted(V4_PUBLICATION_OWNER_MANIFEST - actual)
    ]


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
            active_v4_publication_call = _is_v4_publication_call(name)
            if direct_bridge_append or publisher_redis_call or active_v4_publication_call:
                failures.append(call.lineno)
    return failures


def _worker_assistant_delta_ingress_exists(source: str) -> bool:
    try:
        node = ast.parse(source)
    except SyntaxError:
        return True
    forbidden_symbols = {
        "RedisStreamBridge",
        "canonical_assistant_delta_event",
        "publish_assistant_delta",
    }
    for candidate in ast.walk(node):
        if isinstance(candidate, ast.Constant) and candidate.value == "assistant_text_delta":
            return True
        if isinstance(candidate, (ast.Name, ast.Attribute)):
            name = candidate.id if isinstance(candidate, ast.Name) else candidate.attr
            if name in forbidden_symbols:
                return True
        if isinstance(candidate, (ast.Import, ast.ImportFrom)) and any(
            alias.name.rsplit(".", 1)[-1] in forbidden_symbols
            for alias in candidate.names
        ):
            return True
    return False


def _assistant_delta_ownership_failures(
    *,
    worker_source: str,
    redis_source: str,
    callback_source: str,
    executor_source: str,
    adr_source: str,
) -> list[str]:
    failures: list[str] = []
    if (
        _worker_assistant_delta_ingress_exists(worker_source)
        or "publish_assistant_delta" in redis_source
    ):
        failures.append("worker direct assistant-delta publisher exists")
    if "raise WorkerDirectAssistantDeltaError" not in worker_source:
        failures.append("worker assistant-delta ingress does not fail closed")
    if not (
        "append_callback_v4_rows" in callback_source
        or (
            "canonical_assistant_delta_event" in callback_source
            and "await bridge.append(" in callback_source
        )
    ):
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
    uncommented = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
    marker = "location ~ ^/api/chat/sessions/[A-Za-z0-9_-]+/stream$ {"
    marker_count = uncommented.count(marker)
    if marker_count == 0:
        return ["nginx.conf.template:sse_location_missing"]
    if marker_count != 1:
        return ["nginx.conf.template:sse_location_ambiguous"]
    marker_offset = uncommented.index(marker)
    server_starts = list(
        re.finditer(r"(?m)^\s*server\s*\{", uncommented)
    )
    containing_server_index = next(
        (
            index
            for index, match in reversed(list(enumerate(server_starts)))
            if match.start() < marker_offset
        ),
        None,
    )
    if containing_server_index is None:
        server_source = uncommented
        server_marker_offset = marker_offset
    else:
        server_start = server_starts[containing_server_index].start()
        next_server_index = containing_server_index + 1
        server_end = (
            server_starts[next_server_index].start()
            if next_server_index < len(server_starts)
            else len(uncommented)
        )
        server_source = uncommented[server_start:server_end]
        server_marker_offset = marker_offset - server_start
    sse_probe_path = "/api/chat/sessions/sse-probe/stream"
    # Nginx selects the first matching regex location inside one server. Only
    # an earlier regex that also matches this request can steal it.
    for regex_match in re.finditer(
        r"(?m)^\s*location\s+~(\*)?\s+(.+?)\s*\{",
        server_source[:server_marker_offset],
    ):
        pattern = regex_match.group(2).strip('"\'')
        flags = re.IGNORECASE if regex_match.group(1) else 0
        try:
            steals_sse_request = re.search(pattern, sse_probe_path, flags) is not None
        except re.error:
            steals_sse_request = True
        if steals_sse_request:
            return ["nginx.conf.template:sse_location_precedence_ambiguous"]
    for prefix_match in re.finditer(
        r"(?m)^\s*location\s+\^~\s+([^\s{]+)",
        server_source,
    ):
        prefix = prefix_match.group(1).strip('"\'')
        if sse_probe_path.startswith(prefix):
            return ["nginx.conf.template:sse_location_precedence_ambiguous"]
    block = uncommented.split(marker, 1)[1].split("\n    }", 1)[0]
    required = (
        'proxy_set_header Connection "";',
        'proxy_set_header Accept-Encoding "";',
        "proxy_buffering off;",
        "proxy_request_buffering off;",
        "proxy_cache off;",
        "gzip off;",
        'add_header Cache-Control "no-cache, no-transform" always;',
        "add_header X-Accel-Buffering no always;",
        "proxy_read_timeout ${AI_PLATFORM_FRONTEND_PROXY_READ_TIMEOUT};",
        "proxy_send_timeout ${AI_PLATFORM_FRONTEND_PROXY_SEND_TIMEOUT};",
    )
    return [
        f"nginx.conf.template:sse_directive_missing:{directive}"
        for directive in required
        if directive not in block
    ]


def _retired_v3_runtime_failures(sources: dict[str, str]) -> list[str]:
    retired_markers = (
        "publicRunStreamV3",
        "enable_sse_v3",
        "ENABLE_SSE_V3",
    )
    return [
        f"{path}:retired_v3_runtime_marker:{marker}"
        for path, source in sources.items()
        for marker in retired_markers
        if marker in source
    ]


def _is_awaited_call_statement(statement: ast.stmt, qualified_name: str) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Await)
        and isinstance(statement.value.value, ast.Call)
        and _dotted_name(statement.value.value.func) == qualified_name
    )


def _worker_admission_failures(worker: ast.AST) -> list[str]:
    dispatch_line = _unique_call_line(
        _calls(worker),
        qualified_name="_submit_run_until_cancelled",
    )
    if dispatch_line is None:
        return ["worker.py:v4_admission_not_before_sdk_dispatch"]
    for candidate in ast.walk(worker):
        for _, value in ast.iter_fields(candidate):
            if not isinstance(value, list) or not all(
                isinstance(statement, ast.stmt) for statement in value
            ):
                continue
            for dispatch_index, statement in enumerate(value):
                if (
                    "_submit_run_until_cancelled",
                    dispatch_line,
                ) not in _calls(statement):
                    continue
                if any(
                    _is_awaited_call_statement(prior, "admit_v4_stream")
                    for prior in value[:dispatch_index]
                ):
                    return []
    return ["worker.py:v4_admission_not_before_sdk_dispatch"]


def _frontend_cursor_commit_failures(frontend: str) -> list[str]:
    connect = _typescript_function_body(frontend, "export async function connectToSSE")
    handle_calls = _typescript_call_arguments(
        connect,
        "handlePublicRunStreamFrameV4",
    )
    commit = _typescript_function_body(
        connect,
        "const commitAcceptedStreamEvent",
    )
    transport_commit = _typescript_function_body(
        connect,
        "const commitTransportCursor",
    )
    cursor_assignment = "ctx.acceptedStreamCursorRef.current ="
    if (
        len(handle_calls) != 1
        or "onCommitted: commitAcceptedStreamEvent" not in handle_calls[0]
        or "commitTransportCursor(semanticApplied)" not in commit
        or transport_commit.count(cursor_assignment) != 1
        or connect.count(cursor_assignment) != 1
    ):
        return ["sseConnection.ts:cursor_not_bound_to_reducer_commit"]
    return []


def check() -> list[str]:
    failures = [
        f"public_run_stream_v4:{failure}"
        for failure in generate_sse_v4_contracts.generate(check=True)
    ]
    chat_stream = _function("app/routes/lambchat_compat.py", "chat_session_stream")
    for name, line in _all_calls(chat_stream):
        final_name = name.rsplit(".", 1)[-1]
        if final_name in {"list_run_events", "event_page", "sleep", "read", "xread"}:
            failures.append(f"lambchat_compat.py:{line}:retired_live_call:{final_name}")
    chat_source = (ROOT / "app/routes/lambchat_compat.py").read_text(encoding="utf-8")
    subscribe_position = chat_source.find("await runtime.hub.subscribe")
    replay_position = chat_source.find("await bridge.resolve_resume")
    if (
        subscribe_position < 0
        or replay_position < 0
        or subscribe_position > replay_position
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
    failures.extend(_worker_admission_failures(worker))

    publication_owners = _publication_owner_functions(ROOT)
    failures.extend(_publication_owner_manifest_failures(publication_owners))
    for (path, owner_name), owner in sorted(publication_owners.items()):
        for line in _redis_append_inside_transaction(owner):
            failures.append(
                f"{path}:{owner_name}:{line}:redis_append_inside_pg_transaction"
            )

    event_sink = _nested_function(worker, "event_sink")
    event_sink_calls = _calls(event_sink)
    sink_handoff = _unique_call_line(
        event_sink_calls,
        qualified_name="persist_and_publish_worker_event",
    )
    if sink_handoff is None:
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
    failures.extend(_frontend_cursor_commit_failures(frontend))
    if connect.count("handlePublicRunStreamFrameV4(") != 1:
        failures.append("sseConnection.ts:v4_handler_not_unique")
    active_frontend_paths = (
        "frontend/web/src/hooks/useAgent/sseConnection.ts",
        "frontend/web/src/hooks/useAgent/eventHandlers.ts",
        "frontend/web/src/hooks/useAgent.ts",
    )
    failures.extend(
        _retired_v3_runtime_failures(
            {
                path: (ROOT / path).read_text(encoding="utf-8")
                for path in active_frontend_paths
            }
        )
    )
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
    print("\n".join(errors) if errors else "SSE v4 cutover check passed")
    sys.exit(bool(errors))
