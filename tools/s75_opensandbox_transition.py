from __future__ import annotations

import argparse
import ipaddress
import json
import os
import shlex
import stat
import subprocess
import sys
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Sequence
from urllib.parse import urlsplit

import tools.release_authority as authority

try:
    import fcntl
except ImportError:  # pragma: no cover - transition runs only on Linux
    fcntl = None  # type: ignore[assignment]

LEGACY_PROJECT = authority.COMPOSE_PROJECT
LEGACY_SELECTION = (
    authority.DEFAULT_COMPOSE_RELATIVE_PATH.as_posix(),
    authority.SANDBOX_COMPOSE_RELATIVE_PATH,
)
TARGET_SELECTION = authority.DIRECT_OPENSANDBOX_SELECTION
CONTAINERS = {
    "postgres": "ai-platform-postgres",
    "redis": "ai-platform-redis",
    "minio": "ai-platform-minio",
    "workspace-init": "ai-platform-workspace-init",
    "migrate": "ai-platform-migrate",
    "api": "ai-platform-api",
    "worker": "ai-platform-worker",
    "frontend": "ai-platform-frontend",
}
EXPECTED_VOLUMES = {
    "ai_platform_postgres": (
        "postgres",
        "/var/lib/postgresql/data",
        "ai-platform-internal_ai_platform_postgres",
    ),
    "ai_platform_redis": (
        "redis",
        "/data",
        "ai-platform-internal_ai_platform_redis",
    ),
    "ai_platform_minio": (
        "minio",
        "/data",
        "ai-platform-internal_ai_platform_minio",
    ),
    "ai_platform_sandbox_workspaces": (
        "worker",
        "/tmp/ai-platform-sandbox-workspaces",
        "ai-platform-internal_ai_platform_sandbox_workspaces",
    ),
}
EXPECTED_VOLUME_CONSUMERS = {
    "ai_platform_postgres": {"ai-platform-postgres"},
    "ai_platform_redis": {"ai-platform-redis"},
    "ai_platform_minio": {"ai-platform-minio"},
    "ai_platform_sandbox_workspaces": {"ai-platform-api", "ai-platform-worker"},
}
S75_WORKSPACE_ROOT = "/data/ai-platform-prod/runtime-workspaces"
LEGACY_RUNTIME_RELEASE_ROOT = PurePosixPath("/data/ai-platform-prod/releases")
EXPECTED_WORKSPACE_BINDS = {
    "workspace-init": ("/runtime-workspaces", S75_WORKSPACE_ROOT),
    "api": (S75_WORKSPACE_ROOT, S75_WORKSPACE_ROOT),
    "worker": (S75_WORKSPACE_ROOT, S75_WORKSPACE_ROOT),
}
TARGET_BROKER_CONTAINER = "ai-platform-opensandbox-egress-proxy"
SCHEMA_PATHS = ("app/schema.sql", "app/schema_migrations.py")
ADMISSION_CONTAINERS = ("ai-platform-frontend", "ai-platform-api", "ai-platform-worker")
ACCEPTANCE_PORT_ENVIRONMENT = {
    "AI_PLATFORM_API_PORT": "127.0.0.1:8020",
    "AI_PLATFORM_FRONTEND_PORT": "127.0.0.1:18001",
}
TERMINAL_RUN_STATUSES = ("succeeded", "failed", "cancelled")
TERMINAL_ATTEMPT_STATUSES = ("succeeded", "failed", "cancelled")
LOCK_PATH = Path("/run/lock/ai-platform-s75-opensandbox-transition.lock")
OPENSANDBOX_SERVER_CONFIG_PATH = Path("/etc/ai-platform/opensandbox/server.toml")
OPENSANDBOX_NETWORK_GUARD_SERVICE = "ai-platform-opensandbox-network-guard.service"
OPENSANDBOX_NETWORK_GUARD_CHAIN = "AI_PLATFORM_OPENSANDBOX"
OPENSANDBOX_FORWARD_GUARD_CHAIN = "AI_PLATFORM_OSB_FORWARD"
OPENSANDBOX_NETWORK_GUARD_SOURCE = Path(
    "deploy/opensandbox/ai-platform-opensandbox-network-guard.service"
)
OPENSANDBOX_NETWORK_GUARD_UNIT_PATH = Path(
    "/etc/systemd/system/ai-platform-opensandbox-network-guard.service"
)


class TransitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class LegacyRuntime:
    repo_root: Path
    compose_files: tuple[Path, ...]
    commit: str
    backend_image: str
    frontend_image: str
    executor_image: str


def _run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    timeout = authority.bounded_parity_attempt_timeout(timeout)
    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TransitionError("transition command unavailable") from exc
    if check and result.returncode != 0:
        raise TransitionError("transition command failed")
    return result


def _docker_base(docker_cmd: str) -> list[str]:
    docker = shlex.split(docker_cmd, posix=os.name != "nt")
    if not docker:
        raise TransitionError("docker command is empty")
    return docker


