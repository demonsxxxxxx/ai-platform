from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
import hashlib
import hmac
import inspect
import ipaddress
import json
import os
import re
import secrets
import shlex
import socket
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

try:
    import docker  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised through docker = None path
    docker = None

from app.runtime.sandbox.contracts import (
    EXECUTOR_AUTH_HEADER,
    CallbackTargetValidationError,
    ContainerLease,
    ContainerStatus,
    SandboxRuntimeRequest,
    StopResult,
    WorkspaceLease,
    build_trusted_callback_target,
)
from app.execution_boundary import (
    GOVERNED_EGRESS_PROOF_DEFAULT_KEY_ID,
    GOVERNED_EGRESS_PROOF_LABEL,
    GOVERNED_EGRESS_PROOF_MAX_TTL_SECONDS,
    build_governed_egress_proof,
    governed_egress_authorized_native_tool_scope,
    governed_egress_authorized_skill_scope,
    governed_egress_previous_signing_keys,
    governed_egress_proof_label,
    governed_egress_proof_from_labels,
    has_governed_egress_signing_key,
    is_governed_egress_proof,
)
from app.settings import get_settings
from app.runtime.sandbox.executor_client import (
    EXECUTOR_CONNECT_BASE_URL_METADATA,
    prepare_executor_http_request,
)
from app.runtime.sandbox import governed_egress_diagnostics as egress_diagnostics
from app.runtime.sandbox.filesystem_contract import encode_execd_mode
from app.runtime.sandbox.opensandbox_attestation import build_opensandbox_attestation_probe
from app.runtime.sandbox.providers.opensandbox.startup import (
    OpenSandboxStartupEvidence,
    OpenSandboxStartupEvidenceCarrier,
    OpenSandboxStartupFailure,
    OpenSandboxStartupOperations,
    cleanup_new_sandbox_or_reconcile,
    cleanup_started_sandbox,
    identity_unavailable_cleanup_subject,
    is_authoritative_not_found_error,
    launch_opensandbox_startup,
    reconcile_authoritative_identity_unavailable_cleanup,
    resolve_executor_endpoint,
    unhealthy_readiness_fields,
)
from app.runtime.sandbox.providers.opensandbox import metadata as opensandbox_metadata
from app.runtime.sandbox.opensandbox_trusted_internal import (
    SANDBOX_SECURITY_PROFILE_GOVERNED,
    SANDBOX_SECURITY_PROFILE_LABEL,
    SANDBOX_SECURITY_PROFILE_TRUSTED_INTERNAL,
    ExecutorEgressBases as _ExecutorEgressBases,
    OpenSandboxProfileConfigurationError,
    OpenSandboxSecurityProfile as _OpenSandboxSecurityProfile,
    configured_security_profile,
    governed_opensandbox_lease_labels,
    governed_opensandbox_egress_bases,
    opensandbox_container_name as _opensandbox_container_name,
    opensandbox_status_from_info as _opensandbox_status_from_info,
    requested_opensandbox_image,
    require_provider_profile_compatibility,
    resolve_opensandbox_security_profile,
    runtime_scope_labels,
    trusted_internal_cleanup_identity_is_authorized,
    trusted_internal_lease_labels,
    trusted_internal_orphan_cleanup_identity_is_authorized,
    trusted_internal_orphan_cleanup_metadata_filter,
)
from app.runtime.sandbox import readiness_evidence
from app.runtime.sandbox.workspace_permissions import RUNTIME_GID, RUNTIME_UID
from app.skills.execution_profiles import NATIVE_COMMAND_ISOLATION


