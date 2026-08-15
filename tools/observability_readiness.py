import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.observability_readiness import (  # noqa: E402
    build_observability_readiness,
    render_observability_readiness_markdown,
)
from app.release_evidence_readiness import (  # noqa: E402
    load_reviewed_runtime_acceptance_for_subject,
)


_FULL_COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _runtime_subject_sha(value: str) -> str:
    if not _FULL_COMMIT_SHA_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError("must be a full lowercase 40-character commit SHA")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Print the current ai-platform G9 observability readiness baseline.")
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="Output format. Defaults to markdown.",
    )
    parser.add_argument(
        "--runtime-subject-sha",
        type=_runtime_subject_sha,
        help=(
            "Load reviewed runtime evidence only for this exact subject SHA. "
            "When omitted, historical repository evidence is not treated as current runtime evidence."
        ),
    )
    parser.add_argument(
        "--evidence-root",
        type=Path,
        help="Optional evidence root used with --runtime-subject-sha.",
    )
    args = parser.parse_args()

    if args.evidence_root is not None and args.runtime_subject_sha is None:
        parser.error("--evidence-root requires --runtime-subject-sha")

    runtime_acceptance = None
    if args.runtime_subject_sha is not None:
        runtime_acceptance = load_reviewed_runtime_acceptance_for_subject(
            args.runtime_subject_sha,
            args.evidence_root,
        )

    readiness = build_observability_readiness(
        release_evidence_runtime_acceptance=runtime_acceptance
    )
    if args.format == "json":
        print(json.dumps(readiness, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(render_observability_readiness_markdown(readiness))


if __name__ == "__main__":
    main()
