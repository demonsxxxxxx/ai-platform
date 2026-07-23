import asyncio
import hashlib
import inspect
import importlib
import json
import os
import socket
import threading
import time
import traceback
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
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
    monkeypatch.setattr(
        container_provider,
        "_secure_native_tool_socket_directory",
        lambda _path: None,
        raising=False,
    )
    if request.node.name != "test_default_executor_probes_connect_to_pinned_ip_without_transmitting_private_metadata":
        monkeypatch.setattr(
            container_provider,
            "default_executor_identity_probe",
            lambda executor_url, timeout_seconds, executor_headers: {"uid": 10001, "gid": 10001},
            raising=False,
        )
    settings_getter = container_provider.get_settings

    class EgressEnabledTestSettings:
        sandbox_egress_policy_enabled = True
        sandbox_egress_network_name = "ai-platform-sandbox-egress-internal-v1"
        sandbox_executor_image = "registry.example/ai-platform@sha256:" + "a" * 64
        sandbox_callback_base_url = "http://api.sandbox.internal:8020"
        sandbox_callback_host_gateway = ""
        sandbox_egress_proof_signing_key = "provider-test-proof-key-with-enough-entropy-2026"
        sandbox_egress_proof_key_id = "current"
        ai_platform_runtime_commit = "a" * 40

        def __init__(self, settings: Any) -> None:
            self._settings = settings

        def __getattr__(self, name: str) -> Any:
            return getattr(self._settings, name)

    monkeypatch.setattr(container_provider, "get_settings", lambda: EgressEnabledTestSettings(settings_getter()))


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
    prepare_staged_skills = bool(overrides.pop("prepare_staged_skills", True))
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
    if "workspace_host_path" in overrides and "host_root" not in overrides:
        workspace_path = Path(values["workspace_host_path"])
        values["host_root"] = str(workspace_path.parent)
        values["inputs_host_path"] = str(workspace_path / "inputs")
        values["logs_host_path"] = str(workspace_path.parent / "logs")
    workspace_path = Path(values["workspace_host_path"])
    if prepare_staged_skills and workspace_path.is_dir():
        (workspace_path / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
    return WorkspaceLease(**values)


def governed_docker_settings(**overrides: Any) -> SimpleNamespace:
    values = {
        "sandbox_container_start_timeout_seconds": 30,
        "sandbox_executor_health_timeout_seconds": 60,
        "sandbox_executor_image": "registry.example/ai-platform@sha256:" + "a" * 64,
        "sandbox_executor_published_host": "127.0.0.1",
        "sandbox_workspace_root": "/tmp/ai-platform-sandbox-workspaces",
        "sandbox_callback_base_url": "http://api.sandbox.internal:8020",
        "sandbox_egress_policy_enabled": True,
        "sandbox_egress_network_name": "ai-platform-sandbox-egress-internal-v1",
        "sandbox_egress_proof_signing_key": "provider-test-proof-key-with-enough-entropy-2026",
        "sandbox_egress_proof_key_id": "current",
        "ai_platform_runtime_commit": "a" * 40,
        "sandbox_callback_host_gateway": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def native_tool_subjects() -> list[dict[str, Any]]:
    return [
        {
            "identity": "Skill",
            "declared_identities": ["Skill"],
            "registered": True,
            "declared": True,
            "active": True,
            "distributed": True,
            "execution_strategy": "sdk_restricted",
            "allowed_skill_names": ["native-review"],
        },
        {
            "identity": "Bash",
            "declared_identities": ["Bash"],
            "registered": True,
            "declared": True,
            "active": True,
            "distributed": True,
            "execution_strategy": "sdk_native",
            "command_isolation": "sibling-tool-sandbox-v1",
        },
    ]


def trusted_skill_mount_stub(selected_workspace: WorkspaceLease) -> SimpleNamespace:
    return SimpleNamespace(
        host_path=Path(selected_workspace.workspace_host_path) / ".claude",
        container_path=f"{selected_workspace.workspace_container_path.rstrip('/')}/.claude",
        fingerprint="f" * 64,
    )


def test_skill_mount_and_native_bash_admission_are_independently_derived():
    import app.runtime.sandbox.container_provider as container_provider

    staged_skill = native_tool_subjects()[0]
    native_bash = native_tool_subjects()[1]
    controlled_bash = {
        **native_bash,
        "execution_strategy": "platform_controlled",
        "command_isolation": "minimal-environment-v1",
    }
    cases = (
        ("implicit_native_catalog", [staged_skill, native_bash], True, True),
        ("implicit_platform_controlled_catalog", [staged_skill, controlled_bash], True, False),
        ("staged_catalog_without_bash", [staged_skill], True, False),
        ("native_bash_without_catalog", [native_bash], False, True),
        ("no_catalog_no_bash", [], False, False),
    )

    for case, subjects, mount_required, native_required in cases:
        runtime_request = request(tool_policy_subjects=subjects)
        assert container_provider._staged_skill_mount_required(runtime_request) is mount_required, case
        assert container_provider._native_tool_required(runtime_request) is native_required, case


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
        ports: dict[str, Any] | None = None,
        start_error: Exception | None = None,
        stop_error: Exception | None = None,
        remove_error: Exception | None = None,
        host_port: str | None = "18000",
        exec_exit_code: int = 0,
        exec_error: Exception | None = None,
        **docker_kwargs: Any,
    ) -> None:
        self.id = f"docker-{name}"
        self.image = image
        self.name = name
        self.detach = detach
        self.labels = dict(labels)
        self.volumes = dict(volumes)
        self.environment = dict(environment)
        self.ports = dict(ports or {})
        self.docker_kwargs = dict(docker_kwargs)
        self.status = "created"
        self.started = False
        self.stopped = False
        self.removed = False
        self._start_error = start_error
        self._stop_error = stop_error
        self._remove_error = remove_error
        self._exec_exit_code = exec_exit_code
        self._exec_error = exec_error
        published_host = "0.0.0.0"
        port_binding = self.ports.get("18000/tcp")
        if isinstance(port_binding, tuple) and len(port_binding) == 2:
            candidate_host = str(port_binding[0] or "").strip()
            if candidate_host:
                published_host = candidate_host
        port_bindings = [] if host_port is None else [{"HostIp": published_host, "HostPort": host_port}]
        self.attrs = {
            "Id": self.id,
            "Config": {
                "Labels": self.labels,
                "User": str(docker_kwargs.get("user") or ""),
                "Env": [f"{key}={value}" for key, value in self.environment.items()],
            },
            "HostConfig": {
                "ExtraHosts": docker_kwargs.get("extra_hosts"),
            },
            "NetworkSettings": {
                "Ports": {"18000/tcp": port_bindings},
                "Networks": {},
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
        client = getattr(self, "client", None)
        if client is not None:
            client.detach_from_all_networks(self)

    def reload(self) -> None:
        return None

    def exec_run(self, command: list[str], **kwargs: Any) -> SimpleNamespace:
        if self._exec_error is not None:
            raise self._exec_error
        return SimpleNamespace(exit_code=self._exec_exit_code)


class FakeDockerAPI:
    def __init__(self, client: "FakeDockerClient") -> None:
        self._client = client
        self._exec_containers: dict[str, FakeDockerContainer] = {}
        self.exec_create_calls: list[tuple[str, list[str], dict[str, Any]]] = []
        self.exec_start_calls: list[tuple[str, dict[str, Any]]] = []
        self.exec_inspect_calls: list[str] = []

    def _container(self, container_id: str) -> FakeDockerContainer:
        for container in self._client.containers_by_name.values():
            if container.id == container_id:
                return container
        raise RuntimeError("container not found")

    def exec_create(self, container_id: str, command: list[str], **kwargs):
        container = self._container(container_id)
        self.exec_create_calls.append((container_id, list(command), dict(kwargs)))
        if container._exec_error is not None:
            raise container._exec_error
        exec_id = f"exec-{len(self._exec_containers) + 1}"
        self._exec_containers[exec_id] = container
        return {"Id": exec_id}

    def exec_start(self, exec_id: str, **kwargs) -> None:
        self.exec_start_calls.append((exec_id, dict(kwargs)))
        container = self._exec_containers[exec_id]
        if container._exec_error is not None:
            raise container._exec_error

    def exec_inspect(self, exec_id: str) -> dict[str, Any]:
        self.exec_inspect_calls.append(exec_id)
        container = self._exec_containers[exec_id]
        if container._exec_error is not None:
            raise container._exec_error
        return {"Running": False, "ExitCode": container._exec_exit_code}


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
            exec_exit_code=self._client.exec_exit_code,
            exec_error=self._client.exec_error,
            **kwargs,
        )
        network_name = str(kwargs.get("network") or "")
        if network_name:
            self._client.attach_to_network(container, network_name)
        post_create_mutator = getattr(self._client, "post_create_mutator", None)
        if callable(post_create_mutator):
            post_create_mutator(container)
        self._client.created.append(kwargs)
        self._client.containers_by_name[container.name] = container
        container.client = self._client
        return container

    def list(self, all: bool = False, filters: dict[str, Any] | None = None) -> list[FakeDockerContainer]:
        if self._client.list_error is not None:
            raise self._client.list_error
        containers = list(self._client.containers_by_name.values())
        if not all:
            containers = [container for container in containers if container.status == "running"]
        return containers

    def get(self, name: str) -> FakeDockerContainer:
        if name in self._client.containers_by_name:
            return self._client.containers_by_name[name]
        for container in self._client.containers_by_name.values():
            if container.id == name:
                return container
        raise KeyError(name)


class FakeDockerNetwork(dict):
    def __init__(self, client: "FakeDockerClient", attrs: dict[str, Any]) -> None:
        super().__init__(attrs=attrs)
        self._client = client

    def reload(self) -> None:
        return None

    def connect(self, container: FakeDockerContainer, aliases: list[str] | None = None) -> None:
        self._client.attach_to_network(container, self["attrs"]["Name"], aliases=aliases)

    def disconnect(self, container: FakeDockerContainer, force: bool = False) -> None:
        self._client.detach_from_network(container, self["attrs"]["Name"])

    def remove(self) -> None:
        if self["attrs"].get("Containers"):
            raise RuntimeError("network still has members")
        self._client.networks_by_name.pop(self["attrs"]["Name"], None)


class FakeDockerNetworks:
    def __init__(self, client: "FakeDockerClient") -> None:
        self._client = client

    def get(self, name: str) -> FakeDockerNetwork:
        if name not in self._client.networks_by_name:
            raise KeyError(name)
        return self._client.networks_by_name[name]

    def create(self, name: str, **kwargs) -> FakeDockerNetwork:
        network = FakeDockerNetwork(
            self._client,
            {
                "Id": f"network-{name}",
                "Name": name,
                "Driver": kwargs.get("driver", "bridge"),
                "Internal": kwargs.get("internal", False),
                "Options": kwargs.get("options", {}),
                "Labels": kwargs.get("labels", {}),
                "Containers": {},
            }
        )
        self._client.networks_by_name[name] = network
        self._client.network_create_calls.append((name, kwargs))
        return network

    def list(self) -> list[FakeDockerNetwork]:
        return list(self._client.networks_by_name.values())


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
        exec_exit_code: int = 0,
        exec_error: Exception | None = None,
    ) -> None:
        self.ping_error = ping_error
        self.create_error = create_error
        self.start_error = start_error
        self.stop_error = stop_error
        self.remove_error = remove_error
        self.list_error = list_error
        self.host_port = host_port
        self.exec_exit_code = exec_exit_code
        self.exec_error = exec_error
        self.created: list[dict[str, Any]] = []
        self.containers_by_name: dict[str, FakeDockerContainer] = {}
        self.networks_by_name: dict[str, FakeDockerNetwork] = {
            "ai-platform-sandbox-egress-internal-v1": FakeDockerNetwork(
                self,
                {
                    "Id": "network-internal-v1",
                    "Name": "ai-platform-sandbox-egress-internal-v1",
                    "Driver": "bridge",
                    "Internal": True,
                    "Options": {"com.docker.network.bridge.enable_ip_masquerade": "false"},
                    "Labels": {},
                    "Containers": {},
                }
            )
        }
        self.network_create_calls: list[tuple[str, dict[str, Any]]] = []
        self.api = FakeDockerAPI(self)
        self.containers = FakeDockerContainers(self)
        self.networks = FakeDockerNetworks(self)
        self.ping_count = 0
        self.api_container = FakeDockerContainer(
            image="ai-platform-api:fake",
            name="ai-platform-api",
            detach=True,
            labels={
                "ai-platform.release-role": "api",
                "ai-platform.release-owner": "repo-local-compose",
                "ai-platform.source-commit": "a" * 40,
            },
            volumes={},
            environment={},
        )
        self.api_container.status = "running"
        self.api_container.attrs["State"] = {"Health": {"Status": "healthy"}}
        self.api_container.client = self
        self.attach_to_network(
            self.api_container,
            "ai-platform-sandbox-egress-internal-v1",
            aliases=["api.sandbox.internal"],
        )
        self.containers_by_name[self.api_container.name] = self.api_container

    def attach_to_network(
        self,
        container: FakeDockerContainer,
        network_name: str,
        *,
        aliases: list[str] | None = None,
    ) -> None:
        network = self.networks_by_name.get(network_name)
        if network is None:
            return
        network_id = str(network["attrs"]["Id"])
        networks = container.attrs["NetworkSettings"]["Networks"]
        networks[network_name] = {"NetworkID": network_id, "Aliases": list(aliases or [])}
        network["attrs"]["Containers"][container.id] = {"Name": container.name}

    def detach_from_network(self, container: FakeDockerContainer, network_name: str) -> None:
        container.attrs["NetworkSettings"]["Networks"].pop(network_name, None)
        network = self.networks_by_name.get(network_name)
        if network is not None:
            network["attrs"]["Containers"].pop(container.id, None)

    def detach_from_all_networks(self, container: FakeDockerContainer) -> None:
        for network_name in tuple(container.attrs["NetworkSettings"]["Networks"]):
            self.detach_from_network(container, network_name)

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


def test_default_native_tool_probe_uses_detached_no_output_low_level_api(capsys, caplog):
    from app.runtime.sandbox.container_provider import (
        _NATIVE_TOOL_HEALTH_PROBE_COMMAND,
        _default_native_tool_probe,
    )

    private_token = "test-native-tool-token"
    private_path = "/private/runtime/workspace"

    class ProbeAPI:
        def __init__(self) -> None:
            self.create_calls: list[tuple[str, list[str], dict[str, Any]]] = []
            self.start_calls: list[tuple[str, dict[str, Any]]] = []
            self.inspect_calls: list[str] = []

        def exec_create(self, container_id, command, **kwargs):
            self.create_calls.append((container_id, list(command), dict(kwargs)))
            return {"Id": "fixed-health-probe"}

        def exec_start(self, exec_id, **kwargs):
            self.start_calls.append((exec_id, dict(kwargs)))

        def exec_inspect(self, exec_id):
            self.inspect_calls.append(exec_id)
            return {"Running": False, "ExitCode": 0}

    class Container:
        id = "sidecar-container-id"

        def __init__(self) -> None:
            self.client = type("DockerClient", (), {"api": ProbeAPI()})()

        def exec_run(self, *_args, **_kwargs):
            raise AssertionError("high-level output-returning exec_run must not be used")

    container = Container()
    assert _default_native_tool_probe(container) is True
    api = container.client.api
    assert api.create_calls == [
        (
            container.id,
            list(_NATIVE_TOOL_HEALTH_PROBE_COMMAND),
            {"stdout": False, "stderr": False},
        )
    ]
    assert api.start_calls == [("fixed-health-probe", {"detach": True})]
    assert api.inspect_calls == ["fixed-health-probe"]
    captured = capsys.readouterr()
    assert private_token not in repr(api.create_calls)
    assert private_path not in repr(api.create_calls)
    assert private_token not in captured.out + captured.err + caplog.text
    assert private_path not in captured.out + captured.err + caplog.text


def test_default_native_tool_probe_fails_closed_for_invalid_low_level_states_and_exceptions(capsys, caplog):
    from app.runtime.sandbox.container_provider import (
        _NATIVE_TOOL_HEALTH_PROBE_COMMAND,
        _default_native_tool_probe,
    )

    private_token = "test-native-tool-token"
    private_path = "/private/runtime/workspace"

    class ProbeAPI:
        def __init__(self, *, created: Any = None, start_error: Exception | None = None, inspected: Any = None) -> None:
            self.created = {"Id": "fixed-health-probe"} if created is None else created
            self.start_error = start_error
            self.inspected = {"Running": False, "ExitCode": 0} if inspected is None else inspected
            self.create_calls: list[tuple[str, list[str], dict[str, Any]]] = []

        def exec_create(self, container_id, command, **kwargs):
            self.create_calls.append((container_id, list(command), dict(kwargs)))
            return self.created

        def exec_start(self, _exec_id, **_kwargs):
            if self.start_error is not None:
                raise self.start_error

        def exec_inspect(self, _exec_id):
            return self.inspected

    class Container:
        id = "sidecar-container-id"

        def __init__(self, api: Any) -> None:
            self.client = type("DockerClient", (), {"api": api})()

    cases = (
        ProbeAPI(created=[]),
        ProbeAPI(created={"Id": ""}),
        ProbeAPI(inspected={"Running": False, "ExitCode": 1}),
        ProbeAPI(inspected={"Running": False, "ExitCode": None}),
        ProbeAPI(inspected={"Running": False, "ExitCode": "0"}),
        ProbeAPI(inspected={"Running": False, "ExitCode": True}),
        ProbeAPI(inspected={"Running": None, "ExitCode": 0}),
        ProbeAPI(inspected=[]),
        ProbeAPI(start_error=RuntimeError(f"probe failed for {private_token} at {private_path}")),
    )
    for api in cases:
        assert _default_native_tool_probe(Container(api)) is False
        assert api.create_calls == [
            (
                "sidecar-container-id",
                list(_NATIVE_TOOL_HEALTH_PROBE_COMMAND),
                {"stdout": False, "stderr": False},
            )
        ]

    assert _default_native_tool_probe(object()) is False
    captured = capsys.readouterr()
    assert private_token not in captured.out + captured.err + caplog.text
    assert private_path not in captured.out + captured.err + caplog.text


def test_default_native_tool_probe_accepts_completion_after_observed_probe_delay(monkeypatch):
    from app.runtime.sandbox import container_provider

    assert container_provider._NATIVE_TOOL_HEALTH_PROBE_TIMEOUT_SECONDS == 3.0

    class ProbeAPI:
        def __init__(self) -> None:
            self.inspect_calls = 0

        def exec_create(self, _container_id, _command, **_kwargs):
            return {"Id": "fixed-health-probe"}

        def exec_start(self, _exec_id, **_kwargs):
            return None

        def exec_inspect(self, _exec_id):
            self.inspect_calls += 1
            if self.inspect_calls == 1:
                return {"Running": True, "ExitCode": None}
            return {"Running": False, "ExitCode": 0}

    api = ProbeAPI()
    container = type(
        "Container",
        (),
        {"id": "sidecar-container-id", "client": type("DockerClient", (), {"api": api})()},
    )()
    monotonic_values = iter((0.0, 1.1, 1.1, 1.11))
    sleep_calls: list[float] = []
    monkeypatch.setattr(container_provider.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(container_provider.time, "sleep", sleep_calls.append)

    assert container_provider._default_native_tool_probe(container) is True
    assert api.inspect_calls == 2
    assert sleep_calls == pytest.approx([container_provider._NATIVE_TOOL_HEALTH_PROBE_POLL_INTERVAL_SECONDS])


def test_default_native_tool_probe_fails_closed_after_inspection_deadline(monkeypatch):
    from app.runtime.sandbox import container_provider

    class ProbeAPI:
        def __init__(self) -> None:
            self.inspect_calls = 0

        def exec_create(self, _container_id, _command, **_kwargs):
            return {"Id": "fixed-health-probe"}

        def exec_start(self, _exec_id, **_kwargs):
            return None

        def exec_inspect(self, _exec_id):
            self.inspect_calls += 1
            return {"Running": True, "ExitCode": None}

    api = ProbeAPI()
    container = type(
        "Container",
        (),
        {"id": "sidecar-container-id", "client": type("DockerClient", (), {"api": api})()},
    )()
    monotonic_values = iter((0.0, 2.99, 2.99, 3.0))
    sleep_calls: list[float] = []
    monkeypatch.setattr(container_provider.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(container_provider.time, "sleep", sleep_calls.append)

    assert container_provider._default_native_tool_probe(container) is False
    assert api.inspect_calls == 1
    assert sleep_calls == pytest.approx([container_provider._NATIVE_TOOL_HEALTH_PROBE_POLL_INTERVAL_SECONDS])


def test_default_native_tool_probe_rejects_missing_client_or_api():
    from app.runtime.sandbox.container_provider import _default_native_tool_probe

    assert _default_native_tool_probe(type("Container", (), {"id": "sidecar-container-id"})()) is False
    assert _default_native_tool_probe(
        type("Container", (), {"id": "sidecar-container-id", "client": object()})()
    ) is False


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
    sandbox_executor_image = "registry.example/ai-platform@sha256:" + "a" * 64
    sandbox_callback_base_url = "http://host.docker.internal:8020"
    sandbox_egress_policy_enabled = False
    sandbox_egress_proof_signing_key = "provider-test-proof-key-with-enough-entropy-2026"
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
    opensandbox_external_egress_capability_url = "http://127.0.0.1:18081/opensandbox/external-egress"
    opensandbox_external_egress_capability_token = "capability-test-token"
    opensandbox_external_egress_gateway_policy_subject = "gateway-policy-subject-a"
    opensandbox_external_egress_callback_boundary_subject = "callback-boundary-subject-a"
    opensandbox_executor_image_digest = "sha256:" + "a" * 64
    opensandbox_external_egress_profile_max_ttl_seconds = 300
    opensandbox_external_egress_profile_max_issued_age_seconds = 120
    opensandbox_external_egress_profile_clock_skew_seconds = 30
    opensandbox_external_egress_profile_min_remaining_seconds = 30


class ExternalEgressCapabilitySettings(OpenSandboxSettings):
    """Source-test settings for the required OpenSandbox runsc gateway profile."""


class IncompatibleOpenSandboxNetworkPolicySettings(ExternalEgressCapabilitySettings):
    sandbox_egress_policy_enabled = True


TEST_CAPABILITY_NOW = datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)


def external_egress_capability_profile(
    *,
    now: datetime = TEST_CAPABILITY_NOW,
    **overrides: Any,
) -> dict[str, Any]:
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
        "executor_image_digest": "sha256:" + "a" * 64,
        "issued_at": (now - timedelta(seconds=10)).isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(seconds=120)).isoformat().replace("+00:00", "Z"),
    }
    profile.update(overrides)
    return profile


