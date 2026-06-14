#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.foundation_runtime_concurrency import (  # noqa: E402
    build_foundation_runtime_concurrency_readiness,
    load_foundation_runtime_concurrency_evidence,
    render_foundation_runtime_concurrency_markdown,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize Foundation Runtime 10+ concurrent-run isolation evidence."
    )
    parser.add_argument("--evidence-json", help="Reviewed redacted Foundation Runtime concurrency evidence JSON.")
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="Output format. Defaults to markdown.",
    )
    args = parser.parse_args()

    evidence = load_foundation_runtime_concurrency_evidence(args.evidence_json)
    readiness = build_foundation_runtime_concurrency_readiness(evidence)
    if args.format == "json":
        print(json.dumps(readiness, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(render_foundation_runtime_concurrency_markdown(readiness))


if __name__ == "__main__":
    main()
