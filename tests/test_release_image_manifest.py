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
    _validate_spdx_graph,
    _validate_spdx_image_binding,
    assemble_manifest,
    validate_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "release_image_manifest.py"
SCHEMA_PATH = ROOT / "schemas" / "release-image-manifest.v1.schema.json"
SOURCE_COMMIT = "a" * 40
MANIFEST_DIGEST = "sha256:" + "b" * 64
FAILED_RUN_MANIFEST_DIGEST = (
    "sha256:68acd3cbed541d9551061bfb7cf92e83ae69e021118a0693aeb7e8832a5c330c"
)
FAILED_RUN_PRODUCER_DIGEST = (
    "sha256:973f2d3bed36feb5625f8a5f31cf0c7277c37d097654207e57b2fad46adfeac0"
)
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
SBOM_NAMESPACE_PLACEHOLDER = (
    f"https://github.com/{WORKFLOW_REPOSITORY}/sbom/content-pending"
)
SBOM_BINDING_ANNOTATOR = "Tool: ai-platform-release-image-manifest"
SBOM_BINDING_COMMENT_PREFIX = "ai-platform.sbom-binding.v1:"
REAL_SYFT_1_50_BACKEND_SELF_DEPENDENCY_IDS = (
    "SPDXRef-Package-python-contourpy-81597264a63d721b",
    "SPDXRef-Package-python-contourpy-b851cae05db6c3f4",
)


def _artifact_name(role: str) -> str:
    return f"release-image-subject-{SOURCE_COMMIT}-{RUN_ID}-{RUN_ATTEMPT}-{role}"


def _sbom_namespace(role: str, document: dict[str, object]) -> str:
    normalized = copy.deepcopy(document)
    normalized["documentNamespace"] = SBOM_NAMESPACE_PLACEHOLDER
    content_sha256 = hashlib.sha256(
        json.dumps(
            normalized,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return (
        f"https://github.com/{WORKFLOW_REPOSITORY}/actions/runs/{RUN_ID}/attempts/"
        f"{RUN_ATTEMPT}/sbom/{role}/{SOURCE_COMMIT}/sha256/"
        f"{MANIFEST_DIGEST.removeprefix('sha256:')}/{content_sha256}"
    )


def _canonical_json_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _syft_spdx(role: str) -> dict[str, object]:
    subject = f"ghcr.io/demonsxxxxxx/ai-platform-{role}"
    root_id = f"SPDXRef-DocumentRoot-Image-ghcr.io-demonsxxxxxx-ai-platform-{role}"
    dependency_id = f"SPDXRef-Package-apk-busybox-{role}"
    file_id = f"SPDXRef-File-bin-busybox-{role}"
    digest_hex = MANIFEST_DIGEST.removeprefix("sha256:")
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": subject,
        "documentNamespace": f"https://anchore.com/syft/image/generated-{role}",
        "creationInfo": {
            "created": "2026-08-07T00:00:00Z",
            "creators": ["Tool: syft-1.50.0"],
            "licenseListVersion": "3.27.0",
        },
        "packages": [
            {
                "name": subject,
                "SPDXID": root_id,
                "versionInfo": MANIFEST_DIGEST,
                "supplier": "NOASSERTION",
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "checksums": [{"algorithm": "SHA256", "checksumValue": digest_hex}],
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": (
                            f"pkg:oci/ghcr.io%2Fdemonsxxxxxx%2Fai-platform-{role}"
                            f"@sha256%3A{digest_hex}?arch="
                        ),
                    }
                ],
                "primaryPackagePurpose": "CONTAINER",
            },
            {
                "name": "busybox",
                "SPDXID": dependency_id,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "GPL-2.0-only",
                "copyrightText": "NOASSERTION",
            },
        ],
        "files": [
            {
                "fileName": "/bin/busybox",
                "SPDXID": file_id,
                "checksums": [{"algorithm": "SHA256", "checksumValue": "1" * 64}],
                "licenseConcluded": "NOASSERTION",
                "licenseInfoInFiles": ["NOASSERTION"],
                "copyrightText": "NOASSERTION",
            }
        ],
        "relationships": [
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relatedSpdxElement": root_id,
                "relationshipType": "DESCRIBES",
            },
            {
                "spdxElementId": root_id,
                "relatedSpdxElement": dependency_id,
                "relationshipType": "CONTAINS",
            },
            {
                "spdxElementId": dependency_id,
                "relatedSpdxElement": file_id,
                "relationshipType": "CONTAINS",
            },
        ],
    }


def _captured_syft_1_50_backend_self_dependency_graph() -> dict[str, object]:
    """Minimal graph extracted from Syft 1.50.0's backend digest output."""
    document = _syft_spdx("backend")
    for package_id in REAL_SYFT_1_50_BACKEND_SELF_DEPENDENCY_IDS:
        document["packages"].append(
            {
                "name": "contourpy",
                "SPDXID": package_id,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
            }
        )
        document["relationships"].append(
            {
                "spdxElementId": package_id,
                "relatedSpdxElement": package_id,
                "relationshipType": "DEPENDENCY_OF",
            }
        )
    return document


def _syft_directory_spdx(role: str) -> dict[str, object]:
    root_id = "SPDXRef-DocumentRoot-Directory-workspace"
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "C:/workspace",
        "documentNamespace": "https://anchore.com/syft/dir/generated-workspace",
        "creationInfo": {
            "created": "2026-08-07T00:00:00Z",
            "creators": ["Tool: syft-1.50.0"],
        },
        "packages": [
            {
                "name": "C:/workspace",
                "SPDXID": root_id,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "primaryPackagePurpose": "FILE",
            }
        ],
        "relationships": [
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relatedSpdxElement": root_id,
                "relationshipType": "DESCRIBES",
            }
        ],
    }