def opensandbox_provider(*, health_probe=None, identity_probe=None, capability_profile_fetcher=None, utcnow=None):
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
        authoritative_attestation_probe=lambda _capability, runtime_request, sandbox_id, info: (
            isinstance(info, dict)
            and info.get("id") == sandbox_id
            and info.get("metadata", {}).get("ai-platform.tenant_id") == runtime_request.tenant_id
            and info.get("metadata", {}).get("ai-platform.run_id") == runtime_request.run_id
        ),
        utcnow=utcnow or (lambda: TEST_CAPABILITY_NOW),
    )


@pytest.mark.asyncio
async def test_opensandbox_governed_egress_fails_closed_without_authoritative_topology_attestation(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: ExternalEgressCapabilitySettings())
    provider = opensandbox_provider()
    provider._authoritative_attestation_probe = None

    with pytest.raises(container_provider.OpenSandboxCapabilityAdmissionError, match="unsupported"):
        await provider.create_or_reuse(request(), workspace())

    assert FakeOpenSandbox.instances == {}


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
            "http://127.0.0.1:18081/opensandbox/external-egress",
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
    proof = json.loads(lease.labels["ai-platform.governed_egress.proof"])
    assert proof["provider"] == "opensandbox"
    assert proof["default_deny_outbound"] is True
    assert proof["governed_callback_exception"] is True
    assert proof["policy_bound_enforcement"] is True
    assert "gateway-policy-subject-a" not in lease.labels["ai-platform.governed_egress.proof"]
    assert "ai-platform.external_egress.endpoint" not in lease.labels
    assert "network_policy" not in FakeOpenSandbox.created[0] or FakeOpenSandbox.created[0]["network_policy"] is None


class FakeCapabilityResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        chunks: list[bytes] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._chunks = chunks or []
        self.headers = headers or {}

    @property
    def is_redirect(self) -> bool:
        return 300 <= self.status_code < 400

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request_value = httpx.Request("GET", "http://127.0.0.1:18081/opensandbox/external-egress")
            response = httpx.Response(self.status_code, request=request_value)
            raise httpx.HTTPStatusError("response failure", request=request_value, response=response)

    def iter_bytes(self):
        yield from self._chunks


class FakeCapabilityStream:
    def __init__(self, response: FakeCapabilityResponse) -> None:
        self.response = response

    def __enter__(self) -> FakeCapabilityResponse:
        return self.response

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


class FakeCapabilityClient:
    def __init__(self, *, response: FakeCapabilityResponse | BaseException, calls: list[dict[str, Any]], **kwargs: Any) -> None:
        self.response = response
        self.calls = calls
        self.kwargs = kwargs

    def __enter__(self) -> "FakeCapabilityClient":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def stream(self, method: str, url: str, *, headers: dict[str, str]):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "kwargs": self.kwargs})
        if isinstance(self.response, BaseException):
            raise self.response
        return FakeCapabilityStream(self.response)


def install_default_capability_transport(monkeypatch, response: FakeCapabilityResponse | BaseException) -> list[dict[str, Any]]:
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        container_provider.httpx,
        "Client",
        lambda **kwargs: FakeCapabilityClient(response=response, calls=calls, **kwargs),
    )
    return calls


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",
        "https://8.8.8.8/egress",
        "https://capability.internal/egress",
        "https://localhost./egress",
        "http://10.1.2.3/egress",
        "https://10.1.2.3:0/egress",
        "https://user:pass@10.1.2.3/egress",
        "https://10.1.2.3/egress#fragment",
    ],
)
def test_default_capability_transport_rejects_unpinned_or_unsafe_targets_before_auth(monkeypatch, url):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    calls = install_default_capability_transport(monkeypatch, FakeCapabilityResponse(chunks=[b"{}"]))

    with pytest.raises(container_provider.OpenSandboxCapabilityAdmissionError, match="authenticated endpoint"):
        container_provider._default_opensandbox_capability_profile_fetcher(
            url,
            {"Authorization": "Bearer capability-test-token"},
            9999,
        )

    assert calls == []


def test_default_capability_transport_pins_loopback_caps_timeout_and_hides_response_details(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    payload = json.dumps(external_egress_capability_profile()).encode("utf-8")
    calls = install_default_capability_transport(monkeypatch, FakeCapabilityResponse(chunks=[payload]))

    loaded = container_provider._default_opensandbox_capability_profile_fetcher(
        "http://localhost:18081/opensandbox/external-egress",
        {"Authorization": "Bearer capability-test-token"},
        9999,
    )

    assert loaded["profile_id"] == "profile-a"
    assert calls[0]["url"] == "http://127.0.0.1:18081/opensandbox/external-egress"
    assert calls[0]["headers"] == {"Authorization": "Bearer capability-test-token"}
    assert calls[0]["kwargs"]["follow_redirects"] is False
    assert calls[0]["kwargs"]["trust_env"] is False
    assert calls[0]["kwargs"]["timeout"].connect <= 2.0
    assert calls[0]["kwargs"]["timeout"].read <= 2.0


def test_default_capability_transport_ignores_hostile_proxy_environment(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    proxy_url = "http://proxy.invalid/capability-test-token"
    monkeypatch.setenv("HTTP_PROXY", proxy_url)
    monkeypatch.setenv("HTTPS_PROXY", proxy_url)
    payload = json.dumps(external_egress_capability_profile()).encode("utf-8")
    calls = install_default_capability_transport(monkeypatch, FakeCapabilityResponse(chunks=[payload]))

    container_provider._default_opensandbox_capability_profile_fetcher(
        "http://127.0.0.1:18081/opensandbox/external-egress",
        {"Authorization": "Bearer capability-test-token"},
        1,
    )

    assert calls[0]["kwargs"]["trust_env"] is False
    assert proxy_url not in str(calls[0])


def test_default_capability_transport_bypasses_loopback_proxy_sentinel(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    target_requests: list[str] = []
    proxy_requests: list[str] = []
    payload = json.dumps(external_egress_capability_profile()).encode("utf-8")

    class TargetHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            target_requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):  # noqa: A002
            return None

    class ProxyHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            proxy_requests.append(self.path)
            self.send_response(502)
            self.end_headers()

        def log_message(self, format, *args):  # noqa: A002
            return None

    target = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
    target_thread = threading.Thread(target=target.serve_forever, daemon=True)
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    target_thread.start()
    proxy_thread.start()
    target_url = f"http://127.0.0.1:{target.server_port}/opensandbox/external-egress"
    proxy_url = f"http://127.0.0.1:{proxy.server_port}"
    monkeypatch.setenv("HTTP_PROXY", proxy_url)
    monkeypatch.setenv("HTTPS_PROXY", proxy_url)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    try:
        loaded = container_provider._default_opensandbox_capability_profile_fetcher(
            target_url,
            {"Authorization": "Bearer capability-test-token"},
            1,
        )
    finally:
        target.shutdown()
        proxy.shutdown()
        target.server_close()
        proxy.server_close()
        target_thread.join(timeout=2)
        proxy_thread.join(timeout=2)

    assert loaded["profile_id"] == "profile-a"
    assert target_requests == ["/opensandbox/external-egress"]
    assert proxy_requests == []


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer capability-test-token\r\nX-Injected: true"},
        {"Authorization": "Bearer capability-test-token", "X-Injected": "true"},
    ],
)
def test_default_capability_transport_rejects_invalid_headers_before_network(monkeypatch, headers):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    calls = install_default_capability_transport(monkeypatch, FakeCapabilityResponse(chunks=[b"{}"]))

    with pytest.raises(container_provider.OpenSandboxCapabilityAdmissionError, match="credential is invalid"):
        container_provider._default_opensandbox_capability_profile_fetcher(
            "http://127.0.0.1:18081/opensandbox/external-egress",
            headers,
            1,
        )

    assert calls == []


