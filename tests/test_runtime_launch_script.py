from __future__ import annotations

import os
import json
import re
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time

import pytest
import yaml


yaml.SafeLoader.add_constructor(
    "!reset", lambda loader, node: loader.construct_sequence(node, deep=True)
)


DEPLOY_DIR = Path("deploy/ai-platform")
COMPOSE_FILE = DEPLOY_DIR / "docker-compose.yml"
SANDBOX_COMPOSE_FILE = DEPLOY_DIR / "docker-compose.sandbox.yml"
OPENSANDBOX_COMPOSE_FILE = DEPLOY_DIR / "docker-compose.opensandbox.yml"
OPENSANDBOX_EGRESS_TEMPLATE = DEPLOY_DIR / "opensandbox-egress-nginx.conf.template"
OPENSANDBOX_NETWORK_GUARD_SERVICE = Path(
    "deploy/opensandbox/ai-platform-opensandbox-network-guard.service"
)
OPENSANDBOX_PRODUCTION_SERVICE = Path(
    "deploy/opensandbox/opensandbox-production.service"
)
ENV_EXAMPLE_FILE = DEPLOY_DIR / ".env.example"
OPENSANDBOX_GUARD_TOPOLOGY = {
    "OPENSANDBOX_EXPECTED_NETWORK_MODE": "ai-platform-opensandbox-egress-v2",
    "OPENSANDBOX_EGRESS_BRIDGE": "br-osb-egress2",
    "OPENSANDBOX_EGRESS_SUBNET": "172.31.76.0/24",
    "OPENSANDBOX_EGRESS_PROXY_IPV4": "172.31.76.2",
}


def expand_network_guard_command(
    raw_command: list[str], topology: dict[str, str], *, iptables_dir: Path | None = None
) -> tuple[list[str], bool]:
    """Model systemd's known EnvironmentFile expansion for the guard fixture."""
    command = list(raw_command)
    ignore_failure = command[0].startswith("-")
    command[0] = command[0].removeprefix("-")
    if command[0] == "/bin/sh":
        script = command[2].replace("$$", "$")
        for name, value in topology.items():
            script = script.replace(f"${{{name}}}", value)
        if iptables_dir is not None:
            for family in ("iptables", "ip6tables"):
                script = script.replace(
                    f"/usr/sbin/{family}", shlex.quote(str(iptables_dir / family))
                )
        command[2] = script
    for index, argument in enumerate(command):
        for name, value in topology.items():
            argument = argument.replace(f"${{{name}}}", value)
        command[index] = argument
    if iptables_dir is not None and command[0] in {"/usr/sbin/iptables", "/usr/sbin/ip6tables"}:
        command[0] = str(iptables_dir / Path(command[0]).name)
    return command, ignore_failure


def compose_service_text(compose_text: str, service_name: str) -> str:
    marker = f"\n  {service_name}:"
    section = compose_text.split(marker, 1)[1]
    for next_marker in ("\n  worker:", "\nvolumes:"):
        if next_marker in section:
            section = section.split(next_marker, 1)[0]
    return section


def env_example_values(env_example_text: str) -> dict[str, str]:
    return {
        name: value
        for line in env_example_text.splitlines()
        if line and not line.startswith("#") and "=" in line
        for name, _, value in (line.partition("="),)
    }


def test_company_auth_requires_operator_managed_endpoints_and_api_only_jwt_verification():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    env_example_text = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")
    env_values = env_example_values(env_example_text)

    for service_name in ("api", "worker"):
        service = compose_service_text(compose_text, service_name)
        assert "EXISTING_AUTH_BASE_URL: ${EXISTING_AUTH_BASE_URL:?set EXISTING_AUTH_BASE_URL}" in service
        assert "EXISTING_USER_INFO_BASE_URL: ${EXISTING_USER_INFO_BASE_URL:?set EXISTING_USER_INFO_BASE_URL}" in service
    api_service = compose_service_text(compose_text, "api")
    worker_service = compose_service_text(compose_text, "worker")
    for name in (
        "COMPANY_LOGIN_JWT_SECRET",
        "COMPANY_LOGIN_JWT_ISSUER",
        "COMPANY_LOGIN_JWT_AUDIENCE",
    ):
        assert f"{name}: ${{{name}:?set {name}}}" in api_service
        assert name not in worker_service
        assert name in env_values
    assert env_values["COMPANY_LOGIN_JWT_SECRET"] == ""
    assert env_values["COMPANY_LOGIN_JWT_ISSUER"]
    assert env_values["COMPANY_LOGIN_JWT_AUDIENCE"]
    assert "10.56.0.25" not in compose_text
    assert env_values["EXISTING_AUTH_BASE_URL"] == "http://10.56.0.25:7263"
    assert env_values["EXISTING_USER_INFO_BASE_URL"] == "http://10.56.0.25:5166"


def test_compose_projects_all_browser_authentication_windows_as_twenty_four_hours():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")

    for service_name in ("api", "worker"):
        service = compose_service_text(compose_text, service_name)
        assert 'AI_SESSION_MAX_AGE_SECONDS: "86400"' in service
        assert 'AUTH_CONTEXT_MAX_AGE_SECONDS: "86400"' in service
        assert 'COMPANY_AUTHORITY_FRESHNESS_SECONDS: "86400"' in service
    env_values = env_example_values(ENV_EXAMPLE_FILE.read_text(encoding="utf-8"))
    assert "AI_SESSION_MAX_AGE_SECONDS" not in env_values
    assert "AUTH_CONTEXT_MAX_AGE_SECONDS" not in env_values
    assert "COMPANY_AUTHORITY_FRESHNESS_SECONDS" not in env_values


def test_production_cors_origin_is_operator_managed_and_browser_visible():
    settings_text = Path("app/settings.py").read_text(encoding="utf-8")
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    env_example_text = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")
    env_values = env_example_values(env_example_text)
    retired_origin = "http://10.56.0.211:18001"

    assert retired_origin not in settings_text
    assert retired_origin not in compose_text
    for service_name in ("api", "worker"):
        service = compose_service_text(compose_text, service_name)
        assert (
            "CORS_ALLOW_ORIGINS: ${CORS_ALLOW_ORIGINS:?set CORS_ALLOW_ORIGINS "
            "to the browser-visible frontend origin}"
        ) in service
    assert env_values["CORS_ALLOW_ORIGINS"] == "https://ai-platform.example.internal"
    assert "localhost" not in env_values["CORS_ALLOW_ORIGINS"]
    assert retired_origin not in env_values["CORS_ALLOW_ORIGINS"]
    runbook = Path("docs/operations/release-operations-runbook.md").read_text(encoding="utf-8")
    assert "browser-visible frontend origin" in runbook
    assert "defaults to `18001`" in runbook


def test_worker_is_default_required_service_in_compose():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    worker_section = compose_text.split("\n  worker:", 1)[1].split("\nvolumes:", 1)[0]

    assert "container_name: ai-platform-worker" in worker_section
    assert "profiles:" not in worker_section


def test_worker_compose_forwards_memory_retention_cleanup_settings():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    worker_section = compose_text.split("\n  worker:", 1)[1].split("\nvolumes:", 1)[0]
    env_example_text = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")

    expected_settings = {
        "MEMORY_RETENTION_WORKER_CLEANUP_ENABLED": "true",
        "MEMORY_RETENTION_WORKER_CLEANUP_INTERVAL_SECONDS": "300",
        "MEMORY_RETENTION_WORKER_CLEANUP_LIMIT": "200",
    }
    for name, default in expected_settings.items():
        assert f"{name}={default}" in env_example_text
        assert f"{name}: ${{{name}:-{default}}}" in worker_section


def test_skill_manifest_reference_transport_has_no_rollout_switch():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    api_section = compose_service_text(compose_text, "api")
    worker_section = compose_service_text(compose_text, "worker")
    env_values = env_example_values(ENV_EXAMPLE_FILE.read_text(encoding="utf-8"))

    assert "SKILL_MANIFEST_REFERENCE_WRITES_ENABLED" not in api_section
    assert "SKILL_MANIFEST_REFERENCE_WRITES_ENABLED" not in worker_section
    assert "SKILL_MANIFEST_REFERENCE_WRITES_ENABLED" not in env_values


