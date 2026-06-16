from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime_guard import make_internal_env, require_internal_context
from workflow_states import (
    BODY_SECTIONS_INPUT_STATES,
    BODY_SECTIONS_READY_STATES,
    BODY_SKELETON_READY_STATES,
    FACT_PACKET_INPUT_STATES,
    FACT_PACKET_READY_STATES,
    FINALIZE_READY_STATES,
    STATE_BODY_SKELETON_COMPLETED,
    STATE_BODY_SKELETON_REQUIRED,
    STATE_BODY_SKELETON_SKIPPED_INTERMEDIATE_ONLY,
    STATE_BODY_SECTIONS_REQUIRED,
    STATE_BODY_SECTIONS_REVISION_REQUIRED,
    STATE_BODY_SECTIONS_SUBMITTED,
    STATE_BODY_SECTIONS_VALID,
    STATE_BODY_SECTIONS_VALIDATING,
    STATE_COMPLETED_FINAL,
    STATE_COMPLETED_INTERMEDIATE,
    STATE_FACT_EXTRACTION_REQUIRED,
    STATE_FACT_PROJECT_PROFILE_REQUIRED,
    STATE_FACT_PACKET_SUBMITTED,
    STATE_FACT_PACKET_VALID,
    STATE_FACT_PACKET_VALIDATING,
    STATE_FACT_PACKET_REVISION_REQUIRED,
    STATE_FACT_STUDY_SHARDS_REQUIRED,
    STATE_FAILED,
    STATE_FINAL_VALIDATING,
    STATE_MISSING_EVIDENCE_RECOVERY_EXHAUSTED,
    STATE_MISSING_EVIDENCE_RECOVERY_REQUIRED,
    STATE_NEW,
    STATE_PAUSED,
    STATE_PIPELINE_COMPLETED,
    STATE_PIPELINE_RUNNING,
    STATE_SOURCES_REGISTERED,
    STATE_TABLE_RENDER_READY,
    STATE_TREND_CHARTS_REQUIRED,
    STATE_TREND_CHARTS_REVISION_REQUIRED,
    STATE_TREND_CHARTS_SUBMITTED,
    STATE_TREND_CHARTS_VALID,
    STATE_TREND_CHARTS_VALIDATING,
    STATE_WORKSPACE_PREPARED,
    TREND_CHARTS_INPUT_STATES,
    TREND_CHARTS_READY_STATES,
    allowed_artifacts_for_state,
    delivery_status_for_state,
    state_is_paused_or_terminal,
    terminal_kind_for_state,
)
from validate_fact_packet import required_body_section_keys, trend_chart_candidates


SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = SKILL_ROOT / "assets" / "templates" / "ctd-32s73-stability-template-v2.docx"
DEFAULT_TEMPLATE_ID = "ctd-32s73-stability-template-v2"
PREPARE_SCRIPT = SKILL_ROOT / "scripts" / "prepare_generation_workspace.py"
SOURCE_INDEX_SCRIPT = SKILL_ROOT / "scripts" / "extract_source_index.py"
PDF_OCR_SCRIPT = SKILL_ROOT / "scripts" / "extract_reference_pdf_ocr.py"
VALIDATE_FACT_SCRIPT = SKILL_ROOT / "scripts" / "validate_fact_packet.py"
PIPELINE_SCRIPT = SKILL_ROOT / "scripts" / "run_generation_pipeline.py"
INSPECT_SCRIPT = SKILL_ROOT / "scripts" / "inspect_docx_structure.py"
BUILTIN_BODY_SKELETON_SCRIPT = SKILL_ROOT / "scripts" / "generate_body_skeleton.py"
PDF_OCR_SUFFIXES = {".pdf"}
PDF_OCR_BLOCKING_PREFIXES = (
    "PDF OCR skipped",
    "PDF OCR failed",
    "PDF OCR is required before fact extraction",
    "PDF source is missing and cannot be parsed",
    "auto PDF OCR skipped",
    "auto PDF OCR failed",
)

BODY_SECTION_PLACEHOLDERS = [
    "{{LONG_TERM_INTRO}}",
    "{{LONG_TERM_TREND_INTRO}}",
    "{{LONG_TERM_TREND_SUMMARY}}",
    "{{ACCELERATED_INTRO}}",
    "{{ACCELERATED_TREND_INTRO}}",
    "{{ACCELERATED_TREND_SUMMARY}}",
    "{{STRESS_STUDY_INTRO}}",
    "{{STRESS_LIGHT_REFERENCE}}",
    "{{STRESS_LIGHT_SUMMARY}}",
    "{{STRESS_AGITATION_SUMMARY}}",
    "{{STRESS_FREEZE_THAW_SUMMARY}}",
    "{{STRESS_HIGH_TEMPERATURE_SUMMARY}}",
    "{{STRESS_PH_SUMMARY}}",
    "{{STRESS_OXIDATION_SUMMARY}}",
    "{{FINAL_STABILITY_CONCLUSION}}",
]

ARTIFACT_PROFILES = {"full", "audit", "delivery"}
CORE_ARTIFACT_FILENAMES = {
    "workflow-state.json",
    "workflow-summary.json",
    "workflow-events.jsonl",
    "step-config.json",
    "step-response.json",
    "next-step-event.json",
}
DELIVERY_ARTIFACT_FILENAMES = {
    "filled.docx",
    "validation-report.md",
    "evidence-summary.md",
}
AUDIT_ARTIFACT_FILENAMES = {
    "body-section-application.json",
    "body-sections-applied.docx",
    "body-sections-request.json",
    "body-sections-validation.json",
    "body-skeleton-report.json",
    "fact-packet-validation.json",
    "fact-packet.json",
    "figure-render-manifest.json",
    "generation-manifest.json",
    "missing-evidence-report.md",
    "ocr-auto-summary.json",
    "source-index.json",
    "source-index.md",
    "stress-section-render.json",
    "stress-sections-rendered.docx",
    "table-render-manifest.json",
    "trend-chart-input.json",
    "trend-chart-manifest.json",
    "trend-charts-request.json",
    "trend-charts-validation.json",
    "validation.json",
}
AUDIT_ARTIFACT_DIRNAMES = {
    "trend-charts",
    "ocr",
}

EXPECTED_FACT_PACKET_AGENT_PROVENANCE = {
    "project_profile": {
        "extraction_agent": "reference_fact_extraction_agent",
        "validation_agent": "reference_fact_validation_agent",
    },
    "long_term": {
        "extraction_agent": "reference_fact_extraction_agent",
        "validation_agent": "reference_fact_validation_agent",
    },
    "accelerated": {
        "extraction_agent": "reference_fact_extraction_agent",
        "validation_agent": "reference_fact_validation_agent",
    },
    "stress_study": {
        "extraction_agent": "reference_fact_extraction_agent",
        "validation_agent": "reference_fact_validation_agent",
    },
}
PASSING_AGENT_VALIDATION_STATUSES = {"passed", "passed_with_warnings"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def append_event(output_dir: Path, event: dict[str, Any]) -> None:
    event = {"timestamp": now_iso(), **event}
    events_path = output_dir / "workflow-events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def run_command(command: list[str], stdout_path: Path, output_dir: Path, name: str) -> dict[str, Any]:
    append_event(output_dir, {"event": "command_started", "name": name, "command": command})
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        env=make_internal_env("run_state_machine_workflow.py", f"run {name}"),
    )
    write_text(stdout_path, completed.stdout)
    stderr_path = None
    if completed.stderr:
        stderr_path = stdout_path.with_suffix(stdout_path.suffix + ".stderr.txt")
        write_text(stderr_path, completed.stderr)
    result = {
        "command": command,
        "returncode": completed.returncode,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path) if stderr_path else None,
    }
    append_event(output_dir, {"event": "command_finished", "name": name, "returncode": completed.returncode})
    return result


