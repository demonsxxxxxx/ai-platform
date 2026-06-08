import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.office_context_readiness import (  # noqa: E402
    build_office_context_readiness,
    render_office_context_readiness_markdown,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print the current ai-platform #22 office context-pack readiness baseline."
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="Output format. Defaults to markdown.",
    )
    args = parser.parse_args()

    readiness = build_office_context_readiness()
    if args.format == "json":
        print(json.dumps(readiness, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(render_office_context_readiness_markdown(readiness))


if __name__ == "__main__":
    main()
