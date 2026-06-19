from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.b6_operations_beta_readiness import (  # noqa: E402
    build_b6_operations_beta_readiness,
    render_b6_operations_beta_readiness_markdown,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Report B6 operations beta readiness")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args()

    readiness = build_b6_operations_beta_readiness()
    if args.format == "json":
        print(json.dumps(readiness, ensure_ascii=False, indent=2))
    else:
        print(render_b6_operations_beta_readiness_markdown(readiness))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
