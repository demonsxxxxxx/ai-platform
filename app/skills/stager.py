import errno
import os
import shutil
import stat
from pathlib import Path

from app.path_safety import ensure_creatable_inside
from app.skills.registry import BuiltinSkill
from app.skills.registry import iter_skill_files


_SKILL_DIRECTORY_MODE = 0o755
_SKILL_FILE_MODE = 0o644
_POSIX_ACL_NAMES = ("system.posix_acl_default", "system.posix_acl_access")


def _harden_skill_node(target: int | Path, mode: int) -> None:
    removexattr = getattr(os, "removexattr", None)
    if removexattr is not None:
        for name in _POSIX_ACL_NAMES:
            try:
                removexattr(target, name, follow_symlinks=False) if isinstance(target, Path) else removexattr(target, name)
            except OSError as exc:
                if exc.errno not in {errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP}:
                    raise
    if isinstance(target, Path):
        target.chmod(mode)
    else:
        os.fchmod(target, mode)


def ensure_skill_staging_directory(workspace: Path, directory: Path) -> None:
    created_workspace = False
    try:
        workspace.mkdir(mode=0o700, parents=True, exist_ok=False)
        created_workspace = True
    except FileExistsError:
        pass
    if workspace.is_symlink() or not workspace.is_dir():
        raise ValueError("skill staging path must stay inside the run workspace")
    if created_workspace:
        _harden_skill_node(workspace, _SKILL_DIRECTORY_MODE)
    current = workspace
    for component in directory.relative_to(workspace).parts:
        current /= component
        current.mkdir(mode=0o700, exist_ok=True)
        if current.is_symlink() or not current.is_dir():
            raise ValueError("skill staging path must stay inside the run workspace")
        _harden_skill_node(current, _SKILL_DIRECTORY_MODE)


def write_skill_staging_file(path: Path, content: bytes) -> None:
    if os.name != "posix":
        path.write_bytes(content)
        _harden_skill_node(path, _SKILL_FILE_MODE)
        return
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        remaining = memoryview(content)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("skill staging file write did not advance")
            remaining = remaining[written:]
        _harden_skill_node(descriptor, _SKILL_FILE_MODE)
    finally:
        os.close(descriptor)


def harden_skill_staging_tree(root: Path) -> None:
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        if current.is_symlink() or not current.is_dir():
            raise ValueError("skill staging tree is invalid")
        _harden_skill_node(current, _SKILL_DIRECTORY_MODE)
        for name in dirnames:
            child = current / name
            if child.is_symlink():
                raise ValueError("skill staging tree is invalid")
        for name in filenames:
            child = current / name
            node = child.lstat()
            if not stat.S_ISREG(node.st_mode):
                raise ValueError("skill staging tree is invalid")
            mode = 0o755 if node.st_mode & 0o111 else _SKILL_FILE_MODE
            _harden_skill_node(child, mode)


class SkillStager:
    def stage_skills(self, *, workspace: str | Path, skills: list[BuiltinSkill]) -> list[str]:
        workspace_path = Path(workspace)
        workspace_path.mkdir(parents=True, exist_ok=True)
        ensure_creatable_inside(
            workspace_path,
            workspace_path / ".claude" / "skills",
            "skill staging path must stay inside the run workspace",
        )
        target_root = workspace_path / ".claude" / "skills"
        ensure_skill_staging_directory(workspace_path, target_root)
        ensure_creatable_inside(
            workspace_path,
            target_root,
            "skill staging path must stay inside the run workspace",
        )
        staged: list[str] = []
        for skill in skills:
            source = Path(skill.path)
            if not (source / "SKILL.md").is_file():
                raise ValueError(f"cannot stage skill without SKILL.md: {skill.name}")
            list(iter_skill_files(source))
            if Path(skill.name).name != skill.name:
                raise ValueError(f"invalid skill name for staging: {skill.name}")
            target = target_root / skill.name
            ensure_creatable_inside(workspace_path, target, "skill staging path must stay inside the run workspace")
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target)
            harden_skill_staging_tree(target)
            staged.append(skill.name)
        return staged
