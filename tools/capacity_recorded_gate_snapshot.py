import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.capacity_baseline import (  # noqa: E402
    build_capacity_recorded_gate_batch_snapshot,
    build_capacity_recorded_gate_snapshot,
    render_capacity_recorded_gate_snapshot_markdown,
)
from tools.capacity_cli_inputs import read_capacity_json_object  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Merge one operator-reviewed #21 recorded gate evidence packet into "
            "a sanitized capacity evidence snapshot."
        ),
    )
    parser.add_argument("--runtime-evidence-json", required=True)
    parser.add_argument("--recorded-gate-evidence-json", action="append", required=True)
    parser.add_argument(
        "--profile-evidence-json",
        help=(
            "Operator-reviewed B3 profile evidence JSON. Optional for a single "
            "recorded gate merge, required when repeated --recorded-gate-evidence-json "
            "inputs assemble the all-gates B3 batch snapshot."
        ),
    )
    parser.add_argument("--gate", default="api_read_write_burst")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()

    runtime_evidence = read_capacity_json_object(args.runtime_evidence_json)
    profile_evidence = (
        read_capacity_json_object(args.profile_evidence_json)
        if args.profile_evidence_json
        else None
    )
    gate_evidence_packets = [
        read_capacity_json_object(path)
        for path in args.recorded_gate_evidence_json
    ]
    if len(gate_evidence_packets) == 1:
        result = build_capacity_recorded_gate_snapshot(
            runtime_evidence,
            gate_evidence_packets[0],
            gate=args.gate,
            profile_evidence=profile_evidence,
        )
    else:
        result = build_capacity_recorded_gate_batch_snapshot(
            runtime_evidence,
            gate_evidence_packets,
            profile_evidence=profile_evidence,
        )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_capacity_recorded_gate_snapshot_markdown(result))
    return 0 if result["status"] in {
        "recorded_gate_input_accepted",
        "recorded_gate_batch_input_accepted",
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())
