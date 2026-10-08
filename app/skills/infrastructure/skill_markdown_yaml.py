from __future__ import annotations

import yaml


def load_skill_markdown_metadata(front_matter: str) -> dict[str, str]:
    try:
        metadata = yaml.safe_load(front_matter)
    except yaml.YAMLError as exc:
        raise ValueError("skill_front_matter_invalid_yaml") from exc
    if not isinstance(metadata, dict):
        return {}
    return {
        key: value.strip()
        for key, value in metadata.items()
        if isinstance(key, str) and isinstance(value, str)
    }
