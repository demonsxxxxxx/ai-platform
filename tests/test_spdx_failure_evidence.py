from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.spdx_failure_evidence import write_spdx_failure_evidence


def test_failure_evidence_serialization_is_allowlisted_and_redacts_document_content(
    tmp_path: Path,
):
    sbom = tmp_path / "sbom-backend.spdx.json"
    diagnostic = tmp_path / "spdx-binding-diagnostic-backend.json"
    secret = "never-persist-this-token"
    sbom.write_text(secret, encoding="utf-8")
    document = {
        "packages": [
            {
                "primaryPackagePurpose": "CONTAINER",
                "externalRefs": [{"referenceLocator": secret}],
            }
        ]
    }

    write_spdx_failure_evidence(
        path=diagnostic,
        role="backend",
        subject="ghcr.io/demonsxxxxxx/ai-platform-backend",
        digest="sha256:" + "a" * 64,
        sbom_path=sbom,
        document=document,
        reason_code="sbom_subject_binding.root_external_refs",
    )

    evidence = json.loads(diagnostic.read_text(encoding="utf-8"))
    assert evidence == {
        "command": "spdx-source-hash",
        "image_ref": "ghcr.io/demonsxxxxxx/ai-platform-backend@sha256:" + "a" * 64,
        "manifest_digest": "sha256:" + "a" * 64,
        "reason_code": "sbom_subject_binding.root_external_refs",
        "role": "backend",
        "root_external_ref_count": 1,
        "root_purl_sha256": hashlib.sha256(secret.encode("utf-8")).hexdigest(),
        "sbom_sha256": hashlib.sha256(sbom.read_bytes()).hexdigest(),
        "schema_version": "ai-platform.spdx-failure-evidence.v1",
        "subject": "ghcr.io/demonsxxxxxx/ai-platform-backend",
    }
    assert secret not in diagnostic.read_text(encoding="utf-8")


def test_failure_evidence_serialization_rejects_noncanonical_diagnostic_filename(
    tmp_path: Path,
):
    diagnostic = tmp_path / "arbitrary-output.json"

    write_spdx_failure_evidence(
        path=diagnostic,
        role="frontend",
        subject="ghcr.io/demonsxxxxxx/ai-platform-frontend",
        digest="sha256:" + "b" * 64,
        sbom_path=None,
        document=None,
        reason_code="sbom_subject_binding.document_name",
    )

    assert not diagnostic.exists()
