import asyncio
import inspect
import importlib
import socket
import threading
import time
from typing import Any

import httpx
import pytest

from app.runtime.sandbox.contracts import SandboxRuntimeRequest, WorkspaceLease


@pytest.fixture(autouse=True)
def fixed_runtime_identity_test_seams(monkeypatch, request):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    stat_result = type(
        "RuntimeWorkspaceStat",
        (),
        {"st_uid": 10001, "st_gid": 10001, "st_mode": 0o40700},
    )()
    monkeypatch.setattr(container_provider, "_workspace_owner_stat", lambda _path: stat_result, raising=False)
    if request.node.name != "test_default_executor_probes_connect_to_pinned_ip_without_transmitting_private_metadata":
        monkeypatch.setattr(
            container_provider,
            "default_executor_identity_probe",
            lambda executor_url, timeout_seconds, executor_headers: {"uid": 10001, "gid": 10001},
            raising=False,
        )


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


def workspace(**overrides) -> WorkspaceLease:
    values = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "host_root": "/runtime/tenants/tenant-a/workspaces/workspace-a/users/user-a/sessions/session-a/runs/run-a",
        "workspace_host_path": "/runtime/tenants/tenant-a/workspaces/workspace-a/users/user-a/sessions/session-a/runs/run-a/workspace",
        "workspace_container_path": "/workspace",
        "inputs_host_path": "/runtime/tenants/tenant-a/workspaces/workspace-a/users/user-a/sessions/session-a/runs/run-a/inputs",
        "logs_host_path": "/runtime/tenants/tenant-a/workspaces/workspace-a/users/user-a/sessions/session-a/runs/run-a/logs",
    }
    values.update(overrides)
    return WorkspaceLease(**values)


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
        remove_error: Exception | None = None,
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
        self._remove_error = remove_error
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
        if self._remove_error is not None:
            raise self._remove_error
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
            remove_error=self._client.remove_error,
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
        remove_error: Exception | None = None,
        list_error: Exception | None = None,
        host_port: str | None = "18000",
    ) -> None:
        self.ping_error = ping_error
        self.create_error = create_error
        self.start_error = start_error
        self.stop_error = stop_error
        self.remove_error = remove_error
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
        self.kill_error: Exception | None = None
        self.close_error: Exception | None = None

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
        if self.kill_error is not None:
            raise self.kill_error
        self.killed = True
        self.status.state = "TERMINATED"

    def close(self) -> None:
        if self.close_error is not None:
            raise self.close_error
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
    sandbox_egress_policy_enabled = False
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
    sandbox_runtime_subject = "runtime-subject-a"
    opensandbox_external_egress_capability_url = "https://capabilities.test/opensandbox/external-egress"
    opensandbox_external_egress_capability_token = "capability-test-token"
    opensandbox_external_egress_gateway_policy_subject = "gateway-policy-subject-a"
    opensandbox_external_egress_callback_boundary_subject = "callback-boundary-subject-a"


class ExternalEgressCapabilitySettings(OpenSandboxSettings):
    """Source-test settings for the required OpenSandbox runsc gateway profile."""


class IncompatibleOpenSandboxNetworkPolicySettings(ExternalEgressCapabilitySettings):
    sandbox_egress_policy_enabled = True


def external_egress_capability_profile(**overrides: Any) -> dict[str, Any]:
    profile = {
        "schema_version": "ai-platform.opensandbox.external-egress-capability.v1",
        "profile_id": "profile-a",
        "provider": "opensandbox",
        "opensandbox_endpoint": "http://opensandbox.local:8080",
        "runtime_identity": "runsc",
        "ai_platform_runtime_subject": "runtime-subject-a",
        "gateway_policy_subject": "gateway-policy-subject-a",
        "callback_boundary_subject": "callback-boundary-subject-a",
        "deny_audit_subject": "gateway-deny-audit-subject-a",
        "deny_counter_subject": "gateway-deny-counter-subject-a",
        "issued_at": "2026-07-14T00:00:00Z",
        "expires_at": "2030-01-01T00:00:00Z",
    }
    profile.update(overrides)
    return profile


def opensandbox_provider(*, health_probe=None, identity_probe=None, capability_profile_fetcher=None):
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
        identity_probe=identity_probe
        or (lambda executor_url, timeout_seconds, executor_headers: {"uid": 10001, "gid": 10001}),
        capability_profile_fetcher=capability_profile_fetcher or (lambda *_args: external_egress_capability_profile()),
    )


