from tests.support.terminal_timing import terminal_stage_logs as terminal_stage_logs
import asyncio
import logging

import pytest

from app.platform import tracing
from app.platform.tracing import TerminalStageTiming


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,outcome", [
    (None, "ok"),
    (RuntimeError("private prompt /workspace/output secret-token"), "error"),
    (asyncio.CancelledError("private output"), "cancelled"),
])
@pytest.mark.usefixtures("terminal_stage_logs")
async def test_terminal_stage_times_delay_once_and_preserves_failure(monkeypatch, caplog, failure, outcome):
    now = [10.0]
    monkeypatch.setattr(tracing, "monotonic", lambda: now[0])
    caplog.set_level(logging.INFO)
    timer = TerminalStageTiming("terminal_receipt_commit",
                                run_id="run-a", attempt_id="attempt-a", terminal_status="completed")
    try:
        with timer:
            now[0] += 2.5
            await asyncio.sleep(0)
            if failure is not None:
                raise failure
    except BaseException as caught:
        assert caught is failure
    else:
        assert failure is None
    timer.finish()  # Repeated cleanup cannot emit a second observation.
    records = [r for r in caplog.records if r.msg == "run_terminal_stage"]
    assert len(records) == 1
    record = records[0]
    assert record.terminal_phase_duration_ms == 2500
    assert record.terminal_phase_outcome == outcome
    assert record.terminal_status == "succeeded"
    assert (record.run_id, record.attempt_id) == ("run-a", "attempt-a")
    assert record.exc_info is None
    assert "private" not in repr(vars(record))
    assert "secret-token" not in repr(vars(record))


@pytest.mark.parametrize("status,expected", [("failed", "failed"), ("canceled", "cancelled"),
                                           ("private prompt /path", "unknown"), ({"secret": "value"}, "unknown")])
@pytest.mark.usefixtures("terminal_stage_logs")
def test_terminal_stage_status_is_bounded(monkeypatch, caplog, status, expected):
    times = iter([2.0, 1.0])
    monkeypatch.setattr(tracing, "monotonic", lambda: next(times))
    caplog.set_level(logging.INFO)
    with TerminalStageTiming("result_business_commit",
                             run_id="run-a", attempt_id="attempt-a", terminal_status=status):
        pass
    record = caplog.records[-1]
    assert record.terminal_status == expected
    assert record.terminal_phase_duration_ms == 0
    assert "secret" not in repr(vars(record))
    assert "private prompt" not in repr(vars(record))


@pytest.mark.parametrize("failure", [None, RuntimeError("original"), asyncio.CancelledError()])
def test_logging_failure_never_replaces_operation_result_or_exception(monkeypatch, failure):
    class BrokenLogger:
        def info(self, *args, **kwargs):
            raise RuntimeError("logging unavailable")
    monkeypatch.setattr(tracing, "_terminal_stage_logger", BrokenLogger())
    try:
        with TerminalStageTiming("terminal_receipt_publication", run_id="run-a", attempt_id="attempt-a"):
            if failure is not None:
                raise failure
    except BaseException as caught:
        assert caught is failure
    else:
        assert failure is None