def load_state(output_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    state_path = output_dir / "workflow-state.json"
    if args.resume and state_path.exists():
        return read_json(state_path)
    return {
        "schema_version": "ctd-32s73-workflow-state-v1",
        "run_id": output_dir.name,
        "skill_root": str(SKILL_ROOT),
        "output_dir": str(output_dir),
        "state": STATE_NEW,
        "previous_states": [],
        "template": {
            "template_id": args.template_id,
            "template_path": str(args.template.resolve()),
            "contract_path": str((SKILL_ROOT / "references" / "template-contract.md").resolve()),
        },
        "sources": {
            "allowed": [{"path": source, "status": "registered"} for source in args.source],
            "forbidden": [{"path": source, "status": "excluded"} for source in args.forbidden_source],
        },
        "artifacts": {},
        "commands": {},
        "automation_boundaries": {
            "pdf_ocr": "mandatory_for_allowed_pdf_sources_before_fact_extraction_reuses_existing_ocr_or_paddleocr_token",
            "fact_extraction": "external_reference_fact_extraction_ctd_native_fact_packet_required",
            "trend_chart_extraction": "project_specific_trend_charts_required_after_body_table_fact_packet_validation",
            "body_sections_drafting": "project_specific_body_sections_required_after_trend_chart_validation",
            "missing_evidence_recovery": "project_specific_required_when_unresolved",
            "body_skeleton_docx_editing": "builtin_semantic_placeholder_replacement_with_project_hook_override",
            "table_block_generation": "automated_from_table_block_placeholders_by_run_generation_pipeline",
            "docx_validation": "automated_by_validate_generated_docx",
        },
        "blocking_reasons": [],
        "trend_charts_validated": False,
        "body_sections_validated": False,
        "final": False,
        "resume_command": f"python .\\scripts\\skill_step.py --event {output_dir}\\next-step-event.json",
    }


def save_state(output_dir: Path, state: dict[str, Any]) -> None:
    write_json(output_dir / "workflow-state.json", state)


def transition(output_dir: Path, state: dict[str, Any], new_state: str, status: str = "completed", reason: str | None = None) -> None:
    old_state = state.get("state", STATE_NEW)
    state.setdefault("previous_states", []).append(
        {
            "state": old_state,
            "exited_at": now_iso(),
            "status": status,
            "reason": reason,
        }
    )
    state["state"] = new_state
    append_event(output_dir, {"event": "state_transition", "from": old_state, "to": new_state, "reason": reason})
    save_state(output_dir, state)


def write_summary(output_dir: Path, state: dict[str, Any], status: str, final: bool, blocking_reasons: list[str]) -> None:
    artifacts = state.setdefault("artifacts", {})
    state_name = state.get("state")
    has_required_artifacts = bool(allowed_artifacts_for_state(state_name, blocking_reasons))
    summary = {
        "status": status,
        "state": state_name,
        "final": final,
        "delivery_status": delivery_status_for_state(state_name, final),
        "terminal_kind": terminal_kind_for_state(state_name, final, has_required_artifacts),
        "filled_docx": artifacts.get("filled_docx"),
        "validation_json": artifacts.get("validation_json"),
        "validation_passed": state.get("validation_passed"),
        "trend_charts_validated": bool(state.get("trend_charts_validated")),
        "body_sections_validated": bool(state.get("body_sections_validated")),
        "artifact_profile": state.get("artifact_profile"),
        "artifact_directories": state.get("artifact_directories"),
        "blocking_reasons": blocking_reasons,
        "not_automated_by_state_machine": [
            "project-specific CTD-native fact-packet generation by reference-fact-extraction before this skill consumes it",
            "project-specific trend chart data extraction after body/table validation",
            "project-specific body_sections drafting after trend chart validation",
            "project-specific missing evidence recovery",
            "high-fidelity project-specific prose rewriting beyond the built-in body/skeleton generator",
        ],
    }
    state["final"] = final
    state["blocking_reasons"] = blocking_reasons
    write_json(output_dir / "workflow-summary.json", summary)
    save_state(output_dir, state)


def path_is_top_level_child(path: Path, output_dir: Path) -> bool:
    try:
        return path.resolve().parent == output_dir.resolve()
    except OSError:
        return False


def move_artifact(path: Path, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    if target.exists():
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    shutil.move(str(path), str(target))
    return target


def update_relocated_paths(value: Any, normalized_map: dict[str, str]) -> Any:
    if isinstance(value, dict):
        for key, item in list(value.items()):
            value[key] = update_relocated_paths(item, normalized_map)
        return value
    if isinstance(value, list):
        for idx, item in enumerate(value):
            value[idx] = update_relocated_paths(item, normalized_map)
        return value
    if isinstance(value, str):
        try:
            old = str(Path(value).resolve())
        except OSError:
            return value
        return normalized_map.get(old, value)
    return value


def update_state_paths(state: dict[str, Any], relocation_map: dict[str, str]) -> None:
    normalized = {str(Path(old).resolve()): new for old, new in relocation_map.items()}
    update_relocated_paths(state, normalized)


def classify_artifact(child: Path, profile: str) -> str | None:
    if child.name in CORE_ARTIFACT_FILENAMES or child.name in {"delivery", "_audit", "_debug"}:
        return None
    if child.name in DELIVERY_ARTIFACT_FILENAMES:
        return "delivery" if profile == "delivery" else None
    if child.name in AUDIT_ARTIFACT_FILENAMES or child.name in AUDIT_ARTIFACT_DIRNAMES:
        return "_audit" if profile == "delivery" else None
    return "_debug"


def write_artifact_index(output_dir: Path, state: dict[str, Any], profile: str, relocation_map: dict[str, str]) -> None:
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    lines = [
        "# Artifact Index",
        "",
        f"- artifact_profile: `{profile}`",
        f"- state: `{state.get('state')}`",
        f"- final: `{bool(state.get('final'))}`",
        "",
        "## Key Artifacts",
    ]
    for key in sorted(artifacts):
        lines.append(f"- `{key}`: `{artifacts[key]}`")
    if relocation_map:
        lines.extend(["", "## Relocated Files"])
        for old, new in sorted(relocation_map.items()):
            lines.append(f"- `{Path(old).name}` -> `{new}`")
    index_path = output_dir / "artifact-index.md"
    write_text(index_path, "\n".join(lines) + "\n")
    if profile == "delivery":
        delivery_dir = output_dir / "delivery"
        delivery_dir.mkdir(exist_ok=True)
        shutil.copyfile(output_dir / "workflow-summary.json", delivery_dir / "workflow-summary.json")
        shutil.copyfile(index_path, delivery_dir / "artifact-index.md")


def organize_final_artifacts(output_dir: Path, state: dict[str, Any], profile: str) -> dict[str, str]:
    if profile == "full":
        state["artifact_profile"] = profile
        state["artifact_directories"] = {"delivery": None, "audit": None, "debug": None}
        save_state(output_dir, state)
        return {}
    if profile not in ARTIFACT_PROFILES:
        raise SystemExit(f"Unsupported artifact profile: {profile}")

    relocation_map: dict[str, str] = {}
    for child in list(output_dir.iterdir()):
        bucket = classify_artifact(child, profile)
        if bucket is None:
            continue
        if not path_is_top_level_child(child, output_dir):
            continue
        old_path = str(child.resolve())
        target = move_artifact(child, output_dir / bucket)
        relocation_map[old_path] = str(target.resolve())

    update_state_paths(state, relocation_map)
    if relocation_map:
        map_dir = output_dir / "_audit" if profile == "delivery" else output_dir
        write_json(map_dir / "artifact-relocation-map.json", relocation_map)
    state["artifact_profile"] = profile
    state["artifact_directories"] = {
        "delivery": str((output_dir / "delivery").resolve()) if (output_dir / "delivery").exists() else None,
        "audit": str((output_dir / "_audit").resolve()) if (output_dir / "_audit").exists() else None,
        "debug": str((output_dir / "_debug").resolve()) if (output_dir / "_debug").exists() else None,
    }
    save_state(output_dir, state)
    return relocation_map


def stop_at(output_dir: Path, state: dict[str, Any], stop_state: str, args: argparse.Namespace) -> bool:
    if args.until and state.get("state") == args.until:
        write_summary(output_dir, state, stop_state.lower(), False, state.get("blocking_reasons", []))
        print(json.dumps({"status": "stopped_at_until", "state": state.get("state"), "state_file": str(output_dir / "workflow-state.json")}, ensure_ascii=False, indent=2))
        return True
    return False


def forbidden_from_source(source: str) -> bool:
    name = Path(source).name
    return name.startswith("已完成的申报资料") or name.startswith("~$")


def register_sources(output_dir: Path, state: dict[str, Any], args: argparse.Namespace) -> None:
    allowed = []
    forbidden = [{"path": source, "status": "excluded"} for source in args.forbidden_source]
    for source in args.source:
        if forbidden_from_source(source):
            forbidden.append({"path": source, "status": "excluded", "reason": "Default forbidden source pattern."})
        else:
            allowed.append({"path": source, "status": "registered"})
    state["sources"] = {"allowed": allowed, "forbidden": forbidden}
    save_state(output_dir, state)


def source_path(source: Any) -> str:
    if isinstance(source, dict):
        return str(source.get("path") or source.get("file") or "")
    return str(source)


def allowed_pdf_sources(state: dict[str, Any]) -> list[Path]:
    values = state.get("sources", {}).get("allowed", [])
    if not isinstance(values, list):
        return []
    sources = []
    for item in values:
        path_text = source_path(item)
        if not path_text:
            continue
        path = Path(path_text)
        if path.suffix.lower() in PDF_OCR_SUFFIXES:
            sources.append(path)
    return sources


def ocr_dir_for_source(output_dir: Path, source: Path) -> Path:
    digest = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:10]
    safe_stem = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in source.stem)
    safe_stem = safe_stem[:80] or "source"
    return output_dir / "ocr" / f"{safe_stem}-{digest}"


def resolved_key(path: Path) -> str:
    try:
        return str(path.resolve()).casefold()
    except OSError:
        return str(path.absolute()).casefold()


def existing_ocr_artifact_for_source(output_dir: Path, source: Path) -> dict[str, Any] | None:
    if not output_dir.exists():
        return None
    source_name = source.name.casefold()
    source_resolved = resolved_key(source)
    for manifest_path in output_dir.rglob("*ocr-manifest*.json"):
        try:
            manifest = read_json(manifest_path)
        except (json.JSONDecodeError, OSError):
            continue
        manifest_source = str(manifest.get("source") or "")
        source_matches = (
            manifest_source.casefold() == str(source).casefold()
            or Path(manifest_source).name.casefold() == source_name
            or resolved_key(Path(manifest_source)) == source_resolved
        )
        if not source_matches:
            continue
        combined = manifest.get("combined_markdown")
        combined_path = Path(combined) if combined else None
        if combined_path and not combined_path.is_absolute():
            combined_path = (manifest_path.parent / combined_path).resolve()
        return {
            "manifest": str(manifest_path),
            "combined_markdown": str(combined_path) if combined_path else combined,
            "page_count": manifest.get("page_count") or manifest.get("extract_progress", {}).get("totalPages"),
        }
    return None


def clear_pdf_ocr_blocking_reasons(state: dict[str, Any]) -> None:
    blocking_reasons = state.get("blocking_reasons")
    if not isinstance(blocking_reasons, list):
        return
    state["blocking_reasons"] = [
        reason
        for reason in blocking_reasons
        if not any(str(reason).startswith(prefix) for prefix in PDF_OCR_BLOCKING_PREFIXES)
    ]


