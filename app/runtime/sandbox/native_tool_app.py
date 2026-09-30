from __future__ import annotations

import asyncio
import hmac
import os
import signal
import socket
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator


from app.runtime.sandbox.providers.docker.native_filesystem import NATIVE_SOCKET_PATH

NATIVE_TOOL_AUTH_HEADER = "X-AI-Platform-Native-Tool-Token"
NATIVE_TOOL_MAX_COMMAND_BYTES = 64 * 1024
NATIVE_TOOL_MAX_OUTPUT_BYTES = 1024 * 1024
NATIVE_TOOL_DEFAULT_TIMEOUT_MS = 120_000
NATIVE_TOOL_MAX_TIMEOUT_MS = 600_000
NATIVE_TOOL_TERMINATION_GRACE_SECONDS = 5.0
NATIVE_TOOL_TERMINATION_POLL_SECONDS = 0.05
NATIVE_TOOL_FORCE_KILL_SIGNAL = getattr(signal, "SIGKILL", 9)
NATIVE_TOOL_RUNTIME_UID = 10001
NATIVE_TOOL_RUNTIME_GID = 10001
_PR_GET_DUMPABLE = 3
_PR_SET_DUMPABLE = 4
_NATIVE_TOOL_PRE_SPAWN_FAILURE = "native_tool_stage=pre_spawn;class=failure"
_NATIVE_TOOL_POST_SPAWN_FAILURE = "native_tool_stage=post_spawn;class=failure"


def _require_native_tool_identity(uid: int, gid: int) -> None:
    """Fail closed unless the sidecar is the fixed unprivileged runtime identity."""

    if os.name != "posix" or (uid, gid) != (NATIVE_TOOL_RUNTIME_UID, NATIVE_TOOL_RUNTIME_GID):
        raise RuntimeError("native_tool_identity_invalid")
    try:
        effective_identity = (os.geteuid(), os.getegid())
        supplementary_groups = set(os.getgroups())
    except (AttributeError, OSError) as exc:
        raise RuntimeError("native_tool_identity_invalid") from exc
    if effective_identity != (uid, gid) or not supplementary_groups.issubset({gid}):
        raise RuntimeError("native_tool_identity_invalid")


def _linux_prctl(option: int, argument: int = 0) -> int:
    """Invoke Linux prctl through the standard library without exposing errno."""

    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
        prctl.argtypes = [
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
        ]
        prctl.restype = ctypes.c_int
        return int(prctl(option, argument, 0, 0, 0))
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise RuntimeError("native_tool_non_dumpable_unavailable") from exc


def _set_process_non_dumpable() -> None:
    """Set and verify the Linux non-dumpable process boundary."""

    if os.name != "posix":
        raise RuntimeError("native_tool_non_dumpable_unavailable")
    if _linux_prctl(_PR_SET_DUMPABLE, 0) != 0 or _linux_prctl(_PR_GET_DUMPABLE) != 0:
        raise RuntimeError("native_tool_non_dumpable_failed")


class NativeToolRequest(BaseModel):
    """One bounded command submitted by the credential-bearing SDK executor."""

    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1, max_length=NATIVE_TOOL_MAX_COMMAND_BYTES)
    timeout_ms: int = Field(
        default=NATIVE_TOOL_DEFAULT_TIMEOUT_MS,
        ge=1,
        le=NATIVE_TOOL_MAX_TIMEOUT_MS,
    )

    @field_validator("command")
    @classmethod
    def validate_command_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > NATIVE_TOOL_MAX_COMMAND_BYTES:
            raise ValueError("native_tool_command_too_large")
        return value


class NativeToolResult(BaseModel):
    """Sanitized command result returned across the native-tool socket."""

    model_config = ConfigDict(extra="forbid")

    returncode: int
    stdout: str = ""
    stderr: str = ""
    output_truncated: bool = False
    timed_out: bool = False


async def _read_bounded(stream: asyncio.StreamReader | None) -> tuple[bytes, bool]:
    if stream is None:
        return b"", False
    chunks: list[bytes] = []
    retained = 0
    truncated = False
    while True:
        chunk = await stream.read(64 * 1024)
        if not chunk:
            break
        remaining = NATIVE_TOOL_MAX_OUTPUT_BYTES - retained
        if remaining > 0:
            kept = chunk[:remaining]
            chunks.append(kept)
            retained += len(kept)
        if len(chunk) > max(remaining, 0):
            truncated = True
    return b"".join(chunks), truncated


async def _terminate_process_group(process: asyncio.subprocess.Process, *, include_orphans: bool = False) -> None:
    parent_running = process.returncode is None
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    if parent_running:
        try:
            await asyncio.wait_for(process.wait(), timeout=NATIVE_TOOL_TERMINATION_GRACE_SECONDS)
        except TimeoutError:
            pass
    if include_orphans:
        await asyncio.sleep(0.1)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    if process.returncode is None:
        await process.wait()


