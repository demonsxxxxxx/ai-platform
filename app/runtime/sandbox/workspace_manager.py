import json
from pathlib import Path

from app.runtime.sandbox.contracts import SandboxRuntimeRequest, WorkspaceLease
from app.settings import get_settings


class SandboxWorkspaceManager:
    def __init__(self, root: str | Path | None = None) -> None:
        configured = root if root is not None else get_settings().sandbox_workspace_root
        self.root = Path(configured)

    def prepare(self, request: SandboxRuntimeRequest) -> WorkspaceLease:
        run_root = (
            self.root
            / "tenants"
            / request.tenant_id
            / "workspaces"
            / request.workspace_id
            / "users"
            / request.user_id
            / "sessions"
            / request.session_id
            / "runs"
            / request.run_id
            / "attempts"
            / request.attempt_id
        )
        workspace = run_root / "workspace"
        inputs = workspace / "inputs"
        outputs = workspace / "outputs"
        delivery = outputs / "delivery"
        internal = workspace / ".ai-platform"
        logs = run_root / "logs"
        runtime = run_root / "runtime"
        for directory in (workspace, inputs, outputs, delivery, internal, logs, runtime):
            directory.mkdir(parents=True, exist_ok=True)

        meta = {
            "tenant_id": request.tenant_id,
            "workspace_id": request.workspace_id,
            "user_id": request.user_id,
            "session_id": request.session_id,
            "run_id": request.run_id,
            "attempt_id": request.attempt_id,
            "sandbox_mode": request.sandbox_mode,
            "browser_enabled": request.browser_enabled,
        }
        (runtime / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )

        return WorkspaceLease(
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            user_id=request.user_id,
            session_id=request.session_id,
            run_id=request.run_id,
            host_root=str(run_root),
            workspace_host_path=str(workspace),
            inputs_host_path=str(inputs),
            logs_host_path=str(logs),
        )