def test_compose_forwards_database_pool_settings_to_api_and_worker():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    env_example_text = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")
    api_section = compose_text.split("\n  api:", 1)[1].split("\n  worker:", 1)[0]
    worker_section = compose_text.split("\n  worker:", 1)[1].split("\nvolumes:", 1)[0]

    expected_settings = {
        "DATABASE_POOL_MIN_SIZE": "1",
        "DATABASE_POOL_MAX_SIZE": "10",
        "DATABASE_POOL_TIMEOUT_SECONDS": "10",
        "DATABASE_POOL_MAX_WAITING": "100",
        "DATABASE_POOL_CLOSE_TIMEOUT_SECONDS": "5",
    }
    for name, default in expected_settings.items():
        assert f"{name}={default}" in env_example_text
        assert f"{name}: ${{{name}:-{default}}}" in api_section
        assert f"{name}: ${{{name}:-{default}}}" in worker_section


def test_compose_forwards_queue_quota_settings_to_api_and_worker():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    env_example_text = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")
    api_section = compose_text.split("\n  api:", 1)[1].split("\n  worker:", 1)[0]
    worker_section = compose_text.split("\n  worker:", 1)[1].split("\nvolumes:", 1)[0]

    expected_settings = {
        "MAX_ACTIVE_WORKER_RUNS": "10",
        "QUEUE_TENANT_PROCESSING_LIMIT": "0",
        "QUEUE_USER_PROCESSING_LIMIT": "0",
        "QUEUE_LEASE_SCAN_LIMIT": "50",
        "QUEUE_INSIGHT_SCAN_LIMIT": "500",
        "QUEUE_LEASE_VISIBILITY_TIMEOUT_SECONDS": "900",
    }
    for name, default in expected_settings.items():
        assert f"{name}={default}" in env_example_text
        assert f"{name}: ${{{name}:-{default}}}" in api_section
        assert f"{name}: ${{{name}:-{default}}}" in worker_section


def test_worker_compose_forwards_worker_concurrency_setting_only_to_worker():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    env_example_text = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")
    api_section = compose_text.split("\n  api:", 1)[1].split("\n  worker:", 1)[0]
    worker_section = compose_text.split("\n  worker:", 1)[1].split("\nvolumes:", 1)[0]

    name = "WORKER_CONCURRENCY"
    default = "10"
    assert f"{name}={default}" in env_example_text
    assert f"{name}: ${{{name}:-{default}}}" in worker_section
    assert name not in api_section


def test_compose_and_example_use_sdk_execution_defaults():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    env_example_text = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")

    assert "CLAUDE_AGENT_SDK_TIMEOUT_SECONDS=0" in env_example_text
    assert (
        compose_text.count(
            "CLAUDE_AGENT_SDK_TIMEOUT_SECONDS: ${CLAUDE_AGENT_SDK_TIMEOUT_SECONDS:-0}"
        )
        == 2
    )
    assert "CLAUDE_AGENT_SDK_TIMEOUT_SECONDS:-1200}" not in compose_text
    assert "CLAUDE_AGENT_SDK_MAX_TURNS=256" in env_example_text
    assert (
        compose_text.count(
            "CLAUDE_AGENT_SDK_MAX_TURNS: ${CLAUDE_AGENT_SDK_MAX_TURNS:-256}"
        )
        == 2
    )
    assert "CLAUDE_AGENT_SDK_MAX_TURNS:-128}" not in compose_text


def test_compose_forwards_bounded_redis_pool_to_api_and_worker_without_limiting_server():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    env_example_text = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")
    api_section = compose_text.split("\n  api:", 1)[1].split("\n  worker:", 1)[0]
    worker_section = compose_text.split("\n  worker:", 1)[1].split("\nvolumes:", 1)[0]
    redis_section = compose_text.split("\n  redis:", 1)[1].split("\n  minio:", 1)[0]

    assert "REDIS_MAX_CONNECTIONS=64" in env_example_text
    assert "REDIS_MAX_CONNECTIONS: ${REDIS_MAX_CONNECTIONS:-64}" in api_section
    assert "REDIS_MAX_CONNECTIONS: ${REDIS_MAX_CONNECTIONS:-64}" in worker_section
    assert "maxclients" not in redis_section.lower()


def test_worker_compose_forwards_maintenance_interval_setting():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    env_example_text = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")
    api_section = compose_text.split("\n  api:", 1)[1].split("\n  worker:", 1)[0]
    worker_section = compose_text.split("\n  worker:", 1)[1].split("\nvolumes:", 1)[0]

    name = "WORKER_MAINTENANCE_INTERVAL_SECONDS"
    default = "30"
    assert f"{name}={default}" in env_example_text
    assert f"{name}: ${{{name}:-{default}}}" in worker_section
    assert name not in api_section


def test_poc_gate_default_env_path_is_repository_relative():
    text = Path("tools/verify_poc_gate.py").read_text(encoding="utf-8")

    assert 'REPOSITORY_ROOT = Path(__file__).resolve().parents[1]' in text
    assert 'DEFAULT_DEPLOY_ENV = str(REPOSITORY_ROOT / "deploy/ai-platform/.env")' in text
    assert "/home/" not in text


def test_poc_gate_validates_api_run_id_before_psql_interpolation():
    text = Path("tools/verify_poc_gate.py").read_text(encoding="utf-8")

    assert "from app.validation import assert_safe_id" in text
    assert "run_id = assert_safe_id(str(run_id), \"run_id\")" in text


def test_dockerfile_can_start_sandbox_executor_app():
    content = Path("Dockerfile").read_text(encoding="utf-8")

    assert "EXPOSE 8020" in content
    assert "EXPOSE 8020 18000" not in content
    assert "APP_MODULE" in content
    assert "app.runtime.sandbox.executor_app:create_executor_app" in content
    assert "APP_PORT" in content
    assert "docker-entrypoint.sh" in content
    assert 'CMD ["uvicorn"]' in content


def test_dockerfile_precreates_private_workspace_before_nonroot_executor():
    content = Path("Dockerfile").read_text(encoding="utf-8")
    workspace_instruction = "RUN install -d -o 10001 -g 10001 -m 0700 /workspace"

    assert workspace_instruction in content
    assert content.index(workspace_instruction) < content.index("USER 10001:10001")
    dependency_layer = content[content.index("apt-get update") : content.index("COPY pyproject.toml")]
    assert "/workspace" not in dependency_layer


def test_dockerfile_installs_required_runtime_packages():
    content = Path("Dockerfile").read_text(encoding="utf-8")

    assert "apt-get install -y --no-install-recommends fontconfig fonts-noto-cjk git libexpat1 libssh2-1 pandoc passwd" in content


def test_dockerfile_uses_independent_optional_debian_mirror_args_without_disabling_apt_security():
    content = Path("Dockerfile").read_text(encoding="utf-8")

    assert "ARG APT_MIRROR" in content
    assert "ARG APT_SECURITY_MIRROR" in content
    python_base = (
        "python:3.13.14-slim-bookworm@"
        "sha256:67a1e1f215ccda113cfc024e8639049257e88f273898f595b61476d128d387e8"
    )
    assert content.count(f"FROM {python_base}") == 2
    assert "http://deb.debian.org/debian-security" in content
    assert "https://deb.debian.org/debian-security" in content
    assert "http://security.debian.org/debian-security" in content
    assert "sed -i" not in content
    assert "APT_MIRROR-security" not in content
    assert "trusted=yes" not in content
    assert "allow-unauthenticated" not in content
    assert "apt-get update" in content
    assert "apt-get install" in content
    assert "PIP_TRUSTED_HOST" in content


