import shutil
from pathlib import Path

from app.path_safety import ensure_creatable_inside
from app.skills.registry import BuiltinSkill
from app.skills.registry import iter_skill_files


class SkillStager:
    def __init__(self, staging_subdir: str = ".claude/skills") -> None:
        cleaned = staging_subdir.strip().strip("/") or ".claude/skills"
        if Path(cleaned).is_absolute() or ".." in Path(cleaned).parts:
            raise ValueError("skill staging subdir must stay inside the run workspace")
        self.staging_subdir = cleaned

    def stage_skills(self, *, workspace: str | Path, skills: list[BuiltinSkill]) -> list[str]:
        workspace_path = Path(workspace)
        workspace_path.mkdir(parents=True, exist_ok=True)
        ensure_creatable_inside(
            workspace_path,
            workspace_path / self.staging_subdir,
            "skill staging path must stay inside the run workspace",
        )
        target_root = workspace_path / self.staging_subdir
        target_root.mkdir(parents=True, exist_ok=True)
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
            staged.append(skill.name)
        return staged