def _bound_spdx(role: str) -> dict[str, object]:
    document = _syft_spdx(role)
    unbound_content_sha256 = _canonical_json_sha256(document)
    root_id = document["relationships"][0]["relatedSpdxElement"]
    binding = {
        "annotations_present": False,
        "original_namespace": document["documentNamespace"],
        "unbound_content_sha256": unbound_content_sha256,
    }
    document["annotations"] = [
        {
            "annotationDate": document["creationInfo"]["created"],
            "annotationType": "OTHER",
            "annotator": SBOM_BINDING_ANNOTATOR,
            "comment": SBOM_BINDING_COMMENT_PREFIX
            + json.dumps(
                binding,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
        }
    ]
    document["documentDescribes"] = [root_id]
    document["documentNamespace"] = _sbom_namespace(role, document)
    return document


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
                "unbound_content_sha256": _canonical_json_sha256(_syft_spdx(role)),
            },
            "provenance": {
                "predicate_type": "https://slsa.dev/provenance/v1",
                "attestation_id": f"attestation-{role}",
                "ref": f"https://github.com/{WORKFLOW_REPOSITORY}/attestations/attestation-{role}",
                "bundle_ref": f"github-artifact://{artifact}/provenance-{role}.bundle.json",
                "bundle_sha256": "1" * 64,
                "verification_ref": f"github-artifact://{artifact}/provenance-{role}.verified.json",
                "verification_sha256": "2" * 64,
                "reverification_ref": (
                    f"github-artifact://release-image-evidence-{SOURCE_COMMIT}-{RUN_ID}-"
                    f"{RUN_ATTEMPT}/provenance-{role}.assembly-verified.json"
                ),
                "reverification_sha256": "3" * 64,
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


def _bundle(role: str) -> dict[str, object]:
    statement = {
        "subject": [
            {
                "name": f"ghcr.io/demonsxxxxxx/ai-platform-{role}",
                "digest": {"sha256": MANIFEST_DIGEST.removeprefix("sha256:")},
            }
        ],
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {},
    }
    return {
        "mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
        "dsseEnvelope": {
            "payloadType": "application/vnd.in-toto+json",
            "payload": base64.b64encode(json.dumps(statement).encode()).decode(),
            "signatures": [{"sig": "fixture-signature"}],
        },
        "verificationMaterial": {"timestampVerificationData": {}},
    }


def _verification(
    role: str,
    bundle: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    return [
        {
            "attestation": {"bundle": bundle or _bundle(role)},
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
        reverified = root / f"provenance-{role}.assembly-verified.json"
        sbom = root / f"sbom-{role}.spdx.json"
        scan = root / f"trivy-{role}.json"
        bundle_payload = _bundle(role)
        verification_payload = _verification(role, bundle_payload)
        bundle.write_text(json.dumps(bundle_payload), encoding="utf-8")
        verified.write_text(json.dumps(verification_payload), encoding="utf-8")
        reverified.write_text(json.dumps(verification_payload), encoding="utf-8")
        sbom.write_text(json.dumps(_bound_spdx(role)), encoding="utf-8")
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
        provenance["reverification_sha256"] = hashlib.sha256(reverified.read_bytes()).hexdigest()
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


def _run_bind_spdx(
    path: Path,
    *,
    role: str = "backend",
    source_commit: str = SOURCE_COMMIT,
    manifest_digest: str = MANIFEST_DIGEST,
    run_id: str = RUN_ID,
    run_attempt: int = RUN_ATTEMPT,
    unbound_content_sha256: str | None = None,
    producer_digest: str | None = None,
) -> subprocess.CompletedProcess[str]:
    subject = f"ghcr.io/demonsxxxxxx/ai-platform-{role}"
    arguments = [
        "bind-spdx",
        "--role",
        role,
        "--source-commit",
        source_commit,
        "--manifest-digest",
        manifest_digest,
        "--image-ref",
        f"{subject}@{manifest_digest}",
        "--workflow-run-id",
        run_id,
        "--workflow-run-attempt",
        str(run_attempt),
        "--unbound-content-sha256",
        unbound_content_sha256 or _canonical_json_sha256(_syft_spdx(role)),
        "--sbom-file",
        str(path),
    ]
    arguments.extend(["--producer-digest", producer_digest or manifest_digest])
    return _run_cli(*arguments)


def _run_spdx_source_hash(
    path: Path,
    *,
    role: str = "backend",
    manifest_digest: str = MANIFEST_DIGEST,
    image_ref: str | None = None,
    failure_evidence_file: Path | None = None,
    producer_digest: str | None = None,
) -> subprocess.CompletedProcess[str]:
    subject = f"ghcr.io/demonsxxxxxx/ai-platform-{role}"
    arguments = [
        "spdx-source-hash",
        "--role",
        role,
        "--manifest-digest",
        manifest_digest,
        "--image-ref",
        image_ref or f"{subject}@{manifest_digest}",
        "--sbom-file",
        str(path),
    ]
    if failure_evidence_file is not None:
        arguments.extend(["--failure-evidence-file", str(failure_evidence_file)])
    arguments.extend(["--producer-digest", producer_digest or manifest_digest])
    return _run_cli(*arguments)


def _hosted_linux_syft_1_50_backend_spdx() -> dict[str, object]:
    """Minimal SPDX root captured from the failed Linux Syft 1.50.0 invocation."""
    digest = "sha256:77335e0179abc8286fe3cc637cabb5d78e96595bd66812ab9de7488fa514f3e0"
    subject = "ghcr.io/demonsxxxxxx/ai-platform-backend"
    document = _syft_spdx("backend")
    root = document["packages"][0]
    document["name"] = subject
    document["documentNamespace"] = (
        "https://anchore.com/syft/image/ghcr.io/demonsxxxxxx/"
        "ai-platform-backend-linux-amd64"
    )
    root["name"] = subject
    root["versionInfo"] = digest
    root["checksums"] = [{"algorithm": "SHA256", "checksumValue": digest.removeprefix("sha256:")}]
    root["externalRefs"] = [
        {
            "referenceCategory": "PACKAGE-MANAGER",
            "referenceType": "purl",
            "referenceLocator": (
                "pkg:oci/ghcr.io%2Fdemonsxxxxxx%2Fai-platform-backend@sha256%3A"
                f"{digest.removeprefix('sha256:')}?arch=amd64"
            ),
        }
    ]
    return document


def _failed_run_syft_1_50_backend_spdx() -> dict[str, object]:
    """Minimal root captured from run 31214118574's retained SPDX artifact."""
    digest = FAILED_RUN_MANIFEST_DIGEST
    producer_manifest = FAILED_RUN_PRODUCER_DIGEST.removeprefix("sha256:")
    subject = "ghcr.io/demonsxxxxxx/ai-platform-backend"
    document = _syft_spdx("backend")
    root = document["packages"][0]
    document["name"] = subject
    document["documentNamespace"] = (
        "https://anchore.com/syft/image/ghcr.io/demonsxxxxxx/"
        "ai-platform-backend-8fd9e909-e204-4747-b3a5-5f45f28674a6"
    )
    root["name"] = subject
    root["versionInfo"] = digest
    root["checksums"] = [{"algorithm": "SHA256", "checksumValue": producer_manifest}]
    root["externalRefs"] = [
        {
            "referenceCategory": "PACKAGE-MANAGER",
            "referenceType": "purl",
            "referenceLocator": (
                "pkg:oci/ghcr.io%2Fdemonsxxxxxx%2Fai-platform-backend@sha256%3A"
                f"{producer_manifest}?arch=amd64"
            ),
        }
    ]
    return document


def _oci_index_bytes(*, child_digest: str = FAILED_RUN_PRODUCER_DIGEST) -> bytes:
    return json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": child_digest,
                    "size": 123,
                    "platform": {"architecture": "amd64", "os": "linux"},
                }
            ],
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _run_resolve_producer_digest(
    tmp_path: Path,
    raw_document: bytes,
    *,
    requested_digest: str | None = None,
) -> subprocess.CompletedProcess[str]:
    raw_path = tmp_path / "oci-backend.json"
    raw_path.write_bytes(raw_document)
    requested = requested_digest or ("sha256:" + hashlib.sha256(raw_document).hexdigest())
    return _run_cli(
        "resolve-producer-digest",
        "--role",
        "backend",
        "--manifest-digest",
        requested,
        "--image-ref",
        f"ghcr.io/demonsxxxxxx/ai-platform-backend@{requested}",
        "--oci-file",
        str(raw_path),
    )


def _rewrite_bound_sbom(
    tmp_path: Path,
    manifest: dict[str, object],
    mutation,
) -> None:
    sbom_path = tmp_path / "sbom-backend.spdx.json"
    document = json.loads(sbom_path.read_text(encoding="utf-8"))
    mutation(document)
    document["documentNamespace"] = _sbom_namespace("backend", document)
    sbom_path.write_text(json.dumps(document), encoding="utf-8")
    manifest["subjects"][0]["evidence"]["sbom"]["sha256"] = hashlib.sha256(
        sbom_path.read_bytes()
    ).hexdigest()


def _duplicate_json_member(path: Path, member: str) -> None:
    payload = path.read_text(encoding="utf-8")
    assert member in payload
    path.write_text(payload.replace(member, f"{member}, {member}", 1), encoding="utf-8")


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


def test_assemble_binds_fresh_provenance_reverification_to_ready_manifest(tmp_path: Path):
    source = _manifest()
    _write_evidence(tmp_path, source)
    records = copy.deepcopy(source["subjects"])
    for subject in records:
        provenance = subject["evidence"]["provenance"]
        provenance.pop("reverification_ref")
        provenance.pop("reverification_sha256")

    manifest = assemble_manifest(
        source_commit=SOURCE_COMMIT,
        repository=REPOSITORY,
        workflow=_workflow(),
        subjects=records,
        expected_roles=["backend", "frontend"],
        evidence_root=tmp_path,
    )

    for subject in manifest["subjects"]:
        role = subject["role"]
        provenance = subject["evidence"]["provenance"]
        assert provenance["reverification_ref"] == (
            f"github-artifact://release-image-evidence-{SOURCE_COMMIT}-{RUN_ID}-{RUN_ATTEMPT}/"
            f"provenance-{role}.assembly-verified.json"
        )
        assert provenance["reverification_sha256"] == hashlib.sha256(
            (tmp_path / f"provenance-{role}.assembly-verified.json").read_bytes()
        ).hexdigest()


def test_bind_spdx_cli_writes_deterministic_immutable_subject_namespace(tmp_path: Path):
    sbom_path = tmp_path / "sbom-backend.spdx.json"
    document = _syft_spdx("backend")
    document["documentNamespace"] = "https://anchore.com/syft/image/unstable-value"
    unbound_content_sha256 = _canonical_json_sha256(document)
    sbom_path.write_text(json.dumps(document), encoding="utf-8")

    result = _run_cli(
        "bind-spdx",
        "--role",
        "backend",
        "--source-commit",
        SOURCE_COMMIT,
        "--manifest-digest",
        MANIFEST_DIGEST,
        "--producer-digest",
        MANIFEST_DIGEST,
        "--image-ref",
        f"ghcr.io/demonsxxxxxx/ai-platform-backend@{MANIFEST_DIGEST}",
        "--workflow-run-id",
        RUN_ID,
        "--workflow-run-attempt",
        str(RUN_ATTEMPT),
        "--unbound-content-sha256",
        unbound_content_sha256,
        "--sbom-file",
        str(sbom_path),
    )

    assert result.returncode == 0, result.stderr
    document = json.loads(sbom_path.read_text(encoding="utf-8"))
    assert document["documentDescribes"] == [
        document["relationships"][0]["relatedSpdxElement"]
    ]
    assert document["documentNamespace"] == _sbom_namespace("backend", document)


def test_spdx_source_hash_accepts_hosted_linux_syft_amd64_root_purl(tmp_path: Path):
    sbom_path = tmp_path / "sbom-backend.spdx.json"
    digest = "sha256:77335e0179abc8286fe3cc637cabb5d78e96595bd66812ab9de7488fa514f3e0"
    sbom_path.write_text(json.dumps(_hosted_linux_syft_1_50_backend_spdx()), encoding="utf-8")

    result = _run_spdx_source_hash(sbom_path, manifest_digest=digest)

    assert result.returncode == 0, result.stderr


def test_spdx_source_hash_accepts_exact_failed_run_root_checksum_contour(tmp_path: Path):
    sbom_path = tmp_path / "sbom-backend.spdx.json"
    digest = "sha256:68acd3cbed541d9551061bfb7cf92e83ae69e021118a0693aeb7e8832a5c330c"
    sbom_path.write_text(json.dumps(_failed_run_syft_1_50_backend_spdx()), encoding="utf-8")

    result = _run_spdx_source_hash(
        sbom_path,
        manifest_digest=digest,
        producer_digest=FAILED_RUN_PRODUCER_DIGEST,
    )

    assert result.returncode == 0, result.stderr


def test_spdx_binding_rejects_coordinated_untrusted_root_checksum_and_purl(
    tmp_path: Path,
):
    """The SPDX checksum/PURL pair cannot self-authenticate a producer digest."""
    sbom_path = tmp_path / "sbom-backend.spdx.json"
    document = _failed_run_syft_1_50_backend_spdx()
    arbitrary_digest = "f" * 64
    document["packages"][0]["checksums"] = [
        {"algorithm": "SHA256", "checksumValue": arbitrary_digest}
    ]
    document["packages"][0]["externalRefs"][0]["referenceLocator"] = (
        "pkg:oci/ghcr.io%2Fdemonsxxxxxx%2Fai-platform-backend@sha256%3A"
        f"{arbitrary_digest}?arch=amd64"
    )
    sbom_path.write_text(json.dumps(document), encoding="utf-8")

    source_hash = _run_spdx_source_hash(
        sbom_path,
        manifest_digest=FAILED_RUN_MANIFEST_DIGEST,
        producer_digest=FAILED_RUN_PRODUCER_DIGEST,
    )

    assert source_hash.returncode != 0
    assert "sbom_subject_binding.root_checksums" in source_hash.stderr

    bind = _run_bind_spdx(
        sbom_path,
        manifest_digest=FAILED_RUN_MANIFEST_DIGEST,
        producer_digest=FAILED_RUN_PRODUCER_DIGEST,
        unbound_content_sha256="0" * 64,
    )
    assert bind.returncode != 0
    assert "sbom_subject_binding.root_checksums" in bind.stderr


def test_spdx_binding_requires_an_external_producer_digest_for_both_entry_points(
    tmp_path: Path,
):
    sbom_path = tmp_path / "sbom-backend.spdx.json"
    document = _failed_run_syft_1_50_backend_spdx()
    arbitrary_digest = "f" * 64
    document["packages"][0]["checksums"][0]["checksumValue"] = arbitrary_digest
    document["packages"][0]["externalRefs"][0]["referenceLocator"] = (
        "pkg:oci/ghcr.io%2Fdemonsxxxxxx%2Fai-platform-backend@sha256%3A"
        f"{arbitrary_digest}?arch=amd64"
    )
    sbom_path.write_text(json.dumps(document), encoding="utf-8")

    source_hash = _run_spdx_source_hash(
        sbom_path,
        manifest_digest=FAILED_RUN_MANIFEST_DIGEST,
    )
    bind = _run_bind_spdx(
        sbom_path,
        manifest_digest=FAILED_RUN_MANIFEST_DIGEST,
        unbound_content_sha256="0" * 64,
    )

    assert source_hash.returncode != 0
    assert bind.returncode != 0


def test_resolve_producer_digest_authenticates_raw_index_and_linux_amd64_child(
    tmp_path: Path,
):
    raw_path = tmp_path / "oci-backend.json"
    raw_path.write_bytes(_oci_index_bytes())
    requested_digest = "sha256:" + hashlib.sha256(raw_path.read_bytes()).hexdigest()

    result = _run_cli(
        "resolve-producer-digest",
        "--role",
        "backend",
        "--manifest-digest",
        requested_digest,
        "--image-ref",
        f"ghcr.io/demonsxxxxxx/ai-platform-backend@{requested_digest}",
        "--oci-file",
        str(raw_path),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == FAILED_RUN_PRODUCER_DIGEST


def test_resolve_producer_digest_ignores_a_valid_foreign_platform_variant(tmp_path: Path):
    document = json.loads(_oci_index_bytes())
    document["manifests"].insert(
        0,
        {
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "digest": "sha256:" + "1" * 64,
            "size": 1,
            "platform": {"architecture": "arm64", "os": "linux", "variant": "v8"},
        },
    )

    result = _run_resolve_producer_digest(
        tmp_path,
        json.dumps(document, separators=(",", ":")).encode("utf-8"),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == FAILED_RUN_PRODUCER_DIGEST


def test_failed_run_spdx_requires_the_authenticated_producer_digest(tmp_path: Path):
    sbom_path = tmp_path / "sbom-backend.spdx.json"
    sbom_path.write_text(json.dumps(_failed_run_syft_1_50_backend_spdx()), encoding="utf-8")

    accepted = _run_spdx_source_hash(
        sbom_path,
        manifest_digest=FAILED_RUN_MANIFEST_DIGEST,
        producer_digest=FAILED_RUN_PRODUCER_DIGEST,
    )
    rejected = _run_spdx_source_hash(
        sbom_path,
        manifest_digest=FAILED_RUN_MANIFEST_DIGEST,
        producer_digest=FAILED_RUN_MANIFEST_DIGEST,
    )

    assert accepted.returncode == 0, accepted.stderr
    assert rejected.returncode != 0
    assert "sbom_subject_binding.root_checksums" in rejected.stderr


@pytest.mark.parametrize(
    "document",
    [
        {"schemaVersion": 2, "mediaType": "application/example", "manifests": []},
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": FAILED_RUN_PRODUCER_DIGEST,
                    "platform": {"architecture": "amd64", "os": "linux"},
                }
            ],
        },
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": FAILED_RUN_PRODUCER_DIGEST,
                    "size": 1,
                    "platform": {"architecture": "arm64", "os": "linux"},
                }
            ],
        },
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": FAILED_RUN_PRODUCER_DIGEST,
                    "size": 1,
                    "annotations": {"org.opencontainers.image.architecture": "amd64"},
                }
            ],
        },
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": "sha256:" + "A" * 64,
                    "size": 1,
                    "platform": {"architecture": "amd64", "os": "linux"},
                }
            ],
        },
    ],
)
def test_resolve_producer_digest_rejects_closed_world_invalid_oci_contours(
    tmp_path: Path,
    document: dict[str, object],
):
    result = _run_resolve_producer_digest(
        tmp_path,
        json.dumps(document, separators=(",", ":")).encode("utf-8"),
    )

    assert result.returncode != 0


