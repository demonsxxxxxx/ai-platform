from __future__ import annotations

import asyncio
import hmac
import os
import signal
import stat
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator


NATIVE_TOOL_AUTH_HEADER = "X-AI-Platform-Native-Tool-Token"
NATIVE_TOOL_MAX_COMMAND_BYTES = 64 * 1024
NATIVE_TOOL_MAX_OUTPUT_BYTES = 1024 * 1024
NATIVE_TOOL_DEFAULT_TIMEOUT_MS = 120_000
NATIVE_TOOL_MAX_TIMEOUT_MS = 600_000
NATIVE_TOOL_TERMINATION_GRACE_SECONDS = 5.0
NATIVE_TOOL_TERMINATION_POLL_SECONDS = 0.05
NATIVE_TOOL_SOCKET_PUBLISH_TIMEOUT_SECONDS = 10.0
NATIVE_TOOL_FORCE_KILL_SIGNAL = getattr(signal, "SIGKILL", 9)


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
        if uid_fields and uid_fields[0] == str(uid) and state != "Z":
            process_ids.append(int(candidate.name))
    return process_ids


async def _terminate_uid_processes(uid: int) -> None:
    term_attempts = max(
        1,
        int(NATIVE_TOOL_TERMINATION_GRACE_SECONDS / NATIVE_TOOL_TERMINATION_POLL_SECONDS),
    )
    for kill_signal, attempts in (
        (signal.SIGTERM, term_attempts),
        (NATIVE_TOOL_FORCE_KILL_SIGNAL, 20),
    ):
        for _ in range(attempts):
            process_ids = _active_process_ids_for_uid(uid)
            if not process_ids:
                return
            for process_id in process_ids:
                try:
                    os.kill(process_id, kill_signal)
                except ProcessLookupError:
                    pass
            await asyncio.sleep(NATIVE_TOOL_TERMINATION_POLL_SECONDS)
    if _active_process_ids_for_uid(uid):
        raise RuntimeError("native_tool_process_cleanup_failed")


async def _run_command(
    *,
    command: str,
    workspace: Path,
    uid: int,
    gid: int,
    timeout_ms: int = NATIVE_TOOL_DEFAULT_TIMEOUT_MS,
) -> NativeToolResult:
    temp_dir = workspace / ".native-skill-tmp"
    temp_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(temp_dir, uid, gid)
    environment = {
        "HOME": str(workspace),
        "TMPDIR": str(temp_dir),
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    process = await asyncio.create_subprocess_exec(
        "/bin/bash",
        "-lc",
        command,
        cwd=str(workspace),
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        user=uid,
        group=gid,
        extra_groups=[],
        umask=0o077,
    )
    stdout_task = asyncio.create_task(_read_bounded(process.stdout))
    stderr_task = asyncio.create_task(_read_bounded(process.stderr))
    timed_out = False
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout_ms / 1000.0)
    except TimeoutError:
        timed_out = True
        await _terminate_process_group(process)
    except asyncio.CancelledError:
        await _terminate_process_group(process)
        raise
    else:
        # A native Skill command may background children. The command boundary
        # ends when its shell exits, so no descendant may survive that boundary.
        await _terminate_process_group(process, include_orphans=True)
    finally:
        # The sibling container is dedicated to this run and UID. Sweep every
        # active untrusted process so setsid/new-process-group descendants
        # cannot outlive the command boundary.
        await _terminate_uid_processes(uid)
    stdout, stdout_truncated = await stdout_task
    stderr, stderr_truncated = await stderr_task
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


async def _publish_socket(socket_path: Path) -> None:
    deadline = asyncio.get_running_loop().time() + NATIVE_TOOL_SOCKET_PUBLISH_TIMEOUT_SECONDS
    while asyncio.get_running_loop().time() <= deadline:
        try:
            node = socket_path.lstat()
            if not stat.S_ISSOCK(node.st_mode):
                raise RuntimeError("native_tool_socket_invalid")
            os.chmod(socket_path, 0o666)
            return
        except FileNotFoundError:
            await asyncio.sleep(0.05)
    raise RuntimeError("native_tool_socket_publish_timeout")