def test_default_capability_transport_enforces_total_timeout(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    calls = install_default_capability_transport(monkeypatch, FakeCapabilityResponse(chunks=[b"{}"]))
    monotonic_values = iter((0.0, 2.1))
    monkeypatch.setattr(container_provider.time, "monotonic", lambda: next(monotonic_values))

    with pytest.raises(container_provider.OpenSandboxCapabilityAdmissionError, match="request failed") as exc_info:
        container_provider._default_opensandbox_capability_profile_fetcher(
            "http://127.0.0.1:18081/opensandbox/external-egress",
            {"Authorization": "Bearer capability-test-token"},
            9999,
        )

    assert calls
    assert "capability-test-token" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


@pytest.mark.parametrize(
    "response, expected_message",
    [
        (FakeCapabilityResponse(status_code=302, headers={"location": "https://example.test"}), "redirect"),
        (FakeCapabilityResponse(chunks=[b"{" * (64 * 1024 + 1)]), "too large"),
        (FakeCapabilityResponse(chunks=[b"not-json"]), "malformed"),
        (httpx.ReadTimeout("capability-test-token timeout"), "request failed"),
        (RuntimeError("capability-test-token http://private.invalid"), "request failed"),
    ],
)
def test_default_capability_transport_fails_closed_without_secret_or_response_details(monkeypatch, response, expected_message):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    install_default_capability_transport(monkeypatch, response)

    with pytest.raises(container_provider.OpenSandboxCapabilityAdmissionError, match=expected_message) as exc_info:
        container_provider._default_opensandbox_capability_profile_fetcher(
            "http://127.0.0.1:18081/opensandbox/external-egress",
            {"Authorization": "Bearer capability-test-token"},
            1,
        )

    assert "capability-test-token" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


@pytest.mark.asyncio
async def test_default_capability_transport_surfaces_only_sanitized_auth_failure(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    settings = ExternalEgressCapabilitySettings()
    calls = install_default_capability_transport(monkeypatch, FakeCapabilityResponse(status_code=401))

    with pytest.raises(container_provider.OpenSandboxCapabilityAdmissionError, match="authentication failed") as exc_info:
        await container_provider._admit_opensandbox_external_egress_capability(
            settings=settings,
            fetcher=container_provider._default_opensandbox_capability_profile_fetcher,
            now=TEST_CAPABILITY_NOW,
        )

    assert calls and calls[0]["headers"] == {"Authorization": "Bearer capability-test-token"}
    assert "capability-test-token" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_token",
    ["capability-test-token\rX-Injected", "capability-test-token\nX-Injected", "capability\ttoken", "capability\x7ftoken"],
)
async def test_capability_token_control_characters_fail_before_network(monkeypatch, raw_token):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    settings = ExternalEgressCapabilitySettings()
    settings.opensandbox_external_egress_capability_token = raw_token
    calls = install_default_capability_transport(monkeypatch, FakeCapabilityResponse(chunks=[b"{}"]))

    with pytest.raises(container_provider.OpenSandboxCapabilityAdmissionError, match="credential is invalid") as exc_info:
        await container_provider._admit_opensandbox_external_egress_capability(
            settings=settings,
            fetcher=container_provider._default_opensandbox_capability_profile_fetcher,
            now=TEST_CAPABILITY_NOW,
        )

    assert calls == []
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_token", [" capability-test-token", "capability-test-token ", "capability\ttoken"])
async def test_capability_token_whitespace_fails_before_network(monkeypatch, raw_token):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    settings = ExternalEgressCapabilitySettings()
    settings.opensandbox_external_egress_capability_token = raw_token
    calls = install_default_capability_transport(monkeypatch, FakeCapabilityResponse(chunks=[b"{}"]))

    with pytest.raises(container_provider.OpenSandboxCapabilityAdmissionError, match="credential is invalid") as exc_info:
        await container_provider._admit_opensandbox_external_egress_capability(
            settings=settings,
            fetcher=container_provider._default_opensandbox_capability_profile_fetcher,
            now=TEST_CAPABILITY_NOW,
        )

    assert calls == []
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("image", "configured_digest", "expected_message"),
    [
        ("registry.example/ai-platform:latest", "sha256:" + "a" * 64, "immutable sha256"),
        ("registry.example/ai-platform", "sha256:" + "a" * 64, "immutable sha256"),
        (
            "registry.example/ai-platform@sha256:" + "a" * 64,
            "sha256:" + "b" * 64,
            "does not match",
        ),
    ],
)
async def test_opensandbox_provider_rejects_mutable_or_mismatched_requested_image(
    monkeypatch,
    image,
    configured_digest,
    expected_message,
):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    settings = ExternalEgressCapabilitySettings()
    settings.opensandbox_executor_image = image
    settings.opensandbox_executor_image_digest = configured_digest
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)

    with pytest.raises(container_provider.OpenSandboxCapabilityAdmissionError, match=expected_message):
        await opensandbox_provider().create_or_reuse(request(), workspace())

    assert FakeOpenSandbox.created == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "configured_digest",
    [None, "", " ", "sha256:not-a-digest", "sha256:" + "a" * 64 + " "],
)
async def test_opensandbox_provider_requires_nonblank_configured_digest_before_profile_fetch(
    monkeypatch,
    configured_digest,
):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    settings = ExternalEgressCapabilitySettings()
    settings.opensandbox_executor_image_digest = configured_digest
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    fetch_attempted = False

    def fetch_profile(*_args: Any) -> dict[str, Any]:
        nonlocal fetch_attempted
        fetch_attempted = True
        return external_egress_capability_profile()

    with pytest.raises(container_provider.OpenSandboxCapabilityAdmissionError, match="configured executor digest is invalid"):
        await opensandbox_provider(capability_profile_fetcher=fetch_profile).create_or_reuse(request(), workspace())

    assert fetch_attempted is False
    assert FakeOpenSandbox.created == []


@pytest.mark.asyncio
async def test_opensandbox_provider_uses_valid_requested_immutable_image(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    settings = ExternalEgressCapabilitySettings()
    settings.opensandbox_executor_image = "registry.example/team/ai-platform@sha256:" + "b" * 64
    settings.opensandbox_executor_image_digest = "sha256:" + "b" * 64
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)

    lease = await opensandbox_provider(
        capability_profile_fetcher=lambda *_args: external_egress_capability_profile(
            executor_image_digest="sha256:" + "b" * 64
        )
    ).create_or_reuse(request(), workspace())

    assert FakeOpenSandbox.created[0]["image"] == settings.opensandbox_executor_image
    assert lease.labels["ai-platform.executor.requested_image"] == settings.opensandbox_executor_image
    assert lease.labels["ai-platform.executor.requested_image_digest"] == "sha256:" + "b" * 64


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile", "expected_message"),
    [
        ({}, "capability profile"),
        ({"schema_version": "ai-platform.opensandbox.external-egress-capability.v0"}, "schema"),
        (
            {"issued_at": "2019-12-31T23:59:00Z", "expires_at": "2020-01-01T00:00:00Z"},
            "replayed",
        ),
        ({"runtime_identity": "runc"}, "runtime identity"),
        ({"executor_image_digest": ""}, "executor image digest"),
        ({"executor_image_digest": "sha256:not-a-digest"}, "executor image digest"),
        ({"executor_image_digest": "sha256:" + "b" * 64}, "executor image digest mismatch"),
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
async def test_capability_timestamp_validation_suppresses_raw_profile_context(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: ExternalEgressCapabilitySettings())
    payload = external_egress_capability_profile(issued_at="not-a-timestamp-profile-value")

    with pytest.raises(container_provider.OpenSandboxCapabilityAdmissionError, match="issued_at is invalid") as exc_info:
        await opensandbox_provider(capability_profile_fetcher=lambda *_args: payload).create_or_reuse(request(), workspace())

    assert "not-a-timestamp-profile-value" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True
    assert FakeOpenSandbox.created == []


@pytest.mark.asyncio
async def test_capability_port_validation_suppresses_raw_url_context(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    settings = ExternalEgressCapabilitySettings()
    settings.opensandbox_external_egress_capability_url = "http://127.0.0.1:not-a-port/egress"
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    fetch_called = False

    def fetch_profile(*_args: Any) -> dict[str, Any]:
        nonlocal fetch_called
        fetch_called = True
        return external_egress_capability_profile()

    with pytest.raises(container_provider.OpenSandboxCapabilityAdmissionError, match="authenticated endpoint is invalid") as exc_info:
        await opensandbox_provider(capability_profile_fetcher=fetch_profile).create_or_reuse(request(), workspace())

    assert fetch_called is False
    assert "not-a-port" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True
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
async def test_opensandbox_dispatch_accepts_the_current_capability_proof(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    settings = ExternalEgressCapabilitySettings()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    provider = opensandbox_provider()
    sandbox_request = request()
    leased_workspace = workspace()

    lease = await provider.create_or_reuse(sandbox_request, leased_workspace)
    await provider.validate_for_dispatch(lease, sandbox_request, leased_workspace)

    sandbox = FakeOpenSandbox.instances[lease.container_id]
    assert sandbox.killed is False
    assert provider._leases[f"opensandbox-{lease.run_id}"] is lease
    assert provider._sandboxes[lease.container_id] is sandbox


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile_overrides", "setting_overrides"),
    [
        pytest.param({"profile_id": "profile-b"}, {}, id="profile"),
        pytest.param(
            {"opensandbox_endpoint": "http://opensandbox-rotated.local:8080"},
            {"opensandbox_domain": "opensandbox-rotated.local:8080"},
            id="endpoint",
        ),
        pytest.param(
            {"ai_platform_runtime_subject": "runtime-subject-b"},
            {"sandbox_runtime_subject": "runtime-subject-b"},
            id="runtime-subject",
        ),
        pytest.param(
            {"gateway_policy_subject": "gateway-policy-subject-b"},
            {"opensandbox_external_egress_gateway_policy_subject": "gateway-policy-subject-b"},
            id="gateway-policy-subject",
        ),
        pytest.param(
            {"callback_boundary_subject": "callback-boundary-subject-b"},
            {"opensandbox_external_egress_callback_boundary_subject": "callback-boundary-subject-b"},
            id="callback-subject",
        ),
        pytest.param({"deny_audit_subject": "gateway-deny-audit-subject-b"}, {}, id="deny-audit-subject"),
        pytest.param(
            {"deny_counter_subject": "gateway-deny-counter-subject-b"},
            {},
            id="deny-counter-subject",
        ),
        pytest.param(
            {"executor_image_digest": "sha256:" + "b" * 64},
            {
                "opensandbox_executor_image": "registry.example/ai-platform@sha256:" + "b" * 64,
                "opensandbox_executor_image_digest": "sha256:" + "b" * 64,
            },
            id="executor-image",
        ),
    ],
)
async def test_opensandbox_dispatch_rejects_each_rotated_capability_subject_and_cleans_old_sandbox(
    monkeypatch,
    profile_overrides,
    setting_overrides,
):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    settings = ExternalEgressCapabilitySettings()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    profiles = iter(
        (
            external_egress_capability_profile(),
            external_egress_capability_profile(**profile_overrides),
        )
    )
    provider = opensandbox_provider(capability_profile_fetcher=lambda *_args: next(profiles))
    sandbox_request = request()
    leased_workspace = workspace()
    lease = await provider.create_or_reuse(sandbox_request, leased_workspace)
    sandbox = FakeOpenSandbox.instances[lease.container_id]
    for name, value in setting_overrides.items():
        setattr(settings, name, value)

    with pytest.raises(container_provider.OpenSandboxCapabilityAdmissionError, match="dispatch proof is stale"):
        await provider.validate_for_dispatch(lease, sandbox_request, leased_workspace)

    assert sandbox.killed is True
    assert sandbox.closed is True
    assert lease.container_id not in provider._sandboxes
    assert f"opensandbox-{lease.run_id}" not in provider._leases


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile", "expected_message"),
    [
        (lambda: external_egress_capability_profile(expires_at="2026-07-14T18:00:00Z"), "ttl"),
        (
            lambda: external_egress_capability_profile(
                issued_at="2026-07-14T15:57:50Z",
                expires_at="2026-07-14T16:01:00Z",
            ),
            "replayed",
        ),
        (lambda: external_egress_capability_profile(expires_at="2026-07-14T16:00:10Z"), "remaining"),
        (lambda: external_egress_capability_profile(issued_at="2026-07-14T16:01:00Z"), "not yet valid"),
    ],
)
async def test_opensandbox_provider_rejects_long_lived_replayed_or_near_expiry_profiles(monkeypatch, profile, expected_message):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: ExternalEgressCapabilitySettings())

    with pytest.raises(container_provider.OpenSandboxCapabilityAdmissionError, match=expected_message):
        await opensandbox_provider(capability_profile_fetcher=lambda *_args: profile()).create_or_reuse(request(), workspace())

    assert FakeOpenSandbox.created == []


@pytest.mark.asyncio
async def test_opensandbox_provider_rechecks_expired_profile_after_health_and_cleans_sandbox(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: ExternalEgressCapabilitySettings())
    current_time = [TEST_CAPABILITY_NOW]

    def health_probe(*_args: Any) -> bool:
        current_time[0] = TEST_CAPABILITY_NOW + timedelta(seconds=100)
        return True

    provider = opensandbox_provider(
        health_probe=health_probe,
        capability_profile_fetcher=lambda *_args: external_egress_capability_profile(),
        utcnow=lambda: current_time[0],
    )

    with pytest.raises(container_provider.OpenSandboxCapabilityAdmissionError, match="remaining"):
        await provider.create_or_reuse(request(), workspace())

    sandbox = FakeOpenSandbox.instances["osb-run-a"]
    assert sandbox.killed is True
    assert "osb-run-a" not in provider._sandboxes


@pytest.mark.asyncio
async def test_opensandbox_provider_rejects_valid_profile_rotation_in_cached_reuse_and_cleans_old_sandbox(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: ExternalEgressCapabilitySettings())
    profiles = iter(
        [
            external_egress_capability_profile(profile_id="profile-a"),
            external_egress_capability_profile(profile_id="profile-b", expires_at="2026-07-14T16:02:10Z"),
        ]
    )
    provider = opensandbox_provider(capability_profile_fetcher=lambda *_args: next(profiles))
    lease = await provider.create_or_reuse(request(), workspace())

    with pytest.raises(container_provider.ContainerStartFailedError, match="metadata mismatch"):
        await provider.create_or_reuse(request(), workspace())

    assert FakeOpenSandbox.instances[lease.container_id].killed is True
    assert lease.container_id not in provider._sandboxes
    assert f"opensandbox-{lease.run_id}" not in provider._leases


@pytest.mark.asyncio
async def test_opensandbox_cached_reuse_cleans_old_lease_when_profile_digest_changes(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: ExternalEgressCapabilitySettings())
    profiles = iter(
        [
            external_egress_capability_profile(),
            external_egress_capability_profile(executor_image_digest="sha256:" + "b" * 64),
        ]
    )
    provider = opensandbox_provider(capability_profile_fetcher=lambda *_args: next(profiles))
    lease = await provider.create_or_reuse(request(), workspace())

    with pytest.raises(container_provider.OpenSandboxCapabilityAdmissionError, match="executor image digest mismatch"):
        await provider.create_or_reuse(request(), workspace())

    assert FakeOpenSandbox.instances[lease.container_id].killed is True
    assert lease.container_id not in provider._sandboxes
    assert f"opensandbox-{lease.run_id}" not in provider._leases


@pytest.mark.asyncio
async def test_opensandbox_cached_reuse_cleans_old_lease_when_profile_expiry_changes(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: ExternalEgressCapabilitySettings())
    profiles = iter(
        [
            external_egress_capability_profile(),
            external_egress_capability_profile(expires_at="2026-07-14T16:02:10Z"),
        ]
    )
    provider = opensandbox_provider(capability_profile_fetcher=lambda *_args: next(profiles))
    lease = await provider.create_or_reuse(request(), workspace())

    with pytest.raises(container_provider.ContainerStartFailedError, match="metadata mismatch"):
        await provider.create_or_reuse(request(), workspace())

    assert FakeOpenSandbox.instances[lease.container_id].killed is True
    assert lease.container_id not in provider._sandboxes
    assert f"opensandbox-{lease.run_id}" not in provider._leases


