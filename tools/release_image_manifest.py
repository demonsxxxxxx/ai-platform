from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable


SCHEMA_VERSION = "ai-platform.release-image-manifest.v1"
PLATFORM = "linux/amd64"
REPOSITORY = "https://github.com/demonsxxxxxx/ai-platform.git"
WORKFLOW_REPOSITORY = "demonsxxxxxx/ai-platform"
SUBJECTS = {
    "backend": "ghcr.io/demonsxxxxxx/ai-platform-backend",
    "frontend": "ghcr.io/demonsxxxxxx/ai-platform-frontend",
}
DOCKERFILES = {
    "backend": "Dockerfile",
    "frontend": "frontend/web/Dockerfile",
}

_COMMIT = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}")
_WORKFLOW_REF = re.compile(
    r"demonsxxxxxx/ai-platform/\.github/workflows/"
    r"ai-platform-packaging-publish\.yml@refs/heads/main"
)
_SIGNATURE_IDENTITY = re.compile(
    r"https://github\.com/demonsxxxxxx/ai-platform/\.github/workflows/"
    r"ai-platform-packaging-publish\.yml@refs/heads/main"
)
_TOP_KEYS = {"schema_version", "source_commit", "repository", "workflow", "subjects"}
_WORKFLOW_KEYS = {"repository", "workflow_ref", "run_id", "run_attempt", "head_sha"}
_SUBJECT_KEYS = {"role", "platform", "build", "image", "evidence"}
_BUILD_KEYS = {"context", "dockerfile"}
_CONTEXT_KEYS = {"path", "source_commit"}
_DOCKERFILE_KEYS = {"path", "sha256"}
_IMAGE_KEYS = {"subject", "source_tag", "manifest_digest", "immutable_ref"}
_EVIDENCE_KEYS = {"sbom", "provenance", "signature", "scan"}
_SBOM_KEYS = {"format", "ref", "sha256"}
_PROVENANCE_KEYS = {
    "attestation_id",
    "bundle_ref",
    "bundle_sha256",
    "predicate_type",
    "ref",
    "verification_ref",
    "verification_sha256",
}
_SIGNATURE_KEYS = {"identity", "issuer", "ref"}
_SCAN_KEYS = {"blocking_severities", "ref", "result", "scanner", "sha256"}
_ATTESTATION_ID = re.compile(r"[A-Za-z0-9_-]+")


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name}_object")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{name}_keys")


def _nonempty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name)
    return value


def _fullmatch(pattern: re.Pattern[str], value: Any, name: str) -> str:
    text = _nonempty_string(value, name)
    if pattern.fullmatch(text) is None:
        raise ValueError(name)
    return text


def _artifact_name(*, source_commit: str, workflow: dict[str, Any], role: str) -> str:
    return (
        f"release-image-subject-{source_commit}-{workflow['run_id']}-"
        f"{workflow['run_attempt']}-{role}"
    )


def _require_canonical_expected_roles(expected_roles: Iterable[str] | None) -> None:
    if expected_roles is None:
        return
    supplied = list(expected_roles)
    canonical = list(SUBJECTS)
    if len(supplied) != len(canonical) or sorted(supplied) != sorted(canonical):
        raise ValueError("expected_roles")


def _required_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(name)
    return value


def _required_list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(name)
    return value