def ensure_pdf_ocr_artifacts(output_dir: Path, state: dict[str, Any]) -> None:
    pdf_sources = allowed_pdf_sources(state)
    if not pdf_sources:
        return
    clear_pdf_ocr_blocking_reasons(state)
    summary_path = output_dir / "ocr-auto-summary.json"
    token_available = bool(os.environ.get("PADDLEOCR_TOKEN"))
    summary: dict[str, Any] = {
        "enabled": True,
        "token_env": "PADDLEOCR_TOKEN",
        "token_available": token_available,
        "sources": [],
    }
    state.setdefault("artifacts", {})["ocr_auto_summary"] = str(summary_path)

    sources_requiring_ocr = []
    for source in pdf_sources:
        existing_artifact = existing_ocr_artifact_for_source(output_dir, source)
        if existing_artifact:
            summary["sources"].append(
                {
                    "source": str(source),
                    "exists": source.exists(),
                    "status": "existing_ocr",
                    **existing_artifact,
                }
            )
        elif not source.exists():
            summary["sources"].append(
                {
                    "source": str(source),
                    "exists": False,
                    "status": "skipped_missing_source",
                }
            )
        else:
            sources_requiring_ocr.append(source)

    if not sources_requiring_ocr:
        summary["status"] = "completed"
        state.setdefault("commands", {})["auto_ocr_pdf"] = {
            "returncode": 0,
            "summary": str(summary_path),
            "source_count": len(summary["sources"]),
            "failed_source_count": 0,
        }
        write_json(summary_path, summary)
        save_state(output_dir, state)
        return

    if not token_available:
        summary["status"] = "skipped_missing_token"
        summary["note"] = "Set PADDLEOCR_TOKEN and rerun the same output_dir to generate OCR artifacts before source indexing."
        for source in sources_requiring_ocr:
            summary["sources"].append(
                {
                    "source": str(source),
                    "exists": source.exists(),
                    "status": "skipped_missing_token",
                }
            )
        state.setdefault("commands", {})["auto_ocr_pdf"] = {
            "returncode": None,
            "skipped": True,
            "reason": "missing PADDLEOCR_TOKEN",
            "summary": str(summary_path),
            "source_count": len(pdf_sources),
            "missing_ocr_source_count": len(sources_requiring_ocr),
        }
        blocking_reasons = state.setdefault("blocking_reasons", [])
        reason = "PDF OCR skipped because PADDLEOCR_TOKEN is not set"
        if reason not in blocking_reasons:
            blocking_reasons.append(reason)
        write_json(summary_path, summary)
        save_state(output_dir, state)
        return

    command_results = []
    for source in sources_requiring_ocr:
        source_record: dict[str, Any] = {
            "source": str(source),
            "exists": source.exists(),
        }
        ocr_dir = ocr_dir_for_source(output_dir, source)
        manifest_path = ocr_dir / "ocr-manifest.json"

        command = [
            sys.executable,
            str(PDF_OCR_SCRIPT),
            str(source),
            "--output-dir",
            str(ocr_dir),
            "--manifest-out",
            str(manifest_path),
        ]
        output_name = f"workflow-ocr-{len(command_results) + 1:03d}.txt"
        result = run_command(command, output_dir / output_name, output_dir, f"auto_ocr_pdf_{len(command_results) + 1:03d}")
        command_results.append(result)
        source_record.update(
            {
                "status": "ocr_completed" if result["returncode"] == 0 else "ocr_failed",
                "manifest": str(manifest_path) if manifest_path.exists() else None,
                "combined_markdown": str(ocr_dir / "combined.md") if (ocr_dir / "combined.md").exists() else None,
                "command_stdout": result.get("stdout"),
                "command_stderr": result.get("stderr"),
                "returncode": result["returncode"],
            }
        )
        summary["sources"].append(source_record)

    failed_sources = [source for source in summary["sources"] if source.get("status") == "ocr_failed"]
    summary["status"] = "completed_with_failures" if failed_sources else "completed"
    state.setdefault("commands", {})["auto_ocr_pdf"] = {
        "returncode": 1 if failed_sources else 0,
        "summary": str(summary_path),
        "source_count": len(summary["sources"]),
        "failed_source_count": len(failed_sources),
        "results": command_results,
    }
    if failed_sources:
        blocking_reasons = state.setdefault("blocking_reasons", [])
        reason = "PDF OCR failed for one or more sources; inspect ocr-auto-summary.json"
        if reason not in blocking_reasons:
            blocking_reasons.append(reason)
    write_json(summary_path, summary)
    save_state(output_dir, state)


def prepare_workspace(output_dir: Path, state: dict[str, Any], args: argparse.Namespace) -> None:
    if output_dir.exists() and (output_dir / "generation-manifest.json").exists():
        transition(output_dir, state, STATE_WORKSPACE_PREPARED, reason="workspace already exists")
        return
    command = [sys.executable, str(PREPARE_SCRIPT), str(output_dir), "--template", str(args.template), "--template-id", args.template_id]
    for source in args.source:
        command.extend(["--source", source])
    for source in args.forbidden_source:
        command.extend(["--forbidden-source", source])
    result = run_command(command, output_dir / "workflow-prepare-output.txt", output_dir, "prepare_generation_workspace")
    state.setdefault("commands", {})["prepare_generation_workspace"] = result
    if result["returncode"] != 0:
        transition(output_dir, state, STATE_FAILED, status="failed", reason="workspace preparation failed")
        write_summary(output_dir, state, "failed", False, ["workspace preparation failed"])
        raise SystemExit(1)
    transition(output_dir, state, STATE_WORKSPACE_PREPARED)


def copy_input_artifact(source: Path, destination: Path) -> Path:
    if source.resolve() != destination.resolve():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return destination