@contextmanager
def _transition_lock() -> Iterator[None]:
    if fcntl is None:
        raise TransitionError("transition lock requires POSIX flock")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(LOCK_PATH, flags, 0o600)
    except OSError as exc:
        raise TransitionError("transition lock unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise TransitionError("transition lock metadata mismatch")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise TransitionError("another s75 transition is active") from exc
        yield
    finally:
        os.close(descriptor)


def _docker_json(docker: Sequence[str], *args: str) -> Any:
    try:
        return json.loads(_run([*docker, *args]).stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise TransitionError("invalid Docker inspection result") from exc


def _inspect_container(docker: Sequence[str], name: str) -> dict[str, Any]:
    payload = _docker_json(docker, "container", "inspect", name)
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise TransitionError(f"invalid managed container: {name}")
    return payload[0]


def _labels(container: dict[str, Any]) -> dict[str, str]:
    labels = container.get("Config", {}).get("Labels")
    if not isinstance(labels, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in labels.items()):
        raise TransitionError("invalid managed container labels")
    return labels


def _container_image(container: dict[str, Any]) -> str:
    image = container.get("Config", {}).get("Image")
    if not isinstance(image, str) or not image.strip():
        raise TransitionError("invalid managed container image")
    return image.strip()


def _container_environment(container: dict[str, Any]) -> dict[str, str]:
    values = container.get("Config", {}).get("Env")
    if not isinstance(values, list):
        raise TransitionError("invalid managed container environment")
    result: dict[str, str] = {}
    for item in values:
        if isinstance(item, str) and "=" in item:
            key, value = item.split("=", 1)
            result[key] = value
    return result


def _mount_source(
    container: dict[str, Any],
    destination: str,
    *,
    mount_type: str = "volume",
) -> str:
    mounts = container.get("Mounts")
    if not isinstance(mounts, list):
        raise TransitionError("invalid managed container mounts")
    matches = [
        mount
        for mount in mounts
        if isinstance(mount, dict)
        and mount.get("Destination") == destination
        and mount.get("Type") == mount_type
    ]
    if len(matches) != 1:
        raise TransitionError(f"managed {mount_type} mount mismatch: {destination}")
    source = matches[0].get("Name" if mount_type == "volume" else "Source")
    if not isinstance(source, str) or not source:
        raise TransitionError(f"managed {mount_type} identity missing: {destination}")
    return source


def _require_exact_legacy_inventory(docker: Sequence[str]) -> None:
    result = _run(
        [
            *docker,
            "container",
            "ls",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={LEGACY_PROJECT}",
            "--format",
            '{{.Names}}|{{.Label "com.docker.compose.service"}}',
        ],
        timeout=30,
    )
    expected = {f"{name}|{service}" for service, name in CONTAINERS.items()}
    if set(result.stdout.splitlines()) != expected:
        raise TransitionError("legacy Compose project membership mismatch")


def _require_volume_identities(
    docker: Sequence[str],
    containers: dict[str, dict[str, Any]],
) -> None:
    for logical, (service, destination, expected_name) in EXPECTED_VOLUMES.items():
        if _mount_source(containers[service], destination) != expected_name:
            raise TransitionError(f"legacy volume identity mismatch: {logical}")
        volume = _docker_json(docker, "volume", "inspect", expected_name)
        if not isinstance(volume, list) or len(volume) != 1 or not isinstance(volume[0], dict):
            raise TransitionError(f"legacy volume unavailable: {logical}")
        volume_labels = volume[0].get("Labels")
        if not isinstance(volume_labels, dict) or (
            volume_labels.get("com.docker.compose.project") != LEGACY_PROJECT
            or volume_labels.get("com.docker.compose.volume") != logical
        ):
            raise TransitionError(f"legacy volume ownership mismatch: {logical}")
        consumers = _run(
            [*docker, "container", "ls", "-a", "--filter", f"volume={expected_name}", "--format", "{{.Names}}"],
            timeout=30,
        )
        if set(consumers.stdout.splitlines()) != EXPECTED_VOLUME_CONSUMERS[logical]:
            raise TransitionError(f"legacy volume consumer mismatch: {logical}")
    workspace_name = EXPECTED_VOLUMES["ai_platform_sandbox_workspaces"][2]
    if _mount_source(containers["api"], "/tmp/ai-platform-sandbox-workspaces") != workspace_name:
        raise TransitionError("legacy workspace volume mismatch: api")
    for service, (destination, expected_source) in EXPECTED_WORKSPACE_BINDS.items():
        if _mount_source(containers[service], destination, mount_type="bind") != expected_source:
            raise TransitionError(f"managed workspace bind mismatch: {service}")
    for service in ("api", "worker"):
        if _container_environment(containers[service]).get("SANDBOX_WORKSPACE_ROOT") != S75_WORKSPACE_ROOT:
            raise TransitionError(f"managed workspace root mismatch: {service}")


def _assert_root_owned_checkout(repo_root: Path, commit: str) -> str:
    normalized = authority.assert_managed_target_checkout(
        repo_root,
        commit,
        repo_root.parent,
    )
    try:
        owner = repo_root.stat(follow_symlinks=False).st_uid
    except OSError as exc:
        raise TransitionError("legacy rollback checkout metadata unavailable") from exc
    if owner != 0:
        raise TransitionError("legacy rollback checkout must be root-owned")
    return normalized


def _legacy_runtime(
    docker: Sequence[str],
    legacy_repo_root: Path,
    legacy_commit: str,
) -> LegacyRuntime:
    normalized = _assert_root_owned_checkout(legacy_repo_root, legacy_commit)
    selection = authority.resolve_compose_files(legacy_repo_root, LEGACY_SELECTION)
    _require_exact_legacy_inventory(docker)
    containers = {service: _inspect_container(docker, name) for service, name in CONTAINERS.items()}
    historical_root = LEGACY_RUNTIME_RELEASE_ROOT / normalized
    accepted_locations = {
        (
            ",".join(str(historical_root / path) for path in LEGACY_SELECTION),
            str((historical_root / LEGACY_SELECTION[0]).parent),
        ),
        (
            ",".join(str(path) for path in selection.absolute_paths),
            str(selection.working_dir),
        ),
    }
    runtime_location = None
    for service, container in containers.items():
        labels = _labels(container)
        location = (
            labels.get("com.docker.compose.project.config_files"),
            labels.get("com.docker.compose.project.working_dir"),
        )
        if (
            labels.get("com.docker.compose.project") != LEGACY_PROJECT
            or labels.get("com.docker.compose.service") != service
            or location not in accepted_locations
            or (runtime_location is not None and location != runtime_location)
        ):
            raise TransitionError(f"legacy Compose ownership mismatch: {service}")
        runtime_location = location
    for service in ("api", "worker", "frontend"):
        labels = _labels(containers[service])
        if labels.get("ai-platform.source-commit") != normalized or labels.get("ai-platform.source-dirty") != "false":
            raise TransitionError(f"legacy release provenance mismatch: {service}")
    if _container_image(containers["api"]) != _container_image(containers["worker"]):
        raise TransitionError("legacy backend image mismatch")
    _require_volume_identities(docker, containers)
    executor_image = _container_environment(containers["api"]).get("SANDBOX_EXECUTOR_IMAGE", "").strip()
    if not executor_image:
        raise TransitionError("legacy executor image missing")
    return LegacyRuntime(
        repo_root=legacy_repo_root.resolve(),
        compose_files=selection.absolute_paths,
        commit=normalized,
        backend_image=_container_image(containers["api"]),
        frontend_image=_container_image(containers["frontend"]),
        executor_image=executor_image,
    )


def _require_safe_env_file(path: Path) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise TransitionError("managed environment file unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise TransitionError("managed environment file metadata mismatch")
    return path.resolve(strict=True)


def _require_workspace_root_env(path: Path) -> None:
    inherited = os.environ.get("SANDBOX_WORKSPACE_ROOT")
    if inherited is not None and inherited != S75_WORKSPACE_ROOT:
        raise TransitionError("managed workspace root configuration mismatch")
    try:
        if path.stat().st_size > 1024 * 1024:
            raise TransitionError("managed environment file too large")
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise TransitionError("managed environment file unreadable") from exc
    values = [
        value.strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
        for key, separator, value in (line.partition("="),)
        if separator and key.strip() == "SANDBOX_WORKSPACE_ROOT"
    ]
    if values != [S75_WORKSPACE_ROOT]:
        raise TransitionError("managed workspace root configuration mismatch")


def _require_schema_compatibility(repo_root: Path, legacy_commit: str, target_commit: str) -> None:
    for path in SCHEMA_PATHS:
        legacy = _run(["git", "-C", str(repo_root), "rev-parse", f"{legacy_commit}:{path}"], timeout=30)
        target = _run(["git", "-C", str(repo_root), "rev-parse", f"{target_commit}:{path}"], timeout=30)
        if legacy.stdout.strip() != target.stdout.strip():
            raise TransitionError("target schema is not legacy-rollback compatible")


def _require_opensandbox_server_profile(
    config_path: Path = OPENSANDBOX_SERVER_CONFIG_PATH,
) -> None:
    try:
        metadata = config_path.lstat()
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        server = config["server"]
        runtime = config["runtime"]
        docker = config["docker"]
        secure_runtime = config["secure_runtime"]
        address = ipaddress.ip_address(server.get("host"))
    except (
        KeyError,
        OSError,
        TypeError,
        UnicodeError,
        ValueError,
        tomllib.TOMLDecodeError,
    ) as exc:
        raise TransitionError("OpenSandbox Server configuration is invalid") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or config_path.is_symlink()
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o640
    ):
        raise TransitionError("OpenSandbox Server configuration is invalid")
    if (
        not isinstance(address, ipaddress.IPv4Address)
        or not address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or server.get("port") != 8080
        or runtime.get("type") != "docker"
        or docker.get("host_ip") != str(address)
        or docker.get("network_mode") != authority.DIRECT_OPENSANDBOX_NETWORK_NAME
        or docker.get("no_new_privileges") is not True
        or secure_runtime.get("type") != "gvisor"
        or secure_runtime.get("docker_runtime") != "runsc"
    ):
        raise TransitionError("OpenSandbox Server isolation profile is invalid")


def _require_network_guard(
    repo_root: Path,
    unit_path: Path = OPENSANDBOX_NETWORK_GUARD_UNIT_PATH,
) -> None:
    source_path = repo_root / OPENSANDBOX_NETWORK_GUARD_SOURCE
    try:
        metadata = unit_path.lstat()
        unit_matches = unit_path.read_bytes() == source_path.read_bytes()
    except OSError as exc:
        raise TransitionError("OpenSandbox host-input guard unit is invalid") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or unit_path.is_symlink()
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not unit_matches
    ):
        raise TransitionError("OpenSandbox host-input guard unit is invalid")
    lines = [
        line.strip()
        for line in _run(["iptables-save", "-t", "filter"]).stdout.splitlines()
    ]
    input_rules = [line for line in lines if line.startswith("-A INPUT ")]
    chain_rules = [
        line
        for line in lines
        if line.startswith(f"-A {OPENSANDBOX_NETWORK_GUARD_CHAIN} ")
    ]
    docker_user_rules = [
        line for line in lines if line.startswith("-A DOCKER-USER ")
    ]
    forward_chain_rules = [
        line
        for line in lines
        if line.startswith(f"-A {OPENSANDBOX_FORWARD_GUARD_CHAIN} ")
    ]
    expected_jump = (
        f"-A INPUT -i {authority.DIRECT_OPENSANDBOX_BRIDGE_NAME} "
        f"-j {OPENSANDBOX_NETWORK_GUARD_CHAIN}"
    )
    expected_forward_jump = (
        f"-A DOCKER-USER -i {authority.DIRECT_OPENSANDBOX_BRIDGE_NAME} "
        f"-o {authority.DIRECT_OPENSANDBOX_BRIDGE_NAME} "
        f"-j {OPENSANDBOX_FORWARD_GUARD_CHAIN}"
    )
    expected_forward_rules = [
        f"-A {OPENSANDBOX_FORWARD_GUARD_CHAIN} "
        f"-d {authority.DIRECT_OPENSANDBOX_PROXY_IPV4}/32 -p tcp -m tcp "
        f"--dport {authority.DIRECT_OPENSANDBOX_PROXY_PORT} -m conntrack "
        "--ctstate NEW,ESTABLISHED -j ACCEPT",
        f"-A {OPENSANDBOX_FORWARD_GUARD_CHAIN} "
        f"-s {authority.DIRECT_OPENSANDBOX_PROXY_IPV4}/32 -p tcp -m tcp "
        f"--sport {authority.DIRECT_OPENSANDBOX_PROXY_PORT} -m conntrack "
        "--ctstate ESTABLISHED -j ACCEPT",
        f"-A {OPENSANDBOX_FORWARD_GUARD_CHAIN} -j DROP",
    ]
    if (
        not input_rules
        or input_rules[0] != expected_jump
        or chain_rules
        != [
            f"-A {OPENSANDBOX_NETWORK_GUARD_CHAIN} -m conntrack "
            "--ctstate RELATED,ESTABLISHED -j ACCEPT",
            f"-A {OPENSANDBOX_NETWORK_GUARD_CHAIN} -j DROP",
        ]
        or not docker_user_rules
        or docker_user_rules[0] != expected_forward_jump
        or forward_chain_rules != expected_forward_rules
    ):
        raise TransitionError("OpenSandbox network guard is invalid")


def _require_opensandbox_server_container(docker: Sequence[str]) -> None:
    payload = _docker_json(docker, "container", "inspect", "ai-platform-opensandbox-server")
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not authority._valid_opensandbox_server_network_topology(payload[0])
    ):
        raise TransitionError("OpenSandbox Server container topology is invalid")


