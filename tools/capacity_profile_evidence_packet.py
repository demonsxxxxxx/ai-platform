import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.capacity_baseline import build_capacity_profile_evidence_packet_result


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
            "Build a sanitized operator-reviewed B3 profile evidence packet "
            "without closing B3 or raising defaults."
        ),
    )
    parser.add_argument("--evidence-json", required=True)
    parser.add_argument("--format", choices=("json",), default="json")
    args = parser.parse_args()

    result = build_capacity_profile_evidence_packet_result(
        _read_json(args.evidence_json)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "profile_evidence_packet_ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
