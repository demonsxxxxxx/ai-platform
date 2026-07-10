import asyncio
import inspect
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
        published_host = "0.0.0.0"
        port_binding = ports.get("18000/tcp")
        if isinstance(port_binding, tuple) and len(port_binding) == 2:
            candidate_host = str(port_binding[0] or "").strip()
            if candidate_host:
                published_host = candidate_host
        port_bindings = [] if host_port is None else [{"HostIp": published_host, "HostPort": host_port}]
        self.attrs = {
            "Config": {
                "Labels": self.labels,
                "User": str(docker_kwargs.get("user") or ""),
                "Env": [f"{key}={value}" for key, value in self.environment.items()],
            },
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
        existing = self._client.containers_by_name.get(kwargs["name"])
        if existing is not None and not existing.removed:
            raise RuntimeError(f"Conflict. The container name {kwargs['name']} is already in use.")
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
        if self._client.list_error is not None:
            raise self._client.list_error
        containers = list(self._client.containers_by_name.values())
        if not all:
            containers = [container for container in containers if container.status == "running"]
        return containers

    def get(self, name: str) -> FakeDockerContainer:
        return self._client.containers_by_name[name]


class FakeDockerNetworks:
    def __init__(self, client: "FakeDockerClient") -> None:
        self._client = client

    def get(self, name: str) -> dict[str, Any]:
        if name not in self._client.networks_by_name:
            raise KeyError(name)
        return self._client.networks_by_name[name]

    def create(self, name: str, **kwargs) -> dict[str, Any]:
        network = {"name": name, **kwargs}
        self._client.networks_by_name[name] = network
        self._client.network_create_calls.append((name, kwargs))
        return network


class FakeDockerClient:
    def __init__(
        self,
        *,
        ping_error: Exception | None = None,
        create_error: Exception | None = None,
        start_error: Exception | None = None,
        stop_error: Exception | None = None,
        list_error: Exception | None = None,
        host_port: str | None = "18000",
    ) -> None:
        self.ping_error = ping_error
        self.create_error = create_error
        self.start_error = start_error
        self.stop_error = stop_error
        self.list_error = list_error
        self.host_port = host_port
        self.created: list[dict[str, Any]] = []
        self.containers_by_name: dict[str, FakeDockerContainer] = {}
        self.networks_by_name: dict[str, dict[str, Any]] = {}
        self.network_create_calls: list[tuple[str, dict[str, Any]]] = []
        self.containers = FakeDockerContainers(self)
        self.networks = FakeDockerNetworks(self)
        self.ping_count = 0

    def ping(self) -> None:
        self.ping_count += 1
        if self.ping_error is not None:
            raise self.ping_error


class FakeConnectionConfig:
    def __init__(self, **kwargs) -> None:
        self.kwargs = dict(kwargs)


class FakeOpenSandboxFile:
    def __init__(self, *, path: str, data: str) -> None:
        self.path = path
        self.data = data


class FakeOpenSandboxHost:
    def __init__(self, *, path: str) -> None:
        self.path = path


class FakeOpenSandboxVolume:
    def __init__(
        self,
        *,
        name: str,
        host: FakeOpenSandboxHost,
        mountPath: str,
        readOnly: bool = False,
    ) -> None:
        self.name = name
        self.host = host
        self.mount_path = mountPath
        self.read_only = readOnly


class FakeOpenSandboxNetworkRule:
    def __init__(self, *, action: str, target: str) -> None:
        self.action = action
        self.target = target


class FakeOpenSandboxNetworkPolicy:
    def __init__(self, **kwargs) -> None:
        self.kwargs = dict(kwargs)


class FakeOpenSandboxEndpoint:
    def __init__(self, endpoint: str, headers: dict[str, str] | None = None) -> None:
        self.endpoint = endpoint
        self.headers = headers or {}


class FakeOpenSandboxFiles:
    def __init__(self, sandbox: "FakeOpenSandbox") -> None:
        self.sandbox = sandbox
        self.written: list[FakeOpenSandboxFile] = []
        self.read_returns_bytes = False

    def write_files(self, files: list[FakeOpenSandboxFile]) -> None:
        self.written.extend(files)

    def read_file(self, path: str) -> str | bytes:
        for item in self.written:
            if item.path == path:
                if self.read_returns_bytes:
                    return item.data.encode("utf-8")
                return item.data
        raise FileNotFoundError(path)


class FakeOpenSandboxCommands:
    def __init__(self, sandbox: "FakeOpenSandbox") -> None:
        self.sandbox = sandbox
        self.runs: list[tuple[str, int | None]] = []
        self.exit_code = 0

    def run(self, command: str, timeout: int | None = None) -> object:
        self.runs.append((command, timeout))
        return type("FakeCommandResult", (), {"exit_code": self.exit_code, "stdout": "", "stderr": ""})()


class FakeOpenSandbox:
    created: list[dict[str, Any]] = []
    instances: dict[str, "FakeOpenSandbox"] = {}
    create_error: Exception | None = None
    endpoint_headers: dict[str, str] = {}
    connect_calls: list[dict[str, Any]] = []

    def __init__(
        self,
        *,
        sandbox_id: str,
        metadata: dict[str, str] | None = None,
        state: str = "RUNNING",
        endpoint: str | None = None,
    ) -> None:
        self.id = sandbox_id
        self.metadata = metadata or {}
        self.status = type("FakeSandboxStatus", (), {"state": state})()
        self.endpoint = endpoint or f"http://{sandbox_id}.opensandbox.test:18000"
        self.files = FakeOpenSandboxFiles(self)
        self.commands = FakeOpenSandboxCommands(self)
        self.killed = False
        self.closed = False

    @classmethod
    def reset(cls) -> None:
        cls.created = []
        cls.instances = {}
        cls.create_error = None
        cls.endpoint_headers = {}
        cls.connect_calls = []

    @classmethod
    def create(cls, **kwargs) -> "FakeOpenSandbox":
        if cls.create_error is not None:
            raise cls.create_error
        sandbox = cls(
            sandbox_id=f"osb-{kwargs['metadata']['ai-platform.run_id']}",
            metadata=kwargs["metadata"],
        )
        cls.created.append(kwargs)
        cls.instances[sandbox.id] = sandbox
        return sandbox

    @classmethod
    def connect(cls, sandbox_id: str, **kwargs) -> "FakeOpenSandbox":
        cls.connect_calls.append({"sandbox_id": sandbox_id, **kwargs})
        return cls.instances[sandbox_id]

    def get_endpoint(self, *, port: int, protocol: str = "http") -> FakeOpenSandboxEndpoint:
        return FakeOpenSandboxEndpoint(self.endpoint, headers=self.endpoint_headers)

    def get_info(self) -> object:
        return {
            "id": self.id,
            "metadata": dict(self.metadata),
            "status": {"state": self.status.state},
        }

    def kill(self) -> None:
        self.killed = True
        self.status.state = "TERMINATED"

    def close(self) -> None:
        self.closed = True


class FakeOpenSandboxManager:
    sandboxes: list[FakeOpenSandbox] = []
    killed: list[str] = []

    @classmethod
    def reset(cls) -> None:
        cls.sandboxes = []
        cls.killed = []

    @classmethod
    def create(cls, **_kwargs) -> "FakeOpenSandboxManager":
        return cls()

    async def __aenter__(self) -> "FakeOpenSandboxManager":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def list_sandboxes(self, **_kwargs) -> list[FakeOpenSandbox]:
        return list(self.sandboxes)

    async def kill_sandbox(self, sandbox_id: str) -> None:
        self.killed.append(sandbox_id)
        if sandbox_id in FakeOpenSandbox.instances:
            FakeOpenSandbox.instances[sandbox_id].kill()


class OpenSandboxSettings:
    sandbox_container_provider = "opensandbox"
    sandbox_container_start_timeout_seconds = 30
    sandbox_executor_health_timeout_seconds = 60
    sandbox_executor_image = "ai-platform:local"
    sandbox_callback_base_url = "http://host.docker.internal:8020"
    sandbox_egress_policy_enabled = True
    sandbox_callback_host_gateway = "host.docker.internal"
    opensandbox_domain = "opensandbox.local:8080"
    opensandbox_protocol = "http"
    opensandbox_api_key = "opensandbox-secret"
    opensandbox_use_server_proxy = False
    opensandbox_request_timeout_seconds = 30
    opensandbox_timeout_seconds = 1800
    opensandbox_executor_image = ""
    opensandbox_executor_entrypoint = "/app/docker-entrypoint.sh uvicorn"
    opensandbox_workspace_mount_enabled = True
    opensandbox_startup_io_probe_enabled = True
    opensandbox_allowed_egress_hosts = ""


def opensandbox_provider(*, health_probe=None):
    from app.runtime.sandbox.container_provider import OpenSandboxContainerProvider

    return OpenSandboxContainerProvider(
        sandbox_class=FakeOpenSandbox,
        sandbox_manager_class=FakeOpenSandboxManager,
        connection_config_class=FakeConnectionConfig,
        file_class=FakeOpenSandboxFile,
        host_class=FakeOpenSandboxHost,
        volume_class=FakeOpenSandboxVolume,
        network_policy_class=FakeOpenSandboxNetworkPolicy,
        network_rule_class=FakeOpenSandboxNetworkRule,
        health_probe=health_probe or (lambda executor_url, timeout_seconds: True),
    )


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


def test_create_container_provider_selects_opensandbox_and_still_rejects_unknown_provider(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    container_provider.reset_container_provider_cache()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())

    provider = container_provider.create_container_provider()

    assert isinstance(provider, container_provider.OpenSandboxContainerProvider)
    assert container_provider.create_container_provider("opensandbox") is provider
    with pytest.raises(ValueError, match="Unknown sandbox container provider"):
        container_provider.create_container_provider("opensandbox://token@internal")

    container_provider.reset_container_provider_cache()


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
async def test_docker_provider_list_permission_denied_error_is_sanitized():
    from app.runtime.sandbox.container_provider import DockerContainerProvider, DockerPermissionDeniedError

    fake = FakeDockerClient(list_error=RuntimeError(f"permission denied: {workspace().workspace_host_path}"))
    provider = DockerContainerProvider(docker_client_factory=lambda: fake)

    with pytest.raises(DockerPermissionDeniedError) as exc_info:
        await provider.list_runtime_containers({"tenant_id": "tenant-a"})

    assert exc_info.value.error_code == "docker_permission_denied"
    assert workspace().workspace_host_path not in str(exc_info.value)
    assert str(exc_info.value) == "Docker permission denied"


@pytest.mark.asyncio
async def test_docker_provider_list_daemon_error_is_sanitized():
    from app.runtime.sandbox.container_provider import DockerContainerProvider, DockerUnavailableError

    fake = FakeDockerClient(list_error=RuntimeError("cannot connect to /var/run/docker.sock"))
    provider = DockerContainerProvider(docker_client_factory=lambda: fake)

    with pytest.raises(DockerUnavailableError) as exc_info:
        await provider.list_runtime_containers({"tenant_id": "tenant-a"})

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
async def test_docker_provider_forwards_executor_sdk_environment(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")

    fake = FakeDockerClient()
    monkeypatch.setattr(
        container_provider,
        "get_settings",
        lambda: type(
            "StubSettings",
            (),
            {
                "sandbox_container_start_timeout_seconds": 30,
                "sandbox_executor_health_timeout_seconds": 60,
                "sandbox_executor_image": "ai-platform-executor:dev",
                "sandbox_executor_published_host": "127.0.0.1",
                "sandbox_callback_base_url": "http://host.docker.internal:8020",
                "sandbox_egress_policy_enabled": False,
                "sandbox_egress_network_name": "ai-platform-sandbox-egress",
                "sandbox_callback_host_gateway": "host.docker.internal",
                "openai_base_url": "http://new-api.test/v1",
                "openai_api_key": "test-newapi-token",
                "openai_model": "deepseek-v4-flash",
                "anthropic_base_url": "http://new-api.test",
                "anthropic_auth_token": "test-anthropic-token",
                "anthropic_model": "deepseek-v4-flash",
                "claude_agent_model": "deepseek-v4-flash",
                "default_model_id": "deepseek-v4-flash",
                "model_catalog_json": "[{\"id\":\"deepseek-v4-flash\"}]",
                "claude_agent_sdk_enabled": True,
                "claude_agent_sdk_timeout_seconds": 120,
                "claude_agent_sdk_max_turns": 128,
                "claude_agent_sdk_effort": "xhigh",
                "claude_agent_sdk_max_thinking_tokens": 16384,
                "claude_agent_permission_mode": "bypassPermissions",
                "claude_agent_allowed_tools": "Read,Glob,LS,Bash",
                "claude_agent_disallowed_tools": "",
                "claude_agent_workspace_root": "/tmp/ai-platform-agent-workspaces",
                "claude_agent_sdk_skills": "general-chat,qa-file-reviewer",
                "platform_skills_root": "skills",
                "skill_staging_subdir": ".claude/skills",
                "public_skill_file_overlay_max_bytes": 262144,
            },
        )(),
    )
    provider = container_provider.DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )

    await provider.create_or_reuse(request(), workspace())

    environment = fake.created[0]["environment"]
    assert environment["CLAUDE_AGENT_SDK_ENABLED"] == "true"
    assert environment["ANTHROPIC_BASE_URL"] == "http://new-api.test"
    assert environment["ANTHROPIC_AUTH_TOKEN"] == "test-anthropic-token"
    assert environment["OPENAI_API_KEY"] == "test-newapi-token"
    assert environment["CLAUDE_AGENT_PERMISSION_MODE"] == "bypassPermissions"
    assert environment["CLAUDE_AGENT_ALLOWED_TOOLS"] == "Read,Glob,LS,Bash"
    assert environment["CLAUDE_AGENT_WORKSPACE_ROOT"] == "/workspace"
    assert environment["CLAUDE_AGENT_SDK_SKILLS"] == "general-chat,qa-file-reviewer"


@pytest.mark.asyncio
async def test_docker_provider_records_cold_start_and_healthcheck_latency():
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    class Clock:
        def __init__(self):
            self.values = iter([10.0, 10.07, 10.09, 10.11])
            self.last = 10.11

        def monotonic(self):
            try:
                self.last = next(self.values)
            except StopIteration:
                pass
            return self.last

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
        monotonic=Clock().monotonic,
    )

    lease = await provider.create_or_reuse(request(), workspace())

    assert lease.timings == {
        "sandbox_container_start_latency_ms": 70,
        "sandbox_container_cold_start_latency_ms": 70,
        "sandbox_healthcheck_latency_ms": 20,
    }


