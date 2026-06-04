import importlib
import threading
import time
from typing import Any

import pytest

from app.runtime.sandbox.contracts import SandboxRuntimeRequest, WorkspaceLease


def request(**overrides) -> SandboxRuntimeRequest:
    values = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "agent_id": "general-agent",
        "skill_ids": ["general-chat"],
        "input_message": "hello",
        "sandbox_mode": "ephemeral",
        "browser_enabled": False,
        "model": "deepseek-v4-flash",
        "permissions": ["sandbox.execute"],
        "callback_url": "http://callback",
        "callback_token_id": "cbt_run_a",
    }
    values.update(overrides)
    return SandboxRuntimeRequest(**values)


def workspace() -> WorkspaceLease:
    return WorkspaceLease(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        host_root="/runtime/tenants/tenant-a/workspaces/workspace-a/users/user-a/sessions/session-a/runs/run-a",
        workspace_host_path="/runtime/tenants/tenant-a/workspaces/workspace-a/users/user-a/sessions/session-a/runs/run-a/workspace",
        workspace_container_path="/workspace",
        inputs_host_path="/runtime/tenants/tenant-a/workspaces/workspace-a/users/user-a/sessions/session-a/runs/run-a/inputs",
        logs_host_path="/runtime/tenants/tenant-a/workspaces/workspace-a/users/user-a/sessions/session-a/runs/run-a/logs",
    )


class FakeDockerContainer:
    def __init__(
        self,
        *,
        image: str,
        name: str,
        detach: bool,
        labels: dict[str, str],
        volumes: dict[str, dict[str, str]],
        environment: dict[str, str],
        ports: dict[str, Any],
        start_error: Exception | None = None,
        stop_error: Exception | None = None,
        host_port: str | None = "18000",
        **docker_kwargs: Any,
    ) -> None:
        self.id = f"docker-{name}"
        self.image = image
        self.name = name
        self.detach = detach
        self.labels = dict(labels)
        self.volumes = dict(volumes)
        self.environment = dict(environment)
        self.ports = dict(ports)
        self.docker_kwargs = dict(docker_kwargs)
        self.status = "created"
        self.started = False
        self.stopped = False
        self.removed = False
        self._start_error = start_error
        self._stop_error = stop_error
        port_bindings = [] if host_port is None else [{"HostIp": "0.0.0.0", "HostPort": host_port}]
        self.attrs = {
            "Config": {"Labels": self.labels},
            "NetworkSettings": {
                "Ports": {
                    "18000/tcp": port_bindings
                }
            },
        }

    def start(self) -> None:
        if self._start_error is not None:
            raise self._start_error
        self.started = True
        self.status = "running"

    def stop(self) -> None:
        if self._stop_error is not None:
            raise self._stop_error
        self.stopped = True
        self.status = "exited"

    def remove(self, force: bool = False) -> None:
        self.removed = True
        self.status = "removed"

    def reload(self) -> None:
        return None


class FakeDockerContainers:
    def __init__(self, client: "FakeDockerClient") -> None:
        self._client = client

    def create(self, **kwargs) -> FakeDockerContainer:
        if self._client.create_error is not None:
            raise self._client.create_error
        container = FakeDockerContainer(
            start_error=self._client.start_error,
            stop_error=self._client.stop_error,
            host_port=self._client.host_port,
            **kwargs,
        )
        self._client.created.append(kwargs)
        self._client.containers_by_name[container.name] = container
        return container

    def list(self, all: bool = False, filters: dict[str, Any] | None = None) -> list[FakeDockerContainer]:
        containers = list(self._client.containers_by_name.values())
        if not all:
            containers = [container for container in containers if container.status == "running"]
        return containers

    def get(self, name: str) -> FakeDockerContainer:
        return self._client.containers_by_name[name]


