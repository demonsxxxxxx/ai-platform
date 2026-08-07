from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


SPDX_FAILURE_EVIDENCE_SCHEMA_VERSION = "ai-platform.spdx-failure-evidence.v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _root_metadata(document: dict[str, Any] | None) -> tuple[int | None, str | None]:
    if not isinstance(document, dict):
        return None, None
    packages = document.get("packages")
    if not isinstance(packages, list):
        return None, None
    container_roots = [
        package
        for package in packages
        if isinstance(package, dict) and package.get("primaryPackagePurpose") == "CONTAINER"
    ]
    if len(container_roots) != 1:
        return None, None
    external_refs = container_roots[0].get("externalRefs")
    if not isinstance(external_refs, list):
        return None, None
    locator: str | None = None
    if len(external_refs) == 1 and isinstance(external_refs[0], dict):
        candidate = external_refs[0].get("referenceLocator")
        if isinstance(candidate, str):
            locator = candidate
    return len(external_refs), (
        hashlib.sha256(locator.encode("utf-8")).hexdigest() if locator is not None else None
    )


def write_spdx_failure_evidence(
    *,
    path: Path | None,
    role: str,
    subject: str,
    digest: str,
    sbom_path: Path | None,
    document: dict[str, Any] | None,
    reason_code: str,
) -> None:
    """Persist only release-safe metadata for a failed source-identity check."""
    if path is None or path.name != f"spdx-binding-diagnostic-{role}.json":
        return
    root_external_ref_count, root_purl_sha256 = _root_metadata(document)
    evidence = {
        "schema_version": SPDX_FAILURE_EVIDENCE_SCHEMA_VERSION,
        "command": "spdx-source-hash",
        "reason_code": reason_code,
        "role": role,
        "subject": subject,
        "manifest_digest": digest,
        "image_ref": f"{subject}@{digest}",
        "sbom_sha256": _sha256(sbom_path) if sbom_path is not None and sbom_path.is_file() else None,
        "root_external_ref_count": root_external_ref_count,
        "root_purl_sha256": root_purl_sha256,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        # The original source-identity failure remains authoritative.
        return
