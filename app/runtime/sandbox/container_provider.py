from __future__ import annotations

import asyncio
import inspect
import json
import os
import secrets
import shlex
import stat
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit
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
from app.settings import get_settings
from app.runtime.sandbox.workspace_permissions import RUNTIME_GID, RUNTIME_UID


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


class DockerPermissionDeniedError(SandboxRuntimeError):
    def __init__(self, message: str = "Docker permission denied") -> None:
        super().__init__("docker_permission_denied", message)


class ContainerStartFailedError(SandboxRuntimeError):
    def __init__(self, message: str = "Container start failed") -> None:
        super().__init__("container_start_failed", message)


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


def _published_executor_url_from_container(container: Any) -> str | None:
    ports = getattr(container, "attrs", {}).get("NetworkSettings", {}).get("Ports", {})
    bindings = ports.get("18000/tcp") or []
    if bindings:
        host_port = bindings[0].get("HostPort")
        if host_port:
            host = str(bindings[0].get("HostIp") or "").strip()
            if host in {"", "0.0.0.0", "::"}:
                host = get_settings().sandbox_executor_published_host
            return f"http://{host}:{host_port}"
    return None


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
    if labels.get("ai-platform.owner") != "sandbox-runtime":
        return None
    run_id = labels.get("ai-platform.run_id")
    sandbox_mode = labels.get("ai-platform.sandbox_mode")
    if sandbox_mode not in {"ephemeral", "persistent"}:
        sandbox_mode = None
    container_id = f"exec-{run_id}" if run_id else getattr(container, "id", getattr(container, "name", ""))
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
        executor_url=_published_executor_url_from_container(container),
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
        if (
            str(key).startswith("ai-platform.egress.")
            or str(key).startswith("ai-platform.executor.")
        ) and str(labels.get(key) or "") != expected:
            return False
    return True


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


