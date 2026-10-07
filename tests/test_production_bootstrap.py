from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import socket
import stat
import subprocess
import sys

import pytest

from tools import opensandbox_unit_guard as unit_guard
from tools import production_bootstrap as bootstrap


ROOT = Path(__file__).resolve().parents[1]
COMMIT = "1" * 40
OLD_COMMIT = "2" * 40
BACKEND = "ghcr.io/example/backend@sha256:" + "3" * 64
SERVER_IMAGE = "ghcr.io/example/opensandbox-server@sha256:" + "5" * 64
EXECD_IMAGE = "ghcr.io/example/opensandbox-execd@sha256:" + "6" * 64
EGRESS_IMAGE = "ghcr.io/example/opensandbox-egress@sha256:" + "8" * 64
SERVER_IMAGE_ID = "sha256:" + "7" * 64
WORKSPACE_ROOT = Path("/data/opensandbox/workspaces/ai-platform")
REAL_UNIT_HELPER_VALIDATION = bootstrap._require_root_owned_unit_helper


@pytest.fixture(autouse=True)
def _accepted_network_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    # Functional unit fixtures live in the invoking user's temporary checkout.
    # Root permission enforcement is exercised separately with synthetic metadata.
    monkeypatch.setattr(bootstrap, "_require_root_owned_unit_helper", lambda _path: None)
    monkeypatch.setattr(
        bootstrap.transition,
        "_require_network_guard",
        lambda _checkout, *, topology: None,
    )