def is_placeholder_fact_packet(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        packet = read_json(path)
    except (json.JSONDecodeError, OSError):
        return False
    return packet.get("status") == "pending_fact_extraction" and packet.get("schema_version") != "ctd-32s73-fact-packet-v1"


def ensure_source_index(output_dir: Path, state: dict[str, Any], args: argparse.Namespace) -> None:
    ensure_pdf_ocr_artifacts(output_dir, state)
    json_out = output_dir / "source-index.json"
    markdown_out = output_dir / "source-index.md"
    command = [
        sys.executable,
        str(SOURCE_INDEX_SCRIPT),
        "--workflow-state",
        str(output_dir / "workflow-state.json"),
        "--output-dir",
        str(output_dir),
        "--json-out",
        str(json_out),
        "--markdown-out",
        str(markdown_out),
    ]
    result = run_command(command, output_dir / "workflow-source-index-output.txt", output_dir, "extract_source_index")
    state.setdefault("commands", {})["extract_source_index"] = result
    artifacts = state.setdefault("artifacts", {})
    if json_out.exists():
        artifacts["source_index"] = str(json_out)
    if markdown_out.exists():
        artifacts["source_index_markdown"] = str(markdown_out)
    if result["returncode"] != 0:
        state.setdefault("blocking_reasons", []).append("source index generation failed; inspect workflow-source-index-output.txt")
    save_state(output_dir, state)


def unresolved_pdf_ocr_reasons(output_dir: Path, state: dict[str, Any]) -> list[str]:
    reasons = []
    for source in allowed_pdf_sources(state):
        if not source.exists():
            reasons.append(f"PDF source is missing and cannot be parsed: {source}")
            continue
        if existing_ocr_artifact_for_source(output_dir, source) is None:
            reasons.append(f"PDF OCR is required before fact extraction: {source}")
    return reasons


def run_hook(output_dir: Path, state: dict[str, Any], hook: str, name: str, extra_args: list[str]) -> dict[str, Any]:
    command = [sys.executable, hook, "--workflow-state", str(output_dir / "workflow-state.json"), "--output-dir", str(output_dir)]
    command.extend(extra_args)
    result = run_command(command, output_dir / f"workflow-{name}-output.txt", output_dir, name)
    state.setdefault("commands", {})[name] = result
    save_state(output_dir, state)
    return result


def write_external_fact_packet_request(output_dir: Path, state: dict[str, Any], reason: str, blocking_reasons: list[str] | None = None) -> None:
    artifacts = state.setdefault("artifacts", {})
    request = {
        "schema_version": "ctd-32s73-external-fact-packet-request-v1",
        "required_artifact": "fact_packet",
        "required_output": str(output_dir / "fact-packet.json"),
        "upstream_skill": "reference-fact-extraction",
        "profile_id": "ctd-32s73-stability",
        "required_packet_schema_version": "ctd-32s73-fact-packet-v1",
        "reason": reason,
        "source_index": artifacts.get("source_index"),
        "source_index_markdown": artifacts.get("source_index_markdown"),
        "allowed_sources": state.get("sources", {}).get("allowed", []),
        "forbidden_sources": state.get("sources", {}).get("forbidden", []),
        "notes": [
            "本 skill 不再从来源文件抽取基础事实，也不再接收 project_profile/long_term/accelerated/stress_study fact shards。",
            "请先运行 reference-fact-extraction 的 ctd-32s73-stability profile，生成 CTD-native fact-packet.json。",
            "生成的 fact-packet 必须包含四个 section 的 subagent provenance，并让 trend_charts/body_sections 保持 deferred 或 empty，后续由本 skill 补齐。",
        ],
        "blocking_reasons": blocking_reasons or ["CTD-native fact_packet is required before template filling."],
    }
    write_json(output_dir / "fact-packet-request.json", request)
    artifacts["fact_packet_request"] = str(output_dir / "fact-packet-request.json")
    save_state(output_dir, state)


def require_fact_packet(output_dir: Path, state: dict[str, Any], reason: str, blocking_reasons: list[str] | None = None) -> None:
    write_external_fact_packet_request(output_dir, state, reason, blocking_reasons)
    transition(output_dir, state, STATE_FACT_EXTRACTION_REQUIRED, status="paused", reason=reason)
    write_summary(
        output_dir,
        state,
        "paused_fact_packet_required",
        False,
        blocking_reasons or ["Run reference-fact-extraction with profile_id='ctd-32s73-stability' and submit provided_artifacts.fact_packet."],
    )


def ensure_fact_packet(output_dir: Path, state: dict[str, Any], args: argparse.Namespace) -> Path | None:
    destination = output_dir / "fact-packet.json"
    ensure_source_index(output_dir, state, args)
    pdf_ocr_reasons = unresolved_pdf_ocr_reasons(output_dir, state)
    if pdf_ocr_reasons:
        blocking_reasons = list(state.get("blocking_reasons", []))
        for reason in pdf_ocr_reasons:
            if reason not in blocking_reasons:
                blocking_reasons.append(reason)
        require_fact_packet(output_dir, state, "PDF OCR is required before upstream fact-packet generation", blocking_reasons)
        return None

    if args.fact_packet:
        copy_input_artifact(args.fact_packet, destination)
    if not destination.exists() or is_placeholder_fact_packet(destination):
        require_fact_packet(output_dir, state, "fact packet is required")
        return None
    state.setdefault("artifacts", {})["fact_packet"] = str(destination)
    state["trend_charts_validated"] = False
    state["body_sections_validated"] = False
    transition(output_dir, state, STATE_FACT_PACKET_SUBMITTED)
    return destination


def validate_fact_packet(output_dir: Path, state: dict[str, Any], fact_packet: Path, args: argparse.Namespace) -> dict[str, Any]:
    transition(output_dir, state, STATE_FACT_PACKET_VALIDATING)
    validation_path = output_dir / "fact-packet-validation.json"
    command = [
        sys.executable,
        str(VALIDATE_FACT_SCRIPT),
        str(fact_packet),
        "--json-out",
        str(validation_path),
        "--max-recovery-attempts",
        str(args.max_recovery_attempts),
    ]
    result = run_command(command, output_dir / "fact-packet-validation-output.txt", output_dir, "validate_fact_packet")
    state.setdefault("commands", {})["validate_fact_packet"] = result
    state.setdefault("artifacts", {})["fact_packet_validation"] = str(validation_path)
    report = read_json(validation_path) if validation_path.exists() else {"passed": False, "errors": []}
    state["fact_packet_validation"] = report
    save_state(output_dir, state)
    return report


def handle_fact_validation(output_dir: Path, state: dict[str, Any], report: dict[str, Any], args: argparse.Namespace) -> bool:
    attempts = int(report.get("recovery_attempt_count") or 0)
    has_unresolved = bool(report.get("has_unresolved_facts"))
    messages = [issue.get("message", "fact packet validation error") for issue in report.get("errors", [])]
    blocking_reasons = report.get("blocking_reasons") or messages
    if has_unresolved and attempts >= args.max_recovery_attempts:
        transition(output_dir, state, STATE_MISSING_EVIDENCE_RECOVERY_EXHAUSTED, status="blocked", reason="recovery attempts exhausted")
        write_summary(output_dir, state, "blocked_missing_evidence", False, blocking_reasons or ["missing evidence recovery exhausted"])
        return False
    if has_unresolved:
        if args.recovery_hook:
            result = run_hook(output_dir, state, args.recovery_hook, "missing_evidence_recovery", ["--fact-packet", str(output_dir / "fact-packet.json")])
            if result["returncode"] == 0:
                return True
        transition(output_dir, state, STATE_MISSING_EVIDENCE_RECOVERY_REQUIRED, status="paused", reason="missing evidence recovery required")
        write_summary(output_dir, state, "paused_missing_evidence_recovery_required", False, blocking_reasons)
        return False
    if report.get("render_blocking"):
        transition(output_dir, state, STATE_FACT_PACKET_REVISION_REQUIRED, status="paused", reason="fact packet validation failed")
        write_summary(output_dir, state, "paused_fact_packet_revision_required", False, blocking_reasons)
        return False
    if not report.get("passed"):
        transition(output_dir, state, STATE_FACT_PACKET_REVISION_REQUIRED, status="paused", reason="fact packet validation failed")
        write_summary(output_dir, state, "paused_fact_packet_revision_required", False, blocking_reasons)
        return False
    state["blocking_reasons"] = []
    transition(output_dir, state, STATE_FACT_PACKET_VALID)
    return True


def trend_chart_candidate_summary(packet: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "study_key": candidate.get("study_key"),
            "condition": candidate.get("condition"),
            "item": candidate.get("item"),
            "batches": candidate.get("batches", []),
            "source_paths": candidate.get("source_paths", []),
        }
        for candidate in trend_chart_candidates(packet)
    ]


def write_trend_charts_request(
    output_dir: Path,
    state: dict[str, Any],
    packet: dict[str, Any],
    reasons: list[str] | None = None,
) -> None:
    artifacts = state.setdefault("artifacts", {})
    request = {
        "required_output": str(output_dir / "fact-packet.json"),
        "schema_version": "ctd-32s73-fact-packet-v1",
        "base_fact_packet": str(output_dir / "fact-packet.json"),
        "base_fact_packet_validation": artifacts.get("fact_packet_validation"),
        "trend_charts_validation": artifacts.get("trend_charts_validation"),
        "scope": "Only complete or revise fact-packet.json trend_charts and directly related manual_review_items; preserve already validated body/table facts unless evidence requires a correction.",
        "candidate_numeric_items": trend_chart_candidate_summary(packet),
        "notes": [
            "The body/table fact packet has passed the base validation gate; trend chart data is now required before DOCX rendering.",
            "For long_term and accelerated studies only, create one chart per numeric test item when source data are organized by batch/timepoint and at least one batch has numeric 0-month plus numeric follow-up data.",
            "Do not generate trend_charts for stress_study/influence-factor studies.",
            "If a qualifying numeric item is intentionally not charted, record a source-supported exclusion or data gap in manual_review_items.",
            "Do not draft body_sections in this stage; body_sections are requested only after trend_charts validation passes.",
        ],
        "trend_chart_judgement_policy": [
            "Scope: long_term and accelerated studies only.",
            "Trigger: same test item has batch/timepoint-organized numeric stability results with 0 month and at least one later timepoint for at least one batch.",
            "Granularity: one chart per study condition plus test item; batches are series inside the chart.",
            "Incomplete studies: include charts when 0 month plus follow-up numeric data exist, even if future planned months are still N/A or ---.",
            "Exclusions: influence-factor/stress studies, purely qualitative results, and values that are only limit signs without reportable numeric results.",
        ],
        "blocking_reasons": reasons or [],
    }
    write_json(output_dir / "trend-charts-request.json", request)
    artifacts["trend_charts_request"] = str(output_dir / "trend-charts-request.json")
    save_state(output_dir, state)


def require_trend_charts(output_dir: Path, state: dict[str, Any], packet: dict[str, Any]) -> None:
    reasons = ["trend_charts must be completed after body/table fact packet validation and before DOCX rendering"]
    write_trend_charts_request(output_dir, state, packet, reasons)
    transition(output_dir, state, STATE_TREND_CHARTS_REQUIRED, status="paused", reason="trend chart data required")
    write_summary(output_dir, state, "paused_trend_charts_required", False, reasons)


def ensure_trend_fact_packet(output_dir: Path, state: dict[str, Any], args: argparse.Namespace) -> Path | None:
    destination = output_dir / "fact-packet.json"
    if args.fact_packet:
        copy_input_artifact(args.fact_packet, destination)
    else:
        packet = read_json(destination) if destination.exists() and not is_placeholder_fact_packet(destination) else {}
        if packet:
            write_trend_charts_request(
                output_dir,
                state,
                packet,
                ["trend chart fact packet submission is required before rendering"],
            )
        write_summary(
            output_dir,
            state,
            "paused_trend_charts_required",
            False,
            ["trend chart fact packet submission is required before rendering"],
        )
        return None
    if not destination.exists() or is_placeholder_fact_packet(destination):
        packet = {}
        write_trend_charts_request(output_dir, state, packet, ["fact-packet.json with trend_charts is required"])
        transition(output_dir, state, STATE_TREND_CHARTS_REQUIRED, status="paused", reason="trend chart fact packet is required")
        write_summary(output_dir, state, "paused_trend_charts_required", False, ["fact-packet.json with trend_charts is required"])
        return None
    state.setdefault("artifacts", {})["fact_packet"] = str(destination)
    state["trend_charts_validated"] = False
    state["body_sections_validated"] = False
    transition(output_dir, state, STATE_TREND_CHARTS_SUBMITTED)
    return destination


def validate_trend_charts_fact_packet(output_dir: Path, state: dict[str, Any], fact_packet: Path, args: argparse.Namespace) -> dict[str, Any]:
    transition(output_dir, state, STATE_TREND_CHARTS_VALIDATING)
    validation_path = output_dir / "trend-charts-validation.json"
    command = [
        sys.executable,
        str(VALIDATE_FACT_SCRIPT),
        str(fact_packet),
        "--json-out",
        str(validation_path),
        "--max-recovery-attempts",
        str(args.max_recovery_attempts),
        "--require-trend-charts",
    ]
    result = run_command(command, output_dir / "trend-charts-validation-output.txt", output_dir, "validate_trend_charts")
    state.setdefault("commands", {})["validate_trend_charts"] = result
    state.setdefault("artifacts", {})["trend_charts_validation"] = str(validation_path)
    report = read_json(validation_path) if validation_path.exists() else {"passed": False, "errors": []}
    state["trend_charts_validation"] = report
    save_state(output_dir, state)
    return report