def _validate_provenance_files(
    *,
    evidence_root: Path,
    role: str,
    expected_subject: str,
    digest: str,
    source_commit: str,
    workflow: dict[str, Any],
    provenance: dict[str, Any],
) -> None:
    bundle_path = evidence_root / f"provenance-{role}.bundle.json"
    verification_path = evidence_root / f"provenance-{role}.verified.json"
    if not bundle_path.is_file():
        raise ValueError("provenance_bundle_missing")
    if not verification_path.is_file():
        raise ValueError("provenance_verification_missing")
    if _sha256(bundle_path) != provenance["bundle_sha256"]:
        raise ValueError("provenance_bundle_sha256")
    if _sha256(verification_path) != provenance["verification_sha256"]:
        raise ValueError("provenance_verification_sha256")

    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    if not isinstance(bundle, dict) or bundle.get("mediaType") != (
        "application/vnd.dev.sigstore.bundle.v0.3+json"
    ):
        raise ValueError("provenance_bundle")
    envelope = _required_mapping(bundle.get("dsseEnvelope"), "provenance_bundle_envelope")
    if envelope.get("payloadType") != "application/vnd.in-toto+json":
        raise ValueError("provenance_bundle_payload_type")
    signatures = _required_list(envelope.get("signatures"), "provenance_bundle_signatures")
    if not all(
        isinstance(signature, dict)
        and isinstance(signature.get("sig"), str)
        and bool(signature["sig"])
        for signature in signatures
    ):
        raise ValueError("provenance_bundle_signatures")
    _required_mapping(bundle.get("verificationMaterial"), "provenance_bundle_verification_material")
    try:
        decoded_payload = base64.b64decode(envelope.get("payload", ""), validate=True)
        bundle_statement = json.loads(decoded_payload)
    except (binascii.Error, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError("provenance_bundle_payload") from exc
    verified = json.loads(verification_path.read_text(encoding="utf-8"))
    entries = _required_list(verified, "provenance_verification")
    if len(entries) != 1:
        raise ValueError("provenance_verification_count")
    entry = _required_mapping(entries[0], "provenance_verification_entry")
    result = _required_mapping(entry.get("verificationResult"), "provenance_verification_result")
    timestamps = _required_list(result.get("verifiedTimestamps"), "provenance_verified_timestamps")
    for item in timestamps:
        if not isinstance(item, dict) or item.get("type") not in {"Tlog", "TimestampAuthority"}:
            raise ValueError("provenance_verified_timestamps")
        if not isinstance(item.get("uri"), str) or not item["uri"]:
            raise ValueError("provenance_verified_timestamps")
        timestamp = item.get("timestamp")
        if not isinstance(timestamp, str):
            raise ValueError("provenance_verified_timestamps")
        try:
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("provenance_verified_timestamps") from exc

    statement = _required_mapping(result.get("statement"), "provenance_statement")
    if statement.get("predicateType") != "https://slsa.dev/provenance/v1":
        raise ValueError("provenance_predicate_type")
    subjects = _required_list(statement.get("subject"), "provenance_subject")
    if len(subjects) != 1:
        raise ValueError("provenance_subject_count")
    statement_subject = _required_mapping(subjects[0], "provenance_subject")
    if statement_subject.get("name") != expected_subject:
        raise ValueError("provenance_subject_name")
    statement_digest = _required_mapping(
        statement_subject.get("digest"),
        "provenance_subject_digest",
    )
    if statement_digest.get("sha256") != digest.removeprefix("sha256:"):
        raise ValueError("provenance_subject_digest")

    signature = _required_mapping(result.get("signature"), "provenance_signature")
    certificate = _required_mapping(signature.get("certificate"), "provenance_certificate")
    identity = (
        "https://github.com/demonsxxxxxx/ai-platform/.github/workflows/"
        "ai-platform-packaging-publish.yml@refs/heads/main"
    )
    if certificate.get("subjectAlternativeName") != identity:
        raise ValueError("provenance_workflow_identity")
    if certificate.get("issuer") != "https://token.actions.githubusercontent.com":
        raise ValueError("provenance_oidc_issuer")
    if certificate.get("runnerEnvironment") != "github-hosted":
        raise ValueError("provenance_runner_environment")
    if certificate.get("sourceRepositoryURI") != "https://github.com/demonsxxxxxx/ai-platform":
        raise ValueError("provenance_repository")
    if certificate.get("sourceRepositoryDigest") != source_commit:
        raise ValueError("provenance_source_commit")
    if certificate.get("sourceRepositoryRef") != "refs/heads/main":
        raise ValueError("provenance_source_ref")
    if certificate.get("buildConfigURI") != identity:
        raise ValueError("provenance_workflow_ref")
    expected_run_uri = (
        "https://github.com/demonsxxxxxx/ai-platform/actions/runs/"
        f"{workflow['run_id']}/attempts/{workflow['run_attempt']}"
    )
    if certificate.get("runInvocationURI") != expected_run_uri:
        raise ValueError("provenance_run_identity")
    if bundle_statement != statement:
        raise ValueError("provenance_bundle_statement")


def _validate_sbom_file(
    *,
    evidence_root: Path,
    role: str,
    expected_sha256: str,
) -> None:
    path = evidence_root / f"sbom-{role}.spdx.json"
    if not path.is_file():
        raise ValueError("sbom_missing")
    if _sha256(path) != expected_sha256:
        raise ValueError("sbom_sha256")
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(document, dict)
        or not isinstance(document.get("spdxVersion"), str)
        or not document["spdxVersion"].startswith("SPDX-2.")
        or document.get("SPDXID") != "SPDXRef-DOCUMENT"
    ):
        raise ValueError("sbom_document")


