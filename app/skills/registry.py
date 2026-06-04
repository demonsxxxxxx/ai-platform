from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BuiltinSkill:
    name: str
    description: str
    path: Path
    version: str
    source: dict[str, Any]
    entry: dict[str, Any]


def parse_skill_markdown_front_matter(content: str) -> dict[str, str]:
    normalized = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n") and normalized.strip() != "---":
        return {}
    parts = normalized.split("---", 2)
    if len(parts) < 3:
        return {}
    metadata: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"').strip("'")
    return metadata


def iter_skill_files(path: Path):
    root = Path(path)
    if root.is_symlink():
        raise ValueError(f"skill path must not be a symlink: {root.name}")
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        if current.is_symlink():
            raise ValueError(f"skill directory must not be a symlink: {current.name}")
        for dirname in dirnames:
            child = current / dirname
            if child.is_symlink():
                raise ValueError(f"skill directory must not contain symlinks: {child.name}")
        for filename in filenames:
            item = current / filename
            if item.is_symlink():
                raise ValueError(f"skill directory must not contain symlinks: {item.name}")
            if not item.is_file():
                continue
            relative_path = item.relative_to(root).as_posix()
            if relative_path.startswith("../") or relative_path == "..":
                raise ValueError("skill file path escaped skill root")
            yield item


def skill_content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(iter_skill_files(path), key=lambda child: child.relative_to(path).as_posix()):
        relative_path = str(item.relative_to(path)).replace("\\", "/").encode("utf-8")
        content = item.read_bytes()
        digest.update(len(relative_path).to_bytes(8, "big"))
        digest.update(relative_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


class BuiltinSkillRegistry:
    def __init__(self, skills_root: str | Path) -> None:
        self.skills_root = Path(skills_root)

    def list_builtin_skills(self) -> list[BuiltinSkill]:
        if not self.skills_root.exists():
            return []
        skills: list[BuiltinSkill] = []
        for item in sorted(self.skills_root.iterdir()):
            if item.is_symlink() and item.is_dir():
                raise ValueError(f"built-in skill directory must not be a symlink: {item.name}")
            if not item.is_dir():
                continue
            skill_dir = item
            skill_md = skill_dir / "SKILL.md"
            if skill_md.is_symlink():
                raise ValueError(f"built-in skill SKILL.md must not be a symlink: {skill_dir.name}")
            if not skill_md.is_file():
                raise ValueError(f"missing SKILL.md for built-in skill: {skill_dir.name}")
            content = skill_md.read_text(encoding="utf-8")
            metadata = parse_skill_markdown_front_matter(content)
            name = metadata.get("name") or skill_dir.name
            description = metadata.get("description") or ""
            if name != skill_dir.name:
                raise ValueError(
                    f"built-in skill name mismatch: directory={skill_dir.name} manifest={name}"
                )
            if not description:
                raise ValueError(f"missing description for built-in skill: {skill_dir.name}")
            version = skill_content_hash(skill_dir)
            skills.append(
                BuiltinSkill(
                    name=name,
                    description=description,
                    path=skill_dir,
                    version=version,
                    source={"kind": "builtin", "asset_dir": skill_dir.name, "version": version},
                    entry={"kind": "filesystem", "path": str(skill_dir)},
                )
            )
        return skills
