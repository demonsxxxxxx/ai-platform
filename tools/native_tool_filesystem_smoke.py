"""Exercise native command mounts and private IPC using the built Docker image."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time


def docker(*args: str, environment=None) -> str:
    result = subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=90,
        env={**os.environ, **(environment or {})},
    )
    if result.returncode:
        raise RuntimeError(
            f"docker operation failed: {args[0]} exit={result.returncode}"
        )
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    image = parser.parse_args().image
    name = "native-fs-" + secrets.token_hex(5)
    temporary = tempfile.TemporaryDirectory(prefix="native-fs-")
    root = Path(temporary.name)
    runtime, control = root / "runtime", root / "control"
    runtime.mkdir(mode=0o755)
    control.mkdir(mode=0o755)
    token_environment = {"AI_PLATFORM_NATIVE_TOOL_TOKEN": secrets.token_urlsafe(32)}
    created = False
    try:
        docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0:0",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "CHOWN",
            "--mount",
            f"type=bind,src={runtime},dst=/runtime",
            "--mount",
            f"type=bind,src={control},dst=/control",
            "--entrypoint",
            "python",
            image,
            "-c",
            "import os; os.chown('/runtime',10001,10001); os.chown('/control',10001,10001)",
        )
        # Use the production workspace creator, without a fixture-specific
        # recursive mkdir/chown that could hide a cold-start ownership defect.
        prepared = docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "10001:10001",
            "--cap-drop",
            "ALL",
            "--mount",
            f"type=bind,src={runtime},dst=/runtime",
            "--entrypoint",
            "python",
            image,
            "-c",
            "from app.runtime.sandbox.workspace_manager import SandboxWorkspaceManager; from types import SimpleNamespace; from pathlib import Path; "
            "r=SimpleNamespace(tenant_id='tenant',workspace_id='workspace',user_id='user',session_id='session',run_id='run',attempt_id='attempt',sandbox_mode='ephemeral',browser_enabled=False); "
            "p=Path(SandboxWorkspaceManager(root='/runtime').prepare(r).workspace_host_path); "
            "(p/'.claude/skills/example').mkdir(parents=True); (p/'inputs/input.txt').write_text('input'); "
            "(p/'.claude/skills/example/SKILL.md').write_text('skill'); print(p.relative_to('/runtime'))",
        )
        workspace = runtime / prepared.strip()
        layout = json.loads(
            docker(
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
                "--entrypoint",
                "python",
                image,
                "-c",
                "import json,sys; from types import SimpleNamespace; from pathlib import Path; "
                "from app.runtime.sandbox.providers.docker.native_filesystem import native_container_filesystem,NATIVE_SOCKET_DIRECTORY,NATIVE_SOCKET_PATH; "
                "print(json.dumps({'directory':NATIVE_SOCKET_DIRECTORY,'socket':NATIVE_SOCKET_PATH,'config':native_container_filesystem(sys.argv[1],'/workspace',Path(sys.argv[2]),SimpleNamespace(host_path=Path(sys.argv[1])/'.claude',container_path='/workspace/.claude'))}))",
                str(workspace),
                str(control),
            )
        )
        socket_directory, socket_path, config = (
            layout["directory"],
            layout["socket"],
            layout["config"],
        )
        args = [
            "run",
            "--detach",
            "--name",
            name,
            "--network",
            "none",
            "--user",
            config["user"],
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--env",
            "AI_PLATFORM_NATIVE_TOOL_TOKEN",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
        ]
        for capability in config["cap_add"]:
            args += ["--cap-add", capability]
        for source, mount in config["volumes"].items():
            args += [
                "--mount",
                f"type=bind,src={source},dst={mount['bind']}"
                + (",readonly" if mount["mode"] == "ro" else ""),
            ]
        for path, options in config["tmpfs"].items():
            args += ["--tmpfs", f"{path}:{options}"]
        docker(
            *args,
            "--entrypoint",
            "python",
            image,
            "-m",
            "app.runtime.sandbox.native_tool_app",
            environment=token_environment,
        )
        created = True

        def client(program: str) -> str:
            return docker(
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "10001:10001",
                "--cap-drop",
                "ALL",
                "--read-only",
                "--security-opt",
                "no-new-privileges:true",
                "--mount",
                f"type=bind,src={control},dst={socket_directory},readonly",
                "--env",
                "AI_PLATFORM_NATIVE_TOOL_TOKEN",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
                "--entrypoint",
                "python",
                image,
                "-c",
                program,
                environment=token_environment,
            )

        prefix = f"import httpx,os,json; c=httpx.Client(transport=httpx.HTTPTransport(uds={socket_path!r}),base_url='http://native',timeout=20); h={{'X-AI-Platform-Native-Tool-Token':os.environ['AI_PLATFORM_NATIVE_TOOL_TOKEN']}}; "
        deadline = time.monotonic() + 30
        while True:
            try:
                client(prefix + "assert c.get('/health',headers=h).status_code==200")
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("native IPC did not become ready") from None
                time.sleep(0.2)
        # The primary executor must still write its private SDK directories
        # after the sidecar has mounted them read-only in its own namespace.
        docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "10001:10001",
            "--cap-drop",
            "ALL",
            "--mount",
            f"type=bind,src={workspace},dst=/workspace",
            "--entrypoint",
            "python",
            image,
            "-c",
            "from pathlib import Path; [(Path('/workspace')/name/'private.txt').write_text('private') for name in ['.home','.claude-config','.tmp','.pins']]",
        )
        attack = """from pathlib import Path
