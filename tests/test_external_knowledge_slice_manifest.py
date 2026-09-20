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
    by_slice = {manifest.slice_id: manifest for manifest in manifests}

    assert set(by_slice) == {
        "KACL-23",
        "KACLDM-05",
        "KADMIN-24",
        "KADR-01",
        "KBUILD-29",
        "KCIT-41",
        "KCON-20",
        "KDBACL-12",
        "KDBAGT-13",
        "KDBCIT-17",
        "KDBCON-09",
        "KDBATT-15",
        "KDBEVD-16",
        "KDBRUN-14",
        "KDBSRC-11",
        "KDBSYNC-10",
        "KDOC-00",
        "KDOM-03",
        "KENG-40",
        "KFUSE-38",
        "KMARKET-33",
        "KNORM-07",
        "KNORMAPP-37",
        "KOUTCOME-39",
        "KPROF-28",
        "KPROFDM-06",
        "KPRVCAT-18",
        "KPRVRET-19",
        "KPUB-32",
        "KREADY-46",
        "KRETRY-47",
        "KREXEC-36",
        "KSNAP-35",
        "KSOURCE-22",
        "KSRCUI-25",
        "KSYNC-21",
        "KTRACE-62",
    }
    assert by_slice["KTRACE-62"].atomic_case_ids == ("KAC-FR-KOPS-035",)
    assert by_slice["KSNAP-35"].atomic_case_ids == tuple(
        f"KAC-FR-KADM-{index:03d}" for index in range(18, 27)
    )


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
        return subprocess.CompletedProcess(
            command, 0, stdout="app/knowledge/deleted.py\n"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert git_changed_paths(ROOT, "a" * 40, "b" * 40) == ("app/knowledge/deleted.py",)
    assert "--diff-filter=ACMRD" in observed


def test_changed_path_gate_requires_a_changed_manifest():
    with pytest.raises(ManifestContractError) as error:
        validate_changed_path_coverage(
            ("app/knowledge/models.py",),
            (),
            manifest_dir=DEFAULT_MANIFEST_DIR,
            root=ROOT,
        )

    assert error.value.code == "changed_manifest_required"


@pytest.mark.parametrize(
    "path",
    (
        "app/agent_apps/api.py",
        "app/bootstrap/knowledge.py",
        "app/conversations/application/run_admission.py",
        "app/executors/claude_agent_worker.py",
        "app/settings.py",
        "app/worker.py",
        "deploy/ai-platform/docker-compose.yml",
        "frontend/web/src/features/agent-builder/AgentBuilderRoute.tsx",
        "frontend/web/src/features/agent-market/AgentMarketRoute.tsx",
        "frontend/web/src/components/layout/AppContent/__tests__/ChatAppContent.test.tsx",
        "tests/test_schema_migrations.py",
        "tests/test_settings.py",
    ),
)
def test_changed_path_gate_recognizes_cross_layer_knowledge_paths(path: str):
    with pytest.raises(ManifestContractError) as error:
        validate_changed_path_coverage(
            (path,), (), manifest_dir=DEFAULT_MANIFEST_DIR, root=ROOT
        )

    assert error.value.code == "changed_manifest_required"


def test_changed_path_gate_recognizes_manifest_claimed_shared_path():
    manifest_path = DEFAULT_MANIFEST_DIR / "KMARKET-33.json"
    shared_path = "frontend/web/src/shared/agentProfile.ts"
    manifest = ValidatedManifest(
        path=manifest_path,
        slice_id="KMARKET-33",
        atomic_case_ids=(),
        changed_paths=(shared_path,),
    )

    with pytest.raises(ManifestContractError) as error:
        validate_changed_path_coverage(
            (shared_path,), (manifest,), manifest_dir=DEFAULT_MANIFEST_DIR, root=ROOT
        )

    assert error.value.code == "changed_manifest_required"


def test_changed_manifest_requires_complete_supplied_path_coverage():
    manifest_path = DEFAULT_MANIFEST_DIR / "KMARKET-33.json"
    manifest_relative = manifest_path.relative_to(ROOT).as_posix()
    manifest = ValidatedManifest(
        path=manifest_path,
        slice_id="KMARKET-33",
        atomic_case_ids=(),
        changed_paths=(manifest_relative,),
    )

    with pytest.raises(ManifestContractError) as error:
        validate_changed_path_coverage(
            (manifest_relative, "app/shared_candidate_path.py"),
            (manifest,),
            manifest_dir=DEFAULT_MANIFEST_DIR,
            root=ROOT,
        )

    assert error.value.code == "knowledge_path_uncovered"
    assert error.value.detail == "app/shared_candidate_path.py"


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
