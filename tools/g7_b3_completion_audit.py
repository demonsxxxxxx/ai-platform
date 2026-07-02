import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.g7_b3_completion_audit import (  # noqa: E402
    build_g7_b3_completion_audit,
    render_g7_b3_completion_audit_markdown,
)


def _read_json(path_value: str | None) -> dict[str, object] | None:
    if not path_value:
        return None
    try:
        raw = sys.stdin.read() if path_value == "-" else Path(path_value).read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        input_name = "stdin" if path_value == "-" else Path(path_value).name
        raise SystemExit(f"failed to read JSON input: {input_name}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("JSON input must be an object")
    return payload


def _merge_optional_evidence(
    runtime_observation: dict[str, object] | None,
    *,
    reviewed_release_evidence_paths: list[str],
    foundation_runtime_concurrency_evidence_path: str | None,
) -> dict[str, object] | None:
    if runtime_observation is None:
        return None
    merged = dict(runtime_observation)
    if reviewed_release_evidence_paths:
        existing_entries = merged.get("reviewed_release_evidence_entries")
        entries = list(existing_entries) if isinstance(existing_entries, list) else []
        for path in reviewed_release_evidence_paths:
            evidence = _read_json(path)
            if evidence is not None:
                entries.append(evidence)
        merged["reviewed_release_evidence_entries"] = entries
    if foundation_runtime_concurrency_evidence_path:
        merged["foundation_runtime_concurrency_evidence"] = _read_json(
            foundation_runtime_concurrency_evidence_path
        )
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a fail-closed G7/B3 completion audit from sanitized runtime "
            "observation and optional capacity profile readiness JSON."
        ),
    )
    parser.add_argument("--runtime-observation-json", required=True)
    parser.add_argument("--capacity-profile-readiness-json")
    parser.add_argument(
        "--reviewed-release-evidence-json",
        action="append",
        default=[],
        help="Additional reviewed release-evidence JSON to merge over the runtime observation.",
    )
    parser.add_argument("--foundation-runtime-concurrency-evidence-json")
    parser.add_argument("--current-source-commit", required=True)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args()
    runtime_observation = _merge_optional_evidence(
        _read_json(args.runtime_observation_json),
        reviewed_release_evidence_paths=args.reviewed_release_evidence_json,
        foundation_runtime_concurrency_evidence_path=args.foundation_runtime_concurrency_evidence_json,
    )

    audit = build_g7_b3_completion_audit(
        runtime_observation=runtime_observation,
        capacity_profile_readiness=_read_json(args.capacity_profile_readiness_json),
        current_source_commit=args.current_source_commit,
    )
    if args.format == "json":
        print(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_g7_b3_completion_audit_markdown(audit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