import os
assert 'AI_PLATFORM_NATIVE_TOOL_TOKEN' not in os.environ
assert Path('inputs/input.txt').read_text() == 'input'
assert Path('.claude/skills/example/SKILL.md').read_text() == 'skill'
Path('logs').mkdir()
Path('logs/report.txt').write_text('ordinary output')
for path in ('.home/private.txt', '.claude-config/probe', '.pins/probe', '.tmp/probe', '.ai-platform/probe', 'inputs/input.txt', 'CLAUDE.md', '.claude/skills/example/SKILL.md', '/run/ai-platform-native/native-tool.sock'):
    for action in (lambda p: p.write_text('modified'), lambda p: p.unlink(), lambda p: p.chmod(0o777), lambda p: p.rename(str(p)+'.moved')):
        try:
            action(Path(path))
        except OSError:
            pass
        else:
            raise AssertionError('protected path was mutated: ' + path)
try:
    Path('.home/private.txt').read_text()
except OSError:
    pass
else:
    raise AssertionError('private state was exposed')
for fd in Path('/proc/self/fd').iterdir():
    try:
        assert not os.readlink(fd).startswith('socket:')
    except FileNotFoundError:
        pass
print('boundaries passed')
"""
        command = "python - <<'PY'\n" + attack + "\nPY"
        payload = json.dumps({"command": command, "timeout_ms": 20000})
        response = json.loads(
            client(
                prefix
                + f"r=c.post('/execute',headers=h,json=json.loads({payload!r})); r.raise_for_status(); print(r.text)"
            )
        )
        assert response["returncode"] == 0, response
        assert response["stdout"].strip() == "boundaries passed", response
        # A second call proves the first command could not remove/replace IPC.
        response = json.loads(
            client(
                prefix
                + "r=c.post('/execute',headers=h,json={'command':'cat logs/report.txt'}); r.raise_for_status(); print(r.text)"
            )
        )
        assert (
            response["returncode"] == 0 and response["stdout"] == "ordinary output"
        ), response
        state = docker(
            "exec",
            "--user",
            "10001:10001",
            name,
            "python",
            "-c",
            "from pathlib import Path; s=dict(line.split(':',1) for line in Path('/proc/1/status').read_text().splitlines() if ':' in line); "
            "assert s['Uid'].split()==['10001']*4; assert all(int(s[k],16)==0 for k in ['CapEff','CapPrm','CapInh','CapAmb']); print('identity passed')",
        )
        assert state.strip() == "identity passed"
        docker("rm", "--force", name)
        created = False
        docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0:0",
            "--cap-drop",
            "ALL",
            "--read-only",
            "--mount",
            f"type=bind,src={control},dst={socket_directory}",
            "--entrypoint",
            "python",
            image,
            "-m",
            "app.runtime.sandbox.providers.docker.native_filesystem",
        )
        control.rmdir()
        print("native filesystem, IPC continuity, privilege drop and cleanup passed")
    finally:
        if created:
            docker("rm", "--force", name)
        # Restore ownership of synthetic fixtures so the host can remove them.
        docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0:0",
            "--mount",
            f"type=bind,src={root},dst=/fixture",
            "--entrypoint",
            "python",
            image,
            "-c",
            f"import os; [(os.chown(p,{os.getuid()},{os.getgid()}),[os.chown(os.path.join(p,n),{os.getuid()},{os.getgid()}) for n in files]) for p,dirs,files in os.walk('/fixture')]",
        )
        temporary.cleanup()


if __name__ == "__main__":
    main()