@pytest.mark.asyncio
async def test_opensandbox_provider_maps_lease_and_platform_controls(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())

    provider = opensandbox_provider()
    lease = await provider.create_or_reuse(
        request(resource_limits={"memory_mb": 512, "cpu_count": 2, "pids_limit": 64}),
        workspace(),
    )

    created = FakeOpenSandbox.created[0]
    assert created["image"] == "ai-platform:local"
    assert created["entrypoint"] == ["/app/docker-entrypoint.sh", "uvicorn"]
    assert created["metadata"]["ai-platform.owner"] == "sandbox-runtime"
    assert created["metadata"]["ai-platform.tenant_id"] == "tenant-a"
    assert created["metadata"]["ai-platform.run_id"] == "run-a"
    assert created["env"]["APP_MODULE"] == "app.runtime.sandbox.executor_app:create_executor_app"
    assert created["env"]["AI_PLATFORM_RUN_ID"] == "run-a"
    assert created["resource"] == {"cpu": "2", "memory": "512Mi", "pids": "64"}
    assert created["volumes"][0].host.path == workspace().workspace_host_path
    assert created["volumes"][0].mount_path == "/workspace"
    assert created["network_policy"].kwargs["defaultAction"] == "deny"
    assert created["network_policy"].kwargs["egress"][0].target == "host.docker.internal"

    sandbox = FakeOpenSandbox.instances["osb-run-a"]
    assert sandbox.files.written[0].path == "/workspace/.ai-platform-opensandbox-lease.json"
    assert '"run_id": "run-a"' in sandbox.files.written[0].data
    assert sandbox.commands.runs[0][0] == "test -f /workspace/.ai-platform-opensandbox-lease.json"

    assert lease.container_id == "osb-run-a"
    assert lease.container_name == "opensandbox-run-a"
    assert lease.provider == "opensandbox"
    assert lease.executor_url == "http://osb-run-a.opensandbox.test:18000"
    assert lease.workspace_host_path == workspace().workspace_host_path
    assert lease.workspace_container_path == "/workspace"
    assert lease.labels["ai-platform.provider_backend"] == "opensandbox"
    assert lease.labels["ai-platform.egress.policy"] == "opensandbox-network-policy"