@pytest.mark.asyncio
async def test_opensandbox_provider_admits_only_authenticated_runsc_external_egress_profile(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: ExternalEgressCapabilitySettings())
    requests: list[tuple[str, dict[str, str], float]] = []

    def fetch_profile(url: str, headers: dict[str, str], timeout_seconds: float) -> dict[str, Any]:
        requests.append((url, headers, timeout_seconds))
        return external_egress_capability_profile()

    lease = await opensandbox_provider(capability_profile_fetcher=fetch_profile).create_or_reuse(request(), workspace())

    assert requests == [
        (
            "https://capabilities.test/opensandbox/external-egress",
            {"Authorization": "Bearer capability-test-token"},
            30.0,
        )
    ]
    assert lease.provider == "opensandbox"
    assert lease.labels["ai-platform.external_egress.profile_version"] == "v1"
    assert lease.labels["ai-platform.external_egress.runtime_identity"] == "runsc"
    assert lease.labels["ai-platform.external_egress.gateway_policy_subject"] == "gateway-policy-subject-a"
    assert lease.labels["ai-platform.external_egress.callback_boundary_subject"] == "callback-boundary-subject-a"
    assert lease.labels["ai-platform.runtime_subject"] == "runtime-subject-a"
    assert "network_policy" not in FakeOpenSandbox.created[0] or FakeOpenSandbox.created[0]["network_policy"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile", "expected_message"),
    [
        ({}, "capability profile"),
        ({"schema_version": "ai-platform.opensandbox.external-egress-capability.v0"}, "schema"),
        ({"expires_at": "2020-01-01T00:00:00Z"}, "expired"),
        ({"runtime_identity": "runc"}, "runtime identity"),
        ({"opensandbox_endpoint": "https://drifted.test"}, "endpoint"),
        ({"gateway_policy_subject": "gateway-policy-subject-b"}, "gateway policy subject"),
        ({"callback_boundary_subject": "callback-boundary-subject-b"}, "callback boundary subject"),
    ],
)
async def test_opensandbox_provider_fails_closed_for_missing_stale_or_drifted_external_egress_profile(
    monkeypatch,
    profile,
    expected_message,
):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: ExternalEgressCapabilitySettings())
    payload = external_egress_capability_profile(**profile) if profile else profile

    with pytest.raises(container_provider.OpenSandboxCapabilityAdmissionError, match=expected_message):
        await opensandbox_provider(capability_profile_fetcher=lambda *_args: payload).create_or_reuse(request(), workspace())

    assert FakeOpenSandbox.created == []


@pytest.mark.asyncio
async def test_opensandbox_provider_fails_closed_when_capability_endpoint_rejects_auth(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: ExternalEgressCapabilitySettings())

    def unauthorized_profile(*_args: Any) -> dict[str, Any]:
        request_value = httpx.Request("GET", "https://capabilities.test/opensandbox/external-egress")
        response = httpx.Response(401, request=request_value)
        raise httpx.HTTPStatusError("unauthorized", request=request_value, response=response)

    with pytest.raises(container_provider.OpenSandboxCapabilityAdmissionError, match="authentication failed"):
        await opensandbox_provider(capability_profile_fetcher=unauthorized_profile).create_or_reuse(request(), workspace())

    assert FakeOpenSandbox.created == []


@pytest.mark.asyncio
async def test_opensandbox_provider_rejects_gvisor_runsc_with_opensandbox_network_policy_without_fallback(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: IncompatibleOpenSandboxNetworkPolicySettings())
    profile_fetch_attempted = False

    def fetch_profile(*_args: Any) -> dict[str, Any]:
        nonlocal profile_fetch_attempted
        profile_fetch_attempted = True
        return external_egress_capability_profile()

    with pytest.raises(container_provider.OpenSandboxCapabilityAdmissionError, match="networkPolicy"):
        await opensandbox_provider(capability_profile_fetcher=fetch_profile).create_or_reuse(request(), workspace())

    assert profile_fetch_attempted is False
    assert FakeOpenSandbox.created == []
    assert FakeOpenSandbox.instances == {}


@pytest.mark.asyncio
async def test_opensandbox_provider_rechecks_profile_and_cleans_cached_lease_on_drift(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: ExternalEgressCapabilitySettings())
    profiles = iter(
        [
            external_egress_capability_profile(),
            external_egress_capability_profile(gateway_policy_subject="gateway-policy-subject-drift"),
        ]
    )
    provider = opensandbox_provider(capability_profile_fetcher=lambda *_args: next(profiles))

    lease = await provider.create_or_reuse(request(), workspace())

    with pytest.raises(container_provider.OpenSandboxCapabilityAdmissionError, match="gateway policy subject"):
        await provider.create_or_reuse(request(), workspace())

    assert len(FakeOpenSandbox.created) == 1
    assert FakeOpenSandbox.instances[lease.container_id].killed is True
    assert lease.container_id not in provider._sandboxes
    assert f"opensandbox-{lease.run_id}" not in provider._leases


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


