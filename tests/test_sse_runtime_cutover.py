import ast

from tools import check_sse_runtime_cutover as cutover


def test_release_atomic_cutover_has_no_pg_live_reader_or_predispatch_sdk():
    assert cutover.check() == []


def test_checker_detects_a_retired_pg_live_call(monkeypatch):
    original = cutover._calls

    def calls(node: ast.AST):
        values = original(node)
        return [*values, ("list_run_events", 99)] if getattr(node, "name", "") == "chat_session_stream" else values

    monkeypatch.setattr(cutover, "_calls", calls)
    assert "lambchat_compat.py:99:retired_live_call:list_run_events" in cutover.check()
