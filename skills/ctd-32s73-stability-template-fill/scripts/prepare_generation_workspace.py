from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from runtime_guard import make_internal_env, require_internal_context


SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = SKILL_ROOT / "assets" / "templates" / "ctd-32s73-stability-template-v2.docx"
DEFAULT_TEMPLATE_ID = "ctd-32s73-stability-template-v2"
INSPECT_SCRIPT = SKILL_ROOT / "scripts" / "inspect_docx_structure.py"
VALIDATE_SCRIPT = SKILL_ROOT / "scripts" / "validate_generated_docx.py"


def resolve_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def run_command(command: list[str], stdout_path: Path) -> dict[str, object]:
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        env=make_internal_env("prepare_generation_workspace.py", "run child command"),
    )
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    if completed.stderr:
        stdout_path.with_suffix(stdout_path.suffix + ".stderr.txt").write_text(completed.stderr, encoding="utf-8")
    return {"command": command, "returncode": completed.returncode, "stdout": str(stdout_path)}


def source_lines(title: str, sources: list[str]) -> list[str]:
    lines = [f"## {title}", ""]
    if not sources:
        lines.append("- 未提供")
    else:
        lines.extend(f"- {source}" for source in sources)
    lines.append("")
    return lines


def build_evidence_summary(args: argparse.Namespace, template_path: Path, filled_docx: Path) -> str:
    timestamp = datetime.now(timezone.utc).isoformat()
    lines = [
        "# Evidence Summary",
        "",
        f"Generated at: {timestamp}",
        "",
        "## Active template",
        "",
        f"- Template path: {template_path}",
        f"- Template ID: {args.template_id}",
        f"- Filled DOCX working copy: {filled_docx}",
        "- Status: workspace initialized; replace this note after project facts have been extracted and rendered.",
        "",
    ]
    lines.extend(source_lines("Used sources", args.source))
    lines.extend(source_lines("Excluded sources", args.forbidden_source))
    lines.extend(
        [
            "## Fact extraction status",
            "",
            "- Product identity: pending extraction",
            "- Batch list: pending extraction",
            "- Long-term completed months: pending extraction",
            "- Accelerated completed months: pending extraction",
            "- Stress-study mapping: pending extraction",
            "",
        ]
    )
    return "\n".join(lines)


def build_missing_evidence(args: argparse.Namespace) -> str:
    return "\n".join(
        [
            "# Missing Evidence Report",
            "",
            "This scaffold is created before project-specific fact extraction.",
            "Replace this file with unresolved facts discovered during extraction and rendering.",
            "",
            "## Initial watchlist",
            "",
            "- Confirm product expression and target sample type.",
            "- Confirm batch list and roles from allowed sources.",
            "- Confirm completed time points from actual results, not planned-only protocol schedules.",
            "- Confirm stress-study tables, units, and control recommendations.",
            "",
        ]
    )


def build_validation_report(
    filled_docx: Path,
    evidence_summary: Path,
    structure_snapshot: Path,
    validation_json: Path,
    validation_result: dict[str, object] | None,
) -> str:
    lines = [
        "# Validation Report",
        "",
        "Initial scaffold validation has been run against the copied template working file.",
        "This is not a final project validation report; rerun validation after fact extraction and DOCX rendering.",
        "",
        f"- Filled DOCX: {filled_docx}",
        f"- Evidence summary: {evidence_summary}",
        f"- Structure snapshot: {structure_snapshot}",
        f"- Validation JSON: {validation_json if validation_json.exists() else 'not generated'}",
        "",
        "## Initial validation notes",
        "",
        "The copied blank template is expected to contain placeholders and known caption-reference gaps.",
        "Final output must explain or resolve every warning reported by validate_generated_docx.py.",
        "",
    ]
    if validation_result is not None:
        lines.extend(
            [
                "## Initial validation command",
                "",
                f"- Return code: {validation_result['returncode']}",
                f"- Output: {validation_result['stdout']}",
                "",
            ]
        )
    return "\n".join(lines)


def main() -> None:
    require_internal_context("prepare_generation_workspace.py")
    parser = argparse.ArgumentParser(description="Initialize a CTD 3.2.S.7.3 generation workspace.")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--template-id", default=DEFAULT_TEMPLATE_ID)
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--forbidden-source", action="append", default=[])
    parser.add_argument("--filled-name", default="filled.docx")
    parser.add_argument("--skip-initial-validation", action="store_true")
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    template_path = args.template.resolve()
    filled_docx = output_dir / args.filled_name
    shutil.copyfile(template_path, filled_docx)

    evidence_summary = output_dir / "evidence-summary.md"
    validation_report = output_dir / "validation-report.md"
    missing_evidence = output_dir / "missing-evidence-report.md"
    fact_packet = output_dir / "fact-packet.json"
    structure_snapshot = output_dir / "structure-snapshot.txt"
    validation_json = output_dir / "validation.json"
    manifest = output_dir / "generation-manifest.json"

    write_text(evidence_summary, build_evidence_summary(args, template_path, filled_docx))
    write_text(missing_evidence, build_missing_evidence(args))
    write_text(
        fact_packet,
        json.dumps(
            {
                "status": "pending_fact_extraction",
                "sources": args.source,
                "forbidden_sources": args.forbidden_source,
                "template": str(template_path),
                "template_id": args.template_id,
            },
            ensure_ascii=False,
            indent=2,
        ),
    )

    inspect_result = run_command(
        [
            sys.executable,
            str(INSPECT_SCRIPT),
            str(filled_docx),
            "--caption-paragraphs",
            "--all-tables",
            "--max-rows",
            "2",
        ],
        structure_snapshot,
    )

    validation_result = None
    write_text(validation_report, build_validation_report(filled_docx, evidence_summary, structure_snapshot, validation_json, validation_result))
    if not args.skip_initial_validation:
        command = [
            sys.executable,
            str(VALIDATE_SCRIPT),
            str(filled_docx),
            "--template-docx",
            str(template_path),
            "--evidence-summary",
            str(evidence_summary),
            "--validation-report",
            str(validation_report),
            "--check-caption-references",
            "--json-out",
            str(validation_json),
        ]
        for source in args.source:
            command.extend(["--required-source", source])
        for source in args.forbidden_source:
            command.extend(["--forbidden-source", source])
        validation_result = run_command(command, output_dir / "validation-output.txt")
        write_text(validation_report, build_validation_report(filled_docx, evidence_summary, structure_snapshot, validation_json, validation_result))

    write_text(
        manifest,
        json.dumps(
            {
                "output_dir": str(output_dir),
                "template": str(template_path),
                "template_id": args.template_id,
                "filled_docx": str(filled_docx),
                "evidence_summary": str(evidence_summary),
                "validation_report": str(validation_report),
                "missing_evidence_report": str(missing_evidence),
                "fact_packet": str(fact_packet),
                "structure_snapshot": str(structure_snapshot),
                "validation_json": str(validation_json) if validation_json.exists() else None,
                "inspect_result": inspect_result,
                "validation_result": validation_result,
            },
            ensure_ascii=False,
            indent=2,
        ),
    )

    print(json.dumps({"output_dir": str(output_dir), "manifest": str(manifest)}, ensure_ascii=False, indent=2))


# Internal module — do not invoke directly. Use skill_step.py as the public entry point.
if __name__ == "__main__":
    main()