def _validate_scan_file(
    *,
    evidence_root: Path,
    role: str,
    immutable_ref: str,
    expected_sha256: str,
) -> None:
    path = evidence_root / f"trivy-{role}.json"
    if not path.is_file():
        raise ValueError("scan_missing")
    if _sha256(path) != expected_sha256:
        raise ValueError("scan_sha256")
    report = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or report.get("ArtifactName") != immutable_ref:
        raise ValueError("scan_subject")
    if not isinstance(report.get("SchemaVersion"), int) or report["SchemaVersion"] < 1:
        raise ValueError("scan_document")
    results = report.get("Results")
    if not isinstance(results, list):
        raise ValueError("scan_document")
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("scan_document")
        vulnerabilities = result.get("Vulnerabilities") or []
        if not isinstance(vulnerabilities, list):
            raise ValueError("scan_document")
        if any(
            isinstance(vulnerability, dict)
            and vulnerability.get("Severity") in {"HIGH", "CRITICAL"}
            for vulnerability in vulnerabilities
        ):
            raise ValueError("scan_blocking_vulnerability")


def validate_subject(
    subject: Any,
    *,
    source_commit: str,
    workflow: dict[str, Any],
    evidence_root: Path | None = None,
) -> dict[str, Any]:
    value = _object(subject, "subject")
    _exact_keys(value, _SUBJECT_KEYS, "subject")

    role = _nonempty_string(value["role"], "role")
    if role not in SUBJECTS:
        raise ValueError("role")
    if value["platform"] != PLATFORM:
        raise ValueError("platform")

    build = _object(value["build"], "build")
    _exact_keys(build, _BUILD_KEYS, "build")
    context = _object(build["context"], "context")
    _exact_keys(context, _CONTEXT_KEYS, "context")
    if context["path"] != ".":
        raise ValueError("context_path")
    if context["source_commit"] != source_commit:
        raise ValueError("context_source_commit")
    dockerfile = _object(build["dockerfile"], "dockerfile")
    _exact_keys(dockerfile, _DOCKERFILE_KEYS, "dockerfile")
    if dockerfile["path"] != DOCKERFILES[role]:
        raise ValueError("dockerfile_path")
    _fullmatch(_HEX_SHA256, dockerfile["sha256"], "dockerfile_sha256")

    image = _object(value["image"], "image")
    _exact_keys(image, _IMAGE_KEYS, "image")
    expected_subject = SUBJECTS[role]
    if image["subject"] != expected_subject:
        raise ValueError("image_subject")
    digest = _fullmatch(_DIGEST, image["manifest_digest"], "manifest_digest")
    if image["source_tag"] != f"{expected_subject}:{source_commit}":
        raise ValueError("source_tag")
    if image["immutable_ref"] != f"{expected_subject}@{digest}":
        raise ValueError("immutable_ref")

    evidence = _object(value["evidence"], "evidence")
    _exact_keys(evidence, _EVIDENCE_KEYS, "evidence")
    sbom = _object(evidence["sbom"], "sbom")
    _exact_keys(sbom, _SBOM_KEYS, "sbom")
    if sbom["format"] != "spdx-json":
        raise ValueError("sbom_format")
    if sbom["ref"] != f"oci://{expected_subject}@{digest}#sbom-spdx-attestation":
        raise ValueError("sbom_ref")
    sbom_sha256 = _fullmatch(_HEX_SHA256, sbom["sha256"], "sbom_sha256")
    if evidence_root is not None:
        _validate_sbom_file(
            evidence_root=evidence_root,
            role=role,
            expected_sha256=sbom_sha256,
        )

    provenance = _object(evidence["provenance"], "provenance")
    _exact_keys(provenance, _PROVENANCE_KEYS, "provenance")
    if provenance["predicate_type"] != "https://slsa.dev/provenance/v1":
        raise ValueError("provenance_predicate_type")
    attestation_id = _fullmatch(
        _ATTESTATION_ID,
        provenance["attestation_id"],
        "provenance_attestation_id",
    )
    if provenance["ref"] != (
        f"https://github.com/{WORKFLOW_REPOSITORY}/attestations/{attestation_id}"
    ):
        raise ValueError("provenance_ref")
    artifact_name = _artifact_name(
        source_commit=source_commit,
        workflow=workflow,
        role=role,
    )
    if provenance["bundle_ref"] != (
        f"github-artifact://{artifact_name}/provenance-{role}.bundle.json"
    ):
        raise ValueError("provenance_bundle_ref")
    _fullmatch(_HEX_SHA256, provenance["bundle_sha256"], "provenance_bundle_sha256")
    if provenance["verification_ref"] != (
        f"github-artifact://{artifact_name}/provenance-{role}.verified.json"
    ):
        raise ValueError("provenance_verification_ref")
    _fullmatch(
        _HEX_SHA256,
        provenance["verification_sha256"],
        "provenance_verification_sha256",
    )
    if evidence_root is not None:
        _validate_provenance_files(
            evidence_root=evidence_root,
            role=role,
            expected_subject=expected_subject,
            digest=digest,
            source_commit=source_commit,
            workflow=workflow,
            provenance=provenance,
        )

    signature = _object(evidence["signature"], "signature")
    _exact_keys(signature, _SIGNATURE_KEYS, "signature")
    if _SIGNATURE_IDENTITY.fullmatch(_nonempty_string(signature["identity"], "signature_identity")) is None:
        raise ValueError("signature_identity")
    if signature["issuer"] != "https://token.actions.githubusercontent.com":
        raise ValueError("signature_issuer")
    if signature["ref"] != f"oci://{expected_subject}@{digest}#cosign-keyless-signature":
        raise ValueError("signature_ref")

    scan = _object(evidence["scan"], "scan")
    _exact_keys(scan, _SCAN_KEYS, "scan")
    if scan["blocking_severities"] != ["HIGH", "CRITICAL"]:
        raise ValueError("blocking_severities")
    if scan["result"] != "passed":
        raise ValueError("scan_result")
    if scan["scanner"] != "trivy@0.70.0":
        raise ValueError("scan_scanner")
    if scan["ref"] != f"github-artifact://{artifact_name}/trivy-{role}.json":
        raise ValueError("scan_ref")
    scan_sha256 = _fullmatch(_HEX_SHA256, scan["sha256"], "scan_sha256")
    if evidence_root is not None:
        _validate_scan_file(
            evidence_root=evidence_root,
            role=role,
            immutable_ref=image["immutable_ref"],
            expected_sha256=scan_sha256,
        )
    return value


