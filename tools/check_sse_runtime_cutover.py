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
                in {"open", "publish_assistant_delta", "publish_committed_event"}
            )
            if direct_bridge_append or publisher_redis_call:
                failures.append(call.lineno)
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


def check() -> list[str]:
    failures: list[str] = []
    chat_stream = _function("app/routes/lambchat_compat.py", "chat_session_stream")
    for name, line in _calls(chat_stream):
        final_name = name.rsplit(".", 1)[-1]
        if final_name in {"list_run_events", "event_page", "sleep"}:
            failures.append(f"lambchat_compat.py:{line}:retired_live_call:{final_name}")

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
    return failures


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", default="full", choices=("full",))
    return parser.parse_args(argv)


if __name__ == "__main__":
    _parse_args(sys.argv[1:])
    errors = check()
    print("\n".join(errors) if errors else "SSE v2.1 cutover check passed")
    sys.exit(bool(errors))