def test_runbook_documents_ustc_pair_preflight_no_deploy_probe_and_upstream_rollback():
    text = Path("docs/operations/release-operations-runbook.md").read_text(encoding="utf-8")

    assert 'https://mirrors.ustc.edu.cn/debian"' in text
    assert 'https://mirrors.ustc.edu.cn/debian-security"' in text
    assert "probe-apt-mirrors" in text
    assert "--head" not in text
    assert "HTTPS GET" in text
    assert "does not invoke Compose" in text
    assert "sudo -n docker build" in text
    assert "--apt-mirror \"$APT_MIRROR\"" in text
    assert "--apt-security-mirror \"$APT_SECURITY_MIRROR\"" in text
    assert 'python3 -B "$SOURCE/tools/release_authority.py" probe-apt-mirrors' in text
    assert 'MIRROR_ARGS=()' in text
    assert 'if test -n "${APT_MIRROR:-}"' in text
    assert "leave both" in text
    assert "upstream Debian endpoints" in text


def test_dockerfile_packages_release_evidence_for_runtime_readiness():
    content = Path("Dockerfile").read_text(encoding="utf-8")

    assert "COPY docs/release-evidence /app/docs/release-evidence" in content
    assert "COPY tools /app/tools" in content
    assert "COPY scripts /app/scripts" in content
    assert "COPY docs /app/docs" not in content


def test_dockerfile_generates_in_container_source_authority_markers():
    content = Path("Dockerfile").read_text(encoding="utf-8")

    assert "printf '%s\\n' \"$AI_PLATFORM_BUILD_COMMIT\" > /app/.ai-platform-source-revision" in content
    assert "schema_version='ai-platform.source-snapshot.v1'" in content
    assert "source_tree_commit_sha=commit" in content
    assert "runtime_subject_commit_sha=commit" in content
    assert "dirty = dirty_text != 'false'" in content
    assert "dirty_paths = [] if not dirty else ['unknown_runtime_affecting_dirty_paths']" in content
    assert "runtime_affecting_changes_since_runtime_subject=[]" in content
    assert "runtime_affecting_dirty_paths=dirty_paths" in content
    assert "snapshot_source='dockerfile_build_args'" in content


def test_docker_entrypoint_validates_runtime_env():
    content = Path("docker-entrypoint.sh").read_text(encoding="utf-8")

    assert "case \"$APP_MODULE\"" in content
    assert "app.main:create_app" in content
    assert "app.runtime.sandbox.executor_app:create_executor_app" in content
    assert 'if [ "${1:-}" = "uvicorn" ]' in content
    assert 'exec "$@"' in content
    assert "exec \"$@\"" in content


def test_docker_entrypoint_does_not_double_append_uvicorn_app_when_cmd_already_has_target():
    content = Path("docker-entrypoint.sh").read_text(encoding="utf-8")

    assert 'if [ "${1:-}" = "uvicorn" ] && [ "${2:-}" = "" ]; then' in content
    assert 'exec "$@" "$APP_MODULE" --factory --host 0.0.0.0 --port "$APP_PORT"' in content


def test_backend_image_and_compose_fix_runtime_identity_without_env_override():
    import yaml

    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    compose = yaml.safe_load(compose_text)
    env_example = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")

    assert "groupadd --gid 10001 ai-platform" in dockerfile
    assert "useradd --uid 10001 --gid 10001" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "ENV HOME=/home/ai-platform" in dockerfile
    assert "ENV TMPDIR=/home/ai-platform/tmp" in dockerfile
    assert "ENV XDG_CACHE_HOME=/home/ai-platform/.cache" in dockerfile
    assert compose["services"]["api"]["user"] == "10001:10001"
    assert compose["services"]["worker"]["user"] == "10001:10001"
    assert "AI_PLATFORM_RUNTIME_UID" not in compose_text
    assert "AI_PLATFORM_RUNTIME_GID" not in compose_text
    assert "AI_PLATFORM_RUNTIME_UID" not in env_example
    assert "AI_PLATFORM_RUNTIME_GID" not in env_example


def test_compose_workspace_migration_and_init_are_narrow_and_ordered():
    import yaml

    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    migration = compose["services"]["workspace-migrate"]
    init = compose["services"]["workspace-init"]
    workspace_root = "${SANDBOX_WORKSPACE_ROOT:-/tmp/ai-platform-sandbox-workspaces}"

    assert migration["user"] == "0:0"
    assert migration["network_mode"] == "none"
    assert migration["read_only"] is True
    assert migration["restart"] == "no"
    assert migration["cap_drop"] == ["ALL"]
    assert set(migration["cap_add"]) == {
        "CHOWN",
        "DAC_OVERRIDE",
        "DAC_READ_SEARCH",
        "FOWNER",
    }
    assert migration["entrypoint"] == [
        "python",
        "-m",
        "app.sandbox.infrastructure.workspace_storage_migration",
    ]
    assert migration["volumes"] == [
        "${SANDBOX_WORKSPACE_MIGRATION_SOURCE:?set SANDBOX_WORKSPACE_MIGRATION_SOURCE}:/source-workspaces:ro",
        f"{workspace_root}:/target-workspaces",
    ]

    assert init["user"] == "0:0"
    assert init["network_mode"] == "none"
    assert init["read_only"] is True
    assert init["restart"] == "no"
    assert "ulimits" not in init
    assert init["cap_drop"] == ["ALL"]
    assert set(init["cap_add"]) == {"CHOWN", "DAC_READ_SEARCH", "FOWNER", "SETUID", "SETGID"}
    assert init["entrypoint"] == ["python", "-m", "app.runtime.sandbox.workspace_permissions"]
    assert init["command"] == []
    assert init["volumes"] == [f"{workspace_root}:/runtime-workspaces"]
    assert init["depends_on"]["workspace-migrate"]["condition"] == (
        "service_completed_successfully"
    )
    for service_name in ("api", "worker"):
        assert compose["services"][service_name]["depends_on"]["workspace-init"]["condition"] == "service_completed_successfully"
        assert compose["services"][service_name]["volumes"] == [
            f"{workspace_root}:{workspace_root}"
        ]


def test_sandbox_overlay_grants_socket_and_group_only_to_worker():
    import yaml

    overlay_text = SANDBOX_COMPOSE_FILE.read_text(encoding="utf-8")
    overlay = yaml.safe_load(overlay_text)

    api = overlay["services"]["api"]
    assert "/var/run/docker.sock:/var/run/docker.sock" not in str(api)
    assert "group_add" not in api
    assert api["volumes"] == [
        "${SANDBOX_WORKSPACE_ROOT:-/tmp/ai-platform-sandbox-workspaces}:${SANDBOX_WORKSPACE_ROOT:-/tmp/ai-platform-sandbox-workspaces}"
    ]
    worker = overlay["services"]["worker"]
    assert "/var/run/docker.sock:/var/run/docker.sock" in worker["volumes"]
    assert worker["group_add"] == ["${DOCKER_SOCKET_GID:?set DOCKER_SOCKET_GID}"]
    assert overlay["services"]["workspace-init"]["volumes"] == [
        "${SANDBOX_WORKSPACE_ROOT:-/tmp/ai-platform-sandbox-workspaces}:/runtime-workspaces"
    ]
    env_example = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")
    assert "DOCKER_SOCKET_GID=" in env_example


def test_runtime_entrypoint_fails_closed_before_exec_when_identity_is_wrong():
    content = Path("docker-entrypoint.sh").read_text(encoding="utf-8")

    assert "id -u" in content
    assert "id -g" in content
    assert '"$runtime_uid" != "10001"' in content
    assert '"$runtime_gid" != "10001"' in content
    assert "id -G" in content
    assert "Runtime supplementary groups must not include GID 0" in content
    assert "exec \"$@\"" in content
    assert "workspace_permissions" not in content