def validate_manifest(
    manifest: Any,
    *,
    expected_roles: Iterable[str] | None = None,
    evidence_root: Path | None = None,
) -> dict[str, Any]:
    _require_canonical_expected_roles(expected_roles)
    value = _object(manifest, "manifest")
    _exact_keys(value, _TOP_KEYS, "manifest")
    if value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("schema_version")
    source_commit = _fullmatch(_COMMIT, value["source_commit"], "source_commit")
    if value["repository"] != REPOSITORY:
        raise ValueError("repository")

    workflow = _object(value["workflow"], "workflow")
    _exact_keys(workflow, _WORKFLOW_KEYS, "workflow")
    if workflow["repository"] != WORKFLOW_REPOSITORY:
        raise ValueError("workflow_repository")
    _fullmatch(_WORKFLOW_REF, workflow["workflow_ref"], "workflow_ref")
    if not isinstance(workflow["run_id"], str) or not workflow["run_id"].isdigit():
        raise ValueError("workflow_run_id")
    if not isinstance(workflow["run_attempt"], int) or isinstance(workflow["run_attempt"], bool) or workflow["run_attempt"] < 1:
        raise ValueError("workflow_run_attempt")
    if workflow["head_sha"] != source_commit:
        raise ValueError("workflow_head_sha")

    subjects = value["subjects"]
    if not isinstance(subjects, list):
        raise ValueError("subjects_array")
    roles = [subject.get("role") if isinstance(subject, dict) else None for subject in subjects]
    if len(roles) != len(SUBJECTS) or sorted(roles, key=str) != sorted(SUBJECTS):
        raise ValueError("subject_roles")
    for subject in subjects:
        validate_subject(
            subject,
            source_commit=source_commit,
            workflow=workflow,
            evidence_root=evidence_root,
        )
    return value