@pytest.mark.asyncio
async def test_opensandbox_provider_accepts_byte_readback_from_file_probe(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())

    original_create = FakeOpenSandbox.create

    def create_with_byte_readback(**kwargs):
        sandbox = original_create(**kwargs)
        sandbox.files.read_returns_bytes = True
        return sandbox

    monkeypatch.setattr(FakeOpenSandbox, "create", create_with_byte_readback)
    provider = opensandbox_provider()

    lease = await provider.create_or_reuse(request(), workspace())

    assert lease.provider == "opensandbox"
    assert FakeOpenSandbox.instances["osb-run-a"].files.read_returns_bytes is True


@pytest.mark.asyncio
async def test_opensandbox_provider_fails_when_startup_command_probe_fails(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())

    original_create = FakeOpenSandbox.create

    def create_with_failing_command(**kwargs):
        sandbox = original_create(**kwargs)
        sandbox.commands.exit_code = 127
        return sandbox

    monkeypatch.setattr(FakeOpenSandbox, "create", create_with_failing_command)
    provider = opensandbox_provider()

    with pytest.raises(container_provider.ContainerStartFailedError) as exc_info:
        await provider.create_or_reuse(request(), workspace())

    assert str(exc_info.value) == "OpenSandbox command execution failed"
    sandbox = FakeOpenSandbox.instances["osb-run-a"]
    assert sandbox.killed is True
    assert sandbox.closed is True


