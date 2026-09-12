from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.streaming import api as streaming_api
from app.streaming import redis as redis_transport
from app.streaming.domain import transport as domain_transport


def test_public_boundary_reuses_domain_values_without_legacy_wire_types() -> None:
    assert domain_transport.StreamCursor is streaming_api.StreamCursor
    assert domain_transport.StreamGap is streaming_api.StreamGap
    assert domain_transport.ResumeDecision is streaming_api.ResumeDecision
    assert domain_transport.canonical_json_bytes is streaming_api.canonical_json_bytes
    assert redis_transport.canonical_json_bytes is streaming_api.canonical_json_bytes
    assert redis_transport.StreamContractError is streaming_api.StreamContractError
    assert redis_transport.tenant_scope is streaming_api.tenant_scope
    assert not hasattr(redis_transport, "StreamEnvelope")


def test_domain_preserves_cursor_and_canonical_json_contracts() -> None:
    cursor = domain_transport.StreamCursor.parse("run-a:7:12-3", run_id="run-a")
    assert cursor.event_id == "run-a:7:12-3"
    assert domain_transport.canonical_json_bytes({"b": "\u00e9", "a": 1}) == (
        b'{"a":1,"b":"\xc3\xa9"}'
    )
    with pytest.raises(streaming_api.StreamContractError, match="stream_json_not_canonicalizable"):
        domain_transport.canonical_json_bytes({"value": float("nan")})


def test_domain_values_do_not_own_redis_transport() -> None:
    source = Path(domain_transport.__file__).read_text(encoding="utf-8")
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
    assert not hasattr(domain_transport, "_APPEND_WITH_TTL_LUA")
    assert hasattr(redis_transport, "_APPEND_WITH_TTL_LUA")
