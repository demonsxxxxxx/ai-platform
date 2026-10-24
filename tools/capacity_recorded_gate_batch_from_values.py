import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.capacity_baseline import (  # noqa: E402
    LOAD_TEST_GATES,
    build_capacity_profile_evidence_packet_result,
    build_capacity_recorded_gate_batch_snapshot,
    build_capacity_recorded_gate_evidence_packet_result,
)
from tools.capacity_cli_inputs import read_capacity_json_object  # noqa: E402


def _read_optional_json(path: Path) -> dict[str, object]:
    try:
        return read_capacity_json_object(path)
    except SystemExit:
        return {}


def _gate_values_path(input_dir: Path, gate: str) -> Path:
    slug = gate.replace("_", "-")
    return input_dir / f"capacity-operator-reviewed-evidence-values-{slug}.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build all seven B3 recorded-gate packets from operator-reviewed "
            "value files, then assemble the batch snapshot."
        ),
    )
    parser.add_argument("--runtime-evidence-json", required=True)
    parser.add_argument("--operator-input-dir", required=True)
    parser.add_argument("--cleanup-proof-status", required=True)
    parser.add_argument("--stop-condition-status", required=True)
    parser.add_argument(
        "--triggered-stop-condition",
        action="append",
        default=[],
        help="Repeat for each triggered stop condition. Must be omitted for acceptance.",
    )
    parser.add_argument(
        "--profile-evidence-json",
        help=(
            "Optional profile values JSON. Defaults to the B3 profile values "
            "filename inside --operator-input-dir."
        ),
    )
    parser.add_argument("--format", choices=("json",), default="json")
    args = parser.parse_args()

    input_dir = Path(args.operator_input_dir)
    runtime_evidence = read_capacity_json_object(Path(args.runtime_evidence_json))
    profile_values_path = (
        Path(args.profile_evidence_json)
        if args.profile_evidence_json
        else input_dir / "capacity-operator-reviewed-profile-values-b3-10x4-sdk-subagents.json"
    )
    profile_packet = build_capacity_profile_evidence_packet_result(
        _read_optional_json(profile_values_path)
    )
    gate_packets = [
        build_capacity_recorded_gate_evidence_packet_result(
            gate,
            _read_optional_json(_gate_values_path(input_dir, gate)),
            cleanup_proof_status=args.cleanup_proof_status,
            stop_condition_status=args.stop_condition_status,
            triggered_stop_conditions=args.triggered_stop_condition,
        )
        for gate in LOAD_TEST_GATES
    ]

    result = build_capacity_recorded_gate_batch_snapshot(
        runtime_evidence,
        gate_packets,
        profile_evidence=profile_packet,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "recorded_gate_batch_input_accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
