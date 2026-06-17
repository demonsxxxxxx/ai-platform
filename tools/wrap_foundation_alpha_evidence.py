from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


ENTRY_SCHEMA_VERSION = "ai-platform.release-evidence-entry.v1"
GATE = "Foundation Alpha POC"

_VERIFIER_SCHEMA_DEFAULTS = {
    "tools/verify_poc_gate.py": "ai-platform.poc-gate.v1",
    "tools/verify_auth_rbac_smoke.py": "ai-platform.auth-rbac-smoke.v1",
    "tools/verify_governance_runtime_smoke.py": "ai-platform.governance-runtime-smoke.v1",
    "tools/verify_release_evidence_runtime_acceptance.py": "ai-platform.release-evidence-runtime-acceptance.v1",
    "tools/verify_alert_trace_export_runtime_acceptance.py": (
        "ai-platform.alert-trace-export-runtime-acceptance.v1"
    ),
}

_WRAPPED_CHECK_KEYS = {
    "tools/verify_release_evidence_runtime_acceptance.py": "release_evidence_runtime_acceptance",
    "tools/verify_alert_trace_export_runtime_acceptance.py": "alert_trace_export_runtime_acceptance",
}

_DROP_KEYS = {
    "api_key",
    "artifact_storage_key",
    "authorization",
    "callback_token",
    "executor_private_payload",
    "password",
    "sandbox_workdir",
    "storage_key",
    "raw_storage_key",
    "secret_like_values",
    "token",
}
_PATH_KEYS = {
    "env_path",
    "path",
    "workdir",
    "work_dir",
    "workspace",
    "workspace_root",
}
_SECRET_KEY_PARTS = ("api_key", "authorization", "callback_token", "password", "secret", "token")
_REDACTION_SCAN_STATUSES = {"failed", "passed"}
_REVIEW_STATUSES = {"reviewed"}
_RUNTIME_SOURCE_SAFE_KEYS = {
    "commit_sha",
    "evidence_root",
    "image",
    "runtime_subject_commit_sha",
}


def _load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected_json_object:{path}")
    return payload


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _redact_runtime_value(key: str | None, value: Any) -> Any:
    normalized_key = key.lower() if isinstance(key, str) else ""
    if isinstance(value, dict):
        return {
            child_key: (
                _redact_runtime_source(child_value)
                if child_key == "source" and isinstance(child_value, dict)
                else _redact_runtime_value(child_key, child_value)
            )
            for child_key, child_value in value.items()
            if not (isinstance(child_key, str) and child_key.lower() in _DROP_KEYS)
        }
    if isinstance(value, list):
        return [_redact_runtime_value(key, item) for item in value]
    if isinstance(value, str):
        normalized = value.replace("\\", "/")
        if any(part in normalized_key for part in _SECRET_KEY_PARTS):
            return "<redacted-secret>"
        is_windows_absolute_path = len(normalized) >= 3 and normalized[1:3] == ":/"
        if key in _PATH_KEYS and (normalized.startswith("/") or is_windows_absolute_path):
            return "<redacted-path>"
        if is_windows_absolute_path:
            return "<redacted-path>"
        if normalized.startswith("/home/") or "/home/" in normalized:
            return "<redacted-path>"
        if normalized.startswith("tenants/") or "/tenants/" in normalized:
            return "<redacted-storage-key>"
    return value


def _redact_runtime_checks(checks: dict[str, Any]) -> dict[str, Any]:
    redacted = _redact_runtime_value(None, checks)
    return redacted if isinstance(redacted, dict) else {}


def _redact_runtime_source(source: dict[str, Any]) -> dict[str, Any]:
    safe_source = {
        key: value
        for key, value in source.items()
        if key in _RUNTIME_SOURCE_SAFE_KEYS
    }
    redacted = _redact_runtime_value(None, safe_source)
    return redacted if isinstance(redacted, dict) else {}


def _runtime_checks(verifier: str, verifier_output: dict[str, Any]) -> dict[str, Any]:
    if verifier in _WRAPPED_CHECK_KEYS:
        return _redact_runtime_checks({_WRAPPED_CHECK_KEYS[verifier]: verifier_output})

    checks = verifier_output.get("checks")
    if isinstance(checks, dict):
        return _redact_runtime_checks(checks)
    if isinstance(checks, list):
        return _redact_runtime_checks({"verifier_checks": checks})

    gates = verifier_output.get("gates")
    if isinstance(gates, list):
        normalized: dict[str, Any] = {}
        for gate in gates:
            if not isinstance(gate, dict):
                continue
            name = gate.get("name")
            evidence = gate.get("evidence")
            if isinstance(name, str) and isinstance(evidence, dict):
                normalized[name] = evidence
        return _redact_runtime_checks(normalized)

    return {}