def test_default_executor_probes_connect_to_pinned_ip_without_transmitting_private_metadata(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"uid": 10001, "gid": 10001}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def get(self, url, headers=None):
            calls.append((url, dict(headers or {})))
            return FakeResponse()

    monkeypatch.setattr(container_provider.httpx, "Client", FakeClient)
    private_metadata_key = "X-AI-Platform-Internal-Executor-Connect-Base-Url"
    headers = {
        "X-AI-Platform-Executor-Credential": "executor-secret",
        private_metadata_key: "http://172.17.0.1:43123",
    }
    logical_url = "http://host.docker.internal:43123"

    assert container_provider.default_executor_health_probe(logical_url, 1, headers) is True
    assert container_provider.default_executor_identity_probe(logical_url, 1, headers) == {
        "uid": 10001,
        "gid": 10001,
    }
    assert calls == [
        (
            "http://172.17.0.1:43123/health",
            {
                "X-AI-Platform-Executor-Credential": "executor-secret",
                "Host": "host.docker.internal:43123",
            },
        ),
        (
            "http://172.17.0.1:43123/health/runtime-identity",
            {
                "X-AI-Platform-Executor-Credential": "executor-secret",
                "Host": "host.docker.internal:43123",
            },
        ),
    ]
    assert all(private_metadata_key not in outgoing_headers for _, outgoing_headers in calls)


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
    assert "user" not in created
    assert created["entrypoint"] == ["/app/docker-entrypoint.sh", "uvicorn"]
    assert created["metadata"]["ai-platform.owner"] == "sandbox-runtime"
    assert created["metadata"]["ai-platform.tenant_id"] == "tenant-a"
    assert created["metadata"]["ai-platform.run_id"] == "run-a"
    assert created["metadata"]["ai-platform.executor.user"] == "10001:10001"
    assert created["metadata"]["ai-platform.executor.uid"] == "10001"
    assert created["metadata"]["ai-platform.executor.gid"] == "10001"
    assert created["metadata"]["ai-platform.executor.identity_evidence"] == "authenticated-runtime-endpoint"
    assert created["env"]["APP_MODULE"] == "app.runtime.sandbox.executor_app:create_executor_app"
    assert created["env"]["AI_PLATFORM_RUN_ID"] == "run-a"
    assert created["resource"] == {"cpu": "2", "memory": "512Mi", "pids": "64"}
    assert created["volumes"][0].host.path == workspace().workspace_host_path
    assert created["volumes"][0].mount_path == "/workspace"
    assert created["network_policy"] is None

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
    assert lease.labels["ai-platform.external_egress.runtime_identity"] == "runsc"
    assert lease.labels["ai-platform.external_egress.gateway_policy_subject"] == "gateway-policy-subject-a"
    assert not any(key.startswith("ai-platform.executor.") for key in lease.labels)


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
    assert "user" not in inspect.signature(symbols["sandbox_class"].create).parameters
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
async def test_docker_provider_rejects_cached_reuse_for_same_run_under_different_current_scope():
    from app.runtime.sandbox.container_provider import ContainerStartFailedError, DockerContainerProvider

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )
    first = await provider.create_or_reuse(request(), workspace())

    with pytest.raises(ContainerStartFailedError, match="cached lease scope mismatch"):
        await provider.create_or_reuse(
            request(tenant_id="tenant-b", workspace_id="workspace-b", user_id="user-b", session_id="session-b"),
            workspace(
                tenant_id="tenant-b",
                workspace_id="workspace-b",
                user_id="user-b",
                session_id="session-b",
                host_root="/runtime/tenants/tenant-b/runs/run-a",
                workspace_host_path="/runtime/tenants/tenant-b/runs/run-a/workspace",
                inputs_host_path="/runtime/tenants/tenant-b/runs/run-a/inputs",
                logs_host_path="/runtime/tenants/tenant-b/runs/run-a/logs",
            ),
        )

    container = fake.containers_by_name[first.container_name]
    assert container.stopped is True
    assert container.removed is True


@pytest.mark.asyncio
async def test_docker_cached_scope_mismatch_retains_tracking_when_cleanup_cannot_be_confirmed():
    from app.runtime.sandbox.container_provider import ContainerCleanupFailedError, DockerContainerProvider

    fake = FakeDockerClient(
        stop_error=RuntimeError("stop unavailable"),
        remove_error=RuntimeError("remove unavailable"),
    )
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )
    first = await provider.create_or_reuse(request(), workspace())

    with pytest.raises(ContainerCleanupFailedError) as exc_info:
        await provider.create_or_reuse(
            request(tenant_id="tenant-b", workspace_id="workspace-b", user_id="user-b", session_id="session-b"),
            workspace(
                tenant_id="tenant-b",
                workspace_id="workspace-b",
                user_id="user-b",
                session_id="session-b",
                host_root="/runtime/tenants/tenant-b/runs/run-a",
                workspace_host_path="/runtime/tenants/tenant-b/runs/run-a/workspace",
                inputs_host_path="/runtime/tenants/tenant-b/runs/run-a/inputs",
                logs_host_path="/runtime/tenants/tenant-b/runs/run-a/logs",
            ),
        )

    assert exc_info.value.error_code == "container_cleanup_failed"
    assert provider._leases[first.container_id] is first
    container = fake.containers_by_name[first.container_name]
    assert container.removed is False


@pytest.mark.asyncio
async def test_docker_cached_identity_mismatch_retains_tracking_when_cleanup_cannot_be_confirmed():
    from app.runtime.sandbox.container_provider import ContainerCleanupFailedError, DockerContainerProvider

    calls = 0

    def identity_probe(executor_url, timeout_seconds, executor_headers):
        nonlocal calls
        calls += 1
        return {"uid": 10001, "gid": 10001} if calls == 1 else {"uid": 0, "gid": 0}

    fake = FakeDockerClient(
        stop_error=RuntimeError("stop unavailable"),
        remove_error=RuntimeError("remove unavailable"),
    )
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
        identity_probe=identity_probe,
    )
    first = await provider.create_or_reuse(request(), workspace())

    with pytest.raises(ContainerCleanupFailedError):
        await provider.create_or_reuse(request(), workspace())

    assert provider._leases[first.container_id] is first


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
    assert created["tmpfs"] == {
        "/tmp": "rw,noexec,nosuid,nodev,uid=10001,gid=10001,mode=0700,size=64m",
        "/home/ai-platform": "rw,noexec,nosuid,nodev,uid=10001,gid=10001,mode=0700,size=128m",
    }
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
async def test_docker_provider_runs_executor_as_fixed_runtime_identity(monkeypatch):
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )
    await provider.create_or_reuse(request(), workspace())

    created = fake.created[0]
    assert created["user"] == "10001:10001"
    assert created["privileged"] is False
    assert created["security_opt"] == ["no-new-privileges:true"]
    assert created["cap_drop"] == ["ALL"]
    assert created["read_only"] is True
    assert created["tmpfs"] == {
        "/tmp": "rw,noexec,nosuid,nodev,uid=10001,gid=10001,mode=0700,size=64m",
        "/home/ai-platform": "rw,noexec,nosuid,nodev,uid=10001,gid=10001,mode=0700,size=128m",
    }
    assert created["ports"] == {"18000/tcp": ("127.0.0.1", None)}
    assert "/var/run/docker.sock" not in str(created).lower()


