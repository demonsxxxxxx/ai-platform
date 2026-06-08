import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.trace_audit_export_readiness import build_trace_audit_export_readiness  # noqa: E402


def render_trace_audit_export_readiness_markdown(readiness: dict[str, object]) -> str:
    """Render the trace/audit export contract as operator-readable Markdown."""
    contract = readiness["export_contract"] if isinstance(readiness.get("export_contract"), dict) else {}
    required_fields = contract.get("required_fields") if isinstance(contract, dict) else []
    event_sources = contract.get("allowed_event_sources") if isinstance(contract, dict) else []
    gaps = readiness.get("open_gaps")
    field_lines = "\n".join(f"- `{field}`" for field in required_fields if isinstance(field, str))
    source_lines = "\n".join(f"- `{source}`" for source in event_sources if isinstance(source, str))
    gap_lines = "\n".join(f"- `{gap}`" for gap in gaps if isinstance(gap, str)) if isinstance(gaps, list) else ""
    return (
        "# ai-platform G9 Trace / Audit Export Readiness\n\n"
        f"Schema: `{readiness['schema_version']}`\n\n"
        f"Gate: `{readiness['gate']}`\n\n"
        f"Status: `{readiness['status']}`\n\n"
        f"Active export policy: `{readiness['active_export_policy']}`\n\n"
        f"Export contract: `{contract.get('schema_version')}`\n\n"
        f"Write path: `{contract.get('write_path')}`\n\n"
        "Required fields:\n\n"
        f"{field_lines}\n\n"
        "Allowed event sources:\n\n"
        f"{source_lines}\n\n"
        "Open gaps:\n\n"
        f"{gap_lines}\n\n"
        "This contract does not close G9 and does not export raw runtime payloads.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Print the current ai-platform G9 trace/audit export contract.")
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="Output format. Defaults to markdown.",
    )
    args = parser.parse_args()

    readiness = build_trace_audit_export_readiness()
    if args.format == "json":
        print(json.dumps(readiness, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(render_trace_audit_export_readiness_markdown(readiness))


if __name__ == "__main__":
    main()