def _require_host_prerequisites(repo_root: Path, docker: Sequence[str]) -> None:
    for service in ("opensandbox.service", OPENSANDBOX_NETWORK_GUARD_SERVICE):
        if (
            _run(
                ["systemctl", "is-active", "--quiet", service],
                check=False,
                timeout=15,
            ).returncode
            != 0
        ):
            raise TransitionError(f"host prerequisite inactive: {service}")
    _require_opensandbox_server_profile()
    _require_opensandbox_server_container(docker)
    _require_network_guard(repo_root)


def _quiescence_counts(docker: Sequence[str]) -> tuple[int, int, int]:
    terminals = ",".join(f"'{value}'" for value in TERMINAL_RUN_STATUSES)
    attempt_terminals = ",".join(f"'{value}'" for value in TERMINAL_ATTEMPT_STATUSES)
    sql = (
        "select "
        f"(select count(*) from runs where status not in ({terminals})),"
        f"(select count(*) from run_attempts where status not in ({attempt_terminals})),"
        "(select count(*) from sandbox_leases where status <> 'released');"
    )
    result = _run(
        [
            *docker,
            "exec",
            "ai-platform-postgres",
            "/bin/sh",
            "-ceu",
            'psql -v ON_ERROR_STOP=1 -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"',
            "sh",
            sql,
        ],
        timeout=30,
    )
    try:
        counts = tuple(int(value) for value in result.stdout.strip().split("|"))
    except ValueError as exc:
        raise TransitionError("invalid database quiescence result") from exc
    if len(counts) != 3:
        raise TransitionError("invalid database quiescence result")
    return counts  # type: ignore[return-value]