def assemble_manifest(
    *,
    source_commit: str,
    repository: str,
    workflow: dict[str, Any],
    subjects: Iterable[dict[str, Any]],
    expected_roles: Iterable[str] | None = None,
    evidence_root: Path | None = None,
) -> dict[str, Any]:
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source_commit": source_commit,
        "repository": repository,
        "workflow": workflow,
        "subjects": sorted(subjects, key=lambda subject: str(subject.get("role", ""))),
    }
    validate_manifest(
        manifest,
        expected_roles=expected_roles,
        evidence_root=evidence_root,
    )
    return manifest


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _object(payload, str(path))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _subject_command(args: argparse.Namespace) -> None:
    dockerfile_path = Path(args.dockerfile)
    if _sha256(dockerfile_path) != args.dockerfile_sha256:
        raise ValueError("dockerfile_sha256_mismatch")
    subject = SUBJECTS.get(args.role)
    if subject is None:
        raise ValueError("role")
    workflow = {
        "repository": args.workflow_repository,
        "workflow_ref": args.workflow_ref,
        "run_id": args.run_id,
        "run_attempt": args.run_attempt,
        "head_sha": args.source_commit,
    }
    bundle_path = Path(args.provenance_bundle)
    verification_path = Path(args.provenance_verification)
    sbom_path = Path(args.sbom_file)
    scan_path = Path(args.scan_file)
    if bundle_path.name != f"provenance-{args.role}.bundle.json":
        raise ValueError("provenance_bundle_path")
    if verification_path.name != f"provenance-{args.role}.verified.json":
        raise ValueError("provenance_verification_path")
    if sbom_path.name != f"sbom-{args.role}.spdx.json":
        raise ValueError("sbom_path")
    if scan_path.name != f"trivy-{args.role}.json":
        raise ValueError("scan_path")
    evidence_roots = {
        path.parent.resolve()
        for path in (bundle_path, verification_path, sbom_path, scan_path)
    }
    if len(evidence_roots) != 1:
        raise ValueError("provenance_evidence_root")
    artifact_name = _artifact_name(
        source_commit=args.source_commit,
        workflow=workflow,
        role=args.role,
    )
    record = {
        "role": args.role,
        "platform": PLATFORM,
        "build": {
            "context": {"path": ".", "source_commit": args.source_commit},
            "dockerfile": {"path": args.dockerfile, "sha256": args.dockerfile_sha256},
        },
        "image": {
            "subject": subject,
            "source_tag": f"{subject}:{args.source_commit}",
            "manifest_digest": args.manifest_digest,
            "immutable_ref": f"{subject}@{args.manifest_digest}",
        },
        "evidence": {
            "sbom": {
                "format": "spdx-json",
                "ref": args.sbom_ref,
                "sha256": _sha256(sbom_path),
            },
            "provenance": {
                "predicate_type": "https://slsa.dev/provenance/v1",
                "attestation_id": args.provenance_attestation_id,
                "ref": args.provenance_ref,
                "bundle_ref": (
                    f"github-artifact://{artifact_name}/provenance-{args.role}.bundle.json"
                ),
                "bundle_sha256": _sha256(bundle_path),
                "verification_ref": (
                    f"github-artifact://{artifact_name}/provenance-{args.role}.verified.json"
                ),
                "verification_sha256": _sha256(verification_path),
            },
            "signature": {
                "identity": args.signature_identity,
                "issuer": "https://token.actions.githubusercontent.com",
                "ref": args.signature_ref,
            },
            "scan": {
                "blocking_severities": ["HIGH", "CRITICAL"],
                "ref": args.scan_ref,
                "result": "passed",
                "scanner": "trivy@0.70.0",
                "sha256": _sha256(scan_path),
            },
        },
    }
    validate_subject(
        record,
        source_commit=args.source_commit,
        workflow=workflow,
        evidence_root=bundle_path.parent,
    )
    _write_json(Path(args.output), record)


