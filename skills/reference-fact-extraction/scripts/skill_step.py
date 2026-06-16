from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime_guard import make_internal_env
from profile_loader import REFERENCE_FACT_EXTRACTION_AGENT, REFERENCE_FACT_VALIDATION_AGENT


SKILL_ROOT = Path(__file__).resolve().parents[1]
STEP_EVENT_SCHEMA = "reference-fact-extraction-step-event-v1"
STEP_RESPONSE_SCHEMA = "reference-fact-extraction-step-response-v1"
FORBIDDEN_TOP_LEVEL_KEYS = {"action", "tool_call", "next_state", "state", "submit", "finalize", "render", "validate"}

STATE_NEW = "NEW"
STATE_SOURCES_REGISTERED = "SOURCES_REGISTERED"
STATE_SOURCE_INDEX_READY = "SOURCE_INDEX_READY"
STATE_PRIMARY_CONTEXT_SHARD_REQUIRED = "PRIMARY_CONTEXT_SHARD_REQUIRED"
STATE_DOMAIN_SHARDS_REQUIRED = "DOMAIN_SHARDS_REQUIRED"
STATE_FACT_PACKET_VALIDATING = "FACT_PACKET_VALIDATING"
STATE_COMPLETED_FINAL = "COMPLETED_FINAL"
STATE_FAILED = "FAILED"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def user_path(value: Any) -> Path:
    text = str(value)
    path = Path(text)
    if text == "~" or text.startswith("~/") or text.startswith("~\\"):
        return path.expanduser()
    return path


def resolve_path(value: str | Path, base_dir: Path | None = None, field_name: str = "path") -> Path:
    path = user_path(value)
    if path.is_absolute():
        return path.resolve()
    if base_dir is None:
        raise ValueError(f"Relative {field_name} requires project_root: {value}")
    return (base_dir / path).resolve()


def source_path(source: Any) -> str:
    if isinstance(source, dict):
        return str(source.get("path") or source.get("file") or "")
    return str(source)


def normalize_sources(values: list[Any], project_root: Path | None) -> list[str]:
    paths = []
    for value in values:
        text = source_path(value)
        if text:
            paths.append(str(resolve_path(text, project_root, "sources[]")))
    return paths


def load_profile(profile_id: str) -> dict[str, Any]:
    return read_json(SKILL_ROOT / "profiles" / profile_id / "profile.json")


def allowed_event(output_dir: Path, profile_id: str, artifacts: dict[str, str]) -> dict[str, Any]:
    return {
        "schema_version": STEP_EVENT_SCHEMA,
        "output_dir": str(output_dir),
        "profile_id": profile_id,
        "provided_artifacts": artifacts,
    }


def shard_output_name(spec: dict[str, Any], fallback_key: str) -> str:
    path_name = str(spec.get("path_name") or fallback_key).strip()
    if path_name.endswith(".json"):
        return path_name
    return f"{path_name}.json"


def artifact_paths_for_required(profile: dict[str, Any], required: list[str], output_dir: Path) -> dict[str, str]:
    specs = profile.get("shards") if isinstance(profile.get("shards"), list) else []
    spec_by_key = {str(spec.get("artifact_key")): spec for spec in specs if isinstance(spec, dict) and spec.get("artifact_key")}
    paths: dict[str, str] = {}
    for key in required:
        spec = spec_by_key.get(key, {})
        paths[key] = str(output_dir / "fact-shards" / shard_output_name(spec, key))
    return paths


def subagent_gate() -> dict[str, Any]:
    return {
        "subagent_required": True,
        "required_execution_mode": "subagent",
        "extraction_agent": REFERENCE_FACT_EXTRACTION_AGENT,
        "validation_agent": REFERENCE_FACT_VALIDATION_AGENT,
        "fallback_when_unavailable": "SUBAGENT_REQUIRED",
    }


