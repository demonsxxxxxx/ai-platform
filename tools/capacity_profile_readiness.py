import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.capacity_baseline import (
    LOAD_TEST_GATES,
    build_capacity_profile_readiness,
    render_capacity_profile_readiness_markdown,
)


def _read_json(path_value: str) -> dict[str, object]:
    if path_value == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path_value).read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise SystemExit("capacity profile readiness input JSON must be an object")
    return payload


def _profile_readiness_input(payload: dict[str, object]) -> dict[str, object]:
    if payload.get("schema_version") == "ai-platform.release-evidence-entry.v1":
        artifact_kind = payload.get("artifact_kind")
        evidence_ref = payload.get("evidence_ref")
        if artifact_kind == "capacity_gate_readiness" and isinstance(evidence_ref, dict):
            nested = dict(evidence_ref)
            nested.setdefault(
                "commit_sha",
                payload.get("runtime_subject_commit_sha") or payload.get("commit_sha"),
            )
            return _profile_readiness_input(nested)
    if payload.get("schema_version") == "ai-platform.capacity-runtime-evidence.v1":
        readiness = payload.get("readiness")
        if isinstance(readiness, dict):
            return readiness
        missing_sections = payload.get("admin_runtime_missing_sections")
        missing_gates = payload.get("missing_load_test_gates")
        profile_evidence = payload.get("profile_evidence")
        return {
            "schema_version": "ai-platform.capacity-gate-readiness.v1",
            "status": payload.get("readiness_status"),
            "runtime_identity": {
                "commit_sha": payload.get("commit_sha"),
                "profile": payload.get("runtime_profile"),
            },
            "admin_runtime_evidence": {
                "required_sections": payload.get("admin_runtime_required_sections"),
                "missing_sections": missing_sections,
            },
            "load_test_gates": [
                {
                    "gate": gate,
                    "status": "missing_recorded_load_test_evidence",
                }
                for gate in LOAD_TEST_GATES
            ],
            "missing_load_test_gates": missing_gates,
            "invalid_load_test_evidence": [],
            "profile_evidence": profile_evidence,
            "production_default_decision": payload.get("production_default_decision"),
        }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a secret-safe #21 capacity profile readiness catalog from a "
            "capacity evidence snapshot or gate readiness verdict."
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--snapshot-json",
        help="Path to a capacity evidence snapshot JSON payload, or '-' for stdin.",
    )
    source.add_argument(
        "--readiness-json",
        help="Path to a capacity gate readiness JSON payload, or '-' for stdin.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="Output format. Defaults to markdown.",
    )
    args = parser.parse_args()

    input_path = args.snapshot_json or args.readiness_json
    readiness = build_capacity_profile_readiness(_profile_readiness_input(_read_json(input_path)))
    if args.format == "json":
        print(json.dumps(readiness, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(render_capacity_profile_readiness_markdown(readiness))


if __name__ == "__main__":
    main()