@pytest.mark.parametrize("entrypoint", ["api", "worker", "executor"])
def test_process_entrypoints_render_only_safe_terminal_timing(entrypoint):
    import subprocess
    import sys

    script = '''
import contextlib
import io
import logging
import sys
import tempfile
from app.platform.tracing import TerminalStageTiming, configure_terminal_stage_logging, log_session_store_append_summary
assert logging.getLogger().getEffectiveLevel() == logging.WARNING
entrypoint = sys.argv[1]
if entrypoint == "api":
    from app.main import create_app
    create_app()
elif entrypoint == "executor":
    from app.runtime.sandbox.executor_app import create_executor_app
    with tempfile.TemporaryDirectory() as workspace:
        create_executor_app(workspace_root=workspace)
else:
    from app.worker_main import main
    sys.argv = ["worker", "--help"]
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            main()
        except SystemExit as exc:
            assert exc.code == 0
configure_terminal_stage_logging()
logging.getLogger("app.worker").info("unrelated private prompt")
with TerminalStageTiming("result_business_commit", run_id="run-a", attempt_id="attempt-a", terminal_status="succeeded"):
    pass
log_session_store_append_summary(run_id="run-a", attempt_id="attempt-a", calls=2,
    failed_calls=1, total_ms=250, max_ms=125, active_calls=0)
assert logging.getLogger().getEffectiveLevel() == logging.WARNING
'''
    result = subprocess.run([sys.executable, "-c", script, entrypoint], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    lines = result.stderr.splitlines()
    assert len(lines) == 2, result.stderr
    assert "run_terminal_stage run_id='run-a' attempt_id='attempt-a'" in lines[0]
    assert "phase='result_business_commit' duration_ms=" in lines[0]
    assert "outcome='ok' terminal_status='succeeded'" in lines[0]
    assert "calls=2 failed_calls=1 total_duration_ms=250 max_duration_ms=125 store_active_calls=0" in lines[1]
    assert "private" not in result.stderr



def test_broken_timing_stream_does_not_print_chained_private_exception(monkeypatch, capsys):
    class BrokenStream:
        def write(self, _message):
            raise OSError("private logging destination")
        def flush(self):
            pass

    logger = logging.Logger("isolated-terminal-stage", level=logging.INFO)
    logger.propagate = False
    logger.addHandler(tracing._TerminalStageHandler(BrokenStream()))
    monkeypatch.setattr(tracing, "_terminal_stage_logger", logger)
    monkeypatch.setattr(logging, "raiseExceptions", True)
    failure = RuntimeError("private prompt /workspace/output secret-token")
    with pytest.raises(RuntimeError) as caught:
        with TerminalStageTiming("result_business_commit", run_id="run-a", attempt_id="attempt-a"):
            raise failure
    assert caught.value is failure
    assert capsys.readouterr().err == ""


@pytest.mark.asyncio
@pytest.mark.usefixtures("terminal_stage_logs")
async def test_real_sdk_pre_result_flush_is_visible_in_append_summary(monkeypatch, caplog):
    from claude_agent_sdk._internal.query import Query
    from claude_agent_sdk._internal.transcript_mirror_batcher import TranscriptMirrorBatcher
    from app.execution.infrastructure.harness.claude import session_store_tracking

    now = [10.0]
    monkeypatch.setattr(session_store_tracking, "monotonic", lambda: now[0])
    monkeypatch.setattr(tracing, "monotonic", lambda: now[0])
    raw_result_at = []
    public_message_seen = asyncio.Event()

    class Transport:
        async def read_messages(self):
            yield {"type": "system", "subtype": "init"}
            await public_message_seen.wait()
            now[0] += 0.5  # Non-store work must not be attributed to append.
            yield {"type": "transcript_mirror",
                   "filePath": "/private/projects/project/session.jsonl",
                   "entries": [{"uuid": "entry", "text": "private prompt secret-token"}]}
            raw_result_at.append(now[0])
            yield {"type": "result", "is_error": False}

    class Store:
        accepted_final_sequence = 1

        async def append(self, _key, _entries):
            now[0] += 0.25
            await asyncio.sleep(0)

    async def on_error(*_args):
        raise AssertionError("unexpected mirror error")

    tracker = session_store_tracking.SessionStoreAppendTracker(Store())
    query = Query(Transport(), is_streaming_mode=True)
    query.set_transcript_mirror_batcher(TranscriptMirrorBatcher(
        tracker, "/private/projects", on_error, max_pending_entries=0, max_pending_bytes=0,
    ))
    reader = asyncio.create_task(query._read_messages())
    public_result_at = None
    post_result_timer = None
    try:
        async for message in query.receive_messages():
            tracker.observe_public_message(is_result=message["type"] == "result")
            public_message_seen.set()
            if message["type"] == "result":
                public_result_at = now[0]
                post_result_timer = TerminalStageTiming(
                    "sdk_final_flush", run_id="run-a", attempt_id="attempt-a",
                )
        await reader
        await tracker.wait_idle()
        assert post_result_timer is not None
        post_result_timer.finish()
        tracker.log_append_summary(run_id="run-a", attempt_id="attempt-a")
        tracker.log_append_summary(run_id="run-a", attempt_id="attempt-a")
    finally:
        if not reader.done():
            reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
    assert public_result_at - raw_result_at[0] == 0.25
    stage = [r for r in caplog.records if r.msg == "run_terminal_stage"]
    assert stage[0].terminal_phase_duration_ms == 0
    summary = [r for r in caplog.records if r.msg == "run_session_store_append_summary"]
    assert len(summary) == 1
    record = summary[0]
    assert (record.append_calls, record.append_failed_calls, record.store_active_calls) == (1, 0, 0)
    assert record.append_total_duration_ms == record.append_max_duration_ms == 250
    assert record.latest_public_result_gap_ms == 750
    assert record.append_await_in_latest_result_gap_ms == 250
    assert tracker.main_append_acknowledged and tracker.final_sequence == 1
    assert not tracker.failed
    assert "private" not in repr(vars(record)) and "secret-token" not in repr(vars(record))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("private payload"), asyncio.CancelledError("secret-token")])