def _secure_file(path: Path, text: str, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


def _server_environment(socket_gid: int, **changes: str) -> str:
    values = {
        "OPENSANDBOX_SERVER_IMAGE": SERVER_IMAGE,
        "OPENSANDBOX_SERVER_IMAGE_DIGEST": "sha256:" + "5" * 64,
        "OPENSANDBOX_SERVER_UID": str(max(1, bootstrap.os.getuid())),
        "OPENSANDBOX_SERVER_GID": str(max(1, bootstrap.os.getgid())),
        "OPENSANDBOX_DOCKER_SOCKET_GID": str(socket_gid),
        "OPENSANDBOX_LIFECYCLE_LISTEN_ADDRESS": "10.40.0.10",
        "OPENSANDBOX_EGRESS_BRIDGE": bootstrap.authority.DIRECT_OPENSANDBOX_BRIDGE_NAME,
        "OPENSANDBOX_EGRESS_SUBNET": bootstrap.authority.DIRECT_OPENSANDBOX_SUBNET,
        "OPENSANDBOX_EGRESS_PROXY_IPV4": bootstrap.authority.DIRECT_OPENSANDBOX_PROXY_IPV4,
        **changes,
    }
    return "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"


def _server_config(**changes: str) -> str:
    values = {
        "api_key": "a" * 32,
        "execd_image": EXECD_IMAGE,
        "egress_image": EGRESS_IMAGE,
        "host_ip": "10.40.0.10",
        "network_mode": bootstrap.authority.DIRECT_OPENSANDBOX_NETWORK_NAME,
        "allowed_host_paths": f'["{WORKSPACE_ROOT}"]',
        "sandbox_env": "{}",
        "sandbox_binds": "[]",
        "egress_mode": "dns+nft",
        **changes,
    }
    return f"""
[server]
host = "{values["host_ip"]}"
port = 8080
max_sandbox_timeout_seconds = 86400
api_key = "{values["api_key"]}"

[log]
level = "INFO"

[runtime]
type = "docker"
execd_image = "{values["execd_image"]}"

[egress]
image = "{values["egress_image"]}"
mode = "{values["egress_mode"]}"
disable_ipv6 = true
readiness_timeout_seconds = 30.0

[storage]
allowed_host_paths = {values["allowed_host_paths"]}
volume_default_size = "1Gi"

[store]
type = "sqlite"
path = "/var/lib/ai-platform-opensandbox/opensandbox.db"

[docker]
network_mode = "{values["network_mode"]}"
host_ip = "{values["host_ip"]}"
drop_capabilities = ["AUDIT_WRITE", "MKNOD", "NET_ADMIN", "NET_RAW", "SYS_ADMIN", "SYS_MODULE", "SYS_PTRACE", "SYS_TIME", "SYS_TTY_CONFIG"]
no_new_privileges = true
pids_limit = 4096
sandbox_env = {values["sandbox_env"]}
sandbox_binds = {values["sandbox_binds"]}
port_range_min = 40000
port_range_max = 60000
apparmor_profile = ""
seccomp_profile = ""

[ingress]
mode = "direct"

[secure_runtime]
type = "gvisor"
docker_runtime = "runsc"
"""


def _application_environment(**changes: str) -> str:
    values = {
        "OPENSANDBOX_BASE_URL": "http://10.40.0.10:8080",
        "OPENSANDBOX_API_KEY": "a" * 32,
        "OPENSANDBOX_EXPECTED_NETWORK_MODE": bootstrap.authority.DIRECT_OPENSANDBOX_NETWORK_NAME,
        "OPENSANDBOX_EGRESS_BRIDGE": bootstrap.authority.DIRECT_OPENSANDBOX_BRIDGE_NAME,
        "OPENSANDBOX_EGRESS_SUBNET": bootstrap.authority.DIRECT_OPENSANDBOX_SUBNET,
        "OPENSANDBOX_EGRESS_PROXY_IPV4": bootstrap.authority.DIRECT_OPENSANDBOX_PROXY_IPV4,
        "SANDBOX_WORKSPACE_ROOT": str(WORKSPACE_ROOT),
        "OPENSANDBOX_EXECUTOR_IMAGE": BACKEND,
        "OPENSANDBOX_EXECUTOR_IMAGE_DIGEST": "sha256:" + "3" * 64,
        **changes,
    }
    return "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"


def _host_config() -> bootstrap.OpenSandboxHostConfig:
    return bootstrap.OpenSandboxHostConfig(
        server_image=SERVER_IMAGE,
        server_image_digest="sha256:" + "5" * 64,
        execd_image=EXECD_IMAGE,
        egress_image=EGRESS_IMAGE,
        server_uid=max(1, bootstrap.os.getuid()),
        server_gid=max(1, bootstrap.os.getgid()),
        docker_socket_gid=max(1, bootstrap.os.getgid()),
        lifecycle_address="10.40.0.10",
        topology=bootstrap.authority.DIRECT_OPENSANDBOX_DEFAULT_TOPOLOGY,
        workspace_root=WORKSPACE_ROOT,
        api_key_sha256=bootstrap.hashlib.sha256(("a" * 32).encode("utf-8")).hexdigest(),
        config_sha256="9" * 64,
    )


def test_host_config_requires_secure_consistent_production_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    socket_path = Path("docker.sock")
    unix_socket = socket.socket(socket.AF_UNIX)
    try:
        unix_socket.bind(str(socket_path))
        socket_gid = socket_path.stat().st_gid
        env_file = _secure_file(
            tmp_path / "server.env", _server_environment(socket_gid)
        )
        config_file = _secure_file(
            tmp_path / "server.toml", _server_config(), mode=0o640
        )

        config = bootstrap.load_opensandbox_host_config(
            env_file,
            config_file,
            docker_socket=socket_path,
            expected_uid=bootstrap.os.getuid(),
        )
        config_file.chmod(0o600)
        with pytest.raises(bootstrap.BootstrapError, match="metadata mismatch"):
            bootstrap.load_opensandbox_host_config(
                env_file,
                config_file,
                docker_socket=socket_path,
                expected_uid=bootstrap.os.getuid(),
            )
    finally:
        unix_socket.close()

    assert config.server_image == SERVER_IMAGE
    assert config.execd_image == EXECD_IMAGE
    assert config.egress_image == EGRESS_IMAGE
    assert config.lifecycle_address == "10.40.0.10"
    assert config.topology == bootstrap.authority.DIRECT_OPENSANDBOX_DEFAULT_TOPOLOGY
    assert config.workspace_root == WORKSPACE_ROOT
    assert bootstrap.re.fullmatch(r"[0-9a-f]{64}", config.config_sha256)


def test_operator_topology_and_workspace_root_flow_into_host_config_and_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    def load_config(
        name: str,
        environment_changes: dict[str, str] | None = None,
        config_changes: dict[str, str] | None = None,
    ) -> bootstrap.OpenSandboxHostConfig:
        directory = tmp_path / name
        directory.mkdir()
        socket_path = directory / "docker.sock"
        unix_socket = socket.socket(socket.AF_UNIX)
        try:
            unix_socket.bind(str(socket_path.relative_to(tmp_path)))
            env_file = _secure_file(
                directory / "server.env",
                _server_environment(
                    socket_path.stat().st_gid, **(environment_changes or {})
                ),
            )
            config_file = _secure_file(
                directory / "server.toml",
                _server_config(**(config_changes or {})),
                mode=0o640,
            )
            return bootstrap.load_opensandbox_host_config(
                env_file,
                config_file,
                docker_socket=socket_path,
                expected_uid=bootstrap.os.getuid(),
            )
        finally:
            unix_socket.close()

    baseline = load_config("baseline")
    custom_network_name = "ai-platform-egress-custom"
    custom_bridge = "br-osb-custom"
    custom_subnet = "172.31.76.0/27"
    custom_proxy = "172.31.76.3"
    custom_workspace_root = Path("/srv/opensandbox/workspaces/tenant-a")
    custom = load_config(
        "custom",
        {
            "OPENSANDBOX_EGRESS_BRIDGE": custom_bridge,
            "OPENSANDBOX_EGRESS_SUBNET": custom_subnet,
            "OPENSANDBOX_EGRESS_PROXY_IPV4": custom_proxy,
        },
        {
            "network_mode": custom_network_name,
            "allowed_host_paths": f'["{custom_workspace_root}"]',
        },
    )

    assert custom.topology == bootstrap.authority.DirectOpenSandboxTopology(
        network_name=custom_network_name,
        bridge_name=custom_bridge,
        subnet=custom_subnet,
        proxy_ipv4=custom_proxy,
    )
    assert custom.workspace_root == custom_workspace_root
    assert custom.config_sha256 != baseline.config_sha256

    hash_variants = [
        load_config("network-name", config_changes={"network_mode": custom_network_name}),
        load_config("bridge", {"OPENSANDBOX_EGRESS_BRIDGE": custom_bridge}),
        load_config("subnet", {"OPENSANDBOX_EGRESS_SUBNET": custom_subnet}),
        load_config("proxy", {"OPENSANDBOX_EGRESS_PROXY_IPV4": custom_proxy}),
        load_config(
            "workspace-root",
            config_changes={
                "allowed_host_paths": f'["{custom_workspace_root}"]'
            },
        ),
    ]
    assert all(config.config_sha256 != baseline.config_sha256 for config in hash_variants)


@pytest.mark.parametrize(
    ("environment_change", "config_change", "message"),
    [
        (
            {"OPENSANDBOX_SERVER_IMAGE_DIGEST": "sha256:" + "7" * 64},
            {},
            "image digest mismatch",
        ),
        (
            {},
            {"network_mode": "bridge"},
            "egress topology is invalid",
        ),
        (
            {"OPENSANDBOX_LIFECYCLE_LISTEN_ADDRESS": "8.8.8.8"},
            {},
            "lifecycle address is invalid",
        ),
        ({}, {"api_key": "short"}, "violates production policy"),
        ({}, {"api_key": "a" * 31 + "$"}, "violates production policy"),
        ({}, {"execd_image": "opensandbox-execd:latest"}, "not an immutable image"),
        ({}, {"egress_image": "opensandbox-egress:latest"}, "not an immutable image"),
        (
            {"OPENSANDBOX_EGRESS_BRIDGE": "br-osb;$(touch-bad)"},
            {},
            "egress topology is invalid",
        ),
        (
            {"OPENSANDBOX_EGRESS_SUBNET": "172.31.76.0/27x"},
            {},
            "egress topology is invalid",
        ),
        (
            {"OPENSANDBOX_EGRESS_PROXY_IPV4": "172.31.77.2"},
            {},
            "egress topology is invalid",
        ),
        (
            {"OPENSANDBOX_EGRESS_PROXY_IPV4": "172.31.76.1"},
            {},
            "egress topology is invalid",
        ),
        ({}, {"network_mode": "bridge;malicious"}, "egress topology is invalid"),
        ({}, {"allowed_host_paths": '["/"]'}, "workspace root is invalid"),
        ({}, {"allowed_host_paths": '["../workspace"]'}, "workspace root is invalid"),
        ({}, {"host_ip": "10.40.0.12"}, "violates production policy"),
        ({}, {"sandbox_binds": '["/:/host:rw"]'}, "violates production policy"),
        ({}, {"egress_mode": "dns"}, "violates production policy"),
    ],
)
def test_host_config_rejects_unsafe_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environment_change: dict[str, str],
    config_change: dict[str, str],
    message: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    socket_path = Path("docker.sock")
    unix_socket = socket.socket(socket.AF_UNIX)
    try:
        unix_socket.bind(str(socket_path))
        env_file = _secure_file(
            tmp_path / "server.env",
            _server_environment(socket_path.stat().st_gid, **environment_change),
        )
        config_file = _secure_file(
            tmp_path / "server.toml", _server_config(**config_change), mode=0o640
        )
        with pytest.raises(bootstrap.BootstrapError, match=message):
            bootstrap.load_opensandbox_host_config(
                env_file,
                config_file,
                docker_socket=socket_path,
                expected_uid=bootstrap.os.getuid(),
            )
    finally:
        unix_socket.close()


def test_host_config_rejects_placeholder_and_unsafe_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    socket_path = Path("docker.sock")
    unix_socket = socket.socket(socket.AF_UNIX)
    try:
        unix_socket.bind(str(socket_path))
        env_file = _secure_file(
            tmp_path / "server.env",
            _server_environment(
                socket_path.stat().st_gid,
                OPENSANDBOX_SERVER_UID="REQUIRED_UID",
            ),
        )
        config_file = _secure_file(
            tmp_path / "server.toml", _server_config(), mode=0o640
        )
        with pytest.raises(bootstrap.BootstrapError, match="incomplete"):
            bootstrap.load_opensandbox_host_config(
                env_file,
                config_file,
                docker_socket=socket_path,
                expected_uid=bootstrap.os.getuid(),
            )
        env_file.chmod(0o644)
        with pytest.raises(bootstrap.BootstrapError, match="metadata mismatch"):
            bootstrap.load_opensandbox_host_config(
                env_file,
                config_file,
                docker_socket=socket_path,
                expected_uid=bootstrap.os.getuid(),
            )
    finally:
        unix_socket.close()


def test_host_config_rejects_unreviewed_top_level_sections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    socket_path = Path("docker.sock")
    unix_socket = socket.socket(socket.AF_UNIX)
    try:
        unix_socket.bind(str(socket_path))
        env_file = _secure_file(
            tmp_path / "server.env",
            _server_environment(socket_path.stat().st_gid),
        )
        config_file = _secure_file(
            tmp_path / "server.toml",
            _server_config() + '\n[proxy]\ntarget = "http://127.0.0.1"\n',
            mode=0o640,
        )
        with pytest.raises(bootstrap.BootstrapError, match="incomplete"):
            bootstrap.load_opensandbox_host_config(
                env_file,
                config_file,
                docker_socket=socket_path,
                expected_uid=bootstrap.os.getuid(),
            )
    finally:
        unix_socket.close()


def test_host_config_rejects_writable_parent_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o700)
    unsafe.chmod(0o777)
    socket_path = Path("docker.sock")
    unix_socket = socket.socket(socket.AF_UNIX)
    try:
        unix_socket.bind(str(socket_path))
        env_file = _secure_file(
            unsafe / "server.env",
            _server_environment(socket_path.stat().st_gid),
        )
        config_file = _secure_file(
            tmp_path / "server.toml", _server_config(), mode=0o640
        )
        with pytest.raises(bootstrap.BootstrapError, match="parent chain is unsafe"):
            bootstrap.load_opensandbox_host_config(
                env_file,
                config_file,
                docker_socket=socket_path,
                expected_uid=bootstrap.os.getuid(),
            )
    finally:
        unix_socket.close()


