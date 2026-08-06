"""Single mutation authority for an s72 co-located platform and OpenSandbox release."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import io
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import time
import tomllib
import zipfile
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

try:
    from . import release_authority
except ImportError:  # pragma: no cover - direct script execution
    import release_authority  # type: ignore[no-redef]


BASE_COMPOSE = "deploy/ai-platform/docker-compose.yml"
COLOCATION_COMPOSE = "deploy/ai-platform/docker-compose.s72-colocation.yml"
COMPOSE_FILES = (BASE_COMPOSE, COLOCATION_COMPOSE)
GATEWAY_CONFIG_ROOT = Path("/etc/opensandbox-gateway")
SERVER_CONFIG_ROOT = Path("/etc/ai-platform/opensandbox")
MUTATION_LEASE_PATH = Path("/var/lock/ai-platform-s72-colocation.lock")
RUNTIME_STATE_ROOT = Path("/var/lib/ai-platform-s72-colocation")
OPENSANDBOX_STATE_ROOT = Path("/var/lib/ai-platform-opensandbox")
OPENSANDBOX_UNIT_PATH = Path("/etc/systemd/system/opensandbox.service")
OPENSANDBOX_UNIT_RELATIVE_PATH = "deploy/opensandbox/opensandbox-s72.service"
OPENSANDBOX_CONTAINER_NAME = "ai-platform-opensandbox-server"
OPENSANDBOX_NETWORK_NAME = "ai-platform-opensandbox-lifecycle"
LOCAL_BROKER_ORIGIN = "http://127.0.0.1:18043"
LOCAL_BROKER_BASES = {
    "callback": LOCAL_BROKER_ORIGIN,
    "openai": f"{LOCAL_BROKER_ORIGIN}/openai/v1",
    "anthropic": f"{LOCAL_BROKER_ORIGIN}/anthropic",
}
RETIRED_PLATFORM_KEYS = frozenset(
    "AI_PLATFORM_S72_BRIDGE_PORT AI_PLATFORM_S72_BRIDGE_SERVER_NAME AI_PLATFORM_S72_BRIDGE_ALLOWED_SOURCE_IP "
    "AI_PLATFORM_S72_BRIDGE_TLS_CERT_FILE AI_PLATFORM_S72_BRIDGE_TLS_KEY_FILE "
    "OPENSANDBOX_EXTERNAL_EGRESS_CALLBACK_BASE_URL OPENSANDBOX_EXTERNAL_EGRESS_OPENAI_BASE_URL "
    "OPENSANDBOX_EXTERNAL_EGRESS_ANTHROPIC_BASE_URL".split()
)
REQUIRED_PLATFORM_KEYS = frozenset(
    "AI_PLATFORM_MODEL_UPSTREAM AI_PLATFORM_FRONTEND_PORT WORKER_CLAUDE_AGENT_SDK_ENABLED "
    "CLAUDE_AGENT_PERMISSION_MODE CLAUDE_AGENT_ALLOWED_TOOLS CLAUDE_AGENT_DISALLOWED_TOOLS "
    "SANDBOX_CONTAINER_PROVIDER SANDBOX_SECURITY_PROFILE OPENSANDBOX_API_KEY OPENSANDBOX_DOMAIN "
    "OPENSANDBOX_PROTOCOL OPENSANDBOX_EXECUTOR_IMAGE OPENSANDBOX_EXECUTOR_IMAGE_DIGEST "
    "OPENSANDBOX_ATTESTATION_PATH OPENSANDBOX_ATTESTATION_CONTRACT_VERSION "
    "OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_URL OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_TOKEN "
    "OPENSANDBOX_EXTERNAL_EGRESS_GATEWAY_POLICY_SUBJECT "
    "OPENSANDBOX_EXTERNAL_EGRESS_CALLBACK_BOUNDARY_SUBJECT SANDBOX_CALLBACK_TOKEN "
    "SANDBOX_EGRESS_PROOF_SIGNING_KEY SANDBOX_RUNTIME_SUBJECT".split()
)
GATEWAY_EXPECTED_VALUES = {
    "OPENSANDBOX_GATEWAY_API_KEY_FILE": "/etc/opensandbox-gateway/secrets/lifecycle-api-key",
    "OPENSANDBOX_GATEWAY_CAPABILITY_TOKEN_FILE": "/etc/opensandbox-gateway/secrets/capability-token",
    "OPENSANDBOX_GATEWAY_SIGNING_KEY_FILE": "/etc/opensandbox-gateway/secrets/record-signing-key",
    "OPENSANDBOX_GATEWAY_OPENAI_API_KEY_FILE": "/etc/ai-platform/model-secrets/openai-api-key",
    "OPENSANDBOX_GATEWAY_ANTHROPIC_AUTH_TOKEN_FILE": "/etc/ai-platform/model-secrets/anthropic-auth-token",
    "OPENSANDBOX_GATEWAY_UPSTREAM_TRANSPORT": "loopback_http",
    "OPENSANDBOX_GATEWAY_CALLBACK_BASE": LOCAL_BROKER_BASES["callback"],
    "OPENSANDBOX_GATEWAY_OPENAI_BASE": LOCAL_BROKER_BASES["openai"],
    "OPENSANDBOX_GATEWAY_ANTHROPIC_BASE": LOCAL_BROKER_BASES["anthropic"],
}
MANAGED_CONTAINER_NAMES = ("ai-platform-api", "ai-platform-worker", "ai-platform-frontend")
MIN_COMPOSE_VERSION = (2, 24, 4)
MIN_ROLLBACK_FREE_BYTES = 5 * 1024 * 1024 * 1024
IMMUTABLE_IMAGE_RE = re.compile(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}\Z")
SERVER_REQUIRED_ENV_KEYS = frozenset(
    "OPENSANDBOX_SERVER_IMAGE OPENSANDBOX_SERVER_IMAGE_DIGEST OPENSANDBOX_SERVER_UID "
    "OPENSANDBOX_SERVER_GID OPENSANDBOX_DOCKER_SOCKET_GID".split()
)
DANGEROUS_CAPABILITIES = frozenset({"AUDIT_WRITE", "MKNOD", "NET_ADMIN", "NET_RAW", "SYS_ADMIN", "SYS_MODULE", "SYS_PTRACE", "SYS_TIME", "SYS_TTY_CONFIG"})


class S72ColocationError(RuntimeError):
    """Fail one s72 authority gate without exposing configuration values."""


def _validate_authority_evidence_id(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value):
        raise S72ColocationError("authority evidence id is invalid")


def _command(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int = 30,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = release_authority._run(
        argv,
        cwd=cwd,
        env=None if env is None else dict(env),
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        raise S72ColocationError(f"command gate failed: {Path(argv[0]).name}")
    return result


def _parse_env_file(path: Path) -> dict[str, str]:
    try:
        metadata = path.stat(follow_symlinks=False)
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise S72ColocationError("managed configuration is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise S72ColocationError("managed configuration must be a regular non-link file")
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise S72ColocationError("managed configuration shape is invalid")
        values[key] = value
    return values


def _validate_model_upstream(value: str) -> None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise S72ColocationError("model upstream contract is invalid") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "host.docker.internal"
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port != 3002
    ):
        raise S72ColocationError("model upstream contract is invalid")


def validate_platform_environment(path: Path) -> dict[str, object]:
    values = _parse_env_file(path)
    retired = sorted(RETIRED_PLATFORM_KEYS.intersection(values))
    missing = sorted(key for key in REQUIRED_PLATFORM_KEYS if not values.get(key))
    if retired:
        raise S72ColocationError("retired cross-host platform keys are present")
    if (
        values.get("WORKER_CLAUDE_AGENT_SDK_ENABLED") != "true"
        or values.get("CLAUDE_AGENT_PERMISSION_MODE") != "dontAsk"
        or tuple(item.strip() for item in values.get("CLAUDE_AGENT_ALLOWED_TOOLS", "").split(","))
        != ("Read", "Glob", "LS", "Bash")
        or tuple(item.strip() for item in values.get("CLAUDE_AGENT_DISALLOWED_TOOLS", "").split(","))
        != ("Write", "Edit", "NotebookEdit")
    ):
        raise S72ColocationError("SDK production selection is unsafe")
    if (
        values.get("SANDBOX_CONTAINER_PROVIDER") != "opensandbox"
        or values.get("SANDBOX_SECURITY_PROFILE") != "governed"
    ):
        raise S72ColocationError("sandbox authority selection is unsafe")
    if missing:
        raise S72ColocationError("required s72 platform keys are missing")
    _validate_model_upstream(values["AI_PLATFORM_MODEL_UPSTREAM"])
    image = values["OPENSANDBOX_EXECUTOR_IMAGE"]
    digest = values["OPENSANDBOX_EXECUTOR_IMAGE_DIGEST"]
    _immutable_image(image, digest, subject="OpenSandbox executor")
    return {
        "present_key_count": len(values),
        "required_keys_present": True,
        "retired_keys_absent": True,
        "executor_image_immutable": True,
        "sdk_selection_fail_closed": True,
        "sandbox_authority": "opensandbox",
    }


def _immutable_image(value: str, digest: str, *, subject: str) -> None:
    if (
        not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
        or not IMMUTABLE_IMAGE_RE.fullmatch(value)
        or not value.endswith(f"@{digest}")
    ):
        raise S72ColocationError(f"{subject} image is not immutable")


def _regular_file_metadata(path: Path, *, error: str) -> os.stat_result:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise S72ColocationError(error) from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise S72ColocationError(error)
    return metadata


def _docker_record(docker_cmd: str, identity: str, *, error: str) -> dict[str, Any]:
    try:
        _, record = release_authority._container_inspect_record([docker_cmd], identity)
    except (
        OSError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
        IndexError,
        KeyError,
        TypeError,
        release_authority.ReleaseAuthorityError,
    ) as exc:
        raise S72ColocationError(error) from exc
    return record


def _has_no_new_privileges(host: Mapping[str, object]) -> bool:
    return any(
        str(value).lower() in {"no-new-privileges", "no-new-privileges:true"}
        for value in host.get("SecurityOpt") or []
    )


def _bind_mounts(record: Mapping[str, object]) -> set[tuple[str, str, bool]]:
    return {
        (str(item.get("Source") or ""), str(item.get("Destination") or ""), bool(item.get("RW")))
        for item in record.get("Mounts") or []
        if item.get("Type") == "bind"
    }


def _read_secret_file(path: Path, *, error: str) -> str:
    metadata = _regular_file_metadata(path, error=error)
    if metadata.st_size > 16_384:
        raise S72ColocationError(error)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise S72ColocationError(error) from exc
    if not value:
        raise S72ColocationError(error)
    return value


def _parse_positive_id(value: str, *, subject: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]{0,9}", value):
        raise S72ColocationError(f"{subject} is invalid")
    parsed = int(value)
    if parsed > 2_147_483_647:
        raise S72ColocationError(f"{subject} is invalid")
    return parsed


def validate_opensandbox_server_configuration(
    config_root: Path = SERVER_CONFIG_ROOT,
    *,
    require_root_ownership: bool | None = None,
) -> dict[str, object]:
    require_root = os.name == "posix" if require_root_ownership is None else require_root_ownership
    env_path = config_root / "server.env"
    config_path = config_root / "server.toml"
    env_metadata = _regular_file_metadata(env_path, error="OpenSandbox server environment is unavailable")
    config_metadata = _regular_file_metadata(config_path, error="OpenSandbox server configuration is unavailable")
    if require_root and stat.S_IMODE(env_metadata.st_mode) != 0o600:
        raise S72ColocationError("OpenSandbox server environment ownership contract drifted")
    values = _parse_env_file(env_path)
    if SERVER_REQUIRED_ENV_KEYS.difference(values):
        raise S72ColocationError("OpenSandbox server environment is incomplete")
    uid = _parse_positive_id(values["OPENSANDBOX_SERVER_UID"], subject="OpenSandbox server UID")
    gid = _parse_positive_id(values["OPENSANDBOX_SERVER_GID"], subject="OpenSandbox server GID")
    socket_gid = _parse_positive_id(
        values["OPENSANDBOX_DOCKER_SOCKET_GID"],
        subject="Docker socket GID",
    )
    if uid in {101, 1000} or gid in {101, 1000}:
        raise S72ColocationError("OpenSandbox server identity is not independent")
    if require_root and (
        env_metadata.st_uid != 0
        or env_metadata.st_gid != 0
        or config_metadata.st_uid != 0
        or config_metadata.st_gid != gid
    ):
        raise S72ColocationError("OpenSandbox server configuration ownership contract drifted")
    if require_root and stat.S_IMODE(config_metadata.st_mode) != 0o440:
        raise S72ColocationError("OpenSandbox server configuration ownership contract drifted")
    image = values["OPENSANDBOX_SERVER_IMAGE"]
    image_digest = values["OPENSANDBOX_SERVER_IMAGE_DIGEST"]
    _immutable_image(image, image_digest, subject="OpenSandbox server")
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise S72ColocationError("OpenSandbox server configuration is invalid") from exc
    server = config.get("server") or {}
    runtime = config.get("runtime") or {}
    storage = config.get("storage") or {}
    store = config.get("store") or {}
    docker = config.get("docker") or {}
    ingress = config.get("ingress") or {}
    secure_runtime = config.get("secure_runtime") or {}
    api_key = server.get("api_key")
    execd_image = str(runtime.get("execd_image") or "")
    execd_digest = execd_image.rsplit("@", 1)[-1] if "@" in execd_image else ""
    drop_capabilities = {str(item) for item in docker.get("drop_capabilities") or []}
    if (
        server.get("host") != "0.0.0.0"
        or server.get("port") != 8080
        or not isinstance(api_key, str)
        or len(api_key) < 32
        or len(api_key) > 4096
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in api_key)
        or re.search(r"required|replace|change|placeholder", api_key, re.IGNORECASE)
        or runtime.get("type") != "docker"
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", execd_digest)
        or storage.get("allowed_host_paths") != ["/data/opensandbox/workspaces"]
        or store.get("type") != "sqlite"
        or store.get("path") != "/var/lib/ai-platform-opensandbox/opensandbox.db"
        or docker.get("network_mode") != "none"
        or docker.get("no_new_privileges") is not True
        or not DANGEROUS_CAPABILITIES.issubset(drop_capabilities)
        or not isinstance(docker.get("pids_limit"), int)
        or not 64 <= docker["pids_limit"] <= 65_536
        or ingress.get("mode") != "direct"
        or secure_runtime != {"type": "gvisor", "docker_runtime": "runsc"}
        or "egress" in config
        or "renew_intent" in config
    ):
        raise S72ColocationError("OpenSandbox server security contract drifted")
    return {
        "server_image_digest": image_digest,
        "execd_image_digest": execd_digest,
        "server_uid": uid,
        "server_gid": gid,
        "docker_socket_gid": socket_gid,
        "runtime": "runsc",
        "sandbox_network_mode": "none",
        "allowed_host_paths": ["/data/opensandbox/workspaces"],
    }


def validate_gateway_configuration(
    config_root: Path = GATEWAY_CONFIG_ROOT,
    *,
    require_root_ownership: bool | None = None,
) -> dict[str, object]:
    require_root = os.name == "posix" if require_root_ownership is None else require_root_ownership
    gateway_gid: int | None = None
    if require_root:
        try:
            import grp

            gateway_gid = grp.getgrnam("opensandbox-gateway").gr_gid
        except (ImportError, KeyError) as exc:
            raise S72ColocationError("OpenSandbox gateway group authority is unavailable") from exc
    values = _parse_env_file(config_root / "gateway.env")
    if any(values.get(key) != expected for key, expected in GATEWAY_EXPECTED_VALUES.items()):
        raise S72ColocationError("gateway loopback broker contract drifted")
    if require_root:
        try:
            import pwd
            from services.opensandbox_gateway.server import load_config

            gateway_config, *_ = load_config(values)
            gateway_uid = pwd.getpwnam("opensandbox-gateway").pw_uid
        except (ImportError, KeyError, OSError, ValueError) as exc:
            raise S72ColocationError("gateway security configuration is invalid") from exc
        if values.get("OPENSANDBOX_GATEWAY_ALLOWED_UID") != str(gateway_uid):
            raise S72ColocationError("gateway UID authority drifted")
        if gateway_config.upstream_transport != "loopback_http":
            raise S72ColocationError("gateway loopback broker contract drifted")
    try:
        policy = json.loads((config_root / "egress-policy.v1.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise S72ColocationError("gateway egress policy is invalid") from exc
    expected_targets = {
        kind: {"base_url": base, "expected_ips": ["127.0.0.1"]}
        for kind, base in LOCAL_BROKER_BASES.items()
    }
    if policy != {"version": 1, "targets": expected_targets}:
        raise S72ColocationError("gateway egress policy is not the s72 loopback policy")
    for relative, expected_mode in (
        ("secrets/lifecycle-api-key", 0o440),
        ("secrets/capability-token", 0o440),
        ("secrets/record-signing-key", 0o440),
        ("tls/fullchain.pem", 0o640),
        ("tls/upstream-ca.pem", 0o640),
        ("tls/privkey.pem", 0o440),
    ):
        target = config_root / relative
        metadata = _regular_file_metadata(target, error="gateway secret-file contract is incomplete")
        if stat.S_IMODE(metadata.st_mode) != expected_mode or (
            require_root
            and (metadata.st_uid != 0 or metadata.st_gid not in {0, gateway_gid})
        ):
            raise S72ColocationError("gateway secret-file ownership contract drifted")
    for target in (
        Path("/etc/ai-platform/model-secrets/openai-api-key"),
        Path("/etc/ai-platform/model-secrets/anthropic-auth-token"),
    ):
        metadata = _regular_file_metadata(target, error="model credential secret-file contract is incomplete")
        if stat.S_IMODE(metadata.st_mode) != 0o440 or (
            require_root and (metadata.st_uid != 0 or metadata.st_gid != gateway_gid)
        ):
            raise S72ColocationError("model credential secret-file ownership contract drifted")
    return {
        "upstream_transport": "loopback_http",
        "egress_targets": sorted(expected_targets),
        "secret_files_present": True,
    }


def validate_colocation_identity_coherence(
    platform_env_path: Path,
    server_config_root: Path = SERVER_CONFIG_ROOT,
    gateway_config_root: Path = GATEWAY_CONFIG_ROOT,
) -> dict[str, object]:
    platform_values = _parse_env_file(platform_env_path)
    gateway_values = _parse_env_file(gateway_config_root / "gateway.env")
    try:
        server_config = tomllib.loads(
            (server_config_root / "server.toml").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise S72ColocationError("OpenSandbox identity configuration is invalid") from exc
    lifecycle_secret = _read_secret_file(
        gateway_config_root / "secrets/lifecycle-api-key",
        error="OpenSandbox lifecycle identity is unavailable",
    )
    capability_secret = _read_secret_file(
        gateway_config_root / "secrets/capability-token",
        error="OpenSandbox capability identity is unavailable",
    )
    server_secret = str((server_config.get("server") or {}).get("api_key") or "")
    gateway_authority = gateway_values.get("OPENSANDBOX_GATEWAY_PUBLIC_AUTHORITY", "")
    expected_platform = {
        "OPENSANDBOX_PROTOCOL": "https",
        "OPENSANDBOX_DOMAIN": gateway_authority,
        "OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_URL": f"https://{gateway_authority}/v1/capabilities/governed-egress",
        "OPENSANDBOX_EXECUTOR_IMAGE": gateway_values.get("OPENSANDBOX_GATEWAY_EXECUTOR_IMAGE"),
        "SANDBOX_RUNTIME_SUBJECT": gateway_values.get("OPENSANDBOX_GATEWAY_RUNTIME_SUBJECT"),
        "OPENSANDBOX_EXTERNAL_EGRESS_GATEWAY_POLICY_SUBJECT": gateway_values.get("OPENSANDBOX_GATEWAY_POLICY_SUBJECT"),
        "OPENSANDBOX_EXTERNAL_EGRESS_CALLBACK_BOUNDARY_SUBJECT": gateway_values.get("OPENSANDBOX_GATEWAY_CALLBACK_SUBJECT"),
        "OPENSANDBOX_ATTESTATION_PATH": "/v1/sandboxes/{sandbox_id}/attestation",
        "OPENSANDBOX_ATTESTATION_CONTRACT_VERSION": "ai-platform.opensandbox.topology-attestation.v1",
    }
    coherent = all(platform_values.get(key) == value for key, value in expected_platform.items()) and all(
        hmac.compare_digest(observed, expected)
        for observed, expected in (
            (platform_values.get("OPENSANDBOX_API_KEY", ""), lifecycle_secret),
            (server_secret, lifecycle_secret),
            (platform_values.get("OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_TOKEN", ""), capability_secret),
        )
    )
    if not coherent:
        raise S72ColocationError("OpenSandbox identity and subject coherence drifted")
    return dict.fromkeys(("lifecycle_identity_coherent", "capability_identity_coherent", "runtime_subjects_coherent", "public_authority_coherent"), True)


def _safe_probe(argv: Sequence[str], *, timeout: int = 10) -> dict[str, object]:
    try:
        result = _command(argv, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return {"available": False}
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return {
        "available": result.returncode == 0,
        "summary": lines[0][:160] if result.returncode == 0 and lines else "",
        "line_count": len(lines) if result.returncode == 0 else 0,
    }


def _compose_version(value: str) -> tuple[int, int, int]:
    match = re.search(r"(?:^|[^0-9])(\d+)\.(\d+)\.(\d+)(?:[^0-9]|$)", value.strip())
    if not match:
        raise S72ColocationError("Docker Compose version is invalid")
    return tuple(int(match.group(index)) for index in (1, 2, 3))


def _systemd_projection(unit: str) -> dict[str, object]:
    try:
        result = _command(
            ["systemctl", "show", unit, "--property=LoadState,ActiveState,FragmentPath,UnitFileState"],
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise S72ColocationError("systemd projection is unavailable") from exc
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return {
        "load_state": values.get("LoadState", "unknown"),
        "active_state": values.get("ActiveState", "unknown"),
        "unit_file_state": values.get("UnitFileState", "unknown"),
        "fragment_is_managed_path": values.get("FragmentPath", "")
        in {"", str(Path("/etc/systemd/system") / unit)},
    }


def _runtime_preflight(
    docker_cmd: str,
    server_projection: Mapping[str, object],
    server_config_root: Path,
) -> dict[str, object]:
    docker_version = _command(
        [docker_cmd, "version", "--format", "{{.Server.Version}}"],
        check=False,
    )
    if docker_version.returncode != 0 or not docker_version.stdout.strip():
        raise S72ColocationError("Docker daemon is unavailable")
    compose = _command([docker_cmd, "compose", "version", "--short"], check=False)
    if compose.returncode != 0 or _compose_version(compose.stdout) < MIN_COMPOSE_VERSION:
        raise S72ColocationError("Docker Compose does not support the s72 override contract")
    runsc = _command(["runsc", "--version"], check=False)
    runtimes = _command([docker_cmd, "info", "--format", "{{json .Runtimes}}"], check=False)
    if runsc.returncode != 0 or runtimes.returncode != 0:
        raise S72ColocationError("runsc runtime is unavailable")
    try:
        runtime_map = json.loads(runtimes.stdout)
    except json.JSONDecodeError as exc:
        raise S72ColocationError("Docker runtime projection is invalid") from exc
    if not isinstance(runtime_map, dict) or "runsc" not in runtime_map:
        raise S72ColocationError("Docker daemon has no registered runsc runtime")
    socket_path = Path("/var/run/docker.sock")
    try:
        socket_metadata = socket_path.stat(follow_symlinks=False)
    except OSError as exc:
        raise S72ColocationError("Docker socket authority is unavailable") from exc
    if (
        socket_path.is_symlink()
        or not stat.S_ISSOCK(socket_metadata.st_mode)
        or socket_metadata.st_gid != server_projection["docker_socket_gid"]
    ):
        raise S72ColocationError("Docker socket group authority drifted")
    server_values = _parse_env_file(server_config_root / "server.env")
    image = server_values["OPENSANDBOX_SERVER_IMAGE"]
    local_image = _command([docker_cmd, "image", "inspect", image], check=False)
    registry_image = None
    if local_image.returncode != 0:
        registry_image = _command([docker_cmd, "manifest", "inspect", image], timeout=30, check=False)
        if registry_image.returncode != 0:
            raise S72ColocationError("immutable OpenSandbox server image is unavailable")
    return {
        "docker_server_version": docker_version.stdout.strip()[:80],
        "compose_version": ".".join(str(item) for item in _compose_version(compose.stdout)),
        "runsc_registered": True,
        "server_image_local": local_image.returncode == 0,
        "server_image_registry_verified": registry_image is not None,
    }


def _port_listener_count(port: int) -> int:
    result = _command(["ss", "-ltnH", "sport", "=", f":{port}"], check=False)
    if result.returncode != 0:
        raise S72ColocationError("host listener projection is unavailable")
    return len([line for line in result.stdout.splitlines() if line.strip()])


def _source_projection(source: Path, commit: str) -> dict[str, object]:
    release_authority.assert_clean_coordination_source(source, commit)
    local_main = _command(["git", "rev-parse", "origin/main"], cwd=source).stdout.strip()
    remote_result = _command(
        ["git", "ls-remote", "origin", "refs/heads/main"], cwd=source
    )
    remote_parts = remote_result.stdout.split()
    if len(remote_parts) != 2 or remote_parts[1] != "refs/heads/main":
        raise S72ColocationError("remote main authority is unavailable")
    remote_main = remote_parts[0]
    if local_main != commit or remote_main != commit:
        raise S72ColocationError("exact-main authority drifted")
    return {"head": commit, "origin_main": local_main, "ls_remote_main": remote_main}


def collect_read_only_preflight(
    source: Path,
    commit: str,
    release_root: Path,
    smoke_accounts_file: Path,
    smoke_sample_docx: Path,
    authority_evidence_id: str,
    *,
    docker_cmd: str = "docker",
    gateway_config_root: Path = GATEWAY_CONFIG_ROOT,
    server_config_root: Path = SERVER_CONFIG_ROOT,
) -> dict[str, object]:
    _validate_authority_evidence_id(authority_evidence_id)
    if os.name == "posix" and (
        gateway_config_root != GATEWAY_CONFIG_ROOT
        or server_config_root != SERVER_CONFIG_ROOT
    ):
        raise S72ColocationError("s72 managed configuration roots are fixed")
    source_projection = _source_projection(source, commit)
    if os.name == "posix" and (source.is_symlink() or source.stat().st_uid != 0):
        raise S72ColocationError("coordination source is not root-owned")
    env_file = release_authority.resolve_managed_env_file(release_root, None)
    platform_projection = validate_platform_environment(env_file)
    gateway_projection = validate_gateway_configuration(gateway_config_root)
    server_projection = validate_opensandbox_server_configuration(server_config_root)
    identity_projection = validate_colocation_identity_coherence(
        env_file,
        server_config_root,
        gateway_config_root,
    )
    smoke_projection = validate_smoke_inputs(
        smoke_accounts_file,
        smoke_sample_docx,
        authority_evidence_id,
    )
    runtime_projection = _runtime_preflight(docker_cmd, server_projection, server_config_root)
    opensandbox_unit = _systemd_projection("opensandbox.service")
    gateway_unit = _systemd_projection("opensandbox-gateway.service")
    for projection in (opensandbox_unit, gateway_unit):
        if (
            projection["load_state"] != "not-found"
            and projection["fragment_is_managed_path"] is not True
        ):
            raise S72ColocationError("existing s72 unit ownership drifted")
    port_8080_count = _port_listener_count(8080)
    port_18043_count = _port_listener_count(18043)
    if opensandbox_unit["active_state"] == "active" and port_8080_count != 1:
        raise S72ColocationError("existing OpenSandbox listener ownership drifted")
    if opensandbox_unit["active_state"] != "active" and port_8080_count:
        raise S72ColocationError("port 8080 is occupied outside OpenSandbox authority")
    broker_inspect = _command([docker_cmd, "inspect", "ai-platform-s72-broker-entry"], check=False)
    if port_18043_count and broker_inspect.returncode != 0:
        raise S72ColocationError("port 18043 is occupied outside s72 broker ownership")
    disk = shutil.disk_usage(release_root.parent)
    if disk.free < MIN_ROLLBACK_FREE_BYTES:
        raise S72ColocationError("insufficient free space for release and rollback")
    memory_total = 0
    try:
        memory_line = next(
            line for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines()
            if line.startswith("MemTotal:")
        )
        memory_total = int(memory_line.split()[1]) * 1024
    except (OSError, StopIteration, ValueError):
        pass
    return {
        "verified": True,
        "mutation_performed": False,
        "source": source_projection,
        "host": {
            "os": platform.system(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "memory_total_bytes": memory_total,
            "disk_free_bytes": disk.free,
        },
        "runtime": {
            **runtime_projection,
            "opensandbox_unit": opensandbox_unit,
            "gateway_unit": gateway_unit,
            "port_8080_listener_count": port_8080_count,
            "port_18043_listener_count": port_18043_count,
            "containers": _safe_probe([docker_cmd, "ps", "--format", "{{.Names}} {{.Image}}"]),
            "volumes": _safe_probe([docker_cmd, "volume", "ls", "--format", "{{.Name}}"]),
            "gateway_identity": _safe_probe(["id", "opensandbox-gateway"]),
            "active_sandbox_workloads": _safe_probe(
                [docker_cmd, "ps", "-q", "--filter", "label=ai-platform.owner=sandbox-runtime"]
            ),
        },
        "platform_environment": platform_projection,
        "gateway_configuration": gateway_projection,
        "opensandbox_server_configuration": server_projection,
        "identity_coherence": identity_projection,
        "ordinary_user_smoke_inputs": smoke_projection,
        "rollback_capacity": {
            "minimum_free_bytes": MIN_ROLLBACK_FREE_BYTES,
            "observed_free_bytes": disk.free,
        },
        "compose_files": list(COMPOSE_FILES),
    }


@contextlib.contextmanager
def mutation_lease(path: Path = MUTATION_LEASE_PATH) -> Iterator[None]:
    if os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise S72ColocationError("s72 mutation authority requires root on POSIX")
    import fcntl

    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise S72ColocationError("s72 mutation lease ownership drifted")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise S72ColocationError("s72 mutation lease is already held") from exc
        yield
    finally:
        os.close(descriptor)


def _unit_state(unit: str) -> tuple[bool, bool]:
    enabled = _command(["systemctl", "is-enabled", "--quiet", unit], check=False).returncode == 0
    active = _command(["systemctl", "is-active", "--quiet", unit], check=False).returncode == 0
    return enabled, active


def _inspect_network(docker_cmd: str) -> dict[str, Any] | None:
    result = _command([docker_cmd, "network", "inspect", OPENSANDBOX_NETWORK_NAME], check=False)
    if result.returncode != 0:
        return None
    try:
        values = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise S72ColocationError("OpenSandbox lifecycle network evidence is invalid") from exc
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
        raise S72ColocationError("OpenSandbox lifecycle network evidence is invalid")
    return values[0]


def _ensure_lifecycle_network(docker_cmd: str) -> bool:
    existing = _inspect_network(docker_cmd)
    if existing is not None:
        labels = existing.get("Labels") or {}
        if (
            existing.get("Internal") is not True
            or labels.get("ai-platform.owner") != "s72-colocation-authority"
            or labels.get("ai-platform.security-domain") != "opensandbox-lifecycle"
        ):
            raise S72ColocationError("OpenSandbox lifecycle network ownership drifted")
        return False
    _command(
        [
            docker_cmd,
            "network",
            "create",
            "--internal",
            "--label",
            "ai-platform.owner=s72-colocation-authority",
            "--label",
            "ai-platform.security-domain=opensandbox-lifecycle",
            OPENSANDBOX_NETWORK_NAME,
        ]
    )
    return True


def _wait_http_health(url: str, *, timeout_seconds: float = 30.0) -> None:
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with opener.open(Request(url, headers={"User-Agent": "ai-platform-s72-authority"}), timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            pass
        time.sleep(0.5)
    raise S72ColocationError("s72 runtime health convergence failed")


def _restore_opensandbox_runtime(
    snapshot: Mapping[str, object],
    *,
    docker_cmd: str,
) -> dict[str, object]:
    rollback_errors: list[str] = []
    _command(["systemctl", "stop", "opensandbox.service"], check=False)
    prior_unit = snapshot.get("unit_bytes")
    try:
        if isinstance(prior_unit, bytes):
            temporary = OPENSANDBOX_UNIT_PATH.with_name(f".{OPENSANDBOX_UNIT_PATH.name}.rollback")
            temporary.write_bytes(prior_unit)
            os.chmod(temporary, 0o644)
            os.replace(temporary, OPENSANDBOX_UNIT_PATH)
        elif OPENSANDBOX_UNIT_PATH.exists():
            OPENSANDBOX_UNIT_PATH.unlink()
    except OSError:
        rollback_errors.append("unit")
    if _command(["systemctl", "daemon-reload"], check=False).returncode != 0:
        rollback_errors.append("daemon-reload")
    if snapshot.get("unit_enabled") is True:
        if _command(["systemctl", "enable", "opensandbox.service"], check=False).returncode != 0:
            rollback_errors.append("enable")
    else:
        _command(["systemctl", "disable", "opensandbox.service"], check=False)
        if _command(["systemctl", "is-enabled", "--quiet", "opensandbox.service"], check=False).returncode == 0:
            rollback_errors.append("disable")
    if snapshot.get("unit_active") is True:
        if _command(["systemctl", "restart", "opensandbox.service"], check=False).returncode != 0:
            rollback_errors.append("restart")
        else:
            try:
                _wait_http_health("http://127.0.0.1:8080/health", timeout_seconds=15)
            except S72ColocationError:
                rollback_errors.append("health")
    elif _command([docker_cmd, "inspect", OPENSANDBOX_CONTAINER_NAME], check=False).returncode == 0:
        rollback_errors.append("container")
    if snapshot.get("network_created") is True:
        if _command([docker_cmd, "network", "rm", OPENSANDBOX_NETWORK_NAME], check=False).returncode != 0:
            rollback_errors.append("network")
    if snapshot.get("image_pulled") is True:
        image = str(snapshot.get("server_image") or "")
        image_present = bool(image) and _command(
            [docker_cmd, "image", "inspect", image], check=False
        ).returncode == 0
        if image_present and _command([docker_cmd, "image", "rm", image], check=False).returncode != 0:
            rollback_errors.append("image")
    if rollback_errors:
        raise S72ColocationError("OpenSandbox runtime rollback failed")
    return {
        "restored": True,
        "prior_unit_present": isinstance(prior_unit, bytes),
        "prior_unit_active": snapshot.get("unit_active") is True,
        "new_state_preserved_for_recovery": snapshot.get("state_preexisting") is False,
    }


def _install_opensandbox_runtime(
    checkout: Path,
    commit: str,
    *,
    docker_cmd: str,
    server_config_root: Path,
) -> dict[str, object]:
    server_projection = validate_opensandbox_server_configuration(server_config_root)
    server_values = _parse_env_file(server_config_root / "server.env")
    image = server_values["OPENSANDBOX_SERVER_IMAGE"]
    unit_enabled, unit_active = _unit_state("opensandbox.service")
    unit_bytes: bytes | None = None
    if OPENSANDBOX_UNIT_PATH.exists():
        metadata = _regular_file_metadata(
            OPENSANDBOX_UNIT_PATH,
            error="existing OpenSandbox unit is not a regular file",
        )
        if metadata.st_size > 262_144:
            raise S72ColocationError("existing OpenSandbox unit is too large to snapshot")
        unit_bytes = OPENSANDBOX_UNIT_PATH.read_bytes()
    image_local = _command([docker_cmd, "image", "inspect", image], check=False).returncode == 0
    snapshot: dict[str, object] = {
        "unit_bytes": unit_bytes,
        "unit_enabled": unit_enabled,
        "unit_active": unit_active,
        "network_created": False,
        "image_pulled": False,
        "server_image": image,
        "state_preexisting": OPENSANDBOX_STATE_ROOT.exists(),
    }
    try:
        if not image_local:
            _command([docker_cmd, "pull", image], timeout=900)
            snapshot["image_pulled"] = True
        inspected_image = _command([docker_cmd, "image", "inspect", image])
        try:
            image_record = json.loads(inspected_image.stdout)[0]
        except (IndexError, TypeError, json.JSONDecodeError) as exc:
            raise S72ColocationError("OpenSandbox server image evidence is invalid") from exc
        repo_digests = {str(value).rsplit("@", 1)[-1] for value in image_record.get("RepoDigests") or []}
        if server_projection["server_image_digest"] not in repo_digests:
            raise S72ColocationError("OpenSandbox server image digest evidence drifted")
        snapshot["network_created"] = _ensure_lifecycle_network(docker_cmd)
        if OPENSANDBOX_STATE_ROOT.exists():
            metadata = OPENSANDBOX_STATE_ROOT.stat(follow_symlinks=False)
            if (
                OPENSANDBOX_STATE_ROOT.is_symlink()
                or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != server_projection["server_uid"]
                or metadata.st_gid != server_projection["server_gid"]
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise S72ColocationError("OpenSandbox state ownership drifted")
        else:
            OPENSANDBOX_STATE_ROOT.mkdir(parents=True, mode=0o700)
            os.chown(
                OPENSANDBOX_STATE_ROOT,
                int(server_projection["server_uid"]),
                int(server_projection["server_gid"]),
            )
        unit_source = checkout / OPENSANDBOX_UNIT_RELATIVE_PATH
        metadata = _regular_file_metadata(unit_source, error="OpenSandbox unit source is unavailable")
        if metadata.st_size > 262_144:
            raise S72ColocationError("OpenSandbox unit source is invalid")
        template = unit_source.read_text(encoding="utf-8")
        if template.count("@@SOURCE_COMMIT@@") != 1:
            raise S72ColocationError("OpenSandbox unit provenance template is invalid")
        rendered = template.replace("@@SOURCE_COMMIT@@", commit)
        temporary = OPENSANDBOX_UNIT_PATH.with_name(f".{OPENSANDBOX_UNIT_PATH.name}.{os.getpid()}.tmp")
        temporary.write_text(rendered, encoding="utf-8")
        os.chmod(temporary, 0o644)
        os.replace(temporary, OPENSANDBOX_UNIT_PATH)
        _command(["systemctl", "daemon-reload"])
        _command(["systemctl", "enable", "opensandbox.service"])
        _command(["systemctl", "restart", "opensandbox.service"], timeout=120)
        _wait_http_health("http://127.0.0.1:8080/health")
        return {"snapshot": snapshot, "server_configuration": server_projection}
    except BaseException:
        try:
            _restore_opensandbox_runtime(snapshot, docker_cmd=docker_cmd)
        except BaseException:
            raise S72ColocationError(
                "OpenSandbox runtime installation failed and rollback failed"
            ) from None
        raise S72ColocationError(
            "OpenSandbox runtime installation failed and rollback completed"
        ) from None


def _current_platform_commit(docker_cmd: str, target_checkout: Path) -> str | None:
    present = [
        _command([docker_cmd, "inspect", name], check=False).returncode == 0
        for name in MANAGED_CONTAINER_NAMES
    ]
    if not any(present):
        return None
    if not all(present):
        raise S72ColocationError("current platform provenance is incomplete")
    target_selection = release_authority.resolve_compose_files(target_checkout, COMPOSE_FILES)
    runtime = release_authority._verified_current_runtime(
        [docker_cmd],
        target_selection,
        docker_cmd=docker_cmd,
    )
    return str(runtime["commit"])


def collect_opensandbox_runtime_parity(
    docker_cmd: str,
    commit: str,
    server_config_root: Path = SERVER_CONFIG_ROOT,
) -> dict[str, object]:
    values = _parse_env_file(server_config_root / "server.env")
    record = _docker_record(
        docker_cmd,
        OPENSANDBOX_CONTAINER_NAME,
        error="OpenSandbox runtime evidence is invalid",
    )
    try:
        config = record["Config"]
        host = record["HostConfig"]
        labels = config["Labels"]
        bindings = host["PortBindings"]["8080/tcp"]
    except (KeyError, TypeError) as exc:
        raise S72ColocationError("OpenSandbox runtime evidence is invalid") from exc
    expected_user = f"{values['OPENSANDBOX_SERVER_UID']}:{values['OPENSANDBOX_SERVER_GID']}"
    cap_drop = {str(value).upper() for value in host.get("CapDrop") or []}
    if (
        labels.get("ai-platform.source-commit") != commit
        or labels.get("ai-platform.release-owner") != "s72-colocation-authority"
        or labels.get("ai-platform.release-role") != "opensandbox-server"
        or labels.get("ai-platform.security-domain") != "execution-controller"
        or str(config.get("Image") or "") != values["OPENSANDBOX_SERVER_IMAGE"]
        or str(config.get("User") or "") != expected_user
        or host.get("NetworkMode") != OPENSANDBOX_NETWORK_NAME
        or host.get("Privileged") is True
        or host.get("ReadonlyRootfs") is not True
        or "ALL" not in cap_drop
        or not _has_no_new_privileges(host)
        or bindings != [{"HostIp": "127.0.0.1", "HostPort": "8080"}]
    ):
        raise S72ColocationError("OpenSandbox runtime boundary drifted")
    expected_mounts = {
        ("/var/run/docker.sock", "/var/run/docker.sock", False),
        (str((server_config_root / "server.toml").resolve()), "/etc/opensandbox/config.toml", False),
        (str(OPENSANDBOX_STATE_ROOT), "/var/lib/ai-platform-opensandbox", True),
    }
    if _bind_mounts(record) != expected_mounts:
        raise S72ColocationError("OpenSandbox controller mount boundary drifted")
    network = _inspect_network(docker_cmd)
    if network is None or network.get("Internal") is not True:
        raise S72ColocationError("OpenSandbox lifecycle network parity failed")
    if _command(["systemctl", "is-active", "--quiet", "opensandbox.service"], check=False).returncode != 0:
        raise S72ColocationError("OpenSandbox unit is not active")
    if _command(["systemctl", "is-active", "--quiet", "opensandbox-gateway.service"], check=False).returncode != 0:
        raise S72ColocationError("OpenSandbox gateway unit is not active")
    _wait_http_health("http://127.0.0.1:8080/health", timeout_seconds=5)
    return {
        "verified": True,
        "container": OPENSANDBOX_CONTAINER_NAME,
        "server_image_digest": values["OPENSANDBOX_SERVER_IMAGE_DIGEST"],
        "host_binding": "127.0.0.1:8080",
        "lifecycle_network_internal": True,
        "controller_docker_socket": True,
        "executor_docker_socket": False,
    }


def collect_colocation_parity(
    docker_cmd: str,
    commit: str,
    server_config_root: Path = SERVER_CONFIG_ROOT,
) -> dict[str, object]:
    opensandbox_runtime = collect_opensandbox_runtime_parity(
        docker_cmd,
        commit,
        server_config_root,
    )
    record = _docker_record(
        docker_cmd,
        "ai-platform-s72-broker-entry",
        error="s72 broker runtime evidence is invalid",
    )
    try:
        config = record["Config"]
        host = record["HostConfig"]
        labels = config["Labels"]
        bindings = host["PortBindings"]["8080/tcp"]
        attached_networks = set(record["NetworkSettings"]["Networks"])
    except (KeyError, TypeError) as exc:
        raise S72ColocationError("s72 broker runtime evidence is invalid") from exc
    if (
        labels.get("ai-platform.source-commit") != commit
        or labels.get("ai-platform.release-owner") != "repo-local-compose"
        or labels.get("ai-platform.release-role") != "s72-broker-entry"
        or labels.get("ai-platform.security-domain") != "control-plane-broker"
        or str(config.get("User") or "") != "101:101"
        or str(host.get("NetworkMode") or "") == "host"
        or host.get("Privileged") is True
        or host.get("ReadonlyRootfs") is not True
        or "ALL" not in {str(value).upper() for value in host.get("CapDrop") or []}
        or not _has_no_new_privileges(host)
        or bindings != [{"HostIp": "127.0.0.1", "HostPort": "18043"}]
        or attached_networks
        != {
            f"{release_authority.COMPOSE_PROJECT}_s72_callback",
            f"{release_authority.COMPOSE_PROJECT}_s72_model_host",
        }
    ):
        raise S72ColocationError("s72 broker runtime boundary drifted")
    forbidden = ("docker.sock", "ai_platform_postgres", "ai_platform_redis", "ai_platform_minio")
    mounts = record.get("Mounts") or []
    working_dir = str(labels.get("com.docker.compose.project.working_dir") or "")
    expected_template_source = f"{working_dir}/s72-broker-nginx.conf.template"
    if (
        not working_dir.replace("\\", "/").endswith(
            f"/{commit}/deploy/ai-platform"
        )
        or _bind_mounts(record)
        != {
            (expected_template_source, "/etc/nginx/templates-s72-colocation/default.conf.template", False)
        }
        or any(item.get("Type") == "volume" for item in mounts)
        or set((host.get("Tmpfs") or {}).keys())
        != {"/etc/nginx/conf.d", "/var/cache/nginx", "/var/run"}
        or any(any(marker in str(item.get("Source") or "") for marker in forbidden) for item in mounts)
    ):
        raise S72ColocationError("s72 broker runtime mount boundary drifted")
    _wait_http_health("http://127.0.0.1:18043/healthz", timeout_seconds=5)

    sandbox_list = _command(
        [docker_cmd, "ps", "-q", "--filter", "label=ai-platform.owner=sandbox-runtime"]
    )
    sandbox_ids = [
        line.strip()
        for line in sandbox_list.stdout.splitlines()
        if line.strip()
    ]
    if any(not re.fullmatch(r"[0-9a-f]{64}", sandbox_id) for sandbox_id in sandbox_ids):
        raise S72ColocationError("sandbox runtime inventory is invalid")
    for sandbox_id in sandbox_ids:
        sandbox = _docker_record(
            docker_cmd,
            sandbox_id,
            error="sandbox runtime evidence is invalid",
        )
        try:
            sandbox_host = sandbox["HostConfig"]
            sandbox_config = sandbox["Config"]
        except (KeyError, TypeError) as exc:
            raise S72ColocationError("sandbox runtime evidence is invalid") from exc
        sandbox_mounts = sandbox.get("Mounts") or []
        if (
            sandbox_host.get("Runtime") != "runsc"
            or sandbox_host.get("NetworkMode") != "none"
            or sandbox_host.get("Privileged") is True
            or not _has_no_new_privileges(sandbox_host)
            or not DANGEROUS_CAPABILITIES.issubset(
                {str(value).upper() for value in sandbox_host.get("CapDrop") or []}
            )
            or sandbox_config.get("User") != "1000:1000"
            or len(sandbox_mounts) > 2
            or any(
                not str(item.get("Source") or "").startswith("/data/opensandbox/workspaces/")
                or any(marker in str(item.get("Source") or "") for marker in forbidden)
                for item in sandbox_mounts
            )
        ):
            raise S72ColocationError("sandbox runtime isolation drifted")
    return {
        "verified": True,
        "broker_host_binding": "127.0.0.1:18043",
        "active_sandbox_count": len(sandbox_ids),
        "active_sandboxes_isolated": True,
        "opensandbox_runtime": opensandbox_runtime,
    }


def _gateway_install(checkout: Path, commit: str, evidence_id: str) -> None:
    env = dict(os.environ)
    env.update({
        "OPENSANDBOX_GATEWAY_AUTHORITY_REF": "origin/main",
        "OPENSANDBOX_GATEWAY_EXPECTED_AUTHORITY_SHA": commit,
        "OPENSANDBOX_GATEWAY_AUTHORITY_EVIDENCE_ID": evidence_id,
    })
    _command(
        [str(checkout / "deploy/opensandbox/install-s72.sh"), str(checkout)],
        env=env,
        timeout=900,
    )


def _load_smoke_accounts(path: Path) -> list[str]:
    metadata = _regular_file_metadata(path, error="ordinary-user smoke account file is unavailable")
    if os.name == "posix" and (
        metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise S72ColocationError("ordinary-user smoke account file ownership drifted")
    if metadata.st_size > 65_536:
        raise S72ColocationError("ordinary-user smoke account file is invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise S72ColocationError("ordinary-user smoke account file is invalid") from exc
    if (
        not isinstance(value, list)
        or not 2 <= len(value) <= 16
        or any(not isinstance(item, str) or not 8 <= len(item) <= 1024 for item in value)
    ):
        raise S72ColocationError("ordinary-user smoke account file is invalid")
    return list(value)


def validate_smoke_inputs(
    accounts_path: Path,
    sample_docx: Path,
    evidence_id: str,
) -> dict[str, object]:
    _validate_authority_evidence_id(evidence_id)
    accounts = _load_smoke_accounts(accounts_path)
    tenants: set[str] = set()
    for account in accounts:
        label_part, separator, credentials = account.partition("=")
        tenant, slash, label = label_part.partition("/")
        username, colon, password = credentials.partition(":")
        fields = (tenant, label, username, password)
        if (
            not separator
            or not slash
            or not colon
            or any(not value or len(value) > 512 for value in fields)
            or any(any(ord(character) < 0x20 or ord(character) == 0x7F for character in value) for value in fields)
        ):
            raise S72ColocationError("ordinary-user smoke account file is invalid")
        tenants.add(tenant)
    if len(tenants) < 2:
        raise S72ColocationError("ordinary-user smoke requires at least two tenants")
    metadata = _regular_file_metadata(sample_docx, error="ordinary-user smoke document is unavailable")
    if (
        sample_docx.suffix.lower() != ".docx"
        or metadata.st_size <= 0
        or metadata.st_size > 50 * 1024 * 1024
        or not zipfile.is_zipfile(sample_docx)
    ):
        raise S72ColocationError("ordinary-user smoke document is invalid")
    for directory in (RUNTIME_STATE_ROOT, RUNTIME_STATE_ROOT / "evidence"):
        _root_state_directory(directory, create=False)
    evidence_path = RUNTIME_STATE_ROOT / "evidence" / f"{evidence_id}.ordinary-user-smoke.json"
    if evidence_path.exists():
        raise S72ColocationError("ordinary-user runtime smoke evidence id already exists")
    return {
        "account_count": len(accounts),
        "tenant_count": len(tenants),
        "sample_docx_bytes": metadata.st_size,
        "evidence_id_available": True,
    }


def _root_state_directory(path: Path, *, create: bool) -> None:
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        if not create:
            return
        path.mkdir(parents=True, mode=0o700)
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise S72ColocationError("s72 authority state is unavailable") from exc
    if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode) or (
        os.name == "posix"
        and (metadata.st_uid != 0 or metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) != 0o700)
    ):
        raise S72ColocationError("s72 authority state ownership drifted")
    if create:
        os.chmod(path, 0o700)


def _ordinary_user_runtime_smoke(
    checkout: Path,
    commit: str,
    platform_env_path: Path,
    accounts_path: Path,
    sample_docx: Path,
    evidence_id: str,
) -> dict[str, object]:
    smoke_projection = validate_smoke_inputs(accounts_path, sample_docx, evidence_id)
    accounts = _load_smoke_accounts(accounts_path)
    account_count = int(smoke_projection["account_count"])
    values = _parse_env_file(platform_env_path)
    port = values.get("AI_PLATFORM_FRONTEND_PORT", "")
    if not re.fullmatch(r"[1-9][0-9]{0,4}", port) or int(port) > 65535:
        raise S72ColocationError("ordinary-user smoke frontend authority is invalid")
    try:
        from tools import verify_multiuser_poc
    except ImportError as exc:
        raise S72ColocationError("ordinary-user smoke authority is unavailable") from exc
    argv = [
        str(checkout / "tools/verify_multiuser_poc.py"),
        "--api-url", f"http://127.0.0.1:{port}",
        "--sample-docx", str(sample_docx),
        "--auth-mode", "login",
        "--trusted-header-role", "developer",
        "--foundation-runtime-evidence",
        "--min-concurrent-cases", "12",
        "--commit-sha", commit,
        "--runtime-subject-commit-sha", commit,
    ]
    for account in accounts:
        argv.extend(["--account", account])
    previous_argv = sys.argv
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        sys.argv = argv
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = verify_multiuser_poc.main()
    except SystemExit as exc:
        status = int(exc.code) if isinstance(exc.code, int) else 1
    finally:
        sys.argv = previous_argv
        for index in range(len(argv)):
            argv[index] = ""
        for index in range(len(accounts)):
            accounts[index] = ""
    raw = stdout.getvalue()
    if status != 0:
        raise S72ColocationError("ordinary-user runtime smoke failed")
    try:
        evidence = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise S72ColocationError("ordinary-user runtime smoke evidence is invalid") from exc
    if (
        not isinstance(evidence, dict)
        or evidence.get("commit_sha") != commit
        or evidence.get("runtime_subject_commit_sha") != commit
    ):
        raise S72ColocationError("ordinary-user runtime smoke provenance drifted")
    checks = evidence.get("checks") if isinstance(evidence.get("checks"), dict) else {}
    skill = checks.get("skill_snapshots") if isinstance(checks.get("skill_snapshots"), dict) else {}
    role = evidence.get("role_provenance") if isinstance(evidence.get("role_provenance"), dict) else {}
    if (
        role.get("run_creation_role") != "developer"
        or skill.get("status") != "passed"
        or not isinstance(skill.get("used_count"), int)
        or skill["used_count"] < 1
    ):
        raise S72ColocationError("ordinary-user SDK Skill smoke evidence is incomplete")
    _root_state_directory(RUNTIME_STATE_ROOT, create=True)
    evidence_root = RUNTIME_STATE_ROOT / "evidence"
    _root_state_directory(evidence_root, create=True)
    evidence_path = evidence_root / f"{evidence_id}.ordinary-user-smoke.json"
    if evidence_path.exists():
        raise S72ColocationError("ordinary-user runtime smoke evidence id already exists")
    encoded = raw.encode("utf-8")
    temporary = evidence_path.with_name(f".{evidence_path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(encoded)
    os.chmod(temporary, 0o600)
    os.replace(temporary, evidence_path)
    return {
        "verified": True,
        "evidence_path": str(evidence_path),
        "evidence_sha256": hashlib.sha256(encoded).hexdigest(),
        "evidence_bytes": len(encoded),
        "account_count": account_count,
        "tenant_count": smoke_projection["tenant_count"],
        "used_skill_snapshot_count": skill["used_count"],
    }


def _compose_down(checkout: Path, env_file: Path, docker_cmd: str) -> None:
    selection = release_authority.resolve_compose_files(checkout, COMPOSE_FILES)
    command = [docker_cmd, "compose", "-p", release_authority.COMPOSE_PROJECT, "--env-file", str(env_file)]
    for path in selection.absolute_paths:
        command.extend(["-f", str(path)])
    command.extend(["down", "--remove-orphans"])
    _command(command, cwd=Path(selection.working_dir), timeout=300)


def _restore_platform(
    release_root: Path,
    prior_commit: str | None,
    target_checkout: Path,
    env_file: Path,
    docker_cmd: str,
) -> dict[str, object]:
    if prior_commit is None:
        _compose_down(target_checkout, env_file, docker_cmd)
        return {"mode": "remove-new-runtime", "commit": None}
    restored = release_authority.deploy_main_commit(
        release_root,
        prior_commit,
        docker_cmd=docker_cmd,
        env_file=env_file,
        replace_known_manual_frontend=False,
        compose_files=COMPOSE_FILES,
        strategy="canonical",
    )
    parity = restored.get("parity") if isinstance(restored.get("parity"), dict) else {}
    if parity.get("verified") is not True:
        raise S72ColocationError("platform rollback parity failed")
    return {"mode": "restore-exact-commit", "commit": prior_commit}


def deploy_main_commit(
    source: Path,
    commit: str,
    release_root: Path,
    *,
    authority_evidence_id: str,
    smoke_accounts_file: Path,
    smoke_sample_docx: Path,
    docker_cmd: str = "docker",
    gateway_config_root: Path = GATEWAY_CONFIG_ROOT,
    server_config_root: Path = SERVER_CONFIG_ROOT,
    lease_path: Path = MUTATION_LEASE_PATH,
) -> dict[str, object]:
    _validate_authority_evidence_id(authority_evidence_id)
    with mutation_lease(lease_path):
        preflight = collect_read_only_preflight(
            source,
            commit,
            release_root,
            smoke_accounts_file,
            smoke_sample_docx,
            authority_evidence_id,
            docker_cmd=docker_cmd,
            gateway_config_root=gateway_config_root,
            server_config_root=server_config_root,
        )
        env_file = release_authority.resolve_managed_env_file(release_root, None)
        checkout = release_authority.materialize_main_checkout(release_root, commit)
        prior_commit = _current_platform_commit(docker_cmd, checkout)
        runtime_install: dict[str, object] | None = None
        gateway_installed = False
        try:
            runtime_install = _install_opensandbox_runtime(
                checkout,
                commit,
                docker_cmd=docker_cmd,
                server_config_root=server_config_root,
            )
            _gateway_install(checkout, commit, authority_evidence_id)
            gateway_installed = True
            deployment = release_authority.deploy_main_commit(
                release_root,
                commit,
                docker_cmd=docker_cmd,
                env_file=env_file,
                replace_known_manual_frontend=False,
                compose_files=COMPOSE_FILES,
                strategy="canonical",
                coordination_source=source,
            )
            colocation_parity = collect_colocation_parity(docker_cmd, commit, server_config_root)
            ordinary_user_smoke = _ordinary_user_runtime_smoke(
                checkout,
                commit,
                env_file,
                smoke_accounts_file,
                smoke_sample_docx,
                authority_evidence_id,
            )
            post_smoke_parity = collect_colocation_parity(docker_cmd, commit, server_config_root)
            return {
                "verified": True,
                "commit": commit,
                "compose_files": list(COMPOSE_FILES),
                "gateway_authority_evidence_id": authority_evidence_id,
                "preflight": preflight,
                "deployment": deployment,
                "colocation_parity": colocation_parity,
                "ordinary_user_smoke": ordinary_user_smoke,
                "post_smoke_parity": post_smoke_parity,
                "rollback": {"required": False},
            }
        except BaseException as primary:
            rollback: dict[str, object] = {
                "required": True,
                "platform": None,
                "gateway": None,
                "opensandbox_runtime": None,
            }
            rollback_errors: list[str] = []
            rollback_steps = [
                ("platform", gateway_installed, lambda: _restore_platform(release_root, prior_commit, checkout, env_file, docker_cmd)),
                (
                    "gateway",
                    gateway_installed,
                    lambda: {
                        "restored": _command(
                            [str(checkout / "deploy/opensandbox/rollback-s72.sh")], timeout=300
                        ) is not None
                    },
                ),
                ("opensandbox_runtime", runtime_install is not None, lambda: _restore_opensandbox_runtime(runtime_install["snapshot"], docker_cmd=docker_cmd)),
            ]
            for step, required, action in rollback_steps:
                if not required:
                    continue
                try:
                    rollback[step] = action()
                except BaseException:
                    rollback_errors.append(step)
            error = S72ColocationError(
                "s72 deployment failed and rollback failed"
                if rollback_errors
                else "s72 deployment failed and authoritative rollback completed"
            )
            error.rollback = rollback
            error.primary_type = type(primary).__name__
            raise error from None


def _write_json(value: Mapping[str, object]) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "deploy-main-commit"):
        command = subparsers.add_parser(name)
        command.add_argument("--coordination-source", type=Path, required=True)
        command.add_argument("--commit", required=True)
        command.add_argument("--release-root", type=Path, required=True)
        command.add_argument("--docker-cmd", default="docker")
        command.add_argument("--gateway-config-root", type=Path, default=GATEWAY_CONFIG_ROOT)
        command.add_argument("--server-config-root", type=Path, default=SERVER_CONFIG_ROOT)
        command.add_argument("--authority-evidence-id", required=True)
        command.add_argument("--smoke-accounts-file", type=Path, required=True)
        command.add_argument("--smoke-sample-docx", type=Path, required=True)
    args = parser.parse_args()
    try:
        operation = collect_read_only_preflight if args.command == "preflight" else deploy_main_commit
        result = operation(
            source=args.coordination_source,
            commit=args.commit,
            release_root=args.release_root,
            authority_evidence_id=args.authority_evidence_id,
            smoke_accounts_file=args.smoke_accounts_file,
            smoke_sample_docx=args.smoke_sample_docx,
            docker_cmd=args.docker_cmd,
            gateway_config_root=args.gateway_config_root,
            server_config_root=args.server_config_root,
        )
        _write_json(result)
        return 0
    except (S72ColocationError, release_authority.ReleaseAuthorityError) as exc:
        payload: dict[str, object] = {
            "verified": False,
            "command": args.command,
            "error": str(exc),
        }
        rollback = getattr(exc, "rollback", None)
        if isinstance(rollback, dict):
            payload["rollback"] = rollback
        _write_json(payload)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
