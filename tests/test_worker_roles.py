"""Process composition contracts, isolated from worker dispatch test fixtures."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app import worker_main
from app.settings import Settings


@pytest.mark.parametrize("role", ["integrated", "execution", "finalizer"])
def test_role_settings_and_independent_concurrency(role):
    settings = Settings(_env_file=None, worker_role=role)
    assert settings.worker_role == role
    assert settings.worker_concurrency == 10
    assert settings.worker_finalizer_concurrency == 2
    settings = Settings(_env_file=None, worker_role=role, worker_concurrency=7, worker_finalizer_concurrency=3)
    assert (settings.worker_concurrency, settings.worker_finalizer_concurrency) == (7, 3)


@pytest.mark.parametrize("kwargs", [{"worker_role": "other"}, {"worker_finalizer_concurrency": 0}])
def test_invalid_worker_settings_fail_closed(kwargs):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **kwargs)


@pytest.mark.parametrize("role", ["integrated", "execution", "finalizer"])
def test_role_maintenance_inventory(role):
    settings = SimpleNamespace(worker_role=role)
    recovery = worker_main._worker_recovery_phases(
        settings, v4_capabilities=object(), attempt_lifecycle=object(), lifecycle=object(),
    )
    cleanup = worker_main._worker_cleanup_phases(settings)
    assert set(recovery) == {
        "integrated": {"queue_reclaim", "stale_run_reconciliation"},
        "execution": {"queue_reclaim"},
        "finalizer": {"stale_run_reconciliation"},
    }[role]
    assert set(cleanup) == (set() if role == "execution" else {
        "sandbox_cleanup", "memory_cleanup", "data_retention", "file_upload_session_cleanup",
    })


@pytest.mark.parametrize("role", ["integrated", "execution", "finalizer"])
@pytest.mark.parametrize("count", [0, 1, 3])
@pytest.mark.asyncio
async def test_role_process_owns_exact_tasks_and_one_shutdown(monkeypatch, role, count):
    calls = []
    started = asyncio.Event()
    expected_slots = max(count, 1) if role != "finalizer" else 0
    settings = SimpleNamespace(worker_role=role, worker_finalizer_concurrency=4)
    async def schema():
        calls.append("schema")
    async def close_runtime():
        calls.append("runtime-close")
    async def close_clients():
        calls.append("clients-close")
    async def slot(**kwargs):
        calls.append("slot")
        try:
            await asyncio.Event().wait()
        finally:
            calls.append("slot-closed")
    async def reconciler(stop_event, **kwargs):
        assert kwargs["concurrency"] == 4
        calls.append("reconciler")
        await stop_event.wait()
    async def heartbeat(worker_id, *, role):
        assert role == settings.worker_role
        calls.append("heartbeat")
        await asyncio.Event().wait()
    async def maintenance(*args, **kwargs):
        assert "run_delivery_intents" in kwargs
        calls.append("maintenance")
        await asyncio.Event().wait()
    original_supervise = worker_main._supervise_worker_tasks
    async def supervise(tasks, background):
        await asyncio.sleep(0)
        assert len(tasks) == expected_slots
        assert len(background) == (2 if role == "execution" else 3)
        started.set()
        await original_supervise(tasks, background)
    monkeypatch.setattr(worker_main, "configure_worker_services", lambda: calls.append("bootstrap"))
    monkeypatch.setattr(worker_main, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_main, "require_schema_current", schema)
    monkeypatch.setattr(
        worker_main,
        "build_worker_v4_runtime",
        lambda *a: SimpleNamespace(
            capabilities=object(), aclose=close_runtime,
        ),
    )
    monkeypatch.setattr(worker_main, "_close_runtime_clients", close_clients)
    monkeypatch.setattr(worker_main, "_run_worker_slot", slot)
    monkeypatch.setattr(worker_main, "run_executor_terminal_reconciler", reconciler)
    monkeypatch.setattr(worker_main, "_worker_runtime_heartbeat_until_done", heartbeat)
    monkeypatch.setattr(worker_main, "_maintenance_until_done", maintenance)
    monkeypatch.setattr(worker_main, "_supervise_worker_tasks", supervise)
    task = asyncio.create_task(worker_main.run_worker_pool(worker_count=count))
    try:
        await asyncio.wait_for(started.wait(), 1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert calls[:2] == ["bootstrap", "schema"]
    assert calls.count("slot") == expected_slots
    assert calls.count("slot-closed") == expected_slots
    assert calls.count("reconciler") == (role != "execution")
    assert calls.count("heartbeat") == calls.count("maintenance") == 1
    assert calls[-2:] == ["runtime-close", "clients-close"]


@pytest.mark.parametrize("entrypoint", ["run_worker_pool", "run_forever", "run_once_and_close"])
def test_fresh_entrypoint_binds_all_six_services_before_schema(entrypoint):
    # A new interpreter prevents imports/fixtures/API startup hiding missing ports.
    script = '''
import asyncio
from app import worker_main as w
from app.files.api import file_upload_persistence
from app.execution.application.model_control_plane import configured_model_control_plane
from app.skills.application.skill_markdown import parse_skill_markdown_front_matter
from app.context.application.provider_sessions import configured_provider_session_use_cases
from app.mcp.api import mcp_runtime_services
from app.agent_apps import api as agent_api
class Checked(Exception): pass
async def schema():
    assert file_upload_persistence() is not None
    assert configured_model_control_plane() is not None
    assert parse_skill_markdown_front_matter("---\\nname: test\\n---\\n") == {"name": "test"}
    assert configured_provider_session_use_cases() is not None
    assert mcp_runtime_services() is not None
    assert agent_api.agent_profile_repository._target is not None
    assert agent_api._authority_factory is not None
    raise Checked()
w.require_schema_current = schema
async def close(): pass
w._close_runtime_clients = close
try:
    asyncio.run(CALL)
except Checked:
    print("six-services-ready")
else:
    raise AssertionError("schema check never ran")
'''
    call = {"run_worker_pool": "w.run_worker_pool(worker_count=1)",
            "run_forever": "w.run_forever(attempt_lifecycle=object(), lifecycle=object())",
            "run_once_and_close": "w.run_once_and_close(1)"}[entrypoint]
    result = subprocess.run([sys.executable, "-c", script.replace("CALL", call)],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "six-services-ready"


def test_finalizer_cli_rejects_once_before_startup(monkeypatch, capsys):
    monkeypatch.setattr(worker_main, "get_settings", lambda: SimpleNamespace(worker_role="finalizer"))
    monkeypatch.setattr(sys, "argv", ["worker", "--once"])
    with pytest.raises(SystemExit) as error:
        worker_main.main()
    assert error.value.code == 2
    assert "--once is not supported for finalizer" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_finalizer_single_run_rejects_before_queue_claim(monkeypatch):
    monkeypatch.setattr(worker_main, "get_settings", lambda: SimpleNamespace(worker_role="finalizer"))
    async def forbidden(**kwargs):
        pytest.fail("finalizer must never claim a queue message")
    monkeypatch.setattr(worker_main.queue, "lease_run", forbidden)
    with pytest.raises(ValueError, match="finalizer"):
        await worker_main.run_once(v4_capabilities=object(), attempt_lifecycle=object(), lifecycle=object())
    with pytest.raises(ValueError, match="finalizer"):
        await worker_main.run_once_and_close(1)


@pytest.mark.parametrize("role", ["integrated", "execution", "finalizer"])
def test_heartbeat_role_is_additive_local_metadata(monkeypatch, tmp_path, role):
    path = tmp_path / "heartbeat.json"
    monkeypatch.setattr(worker_main, "worker_runtime_heartbeat_path", lambda: path)
    worker_main.write_worker_runtime_heartbeat("local-worker", role=role)
    data = json.loads(path.read_text())
    assert data["schema_version"] == "ai-platform.worker-runtime-heartbeat.v1"
    assert data["worker_id"] == "local-worker"
    assert data["worker_role"] == role
    assert set(data) == {"schema_version", "worker_id", "worker_role", "runtime_commit", "pid", "observed_at"}


@pytest.mark.parametrize("role", ["integrated", "execution"])
@pytest.mark.asyncio
async def test_cancelled_queued_claim_is_joined_before_clients_and_never_reclaimed(monkeypatch, role):
    started, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []
    async def schema():
        pass
    async def blocked_claim(**kwargs):
        calls.append("claim")
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelling.set()
            await release.wait()
            return None  # Queue client may finish by translating cancellation.
    async def background(*args, **kwargs):
        await asyncio.Event().wait()
    async def runtime_close():
        calls.append("runtime-close")
    async def clients_close():
        calls.append("clients-close")
    monkeypatch.setattr(worker_main, "get_settings", lambda: SimpleNamespace(worker_role=role, max_active_worker_runs=10))
    monkeypatch.setattr(worker_main, "require_schema_current", schema)
    monkeypatch.setattr(worker_main, "queue", SimpleNamespace(lease_run=blocked_claim))
    monkeypatch.setattr(worker_main, "run_executor_terminal_reconciler", background)
    monkeypatch.setattr(worker_main, "_maintenance_until_done", background)
    monkeypatch.setattr(worker_main, "_worker_runtime_heartbeat_until_done", background)
    monkeypatch.setattr(worker_main, "build_worker_v4_runtime", lambda *args: SimpleNamespace(capabilities=object(), aclose=runtime_close))
    monkeypatch.setattr(worker_main, "_close_runtime_clients", clients_close)
    task = asyncio.create_task(worker_main.run_worker_pool(worker_count=1, idle_sleep_seconds=0))
    try:
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        await asyncio.wait_for(cancelling.wait(), 1)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert calls == ["claim"]
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls == ["claim", "runtime-close", "clients-close"]
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("role", ["integrated", "execution", "finalizer"])
@pytest.mark.asyncio
async def test_schema_failure_closes_clients_without_starting_role_tasks(monkeypatch, role):
    calls = []
    async def schema():
        raise RuntimeError("schema unavailable")
    async def close():
        calls.append("closed")
    monkeypatch.setattr(worker_main, "get_settings", lambda: SimpleNamespace(worker_role=role))
    monkeypatch.setattr(worker_main, "require_schema_current", schema)
    monkeypatch.setattr(worker_main, "_close_runtime_clients", close)
    monkeypatch.setattr(worker_main, "build_worker_v4_runtime", lambda *args: pytest.fail("schema must precede runtime creation"))
    with pytest.raises(RuntimeError, match="schema unavailable"):
        await worker_main.run_worker_pool(worker_count=1)
    assert calls == ["closed"]
