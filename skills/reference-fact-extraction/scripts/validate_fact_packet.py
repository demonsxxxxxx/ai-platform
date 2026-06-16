from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from profile_loader import (
    REFERENCE_FACT_EXTRACTION_AGENT,
    REFERENCE_FACT_VALIDATION_AGENT,
    domain_native_top_level_sections,
    load_profile,
    schema_version,
    shard_specs,
    validation_profile,
)
from runtime_guard import require_internal_context


GENERIC_PACKET_SCHEMA_VERSION = "reference-fact-packet-v1"
REQUIRED_PROVENANCE_EXECUTION_MODE = "subagent"

PASSING_VALIDATION_STATUSES = {"passed", "passed_with_warnings"}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def issue(level: str, path: str, message: str) -> dict[str, str]:
    return {"level": level, "path": path, "message": message}


def validate_packet_provenance(packet: dict[str, Any], profile: dict[str, Any] | None = None) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    provenance = packet.get("agent_provenance")
    if not isinstance(provenance, dict):
        return issues
    expected_entries = set(provenance)
    if profile is not None:
        expected_entries.update(shard_specs(profile))
    for shard_name in sorted(expected_entries):
        shard_provenance = provenance.get(shard_name)
        if not isinstance(shard_provenance, dict):
            issues.append(issue("error", f"agent_provenance.{shard_name}", "Missing shard provenance entry."))
            continue
        path = f"agent_provenance.{shard_name}"
        execution_mode = str(shard_provenance.get("execution_mode") or "").strip()
        extraction_agent = str(shard_provenance.get("extraction_agent") or "").strip()
        validation_agent = str(shard_provenance.get("validation_agent") or "").strip()
        validation_status = str(shard_provenance.get("validation_status") or "").strip().lower()
        if execution_mode != REQUIRED_PROVENANCE_EXECUTION_MODE:
            issues.append(
                issue(
                    "error",
                    f"{path}.execution_mode",
                    "Downstream-consumable fact packets require every shard provenance entry to use execution_mode='subagent'. same_thread_separated_pass is blocked.",
                )
            )
        if extraction_agent != REFERENCE_FACT_EXTRACTION_AGENT:
            issues.append(issue("error", f"{path}.extraction_agent", f"Expected {REFERENCE_FACT_EXTRACTION_AGENT!r}."))
        if validation_agent != REFERENCE_FACT_VALIDATION_AGENT:
            issues.append(issue("error", f"{path}.validation_agent", f"Expected {REFERENCE_FACT_VALIDATION_AGENT!r}."))
        if validation_status not in PASSING_VALIDATION_STATUSES:
            issues.append(issue("error", f"{path}.validation_status", "Expected passing validation status."))
        for key in ["source_materials_reviewed", "validation_checks"]:
            if not isinstance(shard_provenance.get(key), list) or not shard_provenance.get(key):
                issues.append(issue("error", f"{path}.{key}", "Must be a non-empty list."))
    return issues