def _active_process_ids_for_uid(uid: int, *, proc_root: Path = Path("/proc")) -> list[int]:
    process_ids: list[int] = []
    sidecar_pid = os.getpid()
    try:
        candidates = list(proc_root.iterdir())
    except OSError:
        return process_ids
    for candidate in candidates:
        if not candidate.name.isdigit():
            continue
        try:
            status_lines = (candidate / "status").read_text(
                encoding="utf-8",
                errors="replace",
            ).splitlines()
        except OSError:
            continue
        status = {
            key: value.strip()
            for line in status_lines
            if ":" in line
            for key, value in [line.split(":", 1)]
        }
        uid_fields = status.get("Uid", "").split()
        state = status.get("State", "")[:1]
        process_id = int(candidate.name)
        if (
            process_id != sidecar_pid
            and uid_fields
            and uid_fields[0] == str(uid)
            and state != "Z"
        ):
            process_ids.append(process_id)
    return process_ids


async def _terminate_uid_processes(uid: int) -> None:
    sidecar_pid = os.getpid()
    term_attempts = max(
        1,
        int(NATIVE_TOOL_TERMINATION_GRACE_SECONDS / NATIVE_TOOL_TERMINATION_POLL_SECONDS),
    )
    for kill_signal, attempts in (
        (signal.SIGTERM, term_attempts),
        (NATIVE_TOOL_FORCE_KILL_SIGNAL, 20),
    ):
        for _ in range(attempts):
            process_ids = [
                process_id
                for process_id in _active_process_ids_for_uid(uid)
                if process_id != sidecar_pid
            ]
            if not process_ids:
                return
            for process_id in process_ids:
                try:
                    os.kill(process_id, kill_signal)
                except ProcessLookupError:
                    pass
            await asyncio.sleep(NATIVE_TOOL_TERMINATION_POLL_SECONDS)
    if any(process_id != sidecar_pid for process_id in _active_process_ids_for_uid(uid)):
        raise RuntimeError("native_tool_process_cleanup_failed")


