import json
from pathlib import Path

from app.runtime.sandbox.contracts import SandboxRuntimeRequest
from app.runtime.sandbox.workspace_manager import SandboxWorkspaceManager


def request(**overrides) -> SandboxRuntimeRequest:
    values = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "agent_id": "general-agent",
        "skill_ids": ["general-chat"],
        "input_message": "hello",
        "sandbox_mode": "ephemeral",
        "browser_enabled": True,
        "model": "deepseek-v4-flash",
        "permissions": ["sandbox.execute"],
        "callback_url": "http://callback",
        "callback_token_id": "cbt_run_a",
    }
    values.update(overrides)
    return SandboxRuntimeRequest(**values)


def expected_run_root(root: Path) -> Path:
    return (
        root
        / "tenants"
        / "tenant-a"
        / "workspaces"
        / "workspace-a"
        / "users"
        / "user-a"
        / "sessions"
        / "session-a"
        / "runs"
        / "run-a"
    )


def test_prepare_creates_platform_workspace_namespace(tmp_path):
    manager = SandboxWorkspaceManager(root=tmp_path)

    lease = manager.prepare(request())

    run_root = expected_run_root(tmp_path)
    meta_path = run_root / "runtime" / "meta.json"

    assert Path(lease.host_root) == run_root
    assert (run_root / "workspace").is_dir()
    assert (run_root / "workspace" / "inputs").is_dir()
    assert (run_root / "workspace" / "outputs" / "delivery").is_dir()
    assert (run_root / "workspace" / ".ai-platform").is_dir()
    assert (run_root / "logs").is_dir()
    assert meta_path.exists()
    assert json.loads(meta_path.read_text(encoding="utf-8")) == {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "sandbox_mode": "ephemeral",
        "browser_enabled": True,
    }


def test_workspace_lease_paths_match_platform_namespace(tmp_path):
    manager = SandboxWorkspaceManager(root=tmp_path)

    lease = manager.prepare(request(browser_enabled=False))

    run_root = expected_run_root(tmp_path)
    assert Path(lease.host_root) == run_root
    assert Path(lease.workspace_host_path) == run_root / "workspace"
    assert Path(lease.inputs_host_path) == run_root / "workspace" / "inputs"
    assert Path(lease.logs_host_path) == run_root / "logs"
    assert lease.workspace_container_path == "/workspace"


def test_user_visible_payload_hides_host_root_and_uses_workspace_mount(tmp_path):
    manager = SandboxWorkspaceManager(root=tmp_path)

    lease = manager.prepare(request())

    payload = lease.user_visible_payload()
    assert str(tmp_path) not in json.dumps(payload)
    assert payload == {
        "workspace": "/workspace",
        "inputs": "/workspace/inputs",
    }


def test_manager_uses_settings_root_when_root_not_provided(monkeypatch, tmp_path):
    class StubSettings:
        sandbox_workspace_root = str(tmp_path / "configured-root")

    monkeypatch.setattr("app.runtime.sandbox.workspace_manager.get_settings", lambda: StubSettings())

    manager = SandboxWorkspaceManager()

    assert manager.root == tmp_path / "configured-root"
