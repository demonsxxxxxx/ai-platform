import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.release_evidence_export_acceptance import build_release_evidence_export_acceptance


def render_release_evidence_export_acceptance_markdown(acceptance: dict[str, object]) -> str:
    """Render release-evidence export acceptance as operator-readable Markdown."""
    entries = acceptance.get("entries")
    entry_lines = ""
    if isinstance(entries, list):
        entry_lines = "\n".join(
            f"- `{entry.get('path')}` `{entry.get('artifact_kind')}` `{entry.get('review_status')}`"
            for entry in entries
            if isinstance(entry, dict)
        )
    blockers = acceptance.get("blockers")
    blocker_lines = ""
    if isinstance(blockers, list):
        blocker_lines = "\n".join(f"- `{blocker}`" for blocker in blockers) or "- none"
    return (
        "# ai-platform Release Evidence Export Acceptance\n\n"
        f"Schema: `{acceptance['schema_version']}`\n\n"
        f"Status: `{acceptance['status']}`\n\n"
        f"Export policy: `{acceptance['export_policy']}`\n\n"
        f"Evidence root: `{acceptance['evidence_root']}`\n\n"
        f"Entry count: `{acceptance['entry_count']}`\n\n"
        f"Safe entry count: `{acceptance['safe_entry_count']}`\n\n"
        "## Entries\n\n"
        f"{entry_lines or '- none'}\n\n"
        "## Blockers\n\n"
        f"{blocker_lines}\n\n"
        "This verifier emits a safe reviewed-evidence index only. It does not export raw runtime payloads or close G9.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify release-evidence export acceptance preconditions.")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument(
        "--evidence-root",
        default=str(ROOT / "docs" / "release-evidence"),
        help="Release evidence root to scan. Defaults to docs/release-evidence.",
    )
    args = parser.parse_args()

    acceptance = build_release_evidence_export_acceptance(evidence_root=Path(args.evidence_root))
    if args.format == "json":
        print(json.dumps(acceptance, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(render_release_evidence_export_acceptance_markdown(acceptance))


if __name__ == "__main__":
    main()
