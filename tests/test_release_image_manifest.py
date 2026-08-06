import copy
import json
from pathlib import Path

import pytest

from tools.release_image_manifest import (
    SCHEMA_VERSION,
    assemble_manifest,
    validate_manifest,
)


SOURCE_COMMIT = "a" * 40
MANIFEST_DIGEST = "sha256:" + "b" * 64
SBOM_DIGEST = "sha256:" + "c" * 64
SCAN_DIGEST = "sha256:" + "d" * 64
REPOSITORY = "https://github.com/demonsxxxxxx/ai-platform.git"


def _subject(role: str) -> dict[str, object]:
    subject = f"ghcr.io/demonsxxxxxx/ai-platform-{role}"
    dockerfile = "Dockerfile" if role == "backend" else "frontend/web/Dockerfile"
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
                "ref": f"https://github.com/demonsxxxxxx/ai-platform/attestations/123-{role}",
            },
            "signature": {
                "identity": "https://github.com/demonsxxxxxx/ai-platform/.github/workflows/ai-platform-packaging-publish.yml@refs/heads/main",
                "issuer": "https://token.actions.githubusercontent.com",
                "ref": f"oci://{subject}@{MANIFEST_DIGEST}#cosign-keyless-signature",
            },
            "scan": {
                "blocking_severities": ["HIGH", "CRITICAL"],
                "ref": f"github-artifact://release-image-evidence-{SOURCE_COMMIT}/trivy-{role}.json",
                "result": "passed",
                "scanner": "trivy@0.70.0",
                "sha256": SCAN_DIGEST.removeprefix("sha256:"),
            },
        },
    }


def _manifest() -> dict[str, object]:
    return assemble_manifest(
        source_commit=SOURCE_COMMIT,
        repository=REPOSITORY,
        workflow={
            "repository": "demonsxxxxxx/ai-platform",
            "workflow_ref": "demonsxxxxxx/ai-platform/.github/workflows/ai-platform-packaging-publish.yml@refs/heads/main",
            "run_id": "123456",
            "run_attempt": 1,
            "head_sha": SOURCE_COMMIT,
        },
        subjects=[_subject("backend"), _subject("frontend")],
        expected_roles={"backend", "frontend"},
    )


def test_manifest_accepts_complete_digest_bound_evidence():
    manifest = _manifest()

    validate_manifest(manifest, expected_roles={"backend", "frontend"})

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
    ],
)
def test_manifest_rejects_unready_or_mismatched_subjects(mutation, message: str):
    manifest = _manifest()
    mutation(manifest)

    with pytest.raises(ValueError, match=message):
        validate_manifest(manifest, expected_roles={"backend", "frontend"})


def test_manifest_rejects_missing_and_duplicate_roles():
    backend_only = _manifest()
    backend_only["subjects"] = [backend_only["subjects"][0]]
    with pytest.raises(ValueError, match="subject_roles"):
        validate_manifest(backend_only, expected_roles={"backend", "frontend"})

    duplicate = _manifest()
    duplicate["subjects"] = [duplicate["subjects"][0], copy.deepcopy(duplicate["subjects"][0])]
    with pytest.raises(ValueError, match="subject_roles"):
        validate_manifest(duplicate, expected_roles={"backend", "frontend"})


def test_manifest_rejects_unknown_fields():
    manifest = _manifest()
    manifest["subjects"][0]["image"]["local_image_id"] = "sha256:" + "f" * 64

    with pytest.raises(ValueError, match="image_keys"):
        validate_manifest(manifest, expected_roles={"backend", "frontend"})


def test_schema_file_is_the_strict_machine_readable_contract():
    schema_path = Path(__file__).resolve().parents[1] / "schemas" / "release-image-manifest.v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["$id"] == "https://github.com/demonsxxxxxx/ai-platform/schemas/release-image-manifest.v1.schema.json"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == SCHEMA_VERSION
    assert schema["properties"]["subjects"]["items"]["additionalProperties"] is False
