import json
import subprocess
from pathlib import Path

import pytest

from tools.validate_external_knowledge_slice_manifest import (
    DEFAULT_MANIFEST_DIR,
    ManifestContractError,
    ValidatedManifest,
    load_atomic_case_ownership,
    git_changed_paths,
    validate_all_manifests,
    validate_changed_path_coverage,
    validate_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
TRACEABILITY = (
    ROOT / "docs" / "product" / "external-knowledge" / "traceability-matrix.md"
)
KTRACE = DEFAULT_MANIFEST_DIR / "KTRACE-62.json"


def _manifest_document() -> dict[str, object]:
    return json.loads(KTRACE.read_text(encoding="utf-8"))


def _write_manifest(tmp_path: Path, document: dict[str, object]) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def test_repository_manifests_match_exact_traceability_ownership():
    manifests = validate_all_manifests()

    assert [manifest.slice_id for manifest in manifests] == ["KDOC-00", "KTRACE-62"]
    assert manifests[1].atomic_case_ids == ("KAC-FR-KOPS-035",)


def test_traceability_derives_the_ktrace_atomic_case_set():
    ownership = load_atomic_case_ownership(TRACEABILITY)

    assert ownership["KTRACE-62"] == ("KAC-FR-KOPS-035",)


def test_traceability_rejects_an_unknown_slice_owner(tmp_path: Path):
    traceability = tmp_path / "traceability.md"
    traceability.write_text(
        "| Requirement | Owning slice | Atomic case | Broader scenario cases | Evidence layer |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| KOPS-035 | KUNKNOWN-99 | KAC-FR-KOPS-035 | KAC-CI-001 | CI/source |\n",
        encoding="utf-8",
    )

    with pytest.raises(ManifestContractError) as error:
        load_atomic_case_ownership(traceability, known_slices=frozenset({"KTRACE-62"}))

    assert error.value.code == "trace_slice_unknown"


@pytest.mark.parametrize(
    ("case_ids", "expected_code"),
    [
        ([], "manifest_case_set_mismatch"),
        (["KAC-FR-KOPS-035", "KAC-FR-KOPS-034"], "manifest_case_set_mismatch"),
        (["KAC-FR-KOPS-034"], "manifest_case_set_mismatch"),
    ],
)
def test_manifest_rejects_missing_extra_and_differently_owned_cases(
    tmp_path: Path, case_ids: list[str], expected_code: str
):
    document = _manifest_document()
    document["atomic_case_ids"] = case_ids

    with pytest.raises(ManifestContractError) as error:
        validate_manifest(_write_manifest(tmp_path, document))

    assert error.value.code == expected_code


def test_manifest_rejects_duplicate_json_keys(tmp_path: Path):
    path = tmp_path / "manifest.json"
    path.write_text(
        '{"schema_version":"ai-platform.external-knowledge-slice-manifest.v1",'
        '"slice_id":"KTRACE-62","slice_id":"KADR-01"}',
        encoding="utf-8",
    )

    with pytest.raises(ManifestContractError) as error:
        validate_manifest(path)

    assert error.value.code == "manifest_duplicate_key"


def test_manifest_rejects_unknown_fields(tmp_path: Path):
    document = _manifest_document()
    document["delivery_status"] = "complete"

    with pytest.raises(ManifestContractError) as error:
        validate_manifest(_write_manifest(tmp_path, document))

    assert error.value.code == "manifest_schema_invalid"


def test_bootstrap_manifest_cannot_claim_product_code(tmp_path: Path):
    document = _manifest_document()
    document["changed_paths"] = [
        "app/knowledge/models.py",
        "docs/product/external-knowledge/manifests/KTRACE-62.json",
    ]

    with pytest.raises(ManifestContractError) as error:
        validate_manifest(_write_manifest(tmp_path, document))

    assert error.value.code == "bootstrap_path_claim_invalid"


def test_git_changed_paths_includes_deleted_files(monkeypatch: pytest.MonkeyPatch):
    observed: list[str] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        observed.extend(command)
        return subprocess.CompletedProcess(command, 0, stdout="app/knowledge/deleted.py\n")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert git_changed_paths(ROOT, "a" * 40, "b" * 40) == (
        "app/knowledge/deleted.py",
    )
    assert "--diff-filter=ACMRD" in observed


def test_changed_path_gate_requires_a_changed_manifest():
    with pytest.raises(ManifestContractError) as error:
        validate_changed_path_coverage(
            ("app/knowledge/models.py",), (), manifest_dir=DEFAULT_MANIFEST_DIR, root=ROOT
        )

    assert error.value.code == "changed_manifest_required"


def test_changed_path_gate_rejects_uncovered_knowledge_path():
    manifest_path = DEFAULT_MANIFEST_DIR / "KTRACE-62.json"
    manifest = ValidatedManifest(
        path=manifest_path,
        slice_id="KTRACE-62",
        atomic_case_ids=("KAC-FR-KOPS-035",),
        changed_paths=(manifest_path.relative_to(ROOT).as_posix(),),
    )
    changed = (
        manifest_path.relative_to(ROOT).as_posix(),
        "app/knowledge/models.py",
    )

    with pytest.raises(ManifestContractError) as error:
        validate_changed_path_coverage(
            changed, (manifest,), manifest_dir=DEFAULT_MANIFEST_DIR, root=ROOT
        )

    assert error.value.code == "knowledge_path_uncovered"


def test_changed_path_gate_accepts_exact_owned_coverage():
    manifest_path = DEFAULT_MANIFEST_DIR / "KTRACE-62.json"
    paths = tuple(
        sorted(
            (
                manifest_path.relative_to(ROOT).as_posix(),
                "app/knowledge/models.py",
            )
        )
    )
    manifest = ValidatedManifest(
        path=manifest_path,
        slice_id="KTRACE-62",
        atomic_case_ids=("KAC-FR-KOPS-035",),
        changed_paths=paths,
    )

    assert validate_changed_path_coverage(
        paths, (manifest,), manifest_dir=DEFAULT_MANIFEST_DIR, root=ROOT
    ) == ("KTRACE-62",)
