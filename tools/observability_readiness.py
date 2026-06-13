import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.observability_readiness import (  # noqa: E402
    build_observability_readiness,
    render_observability_readiness_markdown,
)
from app.release_evidence_readiness import load_latest_reviewed_runtime_acceptance  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Print the current ai-platform G9 observability readiness baseline.")
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="Output format. Defaults to markdown.",
    )
    args = parser.parse_args()

    readiness = build_observability_readiness(
        release_evidence_runtime_acceptance=load_latest_reviewed_runtime_acceptance()
    )
    if args.format == "json":
        print(json.dumps(readiness, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(render_observability_readiness_markdown(readiness))


if __name__ == "__main__":
    main()