def test_resolve_producer_digest_rejects_tampering_duplicate_keys_and_duplicate_platforms(
    tmp_path: Path,
):
    raw = _oci_index_bytes()
    tampered = _run_resolve_producer_digest(
        tmp_path,
        raw,
        requested_digest="sha256:" + "0" * 64,
    )
    duplicate_keys = _run_resolve_producer_digest(
        tmp_path,
        b'{"schemaVersion":2,"schemaVersion":2,"mediaType":"application/vnd.oci.image.index.v1+json","manifests":[]}',
    )
    duplicate_document = json.loads(raw)
    duplicate_document["manifests"].append(copy.deepcopy(duplicate_document["manifests"][0]))
    duplicate_platform = _run_resolve_producer_digest(
        tmp_path,
        json.dumps(duplicate_document, separators=(",", ":")).encode("utf-8"),
    )

    assert tampered.returncode != 0
    assert duplicate_keys.returncode != 0
    assert "json_duplicate_key" in duplicate_keys.stderr
    assert duplicate_platform.returncode != 0


def test_resolve_producer_digest_accepts_an_authenticated_direct_image_manifest(tmp_path: Path):
    raw = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": "sha256:" + "1" * 64,
                "size": 1,
            },
            "layers": [],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    requested_digest = "sha256:" + hashlib.sha256(raw).hexdigest()

    result = _run_resolve_producer_digest(
        tmp_path,
        raw,
        requested_digest=requested_digest,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == requested_digest


def test_bind_spdx_preserves_exact_failed_run_checksum_contour_and_rejects_rebind(
    tmp_path: Path,
):
    sbom_path = tmp_path / "sbom-backend.spdx.json"
    digest = "sha256:68acd3cbed541d9551061bfb7cf92e83ae69e021118a0693aeb7e8832a5c330c"
    sbom_path.write_text(json.dumps(_failed_run_syft_1_50_backend_spdx()), encoding="utf-8")
    source_hash = _run_spdx_source_hash(
        sbom_path,
        manifest_digest=digest,
        producer_digest=FAILED_RUN_PRODUCER_DIGEST,
    )
    assert source_hash.returncode == 0, source_hash.stderr

    first = _run_bind_spdx(
        sbom_path,
        manifest_digest=digest,
        producer_digest=FAILED_RUN_PRODUCER_DIGEST,
        unbound_content_sha256=source_hash.stdout.strip(),
    )
    assert first.returncode == 0, first.stderr
    bound_bytes = sbom_path.read_bytes()

    repeated = _run_bind_spdx(
        sbom_path,
        manifest_digest=digest,
        producer_digest=FAILED_RUN_PRODUCER_DIGEST,
        unbound_content_sha256=source_hash.stdout.strip(),
    )
    assert repeated.returncode == 0, repeated.stderr
    assert sbom_path.read_bytes() == bound_bytes

    rebound = _run_bind_spdx(
        sbom_path,
        manifest_digest=digest,
        producer_digest=FAILED_RUN_PRODUCER_DIGEST,
        run_id="999999",
        unbound_content_sha256=source_hash.stdout.strip(),
    )
    assert rebound.returncode != 0
    assert "sbom_binding_state" in rebound.stderr


@pytest.mark.parametrize(
    "checksums",
    [
        [],
        [
            {
                "algorithm": "SHA256",
                "checksumValue": "973f2d3bed36feb5625f8a5f31cf0c7277c37d097654207e57b2fad46adfeac0",
            },
            {
                "algorithm": "SHA256",
                "checksumValue": "973f2d3bed36feb5625f8a5f31cf0c7277c37d097654207e57b2fad46adfeac0",
            },
        ],
        [{"algorithm": "SHA1", "checksumValue": "1" * 40}],
        [{"algorithm": "SHA256", "checksumValue": "f" * 63}],
        [{"algorithm": "SHA256", "checksumValue": "not-a-sha256"}],
        [{"algorithm": "SHA256", "checksumValue": "f" * 64, "extra": "forbidden"}],
    ],
)
def test_spdx_source_hash_rejects_malformed_root_checksum_contours(
    tmp_path: Path,
    checksums: list[dict[str, str]],
):
    sbom_path = tmp_path / "sbom-backend.spdx.json"
    document = _failed_run_syft_1_50_backend_spdx()
    document["packages"][0]["checksums"] = checksums
    sbom_path.write_text(json.dumps(document), encoding="utf-8")

    result = _run_spdx_source_hash(
        sbom_path,
        manifest_digest="sha256:68acd3cbed541d9551061bfb7cf92e83ae69e021118a0693aeb7e8832a5c330c",
    )

    assert result.returncode != 0
    assert "sbom_subject_binding.root_checksums" in result.stderr


@pytest.mark.parametrize(
    "mutation",
    [
        lambda root: root["checksums"][0].update({"checksumValue": "f" * 64}),
        lambda root: root["externalRefs"][0].update(
            {
                "referenceLocator": (
                    "pkg:oci/ghcr.io%2Fdemonsxxxxxx%2Fai-platform-frontend@sha256%3A"
                    "973f2d3bed36feb5625f8a5f31cf0c7277c37d097654207e57b2fad46adfeac0?arch=amd64"
                )
            }
        ),
        lambda root: root["externalRefs"][0].update(
            {
                "referenceLocator": (
                    "pkg:oci/ghcr.io%2Fdemonsxxxxxx%2Fai-platform-backend@sha256%3A"
                    "973f2d3bed36feb5625f8a5f31cf0c7277c37d097654207e57b2fad46adfeac0?arch=arm64"
                )
            }
        ),
    ],
)
def test_spdx_source_hash_requires_root_checksum_to_match_exact_purl_subject_and_arch(
    tmp_path: Path,
    mutation,
):
    sbom_path = tmp_path / "sbom-backend.spdx.json"
    document = _failed_run_syft_1_50_backend_spdx()
    mutation(document["packages"][0])
    sbom_path.write_text(json.dumps(document), encoding="utf-8")

    result = _run_spdx_source_hash(
        sbom_path,
        manifest_digest="sha256:68acd3cbed541d9551061bfb7cf92e83ae69e021118a0693aeb7e8832a5c330c",
    )

    assert result.returncode != 0
    assert "sbom_subject_binding" in result.stderr


@pytest.mark.parametrize(
    "external_refs",
    [
        [],
        [
            {
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": (
                    "pkg:oci/ghcr.io%2Fdemonsxxxxxx%2Fai-platform-backend@sha256%3A"
                    "77335e0179abc8286fe3cc637cabb5d78e96595bd66812ab9de7488fa514f3e0?arch=amd64"
                ),
            },
            {
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": (
                    "pkg:oci/ghcr.io%2Fdemonsxxxxxx%2Fai-platform-backend@sha256%3A"
                    "77335e0179abc8286fe3cc637cabb5d78e96595bd66812ab9de7488fa514f3e0?arch=amd64"
                ),
            },
        ],
        [
            {
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": (
                    "pkg:oci/ghcr.io%2Fdemonsxxxxxx%2Fai-platform-backend@sha256%3A"
                    "77335e0179abc8286fe3cc637cabb5d78e96595bd66812ab9de7488fa514f3e0?arch=arm64"
                ),
            }
        ],
        [
            {
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": (
                    "pkg:oci/ghcr.io%2Fdemonsxxxxxx%2Fai-platform-frontend@sha256%3A"
                    "77335e0179abc8286fe3cc637cabb5d78e96595bd66812ab9de7488fa514f3e0?arch=amd64"
                ),
            }
        ],
        [
            {
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": (
                    "pkg:oci/ghcr.io%2Fdemonsxxxxxx%2Fai-platform-backend@sha256%3A"
                    "77335e0179abc8286fe3cc637cabb5d78e96595bd66812ab9de7488fa514f3e0?arch=amd64&tag=latest"
                ),
            }
        ],
    ],
)
def test_spdx_source_hash_rejects_ambiguous_or_noncanonical_root_purl(
    tmp_path: Path,
    external_refs: list[dict[str, str]],
):
    sbom_path = tmp_path / "sbom-backend.spdx.json"
    digest = "sha256:77335e0179abc8286fe3cc637cabb5d78e96595bd66812ab9de7488fa514f3e0"
    document = _hosted_linux_syft_1_50_backend_spdx()
    document["packages"][0]["externalRefs"] = external_refs
    sbom_path.write_text(json.dumps(document), encoding="utf-8")

    result = _run_spdx_source_hash(sbom_path, manifest_digest=digest)

    assert result.returncode != 0
    assert "sbom_subject_binding" in result.stderr


