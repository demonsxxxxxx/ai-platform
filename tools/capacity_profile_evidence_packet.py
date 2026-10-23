import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.capacity_baseline import build_capacity_profile_evidence_packet_result  # noqa: E402
from tools.capacity_cli_inputs import read_capacity_json_object  # noqa: E402


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
        read_capacity_json_object(args.evidence_json)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "profile_evidence_packet_ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
