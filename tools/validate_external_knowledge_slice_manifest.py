from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = ROOT / "schemas" / "external-knowledge-slice-manifest.v1.schema.json"
DEFAULT_TRACEABILITY = (
    ROOT / "docs" / "product" / "external-knowledge" / "traceability-matrix.md"
)
DEFAULT_SLICES = (
    ROOT / "docs" / "product" / "external-knowledge" / "implementation-slices.md"
)
DEFAULT_MANIFEST_DIR = (
    ROOT / "docs" / "product" / "external-knowledge" / "manifests"
)
MANIFEST_VERSION = "ai-platform.external-knowledge-slice-manifest.v1"
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SLICE_HEADING_PATTERN = re.compile(r"^### (K[A-Z]+-[0-9]{2}) — ", re.MULTILINE)
TRACE_ROW_PATTERN = re.compile(
    r"^\| (?P<requirement>[A-Z]+-[0-9]{3}) "
    r"\| (?P<slice>K[A-Z]+-[0-9]{2}) "
    r"\| (?P<case>KAC-FR-[A-Z]+-[0-9]{3}) \|",
    re.MULTILINE,
)
KNOWLEDGE_PATH_PREFIXES = (
    "app/knowledge/",
    "app/routes/admin_knowledge.py",
    "app/routes/knowledge.py",
    "docs/product/external-knowledge/",
    "frontend/web/src/features/knowledge/",
    "frontend/web/src/services/api/knowledge.ts",
    "frontend/web/src/types/knowledge.ts",
    "schemas/external-knowledge-",
    "tests/test_external_knowledge",
    "tests/test_knowledge",
    "tools/validate_external_knowledge_slice_manifest.py",
)
BOOTSTRAP_PATH_PREFIXES = {
    "KDOC-00": ("docs/",),
    "KTRACE-62": (
        ".github/workflows/ai-platform-backend.yml",
        "docs/product/external-knowledge/README.md",
        "docs/product/external-knowledge/manifests/KTRACE-62.json",
        "schemas/external-knowledge-slice-manifest.v1.schema.json",
        "tests/test_backend_ci_workflow.py",
        "tests/test_external_knowledge_slice_manifest.py",
        "tools/validate_external_knowledge_slice_manifest.py",
    ),
}


class ManifestContractError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ValidatedManifest:
    path: Path
    slice_id: str
    atomic_case_ids: tuple[str, ...]
    changed_paths: tuple[str, ...]


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ManifestContractError("manifest_duplicate_key", key)
        value[key] = item
    return value


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except ManifestContractError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestContractError("manifest_json_invalid", path.as_posix()) from exc
    if not isinstance(value, dict):
        raise ManifestContractError("manifest_root_invalid", path.as_posix())
    return value


def load_slice_ids(path: Path) -> frozenset[str]:
    text = path.read_text(encoding="utf-8")
    slice_ids = frozenset(SLICE_HEADING_PATTERN.findall(text))
    if not slice_ids:
        raise ManifestContractError("slice_catalog_empty", path.as_posix())
    return slice_ids


def load_atomic_case_ownership(
    path: Path, *, known_slices: frozenset[str] | None = None
) -> dict[str, tuple[str, ...]]:
    text = path.read_text(encoding="utf-8")
    cases_by_slice: dict[str, list[str]] = defaultdict(list)
    requirements: set[str] = set()
    cases: set[str] = set()
    for match in TRACE_ROW_PATTERN.finditer(text):
        requirement = match.group("requirement")
        slice_id = match.group("slice")
        case_id = match.group("case")
        if known_slices is not None and slice_id not in known_slices:
            raise ManifestContractError("trace_slice_unknown", slice_id)
        if case_id != f"KAC-FR-{requirement}":
            raise ManifestContractError(
                "trace_case_identity_mismatch", f"{requirement}:{case_id}"
            )
        if requirement in requirements:
            raise ManifestContractError("trace_requirement_duplicate", requirement)
        if case_id in cases:
            raise ManifestContractError("trace_case_duplicate", case_id)
        requirements.add(requirement)
        cases.add(case_id)
        cases_by_slice[slice_id].append(case_id)
    if not requirements:
        raise ManifestContractError("traceability_empty", path.as_posix())
    return {slice_id: tuple(items) for slice_id, items in cases_by_slice.items()}


def validate_manifest(
    path: Path,
    *,
    schema_path: Path = DEFAULT_SCHEMA,
    traceability_path: Path = DEFAULT_TRACEABILITY,
    slices_path: Path = DEFAULT_SLICES,
) -> ValidatedManifest:
    document = load_json_object(path)
    schema = load_json_object(schema_path)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.absolute_path) or "$"
        raise ManifestContractError(
            "manifest_schema_invalid", f"{path.name}:{location}:{first.message}"
        )

    slice_id = str(document["slice_id"])
    known_slices = load_slice_ids(slices_path)
    if slice_id not in known_slices:
        raise ManifestContractError("manifest_slice_unknown", slice_id)

    ownership = load_atomic_case_ownership(
        traceability_path, known_slices=known_slices
    )
    expected_cases = ownership.get(slice_id, ())
    actual_cases = tuple(str(case_id) for case_id in document["atomic_case_ids"])
    if actual_cases != expected_cases:
        raise ManifestContractError(
            "manifest_case_set_mismatch",
            f"{slice_id}:expected={','.join(expected_cases)}:actual={','.join(actual_cases)}",
        )

    changed_paths = tuple(str(item) for item in document["changed_paths"])
    if changed_paths != tuple(sorted(changed_paths)):
        raise ManifestContractError("manifest_paths_not_sorted", slice_id)
    allowed_prefixes = BOOTSTRAP_PATH_PREFIXES.get(slice_id)
    if allowed_prefixes is not None:
        invalid_paths = [
            changed_path
            for changed_path in changed_paths
            if not any(changed_path.startswith(prefix) for prefix in allowed_prefixes)
        ]
        if invalid_paths:
            raise ManifestContractError(
                "bootstrap_path_claim_invalid",
                f"{slice_id}:{','.join(invalid_paths)}",
            )

    return ValidatedManifest(
        path=path,
        slice_id=slice_id,
        atomic_case_ids=actual_cases,
        changed_paths=changed_paths,
    )