def test_compose_exposes_sandbox_runtime_configuration():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    sandbox_text = SANDBOX_COMPOSE_FILE.read_text(encoding="utf-8")

    for service_name in ["api:", "worker:"]:
        assert service_name in compose_text

    assert "context: ../.." not in compose_text
    assert "build:" not in compose_service_text(compose_text, "api")
    assert "build:" not in compose_service_text(compose_text, "worker")
    assert "build:" not in compose_service_text(compose_text, "frontend")
    assert "container_name: ai-platform-frontend" in compose_text
    assert "${AI_PLATFORM_FRONTEND_IMAGE:?set AI_PLATFORM_FRONTEND_IMAGE}" in compose_text
    assert "${AI_PLATFORM_SOURCE_COMMIT:?set AI_PLATFORM_SOURCE_COMMIT}" in compose_text
    assert "${AI_PLATFORM_FRONTEND_PORT:-18001}:8080" in compose_text
    assert "AUTH_CONTEXT_COOKIE_SECURE: ${AUTH_CONTEXT_COOKIE_SECURE:-true}" in compose_text
    assert "SANDBOX_CONTAINER_PROVIDER" in compose_text
    assert "SANDBOX_EXECUTOR_IMAGE" in compose_text
    assert "SANDBOX_CALLBACK_BASE_URL" in compose_text
    assert "SANDBOX_CALLBACK_TOKEN" in compose_text
    assert "SANDBOX_WORKSPACE_ROOT" in compose_text
    assert "/var/run/docker.sock:/var/run/docker.sock" in sandbox_text
    assert (
        "${SANDBOX_WORKSPACE_ROOT:-/tmp/ai-platform-sandbox-workspaces}:"
        "${SANDBOX_WORKSPACE_ROOT:-/tmp/ai-platform-sandbox-workspaces}"
    ) in compose_text
    assert "SANDBOX_HOST_WORKSPACE_ROOT" not in sandbox_text
    assert "${SANDBOX_WORKSPACE_MIGRATION_SOURCE:?set SANDBOX_WORKSPACE_MIGRATION_SOURCE}:/source-workspaces:ro" in compose_text
    assert "SANDBOX_CONTAINER_PROVIDER: docker" in sandbox_text


def test_opensandbox_overlay_uses_direct_sdk_and_stateless_egress_proxy():
    import yaml

    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    overlay = yaml.safe_load(OPENSANDBOX_COMPOSE_FILE.read_text(encoding="utf-8"))
    env_example = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")

    for service_name in ("api", "worker"):
        assert "SANDBOX_SECURITY_PROFILE" not in compose["services"][service_name]["environment"]
        environment = overlay["services"][service_name]["environment"]
        assert environment["SANDBOX_CONTAINER_PROVIDER"] == "opensandbox"
        assert environment["OPENSANDBOX_USE_SERVER_PROXY"] == "true"
        for topology_key in (
            "OPENSANDBOX_EGRESS_BRIDGE",
            "OPENSANDBOX_EGRESS_SUBNET",
            "OPENSANDBOX_EGRESS_PROXY_IPV4",
        ):
            assert topology_key not in environment
        assert environment["SANDBOX_RUNTIME_SUBJECT"] == "${SANDBOX_RUNTIME_SUBJECT:-direct-opensandbox}"
        assert environment["OPENSANDBOX_EXPECTED_NETWORK_MODE"] == (
            "${OPENSANDBOX_EXPECTED_NETWORK_MODE:?required}"
        )
        assert environment["OPENSANDBOX_EGRESS_PROXY_URL"] == (
            "http://egress.opensandbox.internal:8080"
        )
        for required in (
            "MODEL_CONNECTION_ENCRYPTION_KEY",
            "SANDBOX_EGRESS_PROOF_SIGNING_KEY",
            "OPENSANDBOX_BASE_URL",
            "OPENSANDBOX_API_KEY",
            "OPENSANDBOX_EXECUTOR_IMAGE",
            "OPENSANDBOX_EXECUTOR_IMAGE_DIGEST",
        ):
            assert environment[required].startswith("${")
            assert ":?set " in environment[required]
        assert "MODEL_CONNECTION_ALLOWED_INTERNAL_HOSTS" in environment
        for retired_direct_key in (
            "OPENAI_BASE_URL", "OPENAI_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN",
        ):
            assert environment[retired_direct_key] == ""

    assert overlay["services"]["api"]["environment"]["MODEL_PROXY_INTERNAL_TOKEN"].startswith("${")
    proxy = overlay["services"]["opensandbox-egress-proxy"]
    assert "ports" not in proxy
    assert proxy["networks"] == {
        "default": None,
        "opensandbox_egress_v2": {
            "aliases": ["egress.opensandbox.internal"],
            "ipv4_address": "${OPENSANDBOX_EGRESS_PROXY_IPV4:?required}",
        },
    }
    assert proxy["labels"]["ai-platform.release-role"] == "opensandbox-egress-proxy"
    assert overlay["networks"] == {
        "opensandbox_egress_v2": {
            "name": "${OPENSANDBOX_EXPECTED_NETWORK_MODE:?required}",
            "driver": "bridge",
            "internal": False,
            "enable_ipv6": False,
            "driver_opts": {
                "com.docker.network.bridge.name": "${OPENSANDBOX_EGRESS_BRIDGE:?required}",
                "com.docker.network.bridge.enable_ip_masquerade": "true",
                "com.docker.network.bridge.enable_icc": "false",
            },
            "ipam": {"config": [{"subnet": "${OPENSANDBOX_EGRESS_SUBNET:?required}"}]},
        }
    }
    assert (
        overlay["services"]["opensandbox-egress-proxy"]["networks"][
            "opensandbox_egress_v2"
        ]["ipv4_address"]
        == "${OPENSANDBOX_EGRESS_PROXY_IPV4:?required}"
    )
    assert set(overlay["services"]) == {
        "workspace-migrate",
        "api",
        "worker",
        "workspace-init",
        "postgres",
        "redis",
        "minio",
        "opensandbox-egress-proxy",
    }
    assert overlay["services"]["workspace-migrate"]["volumes"] == [
        "${SANDBOX_WORKSPACE_MIGRATION_SOURCE:?set SANDBOX_WORKSPACE_MIGRATION_SOURCE}:/source-workspaces:ro"
    ]
    assert overlay["services"]["workspace-init"]["volumes"] == [
        "${SANDBOX_WORKSPACE_ROOT:?set SANDBOX_WORKSPACE_ROOT}:/runtime-workspaces"
    ]
    for service_name in ("api", "worker"):
        assert overlay["services"][service_name]["volumes"] == [
            "${SANDBOX_WORKSPACE_ROOT:?set SANDBOX_WORKSPACE_ROOT}:${SANDBOX_WORKSPACE_ROOT:?set SANDBOX_WORKSPACE_ROOT}"
        ]
        assert "volumes" in overlay["services"][service_name]
    for service_name in ("postgres", "redis", "minio"):
        assert overlay["services"][service_name]["ports"] == []
    example_values = env_example_values(env_example)
    for name, value in OPENSANDBOX_GUARD_TOPOLOGY.items():
        assert example_values[name] == value
    assert example_values["OPENSANDBOX_EGRESS_PROXY_BIND_ADDRESS"] == ""
    assert example_values["OPENSANDBOX_EGRESS_PROXY_URL"] == ""
    for fixed_key in (
        "DEPLOYMENT_ENVIRONMENT",
        "SANDBOX_SECURITY_PROFILE",
        "AI_PLATFORM_BUILD_COMMIT",
        "AI_PLATFORM_BUILD_DIRTY",
    ):
        assert f"{fixed_key}=" not in env_example
    assert "trusted_internal" not in env_example
    assert "OPENSANDBOX_TRUSTED_INTERNAL_" not in env_example

    guard = OPENSANDBOX_NETWORK_GUARD_SERVICE.read_text(encoding="utf-8")
    assert "Before=docker.service opensandbox.service" in guard
    assert "RequiredBy=docker.service opensandbox.service" in guard
    assert "EnvironmentFile=/etc/ai-platform/opensandbox/server.env" in guard
    assert '"$$1"' in guard
    assert "-I INPUT 1 -i ${OPENSANDBOX_EGRESS_BRIDGE} -j AI_PLATFORM_OPENSANDBOX" in guard
    assert (
        "-A AI_PLATFORM_OPENSANDBOX -m conntrack --ctstate "
        "RELATED,ESTABLISHED -j ACCEPT"
    ) in guard
    assert "-A AI_PLATFORM_OPENSANDBOX -j DROP" in guard
    assert (
        "-I DOCKER-USER 1 -i ${OPENSANDBOX_EGRESS_BRIDGE} -j AI_PLATFORM_OSB_FORWARD"
    ) in guard
    assert "-I DOCKER-USER 1 -o ${OPENSANDBOX_EGRESS_BRIDGE} -j AI_PLATFORM_OSB_FORWARD" in guard
    assert "-d ${OPENSANDBOX_EGRESS_PROXY_IPV4}/32" in guard
    assert "--dport 8080" in guard
    assert "-A AI_PLATFORM_OSB_FORWARD -i ${OPENSANDBOX_EGRESS_BRIDGE} -d 169.254.0.0/16 -j DROP" in guard
    assert "-A AI_PLATFORM_OSB_FORWARD -i ${OPENSANDBOX_EGRESS_BRIDGE} -d 100.64.0.0/10 -j DROP" in guard
    assert "-A AI_PLATFORM_OSB_FORWARD -o ${OPENSANDBOX_EGRESS_BRIDGE} -j DROP" in guard
    assert "-A AI_PLATFORM_OSB_FORWARD -i ${OPENSANDBOX_EGRESS_BRIDGE} -j RETURN" in guard
    assert "-A AI_PLATFORM_OSB_FORWARD -i ${OPENSANDBOX_EGRESS_BRIDGE} ! -s ${OPENSANDBOX_EGRESS_SUBNET} -j DROP" in guard
    assert "-I FORWARD 1 -i ${OPENSANDBOX_EGRESS_BRIDGE} -j AI_PLATFORM_OSB_IPV6" in guard
    assert "-I INPUT 1 -i ${OPENSANDBOX_EGRESS_BRIDGE} -j AI_PLATFORM_OSB_IPV6" in guard
    assert "-A AI_PLATFORM_OSB_IPV6 -j DROP" in guard

    server_unit = OPENSANDBOX_PRODUCTION_SERVICE.read_text(encoding="utf-8")
    assert "tomllib" in server_unit
    assert "/etc/ai-platform/opensandbox/server.toml" in server_unit
    assert "host == expected" in server_unit


