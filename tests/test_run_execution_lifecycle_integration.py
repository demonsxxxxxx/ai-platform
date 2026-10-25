from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import urllib.error
import urllib.request

import pytest

from app.runtime.sandbox.callback_tokens import (
    CallbackTokenBinding, callback_token_id_for_binding, derive_callback_token,
)
from tests.support.run_lifecycle_integration import (
    CALLBACK_SECRET,
    EXECUTOR_SECRET,
    ROOT,
    ManagedProcess,
    isolated_run_lifecycle_stack,
    select_loopback_port,
    select_trusted_loopback_port,
    wait_for_file,
    wait_for_http,
)


_DETERMINISTIC_EXECUTOR = r'''
import asyncio
import os
import sys
from pathlib import Path

import uvicorn

from app.execution.api import ClaudeSdkAgentEventAdapter
from app.platform.public_payload import sanitize_public_payload, sanitize_public_text
from app.runtime.kernel_contracts import AgentEvent
from app.runtime.sandbox.executor_app import create_executor_app

ack_file = Path(os.environ["AI_PLATFORM_TEST_CALLBACK_ACK_FILE"])
release_file = Path(os.environ["AI_PLATFORM_TEST_CALLBACK_RELEASE_FILE"])

async def runner(request, workspace_root, emit_event):
    adapter = ClaudeSdkAgentEventAdapter(
        run_id=request.run_id,
        attempt_id=request.attempt_id,
        sanitizer=sanitize_public_text,
        payload_sanitizer=sanitize_public_payload,
    )
    candidates = adapter.accept_answer_text("Synthetic lifecycle answer")
    candidates += adapter.complete_answer(None)
    for candidate in candidates:
        accepted = await emit_event(AgentEvent(**candidate.as_agent_event_fields()))
        if not accepted:
            raise RuntimeError("test_callback_not_acknowledged")
    ack_file.write_text("callback-acknowledged", encoding="utf-8")
    while not release_file.exists():
        await asyncio.sleep(0.02)
    return {"status": "completed", "answer_receipt": adapter.answer_receipt}

app = create_executor_app(
    workspace_root=sys.argv[2],
    executor_auth_token=os.environ["AI_PLATFORM_EXECUTOR_AUTH_TOKEN"],
    expected_session_id=os.environ["AI_PLATFORM_SESSION_ID"],
    expected_run_id=os.environ["AI_PLATFORM_RUN_ID"],
    expected_attempt_id=os.environ["AI_PLATFORM_ATTEMPT_ID"],
    trusted_callback_base_url=os.environ["AI_PLATFORM_CALLBACK_BASE_URL"],
    executor_runner=runner,
    dispatch_in_background=True,
    terminal_callback_retry_seconds=2.0,
)
uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
'''


def _post_json(url: str, payload: dict, *, headers: dict[str, str]) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, json.loads(response.read())


def _get_json(url: str, *, headers: dict[str, str]) -> dict:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


@pytest.mark.parametrize("finish", ["terminal_receipt", "kill_at_barrier"])
def test_executor_http_callback_persists_answer_to_real_postgres_and_redis(finish):
    """Seeded execution boundary: not API admission, Worker, or business settlement."""
    async def run_scenario() -> None:
        async with isolated_run_lifecycle_stack() as stack:
            with tempfile.TemporaryDirectory(prefix="run-lifecycle-api-") as temp_name:
                temp_root = Path(temp_name)
                api_port = select_trusted_loopback_port()
                api_base = f"http://127.0.0.1:{api_port}"
                api = ManagedProcess(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "app.main:create_app",
                        "--factory",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(api_port),
                        "--log-level",
                        "warning",
                    ],
                    cwd=temp_root,
                    environment=stack.api_environment(),
                )
                with api:
                    wait_for_http(f"{api_base}/api/ai/ready", process=api)
                    executor_port = select_loopback_port()
                    executor_base = f"http://127.0.0.1:{executor_port}"
                    ack_file = temp_root / "callback-acknowledged"
                    release_file = temp_root / "release-executor"
                    executor_env = {
                        "PATH": os.environ["PATH"],
                        "PYTHONPATH": str(ROOT),
                        "PYTHONNOUSERSITE": "1",
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "PYTHONUNBUFFERED": "1",
                        "AI_PLATFORM_EXECUTOR_AUTH_TOKEN": EXECUTOR_SECRET,
                        "AI_PLATFORM_SESSION_ID": stack.session_id,
                        "AI_PLATFORM_RUN_ID": stack.run_id,
                        "AI_PLATFORM_ATTEMPT_ID": stack.attempt_id,
                        "AI_PLATFORM_CALLBACK_BASE_URL": api_base,
                        "AI_PLATFORM_TEST_CALLBACK_ACK_FILE": str(ack_file),
                        "AI_PLATFORM_TEST_CALLBACK_RELEASE_FILE": str(release_file),
                    }
                    executor = ManagedProcess(
                        [
                            sys.executable,
                            "-c",
                            _DETERMINISTIC_EXECUTOR,
                            str(executor_port),
                            str(temp_root / "executor-workspace"),
                        ],
                        cwd=temp_root,
                        environment=executor_env,
                    )
                    with executor:
                        wait_for_http(f"{executor_base}/health", process=executor)
                        token = derive_callback_token(
                            CALLBACK_SECRET,
                            stack.callback_token_id,
                        )
                        callback_base = api_base
                        task = {
                            "tenant_id": stack.tenant_id,
                            "workspace_id": stack.workspace_id,
                            "user_id": stack.user_id,
                            "session_id": stack.session_id,
                            "run_id": stack.run_id,
                            "attempt_id": stack.attempt_id,
                            "prompt": "Produce the deterministic test answer.",
                            "callback_url": f"{api_base}/api/ai/runtime/callbacks/executor",
                            "callback_token_id": stack.callback_token_id,
                            "callback_token": token,
                            "callback_base_url": callback_base,
                            "config": {},
                        }
                        status, receipt = _post_json(
                            f"{executor_base}/v2/tasks",
                            task,
                            headers={"X-AI-Platform-Executor-Credential": EXECUTOR_SECRET},
                        )
                        assert status == 202
                        assert receipt == {
                            "status": "accepted",
                            "run_id": stack.run_id,
                            "attempt_id": stack.attempt_id,
                        }
                        wait_for_file(ack_file, process=executor)
                        task_state = _get_json(
                            f"{executor_base}/v2/tasks/{stack.run_id}/{stack.attempt_id}",
                            headers={"X-AI-Platform-Executor-Credential": EXECUTOR_SECRET},
                        )
                        assert task_state["status"] == "running"

                        async with stack.connection_factory() as conn:
                            rows = await (
                                await conn.execute(
                                    """
                                    select event_type, visible_to_user, payload_json
                                    from run_events
                                    where tenant_id = %s and run_id = %s
                                    order by sequence
                                    """,
                                    (stack.tenant_id, stack.run_id),
                                )
                            ).fetchall()
                            lease = await (
                                await conn.execute(
                                    """
                                    select status, executor_terminal_json
                                    from sandbox_leases
                                    where tenant_id = %s and run_id = %s and attempt_id = %s
                                    """,
                                    (stack.tenant_id, stack.run_id, stack.attempt_id),
                                )
                            ).fetchone()
                        visible_deltas = [
                            row
                            for row in rows
                            if row["event_type"] == "message.delta"
                            and row["visible_to_user"] is True
                        ]
                        assert len(visible_deltas) == 1
                        assert visible_deltas[0]["payload_json"]["delta"] == (
                            "Synthetic lifecycle answer"
                        )
                        assert lease == {"status": "active", "executor_terminal_json": None}

                        assert stack.redis is not None
                        stream_rows = await stack.redis.xrange(stack.redis_stream_key)
                        envelopes = [
                            json.loads(fields["envelope"])
                            for _entry_id, fields in stream_rows
                        ]
                        assert envelopes[0]["event_type"] == "stream.open"
                        assert any(
                            envelope["event_type"] == "message.delta"
                            and envelope["payload"]["delta"]
                            == "Synthetic lifecycle answer"
                            for envelope in envelopes
                        )

                        old_token_id = callback_token_id_for_binding(CallbackTokenBinding(
                            run_id=stack.run_id, attempt_id=stack.attempt_id,
                            owner_generation=stack.owner_generation - 1,
                        ))
                        stale_callback = {
                            "session_id": stack.session_id, "run_id": stack.run_id,
                            "attempt_id": stack.attempt_id, "callback_token_id": old_token_id,
                            "batch_id": "old-owner-heartbeat", "status": "running",
                            "progress": 5,
                            "state_patch": {"executor_heartbeat": True},
                        }
                        with pytest.raises(urllib.error.HTTPError) as rejected:
                            _post_json(task["callback_url"], stale_callback, headers={
                                "X-AI-Platform-Callback-Token": derive_callback_token(
                                    CALLBACK_SECRET, old_token_id,
                                ),
                            })
                        assert rejected.value.code == 409
                        assert json.loads(rejected.value.read())["detail"] == (
                            "sandbox_runtime_owner_generation_stale"
                        )

                        if finish == "kill_at_barrier":
                            executor_pid = executor.pid
                            executor.process.kill()
                            assert executor.stop() == -signal.SIGKILL
                            assert executor.process.pid == executor_pid
                        else:
                            release_file.touch()
                            deadline = time.monotonic() + 15
                            while True:
                                async with stack.connection_factory() as conn:
                                    terminal = await (await conn.execute(
                                        "select executor_status, executor_terminal_json, "
                                        "executor_reconciliation_status from sandbox_leases "
                                        "where tenant_id = %s and run_id = %s and attempt_id = %s",
                                        (stack.tenant_id, stack.run_id, stack.attempt_id),
                                    )).fetchone()
                                if terminal["executor_terminal_json"] is not None:
                                    break
                                assert time.monotonic() < deadline, "terminal receipt was not persisted"
                                await asyncio.sleep(0.02)
                            assert terminal["executor_status"] == "completed"
                            assert terminal["executor_reconciliation_status"] == "waiting_terminal"
                            assert terminal["executor_terminal_json"]["answer_receipt"]["delta_count"] == 1
                            replay = {
                                "session_id": stack.session_id, "run_id": stack.run_id,
                                "attempt_id": stack.attempt_id,
                                "callback_token_id": stack.callback_token_id,
                                "batch_id": "replayed-terminal", "status": "completed",
                                "progress": 100, "terminal_result": terminal["executor_terminal_json"],
                            }
                            callback_headers = {"X-AI-Platform-Callback-Token": token}
                            first_ack = _post_json(task["callback_url"], replay, headers=callback_headers)
                            second_ack = _post_json(task["callback_url"], replay, headers=callback_headers)
                            assert first_ack[0] == second_ack[0] == 200
                            for key in ("accepted", "event_count", "batch_id"):
                                assert first_ack[1][key] == second_ack[1][key]
                            assert second_ack[1]["deduplicated"] is True
                            async with stack.connection_factory() as conn:
                                persisted = await (await conn.execute(
                                    "select executor_terminal_json from sandbox_leases "
                                    "where tenant_id = %s and run_id = %s and attempt_id = %s",
                                    (stack.tenant_id, stack.run_id, stack.attempt_id),
                                )).fetchone()
                                delta_count = await (await conn.execute(
                                    "select count(*) as n from run_events where tenant_id = %s "
                                    "and run_id = %s and event_type = 'message.delta'",
                                    (stack.tenant_id, stack.run_id),
                                )).fetchone()
                            assert persisted["executor_terminal_json"] == terminal["executor_terminal_json"]
                            assert delta_count["n"] == 1

                        # Neither receiving the receipt nor losing the process grants business
                        # settlement/release authority to this fixture. Those owners run later.
                        async with stack.connection_factory() as conn:
                            state = await (await conn.execute(
                                "select r.status as run_status, a.status as attempt_status, "
                                "a.owner_generation, l.status as lease_status "
                                "from runs r join run_attempts a on a.run_id = r.id "
                                "join sandbox_leases l on l.attempt_id = a.id "
                                "where r.tenant_id = %s and r.id = %s",
                                (stack.tenant_id, stack.run_id),
                            )).fetchone()
                            stale_count = await (await conn.execute(
                                "select count(*) as n from run_events where tenant_id = %s "
                                "and run_id = %s and payload_json->>'batch_id' = %s",
                                (stack.tenant_id, stack.run_id, "old-owner-heartbeat"),
                            )).fetchone()
                        assert state == {
                            "run_status": "running", "attempt_status": "running",
                            "owner_generation": stack.owner_generation, "lease_status": "active",
                        }
                        assert stale_count["n"] == 0
                    assert api.process.poll() is None
        assert api.process.poll() is not None
        assert executor.process.poll() is not None
        assert stack.admin.closed
        assert stack.redis_instance.container_id is None

    asyncio.run(run_scenario())