async def _run_command(
    *,
    command: str,
    workspace: Path,
    uid: int,
    gid: int,
    timeout_ms: int = NATIVE_TOOL_DEFAULT_TIMEOUT_MS,
) -> NativeToolResult:
    _require_native_tool_identity(uid, gid)
    temp_dir = workspace / ".native-skill-tmp"
    temp_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    environment = {
        "HOME": str(workspace),
        "TMPDIR": str(temp_dir),
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    pre_spawn_failed = False
    try:
        process = await asyncio.create_subprocess_exec(
            "/bin/bash",
            "--noprofile",
            "--norc",
            "-c",
            command,
            cwd=str(workspace),
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            umask=0o077,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        pre_spawn_failed = True
    if pre_spawn_failed:
        raise RuntimeError(_NATIVE_TOOL_PRE_SPAWN_FAILURE)
    stdout_task = asyncio.create_task(_read_bounded(process.stdout))
    stderr_task = asyncio.create_task(_read_bounded(process.stderr))
    timed_out = False
    post_spawn_failed = False
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout_ms / 1000.0)
    except TimeoutError:
        timed_out = True
        await _terminate_process_group(process)
    except asyncio.CancelledError:
        await _terminate_process_group(process)
        raise
    except Exception:
        try:
            await _terminate_process_group(process)
        except Exception:
            pass
        post_spawn_failed = True
    else:
        # A native Skill command may background children. The command boundary
        # ends when its shell exits, so no descendant may survive that boundary.
        await _terminate_process_group(process, include_orphans=True)
    finally:
        # The sibling container is dedicated to this run and UID. Sweep every
        # active untrusted process so setsid/new-process-group descendants
        # cannot outlive the command boundary.
        await _terminate_uid_processes(uid)
    if post_spawn_failed:
        raise RuntimeError(_NATIVE_TOOL_POST_SPAWN_FAILURE)
    output_failed = False
    try:
        stdout, stdout_truncated = await stdout_task
        stderr, stderr_truncated = await stderr_task
    except Exception:
        output_failed = True
    if output_failed:
        raise RuntimeError(_NATIVE_TOOL_POST_SPAWN_FAILURE)
    return NativeToolResult(
        returncode=124 if timed_out else int(process.returncode or 0),
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
        output_truncated=stdout_truncated or stderr_truncated,
        timed_out=timed_out,
    )


async def _execute_with_disconnect_cancellation(
    *,
    http_request: Request,
    command: str,
    timeout_ms: int,
    workspace: Path,
    uid: int,
    gid: int,
) -> NativeToolResult:
    run_task = asyncio.create_task(
        _run_command(
            command=command,
            workspace=workspace,
            uid=uid,
            gid=gid,
            timeout_ms=timeout_ms,
        )
    )

    async def wait_for_disconnect() -> None:
        while not await http_request.is_disconnected():
            await asyncio.sleep(NATIVE_TOOL_TERMINATION_POLL_SECONDS)

    disconnect_task = asyncio.create_task(wait_for_disconnect())
    try:
        done, _pending = await asyncio.wait(
            {run_task, disconnect_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if disconnect_task in done and not run_task.done():
            run_task.cancel()
            with suppress(asyncio.CancelledError):
                await run_task
            raise HTTPException(status_code=499, detail="native_tool_client_disconnected")
        return await run_task
    finally:
        disconnect_task.cancel()
        with suppress(asyncio.CancelledError):
            await disconnect_task
        if not run_task.done():
            run_task.cancel()
            with suppress(asyncio.CancelledError):
                await run_task


def _bind_native_tool_socket(socket_path: Path) -> socket.socket:
    """Bind before privilege drop; the command identity cannot replace the listener."""
    if str(socket_path) != NATIVE_SOCKET_PATH or os.geteuid() != 0:
        raise RuntimeError("native_tool_socket_invalid")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    parent_fd = os.open(socket_path.parent, flags)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        node = os.fstat(parent_fd)
        if (node.st_uid, node.st_gid) not in {(0, NATIVE_TOOL_RUNTIME_GID), (NATIVE_TOOL_RUNTIME_UID, NATIVE_TOOL_RUNTIME_GID)}:
            raise RuntimeError("native_tool_socket_parent_owner_invalid")
        os.fchown(parent_fd, 0, NATIVE_TOOL_RUNTIME_GID)
        os.fchmod(parent_fd, 0o750)
        listener.set_inheritable(False)
        listener.bind(str(socket_path))
        os.chown(socket_path, 0, NATIVE_TOOL_RUNTIME_GID, follow_symlinks=False)
        os.chmod(socket_path, 0o660)
        listener.listen(128)
        return listener
    except BaseException:
        listener.close()
        raise
    finally:
        os.close(parent_fd)


def _drop_native_tool_privileges(uid: int, gid: int) -> None:
    if (uid, gid) != (NATIVE_TOOL_RUNTIME_UID, NATIVE_TOOL_RUNTIME_GID):
        raise RuntimeError("native_tool_identity_invalid")
    os.setgroups([])
    os.setresgid(gid, gid, gid)
    os.setresuid(uid, uid, uid)
    _require_native_tool_identity(uid, gid)
    capabilities = {
        key: value.strip() for line in Path("/proc/self/status").read_text().splitlines()
        if ":" in line for key, value in [line.split(":", 1)]
    }
    if any(int(capabilities[key], 16) for key in ("CapInh", "CapPrm", "CapEff", "CapAmb")):
        raise RuntimeError("native_tool_capabilities_not_dropped")
    _set_process_non_dumpable()


def main() -> int:
    """Bind private IPC, permanently drop startup privileges, then accept commands."""
    socket_path = Path(os.getenv("AI_PLATFORM_NATIVE_TOOL_SOCKET") or NATIVE_SOCKET_PATH)
    uid = int(os.getenv("AI_PLATFORM_NATIVE_TOOL_UID") or "10001")
    gid = int(os.getenv("AI_PLATFORM_NATIVE_TOOL_GID") or "10001")
    with _bind_native_tool_socket(socket_path) as listener:
        _drop_native_tool_privileges(uid, gid)
        import uvicorn

        uvicorn.run(
            "app.runtime.sandbox.native_tool_app:create_native_tool_app",
            factory=True, fd=listener.fileno(), loop="asyncio",
            access_log=False, log_level="warning",
        )
    return 0


def create_native_tool_app() -> FastAPI:
    """Build the token-authenticated command sidecar application."""

    token = str(os.getenv("AI_PLATFORM_NATIVE_TOOL_TOKEN") or "")
    workspace = Path(os.getenv("AI_PLATFORM_NATIVE_TOOL_WORKSPACE") or "/workspace")
    uid = int(os.getenv("AI_PLATFORM_NATIVE_TOOL_UID") or "10001")
    gid = int(os.getenv("AI_PLATFORM_NATIVE_TOOL_GID") or "10001")
    lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        _require_native_tool_identity(uid, gid)
        if not token or len(token) < 32:
            raise RuntimeError("native_tool_configuration_invalid")
        resolved_workspace = workspace.resolve(strict=True)
        if not resolved_workspace.is_dir():
            raise RuntimeError("native_tool_workspace_invalid")
        yield

    app = FastAPI(title="ai-platform native Skill tool", version="1", lifespan=lifespan)

    @app.get("/health")
    async def health(
        provided_token: str | None = Header(
            default=None,
            alias=NATIVE_TOOL_AUTH_HEADER,
        ),
    ) -> dict[str, str]:
        if not provided_token or not hmac.compare_digest(provided_token, token):
            raise HTTPException(status_code=403, detail="invalid_native_tool_token")
        return {"status": "ok"}

    @app.post("/execute", response_model=NativeToolResult)
    async def execute(
        payload: NativeToolRequest,
        http_request: Request,
        provided_token: str | None = Header(default=None, alias=NATIVE_TOOL_AUTH_HEADER),
    ) -> NativeToolResult:
        if not provided_token or not hmac.compare_digest(provided_token, token):
            raise HTTPException(status_code=403, detail="invalid_native_tool_token")
        async with lock:
            return await _execute_with_disconnect_cancellation(
                http_request=http_request,
                command=payload.command,
                timeout_ms=payload.timeout_ms,
                workspace=workspace,
                uid=uid,
                gid=gid,
            )

    return app


if __name__ == "__main__":
    raise SystemExit(main())