class FakeDockerClient:
    def __init__(
        self,
        *,
        ping_error: Exception | None = None,
        create_error: Exception | None = None,
        start_error: Exception | None = None,
        stop_error: Exception | None = None,
        host_port: str | None = "18000",
    ) -> None:
        self.ping_error = ping_error
        self.create_error = create_error
        self.start_error = start_error
        self.stop_error = stop_error
        self.host_port = host_port
        self.created: list[dict[str, Any]] = []
        self.containers_by_name: dict[str, FakeDockerContainer] = {}
        self.containers = FakeDockerContainers(self)
        self.ping_count = 0

    def ping(self) -> None:
        self.ping_count += 1
        if self.ping_error is not None:
            raise self.ping_error


@pytest.mark.asyncio
async def test_fake_provider_create_or_reuse_returns_lease_and_tracks_status():
    from app.runtime.sandbox.container_provider import FakeContainerProvider

    provider = FakeContainerProvider(executor_url="http://executor.test")

    lease = await provider.create_or_reuse(request(), workspace())
    statuses = await provider.list_runtime_containers({})

    assert lease.container_id == "exec-run-a"
    assert lease.container_name == "executor-exec-run-a"
    assert lease.provider == "fake"
    assert lease.executor_url == "http://executor.test"
    assert lease.platform_labels()["ai-platform.run_id"] == "run-a"
    assert statuses[0].run_id == "run-a"
    assert statuses[0].status == "running"


@pytest.mark.asyncio
async def test_fake_provider_stop_removes_lease_from_runtime_status():
    from app.runtime.sandbox.container_provider import FakeContainerProvider

    provider = FakeContainerProvider()
    lease = await provider.create_or_reuse(request(), workspace())

    result = await provider.stop(lease, reason="finished")
    statuses = await provider.list_runtime_containers({})

    assert result.container_id == "exec-run-a"
    assert result.status == "stopped"
    assert statuses == []


@pytest.mark.asyncio
async def test_fake_provider_default_executor_url_is_not_an_active_api_port():
    from app.runtime.sandbox.container_provider import FakeContainerProvider

    provider = FakeContainerProvider()

    lease = await provider.create_or_reuse(request(), workspace())

    assert lease.executor_url == "http://fake-sandbox-executor.invalid"