def _add_expected_roles(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-role", action="append", required=True, choices=sorted(SUBJECTS))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and verify immutable release image manifests.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subject = subparsers.add_parser("subject", help="Create one digest-bound subject record.")
    subject.add_argument("--role", required=True, choices=sorted(SUBJECTS))
    subject.add_argument("--source-commit", required=True)
    subject.add_argument("--dockerfile", required=True)
    subject.add_argument("--dockerfile-sha256", required=True)
    subject.add_argument("--manifest-digest", required=True)
    subject.add_argument("--workflow-repository", required=True)
    subject.add_argument("--workflow-ref", required=True)
    subject.add_argument("--run-id", required=True)
    subject.add_argument("--run-attempt", required=True, type=int)
    subject.add_argument("--sbom-ref", required=True)
    subject.add_argument("--sbom-file", required=True)
    subject.add_argument("--provenance-attestation-id", required=True)
    subject.add_argument("--provenance-ref", required=True)
    subject.add_argument("--provenance-bundle", required=True)
    subject.add_argument("--provenance-verification", required=True)
    subject.add_argument("--signature-identity", required=True)
    subject.add_argument("--signature-ref", required=True)
    subject.add_argument("--scan-ref", required=True)
    subject.add_argument("--scan-file", required=True)
    subject.add_argument("--output", required=True)

    assemble = subparsers.add_parser("assemble", help="Assemble all required subject records.")
    assemble.add_argument("--source-commit", required=True)
    assemble.add_argument("--repository", required=True)
    assemble.add_argument("--workflow-repository", required=True)
    assemble.add_argument("--workflow-ref", required=True)
    assemble.add_argument("--run-id", required=True)
    assemble.add_argument("--run-attempt", required=True, type=int)
    assemble.add_argument("--subject-record", action="append", required=True)
    assemble.add_argument("--evidence-root", required=True)
    _add_expected_roles(assemble)
    assemble.add_argument("--output", required=True)

    verify = subparsers.add_parser("verify", help="Verify a complete ready manifest.")
    verify.add_argument("--manifest", required=True)
    verify.add_argument("--evidence-root", required=True)
    _add_expected_roles(verify)

    args = parser.parse_args()
    if args.command == "subject":
        _subject_command(args)
        return 0
    if args.command == "assemble":
        manifest = assemble_manifest(
            source_commit=args.source_commit,
            repository=args.repository,
            workflow={
                "repository": args.workflow_repository,
                "workflow_ref": args.workflow_ref,
                "run_id": args.run_id,
                "run_attempt": args.run_attempt,
                "head_sha": args.source_commit,
            },
            subjects=[_load_object(Path(path)) for path in args.subject_record],
            expected_roles=args.expected_role,
            evidence_root=Path(args.evidence_root),
        )
        _write_json(Path(args.output), manifest)
        return 0
    validate_manifest(
        _load_object(Path(args.manifest)),
        expected_roles=args.expected_role,
        evidence_root=Path(args.evidence_root),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
