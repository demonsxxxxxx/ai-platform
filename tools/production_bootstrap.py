"""Converge OpenSandbox and deploy the approved production subject."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
from http.client import HTTPException
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from typing import Any, Callable, Mapping, Sequence
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener


if __name__ == "__main__" and not sys.flags.isolated:
    raise SystemExit("run production bootstrap through the approved deploy wrapper")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import latest_main_quickstart as latest  # noqa: E402
from tools import release_authority as authority  # noqa: E402
from tools import s75_opensandbox_transition as transition  # noqa: E402
from tools import sandbox_quickstart  # noqa: E402


MANAGED_ROOT = Path("/data/ai-platform-prod")
SERVER_ENV_FILE = Path("/etc/ai-platform/opensandbox/server.env")
SERVER_CONFIG_FILE = Path("/etc/ai-platform/opensandbox/server.toml")
SYSTEMD_UNIT = Path("/etc/systemd/system/opensandbox.service")
UNIT_TEMPLATE = Path("deploy/opensandbox/opensandbox-production.service")
DOCKER_SOCKET = Path("/var/run/docker.sock")
SERVER_STATE_ROOT = Path("/var/lib/ai-platform-opensandbox")
PLATFORM_WORKSPACE_ROOT = MANAGED_ROOT / "runtime-workspaces"
PLATFORM_WORKSPACE_UID = 10001
PLATFORM_WORKSPACE_GID = 10001
SERVER_CONTAINER = "ai-platform-opensandbox-server"
DOCKER = ("docker", "--context", "default")
SYSTEMD = ("systemctl",)
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
IMAGE_RE = re.compile(r"[^\s@]+@(?P<digest>sha256:[0-9a-f]{64})\Z")
API_KEY_RE = re.compile(r"[A-Za-z0-9._~-]{32,256}\Z")
KEY_RE = re.compile(r"[A-Z][A-Z0-9_]*\Z")
SOURCE_LABEL_RE = re.compile(r"ai-platform\.source-commit=(?P<commit>[0-9a-f]{40})")
CONFIG_LABEL_RE = re.compile(
    r"ai-platform\.host-config-sha256=(?P<digest>[0-9a-f]{64})"
)
UNIT_GUARD_RE = re.compile(
    r"/data/ai-platform-prod/releases/(?P<commit>[0-9a-f]{40})/"
    r"tools/opensandbox_unit_guard\.py"
)
SERVER_ENV_KEYS = frozenset(
    {
        "OPENSANDBOX_SERVER_IMAGE",
        "OPENSANDBOX_SERVER_IMAGE_DIGEST",
        "OPENSANDBOX_SERVER_UID",
        "OPENSANDBOX_SERVER_GID",
        "OPENSANDBOX_DOCKER_SOCKET_GID",
        "OPENSANDBOX_LIFECYCLE_LISTEN_ADDRESS",
    }
)
APPLICATION_HOST_KEYS = frozenset(
    {
        "OPENSANDBOX_BASE_URL",
        "OPENSANDBOX_API_KEY",
    }
)
EXPECTED_PROJECT_MEMBERSHIP = {
    *(f"{name}|{service}" for service, name in transition.CONTAINERS.items()),
    f"{transition.TARGET_BROKER_CONTAINER}|opensandbox-egress-proxy",
}
DIRECT_RELEASE_IDENTITIES = {
    "api": (transition.CONTAINERS["api"], "api"),
    "worker": (transition.CONTAINERS["worker"], "worker"),
    "frontend": (transition.CONTAINERS["frontend"], "frontend"),
    "opensandbox-egress-proxy": (
        transition.TARGET_BROKER_CONTAINER,
        "opensandbox-egress-proxy",
    ),
}
MAX_CONFIG_BYTES = 1024 * 1024


class BootstrapError(RuntimeError):
    """A bounded production host or application bootstrap failure."""


@dataclass(frozen=True)
class OpenSandboxHostConfig:
    server_image: str
    server_image_digest: str
    execd_image: str
    egress_image: str
    server_uid: int
    server_gid: int
    docker_socket_gid: int
    lifecycle_address: str
    api_key_sha256: str
    config_sha256: str


@dataclass(frozen=True)
class CurrentRuntime:
    repo_root: Path
    commit: str


class Runner:
    def __init__(self, environment: Mapping[str, str] | None = None) -> None:
        source = os.environ if environment is None else environment
        allowed = (
            "PATH",
            "LANG",
            "LC_ALL",
            "HOME",
            "DOCKER_CONFIG",
            *sandbox_quickstart.PROXY_ENVIRONMENT,
        )
        self.environment = {key: source[key] for key in allowed if key in source}

    def run(
        self,
        command: Sequence[str],
        *,
        output: bool = False,
        check: bool = True,
        timeout: int = 300,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                list(command),
                env=self.environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if output else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise BootstrapError("host command unavailable") from exc
        if check and result.returncode != 0:
            raise BootstrapError("host command failed")
        return result


def _require_root_posix() -> None:
    if os.name != "posix" or os.geteuid() != 0:
        raise BootstrapError("production bootstrap requires root on a POSIX host")


def _validate_parent_chain(
    path: Path,
    *,
    expected_uid: int,
    label: str,
) -> None:
    if not path.is_absolute():
        raise BootstrapError(f"{label} path is not absolute")
    chain = [path.parent, *path.parent.parents]
    for parent in reversed(chain):
        try:
            metadata = parent.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise BootstrapError(f"{label} parent metadata is unavailable") from exc
        mode = stat.S_IMODE(metadata.st_mode)
        sticky_root_parent = (
            metadata.st_uid == 0
            and bool(metadata.st_mode & stat.S_ISVTX)
            and bool(mode & 0o002)
        )
        if (
            parent.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid not in {0, expected_uid}
            or (bool(mode & 0o022) and not sticky_root_parent)
        ):
            raise BootstrapError(f"{label} parent chain is unsafe")


def _read_secure_text(
    path: Path,
    *,
    expected_uid: int = 0,
    expected_gid: int | None = None,
    mode: int = 0o600,
    label: str,
) -> str:
    descriptor: int | None = None
    try:
        _validate_parent_chain(path, expected_uid=expected_uid, label=label)
        metadata = path.lstat()
        if (
            not path.is_absolute()
            or path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or (expected_gid is not None and metadata.st_gid != expected_gid)
            or stat.S_IMODE(metadata.st_mode) != mode
            or metadata.st_size < 1
            or metadata.st_size > MAX_CONFIG_BYTES
        ):
            raise BootstrapError(f"{label} metadata mismatch")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if opened.st_dev != metadata.st_dev or opened.st_ino != metadata.st_ino:
            raise BootstrapError(f"{label} changed during validation")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, MAX_CONFIG_BYTES + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_CONFIG_BYTES:
                raise BootstrapError(f"{label} is too large")
            chunks.append(chunk)
        payload = b"".join(chunks)
        if len(payload) > MAX_CONFIG_BYTES:
            raise BootstrapError(f"{label} is too large")
        return payload.decode("utf-8")
    except BootstrapError:
        raise
    except (OSError, UnicodeError) as exc:
        raise BootstrapError(f"{label} is unavailable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _parse_server_environment(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, raw_value = line.partition("=")
        value = raw_value.strip()
        if (
            not separator
            or KEY_RE.fullmatch(key) is None
            or key in values
            or not value
            or any(character.isspace() for character in value)
            or any(character in value for character in "'\"")
        ):
            raise BootstrapError("OpenSandbox server environment is invalid")
        values[key] = value
    if set(values) != SERVER_ENV_KEYS or any(
        "REQUIRED" in value for value in values.values()
    ):
        raise BootstrapError("OpenSandbox server environment is incomplete")
    return values


def _positive_identity(value: str, name: str) -> int:
    if not value.isascii() or not value.isdecimal():
        raise BootstrapError(f"{name} is invalid")
    parsed = int(value)
    if parsed < 1 or parsed > 2**31 - 1:
        raise BootstrapError(f"{name} is invalid")
    return parsed


def _private_ipv4(value: str, name: str) -> ipaddress.IPv4Address:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise BootstrapError(f"{name} is invalid") from exc
    if (
        not isinstance(address, ipaddress.IPv4Address)
        or not address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise BootstrapError(f"{name} is invalid")
    return address


def _immutable_image(value: Any, name: str) -> tuple[str, str]:
    if not isinstance(value, str) or (match := IMAGE_RE.fullmatch(value)) is None:
        raise BootstrapError(f"{name} is not an immutable image")
    return value, match.group("digest")


def load_opensandbox_host_config(
    env_file: Path = SERVER_ENV_FILE,
    config_file: Path = SERVER_CONFIG_FILE,
    *,
    docker_socket: Path = DOCKER_SOCKET,
    expected_uid: int = 0,
) -> OpenSandboxHostConfig:
    raw_environment = _read_secure_text(
        env_file,
        expected_uid=expected_uid,
        label="OpenSandbox server environment",
    )
    environment = _parse_server_environment(raw_environment)
    server_image, server_digest = _immutable_image(
        environment["OPENSANDBOX_SERVER_IMAGE"],
        "OpenSandbox server image",
    )
    if environment["OPENSANDBOX_SERVER_IMAGE_DIGEST"] != server_digest:
        raise BootstrapError("OpenSandbox server image digest mismatch")
    server_uid = _positive_identity(
        environment["OPENSANDBOX_SERVER_UID"], "OpenSandbox server UID"
    )
    server_gid = _positive_identity(
        environment["OPENSANDBOX_SERVER_GID"], "OpenSandbox server GID"
    )
    socket_gid = _positive_identity(
        environment["OPENSANDBOX_DOCKER_SOCKET_GID"],
        "OpenSandbox Docker socket GID",
    )
    try:
        socket_metadata = docker_socket.stat(follow_symlinks=False)
    except OSError as exc:
        raise BootstrapError("Docker socket is unavailable") from exc
    if (
        not stat.S_ISSOCK(socket_metadata.st_mode)
        or socket_metadata.st_gid != socket_gid
    ):
        raise BootstrapError("OpenSandbox Docker socket GID mismatch")
    lifecycle = _private_ipv4(
        environment["OPENSANDBOX_LIFECYCLE_LISTEN_ADDRESS"],
        "OpenSandbox lifecycle address",
    )

    raw_config = _read_secure_text(
        config_file,
        expected_uid=expected_uid,
        expected_gid=server_gid,
        mode=0o640,
        label="OpenSandbox server configuration",
    )
    try:
        config = tomllib.loads(raw_config)
    except tomllib.TOMLDecodeError as exc:
        raise BootstrapError("OpenSandbox server configuration is invalid") from exc
    server = config.get("server")
    runtime = config.get("runtime")
    storage = config.get("storage")
    store = config.get("store")
    docker = config.get("docker")
    ingress = config.get("ingress")
    secure_runtime = config.get("secure_runtime")
    egress_policy = config.get("egress")
    expected_sections = {
        "server",
        "log",
        "runtime",
        "storage",
        "store",
        "docker",
        "ingress",
        "egress",
        "secure_runtime",
    }
    if (
        not all(
            isinstance(section, dict)
            for section in (
                server,
                runtime,
                storage,
                store,
                docker,
                ingress,
                egress_policy,
                secure_runtime,
            )
        )
        or set(config) != expected_sections
    ):
        raise BootstrapError("OpenSandbox server configuration is incomplete")
    expected_keys = {
        "server": {"host", "port", "max_sandbox_timeout_seconds", "api_key"},
        "log": {"level"},
        "runtime": {"type", "execd_image"},
        "storage": {"allowed_host_paths", "volume_default_size"},
        "store": {"type", "path"},
        "docker": {
            "network_mode",
            "host_ip",
            "drop_capabilities",
            "no_new_privileges",
            "pids_limit",
            "sandbox_env",
            "sandbox_binds",
            "port_range_min",
            "port_range_max",
            "apparmor_profile",
            "seccomp_profile",
        },
        "ingress": {"mode"},
        "egress": {
            "image",
            "mode",
            "disable_ipv6",
            "readiness_timeout_seconds",
        },
        "secure_runtime": {"type", "docker_runtime"},
    }
    if any(set(config[name]) != keys for name, keys in expected_keys.items()):
        raise BootstrapError(
            "OpenSandbox server configuration violates production policy"
        )
    api_key = server.get("api_key")
    execd_image, _ = _immutable_image(
        runtime.get("execd_image"), "OpenSandbox execd image"
    )
    egress_image, _ = _immutable_image(
        egress_policy.get("image"), "OpenSandbox egress image"
    )
    required_capabilities = {
        "AUDIT_WRITE",
        "MKNOD",
        "NET_ADMIN",
        "NET_RAW",
        "SYS_ADMIN",
        "SYS_MODULE",
        "SYS_PTRACE",
        "SYS_TIME",
        "SYS_TTY_CONFIG",
    }
    drop_capabilities = docker.get("drop_capabilities")
    valid = (
        server.get("host") == str(lifecycle)
        and server.get("port") == 8080
        and isinstance(api_key, str)
        and API_KEY_RE.fullmatch(api_key) is not None
        and "REQUIRED" not in api_key
        and server.get("max_sandbox_timeout_seconds") == 86400
        and config["log"].get("level") == "INFO"
        and runtime.get("type") == "docker"
        and storage.get("allowed_host_paths") == []
        and storage.get("volume_default_size") == "1Gi"
        and store.get("type") == "sqlite"
        and store.get("path") == str(SERVER_STATE_ROOT / "opensandbox.db")
        and docker.get("network_mode")
        == authority.DIRECT_OPENSANDBOX_NETWORK_NAME
        and docker.get("host_ip") == str(lifecycle)
        and docker.get("no_new_privileges") is True
        and isinstance(docker.get("pids_limit"), int)
        and 1 <= docker["pids_limit"] <= 65536
        and docker.get("sandbox_env") == {}
        and docker.get("sandbox_binds") == []
        and docker.get("port_range_min") == 40000
        and docker.get("port_range_max") == 60000
        and docker.get("apparmor_profile") == ""
        and docker.get("seccomp_profile") == ""
        and isinstance(drop_capabilities, list)
        and all(isinstance(value, str) for value in drop_capabilities)
        and len(drop_capabilities) == len(required_capabilities)
        and set(drop_capabilities) == required_capabilities
        and ingress.get("mode") == "direct"
        and egress_policy.get("mode") == "dns+nft"
        and egress_policy.get("disable_ipv6") is True
        and egress_policy.get("readiness_timeout_seconds") == 30.0
        and secure_runtime.get("type") == "gvisor"
        and secure_runtime.get("docker_runtime") == "runsc"
    )
    if not valid:
        raise BootstrapError(
            "OpenSandbox server configuration violates production policy"
        )
    canonical_config = json.dumps(
        {"environment": environment, "configuration": config},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return OpenSandboxHostConfig(
        server_image=server_image,
        server_image_digest=server_digest,
        execd_image=execd_image,
        egress_image=egress_image,
        server_uid=server_uid,
        server_gid=server_gid,
        docker_socket_gid=socket_gid,
        lifecycle_address=str(lifecycle),
        api_key_sha256=hashlib.sha256(api_key.encode("utf-8")).hexdigest(),
        config_sha256=hashlib.sha256(canonical_config.encode("utf-8")).hexdigest(),
    )


def _require_host_address_available(address: str, name: str) -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((address, 0))
    except OSError as exc:
        raise BootstrapError(
            f"OpenSandbox {name} address is not assigned to this host"
        ) from exc
    finally:
        probe.close()


def _require_application_host_contract(
    env_file: Path,
    config: OpenSandboxHostConfig,
    *,
    expected_uid: int = 0,
) -> None:
    text = _read_secure_text(
        env_file,
        expected_uid=expected_uid,
        label="production application environment",
    )
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if key not in APPLICATION_HOST_KEYS:
            continue
        value = raw_value.strip()
        if (
            not separator
            or key in values
            or not value
            or any(character.isspace() for character in value)
            or any(character in value for character in "'\"")
        ):
            raise BootstrapError("production OpenSandbox environment is invalid")
        values[key] = value
    if set(values) != APPLICATION_HOST_KEYS:
        raise BootstrapError("production OpenSandbox environment is incomplete")
    try:
        lifecycle = urlsplit(values["OPENSANDBOX_BASE_URL"])
        valid_lifecycle = (
            lifecycle.scheme == "http"
            and lifecycle.hostname == config.lifecycle_address
            and lifecycle.port == 8080
            and lifecycle.username is None
            and lifecycle.password is None
            and lifecycle.path in {"", "/"}
            and not lifecycle.query
            and not lifecycle.fragment
        )
    except ValueError:
        valid_lifecycle = False
    if (
        not valid_lifecycle
        or hashlib.sha256(values["OPENSANDBOX_API_KEY"].encode("utf-8")).hexdigest()
        != config.api_key_sha256
    ):
        raise BootstrapError(
            "production OpenSandbox host/application contract mismatch"
        )


def _ensure_directory(path: Path, *, uid: int, gid: int, mode: int) -> None:
    created = False
    try:
        _validate_parent_chain(
            path,
            expected_uid=uid,
            label="OpenSandbox host directory",
        )
        if not path.exists() and not path.is_symlink():
            path.mkdir(parents=True, mode=mode)
            created = True
            flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(path, flags)
            try:
                os.fchown(descriptor, uid, gid)
                os.fchmod(descriptor, mode)
            finally:
                os.close(descriptor)
        _validate_parent_chain(
            path,
            expected_uid=uid,
            label="OpenSandbox host directory",
        )
        metadata = path.stat(follow_symlinks=False)
        if (
            path.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != uid
            or metadata.st_gid != gid
            or stat.S_IMODE(metadata.st_mode) != mode
        ):
            raise BootstrapError("OpenSandbox host directory metadata mismatch")
    except BootstrapError:
        raise
    except OSError as exc:
        action = "created" if created else "existing"
        raise BootstrapError(f"OpenSandbox {action} host directory is unsafe") from exc


def _ensure_platform_workspace(path: Path = PLATFORM_WORKSPACE_ROOT) -> None:
    try:
        _ensure_directory(
            path,
            uid=PLATFORM_WORKSPACE_UID,
            gid=PLATFORM_WORKSPACE_GID,
            mode=0o750,
        )
    except BootstrapError as exc:
        raise BootstrapError("production workspace root metadata mismatch") from exc


def _docker_json(result: subprocess.CompletedProcess[str], name: str) -> Any:
    try:
        return json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise BootstrapError(f"{name} returned invalid metadata") from exc


def _require_docker_prerequisites(runner: Runner) -> None:
    runner.run([*DOCKER, "version"], timeout=30)
    runner.run([*DOCKER, "compose", "version"], timeout=30)
    runtimes = _docker_json(
        runner.run(
            [*DOCKER, "info", "--format", "{{json .Runtimes}}"],
            output=True,
            timeout=30,
        ),
        "Docker runtime inventory",
    )
    if not isinstance(runtimes, dict) or "runsc" not in runtimes:
        raise BootstrapError("Docker runsc runtime is unavailable")


def _render_unit(template_path: Path, commit: str, config_sha256: str) -> str:
    if COMMIT_RE.fullmatch(commit) is None:
        raise BootstrapError("OpenSandbox unit source commit is invalid")
    if re.fullmatch(r"[0-9a-f]{64}", config_sha256) is None:
        raise BootstrapError("OpenSandbox unit configuration digest is invalid")
    guard_path = template_path.parents[2] / "tools/opensandbox_unit_guard.py"
    try:
        metadata = template_path.stat(follow_symlinks=False)
        guard_metadata = guard_path.stat(follow_symlinks=False)
        text = template_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise BootstrapError("OpenSandbox systemd template is unavailable") from exc
    if (
        template_path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or guard_path.is_symlink()
        or not stat.S_ISREG(guard_metadata.st_mode)
        or text.count("@@SOURCE_COMMIT@@") != 3
        or text.count("@@HOST_CONFIG_SHA256@@") != 1
        or "ai-platform.release-owner=production-bootstrap" not in text
    ):
        raise BootstrapError("OpenSandbox systemd template is invalid")
    return text.replace("@@SOURCE_COMMIT@@", commit).replace(
        "@@HOST_CONFIG_SHA256@@", config_sha256
    )


def _managed_existing_unit(path: Path) -> bytes | None:
    if not path.exists() and not path.is_symlink():
        return None
    try:
        metadata = path.stat(follow_symlinks=False)
        payload = path.read_bytes()
    except OSError as exc:
        raise BootstrapError("installed OpenSandbox unit is unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o644
        or b"ai-platform.release-owner=production-bootstrap" not in payload
    ):
        raise BootstrapError("installed OpenSandbox unit is not bootstrap-managed")
    _unit_source_commit(payload)
    _unit_config_sha256(payload)
    return payload


def _atomic_write_unit(path: Path, payload: bytes) -> None:
    _validate_parent_chain(
        path,
        expected_uid=os.geteuid(),
        label="OpenSandbox systemd unit",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    _validate_parent_chain(
        path,
        expected_uid=os.geteuid(),
        label="OpenSandbox systemd unit",
    )
    parent_metadata = path.parent.stat(follow_symlinks=False)
    if (
        path.parent.is_symlink()
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or parent_metadata.st_uid != os.geteuid()
        or stat.S_IMODE(parent_metadata.st_mode) & 0o022
    ):
        raise BootstrapError("OpenSandbox systemd directory is unsafe")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".opensandbox.service-", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o644)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short systemd unit write")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _unit_source_commit(payload: bytes) -> str:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BootstrapError("installed OpenSandbox unit source is invalid") from exc
    matches = list(SOURCE_LABEL_RE.finditer(text))
    if len(matches) != 1:
        raise BootstrapError("installed OpenSandbox unit source is invalid")
    commit = matches[0].group("commit")
    helper_commits = [match.group("commit") for match in UNIT_GUARD_RE.finditer(text)]
    if helper_commits != [commit, commit]:
        raise BootstrapError("installed OpenSandbox unit source is invalid")
    return commit


def _unit_config_sha256(payload: bytes) -> str:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BootstrapError(
            "installed OpenSandbox unit configuration is invalid"
        ) from exc
    matches = list(CONFIG_LABEL_RE.finditer(text))
    if len(matches) != 1:
        raise BootstrapError("installed OpenSandbox unit configuration is invalid")
    return matches[0].group("digest")


def _unit_is_equivalent(existing: bytes, rendered: bytes) -> bool:
    try:
        existing_text = existing.decode("utf-8")
        rendered_text = rendered.decode("utf-8")
    except UnicodeDecodeError:
        return False
    normalized_existing, count = SOURCE_LABEL_RE.subn(
        "ai-platform.source-commit=@@SOURCE_COMMIT@@", existing_text
    )
    normalized_rendered, rendered_count = SOURCE_LABEL_RE.subn(
        "ai-platform.source-commit=@@SOURCE_COMMIT@@", rendered_text
    )
    normalized_existing, existing_guard_count = UNIT_GUARD_RE.subn(
        "/data/ai-platform-prod/releases/@@SOURCE_COMMIT@@/"
        "tools/opensandbox_unit_guard.py",
        normalized_existing,
    )
    normalized_rendered, rendered_guard_count = UNIT_GUARD_RE.subn(
        "/data/ai-platform-prod/releases/@@SOURCE_COMMIT@@/"
        "tools/opensandbox_unit_guard.py",
        normalized_rendered,
    )
    return (
        count == 1
        and rendered_count == 1
        and existing_guard_count == 2
        and rendered_guard_count == 2
        and normalized_existing == normalized_rendered
    )


def _converge_unit(
    runner: Runner,
    *,
    changed: bool,
) -> None:
    runner.run([*SYSTEMD, "daemon-reload"], timeout=30)
    runner.run([*SYSTEMD, "enable", "opensandbox.service"], timeout=30)
    active = (
        runner.run(
            [*SYSTEMD, "is-active", "--quiet", "opensandbox.service"],
            check=False,
            timeout=15,
        ).returncode
        == 0
    )
    if changed and active:
        runner.run([*SYSTEMD, "restart", "opensandbox.service"], timeout=150)
    elif not active:
        runner.run([*SYSTEMD, "start", "opensandbox.service"], timeout=150)


def _require_server_container_absent(runner: Runner) -> None:
    runner.run([*DOCKER, "version"], timeout=30)
    inventory = runner.run(
        [*DOCKER, "container", "ls", "-a", "--format", "{{.Names}}"],
        output=True,
        timeout=30,
    )
    if SERVER_CONTAINER in inventory.stdout.splitlines():
        raise BootstrapError("OpenSandbox server container remains present")


def _restore_unit(
    runner: Runner,
    previous: bytes | None,
    *,
    unit_path: Path = SYSTEMD_UNIT,
) -> None:
    if previous is None:
        runner.run(
            [*SYSTEMD, "disable", "--now", "opensandbox.service"],
            timeout=90,
        )
        _require_server_container_absent(runner)
        try:
            unit_path.unlink(missing_ok=True)
        except OSError as exc:
            raise BootstrapError("OpenSandbox unit removal failed") from exc
    else:
        runner.run(
            [*SYSTEMD, "stop", "opensandbox.service"],
            timeout=90,
        )
        _require_server_container_absent(runner)
        _atomic_write_unit(unit_path, previous)
    runner.run([*SYSTEMD, "daemon-reload"], timeout=30)
    if previous is not None:
        runner.run([*SYSTEMD, "start", "opensandbox.service"], timeout=150)


def _validate_server_container(
    runner: Runner,
    config: OpenSandboxHostConfig,
    expected_commit: str,
) -> None:
    if COMMIT_RE.fullmatch(expected_commit) is None:
        raise BootstrapError("OpenSandbox server source expectation is invalid")
    image_payload = _docker_json(
        runner.run(
            [*DOCKER, "image", "inspect", config.server_image],
            output=True,
            timeout=30,
        ),
        "OpenSandbox server image",
    )
    image = (
        image_payload[0]
        if isinstance(image_payload, list)
        and len(image_payload) == 1
        and isinstance(image_payload[0], dict)
        else None
    )
    image_config = image.get("Config") if isinstance(image, dict) else None
    image_id = image.get("Id") if isinstance(image, dict) else None
    expected_entrypoint = (
        image_config.get("Entrypoint") if isinstance(image_config, dict) else object()
    )
    payload = _docker_json(
        runner.run(
            [*DOCKER, "container", "inspect", SERVER_CONTAINER],
            output=True,
            timeout=30,
        ),
        "OpenSandbox server container",
    )
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
    ):
        raise BootstrapError("OpenSandbox server container is invalid")
    container = payload[0]
    container_config = container.get("Config")
    host_config = container.get("HostConfig")
    state = container.get("State")
    labels = (
        container_config.get("Labels") if isinstance(container_config, dict) else None
    )
    mounts = container.get("Mounts")
    mount_records = (
        {
            item.get("Destination"): (
                item.get("Type"),
                item.get("Source"),
                item.get("RW"),
            )
            for item in mounts
            if isinstance(item, dict) and isinstance(item.get("Destination"), str)
        }
        if isinstance(mounts, list)
        else {}
    )
    socket_sources = {str(DOCKER_SOCKET)}
    try:
        socket_sources.add(str(DOCKER_SOCKET.resolve(strict=True)))
    except OSError:
        pass
    valid_mounts = (
        isinstance(mounts, list)
        and len(mounts) == 3
        and len(mount_records) == 3
        and mount_records.get("/var/run/docker.sock")
        in {("bind", source, False) for source in socket_sources}
        and mount_records.get("/etc/opensandbox/config.toml")
        == ("bind", str(SERVER_CONFIG_FILE), False)
        and mount_records.get("/var/lib/ai-platform-opensandbox")
        == ("bind", str(SERVER_STATE_ROOT), True)
    )
    tmpfs = host_config.get("Tmpfs") if isinstance(host_config, dict) else None
    tmp_options = (
        set(tmpfs.get("/tmp", "").split(",")) if isinstance(tmpfs, dict) else set()
    )
    valid_tmpfs = (
        isinstance(tmpfs, dict)
        and set(tmpfs) == {"/tmp"}
        and {"rw", "noexec", "nosuid", "nodev"} <= tmp_options
        and bool({"size=64m", "size=67108864"} & tmp_options)
    )
    if (
        not isinstance(image_id, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None
        or not isinstance(image_config, dict)
        or not isinstance(container_config, dict)
        or container.get("Image") != image_id
        or container_config.get("Image") != config.server_image
        or container_config.get("User") != f"{config.server_uid}:{config.server_gid}"
        or container_config.get("Entrypoint") != expected_entrypoint
        or container_config.get("Cmd") != ["--config", "/etc/opensandbox/config.toml"]
        or not isinstance(labels, dict)
        or labels.get("ai-platform.source-commit") != expected_commit
        or labels.get("ai-platform.host-config-sha256") != config.config_sha256
        or labels.get("ai-platform.release-owner") != "production-bootstrap"
        or labels.get("ai-platform.release-role") != "opensandbox-server"
        or labels.get("ai-platform.security-domain") != "execution-controller"
        or not isinstance(host_config, dict)
        or host_config.get("AutoRemove") is not True
        or host_config.get("Privileged") is not False
        or host_config.get("ReadonlyRootfs") is not True
        or host_config.get("CapAdd") not in (None, [])
        or host_config.get("CapDrop") != ["ALL"]
        or host_config.get("SecurityOpt") != ["no-new-privileges"]
        or host_config.get("PidsLimit") != 512
        or not authority._valid_opensandbox_server_network_topology(container)
        or str(config.docker_socket_gid) not in (host_config.get("GroupAdd") or [])
        or host_config.get("Binds") not in (None, [])
        or host_config.get("PortBindings") not in (None, {})
        or not valid_mounts
        or not valid_tmpfs
        or not isinstance(state, dict)
        or state.get("Running") is not True
    ):
        raise BootstrapError("OpenSandbox server runtime identity mismatch")


def _probe_server_health(address: str) -> None:
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(f"http://{address}:8080/health", timeout=10) as response:
            payload = response.read(65537)
            if response.status != 200 or len(payload) > 65536:
                raise BootstrapError("OpenSandbox server health failed")
    except BootstrapError:
        raise
    except (HTTPException, OSError, TimeoutError, URLError) as exc:
        raise BootstrapError("OpenSandbox server health failed") from exc


class HostBootstrap:
    def __init__(
        self,
        checkout: Path,
        *,
        runner: Runner | None = None,
        health_probe: Callable[[str], None] = _probe_server_health,
        env_file: Path = SERVER_ENV_FILE,
        config_file: Path = SERVER_CONFIG_FILE,
        unit_path: Path = SYSTEMD_UNIT,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        health_timeout: int = 120,
    ) -> None:
        self.checkout = checkout
        self.runner = runner or Runner()
        self.health_probe = health_probe
        self.env_file = env_file
        self.config_file = config_file
        self.unit_path = unit_path
        self.monotonic = monotonic
        self.sleep = sleep
        self.health_timeout = health_timeout
        self._rollback_armed = False
        self._unit_changed = False
        self._previous_unit: bytes | None = None
        self._converged_config: OpenSandboxHostConfig | None = None

    def _wait_ready(
        self,
        config: OpenSandboxHostConfig,
        expected_commit: str,
    ) -> None:
        deadline = self.monotonic() + self.health_timeout
        while True:
            try:
                _validate_server_container(self.runner, config, expected_commit)
                self.health_probe(config.lifecycle_address)
                return
            except BootstrapError:
                if self.monotonic() >= deadline:
                    raise BootstrapError(
                        "OpenSandbox host service did not converge"
                    ) from None
                self.sleep(min(2.0, max(0.0, deadline - self.monotonic())))

    def run(
        self,
        commit: str,
        *,
        require_existing_unit: bool = False,
        application_env_file: Path | None = None,
    ) -> OpenSandboxHostConfig:
        config = load_opensandbox_host_config(self.env_file, self.config_file)
        _require_host_address_available(config.lifecycle_address, "lifecycle")
        if application_env_file is not None:
            _require_application_host_contract(application_env_file, config)
        _require_docker_prerequisites(self.runner)
        try:
            transition._require_network_guard(self.checkout)
        except transition.TransitionError as exc:
            raise BootstrapError("OpenSandbox host-input guard is invalid") from exc
        rendered = _render_unit(
            self.checkout / UNIT_TEMPLATE,
            commit,
            config.config_sha256,
        )
        previous = _managed_existing_unit(self.unit_path)
        if require_existing_unit and previous is None:
            raise BootstrapError(
                "existing production runtime lacks a bootstrap-managed OpenSandbox unit"
            )
        previous_config_sha256 = (
            _unit_config_sha256(previous) if previous is not None else None
        )
        if require_existing_unit and previous_config_sha256 != config.config_sha256:
            raise BootstrapError(
                "existing production update cannot change OpenSandbox host configuration"
            )
        rollback_unit = (
            previous if previous_config_sha256 in {None, config.config_sha256} else None
        )
        _ensure_directory(
            SERVER_STATE_ROOT,
            uid=config.server_uid,
            gid=config.server_gid,
            mode=0o700,
        )
        _ensure_platform_workspace()
        for image in (config.server_image, config.execd_image, config.egress_image):
            self.runner.run([*DOCKER, "pull", image], timeout=900)
            self.runner.run([*DOCKER, "image", "inspect", image], timeout=30)
        payload = rendered.encode("utf-8")
        changed = previous is None or not _unit_is_equivalent(previous, payload)
        expected_service_commit = (
            commit if changed else _unit_source_commit(previous or b"")
        )
        try:
            if changed:
                _atomic_write_unit(self.unit_path, payload)
            _converge_unit(self.runner, changed=changed)
            if (
                self.runner.run(
                    [*SYSTEMD, "is-active", "--quiet", "opensandbox.service"],
                    check=False,
                    timeout=15,
                ).returncode
                != 0
            ):
                raise BootstrapError("OpenSandbox host service is inactive")
            try:
                self._wait_ready(config, expected_service_commit)
            except BootstrapError:
                if changed:
                    raise
                self.runner.run(
                    [*SYSTEMD, "restart", "opensandbox.service"], timeout=150
                )
                self._wait_ready(config, expected_service_commit)
        except BaseException:
            if changed:
                try:
                    _restore_unit(
                        self.runner,
                        rollback_unit,
                        unit_path=self.unit_path,
                    )
                    if rollback_unit is not None:
                        self._wait_ready(
                            config,
                            _unit_source_commit(rollback_unit),
                        )
                except BaseException as restore_error:
                    raise BootstrapError(
                        "OpenSandbox host convergence failed and restore failed"
                    ) from restore_error
            raise
        self._rollback_armed = True
        self._unit_changed = changed
        self._previous_unit = rollback_unit
        self._converged_config = config
        return config

    def rollback(self) -> None:
        if not self._rollback_armed:
            return
        if not self._unit_changed:
            self._rollback_armed = False
            return
        config = self._converged_config
        previous = self._previous_unit
        if config is None:
            raise BootstrapError("OpenSandbox host rollback state is invalid")
        _restore_unit(self.runner, previous, unit_path=self.unit_path)
        if previous is not None:
            if (
                self.runner.run(
                    [*SYSTEMD, "is-active", "--quiet", "opensandbox.service"],
                    check=False,
                    timeout=15,
                ).returncode
                != 0
            ):
                raise BootstrapError("restored OpenSandbox host service is inactive")
            self._wait_ready(config, _unit_source_commit(previous))
        self._rollback_armed = False


def _project_membership(docker: Sequence[str]) -> set[str]:
    try:
        result = transition._run(
            [
                *docker,
                "container",
                "ls",
                "-a",
                "--filter",
                f"label=com.docker.compose.project={authority.COMPOSE_PROJECT}",
                "--format",
                '{{.Names}}|{{.Label "com.docker.compose.service"}}',
            ],
            timeout=30,
        )
    except transition.TransitionError as exc:
        raise BootstrapError("production Compose membership is unavailable") from exc
    return {line for line in result.stdout.splitlines() if line}


def _require_direct_runtime_identity(
    docker: Sequence[str],
    commit: str,
) -> None:
    for service, (name, role) in DIRECT_RELEASE_IDENTITIES.items():
        labels = transition._labels(transition._inspect_container(docker, name))
        if (
            labels.get("com.docker.compose.project") != authority.COMPOSE_PROJECT
            or labels.get("com.docker.compose.service") != service
            or labels.get("ai-platform.source-commit") != commit
            or labels.get("ai-platform.source-dirty") != "false"
            or labels.get("ai-platform.release-owner") != "repo-local-compose"
            or labels.get("ai-platform.release-role") != role
        ):
            raise BootstrapError(
                f"existing production release identity is invalid: {service}"
            )


def _current_runtime(
    root: Path,
    docker: Sequence[str],
    *,
    docker_cmd: str,
) -> CurrentRuntime | None:
    membership = _project_membership(docker)
    if not membership:
        return None
    if membership != EXPECTED_PROJECT_MEMBERSHIP:
        raise BootstrapError(
            "existing Compose project is not the direct production contour"
        )
    try:
        api = transition._inspect_container(docker, transition.CONTAINERS["api"])
        commit = transition._labels(api).get("ai-platform.source-commit", "")
        if COMMIT_RE.fullmatch(commit) is None:
            raise BootstrapError("existing production runtime commit is invalid")
        _require_direct_runtime_identity(docker, commit)
        repo_root = root / "releases" / commit
        authority.assert_managed_target_checkout(repo_root, commit, root / "releases")
        transition._require_target_runtime(
            docker,
            target_repo_root=repo_root,
            target_commit=commit,
            docker_cmd=docker_cmd,
        )
    except BootstrapError:
        raise
    except (authority.ReleaseAuthorityError, transition.TransitionError) as exc:
        raise BootstrapError("existing direct production runtime is invalid") from exc
    return CurrentRuntime(repo_root=repo_root, commit=commit)


def _compose_preflight(
    checkout: Path,
    commit: str,
    env_file: Path,
    docker: Sequence[str],
) -> None:
    selection = authority.resolve_compose_files(checkout, transition.TARGET_SELECTION)
    with transition._acceptance_fence(), transition._workspace_root_environment():
        authority._semantic_compose_config_preflight(
            docker,
            selection,
            env_file,
            commit=commit,
        )


def _deploy_checkout(
    checkout: Path,
    commit: str,
    env_file: Path,
    docker: Sequence[str],
    *,
    docker_cmd: str,
) -> None:
    with transition._acceptance_fence(), transition._workspace_root_environment():
        authority.deploy_clean_commit(
            checkout,
            commit,
            docker_cmd=docker_cmd,
            env_file=env_file,
            compose_files=transition.TARGET_SELECTION,
            strategy="canonical",
            replace_known_manual_frontend=False,
        )
        transition._require_target_runtime(
            docker,
            target_repo_root=checkout,
            target_commit=commit,
            docker_cmd=docker_cmd,
        )


def _stop_available_admission(docker: Sequence[str]) -> None:
    try:
        inventory = transition._run(
            [*docker, "container", "ls", "-a", "--format", "{{.Names}}|{{.State}}"],
            timeout=30,
        )
        states: dict[str, str] = {}
        for raw_line in inventory.stdout.splitlines():
            name, separator, state = raw_line.partition("|")
            if not separator or not name or not state or name in states:
                raise BootstrapError("Docker admission inventory is invalid")
            states[name] = state
        for name in transition.ADMISSION_CONTAINERS:
            state = states.get(name)
            if state in {"running", "paused", "restarting"}:
                transition._run([*docker, "stop", name], timeout=90)
        running = transition._run(
            [*docker, "container", "ls", "--format", "{{.Names}}"],
            timeout=30,
        )
        if set(running.stdout.splitlines()) & set(transition.ADMISSION_CONTAINERS):
            raise BootstrapError("production admission remains active")
    except BootstrapError:
        raise
    except transition.TransitionError as exc:
        raise BootstrapError("failed to fence production admission") from exc


def _cleanup_failed_cold_runtime(
    checkout: Path,
    subject: sandbox_quickstart.Subject,
    env_file: Path,
    docker: Sequence[str],
) -> None:
    try:
        executor_image, executor_digest = _immutable_image(
            subject.backend_image,
            "cold production sandbox image",
        )
        selection = authority.resolve_compose_files(
            checkout, transition.TARGET_SELECTION
        )
        command = transition._compose_command(
            docker,
            project=authority.COMPOSE_PROJECT,
            env_file=env_file,
            compose_files=selection.absolute_paths,
            environment=(
                f"AI_PLATFORM_IMAGE={subject.backend_image}",
                f"AI_PLATFORM_FRONTEND_IMAGE={subject.frontend_image}",
                f"SANDBOX_EXECUTOR_IMAGE={executor_image}",
                f"OPENSANDBOX_EXECUTOR_IMAGE={executor_image}",
                f"OPENSANDBOX_EXECUTOR_IMAGE_DIGEST={executor_digest}",
                f"AI_PLATFORM_SOURCE_COMMIT={subject.commit}",
                f"AI_PLATFORM_BUILD_COMMIT={subject.commit}",
                "AI_PLATFORM_BUILD_DIRTY=false",
            ),
        )
        transition._run([*command, "down", "--remove-orphans"], timeout=300)
        if _project_membership(docker):
            raise BootstrapError("failed cold production project remains present")
    except BootstrapError:
        raise
    except (authority.ReleaseAuthorityError, transition.TransitionError) as exc:
        raise BootstrapError("failed cold production cleanup did not converge") from exc


def deploy_production_subject(
    checkout: Path,
    *,
    root: Path = MANAGED_ROOT,
    docker_cmd: str = "docker --context default",
    host_bootstrap_factory: Callable[[Path], HostBootstrap] = HostBootstrap,
) -> sandbox_quickstart.Subject:
    _require_root_posix()
    subject_path = root / "incoming" / "latest-main.json"
    try:
        subject = sandbox_quickstart._load_subject(subject_path, root)
        if subject.env_file is None:
            raise BootstrapError(
                "production subject is missing the managed environment"
            )
        env_file = transition._require_safe_env_file(subject.env_file)
        transition._require_workspace_root_env(env_file)
        authority.assert_managed_target_checkout(
            checkout,
            subject.commit,
            root / "releases",
        )
        docker = transition._docker_base(docker_cmd)
        current = _current_runtime(root, docker, docker_cmd=docker_cmd)
        if current is not None:
            transition._require_quiescent(docker)
            if current.commit != subject.commit:
                transition._require_schema_compatibility(
                    checkout,
                    current.commit,
                    subject.commit,
                )
        host_bootstrap = host_bootstrap_factory(checkout)
        host_bootstrap.run(
            subject.commit,
            require_existing_unit=current is not None,
            application_env_file=env_file,
        )
        try:
            authority.prepare_packaged_release_images(
                subject.commit,
                backend_image=subject.backend_image,
                frontend_image=subject.frontend_image,
                docker_cmd=docker_cmd,
            )
            _compose_preflight(checkout, subject.commit, env_file, docker)
        except BaseException:
            if current is not None:
                try:
                    host_bootstrap.rollback()
                except BaseException as host_rollback_error:
                    raise BootstrapError(
                        "production preflight failed and OpenSandbox host restore failed"
                    ) from host_rollback_error
            raise
        if current is not None and current.commit == subject.commit:
            print("production: already converged")
            return subject
        if current is not None:
            try:
                transition._stop_admission(docker)
                try:
                    transition._require_quiescent(docker)
                except BaseException:
                    transition._restore_admission(docker)
                    raise
            except BaseException:
                try:
                    host_bootstrap.rollback()
                except BaseException as host_rollback_error:
                    raise BootstrapError(
                        "production stop failed and OpenSandbox host restore failed"
                    ) from host_rollback_error
                raise
        try:
            _deploy_checkout(
                checkout,
                subject.commit,
                env_file,
                docker,
                docker_cmd=docker_cmd,
            )
        except BaseException as target_error:
            if current is None:
                try:
                    _stop_available_admission(docker)
                    _cleanup_failed_cold_runtime(
                        checkout,
                        subject,
                        env_file,
                        docker,
                    )
                except BaseException as cleanup_error:
                    raise BootstrapError(
                        "first production start failed and cleanup did not converge"
                    ) from cleanup_error
                raise BootstrapError(
                    "first production start failed; admission was removed and data volumes were preserved"
                ) from target_error
            try:
                _stop_available_admission(docker)
                transition._require_quiescent(docker)
            except BaseException as rollback_fence_error:
                raise BootstrapError(
                    "target deployment failed and rollback safety could not be proven"
                ) from rollback_fence_error
            try:
                host_bootstrap.rollback()
                _deploy_checkout(
                    current.repo_root,
                    current.commit,
                    env_file,
                    docker,
                    docker_cmd=docker_cmd,
                )
            except BaseException as rollback_error:
                raise BootstrapError(
                    "target deployment and previous-runtime restore both failed"
                ) from rollback_error
            raise BootstrapError(
                "target deployment failed; previous production runtime was restored"
            ) from target_error
    except BootstrapError:
        raise
    except (
        authority.ReleaseAuthorityError,
        transition.TransitionError,
        sandbox_quickstart.QuickstartError,
    ) as exc:
        raise BootstrapError("production admission failed") from exc
    print("production: deployment smoke and parity passed")
    print("production: application-owned OpenSandbox acceptance is pending")
    return subject


def _retry_approved_subject(root: Path) -> sandbox_quickstart.Subject:
    subject = sandbox_quickstart._load_subject(
        root / "incoming" / "latest-main.json", root
    )
    checkout = root / "releases" / subject.commit
    sandbox_quickstart.Quickstart(checkout, root)._verify_source(subject)
    return deploy_production_subject(checkout, root=root)


def _interrupt(*_args: object) -> None:
    raise KeyboardInterrupt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bootstrap or update production from fully approved images."
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="wait for exact-main Actions evidence, resolve image digests, and deploy",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="first-deployment root-owned 0600 env path under the managed config root",
    )
    parser.add_argument(
        "--ci-timeout-seconds",
        type=int,
        default=latest.DEFAULT_CI_TIMEOUT_SECONDS,
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.latest and (
        args.env_file is not None
        or args.ci_timeout_seconds != latest.DEFAULT_CI_TIMEOUT_SECONDS
    ):
        print(
            "production bootstrap: failed: --env-file and CI timeout require --latest"
        )
        return 2
    previous_handlers = {
        signum: signal.signal(signum, _interrupt)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        _require_root_posix()
        with latest.deployment_lock(MANAGED_ROOT):
            if args.latest:
                selected_env = args.env_file
                if selected_env is None and os.environ.get(latest.ENV_PATH_VARIABLE):
                    selected_env = Path(os.environ.pop(latest.ENV_PATH_VARIABLE))
                latest._drop_github_tokens(os.environ)
                client = latest.GitHubClient()
                latest.deploy_latest_main(
                    root=MANAGED_ROOT,
                    client=client,
                    env_file=selected_env,
                    ci_timeout_seconds=args.ci_timeout_seconds,
                    deploy=lambda checkout: deploy_production_subject(
                        checkout,
                        root=MANAGED_ROOT,
                    ),
                )
            else:
                for key in latest.TOKEN_VARIABLES:
                    os.environ.pop(key, None)
                _retry_approved_subject(MANAGED_ROOT)
    except (
        BootstrapError,
        latest.LatestMainError,
        authority.ReleaseAuthorityError,
        transition.TransitionError,
        sandbox_quickstart.QuickstartError,
    ) as exc:
        print(f"production bootstrap: failed: {exc} (no data volumes were removed)")
        return 2
    except (OSError, subprocess.SubprocessError, KeyboardInterrupt):
        print(
            "production bootstrap: failed: command error (no data volumes were removed)"
        )
        return 2
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