@pytest.mark.parametrize(
    ("reason", "mutate"),
        [
            ("image_ref", None),
            ("document_name", lambda document: document.__setitem__("name", "foreign")),
        ("root_name", lambda document: document["packages"][0].__setitem__("name", "foreign")),
        (
            "root_version",
            lambda document: document["packages"][0].__setitem__("versionInfo", "sha256:" + "0" * 64),
        ),
        (
            "root_files_analyzed",
            lambda document: document["packages"][0].__setitem__("filesAnalyzed", True),
        ),
        (
            "root_download_location",
            lambda document: document["packages"][0].__setitem__(
                "downloadLocation", "https://example.invalid/foreign"
            ),
        ),
        (
            "root_checksums",
            lambda document: document["packages"][0].__setitem__("checksums", []),
        ),
        (
            "root_external_refs",
            lambda document: document["packages"][0].__setitem__("externalRefs", []),
        ),
    ],
)
def test_spdx_source_hash_preserves_safe_categorical_failure_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
    mutate,
):
    sbom_path = tmp_path / "sbom-backend.spdx.json"
    failure_evidence = tmp_path / "spdx-binding-diagnostic-backend.json"
    document = _syft_spdx("backend")
    if mutate is not None:
        mutate(document)
    sbom_path.write_text(json.dumps(document), encoding="utf-8")
    sentinel = "never-print-this-token"
    monkeypatch.setenv("GH_TOKEN", sentinel)

    result = _run_spdx_source_hash(
        sbom_path,
        image_ref=(
            "ghcr.io/demonsxxxxxx/ai-platform-frontend@" + MANIFEST_DIGEST
            if reason == "image_ref"
            else None
        ),
        failure_evidence_file=failure_evidence,
    )

    assert result.returncode != 0
    assert f"sbom_subject_binding.{reason}" in result.stderr
    assert sentinel not in result.stderr
    evidence = json.loads(failure_evidence.read_text(encoding="utf-8"))
    assert evidence["reason_code"] == f"sbom_subject_binding.{reason}"
    assert evidence["role"] == "backend"
    assert evidence["subject"] == "ghcr.io/demonsxxxxxx/ai-platform-backend"
    assert evidence["manifest_digest"] == MANIFEST_DIGEST
    assert evidence["image_ref"] == f"ghcr.io/demonsxxxxxx/ai-platform-backend@{MANIFEST_DIGEST}"
    assert set(evidence) == {
        "command",
        "image_ref",
        "manifest_digest",
        "reason_code",
        "role",
        "root_external_ref_count",
        "root_purl_sha256",
        "sbom_sha256",
        "schema_version",
        "subject",
    }
    assert sentinel not in json.dumps(evidence, sort_keys=True)


