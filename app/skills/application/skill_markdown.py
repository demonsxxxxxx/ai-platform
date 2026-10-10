from __future__ import annotations

from collections.abc import Callable

_yaml_metadata_loader: Callable[[str], dict[str, str]] | None = None


def configure_skill_markdown_loader(loader: Callable[[str], dict[str, str]]) -> None:
    global _yaml_metadata_loader
    _yaml_metadata_loader = loader


def parse_skill_markdown_front_matter(content: str) -> dict[str, str]:
    normalized = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n") and normalized.strip() != "---":
        return {}
    lines = normalized.split("\n")
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip(" \t") == "---":
            if _yaml_metadata_loader is None:
                raise RuntimeError("skill_markdown_loader_not_configured")
            return _yaml_metadata_loader("\n".join(lines[1:index]))
    return {}
