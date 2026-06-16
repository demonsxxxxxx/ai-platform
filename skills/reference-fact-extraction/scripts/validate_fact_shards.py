from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from profile_loader import (
    REFERENCE_FACT_EXTRACTION_AGENT,
    REFERENCE_FACT_VALIDATION_AGENT,
    fact_sections,
    load_profile,
    schema_version,
    shard_specs,
)
from runtime_guard import require_internal_context


PASSING_VALIDATION_STATUSES = {"passed", "passed_with_warnings"}
REQUIRED_PROVENANCE_EXECUTION_MODE = "subagent"
REQUIRED_PROVENANCE_LIST_FIELDS = {"source_materials_reviewed", "validation_checks"}
DEFAULT_SHARD_SCHEMA_VERSION = "reference-fact-shard-v1"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def add_issue(issues: list[dict[str, str]], level: str, path: str, message: str) -> None:
    issues.append({"level": level, "path": path, "message": message})


def shard_payload(shard: dict[str, Any]) -> dict[str, Any]:
    facts = shard.get("facts")
    return facts if isinstance(facts, dict) else shard


def section_from_shard(shard: dict[str, Any], section: str) -> Any:
    payload = shard_payload(shard)
    return payload.get(section)


def validate_agent_provenance(shard: dict[str, Any], spec: dict[str, Any], issues: list[dict[str, str]]) -> dict[str, Any]:
    provenance = shard.get("agent_provenance")
    if not isinstance(provenance, dict):
        add_issue(issues, "error", "agent_provenance", "Fact shard must include top-level agent_provenance.")
        return {}

    expected_extraction_agent = REFERENCE_FACT_EXTRACTION_AGENT
    expected_validation_agent = REFERENCE_FACT_VALIDATION_AGENT
    extraction_agent = str(provenance.get("extraction_agent") or "")
    validation_agent = str(provenance.get("validation_agent") or "")
    validation_status = str(provenance.get("validation_status") or "").strip().lower()
    execution_mode = str(provenance.get("execution_mode") or "").strip()

    if extraction_agent != expected_extraction_agent:
        add_issue(issues, "error", "agent_provenance.extraction_agent", f"Expected extraction_agent {expected_extraction_agent!r}; got {extraction_agent!r}.")
    if validation_agent != expected_validation_agent:
        add_issue(issues, "error", "agent_provenance.validation_agent", f"Expected validation_agent {expected_validation_agent!r}; got {validation_agent!r}.")
    if validation_status not in PASSING_VALIDATION_STATUSES:
        add_issue(issues, "error", "agent_provenance.validation_status", f"Expected validation_status to be one of {sorted(PASSING_VALIDATION_STATUSES)}.")
    if execution_mode != REQUIRED_PROVENANCE_EXECUTION_MODE:
        add_issue(
            issues,
            "error",
            "agent_provenance.execution_mode",
            "Formal fact shards require execution_mode='subagent'. same_thread_separated_pass is not accepted for downstream-consumable facts; rerun extraction and validation with separate subagents.",
        )
    if not provenance.get("extraction_completed_at"):
        add_issue(issues, "error", "agent_provenance.extraction_completed_at", "Record when the extraction role completed this shard.")
    if not provenance.get("validation_completed_at"):
        add_issue(issues, "error", "agent_provenance.validation_completed_at", "Record when the validation role reviewed this shard.")
    for key in sorted(REQUIRED_PROVENANCE_LIST_FIELDS):
        values = provenance.get(key)
        if not isinstance(values, list) or not any(str(value).strip() for value in values):
            add_issue(issues, "error", f"agent_provenance.{key}", "Must be a non-empty list for auditability.")
    return provenance