def _workspace_owner_stat(workspace_host_path: str) -> os.stat_result:
    if os.name != "posix":
        raise OSError("POSIX ownership semantics unavailable")
    return Path(workspace_host_path).stat(follow_symlinks=False)


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
    return {
        str(key): str(value)
        for key, value in labels.items()
        if not str(key).startswith("ai-platform.executor.")
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


def _trusted_callback_target(settings: Any):
    return build_trusted_callback_target(
        _env_value(settings, "sandbox_callback_base_url"),
        extra_hosts=[_env_value(settings, "sandbox_callback_host_gateway")],
    )


def _executor_environment(
    request: SandboxRuntimeRequest,
    settings: Any,
    *,
    executor_auth_token: str,
    workspace_container_path: str = "/workspace",
) -> dict[str, str]:
    trusted_callback = _trusted_callback_target(settings)
    return {
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


def _opensandbox_image(settings: Any) -> str:
    image = str(getattr(settings, "opensandbox_executor_image", "") or "").strip()
    if image:
        return image
    return str(getattr(settings, "sandbox_executor_image", "ai-platform:local") or "ai-platform:local")


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


def _opensandbox_labels(settings: Any, request: SandboxRuntimeRequest) -> dict[str, str]:
    labels = _platform_metadata(request)
    labels["ai-platform.provider_backend"] = "opensandbox"
    labels.update(_executor_identity_labels())
    if getattr(settings, "sandbox_egress_policy_enabled", False) is True:
        labels["ai-platform.egress.policy"] = "opensandbox-network-policy"
        callback_host = _callback_policy_host(settings)
        if callback_host:
            labels["ai-platform.egress.callback_host"] = callback_host
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
    *,
    host_class: Any,
    volume_class: Any,
) -> list[Any]:
    if getattr(settings, "opensandbox_workspace_mount_enabled", True) is not True:
        return []
    return [
        volume_class(
            name="ai-platform-workspace",
            host=host_class(path=workspace.workspace_host_path),
            mountPath=workspace.workspace_container_path,
            readOnly=False,
        )
    ]


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
        raw_options = network.get("Options") or network.get("options") or {}
    else:
        attrs = getattr(network, "attrs", {})
        raw_options = attrs.get("Options") if isinstance(attrs, dict) else {}
    if not isinstance(raw_options, dict):
        return {}
    return {str(key): str(value).lower() for key, value in raw_options.items()}


def _docker_network_has_no_masquerade(network: Any) -> bool:
    options = _docker_network_options(network)
    return options.get("com.docker.network.bridge.enable_ip_masquerade") == "false"


def _is_network_not_found_error(exc: BaseException) -> bool:
    if isinstance(exc, KeyError):
        return True
    if docker is not None:
        not_found_error = getattr(getattr(docker, "errors", None), "NotFound", None)
        if not_found_error is not None and isinstance(exc, not_found_error):
            return True
    message = str(exc).lower()
    return "not found" in message or "no such network" in message or "404" in message


def _docker_egress_network_kwargs(client: Any, settings: Any) -> dict[str, Any]:
    if getattr(settings, "sandbox_egress_policy_enabled", False) is not True:
        return {}
    network_name = str(getattr(settings, "sandbox_egress_network_name", "") or "").strip()
    if not network_name:
        raise ContainerStartFailedError()
    networks = getattr(client, "networks", None)
    if networks is None:
        raise ContainerStartFailedError()
    try:
        network = networks.get(network_name)
    except Exception as exc:
        if not _is_network_not_found_error(exc):
            raise ContainerStartFailedError() from exc
        try:
            network = networks.create(
                network_name,
                driver="bridge",
                options={"com.docker.network.bridge.enable_ip_masquerade": "false"},
            )
        except Exception as create_exc:
            raise ContainerStartFailedError() from create_exc
    if not _docker_network_has_no_masquerade(network):
        raise ContainerStartFailedError()
    callback_host = str(getattr(settings, "sandbox_callback_host_gateway", "") or "").strip()
    kwargs: dict[str, Any] = {"network": network_name}
    if callback_host:
        kwargs["extra_hosts"] = {callback_host: "host-gateway"}
    return kwargs


def _egress_policy_labels(settings: Any) -> dict[str, str]:
    if getattr(settings, "sandbox_egress_policy_enabled", False) is not True:
        return {}
    network_name = str(getattr(settings, "sandbox_egress_network_name", "") or "").strip()
    callback_host = str(getattr(settings, "sandbox_callback_host_gateway", "") or "").strip()
    if not network_name:
        return {}
    labels = {
        "ai-platform.egress.policy": "default-deny-no-masq",
        "ai-platform.egress.network": network_name,
    }
    if callback_host:
        labels["ai-platform.egress.callback_host"] = callback_host
    return labels


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
    health_url = f"{executor_url.rstrip('/')}/health"
    request_headers = dict(executor_headers or {})
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
    identity_url = f"{executor_url.rstrip('/')}/health/runtime-identity"
    request_headers = dict(executor_headers)
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


def _executor_auth_headers(executor_auth_token: str, headers: dict[str, str] | None = None) -> dict[str, str]:
    resolved = dict(headers or {})
    resolved[EXECUTOR_AUTH_HEADER] = executor_auth_token
    return resolved


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


class DockerContainerProvider:
    def __init__(
        self,
        *,
        docker_client_factory: Callable[[], Any] | None = None,
        health_probe: Callable[..., bool] | None = None,
        identity_probe: Callable[..., dict[str, int]] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._leases: dict[str, ContainerLease] = {}
        self._docker_client_factory = docker_client_factory
        self._health_probe = health_probe or default_executor_health_probe
        self._identity_probe = identity_probe or default_executor_identity_probe
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

    async def _wait_for_executor_url(self, container: Any, timeout_seconds: int) -> str:
        deadline = time.monotonic() + max(timeout_seconds, 1)
        while time.monotonic() <= deadline:
            if hasattr(container, "reload"):
                container.reload()
            executor_url = _published_executor_url_from_container(container)
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

    async def _reuse_existing_container(
        self,
        lease: ContainerLease,
        timeout_seconds: int,
    ) -> ContainerLease | None:
        try:
            container = self._get_client().containers.get(lease.container_name)
        except Exception:
            return None
        status = _container_status_from_labels(container)
        if status is None:
            return None
        if not _status_matches_lease(status, lease):
            return None
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
            executor_url = await self._wait_for_executor_url(container, timeout_seconds)
            executor_headers = _executor_auth_headers(executor_auth_token)
            healthy = await asyncio.to_thread(
                _call_executor_health_probe,
                self._health_probe,
                executor_url,
                timeout_seconds,
                executor_headers,
            )
            if not healthy:
                raise ExecutorHealthTimeoutError()
            identity = await asyncio.to_thread(
                self._identity_probe,
                executor_url,
                timeout_seconds,
                executor_headers,
            )
            _require_expected_executor_identity(identity)
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
        if status is not None and _status_matches_lease(status, lease):
            return _stop_and_remove_container(container)
        return True

    async def create_or_reuse(
        self,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
    ) -> ContainerLease:
        settings = get_settings()
        client = self._get_client()
        container_id = f"exec-{request.run_id}"
        try:
            client.ping()
        except Exception as exc:  # pragma: no cover - branch shape varies by docker SDK/runtime
            normalized_exc = _normalize_docker_availability_error(exc)
            if normalized_exc is not None:
                raise normalized_exc from exc
            raise DockerUnavailableError("Docker daemon is unavailable") from exc
        expected_egress_labels = _egress_policy_labels(settings)
        workspace_user = _docker_workspace_user_value(workspace.workspace_host_path)
        existing = self._leases.get(container_id)
        if existing is not None:
            if not _lease_matches_request_workspace(existing, request, workspace):
                if not self._remove_owned_cached_container(existing):
                    raise ContainerCleanupFailedError("cached container cleanup could not be confirmed")
                self._leases.pop(container_id, None)
                raise ContainerStartFailedError("cached lease scope mismatch")
            existing.labels.update(expected_egress_labels)
            recovered_existing = await self._reuse_existing_container(
                existing,
                settings.sandbox_container_start_timeout_seconds,
            )
            if recovered_existing is None:
                self._leases.pop(container_id, None)
                raise ContainerStartFailedError()
            self._leases[recovered_existing.container_id] = recovered_existing
            return recovered_existing

        bootstrap_lease = _lease_from_request("docker", request, workspace, executor_url=_executor_url())
        bootstrap_lease.labels.update(expected_egress_labels)
        recovered = await self._reuse_existing_container(
            bootstrap_lease,
            settings.sandbox_container_start_timeout_seconds,
        )
        if recovered is not None:
            self._leases[recovered.container_id] = recovered
            return recovered
        cold_start_started_at = self._monotonic()
        executor_auth_token = _generate_executor_auth_token()
        bootstrap_lease.executor_headers = _executor_auth_headers(executor_auth_token)
        try:
            container = client.containers.create(
                image=settings.sandbox_executor_image,
                name=bootstrap_lease.container_name,
                detach=True,
                labels={**bootstrap_lease.platform_labels(), **_executor_identity_labels()},
                volumes={
                    workspace.workspace_host_path: {
                        "bind": workspace.workspace_container_path,
                        "mode": "rw",
                    }
                },
                environment=_executor_environment(
                    request,
                    settings,
                    executor_auth_token=executor_auth_token,
                    workspace_container_path=workspace.workspace_container_path,
                ),
                ports={"18000/tcp": ("127.0.0.1", None)},
                **_docker_egress_network_kwargs(client, settings),
                **_docker_security_kwargs(),
                user=workspace_user,
                **_docker_resource_kwargs(request.resource_limits),
            )
        except CallbackTargetValidationError as exc:
            raise ContainerStartFailedError() from exc
        except Exception as exc:
            normalized_exc = _normalize_docker_availability_error(exc)
            if isinstance(normalized_exc, DockerPermissionDeniedError):
                raise normalized_exc from exc
            if "container" in locals():
                self._cleanup_container_or_track(container, bootstrap_lease)
            raise ContainerStartFailedError() from exc
        try:
            if hasattr(container, "start"):
                container.start()
        except Exception as exc:
            normalized_exc = _normalize_docker_availability_error(exc)
            self._cleanup_container_or_track(container, bootstrap_lease)
            if isinstance(normalized_exc, DockerPermissionDeniedError):
                raise normalized_exc from exc
            raise ContainerStartFailedError() from exc

        try:
            executor_url = await self._wait_for_executor_url(container, settings.sandbox_container_start_timeout_seconds)
            bootstrap_lease.executor_url = executor_url
        except asyncio.CancelledError as exc:
            try:
                self._cleanup_container_or_track(container, bootstrap_lease)
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
            raise
        except ExecutorHealthTimeoutError as exc:
            try:
                self._cleanup_container_or_track(container, bootstrap_lease)
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
            raise
        sandbox_container_cold_start_latency_ms = self._elapsed_ms(cold_start_started_at)
        healthcheck_started_at = self._monotonic()
        executor_headers = _executor_auth_headers(executor_auth_token)
        try:
            healthy = await asyncio.to_thread(
                _call_executor_health_probe,
                self._health_probe,
                executor_url,
                settings.sandbox_executor_health_timeout_seconds,
                executor_headers,
            )
        except asyncio.CancelledError as exc:
            try:
                self._cleanup_container_or_track(container, bootstrap_lease)
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
            raise
        except Exception as exc:
            self._cleanup_container_or_track(container, bootstrap_lease)
            raise ExecutorHealthTimeoutError() from exc
        sandbox_healthcheck_latency_ms = self._elapsed_ms(healthcheck_started_at)
        if not healthy:
            self._cleanup_container_or_track(container, bootstrap_lease)
            raise ExecutorHealthTimeoutError()
        if _container_config_user(container) != workspace_user:
            self._cleanup_container_or_track(container, bootstrap_lease)
            raise ContainerStartFailedError("executor Config.User mismatch")
        try:
            identity = await asyncio.to_thread(
                self._identity_probe,
                executor_url,
                settings.sandbox_executor_health_timeout_seconds,
                executor_headers,
            )
            _require_expected_executor_identity(identity)
        except asyncio.CancelledError as exc:
            try:
                self._cleanup_container_or_track(container, bootstrap_lease)
            except ContainerCleanupFailedError as cleanup_exc:
                raise cleanup_exc from exc
            raise
        except Exception as exc:
            self._cleanup_container_or_track(container, bootstrap_lease)
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
        lease.labels.update(bootstrap_lease.labels)
        self._leases[lease.container_id] = lease
        return lease

    async def stop(self, lease: ContainerLease, *, reason: str) -> StopResult:
        try:
            container = self._get_client().containers.get(lease.container_name)
            status = _container_status_from_labels(container)
            if status is None or not _status_matches_lease(status, lease):
                self._leases.pop(lease.container_id, None)
                return StopResult(container_id=lease.container_id, status="not_found", message=reason)
            if not _stop_and_remove_container(container):
                self._leases.setdefault(lease.container_id, lease)
                return StopResult(container_id=lease.container_id, status="failed", message="Container stop failed")
        except Exception as exc:
            if _is_not_found_error(exc):
                self._leases.pop(lease.container_id, None)
                return StopResult(container_id=lease.container_id, status="not_found", message=reason)
            self._leases.setdefault(lease.container_id, lease)
            return StopResult(container_id=lease.container_id, status="failed", message="Container stop failed")
        self._leases.pop(lease.container_id, None)
        return StopResult(container_id=lease.container_id, status="stopped", message=reason)

    async def list_runtime_containers(self, filters: dict[str, str]) -> list[ContainerStatus]:
        try:
            containers = self._get_client().containers.list(all=True, filters={"label": ["ai-platform.owner=sandbox-runtime"]})
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
            containers = self._get_client().containers.list(all=True, filters={"label": ["ai-platform.owner=sandbox-runtime"]})
        except Exception as exc:
            normalized_exc = _normalize_docker_availability_error(exc)
            if normalized_exc is not None:
                raise normalized_exc from exc
            raise
        results: list[StopResult] = []
        for container in containers:
            status = _container_status_from_labels(container)
            if status is None or not _matches_filters(status, filters):
                continue
            if status.status == "running":
                continue
            if status.status not in {"exited", "dead", "removing", "removed"}:
                continue
            try:
                if hasattr(container, "remove"):
                    container.remove(force=True)
            except Exception:
                results.append(StopResult(container_id=status.container_id, status="failed", message="Container cleanup failed"))
                continue
            results.append(StopResult(container_id=status.container_id, status="stopped", message=reason))
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

    async def create_or_reuse(
        self,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
    ) -> ContainerLease:
        settings = get_settings()
        self._ensure_symbols()
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
                expected_lease.labels.update(_opensandbox_labels(settings, request))
                remote_status = _opensandbox_status_from_info(info)
                if (
                    remote_status is None
                    or remote_status.container_id != cached.container_id
                    or not _status_matches_lease(remote_status, expected_lease)
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
            except asyncio.CancelledError as exc:
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
            return cached

        started_at = self._monotonic()
        connection_config = self._connection_config(settings)
        metadata = _opensandbox_labels(settings, request)
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
            labels=_provider_lease_labels(metadata),
            timings={
                "sandbox_container_start_latency_ms": self._elapsed_ms(started_at),
                "sandbox_container_cold_start_latency_ms": self._elapsed_ms(started_at),
                "sandbox_healthcheck_latency_ms": sandbox_healthcheck_latency_ms,
            },
        )
        self._sandboxes[lease.container_id] = sandbox
        self._leases[f"opensandbox-{request.run_id}"] = lease
        return lease

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
