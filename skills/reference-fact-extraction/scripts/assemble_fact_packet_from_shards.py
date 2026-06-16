from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from profile_loader import (
    domain_native_deferred_sections,
    domain_native_section_sources,
    domain_native_top_level_sections,
    load_profile,
    schema_version,
    shard_specs,
)
from runtime_guard import require_internal_context


GENERIC_PACKET_SCHEMA_VERSION = "reference-fact-packet-v1"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def shard_payload(shard: dict[str, Any]) -> dict[str, Any]:
    facts = shard.get("facts")
    return facts if isinstance(facts, dict) else shard


def section(shard: dict[str, Any], key: str, default: Any) -> Any:
    if key in shard:
        return shard[key]
    payload = shard_payload(shard)
    value = payload.get(key)
    return value if value is not None else default


def list_section(shard: dict[str, Any], key: str) -> list[Any]:
    if isinstance(shard.get(key), list):
        return shard[key]
    payload = shard_payload(shard)
    value = payload.get(key)
    return value if isinstance(value, list) else []


def agent_provenance(shard: dict[str, Any]) -> dict[str, Any]:
    provenance = shard.get("agent_provenance")
    return provenance if isinstance(provenance, dict) else {}


def assemble_generic_packet(profile: dict[str, Any], shards: dict[str, dict[str, Any]], shard_hashes: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    specs = shard_specs(profile)
    facts: dict[str, Any] = {}
    sources: dict[str, Any] = {}
    missing_evidence: list[Any] = []
    manual_review_items: list[Any] = []
    conflicts: list[Any] = []
    provenance: dict[str, Any] = {}

    for shard_type, spec in specs.items():
        shard = shards.get(shard_type, {})
        payload = shard_payload(shard)
        for key in spec.get("allowed_sections") or []:
            if key == "sources":
                value = section(shard, key, None)
                if isinstance(value, dict):
                    sources.update(value)
            elif key in {"missing_evidence", "manual_review_items", "conflicts"}:
                continue
            else:
                value = payload.get(key)
                if value is not None:
                    facts[str(key)] = value
        missing_evidence.extend(list_section(shard, "missing_evidence"))
        manual_review_items.extend(list_section(shard, "manual_review_items"))
        conflicts.extend(list_section(shard, "conflicts"))
        provenance[shard_type] = agent_provenance(shard)

    packet = {
        "schema_version": GENERIC_PACKET_SCHEMA_VERSION,
        "profile_id": profile.get("profile_id"),
        "domain_schema_version": schema_version(profile, "domain_fact_packet", GENERIC_PACKET_SCHEMA_VERSION),
        "sources": sources,
        "facts": facts,
        "missing_evidence": missing_evidence,
        "manual_review_items": manual_review_items,
        "conflicts": conflicts,
        "agent_provenance": provenance,
        "verification": {
            "status": "assembled_from_validated_shards",
            "verified_shards": list(shards),
            "blocking_issues": [],
        },
    }
    report = {
        "schema_version": "reference-fact-packet-assembly-report-v1",
        "profile_id": profile.get("profile_id"),
        "assembled_from": {key: specs[key].get("path_name", key) for key in shards},
        "input_hashes": shard_hashes,
        "agent_provenance": provenance,
        "policy": "Assembler only combines validated fact shards into the fact-packet contract; it must not infer facts.",
        "output_schema_version": GENERIC_PACKET_SCHEMA_VERSION,
        "missing_evidence_count": len(missing_evidence),
        "manual_review_item_count": len(manual_review_items),
        "conflict_count": len(conflicts),
    }
    packet["assembly_report"] = report
    return packet, report


def value_at_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def default_native_section_value(section_name: str) -> Any:
    if section_name in {"missing_evidence", "manual_review_items", "conflicts"}:
        return []
    return {}


def assemble_domain_native_packet(profile: dict[str, Any], shards: dict[str, dict[str, Any]], shard_hashes: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    generic, report = assemble_generic_packet(profile, shards, shard_hashes)
    top_level_sections = domain_native_top_level_sections(profile)
    if not top_level_sections:
        raise ValueError(f"Profile {profile.get('profile_id')!r} must define domain_native_output.top_level_sections for domain_native output.")
    deferred = domain_native_deferred_sections(profile)
    section_sources = domain_native_section_sources(profile)
    packet: dict[str, Any] = {}
    for section_name in top_level_sections:
        if section_name == "schema_version":
            packet[section_name] = schema_version(profile, "domain_fact_packet", GENERIC_PACKET_SCHEMA_VERSION)
            continue
        source_path = section_sources.get(section_name, f"facts.{section_name}")
        value = value_at_path(generic, source_path)
        if value is None and section_name in deferred:
            value = deferred[section_name]
        if value is None:
            value = default_native_section_value(section_name)
        packet[section_name] = value
    report = dict(report)
    report.update(
        {
            "output_schema_version": packet.get("schema_version"),
            "output_mode": "domain_native",
            "profile_native_sections": top_level_sections,
        }
    )
    return packet, report


def main() -> None:
    require_internal_context("assemble_fact_packet_from_shards.py")
    parser = argparse.ArgumentParser(description="Assemble validated reference fact shards into fact-packet.json.")
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--shard", action="append", default=[], help="Shard mapping in the form shard_type=path.json")
    parser.add_argument("--output-mode", choices=["generic", "domain_native"], required=True)
    parser.add_argument("--fact-packet-out", type=Path, required=True)
    parser.add_argument("--report-out", type=Path, required=True)
    args = parser.parse_args()

    profile = load_profile(args.profile_id)
    shards: dict[str, dict[str, Any]] = {}
    shard_hashes: dict[str, str] = {}
    for item in args.shard:
        if "=" not in item:
            raise SystemExit(f"Invalid --shard value {item!r}; expected shard_type=path")
        shard_type, path_text = item.split("=", 1)
        path = Path(path_text)
        shards[shard_type] = read_json(path)
        shard_hashes[shard_type] = sha256_file(path)

    if args.output_mode == "domain_native":
        packet, report = assemble_domain_native_packet(profile, shards, shard_hashes)
    else:
        packet, report = assemble_generic_packet(profile, shards, shard_hashes)
    write_json(args.fact_packet_out, packet)
    write_json(args.report_out, report)
    print(json.dumps({"status": "assembled", "fact_packet": str(args.fact_packet_out), "report": str(args.report_out)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
