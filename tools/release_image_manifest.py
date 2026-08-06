from __future__ import annotations

import argparse
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
_PROVENANCE_KEYS = {"predicate_type", "ref"}
_SIGNATURE_KEYS = {"identity", "issuer", "ref"}
_SCAN_KEYS = {"blocking_severities", "ref", "result", "scanner", "sha256"}


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


def _validate_ref(value: Any, name: str) -> str:
    ref = _nonempty_string(value, name)
    if any(character.isspace() for character in ref):
        raise ValueError(name)
    return ref


def validate_subject(subject: Any, *, source_commit: str) -> dict[str, Any]:
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
    _fullmatch(_HEX_SHA256, sbom["sha256"], "sbom_sha256")

    provenance = _object(evidence["provenance"], "provenance")
    _exact_keys(provenance, _PROVENANCE_KEYS, "provenance")
    if provenance["predicate_type"] != "https://slsa.dev/provenance/v1":
        raise ValueError("provenance_predicate_type")
    provenance_ref = _validate_ref(provenance["ref"], "provenance_ref")
    if re.fullmatch(
        r"https://github\.com/demonsxxxxxx/ai-platform/attestations/[A-Za-z0-9_-]+",
        provenance_ref,
    ) is None:
        raise ValueError("provenance_ref")

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
    if scan["ref"] != (
        f"github-artifact://release-image-evidence-{source_commit}/trivy-{role}.json"
    ):
        raise ValueError("scan_ref")
    _fullmatch(_HEX_SHA256, scan["sha256"], "scan_sha256")
    return value


def validate_manifest(
    manifest: Any,
    *,
    expected_roles: set[str] | None = None,
) -> dict[str, Any]:
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
    validated = [validate_subject(subject, source_commit=source_commit) for subject in subjects]
    roles = [subject["role"] for subject in validated]
    required_roles = expected_roles if expected_roles is not None else set(SUBJECTS)
    if len(roles) != len(required_roles) or set(roles) != required_roles:
        raise ValueError("subject_roles")
    return value


def assemble_manifest(
    *,
    source_commit: str,
    repository: str,
    workflow: dict[str, Any],
    subjects: Iterable[dict[str, Any]],
    expected_roles: set[str] | None = None,
) -> dict[str, Any]:
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source_commit": source_commit,
        "repository": repository,
        "workflow": workflow,
        "subjects": sorted(subjects, key=lambda subject: str(subject.get("role", ""))),
    }
    validate_manifest(manifest, expected_roles=expected_roles)
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
            "sbom": {"format": "spdx-json", "ref": args.sbom_ref, "sha256": args.sbom_sha256},
            "provenance": {
                "predicate_type": "https://slsa.dev/provenance/v1",
                "ref": args.provenance_ref,
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
                "sha256": args.scan_sha256,
            },
        },
    }
    validate_subject(record, source_commit=args.source_commit)
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
    subject.add_argument("--sbom-ref", required=True)
    subject.add_argument("--sbom-sha256", required=True)
    subject.add_argument("--provenance-ref", required=True)
    subject.add_argument("--signature-identity", required=True)
    subject.add_argument("--signature-ref", required=True)
    subject.add_argument("--scan-ref", required=True)
    subject.add_argument("--scan-sha256", required=True)
    subject.add_argument("--output", required=True)

    assemble = subparsers.add_parser("assemble", help="Assemble all required subject records.")
    assemble.add_argument("--source-commit", required=True)
    assemble.add_argument("--repository", required=True)
    assemble.add_argument("--workflow-repository", required=True)
    assemble.add_argument("--workflow-ref", required=True)
    assemble.add_argument("--run-id", required=True)
    assemble.add_argument("--run-attempt", required=True, type=int)
    assemble.add_argument("--subject-record", action="append", required=True)
    _add_expected_roles(assemble)
    assemble.add_argument("--output", required=True)

    verify = subparsers.add_parser("verify", help="Verify a complete ready manifest.")
    verify.add_argument("--manifest", required=True)
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
            expected_roles=set(args.expected_role),
        )
        _write_json(Path(args.output), manifest)
        return 0
    validate_manifest(
        _load_object(Path(args.manifest)),
        expected_roles=set(args.expected_role),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
