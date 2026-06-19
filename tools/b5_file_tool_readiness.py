from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.b5_file_tool_readiness import (  # noqa: E402
    build_b5_file_tool_readiness,
    render_b5_file_tool_readiness_markdown,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Report B5 file/tool readiness")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args()

    readiness = build_b5_file_tool_readiness()
    if args.format == "json":
        print(json.dumps(readiness, ensure_ascii=False, indent=2))
    else:
        print(render_b5_file_tool_readiness_markdown(readiness))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
