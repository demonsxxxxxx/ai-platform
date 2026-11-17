"""PG gateway regression: transport loss must not lose committed public rows."""
import json
from dataclasses import replace

import pytest
from fastapi import HTTPException

from tests import test_lambchat_sse_v21 as support
from app.routes import lambchat_compat as route


def row(seq, *, visible=True):
    return {
        'id': f'evt4_row_{seq}', 'tenant_id': 'tenant-a', 'run_id': 'run-a',
        'sequence': seq, 'event_type': 'message.delta', 'visible_to_user': visible,
        'created_at': '2026-08-09T00:00:00Z',
        'payload_json': {'delta': str(seq), '__stream_v4': {
            'version': 1, 'attempt_id': 'attempt-a', 'stream_incarnation': 1,
            'authorization_epoch': 1, 'message_id': 'msg_run_a',
            'trace_ref': None, 'causation_event_id': None,
        }},
    }


def setup(monkeypatch, rows):
    support.patch_authority(monkeypatch)
    a = replace(support.authority(), open_payload_bytes=json.dumps(support.open_entry().envelope))
    async def admission(**kwargs):
        return {'status': 'running'}, a, support.lease()
    async def bounds(*args, **kwargs):
        return {'run-a': max((r['sequence'] for r in rows), default=0)}
    calls = []
    async def page(*args, after_sequence, through_sequence, limit, **kwargs):
        calls.append((after_sequence, through_sequence))
        return [r for r in rows if after_sequence < r['sequence'] <= through_sequence][:limit]
    monkeypatch.setattr(route, '_await_sse_admission', admission)
    monkeypatch.setattr(route.streaming_run_events, 'list_run_event_sequence_bounds', bounds)
    monkeypatch.setattr(route.streaming_run_events, 'list_run_events', page)
    return calls


@pytest.mark.asyncio
async def test_private_pages_advance_scan_without_public_cursor(monkeypatch):
    calls = setup(monkeypatch, [row(i, visible=i == 205) for i in range(1, 206)])
    response = await support.open_response(support.FakeBridge([]))
    iterator = response.body_iterator
    assert 'event: stream.open' in await iterator.__anext__()
    frame = await iterator.__anext__()
    assert 'id: run-a:205' in frame
    assert calls == [(0, 205), (100, 205), (200, 205)]
    await iterator.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('cursor,code', [('run-a:1:2-0',409), ('run-b:1',400), ('run-a:999',400)])
async def test_resume_rejects_unsafe_cursor(monkeypatch,cursor,code):
    setup(monkeypatch, [row(1)])
    with pytest.raises(HTTPException) as exc:
        await support.open_response(support.FakeBridge([]), last_event_id=cursor)
    assert exc.value.status_code == code


@pytest.mark.asyncio
async def test_redis_failure_heartbeat_replays_new_pg_row(monkeypatch):
    rows = []
    setup(monkeypatch, rows)
    class Bridge(support.FakeBridge):
        async def read_live_page(self, **kwargs):
            rows.append(row(1))
            raise support.StreamTransportUnavailable('redis_down')
    response = await support.open_response(Bridge([]))
    iterator = response.body_iterator
    assert 'event: stream.open' in await iterator.__anext__()
    assert 'id: run-a:1' in await iterator.__anext__()
    await iterator.aclose()


@pytest.mark.asyncio
async def test_contiguous_live_frame_does_not_read_pg(monkeypatch):
    calls = setup(monkeypatch, [])
    class Bridge(support.FakeBridge):
        async def read_live_page(self, **kwargs):
            return (support.entry('1-0','evt4_live','message.delta',{'delta':'live'}),)
    response = await support.open_response(Bridge([]))
    iterator = response.body_iterator
    await iterator.__anext__()
    assert 'id: run-a:1' in await iterator.__anext__()
    assert calls == []
    await iterator.aclose()


@pytest.mark.asyncio
async def test_terminal_cursor_reconnect_emits_only_control_end(monkeypatch):
    terminal = row(1)
    terminal['event_type'] = 'run.succeeded'
    terminal['payload_json'] = {
        '__stream_v4': terminal['payload_json']['__stream_v4'],
        'terminal_event_id': terminal['id'], 'hydrate_required': True,
    }
    setup(monkeypatch, [terminal])
    response = await support.open_response(support.FakeBridge([]), last_event_id='run-a:1')
    body = ''.join([frame async for frame in response.body_iterator])
    assert 'event: stream.end' in body
    assert 'event: run.succeeded' not in body
    assert 'id: run-a:' not in body


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('attempt_id','other'), ('stream_incarnation',2), ('authorization_epoch',2)])
async def test_pg_projection_fences_stale_authority_rows(monkeypatch, field, value):
    stale = row(1)
    stale['payload_json']['__stream_v4'][field] = value
    calls = setup(monkeypatch, [stale, row(2)])
    response = await support.open_response(support.FakeBridge([]))
    iterator = response.body_iterator
    await iterator.__anext__()
    frame = await iterator.__anext__()
    assert 'id: run-a:2' in frame
    assert 'id: run-a:1' not in frame
    assert calls == [(0,2)]
    await iterator.aclose()


@pytest.mark.asyncio
async def test_pg_read_failure_does_not_fall_back_to_redis_authority(monkeypatch):
    setup(monkeypatch, [row(1)])
    async def fail(*args, **kwargs):
        raise RuntimeError('pg_unavailable')
    monkeypatch.setattr(route.streaming_run_events, 'list_run_events', fail)
    response = await support.open_response(support.FakeBridge([]))
    iterator = response.body_iterator
    await iterator.__anext__()
    with pytest.raises(RuntimeError, match='pg_unavailable'):
        await iterator.__anext__()


@pytest.mark.asyncio
async def test_terminal_commit_after_bounds_is_not_mistaken_for_consumed_terminal(monkeypatch):
    rows = []
    setup(monkeypatch, rows)
    gate = support.FrameLeaseGate()
    async def admission(**kwargs):
        return {"status": "running"}, support.authority(), gate
    monkeypatch.setattr(route, "_await_sse_admission", admission)
    calls = 0
    async def bounds(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            # Snapshot was empty, then a terminal transaction commits before
            # the expired lease renewal observes the Run's terminal status.
            terminal = row(2)
            terminal["event_type"] = "run.succeeded"
            terminal["payload_json"] = {
                "__stream_v4": terminal["payload_json"]["__stream_v4"],
                "terminal_event_id": terminal["id"], "hydrate_required": True,
            }
            rows.extend([row(1), terminal])
            gate.valid = False
            return {"run-a": 0}
        return {"run-a": max((item["sequence"] for item in rows), default=0)}
    async def get_run(*args, **kwargs):
        return {"id": "run-a", "session_id": "session-a", "status": "succeeded"}
    monkeypatch.setattr(route.streaming_run_events, "list_run_event_sequence_bounds", bounds)
    monkeypatch.setattr(route.runs_creation, "get_authorized_run", get_run)
    response = await support.open_response(support.FakeBridge([]))
    body = "".join([frame async for frame in response.body_iterator])
    assert "id: run-a:1" in body
    assert "id: run-a:2" in body
    assert body.index("event: message.delta") < body.index("event: run.succeeded") < body.index("event: stream.end")