@pytest.mark.parametrize(
    "change",
    [
        {"OPENSANDBOX_BASE_URL": "http://10.40.0.12:8080"},
        {"OPENSANDBOX_API_KEY": "b" * 32},
        {"OPENSANDBOX_EXPECTED_NETWORK_MODE": "ai-platform-egress-other"},
        {"OPENSANDBOX_EGRESS_BRIDGE": "br-osb-other"},
        {"OPENSANDBOX_EGRESS_SUBNET": "172.31.76.0/25"},
        {"OPENSANDBOX_EGRESS_PROXY_IPV4": "172.31.76.3"},
        {"SANDBOX_WORKSPACE_ROOT": "/srv/opensandbox/workspaces/other"},
    ],
)
def test_application_environment_must_match_the_host_contract(
    tmp_path: Path,
    change: dict[str, str],
) -> None:
    good = _secure_file(tmp_path / "good.env", _application_environment())
    bootstrap._require_application_host_contract(
        good,
        _host_config(),
        expected_uid=bootstrap.os.getuid(),
    )
    bad = _secure_file(tmp_path / "bad.env", _application_environment(**change))
    with pytest.raises(bootstrap.BootstrapError, match="contract mismatch"):
        bootstrap._require_application_host_contract(
            bad,
            _host_config(),
            expected_uid=bootstrap.os.getuid(),
        )


