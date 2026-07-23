from __future__ import annotations

import asyncio
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
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from typing import Any, Callable, Protocol

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
    governed_egress_proof_label,
    governed_egress_proof_from_labels,
    has_governed_egress_signing_key,
)
from app.settings import get_settings
from app.runtime.sandbox.executor_client import (
    EXECUTOR_CONNECT_BASE_URL_METADATA,
    prepare_executor_http_request,
)
from app.runtime.sandbox.workspace_permissions import RUNTIME_GID, RUNTIME_UID
from app.skills.execution_profiles import NATIVE_COMMAND_ISOLATION


class SandboxRuntimeError(RuntimeError):
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


class NativeToolAdmissionError(SandboxRuntimeError):
    """Raised when the isolated native-command sidecar cannot become ready."""

    def __init__(self, message: str = "Native tool sandbox admission failed") -> None:
        super().__init__("native_tool_admission_failed", message)


class ContainerCleanupFailedError(SandboxRuntimeError):
    """Raised when a rejected executor cannot be confirmed stopped and removed."""

    def __init__(self, message: str = "Container cleanup failed") -> None:
        super().__init__("container_cleanup_failed", message)


class ExecutorHealthTimeoutError(SandboxRuntimeError):
    def __init__(self, message: str = "Executor health timeout") -> None:
        super().__init__("executor_health_timeout", message)


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

    async def list_runtime_containers(self, filters: dict[str, str]) -> list[ContainerStatus]: ...

    async def cleanup_orphan_containers(self, filters: dict[str, str], *, reason: str) -> list[StopResult]: ...


def _matches_filters(status: ContainerStatus, filters: dict[str, str]) -> bool:
    for key, expected in filters.items():
        actual = getattr(status, key, None)
        if actual is None:
            detail_value = status.detail.get(key)
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
        labels={"ai-platform.run_id": request.run_id},
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
        if not str(key).startswith("ai-platform.executor.") or str(key) in public_executor_labels
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


def _executor_environment(
    request: SandboxRuntimeRequest,
    settings: Any,
    *,
    executor_auth_token: str,
    workspace_container_path: str = "/workspace",
    native_tool_token: str = "",
    native_tool_socket: str = "",
    governed_docker_egress: bool = False,
) -> dict[str, str]:
    trusted_callback = _trusted_callback_target(settings, allow_host_gateway=not governed_docker_egress)
    environment = {
        "APP_MODULE": "app.runtime.sandbox.executor_app:create_executor_app",
        "APP_PORT": "18000",
        "AI_PLATFORM_SESSION_ID": request.session_id,
        "AI_PLATFORM_RUN_ID": request.run_id,
        "AI_PLATFORM_CALLBACK_BASE_URL": trusted_callback.base_url,
        "SANDBOX_CALLBACK_BASE_URL": trusted_callback.base_url,
        "AI_PLATFORM_EXECUTOR_AUTH_TOKEN": executor_auth_token,
        "OPENAI_BASE_URL": _env_value(settings, "openai_base_url"),
        "OPENAI_API_KEY": _env_value(settings, "openai_api_key"),
        "OPENAI_MODEL": _env_value(settings, "openai_model", "deepseek-v4-flash"),
        "ANTHROPIC_BASE_URL": _env_value(settings, "anthropic_base_url"),
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


def _opensandbox_requested_image(settings: Any) -> tuple[str, str]:
    """Return the immutable image request and its digest, never an observed runtime subject."""

    image = str(getattr(settings, "opensandbox_executor_image", "") or "")
    if not image:
        image = str(getattr(settings, "sandbox_executor_image", "") or "")
    subject, separator, digest = image.partition("@")
    last_path_segment = subject.rsplit("/", 1)[-1]
    if (
        not separator
        or not subject
        or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in subject)
        or "@" in digest
        or ":" in last_path_segment
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
    ):
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox executor image must be an immutable sha256 reference") from None
    configured_digest = str(getattr(settings, "opensandbox_executor_image_digest", "") or "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", configured_digest):
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox configured executor digest is invalid") from None
    if configured_digest != digest:
        raise OpenSandboxCapabilityAdmissionError("OpenSandbox configured executor digest does not match image reference") from None
    return image, digest


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
    expires_at: str
    issued_at_utc: datetime
    expires_at_utc: datetime

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
            runtime_subject=self.runtime_subject,
            policy_subject=self.gateway_policy_subject,
            callback_subject=self.callback_boundary_subject,
            denial_subject=f"{self.deny_audit_subject}:{self.deny_counter_subject}",
            network_id=self.profile_id,
            network_name=self.endpoint,
            # OpenSandbox has no Docker bridge.  Its authenticated runsc
            # capability supplies policy-bound enforcement instead.
            network_internal=False,
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            user_id=request.user_id,
            session_id=request.session_id,
            run_id=request.run_id,
            image_subject=self.requested_image,
            image_digest=self.requested_image_digest,
            authorized_skill_scope=governed_egress_authorized_skill_scope(
                skill_ids=request.skill_ids,
                mcp_tool_ids=request.mcp_tool_ids,
            ),
            authorized_native_tool_scope=governed_egress_authorized_native_tool_scope(request.tool_policy_subjects),
            lease_identity=lease_identity,
            key_id=key_id,
            issued_at=issued_at,
            expires_at=expires_at,
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


