import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.capacity_baseline import (
    build_capacity_recorded_gate_snapshot,
    render_capacity_recorded_gate_snapshot_markdown,
)


def _read_json(path: str) -> dict[str, object]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"failed to read JSON input: {Path(path).name}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"JSON input must be an object: {Path(path).name}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Merge one operator-reviewed #21 recorded gate evidence packet into "
            "a sanitized capacity evidence snapshot."
        ),
    )
    parser.add_argument("--runtime-evidence-json", required=True)
    parser.add_argument("--recorded-gate-evidence-json", required=True)
    parser.add_argument(
        "--profile-evidence-json",
        help=(
            "Optional operator-reviewed B3 profile evidence JSON. When provided, "
            "it is sanitized and written only to load_test_evidence.profile_evidence."
        ),
    )
    parser.add_argument("--gate", default="api_read_write_burst")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()

    result = build_capacity_recorded_gate_snapshot(
        _read_json(args.runtime_evidence_json),
        _read_json(args.recorded_gate_evidence_json),
        gate=args.gate,
        profile_evidence=(
            _read_json(args.profile_evidence_json)
            if args.profile_evidence_json
            else None
        ),
    )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_capacity_recorded_gate_snapshot_markdown(result))
    return 0 if result["status"] == "recorded_gate_input_accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
