"""Synthetic HTTP/SDK fixtures and independent checkpoint assertions."""

import hashlib
import json

from app.execution.application.model_response_evidence import observe_anthropic_text


def response_events(identity, chunks, *, complete=True, stop_reason="end_turn"):
    events = [
        {"type": "message_start", "message": {"id": identity, "role": "assistant", "stop_reason": None}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
    ]
    for index, text in enumerate(chunks, 1):
        events.append({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}})
        if index in {130, 270}:
            events.append({"type": "ping"})  # Flush real answer coalescing at non-sample boundaries.
    if complete:
        events.extend([
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": stop_reason}},
            {"type": "message_stop"},
        ])
    return events


def proxy_checkpoints(events, caplog, *, run_id="run-a", attempt_id="qat-attempt-a"):
    wire = b"".join(b"data: " + json.dumps(event, ensure_ascii=False).encode() + b"\r\n\r\n" for event in events)
    before = len(caplog.records)
    # Split framing and multibyte UTF-8 across actual forwarding chunks.
    chunks = [wire[index:index + 17] for index in range(0, len(wire), 17)]
    assert b"".join(observe_anthropic_text(chunks, run_id=run_id, attempt_id=attempt_id)) == wire
    keys = ("call_ref", "events", "chars", "sha256", "final", "complete", "coverage")
    return [dict(zip(keys, record.args[2:], strict=True)) for record in caplog.records[before:]
            if record.name == "app.execution.application.model_response_evidence"]


def assert_response_checkpoints(wire, sdk, chunks, *, complete=True):
    assert sdk == wire  # Both sides must agree on scope, exact prefix and lifecycle.
    count = len(chunks)
    sampled = [n for n in range(1, count + 1) if n == 1 or n >= 128 and n & (n - 1) == 0]
    assert [item["events"] for item in sdk] == sampled + [count]
    assert len({item["call_ref"] for item in sdk}) == 1
    assert len(sdk[0]["call_ref"]) == 32
    for item in sdk:
        prefix = "".join(chunks[:item["events"]])
        assert item["chars"] == len(prefix)
        assert item["sha256"] == hashlib.sha256(prefix.encode()).hexdigest()
    assert all(not item["final"] and not item["complete"] for item in sdk[:-1])
    assert sdk[-1]["final"] is True
    assert sdk[-1]["complete"] is complete
    assert sdk[-1]["coverage"] == ("text_delta" if complete else "partial_stream_end")