def validate_all_manifests(
    manifest_dir: Path = DEFAULT_MANIFEST_DIR,
    *,
    schema_path: Path = DEFAULT_SCHEMA,
    traceability_path: Path = DEFAULT_TRACEABILITY,
    slices_path: Path = DEFAULT_SLICES,
) -> tuple[ValidatedManifest, ...]:
    paths = tuple(sorted(manifest_dir.glob("*.json")))
    if not paths:
        raise ManifestContractError("manifest_set_empty", manifest_dir.as_posix())
    manifests = tuple(
        validate_manifest(
            path,
            schema_path=schema_path,
            traceability_path=traceability_path,
            slices_path=slices_path,
        )
        for path in paths
    )
    slice_ids = [manifest.slice_id for manifest in manifests]
    if len(slice_ids) != len(set(slice_ids)):
        raise ManifestContractError("manifest_slice_duplicate", ",".join(slice_ids))
    return manifests


def git_changed_paths(root: Path, base_ref: str, head_ref: str) -> tuple[str, ...]:
    for label, value in (("base", base_ref), ("head", head_ref)):
        if not SHA_PATTERN.fullmatch(value):
            raise ManifestContractError("git_subject_invalid", f"{label}:{value}")
    completed = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=ACMRD",
            base_ref,
            head_ref,
            "--",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ManifestContractError("git_diff_failed", "exact subjects unavailable")
    return tuple(path for path in completed.stdout.splitlines() if path)


def _is_knowledge_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in KNOWLEDGE_PATH_PREFIXES)


def validate_changed_path_coverage(
    changed_paths: Iterable[str],
    manifests: Sequence[ValidatedManifest],
    *,
    manifest_dir: Path = DEFAULT_MANIFEST_DIR,
    root: Path = ROOT,
) -> tuple[str, ...]:
    changed = tuple(dict.fromkeys(changed_paths))
    knowledge_changed = tuple(path for path in changed if _is_knowledge_path(path))
    if not knowledge_changed:
        return ()

    manifest_prefix = manifest_dir.relative_to(root).as_posix().rstrip("/") + "/"
    changed_manifest_paths = {
        path for path in changed if path.startswith(manifest_prefix) and path.endswith(".json")
    }
    selected = tuple(
        manifest
        for manifest in manifests
        if manifest.path.relative_to(root).as_posix() in changed_manifest_paths
    )
    if not selected:
        raise ManifestContractError(
            "changed_manifest_required", ",".join(knowledge_changed)
        )

    claimed_by: dict[str, str] = {}
    for manifest in selected:
        for path in manifest.changed_paths:
            if path in claimed_by:
                raise ManifestContractError(
                    "changed_path_claim_duplicate",
                    f"{path}:{claimed_by[path]}:{manifest.slice_id}",
                )
            claimed_by[path] = manifest.slice_id
            if path not in changed:
                raise ManifestContractError(
                    "manifest_path_not_changed", f"{manifest.slice_id}:{path}"
                )

    uncovered = [path for path in knowledge_changed if path not in claimed_by]
    if uncovered:
        raise ManifestContractError(
            "knowledge_path_uncovered", ",".join(uncovered)
        )
    return tuple(manifest.slice_id for manifest in selected)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate External Knowledge slice manifests and exact changed-path coverage."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--traceability", type=Path, default=DEFAULT_TRACEABILITY)
    parser.add_argument("--slices", type=Path, default=DEFAULT_SLICES)
    parser.add_argument("--manifest-dir", type=Path, default=DEFAULT_MANIFEST_DIR)
    parser.add_argument("--base-ref")
    parser.add_argument("--head-ref")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if bool(args.base_ref) != bool(args.head_ref):
        raise ManifestContractError(
            "git_subject_pair_required", "base-ref and head-ref must be supplied together"
        )
    manifests = validate_all_manifests(
        args.manifest_dir,
        schema_path=args.schema,
        traceability_path=args.traceability,
        slices_path=args.slices,
    )
    selected: tuple[str, ...] = ()
    if args.base_ref and args.head_ref:
        changed = git_changed_paths(args.root, args.base_ref, args.head_ref)
        selected = validate_changed_path_coverage(
            changed,
            manifests,
            manifest_dir=args.manifest_dir,
            root=args.root,
        )
    print(
        "external_knowledge_manifest_gate=pass "
        f"manifests={len(manifests)} changed_slices={','.join(selected) or 'none'}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ManifestContractError as exc:
        raise SystemExit(str(exc)) from exc
