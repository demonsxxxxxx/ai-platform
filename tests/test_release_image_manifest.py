import base64
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
import pytest

from tools.release_image_manifest import (
    SCHEMA_VERSION,
    assemble_manifest,
    validate_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "release_image_manifest.py"
SCHEMA_PATH = ROOT / "schemas" / "release-image-manifest.v1.schema.json"
SOURCE_COMMIT = "a" * 40
MANIFEST_DIGEST = "sha256:" + "b" * 64
SBOM_DIGEST = "sha256:" + "c" * 64
SCAN_DIGEST = "sha256:" + "d" * 64
REPOSITORY = "https://github.com/demonsxxxxxx/ai-platform.git"
WORKFLOW_REPOSITORY = "demonsxxxxxx/ai-platform"
WORKFLOW_REF = (
    "demonsxxxxxx/ai-platform/.github/workflows/"
    "ai-platform-packaging-publish.yml@refs/heads/main"
)
SIGNATURE_IDENTITY = f"https://github.com/{WORKFLOW_REF}"
RUN_ID = "123456"
RUN_ATTEMPT = 2


def _artifact_name(role: str) -> str:
    return f"release-image-subject-{SOURCE_COMMIT}-{RUN_ID}-{RUN_ATTEMPT}-{role}"


def _subject(role: str) -> dict[str, object]:
    subject = f"ghcr.io/demonsxxxxxx/ai-platform-{role}"
    dockerfile = "Dockerfile" if role == "backend" else "frontend/web/Dockerfile"
    artifact = _artifact_name(role)
    return {
        "role": role,
        "platform": "linux/amd64",
        "build": {
            "context": {"path": ".", "source_commit": SOURCE_COMMIT},
            "dockerfile": {"path": dockerfile, "sha256": "e" * 64},
        },
        "image": {
            "subject": subject,
            "source_tag": f"{subject}:{SOURCE_COMMIT}",
            "manifest_digest": MANIFEST_DIGEST,
            "immutable_ref": f"{subject}@{MANIFEST_DIGEST}",
        },
        "evidence": {
            "sbom": {
                "format": "spdx-json",
                "ref": f"oci://{subject}@{MANIFEST_DIGEST}#sbom-spdx-attestation",
                "sha256": SBOM_DIGEST.removeprefix("sha256:"),
            },
            "provenance": {
                "predicate_type": "https://slsa.dev/provenance/v1",
                "attestation_id": f"attestation-{role}",
                "ref": f"https://github.com/{WORKFLOW_REPOSITORY}/attestations/attestation-{role}",
                "bundle_ref": f"github-artifact://{artifact}/provenance-{role}.bundle.json",
                "bundle_sha256": "1" * 64,
                "verification_ref": f"github-artifact://{artifact}/provenance-{role}.verified.json",
                "verification_sha256": "2" * 64,
            },
            "signature": {
                "identity": SIGNATURE_IDENTITY,
                "issuer": "https://token.actions.githubusercontent.com",
                "ref": f"oci://{subject}@{MANIFEST_DIGEST}#cosign-keyless-signature",
            },
            "scan": {
                "blocking_severities": ["HIGH", "CRITICAL"],
                "ref": f"github-artifact://{artifact}/trivy-{role}.json",
                "result": "passed",
                "scanner": "trivy@0.70.0",
                "sha256": SCAN_DIGEST.removeprefix("sha256:"),
            },
        },
    }


def _workflow() -> dict[str, object]:
    return {
        "repository": WORKFLOW_REPOSITORY,
        "workflow_ref": WORKFLOW_REF,
        "run_id": RUN_ID,
        "run_attempt": RUN_ATTEMPT,
        "head_sha": SOURCE_COMMIT,
    }


def _manifest() -> dict[str, object]:
    return assemble_manifest(
        source_commit=SOURCE_COMMIT,
        repository=REPOSITORY,
        workflow=_workflow(),
        subjects=[_subject("backend"), _subject("frontend")],
        expected_roles=["backend", "frontend"],
    )


def _verification(role: str) -> list[dict[str, object]]:
    return [
        {
            "attestation": {"bundle": {"mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json"}},
            "verificationResult": {
                "signature": {
                    "certificate": {
                        "subjectAlternativeName": SIGNATURE_IDENTITY,
                        "issuer": "https://token.actions.githubusercontent.com",
                        "runnerEnvironment": "github-hosted",
                        "sourceRepositoryURI": f"https://github.com/{WORKFLOW_REPOSITORY}",
                        "sourceRepositoryDigest": SOURCE_COMMIT,
                        "sourceRepositoryRef": "refs/heads/main",
                        "buildConfigURI": SIGNATURE_IDENTITY,
                        "runInvocationURI": (
                            f"https://github.com/{WORKFLOW_REPOSITORY}/actions/runs/"
                            f"{RUN_ID}/attempts/{RUN_ATTEMPT}"
                        ),
                    }
                },
                "verifiedTimestamps": [
                    {
                        "type": "TimestampAuthority",
                        "uri": "https://timestamp.github.com",
                        "timestamp": "2026-08-07T00:00:00Z",
                    }
                ],
                "statement": {
                    "subject": [
                        {
                            "name": f"ghcr.io/demonsxxxxxx/ai-platform-{role}",
                            "digest": {"sha256": MANIFEST_DIGEST.removeprefix("sha256:")},
                        }
                    ],
                    "predicateType": "https://slsa.dev/provenance/v1",
                    "predicate": {},
                },
            },
        }
    ]


def _write_evidence(root: Path, manifest: dict[str, object]) -> None:
    for subject in manifest["subjects"]:
        role = subject["role"]
        bundle = root / f"provenance-{role}.bundle.json"
        verified = root / f"provenance-{role}.verified.json"
        sbom = root / f"sbom-{role}.spdx.json"
        scan = root / f"trivy-{role}.json"
        statement = _verification(role)[0]["verificationResult"]["statement"]
        payload = base64.b64encode(json.dumps(statement).encode()).decode()
        bundle.write_text(
            json.dumps(
                {
                    "mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
                    "dsseEnvelope": {
                        "payloadType": "application/vnd.in-toto+json",
                        "payload": payload,
                        "signatures": [{"sig": "fixture-signature"}],
                    },
                    "verificationMaterial": {"timestampVerificationData": {}},
                }
            ),
            encoding="utf-8",
        )
        verified.write_text(json.dumps(_verification(role)), encoding="utf-8")
        sbom.write_text(
            json.dumps(
                {
                    "spdxVersion": "SPDX-2.3",
                    "SPDXID": "SPDXRef-DOCUMENT",
                    "name": subject["image"]["immutable_ref"],
                }
            ),
            encoding="utf-8",
        )
        scan.write_text(
            json.dumps(
                {
                    "SchemaVersion": 2,
                    "ArtifactName": subject["image"]["immutable_ref"],
                    "ArtifactType": "container_image",
                    "Results": [],
                }
            ),
            encoding="utf-8",
        )
        provenance = subject["evidence"]["provenance"]
        provenance["bundle_sha256"] = hashlib.sha256(bundle.read_bytes()).hexdigest()
        provenance["verification_sha256"] = hashlib.sha256(verified.read_bytes()).hexdigest()
        subject["evidence"]["sbom"]["sha256"] = hashlib.sha256(sbom.read_bytes()).hexdigest()
        subject["evidence"]["scan"]["sha256"] = hashlib.sha256(scan.read_bytes()).hexdigest()


def _schema() -> dict[str, object]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )


def test_manifest_accepts_complete_digest_bound_evidence(tmp_path: Path):
    manifest = _manifest()
    _write_evidence(tmp_path, manifest)

    validate_manifest(
        manifest,
        expected_roles=["backend", "frontend"],
        evidence_root=tmp_path,
    )

    assert manifest["schema_version"] == SCHEMA_VERSION
    assert [subject["role"] for subject in manifest["subjects"]] == ["backend", "frontend"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["subjects"][0]["image"].update({"manifest_digest": "sha256:123"}), "manifest_digest"),
        (lambda value: value["subjects"][0]["image"].update({"immutable_ref": "ghcr.io/demonsxxxxxx/ai-platform-backend:" + SOURCE_COMMIT}), "immutable_ref"),
        (lambda value: value["subjects"][0]["image"].update({"source_tag": "ghcr.io/demonsxxxxxx/ai-platform-backend:latest"}), "source_tag"),
        (lambda value: value["subjects"][0]["image"].update({"subject": "ghcr.io/other/repository"}), "image_subject"),
        (lambda value: value["subjects"][0]["image"].update({"manifest_digest": "sha256:" + "f" * 64}), "immutable_ref"),
        (lambda value: value["subjects"][0]["build"]["context"].update({"source_commit": "f" * 40}), "context_source_commit"),
        (lambda value: value["workflow"].update({"head_sha": "f" * 40}), "workflow_head_sha"),
        (lambda value: value.update({"source_commit": "sha256:" + "f" * 64}), "source_commit"),
        (lambda value: value["subjects"][0].update({"platform": "linux/arm64"}), "platform"),
        (lambda value: value["subjects"][0]["evidence"].pop("sbom"), "evidence_keys"),
        (lambda value: value["subjects"][0]["evidence"].pop("provenance"), "evidence_keys"),
        (lambda value: value["subjects"][0]["evidence"].pop("signature"), "evidence_keys"),
        (lambda value: value["subjects"][0]["evidence"].pop("scan"), "evidence_keys"),
        (lambda value: value["subjects"][0]["evidence"]["scan"].update({"result": "failed"}), "scan_result"),
        (lambda value: value["subjects"][0]["evidence"]["scan"].update({"blocking_severities": []}), "blocking_severities"),
        (lambda value: value["subjects"][0]["evidence"]["sbom"].update({"format": "text"}), "sbom_format"),
        (lambda value: value["subjects"][0]["evidence"]["sbom"].update({"ref": "sha256:" + "1" * 64}), "sbom_ref"),
        (lambda value: value["subjects"][0]["evidence"]["provenance"].update({"ref": "file://provenance.json"}), "provenance_ref"),
        (lambda value: value["subjects"][0]["evidence"]["signature"].update({"ref": "oci://ghcr.io/demonsxxxxxx/ai-platform-backend:" + SOURCE_COMMIT}), "signature_ref"),
        (lambda value: value["subjects"][0]["evidence"]["scan"].update({"ref": "sha256:" + "2" * 64}), "scan_ref"),
    ],
)
def test_manifest_rejects_unready_or_mismatched_subjects(mutation, message: str):
    manifest = _manifest()
    mutation(manifest)

    with pytest.raises(ValueError, match=message):
        validate_manifest(manifest, expected_roles=["backend", "frontend"])