class HostRunner:
    def __init__(self, *, active: bool = False) -> None:
        self.active = active
        self.commands: list[list[str]] = []

    def run(
        self,
        command: list[str] | tuple[str, ...],
        *,
        output: bool = False,
        check: bool = True,
        timeout: int = 300,
    ) -> subprocess.CompletedProcess[str]:
        del output, check, timeout
        command = list(command)
        self.commands.append(command)
        stdout = ""
        returncode = 0
        if "info" in command:
            stdout = json.dumps({"runc": {}, "runsc": {}})
        elif command[:3] == ["systemctl", "is-active", "--quiet"]:
            returncode = 0 if self.active else 3
        elif command[:2] == ["systemctl", "start"]:
            self.active = True
        elif command[:2] == ["systemctl", "restart"]:
            self.active = True
        return subprocess.CompletedProcess(
            command, returncode, stdout=stdout, stderr=""
        )


def _server_container(commit: str = COMMIT) -> dict[str, object]:
    return {
        "Image": SERVER_IMAGE_ID,
        "Config": {
            "Image": SERVER_IMAGE,
            "User": f"{max(1, bootstrap.os.getuid())}:{max(1, bootstrap.os.getgid())}",
            "Entrypoint": ["/opt/opensandbox/server"],
            "Cmd": ["--config", "/etc/opensandbox/config.toml"],
            "Labels": {
                "ai-platform.source-commit": commit,
                "ai-platform.host-config-sha256": "9" * 64,
                "ai-platform.release-owner": "production-bootstrap",
                "ai-platform.release-role": "opensandbox-server",
                "ai-platform.security-domain": "execution-controller",
            },
        },
        "HostConfig": {
            "AutoRemove": True,
            "Privileged": False,
            "ReadonlyRootfs": True,
            "CapAdd": None,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
            "PidsLimit": 512,
            "NetworkMode": "host",
            "GroupAdd": [str(max(1, bootstrap.os.getgid()))],
            "Binds": None,
            "PortBindings": {},
            "Tmpfs": {"/tmp": "rw,noexec,nosuid,nodev,size=64m"},
        },
        "State": {"Running": True},
        "NetworkSettings": {
            "Networks": {"host": {}},
            "Ports": {"8080/tcp": None},
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": "/var/run/docker.sock",
                "Destination": "/var/run/docker.sock",
                "RW": False,
            },
            {
                "Type": "bind",
                "Source": str(bootstrap.SERVER_CONFIG_FILE),
                "Destination": "/etc/opensandbox/config.toml",
                "RW": False,
            },
            {
                "Type": "bind",
                "Source": str(bootstrap.SERVER_STATE_ROOT),
                "Destination": "/var/lib/ai-platform-opensandbox",
                "RW": True,
            },
        ],
    }


class ContainerInspectRunner:
    def __init__(self, container: dict[str, object]) -> None:
        self.container = container

    def run(
        self,
        command: list[str] | tuple[str, ...],
        *,
        output: bool = False,
        check: bool = True,
        timeout: int = 300,
    ) -> subprocess.CompletedProcess[str]:
        del output, check, timeout
        payload: list[dict[str, object]]
        if "image" in command and "inspect" in command:
            payload = [
                {
                    "Id": SERVER_IMAGE_ID,
                    "Config": {"Entrypoint": ["/opt/opensandbox/server"]},
                }
            ]
        else:
            payload = [self.container]
        return subprocess.CompletedProcess(
            list(command),
            0,
            stdout=json.dumps(payload),
            stderr="",
        )


