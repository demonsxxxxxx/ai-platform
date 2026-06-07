import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.memory_erasure_readiness import build_memory_erasure_readiness, render_memory_erasure_readiness_markdown


def main() -> None:
    parser = argparse.ArgumentParser(description="Print the current ai-platform memory erasure readiness evidence.")
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="Output format. Defaults to markdown.",
    )
    args = parser.parse_args()

    readiness = build_memory_erasure_readiness()
    if args.format == "json":
        print(json.dumps(readiness, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(render_memory_erasure_readiness_markdown(readiness))


if __name__ == "__main__":
    main()
