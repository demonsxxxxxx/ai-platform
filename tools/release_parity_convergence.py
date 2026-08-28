"""Strict parity reporting and bounded post-Compose convergence."""

from __future__ import annotations

import errno
import subprocess
import time
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from urllib.error import HTTPError, URLError


SCHEMA_VERSION = "ai-platform.release-authority.v1"
COMPOSE_PROJECT = "ai-platform-internal"
COMPATIBILITY_IMAGE_COMMIT_LABELS = (
    "ai-platform.source-revision",
    "ai-platform.runtime-subject",
    "ai-platform.source_revision",
    "ai-platform.source_commit",
    "ai-platform.runtime_subject",
    "ai-platform.source_tree_commit",
    "ai_platform_source_revision",
    "ai_platform_source_commit",
    "ai_platform_runtime_subject",
    "ai_platform_source_tree_commit",
)
FINAL_PARITY_CONVERGENCE_TIMEOUT_SECONDS = 45
FINAL_PARITY_POLL_INTERVAL_SECONDS = 2
_TRANSIENT_FINAL_PARITY_ERRNOS = frozenset(
    {
        errno.ECONNREFUSED,
        errno.ECONNRESET,
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
        errno.ETIMEDOUT,
        104,
        10053,
        10054,
        10060,
    }
)
_RETRYABLE_FINAL_PARITY_READINESS_ERRORS = frozenset(
    {
        "worker runtime heartbeat read failed",
        "worker runtime heartbeat process is not alive",
        "worker runtime heartbeat is stale",
    }
)
_FINAL_PARITY_FAILURE_KINDS = frozenset(
    {"attempt-timeout", "startup-readiness", "transient-io", "unverified-parity"}
)
_ACTIVE_PARITY_ATTEMPT: ContextVar[tuple[float, Callable[[], float]] | None] = ContextVar(
    "active_parity_attempt",
    default=None,
)


class ParityAttemptDeadlineExceeded(RuntimeError):
    """Raised before a read-only parity operation would outlive its attempt deadline."""


@contextmanager
def parity_attempt_budget(
    deadline: float,
    *,
    monotonic: Callable[[], float],
):
    """Scope one strict read-only parity collection to its absolute monotonic deadline."""
    token = _ACTIVE_PARITY_ATTEMPT.set((deadline, monotonic))
    try:
        yield
    finally:
        _ACTIVE_PARITY_ATTEMPT.reset(token)


def bounded_parity_attempt_timeout(timeout_seconds: float) -> float:
    """Return the remaining attempt budget capped by an operation's existing timeout."""
    active = _ACTIVE_PARITY_ATTEMPT.get()
    if active is None:
        return timeout_seconds
    deadline, monotonic = active
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise ParityAttemptDeadlineExceeded
    return min(timeout_seconds, remaining)


def compose_identity_mismatches(
    labels: dict[str, Any],
    role: str,
    *,
    expected_compose_dir: str,
    expected_config_files: str,
) -> list[str]:
    mismatches: list[str] = []
    if labels.get("ai-platform.release-owner") != "repo-local-compose":
        mismatches.append(f"{role}_container_not_repo_local_compose_owned")
    if labels.get("ai-platform.release-role") != role:
        mismatches.append(f"{role}_container_role_mismatch")
    if labels.get("com.docker.compose.project.working_dir") != expected_compose_dir:
        mismatches.append(f"{role}_compose_working_dir_mismatch")
    if str(labels.get("com.docker.compose.project.config_files") or "") != expected_config_files:
        mismatches.append(f"{role}_compose_config_mismatch")
    if labels.get("com.docker.compose.project") != COMPOSE_PROJECT:
        mismatches.append(f"{role}_compose_project_mismatch")
    if labels.get("com.docker.compose.service") != role:
        mismatches.append(f"{role}_compose_service_mismatch")
    if labels.get("com.docker.compose.oneoff") != "False":
        mismatches.append(f"{role}_compose_oneoff_mismatch")
    if not str(labels.get("com.docker.compose.config-hash") or "").strip():
        mismatches.append(f"{role}_compose_config_hash_missing")
    return mismatches


