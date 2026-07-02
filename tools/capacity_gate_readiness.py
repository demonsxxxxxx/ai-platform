import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.capacity_baseline import (
    build_capacity_gate_readiness,
    render_capacity_gate_readiness_markdown,
)


def _read_snapshot_json(path_value: str) -> dict[str, object]:
    if path_value == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path_value).read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise SystemExit("snapshot JSON must be an object")
    return payload


def _snapshot_input(payload: dict[str, object]) -> dict[str, object]:
    if payload.get("schema_version") == "ai-platform.capacity-runtime-evidence.v1":
        snapshot = payload.get("snapshot")
        if isinstance(snapshot, dict):
            return snapshot
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a secret-safe #21 capacity gate readiness verdict from a capacity evidence snapshot.",
    )
    parser.add_argument(
        "--snapshot-json",
        required=True,
        help="Path to a capacity evidence snapshot JSON payload, or '-' for stdin.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="Output format. Defaults to markdown.",
    )
    args = parser.parse_args()

    snapshot = _snapshot_input(_read_snapshot_json(args.snapshot_json))
    readiness = build_capacity_gate_readiness(snapshot)
    if args.format == "json":
        print(json.dumps(readiness, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(render_capacity_gate_readiness_markdown(readiness))


if __name__ == "__main__":
    main()
