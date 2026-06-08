import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.release_evidence_readiness import build_release_evidence_readiness


def render_release_evidence_readiness_markdown(readiness: dict[str, object]) -> str:
    """Render release-evidence readiness as operator-readable Markdown."""
    location = readiness["export_location"]
    contract = readiness["evidence_contract"]
    retention = readiness["retention_policy"]
    gaps = "\n".join(f"- {gap}" for gap in readiness["open_gaps"])
    required_fields = "\n".join(f"- `{field}`" for field in contract["required_fields"])
    artifact_kinds = "\n".join(f"- `{kind}`" for kind in contract["accepted_artifact_kinds"])
    delete_targets = "\n".join(f"- `{target}`" for target in retention["forbidden_delete_targets"])
    return (
        "# ai-platform Release Evidence Readiness\n\n"
        f"Schema: `{readiness['schema_version']}`\n\n"
        f"Status: `{readiness['status']}`\n\n"
        f"Export location: `{location['path']}`\n\n"
        f"Index: `{location['index']}`\n\n"
        f"Entry schema: `{contract['schema_version']}`\n\n"
        f"Write path: `{contract['write_path']}`\n\n"
        "## Required Fields\n\n"
        f"{required_fields}\n\n"
        "## Accepted Artifact Kinds\n\n"
        f"{artifact_kinds}\n\n"
        "## Retention Policy\n\n"
        f"Schema: `{retention['schema_version']}`\n\n"
        f"Status: `{retention['status']}`\n\n"
        f"Default retention days: `{retention['default_retention_days']}`\n\n"
        f"Minimum retention days: `{retention['minimum_retention_days']}`\n\n"
        f"Requires review before delete: `{retention['requires_review_before_delete']}`\n\n"
        f"Delete only reviewed redacted entries: `{retention['delete_only_reviewed_redacted_entries']}`\n\n"
        "Forbidden delete targets:\n\n"
        f"{delete_targets}\n\n"
        "## Open Gaps\n\n"
        f"{gaps}\n\n"
        "This contract defines a repository-owned evidence location only. It does not close G9.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Print the G9 release evidence export-location contract.")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args()

    readiness = build_release_evidence_readiness()
    if args.format == "json":
        print(json.dumps(readiness, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(render_release_evidence_readiness_markdown(readiness))


if __name__ == "__main__":
    main()