@pytest.mark.asyncio
async def test_create_container_provider_reuses_process_provider(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    container_provider.reset_container_provider_cache()
    monkeypatch.setattr(
        container_provider,
        "get_settings",
        lambda: type("StubSettings", (), {"sandbox_container_provider": "fake"})(),
    )

    runtime_provider = container_provider.create_container_provider()
    admin_provider = container_provider.create_container_provider()
    lease = await runtime_provider.create_or_reuse(request(), workspace())
    statuses = await admin_provider.list_runtime_containers({"tenant_id": "tenant-a"})

    assert admin_provider is runtime_provider
    assert statuses[0].container_id == lease.container_id

    container_provider.reset_container_provider_cache()


def test_create_container_provider_rejects_unknown_provider(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")

    monkeypatch.setattr(
        container_provider,
        "get_settings",
        lambda: type("StubSettings", (), {"sandbox_container_provider": "mystery"})(),
    )

    with pytest.raises(ValueError, match="Unknown sandbox container provider"):
        container_provider.create_container_provider()


def test_default_executor_health_probe_polls_health_endpoint(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    calls = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def get(self, url):
            calls.append((url, self.timeout))
            return FakeResponse()

    monkeypatch.setattr(container_provider.httpx, "Client", FakeClient)

    assert container_provider.default_executor_health_probe("http://executor.test", 5) is True
    assert calls == [("http://executor.test/health", 1.0)]


def test_default_executor_health_probe_returns_false_after_timeout(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    sleep_calls = []

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def get(self, url):
            raise RuntimeError("not ready")

    current_time = {"value": 0.0}

    def fake_monotonic():
        current_time["value"] += 0.4
        return current_time["value"]

    def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(container_provider.httpx, "Client", FakeClient)
    monkeypatch.setattr(container_provider.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(container_provider.time, "sleep", fake_sleep)

    assert container_provider.default_executor_health_probe("http://executor.test", 1) is False
    assert sleep_calls


@pytest.mark.asyncio
async def test_docker_provider_reports_missing_dependency_when_sdk_not_installed(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")

    monkeypatch.setattr(container_provider, "docker", None)
    provider = container_provider.DockerContainerProvider()

    with pytest.raises(container_provider.DockerUnavailableError) as exc_info:
        await provider.create_or_reuse(request(), workspace())

    assert exc_info.value.error_code == "docker_unavailable"


@pytest.mark.asyncio
async def test_docker_provider_permission_denied_error_is_sanitized():
    from app.runtime.sandbox.container_provider import DockerContainerProvider, DockerPermissionDeniedError

    fake = FakeDockerClient(ping_error=RuntimeError(f"permission denied: {workspace().workspace_host_path}"))
    provider = DockerContainerProvider(docker_client_factory=lambda: fake)

    with pytest.raises(DockerPermissionDeniedError) as exc_info:
        await provider.create_or_reuse(request(), workspace())

    assert exc_info.value.error_code == "docker_permission_denied"
    assert workspace().workspace_host_path not in str(exc_info.value)
    assert str(exc_info.value) == "Docker permission denied"


@pytest.mark.asyncio
async def test_docker_provider_daemon_error_is_sanitized():
    from app.runtime.sandbox.container_provider import DockerContainerProvider, DockerUnavailableError

    fake = FakeDockerClient(ping_error=RuntimeError("cannot connect to /var/run/docker.sock"))
    provider = DockerContainerProvider(docker_client_factory=lambda: fake)

    with pytest.raises(DockerUnavailableError) as exc_info:
        await provider.create_or_reuse(request(), workspace())

    assert exc_info.value.error_code == "docker_unavailable"
    assert "/var/run/docker.sock" not in str(exc_info.value)
    assert str(exc_info.value) == "Docker daemon is unavailable"


@pytest.mark.asyncio
async def test_docker_provider_creates_container_with_workspace_labels_and_env():
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )

    lease = await provider.create_or_reuse(request(), workspace())
    statuses = await provider.list_runtime_containers({"tenant_id": "tenant-a"})

    created = fake.created[0]
    assert created["image"] == "ai-platform-executor:dev"
    assert created["name"] == "executor-exec-run-a"
    assert created["labels"]["ai-platform.run_id"] == "run-a"
    assert created["volumes"][workspace().workspace_host_path]["bind"] == "/workspace"
    assert created["environment"]["AI_PLATFORM_SESSION_ID"] == "session-a"
    assert created["environment"]["APP_MODULE"] == "app.runtime.sandbox.executor_app:create_executor_app"
    assert created["environment"]["APP_PORT"] == "18000"
    assert lease.executor_url == "http://127.0.0.1:18000"
    assert statuses[0].run_id == "run-a"
    assert statuses[0].sandbox_mode == "ephemeral"


@pytest.mark.asyncio
async def test_docker_provider_maps_resource_limits_to_docker_create_kwargs_without_disabling_executor_network():
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )

    await provider.create_or_reuse(
        request(
            resource_limits={
                "memory_mb": 512,
                "cpu_count": 0.5,
                "pids_limit": 128,
                "disk_mb": 1024,
                "egress": "disabled",
            }
        ),
        workspace(),
    )

    created = fake.created[0]
    assert created["mem_limit"] == "512m"
    assert created["nano_cpus"] == 500_000_000
    assert created["pids_limit"] == 128
    assert created["storage_opt"] == {"size": "1024m"}
    assert "network_disabled" not in created


@pytest.mark.asyncio
async def test_docker_provider_stop_calls_container_stop_and_removes_lease():
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )
    lease = await provider.create_or_reuse(request(), workspace())

    result = await provider.stop(lease, reason="cancelled")

    assert result.status == "stopped"
    assert fake.containers_by_name[lease.container_name].stopped is True
    assert fake.containers_by_name[lease.container_name].removed is True
    statuses = await provider.list_runtime_containers({})
    assert statuses[0].run_id == "run-a"
    assert statuses[0].status == "removed"


@pytest.mark.asyncio
async def test_docker_provider_maps_create_failure_to_start_failed():
    from app.runtime.sandbox.container_provider import ContainerStartFailedError, DockerContainerProvider

    fake = FakeDockerClient(create_error=RuntimeError("boom"))
    provider = DockerContainerProvider(docker_client_factory=lambda: fake)

    with pytest.raises(ContainerStartFailedError) as exc_info:
        await provider.create_or_reuse(request(), workspace())

    assert exc_info.value.error_code == "container_start_failed"


@pytest.mark.asyncio
async def test_docker_provider_maps_health_false_to_timeout():
    from app.runtime.sandbox.container_provider import DockerContainerProvider, ExecutorHealthTimeoutError

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: False,
    )

    with pytest.raises(ExecutorHealthTimeoutError) as exc_info:
        await provider.create_or_reuse(request(), workspace())

    assert exc_info.value.error_code == "executor_health_timeout"


@pytest.mark.asyncio
async def test_docker_provider_requires_published_executor_port(monkeypatch):
    from app.runtime.sandbox.container_provider import DockerContainerProvider, ExecutorHealthTimeoutError
    import app.runtime.sandbox.container_provider as container_provider

    settings = type(
        "StubSettings",
        (),
        {
            "sandbox_executor_published_host": "127.0.0.1",
            "sandbox_executor_image": "ai-platform-executor:dev",
            "sandbox_callback_base_url": "http://127.0.0.1:8000",
            "sandbox_container_start_timeout_seconds": 1,
            "sandbox_executor_health_timeout_seconds": 1,
        },
    )()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)

    fake = FakeDockerClient(host_port=None)
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )

    with pytest.raises(ExecutorHealthTimeoutError) as exc_info:
        await provider.create_or_reuse(request(), workspace())

    assert exc_info.value.error_code == "executor_health_timeout"
    assert fake.containers_by_name["executor-exec-run-a"].stopped is True
    assert fake.containers_by_name["executor-exec-run-a"].removed is True


