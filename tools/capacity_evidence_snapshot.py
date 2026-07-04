import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.capacity_baseline import (
    build_capacity_evidence_snapshot,
    render_capacity_evidence_snapshot_markdown,
)
from capacity_cli_inputs import read_optional_host_sandbox_observation_json


def _read_overview_json(path_value: str) -> dict[str, object]:
    if path_value == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path_value).read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise SystemExit("overview JSON must be an object")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a secret-safe #21 capacity evidence snapshot from an Admin Runtime overview JSON export.",
    )
    parser.add_argument(
        "--overview-json",
        required=True,
        help="Path to an exported /api/ai/admin/runtime/overview JSON payload, or '-' for stdin.",
    )
    parser.add_argument(
        "--commit-sha",
        default="unknown",
        help="Deployed commit SHA or image revision label to attach to the evidence snapshot.",
    )
    parser.add_argument(
        "--runtime-profile",
        default="unproven_default",
        help="Operator profile label for this snapshot. Defaults to unproven_default.",
    )
    parser.add_argument(
        "--host-sandbox-observation-json",
        help=(
            "Optional host-side sandbox observation JSON from a Docker-capable "
            "operator host. Does not create load-test evidence."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="Output format. Defaults to markdown.",
    )
    args = parser.parse_args()

    overview = _read_overview_json(args.overview_json)
    host_sandbox_observation, host_sandbox_observation_error = (
        read_optional_host_sandbox_observation_json(args.host_sandbox_observation_json)
    )
    snapshot = build_capacity_evidence_snapshot(
        overview,
        commit_sha=args.commit_sha,
        runtime_profile=args.runtime_profile,
        host_sandbox_observation=host_sandbox_observation,
    )
    if host_sandbox_observation_error is not None:
        snapshot["host_sandbox_observation"] = host_sandbox_observation_error
    if args.format == "json":
        print(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(render_capacity_evidence_snapshot_markdown(snapshot))


if __name__ == "__main__":
    main()