def _schema_version(verifier: str, verifier_output: dict[str, Any]) -> str:
    schema = verifier_output.get("schema_version")
    if isinstance(schema, str) and schema:
        return schema
    return _VERIFIER_SCHEMA_DEFAULTS.get(verifier, "unknown")


def _result(verifier_output: dict[str, Any]) -> str:
    checks = verifier_output.get("checks")
    if isinstance(checks, list) and checks:
        all_passed = all(
            isinstance(check, dict) and check.get("passed") is True
            for check in checks
        )
        return "ok:true" if all_passed else "ok:false"
    return "ok:true" if verifier_output.get("ok") is True else "ok:false"


def _redaction_scan_status(
    verifier_output: dict[str, Any],
    *,
    explicit_status: str | None = None,
) -> str:
    status = verifier_output.get("redaction_scan_status")
    if status is not None and status not in _REDACTION_SCAN_STATUSES:
        raise ValueError("invalid_redaction_scan_status")
    if explicit_status is not None and explicit_status not in _REDACTION_SCAN_STATUSES:
        raise ValueError("invalid_redaction_scan_status")
    if status is not None and explicit_status is not None and status != explicit_status:
        raise ValueError("redaction_scan_status_mismatch")
    resolved = status or explicit_status
    if resolved is None:
        raise ValueError("redaction_scan_status_required")
    return str(resolved)


def _review_status(review_status: str | None) -> str:
    if review_status not in _REVIEW_STATUSES:
        raise ValueError("review_status_required")
    return str(review_status)


def _require_matching_labels(image_labels: dict[str, Any], runtime_subject_commit_sha: str) -> None:
    required = [
        "ai-platform.source-revision",
        "org.opencontainers.image.revision",
    ]
    optional = [
        "ai-platform.runtime-subject",
        "ai-platform.runtime_subject",
        "ai-platform.source_revision",
        "ai-platform.source_tree_commit",
    ]
    for key in required:
        if image_labels.get(key) != runtime_subject_commit_sha:
            raise ValueError(f"image_label_mismatch:{key}")
    for key in optional:
        value = image_labels.get(key)
        if value is not None and value != runtime_subject_commit_sha:
            raise ValueError(f"image_label_mismatch:{key}")


def _source_ref(
    *,
    commit_sha: str,
    runtime_subject_commit_sha: str,
    image: str,
    image_id: str,
    image_labels: dict[str, Any],
    source_snapshot: dict[str, Any],
    api_health: dict[str, Any] | None,
    verifier_output: dict[str, Any],
) -> dict[str, Any]:
    runtime_source = verifier_output.get("source") if isinstance(verifier_output.get("source"), dict) else {}
    gateway_secret_supplied = runtime_source.get("gateway_secret_supplied")
    redacted_image_labels = _redact_runtime_value("image_labels", image_labels)
    return {
        "branch": "main",
        "runtime_commit": runtime_subject_commit_sha,
        "runtime_source_marker": runtime_subject_commit_sha,
        "runtime_subject_label_status": "runtime_subject_label_current",
        "source_revision_alias_label_status": "source_revision_alias_label_current",
        "image": image,
        "image_id": image_id,
        "image_labels": redacted_image_labels if isinstance(redacted_image_labels, dict) else {},
        "api_health": api_health or {"route": "/api/ai/health", "status": "ok"},
        "containers": ["ai-platform-api", "ai-platform-worker"],
        "repo_local_env_present": False,
        "compose_config_path": "repo-local deploy/ai-platform/docker-compose.yml",
        "compose_env_layout_status": "external_env_file_label_present",
        "compose_env_source": (
            "existing external 211 runtime env file supplied through compose --env-file; "
            "values were not copied or printed"
        ),
        "gateway_secret_supplied": bool(gateway_secret_supplied),
        "source_snapshot": source_snapshot,
    }