def issue_is_trend_related(issue: dict[str, Any]) -> bool:
    path = str(issue.get("path") or "")
    message = str(issue.get("message") or "").lower()
    return path.startswith("trend_charts") or "trend chart" in message or "trend_charts" in message


def handle_trend_charts_validation(output_dir: Path, state: dict[str, Any], report: dict[str, Any], args: argparse.Namespace) -> bool:
    attempts = int(report.get("recovery_attempt_count") or 0)
    has_unresolved = bool(report.get("has_unresolved_facts"))
    messages = [issue.get("message", "trend chart validation error") for issue in report.get("errors", [])]
    blocking_reasons = report.get("blocking_reasons") or messages
    if has_unresolved and attempts >= args.max_recovery_attempts:
        transition(output_dir, state, STATE_MISSING_EVIDENCE_RECOVERY_EXHAUSTED, status="blocked", reason="recovery attempts exhausted")
        write_summary(output_dir, state, "blocked_missing_evidence", False, blocking_reasons or ["missing evidence recovery exhausted"])
        return False
    if has_unresolved:
        if args.recovery_hook:
            result = run_hook(output_dir, state, args.recovery_hook, "missing_evidence_recovery", ["--fact-packet", str(output_dir / "fact-packet.json")])
            if result["returncode"] == 0:
                return True
        transition(output_dir, state, STATE_MISSING_EVIDENCE_RECOVERY_REQUIRED, status="paused", reason="missing evidence recovery required")
        write_summary(output_dir, state, "paused_missing_evidence_recovery_required", False, blocking_reasons)
        return False

    errors = [issue for issue in report.get("errors", []) if isinstance(issue, dict)]
    non_trend_errors = [issue for issue in errors if not issue_is_trend_related(issue)]
    if non_trend_errors:
        packet = read_json(output_dir / "fact-packet.json")
        write_trend_charts_request(output_dir, state, packet, blocking_reasons)
        transition(output_dir, state, STATE_FACT_PACKET_REVISION_REQUIRED, status="paused", reason="base fact packet validation failed during trend gate")
        write_summary(output_dir, state, "paused_fact_packet_revision_required", False, blocking_reasons)
        return False

    if report.get("render_blocking") or not report.get("passed"):
        packet = read_json(output_dir / "fact-packet.json")
        write_trend_charts_request(output_dir, state, packet, blocking_reasons)
        transition(output_dir, state, STATE_TREND_CHARTS_REVISION_REQUIRED, status="paused", reason="trend chart validation failed")
        write_summary(output_dir, state, "paused_trend_charts_revision_required", False, blocking_reasons)
        return False

    state["trend_charts_validated"] = True
    state["blocking_reasons"] = []
    transition(output_dir, state, STATE_TREND_CHARTS_VALID)
    return True


def body_section_request_summary(packet: dict[str, Any]) -> dict[str, Any]:
    sections = packet.get("body_sections")
    provided = sorted(str(key) for key in sections) if isinstance(sections, dict) else []
    return {
        "required_keys": required_body_section_keys(packet),
        "provided_keys": provided,
        "writing_patterns": str((SKILL_ROOT / "references" / "writing-patterns.md").resolve()),
        "writing_fact_cases": str((SKILL_ROOT / "references" / "writing-fact-cases.md").resolve()),
    }


def write_body_sections_request(
    output_dir: Path,
    state: dict[str, Any],
    packet: dict[str, Any],
    reasons: list[str] | None = None,
) -> None:
    artifacts = state.setdefault("artifacts", {})
    request = {
        "required_output": str(output_dir / "fact-packet.json"),
        "schema_version": "ctd-32s73-fact-packet-v1",
        "base_fact_packet": str(output_dir / "fact-packet.json"),
        "base_fact_packet_validation": artifacts.get("fact_packet_validation"),
        "trend_charts_validation": artifacts.get("trend_charts_validation"),
        "body_sections_validation": artifacts.get("body_sections_validation"),
        "scope": "Only complete or revise fact-packet.json body_sections and directly related manual_review_items; preserve already validated body/table facts and trend_charts unless evidence requires a correction.",
        "body_section_requirements": body_section_request_summary(packet),
        "writing_pattern_sections_to_apply": [
            "语义占位符",
            "真实结果稿经验沉淀",
            "正文事实案例",
            "长期稳定性",
            "加速稳定性",
            "影响因素研究引言",
            "单项影响因素结果模式",
            "正文中的表格和图",
            "趋势图生成",
            "禁止的捷径",
        ],
        "required_body_section_shape": {
            "placeholder": "{{SECTION_PLACEHOLDER}}",
            "text": "Complete paragraph text supported by fact-packet facts and allowed sources.",
            "source_refs": ["Allowed source evidence for project facts."],
            "writing_pattern_refs": ["真实结果稿经验沉淀.SECTION_KEY.scenario", "事实案例.CASE-..."],
        },
        "notes": [
            "The body/table fact packet and trend_charts have passed validation; semantic paragraph drafting is now required before DOCX rendering.",
            "Read writing-patterns.md, especially 真实结果稿经验沉淀, before drafting body_sections; read writing-fact-cases.md when the fact pattern needs an example.",
            "Use writing patterns only for paragraph organization, transitions, conclusion boundaries, and anti-pattern checks; project facts must come from fact-packet fields and allowed sources.",
            "Use manifest-driven trend tokens such as {{LONG_TERM_TREND_FIGURE_RANGE}} and {{ACCELERATED_TREND_FIGURE_RANGE}} instead of hard-coded unstable figure ranges where possible.",
            "Do not leave template alternatives such as 略微上升/略微下降/波动, XXX, IPXXX, or unsupported stress-study conclusions.",
        ],
        "blocking_reasons": reasons or [],
    }
    write_json(output_dir / "body-sections-request.json", request)
    artifacts["body_sections_request"] = str(output_dir / "body-sections-request.json")
    save_state(output_dir, state)


def require_body_sections(output_dir: Path, state: dict[str, Any], packet: dict[str, Any]) -> None:
    reasons = ["body_sections must be completed after trend_charts validation and before DOCX rendering"]
    write_body_sections_request(output_dir, state, packet, reasons)
    transition(output_dir, state, STATE_BODY_SECTIONS_REQUIRED, status="paused", reason="body sections required")
    write_summary(output_dir, state, "paused_body_sections_required", False, reasons)


def ensure_body_sections_fact_packet(output_dir: Path, state: dict[str, Any], args: argparse.Namespace) -> Path | None:
    destination = output_dir / "fact-packet.json"
    if args.fact_packet:
        copy_input_artifact(args.fact_packet, destination)
    else:
        packet = read_json(destination) if destination.exists() and not is_placeholder_fact_packet(destination) else {}
        if packet:
            write_body_sections_request(
                output_dir,
                state,
                packet,
                ["body_sections fact packet submission is required before rendering"],
            )
        write_summary(
            output_dir,
            state,
            "paused_body_sections_required",
            False,
            ["body_sections fact packet submission is required before rendering"],
        )
        return None
    if not destination.exists() or is_placeholder_fact_packet(destination):
        packet = {}
        write_body_sections_request(output_dir, state, packet, ["fact-packet.json with body_sections is required"])
        transition(output_dir, state, STATE_BODY_SECTIONS_REQUIRED, status="paused", reason="body sections fact packet is required")
        write_summary(output_dir, state, "paused_body_sections_required", False, ["fact-packet.json with body_sections is required"])
        return None
    state.setdefault("artifacts", {})["fact_packet"] = str(destination)
    state["trend_charts_validated"] = False
    state["body_sections_validated"] = False
    transition(output_dir, state, STATE_BODY_SECTIONS_SUBMITTED)
    return destination


def validate_body_sections_fact_packet(output_dir: Path, state: dict[str, Any], fact_packet: Path, args: argparse.Namespace) -> dict[str, Any]:
    transition(output_dir, state, STATE_BODY_SECTIONS_VALIDATING)
    validation_path = output_dir / "body-sections-validation.json"
    command = [
        sys.executable,
        str(VALIDATE_FACT_SCRIPT),
        str(fact_packet),
        "--json-out",
        str(validation_path),
        "--max-recovery-attempts",
        str(args.max_recovery_attempts),
        "--require-trend-charts",
        "--require-body-sections",
    ]
    result = run_command(command, output_dir / "body-sections-validation-output.txt", output_dir, "validate_body_sections")
    state.setdefault("commands", {})["validate_body_sections"] = result
    state.setdefault("artifacts", {})["body_sections_validation"] = str(validation_path)
    report = read_json(validation_path) if validation_path.exists() else {"passed": False, "errors": []}
    state["body_sections_validation"] = report
    save_state(output_dir, state)
    return report


def issue_is_body_section_related(issue: dict[str, Any]) -> bool:
    path = str(issue.get("path") or "")
    message = str(issue.get("message") or "").lower()
    return path.startswith("body_sections") or "body section" in message or "writing_pattern" in message