@pytest.mark.asyncio
async def test_docker_provider_fails_closed_when_workspace_owner_stat_is_unavailable(monkeypatch):
    from app.runtime.sandbox import container_provider
    from app.runtime.sandbox.container_provider import ContainerStartFailedError, DockerContainerProvider

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )
    monkeypatch.setattr(container_provider, "_workspace_owner_stat", lambda _path: (_ for _ in ()).throw(OSError("not available")))

    with pytest.raises(ContainerStartFailedError, match="workspace ownership unavailable"):
        await provider.create_or_reuse(request(), workspace())

    assert fake.created == []


@pytest.mark.asyncio
async def test_docker_provider_fails_closed_when_host_owner_semantics_are_unavailable_on_windows(monkeypatch):
    from app.runtime.sandbox import container_provider
    from app.runtime.sandbox.container_provider import ContainerStartFailedError, DockerContainerProvider

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )
    monkeypatch.setattr(container_provider, "_workspace_owner_stat", lambda _path: (_ for _ in ()).throw(OSError("POSIX unavailable")))

    with pytest.raises(ContainerStartFailedError, match="workspace ownership unavailable"):
        await provider.create_or_reuse(request(), workspace())

    assert fake.created == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("uid", "gid"), [(0, 10001), (10001, 0), (10001, -1), (1000, 1000)])
async def test_docker_provider_rejects_non_target_workspace_owner_before_create(monkeypatch, uid, gid):
    from app.runtime.sandbox import container_provider
    from app.runtime.sandbox.container_provider import ContainerStartFailedError, DockerContainerProvider

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
        identity_probe=lambda executor_url, timeout_seconds, executor_headers: {"uid": 10001, "gid": 10001},
    )
    monkeypatch.setattr(
        container_provider,
        "_workspace_owner_stat",
        lambda _path: type("StatResult", (), {"st_uid": uid, "st_gid": gid, "st_mode": 0o40700})(),
    )

    with pytest.raises(ContainerStartFailedError, match="workspace owner must be 10001:10001"):
        await provider.create_or_reuse(request(), workspace())

    assert fake.created == []


@pytest.mark.asyncio
async def test_docker_provider_uses_and_verifies_exact_runtime_identity(monkeypatch):
    from app.runtime.sandbox import container_provider
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    probes = []
    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
        identity_probe=lambda executor_url, timeout_seconds, executor_headers: probes.append(
            (executor_url, executor_headers)
        ) or {"uid": 10001, "gid": 10001},
    )

    lease = await provider.create_or_reuse(request(), workspace())

    assert fake.created[0]["user"] == "10001:10001"
    assert fake.containers_by_name[lease.container_name].attrs["Config"]["User"] == "10001:10001"
    assert probes[0][1]["X-AI-Platform-Executor-Credential"]
    remote_labels = fake.containers_by_name[lease.container_name].attrs["Config"]["Labels"]
    assert remote_labels["ai-platform.executor.user"] == "10001:10001"
    assert remote_labels["ai-platform.executor.uid"] == "10001"
    assert remote_labels["ai-platform.executor.gid"] == "10001"
    assert remote_labels["ai-platform.executor.identity_evidence"] == "authenticated-runtime-endpoint"
    assert not any(key.startswith("ai-platform.executor.") for key in lease.labels)


@pytest.mark.asyncio
async def test_docker_cached_reuse_rejects_remote_identity_label_mismatch():
    from app.runtime.sandbox.container_provider import ContainerStartFailedError, DockerContainerProvider

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )
    first = await provider.create_or_reuse(request(), workspace())
    container = fake.containers_by_name[first.container_name]
    container.labels["ai-platform.executor.uid"] = "0"

    with pytest.raises(ContainerStartFailedError):
        await provider.create_or_reuse(request(), workspace())

    assert container.stopped is True
    assert container.removed is True


@pytest.mark.asyncio
async def test_docker_provider_cleans_up_when_actual_executor_identity_mismatches(monkeypatch):
    from app.runtime.sandbox import container_provider
    from app.runtime.sandbox.container_provider import ContainerStartFailedError, DockerContainerProvider

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
        identity_probe=lambda executor_url, timeout_seconds, executor_headers: {"uid": 0, "gid": 0},
    )

    with pytest.raises(ContainerStartFailedError, match="executor identity mismatch"):
        await provider.create_or_reuse(request(), workspace())

    container = fake.containers_by_name["executor-exec-run-a"]
    assert container.stopped is True
    assert container.removed is True


@pytest.mark.asyncio
async def test_docker_cold_identity_mismatch_tracks_container_when_cleanup_cannot_be_confirmed():
    from app.runtime.sandbox.container_provider import ContainerCleanupFailedError, DockerContainerProvider

    fake = FakeDockerClient(
        stop_error=RuntimeError("stop unavailable"),
        remove_error=RuntimeError("remove unavailable"),
    )
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
        identity_probe=lambda executor_url, timeout_seconds, executor_headers: {"uid": 0, "gid": 0},
    )

    with pytest.raises(ContainerCleanupFailedError):
        await provider.create_or_reuse(request(), workspace())

    assert provider._leases["exec-run-a"].run_id == "run-a"


