from __future__ import annotations

import ast
from pathlib import Path

from app.streaming import contracts
from app.streaming import redis as redis_transport


def test_redis_module_reexports_wire_contracts_without_duplicate_types() -> None:
    assert redis_transport.StreamCursor is contracts.StreamCursor
    assert redis_transport.StreamEnvelope is contracts.StreamEnvelope
    assert redis_transport.StreamGap is contracts.StreamGap
    assert redis_transport.ResumeDecision is contracts.ResumeDecision
    assert redis_transport.canonical_json_bytes is contracts.canonical_json_bytes
    assert (
        redis_transport.committed_public_stream_event
        is contracts.committed_public_stream_event
    )


def test_wire_contract_module_does_not_own_redis_transport() -> None:
    source = Path(contracts.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    imported.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert not any(
        dependency == owner or dependency.startswith(f"{owner}.")
        for dependency in imported
        for owner in ("redis", "psycopg", "app.settings")
    )
    assert not hasattr(contracts, "_APPEND_WITH_TTL_LUA")
    assert hasattr(redis_transport, "_APPEND_WITH_TTL_LUA")
