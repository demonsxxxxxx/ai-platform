import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.error_taxonomy_dashboard_readiness import build_error_taxonomy_dashboard_readiness  # noqa: E402


def render_error_taxonomy_dashboard_readiness_markdown(readiness: dict[str, object]) -> str:
    """Render the error taxonomy dashboard contract as operator-readable Markdown."""
    contract = readiness["dashboard_contract"] if isinstance(readiness.get("dashboard_contract"), dict) else {}
    fields = contract.get("required_admin_runtime_fields") if isinstance(contract, dict) else []
    categories = contract.get("required_category_ids") if isinstance(contract, dict) else []
    gaps = readiness.get("open_gaps")
    field_lines = "\n".join(f"- `{field}`" for field in fields if isinstance(field, str))
    category_lines = "\n".join(f"- `{category}`" for category in categories if isinstance(category, str))
    gap_lines = "\n".join(f"- `{gap}`" for gap in gaps if isinstance(gap, str)) if isinstance(gaps, list) else ""
    return (
        "# ai-platform G9 Error Taxonomy Dashboard Readiness\n\n"
        f"Schema: `{readiness['schema_version']}`\n\n"
        f"Gate: `{readiness['gate']}`\n\n"
        f"Status: `{readiness['status']}`\n\n"
        f"Active dashboard policy: `{readiness['active_dashboard_policy']}`\n\n"
        f"Dashboard contract: `{contract.get('schema_version')}`\n\n"
        "Required Admin Runtime fields:\n\n"
        f"{field_lines}\n\n"
        "Required categories:\n\n"
        f"{category_lines}\n\n"
        "Open gaps:\n\n"
        f"{gap_lines}\n\n"
        "This contract does not close G9 and does not expose raw runtime payloads.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Print the current ai-platform G9 error taxonomy dashboard contract.")
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="Output format. Defaults to markdown.",
    )
    args = parser.parse_args()

    readiness = build_error_taxonomy_dashboard_readiness()
    if args.format == "json":
        print(json.dumps(readiness, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(render_error_taxonomy_dashboard_readiness_markdown(readiness))


if __name__ == "__main__":
    main()