@pytest.mark.asyncio
async def test_docker_provider_cleans_up_when_identity_probe_is_cancelled():
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    def cancel_identity_probe(executor_url, timeout_seconds, executor_headers):
        raise asyncio.CancelledError()

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
        identity_probe=cancel_identity_probe,
    )

    with pytest.raises(asyncio.CancelledError):
        await provider.create_or_reuse(request(), workspace())

    container = fake.containers_by_name["executor-exec-run-a"]
    assert container.stopped is True
    assert container.removed is True


@pytest.mark.asyncio
async def test_docker_provider_cleans_up_when_executor_url_wait_is_cancelled(monkeypatch):
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )

    async def cancel_wait(container, timeout_seconds, endpoint):
        raise asyncio.CancelledError()

    monkeypatch.setattr(provider, "_wait_for_executor_url", cancel_wait)

    with pytest.raises(asyncio.CancelledError):
        await provider.create_or_reuse(request(), workspace())

    container = fake.containers_by_name["executor-exec-run-a"]
    assert container.stopped is True
    assert container.removed is True


@pytest.mark.asyncio
async def test_docker_cached_reuse_cleans_up_when_executor_url_wait_is_cancelled(monkeypatch):
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )
    first = await provider.create_or_reuse(request(), workspace())

    async def cancel_wait(container, timeout_seconds, endpoint):
        raise asyncio.CancelledError()

    monkeypatch.setattr(provider, "_wait_for_executor_url", cancel_wait)

    with pytest.raises(asyncio.CancelledError):
        await provider.create_or_reuse(request(), workspace())

    container = fake.containers_by_name[first.container_name]
    assert container.stopped is True
    assert container.removed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identity",
    [None, {}, {"uid": 10001}, {"uid": True, "gid": 10001}, {"uid": 10001, "gid": "10001"}],
)
async def test_docker_cached_reuse_rejects_malformed_runtime_identity(identity):
    from app.runtime.sandbox.container_provider import ContainerStartFailedError, DockerContainerProvider

    calls = 0

    def identity_probe(executor_url, timeout_seconds, executor_headers):
        nonlocal calls
        calls += 1
        return {"uid": 10001, "gid": 10001} if calls == 1 else identity

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
        identity_probe=identity_probe,
    )
    first = await provider.create_or_reuse(request(), workspace())

    with pytest.raises(ContainerStartFailedError):
        await provider.create_or_reuse(request(), workspace())

    container = fake.containers_by_name[first.container_name]
    assert container.stopped is True
    assert container.removed is True


@pytest.mark.asyncio
async def test_opensandbox_provider_fails_closed_without_exact_executor_identity(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())
    provider = opensandbox_provider(
        health_probe=lambda executor_url, timeout_seconds: True,
        identity_probe=lambda executor_url, timeout_seconds, executor_headers: {"uid": 0, "gid": 0},
    )

    with pytest.raises(container_provider.ContainerStartFailedError, match="executor identity mismatch"):
        await provider.create_or_reuse(request(), workspace())

    sandbox = FakeOpenSandbox.instances["osb-run-a"]
    assert sandbox.killed is True
    assert sandbox.closed is True


@pytest.mark.asyncio
async def test_opensandbox_cold_identity_mismatch_tracks_sandbox_when_cleanup_cannot_be_confirmed(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())

    def mismatched_identity(executor_url, timeout_seconds, executor_headers):
        sandbox = FakeOpenSandbox.instances["osb-run-a"]
        sandbox.kill_error = RuntimeError("kill unavailable")
        sandbox.close_error = RuntimeError("close unavailable")
        return {"uid": 0, "gid": 0}

    provider = opensandbox_provider(identity_probe=mismatched_identity)

    with pytest.raises(container_provider.ContainerCleanupFailedError):
        await provider.create_or_reuse(request(), workspace())

    tracked = provider._leases["opensandbox-run-a"]
    assert tracked.container_id == "osb-run-a"
    assert provider._sandboxes[tracked.container_id] is FakeOpenSandbox.instances["osb-run-a"]


@pytest.mark.asyncio
async def test_opensandbox_provider_denies_when_runtime_identity_endpoint_is_unsupported(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())

    def unsupported_identity_probe(executor_url, timeout_seconds, executor_headers):
        raise container_provider.ContainerStartFailedError("executor identity unavailable")

    provider = opensandbox_provider(identity_probe=unsupported_identity_probe)

    with pytest.raises(container_provider.ContainerStartFailedError, match="executor identity unavailable"):
        await provider.create_or_reuse(request(), workspace())

    sandbox = FakeOpenSandbox.instances["osb-run-a"]
    assert sandbox.killed is True
    assert sandbox.closed is True


