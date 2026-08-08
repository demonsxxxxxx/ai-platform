import ast
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def _function(path: str, name: str) -> ast.AST:
    tree = ast.parse((ROOT / path).read_text())
    matches = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
    if len(matches) != 1:
        raise RuntimeError(f"{path}:{name}:missing_or_ambiguous")
    return matches[0]


def _calls(node: ast.AST) -> list[tuple[str, int]]:
    return [((call.func.attr if isinstance(call.func, ast.Attribute) else call.func.id), call.lineno) for call in ast.walk(node) if isinstance(call, ast.Call) and isinstance(call.func, (ast.Attribute, ast.Name))]


def check() -> list[str]:
    failures: list[str] = []
    for name, line in _calls(_function("app/routes/lambchat_compat.py", "chat_session_stream")):
        if name in {"list_run_events", "event_page", "sleep"}:
            failures.append(f"lambchat_compat.py:{line}:retired_live_call:{name}")
    positions = {name: line for name, line in _calls(_function("app/worker.py", "process_run_payload")) if name in {"prepare", "open", "confirm", "_submit_run_until_cancelled"}}
    publisher_calls = {
        "prepare": {name for name, _ in _calls(_function("app/streaming/redis.py", "prepare"))},
        "confirm": {name for name, _ in _calls(_function("app/streaming/redis.py", "confirm"))},
    }
    if set(positions) != {"prepare", "open", "confirm", "_submit_run_until_cancelled"} or not positions["prepare"] < positions["open"] < positions["confirm"] < positions["_submit_run_until_cancelled"] or "create_or_get_stream_admission" not in publisher_calls["prepare"] or "confirm_stream_admission" not in publisher_calls["confirm"]:
        failures.append("worker.py:sse_admission_not_before_sdk_dispatch")
    frontend = (ROOT / "frontend/web/src/hooks/useAgent/sseConnection.ts").read_text()
    for retired in ("event.id ||", "uuid()", "await queryAuthoritativeRunStatus({"):
        if retired in frontend[frontend.index("export async function reconnectSSE"):]:
            failures.append(f"sseConnection.ts:retired_live_cursor:{retired}")
    return failures


if __name__ == "__main__":
    errors = check()
    print("\n".join(errors) if errors else "SSE v2.1 cutover check passed")
    sys.exit(bool(errors))