@pytest.mark.asyncio
async def test_opensandbox_provider_cleans_up_when_endpoint_probe_fails(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())

    original_create = FakeOpenSandbox.create

    def create_without_endpoint(**kwargs):
        sandbox = original_create(**kwargs)
        sandbox.endpoint = ""
        return sandbox

    monkeypatch.setattr(FakeOpenSandbox, "create", create_without_endpoint)
    provider = opensandbox_provider()

    with pytest.raises(container_provider.ContainerStartFailedError) as exc_info:
        await provider.create_or_reuse(request(), workspace())

    assert str(exc_info.value) == "OpenSandbox executor endpoint unavailable"
    sandbox = FakeOpenSandbox.instances["osb-run-a"]
    assert sandbox.killed is True
    assert sandbox.closed is True


@pytest.mark.asyncio
async def test_opensandbox_provider_cleans_up_when_created_sandbox_has_no_id(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())

    original_create = FakeOpenSandbox.create

    def create_without_id(**kwargs):
        sandbox = original_create(**kwargs)
        sandbox.id = ""
        return sandbox

    monkeypatch.setattr(FakeOpenSandbox, "create", create_without_id)
    provider = opensandbox_provider()

    with pytest.raises(container_provider.ContainerStartFailedError) as exc_info:
        await provider.create_or_reuse(request(), workspace())

    assert str(exc_info.value) == "OpenSandbox sandbox start failed"
    sandbox = FakeOpenSandbox.instances["osb-run-a"]
    assert sandbox.killed is True
    assert sandbox.closed is True