def response(state: str, reply: str, output_dir: Path, profile_id: str, required: list[str], artifacts: dict[str, str], final: bool = False, blocking: list[str] | None = None, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    blocking = blocking or []
    recoverable = bool(required) and not final and not blocking
    next_artifacts = artifact_paths_for_required(profile or {}, required, output_dir) if required else {}
    out = {
        "schema_version": STEP_RESPONSE_SCHEMA,
        "state": state,
        "reply": reply,
        "done": final or bool(blocking),
        "final": final,
        "terminal_kind": "final" if final else ("blocked_not_deliverable" if blocking else "paused_recoverable" if recoverable else "running"),
        "delivery_status": "final_candidate" if final else "not_deliverable" if blocking else "not_ready",
        "recoverable": recoverable,
        "recovery_mode": "submit_fact_shards" if required else "none",
        "allowed_next_events": [allowed_event(output_dir, profile_id, next_artifacts)] if required else [],
        "required_artifacts": required,
        "blocking_reasons": blocking,
        "artifacts": artifacts,
    }
    if required:
        out["gates"] = {"subagent": subagent_gate()}
    return out


def profile_shard_specs(profile: dict[str, Any]) -> list[dict[str, Any]]:
    shards = profile.get("shards") if isinstance(profile.get("shards"), list) else []
    return [spec for spec in shards if isinstance(spec, dict)]


def shard_request_entry(spec: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    artifact_key = str(spec.get("artifact_key") or spec.get("shard_type") or "")
    return {
        "shard_type": spec.get("shard_type"),
        "artifact_key": artifact_key,
        "required_output_path": str(output_dir / "fact-shards" / shard_output_name(spec, artifact_key)),
        "extraction_agent": REFERENCE_FACT_EXTRACTION_AGENT,
        "validation_agent": REFERENCE_FACT_VALIDATION_AGENT,
        "allowed_sections": spec.get("allowed_sections") or [],
        "required_sections": spec.get("required_sections") or [],
        "depends_on": spec.get("depends_on") or [],
        "subagent_provenance_required": {
            "execution_mode": "subagent",
            "extraction_agent": REFERENCE_FACT_EXTRACTION_AGENT,
            "validation_agent": REFERENCE_FACT_VALIDATION_AGENT,
            "validation_status": "passed | passed_with_warnings",
            "source_materials_reviewed": "non-empty list",
            "validation_checks": "non-empty list",
        },
    }


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_fact_extraction_request(
    output_dir: Path,
    profile: dict[str, Any],
    profile_id: str,
    output_mode: str,
    stage: str,
    required_keys: list[str],
    artifacts: dict[str, str],
    provided: dict[str, Any],
    project_root: Path | None,
) -> None:
    specs = profile_shard_specs(profile)
    spec_by_key = {str(spec.get("artifact_key")): spec for spec in specs if spec.get("artifact_key")}
    requested_specs = [spec_by_key[key] for key in required_keys if key in spec_by_key]
    primary = str(profile.get("primary_context_shard") or "")
    primary_spec = next((spec for spec in specs if spec.get("shard_type") == primary), None)
    primary_key = str(primary_spec.get("artifact_key")) if isinstance(primary_spec, dict) else ""
    primary_path = None
    primary_hash = None
    if primary_key and provided.get(primary_key):
        primary_path = resolve_path(provided[primary_key], project_root, f"provided_artifacts.{primary_key}")
        if primary_path.exists():
            primary_hash = sha256_file(primary_path)
    request = {
        "schema_version": "reference-fact-extraction-request-v1",
        "profile_id": profile_id,
        "stage": stage,
        "output_mode": output_mode,
        "source_index": artifacts.get("source_index_json") or str(output_dir / "source-index.json"),
        "source_index_markdown": artifacts.get("source_index_md") or str(output_dir / "source-index.md"),
        "allowed_sources": "Use only sources listed as allowed in source-index.json.",
        "forbidden_sources": "Do not consume sources listed as forbidden/excluded in source-index.json.",
        "required_output_paths": artifact_paths_for_required(profile, required_keys, output_dir),
        "shards": [shard_request_entry(spec, output_dir) for spec in requested_specs],
        "validated_primary_context": {
            "required": stage == "domain_shards",
            "artifact_key": primary_key or None,
            "path": str(primary_path) if primary_path else None,
            "sha256": primary_hash,
            "validation_report": artifacts.get("fact_shard_validation"),
            "profile_context": "Read the verified project_profile shard before extracting dependent domain shards.",
        },
        "required_packet_output": str(output_dir / "fact-packet.json"),
        "subagent_gate": f"Every shard must be extracted by {REFERENCE_FACT_EXTRACTION_AGENT} and independently validated by {REFERENCE_FACT_VALIDATION_AGENT}; execution_mode must be subagent. The active profile defines shard scope, fields, dependencies, and validation rules.",
    }
    write_json(output_dir / "fact-extraction-request.json", request)
    artifacts["fact_extraction_request"] = str(output_dir / "fact-extraction-request.json")

def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, env=make_internal_env("skill_step.py", "run reference fact extraction step"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Public step entry point for reference fact extraction.")
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument("--response-out", type=Path, required=True)
    args = parser.parse_args()

    event = read_json(args.event)
    if event.get("schema_version") != STEP_EVENT_SCHEMA:
        raise SystemExit(f"Expected schema_version {STEP_EVENT_SCHEMA!r}")
    forbidden = sorted(FORBIDDEN_TOP_LEVEL_KEYS.intersection(event))
    if forbidden:
        raise SystemExit(f"Step event contains forbidden command/state keys: {forbidden}")

    project_root = resolve_path(event["project_root"], None, "project_root") if event.get("project_root") else None
    output_dir = resolve_path(event["output_dir"], project_root, "output_dir")
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_id = str(event.get("profile_id") or "").strip()
    if not profile_id:
        raise SystemExit("Missing required profile_id; upstream must provide an explicit profile. No generic fallback is allowed.")
    profile = load_profile(profile_id)
    artifacts: dict[str, str] = {}

    sources = normalize_sources(event.get("sources") or [], project_root)
    forbidden_sources = normalize_sources(event.get("forbidden_sources") or [], project_root)
    provided = event.get("provided_artifacts") if isinstance(event.get("provided_artifacts"), dict) else {}

    if sources:
        workflow_state = {
            "schema_version": "reference-fact-extraction-workflow-state-v1",
            "profile_id": profile_id,
            "output_dir": str(output_dir),
            "sources": sources,
            "forbidden_sources": forbidden_sources,
            "generated_at": now_iso(),
        }
        state_path = output_dir / "workflow-state.json"
        write_json(state_path, workflow_state)
        cmd = [
            sys.executable,
            str(SKILL_ROOT / "scripts" / "extract_source_index.py"),
            "--workflow-state",
            str(state_path),
            "--output-dir",
            str(output_dir),
        ]
        for source in sources:
            cmd.extend(["--source", source])
        for source in forbidden_sources:
            cmd.extend(["--forbidden-source", source])
        run(cmd)
        artifacts.update({"source_index_json": str(output_dir / "source-index.json"), "source_index_md": str(output_dir / "source-index.md")})

    primary = str(profile.get("primary_context_shard") or "")
    shards = profile_shard_specs(profile)
    primary_spec = next((s for s in shards if s.get("shard_type") == primary), None)
    primary_artifact = primary_spec.get("artifact_key") if isinstance(primary_spec, dict) else None

    options = event.get("options") if isinstance(event.get("options"), dict) else {}
    output_modes = {str(mode).strip() for mode in profile.get("output_modes", []) if str(mode).strip()}
    if not output_modes:
        out = response(STATE_FAILED, "Profile must declare output_modes.", output_dir, profile_id, [], artifacts, blocking=["Profile output_modes is missing or empty; no generic output_mode fallback is allowed."], profile=profile)
        write_json(args.response_out, out)
        return
    requested_output_mode = options.get("output_mode") or profile.get("default_output_mode")
    if not requested_output_mode:
        out = response(STATE_FAILED, "Output mode is required for profile.", output_dir, profile_id, [], artifacts, blocking=["Provide options.output_mode or set profile.default_output_mode; no generic output_mode fallback is allowed."], profile=profile)
        write_json(args.response_out, out)
        return
    output_mode = str(requested_output_mode).strip()
    if output_mode not in output_modes:
        out = response(STATE_FAILED, "Unsupported output mode for profile.", output_dir, profile_id, [], artifacts, blocking=[f"output_mode {output_mode!r} is not in profile.output_modes {sorted(output_modes)}"], profile=profile)
        write_json(args.response_out, out)
        return

    if primary_artifact and primary_artifact not in provided:
        write_fact_extraction_request(output_dir, profile, profile_id, output_mode, "primary_context", [str(primary_artifact)], artifacts, provided, project_root)
        out = response(STATE_PRIMARY_CONTEXT_SHARD_REQUIRED, "Source index is ready. Submit the verified primary context fact shard produced by the required extraction and validation subagents; if subagents are unavailable, report SUBAGENT_REQUIRED.", output_dir, profile_id, [primary_artifact], artifacts, profile=profile)
        write_json(args.response_out, out)
        return

    if primary_artifact and primary_spec and provided.get(primary_artifact):
        primary_validation_report = output_dir / "fact-shard-validation.json"
        primary_path = resolve_path(provided[primary_artifact], project_root, f"provided_artifacts.{primary_artifact}")
        run([
            sys.executable,
            str(SKILL_ROOT / "scripts" / "validate_fact_shards.py"),
            "--profile-id",
            profile_id,
            "--shard",
            f"{primary_spec.get('shard_type')}={primary_path}",
            "--report-out",
            str(primary_validation_report),
        ])
        artifacts["fact_shard_validation"] = str(primary_validation_report)
        primary_validation = read_json(primary_validation_report)
        if not primary_validation.get("passed"):
            out = response(STATE_FAILED, "Primary context fact shard validation failed.", output_dir, profile_id, [], artifacts, blocking=["Fix primary context shard validation errors before extracting dependent domain shards."], profile=profile)
            write_json(args.response_out, out)
            return

    required_domain = [str(s.get("artifact_key")) for s in shards if s.get("shard_type") != primary and s.get("artifact_key")]
    missing_domain = [key for key in required_domain if key not in provided]
    if missing_domain:
        write_fact_extraction_request(output_dir, profile, profile_id, output_mode, "domain_shards", missing_domain, artifacts, provided, project_root)
        out = response(STATE_DOMAIN_SHARDS_REQUIRED, "Primary context shard is available. Submit the verified domain fact shards produced by the required extraction and validation subagents; if subagents are unavailable, report SUBAGENT_REQUIRED.", output_dir, profile_id, missing_domain, artifacts, profile=profile)
        write_json(args.response_out, out)
        return

    shard_args: list[str] = []
    for spec in shards:
        key = str(spec.get("artifact_key"))
        if key and provided.get(key):
            shard_args.extend(["--shard", f"{spec.get('shard_type')}={resolve_path(provided[key], project_root, f'provided_artifacts.{key}')}" ])
    if not shard_args:
        out = response(STATE_FAILED, "No sources or fact shards were provided.", output_dir, profile_id, [], artifacts, blocking=["Provide sources or profile fact shards."], profile=profile)
        write_json(args.response_out, out)
        return

    validation_report = output_dir / "fact-shard-validation.json"
    run([sys.executable, str(SKILL_ROOT / "scripts" / "validate_fact_shards.py"), "--profile-id", profile_id, *shard_args, "--report-out", str(validation_report)])
    validation = read_json(validation_report)
    artifacts["fact_shard_validation"] = str(validation_report)
    if not validation.get("passed"):
        out = response(STATE_FAILED, "Fact shard validation failed.", output_dir, profile_id, [], artifacts, blocking=["Fix shard validation errors before assembly."], profile=profile)
        write_json(args.response_out, out)
        return

    fact_packet = output_dir / "fact-packet.json"
    assembly_report = output_dir / "fact-packet-assembly-report.json"
    run([sys.executable, str(SKILL_ROOT / "scripts" / "assemble_fact_packet_from_shards.py"), "--profile-id", profile_id, *shard_args, "--output-mode", output_mode, "--fact-packet-out", str(fact_packet), "--report-out", str(assembly_report)])
    packet_validation = output_dir / "fact-packet-validation.json"
    run([sys.executable, str(SKILL_ROOT / "scripts" / "validate_fact_packet.py"), "--profile-id", profile_id, "--fact-packet", str(fact_packet), "--report-out", str(packet_validation)])
    packet_report = read_json(packet_validation)
    artifacts.update({"fact_packet": str(fact_packet), "assembly_report": str(assembly_report), "fact_packet_validation": str(packet_validation)})
    if not packet_report.get("passed"):
        out = response(STATE_FAILED, "Fact packet validation failed.", output_dir, profile_id, [], artifacts, blocking=["Fix fact packet validation errors."], profile=profile)
        write_json(args.response_out, out)
        return

    out = response(STATE_COMPLETED_FINAL, "Verified fact packet is ready for downstream consumption.", output_dir, profile_id, [], artifacts, final=True, profile=profile)
    write_json(args.response_out, out)


if __name__ == "__main__":
    main()