def test_manifest_v1_cardinality_cannot_be_weakened_by_expected_roles():
    backend_only = _manifest()
    backend_only["subjects"] = [backend_only["subjects"][0]]
    with pytest.raises(ValueError, match="expected_roles"):
        validate_manifest(backend_only, expected_roles=["backend"])

    frontend_only = _manifest()
    frontend_only["subjects"] = [frontend_only["subjects"][1]]
    with pytest.raises(ValueError, match="expected_roles"):
        validate_manifest(frontend_only, expected_roles=["frontend"])

    duplicate = _manifest()
    duplicate["subjects"] = [duplicate["subjects"][0], copy.deepcopy(duplicate["subjects"][0])]
    with pytest.raises(ValueError, match="subject_roles"):
        validate_manifest(duplicate, expected_roles=["backend", "frontend"])

    with pytest.raises(ValueError, match="expected_roles"):
        validate_manifest(_manifest(), expected_roles=["backend", "backend"])


@pytest.mark.parametrize("command", ["assemble", "verify"])
@pytest.mark.parametrize("case", ["backend-only", "frontend-only", "duplicate", "mismatched"])
def test_cli_assemble_and_verify_reject_noncanonical_role_multisets(
    tmp_path: Path,
    command: str,
    case: str,
):
    manifest = _manifest()
    if case == "backend-only":
        manifest["subjects"] = [manifest["subjects"][0]]
        expected = ["backend", "frontend"]
    elif case == "frontend-only":
        manifest["subjects"] = [manifest["subjects"][1]]
        expected = ["backend", "frontend"]
    elif case == "duplicate":
        manifest["subjects"] = [manifest["subjects"][0], copy.deepcopy(manifest["subjects"][0])]
        expected = ["backend", "frontend"]
    else:
        expected = ["backend", "backend"]

    expected_args = [item for role in expected for item in ("--expected-role", role)]
    if command == "verify":
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        result = _run_cli(
            "verify",
            "--manifest",
            str(manifest_path),
            "--evidence-root",
            str(tmp_path),
            *expected_args,
        )
    else:
        records = []
        for index, subject in enumerate(manifest["subjects"]):
            record = tmp_path / f"subject-{index}.json"
            record.write_text(json.dumps(subject), encoding="utf-8")
            records.extend(("--subject-record", str(record)))
        result = _run_cli(
            "assemble",
            "--source-commit",
            SOURCE_COMMIT,
            "--repository",
            REPOSITORY,
            "--workflow-repository",
            WORKFLOW_REPOSITORY,
            "--workflow-ref",
            WORKFLOW_REF,
            "--run-id",
            RUN_ID,
            "--run-attempt",
            str(RUN_ATTEMPT),
            "--evidence-root",
            str(tmp_path),
            *records,
            *expected_args,
            "--output",
            str(tmp_path / "output.json"),
        )

    assert result.returncode != 0
    expected_error = "expected_roles" if case == "mismatched" else "subject_roles"
    assert expected_error in result.stderr


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda evidence: evidence[0]["verificationResult"]["statement"]["subject"][0]["digest"].update({"sha256": "f" * 64}), "provenance_subject_digest"),
        (lambda evidence: evidence[0]["verificationResult"]["signature"]["certificate"].update({"sourceRepositoryDigest": "f" * 40}), "provenance_source_commit"),
        (lambda evidence: evidence[0]["verificationResult"]["signature"]["certificate"].update({"sourceRepositoryURI": "https://github.com/other/repo"}), "provenance_repository"),
        (lambda evidence: evidence[0]["verificationResult"]["signature"]["certificate"].update({"buildConfigURI": "https://github.com/other/repo/.github/workflows/publish.yml@refs/heads/main"}), "provenance_workflow_ref"),
        (lambda evidence: evidence[0]["verificationResult"]["signature"]["certificate"].update({"sourceRepositoryRef": "refs/heads/release"}), "provenance_source_ref"),
        (lambda evidence: evidence[0]["verificationResult"]["signature"]["certificate"].update({"runInvocationURI": f"https://github.com/{WORKFLOW_REPOSITORY}/actions/runs/999/attempts/{RUN_ATTEMPT}"}), "provenance_run_identity"),
        (lambda evidence: evidence[0]["verificationResult"]["signature"]["certificate"].update({"runInvocationURI": f"https://github.com/{WORKFLOW_REPOSITORY}/actions/runs/{RUN_ID}/attempts/99"}), "provenance_run_identity"),
    ],
)
def test_provenance_verification_rejects_subject_source_and_run_mismatch(
    tmp_path: Path,
    mutation,
    message: str,
):
    manifest = _manifest()
    _write_evidence(tmp_path, manifest)
    evidence_path = tmp_path / "provenance-backend.verified.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    mutation(evidence)
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    manifest["subjects"][0]["evidence"]["provenance"]["verification_sha256"] = hashlib.sha256(
        evidence_path.read_bytes()
    ).hexdigest()

    with pytest.raises(ValueError, match=message):
        validate_manifest(manifest, evidence_root=tmp_path)


