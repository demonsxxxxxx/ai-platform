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
        with TerminalStageTiming("result_publication", run_id="run-a", attempt_id="attempt-a"):
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
from app.platform.tracing import TerminalStageTiming, configure_terminal_stage_logging
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
assert logging.getLogger().getEffectiveLevel() == logging.WARNING
'''
    result = subprocess.run([sys.executable, "-c", script, entrypoint], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    lines = result.stderr.splitlines()
    assert len(lines) == 1, result.stderr
    assert "run_terminal_stage run_id='run-a' attempt_id='attempt-a'" in lines[0]
    assert "phase='result_business_commit' duration_ms=" in lines[0]
    assert "outcome='ok' terminal_status='succeeded'" in lines[0]
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