def test_spdx_image_binding_reports_required_document_describes_reason():
    document = _syft_spdx("backend")
    with pytest.raises(ValueError, match=r"sbom_subject_binding\.document_describes_required"):
        _validate_spdx_image_binding(
            document,
            subject="ghcr.io/demonsxxxxxx/ai-platform-backend",
            digest=MANIFEST_DIGEST,
            require_document_describes=True,
        )


def _replace_root_identity(document: dict[str, object]) -> None:
    old_root_id = document["packages"][0]["SPDXID"]
    new_root_id = "SPDXRef-Package-foreign"
    document["packages"][0]["SPDXID"] = new_root_id
    for relationship in document["relationships"]:
        if relationship["spdxElementId"] == old_root_id:
            relationship["spdxElementId"] = new_root_id
        if relationship["relatedSpdxElement"] == old_root_id:
            relationship["relatedSpdxElement"] = new_root_id


@pytest.mark.parametrize(
    ("reason", "mutate", "require_document_describes"),
    [
        (
            "root_identity",
            _replace_root_identity,
            False,
        ),
        (
            "document_describes_optional",
            lambda document: document.__setitem__(
                "documentDescribes", [document["packages"][1]["SPDXID"]]
            ),
            False,
        ),
    ],
)
def test_spdx_image_binding_reports_reachable_optional_reason_codes(
    reason: str,
    mutate,
    require_document_describes: bool,
):
    document = _syft_spdx("backend")
    mutate(document)

    with pytest.raises(ValueError, match=rf"sbom_subject_binding\.{reason}"):
        _validate_spdx_image_binding(
            document,
            subject="ghcr.io/demonsxxxxxx/ai-platform-backend",
            digest=MANIFEST_DIGEST,
            require_document_describes=require_document_describes,
        )


@pytest.mark.parametrize(("role", "architecture"), [("backend", ""), ("backend", "amd64"), ("frontend", ""), ("frontend", "amd64")])
def test_spdx_source_hash_preserves_existing_backend_frontend_purl_contours(
    tmp_path: Path,
    role: str,
    architecture: str,
):
    sbom_path = tmp_path / f"sbom-{role}.spdx.json"
    failure_evidence = tmp_path / f"spdx-binding-diagnostic-{role}.json"
    document = _syft_spdx(role)
    if architecture:
        document["packages"][0]["externalRefs"][0]["referenceLocator"] = (
            f"pkg:oci/ghcr.io%2Fdemonsxxxxxx%2Fai-platform-{role}@sha256%3A"
            f"{MANIFEST_DIGEST.removeprefix('sha256:')}?arch={architecture}"
        )
    sbom_path.write_text(json.dumps(document), encoding="utf-8")

    result = _run_spdx_source_hash(
        sbom_path,
        role=role,
        failure_evidence_file=failure_evidence,
    )

    assert result.returncode == 0, result.stderr
    assert not failure_evidence.exists()


def test_bind_spdx_namespace_cannot_collide_for_distinct_documents_with_same_tuple(
    tmp_path: Path,
):
    paths = [tmp_path / "first" / "sbom-backend.spdx.json", tmp_path / "second" / "sbom-backend.spdx.json"]
    for index, path in enumerate(paths):
        path.parent.mkdir()
        document = _syft_spdx("backend")
        document["documentNamespace"] = f"https://anchore.com/syft/image/{index}"
        document["creationInfo"]["created"] = f"2026-08-07T00:00:0{index}Z"
        unbound_content_sha256 = _canonical_json_sha256(document)
        path.write_text(json.dumps(document), encoding="utf-8")
        result = _run_cli(
            "bind-spdx",
            "--role",
            "backend",
            "--source-commit",
            SOURCE_COMMIT,
            "--manifest-digest",
            MANIFEST_DIGEST,
            "--producer-digest",
            MANIFEST_DIGEST,
            "--image-ref",
            f"ghcr.io/demonsxxxxxx/ai-platform-backend@{MANIFEST_DIGEST}",
            "--workflow-run-id",
            RUN_ID,
            "--workflow-run-attempt",
            str(RUN_ATTEMPT),
            "--unbound-content-sha256",
            unbound_content_sha256,
            "--sbom-file",
            str(path),
        )
        assert result.returncode == 0, result.stderr

    namespaces = {
        json.loads(path.read_text(encoding="utf-8"))["documentNamespace"] for path in paths
    }
    assert len(namespaces) == 2


def test_bind_spdx_is_byte_identical_on_exact_idempotent_reexecution(tmp_path: Path):
    sbom_path = tmp_path / "sbom-backend.spdx.json"
    sbom_path.write_text(json.dumps(_syft_spdx("backend")), encoding="utf-8")
    first = _run_bind_spdx(sbom_path)
    assert first.returncode == 0, first.stderr
    bound_bytes = sbom_path.read_bytes()

    second = _run_bind_spdx(sbom_path)

    assert second.returncode == 0, second.stderr
    assert sbom_path.read_bytes() == bound_bytes