@pytest.mark.asyncio
async def test_opensandbox_cached_reuse_cleans_up_when_identity_probe_is_cancelled(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())
    calls = 0

    def identity_probe(executor_url, timeout_seconds, executor_headers):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise asyncio.CancelledError()
        return {"uid": 10001, "gid": 10001}

    provider = opensandbox_provider(identity_probe=identity_probe)
    first = await provider.create_or_reuse(request(), workspace())

    with pytest.raises(asyncio.CancelledError):
        await provider.create_or_reuse(request(), workspace())

    sandbox = FakeOpenSandbox.instances[first.container_id]
    assert sandbox.killed is True
    assert sandbox.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identity",
    [None, {}, {"uid": 10001}, {"uid": True, "gid": 10001}, {"uid": 10001, "gid": "10001"}],
)
async def test_opensandbox_cached_reuse_rejects_malformed_runtime_identity(monkeypatch, identity):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())
    calls = 0

    def identity_probe(executor_url, timeout_seconds, executor_headers):
        nonlocal calls
        calls += 1
        return {"uid": 10001, "gid": 10001} if calls == 1 else identity

    provider = opensandbox_provider(identity_probe=identity_probe)
    first = await provider.create_or_reuse(request(), workspace())

    with pytest.raises(container_provider.ContainerStartFailedError, match="executor identity mismatch"):
        await provider.create_or_reuse(request(), workspace())

    sandbox = FakeOpenSandbox.instances[first.container_id]
    assert sandbox.killed is True
    assert sandbox.closed is True


@pytest.mark.asyncio
async def test_opensandbox_cached_reuse_rejects_missing_lease_credential(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())
    provider = opensandbox_provider()
    first = await provider.create_or_reuse(request(), workspace())
    first.executor_headers.clear()

    with pytest.raises(container_provider.ContainerStartFailedError, match="executor identity credential unavailable"):
        await provider.create_or_reuse(request(), workspace())

    sandbox = FakeOpenSandbox.instances[first.container_id]
    assert sandbox.killed is True
    assert sandbox.closed is True


@pytest.mark.asyncio
async def test_opensandbox_rejects_cached_reuse_for_same_run_under_different_current_scope(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())
    provider = opensandbox_provider()
    first = await provider.create_or_reuse(request(), workspace())

    with pytest.raises(container_provider.ContainerStartFailedError, match="cached lease scope mismatch"):
        await provider.create_or_reuse(
            request(tenant_id="tenant-b", workspace_id="workspace-b", user_id="user-b", session_id="session-b"),
            workspace(
                tenant_id="tenant-b",
                workspace_id="workspace-b",
                user_id="user-b",
                session_id="session-b",
                host_root="/runtime/tenants/tenant-b/runs/run-a",
                workspace_host_path="/runtime/tenants/tenant-b/runs/run-a/workspace",
                inputs_host_path="/runtime/tenants/tenant-b/runs/run-a/inputs",
                logs_host_path="/runtime/tenants/tenant-b/runs/run-a/logs",
            ),
        )

    sandbox = FakeOpenSandbox.instances[first.container_id]
    assert sandbox.killed is True
    assert sandbox.closed is True


@pytest.mark.asyncio
async def test_opensandbox_cached_scope_mismatch_retains_tracking_when_cleanup_cannot_be_confirmed(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())
    provider = opensandbox_provider()
    first = await provider.create_or_reuse(request(), workspace())
    sandbox = FakeOpenSandbox.instances[first.container_id]
    sandbox.kill_error = RuntimeError("kill unavailable")
    sandbox.close_error = RuntimeError("close unavailable")

    with pytest.raises(container_provider.ContainerCleanupFailedError) as exc_info:
        await provider.create_or_reuse(
            request(tenant_id="tenant-b", workspace_id="workspace-b", user_id="user-b", session_id="session-b"),
            workspace(
                tenant_id="tenant-b",
                workspace_id="workspace-b",
                user_id="user-b",
                session_id="session-b",
                host_root="/runtime/tenants/tenant-b/runs/run-a",
                workspace_host_path="/runtime/tenants/tenant-b/runs/run-a/workspace",
                inputs_host_path="/runtime/tenants/tenant-b/runs/run-a/inputs",
                logs_host_path="/runtime/tenants/tenant-b/runs/run-a/logs",
            ),
        )

    assert exc_info.value.error_code == "container_cleanup_failed"
    assert provider._leases["opensandbox-run-a"] is first
    assert provider._sandboxes[first.container_id] is sandbox


@pytest.mark.asyncio
async def test_opensandbox_cached_identity_mismatch_retains_tracking_when_cleanup_cannot_be_confirmed(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())
    calls = 0

    def identity_probe(executor_url, timeout_seconds, executor_headers):
        nonlocal calls
        calls += 1
        return {"uid": 10001, "gid": 10001} if calls == 1 else {"uid": 0, "gid": 0}

    provider = opensandbox_provider(identity_probe=identity_probe)
    first = await provider.create_or_reuse(request(), workspace())
    sandbox = FakeOpenSandbox.instances[first.container_id]
    sandbox.kill_error = RuntimeError("kill unavailable")
    sandbox.close_error = RuntimeError("close unavailable")

    with pytest.raises(container_provider.ContainerCleanupFailedError):
        await provider.create_or_reuse(request(), workspace())

    assert provider._leases["opensandbox-run-a"] is first
    assert provider._sandboxes[first.container_id] is sandbox


@pytest.mark.asyncio
async def test_opensandbox_cached_reuse_revalidates_remote_scope_metadata(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())
    provider = opensandbox_provider()
    first = await provider.create_or_reuse(request(), workspace())
    sandbox = FakeOpenSandbox.instances[first.container_id]
    sandbox.metadata["ai-platform.tenant_id"] = "tenant-b"

    with pytest.raises(container_provider.ContainerStartFailedError, match="cached sandbox metadata mismatch"):
        await provider.create_or_reuse(request(), workspace())

    assert sandbox.killed is True
    assert sandbox.closed is True