def _require_quiescent(docker: Sequence[str]) -> None:
    if _quiescence_counts(docker) != (0, 0, 0):
        raise TransitionError("active run, attempt, or sandbox lease blocks transition")
    for owner in ("sandbox-runtime", "sandbox-native-tool"):
        result = _run(
            [*docker, "container", "ls", "-aq", "--filter", f"label=ai-platform.owner={owner}"],
            timeout=15,
        )
        if result.stdout.strip():
            raise TransitionError("sandbox container blocks transition")


def _compose_command(
    docker: Sequence[str],
    *,
    project: str,
    env_file: Path,
    compose_files: Sequence[Path],
    environment: Sequence[str] = (),
) -> list[str]:
    root_key = "SANDBOX_WORKSPACE_ROOT="
    pinned_environment = [value for value in environment if not value.startswith(root_key)]
    pinned_environment.append(f"{root_key}{S75_WORKSPACE_ROOT}")
    docker_with_env = authority._compose_command_with_environment(docker, pinned_environment)
    file_args = [argument for path in compose_files for argument in ("-f", str(path))]
    return [
        *docker_with_env,
        "compose",
        "-p",
        project,
        "--env-file",
        str(env_file),
        *file_args,
    ]


def _legacy_release_environment(runtime: LegacyRuntime) -> list[str]:
    return [
        f"AI_PLATFORM_IMAGE={runtime.backend_image}",
        f"AI_PLATFORM_FRONTEND_IMAGE={runtime.frontend_image}",
        f"SANDBOX_EXECUTOR_IMAGE={runtime.executor_image}",
        f"AI_PLATFORM_SOURCE_COMMIT={runtime.commit}",
        f"AI_PLATFORM_BUILD_COMMIT={runtime.commit}",
        "AI_PLATFORM_BUILD_DIRTY=false",
    ]


def _target_broker_parity(docker: Sequence[str], commit: str) -> dict[str, bool]:
    broker = _inspect_container(docker, TARGET_BROKER_CONTAINER)
    labels = _labels(broker)
    state = broker.get("State")
    health = state.get("Health") if isinstance(state, dict) else None
    if (
        labels.get("com.docker.compose.project") != authority.COMPOSE_PROJECT
        or labels.get("com.docker.compose.service") != "opensandbox-egress-proxy"
        or labels.get("ai-platform.source-commit") != commit
        or not isinstance(state, dict)
        or state.get("Running") is not True
        or not isinstance(health, dict)
        or health.get("Status") not in {"starting", "healthy"}
    ):
        raise TransitionError("target broker runtime is invalid")
    return {"verified": health["Status"] == "healthy"}