@pytest.mark.asyncio
async def test_opensandbox_provider_cleans_up_created_sandbox_on_cancel(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())

    provider = opensandbox_provider()

    async def cancel_after_create(*_args, **_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(provider, "_write_and_verify_sentinel", cancel_after_create)

    with pytest.raises(asyncio.CancelledError):
        await provider.create_or_reuse(request(), workspace())

    sandbox = FakeOpenSandbox.instances["osb-run-a"]
    assert sandbox.killed is True
    assert sandbox.closed is True


@pytest.mark.asyncio
async def test_opensandbox_provider_stop_and_cleanup_are_scope_bounded(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())

    provider = opensandbox_provider()
    lease = await provider.create_or_reuse(request(), workspace())
    stop_result = await provider.stop(lease, reason="dispatch_completed")

    assert stop_result.status == "stopped"
    assert FakeOpenSandbox.instances["osb-run-a"].killed is True
    assert FakeOpenSandbox.instances["osb-run-a"].closed is True

    same_tenant_failed = FakeOpenSandbox(
        sandbox_id="osb-orphan-a",
        metadata={
            "ai-platform.owner": "sandbox-runtime",
            "ai-platform.tenant_id": "tenant-a",
            "ai-platform.workspace_id": "workspace-a",
            "ai-platform.user_id": "user-a",
            "ai-platform.session_id": "session-a",
            "ai-platform.run_id": "run-orphan-a",
            "ai-platform.sandbox_mode": "ephemeral",
            "ai-platform.browser_enabled": "false",
        },
        state="FAILED",
    )
    same_tenant_running = FakeOpenSandbox(
        sandbox_id="osb-running-a",
        metadata={**same_tenant_failed.metadata, "ai-platform.run_id": "run-running-a"},
        state="RUNNING",
    )
    foreign_failed = FakeOpenSandbox(
        sandbox_id="osb-orphan-b",
        metadata={**same_tenant_failed.metadata, "ai-platform.tenant_id": "tenant-b", "ai-platform.run_id": "run-orphan-b"},
        state="FAILED",
    )
    FakeOpenSandbox.instances.update(
        {
            same_tenant_failed.id: same_tenant_failed,
            same_tenant_running.id: same_tenant_running,
            foreign_failed.id: foreign_failed,
        }
    )
    FakeOpenSandboxManager.sandboxes = [same_tenant_failed, same_tenant_running, foreign_failed]

    results = await provider.cleanup_orphan_containers({"tenant_id": "tenant-a"}, reason="admin_runtime")

    assert [item.container_id for item in results] == ["osb-orphan-a"]
    assert FakeOpenSandboxManager.killed == ["osb-orphan-a"]
    assert same_tenant_running.killed is False
    assert foreign_failed.killed is False


@pytest.mark.asyncio
async def test_opensandbox_provider_stop_rejects_scope_mismatch_without_kill(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())

    provider = opensandbox_provider()
    lease = await provider.create_or_reuse(request(), workspace())
    sandbox = FakeOpenSandbox.instances["osb-run-a"]
    sandbox.metadata = {**sandbox.metadata, "ai-platform.tenant_id": "tenant-b"}

    stop_result = await provider.stop(lease, reason="expired")

    assert stop_result.status == "not_found"
    assert sandbox.killed is False
    assert sandbox.closed is False


@pytest.mark.asyncio
async def test_opensandbox_provider_stop_reads_scope_from_sdk_get_info(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())

    class RealShapeOpenSandbox(FakeOpenSandbox):
        def __getattribute__(self, name):
            if name == "metadata":
                raise AttributeError(name)
            return super().__getattribute__(name)

        def get_info(self) -> object:
            return {
                "id": self.id,
                "metadata": object.__getattribute__(self, "_metadata"),
                "status": {"state": self.status.state},
            }

        @classmethod
        def create(cls, **kwargs) -> "RealShapeOpenSandbox":
            sandbox = cls(
                sandbox_id=f"osb-{kwargs['metadata']['ai-platform.run_id']}",
                metadata=kwargs["metadata"],
            )
            object.__setattr__(sandbox, "_metadata", kwargs["metadata"])
            FakeOpenSandbox.created.append(kwargs)
            FakeOpenSandbox.instances[sandbox.id] = sandbox
            return sandbox

        @classmethod
        def connect(cls, sandbox_id: str, **_kwargs) -> "RealShapeOpenSandbox":
            return FakeOpenSandbox.instances[sandbox_id]

    provider = opensandbox_provider()
    provider._sandbox_class = RealShapeOpenSandbox

    lease = await provider.create_or_reuse(request(), workspace())
    stop_result = await provider.stop(lease, reason="dispatch_completed")

    assert stop_result.status == "stopped"
    sandbox = FakeOpenSandbox.instances["osb-run-a"]
    assert sandbox.killed is True
    assert sandbox.closed is True


@pytest.mark.asyncio
async def test_opensandbox_provider_stop_reconnects_without_health_check(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())

    provider = opensandbox_provider()
    lease = await provider.create_or_reuse(request(), workspace())
    provider._sandboxes.clear()

    stop_result = await provider.stop(lease, reason="expired")

    assert stop_result.status == "stopped"
    assert FakeOpenSandbox.connect_calls
    assert FakeOpenSandbox.connect_calls[0]["sandbox_id"] == "osb-run-a"
    assert FakeOpenSandbox.connect_calls[0]["skip_health_check"] is True
    assert FakeOpenSandbox.instances["osb-run-a"].killed is True


@pytest.mark.asyncio
async def test_opensandbox_provider_stop_treats_close_failure_as_stopped_after_kill(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())

    provider = opensandbox_provider()
    lease = await provider.create_or_reuse(request(), workspace())
    sandbox = FakeOpenSandbox.instances["osb-run-a"]

    def fail_close():
        sandbox.closed = True
        raise RuntimeError("local close failed")

    sandbox.close = fail_close

    stop_result = await provider.stop(lease, reason="dispatch_completed")

    assert stop_result.status == "stopped"
    assert sandbox.killed is True
    assert sandbox.closed is True


@pytest.mark.asyncio
async def test_opensandbox_provider_errors_do_not_leak_secret_or_host_path(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())
    leaked = f"{workspace().workspace_host_path} opensandbox-secret"
    FakeOpenSandbox.create_error = RuntimeError(f"cannot mount {leaked}")
    provider = opensandbox_provider()

    with pytest.raises(container_provider.ContainerStartFailedError) as exc_info:
        await provider.create_or_reuse(request(), workspace())

    assert str(exc_info.value) == "OpenSandbox sandbox start failed"
    assert "opensandbox-secret" not in str(exc_info.value)
    assert workspace().workspace_host_path not in str(exc_info.value)


@pytest.mark.asyncio
async def test_opensandbox_provider_keeps_endpoint_headers_private_and_uses_them_for_health_probe(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())
    FakeOpenSandbox.endpoint_headers = {"OPENSANDBOX-EGRESS-AUTH": "opensandbox-secret"}
    health_calls = []

    def health_probe(executor_url, timeout_seconds, executor_headers=None):
        health_calls.append((executor_url, timeout_seconds, executor_headers))
        return True

    provider = opensandbox_provider(health_probe=health_probe)

    lease = await provider.create_or_reuse(request(), workspace())

    assert lease.executor_url == "http://osb-run-a.opensandbox.test:18000"
    assert lease.executor_headers["OPENSANDBOX-EGRESS-AUTH"] == "opensandbox-secret"
    assert lease.executor_headers["X-AI-Platform-Executor-Credential"]
    assert health_calls == [
        (
            "http://osb-run-a.opensandbox.test:18000",
            OpenSandboxSettings.sandbox_executor_health_timeout_seconds,
            {"OPENSANDBOX-EGRESS-AUTH": "opensandbox-secret"},
        )
    ]
    assert "opensandbox-secret" not in str(lease.model_dump())


@pytest.mark.asyncio
async def test_opensandbox_provider_adds_protocol_to_scheme_less_executor_endpoint(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())

    original_create = FakeOpenSandbox.create

    def create_with_scheme_less_endpoint(**kwargs):
        sandbox = original_create(**kwargs)
        sandbox.endpoint = "opensandbox-gateway.test:46471/proxy/18000"
        return sandbox

    monkeypatch.setattr(FakeOpenSandbox, "create", create_with_scheme_less_endpoint)
    provider = opensandbox_provider()

    lease = await provider.create_or_reuse(request(), workspace())

    assert lease.executor_url == "http://opensandbox-gateway.test:46471/proxy/18000"


def test_opensandbox_sdk_symbols_match_adapter_contract():
    import opensandbox  # noqa: F401

    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")

    symbols = container_provider._load_opensandbox_symbols()

    assert {
        "sandbox_class",
        "sandbox_manager_class",
        "connection_config_class",
        "file_class",
        "host_class",
        "volume_class",
        "network_policy_class",
        "network_rule_class",
        "sandbox_filter_class",
    } <= symbols.keys()
    assert "metadata" in inspect.signature(symbols["sandbox_filter_class"]).parameters
    assert "image" in inspect.signature(symbols["sandbox_class"].create).parameters
    assert "metadata" in inspect.signature(symbols["sandbox_class"].create).parameters
    assert "network_policy" in inspect.signature(symbols["sandbox_class"].create).parameters
    assert "volumes" in inspect.signature(symbols["sandbox_class"].create).parameters
    assert "port" in inspect.signature(symbols["sandbox_class"].get_endpoint).parameters
    assert "mountPath" in inspect.signature(symbols["volume_class"]).parameters
    assert "readOnly" in inspect.signature(symbols["volume_class"]).parameters
    assert "defaultAction" in inspect.signature(symbols["network_policy_class"]).parameters
    assert "egress" in inspect.signature(symbols["network_policy_class"]).parameters

    host = symbols["host_class"](path="/tmp/workspace")
    symbols["volume_class"](name="ai-platform-workspace", host=host, mountPath="/workspace", readOnly=False)
    rule = symbols["network_rule_class"](action="allow", target="host.docker.internal")
    symbols["network_policy_class"](defaultAction="deny", egress=[rule])


@pytest.mark.asyncio
async def test_docker_provider_does_not_reuse_same_run_container_with_mismatched_scope_labels():
    from app.runtime.sandbox.container_provider import ContainerStartFailedError, DockerContainerProvider

    fake = FakeDockerClient()
    mismatched = FakeDockerContainer(
        image="ai-platform-executor:dev",
        name="executor-exec-run-a",
        detach=True,
        labels={
            "ai-platform.owner": "sandbox-runtime",
            "ai-platform.tenant_id": "tenant-b",
            "ai-platform.workspace_id": "workspace-b",
            "ai-platform.user_id": "user-b",
            "ai-platform.session_id": "session-b",
            "ai-platform.run_id": "run-a",
            "ai-platform.sandbox_mode": "persistent",
            "ai-platform.browser_enabled": "true",
        },
        volumes={},
        environment={},
        ports={},
    )
    mismatched.status = "running"
    fake.containers_by_name[mismatched.name] = mismatched
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )

    with pytest.raises(ContainerStartFailedError):
        await provider.create_or_reuse(request(), workspace())

    assert len(fake.created) == 0
    assert mismatched.removed is False


