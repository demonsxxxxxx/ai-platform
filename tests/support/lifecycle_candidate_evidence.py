"""Validate maintainer-run lifecycle L-layer candidate evidence bundles.

The validator checks provenance bindings and the presence of redacted,
content-addressed observations. It does not attest that the observations are
authentic; an authorized reviewer must inspect their origin before recording L
acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "ai-platform.lifecycle-candidate-evidence.v2"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
GIT_SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
IMAGE_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
REQUIRED_DEPENDENCIES = (
    "python",
    "postgresql",
    "redis",
    "minio",
    "agent_sdk",
    "provider",
)
REQUIRED_SCENARIOS = {"R01", "R14"}
REQUIRED_ARTIFACTS = {
    "image_inspect",
    "security_profile",
    "provider_acceptance",
    "sdk_invocation",
    "callback_receipt",
    "answer_delivery",
    "file_download",
    "resource_stop",
    "native_resume",
}
PLACEHOLDER_VALUES = {"", "unknown", "n/a", "none", "mock", "not-installed"}
MAX_ARTIFACT_BYTES = 256 * 1024


class EvidenceValidationError(ValueError):
    """The evidence report is incomplete or does not match its source subject."""


def _git(root: Path, *arguments: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=True,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise EvidenceValidationError("unable to resolve candidate Git source") from error
    return completed.stdout


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def capture_candidate_source_binding(repo_root: Path) -> dict[str, object]:
    """Return the exact HEAD, non-ignored source-tree, and lockfile binding."""
    root = repo_root.resolve()
    head_sha = _git(root, "rev-parse", "HEAD").decode("ascii").strip()
    if not GIT_SHA_PATTERN.fullmatch(head_sha):
        raise EvidenceValidationError("candidate HEAD is not a full Git SHA")

    names = _git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    paths = sorted({name for name in names.split(b"\0") if name})
    if not paths:
        raise EvidenceValidationError("candidate source tree is empty")

    tree_digest = hashlib.sha256()
    for raw_name in paths:
        try:
            relative_name = raw_name.decode("utf-8")
        except UnicodeDecodeError as error:
            raise EvidenceValidationError("candidate path is not valid UTF-8") from error
        path = root / relative_name
        if not path.resolve().is_relative_to(root):
            raise EvidenceValidationError("candidate path resolves outside the worktree")
        tree_digest.update(raw_name + b"\0")
        if path.is_symlink():
            target = path.readlink().as_posix().encode("utf-8")
            tree_digest.update(b"symlink\0" + target + b"\0")
        elif path.is_file():
            tree_digest.update(b"file\0" + path.read_bytes() + b"\0")
        else:
            # A deleted tracked path changes the candidate snapshot and must not
            # disappear from its digest.
            tree_digest.update(b"deleted\0")

    lock_path = root / "uv.lock"
    if not lock_path.is_file():
        raise EvidenceValidationError("candidate uv.lock is missing")

    dirty = bool(_git(root, "status", "--porcelain", "--untracked-files=all").strip())
    return {
        "head_sha": head_sha,
        "dirty_source_sha256": tree_digest.hexdigest(),
        "lock_sha256": _sha256(lock_path.read_bytes()),
        "dirty": dirty,
    }


def _require_object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceValidationError(f"{name} must be an object")
    return value


def _require_text(value: object, name: str) -> str:
    if not isinstance(value, str) or value.strip().lower() in PLACEHOLDER_VALUES:
        raise EvidenceValidationError(f"{name} must contain an observed value")
    return value.strip()


def _require_bool(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise EvidenceValidationError(f"{name} must be a boolean observation")
    return value


def _require_hash(value: object, name: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise EvidenceValidationError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _artifact_payloads(
    bundle_root: Path, report: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    records = report.get("evidence_artifacts")
    if not isinstance(records, list):
        raise EvidenceValidationError("evidence_artifacts must be a list")

    payloads: dict[str, dict[str, Any]] = {}
    root = bundle_root.resolve()
    for index, record_value in enumerate(records):
        record = _require_object(record_value, f"evidence_artifacts[{index}]")
        kind = _require_text(record.get("kind"), f"evidence_artifacts[{index}].kind")
        if kind not in REQUIRED_ARTIFACTS:
            raise EvidenceValidationError(f"unexpected evidence artifact kind: {kind}")
        if kind in payloads:
            raise EvidenceValidationError(f"duplicate evidence artifact kind: {kind}")
        relative_path = _require_text(
            record.get("path"), f"evidence_artifacts[{index}].path"
        )
        artifact_path = root / relative_path
        if artifact_path.is_symlink() or not artifact_path.resolve().is_relative_to(root):
            raise EvidenceValidationError(f"evidence artifact escapes bundle: {relative_path}")
        if not artifact_path.is_file():
            raise EvidenceValidationError(f"evidence artifact is missing: {relative_path}")
        content = artifact_path.read_bytes()
        if not content or len(content) > MAX_ARTIFACT_BYTES:
            raise EvidenceValidationError(f"evidence artifact has invalid size: {relative_path}")
        digest = _require_hash(record.get("sha256"), f"evidence_artifacts[{index}].sha256")
        if _sha256(content) != digest:
            raise EvidenceValidationError(f"evidence artifact digest mismatch: {relative_path}")
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise EvidenceValidationError(
                f"evidence artifact is not redacted JSON: {relative_path}"
            ) from error
        payloads[kind] = _require_object(payload, f"artifact {kind}")

    missing = REQUIRED_ARTIFACTS - payloads.keys()
    if missing:
        raise EvidenceValidationError(
            "missing chain evidence artifacts: " + ", ".join(sorted(missing))
        )
    return payloads


def _validate_artifact_chain(
    payloads: dict[str, dict[str, Any]],
    dependencies: dict[str, Any],
    expected_image_id: str,
    binding: dict[str, object],
) -> None:
    image_inspect = payloads["image_inspect"]
    if image_inspect.get("id") != expected_image_id:
        raise EvidenceValidationError("image inspection artifact id does not match candidate")
    image_labels = _require_object(image_inspect.get("labels"), "image_inspect.labels")
    expected_labels = {
        "ai-platform.source-commit": binding["head_sha"],
        "ai-platform.source-tree-sha256": binding["dirty_source_sha256"],
        "ai-platform.uv-lock-sha256": binding["lock_sha256"],
        "ai-platform.build-dirty": str(binding["dirty"]).lower(),
    }
    for name, expected in expected_labels.items():
        if image_labels.get(name) != expected:
            raise EvidenceValidationError(f"candidate image label does not match at {name}")

    profile_report = _require_object(payloads["security_profile"], "security_profile artifact")
    _require_text(profile_report.get("profile_id"), "security_profile artifact.profile_id")
    _require_hash(profile_report.get("sha256"), "security_profile artifact.sha256")
    if profile_report.get("enforced") is not True:
        raise EvidenceValidationError("security profile artifact does not show enforcement")

    accepted = payloads["provider_acceptance"]
    if _require_bool(accepted.get("accepted"), "provider_acceptance.accepted") is not True:
        raise EvidenceValidationError("provider did not record an accepted execution")
    run_id = _require_text(accepted.get("run_id"), "provider_acceptance.run_id")
    attempt_id = _require_text(accepted.get("attempt_id"), "provider_acceptance.attempt_id")
    operation_id = _require_text(accepted.get("operation_id"), "provider_acceptance.operation_id")
    resource_id = _require_text(accepted.get("resource_id"), "provider_acceptance.resource_id")
    if accepted.get("image_id") != expected_image_id:
        raise EvidenceValidationError("provider acceptance used a different image id")
    provider_name = _require_text(accepted.get("provider_name"), "provider_acceptance.provider_name")
    provider_version = _require_text(
        accepted.get("provider_version"), "provider_acceptance.provider_version"
    )
    if provider_version != dependencies["provider"]:
        raise EvidenceValidationError("provider evidence version differs from dependency inventory")

    sdk = payloads["sdk_invocation"]
    if _require_bool(sdk.get("sdk_used"), "sdk_invocation.sdk_used") is not True:
        raise EvidenceValidationError("evidence does not show an actual SDK invocation")
    _require_text(sdk.get("sdk_name"), "sdk_invocation.sdk_name")
    if _require_text(sdk.get("sdk_version"), "sdk_invocation.sdk_version") != dependencies[
        "agent_sdk"
    ]:
        raise EvidenceValidationError("SDK evidence version differs from dependency inventory")
    invocation_id = _require_text(sdk.get("invocation_id"), "sdk_invocation.invocation_id")
    session_id = _require_text(sdk.get("session_id"), "sdk_invocation.session_id")
    sdk_binding = {
        field: _require_text(sdk.get(field), f"sdk_invocation.{field}")
        for field in ("run_id", "attempt_id", "resource_id", "image_id")
    }
    if sdk_binding != {
        "run_id": run_id,
        "attempt_id": attempt_id,
        "resource_id": resource_id,
        "image_id": expected_image_id,
    }:
        raise EvidenceValidationError(
            "SDK invocation is not bound to accepted Run/Attempt/resource/image"
        )

    callback = payloads["callback_receipt"]
    if _require_bool(callback.get("callback_received"), "callback_receipt.callback_received") is not True:
        raise EvidenceValidationError("terminal callback was not observed")
    if callback.get("http_status") != 200 or callback.get("run_id") != run_id or callback.get(
        "attempt_id"
    ) != attempt_id:
        raise EvidenceValidationError("terminal callback is not bound to accepted Run/Attempt")
    _require_hash(callback.get("receipt_sha256"), "callback_receipt.receipt_sha256")

    answer = payloads["answer_delivery"]
    if _require_bool(answer.get("delivered"), "answer_delivery.delivered") is not True:
        raise EvidenceValidationError("committed answer was not read back")
    if answer.get("http_status") != 200 or answer.get("run_id") != run_id:
        raise EvidenceValidationError("answer readback is not bound to accepted Run")
    _require_hash(answer.get("answer_sha256"), "answer_delivery.answer_sha256")

    download = payloads["file_download"]
    if _require_bool(download.get("downloaded"), "file_download.downloaded") is not True:
        raise EvidenceValidationError("selected file was not downloaded")
    if download.get("http_status") != 200 or download.get("run_id") != run_id:
        raise EvidenceValidationError("file download is not bound to accepted Run")
    _require_hash(download.get("sha256"), "file_download.sha256")

    stop = payloads["resource_stop"]
    if _require_bool(stop.get("stop_confirmed"), "resource_stop.stop_confirmed") is not True:
        raise EvidenceValidationError("provider stop was not confirmed")
    if _require_bool(stop.get("released"), "resource_stop.released") is not True:
        raise EvidenceValidationError("provider resource was not released")
    if stop.get("resource_id") != resource_id:
        raise EvidenceValidationError("stop evidence references a different provider resource")

    resume = payloads["native_resume"]
    if _require_bool(resume.get("verified"), "native_resume.verified") is not True:
        raise EvidenceValidationError("cross-sandbox native resume was not verified")
    if _require_bool(
        resume.get("current_authorization_rechecked"),
        "native_resume.current_authorization_rechecked",
    ) is not True:
        raise EvidenceValidationError("native resume did not recheck current authorization")
    resume_session_id = _require_text(resume.get("session_id"), "native_resume.session_id")
    if resume_session_id != session_id:
        raise EvidenceValidationError("native resume did not continue the accepted SDK session")

    source = _require_object(resume.get("source_task"), "native_resume.source_task")
    source_identity = {
        field: _require_text(source.get(field), f"native_resume.source_task.{field}")
        for field in (
            "run_id", "attempt_id", "resource_id", "image_id", "sandbox_id", "epoch_id",
        )
    }
    source_binding = {
        "accepted": _require_bool(
            source.get("accepted"), "native_resume.source_task.accepted"
        ),
        "operation_id": _require_text(
            source.get("operation_id"), "native_resume.source_task.operation_id"
        ),
        "provider_name": _require_text(
            source.get("provider_name"), "native_resume.source_task.provider_name"
        ),
        "provider_version": _require_text(
            source.get("provider_version"), "native_resume.source_task.provider_version"
        ),
    }
    if (
        source_binding
        != {
            "accepted": True,
            "operation_id": operation_id,
            "provider_name": provider_name,
            "provider_version": provider_version,
        }
        or any(
            source_identity[field] != expected
            for field, expected in {
                "run_id": run_id,
                "attempt_id": attempt_id,
                "resource_id": resource_id,
                "image_id": expected_image_id,
            }.items()
        )
        or _require_text(
            source.get("sdk_invocation_id"),
            "native_resume.source_task.sdk_invocation_id",
        )
        != invocation_id
    ):
        raise EvidenceValidationError(
            "native resume source task does not match first accepted execution"
        )

    target = _require_object(resume.get("target_task"), "native_resume.target_task")
    target_identity = {
        field: _require_text(target.get(field), f"native_resume.target_task.{field}")
        for field in (
            "run_id", "attempt_id", "resource_id", "image_id", "sandbox_id", "epoch_id",
        )
    }
    if _require_bool(target.get("accepted"), "native_resume.target_task.accepted") is not True:
        raise EvidenceValidationError("native resume target task was not accepted")
    target_operation_id = _require_text(
        target.get("operation_id"), "native_resume.target_task.operation_id"
    )
    if _require_text(
        target.get("provider_name"), "native_resume.target_task.provider_name"
    ) != provider_name:
        raise EvidenceValidationError("native resume target used a different provider")
    if _require_text(
        target.get("provider_version"), "native_resume.target_task.provider_version"
    ) != dependencies["provider"]:
        raise EvidenceValidationError("native resume target provider version differs from inventory")
    if target_identity["image_id"] != expected_image_id:
        raise EvidenceValidationError("native resume target used a different candidate image")

    target_sdk = _require_object(
        target.get("sdk_invocation"), "native_resume.target_task.sdk_invocation"
    )
    if _require_bool(
        target_sdk.get("sdk_used"), "native_resume.target_task.sdk_invocation.sdk_used"
    ) is not True:
        raise EvidenceValidationError("native resume target did not record SDK use")
    if _require_text(
        target_sdk.get("sdk_name"), "native_resume.target_task.sdk_invocation.sdk_name"
    ) != _require_text(sdk.get("sdk_name"), "sdk_invocation.sdk_name"):
        raise EvidenceValidationError("native resume target used a different SDK")
    if _require_text(
        target_sdk.get("sdk_version"), "native_resume.target_task.sdk_invocation.sdk_version"
    ) != dependencies["agent_sdk"]:
        raise EvidenceValidationError("native resume target SDK version differs from inventory")
    target_invocation_id = _require_text(
        target_sdk.get("invocation_id"),
        "native_resume.target_task.sdk_invocation.invocation_id",
    )
    target_sdk_binding = {
        field: _require_text(
            target_sdk.get(field), f"native_resume.target_task.sdk_invocation.{field}"
        )
        for field in ("session_id", "run_id", "attempt_id", "resource_id", "image_id")
    }
    expected_target_sdk_binding = {
        "session_id": session_id,
        "run_id": target_identity["run_id"],
        "attempt_id": target_identity["attempt_id"],
        "resource_id": target_identity["resource_id"],
        "image_id": expected_image_id,
    }
    if target_sdk_binding != expected_target_sdk_binding:
        differing_field = next(
            field
            for field, value in expected_target_sdk_binding.items()
            if target_sdk_binding[field] != value
        )
        raise EvidenceValidationError(
            "native resume target SDK invocation does not match target task at "
            f"{differing_field}"
        )
    if (
        target_operation_id == operation_id
        or target_invocation_id == invocation_id
        or any(
            target_identity[field] == source_identity[field]
            for field in ("run_id", "attempt_id", "resource_id", "sandbox_id", "epoch_id")
        )
    ):
        raise EvidenceValidationError(
            "native resume target is not a distinct successor task"
        )


def validate_candidate_evidence(
    report_path: Path,
    *,
    repo_root: Path,
    bundle_root: Path,
    image_id: str,
) -> dict[str, object]:
    """Validate a maintainer-run L-layer report against its checked-out subject."""
    root = repo_root.resolve()
    bundle = bundle_root.resolve()
    if bundle.is_relative_to(root):
        raise EvidenceValidationError("evidence bundle must be outside the source worktree")
    if not report_path.resolve().is_relative_to(bundle):
        raise EvidenceValidationError("report must be inside the evidence bundle")
    if not IMAGE_ID_PATTERN.fullmatch(image_id):
        raise EvidenceValidationError("expected image id must be a sha256 image identity")

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvidenceValidationError("unable to read candidate evidence report") from error
    report = _require_object(report, "report")
    if report.get("schema_version") != SCHEMA_VERSION:
        raise EvidenceValidationError("unsupported lifecycle evidence schema")
    if report.get("evidence_level") != "L" or report.get("status") != "passed":
        raise EvidenceValidationError("only a completed L-layer report can validate")

    binding = capture_candidate_source_binding(root)
    source = _require_object(report.get("source"), "source")
    image = _require_object(report.get("image"), "image")
    for key in ("head_sha", "dirty_source_sha256", "lock_sha256", "dirty"):
        if source.get(key) != binding[key]:
            raise EvidenceValidationError(f"report source binding differs at {key}")
    if image.get("id") != image_id:
        raise EvidenceValidationError("report image id differs from inspected candidate")
    if image.get("source_head_sha") != binding["head_sha"]:
        raise EvidenceValidationError("candidate image was built from a different source SHA")
    if image.get("dirty_source_sha256") != binding["dirty_source_sha256"]:
        raise EvidenceValidationError("candidate image source snapshot does not match checkout")
    if image.get("lock_sha256") != binding["lock_sha256"]:
        raise EvidenceValidationError("candidate image lockfile does not match checkout")
    if image.get("build_dirty") is not binding["dirty"]:
        raise EvidenceValidationError("candidate image dirty-build flag does not match checkout")

    dependencies = _require_object(report.get("dependencies"), "dependencies")
    for dependency in REQUIRED_DEPENDENCIES:
        _require_text(dependencies.get(dependency), f"dependencies.{dependency}")
    profile = _require_object(report.get("security_profile"), "security_profile")
    profile_id = _require_text(profile.get("id"), "security_profile.id")
    profile_sha256 = _require_hash(profile.get("sha256"), "security_profile.sha256")
    if _require_bool(profile.get("enforced"), "security_profile.enforced") is not True:
        raise EvidenceValidationError("security profile was not reported as enforced")

    execution = _require_object(report.get("execution"), "execution")
    if _require_bool(execution.get("sdk_used"), "execution.sdk_used") is not True:
        raise EvidenceValidationError("L-layer report must attest actual SDK use")
    if _require_bool(execution.get("provider_used"), "execution.provider_used") is not True:
        raise EvidenceValidationError("L-layer report must attest actual provider use")
    scenarios = _require_object(execution.get("scenarios"), "execution.scenarios")
    if not REQUIRED_SCENARIOS.issubset(scenarios):
        raise EvidenceValidationError("L-layer report is missing R01 or R14")
    for scenario_id in REQUIRED_SCENARIOS:
        scenario = _require_object(scenarios[scenario_id], f"execution.scenarios.{scenario_id}")
        if scenario.get("status") != "passed":
            raise EvidenceValidationError(f"{scenario_id} did not pass")

    payloads = _artifact_payloads(bundle, report)
    profile_artifact = payloads["security_profile"]
    if profile_artifact.get("enforced") is not True:
        raise EvidenceValidationError("security profile artifact does not show enforcement")
    if profile_artifact.get("profile_id") != profile_id or profile_artifact.get(
        "sha256"
    ) != profile_sha256:
        raise EvidenceValidationError("security profile report differs from enforcement artifact")
    _validate_artifact_chain(payloads, dependencies, image_id, binding)
    return {
        "status": "validated",
        "evidence_level": "L",
        "head_sha": binding["head_sha"],
        "dirty_source_sha256": binding["dirty_source_sha256"],
        "lock_sha256": binding["lock_sha256"],
        "image_id": image_id,
        "scenario_ids": sorted(REQUIRED_SCENARIOS),
        "evidence_artifacts": len(payloads),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    binding_parser = subparsers.add_parser(
        "binding", help="print the current source, dirty-tree, and lockfile bindings"
    )
    binding_parser.add_argument("--repo-root", type=Path, required=True)
    validate_parser = subparsers.add_parser("validate", help="validate an L-layer report bundle")
    validate_parser.add_argument("report", type=Path)
    validate_parser.add_argument("--repo-root", type=Path, required=True)
    validate_parser.add_argument("--bundle-root", type=Path, required=True)
    validate_parser.add_argument("--image-id", required=True)
    arguments = parser.parse_args()
    try:
        if arguments.command == "binding":
            summary = capture_candidate_source_binding(arguments.repo_root)
        else:
            summary = validate_candidate_evidence(
                arguments.report,
                repo_root=arguments.repo_root,
                bundle_root=arguments.bundle_root,
                image_id=arguments.image_id,
            )
    except EvidenceValidationError as error:
        print(f"lifecycle evidence rejected: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