def validate_shard(profile: dict[str, Any], shard_type: str, path: Path) -> dict[str, Any]:
    specs = shard_specs(profile)
    if shard_type not in specs:
        raise ValueError(f"Unknown shard_type {shard_type!r} for profile {profile.get('profile_id')!r}")
    spec = specs[shard_type]
    expected_schema = schema_version(profile, "generic_fact_shard", DEFAULT_SHARD_SCHEMA_VERSION)
    issues: list[dict[str, str]] = []
    report: dict[str, Any] = {
        "schema_version": "reference-fact-shard-validation-v1",
        "profile_id": profile.get("profile_id"),
        "shard_type": shard_type,
        "shard_label": spec.get("label", shard_type),
        "path": str(path),
        "agent_provenance": {},
        "errors": [],
        "warnings": [],
        "passed": False,
    }
    if not path.exists():
        add_issue(issues, "error", str(path), "Fact shard file is missing.")
        report["errors"] = [issue for issue in issues if issue["level"] == "error"]
        return report
    try:
        shard = read_json(path)
    except json.JSONDecodeError as exc:
        add_issue(issues, "error", str(path), f"Fact shard is not valid JSON: {exc}.")
        report["errors"] = [issue for issue in issues if issue["level"] == "error"]
        return report
    if not isinstance(shard, dict):
        add_issue(issues, "error", str(path), "Fact shard must be a JSON object.")
        report["errors"] = [issue for issue in issues if issue["level"] == "error"]
        return report

    shard_schema = shard.get("schema_version")
    if shard_schema not in {expected_schema, profile.get("schema_versions", {}).get("domain_fact_shard")}:
        add_issue(issues, "error", "schema_version", f"Expected {expected_schema!r}; got {shard_schema!r}.")
    if shard.get("profile_id", profile.get("profile_id")) != profile.get("profile_id"):
        add_issue(issues, "error", "profile_id", f"Expected profile_id {profile.get('profile_id')!r}.")
    if shard.get("shard_type") != shard_type:
        add_issue(issues, "error", "shard_type", f"Expected {shard_type!r}; got {shard.get('shard_type')!r}.")

    provenance = validate_agent_provenance(shard, spec, issues)
    payload = shard_payload(shard)
    allowed = {str(value) for value in spec.get("allowed_sections") or []}
    present_fact_sections = {key for key in payload if key in fact_sections(profile)}
    disallowed = sorted(present_fact_sections - allowed)
    if disallowed:
        add_issue(issues, "error", "facts", f"Shard contains facts owned by another extraction role: {disallowed}.")
    for section in sorted(str(value) for value in spec.get("required_sections") or []):
        value = section_from_shard(shard, section)
        if not isinstance(value, dict) or not value:
            add_issue(issues, "error", section, "Required shard section is missing or empty.")

    errors = [issue for issue in issues if issue["level"] == "error"]
    warnings = [issue for issue in issues if issue["level"] == "warning"]
    report.update({"agent_provenance": provenance, "errors": errors, "warnings": warnings, "passed": not errors})
    return report


def main() -> None:
    require_internal_context("validate_fact_shards.py")
    parser = argparse.ArgumentParser(description="Validate reference fact shards before fact-packet assembly.")
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--shard", action="append", default=[], help="Shard mapping in the form shard_type=path.json")
    parser.add_argument("--report-out", type=Path, required=True)
    args = parser.parse_args()

    profile = load_profile(args.profile_id)
    reports = []
    for item in args.shard:
        if "=" not in item:
            raise SystemExit(f"Invalid --shard value {item!r}; expected shard_type=path")
        shard_type, path_text = item.split("=", 1)
        reports.append(validate_shard(profile, shard_type, Path(path_text)))

    output = {
        "schema_version": "reference-fact-shard-validation-report-v1",
        "profile_id": profile.get("profile_id"),
        "reports": reports,
        "passed": all(report.get("passed") for report in reports) and bool(reports),
    }
    write_json(args.report_out, output)
    print(json.dumps({"status": "validated", "passed": output["passed"], "report": str(args.report_out)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