def _platform_metadata(request: SandboxRuntimeRequest) -> dict[str, str]:
    return {
        "ai-platform.owner": "sandbox-runtime",
        "ai-platform.tenant_id": request.tenant_id,
        "ai-platform.workspace_id": request.workspace_id,
        "ai-platform.user_id": request.user_id,
        "ai-platform.session_id": request.session_id,
        "ai-platform.run_id": request.run_id,
        "ai-platform.sandbox_mode": request.sandbox_mode,
        "ai-platform.browser_enabled": "true" if request.browser_enabled else "false",
    }


def _opensandbox_labels(
    settings: Any,
    request: SandboxRuntimeRequest,
    capability: OpenSandboxExternalEgressCapability,
    skill_mount: _TrustedSkillMount | None,
    *,
    lease_identity: str | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    labels = _platform_metadata(request)
    labels["ai-platform.provider_backend"] = "opensandbox"
    labels["ai-platform.executor.requested_image"] = capability.requested_image
    labels["ai-platform.executor.requested_image_digest"] = capability.requested_image_digest
    labels.update(_executor_identity_labels())
    labels.update(
        {
            "ai-platform.external_egress.profile_version": "v1",
            "ai-platform.external_egress.profile_id": capability.profile_id,
            "ai-platform.external_egress.runtime_identity": capability.runtime_identity,
            "ai-platform.runtime_subject": capability.runtime_subject,
            "ai-platform.external_egress.gateway_policy_subject": capability.gateway_policy_subject,
            "ai-platform.external_egress.callback_boundary_subject": capability.callback_boundary_subject,
            "ai-platform.external_egress.deny_audit_subject": capability.deny_audit_subject,
            "ai-platform.external_egress.deny_counter_subject": capability.deny_counter_subject,
            "ai-platform.external_egress.profile_requested_image": capability.requested_image,
            "ai-platform.external_egress.profile_requested_image_digest": capability.requested_image_digest,
            "ai-platform.external_egress.profile_expires_at": capability.expires_at,
        }
    )
    if lease_identity is not None:
        labels[GOVERNED_EGRESS_PROOF_LABEL] = governed_egress_proof_label(
            capability.governed_egress_proof(
                signing_key=getattr(settings, "sandbox_egress_proof_signing_key", ""),
                key_id=_governed_egress_proof_key_id(settings),
                request=request,
                lease_identity=lease_identity,
                now=now,
            )
        )
    labels.update(_skill_mount_labels(skill_mount))
    return labels


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
    if getattr(settings, "opensandbox_workspace_mount_enabled", True) is not True:
        if skill_mount is not None:
            raise ContainerStartFailedError("OpenSandbox staged Skill mount requires workspace mounting")
        return []
    try:
        volumes = [
            volume_class(
                name="ai-platform-workspace",
                host=host_class(path=workspace.workspace_host_path),
                mountPath=workspace.workspace_container_path,
                readOnly=False,
            )
        ]
        if skill_mount is not None:
            volumes.append(
                volume_class(
                    name="ai-platform-claude-skills",
                    host=host_class(path=str(skill_mount.host_path)),
                    mountPath=skill_mount.container_path,
                    readOnly=True,
                )
            )
    except (TypeError, ValueError) as exc:
        raise ContainerStartFailedError("OpenSandbox read-only staged Skill mount is unavailable") from exc
    return volumes


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


def _opensandbox_status_from_state(state: object) -> str:
    normalized = str(state or "unknown").strip().lower()
    if normalized in {"running", "ready"}:
        return "running"
    if normalized in {"pending", "creating", "starting"}:
        return "created"
    if normalized in {"terminated", "killed", "deleted"}:
        return "removed"
    if normalized in {"failed", "error"}:
        return "exited"
    if normalized in {"paused", "suspended"}:
        return "paused"
    return normalized or "unknown"


def _opensandbox_metadata_from_info(info: Any) -> dict[str, str]:
    metadata = getattr(info, "metadata", None)
    if metadata is None and isinstance(info, dict):
        metadata = info.get("metadata")
    return {str(key): str(value) for key, value in (metadata or {}).items()}


def _opensandbox_id(info: Any) -> str:
    value = getattr(info, "id", None)
    if value is None and isinstance(info, dict):
        value = info.get("id")
    return str(value or "")


def _opensandbox_state(info: Any) -> str:
    status = getattr(info, "status", None)
    if isinstance(info, dict):
        status = info.get("status")
    state = getattr(status, "state", None)
    if state is None and isinstance(status, dict):
        state = status.get("state")
    if state is None:
        state = getattr(info, "state", None)
    return str(state or "unknown")


def _opensandbox_status_from_info(info: Any) -> ContainerStatus | None:
    metadata = _opensandbox_metadata_from_info(info)
    if metadata.get("ai-platform.owner") != "sandbox-runtime":
        return None
    sandbox_mode = metadata.get("ai-platform.sandbox_mode")
    if sandbox_mode not in {"ephemeral", "persistent"}:
        sandbox_mode = None
    sandbox_id = _opensandbox_id(info)
    run_id = metadata.get("ai-platform.run_id")
    return ContainerStatus(
        container_id=sandbox_id,
        container_name=f"opensandbox-{run_id or sandbox_id}",
        provider="opensandbox",
        status=_opensandbox_status_from_state(_opensandbox_state(info)),
        tenant_id=metadata.get("ai-platform.tenant_id"),
        workspace_id=metadata.get("ai-platform.workspace_id"),
        user_id=metadata.get("ai-platform.user_id"),
        session_id=metadata.get("ai-platform.session_id"),
        run_id=run_id,
        sandbox_mode=sandbox_mode,
        browser_enabled=metadata.get("ai-platform.browser_enabled", "false").lower() == "true",
        executor_url=None,
        detail={"labels": metadata},
    )


def _opensandbox_matches_filters(metadata: dict[str, str], filters: dict[str, str]) -> bool:
    return _matches_filters(
        ContainerStatus(
            container_id="",
            container_name="",
            provider="opensandbox",
            status="unknown",
            tenant_id=metadata.get("ai-platform.tenant_id"),
            workspace_id=metadata.get("ai-platform.workspace_id"),
            user_id=metadata.get("ai-platform.user_id"),
            session_id=metadata.get("ai-platform.session_id"),
            run_id=metadata.get("ai-platform.run_id"),
            sandbox_mode=metadata.get("ai-platform.sandbox_mode") if metadata.get("ai-platform.sandbox_mode") in {"ephemeral", "persistent"} else None,
            browser_enabled=metadata.get("ai-platform.browser_enabled", "false").lower() == "true",
            detail={key.removeprefix("ai-platform."): value for key, value in metadata.items()},
        ),
        filters,
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
    script = (
        "import json,sys,urllib.request; "
        "url=sys.argv[1].rstrip('/')+'/api/ai/health'; "
        "body=json.load(urllib.request.urlopen(url,timeout=3)); "
        "raise SystemExit(0 if body=={'status':'ok','runtime_commit':sys.argv[2]} else 1)"
    )
    try:
        result = container.exec_run(
            ["python", "-c", script, callback_base_url, expected_runtime_commit],
            user=f"{RUNTIME_UID}:{RUNTIME_GID}",
        )
    except Exception:
        return False
    exit_code = getattr(result, "exit_code", None)
    if exit_code is None and isinstance(result, tuple) and result:
        exit_code = result[0]
    return exit_code == 0


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


def _endpoint_headers(endpoint: Any) -> dict[str, str]:
    headers = getattr(endpoint, "headers", None)
    if headers is None and isinstance(endpoint, dict):
        headers = endpoint.get("headers")
    if not isinstance(headers, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        if key is None or value is None:
            continue
        header_name = str(key).strip()
        header_value = str(value)
        if header_name:
            normalized[header_name] = header_value
    return normalized


def _opensandbox_executor_url(raw_url: str, settings: Any) -> str:
    url = raw_url.strip().rstrip("/")
    if not url:
        return ""
    if url.startswith("//"):
        protocol = str(getattr(settings, "opensandbox_protocol", "http") or "http").strip() or "http"
        return f"{protocol}:{url}"
    if "://" not in url:
        protocol = str(getattr(settings, "opensandbox_protocol", "http") or "http").strip() or "http"
        return f"{protocol}://{url.lstrip('/')}"
    return url


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
        return max(int(round((self._monotonic() - started_at) * 1000)), 0)

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
        except Exception as exc:
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
                    workspace_container_path=workspace.workspace_container_path,
                    native_tool_token=native_tool_token,
                    native_tool_socket=_NATIVE_TOOL_SOCKET if native_tool_required else "",
                    governed_docker_egress=True,
                ),
                ports={"18000/tcp": (endpoint.bind_ip, None)},
                **egress_admission.create_kwargs,
                **_docker_security_kwargs(),
                user=workspace_user,
                **_docker_resource_kwargs(request.resource_limits),
            )
            owned_resources.primary = container
        except CallbackTargetValidationError as exc:
            try:
                self._cleanup_runtime_pair_or_track(container, native_tool_container, bootstrap_lease)
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
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
            try:
                self._cleanup_runtime_pair_or_track(container, native_tool_container, bootstrap_lease)
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
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
        # Docker creation is the first point at which the actual container ID
        # and attachment topology exist. Seal no governed proof before this
        # authoritative readback succeeds.
        observed_container_id = str(getattr(container, "id", "") or "").strip()
        if observed_container_id:
            bootstrap_lease.container_id = observed_container_id
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
            try:
                self._cleanup_runtime_pair_or_track(container, native_tool_container, bootstrap_lease)
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
            raise
        try:
            if hasattr(container, "start"):
                container.start()
        except Exception as exc:
            normalized_exc = _normalize_docker_availability_error(exc)
            try:
                self._cleanup_runtime_pair_or_track(container, native_tool_container, bootstrap_lease)
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
            if isinstance(normalized_exc, DockerPermissionDeniedError):
                raise normalized_exc from exc
            raise ContainerStartFailedError() from exc

        callback_reachable = await asyncio.to_thread(
            self._callback_reachability_probe,
            container,
            egress_admission.callback_base_url,
            egress_admission.runtime_commit,
        )
        if not callback_reachable:
            try:
                self._cleanup_runtime_pair_or_track(container, native_tool_container, bootstrap_lease)
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc
            raise GovernedEgressAdmissionError()

        try:
            executor_url = await self._wait_for_executor_url(
                container,
                settings.sandbox_container_start_timeout_seconds,
                endpoint,
            )
            bootstrap_lease.executor_url = executor_url
        except asyncio.CancelledError as exc:
            try:
                self._cleanup_runtime_pair_or_track(container, native_tool_container, bootstrap_lease)
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
            raise
        except ExecutorHealthTimeoutError as exc:
            try:
                self._cleanup_runtime_pair_or_track(container, native_tool_container, bootstrap_lease)
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
            raise
        except Exception as exc:
            try:
                self._cleanup_runtime_pair_or_track(container, native_tool_container, bootstrap_lease)
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
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
            try:
                self._cleanup_runtime_pair_or_track(container, native_tool_container, bootstrap_lease)
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
            raise
        except Exception as exc:
            try:
                self._cleanup_runtime_pair_or_track(container, native_tool_container, bootstrap_lease)
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
            raise ExecutorHealthTimeoutError() from exc
        sandbox_healthcheck_latency_ms = self._elapsed_ms(healthcheck_started_at)
        if not healthy:
            self._cleanup_runtime_pair_or_track(container, native_tool_container, bootstrap_lease)
            raise ExecutorHealthTimeoutError()
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
            try:
                self._cleanup_runtime_pair_or_track(container, native_tool_container, bootstrap_lease)
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
            raise
        except Exception as exc:
            try:
                self._cleanup_runtime_pair_or_track(container, native_tool_container, bootstrap_lease)
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
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
        from opensandbox.models.filesystem import WriteEntry
        from opensandbox.models.sandboxes import Host, NetworkPolicy, NetworkRule, SandboxFilter, Volume
    except ImportError as exc:  # pragma: no cover - exercised through lazy dependency failure
        raise OpenSandboxUnavailableError() from exc
    return {
        "sandbox_class": Sandbox,
        "sandbox_manager_class": SandboxManager,
        "connection_config_class": ConnectionConfig,
        "file_class": WriteEntry,
        "host_class": Host,
        "volume_class": Volume,
        "network_policy_class": NetworkPolicy,
        "network_rule_class": NetworkRule,
        "sandbox_filter_class": SandboxFilter,
    }


class OpenSandboxContainerProvider:
    """ContainerProvider implementation backed by the OpenSandbox API/SDK."""

    def __init__(
        self,
        *,
        sandbox_class: Any | None = None,
        sandbox_manager_class: Any | None = None,
        connection_config_class: Any | None = None,
        file_class: Any | None = None,
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
        self._leases: dict[str, ContainerLease] = {}

    def _ensure_symbols(self) -> None:
        if self._sandbox_class is not None:
            return
        symbols = _load_opensandbox_symbols()
        self._sandbox_class = symbols["sandbox_class"]
        self._sandbox_manager_class = symbols["sandbox_manager_class"]
        self._connection_config_class = symbols["connection_config_class"]
        self._file_class = symbols["file_class"]
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

    async def _call_close(self, sandbox: Any) -> None:
        close = getattr(sandbox, "close", None)
        if close is not None:
            await _maybe_await(close())

    async def _call_kill(self, sandbox: Any) -> None:
        kill = getattr(sandbox, "kill", None)
        if kill is None:
            raise ContainerStartFailedError("OpenSandbox sandbox stop failed")
        await _maybe_await(kill())

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
            },
            sort_keys=True,
        )
        await _maybe_await(sandbox.files.write_files([self._file_class(path=sentinel_path, data=payload)]))
        readback = await _maybe_await(sandbox.files.read_file(sentinel_path))
        if isinstance(readback, bytes):
            readback_text = readback.decode("utf-8")
        else:
            readback_text = str(readback)
        if readback_text != payload:
            raise ContainerStartFailedError("OpenSandbox file verification failed")
        commands = getattr(sandbox, "commands", None)
        if commands is None or not hasattr(commands, "run"):
            raise ContainerStartFailedError("OpenSandbox command execution is unavailable")
        result = await _maybe_await(commands.run(f"test -f {shlex.quote(sentinel_path)}"))
        exit_code = getattr(result, "exit_code", None)
        if exit_code is not None and int(exit_code) != 0:
            raise ContainerStartFailedError("OpenSandbox command execution failed")

    async def _executor_endpoint(self, sandbox: Any, settings: Any) -> tuple[str, dict[str, str]]:
        endpoint = await _maybe_await(sandbox.get_endpoint(port=18000))
        headers = _endpoint_headers(endpoint)
        url = getattr(endpoint, "endpoint", None)
        if url is None and isinstance(endpoint, dict):
            url = endpoint.get("endpoint")
        if not isinstance(url, str) or not url.strip():
            raise ContainerStartFailedError("OpenSandbox executor endpoint unavailable")
        return _opensandbox_executor_url(url, settings), headers

    async def _cleanup_started_sandbox(self, sandbox: Any | None) -> bool:
        if sandbox is None:
            return True
        killed = False
        try:
            await self._call_kill(sandbox)
            killed = True
        except Exception:
            pass
        try:
            await self._call_close(sandbox)
        except Exception:
            pass
        return killed

    def _track_cleanup_pending_sandbox(
        self,
        sandbox: Any,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
        *,
        metadata: dict[str, str],
        executor_auth_token: str,
    ) -> None:
        sandbox_id = str(getattr(sandbox, "id", "") or "")
        if not sandbox_id:
            return
        lease = ContainerLease(
            container_id=sandbox_id,
            container_name=f"opensandbox-{request.run_id}",
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
        self._leases[f"opensandbox-{request.run_id}"] = lease

    async def _cleanup_new_sandbox_or_track(
        self,
        sandbox: Any | None,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
        *,
        metadata: dict[str, str],
        executor_auth_token: str,
    ) -> None:
        if await self._cleanup_started_sandbox(sandbox):
            return
        if sandbox is not None:
            self._track_cleanup_pending_sandbox(
                sandbox,
                request,
                workspace,
                metadata=metadata,
                executor_auth_token=executor_auth_token,
            )
        raise ContainerCleanupFailedError("sandbox cleanup could not be confirmed")

    async def _cleanup_cached_lease_after_capability_rejection(self, request: SandboxRuntimeRequest) -> None:
        """Remove a tracked lease when its next admission profile has drifted or expired."""

        cache_key = f"opensandbox-{request.run_id}"
        cached = self._leases.get(cache_key)
        if cached is None:
            return
        sandbox = self._sandboxes.get(cached.container_id)
        if sandbox is not None and not await self._cleanup_started_sandbox(sandbox):
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
        if not has_governed_egress_signing_key(getattr(settings, "sandbox_egress_proof_signing_key", "")):
            raise OpenSandboxCapabilityAdmissionError("OpenSandbox governed-egress proof key is unavailable") from None
        if self._authoritative_attestation_probe is None:
            raise OpenSandboxCapabilityAdmissionError(
                "OpenSandbox governed egress is unsupported without authoritative topology attestation"
            ) from None
        self._ensure_symbols()
        try:
            capability = await _admit_opensandbox_external_egress_capability(
                settings=settings,
                fetcher=self._capability_profile_fetcher,
                now=self._utcnow(),
            )
        except OpenSandboxCapabilityAdmissionError:
            await self._cleanup_cached_lease_after_capability_rejection(request)
            raise
        skill_mount = _prepare_trusted_skill_mount(request, workspace)
        cached = self._leases.get(f"opensandbox-{request.run_id}")
        if cached is not None and cached.container_id in self._sandboxes:
            sandbox = self._sandboxes[cached.container_id]
            if not _lease_matches_request_workspace(cached, request, workspace):
                if not await self._cleanup_started_sandbox(sandbox):
                    raise ContainerCleanupFailedError("cached sandbox cleanup could not be confirmed")
                self._sandboxes.pop(cached.container_id, None)
                self._leases.pop(f"opensandbox-{request.run_id}", None)
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
                    _opensandbox_labels(settings, request, capability, skill_mount)
                )
                sealed_labels = _opensandbox_labels(
                    settings,
                    request,
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
                await self._require_authoritative_governed_attestation(
                    capability,
                    request,
                    cached.container_id,
                    info,
                )
                if not _governed_egress_labels_match(
                    "opensandbox",
                    cached.labels,
                    sealed_labels,
                    getattr(settings, "sandbox_egress_proof_signing_key", ""),
                    signing_key_id=_governed_egress_proof_key_id(settings),
                    now=self._utcnow(),
                ):
                    raise ContainerStartFailedError("cached sandbox metadata mismatch")
                executor_url, endpoint_headers = await self._executor_endpoint(sandbox, settings)
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
                _ensure_capability_still_valid(capability, now=self._utcnow())
            except asyncio.CancelledError as exc:
                if not await self._cleanup_started_sandbox(sandbox):
                    raise ContainerCleanupFailedError("cached sandbox cleanup could not be confirmed") from exc
                self._sandboxes.pop(cached.container_id, None)
                self._leases.pop(f"opensandbox-{request.run_id}", None)
                raise
            except OpenSandboxCapabilityAdmissionError as exc:
                if not await self._cleanup_started_sandbox(sandbox):
                    raise ContainerCleanupFailedError("cached sandbox cleanup could not be confirmed") from exc
                self._sandboxes.pop(cached.container_id, None)
                self._leases.pop(f"opensandbox-{request.run_id}", None)
                raise
            except Exception as exc:
                if not await self._cleanup_started_sandbox(sandbox):
                    raise ContainerCleanupFailedError("cached sandbox cleanup could not be confirmed") from exc
                self._sandboxes.pop(cached.container_id, None)
                self._leases.pop(f"opensandbox-{request.run_id}", None)
                if isinstance(exc, ContainerStartFailedError):
                    raise
                raise ContainerStartFailedError("executor identity unavailable") from exc
            cached.executor_url = executor_url
            cached.executor_headers = executor_headers
            cached.labels = sealed_labels
            try:
                _ensure_capability_still_valid(capability, now=self._utcnow())
            except OpenSandboxCapabilityAdmissionError as exc:
                if not await self._cleanup_started_sandbox(sandbox):
                    raise ContainerCleanupFailedError("cached sandbox cleanup could not be confirmed") from exc
                self._sandboxes.pop(cached.container_id, None)
                self._leases.pop(f"opensandbox-{request.run_id}", None)
                raise
            return cached

        # A restarted provider has no durable executor credential to safely
        # re-authenticate a remote OpenSandbox process.  Detect an exact owned
        # remote first and fail closed rather than creating a same-scope peer.
        remote_statuses = await self._list_remote_statuses(
            {
                "tenant_id": request.tenant_id,
                "workspace_id": request.workspace_id,
                "user_id": request.user_id,
                "session_id": request.session_id,
                "run_id": request.run_id,
            }
        )
        if remote_statuses:
            if len(remote_statuses) != 1 or remote_statuses[0].status != "running":
                raise ContainerStartFailedError("OpenSandbox remote lease recovery is unsafe")
            raise ContainerStartFailedError("OpenSandbox remote lease requires its existing credential")

        started_at = self._monotonic()
        connection_config = self._connection_config(settings)
        metadata = _opensandbox_labels(settings, request, capability, skill_mount)
        executor_auth_token = _generate_executor_auth_token()
        environment = _executor_environment(
            request,
            settings,
            executor_auth_token=executor_auth_token,
            workspace_container_path=workspace.workspace_container_path,
        )
        kwargs = {
            "image": _opensandbox_image(settings),
            "timeout": timedelta(seconds=max(int(getattr(settings, "opensandbox_timeout_seconds", 1800) or 1800), 1)),
            "ready_timeout": timedelta(
                seconds=max(int(getattr(settings, "sandbox_container_start_timeout_seconds", 30) or 30), 1)
            ),
            "env": environment,
            "metadata": metadata,
            "resource": _opensandbox_resource_limits(request.resource_limits),
            "network_policy": _opensandbox_network_policy(settings, self._network_policy_class, self._network_rule_class),
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
        try:
            sandbox = await _maybe_await(self._sandbox_class.create(**kwargs))
            if getattr(settings, "opensandbox_startup_io_probe_enabled", True) is True:
                await self._write_and_verify_sentinel(sandbox, request, workspace)
            executor_url, executor_headers = await self._executor_endpoint(sandbox, settings)
            sandbox_id = str(getattr(sandbox, "id", "") or "")
            if not sandbox_id:
                raise ContainerStartFailedError("OpenSandbox sandbox start failed")
            info = await _maybe_await(sandbox.get_info())
            remote_status = _opensandbox_status_from_info(info)
            expected_unsealed = _lease_from_request(
                "opensandbox",
                request,
                workspace,
                executor_url=executor_url,
            )
            expected_unsealed.labels.update(metadata)
            if (
                remote_status is None
                or remote_status.container_id != sandbox_id
                or not _status_matches_lease(remote_status, expected_unsealed)
            ):
                raise ContainerStartFailedError("OpenSandbox post-create metadata mismatch")
            await self._require_authoritative_governed_attestation(
                capability,
                request,
                sandbox_id,
                info,
            )
            health_started_at = self._monotonic()
            healthy = await asyncio.to_thread(
                _call_executor_health_probe,
                self._health_probe,
                executor_url,
                int(getattr(settings, "sandbox_executor_health_timeout_seconds", 60) or 60),
                executor_headers,
            )
            sandbox_healthcheck_latency_ms = self._elapsed_ms(health_started_at)
            if not healthy:
                raise ExecutorHealthTimeoutError()
            identity = await asyncio.to_thread(
                self._identity_probe,
                executor_url,
                int(getattr(settings, "sandbox_executor_health_timeout_seconds", 60) or 60),
                _executor_auth_headers(executor_auth_token, executor_headers),
            )
            _require_expected_executor_identity(identity)
            _ensure_capability_still_valid(capability, now=self._utcnow())
        except asyncio.CancelledError as exc:
            try:
                await self._cleanup_new_sandbox_or_track(
                    sandbox,
                    request,
                    workspace,
                    metadata=metadata,
                    executor_auth_token=executor_auth_token,
                )
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
            raise
        except SandboxRuntimeError as exc:
            try:
                await self._cleanup_new_sandbox_or_track(
                    sandbox,
                    request,
                    workspace,
                    metadata=metadata,
                    executor_auth_token=executor_auth_token,
                )
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
            raise
        except Exception as exc:
            try:
                await self._cleanup_new_sandbox_or_track(
                    sandbox,
                    request,
                    workspace,
                    metadata=metadata,
                    executor_auth_token=executor_auth_token,
                )
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
            raise ContainerStartFailedError("OpenSandbox sandbox start failed") from exc

        lease = ContainerLease(
            container_id=sandbox_id,
            container_name=f"opensandbox-{request.run_id}",
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
                _opensandbox_labels(
                    settings,
                    request,
                    capability,
                    skill_mount,
                    lease_identity=f"opensandbox:opensandbox-{request.run_id}:{sandbox_id}",
                    now=self._utcnow(),
                )
            ),
            timings={
                "sandbox_container_start_latency_ms": self._elapsed_ms(started_at),
                "sandbox_container_cold_start_latency_ms": self._elapsed_ms(started_at),
                "sandbox_healthcheck_latency_ms": sandbox_healthcheck_latency_ms,
            },
        )
        try:
            _ensure_capability_still_valid(capability, now=self._utcnow())
        except OpenSandboxCapabilityAdmissionError as exc:
            try:
                await self._cleanup_new_sandbox_or_track(
                    sandbox,
                    request,
                    workspace,
                    metadata=metadata,
                    executor_auth_token=executor_auth_token,
                )
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
            raise
        self._sandboxes[lease.container_id] = sandbox
        self._leases[f"opensandbox-{request.run_id}"] = lease
        return lease

    async def validate_for_dispatch(
        self,
        lease: ContainerLease,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
    ) -> None:
        """Fail closed unless a current authoritative OpenSandbox attestation remains valid."""
        settings = get_settings()
        if not _lease_matches_request_workspace(lease, request, workspace):
            raise OpenSandboxCapabilityAdmissionError("OpenSandbox dispatch scope mismatch")
        if self._authoritative_attestation_probe is None:
            raise OpenSandboxCapabilityAdmissionError(
                "OpenSandbox governed egress is unsupported without authoritative topology attestation"
            )
        try:
            capability = await _admit_opensandbox_external_egress_capability(
                settings=settings,
                fetcher=self._capability_profile_fetcher,
                now=self._utcnow(),
            )
            sandbox = self._sandboxes.get(lease.container_id)
            if sandbox is None:
                sandbox = await self._connect(
                    lease.container_id,
                    self._connection_config(settings),
                    skip_health_check=True,
                )
            info = await _maybe_await(sandbox.get_info())
            await self._require_authoritative_governed_attestation(
                capability,
                request,
                lease.container_id,
                info,
            )
            _ensure_capability_still_valid(capability, now=self._utcnow())
            expected_binding = {
                "tenant_id": request.tenant_id,
                "workspace_id": request.workspace_id,
                "user_id": request.user_id,
                "session_id": request.session_id,
                "run_id": request.run_id,
                "image_subject": lease.labels.get("ai-platform.executor.requested_image", ""),
                "image_digest": lease.labels.get("ai-platform.executor.requested_image_digest", ""),
                "authorized_skill_scope": governed_egress_authorized_skill_scope(
                    skill_ids=request.skill_ids,
                    mcp_tool_ids=request.mcp_tool_ids,
                ),
                "authorized_native_tool_scope": governed_egress_authorized_native_tool_scope(
                    request.tool_policy_subjects
                ),
                "lease_identity": f"opensandbox:{lease.container_name}:{lease.container_id}",
            }
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
        except OpenSandboxCapabilityAdmissionError:
            stop_result = await self.stop(lease, reason="dispatch_attestation_failed")
            if stop_result.status == "failed":
                raise ContainerCleanupFailedError("OpenSandbox dispatch cleanup could not be confirmed") from None
            raise

    async def stop(self, lease: ContainerLease, *, reason: str) -> StopResult:
        settings = get_settings()
        sandbox = self._sandboxes.get(lease.container_id)
        try:
            if sandbox is None:
                sandbox = await self._connect(
                    lease.container_id,
                    self._connection_config(settings),
                    skip_health_check=True,
                )
            if hasattr(sandbox, "get_info"):
                info = await _maybe_await(sandbox.get_info())
            else:
                info = sandbox
            status = _opensandbox_status_from_info(info)
            if status is None or not _status_matches_lease(status, lease):
                self._leases.pop(f"opensandbox-{lease.run_id}", None)
                self._sandboxes.pop(lease.container_id, None)
                return StopResult(container_id=lease.container_id, status="not_found", message=reason)
            if not await self._cleanup_started_sandbox(sandbox):
                self._leases.setdefault(f"opensandbox-{lease.run_id}", lease)
                self._sandboxes[lease.container_id] = sandbox
                return StopResult(container_id=lease.container_id, status="failed", message="OpenSandbox sandbox stop failed")
        except Exception as exc:
            if _is_not_found_error(exc):
                self._leases.pop(f"opensandbox-{lease.run_id}", None)
                self._sandboxes.pop(lease.container_id, None)
                return StopResult(container_id=lease.container_id, status="not_found", message=reason)
            self._leases.setdefault(f"opensandbox-{lease.run_id}", lease)
            if sandbox is not None:
                self._sandboxes[lease.container_id] = sandbox
            return StopResult(container_id=lease.container_id, status="failed", message="OpenSandbox sandbox stop failed")
        self._leases.pop(f"opensandbox-{lease.run_id}", None)
        self._sandboxes.pop(lease.container_id, None)
        return StopResult(container_id=lease.container_id, status="stopped", message=reason)

    async def _list_remote_statuses(self, filters: dict[str, str]) -> list[ContainerStatus]:
        settings = get_settings()
        manager = await self._manager(self._connection_config(settings))
        try:
            metadata_filter = {
                f"ai-platform.{key}": value
                for key, value in filters.items()
                if key in {"tenant_id", "workspace_id", "user_id", "session_id", "run_id", "sandbox_mode"}
            }
            metadata_filter["ai-platform.owner"] = "sandbox-runtime"
            if hasattr(manager, "list_sandbox_infos") and self._sandbox_filter_class is not None:
                paged = await _maybe_await(
                    manager.list_sandbox_infos(
                        self._sandbox_filter_class(metadata=metadata_filter, page_size=100)
                    )
                )
                infos = getattr(paged, "sandbox_infos", None)
                if infos is None and isinstance(paged, dict):
                    infos = paged.get("sandbox_infos")
            else:
                infos = await _maybe_await(manager.list_sandboxes(metadata=metadata_filter))
            statuses = [
                status
                for info in (infos or [])
                if (status := _opensandbox_status_from_info(info)) is not None
            ]
            return [status for status in statuses if _matches_filters(status, filters)]
        finally:
            await self._close_manager(manager)

    async def list_runtime_containers(self, filters: dict[str, str]) -> list[ContainerStatus]:
        try:
            return await self._list_remote_statuses(filters)
        except OpenSandboxUnavailableError:
            raise
        except Exception:
            statuses = [_status_from_lease(lease, status="running") for lease in self._leases.values()]
            return [status for status in statuses if _matches_filters(status, filters)]

    async def cleanup_orphan_containers(self, filters: dict[str, str], *, reason: str) -> list[StopResult]:
        settings = get_settings()
        manager = await self._manager(self._connection_config(settings))
        try:
            metadata_filter = {
                f"ai-platform.{key}": value
                for key, value in filters.items()
                if key in {"tenant_id", "workspace_id", "user_id", "session_id", "run_id", "sandbox_mode"}
            }
            metadata_filter["ai-platform.owner"] = "sandbox-runtime"
            if hasattr(manager, "list_sandbox_infos") and self._sandbox_filter_class is not None:
                paged = await _maybe_await(
                    manager.list_sandbox_infos(
                        self._sandbox_filter_class(metadata=metadata_filter, page_size=100)
                    )
                )
                infos = getattr(paged, "sandbox_infos", None)
                if infos is None and isinstance(paged, dict):
                    infos = paged.get("sandbox_infos")
            else:
                infos = await _maybe_await(manager.list_sandboxes(metadata=metadata_filter))
            results: list[StopResult] = []
            for info in infos or []:
                status = _opensandbox_status_from_info(info)
                if status is None or not _matches_filters(status, filters):
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
    selected = provider_name or get_settings().sandbox_container_provider
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
        provider = OpenSandboxContainerProvider()
        _PROVIDER_CACHE[selected] = provider
        return provider
    raise ValueError(f"Unknown sandbox container provider: {selected}")