@pytest.mark.asyncio
async def test_docker_provider_cached_lease_revalidates_container_scope_labels():
    from app.runtime.sandbox.container_provider import ContainerStartFailedError, DockerContainerProvider

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )
    await provider.create_or_reuse(request(), workspace())
    fake.containers_by_name["executor-exec-run-a"].labels.update(
        {
            "ai-platform.tenant_id": "tenant-b",
            "ai-platform.workspace_id": "workspace-b",
            "ai-platform.user_id": "user-b",
            "ai-platform.session_id": "session-b",
        }
    )
    fake.containers_by_name["executor-exec-run-a"].attrs["Config"]["Labels"] = fake.containers_by_name[
        "executor-exec-run-a"
    ].labels

    with pytest.raises(ContainerStartFailedError):
        await provider.create_or_reuse(request(), workspace())

    assert len(fake.created) == 1


@pytest.mark.asyncio
async def test_docker_provider_rejects_reuse_when_workspace_owner_user_mismatches(monkeypatch):
    from app.runtime.sandbox import container_provider
    from app.runtime.sandbox.container_provider import ContainerStartFailedError, DockerContainerProvider

    class FakePath:
        def __init__(self, value: str) -> None:
            self.value = value

        def stat(self):
            class StatResult:
                st_uid = 1003
                st_gid = 1003

            return StatResult()

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )
    monkeypatch.setattr(container_provider, "Path", FakePath)
    monkeypatch.setattr(container_provider.os, "name", "posix")
    await provider.create_or_reuse(request(), workspace())
    fake.containers_by_name["executor-exec-run-a"].attrs["Config"]["User"] = ""

    with pytest.raises(ContainerStartFailedError):
        await provider.create_or_reuse(request(), workspace())


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
async def test_docker_provider_uses_platform_egress_network_without_disabling_published_executor_port(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")

    fake = FakeDockerClient()
    monkeypatch.setattr(
        container_provider,
        "get_settings",
        lambda: type(
            "StubSettings",
            (),
            {
                "sandbox_container_start_timeout_seconds": 30,
                "sandbox_executor_health_timeout_seconds": 60,
                "sandbox_executor_image": "ai-platform-executor:dev",
                "sandbox_executor_published_host": "127.0.0.1",
                "sandbox_callback_base_url": "http://host.docker.internal:8000",
                "sandbox_egress_policy_enabled": True,
                "sandbox_egress_network_name": "ai-platform-sandbox-egress",
                "sandbox_callback_host_gateway": "host.docker.internal",
            },
        )(),
    )
    provider = container_provider.DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )

    await provider.create_or_reuse(request(), workspace())

    created = fake.created[0]
    assert fake.network_create_calls == [
        (
            "ai-platform-sandbox-egress",
            {
                "driver": "bridge",
                "options": {"com.docker.network.bridge.enable_ip_masquerade": "false"},
            },
        )
    ]
    assert created["network"] == "ai-platform-sandbox-egress"
    assert created["extra_hosts"] == {"host.docker.internal": "host-gateway"}
    assert created["ports"] == {"18000/tcp": ("127.0.0.1", None)}
    assert created["labels"]["ai-platform.egress.policy"] == "default-deny-no-masq"
    assert created["labels"]["ai-platform.egress.network"] == "ai-platform-sandbox-egress"
    assert created["labels"]["ai-platform.egress.callback_host"] == "host.docker.internal"
    assert "network_disabled" not in created


@pytest.mark.asyncio
async def test_docker_provider_rejects_existing_egress_network_when_masquerade_is_enabled(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")

    fake = FakeDockerClient()
    fake.networks_by_name["ai-platform-sandbox-egress"] = {
        "name": "ai-platform-sandbox-egress",
        "options": {"com.docker.network.bridge.enable_ip_masquerade": "true"},
    }
    monkeypatch.setattr(
        container_provider,
        "get_settings",
        lambda: type(
            "StubSettings",
            (),
            {
                "sandbox_container_start_timeout_seconds": 30,
                "sandbox_executor_health_timeout_seconds": 60,
                "sandbox_executor_image": "ai-platform-executor:dev",
                "sandbox_executor_published_host": "127.0.0.1",
                "sandbox_callback_base_url": "http://host.docker.internal:8000",
                "sandbox_egress_policy_enabled": True,
                "sandbox_egress_network_name": "ai-platform-sandbox-egress",
                "sandbox_callback_host_gateway": "host.docker.internal",
            },
        )(),
    )
    provider = container_provider.DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )

    with pytest.raises(container_provider.ContainerStartFailedError):
        await provider.create_or_reuse(request(), workspace())

    assert fake.created == []
    assert fake.network_create_calls == []