def _require_target_executor(docker: Sequence[str]) -> None:
    bindings: set[tuple[str, str, str]] = set()
    backend_image_ids: set[str] = set()
    for service in ("api", "worker"):
        container = _inspect_container(docker, CONTAINERS[service])
        environment = _container_environment(container)
        bindings.add(
            (
                environment.get("SANDBOX_EXECUTOR_IMAGE", "").strip(),
                environment.get("OPENSANDBOX_EXECUTOR_IMAGE", "").strip(),
                environment.get("OPENSANDBOX_EXECUTOR_IMAGE_DIGEST", "").strip(),
            )
        )
        backend_image_ids.add(str(container.get("Image") or "").strip())
    if len(bindings) != 1 or len(backend_image_ids) != 1:
        raise TransitionError("target OpenSandbox executor image mismatch")
    executor_image, opensandbox_image, executor_digest = bindings.pop()
    backend_image_id = backend_image_ids.pop()
    try:
        image = authority._image_record(list(docker), executor_image)
        packaged_reference = authority._packaged_sandbox_executor_reference(image)
    except (OSError, subprocess.CalledProcessError, IndexError, json.JSONDecodeError, authority.ReleaseAuthorityError):
        raise TransitionError("target OpenSandbox executor image mismatch") from None
    if (
        executor_image != packaged_reference
        or opensandbox_image != packaged_reference
        or executor_digest != packaged_reference.rsplit("@", 1)[1]
        or image.get("id") != backend_image_id
    ):
        raise TransitionError("target OpenSandbox executor image mismatch")


def _require_target_lifecycle_reachable(docker: Sequence[str]) -> None:
    endpoints: dict[str, str] = {}
    for service in ("api", "worker"):
        environment = _container_environment(_inspect_container(docker, CONTAINERS[service]))
        base_url = environment.get("OPENSANDBOX_BASE_URL", "").strip()
        try:
            parsed = urlsplit(base_url)
            valid = (
                parsed.scheme in {"http", "https"}
                and parsed.hostname is not None
                and parsed.port is not None
                and parsed.username is None
                and parsed.password is None
                and parsed.path in {"", "/"}
                and not parsed.query
                and not parsed.fragment
            )
        except ValueError:
            valid = False
        if not valid:
            raise TransitionError("target OpenSandbox lifecycle endpoint is invalid")
        endpoints[service] = f"{base_url.rstrip('/')}/health"
    if len(set(endpoints.values())) != 1:
        raise TransitionError("target OpenSandbox lifecycle endpoint mismatch")
    probe = (
        "import sys, urllib.request; "
        "opener = urllib.request.build_opener(urllib.request.ProxyHandler({})); "
        "response = opener.open(sys.argv[1], timeout=5); "
        "raise SystemExit(0 if response.status == 200 else 1)"
    )
    for service, health_url in endpoints.items():
        result = _run(
            [*docker, "exec", CONTAINERS[service], "python", "-c", probe, health_url],
            check=False,
            timeout=15,
        )
        if result.returncode != 0:
            raise TransitionError(f"target OpenSandbox lifecycle unreachable from {service}")


def _require_target_network(docker: Sequence[str]) -> None:
    payload = _docker_json(
        docker, "network", "inspect", authority.DIRECT_OPENSANDBOX_NETWORK_NAME
    )
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise TransitionError("target OpenSandbox network inspection is invalid")
    network = payload[0]
    options = network.get("Options") or {}
    labels = network.get("Labels") or {}
    members = network.get("Containers") or {}
    member_names = {
        value.get("Name")
        for value in members.values()
        if isinstance(value, dict)
    }
    member_addresses = {
        value.get("IPv4Address")
        for value in members.values()
        if isinstance(value, dict)
    }
    ipam = network.get("IPAM")
    ipam_config = ipam.get("Config") if isinstance(ipam, dict) else None
    expected_options = {
        "com.docker.network.bridge.name": authority.DIRECT_OPENSANDBOX_BRIDGE_NAME,
        "com.docker.network.bridge.enable_ip_masquerade": "false",
        "com.docker.network.bridge.enable_icc": "false",
    }
    if (
        network.get("Name") != authority.DIRECT_OPENSANDBOX_NETWORK_NAME
        or network.get("Driver") != "bridge"
        or network.get("Internal") is not True
        or network.get("EnableIPv4") is not True
        or network.get("EnableIPv6") is not False
        or options != expected_options
        or not isinstance(ipam_config, list)
        or len(ipam_config) != 1
        or not isinstance(ipam_config[0], dict)
        or ipam_config[0].get("Subnet") != authority.DIRECT_OPENSANDBOX_SUBNET
        or labels.get("com.docker.compose.project") != authority.COMPOSE_PROJECT
        or labels.get("com.docker.compose.network")
        != authority.DIRECT_OPENSANDBOX_NETWORK_KEY
        or len(members) != 1
        or member_names != {TARGET_BROKER_CONTAINER}
        or member_addresses
        != {f"{authority.DIRECT_OPENSANDBOX_PROXY_IPV4}/24"}
    ):
        raise TransitionError("target OpenSandbox network isolation is invalid")


def _require_target_parity(
    docker: Sequence[str],
    repo_root: Path,
    commit: str,
    *,
    docker_cmd: str,
) -> None:
    def collect(_: float) -> dict[str, Any]:
        parity = authority.collect_live_parity(
            repo_root,
            commit,
            docker_cmd=docker_cmd,
            compose_files=TARGET_SELECTION,
        )
        return parity if parity.get("verified") is not True else _target_broker_parity(docker, commit)

    authority.converge_final_parity(
        collect,
        authority_error_type=authority.ReleaseAuthorityError,
    )
    _require_host_prerequisites(repo_root, docker)
    _require_target_network(docker)
    _require_target_executor(docker)
    _require_target_lifecycle_reachable(docker)