def test_opensandbox_model_and_callback_authority_is_shared_across_services():
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))["services"]
    overlay = yaml.safe_load(OPENSANDBOX_COMPOSE_FILE.read_text(encoding="utf-8"))["services"]
    api = {**compose["api"]["environment"], **overlay["api"]["environment"]}
    worker = {**compose["worker"]["environment"], **overlay["worker"]["environment"]}
    for service_name in ("api", "worker"):
        environment = {**compose[service_name]["environment"], **overlay[service_name]["environment"]}
        for name in ("MODEL_CONNECTION_ENCRYPTION_KEY", "MODEL_CONNECTION_ALLOWED_INTERNAL_HOSTS"):
            assert environment[name] == api[name] == worker[name]
        for name in ("SANDBOX_CALLBACK_BASE_URL", "SANDBOX_CALLBACK_TOKEN"):
            assert environment[name] == api[name] == worker[name]
    proxy = overlay["opensandbox-egress-proxy"]
    assert api["MODEL_PROXY_INTERNAL_TOKEN"] == proxy["environment"]["MODEL_PROXY_INTERNAL_TOKEN"]
    assert "MODEL_PROXY_INTERNAL_TOKEN" not in worker
    assert "ports" not in proxy


def test_opensandbox_guard_refresh_closes_the_bridge_until_both_families_are_ready():
    commands = []
    for line in OPENSANDBOX_NETWORK_GUARD_SERVICE.read_text().splitlines():
        if not line.startswith("ExecStart="):
            continue
        raw_command = shlex.split(line.removeprefix("ExecStart="))
        command, _ = expand_network_guard_command(raw_command, OPENSANDBOX_GUARD_TOPOLOGY)
        if command[0] == "/bin/sh":
            script = command[2].replace(
                '"$1"', OPENSANDBOX_GUARD_TOPOLOGY["OPENSANDBOX_EGRESS_BRIDGE"]
            )
            for statement in script.split(";"):
                command_start = min(
                    (position for position in (
                        statement.find("/usr/sbin/iptables"),
                        statement.find("/usr/sbin/ip6tables"),
                    ) if position >= 0),
                    default=-1,
                )
                if command_start >= 0:
                    commands.append(shlex.split(statement[command_start:]))
        else:
            commands.append(command)
    first_flush = next(index for index, command in enumerate(commands) if "-F" in command)
    last_install = max(index for index, command in enumerate(commands) if "-A" in command or "-I" in command)
    holds = {}
    for index, command in enumerate(commands):
        if command[0] == "/bin/sh":
            command = shlex.split(command[-1].split(";", 1)[0])
        # iptables accepts one source and destination selector per rule.
        assert command.count("-s") <= 1 and command.count("-d") <= 1
        if (
            command[-2:] != ["-j", "DROP"]
            or "-A" in command
            or not {"-I", "-D"}.intersection(command)
        ):
            continue
        operation = "-I" if "-I" in command else "-D"
        offset = command.index(operation)
        tail = command[offset + 1 :]
        if operation == "-I":
            tail.remove("1")
        key = (command[0], *tail)
        holds.setdefault(key, {})[operation] = index
    assert len(holds) == 6
    for indexes in holds.values():
        assert indexes["-I"] < first_flush
        assert indexes["-D"] > last_install
    assert not any("AI_PLATFORM_OSB_OUTPUT" in command for command in commands)


@pytest.mark.parametrize("failed_rule", (
    ("iptables", "-F", "AI_PLATFORM_OPENSANDBOX"),
    ("iptables", "-A", "AI_PLATFORM_OSB_FORWARD", "-i", "br-osb-egress2", "-j", "RETURN"),
    ("ip6tables", "-A", "AI_PLATFORM_OSB_IPV6", "-j", "DROP"),
))
def test_opensandbox_guard_recovers_from_failed_refresh(tmp_path, monkeypatch, failed_rule):
    """Execute the unit against an iptables state double, including its real shell loops."""
    from types import SimpleNamespace

    from tools import s75_opensandbox_transition as transition

    state_path = tmp_path / "rules.json"
    fake_cli = f"#!{sys.executable}\n" + '''
import json
import os
from pathlib import Path
import sys

path = Path(os.environ["OSB_GUARD_TEST_STATE"])
state = json.loads(path.read_text()) if path.exists() else {"tables": {}, "failed": False}
family = Path(sys.argv[0]).name
args = sys.argv[1:]
if args[:2] == ["-w", "30"]:
    args = args[2:]
if [family, *args] == json.loads(os.environ["OSB_GUARD_TEST_FAIL"]) and not state["failed"]:
    state["failed"] = True
    path.write_text(json.dumps(state))
    sys.exit(2)
tables = state["tables"].setdefault(family, {"INPUT": [], "OUTPUT": [], "FORWARD": []})
operation, chain, *rule = args
code = 0
if operation == "-N":
    if chain in tables:
        code = 1
    else:
        tables[chain] = []
elif chain not in tables:
    code = 1
elif operation == "-F":
    tables[chain] = []
elif operation == "-A":
    tables[chain].append(rule)
elif operation == "-I":
    position = int(rule.pop(0)) - 1
    tables[chain].insert(position, rule)
elif operation in ("-C", "-D"):
    if rule not in tables[chain]:
        code = 1
    elif operation == "-D":
        tables[chain].remove(rule)
else:
    code = 2
path.write_text(json.dumps(state))
sys.exit(code)
'''
    for family in ("iptables", "ip6tables"):
        executable = tmp_path / family
        executable.write_text(fake_cli)
        executable.chmod(0o700)
    environment = {
        **os.environ,
        **OPENSANDBOX_GUARD_TOPOLOGY,
        "OSB_GUARD_TEST_STATE": str(state_path),
        "OSB_GUARD_TEST_FAIL": json.dumps(failed_rule),
    }
    commands = [
        shlex.split(line.removeprefix("ExecStart="))
        for line in OPENSANDBOX_NETWORK_GUARD_SERVICE.read_text().splitlines()
        if line.startswith("ExecStart=")
    ]

    def refresh() -> bool:
        for raw_command in commands:
            command, ignore_failure = expand_network_guard_command(
                raw_command, OPENSANDBOX_GUARD_TOPOLOGY, iptables_dir=tmp_path
            )
            result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=10)
            if result.returncode and not ignore_failure:
                return False
        return True

    assert refresh() is False
    tables = json.loads(state_path.read_text())["tables"]
    holds = (
        ("iptables", "INPUT", "-i"),
        ("iptables", "DOCKER-USER", "-i"),
        ("iptables", "DOCKER-USER", "-o"),
        ("ip6tables", "INPUT", "-i"),
        ("ip6tables", "FORWARD", "-i"),
        ("ip6tables", "FORWARD", "-o"),
    )
    for family, chain, interface in holds:
        assert [interface, OPENSANDBOX_GUARD_TOPOLOGY["OPENSANDBOX_EGRESS_BRIDGE"], "-j", "DROP"] in tables[family][chain]
    assert refresh() is True
    assert refresh() is True
    tables = json.loads(state_path.read_text())["tables"]
    for family, chain, interface in holds:
        assert [interface, OPENSANDBOX_GUARD_TOPOLOGY["OPENSANDBOX_EGRESS_BRIDGE"], "-j", "DROP"] not in tables[family][chain]

    unit_path = tmp_path / "guard.service"
    unit_path.write_bytes(OPENSANDBOX_NETWORK_GUARD_SERVICE.read_bytes())
    real_lstat = Path.lstat
    monkeypatch.setattr(Path, "lstat", lambda path: (
        SimpleNamespace(st_mode=0o100644, st_uid=0) if path == unit_path else real_lstat(path)
    ))
    monkeypatch.setattr(transition, "_run", lambda command: SimpleNamespace(stdout="\n".join(
        shlex.join(["-A", chain, *rule])
        for chain, rules in tables["ip6tables" if command[0] == "ip6tables-save" else "iptables"].items()
        for rule in rules
    )))
    from tools.release_authority import validate_direct_opensandbox_topology

    topology = validate_direct_opensandbox_topology(
        OPENSANDBOX_GUARD_TOPOLOGY["OPENSANDBOX_EXPECTED_NETWORK_MODE"],
        OPENSANDBOX_GUARD_TOPOLOGY["OPENSANDBOX_EGRESS_BRIDGE"],
        OPENSANDBOX_GUARD_TOPOLOGY["OPENSANDBOX_EGRESS_SUBNET"],
        OPENSANDBOX_GUARD_TOPOLOGY["OPENSANDBOX_EGRESS_PROXY_IPV4"],
    )
    transition._require_network_guard(Path.cwd(), unit_path=unit_path, topology=topology)