@pytest.mark.asyncio
async def test_opensandbox_cached_reuse_cleans_old_lease_when_requested_image_changes(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    settings = ExternalEgressCapabilitySettings()
    profiles = iter(
        [
            external_egress_capability_profile(),
            external_egress_capability_profile(executor_image_digest="sha256:" + "b" * 64),
        ]
    )
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    provider = opensandbox_provider(capability_profile_fetcher=lambda *_args: next(profiles))
    lease = await provider.create_or_reuse(request(), workspace())
    settings.opensandbox_executor_image = "registry.example/ai-platform@sha256:" + "b" * 64
    settings.opensandbox_executor_image_digest = "sha256:" + "b" * 64

    with pytest.raises(container_provider.ContainerStartFailedError, match="metadata mismatch"):
        await provider.create_or_reuse(request(), workspace())

    assert FakeOpenSandbox.instances[lease.container_id].killed is True
    assert lease.container_id not in provider._sandboxes
    assert f"opensandbox-{lease.run_id}" not in provider._leases


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "label",
    [
        "ai-platform.external_egress.profile_version",
        "ai-platform.external_egress.profile_id",
        "ai-platform.external_egress.runtime_identity",
        "ai-platform.external_egress.gateway_policy_subject",
        "ai-platform.external_egress.callback_boundary_subject",
        "ai-platform.external_egress.deny_audit_subject",
        "ai-platform.external_egress.deny_counter_subject",
        "ai-platform.external_egress.profile_requested_image",
        "ai-platform.external_egress.profile_requested_image_digest",
        "ai-platform.external_egress.profile_expires_at",
        "ai-platform.executor.requested_image",
        "ai-platform.executor.requested_image_digest",
        "ai-platform.runtime_subject",
    ],
)
async def test_opensandbox_cached_reuse_rejects_each_external_egress_or_runtime_subject_label(monkeypatch, label):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: ExternalEgressCapabilitySettings())
    provider = opensandbox_provider()
    lease = await provider.create_or_reuse(request(), workspace())
    sandbox = FakeOpenSandbox.instances[lease.container_id]
    sandbox.metadata[label] = "drifted"

    with pytest.raises(container_provider.ContainerStartFailedError, match="metadata mismatch"):
        await provider.create_or_reuse(request(), workspace())

    assert sandbox.killed is True
    assert lease.container_id not in provider._sandboxes
    assert f"opensandbox-{lease.run_id}" not in provider._leases


@pytest.mark.asyncio
async def test_opensandbox_provider_retains_rotated_cached_lease_when_cleanup_cannot_be_confirmed(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: ExternalEgressCapabilitySettings())
    profiles = iter(
        [
            external_egress_capability_profile(profile_id="profile-a"),
            external_egress_capability_profile(profile_id="profile-b", expires_at="2026-07-14T16:02:10Z"),
        ]
    )
    provider = opensandbox_provider(capability_profile_fetcher=lambda *_args: next(profiles))
    lease = await provider.create_or_reuse(request(), workspace())
    FakeOpenSandbox.instances[lease.container_id].kill_error = RuntimeError("kill unavailable")

    with pytest.raises(container_provider.ContainerCleanupFailedError):
        await provider.create_or_reuse(request(), workspace())

    assert provider._sandboxes[lease.container_id] is FakeOpenSandbox.instances[lease.container_id]
    assert provider._leases[f"opensandbox-{lease.run_id}"] is lease


@pytest.mark.asyncio
async def test_opensandbox_seals_actual_id_and_fails_closed_after_restart_without_duplicate(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    from app.execution_boundary import governed_egress_proof_from_labels

    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    settings = ExternalEgressCapabilitySettings()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    first = opensandbox_provider()
    lease = await first.create_or_reuse(request(), workspace())
    proof = governed_egress_proof_from_labels(
        "opensandbox",
        lease.labels,
        signing_key=settings.sandbox_egress_proof_signing_key,
        expected_binding={"lease_identity": f"opensandbox:{lease.container_name}:{lease.container_id}"},
        now=TEST_CAPABILITY_NOW,
    )

    assert proof is not None
    FakeOpenSandboxManager.sandboxes = [FakeOpenSandbox.instances[lease.container_id]]
    restarted = opensandbox_provider()
    with pytest.raises(container_provider.ContainerStartFailedError, match="existing credential"):
        await restarted.create_or_reuse(request(), workspace())

    assert len(FakeOpenSandbox.created) == 1
    assert FakeOpenSandbox.instances[lease.container_id].killed is False


@pytest.mark.asyncio
async def test_opensandbox_sealed_proof_expiry_is_bounded_by_capability_and_policy(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    from app.execution_boundary import GOVERNED_EGRESS_PROOF_MAX_TTL_SECONDS

    settings = ExternalEgressCapabilitySettings()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    capability = await container_provider._admit_opensandbox_external_egress_capability(
        settings=settings,
        fetcher=lambda *_args: external_egress_capability_profile(),
        now=TEST_CAPABILITY_NOW,
    )
    short = capability.governed_egress_proof(
        signing_key=settings.sandbox_egress_proof_signing_key,
        request=request(),
        lease_identity="opensandbox:opensandbox-run-a:osb-run-a",
        now=TEST_CAPABILITY_NOW,
    )
    long_capability = replace(
        capability,
        expires_at_utc=TEST_CAPABILITY_NOW + timedelta(seconds=GOVERNED_EGRESS_PROOF_MAX_TTL_SECONDS + 60),
    )
    bounded = long_capability.governed_egress_proof(
        signing_key=settings.sandbox_egress_proof_signing_key,
        request=request(),
        lease_identity="opensandbox:opensandbox-run-a:osb-run-a",
        now=TEST_CAPABILITY_NOW,
    )

    assert short["expires_at"] == capability.expires_at_utc.isoformat().replace("+00:00", "Z")
    assert bounded["expires_at"] == (
        TEST_CAPABILITY_NOW + timedelta(seconds=GOVERNED_EGRESS_PROOF_MAX_TTL_SECONDS)
    ).isoformat().replace("+00:00", "Z")


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
async def test_fake_provider_cannot_emit_governed_egress_proof():
    from app.execution_boundary import governed_egress_proof_from_labels
    from app.runtime.sandbox.container_provider import FakeContainerProvider

    lease = await FakeContainerProvider().create_or_reuse(request(), workspace())

    assert governed_egress_proof_from_labels(
        lease.provider,
        lease.labels,
        signing_key="provider-test-proof-key-with-enough-entropy-2026",
    ) is None


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
    assert created["image"] == "registry.example/ai-platform@sha256:" + "a" * 64
    assert created["name"] == "executor-exec-run-a"
    assert created["labels"]["ai-platform.run_id"] == "run-a"
    assert created["volumes"][workspace().workspace_host_path]["bind"] == "/workspace"
    assert created["volumes"] == {
        workspace().workspace_host_path: {"bind": "/workspace", "mode": "rw"}
    }
    assert created["labels"]["ai-platform.skill_mount.required"] == "false"
    assert "AI_PLATFORM_NATIVE_TOOL_TOKEN" not in created["environment"]
    assert "AI_PLATFORM_NATIVE_TOOL_SOCKET" not in created["environment"]
    assert created["environment"]["AI_PLATFORM_SESSION_ID"] == "session-a"
    assert created["environment"]["APP_MODULE"] == "app.runtime.sandbox.executor_app:create_executor_app"
    assert created["environment"]["APP_PORT"] == "18000"
    assert lease.executor_url == "http://127.0.0.1:18000"
    assert statuses[0].run_id == "run-a"
    assert statuses[0].sandbox_mode == "ephemeral"


@pytest.mark.asyncio
async def test_platform_controlled_implicit_catalog_mounts_claude_without_native_sidecar(tmp_path):
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    workspace_path = tmp_path / "run" / "workspace"
    workspace_path.mkdir(parents=True)
    leased_workspace = workspace(workspace_host_path=str(workspace_path))
    staged_skill, native_bash = native_tool_subjects()
    controlled_bash = {
        **native_bash,
        "execution_strategy": "platform_controlled",
        "command_isolation": "minimal-environment-v1",
    }
    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )

    lease = await provider.create_or_reuse(
        request(tool_policy_subjects=[staged_skill, controlled_bash]),
        leased_workspace,
    )

    assert [created["name"] for created in fake.created] == ["executor-exec-run-a"]
    executor = fake.containers_by_name[lease.container_name]
    assert executor.volumes[str(workspace_path)] == {"bind": "/workspace", "mode": "rw"}
    assert executor.volumes[str((workspace_path / ".claude").resolve())] == {
        "bind": "/workspace/.claude",
        "mode": "ro",
    }
    assert "AI_PLATFORM_NATIVE_TOOL_TOKEN" not in executor.environment
    assert "AI_PLATFORM_NATIVE_TOOL_SOCKET" not in executor.environment


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_leaf", [".claude", ".claude/skills"])
async def test_docker_provider_rejects_missing_staged_skill_directories_before_create(
    tmp_path,
    missing_leaf,
):
    from app.runtime.sandbox.container_provider import ContainerStartFailedError, DockerContainerProvider

    workspace_path = tmp_path / "run" / "workspace"
    workspace_path.mkdir(parents=True)
    if missing_leaf != ".claude":
        (workspace_path / ".claude").mkdir()
    leased_workspace = workspace(
        workspace_host_path=str(workspace_path),
        prepare_staged_skills=False,
    )
    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )

    with pytest.raises(ContainerStartFailedError, match="staged Skill"):
        await provider.create_or_reuse(
            request(tool_policy_subjects=native_tool_subjects()[:1]),
            leased_workspace,
        )

    assert fake.created == []


@pytest.mark.asyncio
async def test_docker_provider_rejects_escaped_staged_skill_workspace_before_create(
    tmp_path,
):
    from app.runtime.sandbox.container_provider import ContainerStartFailedError, DockerContainerProvider

    trusted_root = tmp_path / "trusted"
    trusted_root.mkdir()
    outside_workspace = tmp_path / "outside" / "workspace"
    outside_workspace.mkdir(parents=True)
    (outside_workspace / ".claude" / "skills").mkdir(parents=True)
    leased_workspace = workspace(
        host_root=str(trusted_root),
        workspace_host_path=str(outside_workspace),
        prepare_staged_skills=False,
    )
    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )

    with pytest.raises(ContainerStartFailedError, match="staged Skill workspace escapes"):
        await provider.create_or_reuse(
            request(tool_policy_subjects=native_tool_subjects()[:1]),
            leased_workspace,
        )

    assert fake.created == []


@pytest.mark.asyncio
@pytest.mark.parametrize("symlink_leaf", [".claude", ".claude/skills"])
async def test_docker_provider_rejects_symlinked_staged_skill_directories_before_create(
    tmp_path,
    symlink_leaf,
):
    from app.runtime.sandbox.container_provider import ContainerStartFailedError, DockerContainerProvider

    workspace_path = tmp_path / "run" / "workspace"
    outside_claude = tmp_path / "outside-claude"
    workspace_path.mkdir(parents=True)
    (outside_claude / "skills").mkdir(parents=True)
    if symlink_leaf == ".claude/skills":
        (workspace_path / ".claude").mkdir()
        symlink_target = outside_claude / "skills"
    else:
        symlink_target = outside_claude
    try:
        (workspace_path / symlink_leaf).symlink_to(symlink_target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this host")
    leased_workspace = workspace(
        workspace_host_path=str(workspace_path),
        prepare_staged_skills=False,
    )
    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )

    with pytest.raises(ContainerStartFailedError, match="staged Skill"):
        await provider.create_or_reuse(
            request(tool_policy_subjects=native_tool_subjects()[:1]),
            leased_workspace,
        )

    assert fake.created == []


@pytest.mark.asyncio
async def test_docker_provider_scrubs_settings_and_preserves_delivery_writes_before_read_only_mount(
    tmp_path,
):
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    workspace_path = tmp_path / "run" / "workspace"
    (workspace_path / ".claude" / "skills" / "review-skill").mkdir(parents=True)
    (workspace_path / "outputs" / "delivery").mkdir(parents=True)
    for setting_name in ("settings.json", "settings.local.json"):
        (workspace_path / ".claude" / setting_name).write_text("stale", encoding="utf-8")
    leased_workspace = workspace(workspace_host_path=str(workspace_path))
    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )

    await provider.create_or_reuse(
        request(tool_policy_subjects=native_tool_subjects()[:1]),
        leased_workspace,
    )

    assert not (workspace_path / ".claude" / "settings.json").exists()
    assert not (workspace_path / ".claude" / "settings.local.json").exists()
    output_path = workspace_path / "outputs" / "delivery" / "report.txt"
    output_path.write_text("deliverable", encoding="utf-8")
    assert output_path.read_text(encoding="utf-8") == "deliverable"
    created = fake.created[0]
    assert created["volumes"][str(workspace_path)]["mode"] == "rw"
    assert created["volumes"][str((workspace_path / ".claude").resolve())] == {
        "bind": "/workspace/.claude",
        "mode": "ro",
    }


@pytest.mark.asyncio
async def test_docker_provider_rejects_unscrubbable_project_settings_before_create(tmp_path):
    from app.runtime.sandbox.container_provider import ContainerStartFailedError, DockerContainerProvider

    workspace_path = tmp_path / "run" / "workspace"
    (workspace_path / ".claude" / "skills").mkdir(parents=True)
    (workspace_path / ".claude" / "settings.json").mkdir()
    leased_workspace = workspace(workspace_host_path=str(workspace_path))
    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )

    with pytest.raises(ContainerStartFailedError, match="settings path is invalid"):
        await provider.create_or_reuse(
            request(tool_policy_subjects=native_tool_subjects()[:1]),
            leased_workspace,
        )

    assert fake.created == []


@pytest.mark.asyncio
async def test_docker_provider_rejects_cached_executor_after_staged_skill_inode_changes(tmp_path):
    from app.runtime.sandbox.container_provider import ContainerStartFailedError, DockerContainerProvider

    workspace_path = tmp_path / "run" / "workspace"
    (workspace_path / ".claude" / "skills").mkdir(parents=True)
    leased_workspace = workspace(workspace_host_path=str(workspace_path))
    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )
    runtime_request = request(tool_policy_subjects=native_tool_subjects()[:1])

    first_lease = await provider.create_or_reuse(runtime_request, leased_workspace)
    first_container = fake.containers_by_name[first_lease.container_name]
    (workspace_path / ".claude").rename(workspace_path / ".claude-old")
    (workspace_path / ".claude" / "skills").mkdir(parents=True)

    with pytest.raises(ContainerStartFailedError, match="cached lease runtime profile mismatch"):
        await provider.create_or_reuse(runtime_request, leased_workspace)

    assert first_container.removed is True
    assert provider._leases == {}


