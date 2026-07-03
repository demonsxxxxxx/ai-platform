#!/usr/bin/env python3
"""Verify local B1/B5 bounded context runtime readiness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.b1_b5_context_runtime_readiness import (  # noqa: E402
    build_b1_b5_context_runtime_readiness,
    render_b1_b5_context_runtime_readiness_markdown,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify local B1/B5 context continuity runtime contracts."
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="Output format. Defaults to json.",
    )
    args = parser.parse_args()

    readiness = build_b1_b5_context_runtime_readiness()
    if args.format == "markdown":
        print(render_b1_b5_context_runtime_readiness_markdown(readiness))
    else:
        print(json.dumps(readiness, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if readiness.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