@pytest.mark.skipif(
    os.name != "posix" or os.environ.get("GITHUB_ACTIONS") != "true",
    reason="requires the Docker-capable GitHub Linux runner",
)
def test_opensandbox_network_guard_allows_public_egress_and_keeps_host_boundaries():
    def run(
        command: list[str], *, check: bool = True, timeout: int = 120,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            check=check,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    for executable in ("docker", "sudo", "ip"):
        assert shutil.which(executable), f"required executable is unavailable: {executable}"
    assert Path("/usr/sbin/iptables").is_file()
    run(["sudo", "-n", "true"])
    run(["docker", "info"])

    topology = dict(OPENSANDBOX_GUARD_TOPOLOGY)
    network = topology["OPENSANDBOX_EXPECTED_NETWORK_MODE"]
    bridge = topology["OPENSANDBOX_EGRESS_BRIDGE"]
    subnet = topology["OPENSANDBOX_EGRESS_SUBNET"]
    proxy_ipv4 = topology["OPENSANDBOX_EGRESS_PROXY_IPV4"]
    ipv4_chains = (
        "AI_PLATFORM_OPENSANDBOX",
        "AI_PLATFORM_OSB_FORWARD",
    )
    ipv6_chain = "AI_PLATFORM_OSB_IPV6"
    assert run(["docker", "network", "inspect", network], check=False).returncode != 0
    assert run(["ip", "link", "show", "dev", bridge], check=False).returncode != 0
    for chain in ipv4_chains:
        assert (
            run(
                ["sudo", "-n", "/usr/sbin/iptables", "-S", chain],
                check=False,
            ).returncode
            != 0
        )
    assert (
        run(
            ["sudo", "-n", "/usr/sbin/ip6tables", "-S", ipv6_chain],
            check=False,
        ).returncode
        != 0
    )

    suffix = str(os.getpid())
    proxy = f"ai-platform-osb-guard-proxy-{suffix}"
    peer = f"ai-platform-osb-guard-peer-{suffix}"
    client = f"ai-platform-osb-guard-client-{suffix}"
    public_namespace = f"ai-osb-public-{suffix}"
    public_veth = f"osbp{suffix}"
    public_peer = f"osbe{suffix}"
    containers = (proxy, peer, client)
    network_created = False
    public_namespace_created = False
    public_veth_created = False
    public_server: subprocess.Popen | None = None
    guard_owned = False
    listener: socket.socket | None = None
    listener_thread: threading.Thread | None = None
    stop_listener = threading.Event()

    try:
        run(
            [
                "docker",
                "network",
                "create",
                "--driver",
                "bridge",
                "--subnet",
                subnet,
                "--opt",
                f"com.docker.network.bridge.name={bridge}",
                "--opt",
                "com.docker.network.bridge.enable_ip_masquerade=true",
                "--opt",
                "com.docker.network.bridge.enable_icc=false",
                network,
            ]
        )
        network_created = True
        run(["docker", "pull", "redis:7.4-alpine"])
        for name, address, port in (
            (proxy, proxy_ipv4, "8080"),
            (peer, "172.31.76.3", "9090"),
        ):
            run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--name",
                    name,
                    "--network",
                    network,
                    "--ip",
                    address,
                    "redis:7.4-alpine",
                    "sh",
                    "-c",
                    f"while true; do nc -l -p {port} >/dev/null 2>&1; done",
                ]
            )
        run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                client,
                "--network",
                network,
                "--ip",
                "172.31.76.4",
                "redis:7.4-alpine",
                "sh",
                "-c",
                "while true; do sleep 3600; done",
            ]
        )

        # A routed namespace provides a deterministic bridge-external round trip.
        # It has no route back to the sandbox subnet, so a reply requires host NAT.
        assert run(["ip", "route", "show", "11.255.255.0/30"]).stdout.strip() == ""
        run(["sudo", "-n", "ip", "netns", "add", public_namespace])
        public_namespace_created = True
        run(["sudo", "-n", "ip", "link", "add", public_veth, "type", "veth", "peer", "name", public_peer])
        public_veth_created = True
        run(["sudo", "-n", "ip", "link", "set", public_peer, "netns", public_namespace])
        run(["sudo", "-n", "ip", "addr", "add", "11.255.255.1/30", "dev", public_veth])
        run(["sudo", "-n", "ip", "link", "set", public_veth, "up"])
        namespace_command = ["sudo", "-n", "ip", "netns", "exec", public_namespace]
        run([*namespace_command, "ip", "addr", "add", "11.255.255.2/30", "dev", public_peer])
        run([*namespace_command, "ip", "link", "set", public_peer, "up"])
        run([*namespace_command, "ip", "link", "set", "lo", "up"])
        public_server = subprocess.Popen(
            [*namespace_command, sys.executable, "-u", "-c", (
                "import socket\n"
                "listener = socket.socket()\n"
                "listener.bind(('11.255.255.2', 18081))\n"
                "listener.listen()\n"
                "while True:\n"
                "    connection, peer = listener.accept()\n"
                "    with connection:\n"
                "        connection.sendall(('osb-public-roundtrip ' + peer[0] + '\\n').encode())\n"
            )],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("172.31.76.1", 18080))
        listener.listen()
        listener.settimeout(0.2)

        def accept_host_connections() -> None:
            assert listener is not None
            while not stop_listener.is_set():
                try:
                    connection, _ = listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if stop_listener.is_set():
                        return
                    raise
                connection.close()

        listener_thread = threading.Thread(target=accept_host_connections, daemon=True)
        listener_thread.start()

        deadline = time.monotonic() + 15
        for address, port in ((proxy_ipv4, 8080), ("172.31.76.3", 9090), ("11.255.255.2", 18081)):
            while True:
                try:
                    with socket.create_connection((address, port), timeout=1):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.1)

        unit_lines = OPENSANDBOX_NETWORK_GUARD_SERVICE.read_text(
            encoding="utf-8"
        ).splitlines()
        guard_commands = [
            shlex.split(line.removeprefix("ExecStart="))
            for line in unit_lines
            if line.startswith("ExecStart=")
        ]
        assert guard_commands
        guard_owned = True

        def run_guard_command(raw_command: list[str]) -> None:
            command, ignore_failure = expand_network_guard_command(raw_command, topology)
            assert command[0] in {"/usr/sbin/iptables", "/usr/sbin/ip6tables", "/bin/sh"}
            run(["sudo", "-n", *command], check=not ignore_failure)

        # Interrupt one refresh after holds are installed, then retry normally.
        first_flush = next(index for index, command in enumerate(guard_commands) if "-F" in command)
        for command in guard_commands[:first_flush]:
            run_guard_command(command)
        for _ in range(2):
            for command in guard_commands:
                run_guard_command(command)
        for executable, chain, direction in (
            ("iptables", "INPUT", "-i"),
            ("iptables", "DOCKER-USER", "-i"),
            ("iptables", "DOCKER-USER", "-o"),
            ("ip6tables", "INPUT", "-i"),
            ("ip6tables", "FORWARD", "-i"),
            ("ip6tables", "FORWARD", "-o"),
        ):
            assert run(
                ["sudo", "-n", f"/usr/sbin/{executable}", "-C", chain, direction, bridge, "-j", "DROP"],
                check=False,
            ).returncode != 0

        def packet_count(rule_tail: str) -> int:
            from tools.s75_opensandbox_transition import _network_guard_rules_match

            saved = run(
                ["sudo", "-n", "/usr/sbin/iptables-save", "-c", "-t", "filter"]
            ).stdout
            for line in saved.splitlines():
                if line.startswith("[") and _network_guard_rules_match(
                    [line[line.index("]") + 2 :]], [rule_tail],
                ):
                    return int(line[1 : line.index(":")])
            raise AssertionError(f"guard rule is missing: {rule_tail}")

        assert (
            run(
                [
                    "docker",
                    "exec",
                    client,
                    "nc",
                    "-z",
                    "-w",
                    "2",
                    proxy_ipv4,
                    "8080",
                ],
                check=False,
            ).returncode
            == 0
        )
        assert (
            run(
                [
                    "docker",
                    "exec",
                    client,
                    "nc",
                    "-z",
                    "-w",
                    "2",
                    "172.31.76.3",
                    "9090",
                ],
                check=False,
            ).returncode
            != 0
        )
        assert (
            run(
                [
                    "docker",
                    "exec",
                    client,
                    "nc",
                    "-z",
                    "-w",
                    "2",
                    "172.31.76.1",
                    "18080",
                ],
                check=False,
            ).returncode
            != 0
        )
        assert (
            run(
                ["docker", "exec", client, "nc", "-z", "-w", "2", "10.1.2.3", "443"],
                check=False,
            ).returncode
            != 0
        )
        assert (
            run(
                ["docker", "exec", client, "nc", "-z", "-w", "2", "169.254.169.254", "80"],
                check=False,
            ).returncode
            != 0
        )
        assert (
            run(
                ["docker", "exec", client, "nc", "-z", "-w", "2", "100.100.100.200", "80"],
                check=False,
            ).returncode
            != 0
        )
        public_return_before = packet_count(f"-A AI_PLATFORM_OSB_FORWARD -i {bridge} -j RETURN")
        public_response = run(
            ["docker", "exec", client, "nc", "-w", "3", "11.255.255.2", "18081"],
        )
        assert public_response.stdout.strip() == "osb-public-roundtrip 11.255.255.1"
        assert packet_count(f"-A AI_PLATFORM_OSB_FORWARD -i {bridge} -j RETURN") > public_return_before
        assert packet_count(f"-A AI_PLATFORM_OSB_FORWARD -i {bridge} -d 10.0.0.0/8 -j DROP") > 0
        assert packet_count(f"-A AI_PLATFORM_OSB_FORWARD -i {bridge} -d 169.254.0.0/16 -j DROP") > 0
        assert packet_count(f"-A AI_PLATFORM_OSB_FORWARD -i {bridge} -d 100.64.0.0/10 -j DROP") > 0
        assert packet_count(f"-A AI_PLATFORM_OSB_FORWARD -i {bridge} -o {bridge} -j DROP") > 0
        # The trusted host control plane must still reach sandbox services.
        with socket.create_connection(("172.31.76.3", 9090), timeout=2):
            pass

        ipv6_rules = run(
            ["sudo", "-n", "/usr/sbin/ip6tables-save", "-t", "filter"]
        ).stdout
        assert f"-A INPUT -i {bridge} -j AI_PLATFORM_OSB_IPV6" in ipv6_rules
        assert f"-A FORWARD -i {bridge} -j AI_PLATFORM_OSB_IPV6" in ipv6_rules
        assert f"-A FORWARD -o {bridge} -j AI_PLATFORM_OSB_IPV6" in ipv6_rules
    finally:
        cleanup_failures = []

        def cleanup(command: list[str]) -> subprocess.CompletedProcess[str] | None:
            try:
                return run(command, check=False, timeout=15)
            except (OSError, subprocess.TimeoutExpired) as exc:
                cleanup_failures.append(f"{shlex.join(command)}: {type(exc).__name__}")
                return None

        stop_listener.set()
        if listener is not None:
            listener.close()
        if listener_thread is not None:
            listener_thread.join(timeout=2)
        if public_namespace_created:
            processes = cleanup(["sudo", "-n", "ip", "netns", "pids", public_namespace])
            if processes is not None:
                for pid in processes.stdout.split():
                    cleanup(["sudo", "-n", "kill", "-TERM", str(int(pid))])
        if public_server is not None:
            try:
                public_server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cleanup(["sudo", "-n", "kill", "-KILL", str(public_server.pid)])
                try:
                    public_server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    cleanup_failures.append("bridge-external listener did not stop")
        if guard_owned:
            for executable, commands in (
                (
                    "/usr/sbin/iptables",
                    [
                        ["-D", "INPUT", "-i", bridge, "-j", "DROP"],
                        ["-D", "DOCKER-USER", "-i", bridge, "-j", "DROP"],
                        ["-D", "DOCKER-USER", "-o", bridge, "-j", "DROP"],
                        ["-D", "INPUT", "-i", bridge, "-j", "AI_PLATFORM_OPENSANDBOX"],
                        ["-D", "DOCKER-USER", "-i", bridge, "-j", "AI_PLATFORM_OSB_FORWARD"],
                        ["-D", "DOCKER-USER", "-o", bridge, "-j", "AI_PLATFORM_OSB_FORWARD"],
                        *[["-F", chain] for chain in ipv4_chains],
                        *[["-X", chain] for chain in ipv4_chains],
                    ],
                ),
                (
                    "/usr/sbin/ip6tables",
                    [
                        ["-D", "INPUT", "-i", bridge, "-j", "DROP"],
                        ["-D", "FORWARD", "-i", bridge, "-j", "DROP"],
                        ["-D", "FORWARD", "-o", bridge, "-j", "DROP"],
                        ["-D", "INPUT", "-i", bridge, "-j", ipv6_chain],
                        ["-D", "OUTPUT", "-o", bridge, "-j", ipv6_chain],
                        ["-D", "FORWARD", "-i", bridge, "-j", ipv6_chain],
                        ["-D", "FORWARD", "-o", bridge, "-j", ipv6_chain],
                        ["-D", "DOCKER-USER", "-i", bridge, "-j", ipv6_chain],
                        ["-D", "DOCKER-USER", "-o", bridge, "-j", ipv6_chain],
                        ["-F", ipv6_chain],
                        ["-X", ipv6_chain],
                    ],
                ),
            ):
                for command in commands:
                    prefix = ["sudo", "-n", executable, "-w", "30"]
                    if command[0] == "-D":
                        while True:
                            present = cleanup([*prefix, "-C", *command[1:]])
                            if present is None or present.returncode != 0:
                                if present is not None and present.returncode != 1:
                                    cleanup_failures.append(f"could not inspect guard rule: {command}")
                                break
                            removed = cleanup([*prefix, *command])
                            if removed is None or removed.returncode != 0:
                                cleanup_failures.append(f"could not remove guard rule: {command}")
                                break
                    else:
                        cleanup([*prefix, *command])
        cleanup(["docker", "rm", "--force", *containers])
        if network_created:
            cleanup(["docker", "network", "rm", network])
        if public_veth_created:
            cleanup(["sudo", "-n", "ip", "link", "delete", public_veth])
        if public_namespace_created:
            cleanup(["sudo", "-n", "ip", "netns", "delete", public_namespace])
        assert cleanup_failures == [], f"network fixture cleanup failed: {cleanup_failures}"