@pytest.mark.parametrize(
    "namespace_mutation",
    [
        lambda value: value.replace(f"/{SOURCE_COMMIT}/", f"/{'f' * 40}/"),
        lambda value: value.replace(f"/runs/{RUN_ID}/", "/runs/999999/"),
        lambda value: value.replace(f"/attempts/{RUN_ATTEMPT}/", "/attempts/99/"),
        lambda value: value.replace("/sbom/backend/", "/sbom/frontend/"),
        lambda value: (
            f"https://github.com/{WORKFLOW_REPOSITORY}/actions/runs/not-a-run/"
            "attempts/2/sbom/backend/malformed"
        ),
    ],
)
def test_bind_spdx_rejects_existing_bound_namespace_tuple_or_grammar_swap(
    tmp_path: Path,
    namespace_mutation,
):
    sbom_path = tmp_path / "sbom-backend.spdx.json"
    sbom_path.write_text(json.dumps(_syft_spdx("backend")), encoding="utf-8")
    first = _run_bind_spdx(sbom_path)
    assert first.returncode == 0, first.stderr
    document = json.loads(sbom_path.read_text(encoding="utf-8"))
    document["documentNamespace"] = namespace_mutation(document["documentNamespace"])
    sbom_path.write_text(json.dumps(document), encoding="utf-8")

    second = _run_bind_spdx(sbom_path)

    assert second.returncode != 0
    assert "sbom_binding_state" in second.stderr


def test_bind_spdx_rejects_coordinated_bound_content_and_namespace_replacement(
    tmp_path: Path,
):
    sbom_path = tmp_path / "sbom-backend.spdx.json"
    sbom_path.write_text(json.dumps(_syft_spdx("backend")), encoding="utf-8")
    first = _run_bind_spdx(sbom_path)
    assert first.returncode == 0, first.stderr
    document = json.loads(sbom_path.read_text(encoding="utf-8"))
    document["packages"][1]["licenseDeclared"] = "MIT"
    document["documentNamespace"] = _sbom_namespace("backend", document)
    sbom_path.write_text(json.dumps(document), encoding="utf-8")

    second = _run_bind_spdx(sbom_path)

    assert second.returncode != 0
    assert "sbom_binding_state" in second.stderr


def test_bind_spdx_rejects_coordinated_bound_digest_root_and_purl_replacement(
    tmp_path: Path,
):
    sbom_path = tmp_path / "sbom-backend.spdx.json"
    sbom_path.write_text(json.dumps(_syft_spdx("backend")), encoding="utf-8")
    first = _run_bind_spdx(sbom_path)
    assert first.returncode == 0, first.stderr
    replacement_digest = "sha256:" + "f" * 64
    document = json.loads(sbom_path.read_text(encoding="utf-8"))
    root = document["packages"][0]
    root["versionInfo"] = replacement_digest
    root["checksums"][0]["checksumValue"] = "f" * 64
    root["externalRefs"][0]["referenceLocator"] = (
        "pkg:oci/ghcr.io%2Fdemonsxxxxxx%2Fai-platform-backend@sha256%3A"
        f"{'f' * 64}?arch="
    )
    document["documentNamespace"] = _sbom_namespace("backend", document).replace(
        MANIFEST_DIGEST.removeprefix("sha256:"),
        replacement_digest.removeprefix("sha256:"),
    )
    sbom_path.write_text(json.dumps(document), encoding="utf-8")

    second = _run_bind_spdx(sbom_path, manifest_digest=replacement_digest)

    assert second.returncode != 0
    assert "sbom_binding_state" in second.stderr


@pytest.mark.parametrize(
    "mutation",
    [
        lambda document: document["packages"].append(
            {
                **copy.deepcopy(document["packages"][0]),
                "SPDXID": "SPDXRef-Disconnected-Container",
            }
        ),
        lambda document: document["relationships"].append(
            {
                "spdxElementId": document["packages"][0]["SPDXID"],
                "relatedSpdxElement": "SPDXRef-Missing-Node",
                "relationshipType": "CONTAINS",
            }
        ),
        lambda document: document["relationships"].append(
            copy.deepcopy(document["relationships"][1])
        ),
        lambda document: document["relationships"].append(
            {
                "spdxElementId": document["packages"][0]["SPDXID"],
                "relatedSpdxElement": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBED_BY",
            }
        ),
        lambda document: document["packages"][1].update(
            {"SPDXID": "SPDXRef-DOCUMENT"}
        ),
        lambda document: document.setdefault("snippets", []).append(
            {
                "SPDXID": document["files"][0]["SPDXID"],
                "snippetFromFile": document["files"][0]["SPDXID"],
                "ranges": [],
                "licenseConcluded": "NOASSERTION",
                "licenseInfoInSnippets": ["NOASSERTION"],
                "copyrightText": "NOASSERTION",
            }
        ),
        lambda document: (
            document.update(
                {
                    "externalDocumentRefs": [
                        {
                            "externalDocumentId": "DocumentRef-external",
                            "spdxDocument": "https://example.invalid/external.spdx.json",
                            "checksum": {"algorithm": "SHA256", "checksumValue": "2" * 64},
                        }
                    ]
                }
            ),
            document["relationships"].append(
                {
                    "spdxElementId": document["packages"][0]["SPDXID"],
                    "relatedSpdxElement": "DocumentRef-external:SPDXRef-Package",
                    "relationshipType": "CONTAINS",
                }
            ),
        ),
    ],
)
def test_spdx_graph_is_closed_unique_and_single_rooted(
    tmp_path: Path,
    mutation,
):
    manifest = _manifest()
    _write_evidence(tmp_path, manifest)
    _rewrite_bound_sbom(tmp_path, manifest, mutation)

    with pytest.raises(ValueError, match="sbom_graph"):
        validate_manifest(manifest, evidence_root=tmp_path)


@pytest.mark.parametrize(
    ("source_key", "relationship_type", "target_key"),
    [
        ("dependency", "UNKNOWN_EXTENSION", "file"),
        ("file", "CONTAINED_BY", "dependency"),
        ("root", "CONTAINED_BY", "dependency"),
    ],
    ids=[
        "unknown-spdx-2.3-type",
        "equivalent-inverse-duplicate",
        "contradictory-containment",
    ],
)
def test_spdx_relationship_semantics_fail_closed(
    tmp_path: Path,
    source_key: str,
    relationship_type: str,
    target_key: str,
):
    manifest = _manifest()
    _write_evidence(tmp_path, manifest)

    def mutate(document: dict) -> None:
        node_ids = {
            "root": document["packages"][0]["SPDXID"],
            "dependency": document["packages"][1]["SPDXID"],
            "file": document["files"][0]["SPDXID"],
        }
        document["relationships"].append(
            {
                "spdxElementId": node_ids[source_key],
                "relationshipType": relationship_type,
                "relatedSpdxElement": node_ids[target_key],
            }
        )

    _rewrite_bound_sbom(tmp_path, manifest, mutate)

    with pytest.raises(ValueError, match="sbom_graph"):
        validate_manifest(manifest, evidence_root=tmp_path)


def test_spdx_graph_preserves_distinct_legal_relationships_on_same_nodes():
    document = _syft_spdx("backend")
    root_id = document["packages"][0]["SPDXID"]
    dependency_id = document["packages"][1]["SPDXID"]
    document["relationships"].append(
        {
            "spdxElementId": root_id,
            "relationshipType": "DEPENDS_ON",
            "relatedSpdxElement": dependency_id,
        }
    )

    _validate_spdx_graph(document)


def test_spdx_graph_accepts_captured_syft_1_50_package_self_dependencies():
    _validate_spdx_graph(_captured_syft_1_50_backend_self_dependency_graph())


