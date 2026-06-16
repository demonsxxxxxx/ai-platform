from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
PROFILES_ROOT = SKILL_ROOT / "profiles"
REFERENCE_FACT_EXTRACTION_AGENT = "reference_fact_extraction_agent"
REFERENCE_FACT_VALIDATION_AGENT = "reference_fact_validation_agent"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def profile_path(profile_id: str) -> Path:
    normalized = str(profile_id or "").strip()
    if not normalized:
        raise ValueError("profile_id is required; no generic fallback is allowed.")
    path = PROFILES_ROOT / normalized / "profile.json"
    if not path.exists():
        available = sorted(p.parent.name for p in PROFILES_ROOT.glob("*/profile.json"))
        raise FileNotFoundError(f"Unknown profile_id {normalized!r}. Available profiles: {available}")
    return path


def load_profile(profile_id: str | None) -> dict[str, Any]:
    return read_json(profile_path(str(profile_id or "").strip()))


def shard_specs(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    shards = profile.get("shards")
    if not isinstance(shards, list):
        raise ValueError(f"Profile {profile.get('profile_id')!r} must define shards[]")
    specs: dict[str, dict[str, Any]] = {}
    for shard in shards:
        if not isinstance(shard, dict) or not shard.get("shard_type"):
            raise ValueError("Each profile shard must be an object with shard_type")
        specs[str(shard["shard_type"])] = shard
    return specs


def fact_sections(profile: dict[str, Any]) -> set[str]:
    values = profile.get("fact_sections")
    if isinstance(values, list):
        return {str(value) for value in values}
    sections: set[str] = set()
    for shard in profile.get("shards") or []:
        if isinstance(shard, dict):
            sections.update(str(value) for value in shard.get("allowed_sections") or [])
    sections.difference_update(shared_sections(profile))
    return sections


def shared_sections(profile: dict[str, Any]) -> set[str]:
    values = profile.get("shared_sections")
    if isinstance(values, list):
        return {str(value) for value in values}
    return {"missing_evidence", "manual_review_items", "conflicts"}


def schema_version(profile: dict[str, Any], key: str, default: str) -> str:
    versions = profile.get("schema_versions")
    if isinstance(versions, dict) and versions.get(key):
        return str(versions[key])
    return default


def domain_native_output(profile: dict[str, Any]) -> dict[str, Any]:
    config = profile.get("domain_native_output")
    return config if isinstance(config, dict) else {}


def domain_native_top_level_sections(profile: dict[str, Any]) -> list[str]:
    sections = domain_native_output(profile).get("top_level_sections")
    return [str(section) for section in sections] if isinstance(sections, list) else []


def domain_native_deferred_sections(profile: dict[str, Any]) -> dict[str, Any]:
    deferred = domain_native_output(profile).get("deferred_sections")
    return deferred if isinstance(deferred, dict) else {}


def domain_native_section_sources(profile: dict[str, Any]) -> dict[str, str]:
    sources = domain_native_output(profile).get("section_sources")
    return {str(key): str(value) for key, value in sources.items()} if isinstance(sources, dict) else {}


def validation_profile(profile: dict[str, Any]) -> str:
    value = profile.get("validation_profile")
    return str(value) if value else ""
