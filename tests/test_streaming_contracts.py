from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.streaming import contracts
from app.streaming import redis as redis_transport
from app.streaming import api as streaming_api
from app.streaming.domain import transport as domain_transport


def test_redis_module_reexports_wire_contracts_without_duplicate_types() -> None:
    assert domain_transport.StreamCursor is streaming_api.StreamCursor
    assert streaming_api.StreamCursor is contracts.StreamCursor
    assert redis_transport.StreamCursor is contracts.StreamCursor
    assert domain_transport.StreamGap is streaming_api.StreamGap
    assert streaming_api.StreamGap is contracts.StreamGap
    assert redis_transport.StreamEnvelope is contracts.StreamEnvelope
    assert redis_transport.StreamGap is contracts.StreamGap
    assert domain_transport.ResumeDecision is streaming_api.ResumeDecision
    assert streaming_api.ResumeDecision is contracts.ResumeDecision
    assert redis_transport.ResumeDecision is contracts.ResumeDecision
    assert domain_transport.canonical_json_bytes is streaming_api.canonical_json_bytes
    assert streaming_api.canonical_json_bytes is contracts.canonical_json_bytes
    assert redis_transport.canonical_json_bytes is contracts.canonical_json_bytes
    assert domain_transport.STREAM_GAP_SCHEMA == contracts.STREAM_GAP_SCHEMA
    assert (
        redis_transport.committed_public_stream_event
        is contracts.committed_public_stream_event
    )


def test_domain_transport_preserves_cursor_gap_and_canonical_json_contracts() -> None:
    cursor = domain_transport.StreamCursor.parse("run-a:7:12-3", run_id="run-a")
    assert cursor.event_id == "run-a:7:12-3"
    gap = domain_transport.StreamGap(
        "retained_history_unavailable",
        cursor.event_id,
        cursor.stream_incarnation,
        cursor.stream_incarnation,
        "1-0",
        "12-3",
    )
    assert gap.as_public_dict() == {
        "schema": "ai-platform.stream-gap.v3",
        "reason": "retained_history_unavailable",
        "current_stream_incarnation": 7,
        "recovery": "reload_durable_state",
        "requested_event_id": "run-a:7:12-3",
        "requested_stream_incarnation": 7,
        "earliest_available_event_id": "1-0",
        "latest_available_event_id": "12-3",
    }
    assert domain_transport.canonical_json_bytes({"b": "\u00e9", "a": 1}) == (
        b'{"a":1,"b":"\xc3\xa9"}'
    )
    with pytest.raises(streaming_api.StreamContractError, match="stream_json_not_canonicalizable"):
        domain_transport.canonical_json_bytes({"value": float("nan")})


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