@pytest.mark.parametrize(
    ("source", "relationship_type", "target"),
    [
        ("package", "CONTAINS", "package"),
        ("document", "DEPENDS_ON", "document"),
        ("file", "DEPENDENCY_OF", "file"),
    ],
    ids=["self-containment", "document-self-dependency", "file-self-dependency"],
)
def test_spdx_graph_rejects_self_relationships_outside_package_dependencies(
    source: str,
    relationship_type: str,
    target: str,
):
    document = _syft_spdx("backend")
    node_ids = {
        "document": "SPDXRef-DOCUMENT",
        "package": document["packages"][1]["SPDXID"],
        "file": document["files"][0]["SPDXID"],
    }
    document["relationships"].append(
        {
            "spdxElementId": node_ids[source],
            "relationshipType": relationship_type,
            "relatedSpdxElement": node_ids[target],
        }
    )

    with pytest.raises(ValueError, match="sbom_graph"):
        _validate_spdx_graph(document)


@pytest.mark.parametrize(
    ("forward_type", "inverse_type"),
    [
        ("DESCRIBES", "DESCRIBED_BY"),
        ("CONTAINS", "CONTAINED_BY"),
        ("DEPENDS_ON", "DEPENDENCY_OF"),
        ("GENERATES", "GENERATED_FROM"),
        ("ANCESTOR_OF", "DESCENDANT_OF"),
        ("PREREQUISITE_FOR", "HAS_PREREQUISITE"),
    ],
)
def test_spdx_graph_rejects_all_semantically_equivalent_inverse_spellings(
    forward_type: str,
    inverse_type: str,
):
    document = _syft_spdx("backend")
    dependency_id = document["packages"][1]["SPDXID"]
    file_id = document["files"][0]["SPDXID"]
    document["relationships"] = document["relationships"][:2]
    document["relationships"].extend(
        [
            {
                "spdxElementId": dependency_id,
                "relationshipType": forward_type,
                "relatedSpdxElement": file_id,
            },
            {
                "spdxElementId": file_id,
                "relationshipType": inverse_type,
                "relatedSpdxElement": dependency_id,
            },
        ]
    )

    with pytest.raises(ValueError, match="sbom_graph"):
        _validate_spdx_graph(document)


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


@pytest.mark.parametrize("kind", ["ready-manifest", "source-record"])
def test_cli_json_objects_reject_duplicate_top_level_or_nested_keys(
    tmp_path: Path,
    kind: str,
):
    manifest = _manifest()
    _write_evidence(tmp_path, manifest)
    if kind == "ready-manifest":
        path = tmp_path / "release-image-manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        _duplicate_json_member(path, f'"source_commit": "{SOURCE_COMMIT}"')
        result = _run_cli(
            "verify",
            "--manifest",
            str(path),
            "--evidence-root",
            str(tmp_path),
            "--expected-role",
            "backend",
            "--expected-role",
            "frontend",
        )
    else:
        record = copy.deepcopy(manifest["subjects"][0])
        provenance = record["evidence"]["provenance"]
        provenance.pop("reverification_ref")
        provenance.pop("reverification_sha256")
        path = tmp_path / "subject-backend.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        _duplicate_json_member(path, f'"role": "{record["role"]}"')
        result = _run_cli(
            "subject-target",
            "--subject-record",
            str(path),
            "--source-commit",
            SOURCE_COMMIT,
            "--workflow-repository",
            WORKFLOW_REPOSITORY,
            "--workflow-ref",
            WORKFLOW_REF,
            "--run-id",
            RUN_ID,
            "--run-attempt",
            str(RUN_ATTEMPT),
            "--expected-role",
            "backend",
        )

    assert result.returncode != 0
    assert "json_duplicate_key" in result.stderr


@pytest.mark.parametrize(
    ("kind", "member"),
    [
        ("sbom", '"spdxVersion": "SPDX-2.3"'),
        ("scan", '"SchemaVersion": 2'),
        (
            "bundle",
            '"mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json"',
        ),
        (
            "verified",
            '"issuer": "https://token.actions.githubusercontent.com"',
        ),
        (
            "reverified",
            '"issuer": "https://token.actions.githubusercontent.com"',
        ),
    ],
)
def test_evidence_json_rejects_duplicate_keys_at_every_file_ingress(
    tmp_path: Path,
    kind: str,
    member: str,
):
    manifest = _manifest()
    _write_evidence(tmp_path, manifest)
    paths = {
        "sbom": tmp_path / "sbom-backend.spdx.json",
        "scan": tmp_path / "trivy-backend.json",
        "bundle": tmp_path / "provenance-backend.bundle.json",
        "verified": tmp_path / "provenance-backend.verified.json",
        "reverified": tmp_path / "provenance-backend.assembly-verified.json",
    }
    path = paths[kind]
    _duplicate_json_member(path, member)
    subject = manifest["subjects"][0]
    if kind == "sbom":
        subject["evidence"]["sbom"]["sha256"] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    elif kind == "scan":
        subject["evidence"]["scan"]["sha256"] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    else:
        key = {
            "bundle": "bundle_sha256",
            "verified": "verification_sha256",
            "reverified": "reverification_sha256",
        }[kind]
        subject["evidence"]["provenance"][key] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()

    with pytest.raises(ValueError, match="json_duplicate_key"):
        validate_manifest(manifest, evidence_root=tmp_path)