@contextmanager
def _workspace_root_environment() -> Iterator[None]:
    key = "SANDBOX_WORKSPACE_ROOT"
    previous = os.environ.get(key)
    os.environ[key] = S75_WORKSPACE_ROOT
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


@contextmanager
def _acceptance_fence() -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in ACCEPTANCE_PORT_ENVIRONMENT}
    os.environ.update(ACCEPTANCE_PORT_ENVIRONMENT)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _stop_admission(docker: Sequence[str]) -> None:
    stopped: list[str] = []
    try:
        for name in ADMISSION_CONTAINERS:
            state = _inspect_container(docker, name).get("State")
            if not isinstance(state, dict) or state.get("Running") is not True:
                raise TransitionError("legacy admission container is not running")
        for name in ADMISSION_CONTAINERS:
            _run([*docker, "stop", name], timeout=90)
            stopped.append(name)
    except Exception as exc:
        for name in reversed(stopped):
            _run([*docker, "start", name], check=False, timeout=90)
        raise TransitionError("failed to stop legacy admission cleanly") from exc


def _restore_admission(docker: Sequence[str]) -> None:
    for name in ADMISSION_CONTAINERS:
        _run([*docker, "start", name], timeout=90)


def _down(
    docker: Sequence[str],
    *,
    project: str,
    env_file: Path,
    compose_files: Sequence[Path],
    environment: Sequence[str] = (),
) -> None:
    _run(
        [
            *_compose_command(
                docker,
                project=project,
                env_file=env_file,
                compose_files=compose_files,
                environment=environment,
            ),
            "down",
            "--remove-orphans",
        ],
        cwd=compose_files[0].parent,
        timeout=300,
    )


def _legacy_convergence_report(
    docker: Sequence[str], runtime: LegacyRuntime
) -> dict[str, bool]:
    if _legacy_runtime(docker, runtime.repo_root, runtime.commit) != runtime:
        raise TransitionError("legacy rollback identity mismatch")
    verified = True
    for service, name in CONTAINERS.items():
        state = _inspect_container(docker, name).get("State")
        if not isinstance(state, dict):
            raise TransitionError(f"legacy rollback state mismatch: {service}")
        status = state.get("Status")
        running = state.get("Running")
        exit_code = state.get("ExitCode")
        if service in {"migrate", "workspace-init"}:
            if (status, running, exit_code) in {
                ("created", False, 0),
                ("running", True, 0),
            }:
                verified = False
            elif status != "exited" or running is not False or exit_code != 0:
                raise TransitionError(f"legacy rollback state mismatch: {service}")
            continue
        if status != "running" or running is not True:
            raise TransitionError(f"legacy rollback state mismatch: {service}")
        health = state.get("Health")
        if health is None:
            continue
        if not isinstance(health, dict):
            raise TransitionError(f"legacy rollback state mismatch: {service}")
        if health.get("Status") == "starting":
            verified = False
        elif health.get("Status") != "healthy":
            raise TransitionError(f"legacy rollback state mismatch: {service}")
    return {"verified": verified}


def _rollback(
    docker: Sequence[str],
    *,
    runtime: LegacyRuntime,
    target_files: Sequence[Path],
    env_file: Path,
) -> None:
    _down(
        docker,
        project=authority.COMPOSE_PROJECT,
        env_file=env_file,
        compose_files=target_files,
    )
    _run(
        [
            *_compose_command(
                docker,
                project=LEGACY_PROJECT,
                env_file=env_file,
                compose_files=runtime.compose_files,
                environment=_legacy_release_environment(runtime),
            ),
            "up",
            "-d",
            "--no-build",
            "--pull",
            "never",
        ],
        cwd=runtime.compose_files[0].parent,
        timeout=600,
    )
    authority.converge_final_parity(
        lambda _: _legacy_convergence_report(docker, runtime),
        authority_error_type=TransitionError,
    )


def _migrate_locked(
    *,
    target_repo_root: Path,
    target_commit: str,
    legacy_repo_root: Path,
    legacy_commit: str,
    env_file: Path,
    backend_image: str,
    frontend_image: str,
    docker_cmd: str,
) -> dict[str, Any]:
    if os.name != "posix" or os.geteuid() != 0:
        raise TransitionError("s75 transition requires root on a POSIX host")
    docker = _docker_base(docker_cmd)
    safe_env_file = _require_safe_env_file(env_file)
    _require_workspace_root_env(safe_env_file)
    runtime = _legacy_runtime(docker, legacy_repo_root, legacy_commit)
    normalized = authority.assert_managed_target_checkout(
        target_repo_root,
        target_commit,
        target_repo_root.parent,
    )
    _require_host_prerequisites(target_repo_root, docker)
    _require_quiescent(docker)
    _require_schema_compatibility(target_repo_root, runtime.commit, normalized)
    target_selection = authority.resolve_compose_files(target_repo_root, TARGET_SELECTION)
    authority.prepare_packaged_release_images(
        normalized,
        backend_image=backend_image,
        frontend_image=frontend_image,
        docker_cmd=docker_cmd,
    )
    with _acceptance_fence(), _workspace_root_environment():
        authority._semantic_compose_config_preflight(
            docker,
            target_selection,
            safe_env_file,
            commit=normalized,
        )
    runtime = _legacy_runtime(docker, legacy_repo_root, legacy_commit)
    _stop_admission(docker)
    try:
        _require_quiescent(docker)
    except Exception:
        _restore_admission(docker)
        raise
    stopped_runtime = _legacy_runtime(docker, legacy_repo_root, legacy_commit)
    if stopped_runtime != runtime:
        _restore_admission(docker)
        raise TransitionError("legacy runtime changed after admission stopped")
    try:
        _down(
            docker,
            project=LEGACY_PROJECT,
            env_file=safe_env_file,
            compose_files=runtime.compose_files,
            environment=_legacy_release_environment(runtime),
        )
        with _acceptance_fence(), _workspace_root_environment():
            authority.deploy_clean_commit(
                target_repo_root,
                normalized,
                docker_cmd=docker_cmd,
                env_file=safe_env_file,
                compose_files=TARGET_SELECTION,
                strategy="canonical",
                replace_known_manual_frontend=False,
            )
            _require_target_runtime(
                docker,
                target_repo_root=target_repo_root,
                target_commit=normalized,
                docker_cmd=docker_cmd,
            )
    except Exception as exc:
        try:
            _rollback(
                docker,
                runtime=runtime,
                target_files=target_selection.absolute_paths,
                env_file=safe_env_file,
            )
        except Exception as rollback_exc:
            raise TransitionError("target deployment and legacy rollback both failed") from rollback_exc
        raise TransitionError("target deployment failed; legacy runtime restored") from exc
    return {
        "status": "migrated_acceptance_pending",
        "commit": normalized,
        "project": authority.COMPOSE_PROJECT,
        "compose_files": [path.as_posix() for path in target_selection.absolute_paths],
        "volumes": {logical: expected for logical, (_, _, expected) in EXPECTED_VOLUMES.items()},
        "rollback": {
            "legacy_commit": runtime.commit,
            "legacy_backend_image": runtime.backend_image,
            "legacy_frontend_image": runtime.frontend_image,
            "legacy_executor_image": runtime.executor_image,
        },
    }