def test_server_runtime_validation_requires_exact_unit_source_and_safety() -> None:
    container = _server_container()
    bootstrap._validate_server_container(
        ContainerInspectRunner(container),
        _host_config(),
        COMMIT,
    )

    with pytest.raises(bootstrap.BootstrapError, match="identity mismatch"):
        bootstrap._validate_server_container(
            ContainerInspectRunner(container),
            _host_config(),
            OLD_COMMIT,
        )

    host_config = container["HostConfig"]
    assert isinstance(host_config, dict)
    host_config["Privileged"] = True
    with pytest.raises(bootstrap.BootstrapError, match="identity mismatch"):
        bootstrap._validate_server_container(
            ContainerInspectRunner(container),
            _host_config(),
            COMMIT,
        )

    host_config["Privileged"] = False
    host_config["NetworkMode"] = "bridge"
    with pytest.raises(bootstrap.BootstrapError, match="identity mismatch"):
        bootstrap._validate_server_container(
            ContainerInspectRunner(container),
            _host_config(),
            COMMIT,
        )

    host_config["NetworkMode"] = "host"
    network_settings = container["NetworkSettings"]
    assert isinstance(network_settings, dict)
    network_settings["Ports"] = {
        "8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]
    }
    with pytest.raises(bootstrap.BootstrapError, match="identity mismatch"):
        bootstrap._validate_server_container(
            ContainerInspectRunner(container),
            _host_config(),
            COMMIT,
        )

    network_settings["Ports"] = {"8080/tcp": None}
    config = container["Config"]
    assert isinstance(config, dict)
    config["Entrypoint"] = ["/bin/sh"]
    with pytest.raises(bootstrap.BootstrapError, match="identity mismatch"):
        bootstrap._validate_server_container(
            ContainerInspectRunner(container),
            _host_config(),
            COMMIT,
        )


def test_unit_guard_removes_only_the_exact_managed_container() -> None:
    commands: list[list[str]] = []
    inventories = iter(
        [
            "abc123|ai-platform-opensandbox-server\n",
            "",
        ]
    )

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[1:4] == ["container", "ls", "-a"]:
            stdout = next(inventories)
        elif command[1:3] == ["container", "inspect"]:
            stdout = json.dumps(
                [
                    {
                        "Name": "/ai-platform-opensandbox-server",
                        "Config": {
                            "Image": SERVER_IMAGE,
                            "Labels": {
                                "ai-platform.source-commit": COMMIT,
                                "ai-platform.host-config-sha256": "9" * 64,
                                **unit_guard.IDENTITY_LABELS,
                            },
                        },
                    }
                ]
            )
        else:
            stdout = ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    unit_guard.remove_managed_container(run=run)

    assert [unit_guard.DOCKER, "container", "rm", "-f", "abc123"] in commands


def test_unit_guard_rejects_a_foreign_fixed_name_container() -> None:
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[1:4] == ["container", "ls", "-a"]:
            stdout = "abc123|ai-platform-opensandbox-server\n"
        elif command[1:3] == ["container", "inspect"]:
            stdout = json.dumps(
                [
                    {
                        "Name": "/ai-platform-opensandbox-server",
                        "Config": {"Image": SERVER_IMAGE, "Labels": {}},
                    }
                ]
            )
        else:
            stdout = ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    with pytest.raises(unit_guard.GuardError, match="not repository-managed"):
        unit_guard.remove_managed_container(run=run)

    assert not any(command[1:3] == ["container", "rm"] for command in commands)


def test_platform_workspace_uses_the_runtime_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[Path, int, int, int]] = []
    monkeypatch.setattr(
        bootstrap,
        "_ensure_directory",
        lambda path, *, uid, gid, mode: observed.append((path, uid, gid, mode)),
    )

    bootstrap._ensure_platform_workspace(tmp_path / "runtime-workspaces")

    assert observed == [
        (
            tmp_path / "runtime-workspaces",
            bootstrap.PLATFORM_WORKSPACE_UID,
            bootstrap.PLATFORM_WORKSPACE_GID,
            0o750,
        )
    ]


def test_created_host_directory_has_exact_mode_under_strict_umask(
    tmp_path: Path,
) -> None:
    target = tmp_path / "managed"
    previous_umask = bootstrap.os.umask(0o077)
    try:
        bootstrap._ensure_directory(
            target,
            uid=bootstrap.os.getuid(),
            gid=bootstrap.os.getgid(),
            mode=0o750,
        )
    finally:
        bootstrap.os.umask(previous_umask)

    metadata = target.stat(follow_symlinks=False)
    assert stat.S_IMODE(metadata.st_mode) == 0o750


