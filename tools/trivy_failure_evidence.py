from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import unicodedata
from pathlib import Path
from typing import Any


MAX_TRIVY_JSON_BYTES = 4 * 1024 * 1024
MAX_DEPTH = 32
MAX_NODES = 50_000
MAX_STRING_LENGTH = 8_192
MAX_DICT_ITEMS = 128
MAX_LIST_ITEMS = 10_000
MAX_RESULTS = 256
MAX_VULNERABILITIES = 4_096

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SOURCE = re.compile(r"[0-9a-f]{40}\Z")
_RUN = re.compile(r"[1-9][0-9]*\Z")
_SECRET_KEY_MARKERS = frozenset(
    {
        "authorization",
        "password",
        "privatekey",
        "secret",
        "token",
    }
)
_SECRET_KEY_SEQUENCES = (("access", "token"), ("private", "key"))
_SECRET_VALUE = re.compile(
    r"(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|authorization\s*:\s*bearer\s+|password=)",
    re.IGNORECASE,
)
_TOP_KEYS = {
    "ArtifactID",
    "ArtifactName",
    "ArtifactType",
    "CreatedAt",
    "Metadata",
    "ReportID",
    "Results",
    "SchemaVersion",
    "Trivy",
}
_METADATA_KEYS = {
    "DiffIDs",
    "ImageConfig",
    "ImageID",
    "Layers",
    "OS",
    "Reference",
    "RepoDigests",
    "RepoTags",
    "Size",
}
_RESULT_KEYS = {"Class", "Packages", "Target", "Type", "Vulnerabilities"}
_VULNERABILITY_KEYS = {
    "CVSS",
    "CweIDs",
    "DataSource",
    "Description",
    "Fingerprint",
    "FixedVersion",
    "InstalledVersion",
    "LastModifiedDate",
    "Layer",
    "PkgID",
    "PkgIdentifier",
    "PkgName",
    "PrimaryURL",
    "PublishedDate",
    "References",
    "Severity",
    "SeveritySource",
    "Status",
    "Title",
    "VendorIDs",
    "VendorSeverity",
    "VulnerabilityID",
}
_SUBJECTS = {
    "backend": "ghcr.io/demonsxxxxxx/ai-platform-backend",
    "frontend": "ghcr.io/demonsxxxxxx/ai-platform-frontend",
}


def _error(reason: str) -> ValueError:
    return ValueError(reason)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _error("trivy_diagnostic_json_duplicate_key")
        value[key] = item
    return value


def _reject_constant(_: str) -> None:
    raise _error("trivy_diagnostic_json")


def _is_reparse(value: os.stat_result) -> bool:
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(value, "st_file_attributes", 0) & reparse)


def _file_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        stat.S_IFMT(value.st_mode),
        value.st_size,
        getattr(value, "st_mtime_ns", 0),
    )


def _regular_identity(value: os.stat_result, reason: str) -> tuple[int, int, int, int, int]:
    if not stat.S_ISREG(value.st_mode) or _is_reparse(value):
        raise _error(reason)
    return _file_identity(value)


def _open_flags(mode: int) -> int:
    return (
        mode
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )


def _bounded_read(path: Path) -> bytes:
    try:
        expected = _regular_identity(os.lstat(path), "trivy_diagnostic_path")
        descriptor = os.open(path, _open_flags(os.O_RDONLY))
    except OSError as exc:
        raise _error("trivy_diagnostic_path") from exc
    try:
        opened = _regular_identity(os.fstat(descriptor), "trivy_diagnostic_path")
        if opened != expected:
            raise _error("trivy_diagnostic_path")
        if opened[3] > MAX_TRIVY_JSON_BYTES:
            raise _error("trivy_diagnostic_size")

        chunks: list[bytes] = []
        total = 0
        while total <= MAX_TRIVY_JSON_BYTES:
            chunk = os.read(descriptor, min(64 * 1024, MAX_TRIVY_JSON_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)

        after_read = _regular_identity(os.fstat(descriptor), "trivy_diagnostic_path")
        current_path = _regular_identity(os.lstat(path), "trivy_diagnostic_path")
        if opened != after_read or opened != current_path:
            raise _error("trivy_diagnostic_path")
        raw = b"".join(chunks)
        if len(raw) > MAX_TRIVY_JSON_BYTES or len(raw) != opened[3]:
            raise _error("trivy_diagnostic_size")
        return raw
    except OSError as exc:
        raise _error("trivy_diagnostic_path") from exc
    finally:
        os.close(descriptor)


def _validate_string(value: str, *, allow_line_feed: bool = False) -> None:
    if len(value) > MAX_STRING_LENGTH or _SECRET_VALUE.search(value):
        raise _error("trivy_diagnostic_string")
    for character in value:
        if allow_line_feed and character == "\n":
            continue
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF or unicodedata.category(character) in {"Cc", "Cf"}:
            raise _error("trivy_diagnostic_string")


def _key_tokens(value: str) -> tuple[str, ...]:
    tokens: list[str] = []
    run: list[str] = []
    for character in (*value, " "):
        if character.isalnum():
            run.append(character)
            continue
        if not run:
            continue
        start = 0
        for index in range(1, len(run)):
            previous = run[index - 1]
            current = run[index]
            following = run[index + 1] if index + 1 < len(run) else ""
            boundary = (
                (previous.islower() or previous.isdigit()) and current.isupper()
            ) or (
                previous.isupper()
                and current.isupper()
                and following.islower()
            )
            if boundary:
                tokens.append("".join(run[start:index]).casefold())
                start = index
        tokens.append("".join(run[start:]).casefold())
        run = []
    return tuple(tokens)


def _is_secret_key(value: str) -> bool:
    tokens = _key_tokens(value)
    if any(token in _SECRET_KEY_MARKERS for token in tokens):
        return True
    return any(
        tuple(tokens[index : index + len(sequence)]) == sequence
        for sequence in _SECRET_KEY_SEQUENCES
        for index in range(len(tokens) - len(sequence) + 1)
    )


def _validate_bounds(document: Any) -> None:
    nodes = 0
    stack: list[tuple[Any, int, str | None]] = [(document, 0, None)]
    while stack:
        value, depth, field = stack.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise _error("trivy_diagnostic_bounds")
        if isinstance(value, dict):
            if len(value) > MAX_DICT_ITEMS:
                raise _error("trivy_diagnostic_bounds")
            for key, item in value.items():
                if not isinstance(key, str) or _is_secret_key(key):
                    raise _error("trivy_diagnostic_key")
                _validate_string(key)
                stack.append((item, depth + 1, key))
        elif isinstance(value, list):
            if len(value) > MAX_LIST_ITEMS:
                raise _error("trivy_diagnostic_bounds")
            stack.extend((item, depth + 1, None) for item in value)
        elif isinstance(value, str):
            _validate_string(value, allow_line_feed=field == "Description")
        elif value is not None and not isinstance(value, (bool, int, float)):
            raise _error("trivy_diagnostic_type")


def _mapping(value: Any, reason: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(reason)
    return value


def _string(value: Any, reason: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        raise _error(reason)
    return value


def _validate_report(document: Any, *, image_ref: str) -> tuple[int, dict[str, int]]:
    report = _mapping(document, "trivy_diagnostic_document")
    if set(report) - _TOP_KEYS:
        raise _error("trivy_diagnostic_document_keys")
    if report.get("SchemaVersion") != 2:
        raise _error("trivy_diagnostic_schema")
    if report.get("ArtifactName") != image_ref or report.get("ArtifactType") != "container_image":
        raise _error("trivy_diagnostic_subject")
    for optional in ("ArtifactID", "CreatedAt", "ReportID"):
        if optional in report:
            _string(report[optional], "trivy_diagnostic_document")
    if "Metadata" in report:
        metadata = _mapping(report["Metadata"], "trivy_diagnostic_metadata")
        if set(metadata) - _METADATA_KEYS:
            raise _error("trivy_diagnostic_metadata_keys")
    if "Trivy" in report:
        _mapping(report["Trivy"], "trivy_diagnostic_version")
    results = report.get("Results")
    if not isinstance(results, list) or len(results) > MAX_RESULTS:
        raise _error("trivy_diagnostic_results")

    blocking = 0
    severity_counts = {"HIGH": 0, "CRITICAL": 0}
    for item in results:
        result = _mapping(item, "trivy_diagnostic_result")
        if set(result) - _RESULT_KEYS:
            raise _error("trivy_diagnostic_result_keys")
        for required in ("Target", "Class", "Type"):
            _string(result.get(required), "trivy_diagnostic_result")
        packages = result.get("Packages", [])
        if not isinstance(packages, list) or packages:
            raise _error("trivy_diagnostic_packages")
        vulnerabilities = result.get("Vulnerabilities", [])
        if not isinstance(vulnerabilities, list) or len(vulnerabilities) > MAX_VULNERABILITIES:
            raise _error("trivy_diagnostic_vulnerabilities")
        for item in vulnerabilities:
            vulnerability = _mapping(item, "trivy_diagnostic_vulnerability")
            if set(vulnerability) - _VULNERABILITY_KEYS:
                raise _error("trivy_diagnostic_vulnerability_keys")
            for required in ("VulnerabilityID", "PkgName", "InstalledVersion", "Severity"):
                _string(vulnerability.get(required), "trivy_diagnostic_vulnerability")
            if "Fingerprint" in vulnerability:
                fingerprint = _string(
                    vulnerability["Fingerprint"], "trivy_diagnostic_vulnerability"
                )
                if not _DIGEST.fullmatch(fingerprint):
                    raise _error("trivy_diagnostic_vulnerability")
            severity = vulnerability["Severity"]
            if severity not in severity_counts:
                raise _error("trivy_diagnostic_severity")
            severity_counts[severity] += 1
            blocking += 1
    if blocking == 0:
        raise _error("trivy_diagnostic_no_blockers")
    return blocking, severity_counts


def _validate_filename(raw: str, expected: str) -> Path:
    path = Path(raw)
    if raw != expected or path.name != expected or len(path.parts) != 1:
        raise _error("trivy_diagnostic_path")
    return path


def _write_exclusive(path: Path, payload: dict[str, Any]) -> None:
    serialized = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    try:
        descriptor = os.open(path, _open_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL), 0o600)
    except OSError as exc:
        raise _error("trivy_diagnostic_output") from exc
    try:
        created = _regular_identity(os.fstat(descriptor), "trivy_diagnostic_output")
        written = 0
        while written < len(serialized):
            count = os.write(descriptor, serialized[written:])
            if count <= 0:
                raise _error("trivy_diagnostic_output")
            written += count
        os.fsync(descriptor)
        completed = _regular_identity(os.fstat(descriptor), "trivy_diagnostic_output")
        current_path = _regular_identity(os.lstat(path), "trivy_diagnostic_output")
        if created[:3] != completed[:3] or completed != current_path or completed[3] != len(serialized):
            raise _error("trivy_diagnostic_output")
    except Exception as exc:
        # A pathname cannot be conditionally unlinked by inode on every supported runner.
        # Leave any partial entry fail-closed; the workflow uploads only on capture success.
        if isinstance(exc, ValueError):
            raise
        raise _error("trivy_diagnostic_output") from exc
    finally:
        os.close(descriptor)


def capture(args: argparse.Namespace) -> None:
    if args.role not in _SUBJECTS:
        raise _error("trivy_diagnostic_role")
    if not _SOURCE.fullmatch(args.source_commit) or args.source_commit != args.github_sha:
        raise _error("trivy_diagnostic_source")
    repository = "demonsxxxxxx/ai-platform"
    expected_workflow_ref = (
        f"{repository}/.github/workflows/ai-platform-packaging-publish.yml@refs/heads/main"
    )
    if args.workflow_repository != repository:
        raise _error("trivy_diagnostic_repository")
    if args.workflow_ref != expected_workflow_ref:
        raise _error("trivy_diagnostic_workflow")
    if not _RUN.fullmatch(args.run_id) or not _RUN.fullmatch(args.run_attempt):
        raise _error("trivy_diagnostic_run")
    if not _DIGEST.fullmatch(args.manifest_digest):
        raise _error("trivy_diagnostic_digest")
    subject = _SUBJECTS[args.role]
    if args.image_ref != f"{subject}@{args.manifest_digest}":
        raise _error("trivy_diagnostic_subject")

    scan_name = f"trivy-{args.role}.json"
    output_name = f"trivy-failure-diagnostic-{args.role}.json"
    scan_path = _validate_filename(args.scan_file, scan_name)
    output_path = _validate_filename(args.output, output_name)
    raw = _bounded_read(scan_path)
    try:
        document = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise _error("trivy_diagnostic_json") from exc
    _validate_bounds(document)
    blocking, severity_counts = _validate_report(document, image_ref=args.image_ref)
    payload = {
        "authority": "untrusted_failure_diagnostic",
        "image_ref": args.image_ref,
        "manifest_digest": args.manifest_digest,
        "platform": "linux/amd64",
        "reason_code": "trivy_blocking_findings",
        "role": args.role,
        "run_attempt": args.run_attempt,
        "run_id": args.run_id,
        "schema_version": "ai-platform.trivy-failure-diagnostic.v1",
        "source_commit": args.source_commit,
        "trivy_policy": {
            "exit_code": 1,
            "ignore_unfixed": False,
            "package_types": ["os", "library"],
            "severity": ["HIGH", "CRITICAL"],
            "version": "v0.70.0",
        },
        "trivy_report": {
            "blocking_vulnerability_count": blocking,
            "byte_size": len(raw),
            "severity_counts": severity_counts,
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "workflow_ref": args.workflow_ref,
        "workflow_repository": args.workflow_repository,
    }
    _write_exclusive(output_path, payload)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture_parser = subparsers.add_parser("capture")
    for name in (
        "role",
        "source-commit",
        "github-sha",
        "workflow-repository",
        "workflow-ref",
        "run-id",
        "run-attempt",
        "manifest-digest",
        "image-ref",
        "scan-file",
        "output",
    ):
        capture_parser.add_argument(f"--{name}", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "capture":
            capture(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