@pytest.mark.asyncio
async def test_docker_provider_rejects_reused_container_missing_required_egress_labels(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")

    fake = FakeDockerClient()
    fake.networks_by_name["ai-platform-sandbox-egress"] = {
        "name": "ai-platform-sandbox-egress",
        "options": {"com.docker.network.bridge.enable_ip_masquerade": "false"},
    }
    monkeypatch.setattr(
        container_provider,
        "get_settings",
        lambda: type(
            "StubSettings",
            (),
            {
                "sandbox_container_start_timeout_seconds": 30,
                "sandbox_executor_health_timeout_seconds": 60,
                "sandbox_executor_image": "ai-platform-executor:dev",
                "sandbox_executor_published_host": "127.0.0.1",
                "sandbox_callback_base_url": "http://host.docker.internal:8000",
                "sandbox_egress_policy_enabled": True,
                "sandbox_egress_network_name": "ai-platform-sandbox-egress",
                "sandbox_callback_host_gateway": "host.docker.internal",
            },
        )(),
    )
    stale_labels = {
        "ai-platform.owner": "sandbox-runtime",
        "ai-platform.tenant_id": "tenant-a",
        "ai-platform.workspace_id": "workspace-a",
        "ai-platform.user_id": "user-a",
        "ai-platform.session_id": "session-a",
        "ai-platform.run_id": "run-a",
        "ai-platform.sandbox_mode": "ephemeral",
        "ai-platform.browser_enabled": "false",
    }
    stale = FakeDockerContainer(
        image="ai-platform-executor:dev",
        name="executor-exec-run-a",
        detach=True,
        labels=stale_labels,
        volumes={},
        environment={},
        ports={"18000/tcp": None},
    )
    stale.status = "running"
    fake.containers_by_name[stale.name] = stale
    provider = container_provider.DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )

    with pytest.raises(container_provider.ContainerStartFailedError):
        await provider.create_or_reuse(request(), workspace())

    assert fake.created == []


@pytest.mark.asyncio
async def test_docker_provider_cached_lease_keeps_required_egress_labels(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")

    fake = FakeDockerClient()
    monkeypatch.setattr(
        container_provider,
        "get_settings",
        lambda: type(
            "StubSettings",
            (),
            {
                "sandbox_container_start_timeout_seconds": 30,
                "sandbox_executor_health_timeout_seconds": 60,
                "sandbox_executor_image": "ai-platform-executor:dev",
                "sandbox_executor_published_host": "127.0.0.1",
                "sandbox_callback_base_url": "http://host.docker.internal:8000",
                "sandbox_egress_policy_enabled": True,
                "sandbox_egress_network_name": "ai-platform-sandbox-egress",
                "sandbox_callback_host_gateway": "host.docker.internal",
            },
        )(),
    )
    provider = container_provider.DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )

    first_lease = await provider.create_or_reuse(request(), workspace())

    assert first_lease.labels["ai-platform.egress.policy"] == "default-deny-no-masq"
    assert first_lease.labels["ai-platform.egress.network"] == "ai-platform-sandbox-egress"
    assert first_lease.labels["ai-platform.egress.callback_host"] == "host.docker.internal"
    reused_lease = await provider.create_or_reuse(request(), workspace())

    assert reused_lease.container_id == first_lease.container_id
    assert len(fake.created) == 1


@pytest.mark.asyncio
async def test_docker_provider_sets_default_security_options_without_docker_socket_mount():
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )
    leased_workspace = workspace()

    await provider.create_or_reuse(request(), leased_workspace)

    created = fake.created[0]
    assert created["privileged"] is False
    assert created["security_opt"] == ["no-new-privileges:true"]
    assert created["cap_drop"] == ["ALL"]
    assert created["read_only"] is True
    assert created["tmpfs"] == {"/tmp": "rw,noexec,nosuid,size=64m"}
    assert created["ports"] == {"18000/tcp": ("127.0.0.1", None)}
    assert created["volumes"] == {
        leased_workspace.workspace_host_path: {
            "bind": "/workspace",
            "mode": "rw",
        }
    }
    serialized = str(created).lower()
    assert "/var/run/docker.sock" not in serialized


@pytest.mark.asyncio
async def test_docker_provider_runs_executor_as_workspace_owner_when_host_path_is_local(monkeypatch):
    from app.runtime.sandbox import container_provider
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    class FakePath:
        def __init__(self, value: str) -> None:
            self.value = value

        def stat(self):
            class StatResult:
                st_uid = 1003
                st_gid = 1003

            return StatResult()

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )
    monkeypatch.setattr(container_provider, "Path", FakePath)
    monkeypatch.setattr(container_provider.os, "name", "posix")

    await provider.create_or_reuse(request(), workspace())

    created = fake.created[0]
    assert created["user"] == "1003:1003"
    assert created["privileged"] is False
    assert created["security_opt"] == ["no-new-privileges:true"]
    assert created["cap_drop"] == ["ALL"]
    assert created["read_only"] is True
    assert created["tmpfs"] == {"/tmp": "rw,noexec,nosuid,size=64m"}
    assert created["ports"] == {"18000/tcp": ("127.0.0.1", None)}
    assert "/var/run/docker.sock" not in str(created).lower()