@pytest.mark.asyncio
async def test_opensandbox_cached_reuse_rejects_remote_identity_label_mismatch(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())
    provider = opensandbox_provider()
    first = await provider.create_or_reuse(request(), workspace())
    sandbox = FakeOpenSandbox.instances[first.container_id]
    sandbox.metadata["ai-platform.executor.gid"] = "0"

    with pytest.raises(container_provider.ContainerStartFailedError, match="cached sandbox metadata mismatch"):
        await provider.create_or_reuse(request(), workspace())

    assert sandbox.killed is True
    assert sandbox.closed is True


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


def test_executor_published_endpoint_resolves_one_pinned_host_gateway_ipv4(monkeypatch):
    from app.runtime.sandbox import container_provider

    calls: list[tuple[object, ...]] = []

    def getaddrinfo(host, port, *, family, type):
        calls.append((host, port, family, type))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.17.0.1", 0))]

    monkeypatch.setattr(container_provider.socket, "getaddrinfo", getaddrinfo)

    endpoint = container_provider._resolve_executor_published_endpoint("host.docker.internal")

    assert endpoint.published_host == "host.docker.internal"
    assert endpoint.bind_ip == "172.17.0.1"
    assert calls == [("host.docker.internal", None, socket.AF_INET, socket.SOCK_STREAM)]


@pytest.mark.parametrize("published_host", ["", "0.0.0.0", "::"])
def test_executor_published_endpoint_rejects_empty_or_wildcard_host(published_host):
    from app.runtime.sandbox import container_provider

    with pytest.raises(container_provider.ContainerStartFailedError, match="published endpoint"):
        container_provider._resolve_executor_published_endpoint(published_host)


def test_executor_published_endpoint_rejects_unresolvable_host(monkeypatch):
    from app.runtime.sandbox import container_provider

    def fail_resolution(*_args, **_kwargs):
        raise socket.gaierror("resolver detail must stay private")

    monkeypatch.setattr(container_provider.socket, "getaddrinfo", fail_resolution)

    with pytest.raises(container_provider.ContainerStartFailedError, match="published endpoint") as exc_info:
        container_provider._resolve_executor_published_endpoint("missing.internal")

    assert "resolver detail" not in str(exc_info.value)


def test_executor_published_endpoint_rejects_multiple_resolved_addresses(monkeypatch):
    from app.runtime.sandbox import container_provider

    monkeypatch.setattr(
        container_provider.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.17.0.1", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.18.0.1", 0)),
        ],
    )

    with pytest.raises(container_provider.ContainerStartFailedError, match="published endpoint"):
        container_provider._resolve_executor_published_endpoint("host.docker.internal")


def test_executor_published_endpoint_rejects_hostname_resolving_to_loopback(monkeypatch):
    from app.runtime.sandbox import container_provider

    monkeypatch.setattr(
        container_provider.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))],
    )

    with pytest.raises(container_provider.ContainerStartFailedError, match="published endpoint"):
        container_provider._resolve_executor_published_endpoint("host.docker.internal")


@pytest.mark.parametrize("published_host", ["8.8.8.8", "1.1.1.1"])
def test_executor_published_endpoint_rejects_global_or_non_private_literal(published_host):
    from app.runtime.sandbox import container_provider

    with pytest.raises(container_provider.ContainerStartFailedError, match="published endpoint"):
        container_provider._resolve_executor_published_endpoint(published_host)


def test_executor_published_endpoint_rejects_hostname_resolving_to_global_address(monkeypatch):
    from app.runtime.sandbox import container_provider

    monkeypatch.setattr(
        container_provider.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))],
    )

    with pytest.raises(container_provider.ContainerStartFailedError, match="published endpoint"):
        container_provider._resolve_executor_published_endpoint("executor-gateway.example")