@pytest.mark.asyncio
async def test_docker_provider_maps_failed_native_reuse_health_to_admission_failure(tmp_path):
    from app.runtime.sandbox.container_provider import DockerContainerProvider, NativeToolAdmissionError

    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    leased_workspace = workspace(workspace_host_path=str(workspace_path))
    native_subjects = native_tool_subjects()
    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )

    lease = await provider.create_or_reuse(
        request(skill_ids=["native-review"], tool_policy_subjects=native_subjects),
        leased_workspace,
    )

    assert native_subjects[0]["execution_strategy"] == "sdk_restricted"
    assert native_subjects[0]["allowed_skill_names"] == ["native-review"]
    assert [created["name"] for created in fake.created] == ["native-tool-run-a", "executor-exec-run-a"]
    sidecar, executor = fake.created
    assert sidecar["network_mode"] == "none"
    assert "network_disabled" not in sidecar
    assert sidecar["entrypoint"] == ["python", "-m", "app.runtime.sandbox.native_tool_app"]
    assert sidecar["command"] == []
    assert sidecar["user"] == "10001:10001"
    assert sidecar["privileged"] is False
    assert sidecar["security_opt"] == ["no-new-privileges:true"]
    assert sidecar["cap_drop"] == ["ALL"]
    assert "cap_add" not in sidecar
    assert sidecar["read_only"] is True
    expected_skill_mount = {
        "bind": "/workspace/.claude",
        "mode": "ro",
    }
    assert sidecar["volumes"][str(workspace_path)] == {"bind": "/workspace", "mode": "rw"}
    assert executor["volumes"][str(workspace_path)] == {"bind": "/workspace", "mode": "rw"}
    assert sidecar["volumes"][str((workspace_path / ".claude").resolve())] == expected_skill_mount
    assert executor["volumes"][str((workspace_path / ".claude").resolve())] == expected_skill_mount
    mount_fingerprint = lease.labels["ai-platform.skill_mount.fingerprint"]
    assert len(mount_fingerprint) == 64
    assert sidecar["labels"]["ai-platform.skill_mount.fingerprint"] == mount_fingerprint
    assert executor["labels"]["ai-platform.skill_mount.fingerprint"] == mount_fingerprint
    kernel_attack_specs = {
        "direct": "printf tampered > /workspace/.claude/skills/native-review/SKILL.md",
        "chmod": "chmod u+w /workspace/.claude/skills/native-review/SKILL.md",
        "rm": "rm -rf /workspace/.claude/skills/native-review",
        "rename": "mv /workspace/.claude/skills/native-review /workspace/.claude/skills/replaced",
        "symlink": "ln -s /workspace/outputs/delivery /workspace/.claude/skills/output-link",
    }
    assert set(kernel_attack_specs) == {"direct", "chmod", "rm", "rename", "symlink"}
    assert all("/workspace/.claude" in command for command in kernel_attack_specs.values())
    assert sidecar["tmpfs"] == {
        "/tmp": "rw,noexec,nosuid,nodev,uid=10001,gid=10001,mode=0700,size=64m",
        "/home/ai-platform": "rw,noexec,nosuid,nodev,uid=10001,gid=10001,mode=0700,size=32m",
    }
    assert sidecar["environment"] == {
        "AI_PLATFORM_NATIVE_TOOL_TOKEN": executor["environment"]["AI_PLATFORM_NATIVE_TOOL_TOKEN"],
        "AI_PLATFORM_NATIVE_TOOL_WORKSPACE": "/workspace",
        "AI_PLATFORM_NATIVE_TOOL_SOCKET": "/workspace/.ai-platform/native-tool.sock",
        "AI_PLATFORM_NATIVE_TOOL_UID": "10001",
        "AI_PLATFORM_NATIVE_TOOL_GID": "10001",
        "HOME": "/home/ai-platform",
        "TMPDIR": "/tmp",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }
    assert all("AUTH" not in key and "CALLBACK" not in key for key in sidecar["environment"])

    native = fake.containers_by_name["native-tool-run-a"]
    primary = fake.containers_by_name[lease.container_name]
    assert fake.api.exec_create_calls == [
        (
            native.id,
            ["python", "-m", "app.runtime.sandbox.native_tool_health_probe"],
            {"stdout": False, "stderr": False},
        )
    ]
    assert fake.api.exec_start_calls == [("exec-1", {"detach": True})]
    assert fake.api.exec_inspect_calls == ["exec-1"]
    native._exec_exit_code = 1

    with pytest.raises(NativeToolAdmissionError) as exc_info:
        await provider.create_or_reuse(
            request(skill_ids=["native-review"], tool_policy_subjects=native_subjects),
            leased_workspace,
        )

    assert exc_info.value.error_code == "native_tool_admission_failed"
    assert str(exc_info.value) == "Native tool sandbox admission failed"
    assert native.removed is True
    assert primary.removed is True
    assert [created["name"] for created in fake.created] == ["native-tool-run-a", "executor-exec-run-a"]


@pytest.mark.asyncio
async def test_docker_provider_sanitizes_native_sidecar_admission_failure_without_executor_lease(tmp_path):
    from app.runtime.sandbox.container_provider import DockerContainerProvider, NativeToolAdmissionError

    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    native_subjects = [
        {
            "identity": "Skill",
            "declared_identities": ["Skill"],
            "registered": True,
            "declared": True,
            "active": True,
            "distributed": True,
            "execution_strategy": "sdk_native",
        },
        {
            "identity": "Bash",
            "declared_identities": ["Bash"],
            "registered": True,
            "declared": True,
            "active": True,
            "distributed": True,
            "command_isolation": "sibling-tool-sandbox-v1",
        },
    ]
    fake = FakeDockerClient(start_error=RuntimeError(f"cannot mount {workspace_path}"))
    provider = DockerContainerProvider(docker_client_factory=lambda: fake)

    with pytest.raises(NativeToolAdmissionError) as exc_info:
        await provider.create_or_reuse(
            request(skill_ids=["native-review"], tool_policy_subjects=native_subjects),
            workspace(workspace_host_path=str(workspace_path)),
        )

    assert exc_info.value.error_code == "native_tool_admission_failed"
    assert str(exc_info.value) == "Native tool sandbox admission failed"
    assert str(workspace_path) not in str(exc_info.value)
    assert str(workspace_path) not in "".join(
        traceback.format_exception(exc_info.value)
    )
    assert [created["name"] for created in fake.created] == ["native-tool-run-a"]
    assert fake.containers_by_name["native-tool-run-a"].removed is True
    assert provider._leases == {}


@pytest.mark.asyncio
async def test_docker_provider_occupied_native_socket_preflight_has_zero_false_runtime_evidence(
    monkeypatch,
    tmp_path,
):
    import app.runtime.sandbox.container_provider as container_provider
    from app.runtime.sandbox.container_provider import DockerContainerProvider, NativeToolAdmissionError

    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    leased_workspace = workspace(workspace_host_path=str(workspace_path))
    settings = container_provider.get_settings().model_copy(
        update={
            "sandbox_workspace_root": str(tmp_path.parent / "o"),
            "sandbox_executor_image": "registry.example/ai-platform@sha256:" + "a" * 64,
            "sandbox_egress_policy_enabled": True,
                "sandbox_callback_base_url": "http://api.sandbox.internal:8020",
                "sandbox_egress_proof_signing_key": "provider-test-proof-key-with-enough-entropy-2026",
                "ai_platform_runtime_commit": "a" * 40,
        }
    )
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    fake = FakeDockerClient()
    provider = DockerContainerProvider(docker_client_factory=lambda: fake)
    socket_parent = provider._native_tool_socket_host_path(leased_workspace).parent
    occupied_path = socket_parent / "native-tool.sock"
    assert occupied_path == provider._native_tool_socket_host_path(leased_workspace)
    socket_parent.mkdir(parents=True)
    occupied_path.write_text("owned by another subject", encoding="utf-8")
    native_subjects = native_tool_subjects()

    with pytest.raises(NativeToolAdmissionError) as exc_info:
        await provider.create_or_reuse(
            request(skill_ids=["native-review"], tool_policy_subjects=native_subjects),
            leased_workspace,
        )

    assert exc_info.value.error_code == "native_tool_admission_failed"
    assert str(exc_info.value) == "Native tool sandbox admission failed"
    assert occupied_path.is_file()
    assert fake.created == []
    assert provider._leases == {}


@pytest.mark.asyncio
async def test_docker_provider_rejects_preexisting_socket_directory_with_wrong_owner(
    monkeypatch,
    tmp_path,
):
    import app.runtime.sandbox.container_provider as container_provider
    from app.runtime.sandbox.container_provider import DockerContainerProvider, NativeToolAdmissionError

    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    leased_workspace = workspace(workspace_host_path=str(workspace_path))
    settings = container_provider.get_settings().model_copy(
        update={
            "sandbox_workspace_root": str(tmp_path.parent / "w"),
            "sandbox_executor_image": "registry.example/ai-platform@sha256:" + "a" * 64,
            "sandbox_egress_policy_enabled": True,
                "sandbox_callback_base_url": "http://api.sandbox.internal:8020",
                "sandbox_egress_proof_signing_key": "provider-test-proof-key-with-enough-entropy-2026",
                "ai_platform_runtime_commit": "a" * 40,
        }
    )
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    fake = FakeDockerClient()
    provider = DockerContainerProvider(docker_client_factory=lambda: fake)
    socket_dir = provider._native_tool_socket_host_path(leased_workspace).parent
    socket_dir.mkdir(parents=True)
    expected_stat = type(
        "ExpectedRuntimeWorkspaceStat",
        (),
        {"st_uid": 10001, "st_gid": 10001, "st_mode": 0o40700},
    )()
    wrong_owner_stat = type(
        "WrongNativeSocketOwnerStat",
        (),
        {"st_uid": 10002, "st_gid": 10001, "st_mode": 0o40700},
    )()
    monkeypatch.setattr(
        container_provider,
        "_workspace_owner_stat",
        lambda path: wrong_owner_stat if Path(path) == socket_dir else expected_stat,
    )
    native_subjects = native_tool_subjects()

    with pytest.raises(NativeToolAdmissionError):
        await provider.create_or_reuse(
            request(skill_ids=["native-review"], tool_policy_subjects=native_subjects),
            leased_workspace,
        )

    assert socket_dir.is_dir()
    assert fake.created == []
    assert provider._leases == {}


@pytest.mark.asyncio
async def test_docker_provider_native_probe_timeout_is_admission_failure_without_false_runtime_evidence(
    monkeypatch,
    tmp_path,
):
    from app.runtime.sandbox.container_provider import (
        DockerContainerProvider,
        ExecutorHealthTimeoutError,
        NativeToolAdmissionError,
    )

    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    probe_calls = []
    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        native_tool_probe=lambda container: probe_calls.append(container) or False,
    )

    async def false_probe_timeout(container, _timeout_seconds):
        assert provider._native_tool_probe(container) is False
        raise ExecutorHealthTimeoutError("native tool sandbox did not become ready")

    monkeypatch.setattr(provider, "_wait_for_native_tool_socket", false_probe_timeout)
    native_subjects = [
        {
            "identity": "Skill",
            "declared_identities": ["Skill"],
            "registered": True,
            "declared": True,
            "active": True,
            "distributed": True,
            "execution_strategy": "sdk_native",
        },
        {
            "identity": "Bash",
            "declared_identities": ["Bash"],
            "registered": True,
            "declared": True,
            "active": True,
            "distributed": True,
            "command_isolation": "sibling-tool-sandbox-v1",
        },
    ]

    with pytest.raises(NativeToolAdmissionError) as exc_info:
        await provider.create_or_reuse(
            request(skill_ids=["native-review"], tool_policy_subjects=native_subjects),
            workspace(workspace_host_path=str(workspace_path)),
        )

    assert exc_info.value.error_code == "native_tool_admission_failed"
    assert str(exc_info.value) == "Native tool sandbox admission failed"
    assert len(probe_calls) == 1
    assert probe_calls[0] is fake.containers_by_name["native-tool-run-a"]
    assert [created["name"] for created in fake.created] == ["native-tool-run-a"]
    assert fake.containers_by_name["native-tool-run-a"].removed is True
    assert provider._leases == {}


@pytest.mark.asyncio
async def test_docker_provider_bounds_stuck_native_exec_await_and_cleans_sidecar(tmp_path):
    from app.runtime.sandbox.container_provider import DockerContainerProvider, NativeToolAdmissionError

    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    probe_started = threading.Event()
    probe_finished = threading.Event()
    release_probe = threading.Event()

    def stuck_probe(_container):
        probe_started.set()
        release_probe.wait(timeout=5)
        probe_finished.set()
        return False

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        native_tool_probe=stuck_probe,
    )
    try:
        with pytest.raises(NativeToolAdmissionError):
            await asyncio.wait_for(
                provider._start_native_tool_container(
                    request=request(tool_policy_subjects=native_tool_subjects()),
                    workspace=workspace(workspace_host_path=str(workspace_path)),
                    token="t" * 32,
                    timeout_seconds=0.05,
                ),
                timeout=1,
            )

        assert probe_started.is_set()
        assert probe_finished.is_set() is False
        assert fake.containers_by_name["native-tool-run-a"].removed is True
        assert provider._leases == {}
    finally:
        release_probe.set()
        assert probe_finished.wait(timeout=1)


@pytest.mark.asyncio
async def test_docker_provider_reuse_cancellation_cleans_runtime_pair_while_exec_thread_finishes_later(
    tmp_path,
):
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    leased_workspace = workspace(workspace_host_path=str(workspace_path))
    native_subjects = [
        {
            "identity": "Skill",
            "declared_identities": ["Skill"],
            "registered": True,
            "declared": True,
            "active": True,
            "distributed": True,
            "execution_strategy": "sdk_native",
        },
        {
            "identity": "Bash",
            "declared_identities": ["Bash"],
            "registered": True,
            "declared": True,
            "active": True,
            "distributed": True,
            "command_isolation": "sibling-tool-sandbox-v1",
        },
    ]
    selected_request = request(skill_ids=["native-review"], tool_policy_subjects=native_subjects)
    probe_started = threading.Event()
    probe_finished = threading.Event()
    release_probe = threading.Event()

    def stuck_probe(_container):
        probe_started.set()
        release_probe.wait(timeout=5)
        probe_finished.set()
        return False

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )
    lease = await provider.create_or_reuse(selected_request, leased_workspace)
    native = fake.containers_by_name["native-tool-run-a"]
    primary = fake.containers_by_name[lease.container_name]
    provider._native_tool_probe = stuck_probe
    task = asyncio.create_task(
        provider.create_or_reuse(selected_request, leased_workspace)
    )
    try:
        assert await asyncio.to_thread(probe_started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert probe_finished.is_set() is False
        assert native.removed is True
        assert primary.removed is True
        assert provider._leases == {}
    finally:
        release_probe.set()
        assert probe_finished.wait(timeout=1)


def _workspace_path_for_native_socket_bytes(target_bytes: int) -> str:
    base = "C:\\g3\\workspace"
    suffix = str(Path(base) / ".ai-platform" / "native-tool.sock")
    padding = target_bytes - len(os.fsencode(suffix))
    assert padding >= 0
    workspace_path = f"{base}{'x' * padding}"
    assert len(
        os.fsencode(str(Path(workspace_path) / ".ai-platform" / "native-tool.sock"))
    ) == target_bytes
    return workspace_path


@pytest.mark.asyncio
@pytest.mark.parametrize("workspace_socket_path_bytes", [211, 51])
async def test_docker_provider_uses_short_host_socket_and_probes_health_inside_container(
    monkeypatch,
    workspace_socket_path_bytes,
):
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )
    workspace_path = _workspace_path_for_native_socket_bytes(workspace_socket_path_bytes)
    leased_workspace = workspace(workspace_host_path=workspace_path)
    host_socket_path = provider._native_tool_socket_host_path(leased_workspace)
    host_socket_path_bytes = len(os.fsencode(str(host_socket_path)))
    monkeypatch.setattr(
        provider,
        "_prepare_native_tool_socket",
        lambda selected_workspace: provider._native_tool_socket_host_path(selected_workspace),
    )
    monkeypatch.setattr(
        "app.runtime.sandbox.container_provider._prepare_trusted_skill_mount",
        lambda _request, selected_workspace: trusted_skill_mount_stub(selected_workspace),
    )
    native_subjects = native_tool_subjects()

    lease = await provider.create_or_reuse(
        request(skill_ids=["native-review"], tool_policy_subjects=native_subjects),
        leased_workspace,
    )

    native = fake.containers_by_name["native-tool-run-a"]
    executor = fake.containers_by_name[lease.container_name]
    token = native.environment["AI_PLATFORM_NATIVE_TOOL_TOKEN"]
    serialized_exec = repr(fake.api.exec_create_calls)
    assert fake.api.exec_create_calls == [
        (
            native.id,
            ["python", "-m", "app.runtime.sandbox.native_tool_health_probe"],
            {"stdout": False, "stderr": False},
        )
    ]
    assert fake.api.exec_start_calls == [("exec-1", {"detach": True})]
    assert fake.api.exec_inspect_calls == ["exec-1"]
    assert token not in serialized_exec
    assert workspace_path not in serialized_exec
    assert lease.labels["ai-platform.native_tool_admission_phase"] == "authenticated_container_uds_health"
    assert workspace_socket_path_bytes in {51, 211}
    assert host_socket_path_bytes <= 107
    assert host_socket_path.parent != Path(workspace_path) / ".ai-platform"
    assert host_socket_path.name == "native-tool.sock"
    assert lease.labels["ai-platform.native_tool_host_socket_path_bytes"] == str(host_socket_path_bytes)
    assert lease.labels["ai-platform.native_tool_container_socket_path_bytes"] == "40"
    assert native.labels["ai-platform.native_tool_host_socket_path_bytes"] == str(host_socket_path_bytes)
    expected_socket_mount = {
        "bind": "/workspace/.ai-platform",
        "mode": "rw",
    }
    assert native.volumes[str(host_socket_path.parent)] == expected_socket_mount
    assert executor.volumes[str(host_socket_path.parent)] == expected_socket_mount
    assert native.environment["AI_PLATFORM_NATIVE_TOOL_SOCKET"] == "/workspace/.ai-platform/native-tool.sock"
    assert executor.environment["AI_PLATFORM_NATIVE_TOOL_SOCKET"] == "/workspace/.ai-platform/native-tool.sock"
    assert host_socket_path == (
        Path(next(
            host_path
            for host_path, mount in native.volumes.items()
            if mount == expected_socket_mount
        ))
        / Path(native.environment["AI_PLATFORM_NATIVE_TOOL_SOCKET"]).name
    )
    assert host_socket_path == (
        Path(next(
            host_path
            for host_path, mount in executor.volumes.items()
            if mount == expected_socket_mount
        ))
        / Path(executor.environment["AI_PLATFORM_NATIVE_TOOL_SOCKET"]).name
    )


