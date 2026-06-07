import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.capacity_baseline import build_capacity_baseline, render_capacity_baseline_markdown


def main() -> None:
    parser = argparse.ArgumentParser(description="Print the current ai-platform capacity baseline.")
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="Output format. Defaults to markdown.",
    )
    args = parser.parse_args()

    baseline = build_capacity_baseline()
    if args.format == "json":
        print(json.dumps(baseline, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(render_capacity_baseline_markdown(baseline))


if __name__ == "__main__":
    main()
