from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Protocol

import httpx

try:
    import docker  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised through docker = None path
    docker = None

from app.runtime.sandbox.contracts import ContainerLease, ContainerStatus, SandboxRuntimeRequest, StopResult, WorkspaceLease
from app.settings import get_settings


class SandboxRuntimeError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class DockerUnavailableError(SandboxRuntimeError):
    def __init__(self, message: str = "Docker SDK is unavailable") -> None:
        super().__init__("docker_unavailable", message)


class DockerPermissionDeniedError(SandboxRuntimeError):
    def __init__(self, message: str = "Docker permission denied") -> None:
        super().__init__("docker_permission_denied", message)


class ContainerStartFailedError(SandboxRuntimeError):
    def __init__(self, message: str = "Container start failed") -> None:
        super().__init__("container_start_failed", message)


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
            host = get_settings().sandbox_executor_published_host
            return f"http://{host}:{host_port}"
    return None


def _lease_from_request(
    provider: str,
    request: SandboxRuntimeRequest,
    workspace: WorkspaceLease,
    *,
    executor_url: str,
    timings: dict[str, int] | None = None,
) -> ContainerLease:
    container_id = f"exec-{request.run_id}"
    return ContainerLease(
        container_id=container_id,
        container_name=f"executor-{container_id}",
        provider=provider,
        executor_url=executor_url,
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
        if str(key).startswith("ai-platform.egress.") and str(labels.get(key) or "") != expected:
            return False
    return True


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
        "tmpfs": {"/tmp": "rw,noexec,nosuid,size=64m"},
    }


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


def default_executor_health_probe(executor_url: str, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + max(timeout_seconds, 1)
    health_url = f"{executor_url.rstrip('/')}/health"
    while time.monotonic() <= deadline:
        try:
            with httpx.Client(timeout=1.0) as client:
                response = client.get(health_url)
                response.raise_for_status()
            return True
        except Exception:
            time.sleep(0.25)
    return False


def _stop_and_remove_container(container: Any) -> None:
    if hasattr(container, "stop"):
        try:
            container.stop()
        except Exception:
            pass
    if hasattr(container, "remove"):
        try:
            container.remove(force=True)
        except Exception:
            pass


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
        lease = _lease_from_request("fake", request, workspace, executor_url=self._executor_url)
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
        health_probe: Callable[[str, int], bool] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._leases: dict[str, ContainerLease] = {}
        self._docker_client_factory = docker_client_factory
        self._health_probe = health_probe or default_executor_health_probe
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
        executor_url = await self._wait_for_executor_url(container, timeout_seconds)
        return ContainerLease(
            container_id=lease.container_id,
            container_name=lease.container_name,
            provider="docker",
            executor_url=executor_url,
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
        existing = self._leases.get(container_id)
        if existing is not None:
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
        try:
            container = client.containers.create(
                image=settings.sandbox_executor_image,
                name=bootstrap_lease.container_name,
                detach=True,
                labels=bootstrap_lease.platform_labels(),
                volumes={
                    workspace.workspace_host_path: {
                        "bind": workspace.workspace_container_path,
                        "mode": "rw",
                    }
                },
                environment={
                    "APP_MODULE": "app.runtime.sandbox.executor_app:create_executor_app",
                    "APP_PORT": "18000",
                    "AI_PLATFORM_SESSION_ID": request.session_id,
                    "AI_PLATFORM_RUN_ID": request.run_id,
                    "AI_PLATFORM_CALLBACK_BASE_URL": settings.sandbox_callback_base_url,
                },
                ports={"18000/tcp": None},
                **_docker_egress_network_kwargs(client, settings),
                **_docker_security_kwargs(),
                **_docker_resource_kwargs(request.resource_limits),
            )
            if hasattr(container, "start"):
                container.start()
        except Exception as exc:
            normalized_exc = _normalize_docker_availability_error(exc)
            if isinstance(normalized_exc, DockerPermissionDeniedError):
                raise normalized_exc from exc
            if "container" in locals():
                _stop_and_remove_container(container)
            raise ContainerStartFailedError() from exc

        try:
            executor_url = await self._wait_for_executor_url(container, settings.sandbox_container_start_timeout_seconds)
        except ExecutorHealthTimeoutError:
            _stop_and_remove_container(container)
            raise
        sandbox_container_cold_start_latency_ms = self._elapsed_ms(cold_start_started_at)
        healthcheck_started_at = self._monotonic()
        try:
            healthy = await asyncio.to_thread(
                self._health_probe,
                executor_url,
                settings.sandbox_executor_health_timeout_seconds,
            )
        except Exception as exc:
            _stop_and_remove_container(container)
            raise ExecutorHealthTimeoutError() from exc
        sandbox_healthcheck_latency_ms = self._elapsed_ms(healthcheck_started_at)
        if not healthy:
            _stop_and_remove_container(container)
            raise ExecutorHealthTimeoutError()

        lease = _lease_from_request(
            "docker",
            request,
            workspace,
            executor_url=executor_url,
            timings={
                "sandbox_container_cold_start_latency_ms": sandbox_container_cold_start_latency_ms,
                "sandbox_healthcheck_latency_ms": sandbox_healthcheck_latency_ms,
            },
        )
        lease.labels.update(bootstrap_lease.labels)
        self._leases[lease.container_id] = lease
        return lease

    async def stop(self, lease: ContainerLease, *, reason: str) -> StopResult:
        self._leases.pop(lease.container_id, None)
        try:
            container = self._get_client().containers.get(lease.container_name)
            status = _container_status_from_labels(container)
            if status is None or not _status_matches_lease(status, lease):
                return StopResult(container_id=lease.container_id, status="not_found", message=reason)
            if hasattr(container, "stop"):
                container.stop()
            if hasattr(container, "remove"):
                container.remove(force=True)
        except Exception as exc:
            if _is_not_found_error(exc):
                return StopResult(container_id=lease.container_id, status="not_found", message=reason)
            return StopResult(container_id=lease.container_id, status="failed", message="Container stop failed")
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
    raise ValueError(f"Unknown sandbox container provider: {selected}")