def test_opensandbox_egress_proxy_preserves_existing_model_and_callback_authorities():
    template = OPENSANDBOX_EGRESS_TEMPLATE.read_text(encoding="utf-8")

    # The proxy must forward every current fenced callback and no retired route.
    callbacks = set(re.findall(r'@router\.post\(\s*"/runtime/callbacks/([^"/]+)"',
                              Path("app/routes/runtime_callbacks.py").read_text()))
    match = re.search(r'/api/ai/runtime/callbacks/\(([^)]+)\)\$', template)
    assert match is not None
    assert set(match.group(1).split("|")) == callbacks
    assert callbacks == {"executor", "context-retrieval", "provider-session", "inputs"}
    assert 'location ~ "^/openai/(?<openai_run_id>[A-Za-z0-9_-]{1,128})/(?<openai_attempt_id>[A-Za-z0-9_-]{1,128})/(?<openai_model_path>v1/(chat/completions|responses))$" {' in template
    assert 'location ~ "^/anthropic/(?<anthropic_run_id>[A-Za-z0-9_-]{1,128})/(?<anthropic_attempt_id>[A-Za-z0-9_-]{1,128})/(?<anthropic_model_path>v1/messages(?:/count_tokens)?)$" {' in template
    assert "/api/ai/internal/model-proxy/openai/" in template
    assert "/api/ai/internal/model-proxy/anthropic/" in template
    assert template.count("proxy_set_header Authorization \"\";") == 3
    assert template.count("proxy_set_header X-AI-Platform-Internal-Token ${MODEL_PROXY_INTERNAL_TOKEN};") == 2
    assert template.count("proxy_set_header X-AI-Platform-Model-Authorization $http_authorization;") == 2
    assert template.count("proxy_set_header X-AI-Platform-Model-Api-Key $http_x_api_key;") == 2
    assert "location / {\n        return 404;\n    }" in template
    assert "gateway" not in template.casefold()