@pytest.mark.asyncio
async def test_docker_provider_omits_workspace_owner_user_when_stat_is_unavailable(monkeypatch):
    from app.runtime.sandbox import container_provider
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    class FakePath:
        def __init__(self, value: str) -> None:
            self.value = value

        def stat(self):
            raise OSError("not available")

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )
    monkeypatch.setattr(container_provider, "Path", FakePath)
    monkeypatch.setattr(container_provider.os, "name", "posix")

    await provider.create_or_reuse(request(), workspace())

    assert "user" not in fake.created[0]


@pytest.mark.asyncio
async def test_docker_provider_omits_workspace_owner_user_on_windows(monkeypatch):
    from app.runtime.sandbox import container_provider
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    class FakePath:
        def __init__(self, value: str) -> None:
            self.value = value

        def stat(self):
            class StatResult:
                st_uid = 1003
                st_gid = 1003

            return StatResult()

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )
    monkeypatch.setattr(container_provider, "Path", FakePath)
    monkeypatch.setattr(container_provider.os, "name", "nt")

    await provider.create_or_reuse(request(), workspace())

    assert "user" not in fake.created[0]


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
async def test_docker_provider_stop_refuses_container_without_matching_sandbox_labels():
    from app.runtime.sandbox.container_provider import DockerContainerProvider
    from app.runtime.sandbox.contracts import ContainerLease

    fake = FakeDockerClient()
    api_container = FakeDockerContainer(
        image="ai-platform:local",
        name="ai-platform-api",
        detach=True,
        labels={"com.docker.compose.service": "api"},
        volumes={},
        environment={},
        ports={},
    )
    api_container.status = "running"
    fake.containers_by_name[api_container.name] = api_container
    provider = DockerContainerProvider(docker_client_factory=lambda: fake)
    lease = ContainerLease(
        container_id="exec-run-a",
        container_name="ai-platform-api",
        provider="docker",
        executor_url="http://sandbox-runtime.invalid",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        sandbox_mode="ephemeral",
        browser_enabled=False,
        workspace_host_path="",
    )

    result = await provider.stop(lease, reason="cancelled")

    assert result.status == "not_found"
    assert api_container.stopped is False
    assert api_container.removed is False


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
async def test_docker_provider_uses_loopback_executor_url_and_private_auth_header(monkeypatch):
    from app.runtime.sandbox import container_provider
    from app.runtime.sandbox.contracts import EXECUTOR_AUTH_HEADER
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    settings = type(
        "StubSettings",
        (),
        {
            "sandbox_executor_published_host": "127.0.0.1",
            "sandbox_executor_image": "ai-platform-executor:dev",
            "sandbox_callback_base_url": "http://platform.test",
            "sandbox_callback_host_gateway": "host.docker.internal",
            "sandbox_egress_policy_enabled": False,
            "sandbox_container_start_timeout_seconds": 5,
            "sandbox_executor_health_timeout_seconds": 5,
        },
    )()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)

    fake = FakeDockerClient(host_port="43123")
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )

    lease = await provider.create_or_reuse(request(), workspace())

    created = fake.created[0]
    assert lease.executor_url == "http://127.0.0.1:43123"
    assert created["ports"] == {"18000/tcp": ("127.0.0.1", None)}
    assert created["environment"]["AI_PLATFORM_EXECUTOR_AUTH_TOKEN"]
    assert lease.executor_headers[EXECUTOR_AUTH_HEADER] == created["environment"]["AI_PLATFORM_EXECUTOR_AUTH_TOKEN"]
    assert lease.executor_headers[EXECUTOR_AUTH_HEADER] not in str(lease.platform_labels())
    assert "AI_PLATFORM_EXECUTOR_AUTH_TOKEN" not in str(lease.platform_labels())


@pytest.mark.asyncio
async def test_docker_provider_rejects_untrusted_public_callback_base_url(monkeypatch):
    from app.runtime.sandbox import container_provider
    from app.runtime.sandbox.container_provider import ContainerStartFailedError, DockerContainerProvider

    settings = type(
        "StubSettings",
        (),
        {
            "sandbox_executor_published_host": "127.0.0.1",
            "sandbox_executor_image": "ai-platform-executor:dev",
            "sandbox_callback_base_url": "http://example.com",
            "sandbox_callback_host_gateway": "",
            "sandbox_egress_policy_enabled": False,
            "sandbox_container_start_timeout_seconds": 5,
            "sandbox_executor_health_timeout_seconds": 5,
        },
    )()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )

    with pytest.raises(ContainerStartFailedError):
        await provider.create_or_reuse(request(), workspace())


@pytest.mark.asyncio
async def test_docker_provider_rejects_link_local_callback_base_url(monkeypatch):
    from app.runtime.sandbox import container_provider
    from app.runtime.sandbox.container_provider import ContainerStartFailedError, DockerContainerProvider

    settings = type(
        "StubSettings",
        (),
        {
            "sandbox_executor_published_host": "127.0.0.1",
            "sandbox_executor_image": "ai-platform-executor:dev",
            "sandbox_callback_base_url": "http://169.254.169.254",
            "sandbox_callback_host_gateway": "",
            "sandbox_egress_policy_enabled": False,
            "sandbox_container_start_timeout_seconds": 5,
            "sandbox_executor_health_timeout_seconds": 5,
        },
    )()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )

    with pytest.raises(ContainerStartFailedError):
        await provider.create_or_reuse(request(), workspace())


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
async def test_docker_provider_stop_maps_sdk_not_found_to_not_found():
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    class NotFoundContainers(FakeDockerContainers):
        def get(self, name: str) -> FakeDockerContainer:
            raise RuntimeError("404 Client Error for http://docker/containers/executor-exec-run-a/json: Not Found")

    fake = FakeDockerClient()
    provider = DockerContainerProvider(docker_client_factory=lambda: fake, health_probe=lambda executor_url, timeout_seconds: True)
    lease = await provider.create_or_reuse(request(), workspace())
    fake.containers = NotFoundContainers(fake)

    result = await provider.stop(lease, reason="cancel_requested")

    assert result.status == "not_found"
    assert result.container_id == "exec-run-a"


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
