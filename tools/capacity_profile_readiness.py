import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.capacity_baseline import (
    build_capacity_profile_readiness,
    render_capacity_profile_readiness_markdown,
)


def _read_json(path_value: str) -> dict[str, object]:
    if path_value == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path_value).read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise SystemExit("capacity profile readiness input JSON must be an object")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a secret-safe #21 capacity profile readiness catalog from a "
            "capacity evidence snapshot or gate readiness verdict."
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--snapshot-json",
        help="Path to a capacity evidence snapshot JSON payload, or '-' for stdin.",
    )
    source.add_argument(
        "--readiness-json",
        help="Path to a capacity gate readiness JSON payload, or '-' for stdin.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="Output format. Defaults to markdown.",
    )
    args = parser.parse_args()

    input_path = args.snapshot_json or args.readiness_json
    readiness = build_capacity_profile_readiness(_read_json(input_path))
    if args.format == "json":
        print(json.dumps(readiness, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(render_capacity_profile_readiness_markdown(readiness))


if __name__ == "__main__":
    main()