def test_compose_does_not_mount_docker_socket_by_default():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    sandbox_text = SANDBOX_COMPOSE_FILE.read_text(encoding="utf-8")

    assert "/var/run/docker.sock:/var/run/docker.sock" not in compose_text
    assert "/var/run/docker.sock:/var/run/docker.sock" in sandbox_text
    assert "ai_platform_sandbox_workspaces:/tmp/ai-platform-sandbox-workspaces" not in sandbox_text


def test_compose_requires_non_empty_sandbox_callback_token():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    env_example_text = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")
    env_values = env_example_values(env_example_text)

    assert "SANDBOX_CALLBACK_TOKEN: ${SANDBOX_CALLBACK_TOKEN:?set SANDBOX_CALLBACK_TOKEN}" in compose_text
    assert env_values["SANDBOX_CALLBACK_TOKEN"] == ""
    assert "SANDBOX_CALLBACK_TOKEN=change_me_sandbox_callback_token" not in env_example_text


def test_env_example_documents_sandbox_egress_policy_defaults():
    env_example_text = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")
    direct_text = OPENSANDBOX_COMPOSE_FILE.read_text(encoding="utf-8")

    for expected in [
        "SANDBOX_CONTAINER_PROVIDER=opensandbox",
        "SANDBOX_EXECUTOR_IMAGE=ai-platform:local",
        "SANDBOX_EXECUTOR_PUBLISHED_HOST=host.docker.internal",
        "SANDBOX_WORKSPACE_MIGRATION_SOURCE=/data/ai-platform/runtime-workspaces",
        "SANDBOX_WORKSPACE_ROOT=/data/opensandbox/workspaces/ai-platform",
        "SANDBOX_CALLBACK_BASE_URL=http://api.sandbox.internal:8020",
        "SANDBOX_EGRESS_POLICY_ENABLED=false",
        "SANDBOX_EGRESS_PROOF_SIGNING_KEY=replace_me_with_a_random_32_byte_minimum_value",
        "SANDBOX_EGRESS_PROOF_KEY_ID=current",
        "SANDBOX_EGRESS_PROOF_PREVIOUS_KEYS_JSON=",
        "SANDBOX_CALLBACK_HOST_GATEWAY=",
    ]:
        assert expected in env_example_text

    assert "SANDBOX_CONTAINER_PROVIDER=fake" not in env_example_text
    assert "SANDBOX_CALLBACK_TOKEN=change_me_sandbox_callback_token" not in env_example_text
    assert direct_text.count("SANDBOX_CONTAINER_PROVIDER: opensandbox") == 2
    assert direct_text.count('OPENSANDBOX_USE_SERVER_PROXY: "true"') == 2
    assert direct_text.count(
        "OPENSANDBOX_EXPECTED_NETWORK_MODE: ${OPENSANDBOX_EXPECTED_NETWORK_MODE:?required}"
    ) == 2
    assert direct_text.count("      OPENSANDBOX_EGRESS_PROXY_URL:") == 2
    assert "SANDBOX_SECURITY_PROFILE" not in direct_text


def test_compose_passes_sandbox_egress_policy_env_to_api_and_worker():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")

    for service_name in ("api", "worker"):
        service_text = compose_service_text(compose_text, service_name)
        for expected in [
            "SANDBOX_EGRESS_POLICY_ENABLED: ${SANDBOX_EGRESS_POLICY_ENABLED:-false}",
            "SANDBOX_EGRESS_PROOF_SIGNING_KEY: ${SANDBOX_EGRESS_PROOF_SIGNING_KEY:-}",
            "SANDBOX_EGRESS_PROOF_KEY_ID: ${SANDBOX_EGRESS_PROOF_KEY_ID:-current}",
            "SANDBOX_EGRESS_PROOF_PREVIOUS_KEYS_JSON: ${SANDBOX_EGRESS_PROOF_PREVIOUS_KEYS_JSON:-}",
            "SANDBOX_CALLBACK_HOST_GATEWAY: ${SANDBOX_CALLBACK_HOST_GATEWAY:-}",
        ]:
            assert expected in service_text

    api_text = compose_service_text(compose_text, "api")
    assert "healthcheck:" in api_text
    assert "/api/ai/ready" in api_text
    assert "AI_PLATFORM_RUNTIME_COMMIT" in api_text


def test_compose_defines_new_internal_egress_network_for_api_callback_only():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    api_text = compose_service_text(compose_text, "api")

    assert "name: ai-platform-sandbox-egress-internal-v1" in compose_text
    assert "internal: true" in compose_text
    assert 'com.docker.network.bridge.enable_ip_masquerade: "false"' in compose_text
    assert "api.sandbox.internal" in api_text
    assert compose_text.count("sandbox_egress_internal_v1:") == 2


def test_compose_requires_core_production_secrets():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")

    for required in [
        "${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD}",
        "${MINIO_ROOT_PASSWORD:?set MINIO_ROOT_PASSWORD}",
        "${TRUSTED_PRINCIPAL_SECRET:?set TRUSTED_PRINCIPAL_SECRET}",
        "${AI_SESSION_SECRET:?set AI_SESSION_SECRET}",
    ]:
        assert required in compose_text

    assert "ai_platform_dev_password" not in compose_text
    assert "ai_platform_minio_password" not in compose_text
    assert "TRUSTED_PRINCIPAL_SECRET: ${TRUSTED_PRINCIPAL_SECRET:-}" not in compose_text
    assert "AI_SESSION_SECRET: ${AI_SESSION_SECRET:-}" not in compose_text