class SandboxRuntimeError(OpenSandboxStartupEvidenceCarrier, RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class DockerUnavailableError(SandboxRuntimeError):
    def __init__(self, message: str = "Docker SDK is unavailable") -> None:
        super().__init__("docker_unavailable", message)


class OpenSandboxUnavailableError(SandboxRuntimeError):
    """Raised when the optional OpenSandbox SDK cannot be imported or used."""

    def __init__(self, message: str = "OpenSandbox SDK is unavailable") -> None:
        super().__init__("opensandbox_unavailable", message)


class OpenSandboxCapabilityAdmissionError(SandboxRuntimeError):
    """Raised before OpenSandbox dispatch when external-egress capability is unproven."""

    def __init__(self, message: str = "OpenSandbox external-egress capability admission failed") -> None:
        super().__init__("opensandbox_capability_admission_failed", message)


class GovernedEgressAdmissionError(SandboxRuntimeError):
    """Raised before sandbox side effects when default-deny egress cannot be proven."""

    def __init__(self) -> None:
        super().__init__("sandbox_egress_unavailable", "Governed sandbox egress is unavailable; contact an operator.")


class DockerPermissionDeniedError(SandboxRuntimeError):
    def __init__(self, message: str = "Docker permission denied") -> None:
        super().__init__("docker_permission_denied", message)


class ContainerStartFailedError(SandboxRuntimeError):
    def __init__(self, message: str = "Container start failed") -> None:
        super().__init__("container_start_failed", message)


class OpenSandboxStartupFailedError(ContainerStartFailedError):
    """Generic public startup failure with safe private OpenSandbox evidence."""
    def __init__(self, evidence: OpenSandboxStartupEvidence, message: str = "OpenSandbox sandbox start failed") -> None:
        super().__init__(message)
        self.private_evidence = evidence.private_payload()


class NativeToolAdmissionError(SandboxRuntimeError):
    """Raised when the isolated native-command sidecar cannot become ready."""

    def __init__(self, message: str = "Native tool sandbox admission failed") -> None:
        super().__init__("native_tool_admission_failed", message)


class ContainerCleanupFailedError(SandboxRuntimeError):
    """Raised when a rejected executor cannot be confirmed stopped and removed."""

    def __init__(self, message: str = "Container cleanup failed", *, readiness_evidence: readiness_evidence.ExecutorReadinessEvidence | None = None, cleanup_subject: dict[str, str] | None = None) -> None:
        super().__init__("container_cleanup_failed", message)
        self.readiness_evidence, self.cleanup_subject = readiness_evidence, cleanup_subject


class ExecutorHealthTimeoutError(SandboxRuntimeError):
    def __init__(self, message: str = "Executor health timeout", *, readiness_evidence=None) -> None:
        super().__init__("executor_health_timeout", message)
        self.readiness_evidence = readiness_evidence


class ContainerProvider(Protocol):
    async def create_or_reuse(
        self,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
    ) -> ContainerLease: ...

    async def stop(self, lease: ContainerLease, *, reason: str) -> StopResult: ...

    async def validate_for_dispatch(
        self,
        lease: ContainerLease,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
    ) -> None: ...

    async def stage_workspace(
        self,
        lease: ContainerLease,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
    ) -> None:
        """Stage the controller-owned attempt files before executor dispatch."""

        ...

    async def collect_workspace(
        self,
        lease: ContainerLease,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
    ) -> None:
        """Collect provider-approved attempt outputs after executor dispatch."""

        ...

    async def list_runtime_containers(self, filters: dict[str, str]) -> list[ContainerStatus]: ...

    async def cleanup_orphan_containers(self, filters: dict[str, str], *, reason: str) -> list[StopResult]: ...


def _matches_filters(status: ContainerStatus, filters: dict[str, str]) -> bool:
    for key, expected in filters.items():
        actual = getattr(status, key, None)
        if actual is None:
            detail_value = status.detail.get(key)
            if detail_value is None and key == "attempt_id":
                labels = status.detail.get("labels")
                if isinstance(labels, dict):
                    detail_value = labels.get("ai-platform.attempt_id")
            if detail_value is None or str(detail_value) != str(expected):
                return False
            continue
        if str(actual) != str(expected):
            return False
    return True


def _executor_url() -> str:
    host = get_settings().sandbox_executor_published_host
    return f"http://{host}:18000"


@dataclass(frozen=True)
class _ExecutorPublishedEndpoint:
    published_host: str
    bind_ip: str


def _resolve_executor_published_endpoint(raw_host: str) -> _ExecutorPublishedEndpoint:
    host = str(raw_host or "").strip()
    if not host or host in {"0.0.0.0", "::"}:
        raise ContainerStartFailedError("executor published endpoint is invalid")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if literal.version != 4 or literal.is_unspecified:
            raise ContainerStartFailedError("executor published endpoint is invalid")
        if not literal.is_loopback and not literal.is_private:
            raise ContainerStartFailedError("executor published endpoint is invalid")
        return _ExecutorPublishedEndpoint(published_host=host, bind_ip=str(literal))
    try:
        addresses = {
            str(ipaddress.IPv4Address(result[4][0]))
            for result in socket.getaddrinfo(host, None, family=socket.AF_INET, type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError, IndexError, TypeError) as exc:
        raise ContainerStartFailedError("executor published endpoint is invalid") from exc
    if len(addresses) != 1:
        raise ContainerStartFailedError("executor published endpoint is invalid")
    bind_ip = next(iter(addresses))
    resolved_ip = ipaddress.IPv4Address(bind_ip)
    if not resolved_ip.is_loopback and not resolved_ip.is_private:
        raise ContainerStartFailedError("executor published endpoint is invalid")
    if resolved_ip.is_loopback and host.lower() != "localhost":
        raise ContainerStartFailedError("executor published endpoint is invalid")
    return _ExecutorPublishedEndpoint(published_host=host, bind_ip=bind_ip)


def _published_executor_url_from_container(
    container: Any,
    endpoint: _ExecutorPublishedEndpoint | None = None,
) -> str | None:
    ports = getattr(container, "attrs", {}).get("NetworkSettings", {}).get("Ports", {})
    bindings = ports.get("18000/tcp") or []
    if len(bindings) != 1:
        if endpoint is not None and bindings:
            raise ContainerStartFailedError("executor published endpoint mismatch")
        return None
    binding = bindings[0]
    host_port = str(binding.get("HostPort") or "").strip()
    try:
        port_number = int(host_port)
    except ValueError:
        port_number = 0
    if not 1 <= port_number <= 65535:
        if endpoint is not None:
            raise ContainerStartFailedError("executor published endpoint mismatch")
        return None
    host = str(binding.get("HostIp") or "").strip()
    if endpoint is not None:
        if host != endpoint.bind_ip:
            raise ContainerStartFailedError("executor published endpoint mismatch")
        return f"http://{endpoint.published_host}:{port_number}"
    if host in {"", "0.0.0.0", "::"}:
        return None
    return f"http://{host}:{port_number}"


def _docker_readiness_snapshot(
    container: Any, endpoint: _ExecutorPublishedEndpoint | None = None
) -> tuple[object, object, bool]:
    try:
        container.reload()
        published = endpoint is None or bool(_published_executor_url_from_container(container, endpoint))
        return container.attrs, container.status, published
    except Exception:
        return None, None, endpoint is None


def _lease_from_request(
    provider: str,
    request: SandboxRuntimeRequest,
    workspace: WorkspaceLease,
    *,
    executor_url: str,
    executor_headers: dict[str, str] | None = None,
    timings: dict[str, int] | None = None,
) -> ContainerLease:
    container_id = f"exec-{request.run_id}"
    return ContainerLease(
        container_id=container_id,
        container_name=f"executor-{container_id}",
        provider=provider,
        executor_url=executor_url,
        executor_headers=dict(executor_headers or {}),
        tenant_id=request.tenant_id,
        workspace_id=request.workspace_id,
        user_id=request.user_id,
        session_id=request.session_id,
        run_id=request.run_id,
        sandbox_mode=request.sandbox_mode,
        browser_enabled=request.browser_enabled,
        workspace_host_path=workspace.workspace_host_path,
        workspace_container_path=workspace.workspace_container_path,
        labels={
            "ai-platform.run_id": request.run_id,
            "ai-platform.attempt_id": request.attempt_id,
        },
        timings=timings or {},
    )


def _status_from_lease(lease: ContainerLease, *, status: str) -> ContainerStatus:
    return ContainerStatus(
        container_id=lease.container_id,
        container_name=lease.container_name,
        provider=lease.provider,
        status=status,
        tenant_id=lease.tenant_id,
        workspace_id=lease.workspace_id,
        user_id=lease.user_id,
        session_id=lease.session_id,
        run_id=lease.run_id,
        sandbox_mode=lease.sandbox_mode,
        browser_enabled=lease.browser_enabled,
        executor_url=lease.executor_url,
        detail={"labels": lease.platform_labels()},
    )


def _container_labels(container: Any) -> dict[str, str]:
    labels = getattr(container, "labels", None)
    if labels is None:
        labels = getattr(container, "attrs", {}).get("Config", {}).get("Labels", {})
    return {str(key): str(value) for key, value in (labels or {}).items()}


def _container_status_from_labels(container: Any) -> ContainerStatus | None:
    labels = _container_labels(container)
    owner = labels.get("ai-platform.owner")
    if owner not in {"sandbox-runtime", "sandbox-native-tool"}:
        return None
    run_id = labels.get("ai-platform.run_id")
    sandbox_mode = labels.get("ai-platform.sandbox_mode")
    if sandbox_mode not in {"ephemeral", "persistent"}:
        sandbox_mode = None
    if run_id:
        container_id = f"native-tool-{run_id}" if owner == "sandbox-native-tool" else f"exec-{run_id}"
    else:
        container_id = getattr(container, "id", getattr(container, "name", ""))
    return ContainerStatus(
        container_id=container_id,
        container_name=getattr(container, "name", ""),
        provider="docker",
        status=getattr(container, "status", "unknown"),
        tenant_id=labels.get("ai-platform.tenant_id"),
        workspace_id=labels.get("ai-platform.workspace_id"),
        user_id=labels.get("ai-platform.user_id"),
        session_id=labels.get("ai-platform.session_id"),
        run_id=run_id,
        sandbox_mode=sandbox_mode,
        browser_enabled=labels.get("ai-platform.browser_enabled", "false").lower() == "true",
        executor_url=(
            None
            if owner == "sandbox-native-tool"
            else _published_executor_url_from_container(container)
        ),
        detail={"labels": labels},
    )


def _container_config_user(container: Any) -> str:
    return str(getattr(container, "attrs", {}).get("Config", {}).get("User") or "")


def _status_matches_lease(status: ContainerStatus, lease: ContainerLease) -> bool:
    if lease.provider == "opensandbox":
        return opensandbox_metadata.opensandbox_status_matches_lease(status.detail.get("labels"), lease.labels)
    if not (
        status.tenant_id == lease.tenant_id
        and status.workspace_id == lease.workspace_id
        and status.user_id == lease.user_id
        and status.session_id == lease.session_id
        and status.run_id == lease.run_id
        and status.sandbox_mode == lease.sandbox_mode
        and status.browser_enabled == lease.browser_enabled
    ):
        return False
    labels = status.detail.get("labels")
    if not isinstance(labels, dict):
        labels = {}
    expected_attempt_id = lease.labels.get("ai-platform.attempt_id")
    if expected_attempt_id and str(labels.get("ai-platform.attempt_id") or "") != expected_attempt_id:
        return False
    for key, expected in lease.labels.items():
        # Docker labels are immutable. The proof is sealed only after create
        # readback and is kept on the lease/durable projection.
        if lease.provider in {"docker", "opensandbox"} and key == GOVERNED_EGRESS_PROOF_LABEL:
            continue
        if (
            str(key).startswith("ai-platform.egress.")
            or str(key).startswith("ai-platform.executor.")
            or str(key).startswith("ai-platform.external_egress.")
            or str(key).startswith("ai-platform.governed_egress.")
            or str(key).startswith("ai-platform.skill_mount.")
            or str(key) == "ai-platform.runtime_subject"
            or str(key) == SANDBOX_SECURITY_PROFILE_LABEL
        ) and str(labels.get(key) or "") != expected:
            return False
    return True


def _governed_egress_labels_match(
    provider: str,
    stored_labels: dict[str, str],
    expected_labels: dict[str, str],
    signing_key: object,
    *,
    signing_key_id: object = GOVERNED_EGRESS_PROOF_DEFAULT_KEY_ID,
    now: datetime | None = None,
) -> bool:
    stored = governed_egress_proof_from_labels(
        provider,
        stored_labels,
        signing_key=signing_key,
        signing_key_id=signing_key_id,
        now=now,
    )
    expected = governed_egress_proof_from_labels(
        provider,
        expected_labels,
        signing_key=signing_key,
        signing_key_id=signing_key_id,
        now=now,
    )
    if stored is None or expected is None:
        return False
    binding_keys = {
        "provider",
        "source",
        "evidence_class",
        "default_deny_outbound",
        "governed_callback_exception",
        "policy_bound_enforcement",
        "network_internal",
    }
    binding_keys.update(key for key in stored if key.endswith("_sha256"))
    return all(hmac.compare_digest(str(stored.get(key) or ""), str(expected.get(key) or "")) for key in binding_keys)


def _container_scope_key(status: ContainerStatus) -> tuple[str | None, ...]:
    return (
        status.tenant_id,
        status.workspace_id,
        status.user_id,
        status.session_id,
        status.run_id,
        status.sandbox_mode,
    )


def _lease_matches_request_workspace(
    lease: ContainerLease,
    request: SandboxRuntimeRequest,
    workspace: WorkspaceLease,
) -> bool:
    return (
        lease.tenant_id == request.tenant_id == workspace.tenant_id
        and lease.workspace_id == request.workspace_id == workspace.workspace_id
        and lease.user_id == request.user_id == workspace.user_id
        and lease.session_id == request.session_id == workspace.session_id
        and lease.run_id == request.run_id == workspace.run_id
        and lease.labels.get("ai-platform.attempt_id") == request.attempt_id
        and lease.sandbox_mode == request.sandbox_mode
        and lease.browser_enabled == request.browser_enabled
        and lease.workspace_host_path == workspace.workspace_host_path
        and lease.workspace_container_path == workspace.workspace_container_path
    )


def _positive_int_limit(resource_limits: dict[str, Any], key: str) -> int | None:
    value = resource_limits.get(key)
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ContainerStartFailedError() from exc
    if parsed <= 0:
        raise ContainerStartFailedError()
    return parsed


def _docker_resource_kwargs(resource_limits: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(resource_limits, dict):
        return {}
    kwargs: dict[str, Any] = {}
    memory_mb = _positive_int_limit(resource_limits, "memory_mb")
    if memory_mb is not None:
        kwargs["mem_limit"] = f"{memory_mb}m"
    cpu_count = resource_limits.get("cpu_count")
    if cpu_count is not None:
        try:
            parsed_cpu = float(cpu_count)
        except (TypeError, ValueError) as exc:
            raise ContainerStartFailedError() from exc
        if parsed_cpu <= 0:
            raise ContainerStartFailedError()
        kwargs["nano_cpus"] = int(parsed_cpu * 1_000_000_000)
    pids_limit = _positive_int_limit(resource_limits, "pids_limit")
    if pids_limit is not None:
        kwargs["pids_limit"] = pids_limit
    disk_mb = _positive_int_limit(resource_limits, "disk_mb")
    if disk_mb is not None:
        kwargs["storage_opt"] = {"size": f"{disk_mb}m"}
    return kwargs


def _docker_security_kwargs() -> dict[str, Any]:
    return {
        "privileged": False,
        "security_opt": ["no-new-privileges:true"],
        "cap_drop": ["ALL"],
        "read_only": True,
        "tmpfs": {
            "/tmp": f"rw,noexec,nosuid,nodev,uid={RUNTIME_UID},gid={RUNTIME_GID},mode=0700,size=64m",
            "/home/ai-platform": f"rw,noexec,nosuid,nodev,uid={RUNTIME_UID},gid={RUNTIME_GID},mode=0700,size=128m",
        },
    }


_NATIVE_TOOL_OWNER = "sandbox-native-tool"
_NATIVE_TOOL_SOCKET = "/workspace/.ai-platform/native-tool.sock"
_NATIVE_TOOL_HOST_SOCKET_ROOT = ".uds"
# Docker bind-mounts the scoped host directory onto the parent of this path.
# Therefore this basename is also the actual host socket leaf created by the
# native sidecar; preflight and cleanup must use the same leaf.
_NATIVE_TOOL_HOST_SOCKET_NAME = _NATIVE_TOOL_SOCKET.rpartition("/")[2]
_UNIX_SOCKET_PATH_MAX_BYTES = 107
_NATIVE_TOOL_HEALTH_PROBE_COMMAND = (
    "python",
    "-m",
    "app.runtime.sandbox.native_tool_health_probe",
)
_NATIVE_TOOL_HEALTH_PROBE_TIMEOUT_SECONDS = 3.0
_NATIVE_TOOL_HEALTH_PROBE_POLL_INTERVAL_SECONDS = 0.01
_NATIVE_TOOL_ADMISSION_PHASE = "authenticated_container_uds_health"
_CLAUDE_PROJECT_SETTING_NAMES = ("settings.json", "settings.local.json")


@dataclass(frozen=True)
class _TrustedSkillMount:
    host_path: Path
    container_path: str
    fingerprint: str


def _tool_policy_subject_authorized(subject: dict[str, Any], identity: str) -> bool:
    declared = subject.get("declared_identities")
    declared_identities = {
        str(item)
        for item in declared
        if isinstance(item, str) and item
    } if isinstance(declared, list) else set()
    return (
        str(subject.get("identity") or "") == identity
        and all(subject.get(key) is True for key in ("registered", "declared", "active", "distributed"))
        and identity in declared_identities
    )


def _staged_skill_mount_required(request: SandboxRuntimeRequest) -> bool:
    return any(
        _tool_policy_subject_authorized(subject, "Skill")
        and isinstance(subject.get("allowed_skill_names"), list)
        and any(isinstance(name, str) and name for name in subject["allowed_skill_names"])
        for subject in request.tool_policy_subjects
        if isinstance(subject, dict)
    )


def _native_tool_required(request: SandboxRuntimeRequest) -> bool:
    return any(
        _tool_policy_subject_authorized(subject, "Bash")
        and str(subject.get("command_isolation") or "") == NATIVE_COMMAND_ISOLATION
        for subject in request.tool_policy_subjects
        if isinstance(subject, dict)
    )


def _real_directory(path: Path, *, error: str) -> tuple[Path, os.stat_result]:
    try:
        node = path.lstat()
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ContainerStartFailedError(error) from exc
    if stat.S_ISLNK(node.st_mode) or not stat.S_ISDIR(node.st_mode):
        raise ContainerStartFailedError(error)
    return resolved, node


def _scrub_host_project_settings(claude_dir: Path) -> None:
    for name in _CLAUDE_PROJECT_SETTING_NAMES:
        setting = claude_dir / name
        try:
            node = setting.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ContainerStartFailedError("staged Skill settings cannot be inspected") from exc
        if not (stat.S_ISREG(node.st_mode) or stat.S_ISLNK(node.st_mode)):
            raise ContainerStartFailedError("staged Skill settings path is invalid")
        try:
            setting.unlink()
        except OSError as exc:
            raise ContainerStartFailedError("staged Skill settings cannot be scrubbed") from exc
        try:
            setting.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ContainerStartFailedError("staged Skill settings cannot be validated") from exc
        raise ContainerStartFailedError("staged Skill settings cannot be scrubbed")


def _prepare_trusted_skill_mount(
    request: SandboxRuntimeRequest,
    workspace: WorkspaceLease,
) -> _TrustedSkillMount | None:
    """Derive, scrub, and validate the staged Skill mount only from the trusted lease."""

    if not _staged_skill_mount_required(request):
        return None
    workspace_path = Path(workspace.workspace_host_path)
    workspace_root, workspace_node = _real_directory(
        workspace_path,
        error="staged Skill workspace is invalid",
    )
    try:
        trusted_host_root = Path(workspace.host_root).resolve(strict=True)
        workspace_root.relative_to(trusted_host_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ContainerStartFailedError("staged Skill workspace escapes trusted run root") from exc

    claude_path = workspace_path / ".claude"
    claude_root, claude_node = _real_directory(
        claude_path,
        error="staged Skill .claude directory is invalid",
    )
    skills_root, skills_node = _real_directory(
        claude_path / "skills",
        error="staged Skill directory is invalid",
    )
    try:
        claude_root.relative_to(workspace_root)
        skills_root.relative_to(claude_root)
    except ValueError as exc:
        raise ContainerStartFailedError("staged Skill directory escapes trusted workspace") from exc

    _scrub_host_project_settings(claude_path)
    final_claude_root, final_claude_node = _real_directory(
        claude_path,
        error="staged Skill .claude directory changed during validation",
    )
    final_skills_root, final_skills_node = _real_directory(
        claude_path / "skills",
        error="staged Skill directory changed during validation",
    )
    if (
        final_claude_root != claude_root
        or final_skills_root != skills_root
        or (final_claude_node.st_dev, final_claude_node.st_ino)
        != (claude_node.st_dev, claude_node.st_ino)
        or (final_skills_node.st_dev, final_skills_node.st_ino)
        != (skills_node.st_dev, skills_node.st_ino)
    ):
        raise ContainerStartFailedError("staged Skill directory changed during validation")

    fingerprint_payload = "\0".join(
        str(value)
        for value in (
            workspace_node.st_dev,
            workspace_node.st_ino,
            claude_node.st_dev,
            claude_node.st_ino,
            skills_node.st_dev,
            skills_node.st_ino,
        )
    )
    return _TrustedSkillMount(
        host_path=claude_root,
        container_path=f"{workspace.workspace_container_path.rstrip('/')}/.claude",
        fingerprint=hashlib.sha256(fingerprint_payload.encode("ascii")).hexdigest(),
    )


def _skill_mount_labels(skill_mount: _TrustedSkillMount | None) -> dict[str, str]:
    return {
        "ai-platform.skill_mount.required": _env_bool(skill_mount is not None),
        "ai-platform.skill_mount.fingerprint": skill_mount.fingerprint if skill_mount is not None else "",
    }


def _native_tool_container_name(run_id: str) -> str:
    return f"native-tool-{run_id}"


def _native_tool_socket_host_path(workspace: WorkspaceLease | ContainerLease) -> Path:
    identity = "\0".join(
        (
            workspace.tenant_id,
            workspace.workspace_id,
            workspace.user_id,
            workspace.session_id,
            workspace.run_id,
        )
    )
    socket_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    socket_path = (
        Path(get_settings().sandbox_workspace_root)
        / _NATIVE_TOOL_HOST_SOCKET_ROOT
        / socket_key
        / _NATIVE_TOOL_HOST_SOCKET_NAME
    )
    return socket_path


def _native_tool_admission_evidence(
    workspace: WorkspaceLease | ContainerLease,
) -> dict[str, str]:
    """Return path-length-only evidence for the container-local admission probe."""

    host_socket_path = _native_tool_socket_host_path(workspace)
    return {
        "ai-platform.native_tool_admission_phase": _NATIVE_TOOL_ADMISSION_PHASE,
        "ai-platform.native_tool_host_socket_path_bytes": str(
            len(os.fsencode(str(host_socket_path)))
        ),
        "ai-platform.native_tool_container_socket_path_bytes": str(
            len(_NATIVE_TOOL_SOCKET.encode("utf-8"))
        ),
    }


def _native_tool_labels(
    request: SandboxRuntimeRequest,
    workspace: WorkspaceLease,
    skill_mount: _TrustedSkillMount | None,
) -> dict[str, str]:
    return {
        "ai-platform.owner": _NATIVE_TOOL_OWNER,
        "ai-platform.role": "native-skill-command",
        "ai-platform.tenant_id": request.tenant_id,
        "ai-platform.workspace_id": request.workspace_id,
        "ai-platform.user_id": request.user_id,
        "ai-platform.session_id": request.session_id,
        "ai-platform.run_id": request.run_id,
        "ai-platform.sandbox_mode": request.sandbox_mode,
        "ai-platform.browser_enabled": "true" if request.browser_enabled else "false",
        **_native_tool_admission_evidence(workspace),
        **_skill_mount_labels(skill_mount),
    }


def _native_tool_security_kwargs() -> dict[str, Any]:
    return {
        "privileged": False,
        "security_opt": ["no-new-privileges:true"],
        "cap_drop": ["ALL"],
        "read_only": True,
        "tmpfs": {
            "/tmp": f"rw,noexec,nosuid,nodev,uid={RUNTIME_UID},gid={RUNTIME_GID},mode=0700,size=64m",
            "/home/ai-platform": (
                f"rw,noexec,nosuid,nodev,uid={RUNTIME_UID},gid={RUNTIME_GID},mode=0700,size=32m"
            ),
        },
    }


def _native_tool_environment(token: str) -> dict[str, str]:
    return {
        "AI_PLATFORM_NATIVE_TOOL_TOKEN": token,
        "AI_PLATFORM_NATIVE_TOOL_WORKSPACE": "/workspace",
        "AI_PLATFORM_NATIVE_TOOL_SOCKET": _NATIVE_TOOL_SOCKET,
        "AI_PLATFORM_NATIVE_TOOL_UID": str(RUNTIME_UID),
        "AI_PLATFORM_NATIVE_TOOL_GID": str(RUNTIME_GID),
        "HOME": "/home/ai-platform",
        "TMPDIR": "/tmp",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }


def _default_native_tool_probe(container: Any) -> bool:
    """Run the fixed authenticated health probe inside the sidecar namespace."""

    try:
        client = getattr(container, "client", None)
        api = getattr(client, "api", None)
        container_id = getattr(container, "id", None)
        if api is None or not isinstance(container_id, str) or not container_id:
            return False
        created = api.exec_create(
            container_id,
            list(_NATIVE_TOOL_HEALTH_PROBE_COMMAND),
            stdout=False,
            stderr=False,
        )
        if not isinstance(created, dict):
            return False
        exec_id = created.get("Id")
        if not isinstance(exec_id, str) or not exec_id:
            return False
        api.exec_start(exec_id, detach=True)
        deadline = time.monotonic() + _NATIVE_TOOL_HEALTH_PROBE_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            inspected = api.exec_inspect(exec_id)
            if not isinstance(inspected, dict):
                return False
            if inspected.get("Running") is True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(_NATIVE_TOOL_HEALTH_PROBE_POLL_INTERVAL_SECONDS, remaining))
                continue
            if inspected.get("Running") is not False:
                return False
            exit_code = inspected.get("ExitCode")
            return type(exit_code) is int and exit_code == 0
        return False
    except Exception:
        return False


def _workspace_owner_stat(workspace_host_path: str) -> os.stat_result:
    if os.name != "posix":
        raise OSError("POSIX ownership semantics unavailable")
    return Path(workspace_host_path).stat(follow_symlinks=False)


def _secure_native_tool_socket_directory(socket_dir: Path) -> None:
    if os.name != "posix":
        return
    os.chown(socket_dir, RUNTIME_UID, RUNTIME_GID)
    os.chmod(socket_dir, 0o700)


def _docker_workspace_user_kwargs(workspace_host_path: str) -> dict[str, str]:
    try:
        stat_result = _workspace_owner_stat(workspace_host_path)
    except (OSError, TypeError, ValueError) as exc:
        raise ContainerStartFailedError("workspace ownership unavailable") from exc
    uid = getattr(stat_result, "st_uid", None)
    gid = getattr(stat_result, "st_gid", None)
    mode = getattr(stat_result, "st_mode", None)
    if not isinstance(mode, int) or not stat.S_ISDIR(mode):
        raise ContainerStartFailedError("workspace ownership unavailable")
    if not isinstance(uid, int) or not isinstance(gid, int) or (uid, gid) != (RUNTIME_UID, RUNTIME_GID):
        raise ContainerStartFailedError(f"workspace owner must be {RUNTIME_UID}:{RUNTIME_GID}")
    return {"user": f"{RUNTIME_UID}:{RUNTIME_GID}"}


def _docker_workspace_user_value(workspace_host_path: str) -> str:
    return _docker_workspace_user_kwargs(workspace_host_path)["user"]


def _executor_identity_labels() -> dict[str, str]:
    return {
        "ai-platform.executor.user": f"{RUNTIME_UID}:{RUNTIME_GID}",
        "ai-platform.executor.uid": str(RUNTIME_UID),
        "ai-platform.executor.gid": str(RUNTIME_GID),
        "ai-platform.executor.identity_evidence": "authenticated-runtime-endpoint",
    }


def _provider_lease_labels(labels: dict[str, str]) -> dict[str, str]:
    public_executor_labels = {
        "ai-platform.executor.requested_image",
        "ai-platform.executor.requested_image_digest",
    }
    return {
        str(key): str(value)
        for key, value in labels.items()
        if (
            (not str(key).startswith("ai-platform.executor.") or str(key) in public_executor_labels)
        )
    }


def _status_has_expected_executor_identity_labels(status: ContainerStatus) -> bool:
    labels = status.detail.get("labels")
    if not isinstance(labels, dict):
        return False
    return all(str(labels.get(key) or "") == expected for key, expected in _executor_identity_labels().items())


def _env_bool(value: object) -> str:
    return "true" if value is True or str(value).strip().lower() in {"1", "true", "yes", "on"} else "false"


def _env_value(settings: Any, name: str, default: object = "") -> str:
    value = getattr(settings, name, default)
    if value is None:
        return ""
    return str(value)


def _trusted_callback_target(settings: Any, *, allow_host_gateway: bool = True):
    return build_trusted_callback_target(
        _env_value(settings, "sandbox_callback_base_url"),
        extra_hosts=[_env_value(settings, "sandbox_callback_host_gateway")] if allow_host_gateway else [],
    )


OPENSANDBOX_UPSTREAM_BRIDGE_VERSION = "v1"
def _opensandbox_external_egress_bases(settings: Any) -> _ExecutorEgressBases:
    try:
        return governed_opensandbox_egress_bases(settings)
    except OpenSandboxProfileConfigurationError as exc:
        raise OpenSandboxCapabilityAdmissionError(str(exc)) from None


def executor_callback_target(settings: Any, provider_name: str):
    """Resolve the provider-specific callback target through one validation path."""

    try:
        selected_provider = require_provider_profile_compatibility(settings, provider_name)
    except OpenSandboxProfileConfigurationError as exc:
        raise OpenSandboxCapabilityAdmissionError(str(exc)) from None
    if selected_provider == "opensandbox":
        return _opensandbox_security_profile(settings).egress_bases.callback_target()
    return _trusted_callback_target(settings)


def _docker_executor_egress_bases(settings: Any, *, governed_docker_egress: bool) -> _ExecutorEgressBases:
    callback = _trusted_callback_target(settings, allow_host_gateway=not governed_docker_egress)
    return _ExecutorEgressBases(
        callback_base_url=callback.base_url,
        openai_base_url=_env_value(settings, "openai_base_url"),
        anthropic_base_url=_env_value(settings, "anthropic_base_url"),
    )


def _executor_environment(
    request: SandboxRuntimeRequest,
    settings: Any,
    *,
    executor_auth_token: str,
    egress_bases: _ExecutorEgressBases,
    workspace_container_path: str = "/workspace",
    native_tool_token: str = "",
    native_tool_socket: str = "",
) -> dict[str, str]:
    environment = {
        "APP_MODULE": "app.runtime.sandbox.executor_app:create_executor_app",
        "APP_PORT": "18000",
        "AI_PLATFORM_SESSION_ID": request.session_id,
        "AI_PLATFORM_RUN_ID": request.run_id,
        "AI_PLATFORM_ATTEMPT_ID": request.attempt_id,
        "AI_PLATFORM_CALLBACK_BASE_URL": egress_bases.callback_base_url,
        "SANDBOX_CALLBACK_BASE_URL": egress_bases.callback_base_url,
        "AI_PLATFORM_EXECUTOR_AUTH_TOKEN": executor_auth_token,
        "OPENAI_BASE_URL": egress_bases.openai_base_url,
        "OPENAI_API_KEY": _env_value(settings, "openai_api_key"),
        "OPENAI_MODEL": _env_value(settings, "openai_model", "deepseek-v4-flash"),
        "ANTHROPIC_BASE_URL": egress_bases.anthropic_base_url,
        "ANTHROPIC_AUTH_TOKEN": _env_value(settings, "anthropic_auth_token"),
        "ANTHROPIC_MODEL": _env_value(settings, "anthropic_model", "deepseek-v4-flash"),
        "CLAUDE_AGENT_MODEL": _env_value(settings, "claude_agent_model", "deepseek-v4-flash"),
        "DEFAULT_MODEL_ID": _env_value(settings, "default_model_id"),
        "MODEL_CATALOG_JSON": _env_value(settings, "model_catalog_json"),
        "CLAUDE_AGENT_SDK_ENABLED": _env_bool(getattr(settings, "claude_agent_sdk_enabled", False)),
        "CLAUDE_AGENT_SDK_TIMEOUT_SECONDS": _env_value(settings, "claude_agent_sdk_timeout_seconds", 120),
        "CLAUDE_AGENT_SDK_MAX_TURNS": _env_value(settings, "claude_agent_sdk_max_turns", 128),
        "CLAUDE_AGENT_SDK_EFFORT": _env_value(settings, "claude_agent_sdk_effort", "xhigh"),
        "CLAUDE_AGENT_SDK_MAX_THINKING_TOKENS": _env_value(
            settings,
            "claude_agent_sdk_max_thinking_tokens",
            16384,
        ),
        "CLAUDE_AGENT_PERMISSION_MODE": _env_value(settings, "claude_agent_permission_mode", "dontAsk"),
        "CLAUDE_AGENT_ALLOWED_TOOLS": _env_value(settings, "claude_agent_allowed_tools", "Read,Glob,LS"),
        "CLAUDE_AGENT_DISALLOWED_TOOLS": _env_value(
            settings,
            "claude_agent_disallowed_tools",
            "Write,Edit,NotebookEdit",
        ),
        "CLAUDE_AGENT_WORKSPACE_ROOT": workspace_container_path,
        "CLAUDE_AGENT_SDK_SKILLS": _env_value(settings, "claude_agent_sdk_skills"),
        "PLATFORM_SKILLS_ROOT": _env_value(settings, "platform_skills_root", "skills"),
        "SKILL_STAGING_SUBDIR": _env_value(settings, "skill_staging_subdir", ".claude/skills"),
        "PUBLIC_SKILL_FILE_OVERLAY_MAX_BYTES": _env_value(
            settings,
            "public_skill_file_overlay_max_bytes",
            262144,
        ),
    }
    if native_tool_token and native_tool_socket:
        environment["AI_PLATFORM_NATIVE_TOOL_TOKEN"] = native_tool_token
        environment["AI_PLATFORM_NATIVE_TOOL_SOCKET"] = native_tool_socket
    return environment


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _opensandbox_entrypoint(settings: Any) -> list[str]:
    raw = str(getattr(settings, "opensandbox_executor_entrypoint", "") or "").strip()
    if not raw:
        return ["/app/docker-entrypoint.sh", "uvicorn"]
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ContainerStartFailedError("OpenSandbox executor entrypoint is invalid") from exc
        if isinstance(parsed, list) and all(isinstance(item, str) and item for item in parsed):
            return parsed
        raise ContainerStartFailedError("OpenSandbox executor entrypoint is invalid")
    try:
        return shlex.split(raw)
    except ValueError as exc:
        raise ContainerStartFailedError("OpenSandbox executor entrypoint is invalid") from exc


def _opensandbox_requested_image(
    settings: Any,
    *,
    allow_local_image_id: bool = False,
) -> tuple[str, str]:
    """Return the immutable image request and its digest, never an observed runtime subject."""

    try:
        return requested_opensandbox_image(
            settings,
            allow_local_image_id=allow_local_image_id,
        )
    except OpenSandboxProfileConfigurationError as exc:
        raise OpenSandboxCapabilityAdmissionError(str(exc)) from None


def _opensandbox_security_profile(settings: Any) -> _OpenSandboxSecurityProfile:
    try:
        return resolve_opensandbox_security_profile(settings)
    except OpenSandboxProfileConfigurationError as exc:
        raise OpenSandboxCapabilityAdmissionError(str(exc)) from None


def _opensandbox_image(settings: Any) -> str:
    return _opensandbox_requested_image(settings)[0]


def _opensandbox_resource_limits(resource_limits: dict[str, Any]) -> dict[str, str]:
    if not isinstance(resource_limits, dict):
        return {}
    resource: dict[str, str] = {}
    memory_mb = _positive_int_limit(resource_limits, "memory_mb")
    if memory_mb is not None:
        resource["memory"] = f"{memory_mb}Mi"
    cpu_count = resource_limits.get("cpu_count")
    if cpu_count is not None:
        try:
            parsed_cpu = float(cpu_count)
        except (TypeError, ValueError) as exc:
            raise ContainerStartFailedError("OpenSandbox resource limits are invalid") from exc
        if parsed_cpu <= 0:
            raise ContainerStartFailedError("OpenSandbox resource limits are invalid")
        resource["cpu"] = str(int(parsed_cpu)) if parsed_cpu.is_integer() else str(parsed_cpu)
    pids_limit = _positive_int_limit(resource_limits, "pids_limit")
    if pids_limit is not None:
        resource["pids"] = str(pids_limit)
    disk_mb = _positive_int_limit(resource_limits, "disk_mb")
    if disk_mb is not None:
        resource["storage"] = f"{disk_mb}Mi"
    return resource


OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_SCHEMA_VERSION = "ai-platform.opensandbox.external-egress-capability.v1"
OPENSANDBOX_EXTERNAL_EGRESS_RUNTIME_IDENTITY = "runsc"
CAPABILITY_PROFILE_MAX_TTL_SECONDS = 300
CAPABILITY_PROFILE_MAX_ISSUED_AGE_SECONDS = 120
CAPABILITY_PROFILE_CLOCK_SKEW_SECONDS = 30
CAPABILITY_PROFILE_MIN_REMAINING_SECONDS = 30
CAPABILITY_PROFILE_MAX_REQUEST_SECONDS = 2.0
CAPABILITY_PROFILE_MAX_RESPONSE_BYTES = 64 * 1024
CAPABILITY_PROFILE_MAX_TOKEN_BYTES = 4096
CapabilityProfileFetcher = Callable[[str, dict[str, str], float], dict[str, Any]]
_OPENSANDBOX_CAPABILITY_PROFILE_FIELDS = {
    "schema_version",
    "profile_id",
    "provider",
    "issued_at",
    "expires_at",
    "opensandbox_endpoint",
    "runtime_identity",
    "ai_platform_runtime_subject",
    "gateway_policy_subject",
    "callback_boundary_subject",
    "deny_audit_subject",
    "deny_counter_subject",
    "executor_image_digest",
    "upstream_bridge_version",
    "callback_base_url",
    "openai_base_url",
    "anthropic_base_url",
    "proof_key_id",
    "profile_signature",
}


def _opensandbox_governed_runtime_subject(runtime_identity: str, runtime_subject: str) -> str:
    """Bind the exact runsc identity and platform runtime subject as one proof subject."""

    return json.dumps(
        {
            "runtime_identity": runtime_identity,
            "runtime_subject": runtime_subject,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _opensandbox_governed_denial_subject(deny_audit_subject: str, deny_counter_subject: str) -> str:
    """Injectively bind the governed denial audit and counter subjects."""

    return json.dumps(
        {
            "deny_audit_subject": deny_audit_subject,
            "deny_counter_subject": deny_counter_subject,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True)
class OpenSandboxExternalEgressCapability:
    """Validated, non-secret profile values bound to an OpenSandbox lease."""

    profile_id: str
    endpoint: str
    runtime_identity: str
    runtime_subject: str
    gateway_policy_subject: str
    callback_boundary_subject: str
    deny_audit_subject: str
    deny_counter_subject: str
    requested_image: str
    requested_image_digest: str
    upstream_bridge_version: str
    callback_base_url: str
    openai_base_url: str
    anthropic_base_url: str
    expires_at: str
    issued_at_utc: datetime
    expires_at_utc: datetime

    def executor_egress_bases(self) -> _ExecutorEgressBases:
        """Return the exact signed bridge bases admitted for executor creation."""

        return _ExecutorEgressBases(
            callback_base_url=self.callback_base_url,
            openai_base_url=self.openai_base_url,
            anthropic_base_url=self.anthropic_base_url,
        )

    def _governed_egress_binding(
        self,
        *,
        request: SandboxRuntimeRequest,
        lease_identity: str,
    ) -> dict[str, object]:
        """Return every governed subject shared by proof creation and dispatch validation."""

        return {
            "runtime_subject": _opensandbox_governed_runtime_subject(
                self.runtime_identity,
                self.runtime_subject,
            ),
            "policy_subject": self.gateway_policy_subject,
            "callback_subject": self.callback_boundary_subject,
            "denial_subject": _opensandbox_governed_denial_subject(
                self.deny_audit_subject,
                self.deny_counter_subject,
            ),
            "network_id": self.profile_id,
            "network_name": self.endpoint,
            "tenant_id": request.tenant_id,
            "workspace_id": request.workspace_id,
            "user_id": request.user_id,
            "session_id": request.session_id,
            "run_id": request.run_id,
            "attempt_id": request.attempt_id,
            "image_subject": self.requested_image,
            "image_digest": self.requested_image_digest,
            "authorized_skill_scope": governed_egress_authorized_skill_scope(
                skill_ids=request.skill_ids,
                mcp_tool_ids=request.mcp_tool_ids,
            ),
            "authorized_native_tool_scope": governed_egress_authorized_native_tool_scope(
                request.tool_policy_subjects
            ),
            "lease_identity": lease_identity,
        }

    def governed_egress_proof(
        self,
        *,
        signing_key: object,
        key_id: object = GOVERNED_EGRESS_PROOF_DEFAULT_KEY_ID,
        request: SandboxRuntimeRequest,
        lease_identity: str,
        now: datetime | None = None,
    ) -> dict[str, object]:
        """Project the authenticated capability into the shared redacted proof contract."""
        issued_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        expires_at = min(
            self.expires_at_utc,
            issued_at + timedelta(seconds=GOVERNED_EGRESS_PROOF_MAX_TTL_SECONDS),
        )
        return build_governed_egress_proof(
            signing_key=signing_key,
            provider="opensandbox",
            # OpenSandbox has no Docker bridge.  Its authenticated runsc
            # capability supplies policy-bound enforcement instead.
            network_internal=False,
            key_id=key_id,
            issued_at=issued_at,
            expires_at=expires_at,
            **self._governed_egress_binding(
                request=request,
                lease_identity=lease_identity,
            ),
        )

    def lease_labels(
        self,
        *,
        signing_key: object,
        key_id: object = GOVERNED_EGRESS_PROOF_DEFAULT_KEY_ID,
        request: SandboxRuntimeRequest,
        lease_identity: str,
        now: datetime | None = None,
    ) -> dict[str, str]:
        proof = self.governed_egress_proof(
            signing_key=signing_key,
            key_id=key_id,
            request=request,
            lease_identity=lease_identity,
            now=now,
        )
        return {
            "ai-platform.external_egress.profile_version": "v1",
            "ai-platform.external_egress.profile_id": self.profile_id,
            "ai-platform.external_egress.runtime_identity": self.runtime_identity,
            "ai-platform.runtime_subject": self.runtime_subject,
            "ai-platform.external_egress.gateway_policy_subject": self.gateway_policy_subject,
            "ai-platform.external_egress.callback_boundary_subject": self.callback_boundary_subject,
            "ai-platform.external_egress.deny_audit_subject": self.deny_audit_subject,
            "ai-platform.external_egress.deny_counter_subject": self.deny_counter_subject,
            "ai-platform.external_egress.profile_requested_image": self.requested_image,
            "ai-platform.external_egress.profile_requested_image_digest": self.requested_image_digest,
            "ai-platform.external_egress.upstream_bridge_version": self.upstream_bridge_version,
            "ai-platform.external_egress.callback_base_sha256": hashlib.sha256(
                self.callback_base_url.encode("utf-8")
            ).hexdigest(),
            "ai-platform.external_egress.openai_base_sha256": hashlib.sha256(
                self.openai_base_url.encode("utf-8")
            ).hexdigest(),
            "ai-platform.external_egress.anthropic_base_sha256": hashlib.sha256(
                self.anthropic_base_url.encode("utf-8")
            ).hexdigest(),
            "ai-platform.external_egress.profile_expires_at": self.expires_at,
            GOVERNED_EGRESS_PROOF_LABEL: governed_egress_proof_label(proof),
        }


def _required_capability_value(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise OpenSandboxCapabilityAdmissionError(f"OpenSandbox capability profile {field} is missing") from None
    return normalized


def _required_profile_executor_image_digest(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability profile executor image digest is invalid") from None
    return value


def _validated_configured_capability_token(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > CAPABILITY_PROFILE_MAX_TOKEN_BYTES
        or any(not 0x21 <= ord(character) <= 0x7E for character in value)
    ):
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability authentication credential is invalid") from None
    return value


def _opensandbox_endpoint_subject(settings: Any) -> str:
    protocol = _required_capability_value(getattr(settings, "opensandbox_protocol", ""), field="endpoint protocol")
    domain = _required_capability_value(getattr(settings, "opensandbox_domain", ""), field="endpoint domain")
    try:
        parsed = urlsplit(f"{protocol}://{domain}")
    except ValueError:
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability endpoint configuration is invalid") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability endpoint configuration is invalid") from None
    try:
        port = parsed.port
    except ValueError:
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability endpoint configuration is invalid") from None
    if port is not None and not 1 <= port <= 65535:
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability endpoint configuration is invalid") from None
    host = parsed.hostname.lower()
    netloc = f"[{host}]" if ":" in host and not host.startswith("[") else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return f"{parsed.scheme.lower()}://{netloc}"


def _parse_profile_timestamp(value: object, *, field: str) -> datetime:
    raw = _required_capability_value(value, field=field)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise OpenSandboxCapabilityAdmissionError(f"OpenSandbox capability profile {field} is invalid") from None
    if parsed.tzinfo is None:
        raise OpenSandboxCapabilityAdmissionError(f"OpenSandbox capability profile {field} is invalid") from None
    return parsed.astimezone(timezone.utc)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalized_capability_profile_endpoint(url: str) -> str:
    """Return a DNS-free, transport-safe endpoint before an auth header is sent."""

    try:
        parsed = urlsplit(str(url or "").strip())
        port = parsed.port
    except ValueError:
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability authenticated endpoint is invalid") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability authenticated endpoint is invalid") from None
    if port is not None and not 1 <= port <= 65535:
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability authenticated endpoint is invalid") from None
    host = parsed.hostname.lower()
    if host == "localhost":
        pinned_host = "127.0.0.1"
    else:
        try:
            parsed_ip = ipaddress.ip_address(host)
        except ValueError:
            raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability authenticated endpoint is invalid") from None
        if (
            parsed_ip.version != 4
            or parsed_ip.is_link_local
            or parsed_ip.is_multicast
            or parsed_ip.is_unspecified
            or parsed_ip.is_reserved
            or not (parsed_ip.is_loopback or parsed_ip.is_private)
        ):
            raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability authenticated endpoint is invalid") from None
        pinned_host = str(parsed_ip)
    is_loopback = ipaddress.ip_address(pinned_host).is_loopback
    if parsed.scheme == "http" and not is_loopback:
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability authenticated endpoint requires HTTPS") from None
    netloc = pinned_host if port is None else f"{pinned_host}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path or "/", "", ""))


def _requested_executor_image_digest(settings: Any) -> str:
    return _opensandbox_requested_image(settings)[1]


def _validated_capability_request_headers(headers: dict[str, str]) -> dict[str, str]:
    authorization = headers.get("Authorization")
    if (
        set(headers) != {"Authorization"}
        or not isinstance(authorization, str)
        or not authorization.startswith("Bearer ")
    ):
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability authentication credential is invalid") from None
    token = authorization.removeprefix("Bearer ")
    if not token or any(not 0x21 <= ord(character) <= 0x7E for character in token):
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability authentication credential is invalid") from None
    return {"Authorization": f"Bearer {token}"}


def _default_opensandbox_capability_profile_fetcher(
    url: str,
    headers: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    endpoint = _normalized_capability_profile_endpoint(url)
    safe_headers = _validated_capability_request_headers(headers)
    timeout = min(max(float(timeout_seconds), 0.1), CAPABILITY_PROFILE_MAX_REQUEST_SECONDS)
    started_at = time.monotonic()
    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout=timeout, connect=min(timeout, 1.0)),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            with client.stream("GET", endpoint, headers=safe_headers) as response:
                if response.is_redirect:
                    raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability endpoint redirect is rejected") from None
                if response.status_code in {401, 403}:
                    raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability endpoint authentication failed") from None
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() - started_at > CAPABILITY_PROFILE_MAX_REQUEST_SECONDS:
                        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability endpoint request failed") from None
                    content.extend(chunk)
                    if len(content) > CAPABILITY_PROFILE_MAX_RESPONSE_BYTES:
                        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability profile response is too large") from None
        if time.monotonic() - started_at > CAPABILITY_PROFILE_MAX_REQUEST_SECONDS:
            raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability endpoint request failed") from None
        payload = json.loads(bytes(content))
    except OpenSandboxCapabilityAdmissionError as exc:
        message = str(exc)
        if message in {
            "OpenSandbox capability endpoint redirect is rejected",
            "OpenSandbox capability endpoint authentication failed",
            "OpenSandbox capability endpoint request failed",
            "OpenSandbox capability profile response is too large",
        }:
            raise OpenSandboxCapabilityAdmissionError(message) from None
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability endpoint request failed") from None
    except json.JSONDecodeError:
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability profile is malformed") from None
    except Exception:
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability endpoint request failed") from None
    if not isinstance(payload, dict):
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability profile is malformed") from None
    return payload


def _validate_opensandbox_external_egress_profile(
    profile: object,
    *,
    settings: Any,
    now: datetime | None = None,
) -> OpenSandboxExternalEgressCapability:
    if not isinstance(profile, dict):
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability profile is malformed") from None
    if set(profile) != _OPENSANDBOX_CAPABILITY_PROFILE_FIELDS:
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability profile shape is invalid") from None
    proof_key_id = _required_capability_value(profile.get("proof_key_id"), field="proof_key_id")
    if proof_key_id != _governed_egress_proof_key_id(settings):
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability profile proof key mismatch") from None
    signature = profile.get("profile_signature")
    signing_key = str(getattr(settings, "sandbox_egress_proof_signing_key", "") or "")
    if not has_governed_egress_signing_key(signing_key) or not isinstance(signature, str) or re.fullmatch(r"[0-9a-f]{64}", signature) is None:
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability profile signature is invalid") from None
    unsigned = {key: value for key, value in profile.items() if key != "profile_signature"}
    expected_signature = hmac.new(
        signing_key.encode("utf-8"),
        json.dumps(unsigned, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability profile signature is invalid") from None
    if profile.get("schema_version") != OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_SCHEMA_VERSION:
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability profile schema is unsupported") from None
    if profile.get("provider") != "opensandbox":
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability profile provider mismatch") from None
    issued_at = _parse_profile_timestamp(profile.get("issued_at"), field="issued_at")
    expires_at = _parse_profile_timestamp(profile.get("expires_at"), field="expires_at")
    current_time = (now or _utcnow()).astimezone(timezone.utc)
    if expires_at <= issued_at or expires_at - issued_at > timedelta(seconds=CAPABILITY_PROFILE_MAX_TTL_SECONDS):
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability profile ttl is invalid") from None
    if issued_at < current_time - timedelta(seconds=CAPABILITY_PROFILE_MAX_ISSUED_AGE_SECONDS):
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability profile is replayed") from None
    if issued_at > current_time + timedelta(seconds=CAPABILITY_PROFILE_CLOCK_SKEW_SECONDS):
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability profile is expired or not yet valid") from None
    if expires_at - current_time < timedelta(seconds=CAPABILITY_PROFILE_MIN_REMAINING_SECONDS):
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability profile remaining validity is insufficient") from None

    endpoint = _required_capability_value(profile.get("opensandbox_endpoint"), field="opensandbox_endpoint")
    if endpoint != _opensandbox_endpoint_subject(settings):
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability profile endpoint drift detected") from None
    runtime_identity = _required_capability_value(profile.get("runtime_identity"), field="runtime_identity")
    if runtime_identity != OPENSANDBOX_EXTERNAL_EGRESS_RUNTIME_IDENTITY:
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability profile runtime identity must be runsc") from None

    runtime_subject = _required_capability_value(
        getattr(settings, "sandbox_runtime_subject", ""), field="configured runtime subject"
    )
    if _required_capability_value(profile.get("ai_platform_runtime_subject"), field="ai_platform_runtime_subject") != runtime_subject:
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability profile runtime subject drift detected") from None
    gateway_policy_subject = _required_capability_value(profile.get("gateway_policy_subject"), field="gateway_policy_subject")
    if gateway_policy_subject != _required_capability_value(
        getattr(settings, "opensandbox_external_egress_gateway_policy_subject", ""),
        field="configured gateway policy subject",
    ):
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability profile gateway policy subject drift detected") from None
    callback_boundary_subject = _required_capability_value(
        profile.get("callback_boundary_subject"), field="callback_boundary_subject"
    )
    if callback_boundary_subject != _required_capability_value(
        getattr(settings, "opensandbox_external_egress_callback_boundary_subject", ""),
        field="configured callback boundary subject",
    ):
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability profile callback boundary subject drift detected") from None
    configured_bases = _opensandbox_external_egress_bases(settings)
    if profile.get("upstream_bridge_version") != OPENSANDBOX_UPSTREAM_BRIDGE_VERSION:
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability upstream bridge version is unsupported") from None
    for field, expected in (
        ("callback_base_url", configured_bases.callback_base_url),
        ("openai_base_url", configured_bases.openai_base_url),
        ("anthropic_base_url", configured_bases.anthropic_base_url),
    ):
        if _required_capability_value(profile.get(field), field=field) != expected:
            raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability upstream bridge drift detected") from None
    requested_image, requested_image_digest = _opensandbox_requested_image(settings)
    profile_image_digest = _required_profile_executor_image_digest(profile.get("executor_image_digest"))
    if profile_image_digest != requested_image_digest:
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability profile executor image digest mismatch") from None
    return OpenSandboxExternalEgressCapability(
        profile_id=_required_capability_value(profile.get("profile_id"), field="profile_id"),
        endpoint=endpoint,
        runtime_identity=runtime_identity,
        runtime_subject=runtime_subject,
        gateway_policy_subject=gateway_policy_subject,
        callback_boundary_subject=callback_boundary_subject,
        deny_audit_subject=_required_capability_value(profile.get("deny_audit_subject"), field="deny_audit_subject"),
        deny_counter_subject=_required_capability_value(profile.get("deny_counter_subject"), field="deny_counter_subject"),
        requested_image=requested_image,
        requested_image_digest=profile_image_digest,
        upstream_bridge_version=OPENSANDBOX_UPSTREAM_BRIDGE_VERSION,
        callback_base_url=configured_bases.callback_base_url,
        openai_base_url=configured_bases.openai_base_url,
        anthropic_base_url=configured_bases.anthropic_base_url,
        expires_at=expires_at.isoformat().replace("+00:00", "Z"),
        issued_at_utc=issued_at,
        expires_at_utc=expires_at,
    )


def _ensure_capability_still_valid(capability: OpenSandboxExternalEgressCapability, *, now: datetime) -> None:
    if capability.expires_at_utc - now.astimezone(timezone.utc) < timedelta(seconds=CAPABILITY_PROFILE_MIN_REMAINING_SECONDS):
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability profile remaining validity is insufficient") from None


async def _admit_opensandbox_external_egress_capability(
    *,
    settings: Any,
    fetcher: CapabilityProfileFetcher,
    now: datetime | None = None,
) -> OpenSandboxExternalEgressCapability:
    if getattr(settings, "sandbox_egress_policy_enabled", False) is True:
        raise OpenSandboxCapabilityAdmissionError(
            "gVisor/runsc OpenSandbox external-egress does not support OpenSandbox networkPolicy"
        ) from None
    _opensandbox_external_egress_bases(settings)
    capability_url = _required_capability_value(
        getattr(settings, "opensandbox_external_egress_capability_url", ""),
        field="authenticated endpoint",
    )
    endpoint = _normalized_capability_profile_endpoint(capability_url)
    _requested_executor_image_digest(settings)
    capability_token = _validated_configured_capability_token(
        getattr(settings, "opensandbox_external_egress_capability_token", "")
    )
    headers = _validated_capability_request_headers({"Authorization": f"Bearer {capability_token}"})
    try:
        profile = await asyncio.to_thread(
            fetcher,
            endpoint,
            headers,
            float(getattr(settings, "opensandbox_request_timeout_seconds", 30.0) or 30.0),
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {401, 403}:
            raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability endpoint authentication failed") from None
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability endpoint request failed") from None
    except OpenSandboxCapabilityAdmissionError as exc:
        if str(exc) == "OpenSandbox capability endpoint authentication failed":
            raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability endpoint authentication failed") from None
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability endpoint request failed") from None
    except Exception:
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox capability endpoint request failed") from None
    return _validate_opensandbox_external_egress_profile(profile, settings=settings, now=now)


_platform_metadata = runtime_scope_labels


def _opensandbox_labels(
    settings: Any,
    request: SandboxRuntimeRequest,
    capability: OpenSandboxExternalEgressCapability,
    skill_mount: _TrustedSkillMount | None,
    *,
    lease_identity: str | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    proof_label = (
        governed_egress_proof_label(
            capability.governed_egress_proof(
                signing_key=getattr(settings, "sandbox_egress_proof_signing_key", ""),
                key_id=_governed_egress_proof_key_id(settings),
                request=request,
                lease_identity=lease_identity,
                now=now,
            )
        )
        if lease_identity is not None
        else None
    )
    return governed_opensandbox_lease_labels(
        request,
        capability,
        executor_identity_labels=_executor_identity_labels(),
        skill_mount_labels=_skill_mount_labels(skill_mount),
        governed_proof_label=proof_label,
    )


def _trusted_internal_opensandbox_labels(
    request: SandboxRuntimeRequest,
    profile: _OpenSandboxSecurityProfile,
    skill_mount: _TrustedSkillMount | None,
) -> dict[str, str]:
    return trusted_internal_lease_labels(
        request,
        profile,
        executor_identity_labels=_executor_identity_labels(),
        skill_mount_labels=_skill_mount_labels(skill_mount),
    )


def _opensandbox_profile_labels(
    settings: Any,
    request: SandboxRuntimeRequest,
    profile: _OpenSandboxSecurityProfile,
    capability: OpenSandboxExternalEgressCapability | None,
    skill_mount: _TrustedSkillMount | None,
    *,
    lease_identity: str | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    if not profile.governed:
        return _trusted_internal_opensandbox_labels(request, profile, skill_mount)
    if capability is None:
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox governed capability is unavailable")
    return _opensandbox_labels(
        settings,
        request,
        capability,
        skill_mount,
        lease_identity=lease_identity,
        now=now,
    )


def _callback_policy_host(settings: Any) -> str:
    callback_host = str(getattr(settings, "sandbox_callback_host_gateway", "") or "").strip()
    if callback_host:
        return callback_host
    try:
        return _trusted_callback_target(settings).host
    except CallbackTargetValidationError:
        return ""


def _split_csv(value: object) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _opensandbox_network_policy(settings: Any, network_policy_class: Any, network_rule_class: Any) -> Any | None:
    if getattr(settings, "sandbox_egress_policy_enabled", False) is not True:
        return None
    allowed_hosts = []
    callback_host = _callback_policy_host(settings)
    if callback_host:
        allowed_hosts.append(callback_host)
    allowed_hosts.extend(_split_csv(getattr(settings, "opensandbox_allowed_egress_hosts", "")))
    rules = [network_rule_class(action="allow", target=host) for host in dict.fromkeys(allowed_hosts)]
    return network_policy_class(defaultAction="deny", egress=rules)


def _opensandbox_volumes(
    settings: Any,
    workspace: WorkspaceLease,
    skill_mount: _TrustedSkillMount | None,
    *,
    host_class: Any,
    volume_class: Any,
) -> list[Any]:
    """Remote OpenSandbox never receives a controller-local Host bind.

    The parameters remain temporarily for SDK-create shape compatibility; workspace
    transfer occurs only after the ready sandbox has a durable runtime lease.
    """

    del settings, workspace, skill_mount, host_class, volume_class
    return []


def _opensandbox_connection_config(settings: Any, connection_config_class: Any) -> Any:
    return connection_config_class(
        api_key=str(getattr(settings, "opensandbox_api_key", "") or "") or None,
        domain=str(getattr(settings, "opensandbox_domain", "") or "localhost:8080"),
        protocol=str(getattr(settings, "opensandbox_protocol", "http") or "http"),
        request_timeout=timedelta(
            seconds=max(float(getattr(settings, "opensandbox_request_timeout_seconds", 30.0) or 30.0), 1.0)
        ),
        use_server_proxy=bool(getattr(settings, "opensandbox_use_server_proxy", False)),
    )


def _opensandbox_sentinel_path(workspace: WorkspaceLease) -> str:
    return f"{workspace.workspace_container_path.rstrip('/')}/.ai-platform-opensandbox-lease.json"


_OPENSANDBOX_STAGE_MAX_FILES = 512
_OPENSANDBOX_STAGE_MAX_FILE_BYTES = 32 * 1024 * 1024
_OPENSANDBOX_STAGE_MAX_TOTAL_BYTES = 128 * 1024 * 1024
_OPENSANDBOX_STAGE_MAX_DIRECTORIES = 512
_OPENSANDBOX_COLLECT_MAX_FILES = 128
_OPENSANDBOX_COLLECT_MAX_FILE_BYTES = 64 * 1024 * 1024
_OPENSANDBOX_COLLECT_MAX_TOTAL_BYTES = 256 * 1024 * 1024
_OPENSANDBOX_COLLECT_MAX_DIRECTORIES = 256


@dataclass(frozen=True)
class _WorkspaceFileSnapshot:
    device: int
    inode: int
    mode: int
    link_count: int
    size: int
    modified_ns: int


@dataclass(frozen=True)
class _WorkspaceDirectorySnapshot:
    device: int
    inode: int
    mode: int


@dataclass(frozen=True)
class _OpenSandboxWorkspaceFile:
    relative_path: str
    source_path: Path
    snapshot: _WorkspaceFileSnapshot
    ancestor_directories: tuple[tuple[Path, _WorkspaceDirectorySnapshot], ...]


def _safe_workspace_relative_path(value: str) -> str:
    """Validate one controller-owned workspace-relative POSIX path."""

    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ContainerStartFailedError("workspace transfer path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ContainerStartFailedError("workspace transfer path is invalid")
    normalized = path.as_posix()
    if normalized != value:
        raise ContainerStartFailedError("workspace transfer path is invalid")
    return normalized


def _workspace_file_snapshot(path: Path) -> _WorkspaceFileSnapshot:
    try:
        node = path.lstat()
    except OSError as exc:
        raise ContainerStartFailedError("workspace transfer source is unavailable") from exc
    if not stat.S_ISREG(node.st_mode) or stat.S_ISLNK(node.st_mode) or node.st_nlink != 1:
        raise ContainerStartFailedError("workspace transfer source is invalid")
    return _WorkspaceFileSnapshot(
        device=int(node.st_dev),
        inode=int(node.st_ino),
        mode=int(node.st_mode),
        link_count=int(node.st_nlink),
        size=int(node.st_size),
        modified_ns=int(node.st_mtime_ns),
    )


def _assert_workspace_directory(path: Path) -> _WorkspaceDirectorySnapshot:
    try:
        node = path.lstat()
    except OSError as exc:
        raise ContainerStartFailedError("workspace transfer source is unavailable") from exc
    if stat.S_ISLNK(node.st_mode) or not stat.S_ISDIR(node.st_mode):
        raise ContainerStartFailedError("workspace transfer source is invalid")
    return _WorkspaceDirectorySnapshot(
        device=int(node.st_dev),
        inode=int(node.st_ino),
        mode=int(node.st_mode),
    )


def _directory_snapshot_from_stat(node: os.stat_result) -> _WorkspaceDirectorySnapshot:
    if not stat.S_ISDIR(node.st_mode):
        raise ContainerStartFailedError("workspace transfer source is invalid")
    return _WorkspaceDirectorySnapshot(
        device=int(node.st_dev),
        inode=int(node.st_ino),
        mode=int(node.st_mode),
    )


def _workspace_file_snapshot_from_stat(node: os.stat_result) -> _WorkspaceFileSnapshot:
    if not stat.S_ISREG(node.st_mode) or node.st_nlink != 1:
        raise ContainerStartFailedError("workspace transfer source is invalid")
    return _WorkspaceFileSnapshot(
        device=int(node.st_dev),
        inode=int(node.st_ino),
        mode=int(node.st_mode),
        link_count=int(node.st_nlink),
        size=int(node.st_size),
        modified_ns=int(node.st_mtime_ns),
    )


def _secure_workspace_transfer_supported() -> bool:
    return bool(
        getattr(os, "O_DIRECTORY", None)
        and getattr(os, "O_NOFOLLOW", None)
        and os.open in os.supports_dir_fd
        and os.rename in os.supports_dir_fd
    )


def _require_secure_workspace_transfer() -> None:
    if not _secure_workspace_transfer_supported():
        raise ContainerStartFailedError("OpenSandbox secure workspace transfer is unavailable on this controller")


def _directory_open_flags() -> int:
    return int(os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))


def _file_open_flags() -> int:
    return int(os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))


def _open_workspace_directory_fd(
    path: Path,
    expected_snapshot: _WorkspaceDirectorySnapshot | None = None,
) -> int:
    _require_secure_workspace_transfer()
    try:
        descriptor = os.open(path, _directory_open_flags())
    except OSError as exc:
        raise ContainerStartFailedError("workspace transfer source is unavailable") from exc
    try:
        snapshot = _directory_snapshot_from_stat(os.fstat(descriptor))
        if expected_snapshot is not None and snapshot != expected_snapshot:
            raise ContainerStartFailedError("workspace transfer source changed during read")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_workspace_relative_parent_fd(
    root_descriptor: int,
    relative_path: str,
    *,
    create: bool,
) -> tuple[int, str]:
    """Open a no-follow parent chain below a pinned workspace directory descriptor."""

    parts = PurePosixPath(_safe_workspace_relative_path(relative_path)).parts
    descriptor = os.dup(root_descriptor)
    try:
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise ContainerStartFailedError("workspace output destination is unavailable") from exc
            try:
                next_descriptor = os.open(part, _directory_open_flags(), dir_fd=descriptor)
            except OSError as exc:
                raise ContainerStartFailedError("workspace output destination is invalid") from exc
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor, parts[-1]
    except BaseException:
        os.close(descriptor)
        raise


def _open_workspace_file_fd(entry: _OpenSandboxWorkspaceFile) -> int:
    if not entry.ancestor_directories:
        raise ContainerStartFailedError("workspace transfer source is invalid")
    root, root_snapshot = entry.ancestor_directories[0]
    descriptor = _open_workspace_directory_fd(root, root_snapshot)
    current_path = root
    expected_directories = dict(entry.ancestor_directories)
    try:
        for part in PurePosixPath(entry.relative_path).parts[:-1]:
            current_path = current_path / part
            expected = expected_directories.get(current_path)
            if expected is None:
                raise ContainerStartFailedError("workspace transfer source is invalid")
            next_descriptor = os.open(part, _directory_open_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            if _directory_snapshot_from_stat(os.fstat(descriptor)) != expected:
                raise ContainerStartFailedError("workspace transfer source changed during read")
        try:
            file_descriptor = os.open(
                PurePosixPath(entry.relative_path).name,
                _file_open_flags(),
                dir_fd=descriptor,
            )
        except OSError as exc:
            raise ContainerStartFailedError("workspace transfer source cannot be read") from exc
    finally:
        os.close(descriptor)
    try:
        if _workspace_file_snapshot_from_stat(os.fstat(file_descriptor)) != entry.snapshot:
            raise ContainerStartFailedError("workspace transfer source changed during read")
        return file_descriptor
    except BaseException:
        os.close(file_descriptor)
        raise


def _stage_skills_required(request: SandboxRuntimeRequest) -> bool:
    return _staged_skill_mount_required(request) or any(skill_id != "general-chat" for skill_id in request.skill_ids)


def _build_opensandbox_workspace_manifest(
    request: SandboxRuntimeRequest,
    workspace: WorkspaceLease,
) -> tuple[list[str], list[_OpenSandboxWorkspaceFile]]:
    """Capture a bounded, no-follow manifest for remote workspace transfer."""

    root = Path(workspace.workspace_host_path)
    root_snapshot = _assert_workspace_directory(root)
    try:
        root.resolve(strict=True).relative_to(Path(workspace.host_root).resolve(strict=True))
    except (OSError, RuntimeError, ValueError) as exc:
        raise ContainerStartFailedError("workspace transfer source escapes attempt root") from exc

    directories = {"inputs", "outputs", "outputs/delivery", ".ai-platform"}
    files: list[_OpenSandboxWorkspaceFile] = []
    total_bytes = 0

    def add_file(
        path: Path,
        relative_path: str,
        ancestor_directories: tuple[tuple[Path, _WorkspaceDirectorySnapshot], ...],
    ) -> None:
        nonlocal total_bytes
        snapshot = _workspace_file_snapshot(path)
        if snapshot.size > _OPENSANDBOX_STAGE_MAX_FILE_BYTES:
            raise ContainerStartFailedError("workspace transfer exceeds file byte limit")
        total_bytes += snapshot.size
        if total_bytes > _OPENSANDBOX_STAGE_MAX_TOTAL_BYTES:
            raise ContainerStartFailedError("workspace transfer exceeds total byte limit")
        if len(files) >= _OPENSANDBOX_STAGE_MAX_FILES:
            raise ContainerStartFailedError("workspace transfer exceeds file count limit")
        files.append(
            _OpenSandboxWorkspaceFile(
                relative_path=_safe_workspace_relative_path(relative_path),
                source_path=path,
                snapshot=snapshot,
                ancestor_directories=ancestor_directories,
            )
        )

    def walk(
        directory: Path,
        relative_root: str,
        ancestor_directories: tuple[tuple[Path, _WorkspaceDirectorySnapshot], ...],
    ) -> None:
        directory_snapshot = _assert_workspace_directory(directory)
        stable_ancestors = (*ancestor_directories, (directory, directory_snapshot))
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise ContainerStartFailedError("workspace transfer source cannot be read") from exc
        if _assert_workspace_directory(directory) != directory_snapshot:
            raise ContainerStartFailedError("workspace transfer source changed during manifest")
        for child in children:
            name = child.name
            if not name or name in {".", ".."} or "\x00" in name or "/" in name or "\\" in name:
                raise ContainerStartFailedError("workspace transfer path is invalid")
            relative_path = name if not relative_root else f"{relative_root}/{name}"
            try:
                node = child.lstat()
            except OSError as exc:
                raise ContainerStartFailedError("workspace transfer source is unavailable") from exc
            if stat.S_ISLNK(node.st_mode):
                raise ContainerStartFailedError("workspace transfer source is invalid")
            if stat.S_ISDIR(node.st_mode):
                directories.add(_safe_workspace_relative_path(relative_path))
                if len(directories) > _OPENSANDBOX_STAGE_MAX_DIRECTORIES:
                    raise ContainerStartFailedError("workspace transfer exceeds directory limit")
                walk(child, relative_path, stable_ancestors)
            elif stat.S_ISREG(node.st_mode):
                add_file(child, relative_path, stable_ancestors)
            else:
                raise ContainerStartFailedError("workspace transfer source is invalid")
        if _assert_workspace_directory(directory) != directory_snapshot:
            raise ContainerStartFailedError("workspace transfer source changed during manifest")

    # Root materialized files are direct workspace children.  Never transfer
    # hidden/private run trees by incidental recursion.
    try:
        root_children = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise ContainerStartFailedError("workspace transfer source cannot be read") from exc
    if _assert_workspace_directory(root) != root_snapshot:
        raise ContainerStartFailedError("workspace transfer source changed during manifest")
    named_source_directories = {"inputs", ".ai-platform"}
    if _stage_skills_required(request):
        named_source_directories.add(".claude")
    for child in root_children:
        try:
            node = child.lstat()
        except OSError as exc:
            raise ContainerStartFailedError("workspace transfer source is unavailable") from exc
        if stat.S_ISLNK(node.st_mode):
            raise ContainerStartFailedError("workspace transfer source is invalid")
        if stat.S_ISREG(node.st_mode):
            add_file(child, child.name, ((root, root_snapshot),))
            continue
        if not stat.S_ISDIR(node.st_mode):
            raise ContainerStartFailedError("workspace transfer source is invalid")
        if child.name in named_source_directories:
            if child.name == ".claude":
                skills_root = child / "skills"
                claude_snapshot = _assert_workspace_directory(child)
                _assert_workspace_directory(skills_root)
                directories.update({".claude", ".claude/skills"})
                walk(
                    skills_root,
                    ".claude/skills",
                    ((root, root_snapshot), (child, claude_snapshot)),
                )
            else:
                directories.add(_safe_workspace_relative_path(child.name))
                walk(child, child.name, ((root, root_snapshot),))
    if _stage_skills_required(request) and not (root / ".claude" / "skills").is_dir():
        raise ContainerStartFailedError("workspace transfer Skill source is unavailable")
    if _assert_workspace_directory(root) != root_snapshot:
        raise ContainerStartFailedError("workspace transfer source changed during manifest")
    return sorted(directories, key=lambda item: (item.count("/"), item)), sorted(files, key=lambda item: item.relative_path)


def _read_stable_workspace_file(entry: _OpenSandboxWorkspaceFile) -> bytes:
    """Read via an anchored no-follow descriptor chain and prove it remained stable."""

    for directory, snapshot in entry.ancestor_directories:
        if _assert_workspace_directory(directory) != snapshot:
            raise ContainerStartFailedError("workspace transfer source changed during read")
    before = _workspace_file_snapshot(entry.source_path)
    if before != entry.snapshot:
        raise ContainerStartFailedError("workspace transfer source changed during read")
    try:
        descriptor = _open_workspace_file_fd(entry)
        try:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _OPENSANDBOX_STAGE_MAX_FILE_BYTES:
                    raise ContainerStartFailedError("workspace transfer exceeds file byte limit")
                chunks.append(chunk)
            if total != entry.snapshot.size or _workspace_file_snapshot_from_stat(os.fstat(descriptor)) != entry.snapshot:
                raise ContainerStartFailedError("workspace transfer source changed during read")
        finally:
            os.close(descriptor)
    except SandboxRuntimeError:
        raise
    except OSError as exc:
        raise ContainerStartFailedError("workspace transfer source cannot be read") from exc
    if _workspace_file_snapshot(entry.source_path) != entry.snapshot:
        raise ContainerStartFailedError("workspace transfer source changed during read")
    for directory, snapshot in entry.ancestor_directories:
        if _assert_workspace_directory(directory) != snapshot:
            raise ContainerStartFailedError("workspace transfer source changed during read")
    return b"".join(chunks)


_OPENSANDBOX_CONFIRMED_STOP_STATUSES = frozenset({"running", "created", "removed", "exited", "paused"})


def _opensandbox_cleanup_expected_binding(
    status: ContainerStatus,
    lease: ContainerLease,
) -> tuple[dict[str, object], datetime, str] | None:
    """Derive signed cleanup subjects only from complete authoritative remote metadata."""

    labels = status.detail.get("labels")
    if not opensandbox_metadata.opensandbox_status_matches_lease(labels, lease.labels):
        return None
    labels = lease.labels
    runtime_identity = str(labels.get("ai-platform.external_egress.runtime_identity") or "")
    runtime_subject = str(labels.get("ai-platform.runtime_subject") or "")
    gateway_policy_subject = str(labels.get("ai-platform.external_egress.gateway_policy_subject") or "")
    callback_boundary_subject = str(labels.get("ai-platform.external_egress.callback_boundary_subject") or "")
    deny_audit_subject = str(labels.get("ai-platform.external_egress.deny_audit_subject") or "")
    deny_counter_subject = str(labels.get("ai-platform.external_egress.deny_counter_subject") or "")
    profile_id = str(labels.get("ai-platform.external_egress.profile_id") or "")
    endpoint_sha256 = str(labels.get("ai-platform.external_egress.endpoint_sha256") or "")
    requested_image = str(labels.get("ai-platform.executor.requested_image") or "")
    requested_image_digest = str(labels.get("ai-platform.executor.requested_image_digest") or "")
    profile_requested_image = str(labels.get("ai-platform.external_egress.profile_requested_image") or "")
    profile_requested_image_digest = str(
        labels.get("ai-platform.external_egress.profile_requested_image_digest") or ""
    )
    required_subjects = (
        runtime_subject,
        gateway_policy_subject,
        callback_boundary_subject,
        deny_audit_subject,
        deny_counter_subject,
        profile_id,
        requested_image,
        requested_image_digest,
    )
    if (
        runtime_identity != OPENSANDBOX_EXTERNAL_EGRESS_RUNTIME_IDENTITY
        or labels.get("ai-platform.provider_backend") != "opensandbox"
        or labels.get("ai-platform.external_egress.profile_version") != "v1"
        or any(not subject for subject in required_subjects)
        or not re.fullmatch(r"[0-9a-f]{64}", endpoint_sha256)
        or profile_requested_image != requested_image
        or profile_requested_image_digest != requested_image_digest
    ):
        return None
    try:
        profile_expires_at = _parse_profile_timestamp(
            labels.get("ai-platform.external_egress.profile_expires_at"),
            field="profile_expires_at",
        )
    except OpenSandboxCapabilityAdmissionError:
        return None
    return (
        {
            "runtime_subject": _opensandbox_governed_runtime_subject(runtime_identity, runtime_subject),
            "policy_subject": gateway_policy_subject,
            "callback_subject": callback_boundary_subject,
            "denial_subject": _opensandbox_governed_denial_subject(
                deny_audit_subject,
                deny_counter_subject,
            ),
            "network_id": profile_id,
            "tenant_id": lease.tenant_id,
            "workspace_id": lease.workspace_id,
            "user_id": lease.user_id,
            "session_id": lease.session_id,
            "run_id": lease.run_id,
            "attempt_id": str(labels.get("ai-platform.attempt_id") or ""),
            "image_subject": requested_image,
            "image_digest": requested_image_digest,
            "lease_identity": f"opensandbox:{lease.container_name}:{lease.container_id}",
        },
        profile_expires_at,
        endpoint_sha256,
    )


def _opensandbox_cleanup_identity_is_authorized(
    status: ContainerStatus,
    lease: ContainerLease,
    settings: Any,
    *,
    now: datetime,
) -> bool:
    """Verify remote cleanup identity using the lease's profile-specific evidence."""

    status_labels = status.detail.get("labels")
    if not isinstance(status_labels, dict):
        return False
    lease_profile = str(lease.labels.get(SANDBOX_SECURITY_PROFILE_LABEL) or SANDBOX_SECURITY_PROFILE_GOVERNED)
    if lease_profile == SANDBOX_SECURITY_PROFILE_TRUSTED_INTERNAL:
        return trusted_internal_cleanup_identity_is_authorized(status_labels, lease.labels)
    if lease_profile != SANDBOX_SECURITY_PROFILE_GOVERNED:
        return False
    encoded_proof = lease.labels.get(GOVERNED_EGRESS_PROOF_LABEL)
    if not isinstance(encoded_proof, str):
        return False
    try:
        proof = json.loads(encoded_proof)
    except (TypeError, ValueError):
        return False
    expected = _opensandbox_cleanup_expected_binding(status, lease)
    if expected is None or not isinstance(proof, dict):
        return False
    expected_binding, profile_expires_at, endpoint_sha256 = expected
    try:
        proof_expires_at = _parse_profile_timestamp(proof.get("expires_at"), field="proof_expires_at")
    except OpenSandboxCapabilityAdmissionError:
        return False
    if (
        proof_expires_at != profile_expires_at
        or not hmac.compare_digest(str(proof.get("network_name_sha256") or ""), endpoint_sha256)
    ):
        return False
    return is_governed_egress_proof(
        proof,
        provider="opensandbox",
        signing_key=getattr(settings, "sandbox_egress_proof_signing_key", ""),
        signing_key_id=_governed_egress_proof_key_id(settings),
        previous_signing_keys=governed_egress_previous_signing_keys(
            getattr(settings, "sandbox_egress_proof_previous_keys_json", "")
        ),
        allow_previous_keys=True,
        expected_binding=expected_binding,
        now=now,
        require_fresh=False,
    )


def _docker_network_options(network: Any) -> dict[str, str]:
    if isinstance(network, dict):
        attrs = network.get("attrs")
        raw_options = (
            (attrs.get("Options") if isinstance(attrs, dict) else None)
            or network.get("Options")
            or network.get("options")
            or {}
        )
    else:
        attrs = getattr(network, "attrs", {})
        raw_options = attrs.get("Options") if isinstance(attrs, dict) else {}
    if not isinstance(raw_options, dict):
        return {}
    return {str(key): str(value).lower() for key, value in raw_options.items()}


def _docker_network_has_no_masquerade(network: Any) -> bool:
    options = _docker_network_options(network)
    return options.get("com.docker.network.bridge.enable_ip_masquerade") == "false"


def _docker_network_authoritative_attrs(network: Any) -> dict[str, Any]:
    attrs = network.get("attrs") if isinstance(network, dict) else getattr(network, "attrs", None)
    return dict(attrs) if isinstance(attrs, dict) else {}


def _is_versioned_internal_network_name(name: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9_.-]*-internal-v[1-9][0-9]*", name))


def _docker_governed_network_identity(network: Any, configured_name: str) -> tuple[str, str]:
    if hasattr(network, "reload"):
        try:
            network.reload()
        except Exception:
            raise GovernedEgressAdmissionError() from None
    attrs = _docker_network_authoritative_attrs(network)
    network_id = str(attrs.get("Id") or "").strip()
    network_name = str(attrs.get("Name") or "").strip()
    if (
        not network_id
        or len(network_id) > 512
        or network_name != configured_name
        or attrs.get("Driver") != "bridge"
        or attrs.get("Internal") is not True
        # Treat every unrecognized bridge option as a new enforcement surface.
        # The governed network is deliberately minimal: an internal bridge with
        # masquerading disabled.  A permissive option must not be accepted just
        # because the network happens to also set Internal=true.
        or _docker_network_options(network)
        != {"com.docker.network.bridge.enable_ip_masquerade": "false"}
    ):
        raise GovernedEgressAdmissionError() from None
    return network_id, network_name


_GOVERNED_DOCKER_CALLBACK_ALIAS = "api.sandbox.internal"
_GOVERNED_DOCKER_API_RELEASE_OWNER = "repo-local-compose"
_GOVERNED_DOCKER_NETWORK_OWNER = "sandbox-runtime-governed-egress-v2"


def _runtime_release_commit(settings: Any) -> str:
    """Return the exact release commit which must match the callback witness."""
    commit = str(getattr(settings, "ai_platform_runtime_commit", "") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise GovernedEgressAdmissionError() from None
    return commit


def _governed_egress_proof_key_id(settings: Any) -> str:
    key_id = str(
        getattr(settings, "sandbox_egress_proof_key_id", GOVERNED_EGRESS_PROOF_DEFAULT_KEY_ID) or ""
    ).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", key_id):
        raise GovernedEgressAdmissionError() from None
    return key_id


def _docker_governed_callback_target(settings: Any) -> Any:
    try:
        callback = _trusted_callback_target(settings, allow_host_gateway=False)
    except CallbackTargetValidationError:
        raise GovernedEgressAdmissionError() from None
    # A suffix-only internal hostname is not enough.  The governed bridge has
    # one deliberately named API witness, supplied by Compose/operator state.
    if callback.host != _GOVERNED_DOCKER_CALLBACK_ALIAS:
        raise GovernedEgressAdmissionError() from None
    return callback


def _docker_image_subjects(settings: Any) -> tuple[str, str]:
    image = str(getattr(settings, "sandbox_executor_image", "") or "").strip()
    # A release can only resolve a locally-built image to its immutable Docker
    # ID.  Accept that offline subject, but never a mutable tag by itself.
    if re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        return image, image
    image_name, separator, digest = image.rpartition("@")
    if (
        not image_name
        or separator != "@"
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
        or len(image) > 2048
    ):
        raise GovernedEgressAdmissionError() from None
    return image, digest


@dataclass(frozen=True)
class _DockerGovernedEgressAdmission:
    create_kwargs: dict[str, Any]
    lease_labels: dict[str, str]
    network_id: str
    network_name: str
    callback_base_url: str
    runtime_commit: str


def _governed_docker_network_name(lease: ContainerLease) -> str:
    """Derive a non-secret, per-lease bridge name that cannot be shared by runs."""
    scope = "\x00".join(
        (lease.tenant_id, lease.workspace_id, lease.user_id, lease.session_id, lease.run_id, lease.container_name)
    )
    return f"ai-platform-sandbox-egress-v2-{hashlib.sha256(scope.encode('utf-8')).hexdigest()[:32]}"


def _governed_docker_network_labels(lease: ContainerLease) -> dict[str, str]:
    return {
        "ai-platform.owner": _GOVERNED_DOCKER_NETWORK_OWNER,
        "ai-platform.tenant_id": lease.tenant_id,
        "ai-platform.workspace_id": lease.workspace_id,
        "ai-platform.user_id": lease.user_id,
        "ai-platform.session_id": lease.session_id,
        "ai-platform.run_id": lease.run_id,
        "ai-platform.container_name": lease.container_name,
    }


def _lease_from_owned_governed_network(network: Any) -> ContainerLease | None:
    """Recover only the non-secret lease identity necessary to clean an orphan bridge."""
    attrs = _docker_network_authoritative_attrs(network)
    labels = attrs.get("Labels")
    if not isinstance(labels, dict) or labels.get("ai-platform.owner") != _GOVERNED_DOCKER_NETWORK_OWNER:
        return None
    values = {
        key: str(labels.get(key) or "").strip()
        for key in (
            "ai-platform.tenant_id",
            "ai-platform.workspace_id",
            "ai-platform.user_id",
            "ai-platform.session_id",
            "ai-platform.run_id",
            "ai-platform.container_name",
        )
    }
    if not all(values.values()) or values["ai-platform.container_name"] != f"executor-exec-{values['ai-platform.run_id']}":
        return None
    lease = ContainerLease(
        container_id=f"exec-{values['ai-platform.run_id']}",
        container_name=values["ai-platform.container_name"],
        provider="docker",
        executor_url="http://sandbox-runtime.invalid",
        tenant_id=values["ai-platform.tenant_id"],
        workspace_id=values["ai-platform.workspace_id"],
        user_id=values["ai-platform.user_id"],
        session_id=values["ai-platform.session_id"],
        run_id=values["ai-platform.run_id"],
        sandbox_mode="ephemeral",
        browser_enabled=False,
        workspace_host_path="",
    )
    try:
        _docker_owned_governed_network(network, lease)
    except GovernedEgressAdmissionError:
        return None
    return lease


def _docker_api_callback_witness(client: Any, expected_source: str) -> tuple[Any, str, str]:
    """Return the one running, healthy, provenance-bound platform API witness."""
    try:
        containers = client.containers.list(all=True)
    except Exception:
        raise GovernedEgressAdmissionError() from None
    witnesses: list[tuple[Any, str, str]] = []
    for container in containers:
        if hasattr(container, "reload"):
            try:
                container.reload()
            except Exception:
                continue
        labels = _container_labels(container)
        if (
            labels.get("ai-platform.release-role") != "api"
            or labels.get("ai-platform.release-owner") != _GOVERNED_DOCKER_API_RELEASE_OWNER
        ):
            continue
        source = str(labels.get("ai-platform.source-commit") or "")
        attrs = getattr(container, "attrs", {})
        state = attrs.get("State") if isinstance(attrs, dict) else None
        health = state.get("Health") if isinstance(state, dict) else None
        container_id = str(getattr(container, "id", "") or "").strip()
        inspected_id = str(attrs.get("Id") or "").strip() if isinstance(attrs, dict) else ""
        if (
            source != expected_source
            or getattr(container, "status", "") != "running"
            or not isinstance(health, dict)
            or health.get("Status") != "healthy"
            or not container_id
            or container_id != inspected_id
        ):
            continue
        witnesses.append((container, container_id, source))
    if len(witnesses) != 1:
        raise GovernedEgressAdmissionError() from None
    return witnesses[0]


def _docker_owned_api_callback_container(client: Any, expected_source: str) -> tuple[Any, str]:
    """Locate exactly one release-bound API for safe owned-network cleanup."""
    try:
        containers = client.containers.list(all=True)
    except Exception:
        raise GovernedEgressAdmissionError() from None
    candidates: list[tuple[Any, str]] = []
    for container in containers:
        if hasattr(container, "reload"):
            try:
                container.reload()
            except Exception:
                continue
        labels = _container_labels(container)
        attrs = getattr(container, "attrs", {})
        container_id = str(getattr(container, "id", "") or "").strip()
        inspected_id = str(attrs.get("Id") or "").strip() if isinstance(attrs, dict) else ""
        if (
            labels.get("ai-platform.release-role") == "api"
            and labels.get("ai-platform.release-owner") == _GOVERNED_DOCKER_API_RELEASE_OWNER
            and labels.get("ai-platform.source-commit") == expected_source
            and container_id
            and container_id == inspected_id
        ):
            candidates.append((container, container_id))
    if len(candidates) != 1:
        raise GovernedEgressAdmissionError() from None
    return candidates[0]


def _docker_owned_governed_network(network: Any, lease: ContainerLease) -> tuple[str, str]:
    name = _governed_docker_network_name(lease)
    network_id, network_name = _docker_governed_network_identity(network, name)
    attrs = _docker_network_authoritative_attrs(network)
    labels = attrs.get("Labels")
    if not isinstance(labels, dict) or any(
        str(labels.get(key) or "") != value
        for key, value in _governed_docker_network_labels(lease).items()
    ):
        raise GovernedEgressAdmissionError() from None
    return network_id, network_name


def _get_or_create_governed_docker_network(client: Any, lease: ContainerLease) -> tuple[Any, str, str]:
    name = _governed_docker_network_name(lease)
    networks = getattr(client, "networks", None)
    if networks is None:
        raise GovernedEgressAdmissionError() from None
    try:
        network = networks.get(name)
    except Exception:
        try:
            network = networks.create(
                name,
                driver="bridge",
                internal=True,
                options={"com.docker.network.bridge.enable_ip_masquerade": "false"},
                labels=_governed_docker_network_labels(lease),
            )
        except Exception:
            try:
                network = networks.get(name)
            except Exception:
                raise GovernedEgressAdmissionError() from None
    network_id, network_name = _docker_owned_governed_network(network, lease)
    return network, network_id, network_name


def _attach_api_callback_witness(network: Any, api_container: Any) -> None:
    connect = getattr(network, "connect", None)
    if not callable(connect):
        raise GovernedEgressAdmissionError() from None
    try:
        connect(api_container, aliases=[_GOVERNED_DOCKER_CALLBACK_ALIAS])
    except Exception:
        # Docker reports an error when an already-attached API is reconnected.
        # The following authoritative readback decides whether that is safe.
        pass


def _docker_network_attachment(container: Any, network_name: str, network_id: str) -> tuple[str, dict[str, Any]]:
    if hasattr(container, "reload"):
        try:
            container.reload()
        except Exception:
            raise GovernedEgressAdmissionError() from None
    attrs = getattr(container, "attrs", {})
    if not isinstance(attrs, dict):
        raise GovernedEgressAdmissionError() from None
    container_id = str(getattr(container, "id", "") or "").strip()
    inspected_id = str(attrs.get("Id") or "").strip()
    network_settings = attrs.get("NetworkSettings")
    networks = network_settings.get("Networks") if isinstance(network_settings, dict) else None
    host_config = attrs.get("HostConfig")
    extra_hosts = host_config.get("ExtraHosts") if isinstance(host_config, dict) else None
    if (
        not container_id
        or container_id != inspected_id
        or not isinstance(networks, dict)
        or set(networks) != {network_name}
        or extra_hosts not in (None, [])
    ):
        raise GovernedEgressAdmissionError() from None
    attachment = networks.get(network_name)
    attachment_id = str(
        attachment.get("NetworkID") if isinstance(attachment, dict) else ""
    ).strip()
    if attachment_id != network_id:
        raise GovernedEgressAdmissionError() from None
    return container_id, dict(attachment)


def _docker_callback_endpoint_subject(
    client: Any,
    *,
    network_name: str,
    network_id: str,
    callback_base_url: str,
    expected_source: str,
) -> str:
    container, container_id, source = _docker_api_callback_witness(client, expected_source)
    attrs = getattr(container, "attrs", {})
    network_settings = attrs.get("NetworkSettings") if isinstance(attrs, dict) else None
    networks = network_settings.get("Networks") if isinstance(network_settings, dict) else None
    attachment = networks.get(network_name) if isinstance(networks, dict) else None
    aliases = attachment.get("Aliases") if isinstance(attachment, dict) else None
    if (
        not isinstance(attachment, dict)
        or str(attachment.get("NetworkID") or "") != network_id
        or not isinstance(aliases, list)
        or aliases.count(_GOVERNED_DOCKER_CALLBACK_ALIAS) != 1
    ):
        raise GovernedEgressAdmissionError() from None
    return f"{callback_base_url}|{container_id}|{source}|{network_id}|{_GOVERNED_DOCKER_CALLBACK_ALIAS}"


def _docker_complete_governed_network_members(
    client: Any,
    *,
    network: Any,
    lease: ContainerLease,
    network_id: str,
    network_name: str,
    callback_base_url: str,
    expected_source: str,
) -> tuple[Any, Any]:
    """Prove a governed bridge has exactly its current API witness and primary lease."""
    _docker_owned_governed_network(network, lease)
    api_container, api_id, _api_source = _docker_api_callback_witness(client, expected_source)
    try:
        primary = client.containers.get(lease.container_name)
    except Exception:
        raise GovernedEgressAdmissionError() from None
    if not DockerContainerProvider._is_exact_owned_remote_container(
        primary,
        lease,
        require_lease_identity=True,
    ):
        raise GovernedEgressAdmissionError() from None
    primary_id, _attachment = _docker_network_attachment(primary, network_name, network_id)
    attrs = _docker_network_authoritative_attrs(network)
    members = attrs.get("Containers")
    if not isinstance(members, dict) or {str(member_id) for member_id in members} != {api_id, primary_id}:
        raise GovernedEgressAdmissionError() from None
    _docker_callback_endpoint_subject(
        client,
        network_name=network_name,
        network_id=network_id,
        callback_base_url=callback_base_url,
        expected_source=expected_source,
    )
    return primary, api_container


def _seal_docker_governed_egress_after_readback(
    client: Any,
    settings: Any,
    request: SandboxRuntimeRequest,
    lease: ContainerLease,
    admission: _DockerGovernedEgressAdmission,
    container: Any,
) -> dict[str, str]:
    """Seal Docker evidence only after inspecting the created runtime container."""
    try:
        network = client.networks.get(admission.network_name)
    except Exception:
        raise GovernedEgressAdmissionError() from None
    network_id, network_name = _docker_governed_network_identity(network, admission.network_name)
    if network_id != admission.network_id or network_name != admission.network_name:
        raise GovernedEgressAdmissionError() from None
    container_id, _attachment = _docker_network_attachment(container, network_name, network_id)
    if container_id != lease.container_id:
        raise GovernedEgressAdmissionError() from None
    _docker_complete_governed_network_members(
        client,
        network=network,
        lease=lease,
        network_id=network_id,
        network_name=network_name,
        callback_base_url=admission.callback_base_url,
        expected_source=admission.runtime_commit,
    )
    callback_subject = _docker_callback_endpoint_subject(
        client,
        network_name=network_name,
        network_id=network_id,
        callback_base_url=admission.callback_base_url,
        expected_source=admission.runtime_commit,
    )
    try:
        proof = build_governed_egress_proof(
            signing_key=getattr(settings, "sandbox_egress_proof_signing_key", ""),
            provider="docker",
            runtime_subject="docker-internal-bridge",
            policy_subject=f"{network_id}:{network_name}:internal",
            callback_subject=callback_subject,
            denial_subject=f"{network_id}:internal-default-deny",
            network_id=network_id,
            network_name=network_name,
            network_internal=True,
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            user_id=request.user_id,
            session_id=request.session_id,
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            image_subject=admission.lease_labels["ai-platform.executor.requested_image"],
            image_digest=admission.lease_labels["ai-platform.executor.requested_image_digest"],
            authorized_skill_scope=governed_egress_authorized_skill_scope(
                skill_ids=request.skill_ids,
                mcp_tool_ids=request.mcp_tool_ids,
            ),
            authorized_native_tool_scope=governed_egress_authorized_native_tool_scope(request.tool_policy_subjects),
            lease_identity=f"docker:{lease.container_name}:{container_id}",
            key_id=_governed_egress_proof_key_id(settings),
        )
    except (KeyError, ValueError):
        raise GovernedEgressAdmissionError() from None
    return {**admission.lease_labels, GOVERNED_EGRESS_PROOF_LABEL: governed_egress_proof_label(proof)}


def _admit_docker_governed_egress(
    client: Any,
    settings: Any,
    request: SandboxRuntimeRequest,
    lease: ContainerLease,
) -> _DockerGovernedEgressAdmission:
    if getattr(settings, "sandbox_egress_policy_enabled", False) is not True:
        raise GovernedEgressAdmissionError() from None
    signing_key = getattr(settings, "sandbox_egress_proof_signing_key", "")
    if not has_governed_egress_signing_key(signing_key):
        raise GovernedEgressAdmissionError() from None
    image_subject, image_digest = _docker_image_subjects(settings)
    callback = _docker_governed_callback_target(settings)
    runtime_commit = _runtime_release_commit(settings)
    api_container, _api_container_id, _api_source = _docker_api_callback_witness(client, runtime_commit)
    network, network_id, verified_network_name = _get_or_create_governed_docker_network(client, lease)
    _attach_api_callback_witness(network, api_container)
    _docker_callback_endpoint_subject(
        client,
        network_name=verified_network_name,
        network_id=network_id,
        callback_base_url=callback.base_url,
        expected_source=runtime_commit,
    )
    return _DockerGovernedEgressAdmission(
        create_kwargs={"network": verified_network_name},
        lease_labels={
            "ai-platform.executor.requested_image": image_subject,
            "ai-platform.executor.requested_image_digest": image_digest,
        },
        network_id=network_id,
        network_name=verified_network_name,
        callback_base_url=callback.base_url,
        runtime_commit=runtime_commit,
    )


def _is_permission_denied(message: str) -> bool:
    return "permission denied" in message.lower()


def _is_docker_daemon_unavailable(message: str) -> bool:
    normalized = message.lower()
    return (
        "cannot connect" in normalized
        or "connection refused" in normalized
        or "connection aborted" in normalized
        or "no such file" in normalized
        or "docker daemon" in normalized
        or "docker.sock" in normalized
    )


def _normalize_docker_availability_error(exc: BaseException) -> SandboxRuntimeError | None:
    message = str(exc)
    if _is_permission_denied(message):
        return DockerPermissionDeniedError()
    if _is_docker_daemon_unavailable(message):
        return DockerUnavailableError("Docker daemon is unavailable")
    return None


def _is_not_found_error(exc: BaseException) -> bool:
    if isinstance(exc, KeyError):
        return True
    if docker is not None:
        not_found_error = getattr(getattr(docker, "errors", None), "NotFound", None)
        if not_found_error is not None and isinstance(exc, not_found_error):
            return True
    message = str(exc).lower()
    return ("not found" in message or "no such container" in message) and (
        "container" in message or "docker" in message or "404" in message
    )


def default_executor_health_probe(
    executor_url: str,
    timeout_seconds: int,
    executor_headers: dict[str, str] | None = None,
) -> bool:
    deadline = time.monotonic() + max(timeout_seconds, 1)
    logical_health_url = f"{executor_url.rstrip('/')}/health"
    health_url, request_headers = prepare_executor_http_request(logical_health_url, executor_headers)
    while time.monotonic() <= deadline:
        try:
            with httpx.Client(timeout=1.0) as client:
                if request_headers:
                    response = client.get(health_url, headers=request_headers)
                else:
                    response = client.get(health_url)
                response.raise_for_status()
            return True
        except Exception:
            time.sleep(0.25)
    return False


def default_executor_identity_probe(
    executor_url: str,
    timeout_seconds: int,
    executor_headers: dict[str, str],
) -> dict[str, int]:
    """Read the effective executor process identity over its lease credential."""

    deadline = time.monotonic() + max(timeout_seconds, 1)
    logical_identity_url = f"{executor_url.rstrip('/')}/health/runtime-identity"
    identity_url, request_headers = prepare_executor_http_request(logical_identity_url, executor_headers)
    if not request_headers.get(EXECUTOR_AUTH_HEADER):
        raise ContainerStartFailedError("executor identity credential unavailable")
    while time.monotonic() <= deadline:
        try:
            with httpx.Client(timeout=1.0) as client:
                response = client.get(identity_url, headers=request_headers)
                response.raise_for_status()
                payload = response.json()
            if not isinstance(payload, dict) or set(payload) != {"uid", "gid"}:
                raise ValueError("invalid executor identity response")
            uid = payload.get("uid")
            gid = payload.get("gid")
            if not isinstance(uid, int) or isinstance(uid, bool) or not isinstance(gid, int) or isinstance(gid, bool):
                raise ValueError("invalid executor identity response")
            return {"uid": uid, "gid": gid}
        except (httpx.HTTPError, TypeError, ValueError):
            time.sleep(0.25)
    raise ContainerStartFailedError("executor identity unavailable")


def default_governed_callback_reachability_probe(
    container: Any,
    callback_base_url: str,
    expected_runtime_commit: str,
) -> bool:
    """Prove sandbox-to-API HTTP health and release identity without retaining payloads."""
    parsed = urlsplit(callback_base_url)
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or not re.fullmatch(r"[0-9a-f]{40}", expected_runtime_commit)
        or not hasattr(container, "exec_run")
    ):
        return False
    try:
        result = container.exec_run(
            egress_diagnostics.callback_probe_command(callback_base_url, expected_runtime_commit),
            user=f"{RUNTIME_UID}:{RUNTIME_GID}",
        )
    except Exception:
        return egress_diagnostics.record_callback_exec_exception()
    return egress_diagnostics.record_callback_exec_result(result)


def _require_expected_executor_identity(identity: object) -> None:
    if not isinstance(identity, dict):
        raise ContainerStartFailedError("executor identity mismatch")
    uid = identity.get("uid")
    gid = identity.get("gid")
    if isinstance(uid, bool) or isinstance(gid, bool) or (uid, gid) != (RUNTIME_UID, RUNTIME_GID):
        raise ContainerStartFailedError("executor identity mismatch")


def _call_executor_health_probe(
    health_probe: Callable[..., bool],
    executor_url: str,
    timeout_seconds: int,
    executor_headers: dict[str, str] | None = None,
) -> bool:
    try:
        parameters = inspect.signature(health_probe).parameters.values()
    except (TypeError, ValueError):
        return health_probe(executor_url, timeout_seconds)
    accepts_headers = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD or parameter.name == "executor_headers"
        for parameter in parameters
    )
    if accepts_headers:
        return health_probe(executor_url, timeout_seconds, executor_headers=dict(executor_headers or {}))
    return health_probe(executor_url, timeout_seconds)


def _stop_and_remove_container(container: Any) -> bool:
    stop_succeeded = not hasattr(container, "stop")
    if hasattr(container, "stop"):
        try:
            container.stop()
            stop_succeeded = True
        except Exception:
            pass
    remove_succeeded = not hasattr(container, "remove")
    if hasattr(container, "remove"):
        try:
            container.remove(force=True)
            remove_succeeded = True
        except Exception:
            pass
    return remove_succeeded or (stop_succeeded and not hasattr(container, "remove"))


def _generate_executor_auth_token() -> str:
    return secrets.token_urlsafe(32)


def _executor_auth_headers(
    executor_auth_token: str,
    headers: dict[str, str] | None = None,
    *,
    connect_base_url: str = "",
) -> dict[str, str]:
    resolved = dict(headers or {})
    resolved[EXECUTOR_AUTH_HEADER] = executor_auth_token
    if connect_base_url:
        resolved[EXECUTOR_CONNECT_BASE_URL_METADATA] = connect_base_url
    return resolved


def _executor_connect_base_url(executor_url: str, endpoint: _ExecutorPublishedEndpoint) -> str:
    parsed = urlsplit(executor_url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ContainerStartFailedError("executor published endpoint mismatch") from exc
    if parsed.scheme != "http" or not port:
        raise ContainerStartFailedError("executor published endpoint mismatch")
    return f"http://{endpoint.bind_ip}:{port}"


def _container_environment(container: Any) -> dict[str, str]:
    raw_environment = getattr(container, "environment", None)
    if isinstance(raw_environment, dict):
        return {str(key): str(value) for key, value in raw_environment.items()}
    raw_environment = getattr(container, "attrs", {}).get("Config", {}).get("Env", [])
    if not isinstance(raw_environment, list):
        return {}
    environment: dict[str, str] = {}
    for item in raw_environment:
        if not isinstance(item, str) or "=" not in item:
            continue
        key, value = item.split("=", 1)
        if key:
            environment[key] = value
    return environment


def _container_executor_auth_token(container: Any) -> str:
    return _container_environment(container).get("AI_PLATFORM_EXECUTOR_AUTH_TOKEN", "")


class FakeContainerProvider:
    def __init__(self, executor_url: str = "http://fake-sandbox-executor.invalid") -> None:
        self._executor_url = executor_url
        self._leases: dict[str, ContainerLease] = {}

    async def create_or_reuse(
        self,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
    ) -> ContainerLease:
        container_id = f"exec-{request.run_id}"
        existing = self._leases.get(container_id)
        if existing is not None:
            return existing
        lease = _lease_from_request(
            "fake",
            request,
            workspace,
            executor_url=self._executor_url,
            executor_headers=_executor_auth_headers(f"fake-executor-token-{request.run_id}"),
        )
        self._leases[lease.container_id] = lease
        return lease

    async def validate_for_dispatch(
        self,
        lease: ContainerLease,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
    ) -> None:
        return None

    async def stage_workspace(
        self,
        lease: ContainerLease,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
    ) -> None:
        return None

    async def collect_workspace(
        self,
        lease: ContainerLease,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
    ) -> None:
        return None

    async def stop(self, lease: ContainerLease, *, reason: str) -> StopResult:
        removed = self._leases.pop(lease.container_id, None)
        if removed is None:
            return StopResult(container_id=lease.container_id, status="not_found", message=reason)
        return StopResult(container_id=lease.container_id, status="stopped", message=reason)

    async def list_runtime_containers(self, filters: dict[str, str]) -> list[ContainerStatus]:
        statuses = [_status_from_lease(lease, status="running") for lease in self._leases.values()]
        return [status for status in statuses if _matches_filters(status, filters)]

    async def cleanup_orphan_containers(self, filters: dict[str, str], *, reason: str) -> list[StopResult]:
        return []


@dataclass
class _DockerOwnedResourceScope:
    """One cleanup owner for the exact per-lease bridge and its runtime pair."""

    provider: "DockerContainerProvider"
    lease: ContainerLease
    primary: Any | None = None
    native: Any | None = None
    native_socket_owned: bool = False

    def abort(self) -> None:
        """Stop tracked owned containers, then detach and remove only the owned bridge."""
        self.provider._cleanup_runtime_pair_or_track(
            self.primary,
            self.native,
            self.lease,
            remove_native_socket=self.native_socket_owned,
        )


class DockerContainerProvider:
    def __init__(
        self,
        *,
        docker_client_factory: Callable[[], Any] | None = None,
        health_probe: Callable[..., bool] | None = None,
        identity_probe: Callable[..., dict[str, int]] | None = None,
        callback_reachability_probe: Callable[..., bool] | None = None,
        native_tool_probe: Callable[[Any], bool] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._leases: dict[str, ContainerLease] = {}
        self._docker_client_factory = docker_client_factory
        self._health_probe = health_probe or default_executor_health_probe
        self._identity_probe = identity_probe or default_executor_identity_probe
        self._callback_reachability_probe = callback_reachability_probe or default_governed_callback_reachability_probe
        self._native_tool_probe = native_tool_probe or _default_native_tool_probe
        self._monotonic = monotonic or time.monotonic
        self._client: Any | None = None

    def assert_available(self) -> None:
        if self._docker_client_factory is None and docker is None:
            raise DockerUnavailableError("Docker SDK for Python is not installed")

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        self.assert_available()
        if self._docker_client_factory is not None:
            self._client = self._docker_client_factory()
            return self._client
        self._client = docker.from_env()
        return self._client

    async def _wait_for_executor_url(
        self,
        container: Any,
        timeout_seconds: int,
        endpoint: _ExecutorPublishedEndpoint,
    ) -> str:
        deadline = time.monotonic() + max(timeout_seconds, 1)
        while time.monotonic() <= deadline:
            if hasattr(container, "reload"):
                container.reload()
            executor_url = _published_executor_url_from_container(container, endpoint)
            if getattr(container, "status", None) == "running" and executor_url:
                return executor_url
            await asyncio.sleep(0.25)
        raise ExecutorHealthTimeoutError()

    def _elapsed_ms(self, started_at: float) -> int:
        return readiness_evidence.bounded_elapsed_ms(started_at, self._monotonic())

    def _cleanup_container_or_track(self, container: Any, lease: ContainerLease) -> None:
        if _stop_and_remove_container(container):
            return
        self._leases[lease.container_id] = lease
        raise ContainerCleanupFailedError("container cleanup could not be confirmed")

    def _remove_owned_governed_network(self, lease: ContainerLease) -> bool:
        """Remove only an exact-owned bridge after its primary has been removed."""
        try:
            network = self._get_client().networks.get(_governed_docker_network_name(lease))
        except Exception as exc:
            return _is_not_found_error(exc)
        try:
            _docker_owned_governed_network(network, lease)
        except Exception:
            return False
        attrs = _docker_network_authoritative_attrs(network)
        members = attrs.get("Containers")
        if not isinstance(members, dict):
            return False
        member_ids = {str(member_id) for member_id in members}
        # Pre-attach admission failures leave an exact-owned bridge empty.  It
        # is safe to remove that resource without touching the API.  Once the
        # API is attached, require it to be the only remaining member before
        # disconnecting it; any other peer is preserved for explicit recovery.
        if not member_ids:
            remove = getattr(network, "remove", None)
            if not callable(remove):
                return False
            try:
                remove()
            except Exception:
                return False
            return True
        try:
            api_container, api_id = _docker_owned_api_callback_container(
                self._get_client(),
                _runtime_release_commit(get_settings()),
            )
        except Exception:
            return False
        if member_ids != {api_id}:
            return False
        disconnect = getattr(network, "disconnect", None)
        if not callable(disconnect):
            return False
        try:
            disconnect(api_container, force=True)
        except Exception:
            return False
        remove = getattr(network, "remove", None)
        if not callable(remove):
            return False
        try:
            remove()
        except Exception:
            return False
        return True

    @staticmethod
    def _native_tool_socket_host_path(workspace: WorkspaceLease | ContainerLease) -> Path:
        return _native_tool_socket_host_path(workspace)

    def _prepare_native_tool_socket(self, workspace: WorkspaceLease) -> Path:
        socket_path = self._native_tool_socket_host_path(workspace)
        if len(os.fsencode(str(socket_path))) > _UNIX_SOCKET_PATH_MAX_BYTES:
            raise ContainerStartFailedError("native tool socket path exceeds platform limit")
        socket_root = socket_path.parent.parent
        socket_dir = socket_path.parent
        socket_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if socket_root.is_symlink() or not socket_root.is_dir():
            raise ContainerStartFailedError("native tool socket root is invalid")
        try:
            socket_dir.mkdir(mode=0o700)
            socket_dir_created = True
        except FileExistsError:
            socket_dir_created = False
        if socket_dir.is_symlink() or not socket_dir.is_dir():
            raise ContainerStartFailedError("native tool socket directory is invalid")
        try:
            if socket_dir_created:
                _secure_native_tool_socket_directory(socket_dir)
            directory_stat = _workspace_owner_stat(str(socket_dir))
            if (
                (directory_stat.st_uid, directory_stat.st_gid) != (RUNTIME_UID, RUNTIME_GID)
                or stat.S_IMODE(directory_stat.st_mode) != 0o700
            ):
                raise ContainerStartFailedError("native tool socket directory ownership is invalid")
        except OSError as exc:
            raise ContainerStartFailedError("native tool socket directory cannot be secured") from exc
        unexpected_entries = [entry for entry in socket_dir.iterdir() if entry != socket_path]
        if unexpected_entries:
            raise ContainerStartFailedError("native tool socket directory is occupied")
        if socket_path.exists() or socket_path.is_symlink():
            try:
                node = socket_path.lstat()
            except OSError as exc:
                raise ContainerStartFailedError("native tool socket cannot be inspected") from exc
            if not stat.S_ISSOCK(node.st_mode):
                raise ContainerStartFailedError("native tool socket path is occupied")
            socket_path.unlink()
        return socket_path

    def _remove_native_tool_socket(self, workspace: WorkspaceLease | ContainerLease) -> bool:
        try:
            socket_path = self._native_tool_socket_host_path(workspace)
            if socket_path.exists() or socket_path.is_symlink():
                node = socket_path.lstat()
                if not stat.S_ISSOCK(node.st_mode):
                    return False
                socket_path.unlink()
            if socket_path.parent.exists() or socket_path.parent.is_symlink():
                if socket_path.parent.is_symlink() or not socket_path.parent.is_dir():
                    return False
                socket_path.parent.rmdir()
            return True
        except OSError:
            return False

    async def _probe_native_tool_before_deadline(self, container: Any, deadline: float) -> bool:
        """Await one sidecar control-plane probe without exceeding its admission deadline."""

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ExecutorHealthTimeoutError("native tool sandbox did not become ready")
        try:
            # wait_for bounds this admission await and lets the owner clean the
            # runtime pair. The Docker SDK call runs in a worker thread and is
            # not cooperatively cancellable, so it may finish after this await.
            return bool(
                await asyncio.wait_for(
                    asyncio.to_thread(self._native_tool_probe, container),
                    timeout=remaining,
                )
            )
        except TimeoutError:
            raise ExecutorHealthTimeoutError("native tool sandbox did not become ready") from None
        except asyncio.CancelledError:
            raise
        except Exception:
            return False

    async def _wait_for_native_tool_socket(self, container: Any, timeout_seconds: float) -> None:
        deadline = time.monotonic() + max(float(timeout_seconds), 0.001)
        while True:
            if await self._probe_native_tool_before_deadline(container, deadline):
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.1, remaining))
        raise ExecutorHealthTimeoutError("native tool sandbox did not become ready")

    def _owned_native_tool_container(self, lease: ContainerLease) -> Any | None:
        try:
            container = self._get_client().containers.get(_native_tool_container_name(lease.run_id))
        except Exception:
            return None
        labels = _container_labels(container)
        expected = {
            "ai-platform.owner": _NATIVE_TOOL_OWNER,
            "ai-platform.tenant_id": lease.tenant_id,
            "ai-platform.workspace_id": lease.workspace_id,
            "ai-platform.user_id": lease.user_id,
            "ai-platform.session_id": lease.session_id,
            "ai-platform.run_id": lease.run_id,
        }
        if any(str(labels.get(key) or "") != value for key, value in expected.items()):
            return None
        return container

    def _remove_owned_native_tool_container(self, lease: ContainerLease) -> bool:
        container = self._owned_native_tool_container(lease)
        return True if container is None else _stop_and_remove_container(container)

    def _cleanup_runtime_pair_or_track(
        self,
        container: Any | None,
        native_tool_container: Any | None,
        lease: ContainerLease,
        *,
        remove_native_socket: bool = True,
    ) -> None:
        primary_removed = container is None or _stop_and_remove_container(container)
        native_removed = native_tool_container is None or _stop_and_remove_container(native_tool_container)
        socket_removed = (
            self._remove_native_tool_socket(lease)
            if remove_native_socket
            and str(lease.labels.get("ai-platform.native_tool_required") or "") == "true"
            else True
        )
        network_removed = self._remove_owned_governed_network(lease) if primary_removed else False
        if primary_removed and native_removed and socket_removed and network_removed:
            return
        self._leases[lease.container_id] = lease
        raise ContainerCleanupFailedError("runtime container pair cleanup could not be confirmed")

    def _cleanup_runtime_pair_for_error(self, container: Any, native: Any, lease: ContainerLease, cause: BaseException, evidence: readiness_evidence.ExecutorReadinessEvidence | None = None) -> BaseException:
        try:
            self._cleanup_runtime_pair_or_track(container, native, lease)
        except ContainerCleanupFailedError as cleanup_exc:
            cleanup_exc.readiness_evidence = evidence
            raise cleanup_exc from cause
        return cause

    async def _native_tool_reuse_valid(
        self,
        lease: ContainerLease,
        timeout_seconds: float,
    ) -> bool:
        if str(lease.labels.get("ai-platform.native_tool_required") or "") != "true":
            return False
        try:
            executor = self._get_client().containers.get(lease.container_name)
        except Exception:
            return False
        tool = self._owned_native_tool_container(lease)
        if tool is not None and hasattr(tool, "reload"):
            try:
                tool.reload()
            except Exception:
                return False
        if tool is None or getattr(tool, "status", "running") not in {"created", "running"}:
            return False
        tool_labels = _container_labels(tool)
        if any(
            tool_labels.get(key) != value
            for key, value in lease.labels.items()
            if key.startswith("ai-platform.skill_mount.")
        ):
            return False
        executor_token = _container_environment(executor).get("AI_PLATFORM_NATIVE_TOOL_TOKEN", "")
        tool_token = _container_environment(tool).get("AI_PLATFORM_NATIVE_TOOL_TOKEN", "")
        if not executor_token or not hmac.compare_digest(executor_token, tool_token):
            return False
        deadline = time.monotonic() + max(float(timeout_seconds), 0.001)
        try:
            return await self._probe_native_tool_before_deadline(tool, deadline)
        except ExecutorHealthTimeoutError:
            return False

    def _discard_native_tool_reuse(self, lease: ContainerLease) -> None:
        native_removed = self._remove_owned_native_tool_container(lease)
        primary_removed = self._remove_owned_cached_container(lease)
        socket_removed = (
            self._remove_native_tool_socket(lease)
            if str(lease.labels.get("ai-platform.native_tool_required") or "") == "true"
            else True
        )
        self._leases.pop(lease.container_id, None)
        if native_removed and primary_removed and socket_removed:
            return
        self._leases[lease.container_id] = lease
        raise ContainerCleanupFailedError("runtime container pair cleanup could not be confirmed")

    async def _admit_native_tool_reuse(self, lease: ContainerLease, timeout_seconds: float) -> None:
        try:
            valid = await self._native_tool_reuse_valid(lease, timeout_seconds)
        except asyncio.CancelledError:
            self._discard_native_tool_reuse(lease)
            raise
        if valid:
            return
        self._discard_native_tool_reuse(lease)
        raise NativeToolAdmissionError() from None

    async def _start_native_tool_container(
        self,
        *,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
        token: str,
        timeout_seconds: float,
        skill_mount: _TrustedSkillMount | None = None,
    ) -> Any:
        client = self._get_client()
        container = None
        socket_path: Path | None = None
        socket_prepared = False
        try:
            trusted_skill_mount = skill_mount or _prepare_trusted_skill_mount(request, workspace)
            socket_path = self._prepare_native_tool_socket(workspace)
            socket_prepared = True
            existing_lease = _lease_from_request("docker", request, workspace, executor_url=_executor_url())
            if not self._remove_owned_native_tool_container(existing_lease):
                raise ContainerCleanupFailedError("native tool container cleanup could not be confirmed")
            container = client.containers.create(
                image=get_settings().sandbox_executor_image,
                name=_native_tool_container_name(request.run_id),
                detach=True,
                labels=_native_tool_labels(request, workspace, trusted_skill_mount),
                volumes={
                    workspace.workspace_host_path: {
                        "bind": workspace.workspace_container_path,
                        "mode": "rw",
                    },
                    **(
                        {
                            str(trusted_skill_mount.host_path): {
                                "bind": trusted_skill_mount.container_path,
                                "mode": "ro",
                            }
                        }
                        if trusted_skill_mount is not None
                        else {}
                    ),
                    str(socket_path.parent): {
                        "bind": f"{workspace.workspace_container_path.rstrip('/')}/.ai-platform",
                        "mode": "rw",
                    },
                },
                environment=_native_tool_environment(token),
                # The launcher establishes the UDS parent before Uvicorn binds
                # it. Lifespan hooks run too late to repair a missing parent.
                entrypoint=["python", "-m", "app.runtime.sandbox.native_tool_app"],
                command=[],
                network_mode="none",
                user=f"{RUNTIME_UID}:{RUNTIME_GID}",
                **_native_tool_security_kwargs(),
                **_docker_resource_kwargs(request.resource_limits),
            )
            container.start()
            await self._wait_for_native_tool_socket(container, timeout_seconds)
            return container
        except asyncio.CancelledError:
            container_removed = container is None or _stop_and_remove_container(container)
            socket_removed = not socket_prepared or self._remove_native_tool_socket(workspace)
            if not (container_removed and socket_removed):
                raise ContainerCleanupFailedError("native tool container cleanup could not be confirmed")
            raise
        except ContainerCleanupFailedError:
            raise
        except Exception as exc:
            normalized_exc = _normalize_docker_availability_error(exc)
            container_removed = container is None or _stop_and_remove_container(container)
            socket_removed = not socket_prepared or self._remove_native_tool_socket(workspace)
            if not (container_removed and socket_removed):
                raise ContainerCleanupFailedError("native tool container cleanup could not be confirmed") from exc
            if normalized_exc is not None:
                raise normalized_exc from exc
            if isinstance(exc, (ContainerStartFailedError, ExecutorHealthTimeoutError)):
                raise NativeToolAdmissionError() from None
            if isinstance(exc, SandboxRuntimeError):
                raise
            raise NativeToolAdmissionError() from None

    async def _reuse_existing_container(
        self,
        lease: ContainerLease,
        timeout_seconds: int,
        endpoint: _ExecutorPublishedEndpoint,
    ) -> ContainerLease | None:
        try:
            container = self._get_client().containers.get(lease.container_name)
        except Exception:
            return None
        status = _container_status_from_labels(container)
        if status is None:
            return None
        if not _status_matches_lease(status, lease):
            labels = status.detail.get("labels") if isinstance(status.detail, dict) else None
            stored_proof = labels.get(GOVERNED_EGRESS_PROOF_LABEL) if isinstance(labels, dict) else None
            if lease.provider != "docker" or not isinstance(stored_proof, str):
                return None
            candidate_labels = dict(lease.labels)
            candidate_labels[GOVERNED_EGRESS_PROOF_LABEL] = stored_proof
            candidate = lease.model_copy(update={"labels": candidate_labels})
            if not (
                _status_matches_lease(status, candidate)
                and self._cached_governed_egress_matches(
                    candidate,
                    lease.labels,
                    getattr(get_settings(), "sandbox_egress_proof_signing_key", ""),
                )
            ):
                return None
            lease = candidate
        if not _status_has_expected_executor_identity_labels(status):
            self._cleanup_container_or_track(container, lease)
            return None
        expected_user = f"{RUNTIME_UID}:{RUNTIME_GID}"
        if _container_config_user(container) != expected_user:
            self._cleanup_container_or_track(container, lease)
            return None
        executor_auth_token = _container_executor_auth_token(container)
        if not executor_auth_token:
            self._cleanup_container_or_track(container, lease)
            return None
        try:
            executor_url = await self._wait_for_executor_url(container, timeout_seconds, endpoint)
            executor_headers = _executor_auth_headers(
                executor_auth_token,
                connect_base_url=_executor_connect_base_url(executor_url, endpoint),
            )
            probe_url, probe_headers = prepare_executor_http_request(executor_url, executor_headers)
            healthy = await asyncio.to_thread(
                _call_executor_health_probe,
                self._health_probe,
                probe_url,
                timeout_seconds,
                probe_headers,
            )
            if not healthy:
                raise ExecutorHealthTimeoutError()
            identity = await asyncio.to_thread(
                self._identity_probe,
                probe_url,
                timeout_seconds,
                probe_headers,
            )
            _require_expected_executor_identity(identity)
            current_settings = get_settings()
            callback = _docker_governed_callback_target(current_settings)
            if not await asyncio.to_thread(
                self._callback_reachability_probe,
                container,
                callback.base_url,
                _runtime_release_commit(current_settings),
            ):
                egress_diagnostics.record_admission_failure(egress_diagnostics.AdmissionGate.CALLBACK_REACHABILITY)
                raise GovernedEgressAdmissionError()
        except asyncio.CancelledError as exc:
            try:
                self._cleanup_container_or_track(container, lease)
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
            raise
        except Exception as exc:
            try:
                self._cleanup_container_or_track(container, lease)
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
            return None
        return ContainerLease(
            container_id=lease.container_id,
            container_name=lease.container_name,
            provider="docker",
            executor_url=executor_url,
            executor_headers=executor_headers,
            tenant_id=lease.tenant_id,
            workspace_id=lease.workspace_id,
            user_id=lease.user_id,
            session_id=lease.session_id,
            run_id=lease.run_id,
            sandbox_mode=lease.sandbox_mode,
            browser_enabled=lease.browser_enabled,
            workspace_host_path=lease.workspace_host_path,
            workspace_container_path=lease.workspace_container_path,
            labels=lease.labels,
        )

    def _remove_owned_cached_container(self, lease: ContainerLease) -> bool:
        try:
            container = self._get_client().containers.get(lease.container_name)
        except Exception as exc:
            return _is_not_found_error(exc)
        status = _container_status_from_labels(container)
        if (
            status is not None
            and _status_matches_lease(status, lease)
            and self._is_exact_owned_remote_container(container, lease, require_lease_identity=True)
        ):
            return _stop_and_remove_container(container)
        return False

    def _cached_governed_egress_matches(
        self,
        lease: ContainerLease,
        expected_labels: dict[str, str],
        signing_key: object,
    ) -> bool:
        return _governed_egress_labels_match("docker", lease.labels, expected_labels, signing_key)

    def _cached_lease_for_run(self, run_id: str) -> ContainerLease | None:
        """Return the sole tracked Docker lease for a run, keyed by real container ID."""
        return next((lease for lease in self._leases.values() if lease.run_id == run_id), None)

    @staticmethod
    def _is_exact_owned_remote_container(
        container: Any,
        lease: ContainerLease,
        *,
        require_lease_identity: bool = False,
    ) -> bool:
        labels = _container_labels(container)
        if not all(
            labels.get(key) == value
            for key, value in {
                "ai-platform.owner": "sandbox-runtime",
                "ai-platform.tenant_id": lease.tenant_id,
                "ai-platform.workspace_id": lease.workspace_id,
                "ai-platform.user_id": lease.user_id,
                "ai-platform.session_id": lease.session_id,
                "ai-platform.run_id": lease.run_id,
            }.items()
        ):
            return False
        container_id = str(getattr(container, "id", "") or "").strip()
        attrs = getattr(container, "attrs", {})
        inspected_id = str(attrs.get("Id") or "").strip() if isinstance(attrs, dict) else ""
        return bool(container_id and container_id == inspected_id) and (
            not require_lease_identity or container_id == lease.container_id
        )

    async def _recover_remote_docker_lease(
        self,
        *,
        bootstrap_lease: ContainerLease,
        remembered_lease: ContainerLease | None,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
        settings: Any,
        admission: _DockerGovernedEgressAdmission,
        timeout_seconds: int,
        endpoint: _ExecutorPublishedEndpoint,
    ) -> ContainerLease | None:
        """Adopt only a readback-verified remote lease or clean it before reuse."""
        try:
            container = self._get_client().containers.get(bootstrap_lease.container_name)
        except Exception as exc:
            if _is_not_found_error(exc):
                return None
            raise ContainerStartFailedError("deterministic Docker lease inspection failed") from exc
        if not self._is_exact_owned_remote_container(
            container,
            remembered_lease or bootstrap_lease,
            require_lease_identity=remembered_lease is not None,
        ):
            if remembered_lease is None:
                raise ContainerStartFailedError("deterministic Docker container is occupied")
            self._leases.pop(remembered_lease.container_id, None)
            raise ContainerStartFailedError("deterministic Docker container identity mismatch")
        tracked_id = str(getattr(container, "id", "") or "").strip()
        candidate = (
            remembered_lease
            if remembered_lease is not None
            else bootstrap_lease.model_copy(update={"container_id": tracked_id or bootstrap_lease.container_id})
        )
        try:
            expected_labels = _seal_docker_governed_egress_after_readback(
                self._get_client(),
                settings,
                request,
                candidate,
                admission,
                container,
            )
            stored_labels = remembered_lease.labels if remembered_lease is not None else _container_labels(container)
            if not self._cached_governed_egress_matches(
                candidate.model_copy(update={"labels": stored_labels}),
                expected_labels,
                getattr(settings, "sandbox_egress_proof_signing_key", ""),
            ):
                raise GovernedEgressAdmissionError()
        except Exception as exc:
            if isinstance(exc, GovernedEgressAdmissionError):
                egress_diagnostics.record_admission_failure(egress_diagnostics.AdmissionGate.POST_CREATE_PROOF_SEAL)
            self._cleanup_runtime_pair_or_track(
                container,
                self._owned_native_tool_container(candidate),
                candidate,
            )
            self._leases.pop(candidate.container_id, None)
            return None
        candidate.labels.update(expected_labels)
        status = _container_status_from_labels(container)
        if status is None or not _status_matches_lease(status, candidate):
            self._cleanup_runtime_pair_or_track(
                container,
                self._owned_native_tool_container(candidate),
                candidate,
            )
            self._leases.pop(candidate.container_id, None)
            return None
        recovered = await self._reuse_existing_container(candidate, timeout_seconds, endpoint)
        if recovered is None:
            return None
        return recovered

    async def create_or_reuse(
        self,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
    ) -> ContainerLease:
        settings = get_settings()
        endpoint = _resolve_executor_published_endpoint(settings.sandbox_executor_published_host)
        client = self._get_client()
        try:
            client.ping()
        except Exception as exc:  # pragma: no cover - branch shape varies by docker SDK/runtime
            normalized_exc = _normalize_docker_availability_error(exc)
            if normalized_exc is not None:
                raise normalized_exc from exc
            raise DockerUnavailableError("Docker daemon is unavailable") from exc
        bootstrap_lease = _lease_from_request("docker", request, workspace, executor_url=_executor_url())
        existing = self._cached_lease_for_run(request.run_id)
        # This runs before staged Skill scrubbing, cached-lease acceptance, or
        # either executor/native-tool container can be created or reused.
        try:
            egress_admission = _admit_docker_governed_egress(
                client,
                settings,
                request,
                bootstrap_lease,
            )
        except GovernedEgressAdmissionError:
            egress_diagnostics.record_admission_failure(egress_diagnostics.AdmissionGate.PREFLIGHT_TOPOLOGY)
            cleanup_lease = existing or bootstrap_lease
            try:
                remote = client.containers.get(cleanup_lease.container_name)
            except Exception:
                remote = None
            remote_is_owned = remote is not None and self._is_exact_owned_remote_container(
                remote,
                cleanup_lease,
                require_lease_identity=existing is not None,
            )
            if remote is not None and existing is not None and not remote_is_owned:
                raise ContainerStartFailedError("deterministic Docker container identity mismatch") from None
            if remote_is_owned:
                observed_id = str(getattr(remote, "id", "") or "").strip()
                if observed_id:
                    cleanup_lease.container_id = observed_id
                self._cleanup_runtime_pair_or_track(
                    remote,
                    self._owned_native_tool_container(cleanup_lease),
                    cleanup_lease,
                )
                self._leases.pop(cleanup_lease.container_id, None)
            elif not self._remove_owned_governed_network(cleanup_lease):
                self._leases.setdefault(cleanup_lease.container_id, cleanup_lease)
                raise ContainerCleanupFailedError("governed network cleanup could not be confirmed") from None
            raise
        owned_resources = _DockerOwnedResourceScope(self, bootstrap_lease)
        try:
            skill_mount = _prepare_trusted_skill_mount(request, workspace)
            skill_mount_labels = _skill_mount_labels(skill_mount)
            native_tool_required = _native_tool_required(request)
            native_tool_admission_evidence = (
                _native_tool_admission_evidence(workspace) if native_tool_required else {}
            )
        except BaseException as exc:
            try:
                owned_resources.abort()
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
            raise
        workspace_user = _docker_workspace_user_value(workspace.workspace_host_path)
        bootstrap_lease.labels.update(egress_admission.lease_labels)
        bootstrap_lease.labels["ai-platform.native_tool_required"] = _env_bool(native_tool_required)
        bootstrap_lease.labels.update(native_tool_admission_evidence)
        bootstrap_lease.labels.update(skill_mount_labels)
        if existing is not None:
            existing_native_tool_required = (
                str(existing.labels.get("ai-platform.native_tool_required") or "") == "true"
            )
            if not _lease_matches_request_workspace(existing, request, workspace):
                native_removed = self._remove_owned_native_tool_container(existing)
                primary_removed = self._remove_owned_cached_container(existing)
                socket_removed = (
                    self._remove_native_tool_socket(existing)
                    if existing_native_tool_required
                    else True
                )
                self._leases.pop(existing.container_id, None)
                owned_resources.lease = existing
                try:
                    if native_removed and primary_removed and socket_removed:
                        owned_resources.abort()
                except ContainerCleanupFailedError:
                    self._leases[existing.container_id] = existing
                    raise
                if not (native_removed and primary_removed and socket_removed):
                    self._leases[existing.container_id] = existing
                    raise ContainerCleanupFailedError("runtime container pair cleanup could not be confirmed")
                raise ContainerStartFailedError("cached lease scope mismatch")
            if (
                existing_native_tool_required != native_tool_required
                or any(existing.labels.get(key) != value for key, value in skill_mount_labels.items())
            ):
                native_removed = self._remove_owned_native_tool_container(existing)
                primary_removed = self._remove_owned_cached_container(existing)
                socket_removed = (
                    self._remove_native_tool_socket(existing)
                    if existing_native_tool_required
                    else True
                )
                self._leases.pop(existing.container_id, None)
                owned_resources.lease = existing
                try:
                    if native_removed and primary_removed and socket_removed:
                        owned_resources.abort()
                except ContainerCleanupFailedError:
                    self._leases[existing.container_id] = existing
                    raise
                if not (native_removed and primary_removed and socket_removed):
                    self._leases[existing.container_id] = existing
                    raise ContainerCleanupFailedError("runtime container pair cleanup could not be confirmed")
                raise ContainerStartFailedError("cached lease runtime profile mismatch")
        if existing is not None:
            recovered_existing = await self._recover_remote_docker_lease(
                bootstrap_lease=bootstrap_lease,
                remembered_lease=existing,
                request=request,
                workspace=workspace,
                settings=settings,
                admission=egress_admission,
                timeout_seconds=settings.sandbox_container_start_timeout_seconds,
                endpoint=endpoint,
            )
            if recovered_existing is None:
                raise ContainerStartFailedError()
            elif native_tool_required:
                try:
                    await self._admit_native_tool_reuse(
                        recovered_existing,
                        settings.sandbox_container_start_timeout_seconds,
                    )
                except BaseException as exc:
                    owned_resources.lease = recovered_existing
                    try:
                        candidate = client.containers.get(recovered_existing.container_name)
                    except Exception:
                        candidate = None
                    if self._is_exact_owned_remote_container(candidate, recovered_existing, require_lease_identity=True):
                        owned_resources.primary = candidate
                    owned_resources.native = self._owned_native_tool_container(recovered_existing)
                    owned_resources.native_socket_owned = owned_resources.native is not None
                    try:
                        owned_resources.abort()
                    except ContainerCleanupFailedError as cleanup_exc:
                        raise cleanup_exc from exc
                    raise
            if recovered_existing is not None:
                self._leases[recovered_existing.container_id] = recovered_existing
                return recovered_existing

        recovered = await self._recover_remote_docker_lease(
            bootstrap_lease=bootstrap_lease,
            remembered_lease=None,
            request=request,
            workspace=workspace,
            settings=settings,
            admission=egress_admission,
            timeout_seconds=settings.sandbox_container_start_timeout_seconds,
            endpoint=endpoint,
        )
        if recovered is not None:
            if native_tool_required:
                try:
                    await self._admit_native_tool_reuse(
                        recovered,
                        settings.sandbox_container_start_timeout_seconds,
                    )
                except BaseException as exc:
                    owned_resources.lease = recovered
                    try:
                        candidate = client.containers.get(recovered.container_name)
                    except Exception:
                        candidate = None
                    if self._is_exact_owned_remote_container(candidate, recovered, require_lease_identity=True):
                        owned_resources.primary = candidate
                    owned_resources.native = self._owned_native_tool_container(recovered)
                    owned_resources.native_socket_owned = owned_resources.native is not None
                    try:
                        owned_resources.abort()
                    except ContainerCleanupFailedError as cleanup_exc:
                        raise cleanup_exc from exc
                    raise
        if recovered is not None:
            self._leases[recovered.container_id] = recovered
            return recovered
        # Recovery can remove an invalid remote lease and its per-lease
        # network. Re-run admission so cold creation never uses a stale
        # network object or topology witnessed before that cleanup.
        try:
            egress_admission = _admit_docker_governed_egress(
                client,
                settings,
                request,
                bootstrap_lease,
            )
        except GovernedEgressAdmissionError:
            egress_diagnostics.record_admission_failure(egress_diagnostics.AdmissionGate.PREFLIGHT_TOPOLOGY)
            if not self._remove_owned_governed_network(bootstrap_lease):
                self._leases.setdefault(bootstrap_lease.container_id, bootstrap_lease)
                raise ContainerCleanupFailedError("governed network cleanup could not be confirmed") from None
            raise
        cold_start_started_at = self._monotonic()
        executor_auth_token = _generate_executor_auth_token()
        native_tool_token = _generate_executor_auth_token() if native_tool_required else ""
        bootstrap_lease.executor_headers = _executor_auth_headers(executor_auth_token)
        native_tool_container = None
        container = None
        try:
            if native_tool_required:
                native_tool_container = await self._start_native_tool_container(
                    request=request,
                    workspace=workspace,
                    token=native_tool_token,
                    timeout_seconds=settings.sandbox_container_start_timeout_seconds,
                    skill_mount=skill_mount,
                )
                owned_resources.native = native_tool_container
                owned_resources.native_socket_owned = True
            container = client.containers.create(
                image=settings.sandbox_executor_image,
                name=bootstrap_lease.container_name,
                detach=True,
                labels={**bootstrap_lease.platform_labels(), **_executor_identity_labels()},
                volumes={
                    workspace.workspace_host_path: {
                        "bind": workspace.workspace_container_path,
                        "mode": "rw",
                    },
                    **(
                        {
                            str(skill_mount.host_path): {
                                "bind": skill_mount.container_path,
                                "mode": "ro",
                            }
                        }
                        if skill_mount is not None
                        else {}
                    ),
                    **(
                        {
                            str(self._native_tool_socket_host_path(workspace).parent): {
                                "bind": f"{workspace.workspace_container_path.rstrip('/')}/.ai-platform",
                                "mode": "rw",
                            }
                        }
                        if native_tool_required
                        else {}
                    ),
                },
                environment=_executor_environment(
                    request,
                    settings,
                    executor_auth_token=executor_auth_token,
                    egress_bases=_docker_executor_egress_bases(
                        settings,
                        governed_docker_egress=True,
                    ),
                    workspace_container_path=workspace.workspace_container_path,
                    native_tool_token=native_tool_token,
                    native_tool_socket=_NATIVE_TOOL_SOCKET if native_tool_required else "",
                ),
                ports={"18000/tcp": (endpoint.bind_ip, None)},
                **egress_admission.create_kwargs,
                **_docker_security_kwargs(),
                user=workspace_user,
                **_docker_resource_kwargs(request.resource_limits),
            )
            owned_resources.primary = container
        except CallbackTargetValidationError as exc:
            self._cleanup_runtime_pair_for_error(container, native_tool_container, bootstrap_lease, exc)
            raise ContainerStartFailedError() from exc
        except ContainerCleanupFailedError:
            raise
        except NativeToolAdmissionError as exc:
            try:
                owned_resources.abort()
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
            raise
        except Exception as exc:
            normalized_exc = _normalize_docker_availability_error(exc)
            self._cleanup_runtime_pair_for_error(container, native_tool_container, bootstrap_lease, exc)
            if normalized_exc is not None:
                raise normalized_exc from exc
            if isinstance(exc, SandboxRuntimeError):
                raise
            raise ContainerStartFailedError() from exc
        if container is None:
            try:
                self._cleanup_runtime_pair_or_track(None, native_tool_container, bootstrap_lease)
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc
            raise ContainerStartFailedError()
        # Docker exposes authoritative network membership only after the idle
        # executor starts. Seal no proof before immediate post-start readback.
        observed_container_id = str(getattr(container, "id", "") or "").strip()
        if observed_container_id:
            bootstrap_lease.container_id = observed_container_id
        try:
            if hasattr(container, "start"):
                container.start()
        except Exception as exc:
            normalized_exc = _normalize_docker_availability_error(exc)
            self._cleanup_runtime_pair_for_error(container, native_tool_container, bootstrap_lease, exc)
            if isinstance(normalized_exc, DockerPermissionDeniedError):
                raise normalized_exc from exc
            raise ContainerStartFailedError() from exc
        try:
            bootstrap_lease.labels.update(
                _seal_docker_governed_egress_after_readback(
                    client,
                    settings,
                    request,
                    bootstrap_lease,
                    egress_admission,
                    container,
                )
            )
        except GovernedEgressAdmissionError as exc:
            egress_diagnostics.record_admission_failure(egress_diagnostics.AdmissionGate.POST_CREATE_PROOF_SEAL)
            self._cleanup_runtime_pair_for_error(container, native_tool_container, bootstrap_lease, exc)
            raise

        callback_reachable = await asyncio.to_thread(
            self._callback_reachability_probe,
            container,
            egress_admission.callback_base_url,
            egress_admission.runtime_commit,
        )
        if not callback_reachable:
            egress_diagnostics.record_admission_failure(egress_diagnostics.AdmissionGate.CALLBACK_REACHABILITY)
            try:
                self._cleanup_runtime_pair_or_track(container, native_tool_container, bootstrap_lease)
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc
            raise GovernedEgressAdmissionError()

        publish_wait_started_at = time.monotonic()
        try:
            executor_url = await self._wait_for_executor_url(
                container,
                settings.sandbox_container_start_timeout_seconds,
                endpoint,
            )
            bootstrap_lease.executor_url = executor_url
        except asyncio.CancelledError as exc:
            self._cleanup_runtime_pair_for_error(container, native_tool_container, bootstrap_lease, exc)
            raise
        except ExecutorHealthTimeoutError as exc:
            evidence = readiness_evidence.normalize_docker_readiness_evidence(
                "publish_wait", *_docker_readiness_snapshot(container, endpoint),
                "not_attempted", readiness_evidence.bounded_elapsed_ms(publish_wait_started_at, time.monotonic()),
            )
            self._cleanup_runtime_pair_for_error(container, native_tool_container, bootstrap_lease, exc, evidence)
            raise ExecutorHealthTimeoutError(readiness_evidence=evidence) from exc
        except Exception as exc:
            self._cleanup_runtime_pair_for_error(container, native_tool_container, bootstrap_lease, exc)
            if isinstance(exc, ContainerStartFailedError):
                raise
            raise ContainerStartFailedError() from exc
        sandbox_container_cold_start_latency_ms = self._elapsed_ms(cold_start_started_at)
        healthcheck_started_at = self._monotonic()
        executor_headers = _executor_auth_headers(
            executor_auth_token,
            connect_base_url=_executor_connect_base_url(executor_url, endpoint),
        )
        probe_url, probe_headers = prepare_executor_http_request(executor_url, executor_headers)
        try:
            healthy = await asyncio.to_thread(
                _call_executor_health_probe,
                self._health_probe,
                probe_url,
                settings.sandbox_executor_health_timeout_seconds,
                probe_headers,
            )
        except asyncio.CancelledError as exc:
            self._cleanup_runtime_pair_for_error(container, native_tool_container, bootstrap_lease, exc)
            raise
        except Exception as exc:
            evidence = readiness_evidence.normalize_docker_readiness_evidence(
                "health_probe", *_docker_readiness_snapshot(container),
                readiness_evidence.health_failure_outcome(exc), self._elapsed_ms(healthcheck_started_at),
            )
            self._cleanup_runtime_pair_for_error(container, native_tool_container, bootstrap_lease, exc, evidence)
            raise ExecutorHealthTimeoutError(readiness_evidence=evidence) from exc
        sandbox_healthcheck_latency_ms = self._elapsed_ms(healthcheck_started_at)
        if not healthy:
            evidence = readiness_evidence.normalize_docker_readiness_evidence(
                "health_probe", *_docker_readiness_snapshot(container), "unhealthy", sandbox_healthcheck_latency_ms,
            )
            raise self._cleanup_runtime_pair_for_error(container, native_tool_container, bootstrap_lease, ExecutorHealthTimeoutError(readiness_evidence=evidence), evidence)
        if _container_config_user(container) != workspace_user:
            self._cleanup_runtime_pair_or_track(container, native_tool_container, bootstrap_lease)
            raise ContainerStartFailedError("executor Config.User mismatch")
        try:
            identity = await asyncio.to_thread(
                self._identity_probe,
                probe_url,
                settings.sandbox_executor_health_timeout_seconds,
                probe_headers,
            )
            _require_expected_executor_identity(identity)
        except asyncio.CancelledError as exc:
            self._cleanup_runtime_pair_for_error(container, native_tool_container, bootstrap_lease, exc)
            raise
        except Exception as exc:
            self._cleanup_runtime_pair_for_error(container, native_tool_container, bootstrap_lease, exc)
            if isinstance(exc, ContainerStartFailedError):
                raise
            raise ContainerStartFailedError("executor identity unavailable") from exc

        lease = _lease_from_request(
            "docker",
            request,
            workspace,
            executor_url=executor_url,
            executor_headers=executor_headers,
            timings={
                "sandbox_container_start_latency_ms": sandbox_container_cold_start_latency_ms,
                "sandbox_container_cold_start_latency_ms": sandbox_container_cold_start_latency_ms,
                "sandbox_healthcheck_latency_ms": sandbox_healthcheck_latency_ms,
            },
        )
        lease.container_id = bootstrap_lease.container_id
        lease.labels.update(bootstrap_lease.labels)
        self._leases[lease.container_id] = lease
        return lease

    async def validate_for_dispatch(
        self,
        lease: ContainerLease,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
    ) -> None:
        """Revalidate a Docker lease immediately before any executor dispatch."""
        primary: Any | None = None
        try:
            settings = get_settings()
            if not _lease_matches_request_workspace(lease, request, workspace):
                raise GovernedEgressAdmissionError()
            expected_binding = {
                "tenant_id": request.tenant_id,
                "workspace_id": request.workspace_id,
                "user_id": request.user_id,
                "session_id": request.session_id,
                "run_id": request.run_id,
                "attempt_id": request.attempt_id,
                "image_subject": lease.labels.get("ai-platform.executor.requested_image", ""),
                "image_digest": lease.labels.get("ai-platform.executor.requested_image_digest", ""),
                "authorized_skill_scope": governed_egress_authorized_skill_scope(
                    skill_ids=request.skill_ids,
                    mcp_tool_ids=request.mcp_tool_ids,
                ),
                "authorized_native_tool_scope": governed_egress_authorized_native_tool_scope(
                    request.tool_policy_subjects
                ),
                "lease_identity": f"docker:{lease.container_name}:{lease.container_id}",
            }
            client = self._get_client()
            callback = _docker_governed_callback_target(settings)
            runtime_commit = _runtime_release_commit(settings)
            network_name = _governed_docker_network_name(lease)
            network = client.networks.get(network_name)
            network_id, verified_name = _docker_owned_governed_network(network, lease)
            if verified_name != network_name:
                raise GovernedEgressAdmissionError()
            primary, _api = _docker_complete_governed_network_members(
                client,
                network=network,
                lease=lease,
                network_id=network_id,
                network_name=network_name,
                callback_base_url=callback.base_url,
                expected_source=runtime_commit,
            )
            callback_subject = _docker_callback_endpoint_subject(
                client,
                network_name=network_name,
                network_id=network_id,
                callback_base_url=callback.base_url,
                expected_source=runtime_commit,
            )
            expected_binding.update(
                {
                    "runtime_subject": "docker-internal-bridge",
                    "policy_subject": f"{network_id}:{network_name}:internal",
                    "callback_subject": callback_subject,
                    "denial_subject": f"{network_id}:internal-default-deny",
                    "network_id": network_id,
                    "network_name": network_name,
                }
            )
            proof = governed_egress_proof_from_labels(
                "docker",
                lease.labels,
                signing_key=getattr(settings, "sandbox_egress_proof_signing_key", ""),
                signing_key_id=_governed_egress_proof_key_id(settings),
                expected_binding=expected_binding,
            )
            if proof is None:
                raise GovernedEgressAdmissionError()
            if not await asyncio.to_thread(
                self._callback_reachability_probe,
                primary,
                callback.base_url,
                runtime_commit,
            ):
                raise GovernedEgressAdmissionError()
        except Exception as exc:
            if primary is None:
                try:
                    candidate = self._get_client().containers.get(lease.container_name)
                except Exception:
                    candidate = None
                if self._is_exact_owned_remote_container(
                    candidate,
                    lease,
                    require_lease_identity=True,
                ):
                    primary = candidate
            self._cleanup_runtime_pair_or_track(
                primary,
                self._owned_native_tool_container(lease),
                lease,
            )
            self._leases.pop(lease.container_id, None)
            if isinstance(exc, GovernedEgressAdmissionError):
                raise
            raise GovernedEgressAdmissionError() from None

    async def stage_workspace(
        self,
        lease: ContainerLease,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
    ) -> None:
        """Docker already owns the verified local workspace bind."""

        return None

    async def collect_workspace(
        self,
        lease: ContainerLease,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
    ) -> None:
        """Docker writes directly to the controller-visible workspace bind."""

        return None

    async def stop(self, lease: ContainerLease, *, reason: str) -> StopResult:
        primary_status = "not_found"
        primary_failed = False
        try:
            container = self._get_client().containers.get(lease.container_name)
            status = _container_status_from_labels(container)
            if (
                status is None
                or not _status_matches_lease(status, lease)
                or not self._is_exact_owned_remote_container(container, lease, require_lease_identity=True)
            ):
                primary_status = "not_found"
            elif not _stop_and_remove_container(container):
                primary_failed = True
            else:
                primary_status = "stopped"
        except Exception as exc:
            primary_failed = not _is_not_found_error(exc)

        native_required = str(lease.labels.get("ai-platform.native_tool_required") or "") == "true"
        native_failed = False
        if native_required:
            native_failed = not self._remove_owned_native_tool_container(lease)
            if not self._remove_native_tool_socket(lease):
                native_failed = True

        network_failed = False
        if not primary_failed:
            network_failed = not self._remove_owned_governed_network(lease)
        if primary_failed or native_failed or network_failed:
            self._leases.setdefault(lease.container_id, lease)
            return StopResult(container_id=lease.container_id, status="failed", message="Container stop failed")
        self._leases.pop(lease.container_id, None)
        return StopResult(container_id=lease.container_id, status=primary_status, message=reason)

    async def list_runtime_containers(self, filters: dict[str, str]) -> list[ContainerStatus]:
        try:
            containers = self._get_client().containers.list(
                all=True,
                filters={"label": ["ai-platform.owner"]},
            )
        except Exception as exc:
            normalized_exc = _normalize_docker_availability_error(exc)
            if normalized_exc is not None:
                raise normalized_exc from exc
            raise
        statuses = []
        for container in containers:
            status = _container_status_from_labels(container)
            if status is not None:
                statuses.append(status)
        return [status for status in statuses if _matches_filters(status, filters)]

    async def cleanup_orphan_containers(self, filters: dict[str, str], *, reason: str) -> list[StopResult]:
        try:
            containers = self._get_client().containers.list(
                all=True,
                filters={"label": ["ai-platform.owner"]},
            )
        except Exception as exc:
            normalized_exc = _normalize_docker_availability_error(exc)
            if normalized_exc is not None:
                raise normalized_exc from exc
            raise
        owned: list[tuple[Any, ContainerStatus]] = []
        for container in containers:
            status = _container_status_from_labels(container)
            if status is None or not _matches_filters(status, filters):
                continue
            owned.append((container, status))
        live_primary_scopes = {
            _container_scope_key(status)
            for _container, status in owned
            if status.detail.get("labels", {}).get("ai-platform.owner") == "sandbox-runtime"
            and status.status in {"created", "running", "restarting"}
        }
        results: list[StopResult] = []
        for container, status in owned:
            labels = status.detail.get("labels")
            owner = labels.get("ai-platform.owner") if isinstance(labels, dict) else ""
            if owner == _NATIVE_TOOL_OWNER:
                if (
                    status.status in {"created", "running", "restarting"}
                    and _container_scope_key(status) in live_primary_scopes
                ):
                    continue
            elif status.status == "running":
                continue
            elif status.status not in {"exited", "dead", "removing", "removed"}:
                continue
            try:
                if hasattr(container, "remove"):
                    container.remove(force=True)
            except Exception:
                results.append(StopResult(container_id=status.container_id, status="failed", message="Container cleanup failed"))
                continue
            results.append(StopResult(container_id=status.container_id, status="stopped", message=reason))
        try:
            networks = self._get_client().networks.list()
        except Exception:
            return results
        for network in networks:
            lease = _lease_from_owned_governed_network(network)
            if lease is None or not _matches_filters(_status_from_lease(lease, status="removed"), filters):
                continue
            if self._remove_owned_governed_network(lease):
                results.append(
                    StopResult(
                        container_id=f"network:{_governed_docker_network_name(lease)}",
                        status="stopped",
                        message=reason,
                    )
                )
        return results


def _load_opensandbox_symbols() -> dict[str, Any]:
    try:
        from opensandbox import Sandbox, SandboxManager
        from opensandbox.config import ConnectionConfig
        from opensandbox.models.filesystem import DirectoryListEntry, WriteEntry
        from opensandbox.models.sandboxes import Host, NetworkPolicy, NetworkRule, SandboxFilter, Volume
    except ImportError as exc:  # pragma: no cover - exercised through lazy dependency failure
        raise OpenSandboxUnavailableError() from exc
    return {
        "sandbox_class": Sandbox,
        "sandbox_manager_class": SandboxManager,
        "connection_config_class": ConnectionConfig,
        "file_class": WriteEntry,
        "directory_entry_class": DirectoryListEntry,
        "host_class": Host,
        "volume_class": Volume,
        "network_policy_class": NetworkPolicy,
        "network_rule_class": NetworkRule,
        "sandbox_filter_class": SandboxFilter,
    }


def _opensandbox_cache_key(run_id: str, attempt_id: str) -> tuple[str, str]:
    """Return the exact in-process identity of one OpenSandbox execution attempt."""

    return run_id, attempt_id


def _opensandbox_cache_key_for_lease(lease: ContainerLease) -> tuple[str, str] | None:
    attempt_id = lease.labels.get("ai-platform.attempt_id")
    if not isinstance(attempt_id, str) or not attempt_id:
        return None
    return _opensandbox_cache_key(lease.run_id, attempt_id)


class OpenSandboxContainerProvider:
    """ContainerProvider implementation backed by the OpenSandbox API/SDK."""

    def __init__(
        self,
        *,
        sandbox_class: Any | None = None,
        sandbox_manager_class: Any | None = None,
        connection_config_class: Any | None = None,
        file_class: Any | None = None,
        directory_entry_class: Any | None = None,
        host_class: Any | None = None,
        volume_class: Any | None = None,
        network_policy_class: Any | None = None,
        network_rule_class: Any | None = None,
        sandbox_filter_class: Any | None = None,
        health_probe: Callable[..., bool] | None = None,
        identity_probe: Callable[..., dict[str, int]] | None = None,
        capability_profile_fetcher: CapabilityProfileFetcher | None = None,
        authoritative_attestation_probe: Callable[[Any, SandboxRuntimeRequest, str, Any], bool] | None = None,
        utcnow: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._sandbox_class = sandbox_class
        self._sandbox_manager_class = sandbox_manager_class
        self._connection_config_class = connection_config_class
        self._file_class = file_class
        self._directory_entry_class = directory_entry_class
        self._host_class = host_class
        self._volume_class = volume_class
        self._network_policy_class = network_policy_class
        self._network_rule_class = network_rule_class
        self._sandbox_filter_class = sandbox_filter_class
        self._health_probe = health_probe or default_executor_health_probe
        self._identity_probe = identity_probe or default_executor_identity_probe
        self._capability_profile_fetcher = capability_profile_fetcher or _default_opensandbox_capability_profile_fetcher
        self._authoritative_attestation_probe = authoritative_attestation_probe
        self._utcnow = utcnow or _utcnow
        self._monotonic = monotonic or time.monotonic
        self._sandboxes: dict[str, Any] = {}
        self._leases: dict[tuple[str, str], ContainerLease] = {}

    def _ensure_symbols(self) -> None:
        if self._sandbox_class is not None:
            return
        symbols = _load_opensandbox_symbols()
        self._sandbox_class = symbols["sandbox_class"]
        self._sandbox_manager_class = symbols["sandbox_manager_class"]
        self._connection_config_class = symbols["connection_config_class"]
        self._file_class = symbols["file_class"]
        self._directory_entry_class = symbols["directory_entry_class"]
        self._host_class = symbols["host_class"]
        self._volume_class = symbols["volume_class"]
        self._network_policy_class = symbols["network_policy_class"]
        self._network_rule_class = symbols["network_rule_class"]
        self._sandbox_filter_class = symbols["sandbox_filter_class"]

    def _connection_config(self, settings: Any) -> Any:
        self._ensure_symbols()
        return _opensandbox_connection_config(settings, self._connection_config_class)

    def _elapsed_ms(self, started_at: float) -> int:
        return max(int(round((self._monotonic() - started_at) * 1000)), 0)

    async def _connect(self, sandbox_id: str, connection_config: Any, *, skip_health_check: bool = False) -> Any:
        self._ensure_symbols()
        connect = getattr(self._sandbox_class, "connect", None)
        if connect is None:
            raise ContainerStartFailedError("OpenSandbox sandbox stop failed")
        return await _maybe_await(
            connect(
                sandbox_id,
                connection_config=connection_config,
                skip_health_check=skip_health_check,
            )
        )

    async def _manager(self, connection_config: Any) -> Any:
        self._ensure_symbols()
        create = getattr(self._sandbox_manager_class, "create", None)
        if create is None:
            raise OpenSandboxUnavailableError("OpenSandbox manager is unavailable")
        return await _maybe_await(create(connection_config=connection_config))

    async def _close_manager(self, manager: Any) -> None:
        close = getattr(manager, "close", None)
        if close is not None:
            await _maybe_await(close())

    @staticmethod
    def _pagination_value(pagination: Any, snake_name: str, camel_name: str) -> Any:
        if isinstance(pagination, dict):
            return pagination.get(snake_name, pagination.get(camel_name))
        return getattr(pagination, snake_name, None)

    async def _list_all_sandbox_infos(self, manager: Any, metadata_filter: dict[str, str]) -> list[Any]:
        """Exhaust the real SDK page contract with strict bounded progress guards."""

        if not (hasattr(manager, "list_sandbox_infos") and self._sandbox_filter_class is not None):
            infos = list(await _maybe_await(manager.list_sandboxes(metadata=metadata_filter)) or [])
            if len(infos) > 10000:
                raise ContainerStartFailedError("OpenSandbox inventory exceeded bounded pages")
            seen: set[str] = set()
            for info in infos:
                status = _opensandbox_status_from_info(info)
                if status is None or status.container_id in seen:
                    raise ContainerStartFailedError("OpenSandbox inventory pagination is ambiguous")
                seen.add(status.container_id)
            return infos
        collected: list[Any] = []
        seen: set[str] = set()
        for page in range(1, 101):
            paged = await _maybe_await(
                manager.list_sandbox_infos(
                    self._sandbox_filter_class(metadata=metadata_filter, page=page, page_size=100)
                )
            )
            infos = getattr(paged, "sandbox_infos", None)
            pagination = getattr(paged, "pagination", None)
            if isinstance(paged, dict):
                infos = paged.get("sandbox_infos")
                pagination = paged.get("pagination")
            actual_page = self._pagination_value(pagination, "page", "page")
            actual_size = self._pagination_value(pagination, "page_size", "pageSize")
            has_next = self._pagination_value(pagination, "has_next_page", "hasNextPage")
            if (
                not isinstance(infos, list)
                or type(actual_page) is not int
                or actual_page != page
                or type(actual_size) is not int
                or actual_size != 100
                or type(has_next) is not bool
            ):
                raise ContainerStartFailedError("OpenSandbox inventory pagination is ambiguous")
            for info in infos:
                status = _opensandbox_status_from_info(info)
                if status is None or status.container_id in seen:
                    raise ContainerStartFailedError("OpenSandbox inventory pagination is ambiguous")
                seen.add(status.container_id)
                collected.append(info)
            if not has_next:
                return collected
        raise ContainerStartFailedError("OpenSandbox inventory exceeded bounded pages")

    async def _write_and_verify_sentinel(
        self,
        sandbox: Any,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
    ) -> None:
        sentinel_path = _opensandbox_sentinel_path(workspace)
        payload = json.dumps(
            {
                "schema_version": "ai-platform.opensandbox-lease.v1",
                "tenant_id": request.tenant_id,
                "workspace_id": request.workspace_id,
                "user_id": request.user_id,
                "session_id": request.session_id,
                "run_id": request.run_id,
                "attempt_id": request.attempt_id,
            },
            sort_keys=True,
        )
        await _maybe_await(
            sandbox.files.write_files([self._file_class(path=sentinel_path, data=payload, mode=encode_execd_mode(0o600))])
        )
        readback = await _maybe_await(sandbox.files.read_file(sentinel_path))
        if isinstance(readback, bytes):
            readback_text = readback.decode("utf-8")
        else:
            readback_text = str(readback)
        if readback_text != payload:
            raise ContainerStartFailedError("OpenSandbox file verification failed")

    def _track_cleanup_pending_sandbox(
        self,
        sandbox: Any,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
        *,
        metadata: dict[str, str],
        executor_auth_token: str,
    ) -> dict[str, str] | None:
        sandbox_id = str(getattr(sandbox, "id", "") or "")
        if not sandbox_id:
            return identity_unavailable_cleanup_subject(request.run_id, request.attempt_id)
        lease = ContainerLease(
            container_id=sandbox_id,
            container_name=_opensandbox_container_name(request.run_id, request.attempt_id),
            provider="opensandbox",
            executor_url="",
            executor_headers=_executor_auth_headers(executor_auth_token),
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            user_id=request.user_id,
            session_id=request.session_id,
            run_id=request.run_id,
            sandbox_mode=request.sandbox_mode,
            browser_enabled=request.browser_enabled,
            workspace_host_path=workspace.workspace_host_path,
            workspace_container_path=workspace.workspace_container_path,
            labels=_provider_lease_labels(metadata),
        )
        self._sandboxes[sandbox_id] = sandbox
        self._leases[_opensandbox_cache_key(request.run_id, request.attempt_id)] = lease
        return None

    async def _cleanup_cached_lease_after_capability_rejection(self, request: SandboxRuntimeRequest) -> None:
        """Remove a tracked lease when its next admission profile has drifted or expired."""

        cache_key = _opensandbox_cache_key(request.run_id, request.attempt_id)
        cached = self._leases.get(cache_key)
        if cached is None:
            return
        sandbox = self._sandboxes.get(cached.container_id)
        if sandbox is not None and not await cleanup_started_sandbox(sandbox):
            raise ContainerCleanupFailedError("cached sandbox cleanup could not be confirmed")
        self._sandboxes.pop(cached.container_id, None)
        self._leases.pop(cache_key, None)

    async def _require_authoritative_governed_attestation(
        self,
        capability: OpenSandboxExternalEgressCapability,
        request: SandboxRuntimeRequest,
        sandbox_id: str,
        info: Any,
    ) -> None:
        """Require a provider-supplied post-create topology attestation before sealing proof."""
        probe = self._authoritative_attestation_probe
        if probe is None:
            raise OpenSandboxCapabilityAdmissionError(
                "OpenSandbox governed egress is unsupported without authoritative topology attestation"
            )
        try:
            attested = probe(capability, request, sandbox_id, info)
            if inspect.isawaitable(attested):
                attested = await attested
        except Exception:
            attested = False
        if attested is not True:
            raise OpenSandboxCapabilityAdmissionError(
                "OpenSandbox governed egress authoritative attestation failed"
            )

    async def create_or_reuse(
        self,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
    ) -> ContainerLease:
        settings = get_settings()
        cleanup_key = _opensandbox_cache_key(request.run_id, request.attempt_id)
        try:
            profile = _opensandbox_security_profile(settings)
        except OpenSandboxCapabilityAdmissionError:
            await self._cleanup_cached_lease_after_capability_rejection(request)
            raise
        capability: OpenSandboxExternalEgressCapability | None = None
        if profile.governed:
            if not has_governed_egress_signing_key(getattr(settings, "sandbox_egress_proof_signing_key", "")):
                raise OpenSandboxCapabilityAdmissionError("OpenSandbox governed-egress proof key is unavailable") from None
            if self._authoritative_attestation_probe is None:
                raise OpenSandboxCapabilityAdmissionError(
                    "OpenSandbox governed egress is unsupported without authoritative topology attestation"
                ) from None
        self._ensure_symbols()
        if profile.governed:
            try:
                capability = await _admit_opensandbox_external_egress_capability(
                    settings=settings,
                    fetcher=self._capability_profile_fetcher,
                    now=self._utcnow(),
                )
            except OpenSandboxCapabilityAdmissionError:
                await self._cleanup_cached_lease_after_capability_rejection(request)
                raise
        # Remote OpenSandbox has no controller filesystem identity.  Stage the
        # controlled Skill tree only after this ready sandbox has a DB lease.
        skill_mount = None
        metadata = _opensandbox_profile_labels(settings, request, profile, capability, skill_mount)
        try:
            provider_metadata = opensandbox_metadata.normalize_opensandbox_metadata(metadata)
        except opensandbox_metadata.OpenSandboxMetadataError as exc:
            raise ContainerStartFailedError("OpenSandbox metadata is invalid") from exc
        cache_key = cleanup_key
        cached = self._leases.get(cache_key)
        if cached is not None and cached.container_id in self._sandboxes:
            sandbox = self._sandboxes[cached.container_id]
            if not _lease_matches_request_workspace(cached, request, workspace):
                if not await cleanup_started_sandbox(sandbox):
                    raise ContainerCleanupFailedError("cached sandbox cleanup could not be confirmed")
                self._sandboxes.pop(cached.container_id, None)
                self._leases.pop(cache_key, None)
                raise ContainerStartFailedError("cached lease scope mismatch")
            try:
                info = await _maybe_await(sandbox.get_info())
                expected_lease = _lease_from_request(
                    "opensandbox",
                    request,
                    workspace,
                    executor_url=cached.executor_url,
                )
                expected_lease.labels.update(
                    provider_metadata
                )
                sealed_labels = _opensandbox_profile_labels(
                    settings,
                    request,
                    profile,
                    capability,
                    skill_mount,
                    lease_identity=f"opensandbox:{cached.container_name}:{cached.container_id}",
                    now=self._utcnow(),
                )
                remote_status = _opensandbox_status_from_info(info)
                if (
                    remote_status is None
                    or remote_status.container_id != cached.container_id
                    or (
                        not _status_matches_lease(remote_status, expected_lease)
                        and not (
                            profile.governed
                            and
                            isinstance(remote_status.detail.get("labels"), dict)
                            and _governed_egress_labels_match(
                                "opensandbox",
                                remote_status.detail["labels"],
                                expected_lease.labels,
                                getattr(settings, "sandbox_egress_proof_signing_key", ""),
                                signing_key_id=_governed_egress_proof_key_id(settings),
                                now=self._utcnow(),
                            )
                            and _status_matches_lease(
                                remote_status,
                                expected_lease.model_copy(
                                    update={
                                        "labels": {
                                            **expected_lease.labels,
                                            GOVERNED_EGRESS_PROOF_LABEL: remote_status.detail["labels"].get(
                                                GOVERNED_EGRESS_PROOF_LABEL, ""
                                            ),
                                        }
                                    }
                                ),
                            )
                        )
                    )
                ):
                    raise ContainerStartFailedError("cached sandbox metadata mismatch")
                if profile.governed:
                    if capability is None:
                        raise OpenSandboxCapabilityAdmissionError("OpenSandbox governed capability is unavailable")
                    await self._require_authoritative_governed_attestation(
                        capability,
                        request,
                        cached.container_id,
                        info,
                    )
                    labels_match = _governed_egress_labels_match(
                        "opensandbox",
                        cached.labels,
                        sealed_labels,
                        getattr(settings, "sandbox_egress_proof_signing_key", ""),
                        signing_key_id=_governed_egress_proof_key_id(settings),
                        now=self._utcnow(),
                    )
                else:
                    labels_match = cached.labels == _provider_lease_labels(sealed_labels)
                if not labels_match:
                    raise ContainerStartFailedError("cached sandbox metadata mismatch")
                executor_url, endpoint_headers = await resolve_executor_endpoint(sandbox, settings, error_factory=ContainerStartFailedError)
                cached_auth_token = str(cached.executor_headers.get(EXECUTOR_AUTH_HEADER) or "")
                if not cached_auth_token:
                    raise ContainerStartFailedError("executor identity credential unavailable")
                executor_headers = _executor_auth_headers(
                    cached_auth_token,
                    endpoint_headers,
                )
                healthy = await asyncio.to_thread(
                    _call_executor_health_probe,
                    self._health_probe,
                    executor_url,
                    int(getattr(settings, "sandbox_executor_health_timeout_seconds", 60) or 60),
                    executor_headers,
                )
                if not healthy:
                    raise ExecutorHealthTimeoutError()
                identity = await asyncio.to_thread(
                    self._identity_probe,
                    executor_url,
                    int(getattr(settings, "sandbox_executor_health_timeout_seconds", 60) or 60),
                    executor_headers,
                )
                _require_expected_executor_identity(identity)
                if capability is not None:
                    _ensure_capability_still_valid(capability, now=self._utcnow())
            except asyncio.CancelledError as exc:
                if not await cleanup_started_sandbox(sandbox):
                    raise ContainerCleanupFailedError("cached sandbox cleanup could not be confirmed") from exc
                self._sandboxes.pop(cached.container_id, None)
                self._leases.pop(cache_key, None)
                raise
            except OpenSandboxCapabilityAdmissionError as exc:
                if not await cleanup_started_sandbox(sandbox):
                    raise ContainerCleanupFailedError("cached sandbox cleanup could not be confirmed") from exc
                self._sandboxes.pop(cached.container_id, None)
                self._leases.pop(cache_key, None)
                raise
            except Exception as exc:
                if not await cleanup_started_sandbox(sandbox):
                    raise ContainerCleanupFailedError("cached sandbox cleanup could not be confirmed") from exc
                self._sandboxes.pop(cached.container_id, None)
                self._leases.pop(cache_key, None)
                if isinstance(exc, ContainerStartFailedError):
                    raise
                raise ContainerStartFailedError("executor identity unavailable") from exc
            cached.executor_url = executor_url
            cached.executor_headers = executor_headers
            cached.labels = _provider_lease_labels(sealed_labels)
            if capability is not None:
                try:
                    _ensure_capability_still_valid(capability, now=self._utcnow())
                except OpenSandboxCapabilityAdmissionError as exc:
                    if not await cleanup_started_sandbox(sandbox):
                        raise ContainerCleanupFailedError("cached sandbox cleanup could not be confirmed") from exc
                    self._sandboxes.pop(cached.container_id, None)
                    self._leases.pop(cache_key, None)
                    raise
            return cached

        reconciled_identity_unavailable_cleanup = await reconcile_authoritative_identity_unavailable_cleanup(
            self,
            request=request,
            workspace=workspace,
            settings=settings,
            metadata=provider_metadata,
            cache_key=cache_key,
            required=False,
            cleanup_subject=identity_unavailable_cleanup_subject(request.run_id, request.attempt_id),
            status_from_info=_opensandbox_status_from_info,
            sealed_metadata_for_id=lambda sandbox_id: _opensandbox_profile_labels(
                settings,
                request,
                profile,
                capability,
                skill_mount,
                lease_identity=f"opensandbox:{_opensandbox_container_name(request.run_id, request.attempt_id)}:{sandbox_id}",
                now=self._utcnow(),
            ),
            cleanup_error=lambda message, subject: ContainerCleanupFailedError(message, cleanup_subject=subject),
        )

        # A restarted provider has no durable executor credential to safely
        # re-authenticate a remote OpenSandbox process.  Detect an exact owned
        # remote first and fail closed rather than creating a same-scope peer.
        if not reconciled_identity_unavailable_cleanup:
            remote_statuses = await self._list_remote_statuses(
                {
                    "tenant_id": request.tenant_id,
                    "workspace_id": request.workspace_id,
                    "user_id": request.user_id,
                    "session_id": request.session_id,
                    "run_id": request.run_id,
                    "attempt_id": request.attempt_id,
                }
            )
            if remote_statuses:
                if len(remote_statuses) != 1 or remote_statuses[0].status != "running":
                    raise ContainerStartFailedError("OpenSandbox remote lease recovery is unsafe")
                raise ContainerStartFailedError("OpenSandbox remote lease requires its existing credential")

        started_at = self._monotonic()
        connection_config = self._connection_config(settings)
        executor_auth_token = _generate_executor_auth_token()

        async def cleanup_new(sandbox: Any | None, original_error: SandboxRuntimeError | None = None) -> None:
            return await cleanup_new_sandbox_or_reconcile(
                self,
                sandbox=sandbox,
                request=request,
                workspace=workspace,
                metadata=metadata,
                executor_auth_token=executor_auth_token,
                original_error=original_error,
                reconcile_identity=lambda subject: reconcile_authoritative_identity_unavailable_cleanup(
                    self,
                    request=request,
                    workspace=workspace,
                    settings=settings,
                    metadata=provider_metadata,
                    cache_key=cache_key,
                    required=True,
                    cleanup_subject=subject,
                    status_from_info=_opensandbox_status_from_info,
                    sealed_metadata_for_id=lambda sandbox_id: _opensandbox_profile_labels(
                        settings,
                        request,
                        profile,
                        capability,
                        skill_mount,
                        lease_identity=f"opensandbox:{_opensandbox_container_name(request.run_id, request.attempt_id)}:{sandbox_id}",
                        now=self._utcnow(),
                    ),
                    cleanup_error=lambda message, cleanup_subject: ContainerCleanupFailedError(
                        message,
                        cleanup_subject=cleanup_subject,
                    ),
                ),
                cleanup_error=lambda message, cleanup_subject, error: ContainerCleanupFailedError(
                    message,
                    readiness_evidence=(
                        error.readiness_evidence if isinstance(error, ExecutorHealthTimeoutError) else None
                    ),
                    cleanup_subject=cleanup_subject,
                ),
            )

        executor_egress_bases = (
            capability.executor_egress_bases()
            if capability is not None
            else profile.egress_bases
        )
        environment = _executor_environment(
            request,
            settings,
            executor_auth_token=executor_auth_token,
            egress_bases=executor_egress_bases,
            workspace_container_path=workspace.workspace_container_path,
        )
        kwargs = {
            "image": profile.requested_image if not profile.governed else _opensandbox_image(settings),
            "timeout": timedelta(seconds=max(int(getattr(settings, "opensandbox_timeout_seconds", 1800) or 1800), 1)),
            "ready_timeout": timedelta(
                seconds=max(int(getattr(settings, "sandbox_container_start_timeout_seconds", 30) or 30), 1)
            ),
            "env": environment,
            "metadata": provider_metadata,
            "resource": _opensandbox_resource_limits(request.resource_limits),
            "network_policy": (
                _opensandbox_network_policy(settings, self._network_policy_class, self._network_rule_class)
                if profile.governed
                else None
            ),
            "entrypoint": _opensandbox_entrypoint(settings),
            "volumes": _opensandbox_volumes(
                settings,
                workspace,
                skill_mount,
                host_class=self._host_class,
                volume_class=self._volume_class,
            ),
            "connection_config": connection_config,
        }
        sandbox: Any | None = None
        async def create_sandbox() -> Any:
            nonlocal sandbox
            sandbox = await _maybe_await(self._sandbox_class.create(**kwargs))
            if not str(getattr(sandbox, "id", "") or ""):
                raise ContainerStartFailedError("OpenSandbox sandbox start failed")
            return sandbox
        async def read_back_started_sandbox(started_sandbox: Any, executor_url: str) -> str:
            sandbox_id = str(getattr(started_sandbox, "id", "") or "")
            if not sandbox_id:
                raise ContainerStartFailedError("OpenSandbox sandbox start failed")
            info = await _maybe_await(started_sandbox.get_info())
            remote_status = _opensandbox_status_from_info(info)
            expected_unsealed = _lease_from_request(
                "opensandbox",
                request,
                workspace,
                executor_url=executor_url,
            )
            expected_unsealed.labels.update(provider_metadata)
            if (
                remote_status is None
                or remote_status.container_id != sandbox_id
                or not _status_matches_lease(remote_status, expected_unsealed)
            ):
                raise ContainerStartFailedError("OpenSandbox post-create metadata mismatch")
            if capability is not None:
                await self._require_authoritative_governed_attestation(
                    capability,
                    request,
                    sandbox_id,
                    info,
                )
            return sandbox_id

        async def check_executor_health(executor_url: str, endpoint_headers: dict[str, str]) -> int:
            health_started_at = self._monotonic()
            healthy = await asyncio.to_thread(
                _call_executor_health_probe,
                self._health_probe,
                executor_url,
                int(getattr(settings, "sandbox_executor_health_timeout_seconds", 60) or 60),
                (
                    endpoint_headers
                    if profile.governed
                    else _executor_auth_headers(executor_auth_token, endpoint_headers)
                ),
            )
            sandbox_healthcheck_latency_ms = self._elapsed_ms(health_started_at)
            if not healthy:
                raise ExecutorHealthTimeoutError(readiness_evidence=readiness_evidence.ExecutorReadinessEvidence(**unhealthy_readiness_fields(sandbox_healthcheck_latency_ms)))
            return sandbox_healthcheck_latency_ms

        async def verify_executor_identity(executor_url: str, endpoint_headers: dict[str, str]) -> None:
            identity = await asyncio.to_thread(
                self._identity_probe,
                executor_url,
                int(getattr(settings, "sandbox_executor_health_timeout_seconds", 60) or 60),
                _executor_auth_headers(executor_auth_token, endpoint_headers),
            )
            _require_expected_executor_identity(identity)
            if capability is not None:
                _ensure_capability_still_valid(capability, now=self._utcnow())

        try:
            startup_result = await launch_opensandbox_startup(
                OpenSandboxStartupOperations(
                    create=create_sandbox,
                    resolve_endpoint=lambda started_sandbox: resolve_executor_endpoint(
                        started_sandbox, settings, error_factory=ContainerStartFailedError
                    ),
                    readback=read_back_started_sandbox,
                    health=check_executor_health,
                    identity=verify_executor_identity,
                ),
                passthrough_error_types=(OpenSandboxCapabilityAdmissionError,),
                typed_error_types=(SandboxRuntimeError,),
                typed_error_evidence_attacher=lambda error, evidence: error.attach_opensandbox_startup_evidence(evidence)
                if isinstance(error, SandboxRuntimeError)
                else None,
            )
            sandbox = startup_result.sandbox
            sandbox_id = startup_result.sandbox_id
            executor_url = startup_result.executor_url
            executor_headers = startup_result.executor_headers
            sandbox_healthcheck_latency_ms = startup_result.healthcheck_latency_ms
        except asyncio.CancelledError as exc:
            try:
                await cleanup_new(sandbox)
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
            raise
        except OpenSandboxStartupFailure as exc:
            sandbox = exc.sandbox
            try:
                await cleanup_new(sandbox)
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
            message = str(exc.cause) if isinstance(exc.cause, SandboxRuntimeError) else "OpenSandbox sandbox start failed"
            raise OpenSandboxStartupFailedError(exc.evidence, message=message) from exc
        except SandboxRuntimeError as exc:
            try:
                await cleanup_new(sandbox, exc)
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
            raise
        except Exception as exc:
            try:
                await cleanup_new(sandbox)
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
            raise ContainerStartFailedError("OpenSandbox sandbox start failed") from exc

        lease = ContainerLease(
            container_id=sandbox_id,
            container_name=_opensandbox_container_name(request.run_id, request.attempt_id),
            provider="opensandbox",
            executor_url=executor_url,
            executor_headers=_executor_auth_headers(executor_auth_token, executor_headers),
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            user_id=request.user_id,
            session_id=request.session_id,
            run_id=request.run_id,
            sandbox_mode=request.sandbox_mode,
            browser_enabled=request.browser_enabled,
            workspace_host_path=workspace.workspace_host_path,
            workspace_container_path=workspace.workspace_container_path,
            labels=_provider_lease_labels(
                _opensandbox_profile_labels(
                    settings,
                    request,
                    profile,
                    capability,
                    skill_mount,
                    lease_identity=f"opensandbox:{_opensandbox_container_name(request.run_id, request.attempt_id)}:{sandbox_id}",
                    now=self._utcnow(),
                )
            ),
            timings={
                "sandbox_container_start_latency_ms": self._elapsed_ms(started_at),
                "sandbox_container_cold_start_latency_ms": self._elapsed_ms(started_at),
                "sandbox_healthcheck_latency_ms": sandbox_healthcheck_latency_ms,
            },
        )
        if capability is not None:
            try:
                _ensure_capability_still_valid(capability, now=self._utcnow())
            except OpenSandboxCapabilityAdmissionError as exc:
                try:
                    await cleanup_new(sandbox)
                except ContainerCleanupFailedError as cleanup_exc:
                    raise cleanup_exc from exc
                raise
        self._sandboxes[lease.container_id] = sandbox
        self._leases[cache_key] = lease
        return lease

    async def validate_for_dispatch(
        self,
        lease: ContainerLease,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
    ) -> None:
        """Fail closed unless the configured OpenSandbox profile remains valid."""
        settings = get_settings()
        if not _lease_matches_request_workspace(lease, request, workspace):
            raise OpenSandboxCapabilityAdmissionError("OpenSandbox dispatch scope mismatch")
        try:
            profile = _opensandbox_security_profile(settings)
            sandbox = self._sandboxes.get(lease.container_id)
            if sandbox is None:
                sandbox = await self._connect(
                    lease.container_id,
                    self._connection_config(settings),
                    skip_health_check=True,
                )
            info = await _maybe_await(sandbox.get_info())
            if profile.governed:
                if self._authoritative_attestation_probe is None:
                    raise OpenSandboxCapabilityAdmissionError(
                        "OpenSandbox governed egress is unsupported without authoritative topology attestation"
                    )
                capability = await _admit_opensandbox_external_egress_capability(
                    settings=settings,
                    fetcher=self._capability_profile_fetcher,
                    now=self._utcnow(),
                )
                await self._require_authoritative_governed_attestation(
                    capability,
                    request,
                    lease.container_id,
                    info,
                )
                _ensure_capability_still_valid(capability, now=self._utcnow())
                expected_binding = capability._governed_egress_binding(
                    request=request,
                    lease_identity=f"opensandbox:{lease.container_name}:{lease.container_id}",
                )
                if (
                    governed_egress_proof_from_labels(
                        "opensandbox",
                        lease.labels,
                        signing_key=getattr(settings, "sandbox_egress_proof_signing_key", ""),
                        signing_key_id=_governed_egress_proof_key_id(settings),
                        expected_binding=expected_binding,
                        now=self._utcnow(),
                    )
                    is None
                ):
                    raise OpenSandboxCapabilityAdmissionError("OpenSandbox dispatch proof is stale")
                return

            remote_status = _opensandbox_status_from_info(info)
            expected_labels = _trusted_internal_opensandbox_labels(
                request,
                profile,
                None,
            )
            expected_lease = lease.model_copy(update={"labels": _provider_lease_labels(expected_labels)})
            if (
                remote_status is None
                or remote_status.container_id != lease.container_id
                or remote_status.status != "running"
                or not _status_matches_lease(remote_status, expected_lease)
                or not opensandbox_metadata.opensandbox_metadata_matches(
                    remote_status.detail.get("labels", {}), _executor_identity_labels()
                )
                or lease.labels != _provider_lease_labels(expected_labels)
                or GOVERNED_EGRESS_PROOF_LABEL in lease.labels
                or any(
                    str(key).startswith(("ai-platform.external_egress.", "ai-platform.governed_egress."))
                    or str(key) == "ai-platform.runtime_subject"
                    for key in lease.labels
                )
            ):
                raise OpenSandboxCapabilityAdmissionError("trusted_internal OpenSandbox dispatch metadata is stale")
            executor_auth_token = str(lease.executor_headers.get(EXECUTOR_AUTH_HEADER) or "")
            if not executor_auth_token:
                raise OpenSandboxCapabilityAdmissionError("trusted_internal executor credential is unavailable")
            executor_url, endpoint_headers = await resolve_executor_endpoint(sandbox, settings, error_factory=ContainerStartFailedError)
            if executor_url != lease.executor_url:
                raise OpenSandboxCapabilityAdmissionError("trusted_internal executor endpoint is stale")
            executor_headers = _executor_auth_headers(executor_auth_token, endpoint_headers)
            healthy = await asyncio.to_thread(
                _call_executor_health_probe,
                self._health_probe,
                executor_url,
                int(getattr(settings, "sandbox_executor_health_timeout_seconds", 60) or 60),
                executor_headers,
            )
            if not healthy:
                raise ExecutorHealthTimeoutError()
            identity = await asyncio.to_thread(
                self._identity_probe,
                executor_url,
                int(getattr(settings, "sandbox_executor_health_timeout_seconds", 60) or 60),
                executor_headers,
            )
            _require_expected_executor_identity(identity)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            stop_result = await self.stop(lease, reason="dispatch_attestation_failed")
            if stop_result.status == "failed":
                raise ContainerCleanupFailedError("OpenSandbox dispatch cleanup could not be confirmed") from exc
            if isinstance(exc, SandboxRuntimeError):
                raise
            raise OpenSandboxCapabilityAdmissionError("OpenSandbox dispatch validation failed") from None

    async def _workspace_transfer_sandbox(
        self,
        lease: ContainerLease,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
    ) -> Any:
        if not _lease_matches_request_workspace(lease, request, workspace):
            raise ContainerStartFailedError("OpenSandbox workspace transfer scope mismatch")
        sandbox = self._sandboxes.get(lease.container_id)
        if sandbox is None:
            sandbox = await self._connect(
                lease.container_id,
                self._connection_config(get_settings()),
                skip_health_check=True,
            )
            self._sandboxes[lease.container_id] = sandbox
        return sandbox

    async def stage_workspace(
        self,
        lease: ContainerLease,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
    ) -> None:
        """Synchronize the bounded attempt workspace after remote sandbox readiness."""

        try:
            _require_secure_workspace_transfer()
            sandbox = await self._workspace_transfer_sandbox(lease, request, workspace)
            filesystem = getattr(sandbox, "files", None)
            if (
                filesystem is None
                or self._file_class is None
                or not hasattr(filesystem, "create_directories")
                or not hasattr(filesystem, "write_files")
                or not hasattr(filesystem, "read_file")
            ):
                raise ContainerStartFailedError("OpenSandbox filesystem staging is unavailable")
            directories, files = _build_opensandbox_workspace_manifest(request, workspace)
            remote_root = workspace.workspace_container_path.rstrip("/")
            remote_directories = [self._file_class(path=remote_root, data=None, mode=encode_execd_mode(0o700))] + [
                self._file_class(path=f"{remote_root}/{relative_path}", data=None, mode=encode_execd_mode(0o700))
                for relative_path in directories
            ]
            await _maybe_await(filesystem.create_directories(remote_directories))
            for entry in files:
                payload = _read_stable_workspace_file(entry)
                mode = encode_execd_mode(0o700 if entry.snapshot.mode & stat.S_IXUSR else 0o600)
                await _maybe_await(
                    filesystem.write_files(
                        [
                            self._file_class(
                                path=f"{remote_root}/{entry.relative_path}",
                                data=payload,
                                mode=mode,
                            )
                        ]
                    )
                )
            await self._write_and_verify_sentinel(sandbox, request, workspace)
        except asyncio.CancelledError:
            raise
        except SandboxRuntimeError:
            raise
        except Exception as exc:
            raise ContainerStartFailedError("OpenSandbox workspace staging failed") from exc

    @staticmethod
    def _filesystem_entry_value(entry: Any, name: str) -> Any:
        if isinstance(entry, dict):
            if name == "entry_type":
                return entry.get("entry_type", entry.get("type"))
            return entry.get(name)
        if name == "entry_type":
            return getattr(entry, "entry_type", getattr(entry, "type", None))
        return getattr(entry, name, None)

    def _remote_workspace_entry(
        self,
        entry: Any,
        workspace: WorkspaceLease,
    ) -> tuple[str, str, int]:
        raw_path = self._filesystem_entry_value(entry, "path")
        remote_root = workspace.workspace_container_path.rstrip("/")
        if not isinstance(raw_path, str) or "\x00" in raw_path or not raw_path.startswith(f"{remote_root}/"):
            raise ContainerStartFailedError("OpenSandbox workspace collection path is invalid")
        relative_path = _safe_workspace_relative_path(raw_path[len(remote_root) + 1 :])
        entry_type = str(self._filesystem_entry_value(entry, "entry_type") or "").lower()
        if entry_type not in {"file", "directory"}:
            raise ContainerStartFailedError("OpenSandbox workspace collection entry is invalid")
        try:
            size = int(self._filesystem_entry_value(entry, "size"))
        except (TypeError, ValueError) as exc:
            raise ContainerStartFailedError("OpenSandbox workspace collection entry is invalid") from exc
        if size < 0:
            raise ContainerStartFailedError("OpenSandbox workspace collection entry is invalid")
        return relative_path, entry_type, size

    async def _list_remote_workspace_directory(
        self,
        filesystem: Any,
        workspace: WorkspaceLease,
        relative_directory: str,
    ) -> list[tuple[str, str, int]]:
        if self._directory_entry_class is None or not hasattr(filesystem, "list_directory"):
            raise ContainerStartFailedError("OpenSandbox workspace collection is unavailable")
        remote_root = workspace.workspace_container_path.rstrip("/")
        path = remote_root if not relative_directory else f"{remote_root}/{relative_directory}"
        raw_entries = await _maybe_await(
            filesystem.list_directory(self._directory_entry_class(path=path, depth=1))
        )
        if not isinstance(raw_entries, list):
            raise ContainerStartFailedError("OpenSandbox workspace collection is invalid")
        if len(raw_entries) > _OPENSANDBOX_COLLECT_MAX_FILES + _OPENSANDBOX_COLLECT_MAX_DIRECTORIES:
            raise ContainerStartFailedError("workspace artifacts exceed the directory limit")
        entries = [self._remote_workspace_entry(entry, workspace) for entry in raw_entries]
        expected_parent = PurePosixPath(relative_directory)
        for relative_path, _entry_type, _size in entries:
            if PurePosixPath(relative_path).parent != expected_parent:
                raise ContainerStartFailedError("OpenSandbox workspace collection is invalid")
        return entries

    async def _remote_file_matches_listing(
        self,
        filesystem: Any,
        remote_path: str,
        expected_size: int,
    ) -> bool:
        if not hasattr(filesystem, "get_file_info"):
            return False
        details = await _maybe_await(filesystem.get_file_info([remote_path]))
        entry = details.get(remote_path) if isinstance(details, dict) else None
        if entry is None:
            return False
        entry_type = str(self._filesystem_entry_value(entry, "entry_type") or "").lower()
        try:
            size = int(self._filesystem_entry_value(entry, "size"))
        except (TypeError, ValueError):
            return False
        return entry_type == "file" and size == expected_size

    async def _stream_remote_workspace_file(
        self,
        filesystem: Any,
        remote_path: str,
        *,
        destination: Any | None = None,
    ) -> tuple[int, str]:
        total = 0
        digest = hashlib.sha256()
        stream = await _maybe_await(filesystem.read_bytes_stream(remote_path, chunk_size=64 * 1024))
        if not isinstance(stream, AsyncIterable):
            raise ContainerStartFailedError("OpenSandbox workspace collection is invalid")
        async for chunk in stream:
            if not isinstance(chunk, bytes):
                raise ContainerStartFailedError("OpenSandbox workspace collection is invalid")
            total += len(chunk)
            if total > _OPENSANDBOX_COLLECT_MAX_FILE_BYTES:
                raise ContainerStartFailedError("workspace artifacts exceed the per-file byte limit")
            digest.update(chunk)
            if destination is not None:
                destination.write(chunk)
        return total, digest.hexdigest()

    async def _download_remote_workspace_file(
        self,
        filesystem: Any,
        workspace: WorkspaceLease,
        relative_path: str,
        expected_size: int,
        *,
        destination_root: Path,
    ) -> None:
        if not hasattr(filesystem, "read_bytes_stream"):
            raise ContainerStartFailedError("OpenSandbox workspace collection is unavailable")
        if expected_size > _OPENSANDBOX_COLLECT_MAX_FILE_BYTES:
            raise ContainerStartFailedError("workspace artifacts exceed the per-file byte limit")
        remote_path = f"{workspace.workspace_container_path.rstrip('/')}/{relative_path}"
        root_snapshot = _assert_workspace_directory(destination_root)
        root_descriptor = _open_workspace_directory_fd(destination_root, root_snapshot)
        parent_descriptor: int | None = None
        temporary_name: str | None = None
        try:
            parent_descriptor, target_name = _open_workspace_relative_parent_fd(
                root_descriptor,
                relative_path,
                create=True,
            )
            temporary_name = f".ai-platform-download-{secrets.token_hex(16)}"
            try:
                temporary_descriptor = os.open(
                    temporary_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                    dir_fd=parent_descriptor,
                )
            except OSError as exc:
                raise ContainerStartFailedError("workspace collection staging is unavailable") from exc
            with os.fdopen(temporary_descriptor, "wb") as destination:
                total, digest = await self._stream_remote_workspace_file(
                    filesystem,
                    remote_path,
                    destination=destination,
                )
                destination.flush()
                os.fsync(destination.fileno())
            if total != expected_size or not await self._remote_file_matches_listing(
                filesystem,
                remote_path,
                expected_size,
            ):
                raise ContainerStartFailedError("OpenSandbox workspace output changed during download")
            verification_total, verification_digest = await self._stream_remote_workspace_file(filesystem, remote_path)
            if (
                verification_total != expected_size
                or verification_digest != digest
                or not await self._remote_file_matches_listing(filesystem, remote_path, expected_size)
            ):
                raise ContainerStartFailedError("OpenSandbox workspace output changed during download")
            os.replace(temporary_name, target_name, src_dir_fd=parent_descriptor, dst_dir_fd=parent_descriptor)
            temporary_name = None
        except SandboxRuntimeError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise ContainerStartFailedError("OpenSandbox workspace collection failed") from exc
        finally:
            if temporary_name is not None and parent_descriptor is not None:
                try:
                    os.unlink(temporary_name, dir_fd=parent_descriptor)
                except OSError:
                    pass
            if parent_descriptor is not None:
                os.close(parent_descriptor)
            os.close(root_descriptor)

    @staticmethod
    def _temporary_collection_root(workspace_root: Path) -> Path:
        root_snapshot = _assert_workspace_directory(workspace_root)
        root_descriptor = _open_workspace_directory_fd(workspace_root, root_snapshot)
        try:
            for _unused in range(3):
                name = f".ai-platform-collect-{secrets.token_hex(16)}"
                try:
                    os.mkdir(name, mode=0o700, dir_fd=root_descriptor)
                except FileExistsError:
                    continue
                except OSError as exc:
                    raise ContainerStartFailedError("workspace collection staging is unavailable") from exc
                return workspace_root / name
            raise ContainerStartFailedError("workspace collection staging is unavailable")
        finally:
            os.close(root_descriptor)

    @staticmethod
    def _remove_temporary_collection_root(staging_root: Path) -> None:
        if not staging_root.name.startswith(".ai-platform-collect-"):
            raise ContainerStartFailedError("workspace collection staging cleanup failed")
        parent_snapshot = _assert_workspace_directory(staging_root.parent)
        parent_descriptor = _open_workspace_directory_fd(staging_root.parent, parent_snapshot)
        try:
            try:
                descriptor = os.open(staging_root.name, _directory_open_flags(), dir_fd=parent_descriptor)
            except FileNotFoundError:
                return
            except OSError as exc:
                raise ContainerStartFailedError("workspace collection staging cleanup failed") from exc
            try:
                for _relative, directories, files, current_descriptor in os.fwalk(
                    ".",
                    topdown=False,
                    follow_symlinks=False,
                    dir_fd=descriptor,
                ):
                    for name in files:
                        os.unlink(name, dir_fd=current_descriptor)
                    for name in directories:
                        try:
                            os.rmdir(name, dir_fd=current_descriptor)
                        except NotADirectoryError:
                            os.unlink(name, dir_fd=current_descriptor)
                os.rmdir(staging_root.name, dir_fd=parent_descriptor)
            except OSError as exc:
                raise ContainerStartFailedError("workspace collection staging cleanup failed") from exc
            finally:
                os.close(descriptor)
        finally:
            os.close(parent_descriptor)

    def _publish_collected_workspace_files(
        self,
        staging_root: Path,
        workspace_root: Path,
        selected_files: list[tuple[str, int]],
    ) -> None:
        """Atomically publish a fully downloaded batch, restoring prior files on failure."""

        staging_snapshot = _assert_workspace_directory(staging_root)
        workspace_snapshot = _assert_workspace_directory(workspace_root)
        staging_descriptor = _open_workspace_directory_fd(staging_root, staging_snapshot)
        workspace_descriptor = _open_workspace_directory_fd(workspace_root, workspace_snapshot)
        previous: list[tuple[str, bool, bool]] = []
        try:
            for relative_path, _expected_size in sorted(selected_files):
                source_parent, source_name = _open_workspace_relative_parent_fd(
                    staging_descriptor,
                    relative_path,
                    create=False,
                )
                target_parent, target_name = _open_workspace_relative_parent_fd(
                    workspace_descriptor,
                    relative_path,
                    create=True,
                )
                try:
                    source_node = os.stat(source_name, dir_fd=source_parent, follow_symlinks=False)
                    if stat.S_ISLNK(source_node.st_mode) or not stat.S_ISREG(source_node.st_mode) or source_node.st_nlink != 1:
                        raise ContainerStartFailedError("workspace collection staging is invalid")
                    try:
                        target_node = os.stat(target_name, dir_fd=target_parent, follow_symlinks=False)
                    except FileNotFoundError:
                        has_backup = False
                    else:
                        if (
                            stat.S_ISLNK(target_node.st_mode)
                            or not stat.S_ISREG(target_node.st_mode)
                            or target_node.st_nlink != 1
                        ):
                            raise ContainerStartFailedError("workspace output destination is invalid")
                        backup_parent, backup_name = _open_workspace_relative_parent_fd(
                            staging_descriptor,
                            f".rollback/{relative_path}",
                            create=True,
                        )
                        try:
                            os.replace(
                                target_name,
                                backup_name,
                                src_dir_fd=target_parent,
                                dst_dir_fd=backup_parent,
                            )
                        finally:
                            os.close(backup_parent)
                        has_backup = True
                    previous.append((relative_path, has_backup, False))
                    os.replace(source_name, target_name, src_dir_fd=source_parent, dst_dir_fd=target_parent)
                    previous[-1] = (relative_path, has_backup, True)
                finally:
                    os.close(source_parent)
                    os.close(target_parent)
        except SandboxRuntimeError:
            self._rollback_collected_workspace_files(previous, staging_descriptor, workspace_descriptor)
            raise
        except OSError as exc:
            self._rollback_collected_workspace_files(previous, staging_descriptor, workspace_descriptor)
            raise ContainerStartFailedError("workspace output publication failed") from exc
        finally:
            os.close(workspace_descriptor)
            os.close(staging_descriptor)

    @staticmethod
    def _rollback_collected_workspace_files(
        previous: list[tuple[str, bool, bool]],
        staging_descriptor: int,
        workspace_descriptor: int,
    ) -> None:
        rollback_failed = False
        for relative_path, has_backup, published in reversed(previous):
            target_parent: int | None = None
            try:
                target_parent, target_name = _open_workspace_relative_parent_fd(
                    workspace_descriptor,
                    relative_path,
                    create=False,
                )
                if published:
                    os.unlink(target_name, dir_fd=target_parent)
                if has_backup:
                    backup_parent, backup_name = _open_workspace_relative_parent_fd(
                        staging_descriptor,
                        f".rollback/{relative_path}",
                        create=False,
                    )
                    try:
                        os.replace(backup_name, target_name, src_dir_fd=backup_parent, dst_dir_fd=target_parent)
                    finally:
                        os.close(backup_parent)
            except (OSError, SandboxRuntimeError):
                rollback_failed = True
            finally:
                if target_parent is not None:
                    os.close(target_parent)
        if rollback_failed:
            raise ContainerStartFailedError("workspace output rollback failed")

    async def collect_workspace(
        self,
        lease: ContainerLease,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
    ) -> None:
        """Publish only bounded legacy and delivery outputs from remote OpenSandbox."""

        staging_root: Path | None = None
        try:
            _require_secure_workspace_transfer()
            sandbox = await self._workspace_transfer_sandbox(lease, request, workspace)
            filesystem = getattr(sandbox, "files", None)
            if filesystem is None:
                raise ContainerStartFailedError("OpenSandbox workspace collection is unavailable")
            root_entries = await self._list_remote_workspace_directory(filesystem, workspace, "")
            pending: list[tuple[str, bool, bool]] = []
            seen_directories: set[str] = set()
            for relative_path, entry_type, _size in root_entries:
                if relative_path not in {"output", "outputs"}:
                    continue
                if entry_type != "directory":
                    raise ContainerStartFailedError("OpenSandbox workspace collection is invalid")
                pending.append((relative_path, relative_path == "output", False))
                seen_directories.add(relative_path)

            selected_files: list[tuple[str, int]] = []
            selected_file_paths: set[str] = set()
            while pending:
                relative_directory, legacy_output, delivery_output = pending.pop()
                if len(seen_directories) > _OPENSANDBOX_COLLECT_MAX_DIRECTORIES:
                    raise ContainerStartFailedError("workspace artifacts exceed the directory limit")
                for relative_path, entry_type, size in await self._list_remote_workspace_directory(
                    filesystem,
                    workspace,
                    relative_directory,
                ):
                    name = PurePosixPath(relative_path).name
                    if entry_type == "directory":
                        if relative_path in seen_directories:
                            raise ContainerStartFailedError("OpenSandbox workspace collection is invalid")
                        if len(seen_directories) >= _OPENSANDBOX_COLLECT_MAX_DIRECTORIES:
                            raise ContainerStartFailedError("workspace artifacts exceed the directory limit")
                        seen_directories.add(relative_path)
                        pending.append(
                            (
                                relative_path,
                                legacy_output,
                                delivery_output or (not legacy_output and name == "delivery"),
                            )
                        )
                        continue
                    if legacy_output or delivery_output:
                        if size > _OPENSANDBOX_COLLECT_MAX_FILE_BYTES:
                            raise ContainerStartFailedError("workspace artifacts exceed the per-file byte limit")
                        if (
                            relative_path in selected_file_paths
                            or len(selected_files) >= _OPENSANDBOX_COLLECT_MAX_FILES
                        ):
                            raise ContainerStartFailedError("workspace artifacts exceed the file count limit")
                        selected_file_paths.add(relative_path)
                        selected_files.append((relative_path, size))
            declared_total = sum(size for _relative_path, size in selected_files)
            if declared_total > _OPENSANDBOX_COLLECT_MAX_TOTAL_BYTES:
                raise ContainerStartFailedError("workspace artifacts exceed the total byte limit")
            workspace_root = Path(workspace.workspace_host_path)
            staging_root = self._temporary_collection_root(workspace_root)
            downloaded_total = 0
            for relative_path, expected_size in sorted(selected_files):
                await self._download_remote_workspace_file(
                    filesystem,
                    workspace,
                    relative_path,
                    expected_size,
                    destination_root=staging_root,
                )
                downloaded_total += expected_size
                if downloaded_total > _OPENSANDBOX_COLLECT_MAX_TOTAL_BYTES:
                    raise ContainerStartFailedError("workspace artifacts exceed the total byte limit")
            self._publish_collected_workspace_files(staging_root, workspace_root, selected_files)
        except asyncio.CancelledError:
            raise
        except SandboxRuntimeError:
            raise
        except Exception as exc:
            raise ContainerStartFailedError("OpenSandbox workspace collection failed") from exc
        finally:
            if staging_root is not None:
                self._remove_temporary_collection_root(staging_root)

    async def stop(self, lease: ContainerLease, *, reason: str) -> StopResult:
        settings = get_settings()
        cache_key = _opensandbox_cache_key_for_lease(lease)
        tracked_keys = [
            key
            for key, tracked in self._leases.items()
            if tracked.container_id == lease.container_id and tracked.run_id == lease.run_id
        ]
        if len(tracked_keys) == 1:
            if cache_key is not None and cache_key != tracked_keys[0]:
                return StopResult(
                    container_id=lease.container_id,
                    status="failed",
                    message="OpenSandbox sandbox stop failed",
                )
            cache_key = tracked_keys[0]
        elif tracked_keys or cache_key is None:
            return StopResult(
                container_id=lease.container_id,
                status="failed",
                message="OpenSandbox sandbox stop failed",
            )
        lease = self._leases.get(cache_key, lease)
        sandbox = self._sandboxes.get(lease.container_id)
        if sandbox is None:
            try:
                connection_config = self._connection_config(settings)
            except Exception:
                self._leases.setdefault(cache_key, lease)
                return StopResult(
                    container_id=lease.container_id,
                    status="failed",
                    message="OpenSandbox sandbox stop failed",
                )
            try:
                sandbox = await self._connect(
                    lease.container_id,
                    connection_config,
                    skip_health_check=True,
                )
            except Exception as exc:
                if is_authoritative_not_found_error(exc):
                    self._leases.pop(cache_key, None)
                    self._sandboxes.pop(lease.container_id, None)
                    return StopResult(container_id=lease.container_id, status="not_found", message=reason)
                self._leases.setdefault(cache_key, lease)
                return StopResult(
                    container_id=lease.container_id,
                    status="failed",
                    message="OpenSandbox sandbox stop failed",
                )
        try:
            if hasattr(sandbox, "get_info"):
                info = await _maybe_await(sandbox.get_info())
            else:
                info = sandbox
            status = _opensandbox_status_from_info(info)
            if (
                status is None
                or status.container_id != lease.container_id
                or status.provider != lease.provider
                or not _status_matches_lease(status, lease)
                or status.status not in _OPENSANDBOX_CONFIRMED_STOP_STATUSES
                or not opensandbox_metadata.opensandbox_metadata_matches(
                    status.detail.get("labels", {}), _executor_identity_labels()
                )
                or not _opensandbox_cleanup_identity_is_authorized(
                    status,
                    lease,
                    settings,
                    now=self._utcnow(),
                )
            ):
                self._leases.setdefault(cache_key, lease)
                self._sandboxes[lease.container_id] = sandbox
                return StopResult(
                    container_id=lease.container_id,
                    status="failed",
                    message="OpenSandbox sandbox stop failed",
                )
            try:
                cleanup_confirmed = await cleanup_started_sandbox(
                    sandbox,
                    propagate_authoritative_not_found=True,
                )
            except Exception as exc:
                if is_authoritative_not_found_error(exc):
                    self._leases.pop(cache_key, None)
                    self._sandboxes.pop(lease.container_id, None)
                    return StopResult(container_id=lease.container_id, status="not_found", message=reason)
                raise
            if not cleanup_confirmed:
                self._leases.setdefault(cache_key, lease)
                self._sandboxes[lease.container_id] = sandbox
                return StopResult(container_id=lease.container_id, status="failed", message="OpenSandbox sandbox stop failed")
        except Exception:
            self._leases.setdefault(cache_key, lease)
            self._sandboxes[lease.container_id] = sandbox
            return StopResult(container_id=lease.container_id, status="failed", message="OpenSandbox sandbox stop failed")
        self._leases.pop(cache_key, None)
        self._sandboxes.pop(lease.container_id, None)
        return StopResult(container_id=lease.container_id, status="stopped", message=reason)

    async def _list_remote_statuses(self, filters: dict[str, str]) -> list[ContainerStatus]:
        settings = get_settings()
        manager = await self._manager(self._connection_config(settings))
        try:
            raw_metadata_filter = {
                f"ai-platform.{key}": value
                for key, value in filters.items()
                if key in {"tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id", "sandbox_mode"}
            }
            raw_metadata_filter["ai-platform.owner"] = "sandbox-runtime"
            try:
                metadata_filter = opensandbox_metadata.normalize_opensandbox_metadata(raw_metadata_filter)
            except opensandbox_metadata.OpenSandboxMetadataError as exc:
                raise ContainerStartFailedError("OpenSandbox metadata is invalid") from exc
            infos = await self._list_all_sandbox_infos(manager, metadata_filter)
            statuses = [
                status
                for info in (infos or [])
                if (status := _opensandbox_status_from_info(info)) is not None
            ]
            return [
                status
                for status in statuses
                if opensandbox_metadata.opensandbox_metadata_matches_filters(status.detail.get("labels", {}), filters)
            ]
        finally:
            await self._close_manager(manager)

    async def list_runtime_containers(self, filters: dict[str, str]) -> list[ContainerStatus]:
        try:
            return await self._list_remote_statuses(filters)
        except OpenSandboxUnavailableError:
            raise
        except ContainerStartFailedError:
            raise
        except Exception as exc:
            raise ContainerStartFailedError("OpenSandbox inventory failed") from exc

    async def cleanup_orphan_containers(self, filters: dict[str, str], *, reason: str) -> list[StopResult]:
        metadata_filter = trusted_internal_orphan_cleanup_metadata_filter(filters)
        if metadata_filter is None:
            return []
        settings = get_settings()
        manager = await self._manager(self._connection_config(settings))
        try:
            infos = await self._list_all_sandbox_infos(manager, metadata_filter)
            results: list[StopResult] = []
            for info in infos or []:
                status = _opensandbox_status_from_info(info)
                if status is None or not trusted_internal_orphan_cleanup_identity_is_authorized(
                    status.detail.get("labels"),
                    filters,
                ):
                    continue
                if status.status == "running":
                    continue
                if status.status not in {"exited", "removed", "paused"}:
                    continue
                try:
                    await _maybe_await(manager.kill_sandbox(status.container_id))
                except Exception:
                    results.append(
                        StopResult(
                            container_id=status.container_id,
                            status="failed",
                            message="OpenSandbox cleanup failed",
                        )
                    )
                    continue
                results.append(StopResult(container_id=status.container_id, status="stopped", message=reason))
            return results
        finally:
            await self._close_manager(manager)


_PROVIDER_CACHE: dict[str, ContainerProvider] = {}


def reset_container_provider_cache() -> None:
    _PROVIDER_CACHE.clear()


def create_container_provider(provider_name: str | None = None) -> ContainerProvider:
    settings = get_settings()
    try:
        selected = require_provider_profile_compatibility(
            settings,
            provider_name or settings.sandbox_container_provider,
        )
    except OpenSandboxProfileConfigurationError as exc:
        raise OpenSandboxCapabilityAdmissionError(str(exc)) from None
    configured_profile = configured_security_profile(settings)
    cached = _PROVIDER_CACHE.get(selected)
    if cached is not None:
        return cached
    if selected == "fake":
        provider = FakeContainerProvider()
        _PROVIDER_CACHE[selected] = provider
        return provider
    if selected == "docker":
        provider = DockerContainerProvider()
        _PROVIDER_CACHE[selected] = provider
        return provider
    if selected == "opensandbox":
        provider = OpenSandboxContainerProvider(
            authoritative_attestation_probe=(
                None
                if configured_profile == SANDBOX_SECURITY_PROFILE_TRUSTED_INTERNAL
                else build_opensandbox_attestation_probe(settings)
            )
        )
        _PROVIDER_CACHE[selected] = provider
        return provider
    raise ValueError(f"Unknown sandbox container provider: {selected}")