def migrate(
    *,
    target_repo_root: Path,
    target_commit: str,
    legacy_repo_root: Path,
    legacy_commit: str,
    env_file: Path,
    backend_image: str,
    frontend_image: str,
    docker_cmd: str,
) -> dict[str, Any]:
    with _transition_lock():
        return _migrate_locked(
            target_repo_root=target_repo_root,
            target_commit=target_commit,
            legacy_repo_root=legacy_repo_root,
            legacy_commit=legacy_commit,
            env_file=env_file,
            backend_image=backend_image,
            frontend_image=frontend_image,
            docker_cmd=docker_cmd,
        )


def finalize(
    *,
    target_repo_root: Path,
    target_commit: str,
    env_file: Path,
    docker_cmd: str,
) -> dict[str, Any]:
    if os.name != "posix" or os.geteuid() != 0:
        raise TransitionError("s75 transition requires root on a POSIX host")
    with _transition_lock():
        docker = _docker_base(docker_cmd)
        safe_env_file = _require_safe_env_file(env_file)
        _require_workspace_root_env(safe_env_file)
        with _acceptance_fence(), _workspace_root_environment():
            normalized, _ = _require_target_runtime(
                docker,
                target_repo_root=target_repo_root,
                target_commit=target_commit,
                docker_cmd=docker_cmd,
            )
        _require_quiescent(docker)
        try:
            with _workspace_root_environment():
                authority.deploy_clean_commit(
                    target_repo_root,
                    normalized,
                    docker_cmd=docker_cmd,
                    env_file=safe_env_file,
                    compose_files=TARGET_SELECTION,
                    strategy="canonical",
                    replace_known_manual_frontend=False,
                )
                _require_target_runtime(
                    docker,
                    target_repo_root=target_repo_root,
                    target_commit=normalized,
                    docker_cmd=docker_cmd,
                )
        except Exception as exc:
            try:
                with _acceptance_fence(), _workspace_root_environment():
                    authority.deploy_clean_commit(
                        target_repo_root,
                        normalized,
                        docker_cmd=docker_cmd,
                        env_file=safe_env_file,
                        compose_files=TARGET_SELECTION,
                        strategy="canonical",
                        replace_known_manual_frontend=False,
                    )
                    _require_target_runtime(
                        docker,
                        target_repo_root=target_repo_root,
                        target_commit=normalized,
                        docker_cmd=docker_cmd,
                    )
            except Exception as fence_exc:
                raise TransitionError("final admission failed and the acceptance fence could not be restored") from fence_exc
            raise TransitionError("final admission failed; target runtime restored behind acceptance fence") from exc
        return {
            "status": "admitted",
            "commit": normalized,
            "project": authority.COMPOSE_PROJECT,
        }


def _validated_rollback_runtime(
    docker: Sequence[str],
    *,
    legacy_repo_root: Path,
    legacy_commit: str,
    backend_image: str,
    frontend_image: str,
    executor_image: str,
) -> LegacyRuntime:
    normalized = _assert_root_owned_checkout(legacy_repo_root, legacy_commit)
    selection = authority.resolve_compose_files(legacy_repo_root, LEGACY_SELECTION)
    repository = authority.authoritative_repository(legacy_repo_root)
    records: dict[str, dict[str, Any]] = {}
    for role, reference in (("backend", backend_image), ("frontend", frontend_image)):
        record = authority._image_record(list(docker), reference)
        authority._validate_release_image(
            record,
            commit=normalized,
            repository=repository,
            role=role,
        )
        records[role] = record
    executor_record = authority._image_record(list(docker), executor_image)
    if executor_record.get("id") != records["backend"].get("id"):
        raise TransitionError("legacy executor image must resolve to the verified backend image")
    return LegacyRuntime(
        repo_root=legacy_repo_root.resolve(),
        compose_files=selection.absolute_paths,
        commit=normalized,
        backend_image=backend_image,
        frontend_image=frontend_image,
        executor_image=executor_image,
    )