def validate_generic_packet(packet: dict[str, Any], profile: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if packet.get("schema_version") != GENERIC_PACKET_SCHEMA_VERSION:
        issues.append(issue("error", "schema_version", f"Expected {GENERIC_PACKET_SCHEMA_VERSION!r}."))
    if packet.get("profile_id") != profile.get("profile_id"):
        issues.append(issue("error", "profile_id", f"Expected {profile.get('profile_id')!r}."))
    for key in ["sources", "facts", "agent_provenance", "verification"]:
        if not isinstance(packet.get(key), dict):
            issues.append(issue("error", key, "Must be an object."))
    for key in ["missing_evidence", "manual_review_items"]:
        if not isinstance(packet.get(key), list):
            issues.append(issue("error", key, "Must be a list."))
    return issues


REQUIRED_CTD_TOP_LEVEL_KEYS = [
    "schema_version",
    "project_profile",
    "sources",
    "long_term",
    "accelerated",
    "stress_study",
    "trend_charts",
    "body_sections",
    "docx_render_plan",
    "missing_evidence",
    "manual_review_items",
    "agent_provenance",
]


def has_value(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def validate_ctd_base_packet(packet: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for key in REQUIRED_CTD_TOP_LEVEL_KEYS:
        if key not in packet:
            issues.append(issue("error", key, "Missing required CTD fact-packet top-level key."))

    profile = packet.get("project_profile")
    if not isinstance(profile, dict):
        issues.append(issue("error", "project_profile", "Must be an object."))
    else:
        batches = profile.get("batches")
        if not isinstance(batches, list) or not batches:
            issues.append(issue("error", "project_profile.batches", "Must be a non-empty list."))
        else:
            seen: set[str] = set()
            for idx, batch in enumerate(batches):
                if not isinstance(batch, dict):
                    issues.append(issue("error", f"project_profile.batches[{idx}]", "Batch entry must be an object."))
                    continue
                batch_no = str(batch.get("batch_no") or "").strip()
                if not batch_no:
                    issues.append(issue("error", f"project_profile.batches[{idx}].batch_no", "Batch entry must include batch_no."))
                elif batch_no in seen:
                    issues.append(issue("error", f"project_profile.batches[{idx}].batch_no", f"Duplicate batch_no: {batch_no}."))
                seen.add(batch_no)

    sources = packet.get("sources")
    if not isinstance(sources, dict):
        issues.append(issue("error", "sources", "Must be an object."))
    else:
        if not isinstance(sources.get("allowed"), list):
            issues.append(issue("error", "sources.allowed", "Must list allowed sources."))
        if not isinstance(sources.get("forbidden"), list):
            issues.append(issue("error", "sources.forbidden", "Must list forbidden/excluded sources."))

    for study_key in ["long_term", "accelerated", "stress_study"]:
        study = packet.get(study_key)
        if not isinstance(study, dict):
            issues.append(issue("error", study_key, "Must be an object."))
            continue
        inputs = study.get("table_render_inputs")
        if not isinstance(inputs, list):
            issues.append(issue("error", f"{study_key}.table_render_inputs", "Must be a list."))
        else:
            for idx, table in enumerate(inputs):
                path = f"{study_key}.table_render_inputs[{idx}]"
                if not isinstance(table, dict):
                    issues.append(issue("error", path, "Table render input must be an object."))
                    continue
                for key in ["role", "batch_no", "time_header", "rows"]:
                    if key not in table:
                        issues.append(issue("error", f"{path}.{key}", "Missing required table render field."))
                if not isinstance(table.get("rows"), list):
                    issues.append(issue("error", f"{path}.rows", "Rows must be a list."))
        if study_key in {"long_term", "accelerated"} and not isinstance(study.get("marker_semantics"), dict):
            issues.append(issue("error", f"{study_key}.marker_semantics", "Must describe N/A and --- marker semantics."))

    plan = packet.get("docx_render_plan")
    if not isinstance(plan, dict):
        issues.append(issue("error", "docx_render_plan", "Must be an object."))
    elif not isinstance(plan.get("render_scope"), dict):
        issues.append(issue("error", "docx_render_plan.render_scope", "Must declare render scope."))

    trend = packet.get("trend_charts")
    if not isinstance(trend, dict):
        issues.append(issue("error", "trend_charts", "Must be an object."))
    elif trend.get("charts") not in ([], None) and isinstance(trend.get("charts"), list) and trend.get("charts"):
        issues.append(issue("error", "trend_charts.charts", "Base fact packet must defer trend charts; charts must be empty until downstream trend stage."))

    body = packet.get("body_sections")
    if not isinstance(body, dict):
        issues.append(issue("error", "body_sections", "Must be an object, empty or deferred in base fact packet."))
    elif any(has_value(value.get("text") if isinstance(value, dict) else value) for value in body.values()):
        issues.append(issue("error", "body_sections", "Base fact packet must defer body section text to downstream body stage."))

    provenance = packet.get("agent_provenance")
    if not isinstance(provenance, dict):
        issues.append(issue("error", "agent_provenance", "Must be an object."))
    else:
        for section in ["project_profile", "long_term", "accelerated", "stress_study"]:
            if not isinstance(provenance.get(section), dict):
                issues.append(issue("error", f"agent_provenance.{section}", "Missing provenance entry."))
    return issues


def validate_domain_native_packet(packet: dict[str, Any], profile: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    expected = schema_version(profile, "domain_fact_packet", GENERIC_PACKET_SCHEMA_VERSION)
    if packet.get("schema_version") != expected:
        issues.append(issue("error", "schema_version", f"Expected domain packet schema {expected!r}."))
    required = domain_native_top_level_sections(profile)
    if not required:
        issues.append(issue("error", "domain_native_output.top_level_sections", "Profile must declare top-level sections for domain-native output."))
    for key in required:
        if key not in packet:
            issues.append(issue("error", str(key), "Missing required domain-native top-level section."))
        elif packet.get(key) is None:
            issues.append(issue("error", str(key), "Domain-native top-level section must not be null."))
    for key in ["missing_evidence", "manual_review_items", "conflicts"]:
        if key in packet and not isinstance(packet.get(key), list):
            issues.append(issue("error", key, "Must be a list."))
    provenance = packet.get("agent_provenance")
    if not isinstance(provenance, dict):
        issues.append(issue("error", "agent_provenance", "Must be an object."))
    else:
        for shard_type in shard_specs(profile):
            if not isinstance(provenance.get(shard_type), dict):
                issues.append(issue("error", f"agent_provenance.{shard_type}", "Missing provenance entry for profile shard."))
    if validation_profile(profile) == "ctd_32s73_base":
        issues.extend(validate_ctd_base_packet(packet))
    return issues


def main() -> None:
    require_internal_context("validate_fact_packet.py")
    parser = argparse.ArgumentParser(description="Validate an assembled reference fact packet.")
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--fact-packet", type=Path, required=True)
    parser.add_argument("--report-out", type=Path, required=True)
    args = parser.parse_args()

    profile = load_profile(args.profile_id)
    packet = read_json(args.fact_packet)
    if packet.get("schema_version") == GENERIC_PACKET_SCHEMA_VERSION:
        issues = validate_generic_packet(packet, profile)
        output_mode = "generic"
    else:
        issues = validate_domain_native_packet(packet, profile)
        output_mode = "domain_native"
    issues.extend(validate_packet_provenance(packet, profile))
    errors = [item for item in issues if item["level"] == "error"]
    warnings = [item for item in issues if item["level"] == "warning"]
    report = {
        "schema_version": "reference-fact-packet-validation-v1",
        "profile_id": profile.get("profile_id"),
        "output_mode": output_mode,
        "path": str(args.fact_packet),
        "errors": errors,
        "warnings": warnings,
        "passed": not errors,
    }
    write_json(args.report_out, report)
    print(json.dumps({"status": "validated", "passed": report["passed"], "report": str(args.report_out)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
