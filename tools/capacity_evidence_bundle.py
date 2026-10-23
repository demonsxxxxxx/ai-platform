import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.capacity_baseline import (  # noqa: E402
    build_capacity_evidence_bundle,
    render_capacity_evidence_bundle_markdown,
)
from tools.capacity_cli_inputs import read_capacity_json_object  # noqa: E402


def main() -> int:
    """Build a fail-closed #21 operator evidence bundle from captured JSON files."""
    parser = argparse.ArgumentParser(
        description=(
            "Assemble runtime evidence and bounded probe output into a #21 "
            "capacity evidence draft without marking a load-test gate recorded."
        ),
    )
    parser.add_argument("--start-runtime-evidence-json")
    parser.add_argument("--runtime-evidence-json", required=True)
    parser.add_argument("--bounded-probe-json", required=True)
    parser.add_argument("--cleanup-proof-json")
    parser.add_argument("--gate", default="api_read_write_burst")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args()

    bundle = build_capacity_evidence_bundle(
        read_capacity_json_object(args.runtime_evidence_json),
        read_capacity_json_object(args.bounded_probe_json),
        gate=args.gate,
        start_runtime_evidence=(
            read_capacity_json_object(args.start_runtime_evidence_json)
            if args.start_runtime_evidence_json
            else None
        ),
        cleanup_proof=(
            read_capacity_json_object(args.cleanup_proof_json)
            if args.cleanup_proof_json
            else None
        ),
    )
    if args.format == "json":
        print(json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_capacity_evidence_bundle_markdown(bundle))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