def _require_target_runtime(
    docker: Sequence[str],
    *,
    target_repo_root: Path,
    target_commit: str,
    docker_cmd: str,
) -> tuple[str, tuple[Path, ...]]:
    normalized = authority.assert_managed_target_checkout(
        target_repo_root,
        target_commit,
        target_repo_root.parent,
    )
    selection = authority.resolve_compose_files(target_repo_root, TARGET_SELECTION)
    _require_target_parity(docker, target_repo_root, normalized, docker_cmd=docker_cmd)
    containers = {
        service: _inspect_container(docker, name)
        for service, name in CONTAINERS.items()
    }
    for service, container in containers.items():
        labels = _labels(container)
        if (
            labels.get("com.docker.compose.project") != authority.COMPOSE_PROJECT
            or labels.get("com.docker.compose.service") != service
        ):
            raise TransitionError(f"target Compose ownership mismatch: {service}")
    _require_volume_identities(docker, containers)
    return normalized, selection.absolute_paths


def rollback(
    *,
    target_repo_root: Path,
    target_commit: str,
    legacy_repo_root: Path,
    legacy_commit: str,
    env_file: Path,
    legacy_backend_image: str,
    legacy_frontend_image: str,
    legacy_executor_image: str,
    docker_cmd: str,
) -> dict[str, Any]:
    if os.name != "posix" or os.geteuid() != 0:
        raise TransitionError("s75 transition requires root on a POSIX host")
    with _transition_lock():
        docker = _docker_base(docker_cmd)
        safe_env_file = _require_safe_env_file(env_file)
        _require_workspace_root_env(safe_env_file)
        runtime = _validated_rollback_runtime(
            docker,
            legacy_repo_root=legacy_repo_root,
            legacy_commit=legacy_commit,
            backend_image=legacy_backend_image,
            frontend_image=legacy_frontend_image,
            executor_image=legacy_executor_image,
        )
        with _workspace_root_environment():
            normalized, target_files = _require_target_runtime(
                docker,
                target_repo_root=target_repo_root,
                target_commit=target_commit,
                docker_cmd=docker_cmd,
            )
        _require_schema_compatibility(target_repo_root, runtime.commit, normalized)
        _require_quiescent(docker)
        _stop_admission(docker)
        try:
            _require_quiescent(docker)
        except Exception:
            _restore_admission(docker)
            raise
        try:
            _rollback(
                docker,
                runtime=runtime,
                target_files=target_files,
                env_file=safe_env_file,
            )
        except Exception as rollback_exc:
            try:
                _down(
                    docker,
                    project=LEGACY_PROJECT,
                    env_file=safe_env_file,
                    compose_files=runtime.compose_files,
                    environment=_legacy_release_environment(runtime),
                )
                with _acceptance_fence(), _workspace_root_environment():
                    authority.deploy_clean_commit(
                        target_repo_root,
                        normalized,
                        docker_cmd=docker_cmd,
                        env_file=safe_env_file,
                        compose_files=TARGET_SELECTION,
                        strategy="canonical",
                        replace_known_manual_frontend=False,
                    )
                    _require_target_runtime(
                        docker,
                        target_repo_root=target_repo_root,
                        target_commit=normalized,
                        docker_cmd=docker_cmd,
                    )
            except Exception as target_restore_exc:
                raise TransitionError("legacy rollback and target restoration both failed") from target_restore_exc
            raise TransitionError("legacy rollback failed; target runtime restored") from rollback_exc
        return {
            "status": "rolled_back",
            "commit": runtime.commit,
            "project": LEGACY_PROJECT,
            "volumes": {logical: expected for logical, (_, _, expected) in EXPECTED_VOLUMES.items()},
        }


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target-repo-root", required=True, type=Path)
    parser.add_argument("--target-commit", required=True)
    parser.add_argument("--legacy-repo-root", required=True, type=Path)
    parser.add_argument("--legacy-commit", required=True)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--docker-cmd", default="docker")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Governed one-time s75 OpenSandbox transition")
    commands = parser.add_subparsers(dest="command", required=True)
    migrate_parser = commands.add_parser("migrate")
    _add_common_arguments(migrate_parser)
    migrate_parser.add_argument("--backend-image", required=True)
    migrate_parser.add_argument("--frontend-image", required=True)
    finalize_parser = commands.add_parser("finalize")
    finalize_parser.add_argument("--target-repo-root", required=True, type=Path)
    finalize_parser.add_argument("--target-commit", required=True)
    finalize_parser.add_argument("--env-file", required=True, type=Path)
    finalize_parser.add_argument("--docker-cmd", default="docker")
    rollback_parser = commands.add_parser("rollback")
    _add_common_arguments(rollback_parser)
    rollback_parser.add_argument("--legacy-backend-image", required=True)
    rollback_parser.add_argument("--legacy-frontend-image", required=True)
    rollback_parser.add_argument("--legacy-executor-image", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "migrate":
            result = migrate(
                target_repo_root=args.target_repo_root,
                target_commit=args.target_commit,
                legacy_repo_root=args.legacy_repo_root,
                legacy_commit=args.legacy_commit,
                env_file=args.env_file,
                backend_image=args.backend_image,
                frontend_image=args.frontend_image,
                docker_cmd=args.docker_cmd,
            )
        elif args.command == "finalize":
            result = finalize(
                target_repo_root=args.target_repo_root,
                target_commit=args.target_commit,
                env_file=args.env_file,
                docker_cmd=args.docker_cmd,
            )
        else:
            result = rollback(
                target_repo_root=args.target_repo_root,
                target_commit=args.target_commit,
                legacy_repo_root=args.legacy_repo_root,
                legacy_commit=args.legacy_commit,
                env_file=args.env_file,
                legacy_backend_image=args.legacy_backend_image,
                legacy_frontend_image=args.legacy_frontend_image,
                legacy_executor_image=args.legacy_executor_image,
                docker_cmd=args.docker_cmd,
            )
    except (TransitionError, authority.ReleaseAuthorityError) as exc:
        print(f"s75 transition failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
