import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.capacity_baseline import build_capacity_operator_evidence_template_bundle


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _materialize_template_files(
    bundle: dict[str, object],
    output_dir: Path,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []

    manifest_name = "capacity-operator-evidence-template-bundle.json"
    _write_json(output_dir / manifest_name, bundle)
    files.append(manifest_name)

    profile_template = bundle["profile_template"]
    if not isinstance(profile_template, dict):
        raise SystemExit("profile_template must be an object")
    profile_filename = str(profile_template["output_filename"])
    profile_values = profile_template["values"]
    if not isinstance(profile_values, dict):
        raise SystemExit("profile_template.values must be an object")
    _write_json(output_dir / profile_filename, profile_values)
    files.append(profile_filename)

    gate_templates = bundle["recorded_gate_value_templates"]
    if not isinstance(gate_templates, dict):
        raise SystemExit("recorded_gate_value_templates must be an object")
    for gate in sorted(gate_templates):
        template = gate_templates[gate]
        if not isinstance(template, dict):
            raise SystemExit("recorded gate template must be an object")
        filename = str(template["output_filename"])
        values = template["values"]
        if not isinstance(values, dict):
            raise SystemExit("recorded gate template values must be an object")
        _write_json(output_dir / filename, values)
        files.append(filename)

    return {
        "status": "template_files_written",
        "output_dir": output_dir.name,
        "files": files,
        "does_not_mark_gate_recorded": True,
        "does_not_close_b3_gate": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Print draft-only operator input templates for B3 capacity evidence. "
            "The output is not recorded gate evidence."
        ),
    )
    parser.add_argument("--format", choices=("json",), default="json")
    parser.add_argument(
        "--output-dir",
        default="",
        help=(
            "Optional directory to write the draft value JSON files and manifest. "
            "Only file names are printed in the result."
        ),
    )
    args = parser.parse_args()

    bundle = build_capacity_operator_evidence_template_bundle()
    if args.output_dir:
        bundle["materialization"] = _materialize_template_files(
            bundle,
            Path(args.output_dir),
        )
    print(json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
