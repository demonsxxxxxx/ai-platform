import asyncio
import base64
import signal
import shlex
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.executors import claude_agent_sdk_runner
from app.runtime.sandbox import native_tool_app


def test_native_tool_launcher_prepares_socket_parent_before_uvicorn_binds(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    socket_path = workspace / ".ai-platform" / "native-tool.sock"
    calls = {}

    def prepare_socket_parent(**kwargs):
        calls["prepare"] = kwargs
        return socket_path

    monkeypatch.setattr(native_tool_app, "_prepare_socket_parent", prepare_socket_parent)
    monkeypatch.setenv("AI_PLATFORM_NATIVE_TOOL_WORKSPACE", str(workspace))
    monkeypatch.setenv("AI_PLATFORM_NATIVE_TOOL_SOCKET", str(socket_path))
    monkeypatch.setenv("AI_PLATFORM_NATIVE_TOOL_UID", "10001")
    monkeypatch.setenv("AI_PLATFORM_NATIVE_TOOL_GID", "10001")

    class FakeUvicorn:
        @staticmethod
        def run(*args, **kwargs):
            calls["run"] = (args, kwargs)

    monkeypatch.setitem(sys.modules, "uvicorn", FakeUvicorn)

    assert native_tool_app.main() == 0
    assert calls["prepare"] == {
        "workspace": workspace,
        "socket_path": socket_path,
        "uid": 10001,
        "gid": 10001,
    }
    assert calls["run"] == (
        ("app.runtime.sandbox.native_tool_app:create_native_tool_app",),
        {"factory": True, "uds": str(socket_path), "access_log": False, "log_level": "warning"},
    )


def test_native_tool_socket_parent_fails_closed_without_posix_directory_flags(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    socket_path = workspace / ".ai-platform" / "native-tool.sock"

    monkeypatch.setattr(native_tool_app.os, "name", "nt")
    monkeypatch.setattr(
        native_tool_app.os,
        "open",
        lambda *_args, **_kwargs: pytest.fail("unsupported platform must fail before opening paths"),
    )

    with pytest.raises(RuntimeError, match="native_tool_secure_filesystem_unavailable"):
        native_tool_app._prepare_socket_parent(
            workspace=workspace,
            socket_path=socket_path,
            uid=10001,
            gid=10001,
        )


def test_native_tool_socket_parent_is_created_and_revalidated_through_directory_fds(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    socket_path = workspace / ".ai-platform" / "native-tool.sock"
    calls = []
    parent_open_count = 0

    def node(*, device, inode, uid=10001, gid=10001):
        return type(
            "Node",
            (),
            {"st_mode": 0o40700, "st_dev": device, "st_ino": inode, "st_uid": uid, "st_gid": gid},
        )()

    def open_node(path, flags, *, dir_fd=None):
        nonlocal parent_open_count
        calls.append(("open", path, flags, dir_fd))
        if path == workspace:
            return 10
        assert path == ".ai-platform" and dir_fd == 10
        parent_open_count += 1
        if parent_open_count == 1:
            raise FileNotFoundError
        return 11

    monkeypatch.setattr(Path, "resolve", lambda path, strict=False: path)
    monkeypatch.setattr(native_tool_app.os, "name", "posix")
    monkeypatch.setattr(native_tool_app.os, "O_NOFOLLOW", 0x100, raising=False)
    monkeypatch.setattr(native_tool_app.os, "O_DIRECTORY", 0x200, raising=False)
    monkeypatch.setattr(native_tool_app.os, "open", open_node)
    monkeypatch.setattr(
        native_tool_app.os,
        "fstat",
        lambda fd: node(device=1, inode=1) if fd == 10 else node(device=2, inode=2),
    )
    monkeypatch.setattr(
        native_tool_app.os,
        "mkdir",
        lambda path, mode, *, dir_fd: calls.append(("mkdir", path, mode, dir_fd)),
    )
    monkeypatch.setattr(
        native_tool_app.os,
        "fchown",
        lambda fd, uid, gid: calls.append(("fchown", fd, uid, gid)),
        raising=False,
    )
    monkeypatch.setattr(
        native_tool_app.os,
        "fchmod",
        lambda fd, mode: calls.append(("fchmod", fd, mode)),
        raising=False,
    )
    monkeypatch.setattr(
        native_tool_app.os,
        "stat",
        lambda path, *, dir_fd, follow_symlinks: node(device=2, inode=2),
    )
    monkeypatch.setattr(native_tool_app.os, "close", lambda fd: calls.append(("close", fd)))

    assert native_tool_app._prepare_socket_parent(
        workspace=workspace,
        socket_path=socket_path,
        uid=10001,
        gid=10001,
    ) == socket_path
    assert ("mkdir", ".ai-platform", 0o700, 10) in calls
    assert ("fchown", 11, 10001, 10001) in calls
    assert ("fchmod", 11, 0o700) in calls
    assert calls[-2:] == [("close", 11), ("close", 10)]


@pytest.mark.parametrize(
    ("foreign_owner", "path_inode", "expected_error"),
    [
        (True, 2, "native_tool_socket_parent_owner_invalid"),
        (False, 3, "native_tool_socket_parent_changed"),
    ],
)
def test_native_tool_socket_parent_rejects_foreign_owner_or_path_swap(
    monkeypatch,
    tmp_path,
    foreign_owner,
    path_inode,
    expected_error,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    socket_path = workspace / ".ai-platform" / "native-tool.sock"
    closed = []

    def node(*, device, inode, uid=10001, gid=10001):
        return type(
            "Node",
            (),
            {"st_mode": 0o40700, "st_dev": device, "st_ino": inode, "st_uid": uid, "st_gid": gid},
        )()

    monkeypatch.setattr(Path, "resolve", lambda path, strict=False: path)
    monkeypatch.setattr(native_tool_app.os, "name", "posix")
    monkeypatch.setattr(native_tool_app.os, "O_NOFOLLOW", 0x100, raising=False)
    monkeypatch.setattr(native_tool_app.os, "O_DIRECTORY", 0x200, raising=False)
    monkeypatch.setattr(
        native_tool_app.os,
        "open",
        lambda path, flags, *, dir_fd=None: 10 if path == workspace else 11,
    )
    monkeypatch.setattr(
        native_tool_app.os,
        "fstat",
        lambda fd: (
            node(device=1, inode=1)
            if fd == 10
            else node(device=2, inode=2, uid=99999 if foreign_owner else 10001, gid=99999 if foreign_owner else 10001)
        ),
    )
    monkeypatch.setattr(native_tool_app.os, "fchmod", lambda *_args: None, raising=False)
    monkeypatch.setattr(
        native_tool_app.os,
        "stat",
        lambda path, *, dir_fd, follow_symlinks: node(device=2, inode=path_inode),
    )
    monkeypatch.setattr(native_tool_app.os, "close", lambda fd: closed.append(fd))

    with pytest.raises(RuntimeError, match=expected_error):
        native_tool_app._prepare_socket_parent(
            workspace=workspace,
            socket_path=socket_path,
            uid=10001,
            gid=10001,
        )
    assert closed == [11, 10]


@pytest.mark.asyncio
async def test_native_tool_command_uses_minimal_environment_and_process_isolation(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    captured = {}

    class Process:
        pid = 123
        returncode = 0
        stdout = None
        stderr = None

        async def wait(self):
            return 0

    async def create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return Process()

    async def terminate(_process, *, include_orphans=False):
        captured["include_orphans"] = include_orphans

    async def terminate_uid(uid):
        captured["terminated_uid"] = uid

    monkeypatch.setattr(native_tool_app.os, "chown", lambda *_args: None, raising=False)
    monkeypatch.setattr(native_tool_app.asyncio, "create_subprocess_exec", create_subprocess_exec)
    monkeypatch.setattr(native_tool_app, "_terminate_process_group", terminate)
    monkeypatch.setattr(native_tool_app, "_terminate_uid_processes", terminate_uid)
    monkeypatch.setenv("AI_PLATFORM_SECRET", "must-not-reach-native-command")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "must-not-reach-native-command")

    result = await native_tool_app._run_command(
        command="printf safe",
        workspace=workspace,
        uid=10001,
        gid=10001,
        timeout_ms=5_000,
    )

    assert result.returncode == 0
    assert captured["args"] == ("/bin/bash", "-lc", "printf safe")
    assert captured["kwargs"]["cwd"] == str(workspace)
    assert captured["kwargs"]["start_new_session"] is True
    assert captured["kwargs"]["user"] == 10001
    assert captured["kwargs"]["group"] == 10001
    assert captured["kwargs"]["extra_groups"] == []
    assert captured["kwargs"]["umask"] == 0o077
    assert captured["include_orphans"] is True
    assert captured["terminated_uid"] == 10001
    assert captured["kwargs"]["env"] == {
        "HOME": str(workspace),
        "TMPDIR": str(workspace / ".native-skill-tmp"),
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


@pytest.mark.asyncio
async def test_native_tool_cancellation_terminates_the_process_group(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    terminated = []

    class Process:
        pid = 123
        returncode = None
        stdout = None
        stderr = None

        async def wait(self):
            raise asyncio.CancelledError()

    async def create_subprocess_exec(*_args, **_kwargs):
        return Process()

    async def terminate(process, *, include_orphans=False):
        terminated.append((process.pid, include_orphans))

    async def terminate_uid(uid):
        terminated.append((uid, "uid"))

    monkeypatch.setattr(native_tool_app.os, "chown", lambda *_args: None, raising=False)
    monkeypatch.setattr(native_tool_app.asyncio, "create_subprocess_exec", create_subprocess_exec)
    monkeypatch.setattr(native_tool_app, "_terminate_process_group", terminate)
    monkeypatch.setattr(native_tool_app, "_terminate_uid_processes", terminate_uid)

    with pytest.raises(asyncio.CancelledError):
        await native_tool_app._run_command(
            command="sleep 1",
            workspace=workspace,
            uid=10001,
            gid=10001,
        )

    assert terminated == [(123, False), (10001, "uid")]


def test_native_tool_process_sweep_finds_detached_active_uid_processes(tmp_path):
    proc_root = tmp_path / "proc"
    for process_id, uid, state in (("101", 10001, "S"), ("102", 10001, "Z"), ("103", 10002, "S")):
        process_dir = proc_root / process_id
        process_dir.mkdir(parents=True)
        (process_dir / "status").write_text(
            f"Name:\tprobe\nState:\t{state} (probe)\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n",
            encoding="utf-8",
        )

    assert native_tool_app._active_process_ids_for_uid(10001, proc_root=proc_root) == [101]


@pytest.mark.asyncio
async def test_native_tool_process_sweep_escalates_to_kill(monkeypatch):
    observed = [[222], [222], []]
    killed = []

    monkeypatch.setattr(native_tool_app, "NATIVE_TOOL_TERMINATION_GRACE_SECONDS", 0)
    monkeypatch.setattr(
        native_tool_app,
        "_active_process_ids_for_uid",
        lambda _uid: observed.pop(0),
    )
    monkeypatch.setattr(native_tool_app.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    await native_tool_app._terminate_uid_processes(10001)

    assert killed == [
        (222, signal.SIGTERM),
        (222, native_tool_app.NATIVE_TOOL_FORCE_KILL_SIGNAL),
    ]


@pytest.mark.asyncio
async def test_native_tool_disconnect_cancels_inflight_command(monkeypatch, tmp_path):
    cancelled = []

    async def run_command(**_kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    class DisconnectedRequest:
        async def is_disconnected(self):
            return True

    monkeypatch.setattr(native_tool_app, "_run_command", run_command)

    with pytest.raises(HTTPException) as exc_info:
        await native_tool_app._execute_with_disconnect_cancellation(
            http_request=DisconnectedRequest(),
            command="sleep 60",
            timeout_ms=1_000,
            workspace=tmp_path,
            uid=10001,
            gid=10001,
        )

    assert exc_info.value.status_code == 499
    assert cancelled == [True]


def test_native_skill_workspace_paths_are_confined_and_proxy_carries_command_as_data(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "inputs").mkdir(parents=True)
    (workspace / "outputs" / "delivery").mkdir(parents=True)
    (workspace / ".ai-platform").mkdir()
    subject = {"workspace_contract": "ai-platform.skill-workspace.v1"}

    assert claude_agent_sdk_runner._workspace_path_parameters_authorized(
        subject,
        "Read",
        {"file_path": "inputs/source.docx"},
        workspace_root=workspace,
    )
    assert claude_agent_sdk_runner._workspace_path_parameters_authorized(
        subject,
        "Write",
        {"file_path": "outputs/delivery/report.pdf"},
        workspace_root=workspace,
    )
    assert not claude_agent_sdk_runner._workspace_path_parameters_authorized(
        subject,
        "Read",
        {"file_path": "../outside.txt"},
        workspace_root=workspace,
    )
    assert not claude_agent_sdk_runner._workspace_path_parameters_authorized(
        subject,
        "Read",
        {"file_path": str(workspace / ".ai-platform" / "native-tool.sock")},
        workspace_root=workspace,
    )
    assert claude_agent_sdk_runner._workspace_path_parameters_authorized(
        subject,
        "Glob",
        {"path": ".", "pattern": "inputs/**/*.xlsx"},
        workspace_root=workspace,
    )
    assert claude_agent_sdk_runner._workspace_path_parameters_authorized(
        subject,
        "Read",
        {"file_path": ".claude/skills/native-skill/SKILL.md"},
        workspace_root=workspace,
    )
    assert claude_agent_sdk_runner._workspace_path_parameters_authorized(
        subject,
        "Glob",
        {"path": ".", "pattern": ".claude/skills/**/*.py"},
        workspace_root=workspace,
    )
    for forbidden_input in (
        {"path": ".", "pattern": "/proc/**"},
        {"path": ".", "pattern": ".ai-platform/**"},
        {"path": ".", "pattern": ".home/**"},
        {"path": ".", "pattern": ".*/**"},
        {"path": ".", "pattern": "**/*.xlsx"},
        {"path": ".", "pattern": "{.home,.tmp}/**"},
        {"path": ".", "pattern": "@(.home|.tmp)/**"},
        {"path": ".", "pattern": ".claude/skills/{../settings.json,ok.md}"},
        {"path": ".", "pattern": "inputs/{../.home,ok}/**"},
        {"path": "inputs", "pattern": "{../.home,ok}/**"},
        {"path": ".claude-config", "pattern": "**/*"},
    ):
        assert not claude_agent_sdk_runner._workspace_path_parameters_authorized(
            subject,
            "Glob",
            forbidden_input,
            workspace_root=workspace,
        )
    for internal_root in (".claude-config", ".home", ".pins", ".tmp"):
        assert not claude_agent_sdk_runner._workspace_path_parameters_authorized(
            subject,
            "Read",
            {"file_path": str(workspace / internal_root / "private")},
            workspace_root=workspace,
        )
    assert not claude_agent_sdk_runner._workspace_path_parameters_authorized(
        subject,
        "Read",
        {"file_path": ".claude/settings.json"},
        workspace_root=workspace,
    )

    monkeypatch.setenv("AI_PLATFORM_NATIVE_TOOL_SOCKET", "/workspace/.ai-platform/native-tool.sock")
    monkeypatch.setenv("AI_PLATFORM_NATIVE_TOOL_TOKEN", "x" * 32)
    command = "python scripts/run.py 'quoted; value'"
    proxied = claude_agent_sdk_runner._native_tool_proxy_input(
        {"command": command, "timeout": 5_000}
    )

    assert proxied is not None
    assert "native_tool_proxy.py" in proxied["command"]
    assert " -I " in proxied["command"]
    assert str(claude_agent_sdk_runner._NATIVE_TOOL_PROXY_SCRIPT) in proxied["command"]
    assert " -m " not in proxied["command"]
    assert command not in proxied["command"]
    proxy_args = shlex.split(proxied["command"])
    encoded = proxy_args[-2]
    assert base64.b64decode(encoded).decode("utf-8") == command
    assert proxy_args[-1] == "5000"
    assert proxied["timeout"] == 5_000
    assert claude_agent_sdk_runner._native_tool_proxy_input(
        {"command": command, "timeout": 600_001}
    ) is None