def _checkout_with_unit(tmp_path: Path) -> Path:
    checkout = tmp_path / "checkout"
    destination = checkout / bootstrap.UNIT_TEMPLATE
    destination.parent.mkdir(parents=True)
    destination.write_text(
        (ROOT / bootstrap.UNIT_TEMPLATE).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    guard = checkout / "tools/opensandbox_unit_guard.py"
    guard.parent.mkdir(parents=True)
    guard.write_text(
        (ROOT / "tools/opensandbox_unit_guard.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    commands = [
        ["git", "init", "--quiet", str(checkout)],
        ["git", "-C", str(checkout), "config", "user.name", "Codex Test"],
        [
            "git",
            "-C",
            str(checkout),
            "config",
            "user.email",
            "codex-test@example.invalid",
        ],
        [
            "git",
            "-C",
            str(checkout),
            "add",
            bootstrap.UNIT_TEMPLATE.as_posix(),
            "tools/opensandbox_unit_guard.py",
        ],
        ["git", "-C", str(checkout), "commit", "--quiet", "-m", "test checkout"],
    ]
    for command in commands:
        subprocess.run(command, check=True, capture_output=True, text=True)
    return checkout


def _checkout_commit(checkout: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_host_bootstrap_installs_unit_and_starts_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = _checkout_with_unit(tmp_path)
    commit = _checkout_commit(checkout)
    unit_path = tmp_path / "systemd" / "opensandbox.service"
    runner = HostRunner()
    events: list[str] = []
    monkeypatch.setattr(
        bootstrap, "load_opensandbox_host_config", lambda *_: _host_config()
    )
    monkeypatch.setattr(bootstrap, "_require_host_address_available", lambda *_: None)
    monkeypatch.setattr(bootstrap, "_ensure_directory", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bootstrap, "_ensure_platform_workspace", lambda _path: None)
    monkeypatch.setattr(
        bootstrap, "_validate_server_container", lambda *_: events.append("container")
    )

    result = bootstrap.HostBootstrap(
        checkout,
        runner=runner,
        health_probe=lambda address: events.append(f"health:{address}"),
        unit_path=unit_path,
        health_timeout=0,
    ).run(commit)

    assert result == _host_config()
    assert unit_path.stat().st_mode & 0o777 == 0o644
    assert f"ai-platform.source-commit={commit}" in unit_path.read_text(
        encoding="utf-8"
    )
    helper_path = str((checkout / "tools/opensandbox_unit_guard.py").resolve())
    assert unit_path.read_text(encoding="utf-8").count(helper_path) == 2
    assert [command[-1] for command in runner.commands if "pull" in command] == [
        SERVER_IMAGE,
        EXECD_IMAGE,
        EGRESS_IMAGE,
    ]
    assert ["systemctl", "start", "opensandbox.service"] in runner.commands
    assert events == ["container", "health:10.40.0.10"]


def test_host_bootstrap_rejects_missing_network_guard_before_unit_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = _checkout_with_unit(tmp_path)
    commit = _checkout_commit(checkout)
    unit_path = tmp_path / "systemd" / "opensandbox.service"
    runner = HostRunner()
    monkeypatch.setattr(
        bootstrap, "load_opensandbox_host_config", lambda *_: _host_config()
    )
    monkeypatch.setattr(bootstrap, "_require_host_address_available", lambda *_: None)
    monkeypatch.setattr(
        bootstrap.transition,
        "_require_network_guard",
        lambda _checkout, *, topology: (_ for _ in ()).throw(
            bootstrap.transition.TransitionError("guard invalid")
        ),
    )

    with pytest.raises(bootstrap.BootstrapError, match="host-input guard is invalid"):
        bootstrap.HostBootstrap(
            checkout,
            runner=runner,
            health_probe=lambda _address: None,
            unit_path=unit_path,
        ).run(commit)

    assert not unit_path.exists()
    assert not any("pull" in command for command in runner.commands)


def test_host_bootstrap_rejects_source_checkout_drift_before_host_preparation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _checkout_with_unit(tmp_path)
    commit = _checkout_commit(checkout)
    helper = checkout / "tools/opensandbox_unit_guard.py"
    helper.write_text(helper.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")
    runner = HostRunner()
    unit_path = tmp_path / "systemd" / "opensandbox.service"
    monkeypatch.setattr(
        bootstrap,
        "load_opensandbox_host_config",
        lambda *_args: pytest.fail("host configuration was read before source verification"),
    )

    with pytest.raises(bootstrap.BootstrapError, match="source checkout does not match"):
        bootstrap.HostBootstrap(
            checkout,
            runner=runner,
            health_probe=lambda _address: None,
            unit_path=unit_path,
        ).run(commit)

    assert runner.commands == []
    assert not unit_path.exists()


@pytest.mark.parametrize("unsafe", [
    "helper-owner", "parent-owner", "helper-write", "parent-write",
    "helper-symlink", "parent-symlink",
])
def test_clean_checkout_with_an_unprotected_root_helper_is_rejected_before_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsafe: str,
) -> None:
    checkout = _checkout_with_unit(tmp_path)
    commit = _checkout_commit(checkout)
    helper = checkout / "tools/opensandbox_unit_guard.py"
    paths = {helper, *helper.parents}
    original_lstat = Path.lstat

    def metadata(path):
        if path not in paths:
            return original_lstat(path)
        is_helper = path == helper
        mode = stat.S_IFREG | 0o644 if is_helper else stat.S_IFDIR | 0o755
        owner = 0
        selected = helper if unsafe.startswith("helper") else checkout
        if path == selected:
            if unsafe.endswith("owner"):
                owner = 1001
            elif unsafe.endswith("write"):
                mode |= 0o020
            else:
                mode = stat.S_IFLNK | 0o777
        return SimpleNamespace(st_mode=mode, st_uid=owner)

    monkeypatch.setattr(Path, "lstat", metadata)
    monkeypatch.setattr(bootstrap, "_require_root_owned_unit_helper", REAL_UNIT_HELPER_VALIDATION)
    monkeypatch.setattr(bootstrap, "load_opensandbox_host_config", lambda *_args: pytest.fail("host preparation began"))
    runner = HostRunner()
    unit_path = tmp_path / "systemd/opensandbox.service"

    with pytest.raises(bootstrap.BootstrapError, match="unit helper path is unsafe"):
        bootstrap.HostBootstrap(checkout, runner=runner, unit_path=unit_path).run(commit)
    assert runner.commands == []
    assert not unit_path.exists()


def test_equivalent_managed_unit_does_not_restart_active_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = _checkout_with_unit(tmp_path)
    commit = _checkout_commit(checkout)
    unit_path = tmp_path / "systemd" / "opensandbox.service"
    unit_path.parent.mkdir(parents=True)
    unit_path.write_text(
        bootstrap._render_unit(
            checkout / bootstrap.UNIT_TEMPLATE,
            OLD_COMMIT,
            _host_config().config_sha256,
        ),
        encoding="utf-8",
    )
    unit_path.chmod(0o644)
    runner = HostRunner(active=True)
    monkeypatch.setattr(
        bootstrap, "load_opensandbox_host_config", lambda *_: _host_config()
    )
    monkeypatch.setattr(bootstrap, "_require_host_address_available", lambda *_: None)
    monkeypatch.setattr(bootstrap, "_ensure_directory", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bootstrap, "_ensure_platform_workspace", lambda _path: None)
    observed_commits: list[str] = []
    monkeypatch.setattr(
        bootstrap,
        "_validate_server_container",
        lambda _runner, _config, expected: observed_commits.append(expected),
    )

    bootstrap.HostBootstrap(
        checkout,
        runner=runner,
        health_probe=lambda _address: None,
        unit_path=unit_path,
        health_timeout=0,
    ).run(commit)

    assert not any(
        command[:2] == ["systemctl", "restart"] for command in runner.commands
    )
    assert f"ai-platform.source-commit={OLD_COMMIT}" in unit_path.read_text(
        encoding="utf-8"
    )
    assert observed_commits == [OLD_COMMIT]


def test_changed_host_unit_can_restore_the_previous_running_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _checkout_with_unit(tmp_path)
    commit = _checkout_commit(checkout)
    unit_path = tmp_path / "systemd" / "opensandbox.service"
    unit_path.parent.mkdir(parents=True)
    previous = bootstrap._render_unit(
        checkout / bootstrap.UNIT_TEMPLATE,
        OLD_COMMIT,
        _host_config().config_sha256,
    )
    previous = previous.replace(
        str((checkout / "tools/opensandbox_unit_guard.py").resolve()),
        f"/data/ai-platform-prod/releases/{OLD_COMMIT}/tools/opensandbox_unit_guard.py",
    )
    previous = previous.replace("KillMode=control-group", "KillMode=process")
    unit_path.write_text(previous, encoding="utf-8")
    unit_path.chmod(0o644)
    runner = HostRunner(active=True)
    observed_commits: list[str] = []
    monkeypatch.setattr(
        bootstrap, "load_opensandbox_host_config", lambda *_: _host_config()
    )
    monkeypatch.setattr(bootstrap, "_require_host_address_available", lambda *_: None)
    monkeypatch.setattr(bootstrap, "_ensure_directory", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bootstrap, "_ensure_platform_workspace", lambda _path: None)
    monkeypatch.setattr(
        bootstrap,
        "_validate_server_container",
        lambda _runner, _config, expected: observed_commits.append(expected),
    )
    host = bootstrap.HostBootstrap(
        checkout,
        runner=runner,
        health_probe=lambda _address: None,
        unit_path=unit_path,
        health_timeout=0,
    )

    host.run(commit, require_existing_unit=True)
    host.rollback()

    assert unit_path.read_text(encoding="utf-8") == previous
    assert observed_commits == [commit, OLD_COMMIT]
    assert ["systemctl", "stop", "opensandbox.service"] in runner.commands


def test_existing_runtime_requires_a_bootstrap_managed_host_unit_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _checkout_with_unit(tmp_path)
    commit = _checkout_commit(checkout)
    unit_path = tmp_path / "systemd" / "opensandbox.service"
    runner = HostRunner()
    monkeypatch.setattr(
        bootstrap, "load_opensandbox_host_config", lambda *_: _host_config()
    )
    monkeypatch.setattr(bootstrap, "_require_host_address_available", lambda *_: None)
    monkeypatch.setattr(
        bootstrap,
        "_ensure_directory",
        lambda *_args, **_kwargs: pytest.fail("host directory was mutated"),
    )
    monkeypatch.setattr(
        bootstrap,
        "_ensure_platform_workspace",
        lambda _path: pytest.fail("platform workspace was mutated"),
    )

    with pytest.raises(bootstrap.BootstrapError, match="lacks a bootstrap-managed"):
        bootstrap.HostBootstrap(
            checkout,
            runner=runner,
            health_probe=lambda _address: None,
            unit_path=unit_path,
            health_timeout=0,
        ).run(commit, require_existing_unit=True)

    assert not any("pull" in command for command in runner.commands)
    assert not any("network" in command for command in runner.commands)


def test_existing_runtime_rejects_opensandbox_host_configuration_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _checkout_with_unit(tmp_path)
    commit = _checkout_commit(checkout)
    unit_path = tmp_path / "systemd" / "opensandbox.service"
    unit_path.parent.mkdir(parents=True)
    unit_path.write_text(
        bootstrap._render_unit(
            checkout / bootstrap.UNIT_TEMPLATE,
            OLD_COMMIT,
            "a" * 64,
        ),
        encoding="utf-8",
    )
    unit_path.chmod(0o644)
    runner = HostRunner(active=True)
    monkeypatch.setattr(
        bootstrap, "load_opensandbox_host_config", lambda *_: _host_config()
    )
    monkeypatch.setattr(bootstrap, "_require_host_address_available", lambda *_: None)
    monkeypatch.setattr(
        bootstrap,
        "_ensure_directory",
        lambda *_args, **_kwargs: pytest.fail("host directory was mutated"),
    )

    with pytest.raises(bootstrap.BootstrapError, match="cannot change OpenSandbox"):
        bootstrap.HostBootstrap(
            checkout,
            runner=runner,
            health_probe=lambda _address: None,
            unit_path=unit_path,
            health_timeout=0,
        ).run(commit, require_existing_unit=True)

    assert not any("pull" in command for command in runner.commands)
    assert not any("network" in command for command in runner.commands)


def test_unit_restore_fails_if_the_server_container_remains_present(
    tmp_path: Path,
) -> None:
    class RemainingContainerRunner(HostRunner):
        def run(
            self,
            command: list[str] | tuple[str, ...],
            *,
            output: bool = False,
            check: bool = True,
            timeout: int = 300,
        ) -> subprocess.CompletedProcess[str]:
            if list(command)[-5:] == [
                "container",
                "ls",
                "-a",
                "--format",
                "{{.Names}}",
            ]:
                return subprocess.CompletedProcess(
                    list(command),
                    0,
                    stdout=f"{bootstrap.SERVER_CONTAINER}\n",
                    stderr="",
                )
            return super().run(
                command,
                output=output,
                check=check,
                timeout=timeout,
            )

    with pytest.raises(bootstrap.BootstrapError, match="remains present"):
        bootstrap._restore_unit(
            RemainingContainerRunner(),
            None,
            unit_path=tmp_path / "opensandbox.service",
        )


@pytest.mark.parametrize(
    ("previous", "failed_action"),
    [
        (None, "disable"),
        (b"previous managed unit", "stop"),
    ],
)
def test_unit_restore_fails_closed_when_systemd_stop_fails(
    tmp_path: Path,
    previous: bytes | None,
    failed_action: str,
) -> None:
    class FailingSystemdRunner(HostRunner):
        def run(
            self,
            command: list[str] | tuple[str, ...],
            *,
            output: bool = False,
            check: bool = True,
            timeout: int = 300,
        ) -> subprocess.CompletedProcess[str]:
            command = list(command)
            if command[:2] == ["systemctl", failed_action]:
                self.commands.append(command)
                result = subprocess.CompletedProcess(command, 1, stdout="", stderr="")
                if check:
                    raise bootstrap.BootstrapError("host command failed")
                return result
            return super().run(
                command,
                output=output,
                check=check,
                timeout=timeout,
            )

    with pytest.raises(bootstrap.BootstrapError, match="host command failed"):
        bootstrap._restore_unit(
            FailingSystemdRunner(),
            previous,
            unit_path=tmp_path / "opensandbox.service",
        )


def test_retired_application_cli_fails_before_host_preparation() -> None:
    result = subprocess.run(
        [sys.executable, "-I", str(ROOT / "tools/production_bootstrap.py"), "--latest"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "release package's deploy.py" in result.stderr


def test_production_unit_is_distinctly_managed_and_uses_host_network_guard() -> None:
    unit = (ROOT / bootstrap.UNIT_TEMPLATE).read_text(encoding="utf-8")
    assert unit.count("@@SOURCE_COMMIT@@") == 1
    assert unit.count("@@HOST_CONFIG_SHA256@@") == 1
    assert unit.count("@@GUARD_PATH@@") == 2
    assert "ai-platform.release-owner=production-bootstrap" in unit
    assert "--network host" in unit
    assert "--publish" not in unit
    assert "OPENSANDBOX_EGRESS_LISTEN_ADDRESS" not in unit
    assert "Requires=docker.service ai-platform-opensandbox-network-guard.service" in unit
    assert "ExecStartPre=/usr/bin/python3 -I @@GUARD_PATH@@" in unit
    assert "ExecStop=/usr/bin/python3 -I @@GUARD_PATH@@" in unit
    assert "docker rm -f ai-platform-opensandbox-server" not in unit
    assert "KillMode=control-group" in unit


def test_readme_runbook_and_examples_expose_the_production_package() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    runbook = (ROOT / "docs/operations/release-operations-runbook.md").read_text(
        encoding="utf-8"
    )
    production = (ROOT / "docs/operations/production-bootstrap.md").read_text(
        encoding="utf-8"
    )
    environment = (ROOT / "deploy/opensandbox/server-production.env.example").read_text(
        encoding="utf-8"
    )
    config = (ROOT / "deploy/opensandbox/server-production.toml.example").read_text(
        encoding="utf-8"
    )

    command = "python3 deploy.py"
    assert command in readme and command in runbook and command in production
    assert "ai-platform-production.tar.gz" in readme
    assert "/absolute/path/to/operator.env" in production
    assert "root:<OPENSANDBOX_SERVER_GID> 0640" in production
    assert "application package" in production
    assert "Docker daemon authority" in production
    declared = {
        line.partition("=")[0]
        for line in environment.splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    assert declared == bootstrap.SERVER_ENV_KEYS
    assert "REQUIRED_RANDOM_LIFECYCLE_API_KEY_AT_LEAST_32_BYTES" in config
    assert "root:<OPENSANDBOX_SERVER_GID> mode 0640" in config
    assert 'docker_runtime = "runsc"' in config
    assert 'host = "REQUIRED_PRIVATE_LIFECYCLE_IPV4_ADDRESS"' in config
    assert (
        'network_mode = "ai-platform-opensandbox-egress-v2"' in config
    )
    assert 'mode = "dns+nft"' in config
    assert 'allowed_host_paths = ["/data/opensandbox/workspaces/ai-platform"]' in config
    assert "sandbox_binds = []" in config
    assert "server/v0.1.13-or-newer" in environment