def test_docker_provider_native_socket_paths_are_scope_unique_and_bounded(monkeypatch):
    import app.runtime.sandbox.container_provider as container_provider
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    settings = container_provider.get_settings().model_copy(
        update={"sandbox_workspace_root": "/tmp/ai-platform-sandbox-workspaces"}
    )
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    provider = DockerContainerProvider(docker_client_factory=FakeDockerClient)

    first = provider._native_tool_socket_host_path(workspace(run_id="run-a"))
    second = provider._native_tool_socket_host_path(workspace(run_id="run-b"))

    assert first != second
    assert first.name == second.name == "native-tool.sock"
    assert len(first.parent.name) == len(second.parent.name) == 24
    assert len(os.fsencode(str(first))) <= 107
    assert len(os.fsencode(str(second))) <= 107


@pytest.mark.asyncio
async def test_docker_provider_rejects_overlong_configured_socket_root_before_container_start(
    monkeypatch,
):
    import app.runtime.sandbox.container_provider as container_provider
    from app.runtime.sandbox.container_provider import DockerContainerProvider, NativeToolAdmissionError

    settings = container_provider.get_settings().model_copy(
        update={
            "sandbox_workspace_root": _workspace_path_for_native_socket_bytes(211),
            "sandbox_executor_image": "registry.example/ai-platform@sha256:" + "a" * 64,
            "sandbox_egress_policy_enabled": True,
                "sandbox_callback_base_url": "http://api.sandbox.internal:8020",
                "sandbox_egress_proof_signing_key": "provider-test-proof-key-with-enough-entropy-2026",
                "ai_platform_runtime_commit": "a" * 40,
        }
    )
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    monkeypatch.setattr(
        container_provider,
        "_prepare_trusted_skill_mount",
        lambda _request, selected_workspace: trusted_skill_mount_stub(selected_workspace),
    )
    fake = FakeDockerClient()
    provider = DockerContainerProvider(docker_client_factory=lambda: fake)
    native_subjects = native_tool_subjects()

    with pytest.raises(NativeToolAdmissionError) as exc_info:
        await provider.create_or_reuse(
            request(skill_ids=["native-review"], tool_policy_subjects=native_subjects),
            workspace(),
        )

    assert exc_info.value.error_code == "native_tool_admission_failed"
    assert fake.created == []
    assert provider._leases == {}


@pytest.mark.asyncio
async def test_docker_provider_stop_removes_only_the_owned_short_socket_directory(
    monkeypatch,
    tmp_path,
):
    import app.runtime.sandbox.container_provider as container_provider
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    leased_workspace = workspace(workspace_host_path=str(workspace_path))
    short_socket_root = Path(".pytest-tmp") / f"issue549-native-{hashlib.sha256(str(tmp_path).encode()).hexdigest()[:8]}"
    settings = container_provider.get_settings().model_copy(
        update={
            "sandbox_workspace_root": str(short_socket_root),
            "sandbox_executor_image": "registry.example/ai-platform@sha256:" + "a" * 64,
            "sandbox_egress_policy_enabled": True,
                "sandbox_callback_base_url": "http://api.sandbox.internal:8020",
                "sandbox_egress_proof_signing_key": "provider-test-proof-key-with-enough-entropy-2026",
                "ai_platform_runtime_commit": "a" * 40,
        }
    )
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    fake = FakeDockerClient()
    provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
        native_tool_probe=lambda _container: True,
    )
    native_subjects = native_tool_subjects()

    lease = await provider.create_or_reuse(
        request(skill_ids=["native-review"], tool_policy_subjects=native_subjects),
        leased_workspace,
    )
    native = fake.containers_by_name["native-tool-run-a"]
    socket_dir = Path(next(
        host_path
        for host_path, mount in native.volumes.items()
        if mount["bind"] == "/workspace/.ai-platform"
    ))
    actual_socket_path = socket_dir / Path(
        native.environment["AI_PLATFORM_NATIVE_TOOL_SOCKET"]
    ).name
    assert actual_socket_path == provider._native_tool_socket_host_path(leased_workspace)
    assert socket_dir.is_dir()
    actual_socket_path.write_text("test socket leaf", encoding="utf-8")
    unrelated_scope = socket_dir.parent / "unrelated-scope"
    unrelated_scope.mkdir(exist_ok=True)
    monkeypatch.setattr(container_provider.stat, "S_ISSOCK", lambda _mode: True)

    result = await provider.stop(lease, reason="test-complete")

    assert result.status == "stopped"
    assert actual_socket_path.exists() is False
    assert socket_dir.exists() is False
    assert unrelated_scope.is_dir()


@pytest.mark.asyncio
async def test_docker_provider_native_exec_failure_times_out_and_sanitizes_all_probe_inputs(
    monkeypatch,
):
    from app.runtime.sandbox.container_provider import (
        DockerContainerProvider,
        ExecutorHealthTimeoutError,
        NativeToolAdmissionError,
    )

    private_command = "private-native-command"
    private_path = _workspace_path_for_native_socket_bytes(211)
    fake = FakeDockerClient(
        exec_error=RuntimeError(f"exec failed for {private_command} at {private_path}"),
    )
    provider = DockerContainerProvider(docker_client_factory=lambda: fake)
    leased_workspace = workspace(workspace_host_path=private_path)
    monkeypatch.setattr(
        provider,
        "_prepare_native_tool_socket",
        lambda selected_workspace: provider._native_tool_socket_host_path(selected_workspace),
    )
    monkeypatch.setattr(
        "app.runtime.sandbox.container_provider._prepare_trusted_skill_mount",
        lambda _request, selected_workspace: trusted_skill_mount_stub(selected_workspace),
    )

    async def bounded_timeout(container, _timeout_seconds):
        assert provider._native_tool_probe(container) is False
        raise ExecutorHealthTimeoutError("native tool sandbox did not become ready")

    monkeypatch.setattr(provider, "_wait_for_native_tool_socket", bounded_timeout)
    native_subjects = [
        {
            "identity": "Skill",
            "declared_identities": ["Skill"],
            "registered": True,
            "declared": True,
            "active": True,
            "distributed": True,
            "execution_strategy": "sdk_native",
        },
        {
            "identity": "Bash",
            "declared_identities": ["Bash"],
            "registered": True,
            "declared": True,
            "active": True,
            "distributed": True,
            "command_isolation": "sibling-tool-sandbox-v1",
        },
    ]

    with pytest.raises(NativeToolAdmissionError) as exc_info:
        await provider.create_or_reuse(
            request(skill_ids=["native-review"], tool_policy_subjects=native_subjects),
            leased_workspace,
        )

    native = fake.containers_by_name["native-tool-run-a"]
    token = native.environment["AI_PLATFORM_NATIVE_TOOL_TOKEN"]
    diagnostic = str(exc_info.value)
    rendered_exception = "".join(traceback.format_exception(exc_info.value))
    assert diagnostic == "Native tool sandbox admission failed"
    assert all(
        value not in diagnostic and value not in rendered_exception
        for value in (token, private_command, private_path)
    )
    assert token not in repr(fake.api.exec_create_calls)
    assert private_path not in repr(fake.api.exec_create_calls)
    assert native.removed is True
    assert provider._leases == {}


@pytest.mark.asyncio
async def test_docker_provider_preserves_existing_native_sidecar_cleanup_failure(tmp_path):
    from app.runtime.sandbox.container_provider import ContainerCleanupFailedError, DockerContainerProvider

    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    old_sidecar = FakeDockerContainer(
        image="ai-platform-executor:dev",
        name="native-tool-run-a",
        detach=True,
        labels={
            "ai-platform.owner": "sandbox-native-tool",
            "ai-platform.tenant_id": "tenant-a",
            "ai-platform.workspace_id": "workspace-a",
            "ai-platform.user_id": "user-a",
            "ai-platform.session_id": "session-a",
            "ai-platform.run_id": "run-a",
        },
        volumes={},
        environment={},
        stop_error=RuntimeError("old sidecar stop failed"),
        remove_error=RuntimeError("old sidecar remove failed"),
    )
    fake = FakeDockerClient()
    fake.containers_by_name[old_sidecar.name] = old_sidecar
    provider = DockerContainerProvider(docker_client_factory=lambda: fake)
    native_subjects = [
        {
            "identity": "Skill",
            "declared_identities": ["Skill"],
            "registered": True,
            "declared": True,
            "active": True,
            "distributed": True,
            "execution_strategy": "sdk_native",
        },
        {
            "identity": "Bash",
            "declared_identities": ["Bash"],
            "registered": True,
            "declared": True,
            "active": True,
            "distributed": True,
            "command_isolation": "sibling-tool-sandbox-v1",
        },
    ]

    with pytest.raises(ContainerCleanupFailedError) as exc_info:
        await provider.create_or_reuse(
            request(skill_ids=["native-review"], tool_policy_subjects=native_subjects),
            workspace(workspace_host_path=str(workspace_path)),
        )

    assert exc_info.value.error_code == "container_cleanup_failed"
    assert str(exc_info.value) == "native tool container cleanup could not be confirmed"
    assert old_sidecar.removed is False
    assert fake.created == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_stage", "raw_error", "expected_error_name", "expected_code", "expected_message"),
    [
        ("create", "permission denied: private workspace", "permission", "docker_permission_denied", "Docker permission denied"),
        ("start", "permission denied: private workspace", "permission", "docker_permission_denied", "Docker permission denied"),
        ("create", "cannot connect to /var/run/docker.sock", "unavailable", "docker_unavailable", "Docker daemon is unavailable"),
        ("start", "cannot connect to /var/run/docker.sock", "unavailable", "docker_unavailable", "Docker daemon is unavailable"),
    ],
)
async def test_docker_provider_preserves_native_sidecar_docker_failure_taxonomy(
    tmp_path,
    failure_stage,
    raw_error,
    expected_error_name,
    expected_code,
    expected_message,
):
    from app.runtime.sandbox.container_provider import (
        DockerContainerProvider,
        DockerPermissionDeniedError,
        DockerUnavailableError,
    )

    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    failure_kwargs = {f"{failure_stage}_error": RuntimeError(raw_error)}
    fake = FakeDockerClient(**failure_kwargs)
    provider = DockerContainerProvider(docker_client_factory=lambda: fake)
    expected_error = DockerPermissionDeniedError if expected_error_name == "permission" else DockerUnavailableError
    native_subjects = [
        {
            "identity": "Skill",
            "declared_identities": ["Skill"],
            "registered": True,
            "declared": True,
            "active": True,
            "distributed": True,
            "execution_strategy": "sdk_native",
        },
        {
            "identity": "Bash",
            "declared_identities": ["Bash"],
            "registered": True,
            "declared": True,
            "active": True,
            "distributed": True,
            "command_isolation": "sibling-tool-sandbox-v1",
        },
    ]

    with pytest.raises(expected_error) as exc_info:
        await provider.create_or_reuse(
            request(skill_ids=["native-review"], tool_policy_subjects=native_subjects),
            workspace(workspace_host_path=str(workspace_path)),
        )

    assert exc_info.value.error_code == expected_code
    assert str(exc_info.value) == expected_message
    assert raw_error not in str(exc_info.value)
    assert provider._leases == {}


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
                "sandbox_executor_image": "registry.example/ai-platform@sha256:" + "a" * 64,
                "sandbox_executor_published_host": "127.0.0.1",
                "sandbox_callback_base_url": "http://api.sandbox.internal:8020",
                "sandbox_egress_policy_enabled": True,
                    "sandbox_egress_network_name": "ai-platform-sandbox-egress-internal-v1",
                    "sandbox_egress_proof_signing_key": "provider-test-proof-key-with-enough-entropy-2026",
                    "ai_platform_runtime_commit": "a" * 40,
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
    assert created["image"] == "registry.example/ai-platform@sha256:" + "a" * 64
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
    assert lease.labels["ai-platform.external_egress.profile_requested_image"] == "registry.example/ai-platform@sha256:" + "a" * 64
    assert lease.labels["ai-platform.external_egress.profile_requested_image_digest"] == "sha256:" + "a" * 64
    assert lease.labels["ai-platform.external_egress.profile_expires_at"] == "2026-07-14T16:02:00Z"
    assert lease.labels["ai-platform.executor.requested_image"] == "registry.example/ai-platform@sha256:" + "a" * 64
    assert lease.labels["ai-platform.executor.requested_image_digest"] == "sha256:" + "a" * 64
    assert not any(
        key.startswith("ai-platform.executor.")
        and key not in {
            "ai-platform.executor.requested_image",
            "ai-platform.executor.requested_image_digest",
        }
        for key in lease.labels
    )


@pytest.mark.asyncio
async def test_opensandbox_skill_bash_run_uses_nested_read_only_claude_mount(
    monkeypatch,
    tmp_path,
):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())
    workspace_path = tmp_path / "run" / "workspace"
    workspace_path.mkdir(parents=True)
    leased_workspace = workspace(workspace_host_path=str(workspace_path))

    await opensandbox_provider().create_or_reuse(
        request(tool_policy_subjects=native_tool_subjects()),
        leased_workspace,
    )

    volumes = FakeOpenSandbox.created[0]["volumes"]
    assert [(volume.host.path, volume.mount_path, volume.read_only) for volume in volumes] == [
        (str(workspace_path), "/workspace", False),
        (str((workspace_path / ".claude").resolve()), "/workspace/.claude", True),
    ]
    assert FakeOpenSandbox.created[0]["metadata"]["ai-platform.skill_mount.required"] == "true"
    assert len(FakeOpenSandbox.created[0]["metadata"]["ai-platform.skill_mount.fingerprint"]) == 64


@pytest.mark.asyncio
async def test_opensandbox_skill_bash_run_fails_closed_without_read_only_volume_support(
    monkeypatch,
    tmp_path,
):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())
    workspace_path = tmp_path / "run" / "workspace"
    workspace_path.mkdir(parents=True)
    leased_workspace = workspace(workspace_host_path=str(workspace_path))

    class VolumeWithoutReadOnly:
        def __init__(self, *, name, host, mountPath):
            self.name = name
            self.host = host
            self.mount_path = mountPath

    provider = opensandbox_provider()
    provider._volume_class = VolumeWithoutReadOnly

    with pytest.raises(
        container_provider.ContainerStartFailedError,
        match="read-only staged Skill mount is unavailable",
    ):
        await provider.create_or_reuse(
            request(tool_policy_subjects=native_tool_subjects()),
            leased_workspace,
        )

    assert FakeOpenSandbox.created == []


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
    assert lease.container_id not in provider._sandboxes
    assert f"opensandbox-{lease.run_id}" not in provider._leases

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

    assert stop_result.status == "failed"
    assert sandbox.killed is False
    assert sandbox.closed is False
    assert provider._leases[f"opensandbox-{lease.run_id}"] is lease
    assert provider._sandboxes[lease.container_id] is sandbox