@pytest.fixture(scope="module")
def lifecycle_control_image(tmp_path_factory):
    import subprocess

    from tests.support.run_lifecycle_docker import build_control_image

    evidence = tmp_path_factory.mktemp("lifecycle-image")
    image_id = build_control_image(evidence)
    source_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
    ).strip()
    return image_id, source_commit


@pytest.mark.parametrize(
    ("selected_files", "fault"),
    [
        (False, None),
        (True, None),
        (True, "worker_restart"),
        (False, "terminal_ack_loss"),
        (True, "terminal_callback_loss"),
        (False, "running_cancel"),
        (False, "receipt_cancel"),
        (False, "stop_retry"),
        (False, "executor_timeout"),
        (False, "executor_kill"),
        (False, "provider_unreachable"),
    ],
    ids=[
        "answer",
        "selected-files",
        "worker-restart",
        "terminal-ack-loss",
        "terminal-callback-loss",
        "running-cancel",
        "receipt-cancel",
        "stop-retry",
        "executor-timeout",
        "executor-kill",
        "provider-unreachable",
    ],
)
def test_admission_worker_settlement_delivery_reclamation(
    lifecycle_control_image, tmp_path, selected_files, fault,
):
    """C layer: actual services and Docker provider, deterministic SDK query only."""
    from contextlib import asynccontextmanager
    import hashlib

    import httpx
    import psycopg
    from psycopg.rows import dict_row
    from redis.asyncio import Redis

    from app import schema_migrations
    from tests.support.run_lifecycle_docker import (
        LifecycleDockerStack,
        write_lifecycle_control_evidence,
    )
    from tests.support.run_lifecycle_executor import (
        SELECTED_FILE_CONTENTS, SELECTED_FILES, TEST_ANSWER,
    )
    from tests.support.run_lifecycle_process_faults import (
        SHORT_EXECUTION_MAX_SECONDS,
        kill_executor_process,
        pause_executor_process,
        release_executor_runner,
        resume_executor_process,
    )
    from tests.support.run_lifecycle_faults import (
        TERMINAL_ACK_FAULT_MARKER_PATH,
        TERMINAL_CALLBACK_LOSS_MARKER_PATH,
    )
    from tests.support.run_lifecycle_seed import seed_admission

    async def scenario(stack):
        @asynccontextmanager
        async def connection():
            async with await psycopg.AsyncConnection.connect(
                stack.postgres_dsn, row_factory=dict_row,
            ) as conn:
                yield conn

        async def index_connection():
            return await psycopg.AsyncConnection.connect(
                stack.postgres_dsn, autocommit=True, row_factory=dict_row,
            )

        await schema_migrations.apply_migrations(
            transaction_factory=connection, index_connection_factory=index_connection,
        )
        async with connection() as conn:
            await seed_admission(
                conn, workspace_id=stack.workspace_id, user_id=stack.user_id,
                encryption_key=stack.encryption_key,
            )
            for table in ("sessions", "runs", "run_attempts", "sandbox_leases"):
                assert (await (await conn.execute(f"select count(*) as n from {table}")).fetchone())["n"] == 0

        await asyncio.to_thread(
            stack.start_api,
            drop_terminal_ack_once=fault == "terminal_ack_loss",
            reject_terminal_callbacks=fault == "terminal_callback_loss",
        )
        headers = {
            "X-AI-User-ID": stack.user_id, "X-AI-Tenant-ID": stack.tenant_id,
            "X-AI-User-Name": "N01", "X-AI-Gateway-Secret": stack.gateway_secret,
            "X-AI-Permissions": "artifact:download",
        }
        if fault == "executor_timeout":
            # resource_limits is private runtime input. The production admission
            # path strips it from ordinary-user input, so exercise the authorized
            # admin path while still creating the Run through the API.
            headers["X-AI-Roles"] = "platform_admin"
        run_input = {
            "message": "SELECT_FILES" if selected_files else "ANSWER_ONLY",
        }
        if fault == "executor_timeout":
            run_input["resource_limits"] = {
                "max_seconds": SHORT_EXECUTION_MAX_SECONDS,
            }
        async with httpx.AsyncClient(
            base_url=stack.api_base, headers=headers, trust_env=False, timeout=30,
        ) as http:
            response = await http.post("/api/ai/runs", json={
                "tenant_id": stack.tenant_id, "workspace_id": stack.workspace_id,
                "agent_id": "general-agent", "capability_id": "general_chat",
                "title": "N01 real control chain",
                "input": run_input,
            })
            assert response.status_code == 200, response.text
            admitted = response.json()
            assert admitted["status"] == "queued"
            run_id = admitted["run_id"]

            async def state():
                async with connection() as conn:
                    return await (await conn.execute(
                        "select r.status as run_status, r.error_code, r.result_json, "
                        "r.cancel_requested_at, r.cancel_requested_by, "
                        "(select jsonb_agg(payload_json) from run_events where run_id=r.id "
                        "and event_type='capability_not_authorized') as denials, "
                        "a.id as attempt_id, a.status as attempt_status, a.owner_generation, "
                        "l.status as lease_status, l.executor_status, "
                        "l.executor_reconciliation_status, l.executor_terminal_json, "
                        "l.executor_reconciliation_attempt_count, "
                        "l.executor_reconciliation_error, "
                        "l.executor_reconciliation_context_json is not null "
                        "as has_reconciliation_context "
                        "from runs r left join run_attempts a on a.run_id = r.id "
                        "left join sandbox_leases l on l.attempt_id = a.id "
                        "where r.id = %s", (run_id,),
                    )).fetchone()

            async def poll(
                predicate, *, timeout=180, expected_terminal_statuses=(),
            ):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    snapshot = await state()
                    await asyncio.to_thread(stack.capture_run_logs, run_id)
                    if (
                        snapshot["run_status"] in {"failed", "dead_letter", "cancelled"}
                        and snapshot["run_status"] not in expected_terminal_statuses
                    ):
                        async with connection() as conn:
                            diagnostics = await (await conn.execute(
                                "select payload_json from run_diagnostics where run_id=%s", (run_id,),
                            )).fetchall()
                        (tmp_path / "failed-state.json").write_text(
                            stack._redact(json.dumps({"state": snapshot, "diagnostics": diagnostics}, default=str, indent=2)),
                            encoding="utf-8",
                        )
                        raise AssertionError(f"Lifecycle failed; see {tmp_path / 'failed-state.json'}: {snapshot['error_code']}")
                    result = await predicate(snapshot)
                    if result:
                        return result
                    await asyncio.sleep(0.5)
                raise AssertionError(f"Lifecycle barrier timed out: {await state()}")

            async def read_terminal_ack_marker():
                api = stack._containers["api"]
                result = await asyncio.to_thread(
                    api.exec_run,
                    [
                        "python", "-c",
                        "from pathlib import Path; import sys; "
                        "p=Path(sys.argv[1]); "
                        "print(p.read_text() if p.is_file() else '{}')",
                        TERMINAL_ACK_FAULT_MARKER_PATH,
                    ],
                )
                assert result.exit_code == 0, result.output
                raw = result.output.decode("utf-8") if isinstance(result.output, bytes) else result.output
                return json.loads(raw)

            async def read_terminal_callback_loss_marker():
                api = stack._containers["api"]
                result = await asyncio.to_thread(
                    api.exec_run,
                    [
                        "python", "-c",
                        "from pathlib import Path; import sys; "
                        "p=Path(sys.argv[1]); "
                        "print(p.read_text() if p.is_file() else '{}')",
                        TERMINAL_CALLBACK_LOSS_MARKER_PATH,
                    ],
                )
                assert result.exit_code == 0, result.output
                raw = result.output.decode("utf-8") if isinstance(result.output, bytes) else result.output
                return json.loads(raw)

            async def read_executor_task_status(executor_container):
                script = "\n".join((
                    "import json, os, sys",
                    "from urllib.request import Request, urlopen",
                    "url = 'http://127.0.0.1:18000/v2/tasks/' + sys.argv[1] + '/' + sys.argv[2]",
                    "headers = {'X-AI-Platform-Executor-Credential': os.environ['AI_PLATFORM_EXECUTOR_AUTH_TOKEN']}",
                    "with urlopen(Request(url, headers=headers), timeout=5) as response:",
                    "    payload = json.load(response)",
                    "print(json.dumps({key: payload.get(key) for key in ('status', 'run_id', 'attempt_id', 'error_message', 'terminal_result')}))",
                ))
                result = await asyncio.to_thread(
                    executor_container.exec_run,
                    ["python", "-c", script, run_id, running["attempt_id"]],
                )
                assert result.exit_code == 0, result.output
                raw = result.output.decode("utf-8") if isinstance(result.output, bytes) else result.output
                return json.loads(raw)

            async def outcome_facts():
                async with connection() as conn:
                    return await (await conn.execute(
                        "select "
                        "(select count(*) from run_attempts where tenant_id=%s and run_id=%s) as attempts, "
                        "(select count(*) from provider_turn_receipts where tenant_id=%s and run_id=%s) as turns, "
                        "(select count(*) from provider_turn_receipts where tenant_id=%s and run_id=%s and state='committed') as committed_turns, "
                        "(select count(*) from run_events where tenant_id=%s and run_id=%s and event_type='message.delta') as answer_deltas, "
                        "(select count(*) from run_events where tenant_id=%s and run_id=%s and event_type='message.completed') as answer_completions, "
                        "(select count(*) from messages where tenant_id=%s and run_id=%s and role='assistant') as assistant_messages, "
                        "(select count(*) from artifacts where tenant_id=%s and run_id=%s) as artifacts, "
                        "(select count(*) from run_events where tenant_id=%s and run_id=%s and event_type='cancel_requested') as cancel_requests, "
                        "(select count(*) from run_events where tenant_id=%s and run_id=%s and event_type='run_cancelled') as cancelled_events, "
                        "(select count(*) from run_events where tenant_id=%s and run_id=%s and event_type='run_succeeded') as succeeded_events, "
                        "(select count(*) from run_events where tenant_id=%s and run_id=%s and event_type='run_failed') as failed_events, "
                        "(select count(*) from run_events where tenant_id=%s and run_id=%s and event_type='sandbox_runtime_cleanup_failed') as cleanup_failures, "
                        "(select count(*) from run_events where tenant_id=%s and run_id=%s and event_type='sandbox_runtime_cleanup_succeeded') as cleanup_successes",
                        tuple(
                            value
                            for _ in range(13)
                            for value in (stack.tenant_id, run_id)
                        ),
                    )).fetchone()

            async def wait_for_probe_failure():
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    snapshot = await state()
                    if (
                        snapshot["executor_reconciliation_attempt_count"] >= 1
                        and snapshot["executor_reconciliation_error"]
                        == "sandbox_executor_probe_failed"
                    ):
                        return snapshot
                    if snapshot["executor_terminal_json"] is not None:
                        raise AssertionError(
                            "temporary provider outage fabricated an executor terminal receipt"
                        )
                    if snapshot["run_status"] in {"failed", "cancelled", "dead_letter"}:
                        raise AssertionError(
                            f"temporary provider outage terminalized the Run: {snapshot}"
                        )
                    await asyncio.sleep(0.25)
                raise AssertionError(
                    f"provider probe failure was not persisted: {await state()}"
                )

            async def cancelled_and_released(snapshot):
                if (
                    snapshot["run_status"],
                    snapshot["attempt_status"],
                    snapshot["lease_status"],
                ) != ("cancelled", "cancelled", "released"):
                    return False
                await asyncio.to_thread(stack.assert_no_run_resources, run_id)
                return snapshot

            async def complete_cancellation_case(
                *, fault_evidence, expected_answer_events: int,
                expected_cleanup_successes: int = 1,
            ):
                final = await poll(
                    cancelled_and_released,
                    timeout=180,
                    expected_terminal_statuses=("cancelled",),
                )
                assert final["attempt_id"] == running["attempt_id"]
                facts = await outcome_facts()
                assert facts["attempts"] == 1
                assert facts["cancel_requests"] == 1
                assert facts["cancelled_events"] == 1
                assert facts["succeeded_events"] == 0
                assert facts["failed_events"] == 0
                assert facts["answer_deltas"] == expected_answer_events
                assert facts["answer_completions"] == expected_answer_events
                assert facts["assistant_messages"] == 0
                assert facts["committed_turns"] == 0
                assert facts["artifacts"] == 0
                assert facts["cleanup_failures"] == (1 if fault == "stop_retry" else 0)
                assert facts["cleanup_successes"] == expected_cleanup_successes

                repeated = await http.post(f"/api/ai/runs/{run_id}/cancel")
                assert repeated.status_code == 404, repeated.text
                assert repeated.json() == {"detail": "active_run_not_found"}
                assert await state() == final
                await asyncio.to_thread(stack.assert_no_run_resources, run_id)
                write_lifecycle_control_evidence(
                    tmp_path,
                    image_id=lifecycle_control_image[0],
                    source_commit=lifecycle_control_image[1],
                    stack=stack,
                    scenario={
                        "run_id": run_id,
                        "attempt_id": final["attempt_id"],
                        "provider": "docker",
                        "fault_scenario": fault,
                        "intermediate": fault_evidence,
                        "final_state": final,
                        "counts": facts,
                        "repeated_cancel_http_status": repeated.status_code,
                        "production_resources_absent_before_teardown": True,
                    },
                )
                return final

            await asyncio.to_thread(stack.start_worker)

            async def entered(snapshot):
                if (
                    snapshot["executor_status"] not in {"accepted", "running"}
                    or snapshot["has_reconciliation_context"] is not True
                ):
                    return None
                containers = await asyncio.to_thread(
                    stack._client.containers.list, all=True,
                    filters={"label": [f"ai-platform.run_id={run_id}", "ai-platform.owner=sandbox-runtime"]},
                )
                for container in containers:
                    probe = await asyncio.to_thread(container.exec_run, [
                        "python", "-c",
                        "from pathlib import Path; import json; "
                        "p=next(Path('/workspace').rglob('.n01-entered'),None); "
                        "print(p.read_text() if p else '{}')",
                    ])
                    if probe.exit_code == 0:
                        marker = json.loads(probe.output)
                        if marker.get("run_id") == run_id:
                            assert marker["attempt_id"] == snapshot["attempt_id"]
                            if fault == "executor_timeout":
                                assert marker["resource_limits"] == {
                                    "max_seconds": SHORT_EXECUTION_MAX_SECONDS,
                                }
                            return container
                return None

            executor = await poll(entered)
            running = await state()
            assert running["run_status"] == running["attempt_status"] == "running"
            assert running["has_reconciliation_context"] is True
            assert running["lease_status"] == "active"
            assert running["executor_terminal_json"] is None
            restart_evidence = None
            terminal_ack_evidence = None
            executor_ack_evidence = None
            terminal_callback_loss_evidence = None
            process_fault_evidence = None
            worker = stack._containers["worker"]

            if fault == "running_cancel":
                before_foreign_cancel = await state()
                foreign_cancel = await http.post(
                    f"/api/ai/runs/{run_id}/cancel",
                    headers={**headers, "X-AI-User-ID": "88000002"},
                )
                assert foreign_cancel.status_code == 404, foreign_cancel.text
                assert foreign_cancel.json() == {"detail": "active_run_not_found"}
                assert await state() == before_foreign_cancel
                assert (await outcome_facts())["answer_deltas"] == 0

                owner_cancel = await http.post(f"/api/ai/runs/{run_id}/cancel")
                assert owner_cancel.status_code == 200, owner_cancel.text
                assert owner_cancel.json() == {
                    "run_id": run_id,
                    "session_id": None,
                    "status": "cancel_requested",
                }
                after_owner_cancel = await state()
                assert after_owner_cancel["attempt_id"] == running["attempt_id"]
                assert after_owner_cancel["cancel_requested_at"] is not None
                assert after_owner_cancel["cancel_requested_by"] == stack.user_id
                assert after_owner_cancel["lease_status"] == "released"
                await complete_cancellation_case(
                    fault_evidence={
                        "foreign_cancel_http_status": foreign_cancel.status_code,
                        "state_unchanged_after_foreign_cancel": True,
                        "owner_cancel_http_status": owner_cancel.status_code,
                        "owner_cancel_state": after_owner_cancel,
                    },
                    expected_answer_events=0,
                )
                return

            if fault == "stop_retry":
                stopped_proxy_id = await asyncio.to_thread(stack.stop_docker_proxy)
                try:
                    first_cancel = await http.post(f"/api/ai/runs/{run_id}/cancel")
                    assert first_cancel.status_code == 502, first_cancel.text
                    assert first_cancel.json() == {
                        "detail": "sandbox_runtime_cleanup_failed",
                    }
                    after_failed_stop = await state()
                    assert after_failed_stop["run_status"] == "running"
                    assert after_failed_stop["attempt_status"] == "cancel_requested"
                    assert after_failed_stop["attempt_id"] == running["attempt_id"]
                    assert after_failed_stop["cancel_requested_at"] is not None
                    assert after_failed_stop["cancel_requested_by"] == stack.user_id
                    assert after_failed_stop["lease_status"] == "active"
                    assert after_failed_stop["executor_terminal_json"] is None
                    assert after_failed_stop["executor_status"] == "running"
                    failed_stop_counts = await outcome_facts()
                    assert failed_stop_counts["cancel_requests"] == 1
                    assert failed_stop_counts["cleanup_failures"] == 1
                    assert failed_stop_counts["answer_deltas"] == 0
                    executor.reload()
                    assert executor.status == "running"
                finally:
                    restored_proxy_id = await asyncio.to_thread(
                        stack.start_docker_proxy,
                    )
                assert restored_proxy_id == stopped_proxy_id

                retry_cancel = await http.post(f"/api/ai/runs/{run_id}/cancel")
                assert retry_cancel.status_code == 200, retry_cancel.text
                assert retry_cancel.json()["status"] == "cancel_requested"
                after_retry = await state()
                assert after_retry["attempt_id"] == running["attempt_id"]
                assert after_retry["lease_status"] == "released"
                assert after_retry["cancel_requested_by"] == stack.user_id
                await complete_cancellation_case(
                    fault_evidence={
                        "docker_proxy_container_id": stopped_proxy_id,
                        "first_cancel_http_status": first_cancel.status_code,
                        "first_cancel_state": after_failed_stop,
                        "counts_after_failed_stop": failed_stop_counts,
                        "provider_restored": True,
                        "retry_cancel_http_status": retry_cancel.status_code,
                        "state_after_retry": after_retry,
                    },
                    expected_answer_events=0,
                )
                return

            if fault == "receipt_cancel":
                stopped_worker_id = await asyncio.to_thread(
                    stack.stop_worker, timeout_seconds=5,
                )
                worker.reload()
                assert stopped_worker_id == worker.id
                assert worker.status != "running"
                release_executor_runner(executor)

                async def receipt_pending(snapshot):
                    return (
                        snapshot
                        if snapshot["executor_terminal_json"] is not None
                        and snapshot["executor_reconciliation_status"] == "pending"
                        else False
                    )

                before_cancel = await poll(receipt_pending, timeout=120)
                receipt = before_cancel["executor_terminal_json"]
                assert receipt["status"] == "completed"
                assert receipt["sdk_used"] is False
                assert before_cancel["executor_status"] == "completed"
                assert before_cancel["has_reconciliation_context"] is True
                assert before_cancel["run_status"] == "running"
                assert before_cancel["attempt_status"] == "running"
                assert before_cancel["attempt_id"] == running["attempt_id"]
                assert before_cancel["lease_status"] == "active"
                assert (await outcome_facts())["assistant_messages"] == 0

                owner_cancel = await http.post(f"/api/ai/runs/{run_id}/cancel")
                assert owner_cancel.status_code == 200, owner_cancel.text
                assert owner_cancel.json()["status"] == "cancel_requested"
                after_cancel = await state()
                assert after_cancel["attempt_id"] == running["attempt_id"]
                assert after_cancel["cancel_requested_at"] is not None
                assert after_cancel["cancel_requested_by"] == stack.user_id
                assert after_cancel["lease_status"] == "active"
                assert after_cancel["executor_status"] == "completed"
                assert after_cancel["executor_reconciliation_status"] == "pending"
                assert after_cancel["executor_terminal_json"] == receipt

                restarted_worker_id = await asyncio.to_thread(stack.start_worker)
                assert restarted_worker_id == stopped_worker_id
                await complete_cancellation_case(
                    fault_evidence={
                        "worker_container_id": stopped_worker_id,
                        "persisted_terminal_receipt_before_cancel": receipt,
                        "state_before_cancel": before_cancel,
                        "owner_cancel_http_status": owner_cancel.status_code,
                        "state_after_cancel": after_cancel,
                        "worker_restarted": True,
                    },
                    expected_answer_events=1,
                    # The API defers provider stop while the terminal receipt is
                    # pending; the reconciler owns physical release after settlement.
                    expected_cleanup_successes=0,
                )
                return

            if fault == "executor_timeout":
                async with connection() as conn:
                    admitted_limits = await (await conn.execute(
                        "select input_json->'input'->'resource_limits' as resource_limits "
                        "from runs where tenant_id=%s and id=%s",
                        (stack.tenant_id, run_id),
                    )).fetchone()
                assert admitted_limits["resource_limits"] == {
                    "max_seconds": SHORT_EXECUTION_MAX_SECONDS,
                }

                async def deadline_failed_and_released(snapshot):
                    if (
                        snapshot["run_status"],
                        snapshot["attempt_status"],
                        snapshot["lease_status"],
                        snapshot["executor_reconciliation_status"],
                    ) != ("failed", "failed", "released", "finalized"):
                        return False
                    await asyncio.to_thread(stack.assert_no_run_resources, run_id)
                    return snapshot

                timed_out = await poll(
                    deadline_failed_and_released,
                    timeout=180,
                    expected_terminal_statuses=("failed",),
                )
                receipt = timed_out["executor_terminal_json"]
                assert timed_out["error_code"] == "executor_deadline_exceeded"
                assert timed_out["executor_status"] == "failed"
                assert receipt["status"] == "failed"
                assert receipt["error_code"] == "executor_deadline_exceeded"
                assert receipt["requested_max_seconds"] == SHORT_EXECUTION_MAX_SECONDS
                # This executor-reported monotonic interval starts immediately before
                # the runner call; it excludes container startup and polling time.
                assert (
                    (SHORT_EXECUTION_MAX_SECONDS - 1.0) * 1_000
                    <= receipt["timeout_elapsed_ms"]
                    <= 40_000
                )
                assert timed_out["attempt_id"] == running["attempt_id"]
                facts = await outcome_facts()
                assert facts["attempts"] == 1
                assert facts["turns"] == facts["committed_turns"] == 0
                assert facts["answer_deltas"] == facts["answer_completions"] == 0
                assert facts["assistant_messages"] == facts["artifacts"] == 0
                assert facts["cancel_requests"] == facts["cancelled_events"] == 0
                assert facts["succeeded_events"] == 0
                assert facts["failed_events"] == 1
                write_lifecycle_control_evidence(
                    tmp_path,
                    image_id=lifecycle_control_image[0],
                    source_commit=lifecycle_control_image[1],
                    stack=stack,
                    scenario={
                        "run_id": run_id,
                        "attempt_id": timed_out["attempt_id"],
                        "provider": "docker",
                        "fault_scenario": fault,
                        "admitted_resource_limits": admitted_limits["resource_limits"],
                        "executor_terminal_receipt": receipt,
                        "final_state": timed_out,
                        "counts": facts,
                        "production_resources_absent_before_teardown": True,
                    },
                )
                return

            if fault == "executor_kill":
                process_fault_evidence = await asyncio.to_thread(
                    kill_executor_process, executor,
                )

                async def executor_lost_and_released(snapshot):
                    if (
                        snapshot["run_status"],
                        snapshot["attempt_status"],
                        snapshot["lease_status"],
                        snapshot["executor_reconciliation_status"],
                    ) != ("failed", "failed", "released", "finalized"):
                        return False
                    await asyncio.to_thread(stack.assert_no_run_resources, run_id)
                    return snapshot

                lost = await poll(
                    executor_lost_and_released,
                    timeout=180,
                    expected_terminal_statuses=("failed",),
                )
                receipt = lost["executor_terminal_json"]
                assert receipt["error_code"] == "sandbox_executor_lost"
                assert lost["error_code"] == "sandbox_executor_lost"
                assert lost["attempt_id"] == running["attempt_id"]
                facts = await outcome_facts()
                assert facts["attempts"] == 1
                assert facts["turns"] == facts["committed_turns"] == 0
                assert facts["answer_deltas"] == facts["answer_completions"] == 0
                assert facts["assistant_messages"] == facts["artifacts"] == 0
                assert facts["failed_events"] == 1
                assert facts["succeeded_events"] == facts["cancelled_events"] == 0
                write_lifecycle_control_evidence(
                    tmp_path,
                    image_id=lifecycle_control_image[0],
                    source_commit=lifecycle_control_image[1],
                    stack=stack,
                    scenario={
                        "run_id": run_id,
                        "attempt_id": lost["attempt_id"],
                        "provider": "docker",
                        "fault_scenario": fault,
                        "process_fault": process_fault_evidence,
                        "executor_terminal_receipt": receipt,
                        "final_state": lost,
                        "counts": facts,
                        "production_resources_absent_before_teardown": True,
                    },
                )
                return

            if fault == "provider_unreachable":
                process_fault_evidence = {
                    "executor_pause": await asyncio.to_thread(
                        pause_executor_process, executor,
                    ),
                }
                assert process_fault_evidence["executor_pause"][
                    "container_paused"
                ] is True
                stopped_proxy_id = await asyncio.to_thread(stack.stop_docker_proxy)
                try:
                    probe_failure = await wait_for_probe_failure()
                    assert probe_failure["run_status"] == "running"
                    assert probe_failure["attempt_status"] == "running"
                    assert probe_failure["attempt_id"] == running["attempt_id"]
                    assert probe_failure["lease_status"] == "active"
                    assert probe_failure["executor_status"] == "running"
                    assert probe_failure["executor_terminal_json"] is None
                    assert probe_failure["executor_reconciliation_status"] == (
                        "waiting_terminal"
                    )
                    executor.reload()
                    assert executor.attrs.get("State", {}).get("Paused") is True
                    process_fault_evidence["probe_failure_snapshot"] = probe_failure
                finally:
                    restored_proxy_id = await asyncio.to_thread(
                        stack.start_docker_proxy,
                    )
                assert restored_proxy_id == stopped_proxy_id
                process_fault_evidence["docker_proxy_container_id"] = stopped_proxy_id
                process_fault_evidence["provider_restored"] = True
                process_fault_evidence["executor_resume"] = await asyncio.to_thread(
                    resume_executor_process, executor,
                )
                assert process_fault_evidence["executor_resume"][
                    "container_paused"
                ] is False

            if fault == "worker_restart":
                before_log = await asyncio.to_thread(
                    stack.capture_worker_logs, "before-restart",
                )
                stopped_id = await asyncio.to_thread(stack.kill_worker)
                worker.reload()
                assert stopped_id == worker.id
                assert worker.status != "running"

                restarted_id = await asyncio.to_thread(stack.start_worker)
                worker.reload()
                assert restarted_id == stopped_id
                assert worker.status == "running"
                after_log = await asyncio.to_thread(
                    stack.capture_worker_logs, "after-restart",
                )
                after_restart = await state()
                assert after_restart["run_status"] == "running"
                assert after_restart["attempt_status"] == "running"
                assert after_restart["attempt_id"] == running["attempt_id"]
                assert after_restart["lease_status"] == "active"

                barrier = await asyncio.to_thread(executor.exec_run, [
                    "python", "-c",
                    "from pathlib import Path; import json; "
                    "p=next(Path('/workspace').rglob('.n01-entered')); "
                    "r=p.with_name('.n01-release'); "
                    "print(json.dumps({'entered': json.loads(p.read_text()), 'released': r.exists()}))",
                ])
                assert barrier.exit_code == 0, barrier.output
                barrier_state = json.loads(barrier.output)
                assert barrier_state["entered"]["run_id"] == run_id
                assert barrier_state["entered"]["attempt_id"] == running["attempt_id"]
                assert barrier_state["released"] is False
                restart_evidence = {
                    "worker_container_id": stopped_id,
                    "worker_status_after_restart": worker.status,
                    "logs_before_restart": before_log,
                    "logs_after_restart": after_log,
                    "attempt_id_before_and_after": running["attempt_id"],
                    "runner_barrier_held_during_restart": True,
                }
            elif fault in {"terminal_ack_loss", "terminal_callback_loss"}:
                stopped_id = await asyncio.to_thread(
                    stack.stop_worker, timeout_seconds=5,
                )
                worker.reload()
                assert stopped_id == worker.id
                assert worker.status != "running"

            await asyncio.to_thread(release_executor_runner, executor)

            if fault == "terminal_ack_loss":
                deadline = time.monotonic() + 45
                terminal_ack_marker = {}
                while time.monotonic() < deadline:
                    terminal_ack_marker = await read_terminal_ack_marker()
                    retry = terminal_ack_marker.get("retry")
                    if isinstance(retry, dict):
                        break
                    await asyncio.sleep(0.2)

                assert terminal_ack_marker.get("run_id") == run_id
                assert terminal_ack_marker.get("attempt_id") == running["attempt_id"]
                assert terminal_ack_marker.get("status") == "completed"
                assert terminal_ack_marker.get("fault_type") == "content_length_truncation"
                assert terminal_ack_marker.get("original_http_status") == 200
                assert terminal_ack_marker.get("advertised_content_length") == (
                    terminal_ack_marker.get("transmitted_body_length") + 1
                )
                assert len(terminal_ack_marker.get("request_body_sha256", "")) == 64
                assert len(terminal_ack_marker.get("response_body_sha256", "")) == 64
                assert not any("token" in key.casefold() for key in terminal_ack_marker)
                retry = terminal_ack_marker.get("retry")
                assert isinstance(retry, dict)
                assert retry["run_id"] == run_id
                assert retry["attempt_id"] == running["attempt_id"]
                assert retry["batch_id"] == terminal_ack_marker["batch_id"]
                assert retry["status"] == "completed"
                assert retry["request_body_sha256"] == terminal_ack_marker["request_body_sha256"]
                assert retry["http_status"] == 200
                assert retry["accepted"] is True
                assert retry["deduplicated"] is True
                assert len(retry["response_body_sha256"]) == 64

                while time.monotonic() < deadline:
                    executor_ack_evidence = await read_executor_task_status(executor)
                    if executor_ack_evidence["status"] == "completed":
                        break
                    await asyncio.sleep(0.1)
                assert executor_ack_evidence["status"] == "completed"
                transport_error = str(executor_ack_evidence.get("error_message") or "")
                assert transport_error.startswith("RemoteProtocolError:"), transport_error
                assert "complete message body" in transport_error.casefold(), transport_error

                before_reconcile = await state()
                assert before_reconcile["run_status"] == "running"
                assert before_reconcile["attempt_status"] == "running"
                assert before_reconcile["attempt_id"] == running["attempt_id"]
                assert before_reconcile["lease_status"] == "active"
                assert before_reconcile["executor_status"] == "completed"
                assert before_reconcile["executor_reconciliation_status"] == "pending"
                assert before_reconcile["executor_terminal_json"] is not None
                assert before_reconcile["executor_terminal_json"]["answer_receipt"]["delta_count"] == 1

                (tmp_path / "terminal-ack-fault.json").write_text(
                    json.dumps(terminal_ack_marker, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                terminal_ack_evidence = terminal_ack_marker
                executor_ack_evidence = {
                    "status": executor_ack_evidence["status"],
                    "transport_error_type": transport_error.split(":", 1)[0],
                    "transport_failure_observed": True,
                    "retry_batch_id": retry["batch_id"],
                }

                restarted_id = await asyncio.to_thread(stack.start_worker)
                worker.reload()
                assert restarted_id == stopped_id
                assert worker.status == "running"
                restart_evidence = {
                    "worker_container_id": stopped_id,
                    "worker_status_after_restart": worker.status,
                    "attempt_id_before_and_after": running["attempt_id"],
                    "terminal_receipt_persisted_before_reconciliation": True,
                }
            elif fault == "terminal_callback_loss":
                deadline = time.monotonic() + 45
                terminal_callback_marker = {}
                executor_task_status = {}
                while time.monotonic() < deadline:
                    terminal_callback_marker = await read_terminal_callback_loss_marker()
                    executor_task_status = await read_executor_task_status(executor)
                    if (
                        terminal_callback_marker.get("count", 0) >= 1
                        and executor_task_status.get("status") == "completed"
                        and isinstance(executor_task_status.get("terminal_result"), dict)
                        and str(executor_task_status.get("error_message") or "").startswith(
                            "HTTPStatusError:"
                        )
                        and "503 Service Unavailable" in str(
                            executor_task_status.get("error_message") or ""
                        )
                    ):
                        break
                    await asyncio.sleep(0.2)

                assert terminal_callback_marker.get("scope") == {
                    "run_id": run_id,
                    "attempt_id": running["attempt_id"],
                }
                assert terminal_callback_marker.get("status") == "completed"
                assert isinstance(terminal_callback_marker.get("batch_id"), str)
                assert terminal_callback_marker.get("count", 0) >= 1
                assert terminal_callback_marker.get("fault_type") == (
                    "terminal_callback_rejected"
                )
                assert terminal_callback_marker.get("http_status") == 503
                assert len(terminal_callback_marker.get("body_sha256", "")) == 64
                assert len(
                    terminal_callback_marker.get(
                        "terminal_result_fields_sha256", ""
                    )
                ) == 64
                assert not any(
                    "token" in key.casefold() for key in terminal_callback_marker
                )
                assert executor_task_status["run_id"] == run_id
                assert executor_task_status["attempt_id"] == running["attempt_id"]
                transport_error = str(
                    executor_task_status.get("error_message") or ""
                )
                assert transport_error.startswith("HTTPStatusError:")
                assert "503 Service Unavailable" in transport_error
                terminal_result = executor_task_status["terminal_result"]
                assert terminal_result["status"] == "completed"
                assert terminal_result["run_id"] == run_id
                assert terminal_result["sdk_used"] is False
                assert terminal_result["answer_receipt"]["delta_count"] == 1
                assert terminal_result["response_files"] == list(SELECTED_FILES)
                assert terminal_result["provider_session_final_sequence"] >= 2
                terminal_result_key_fields = {
                    key: terminal_result[key]
                    for key in (
                        "status",
                        "run_id",
                        "answer_receipt",
                        "response_files",
                        "response_file_descriptors",
                        "provider_session_final_sequence",
                    )
                    if key in terminal_result
                }
                terminal_result_key_digest = hashlib.sha256(
                    json.dumps(
                        terminal_result_key_fields,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                assert terminal_callback_marker["terminal_result_fields_sha256"] == (
                    terminal_result_key_digest
                )

                before_probe = await state()
                assert before_probe["run_status"] == "running"
                assert before_probe["attempt_status"] == "running"
                assert before_probe["attempt_id"] == running["attempt_id"]
                assert before_probe["lease_status"] == "active"
                assert before_probe["executor_terminal_json"] is None
                assert before_probe["has_reconciliation_context"] is True
                async with connection() as conn:
                    persisted_before_probe = await (await conn.execute(
                        "select "
                        "(select count(*) from run_events where tenant_id=%s "
                        "and run_id=%s and event_type='message.delta') as answer_deltas, "
                        "(select count(*) from run_events where tenant_id=%s "
                        "and run_id=%s and event_type='message.completed') as answer_completions, "
                        "(select count(*) from provider_session_entries entry "
                        "join provider_session_epochs epoch on epoch.id=entry.epoch_id "
                        "where epoch.tenant_id=%s and epoch.writer_run_id=%s "
                        "and epoch.writer_attempt_id=%s) as session_entries, "
                        "(select count(*) from provider_session_append_receipts receipt "
                        "join provider_session_epochs epoch on epoch.id=receipt.epoch_id "
                        "where epoch.tenant_id=%s and epoch.writer_run_id=%s "
                        "and epoch.writer_attempt_id=%s) as session_append_receipts",
                        (
                            stack.tenant_id, run_id,
                            stack.tenant_id, run_id,
                            stack.tenant_id, run_id, running["attempt_id"],
                            stack.tenant_id, run_id, running["attempt_id"],
                        ),
                    )).fetchone()
                assert persisted_before_probe == {
                    "answer_deltas": 1,
                    "answer_completions": 1,
                    "session_entries": 2,
                    "session_append_receipts": 1,
                }

                retry_deadline = time.monotonic() + 20
                while time.monotonic() < retry_deadline:
                    terminal_callback_marker = (
                        await read_terminal_callback_loss_marker()
                    )
                    if terminal_callback_marker.get("count", 0) >= 2:
                        break
                    await asyncio.sleep(0.2)
                assert terminal_callback_marker.get("count", 0) >= 2
                terminal_callback_loss_evidence = {
                    "marker_before_probe": terminal_callback_marker,
                    "executor_status_before_probe": {
                        "status": executor_task_status["status"],
                        "run_id": executor_task_status["run_id"],
                        "attempt_id": executor_task_status["attempt_id"],
                        "terminal_callback_http_503_observed": True,
                        "terminal_result_fields_sha256": terminal_result_key_digest,
                    },
                    "postgres_before_probe": {
                        "run_status": before_probe["run_status"],
                        "attempt_status": before_probe["attempt_status"],
                        "lease_status": before_probe["lease_status"],
                        "executor_terminal_json_present": False,
                        "has_reconciliation_context": before_probe[
                            "has_reconciliation_context"
                        ],
                        **persisted_before_probe,
                    },
                }
                (tmp_path / "terminal-callback-loss.json").write_text(
                    json.dumps(terminal_callback_loss_evidence, indent=2) + "\n",
                    encoding="utf-8",
                )

                restarted_id = await asyncio.to_thread(stack.start_worker)
                worker.reload()
                assert restarted_id == stopped_id
                assert worker.status == "running"
                restart_evidence = {
                    "worker_container_id": stopped_id,
                    "worker_status_after_restart": worker.status,
                    "attempt_id_before_and_after": running["attempt_id"],
                    "terminal_callback_rejection_remains_enabled": True,
                    "retry_rejections_observed_before_probe": terminal_callback_marker[
                        "count"
                    ],
                }

            async def reclaimed(snapshot):
                if (snapshot["run_status"], snapshot["attempt_status"], snapshot["lease_status"],
                    snapshot["executor_reconciliation_status"]) != (
                    "succeeded", "succeeded", "released", "finalized",
                ):
                    return False
                await asyncio.to_thread(stack.assert_no_run_resources, run_id)
                return snapshot

            settlement_timeout = 300 if fault in {
                "worker_restart", "terminal_ack_loss", "terminal_callback_loss",
            } else 120
            settled = await poll(reclaimed, timeout=settlement_timeout)
            assert settled["attempt_id"] == running["attempt_id"]
            assert settled["executor_status"] == "completed"
            if fault == "terminal_callback_loss":
                persisted_terminal_result = settled["executor_terminal_json"]
                assert isinstance(persisted_terminal_result, dict)
                for key in (
                    "status",
                    "run_id",
                    "answer_receipt",
                    "response_files",
                    "response_file_descriptors",
                    "provider_session_final_sequence",
                    "sdk_used",
                ):
                    assert persisted_terminal_result.get(key) == terminal_result.get(key)
                marker_after_probe = await read_terminal_callback_loss_marker()
                assert marker_after_probe.get("count", 0) >= terminal_callback_marker[
                    "count"
                ]
                terminal_callback_loss_evidence["marker_after_probe"] = (
                    marker_after_probe
                )
                terminal_callback_loss_evidence["postgres_after_probe"] = {
                    "run_status": settled["run_status"],
                    "attempt_status": settled["attempt_status"],
                    "lease_status": settled["lease_status"],
                    "executor_status": settled["executor_status"],
                    "executor_reconciliation_status": settled[
                        "executor_reconciliation_status"
                    ],
                    "executor_terminal_json_present": True,
                    "persisted_terminal_result_fields_match_executor_status": True,
                }

            readback = await http.get(f"/api/ai/runs/{run_id}")
            assert readback.status_code == 200, readback.text
            payload = readback.json()
            assert payload["status"] == "succeeded"
            assert TEST_ANSWER in json.dumps(payload, ensure_ascii=False)
            artifacts = payload["artifacts"]
            expected = list(SELECTED_FILES) if selected_files else []
            assert len(artifacts) == len(expected)
            async with connection() as conn:
                ordered_artifacts = await (await conn.execute(
                    "select id, manifest_json from artifacts where tenant_id=%s and run_id=%s "
                    "order by (manifest_json->>'delivery_position')::int",
                    (stack.tenant_id, run_id),
                )).fetchall()
                messages = await (await conn.execute(
                    "select metadata_json from messages where tenant_id=%s and run_id=%s and role='assistant'",
                    (stack.tenant_id, run_id),
                )).fetchall()
            ordered_ids = [artifact["id"] for artifact in ordered_artifacts]
            assert len(messages) == 1
            assert messages[0]["metadata_json"]["artifact_ids"] == ordered_ids
            assert [artifact["manifest_json"]["delivery_position"] for artifact in ordered_artifacts] == list(range(len(expected)))
            assert [artifact["manifest_json"]["delivery_role"] for artifact in ordered_artifacts] == (["primary", "supporting"] if selected_files else [])
            assert set(ordered_ids) == {artifact["id"] for artifact in artifacts}
            # Run cards are ordered by created_at, which can tie within a commit.
            # Message attachment IDs and delivery_position own selection order.
            cards_by_id = {artifact["id"]: artifact for artifact in artifacts}
            artifacts = [cards_by_id[artifact_id] for artifact_id in ordered_ids]
            digests = []
            for artifact, relative_path in zip(artifacts, expected, strict=True):
                download = await http.get(f"/api/ai/artifacts/{artifact['id']}/download")
                assert download.status_code == 200, download.text
                assert download.content == SELECTED_FILE_CONTENTS[relative_path]
                digests.append(hashlib.sha256(download.content).hexdigest())
                denied = await http.get(
                    f"/api/ai/artifacts/{artifact['id']}/download",
                    headers={**headers, "X-AI-User-ID": "88000002"},
                )
                assert denied.status_code in {403, 404}

            async with connection() as conn:
                counts = await (await conn.execute(
                    "select (select count(*) from run_attempts where run_id=%s) as attempts, "
                    "(select count(*) from provider_turn_receipts where run_id=%s) as turns, "
                    "(select count(*) from provider_turn_receipts where run_id=%s and state='committed') as committed, "
                    "(select count(*) from run_events where run_id=%s and event_type='message.completed') as answers, "
                    "(select count(*) from run_events where run_id=%s and event_type='message.delta') as deltas, "
                    "(select count(*) from artifacts where tenant_id=%s and run_id=%s) as artifacts",
                    (run_id, run_id, run_id, run_id, run_id, stack.tenant_id, run_id),
                )).fetchone()
                assert counts == {
                    "attempts": 1,
                    "turns": 1,
                    "committed": 1,
                    "answers": 1,
                    "deltas": 1,
                    "artifacts": len(expected),
                }
                coverage = await (await conn.execute(
                    "select p.state, p.committed_coverage_sha256, p.final_sequence, "
                    "e.coverage_source_sha256, e.writer_run_id "
                    "from provider_turn_receipts p join provider_session_epochs e on e.id=p.epoch_id "
                    "where p.run_id=%s", (run_id,),
                )).fetchone()
                assert coverage["committed_coverage_sha256"] == coverage["coverage_source_sha256"]
                assert len(coverage["coverage_source_sha256"]) == 64
                assert coverage["writer_run_id"] is None
                assert coverage["final_sequence"] >= 2

            async with Redis.from_url(stack.redis_url) as redis:
                prefix = stack.environment["QUEUE_KEY_PREFIX"]
                for suffix in ("queued", "processing", "processing-meta", "retry-meta", "dead-letter", "queued-meta", "queued-run-index", "queued-order"):
                    assert not await redis.exists(f"{prefix}:{suffix}"), suffix

            write_lifecycle_control_evidence(
                tmp_path,
                image_id=lifecycle_control_image[0],
                source_commit=lifecycle_control_image[1],
                stack=stack,
                scenario={
                    "run_id": run_id,
                    "attempt_id": settled["attempt_id"],
                    "provider": "docker",
                    "selected_files": expected,
                    "download_sha256": digests,
                    "counts": counts,
                    "coverage": coverage,
                    "message_attachment_ids": ordered_ids,
                    "fault_scenario": fault,
                    "worker_restart": restart_evidence,
                    "terminal_ack_fault": terminal_ack_evidence,
                    "executor_ack_delivery": executor_ack_evidence,
                    "terminal_callback_loss": terminal_callback_loss_evidence,
                    "process_fault": process_fault_evidence,
                    "production_resources_absent_before_teardown": True,
                },
            )

    with LifecycleDockerStack(*lifecycle_control_image, evidence_dir=tmp_path) as stack:
        asyncio.run(scenario(stack))


def test_queued_run_owner_cancel_clears_queue_and_persists_once(
    lifecycle_control_image, tmp_path,
):
    """C layer: a queued Run cancels before Worker admission or Attempt creation."""
    from contextlib import asynccontextmanager

    import httpx
    import psycopg
    from psycopg.rows import dict_row
    from redis.asyncio import Redis

    from app import schema_migrations
    from tests.support.run_lifecycle_docker import (
        LifecycleDockerStack,
        write_lifecycle_control_evidence,
    )
    from tests.support.run_lifecycle_seed import seed_admission

    async def scenario(stack):
        @asynccontextmanager
        async def connection():
            async with await psycopg.AsyncConnection.connect(
                stack.postgres_dsn, row_factory=dict_row,
            ) as conn:
                yield conn

        async def index_connection():
            return await psycopg.AsyncConnection.connect(
                stack.postgres_dsn, autocommit=True, row_factory=dict_row,
            )

        await schema_migrations.apply_migrations(
            transaction_factory=connection,
            index_connection_factory=index_connection,
        )
        async with connection() as conn:
            await seed_admission(
                conn,
                workspace_id=stack.workspace_id,
                user_id=stack.user_id,
                encryption_key=stack.encryption_key,
            )
            for table in ("sessions", "runs", "run_attempts", "sandbox_leases"):
                count = await (await conn.execute(
                    f"select count(*) as n from {table}"
                )).fetchone()
                assert count["n"] == 0

        await asyncio.to_thread(stack.start_api)
        headers = {
            "X-AI-User-ID": stack.user_id,
            "X-AI-Tenant-ID": stack.tenant_id,
            "X-AI-User-Name": "N02",
            "X-AI-Gateway-Secret": stack.gateway_secret,
            "X-AI-Permissions": "artifact:download",
        }
        async with httpx.AsyncClient(
            base_url=stack.api_base,
            headers=headers,
            trust_env=False,
            timeout=30,
        ) as http:
            admission = await http.post("/api/ai/runs", json={
                "tenant_id": stack.tenant_id,
                "workspace_id": stack.workspace_id,
                "agent_id": "general-agent",
                "capability_id": "general_chat",
                "title": "N02 queued cancellation",
                "input": {"message": "Cancel before Worker admission."},
            })
            assert admission.status_code == 200, admission.text
            admitted = admission.json()
            assert admitted["status"] == "queued"
            run_id = admitted["run_id"]

            async def persisted_facts():
                async with connection() as conn:
                    return await (await conn.execute(
                        "select r.status, r.cancel_requested_at, r.cancel_requested_by, "
                        "(select count(*) from run_events where tenant_id=%s and run_id=%s) "
                        "as run_event_count, "
                        "(select count(*) from run_events where tenant_id=%s and run_id=%s "
                        "and event_type='cancel_requested') as cancel_requested_events, "
                        "(select count(*) from run_events where tenant_id=%s and run_id=%s "
                        "and event_type='run_cancelled') as run_cancelled_events, "
                        "(select count(*) from run_attempts where tenant_id=%s and run_id=%s) "
                        "as attempts, "
                        "(select count(*) from sandbox_leases where tenant_id=%s and run_id=%s) "
                        "as leases, "
                        "(select count(*) from provider_turn_receipts where tenant_id=%s and run_id=%s) "
                        "as provider_turns, "
                        "(select count(*) from messages where tenant_id=%s and run_id=%s "
                        "and role='assistant') as assistant_messages, "
                        "(select count(*) from artifacts where tenant_id=%s and run_id=%s) "
                        "as artifacts, "
                        "(select count(*) from audit_logs where tenant_id=%s and target_type='run' "
                        "and target_id=%s and action in ('run.cancel', 'run.cancelled')) "
                        "as cancellation_audits "
                        "from runs r where r.tenant_id=%s and r.id=%s",
                        (
                            stack.tenant_id, run_id,
                            stack.tenant_id, run_id,
                            stack.tenant_id, run_id,
                            stack.tenant_id, run_id,
                            stack.tenant_id, run_id,
                            stack.tenant_id, run_id,
                            stack.tenant_id, run_id,
                            stack.tenant_id, run_id,
                            stack.tenant_id, run_id,
                            stack.tenant_id, run_id,
                        ),
                    )).fetchone()

            async def redis_queue_facts():
                prefix = stack.environment["QUEUE_KEY_PREFIX"]
                queued_key = f"{prefix}:queued"
                index_key = f"{prefix}:queued-run-index"
                metadata_key = f"{prefix}:queued-meta"
                order_key = f"{prefix}:queued-order"
                async with Redis.from_url(stack.redis_url) as redis:
                    queued = [
                        json.loads(raw)
                        for raw in await redis.lrange(queued_key, 0, -1)
                    ]
                    index_value = await redis.hget(
                        index_key, f"{stack.tenant_id}:{run_id}"
                    )
                    # Read metadata independently of the index so an orphaned
                    # entry cannot disappear from the cleanup assertion.
                    metadata = {
                        message_id.decode("utf-8"): json.loads(raw_metadata)
                        for message_id, raw_metadata in (
                            await redis.hgetall(metadata_key)
                        ).items()
                    }
                    order_ids = {
                        item.decode("utf-8") if isinstance(item, bytes) else str(item)
                        for item in await redis.zrange(order_key, 0, -1)
                    }
                    return queued, index_value, metadata, order_ids

            (
                queued_before,
                run_index_before,
                metadata_before,
                order_before,
            ) = await redis_queue_facts()
            queued_for_run = [
                payload for payload in queued_before
                if payload.get("tenant_id") == stack.tenant_id
                and payload.get("run_id") == run_id
            ]
            assert len(queued_for_run) == 1
            assert run_index_before is not None
            indexed_message_ids = json.loads(run_index_before)
            assert len(indexed_message_ids) == 1
            indexed_message_id = str(indexed_message_ids[0])
            assert metadata_before[indexed_message_id]["run_id"] == run_id
            assert metadata_before[indexed_message_id]["tenant_id"] == stack.tenant_id
            assert indexed_message_id in order_before
            before_unauthorized = await persisted_facts()
            assert before_unauthorized["status"] == "queued"
            assert before_unauthorized["cancel_requested_at"] is None
            assert before_unauthorized["cancel_requested_by"] is None

            foreign_cancel = await http.post(
                f"/api/ai/runs/{run_id}/cancel",
                headers={**headers, "X-AI-User-ID": "88000002"},
            )
            assert foreign_cancel.status_code == 404, foreign_cancel.text
            assert foreign_cancel.json() == {"detail": "active_run_not_found"}
            assert await persisted_facts() == before_unauthorized
            (
                queued_after_foreign,
                foreign_run_index,
                metadata_after_foreign,
                order_after_foreign,
            ) = await redis_queue_facts()
            assert [
                payload for payload in queued_after_foreign
                if payload.get("tenant_id") == stack.tenant_id
                and payload.get("run_id") == run_id
            ] == queued_for_run
            assert foreign_run_index == run_index_before
            assert metadata_after_foreign == metadata_before
            assert indexed_message_id in order_after_foreign

            owner_cancel = await http.post(f"/api/ai/runs/{run_id}/cancel")
            assert owner_cancel.status_code == 200, owner_cancel.text
            assert owner_cancel.json() == {
                "run_id": run_id, "session_id": None, "status": "cancelled",
            }
            cancelled = await persisted_facts()
            assert cancelled["status"] == "cancelled"
            assert cancelled["cancel_requested_at"] is not None
            assert cancelled["cancel_requested_by"] == stack.user_id
            assert cancelled["attempts"] == 0
            assert cancelled["leases"] == 0
            assert cancelled["provider_turns"] == 0
            assert cancelled["assistant_messages"] == 0
            assert cancelled["artifacts"] == 0
            assert cancelled["cancel_requested_events"] == 1
            assert cancelled["run_cancelled_events"] == 1
            assert cancelled["cancellation_audits"] >= 1

            (
                queued_after_cancel,
                run_index_after,
                metadata_after_cancel,
                order_after_cancel,
            ) = await redis_queue_facts()
            assert not any(
                payload.get("tenant_id") == stack.tenant_id
                and payload.get("run_id") == run_id
                for payload in queued_after_cancel
            )
            assert run_index_after is None
            assert indexed_message_id not in metadata_after_cancel
            assert indexed_message_id not in order_after_cancel

            readback = await http.get(f"/api/ai/runs/{run_id}")
            assert readback.status_code == 200, readback.text
            assert readback.json()["status"] == "cancelled"

            repeated_cancel = await http.post(f"/api/ai/runs/{run_id}/cancel")
            assert repeated_cancel.status_code == 404, repeated_cancel.text
            assert repeated_cancel.json() == {"detail": "active_run_not_found"}
            assert await persisted_facts() == cancelled
            (
                queued_after_repeat,
                run_index_after_repeat,
                metadata_after_repeat,
                order_after_repeat,
            ) = await redis_queue_facts()
            assert not any(
                payload.get("tenant_id") == stack.tenant_id
                and payload.get("run_id") == run_id
                for payload in queued_after_repeat
            )
            assert run_index_after_repeat is None
            assert indexed_message_id not in metadata_after_repeat
            assert indexed_message_id not in order_after_repeat
            assert "worker" not in stack._containers
            await asyncio.to_thread(stack.assert_no_run_resources, run_id)
            write_lifecycle_control_evidence(
                tmp_path,
                image_id=lifecycle_control_image[0],
                source_commit=lifecycle_control_image[1],
                stack=stack,
                scenario={
                    "run_id": run_id,
                    "fault_scenario": "queued_cancel",
                    "facts_before_cancel": before_unauthorized,
                    "facts_after_cancel": cancelled,
                    "foreign_cancel_http_status": foreign_cancel.status_code,
                    "repeated_cancel_http_status": repeated_cancel.status_code,
                    "queue_delivery_and_indexes_absent": True,
                    "worker_started": False,
                    "production_resources_absent_before_teardown": True,
                },
            )

    with LifecycleDockerStack(*lifecycle_control_image, evidence_dir=tmp_path) as stack:
        asyncio.run(scenario(stack))