def test_dsse_payload_rejects_duplicate_nested_json_keys(tmp_path: Path):
    manifest = _manifest()
    _write_evidence(tmp_path, manifest)
    bundle_path = tmp_path / "provenance-backend.bundle.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    statement = json.loads(base64.b64decode(bundle["dsseEnvelope"]["payload"]))
    payload = json.dumps(statement)
    digest_member = f'"sha256": "{MANIFEST_DIGEST.removeprefix("sha256:")}"'
    assert digest_member in payload
    payload = payload.replace(digest_member, f"{digest_member}, {digest_member}", 1)
    bundle["dsseEnvelope"]["payload"] = base64.b64encode(payload.encode()).decode()
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    for name in (
        "provenance-backend.verified.json",
        "provenance-backend.assembly-verified.json",
    ):
        path = tmp_path / name
        verification = json.loads(path.read_text(encoding="utf-8"))
        verification[0]["attestation"]["bundle"] = bundle
        path.write_text(json.dumps(verification), encoding="utf-8")
    provenance = manifest["subjects"][0]["evidence"]["provenance"]
    provenance["bundle_sha256"] = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    provenance["verification_sha256"] = hashlib.sha256(
        (tmp_path / "provenance-backend.verified.json").read_bytes()
    ).hexdigest()
    provenance["reverification_sha256"] = hashlib.sha256(
        (tmp_path / "provenance-backend.assembly-verified.json").read_bytes()
    ).hexdigest()

    with pytest.raises(ValueError, match="json_duplicate_key"):
        validate_manifest(manifest, evidence_root=tmp_path)


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
    reverified_path = tmp_path / "provenance-backend.assembly-verified.json"
    reverified_path.write_text(json.dumps(evidence), encoding="utf-8")
    provenance = manifest["subjects"][0]["evidence"]["provenance"]
    provenance["verification_sha256"] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    provenance["reverification_sha256"] = hashlib.sha256(
        reverified_path.read_bytes()
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
    verified_path = tmp_path / "provenance-backend.verified.json"
    verified = json.loads(verified_path.read_text(encoding="utf-8"))
    verified[0]["attestation"]["bundle"] = bundle
    verified_path.write_text(json.dumps(verified), encoding="utf-8")
    reverified_path = tmp_path / "provenance-backend.assembly-verified.json"
    reverified_path.write_text(json.dumps(verified), encoding="utf-8")
    provenance = manifest["subjects"][0]["evidence"]["provenance"]
    provenance["bundle_sha256"] = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    provenance["verification_sha256"] = hashlib.sha256(verified_path.read_bytes()).hexdigest()
    provenance["reverification_sha256"] = hashlib.sha256(
        reverified_path.read_bytes()
    ).hexdigest()

    with pytest.raises(ValueError, match="provenance_bundle_statement"):
        validate_manifest(manifest, evidence_root=tmp_path)


def test_provenance_coordinated_bundle_substitution_requires_assembly_reverification(
    tmp_path: Path,
):
    manifest = _manifest()
    _write_evidence(tmp_path, manifest)
    bundle_path = tmp_path / "provenance-backend.bundle.json"
    verified_path = tmp_path / "provenance-backend.verified.json"

    substituted = json.loads(bundle_path.read_text(encoding="utf-8"))
    substituted["dsseEnvelope"]["signatures"] = [{"sig": "attacker-controlled-signature"}]
    substituted["verificationMaterial"] = {"certificate": "attacker-controlled-material"}
    bundle_path.write_text(json.dumps(substituted), encoding="utf-8")
    verified = json.loads(verified_path.read_text(encoding="utf-8"))
    verified[0]["attestation"]["bundle"] = substituted
    verified_path.write_text(json.dumps(verified), encoding="utf-8")
    provenance = manifest["subjects"][0]["evidence"]["provenance"]
    provenance["bundle_sha256"] = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    provenance["verification_sha256"] = hashlib.sha256(verified_path.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="provenance_assembly_reverification"):
        validate_manifest(manifest, evidence_root=tmp_path)


def test_provenance_verified_timestamp_requires_complete_trusted_result(tmp_path: Path):
    manifest = _manifest()
    _write_evidence(tmp_path, manifest)
    verified_path = tmp_path / "provenance-backend.verified.json"
    verified = json.loads(verified_path.read_text(encoding="utf-8"))
    verified[0]["verificationResult"]["verifiedTimestamps"] = [{}]
    verified_path.write_text(json.dumps(verified), encoding="utf-8")
    reverified_path = tmp_path / "provenance-backend.assembly-verified.json"
    reverified_path.write_text(json.dumps(verified), encoding="utf-8")
    provenance = manifest["subjects"][0]["evidence"]["provenance"]
    provenance["verification_sha256"] = hashlib.sha256(verified_path.read_bytes()).hexdigest()
    provenance["reverification_sha256"] = hashlib.sha256(
        reverified_path.read_bytes()
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


@pytest.mark.parametrize(
    "vulnerability",
    [
        "not-an-object",
        ["not-an-object"],
        None,
        {"VulnerabilityID": "CVE-MISSING-SEVERITY"},
        {"VulnerabilityID": "CVE-UNKNOWN-SEVERITY", "Severity": "UNRECOGNIZED"},
    ],
)
def test_trivy_vulnerability_entries_require_recognized_severity(
    tmp_path: Path,
    vulnerability: object,
):
    manifest = _manifest()
    _write_evidence(tmp_path, manifest)
    scan_path = tmp_path / "trivy-backend.json"
    scan = json.loads(scan_path.read_text(encoding="utf-8"))
    scan["Results"] = [{"Target": "fixture", "Vulnerabilities": [vulnerability]}]
    scan_path.write_text(json.dumps(scan), encoding="utf-8")
    manifest["subjects"][0]["evidence"]["scan"]["sha256"] = hashlib.sha256(
        scan_path.read_bytes()
    ).hexdigest()

    with pytest.raises(ValueError, match="scan_vulnerability_severity"):
        validate_manifest(manifest, evidence_root=tmp_path)


def test_spdx_document_namespace_binds_exact_subject_source_and_digest(tmp_path: Path):
    manifest = _manifest()
    _write_evidence(tmp_path, manifest)
    sbom_path = tmp_path / "sbom-backend.spdx.json"
    unrelated = json.loads(sbom_path.read_text(encoding="utf-8"))
    unrelated["documentNamespace"] = (
        f"https://github.com/{WORKFLOW_REPOSITORY}/sbom/frontend/{'f' * 40}/"
        f"sha256/{'e' * 64}"
    )
    sbom_path.write_text(json.dumps(unrelated), encoding="utf-8")
    manifest["subjects"][0]["evidence"]["sbom"]["sha256"] = hashlib.sha256(
        sbom_path.read_bytes()
    ).hexdigest()

    Draft202012Validator(_schema()).validate(manifest)
    with pytest.raises(ValueError, match="sbom_binding_state"):
        validate_manifest(manifest, evidence_root=tmp_path)


def test_schema_valid_syft_directory_sbom_cannot_be_coordinated_into_image_evidence(
    tmp_path: Path,
):
    manifest = _manifest()
    _write_evidence(tmp_path, manifest)
    sbom_path = tmp_path / "sbom-backend.spdx.json"
    directory = _syft_directory_spdx("backend")
    directory["documentDescribes"] = [
        directory["relationships"][0]["relatedSpdxElement"]
    ]
    directory["documentNamespace"] = _sbom_namespace("backend", directory)
    sbom_path.write_text(json.dumps(directory), encoding="utf-8")
    manifest["subjects"][0]["evidence"]["sbom"]["sha256"] = hashlib.sha256(
        sbom_path.read_bytes()
    ).hexdigest()

    with pytest.raises(ValueError, match="sbom_(subject_binding|graph)"):
        validate_manifest(manifest, evidence_root=tmp_path)


def test_other_syft_image_sbom_cannot_be_coordinated_into_subject_evidence(tmp_path: Path):
    manifest = _manifest()
    _write_evidence(tmp_path, manifest)
    sbom_path = tmp_path / "sbom-backend.spdx.json"
    unrelated = _syft_spdx("frontend")
    unrelated["documentDescribes"] = [
        unrelated["relationships"][0]["relatedSpdxElement"]
    ]
    unrelated["documentNamespace"] = _sbom_namespace("backend", unrelated)
    sbom_path.write_text(json.dumps(unrelated), encoding="utf-8")
    manifest["subjects"][0]["evidence"]["sbom"]["sha256"] = hashlib.sha256(
        sbom_path.read_bytes()
    ).hexdigest()

    with pytest.raises(ValueError, match="sbom_subject_binding"):
        validate_manifest(manifest, evidence_root=tmp_path)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda document: document.update(
            {"name": "ghcr.io/demonsxxxxxx/ai-platform-frontend"}
        ),
        lambda document: document["packages"][0].update(
            {"name": "ghcr.io/demonsxxxxxx/ai-platform-frontend"}
        ),
        lambda document: document["packages"][0].update(
            {"versionInfo": "sha256:" + "f" * 64}
        ),
        lambda document: document["packages"][0]["checksums"][0].update(
            {"checksumValue": "f" * 64}
        ),
        lambda document: document["packages"][0]["externalRefs"][0].update(
            {
                "referenceLocator": (
                    "pkg:oci/ghcr.io%2Fdemonsxxxxxx%2Fai-platform-frontend"
                    f"@sha256%3A{MANIFEST_DIGEST.removeprefix('sha256:')}?arch="
                )
            }
        ),
        lambda document: document["packages"][0]["externalRefs"][0].update(
            {
                "referenceLocator": (
                    "pkg:oci/ghcr.io%252Fdemonsxxxxxx%252Fai-platform-backend"
                    f"@sha256%253A{MANIFEST_DIGEST.removeprefix('sha256:')}?arch="
                )
            }
        ),
        lambda document: document["packages"][0].update(
            {"primaryPackagePurpose": "FILE"}
        ),
        lambda document: document["relationships"][0].update(
            {"relatedSpdxElement": document["packages"][1]["SPDXID"]}
        ),
        lambda document: document.update(
            {"documentDescribes": [document["packages"][1]["SPDXID"]]}
        ),
    ],
)
def test_spdx_root_image_identity_swaps_fail_with_coordinated_hash(
    tmp_path: Path,
    mutation,
):
    manifest = _manifest()
    _write_evidence(tmp_path, manifest)
    sbom_path = tmp_path / "sbom-backend.spdx.json"
    document = json.loads(sbom_path.read_text(encoding="utf-8"))
    mutation(document)
    document["documentNamespace"] = _sbom_namespace("backend", document)
    sbom_path.write_text(json.dumps(document), encoding="utf-8")
    manifest["subjects"][0]["evidence"]["sbom"]["sha256"] = hashlib.sha256(
        sbom_path.read_bytes()
    ).hexdigest()

    with pytest.raises(ValueError, match="sbom_(subject_binding|graph)"):
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
        lambda value: value["subjects"][0]["evidence"]["provenance"].update({"reverification_ref": f"github-artifact://release-image-evidence-{SOURCE_COMMIT}-{RUN_ID}-{RUN_ATTEMPT}/provenance-frontend.assembly-verified.json"}),
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