@pytest.mark.asyncio
async def test_opensandbox_provider_stop_clears_tracking_for_authoritative_sdk_not_found(monkeypatch):
    from opensandbox.exceptions import SandboxApiException

    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())
    provider = opensandbox_provider()
    lease = await provider.create_or_reuse(request(), workspace())
    sandbox = FakeOpenSandbox.instances[lease.container_id]
    provider._sandboxes.clear()

    def authoritative_not_found(cls, sandbox_id, **_kwargs):
        raise SandboxApiException(
            f"sandbox {sandbox_id} is absent",
            status_code=404,
        )

    monkeypatch.setattr(FakeOpenSandbox, "connect", classmethod(authoritative_not_found))

    stop_result = await provider.stop(lease, reason="expired")

    assert stop_result.status == "not_found"
    assert sandbox.killed is False
    assert lease.container_id not in provider._sandboxes
    assert f"opensandbox-{lease.run_id}" not in provider._leases


@pytest.mark.asyncio
async def test_opensandbox_provider_stop_retains_tracking_for_untrusted_not_found_signal(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    FakeOpenSandboxManager.reset()
    monkeypatch.setattr(container_provider, "get_settings", lambda: OpenSandboxSettings())
    provider = opensandbox_provider()
    lease = await provider.create_or_reuse(request(), workspace())
    provider._sandboxes.clear()

    def untrusted_not_found(cls, sandbox_id, **_kwargs):
        raise RuntimeError(f"sandbox container {sandbox_id} not found (404)")

    monkeypatch.setattr(FakeOpenSandbox, "connect", classmethod(untrusted_not_found))

    stop_result = await provider.stop(lease, reason="expired")

    assert stop_result.status == "failed"
    assert provider._leases[f"opensandbox-{lease.run_id}"] is lease


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
    assert fake.containers_by_name["executor-exec-run-a"].removed is False
    assert provider._leases == {}


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
@pytest.mark.parametrize(
    ("settings", "network"),
    (
        (governed_docker_settings(sandbox_egress_policy_enabled=False), None),
        (governed_docker_settings(sandbox_egress_policy_enabled="unknown"), None),
        (governed_docker_settings(sandbox_egress_proof_signing_key="too-short"), None),
        (governed_docker_settings(sandbox_executor_image="ai-platform-executor:mutable"), None),
    ),
    ids=("disabled", "unknown", "weak-key", "mutable-image"),
)
async def test_docker_provider_fails_closed_before_container_side_effects_without_proven_governed_egress(
    monkeypatch,
    settings,
    network,
):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")

    fake = FakeDockerClient()
    fake.networks_by_name.clear()
    if network is not None:
        fake.networks_by_name["ai-platform-sandbox-egress-internal-v1"] = network
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    provider = container_provider.DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )

    with pytest.raises(container_provider.GovernedEgressAdmissionError) as exc_info:
        await provider.create_or_reuse(request(), workspace())

    assert exc_info.value.error_code == "sandbox_egress_unavailable"
    assert str(exc_info.value) == "Governed sandbox egress is unavailable; contact an operator."
    assert fake.created == []
    assert fake.network_create_calls == []


@pytest.mark.asyncio
async def test_docker_provider_creates_owned_per_lease_internal_network(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    fake = FakeDockerClient()
    monkeypatch.setattr(container_provider, "get_settings", lambda: governed_docker_settings())
    provider = container_provider.DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda *_args: True,
        identity_probe=lambda *_args: {"uid": 10001, "gid": 10001},
    )

    lease = await provider.create_or_reuse(request(), workspace())

    assert len(fake.network_create_calls) == 1
    network_name, kwargs = fake.network_create_calls[0]
    assert network_name == container_provider._governed_docker_network_name(lease)
    assert kwargs["internal"] is True
    assert kwargs["options"] == {"com.docker.network.bridge.enable_ip_masquerade": "false"}


@pytest.mark.asyncio
async def test_docker_governed_network_is_per_sandbox_and_contains_only_api_and_lease(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    fake = FakeDockerClient()
    monkeypatch.setattr(container_provider, "get_settings", lambda: governed_docker_settings())
    provider = container_provider.DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda *_args: True,
        identity_probe=lambda *_args: {"uid": 10001, "gid": 10001},
    )

    first = await provider.create_or_reuse(request(), workspace())
    second = await provider.create_or_reuse(
        request(run_id="run-b", session_id="session-b"),
        workspace(run_id="run-b", session_id="session-b"),
    )
    first_network = container_provider._governed_docker_network_name(first)
    second_network = container_provider._governed_docker_network_name(second)

    assert first_network != second_network
    for lease, network_name in ((first, first_network), (second, second_network)):
        network = fake.networks_by_name[network_name]
        assert network["attrs"]["Internal"] is True
        assert network["attrs"]["Labels"]["ai-platform.owner"] == "sandbox-runtime-governed-egress-v2"
        assert set(network["attrs"]["Containers"]) == {
            fake.api_container.id,
            fake.containers_by_name[lease.container_name].id,
        }
        assert set(fake.containers_by_name[lease.container_name].attrs["NetworkSettings"]["Networks"]) == {
            network_name
        }
    assert second.container_id not in fake.networks_by_name[first_network]["attrs"]["Containers"]


@pytest.mark.asyncio
async def test_docker_dispatch_rejects_and_tracks_an_extra_governed_network_peer(monkeypatch):
    """A third member fails closed instead of becoming a cross-sandbox path."""
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    fake = FakeDockerClient()
    monkeypatch.setattr(container_provider, "get_settings", lambda: governed_docker_settings())
    provider = container_provider.DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda *_args: True,
        identity_probe=lambda *_args: {"uid": 10001, "gid": 10001},
    )
    sandbox_request = request()
    leased_workspace = workspace()
    lease = await provider.create_or_reuse(sandbox_request, leased_workspace)
    peer = FakeDockerContainer(
        image="unrelated:immutable",
        name="unrelated-peer",
        detach=True,
        labels={"ai-platform.owner": "another-runtime"},
        volumes={},
        environment={},
    )
    fake.containers_by_name[peer.name] = peer
    fake.attach_to_network(peer, container_provider._governed_docker_network_name(lease))

    with pytest.raises(container_provider.ContainerCleanupFailedError, match="cleanup"):
        await provider.validate_for_dispatch(lease, sandbox_request, leased_workspace)

    assert fake.containers_by_name[lease.container_name].removed is True
    assert peer.removed is False
    assert provider._leases[lease.container_id] is lease


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ("bridge", "api-witness"))
async def test_docker_dispatch_binds_current_topology_to_the_signed_proof(monkeypatch, replacement):
    """A compliant replacement with the same name cannot reuse stale proof evidence."""
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    fake = FakeDockerClient()
    monkeypatch.setattr(container_provider, "get_settings", lambda: governed_docker_settings())
    provider = container_provider.DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda *_args: True,
        identity_probe=lambda *_args: {"uid": 10001, "gid": 10001},
    )
    sandbox_request = request()
    leased_workspace = workspace()
    lease = await provider.create_or_reuse(sandbox_request, leased_workspace)
    await provider.validate_for_dispatch(lease, sandbox_request, leased_workspace)
    network_name = container_provider._governed_docker_network_name(lease)
    network = fake.networks_by_name[network_name]
    primary = fake.containers_by_name[lease.container_name]

    if replacement == "bridge":
        replacement_id = "network-replacement"
        network["attrs"]["Id"] = replacement_id
        fake.api_container.attrs["NetworkSettings"]["Networks"][network_name]["NetworkID"] = replacement_id
        primary.attrs["NetworkSettings"]["Networks"][network_name]["NetworkID"] = replacement_id
    else:
        prior_id = fake.api_container.id
        replacement_id = "api-witness-replacement"
        fake.api_container.id = replacement_id
        fake.api_container.attrs["Id"] = replacement_id
        members = network["attrs"]["Containers"]
        members.pop(prior_id)
        members[replacement_id] = {"Name": fake.api_container.name}

    with pytest.raises(container_provider.GovernedEgressAdmissionError):
        await provider.validate_for_dispatch(lease, sandbox_request, leased_workspace)

    assert primary.removed is True
    assert lease.container_id not in provider._leases


@pytest.mark.asyncio
async def test_docker_rejects_unreachable_or_stale_api_callback_before_dispatch(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    fake = FakeDockerClient()
    monkeypatch.setattr(container_provider, "get_settings", lambda: governed_docker_settings())
    provider = container_provider.DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda *_args: True,
        identity_probe=lambda *_args: {"uid": 10001, "gid": 10001},
        callback_reachability_probe=lambda *_args: False,
    )

    with pytest.raises(container_provider.GovernedEgressAdmissionError):
        await provider.create_or_reuse(request(), workspace())

    assert fake.containers_by_name["executor-exec-run-a"].removed is True
    assert all("executor-exec-run-a" not in name for name in fake.networks_by_name)
    fake.api_container.attrs["State"]["Health"]["Status"] = "unhealthy"
    with pytest.raises(container_provider.GovernedEgressAdmissionError):
        await provider.create_or_reuse(request(run_id="run-c"), workspace(run_id="run-c"))


@pytest.mark.asyncio
async def test_docker_requires_api_witness_to_match_the_exact_runtime_commit(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    fake = FakeDockerClient()
    fake.api_container.labels["ai-platform.source-commit"] = "b" * 40
    fake.api_container.attrs["Config"]["Labels"] = fake.api_container.labels
    monkeypatch.setattr(container_provider, "get_settings", lambda: governed_docker_settings())
    provider = container_provider.DockerContainerProvider(docker_client_factory=lambda: fake)

    with pytest.raises(container_provider.GovernedEgressAdmissionError):
        await provider.create_or_reuse(request(), workspace())

    assert fake.created == []


@pytest.mark.asyncio
async def test_docker_remembered_identity_mismatch_preserves_same_name_container(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    fake = FakeDockerClient()
    monkeypatch.setattr(container_provider, "get_settings", lambda: governed_docker_settings())
    provider = container_provider.DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda *_args: True,
        identity_probe=lambda *_args: {"uid": 10001, "gid": 10001},
    )
    lease = await provider.create_or_reuse(request(), workspace())
    remote = fake.containers_by_name[lease.container_name]
    remote.attrs["Id"] = "docker-replaced-container"

    with pytest.raises(container_provider.ContainerStartFailedError, match="identity mismatch"):
        await provider.create_or_reuse(request(), workspace())

    assert remote.removed is False
    assert lease.container_id not in provider._leases


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("extra-network", "wrong-network", "container-id", "host-gateway"))
async def test_docker_post_create_readback_rejects_unattested_runtime_topology(monkeypatch, failure):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    fake = FakeDockerClient()

    def mutate(container):
        governed_network_name = next(iter(container.attrs["NetworkSettings"]["Networks"]))
        governed_network = fake.networks_by_name[governed_network_name]
        if failure == "extra-network":
            container.attrs["NetworkSettings"]["Networks"]["default"] = {"NetworkID": "default-network"}
        elif failure == "wrong-network":
            governed_network["attrs"]["Containers"].pop(container.id, None)
            container.attrs["NetworkSettings"]["Networks"] = {"default": {"NetworkID": "default-network"}}
        elif failure == "container-id":
            container.attrs["Id"] = "docker-forged-id"
        else:
            container.attrs["HostConfig"]["ExtraHosts"] = ["host.docker.internal:host-gateway"]

    fake.post_create_mutator = mutate
    monkeypatch.setattr(container_provider, "get_settings", lambda: governed_docker_settings())
    provider = container_provider.DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda *_args: True,
        identity_probe=lambda *_args: {"uid": 10001, "gid": 10001},
    )

    with pytest.raises(container_provider.GovernedEgressAdmissionError):
        await provider.create_or_reuse(request(), workspace())

    primary = fake.containers_by_name["executor-exec-run-a"]
    assert primary.started is False
    assert primary.removed is True
    assert provider._leases == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("absent", "duplicate", "alias-drift", "network-drift"))
async def test_docker_requires_exact_single_attested_api_callback_endpoint(monkeypatch, failure):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    fake = FakeDockerClient()
    network_name = "ai-platform-sandbox-egress-internal-v1"
    if failure == "absent":
        fake.containers_by_name.pop(fake.api_container.name)
    elif failure == "duplicate":
        duplicate = FakeDockerContainer(
            image="ai-platform-api:fake",
            name="ai-platform-api-duplicate",
            detach=True,
            labels={
                "ai-platform.release-role": "api",
                "ai-platform.release-owner": "repo-local-compose",
                "ai-platform.source-commit": "a" * 40,
            },
            volumes={},
            environment={},
        )
        duplicate.status = "running"
        duplicate.attrs["State"] = {"Health": {"Status": "healthy"}}
        fake.attach_to_network(duplicate, network_name, aliases=["api.sandbox.internal"])
        fake.containers_by_name[duplicate.name] = duplicate
    elif failure == "alias-drift":
        original_attach = fake.attach_to_network

        def attach_with_alias_drift(container, attached_network, *, aliases=None):
            original_attach(
                container,
                attached_network,
                aliases=(
                    ["api-v2.sandbox.internal"]
                    if container is fake.api_container and attached_network.startswith("ai-platform-sandbox-egress-v2-")
                    else aliases
                ),
            )

        fake.attach_to_network = attach_with_alias_drift
    else:
        original_attach = fake.attach_to_network

        def attach_without_callback(container, attached_network, *, aliases=None):
            if container is fake.api_container and attached_network.startswith("ai-platform-sandbox-egress-v2-"):
                return
            original_attach(container, attached_network, aliases=aliases)

        fake.attach_to_network = attach_without_callback
    monkeypatch.setattr(container_provider, "get_settings", lambda: governed_docker_settings())
    provider = container_provider.DockerContainerProvider(docker_client_factory=lambda: fake)

    with pytest.raises(container_provider.GovernedEgressAdmissionError):
        await provider.create_or_reuse(request(), workspace())

    assert fake.created == []


@pytest.mark.asyncio
async def test_docker_post_create_proof_binds_authoritative_container_id(monkeypatch):
    from app.execution_boundary import governed_egress_proof_from_labels

    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    fake = FakeDockerClient()
    settings = governed_docker_settings()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    provider = container_provider.DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda *_args: True,
        identity_probe=lambda *_args: {"uid": 10001, "gid": 10001},
    )

    lease = await provider.create_or_reuse(request(), workspace())
    proof = governed_egress_proof_from_labels(
        "docker",
        lease.labels,
        signing_key=settings.sandbox_egress_proof_signing_key,
        expected_binding={"lease_identity": f"docker:{lease.container_name}:{lease.container_id}"},
    )

    assert proof is not None
    assert lease.container_id == fake.containers_by_name[lease.container_name].id
    assert "ai-platform.governed_egress.proof" not in fake.containers_by_name[lease.container_name].labels
    assert "api.sandbox.internal" not in lease.labels["ai-platform.governed_egress.proof"]
    assert fake.api_container.id not in lease.labels["ai-platform.governed_egress.proof"]