def test_published_executor_url_does_not_fallback_from_wildcard_inspect_binding(monkeypatch):
    from app.runtime.sandbox import container_provider

    settings = type("StubSettings", (), {"sandbox_executor_published_host": "host.docker.internal"})()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    container = type(
        "StubContainer",
        (),
        {
            "attrs": {
                "NetworkSettings": {
                    "Ports": {"18000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "43123"}]}
                }
            }
        },
    )()

    assert container_provider._published_executor_url_from_container(container) is None


def test_published_executor_url_rejects_additional_inspect_binding():
    from app.runtime.sandbox import container_provider

    endpoint = container_provider._ExecutorPublishedEndpoint(
        published_host="host.docker.internal",
        bind_ip="172.17.0.1",
    )
    container = type(
        "StubContainer",
        (),
        {
            "attrs": {
                "NetworkSettings": {
                    "Ports": {
                        "18000/tcp": [
                            {"HostIp": "172.17.0.1", "HostPort": "43123"},
                            {"HostIp": "0.0.0.0", "HostPort": "43123"},
                        ]
                    }
                }
            }
        },
    )()

    with pytest.raises(container_provider.ContainerStartFailedError, match="published endpoint mismatch"):
        container_provider._published_executor_url_from_container(container, endpoint)


@pytest.mark.parametrize("host_port", ["", "0", "65536", "not-a-port"])
def test_published_executor_url_rejects_invalid_inspect_host_port(host_port):
    from app.runtime.sandbox import container_provider

    endpoint = container_provider._ExecutorPublishedEndpoint(
        published_host="host.docker.internal",
        bind_ip="172.17.0.1",
    )
    container = type(
        "StubContainer",
        (),
        {
            "attrs": {
                "NetworkSettings": {
                    "Ports": {"18000/tcp": [{"HostIp": "172.17.0.1", "HostPort": host_port}]}
                }
            }
        },
    )()

    with pytest.raises(container_provider.ContainerStartFailedError, match="published endpoint mismatch"):
        container_provider._published_executor_url_from_container(container, endpoint)


@pytest.mark.asyncio
async def test_docker_provider_binds_pinned_host_gateway_and_publishes_configured_hostname(monkeypatch):
    from app.runtime.sandbox import container_provider
    from app.runtime.sandbox.contracts import EXECUTOR_AUTH_HEADER
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    settings = type(
        "StubSettings",
        (),
        {
            "sandbox_executor_published_host": "host.docker.internal",
            "sandbox_executor_image": "ai-platform-executor:dev",
            "sandbox_callback_base_url": "http://platform.test",
            "sandbox_callback_host_gateway": "host.docker.internal",
            "sandbox_egress_policy_enabled": False,
            "sandbox_container_start_timeout_seconds": 5,
            "sandbox_executor_health_timeout_seconds": 5,
        },
    )()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    resolution_calls = 0

    def getaddrinfo(*_args, **_kwargs):
        nonlocal resolution_calls
        resolution_calls += 1
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.17.0.1", 0))]

    monkeypatch.setattr(container_provider.socket, "getaddrinfo", getaddrinfo)
    probes: list[tuple[str, dict[str, str]]] = []
    fake = FakeDockerClient(host_port="43123")
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds, executor_headers: probes.append(
            (executor_url, executor_headers)
        )
        or True,
        identity_probe=lambda executor_url, timeout_seconds, executor_headers: probes.append(
            (executor_url, executor_headers)
        )
        or {"uid": 10001, "gid": 10001},
    )

    lease = await provider.create_or_reuse(request(), workspace())

    created = fake.created[0]
    assert resolution_calls == 1
    assert created["ports"] == {"18000/tcp": ("172.17.0.1", None)}
    assert lease.executor_url == "http://host.docker.internal:43123"
    assert [url for url, _headers in probes] == ["http://172.17.0.1:43123", "http://172.17.0.1:43123"]
    assert all(headers[EXECUTOR_AUTH_HEADER] == lease.executor_headers[EXECUTOR_AUTH_HEADER] for _, headers in probes)
    assert all(headers["Host"] == "host.docker.internal:43123" for _, headers in probes)
    assert all(container_provider.EXECUTOR_CONNECT_BASE_URL_METADATA not in headers for _, headers in probes)
    assert lease.executor_headers[container_provider.EXECUTOR_CONNECT_BASE_URL_METADATA] == "http://172.17.0.1:43123"
    assert container_provider.EXECUTOR_CONNECT_BASE_URL_METADATA not in str(lease.model_dump())
    assert created["privileged"] is False
    assert created["read_only"] is True
    assert created["cap_drop"] == ["ALL"]
    assert "network_mode" not in created


@pytest.mark.asyncio
async def test_docker_provider_rebuilds_instead_of_reusing_when_inspected_bind_ip_drifted(monkeypatch):
    from app.runtime.sandbox import container_provider
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    settings = type(
        "StubSettings",
        (),
        {
            "sandbox_executor_published_host": "host.docker.internal",
            "sandbox_executor_image": "ai-platform-executor:dev",
            "sandbox_callback_base_url": "http://platform.test",
            "sandbox_callback_host_gateway": "host.docker.internal",
            "sandbox_egress_policy_enabled": False,
            "sandbox_container_start_timeout_seconds": 5,
            "sandbox_executor_health_timeout_seconds": 5,
        },
    )()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    monkeypatch.setattr(
        container_provider.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.17.0.1", 0))],
    )
    fake = FakeDockerClient(host_port="43123")
    first = DockerContainerProvider(docker_client_factory=lambda: fake, health_probe=lambda *_args, **_kwargs: True)
    lease = await first.create_or_reuse(request(), workspace())
    container = fake.containers_by_name[lease.container_name]
    container.attrs["NetworkSettings"]["Ports"]["18000/tcp"][0]["HostIp"] = "127.0.0.1"
    restarted = DockerContainerProvider(docker_client_factory=lambda: fake, health_probe=lambda *_args, **_kwargs: True)

    replacement = await restarted.create_or_reuse(request(), workspace())

    assert container.stopped is True
    assert container.removed is True
    assert len(fake.created) == 2
    assert fake.created[1]["ports"] == {"18000/tcp": ("172.17.0.1", None)}
    assert replacement.executor_url == "http://host.docker.internal:43123"


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
async def test_docker_provider_stop_retains_lease_when_removal_cannot_be_confirmed():
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )
    lease = await provider.create_or_reuse(request(), workspace())
    container = fake.containers_by_name[lease.container_name]
    container._stop_error = RuntimeError("stop unavailable")
    container._remove_error = RuntimeError("remove unavailable")

    result = await provider.stop(lease, reason="cancel_requested")

    assert result.status == "failed"
    assert provider._leases[lease.container_id] is lease


@pytest.mark.asyncio
async def test_opensandbox_provider_stop_retains_lease_when_kill_cannot_be_confirmed(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())
    provider = opensandbox_provider()
    lease = await provider.create_or_reuse(request(), workspace())
    sandbox = FakeOpenSandbox.instances[lease.container_id]
    sandbox.kill_error = RuntimeError("kill unavailable")

    result = await provider.stop(lease, reason="cancel_requested")

    assert result.status == "failed"
    assert provider._leases["opensandbox-run-a"] is lease
    assert provider._sandboxes[lease.container_id] is sandbox


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