def handle_body_sections_validation(output_dir: Path, state: dict[str, Any], report: dict[str, Any], args: argparse.Namespace) -> bool:
    attempts = int(report.get("recovery_attempt_count") or 0)
    has_unresolved = bool(report.get("has_unresolved_facts"))
    messages = [issue.get("message", "body_sections validation error") for issue in report.get("errors", [])]
    blocking_reasons = report.get("blocking_reasons") or messages
    if has_unresolved and attempts >= args.max_recovery_attempts:
        transition(output_dir, state, STATE_MISSING_EVIDENCE_RECOVERY_EXHAUSTED, status="blocked", reason="recovery attempts exhausted")
        write_summary(output_dir, state, "blocked_missing_evidence", False, blocking_reasons or ["missing evidence recovery exhausted"])
        return False
    if has_unresolved:
        if args.recovery_hook:
            result = run_hook(output_dir, state, args.recovery_hook, "missing_evidence_recovery", ["--fact-packet", str(output_dir / "fact-packet.json")])
            if result["returncode"] == 0:
                return True
        transition(output_dir, state, STATE_MISSING_EVIDENCE_RECOVERY_REQUIRED, status="paused", reason="missing evidence recovery required")
        write_summary(output_dir, state, "paused_missing_evidence_recovery_required", False, blocking_reasons)
        return False

    errors = [issue for issue in report.get("errors", []) if isinstance(issue, dict)]
    trend_errors = [issue for issue in errors if issue_is_trend_related(issue)]
    non_body_non_trend_errors = [
        issue for issue in errors if not issue_is_body_section_related(issue) and not issue_is_trend_related(issue)
    ]
    if non_body_non_trend_errors:
        packet = read_json(output_dir / "fact-packet.json")
        write_body_sections_request(output_dir, state, packet, blocking_reasons)
        state["trend_charts_validated"] = False
        state["body_sections_validated"] = False
        transition(output_dir, state, STATE_FACT_PACKET_REVISION_REQUIRED, status="paused", reason="base fact packet validation failed during body sections gate")
        write_summary(output_dir, state, "paused_fact_packet_revision_required", False, blocking_reasons)
        return False

    if trend_errors:
        packet = read_json(output_dir / "fact-packet.json")
        write_trend_charts_request(output_dir, state, packet, blocking_reasons)
        state["trend_charts_validated"] = False
        state["body_sections_validated"] = False
        transition(output_dir, state, STATE_TREND_CHARTS_REVISION_REQUIRED, status="paused", reason="trend chart validation failed during body sections gate")
        write_summary(output_dir, state, "paused_trend_charts_revision_required", False, blocking_reasons)
        return False

    if report.get("render_blocking") or not report.get("passed"):
        packet = read_json(output_dir / "fact-packet.json")
        write_body_sections_request(output_dir, state, packet, blocking_reasons)
        state["trend_charts_validated"] = True
        state["body_sections_validated"] = False
        transition(output_dir, state, STATE_BODY_SECTIONS_REVISION_REQUIRED, status="paused", reason="body sections validation failed")
        write_summary(output_dir, state, "paused_body_sections_revision_required", False, blocking_reasons)
        return False

    state["trend_charts_validated"] = True
    state["body_sections_validated"] = True
    state["blocking_reasons"] = []
    transition(output_dir, state, STATE_BODY_SECTIONS_VALID)
    return True


def render_scope(packet: dict[str, Any]) -> dict[str, Any]:
    plan = packet.get("docx_render_plan")
    if not isinstance(plan, dict):
        return {}
    scope = plan.get("render_scope")
    return scope if isinstance(scope, dict) else {}


def batch_structure_action(packet: dict[str, Any]) -> str:
    plan = packet.get("docx_render_plan")
    if not isinstance(plan, dict):
        return ""
    scope = render_scope(packet)
    if scope.get("batch_structure_action"):
        return str(scope.get("batch_structure_action"))
    return str(plan.get("batch_count_action") or "")


def has_body_sections(packet: dict[str, Any]) -> bool:
    sections = packet.get("body_sections")
    if isinstance(sections, dict) and sections:
        return True
    plan = packet.get("docx_render_plan")
    if isinstance(plan, dict):
        sections = plan.get("body_sections")
    return isinstance(sections, dict) and bool(sections)