@pytest.mark.asyncio
async def test_docker_provider_stop_reaches_container_without_cached_lease():
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )
    lease = await provider.create_or_reuse(request(), workspace())
    provider._leases.clear()

    result = await provider.stop(lease, reason="recovered_cancel")

    assert result.status == "stopped"
    assert fake.containers_by_name[lease.container_name].stopped is True
    assert fake.containers_by_name[lease.container_name].removed is True


@pytest.mark.asyncio
async def test_docker_provider_reuses_existing_docker_container_after_restart():
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    fake = FakeDockerClient()
    first_provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )
    first_lease = await first_provider.create_or_reuse(request(), workspace())
    restarted_provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )

    second_lease = await restarted_provider.create_or_reuse(request(), workspace())

    assert second_lease.container_name == first_lease.container_name
    assert len(fake.created) == 1


@pytest.mark.asyncio
async def test_docker_provider_errors_do_not_expose_host_paths():
    from app.runtime.sandbox.container_provider import ContainerStartFailedError, DockerContainerProvider

    fake = FakeDockerClient(start_error=RuntimeError(f"cannot mount {workspace().workspace_host_path}"))
    provider = DockerContainerProvider(docker_client_factory=lambda: fake)

    with pytest.raises(ContainerStartFailedError) as exc_info:
        await provider.create_or_reuse(request(), workspace())

    assert workspace().workspace_host_path not in str(exc_info.value)
    assert str(exc_info.value) == "Container start failed"


@pytest.mark.asyncio
async def test_docker_provider_removes_container_after_health_timeout():
    from app.runtime.sandbox.container_provider import DockerContainerProvider, ExecutorHealthTimeoutError

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: False,
    )

    with pytest.raises(ExecutorHealthTimeoutError):
        await provider.create_or_reuse(request(), workspace())

    container = fake.containers_by_name["executor-exec-run-a"]
    assert container.stopped is True
    assert container.removed is True


@pytest.mark.asyncio
async def test_docker_provider_cleanup_removes_container_when_stop_fails():
    from app.runtime.sandbox.container_provider import DockerContainerProvider, ExecutorHealthTimeoutError

    fake = FakeDockerClient(stop_error=RuntimeError("stop failed with /runtime/path"))
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: False,
    )

    with pytest.raises(ExecutorHealthTimeoutError):
        await provider.create_or_reuse(request(), workspace())

    container = fake.containers_by_name["executor-exec-run-a"]
    assert container.removed is True