@pytest.mark.asyncio
async def test_docker_production_factory_uses_authoritative_governed_egress_admission(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    fake = FakeDockerClient()
    container_provider.reset_container_provider_cache()
    monkeypatch.setattr(container_provider, "get_settings", lambda: governed_docker_settings())
    monkeypatch.setattr(container_provider, "docker", SimpleNamespace(from_env=lambda: fake))
    try:
        provider = container_provider.create_container_provider("docker")
        assert isinstance(provider, container_provider.DockerContainerProvider)
        provider._health_probe = lambda *_args: True
        provider._identity_probe = lambda *_args: {"uid": 10001, "gid": 10001}

        lease = await provider.create_or_reuse(request(), workspace())

        assert lease.container_id == fake.containers_by_name[lease.container_name].id
        assert fake.created[0]["network"].startswith("ai-platform-sandbox-egress-v2-")
    finally:
        container_provider.reset_container_provider_cache()


@pytest.mark.asyncio
async def test_docker_reuses_fresh_post_create_proof_when_authoritative_state_is_unchanged(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    fake = FakeDockerClient()
    monkeypatch.setattr(container_provider, "get_settings", lambda: governed_docker_settings())
    provider = container_provider.DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda *_args: True,
        identity_probe=lambda *_args: {"uid": 10001, "gid": 10001},
    )

    first = await provider.create_or_reuse(request(), workspace())
    second = await provider.create_or_reuse(request(), workspace())

    assert second.container_id == first.container_id
    assert len(fake.created) == 1
    assert fake.containers_by_name[first.container_name].removed is False


@pytest.mark.asyncio
async def test_docker_restart_with_rotated_proof_key_cleans_remote_before_cold_create(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    fake = FakeDockerClient()
    settings = governed_docker_settings()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    first = container_provider.DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda *_args: True,
        identity_probe=lambda *_args: {"uid": 10001, "gid": 10001},
    )
    lease = await first.create_or_reuse(request(), workspace())
    remote = fake.containers_by_name[lease.container_name]
    remote.labels["ai-platform.governed_egress.proof"] = lease.labels["ai-platform.governed_egress.proof"]
    remote.attrs["Config"]["Labels"] = remote.labels
    settings.sandbox_egress_proof_signing_key = "rotated-proof-key-with-enough-independent-entropy-2026"
    restarted = container_provider.DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda *_args: True,
        identity_probe=lambda *_args: {"uid": 10001, "gid": 10001},
    )

    replacement = await restarted.create_or_reuse(request(), workspace())

    assert remote.removed is True
    assert replacement.container_name == lease.container_name
    assert len(fake.created) == 2


@pytest.mark.asyncio
async def test_docker_restart_with_expired_signed_proof_cleans_remote_before_cold_create(monkeypatch):
    from app.execution_boundary import (
        build_governed_egress_proof,
        governed_egress_authorized_native_tool_scope,
        governed_egress_authorized_skill_scope,
        governed_egress_proof_label,
    )

    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    fake = FakeDockerClient()
    settings = governed_docker_settings()
    sandbox_request = request()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    first = container_provider.DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda *_args: True,
        identity_probe=lambda *_args: {"uid": 10001, "gid": 10001},
    )
    lease = await first.create_or_reuse(sandbox_request, workspace())
    network_name = settings.sandbox_egress_network_name
    network_id = fake.networks_by_name[network_name]["attrs"]["Id"]
    now = datetime.now(timezone.utc)
    expired_proof = build_governed_egress_proof(
        signing_key=settings.sandbox_egress_proof_signing_key,
        provider="docker",
        runtime_subject="docker-internal-bridge",
        policy_subject=f"{network_id}:{network_name}:internal",
        callback_subject=(
            f"{settings.sandbox_callback_base_url}|{fake.api_container.id}|{network_id}|api.sandbox.internal"
        ),
        denial_subject=f"{network_id}:internal-default-deny",
        network_id=network_id,
        network_name=network_name,
        network_internal=True,
        tenant_id=sandbox_request.tenant_id,
        workspace_id=sandbox_request.workspace_id,
        user_id=sandbox_request.user_id,
        session_id=sandbox_request.session_id,
        run_id=sandbox_request.run_id,
        image_subject=lease.labels["ai-platform.executor.requested_image"],
        image_digest=lease.labels["ai-platform.executor.requested_image_digest"],
        authorized_skill_scope=governed_egress_authorized_skill_scope(
            skill_ids=sandbox_request.skill_ids,
            mcp_tool_ids=sandbox_request.mcp_tool_ids,
        ),
        authorized_native_tool_scope=governed_egress_authorized_native_tool_scope(
            sandbox_request.tool_policy_subjects
        ),
        lease_identity=f"docker:{lease.container_name}:{lease.container_id}",
        issued_at=now - timedelta(minutes=2),
        expires_at=now - timedelta(seconds=1),
    )
    remote = fake.containers_by_name[lease.container_name]
    remote.labels["ai-platform.governed_egress.proof"] = governed_egress_proof_label(expired_proof)
    remote.attrs["Config"]["Labels"] = remote.labels
    restarted = container_provider.DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda *_args: True,
        identity_probe=lambda *_args: {"uid": 10001, "gid": 10001},
    )

    replacement = await restarted.create_or_reuse(sandbox_request, workspace())

    assert remote.removed is True
    assert replacement.container_name == lease.container_name
    assert len(fake.created) == 2


@pytest.mark.asyncio
async def test_docker_restart_invalid_remote_proof_tracks_cleanup_failure(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    fake = FakeDockerClient(
        stop_error=RuntimeError("stop unavailable"),
        remove_error=RuntimeError("remove unavailable"),
    )
    settings = governed_docker_settings()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    first = container_provider.DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda *_args: True,
        identity_probe=lambda *_args: {"uid": 10001, "gid": 10001},
    )
    lease = await first.create_or_reuse(request(), workspace())
    remote = fake.containers_by_name[lease.container_name]
    remote.labels["ai-platform.governed_egress.proof"] = "forged"
    remote.attrs["Config"]["Labels"] = remote.labels
    restarted = container_provider.DockerContainerProvider(docker_client_factory=lambda: fake)

    with pytest.raises(container_provider.ContainerCleanupFailedError):
        await restarted.create_or_reuse(request(), workspace())

    assert restarted._leases[lease.container_id].container_name == lease.container_name
    assert remote.removed is False


@pytest.mark.asyncio
async def test_docker_restart_refuses_unrelated_same_name_without_touching_it(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    fake = FakeDockerClient()
    unrelated = FakeDockerContainer(
        image="unrelated:latest",
        name="executor-exec-run-a",
        detach=True,
        labels={"ai-platform.owner": "another-runtime"},
        volumes={},
        environment={},
    )
    fake.containers_by_name[unrelated.name] = unrelated
    monkeypatch.setattr(container_provider, "get_settings", lambda: governed_docker_settings())
    provider = container_provider.DockerContainerProvider(docker_client_factory=lambda: fake)

    with pytest.raises(container_provider.ContainerStartFailedError, match="occupied"):
        await provider.create_or_reuse(request(), workspace())

    assert unrelated.stopped is False
    assert unrelated.removed is False
    assert fake.created == []


@pytest.mark.asyncio
async def test_docker_cached_egress_drift_stops_before_releasing_tracking(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    fake = FakeDockerClient()
    settings = governed_docker_settings()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    provider = container_provider.DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda *_args: True,
        identity_probe=lambda *_args: {"uid": 10001, "gid": 10001},
    )

    lease = await provider.create_or_reuse(request(), workspace())
    fake.api_container.status = "exited"

    with pytest.raises(container_provider.GovernedEgressAdmissionError):
        await provider.create_or_reuse(request(), workspace())

    assert fake.containers_by_name[lease.container_name].removed is True
    assert lease.container_id not in provider._leases


@pytest.mark.asyncio
async def test_docker_cached_egress_drift_keeps_tracking_when_stop_cannot_be_confirmed(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    fake = FakeDockerClient()
    settings = governed_docker_settings()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    provider = container_provider.DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda *_args: True,
        identity_probe=lambda *_args: {"uid": 10001, "gid": 10001},
    )

    lease = await provider.create_or_reuse(request(), workspace())
    fake.containers_by_name[lease.container_name]._stop_error = RuntimeError("stop failed")
    fake.containers_by_name[lease.container_name]._remove_error = RuntimeError("remove failed")
    fake.api_container.status = "exited"

    with pytest.raises(container_provider.ContainerCleanupFailedError):
        await provider.create_or_reuse(request(), workspace())

    assert provider._leases[lease.container_id].container_id == lease.container_id
    assert fake.containers_by_name[lease.container_name].removed is False


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
    assert {
        key for key in lease.labels if key.startswith("ai-platform.executor.")
    } == {
        "ai-platform.executor.requested_image",
        "ai-platform.executor.requested_image_digest",
    }


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

    assert next(iter(provider._leases.values())).run_id == "run-a"


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
                "sandbox_executor_image": "registry.example/ai-platform@sha256:" + "a" * 64,
            "sandbox_callback_base_url": "http://api.sandbox.internal:8020",
            "sandbox_egress_policy_enabled": True,
                "sandbox_egress_network_name": "ai-platform-sandbox-egress-internal-v1",
                    "sandbox_egress_proof_signing_key": "provider-test-proof-key-with-enough-entropy-2026",
                "ai_platform_runtime_commit": "a" * 40,
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
async def test_docker_provider_recreates_unsealed_remote_container_after_restart():
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    fake = FakeDockerClient()
    first_provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )
    first_lease = await first_provider.create_or_reuse(request(), workspace())
    first_container = fake.containers_by_name[first_lease.container_name]
    restarted_provider = DockerContainerProvider(
        docker_client_factory=lambda: fake,
        health_probe=lambda executor_url, timeout_seconds: True,
    )

    second_lease = await restarted_provider.create_or_reuse(request(), workspace())

    assert second_lease.container_name == first_lease.container_name
    assert len(fake.created) == 2
    assert first_container.removed is True


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
                "sandbox_executor_image": "registry.example/ai-platform@sha256:" + "a" * 64,
            "sandbox_callback_base_url": "http://api.sandbox.internal:8020",
            "sandbox_callback_host_gateway": "host.docker.internal",
            "sandbox_egress_policy_enabled": True,
                "sandbox_egress_network_name": "ai-platform-sandbox-egress-internal-v1",
                    "sandbox_egress_proof_signing_key": "provider-test-proof-key-with-enough-entropy-2026",
                "ai_platform_runtime_commit": "a" * 40,
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
async def test_docker_provider_publishes_configured_hostname_without_governed_host_gateway(monkeypatch):
    from app.runtime.sandbox import container_provider
    from app.runtime.sandbox.contracts import EXECUTOR_AUTH_HEADER
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    settings = type(
        "StubSettings",
        (),
        {
            "sandbox_executor_published_host": "host.docker.internal",
                "sandbox_executor_image": "registry.example/ai-platform@sha256:" + "a" * 64,
            "sandbox_callback_base_url": "http://api.sandbox.internal:8020",
            "sandbox_callback_host_gateway": "host.docker.internal",
            "sandbox_egress_policy_enabled": True,
                "sandbox_egress_network_name": "ai-platform-sandbox-egress-internal-v1",
                    "sandbox_egress_proof_signing_key": "provider-test-proof-key-with-enough-entropy-2026",
                "ai_platform_runtime_commit": "a" * 40,
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
    assert "extra_hosts" not in created


@pytest.mark.asyncio
async def test_docker_provider_rebuilds_instead_of_reusing_when_inspected_bind_ip_drifted(monkeypatch):
    from app.runtime.sandbox import container_provider
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    settings = type(
        "StubSettings",
        (),
        {
            "sandbox_executor_published_host": "host.docker.internal",
                "sandbox_executor_image": "registry.example/ai-platform@sha256:" + "a" * 64,
            "sandbox_callback_base_url": "http://api.sandbox.internal:8020",
            "sandbox_callback_host_gateway": "host.docker.internal",
            "sandbox_egress_policy_enabled": True,
                "sandbox_egress_network_name": "ai-platform-sandbox-egress-internal-v1",
                    "sandbox_egress_proof_signing_key": "provider-test-proof-key-with-enough-entropy-2026",
                "ai_platform_runtime_commit": "a" * 40,
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
    from app.runtime.sandbox.container_provider import DockerContainerProvider, GovernedEgressAdmissionError

    settings = type(
        "StubSettings",
        (),
        {
            "sandbox_executor_published_host": "127.0.0.1",
                "sandbox_executor_image": "registry.example/ai-platform@sha256:" + "a" * 64,
            "sandbox_callback_base_url": "http://example.com",
            "sandbox_callback_host_gateway": "",
            "sandbox_egress_policy_enabled": True,
            "sandbox_egress_network_name": "ai-platform-sandbox-egress-internal-v1",
                "sandbox_egress_proof_signing_key": "provider-test-proof-key-with-enough-entropy-2026",
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

    with pytest.raises(GovernedEgressAdmissionError):
        await provider.create_or_reuse(request(), workspace())


@pytest.mark.asyncio
async def test_docker_provider_rejects_link_local_callback_base_url(monkeypatch):
    from app.runtime.sandbox import container_provider
    from app.runtime.sandbox.container_provider import DockerContainerProvider, GovernedEgressAdmissionError

    settings = type(
        "StubSettings",
        (),
        {
            "sandbox_executor_published_host": "127.0.0.1",
                "sandbox_executor_image": "registry.example/ai-platform@sha256:" + "a" * 64,
            "sandbox_callback_base_url": "http://169.254.169.254",
            "sandbox_callback_host_gateway": "",
            "sandbox_egress_policy_enabled": True,
            "sandbox_egress_network_name": "ai-platform-sandbox-egress-internal-v1",
                "sandbox_egress_proof_signing_key": "provider-test-proof-key-with-enough-entropy-2026",
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

    with pytest.raises(GovernedEgressAdmissionError):
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

    assert result.status == "failed"
    assert lease.container_id in provider._leases
    assert result.container_id == lease.container_id


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


@pytest.mark.asyncio
async def test_docker_provider_lists_and_reclaims_running_orphan_native_tool_sidecar():
    from app.runtime.sandbox.container_provider import DockerContainerProvider

    fake = FakeDockerClient()
    provider = DockerContainerProvider(docker_client_factory=lambda: fake)

    def container(*, name, owner, run_id, tenant_id="tenant-a"):
        labels = {
            "ai-platform.owner": owner,
            "ai-platform.tenant_id": tenant_id,
            "ai-platform.workspace_id": "workspace-a",
            "ai-platform.user_id": "user-a",
            "ai-platform.session_id": "session-a",
            "ai-platform.run_id": run_id,
            "ai-platform.sandbox_mode": "ephemeral",
            "ai-platform.browser_enabled": "false",
        }
        if owner == "sandbox-native-tool":
            labels["ai-platform.role"] = "native-skill-command"
        item = FakeDockerContainer(
            image="ai-platform-executor:dev",
            name=name,
            detach=True,
            labels=labels,
            volumes={},
            environment={},
            ports={},
        )
        item.status = "running"
        return item

    primary = container(
        name="executor-paired",
        owner="sandbox-runtime",
        run_id="run-paired",
    )
    paired_native = container(
        name="native-tool-run-paired",
        owner="sandbox-native-tool",
        run_id="run-paired",
    )
    orphan_native = container(
        name="native-tool-run-orphan",
        owner="sandbox-native-tool",
        run_id="run-orphan",
    )
    foreign_native = container(
        name="native-tool-run-foreign",
        owner="sandbox-native-tool",
        run_id="run-foreign",
        tenant_id="tenant-b",
    )
    fake.containers_by_name = {
        item.name: item
        for item in (primary, paired_native, orphan_native, foreign_native)
    }

    statuses = await provider.list_runtime_containers({"tenant_id": "tenant-a"})
    assert {status.container_id for status in statuses} == {
        "exec-run-paired",
        "native-tool-run-paired",
        "native-tool-run-orphan",
    }

    results = await provider.cleanup_orphan_containers(
        {"tenant_id": "tenant-a"},
        reason="admin_runtime",
    )

    assert [result.container_id for result in results] == ["native-tool-run-orphan"]
    assert orphan_native.removed is True
    assert paired_native.removed is False
    assert foreign_native.removed is False
