import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.capacity_baseline import (
    build_capacity_evidence_bundle,
    render_capacity_evidence_bundle_markdown,
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
        _read_json(args.runtime_evidence_json),
        _read_json(args.bounded_probe_json),
        gate=args.gate,
        start_runtime_evidence=(
            _read_json(args.start_runtime_evidence_json)
            if args.start_runtime_evidence_json
            else None
        ),
        cleanup_proof=(
            _read_json(args.cleanup_proof_json)
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