def test_provenance_verification_rejects_fake_url_stale_hash_and_missing_file(tmp_path: Path):
    manifest = _manifest()
    _write_evidence(tmp_path, manifest)

    fake_url = copy.deepcopy(manifest)
    fake_url["subjects"][0]["evidence"]["provenance"]["ref"] = (
        f"https://github.com/{WORKFLOW_REPOSITORY}/attestations/fake-but-valid"
    )
    with pytest.raises(ValueError, match="provenance_ref"):
        validate_manifest(fake_url, evidence_root=tmp_path)

    stale_hash = copy.deepcopy(manifest)
    stale_hash["subjects"][0]["evidence"]["provenance"]["verification_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="provenance_verification_sha256"):
        validate_manifest(stale_hash, evidence_root=tmp_path)

    (tmp_path / "provenance-backend.verified.json").unlink()
    with pytest.raises(ValueError, match="provenance_verification_missing"):
        validate_manifest(manifest, evidence_root=tmp_path)


def test_provenance_bundle_must_match_verified_statement(tmp_path: Path):
    manifest = _manifest()
    _write_evidence(tmp_path, manifest)
    bundle_path = tmp_path / "provenance-backend.bundle.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    statement = json.loads(base64.b64decode(bundle["dsseEnvelope"]["payload"]))
    statement["subject"][0]["digest"]["sha256"] = "f" * 64
    bundle["dsseEnvelope"]["payload"] = base64.b64encode(
        json.dumps(statement).encode()
    ).decode()
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    manifest["subjects"][0]["evidence"]["provenance"]["bundle_sha256"] = hashlib.sha256(
        bundle_path.read_bytes()
    ).hexdigest()

    with pytest.raises(ValueError, match="provenance_bundle_statement"):
        validate_manifest(manifest, evidence_root=tmp_path)


def test_provenance_verified_timestamp_requires_complete_trusted_result(tmp_path: Path):
    manifest = _manifest()
    _write_evidence(tmp_path, manifest)
    verified_path = tmp_path / "provenance-backend.verified.json"
    verified = json.loads(verified_path.read_text(encoding="utf-8"))
    verified[0]["verificationResult"]["verifiedTimestamps"] = [{}]
    verified_path.write_text(json.dumps(verified), encoding="utf-8")
    manifest["subjects"][0]["evidence"]["provenance"]["verification_sha256"] = hashlib.sha256(
        verified_path.read_bytes()
    ).hexdigest()

    with pytest.raises(ValueError, match="provenance_verified_timestamps"):
        validate_manifest(manifest, evidence_root=tmp_path)


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("missing-sbom", "sbom_missing"),
        ("stale-sbom", "sbom_sha256"),
        ("invalid-sbom", "sbom_document"),
        ("missing-scan", "scan_missing"),
        ("stale-scan", "scan_sha256"),
        ("wrong-scan-subject", "scan_subject"),
        ("blocking-vulnerability", "scan_blocking_vulnerability"),
    ],
)
def test_auxiliary_evidence_files_fail_closed(tmp_path: Path, case: str, message: str):
    manifest = _manifest()
    _write_evidence(tmp_path, manifest)
    sbom_path = tmp_path / "sbom-backend.spdx.json"
    scan_path = tmp_path / "trivy-backend.json"
    if case == "missing-sbom":
        sbom_path.unlink()
    elif case == "stale-sbom":
        sbom_path.write_text("{}", encoding="utf-8")
    elif case == "invalid-sbom":
        sbom_path.write_text("{}", encoding="utf-8")
        manifest["subjects"][0]["evidence"]["sbom"]["sha256"] = hashlib.sha256(
            sbom_path.read_bytes()
        ).hexdigest()
    elif case == "missing-scan":
        scan_path.unlink()
    elif case == "stale-scan":
        scan_path.write_text("{}", encoding="utf-8")
    else:
        scan = json.loads(scan_path.read_text(encoding="utf-8"))
        if case == "wrong-scan-subject":
            scan["ArtifactName"] = "ghcr.io/demonsxxxxxx/ai-platform-backend:latest"
        else:
            scan["Results"] = [
                {
                    "Target": "fixture",
                    "Vulnerabilities": [{"VulnerabilityID": "CVE-TEST", "Severity": "CRITICAL"}],
                }
            ]
        scan_path.write_text(json.dumps(scan), encoding="utf-8")
        manifest["subjects"][0]["evidence"]["scan"]["sha256"] = hashlib.sha256(
            scan_path.read_bytes()
        ).hexdigest()

    with pytest.raises(ValueError, match=message):
        validate_manifest(manifest, evidence_root=tmp_path)


