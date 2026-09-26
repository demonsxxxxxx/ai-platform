import errno
import json
import os
from pathlib import Path

from app.runtime.sandbox.contracts import SandboxRuntimeRequest, WorkspaceLease
from app.sandbox.api import PLATFORM_CLAUDE_INSTRUCTIONS_FILENAME
from app.settings import get_settings


PLATFORM_CLAUDE_PROJECT_INSTRUCTIONS = """# AI Platform 任务默认规则

- 默认使用简体中文回复用户。
- 面向用户的计划、进度说明、思考摘要和最终答复使用简体中文。
- 代码、命令、文件名、路径、接口字段和必须保持准确的专有名词可以保留原文。
- 用户明确指定其他语言时，遵循用户本次要求。
"""


_DEFAULT_ACL_XATTR = "system.posix_acl_default"
_ACCESS_ACL_XATTR = "system.posix_acl_access"
_WORKSPACE_DIRECTORY_MODE = 0o755
_WORKSPACE_FILE_MODE = 0o644


def _secure_directory_flags() -> int:
    return int(os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))


def _remove_acl(descriptor: int, name: str) -> None:
    removexattr = getattr(os, "removexattr", None)
    if removexattr is None:
        return
    try:
        removexattr(descriptor, name)
    except OSError as exc:
        if exc.errno not in {errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP}:
            raise


def _harden_workspace_directory_fd(descriptor: int) -> None:
    _remove_acl(descriptor, _DEFAULT_ACL_XATTR)
    _remove_acl(descriptor, _ACCESS_ACL_XATTR)
    os.fchmod(descriptor, _WORKSPACE_DIRECTORY_MODE)


def _ensure_workspace_root(root: Path) -> int | None:
    if os.name == "posix":
        if not (
            getattr(os, "O_DIRECTORY", 0)
            and getattr(os, "O_NOFOLLOW", 0)
            and os.open in os.supports_dir_fd
            and os.mkdir in os.supports_dir_fd
        ):
            raise OSError("secure POSIX workspace creation is unavailable")
        descriptor = os.open(root.anchor or ".", _secure_directory_flags())
        current = descriptor
        parts = root.parts[1:] if root.anchor else root.parts
        if not parts or any(component in {"", ".", ".."} for component in parts):
            os.close(descriptor)
            raise OSError("workspace root path is invalid")
        try:
            for index, component in enumerate(parts):
                try:
                    os.mkdir(component, mode=_WORKSPACE_DIRECTORY_MODE, dir_fd=current)
                    created = True
                except FileExistsError:
                    created = False
                next_descriptor = os.open(
                    component,
                    _secure_directory_flags(),
                    dir_fd=current,
                )
                try:
                    if created:
                        _harden_workspace_directory_fd(next_descriptor)
                    elif index == len(parts) - 1:
                        _remove_acl(next_descriptor, _DEFAULT_ACL_XATTR)
                        _remove_acl(next_descriptor, _ACCESS_ACL_XATTR)
                except BaseException:
                    os.close(next_descriptor)
                    raise
                os.close(current)
                current = next_descriptor
            return current
        except BaseException:
            os.close(current)
            raise

    if not root.parts or root in {Path("."), Path(root.anchor)}:
        raise OSError("workspace root path is invalid")
    absolute_root = Path(os.path.abspath(root))
    directory = Path(absolute_root.anchor)
    for component in absolute_root.parts[1:]:
        directory /= component
        try:
            directory.lstat()
        except FileNotFoundError:
            directory.mkdir(mode=_WORKSPACE_DIRECTORY_MODE)
        if _is_windows_reparse_point(directory) or not directory.is_dir():
            raise OSError("workspace root contains a reparse point")
    return None


def _is_windows_reparse_point(path: Path) -> bool:
    if os.name != "nt":
        return False
    is_junction = getattr(path, "is_junction", None)
    if is_junction is not None and is_junction():
        return True
    node = path.lstat()
    return bool(getattr(node, "st_file_attributes", 0) & 0x400)


def _secure_workspace_directory_tree(root_descriptor: int, components: tuple[str, ...]) -> None:
    descriptor = os.dup(root_descriptor)
    try:
        for component in components:
            try:
                os.mkdir(component, mode=_WORKSPACE_DIRECTORY_MODE, dir_fd=descriptor)
            except FileExistsError:
                pass
            next_descriptor = os.open(
                component,
                _secure_directory_flags(),
                dir_fd=descriptor,
            )
            try:
                _harden_workspace_directory_fd(next_descriptor)
            except BaseException:
                os.close(next_descriptor)
                raise
            os.close(descriptor)
            descriptor = next_descriptor
    finally:
        os.close(descriptor)


def _legacy_workspace_directory_tree(root: Path, components: tuple[str, ...]) -> None:
    directory = root
    for component in components:
        directory /= component
        directory.mkdir(mode=_WORKSPACE_DIRECTORY_MODE, exist_ok=True)
        if _is_windows_reparse_point(directory) or not directory.is_dir():
            raise OSError("workspace path contains a reparse point")
        directory.chmod(_WORKSPACE_DIRECTORY_MODE)


def _create_workspace_directory_tree(
    root: Path,
    components: tuple[str, ...],
    root_descriptor: int | None,
) -> None:
    if root_descriptor is not None:
        _secure_workspace_directory_tree(root_descriptor, components)
    else:
        _legacy_workspace_directory_tree(root, components)


def _write_platform_claude_instructions(workspace: Path, internal: Path) -> None:
    target = workspace / PLATFORM_CLAUDE_INSTRUCTIONS_FILENAME
    temporary = internal / ".platform-claude-instructions.tmp"
    try:
        temporary.write_text(PLATFORM_CLAUDE_PROJECT_INSTRUCTIONS, encoding="utf-8")
        temporary.chmod(0o444)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


class SandboxWorkspaceManager:
    def __init__(self, root: str | Path | None = None) -> None:
        settings = get_settings() if root is None or os.name != "posix" else None
        configured = root if root is not None else settings.sandbox_workspace_root
        self.root = Path(configured)
        provider = str(
            getattr(settings, "sandbox_container_provider", "fake") or ""
        ).strip().lower()
        self._requires_secure_creation = provider in {"docker", "opensandbox"}

    def prepare(self, request: SandboxRuntimeRequest) -> WorkspaceLease:
        if os.name != "posix" and self._requires_secure_creation:
            raise OSError("secure host-bind workspace creation is unavailable")
        path_components = (
            "tenants",
            request.tenant_id,
            "workspaces",
            request.workspace_id,
            "users",
            request.user_id,
            "sessions",
            request.session_id,
            "runs",
            request.run_id,
            "attempts",
            request.attempt_id,
        )
        run_root = self.root.joinpath(*path_components)
        workspace = run_root / "workspace"
        inputs = workspace / "inputs"
        internal = workspace / ".ai-platform"
        logs = run_root / "logs"
        runtime = run_root / "runtime"
        root_descriptor = _ensure_workspace_root(self.root)
        try:
            for components in (
                path_components + ("workspace",),
                path_components + ("workspace", "inputs"),
                path_components + ("workspace", "outputs"),
                path_components + ("workspace", "outputs", "delivery"),
                path_components + ("workspace", ".ai-platform"),
                path_components + ("logs",),
                path_components + ("runtime",),
            ):
                _create_workspace_directory_tree(self.root, components, root_descriptor)
        finally:
            if root_descriptor is not None:
                os.close(root_descriptor)
        _write_platform_claude_instructions(workspace, internal)

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
        meta_path = runtime / "meta.json"
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        meta_path.chmod(_WORKSPACE_FILE_MODE)

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
