#!/usr/bin/env python3
"""Verify runtime-packaged release evidence export and retention acceptance."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.release_evidence_runtime_acceptance import build_release_evidence_runtime_acceptance


def render_release_evidence_runtime_acceptance_markdown(acceptance: dict[str, object]) -> str:
    """Render release-evidence runtime acceptance as operator-readable Markdown."""
    checks = acceptance.get("checks") if isinstance(acceptance.get("checks"), dict) else {}
    runtime_export = (
        checks.get("runtime_export_acceptance") if isinstance(checks.get("runtime_export_acceptance"), dict) else {}
    )
    retention = (
        checks.get("retention_runtime_acceptance")
        if isinstance(checks.get("retention_runtime_acceptance"), dict)
        else {}
    )
    gaps = acceptance.get("open_gaps") if isinstance(acceptance.get("open_gaps"), list) else []
    gap_lines = "\n".join(f"- `{gap}`" for gap in gaps) or "- none"
    return (
        "# ai-platform Release Evidence Runtime Acceptance\n\n"
        f"Schema: `{acceptance['schema_version']}`\n\n"
        f"Status: `{acceptance['status']}`\n\n"
        f"OK: `{acceptance['ok']}`\n\n"
        "## Runtime Export\n\n"
        f"- status: `{runtime_export.get('status')}`\n"
        f"- policy: `{runtime_export.get('export_policy')}`\n"
        f"- safe entries: `{runtime_export.get('safe_entry_count')}`\n"
        f"- blocked entries: `{runtime_export.get('blocked_entry_count')}`\n"
        f"- safe entry fields only: `{runtime_export.get('safe_entry_fields_only')}`\n\n"
        "## Retention\n\n"
        f"- status: `{retention.get('status')}`\n"
        f"- policy schema: `{retention.get('schema_version')}`\n"
        f"- review before delete: `{retention.get('requires_review_before_delete')}`\n"
        f"- delete only reviewed redacted entries: `{retention.get('delete_only_reviewed_redacted_entries')}`\n\n"
        "## Open Gaps\n\n"
        f"{gap_lines}\n\n"
        "This verifier emits safe runtime acceptance evidence only. It does not export raw runtime payloads or close G9.\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify release-evidence runtime acceptance preconditions.")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument(
        "--evidence-root",
        default=str(ROOT / "docs" / "release-evidence"),
        help="Release evidence root to scan. Defaults to docs/release-evidence.",
    )
    parser.add_argument("--commit-sha", default=os.environ.get("AI_PLATFORM_COMMIT_SHA", "unknown"))
    parser.add_argument(
        "--runtime-subject-commit-sha",
        default=os.environ.get("AI_PLATFORM_RUNTIME_SUBJECT_COMMIT_SHA", ""),
    )
    parser.add_argument("--image", default=os.environ.get("AI_PLATFORM_IMAGE", ""))
    args = parser.parse_args()

    acceptance = build_release_evidence_runtime_acceptance(
        evidence_root=Path(args.evidence_root),
        commit_sha=args.commit_sha,
        runtime_subject_commit_sha=args.runtime_subject_commit_sha,
        image=args.image,
    )
    if args.format == "json":
        print(json.dumps(acceptance, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_release_evidence_runtime_acceptance_markdown(acceptance))
    return 0 if acceptance["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