def body_skeleton_required(packet: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    plan = packet.get("docx_render_plan")
    if not isinstance(plan, dict):
        return False, []
    prose_action = str(plan.get("body_prose_action") or plan.get("prose_action") or ("body_sections" if has_body_sections(packet) else "project_specific_rewrite"))
    if prose_action in {"body_sections", "semantic_placeholders"}:
        if not has_body_sections(packet):
            reasons.append("body_sections are required for semantic placeholder replacement")
    elif prose_action not in {"builtin_placeholder_only", "none", "not_required"}:
        reasons.append(f"body_prose_action={prose_action}")
    return bool(reasons), reasons


def write_body_skeleton_request(output_dir: Path, packet: dict[str, Any], reasons: list[str]) -> None:
    request = {
        "required_output_docx": str(output_dir / "body-and-skeleton-filled.docx"),
        "required_report": str(output_dir / "body-skeleton-report.json"),
        "input_fact_packet": str(output_dir / "fact-packet.json"),
        "writing_patterns": str((SKILL_ROOT / "references" / "writing-patterns.md").resolve()),
        "render_scope": render_scope(packet),
        "required_actions_from_docx_render_plan": reasons,
        "body_section_placeholders": BODY_SECTION_PLACEHOLDERS,
        "paragraph_replacement_policy": [
            "Prefer semantic paragraph slots such as {{LONG_TERM_INTRO}} and fill them from fact-packet body_sections.",
            "Rewrite affected body paragraphs as complete paragraphs; do not rely on token-by-token XXX placeholder substitution for narrative text.",
            "Use writing_patterns plus fact-packet facts to write body_sections for long-term, accelerated, stress-study, table/figure-reference, and final-summary paragraphs.",
            "Apply the 真实结果稿经验沉淀 section as paragraph organization guidance, and record matching pattern labels in body_sections.*.writing_pattern_refs when revising the fact packet.",
            "Adjust batch roles/counts, table ranges, figure references, completed timepoints, continuation wording, trend wording, and omitted-study wording to the actual project.",
            "If the fact packet does not support a sentence, remove or rewrite the sentence; if the fact is needed, revise fact-packet.json first.",
        ],
        "content_provenance_policy": [
            "filled.docx project-specific facts must come from fact-packet.json.",
            "Use template, writing patterns, and scripts only for structure, styling, fixed labels, captions, table slots, and expression patterns.",
            "Do not introduce product names, batches, conditions, timepoints, results, trends, figure/table references, or conclusions that are absent from fact-packet.json.",
            "If drafting needs a missing project fact, revise fact-packet.json and resubmit it through the step workflow before editing the DOCX.",
        ],
        "prose_requirements": [
            "Read writing_patterns before drafting or replacing long-term, accelerated, stress-study, and final conclusion paragraphs.",
            "Use the writing patterns as a checklist, not generic boilerplate: preserve source-supported product, batch, condition, table, figure, trend, and continuation facts.",
            "Do not leave template alternatives such as 略微上升/略微下降/波动; choose source-supported wording.",
            "Do not describe stress studies as meeting acceptance criteria unless the source explicitly defines applicable criteria.",
            "Do not keep figure references unless the corresponding generated figure and manifest entry exist.",
        ],
        "table_block_contract": [
            "Do not clone, delete, or number result tables in the body skeleton DOCX.",
            "Leave structural table block placeholders such as {{LONG_TERM_TABLE_BLOCK}} and {{STRESS_LIGHT_TABLE_BLOCK}} in place.",
            "render_stability_tables.py replaces table block placeholders from fact-packet table_render_inputs and records generated_table_slots in table-render-manifest.json.",
        ],
        "boundary": "Project-specific DOCX body and skeleton editing is not automated by generic scripts, and must not add project facts outside fact-packet.json.",
    }
    write_json(output_dir / "body-skeleton-request.json", request)


def ensure_body_skeleton(output_dir: Path, state: dict[str, Any], packet: dict[str, Any], args: argparse.Namespace) -> Path | None:
    destination = output_dir / "body-and-skeleton-filled.docx"
    required, reasons = body_skeleton_required(packet)
    if required and not args.working_docx and not args.body_skeleton_hook:
        write_body_skeleton_request(output_dir, packet, reasons)
        state.setdefault("artifacts", {})["body_skeleton_request"] = str(output_dir / "body-skeleton-request.json")
        if args.allow_intermediate:
            transition(output_dir, state, STATE_BODY_SKELETON_SKIPPED_INTERMEDIATE_ONLY, reason="intermediate mode uses bundled template as working DOCX")
            return None
        transition(output_dir, state, STATE_BODY_SKELETON_REQUIRED, status="paused", reason="body/skeleton DOCX editing required")
        write_summary(output_dir, state, "paused_body_skeleton_required", False, reasons)
        return None
    if args.body_skeleton_hook:
        result = run_hook(
            output_dir,
            state,
            args.body_skeleton_hook,
            "body_skeleton_hook",
            [
                "--fact-packet",
                str(output_dir / "fact-packet.json"),
                "--template",
                str(args.template),
                "--output-docx",
                str(destination),
                "--manifest-out",
                str(output_dir / "body-skeleton-report.json"),
            ],
        )
        if result["returncode"] != 0:
            transition(output_dir, state, STATE_FAILED, status="failed", reason="body skeleton hook failed")
            write_summary(output_dir, state, "failed_body_skeleton_hook", False, ["body skeleton hook failed"])
            raise SystemExit(1)
    elif args.working_docx:
        copy_input_artifact(args.working_docx, destination)
    else:
        result = run_hook(
            output_dir,
            state,
            str(BUILTIN_BODY_SKELETON_SCRIPT),
            "builtin_body_skeleton",
            [
                "--fact-packet",
                str(output_dir / "fact-packet.json"),
                "--template",
                str(args.template),
                "--output-docx",
                str(destination),
                "--manifest-out",
                str(output_dir / "body-skeleton-report.json"),
            ],
        )
        if result["returncode"] != 0:
            write_body_skeleton_request(output_dir, packet, ["builtin body/skeleton generation failed"])
            state.setdefault("artifacts", {})["body_skeleton_request"] = str(output_dir / "body-skeleton-request.json")
            transition(output_dir, state, STATE_BODY_SKELETON_REQUIRED, status="paused", reason="body/skeleton DOCX editing required")
            write_summary(output_dir, state, "paused_body_skeleton_required", False, ["builtin body/skeleton generation failed"])
            return None
    if destination.exists():
        artifacts = state.setdefault("artifacts", {})
        artifacts["body_skeleton_docx"] = str(destination)
        report = output_dir / "body-skeleton-report.json"
        if report.exists():
            artifacts["body_skeleton_report"] = str(report)
        transition(output_dir, state, STATE_BODY_SKELETON_COMPLETED)
        run_command(
            [sys.executable, str(INSPECT_SCRIPT), str(destination), "--caption-paragraphs", "--all-tables", "--max-rows", "2"],
            output_dir / "body-skeleton-structure-snapshot.txt",
            output_dir,
            "inspect_body_skeleton",
        )
        return destination
    transition(output_dir, state, STATE_BODY_SKELETON_SKIPPED_INTERMEDIATE_ONLY, reason="intermediate mode uses bundled template as working DOCX")
    return None


def run_pipeline(output_dir: Path, state: dict[str, Any], working_docx: Path | None, args: argparse.Namespace) -> bool:
    if not bool(state.get("trend_charts_validated")):
        packet = read_json(output_dir / "fact-packet.json") if (output_dir / "fact-packet.json").exists() else {}
        require_trend_charts(output_dir, state, packet)
        return False
    if not bool(state.get("body_sections_validated")):
        packet = read_json(output_dir / "fact-packet.json") if (output_dir / "fact-packet.json").exists() else {}
        require_body_sections(output_dir, state, packet)
        return False
    transition(output_dir, state, STATE_TABLE_RENDER_READY)
    transition(output_dir, state, STATE_PIPELINE_RUNNING)
    command = [
        sys.executable,
        str(PIPELINE_SCRIPT),
        "--fact-packet",
        str(output_dir / "fact-packet.json"),
        "--output-dir",
        str(output_dir),
        "--template",
        str(args.template),
        "--template-id",
        args.template_id,
        "--max-recovery-attempts",
        str(args.max_recovery_attempts),
    ]
    if working_docx:
        command.extend(["--working-docx", str(working_docx)])
    if args.disallow_hour_time:
        command.append("--disallow-hour-time")
    if args.min_tables is not None:
        command.extend(["--min-tables", str(args.min_tables)])
    if args.max_tables is not None:
        command.extend(["--max-tables", str(args.max_tables)])
    for batch in args.expected_batch:
        command.extend(["--expected-batch", batch])
    for warning in args.expected_warning:
        command.extend(["--expected-warning", warning])
    result = run_command(command, output_dir / "workflow-pipeline-output.txt", output_dir, "run_generation_pipeline")
    state.setdefault("commands", {})["run_generation_pipeline"] = result
    if result["returncode"] != 0:
        transition(output_dir, state, STATE_FAILED, status="failed", reason="generation pipeline failed")
        write_summary(output_dir, state, "failed_pipeline", False, ["generation pipeline failed"])
        return False
    artifacts = state.setdefault("artifacts", {})
    for key, filename in {
        "body_section_application": "body-section-application.json",
        "body_sections_applied_docx": "body-sections-applied.docx",
        "figure_render_manifest": "figure-render-manifest.json",
        "filled_docx": "filled.docx",
        "stress_section_render": "stress-section-render.json",
        "stress_sections_rendered_docx": "stress-sections-rendered.docx",
        "table_render_manifest": "table-render-manifest.json",
        "trend_chart_manifest": "trend-chart-manifest.json",
        "validation_json": "validation.json",
        "validation_report": "validation-report.md",
        "generation_manifest": "generation-manifest.json",
    }.items():
        path = output_dir / filename
        if path.exists():
            artifacts[key] = str(path)
    validation = read_json(output_dir / "validation.json") if (output_dir / "validation.json").exists() else {}
    state["validation_passed"] = bool(validation.get("passed"))
    transition(output_dir, state, STATE_PIPELINE_COMPLETED)
    transition(output_dir, state, STATE_FINAL_VALIDATING)
    return True


def finalize(output_dir: Path, state: dict[str, Any], working_docx: Path | None, args: argparse.Namespace) -> None:
    if not bool(state.get("trend_charts_validated")):
        packet = read_json(output_dir / "fact-packet.json") if (output_dir / "fact-packet.json").exists() else {}
        require_trend_charts(output_dir, state, packet)
        print(json.dumps(read_json(output_dir / "workflow-summary.json"), ensure_ascii=False, indent=2))
        return
    if not bool(state.get("body_sections_validated")):
        packet = read_json(output_dir / "fact-packet.json") if (output_dir / "fact-packet.json").exists() else {}
        require_body_sections(output_dir, state, packet)
        print(json.dumps(read_json(output_dir / "workflow-summary.json"), ensure_ascii=False, indent=2))
        return
    validation_passed = bool(state.get("validation_passed"))
    if validation_passed and working_docx:
        transition(output_dir, state, STATE_COMPLETED_FINAL)
        write_summary(output_dir, state, "completed_final", True, [])
        relocation_map = organize_final_artifacts(output_dir, state, args.artifact_profile)
        write_summary(output_dir, state, "completed_final", True, [])
        write_artifact_index(output_dir, state, args.artifact_profile, relocation_map)
    else:
        transition(output_dir, state, STATE_COMPLETED_INTERMEDIATE)
        reasons = []
        if not validation_passed:
            reasons.append("final validation did not pass")
        if not working_docx:
            reasons.append("body/skeleton DOCX was not supplied; output is an intermediate smoke-test artifact")
        write_summary(output_dir, state, "completed_intermediate", False, reasons)
    print(json.dumps(read_json(output_dir / "workflow-summary.json"), ensure_ascii=False, indent=2))


def path_from_state_artifact(state: dict[str, Any], key: str) -> Path | None:
    artifacts = state.get("artifacts")
    if not isinstance(artifacts, dict):
        return None
    value = artifacts.get(key)
    if not value:
        return None
    path = Path(str(value))
    return path if path.exists() else None


def print_existing_summary(output_dir: Path, state: dict[str, Any]) -> None:
    summary_path = output_dir / "workflow-summary.json"
    if summary_path.exists():
        print(json.dumps(read_json(summary_path), ensure_ascii=False, indent=2))
        return
    print(json.dumps({"status": "no_advance", "state": state.get("state"), "state_file": str(output_dir / "workflow-state.json")}, ensure_ascii=False, indent=2))


def complete_fact_packet_stage(output_dir: Path, state: dict[str, Any], fact_packet: Path, args: argparse.Namespace) -> bool:
    while True:
        report = validate_fact_packet(output_dir, state, fact_packet, args)
        should_continue = handle_fact_validation(output_dir, state, report, args)
        if not should_continue:
            return False
        if state.get("state") == STATE_FACT_PACKET_VALID:
            return True
        fact_packet = output_dir / "fact-packet.json"


def complete_trend_charts_stage(output_dir: Path, state: dict[str, Any], fact_packet: Path, args: argparse.Namespace) -> bool:
    while True:
        report = validate_trend_charts_fact_packet(output_dir, state, fact_packet, args)
        should_continue = handle_trend_charts_validation(output_dir, state, report, args)
        if not should_continue:
            return False
        if state.get("state") == STATE_TREND_CHARTS_VALID:
            return True
        fact_packet = output_dir / "fact-packet.json"


def complete_body_sections_stage(output_dir: Path, state: dict[str, Any], fact_packet: Path, args: argparse.Namespace) -> bool:
    while True:
        report = validate_body_sections_fact_packet(output_dir, state, fact_packet, args)
        should_continue = handle_body_sections_validation(output_dir, state, report, args)
        if not should_continue:
            return False
        if state.get("state") == STATE_BODY_SECTIONS_VALID:
            return True
        fact_packet = output_dir / "fact-packet.json"


def complete_after_fact_packet_valid(output_dir: Path, state: dict[str, Any], args: argparse.Namespace) -> None:
    packet = read_json(output_dir / "fact-packet.json")
    require_trend_charts(output_dir, state, packet)


def complete_after_trend_charts_valid(output_dir: Path, state: dict[str, Any], args: argparse.Namespace) -> None:
    packet = read_json(output_dir / "fact-packet.json")
    require_body_sections(output_dir, state, packet)


def complete_after_body_sections_valid(output_dir: Path, state: dict[str, Any], args: argparse.Namespace) -> None:
    packet = read_json(output_dir / "fact-packet.json")
    working_docx = ensure_body_skeleton(output_dir, state, packet, args)
    if state.get("state") == STATE_BODY_SKELETON_REQUIRED or stop_at(output_dir, state, state.get("state", ""), args):
        return
    if not run_pipeline(output_dir, state, working_docx, args):
        return
    finalize(output_dir, state, working_docx, args)


def complete_from_existing_body_skeleton(output_dir: Path, state: dict[str, Any], args: argparse.Namespace) -> None:
    working_docx = None
    if state.get("state") != STATE_BODY_SKELETON_SKIPPED_INTERMEDIATE_ONLY:
        working_docx = path_from_state_artifact(state, "body_skeleton_docx")
    if not run_pipeline(output_dir, state, working_docx, args):
        return
    finalize(output_dir, state, working_docx, args)


def complete_from_pipeline_finished(output_dir: Path, state: dict[str, Any], args: argparse.Namespace) -> None:
    working_docx = path_from_state_artifact(state, "body_skeleton_docx")
    finalize(output_dir, state, working_docx, args)


def run_from_current_state(output_dir: Path, state: dict[str, Any], args: argparse.Namespace) -> None:
    state_name = state.get("state")

    if state_name == STATE_NEW:
        prepare_workspace(output_dir, state, args)
        if stop_at(output_dir, state, STATE_WORKSPACE_PREPARED, args):
            return
        state_name = state.get("state")

    if state_name == STATE_WORKSPACE_PREPARED:
        register_sources(output_dir, state, args)
        transition(output_dir, state, STATE_SOURCES_REGISTERED)
        if stop_at(output_dir, state, STATE_SOURCES_REGISTERED, args):
            return
        state_name = state.get("state")

    if state_name == STATE_SOURCES_REGISTERED:
        fact_packet = ensure_fact_packet(output_dir, state, args)
        if fact_packet is None or stop_at(output_dir, state, state.get("state", ""), args):
            return
        if complete_fact_packet_stage(output_dir, state, fact_packet, args):
            if stop_at(output_dir, state, state.get("state", ""), args):
                return
            complete_after_fact_packet_valid(output_dir, state, args)
        return

    if state_name in FACT_PACKET_INPUT_STATES:
        fact_packet = ensure_fact_packet(output_dir, state, args)
        if fact_packet is None or stop_at(output_dir, state, state.get("state", ""), args):
            return
        if complete_fact_packet_stage(output_dir, state, fact_packet, args):
            if stop_at(output_dir, state, state.get("state", ""), args):
                return
            complete_after_fact_packet_valid(output_dir, state, args)
        return

    if state_name == STATE_FAILED and args.fact_packet:
        fact_packet = ensure_fact_packet(output_dir, state, args)
        if fact_packet is None or stop_at(output_dir, state, state.get("state", ""), args):
            return
        if complete_fact_packet_stage(output_dir, state, fact_packet, args):
            if stop_at(output_dir, state, state.get("state", ""), args):
                return
            complete_after_fact_packet_valid(output_dir, state, args)
        return

    if state_name in FACT_PACKET_READY_STATES:
        fact_packet = output_dir / "fact-packet.json"
        if not fact_packet.exists() or is_placeholder_fact_packet(fact_packet):
            require_fact_packet(
                output_dir,
                state,
                "fact packet is required",
                ["Run reference-fact-extraction with profile_id='ctd-32s73-stability' and submit CTD-native fact_packet."],
            )
            return
        if state_name != STATE_FACT_PACKET_VALID:
            if not complete_fact_packet_stage(output_dir, state, fact_packet, args):
                return
            if stop_at(output_dir, state, state.get("state", ""), args):
                return
        complete_after_fact_packet_valid(output_dir, state, args)
        return

    if state_name in TREND_CHARTS_INPUT_STATES:
        fact_packet = ensure_trend_fact_packet(output_dir, state, args)
        if fact_packet is None or stop_at(output_dir, state, state.get("state", ""), args):
            return
        if complete_trend_charts_stage(output_dir, state, fact_packet, args):
            if stop_at(output_dir, state, state.get("state", ""), args):
                return
            complete_after_trend_charts_valid(output_dir, state, args)
        return

    if state_name in TREND_CHARTS_READY_STATES:
        if stop_at(output_dir, state, state.get("state", ""), args):
            return
        complete_after_trend_charts_valid(output_dir, state, args)
        return

    if state_name in BODY_SECTIONS_INPUT_STATES:
        fact_packet = ensure_body_sections_fact_packet(output_dir, state, args)
        if fact_packet is None or stop_at(output_dir, state, state.get("state", ""), args):
            return
        if complete_body_sections_stage(output_dir, state, fact_packet, args):
            if stop_at(output_dir, state, state.get("state", ""), args):
                return
            complete_after_body_sections_valid(output_dir, state, args)
        return

    if state_name in BODY_SECTIONS_READY_STATES:
        if stop_at(output_dir, state, state.get("state", ""), args):
            return
        complete_after_body_sections_valid(output_dir, state, args)
        return

    if state_name == STATE_BODY_SKELETON_REQUIRED:
        packet = read_json(output_dir / "fact-packet.json")
        working_docx = ensure_body_skeleton(output_dir, state, packet, args)
        if state.get("state") == STATE_BODY_SKELETON_REQUIRED or stop_at(output_dir, state, state.get("state", ""), args):
            return
        if not run_pipeline(output_dir, state, working_docx, args):
            return
        finalize(output_dir, state, working_docx, args)
        return

    if state_name == STATE_COMPLETED_INTERMEDIATE and args.working_docx:
        packet = read_json(output_dir / "fact-packet.json")
        working_docx = ensure_body_skeleton(output_dir, state, packet, args)
        if state.get("state") == STATE_BODY_SKELETON_REQUIRED or stop_at(output_dir, state, state.get("state", ""), args):
            return
        if not run_pipeline(output_dir, state, working_docx, args):
            return
        finalize(output_dir, state, working_docx, args)
        return

    if state_name in BODY_SKELETON_READY_STATES:
        complete_from_existing_body_skeleton(output_dir, state, args)
        return

    if state_name in FINALIZE_READY_STATES:
        complete_from_pipeline_finished(output_dir, state, args)
        return

    if state_is_paused_or_terminal(state_name):
        print_existing_summary(output_dir, state)
        return

    transition(output_dir, state, STATE_PAUSED, status="paused", reason=f"cannot resume from state {state_name!r}")
    write_summary(output_dir, state, "paused_unknown_state", False, [f"cannot resume from state {state_name!r}"])


def apply_config(args: argparse.Namespace) -> argparse.Namespace:
    if not args.config:
        if args.output_dir is None:
            raise SystemExit("--output-dir is required unless --config is provided")
        return args

    config = read_json(args.config)
    if args.output_dir is None and config.get("output_dir"):
        args.output_dir = Path(config["output_dir"])
    if args.template == DEFAULT_TEMPLATE and config.get("template"):
        args.template = Path(config["template"])
    if args.template_id == DEFAULT_TEMPLATE_ID and config.get("template_id"):
        args.template_id = str(config["template_id"])
    if not args.source:
        args.source = list(config.get("sources") or [])
    if not args.forbidden_source:
        args.forbidden_source = list(config.get("forbidden_sources") or [])
    if args.fact_packet is None and config.get("fact_packet"):
        args.fact_packet = Path(config["fact_packet"])
    if args.working_docx is None and config.get("working_docx"):
        args.working_docx = Path(config["working_docx"])

    hooks = config.get("hooks") if isinstance(config.get("hooks"), dict) else {}
    if args.recovery_hook is None and hooks.get("recovery_hook"):
        args.recovery_hook = str(hooks["recovery_hook"])
    if args.body_skeleton_hook is None and hooks.get("body_skeleton_hook"):
        args.body_skeleton_hook = str(hooks["body_skeleton_hook"])

    options = config.get("options") if isinstance(config.get("options"), dict) else {}
    args.resume = bool(args.resume or options.get("resume"))
    args.allow_intermediate = bool(args.allow_intermediate or options.get("allow_intermediate"))
    args.auto_ocr_pdf = bool(args.auto_ocr_pdf or options.get("auto_ocr_pdf"))
    args.disallow_hour_time = bool(args.disallow_hour_time or options.get("disallow_hour_time"))
    if options.get("artifact_profile") is not None:
        args.artifact_profile = str(options["artifact_profile"])
    if args.max_recovery_attempts == 2 and options.get("max_recovery_attempts") is not None:
        args.max_recovery_attempts = int(options["max_recovery_attempts"])
    if args.min_tables is None and options.get("min_tables") is not None:
        args.min_tables = int(options["min_tables"])
    if args.max_tables is None and options.get("max_tables") is not None:
        args.max_tables = int(options["max_tables"])
    if not args.expected_batch:
        args.expected_batch = list(options.get("expected_batches") or [])
    if not args.expected_warning:
        args.expected_warning = list(options.get("expected_warnings") or [])

    if args.output_dir is None:
        raise SystemExit("Config file must include output_dir, or pass --output-dir explicitly")
    return args


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a state-machine workflow for CTD 3.2.S.7.3 stability DOCX generation.")
    parser.add_argument("--config", type=Path, help="Optional workflow config JSON; CLI arguments override config values.")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--forbidden-source", action="append", default=[])
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--template-id", default=DEFAULT_TEMPLATE_ID)
    parser.add_argument("--fact-packet", type=Path)
    parser.add_argument("--working-docx", type=Path)
    parser.add_argument("--recovery-hook")
    parser.add_argument("--body-skeleton-hook")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--until")
    parser.add_argument("--allow-intermediate", action="store_true")
    parser.add_argument("--auto-ocr-pdf", action="store_true", help="Compatibility flag; allowed PDF sources are OCR-processed by default before source-index.")
    parser.add_argument("--max-recovery-attempts", type=int, default=2)
    parser.add_argument("--expected-batch", action="append", default=[])
    parser.add_argument("--expected-warning", action="append", default=[])
    parser.add_argument("--min-tables", type=int)
    parser.add_argument("--max-tables", type=int)
    parser.add_argument("--disallow-hour-time", action="store_true")
    parser.add_argument("--artifact-profile", choices=sorted(ARTIFACT_PROFILES), default="delivery")
    return apply_config(parser.parse_args())


def main() -> None:
    require_internal_context("run_state_machine_workflow.py")
    args = parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    args.template = args.template.resolve()
    state = load_state(output_dir, args)
    append_event(output_dir, {"event": "workflow_started", "resume": args.resume})

    if state.get("state") not in {STATE_NEW, STATE_WORKSPACE_PREPARED, STATE_SOURCES_REGISTERED} and args.resume:
        append_event(output_dir, {"event": "resume_from_state", "state": state.get("state")})
    run_from_current_state(output_dir, state, args)


# Internal module — do not invoke directly. Use skill_step.py as the public entry point.
if __name__ == "__main__":
    main()