def build_parity_report(
    *,
    expected_commit: str,
    source: dict[str, Any],
    images: dict[str, dict[str, Any]],
    containers: dict[str, dict[str, Any]],
    runtime: dict[str, Any],
    expected_compose_dir: str,
    expected_repository: str,
    normalize_commit: Callable[[str], str],
    expected_compose_files: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Build a strict same-commit report for source, images, and runtime subjects."""
    commit = normalize_commit(expected_commit)
    mismatches: list[str] = []
    if source.get("commit") != commit:
        mismatches.append("source_commit_mismatch")
    if source.get("dirty") is not False:
        mismatches.append("source_not_clean")

    for role in ("backend", "frontend"):
        image = images.get(role, {})
        labels = image.get("labels") if isinstance(image.get("labels"), dict) else {}
        if labels.get("ai-platform.source-commit") != commit:
            mismatches.append(f"{role}_image_commit_mismatch")
        if labels.get("org.opencontainers.image.revision") != commit:
            mismatches.append(f"{role}_image_oci_revision_mismatch")
        if labels.get("ai-platform.source-repository") != expected_repository:
            mismatches.append(f"{role}_image_repository_mismatch")
        if labels.get("ai-platform.build-dirty") != "false":
            mismatches.append(f"{role}_image_dirty_label_mismatch")
        if labels.get("ai-platform.release-role") != role:
            mismatches.append(f"{role}_image_role_mismatch")
        if any(
            label in labels and labels.get(label) != commit
            for label in COMPATIBILITY_IMAGE_COMMIT_LABELS
        ):
            mismatches.append(f"{role}_image_compatibility_commit_mismatch")

    expected_config_files = ",".join(expected_compose_files) if expected_compose_files else (
        f"{expected_compose_dir}/docker-compose.yml"
    )
    expected_image_roles = {"api": "backend", "worker": "backend", "frontend": "frontend"}
    for role, image_role in expected_image_roles.items():
        container = containers.get(role, {})
        labels = container.get("labels") if isinstance(container.get("labels"), dict) else {}
        if container.get("running") is not True:
            mismatches.append(f"{role}_container_not_running")
        mismatches.extend(
            compose_identity_mismatches(
                labels,
                role,
                expected_compose_dir=expected_compose_dir,
                expected_config_files=expected_config_files,
            )
        )
        if labels.get("ai-platform.source-commit") != commit:
            mismatches.append(f"{role}_container_commit_mismatch")
        if labels.get("ai-platform.source-dirty") != "false":
            mismatches.append(f"{role}_container_dirty_label_mismatch")
        expected_image_id = images.get(image_role, {}).get("id")
        if not expected_image_id or container.get("image_id") != expected_image_id:
            mismatches.append(f"{role}_container_image_mismatch")

    for role in ("api", "worker", "frontend"):
        if runtime.get(f"{role}_commit") != commit:
            mismatches.append(f"{role}_runtime_commit_mismatch")
    if runtime.get("api_sandbox_executor_image_matches_expected") is not True:
        mismatches.append("api_sandbox_executor_image_mismatch")
    if runtime.get("worker_sandbox_executor_image_matches_expected") is not True:
        mismatches.append("worker_sandbox_executor_image_mismatch")
    if runtime.get("api_worker_sandbox_executor_images_match") is not True:
        mismatches.append("api_worker_sandbox_executor_image_mismatch")
    if runtime.get("api_health_status") != "ok":
        mismatches.append("api_health_not_ok")
    if runtime.get("worker_running") is not True:
        mismatches.append("worker_not_running")

    return {
        "schema_version": SCHEMA_VERSION,
        "expected_commit": commit,
        "verified": not mismatches,
        "mismatches": sorted(set(mismatches)),
        "source": source,
        "images": images,
        "containers": containers,
        "runtime": runtime,
    }


def converge_final_parity(
    collect: Callable[[float], dict[str, Any]],
    *,
    authority_error_type: type[Exception],
    timeout_seconds: float = FINAL_PARITY_CONVERGENCE_TIMEOUT_SECONDS,
    poll_interval_seconds: float = FINAL_PARITY_POLL_INTERVAL_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Bound post-Compose read-only parity collection until strict verification succeeds."""
    if timeout_seconds <= 0 or poll_interval_seconds <= 0:
        raise authority_error_type("final parity convergence configuration is invalid")
    deadline = monotonic() + timeout_seconds
    attempts = 0
    last_failure_kind = "unverified-parity"
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            error = authority_error_type("final parity did not converge")
            error.parity_attempts = min(attempts, 10_000)
            error.parity_last_failure_kind = last_failure_kind
            raise error
        attempts += 1
        try:
            with parity_attempt_budget(deadline, monotonic=monotonic):
                report = collect(remaining)
        except (ParityAttemptDeadlineExceeded, TimeoutError, subprocess.TimeoutExpired):
            last_failure_kind = "attempt-timeout"
        except HTTPError:
            raise
        except URLError:
            last_failure_kind = "transient-io"
        except OSError as exc:
            if exc.errno not in _TRANSIENT_FINAL_PARITY_ERRNOS:
                raise
            last_failure_kind = "transient-io"
        except authority_error_type as exc:
            if str(exc) not in _RETRYABLE_FINAL_PARITY_READINESS_ERRORS:
                raise
            last_failure_kind = "startup-readiness"
        else:
            if monotonic() >= deadline:
                last_failure_kind = "attempt-timeout"
            elif report.get("verified") is True:
                return report
            else:
                last_failure_kind = "unverified-parity"

        remaining = deadline - monotonic()
        if remaining <= 0:
            error = authority_error_type("final parity did not converge")
            error.parity_attempts = min(attempts, 10_000)
            error.parity_last_failure_kind = last_failure_kind
            raise error
        sleep(min(poll_interval_seconds, remaining))


def convergence_failure_evidence(error: BaseException) -> dict[str, Any]:
    """Return only bounded final-parity convergence evidence from an authority error."""
    details = vars(error)
    attempts = details.get("parity_attempts")
    last_failure_kind = details.get("parity_last_failure_kind")
    evidence: dict[str, Any] = {}
    if isinstance(attempts, int) and not isinstance(attempts, bool) and 1 <= attempts <= 10_000:
        evidence["parity_attempts"] = attempts
    if isinstance(last_failure_kind, str) and last_failure_kind in _FINAL_PARITY_FAILURE_KINDS:
        evidence["parity_last_failure_kind"] = last_failure_kind
    return evidence
