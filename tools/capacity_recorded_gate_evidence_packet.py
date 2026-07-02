import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.capacity_baseline import build_capacity_recorded_gate_evidence_packet_result


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
            "Build a sanitized operator-reviewed capacity recorded-gate "
            "evidence packet without marking the gate recorded."
        ),
    )
    parser.add_argument("--gate", required=True)
    parser.add_argument("--evidence-json", required=True)
    parser.add_argument("--cleanup-proof-status", required=True)
    parser.add_argument("--stop-condition-status", required=True)
    parser.add_argument(
        "--triggered-stop-condition",
        action="append",
        default=[],
        help="Repeat for each triggered stop condition. Must be omitted for acceptance.",
    )
    parser.add_argument("--format", choices=("json",), default="json")
    args = parser.parse_args()

    result = build_capacity_recorded_gate_evidence_packet_result(
        args.gate,
        _read_json(args.evidence_json),
        cleanup_proof_status=args.cleanup_proof_status,
        stop_condition_status=args.stop_condition_status,
        triggered_stop_conditions=args.triggered_stop_condition,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "recorded_gate_evidence_packet_ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