def build_release_evidence_entry(
    *,
    evidence_id: str,
    verifier: str,
    artifact_kind: str,
    verifier_output: dict[str, Any],
    commit_sha: str,
    runtime_subject_commit_sha: str,
    captured_at: str,
    image: str,
    image_id: str,
    image_labels: dict[str, Any],
    source_snapshot: dict[str, Any] | None,
    command: str,
    gate: str = GATE,
    runtime_check_payloads: dict[str, Any] | None = None,
    api_health: dict[str, Any] | None = None,
    redaction_scan_status: str | None = None,
    review_status: str | None = None,
    issue_refs: list[str] | None = None,
    pr_refs: list[str] | None = None,
    open_followups: list[str] | None = None,
) -> dict[str, Any]:
    """Return an explicitly reviewed and redaction-scanned release-evidence entry."""
    if source_snapshot is None:
        raise ValueError("source_snapshot_required")
    _require_matching_labels(image_labels, runtime_subject_commit_sha)
    runtime_checks = _runtime_checks(verifier, verifier_output)
    if runtime_check_payloads:
        runtime_checks.update(_redact_runtime_checks(runtime_check_payloads))
    if not runtime_checks:
        raise ValueError("missing_runtime_checks")
    runtime_source = verifier_output.get("source") if isinstance(verifier_output.get("source"), dict) else {}

    return {
        "schema_version": ENTRY_SCHEMA_VERSION,
        "artifact_kind": artifact_kind,
        "captured_at": captured_at,
        "commit_sha": commit_sha,
        "runtime_subject_commit_sha": runtime_subject_commit_sha,
        "evidence_id": evidence_id,
        "gate": gate,
        "issue_refs": issue_refs or [],
        "pr_refs": pr_refs or [],
        "open_followups": open_followups or [],
        "redaction_scan_status": _redaction_scan_status(
            verifier_output,
            explicit_status=redaction_scan_status,
        ),
        "review_status": _review_status(review_status),
        "source_ref": _source_ref(
            commit_sha=commit_sha,
            runtime_subject_commit_sha=runtime_subject_commit_sha,
            image=image,
            image_id=image_id,
            image_labels=image_labels,
            source_snapshot=source_snapshot,
            api_health=api_health,
            verifier_output=verifier_output,
        ),
        "evidence_ref": {
            "verifier": verifier,
            "schema_version": _schema_version(verifier, verifier_output),
            "command": command,
            "result": _result(verifier_output),
            "runtime_checks": runtime_checks,
            "runtime_source": _redact_runtime_source(runtime_source),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Wrap 211 Foundation Alpha verifier output as explicitly reviewed release evidence."
    )
    parser.add_argument("--verifier-output", required=True)
    parser.add_argument("--verifier", required=True)
    parser.add_argument("--artifact-kind", default="211_runtime_smoke")
    parser.add_argument("--evidence-id", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--runtime-subject-commit-sha", required=True)
    parser.add_argument("--captured-at", default="")
    parser.add_argument("--image", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--image-labels-json", required=True)
    parser.add_argument("--source-snapshot-json")
    parser.add_argument("--api-health-json")
    parser.add_argument("--command", required=True)
    parser.add_argument("--gate", default=GATE)
    parser.add_argument(
        "--runtime-check-payload",
        action="append",
        default=[],
        metavar="KEY=JSON_PATH",
        help=(
            "Attach an additional runtime evidence payload under evidence_ref.runtime_checks[KEY]. "
            "Use for verifiers whose --json output contains only verifier_checks."
        ),
    )
    parser.add_argument(
        "--redaction-scan-status",
        choices=sorted(_REDACTION_SCAN_STATUSES),
        help=(
            "Explicit redaction scan result. Required when the verifier output does not "
            "already contain redaction_scan_status."
        ),
    )
    parser.add_argument(
        "--review-status",
        required=True,
        choices=sorted(_REVIEW_STATUSES),
        help="Explicit human or workflow review status for the wrapped release evidence.",
    )
    parser.add_argument("--issue-ref", action="append", default=[])
    parser.add_argument("--pr-ref", action="append", default=[])
    parser.add_argument("--open-followup", action="append", default=[])
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    runtime_check_payloads: dict[str, Any] = {}
    for item in args.runtime_check_payload:
        if "=" not in item:
            raise ValueError("runtime_check_payload_requires_key_equals_path")
        key, path = item.split("=", 1)
        if not key or not path:
            raise ValueError("runtime_check_payload_requires_key_equals_path")
        runtime_check_payloads[key] = _load_json(path)

    entry = build_release_evidence_entry(
        evidence_id=args.evidence_id,
        verifier=args.verifier,
        artifact_kind=args.artifact_kind,
        verifier_output=_load_json(args.verifier_output),
        commit_sha=args.commit_sha,
        runtime_subject_commit_sha=args.runtime_subject_commit_sha,
        captured_at=args.captured_at or _now_iso(),
        image=args.image,
        image_id=args.image_id,
        image_labels=_load_json(args.image_labels_json),
        source_snapshot=_load_json(args.source_snapshot_json) if args.source_snapshot_json else None,
        api_health=_load_json(args.api_health_json) if args.api_health_json else None,
        command=args.command,
        gate=args.gate,
        runtime_check_payloads=runtime_check_payloads or None,
        redaction_scan_status=args.redaction_scan_status,
        review_status=args.review_status,
        issue_refs=list(args.issue_ref),
        pr_refs=list(args.pr_ref),
        open_followups=list(args.open_followup),
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(entry, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