def test_manifest_rejects_unknown_fields():
    manifest = _manifest()
    manifest["subjects"][0]["image"]["local_image_id"] = "sha256:" + "f" * 64

    with pytest.raises(ValueError, match="image_keys"):
        validate_manifest(manifest, expected_roles=["backend", "frontend"])


def test_json_schema_rejects_cross_role_combinations():
    validator = Draft202012Validator(_schema())
    validator.validate(_manifest())

    mutations = [
        lambda value: value["subjects"][0]["build"]["dockerfile"].update({"path": "frontend/web/Dockerfile"}),
        lambda value: value["subjects"][0]["image"].update({"subject": "ghcr.io/demonsxxxxxx/ai-platform-frontend"}),
        lambda value: value["subjects"][0]["image"].update({"source_tag": f"ghcr.io/demonsxxxxxx/ai-platform-frontend:{SOURCE_COMMIT}"}),
        lambda value: value["subjects"][0]["image"].update({"immutable_ref": f"ghcr.io/demonsxxxxxx/ai-platform-frontend@{MANIFEST_DIGEST}"}),
        lambda value: value["subjects"][0]["evidence"]["sbom"].update({"ref": f"oci://ghcr.io/demonsxxxxxx/ai-platform-frontend@{MANIFEST_DIGEST}#sbom-spdx-attestation"}),
        lambda value: value["subjects"][0]["evidence"]["provenance"].update({"bundle_ref": f"github-artifact://{_artifact_name('frontend')}/provenance-frontend.bundle.json"}),
        lambda value: value["subjects"][0]["evidence"]["provenance"].update({"verification_ref": f"github-artifact://{_artifact_name('frontend')}/provenance-frontend.verified.json"}),
        lambda value: value["subjects"][0]["evidence"]["signature"].update({"ref": f"oci://ghcr.io/demonsxxxxxx/ai-platform-frontend@{MANIFEST_DIGEST}#cosign-keyless-signature"}),
        lambda value: value["subjects"][0]["evidence"]["scan"].update({"ref": f"github-artifact://{_artifact_name('frontend')}/trivy-frontend.json"}),
    ]
    for mutation in mutations:
        manifest = _manifest()
        mutation(manifest)
        with pytest.raises(ValidationError):
            validator.validate(manifest)


def test_schema_defers_cross_field_equality_to_semantic_verifier():
    validator = Draft202012Validator(_schema())
    manifest = _manifest()
    manifest["workflow"]["head_sha"] = "f" * 40

    validator.validate(manifest)
    with pytest.raises(ValueError, match="workflow_head_sha"):
        validate_manifest(manifest)


def test_schema_file_is_the_strict_machine_readable_shape_contract():
    schema = _schema()

    assert schema["$id"] == "https://github.com/demonsxxxxxx/ai-platform/schemas/release-image-manifest.v1.schema.json"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == SCHEMA_VERSION
    assert schema["properties"]["subjects"]["items"]["additionalProperties"] is False
    assert "semantic verifier" in schema["$comment"]