@pytest.mark.asyncio
async def test_docker_provider_runs_health_probe_off_event_loop_thread():
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    fake = FakeDockerClient()
    event_loop_thread = threading.get_ident()
    probe_threads = []

    def blocking_health_probe(executor_url, timeout_seconds):
        probe_threads.append(threading.get_ident())
        time.sleep(0.01)
        return True

    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=blocking_health_probe,
    )

    await provider.create_or_reuse(request(), workspace())

    assert probe_threads
    assert probe_threads[0] != event_loop_thread


@pytest.mark.asyncio
async def test_docker_provider_cleanup_orphan_containers_removes_stopped_same_tenant_only():
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    fake = FakeDockerClient()
    provider = DockerContainerProvider(docker_client_factory=lambda: fake)
    same_tenant_exited = FakeDockerContainer(
        image="ai-platform-executor:dev",
        name="executor-orphan-a",
        detach=True,
        labels={
            "ai-platform.owner": "sandbox-runtime",
            "ai-platform.tenant_id": "tenant-a",
            "ai-platform.workspace_id": "workspace-a",
            "ai-platform.user_id": "user-a",
            "ai-platform.session_id": "session-a",
            "ai-platform.run_id": "run-orphan-a",
            "ai-platform.sandbox_mode": "ephemeral",
            "ai-platform.browser_enabled": "false",
        },
        volumes={},
        environment={},
        ports={},
    )
    same_tenant_exited.status = "exited"
    same_tenant_running = FakeDockerContainer(
        image="ai-platform-executor:dev",
        name="executor-running-a",
        detach=True,
        labels={
            "ai-platform.owner": "sandbox-runtime",
            "ai-platform.tenant_id": "tenant-a",
            "ai-platform.workspace_id": "workspace-a",
            "ai-platform.user_id": "user-a",
            "ai-platform.session_id": "session-a",
            "ai-platform.run_id": "run-running-a",
            "ai-platform.sandbox_mode": "ephemeral",
            "ai-platform.browser_enabled": "false",
        },
        volumes={},
        environment={},
        ports={},
    )
    same_tenant_running.status = "running"
    same_tenant_created = FakeDockerContainer(
        image="ai-platform-executor:dev",
        name="executor-created-a",
        detach=True,
        labels={
            "ai-platform.owner": "sandbox-runtime",
            "ai-platform.tenant_id": "tenant-a",
            "ai-platform.workspace_id": "workspace-a",
            "ai-platform.user_id": "user-a",
            "ai-platform.session_id": "session-a",
            "ai-platform.run_id": "run-created-a",
            "ai-platform.sandbox_mode": "ephemeral",
            "ai-platform.browser_enabled": "false",
        },
        volumes={},
        environment={},
        ports={},
    )
    same_tenant_created.status = "created"
    foreign_exited = FakeDockerContainer(
        image="ai-platform-executor:dev",
        name="executor-orphan-b",
        detach=True,
        labels={
            "ai-platform.owner": "sandbox-runtime",
            "ai-platform.tenant_id": "tenant-b",
            "ai-platform.workspace_id": "workspace-b",
            "ai-platform.user_id": "user-b",
            "ai-platform.session_id": "session-b",
            "ai-platform.run_id": "run-orphan-b",
            "ai-platform.sandbox_mode": "ephemeral",
            "ai-platform.browser_enabled": "false",
        },
        volumes={},
        environment={},
        ports={},
    )
    foreign_exited.status = "exited"
    fake.containers_by_name = {
        same_tenant_exited.name: same_tenant_exited,
        same_tenant_running.name: same_tenant_running,
        same_tenant_created.name: same_tenant_created,
        foreign_exited.name: foreign_exited,
    }

    results = await provider.cleanup_orphan_containers({"tenant_id": "tenant-a"}, reason="admin_runtime")

    assert [item.container_id for item in results] == ["exec-run-orphan-a"]
    assert same_tenant_exited.removed is True
    assert same_tenant_running.removed is False
    assert same_tenant_created.removed is False
    assert foreign_exited.removed is False