def _prepare_socket_parent(*, workspace: Path, socket_path: Path, uid: int, gid: int) -> Path:
    """Create the fixed UDS parent before Uvicorn binds the native-tool socket."""

    try:
        resolved_workspace = workspace.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("native_tool_workspace_invalid") from exc
    expected_socket = resolved_workspace / ".ai-platform" / "native-tool.sock"
    if socket_path != expected_socket:
        raise RuntimeError("native_tool_socket_invalid")
    socket_parent = expected_socket.parent
    try:
        node = socket_parent.lstat()
    except FileNotFoundError:
        socket_parent.mkdir(mode=0o700, parents=False)
        os.chown(socket_parent, uid, gid)
    except OSError as exc:
        raise RuntimeError("native_tool_socket_invalid") from exc
    else:
        if not stat.S_ISDIR(node.st_mode) or stat.S_ISLNK(node.st_mode):
            raise RuntimeError("native_tool_socket_invalid")
        if (node.st_uid, node.st_gid) != (uid, gid):
            raise RuntimeError("native_tool_socket_parent_owner_invalid")
        os.chmod(socket_parent, 0o700)
    return expected_socket


def main() -> int:
    """Launch the native-tool app only after its UDS bind location is safe."""

    workspace = Path(os.getenv("AI_PLATFORM_NATIVE_TOOL_WORKSPACE") or "/workspace")
    socket_path = Path(
        os.getenv("AI_PLATFORM_NATIVE_TOOL_SOCKET")
        or workspace / ".ai-platform" / "native-tool.sock"
    )
    uid = int(os.getenv("AI_PLATFORM_NATIVE_TOOL_UID") or "10001")
    gid = int(os.getenv("AI_PLATFORM_NATIVE_TOOL_GID") or "10001")
    prepared_socket = _prepare_socket_parent(
        workspace=workspace,
        socket_path=socket_path,
        uid=uid,
        gid=gid,
    )
    import uvicorn

    uvicorn.run(
        "app.runtime.sandbox.native_tool_app:create_native_tool_app",
        factory=True,
        uds=str(prepared_socket),
        access_log=False,
        log_level="warning",
    )
    return 0


def create_native_tool_app() -> FastAPI:
    """Build the token-authenticated command sidecar application."""

    token = str(os.getenv("AI_PLATFORM_NATIVE_TOOL_TOKEN") or "")
    workspace = Path(os.getenv("AI_PLATFORM_NATIVE_TOOL_WORKSPACE") or "/workspace")
    socket_path = Path(
        os.getenv("AI_PLATFORM_NATIVE_TOOL_SOCKET")
        or workspace / ".ai-platform" / "native-tool.sock"
    )
    uid = int(os.getenv("AI_PLATFORM_NATIVE_TOOL_UID") or "10001")
    gid = int(os.getenv("AI_PLATFORM_NATIVE_TOOL_GID") or "10001")
    lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if os.geteuid() != 0 or not token or len(token) < 32:
            raise RuntimeError("native_tool_configuration_invalid")
        resolved_workspace = workspace.resolve(strict=True)
        if not resolved_workspace.is_dir():
            raise RuntimeError("native_tool_workspace_invalid")
        expected_socket = resolved_workspace / ".ai-platform" / "native-tool.sock"
        if socket_path != expected_socket or socket_path.parent.is_symlink():
            raise RuntimeError("native_tool_socket_invalid")
        publisher = asyncio.create_task(_publish_socket(socket_path))
        try:
            yield
        finally:
            if not publisher.done():
                publisher.cancel()
            try:
                await publisher
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="ai-platform native Skill tool", version="1", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
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