@pytest.mark.usefixtures("terminal_stage_logs")
async def test_append_observation_preserves_failure_identity_retry_and_receipt(monkeypatch, caplog, failure):
    from app.execution.infrastructure.harness.claude import session_store_tracking
    now = [1.0]
    monkeypatch.setattr(session_store_tracking, "monotonic", lambda: now[0])

    class Store:
        accepted_final_sequence = 3
        attempts = 0

        async def append(self, _key, _entries):
            self.attempts += 1
            now[0] += 0.125
            if self.attempts == 1:
                raise failure

    tracker = session_store_tracking.SessionStoreAppendTracker(Store())
    key, entries = {"session_id": "private"}, [{"text": "private payload"}]
    with pytest.raises(type(failure)) as caught:
        await tracker.append(key, entries)
    assert caught.value is failure
    assert tracker.failed and tracker.active_calls == 0
    assert not tracker.main_append_acknowledged
    await tracker.append(key, entries)
    assert not tracker.failed and tracker.final_sequence == 3
    tracker.log_append_summary(run_id="run-a", attempt_id="attempt-a")
    record = caplog.records[-1]
    assert (record.append_calls, record.append_failed_calls) == (2, 1)
    assert (record.append_total_duration_ms, record.append_max_duration_ms) == (250, 125)
    assert record.exc_info is None
    assert "private" not in repr(vars(record)) and "secret-token" not in repr(vars(record))


def test_append_summary_logging_failure_and_counter_bounds(monkeypatch):
    from app.execution.infrastructure.harness.claude import session_store_tracking
    tracker = session_store_tracking.SessionStoreAppendTracker(object())
    tracker._append_total_seconds = tracker._append_max_seconds = 1_000_000_000
    observations = []
    monkeypatch.setattr(session_store_tracking, "log_session_store_append_summary", lambda **kw: observations.append(kw))
    tracker.log_append_summary(run_id="run-a", attempt_id="attempt-a")
    assert observations[0]["total_ms"] == observations[0]["max_ms"] == 1_000_000_000

    class BrokenLogger:
        def info(self, *_args, **_kwargs):
            raise RuntimeError("private logging destination")
    monkeypatch.setattr(tracing, "_terminal_stage_logger", BrokenLogger())
    tracing.log_session_store_append_summary(run_id="run-a", attempt_id="attempt-a", calls=0,
        failed_calls=0, total_ms=0, max_ms=0, active_calls=0)


@pytest.mark.asyncio
@pytest.mark.usefixtures("terminal_stage_logs")
async def test_result_gap_clips_inflight_append_and_keeps_latest_result(monkeypatch, caplog):
    from app.execution.infrastructure.harness.claude import session_store_tracking
    now = [10.0]
    monkeypatch.setattr(session_store_tracking, "monotonic", lambda: now[0])
    caplog.set_level(logging.INFO)
    started, release = asyncio.Event(), asyncio.Event()

    class Store:
        async def append(self, *_args):
            started.set()
            await release.wait()

    tracker = session_store_tracking.SessionStoreAppendTracker(Store())
    task = asyncio.create_task(tracker.append({}, [{"text": "private"}]))
    try:
        await started.wait()
        now[0] = 11.0
        tracker.observe_public_message(is_result=False)
        now[0] = 11.25
        tracker.observe_public_message(is_result=True)
        # Neither pre-window work nor post-Result work belongs to this gap.
        now[0] = 12.0
        tracker.observe_public_message(is_result=False)
        release.set()
        await task
        tracker.log_append_summary(run_id="run-a", attempt_id="attempt-a")
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    record = caplog.records[-1]
    assert record.append_total_duration_ms == 2000
    assert record.latest_public_result_gap_ms == 250
    assert record.append_await_in_latest_result_gap_ms == 250
    assert "private" not in repr(vars(record))


@pytest.mark.parametrize("first_result", [False, True])
@pytest.mark.usefixtures("terminal_stage_logs")
def test_result_gap_without_preceding_message_is_absent(caplog, first_result):
    from app.execution.infrastructure.harness.claude.session_store_tracking import SessionStoreAppendTracker
    caplog.set_level(logging.INFO)
    tracker = SessionStoreAppendTracker(object())
    if first_result:
        tracker.observe_public_message(is_result=True)
    tracker.log_append_summary(run_id="run-a", attempt_id="attempt-a")
    record = caplog.records[-1]
    assert record.latest_public_result_gap_ms is None
    assert record.append_await_in_latest_result_gap_ms is None
