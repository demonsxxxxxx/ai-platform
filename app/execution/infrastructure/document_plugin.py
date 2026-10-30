"""Materialize the pinned upstream plugin without modifying its Skill contents."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import zipfile

from app.execution.domain.document_plugin import (
    DOCUMENT_PLUGIN_ARCHIVE_SHA256,
    DOCUMENT_PLUGIN_REVISION,
    DOCUMENT_PLUGIN_SKILLS,
)

DOCUMENT_PLUGIN_PATH = Path("/opt/ai-platform/document-skills")


def install_document_plugin(archive: Path, destination: Path) -> None:
    if hashlib.sha256(archive.read_bytes()).hexdigest() != DOCUMENT_PLUGIN_ARCHIVE_SHA256:
        raise ValueError("document_plugin_archive_invalid")
    if destination.exists():
        raise ValueError("document_plugin_destination_exists")
    prefix = f"skills-{DOCUMENT_PLUGIN_REVISION}/skills/"
    with zipfile.ZipFile(archive) as source:
        for entry in source.infolist():
            if not entry.filename.startswith(prefix):
                continue
            relative = PurePosixPath(entry.filename.removeprefix(prefix))
            if not relative.parts or relative.parts[0] not in DOCUMENT_PLUGIN_SKILLS:
                continue
            if relative.is_absolute() or ".." in relative.parts or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("document_plugin_archive_path_invalid")
            target = destination / "skills" / relative
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(entry) as content, target.open("xb") as output:
                    shutil.copyfileobj(content, output)
    for name in DOCUMENT_PLUGIN_SKILLS:
        for filename in ("SKILL.md", "LICENSE.txt"):
            if not (destination / "skills" / name / filename).is_file():
                raise ValueError("document_plugin_skill_missing")
    manifest = destination / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"name": "document-skills", "version": "1.0.0", "description": "Official Anthropic document Skills", "skills": "./skills/"}) + "\n", encoding="utf-8")
    (destination / "upstream-revision").write_text(DOCUMENT_PLUGIN_REVISION + "\n", encoding="utf-8")


def document_plugin_options(revision: object) -> list[dict[str, str]]:
    if revision is None:
        return []
    try:
        installed_revision = (DOCUMENT_PLUGIN_PATH / "upstream-revision").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError("document_plugin_not_installed") from exc
    if revision != DOCUMENT_PLUGIN_REVISION or installed_revision != revision:
        raise ValueError("document_plugin_revision_invalid")
    return [{"type": "local", "path": str(DOCUMENT_PLUGIN_PATH)}]


def document_plugin_read_allowed(raw: object, revision: object) -> bool:
    if revision != DOCUMENT_PLUGIN_REVISION or not isinstance(raw, str):
        return False
    try:
        path = Path(raw)
        path.relative_to(DOCUMENT_PLUGIN_PATH / "skills")
        path.resolve().relative_to((DOCUMENT_PLUGIN_PATH / "skills").resolve())
    except (OSError, ValueError):
        return False
    return True


if __name__ == "__main__":
    import sys
    install_document_plugin(Path(sys.argv[1]), Path(sys.argv[2]))
