from __future__ import annotations

from pathlib import Path

DEPLOY_DIR = Path("deploy/ai-platform")
COMPOSE_FILE = DEPLOY_DIR / "docker-compose.yml"
SANDBOX_COMPOSE_FILE = DEPLOY_DIR / "docker-compose.sandbox.yml"
OPENSANDBOX_COMPOSE_FILE = DEPLOY_DIR / "docker-compose.opensandbox.yml"
OPENSANDBOX_INTERNAL_TEST_COMPOSE_FILE = DEPLOY_DIR / "docker-compose.opensandbox-internal-test.yml"
S72_COLOCATION_COMPOSE_FILE = DEPLOY_DIR / "docker-compose.s72-colocation.yml"
ENV_EXAMPLE_FILE = DEPLOY_DIR / ".env.example"
REPOSITORY_DEPLOY_ENV = "${PROJECT_DIR}/deploy/ai-platform/.env"


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


def test_company_auth_requires_operator_managed_endpoints_for_api_and_worker():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    env_example_text = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")
    env_values = env_example_values(env_example_text)

    for service_name in ("api", "worker"):
        service = compose_service_text(compose_text, service_name)
        assert "EXISTING_AUTH_BASE_URL: ${EXISTING_AUTH_BASE_URL:?set EXISTING_AUTH_BASE_URL}" in service
        assert "EXISTING_USER_INFO_BASE_URL: ${EXISTING_USER_INFO_BASE_URL:?set EXISTING_USER_INFO_BASE_URL}" in service
    assert "10.56.0.25" not in compose_text
    assert env_values["EXISTING_AUTH_BASE_URL"] == "http://10.56.0.25:7263"
    assert env_values["EXISTING_USER_INFO_BASE_URL"] == "http://10.56.0.25:5166"


def test_compose_projects_all_browser_authentication_windows_as_twenty_four_hours():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    env_values = env_example_values(ENV_EXAMPLE_FILE.read_text(encoding="utf-8"))

    for service_name in ("api", "worker"):
        service = compose_service_text(compose_text, service_name)
        assert "AI_SESSION_MAX_AGE_SECONDS: ${AI_SESSION_MAX_AGE_SECONDS:-86400}" in service
        assert "AUTH_CONTEXT_MAX_AGE_SECONDS: ${AUTH_CONTEXT_MAX_AGE_SECONDS:-86400}" in service
        assert (
            "COMPANY_AUTHORITY_FRESHNESS_SECONDS: "
            "${COMPANY_AUTHORITY_FRESHNESS_SECONDS:-86400}"
        ) in service
    assert env_values["AI_SESSION_MAX_AGE_SECONDS"] == "86400"
    assert env_values["AUTH_CONTEXT_MAX_AGE_SECONDS"] == "86400"
    assert env_values["COMPANY_AUTHORITY_FRESHNESS_SECONDS"] == "86400"


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


def test_run_api_with_deploy_env_derives_database_and_s3_settings():
    script = Path("tools/run_api_with_deploy_env.sh")

    text = script.read_text(encoding="utf-8")

    assert REPOSITORY_DEPLOY_ENV in text
    assert "/home/" not in text
    assert 'PORT="${AI_PLATFORM_PORT:-8020}"' in text
    assert "Default: 8020" in text
    assert "18080" not in text
    assert 'DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:${POSTGRES_PORT}/${POSTGRES_DB}"' in text
    assert 'S3_ENDPOINT_URL="http://localhost:${MINIO_API_PORT}"' in text
    assert 'S3_ACCESS_KEY_ID="${MINIO_ROOT_USER}"' in text
    assert 'S3_SECRET_ACCESS_KEY="${MINIO_ROOT_PASSWORD}"' in text
    assert 'CLAUDE_AGENT_SDK_ENABLED=false' in text
    assert "--check-env" in text
    assert "sed -E 's/=.*/=SET/'" in text
    assert "TRUSTED_PRINCIPAL_SECRET|CLAUDE_AGENT_SDK_ENABLED" not in text


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


def test_compose_forwards_bounded_redis_pool_to_api_and_worker_without_limiting_server():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    env_example_text = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")
    api_section = compose_text.split("\n  api:", 1)[1].split("\n  worker:", 1)[0]
    worker_section = compose_text.split("\n  worker:", 1)[1].split("\nvolumes:", 1)[0]
    redis_section = compose_text.split("\n  redis:", 1)[1].split("\n  minio:", 1)[0]

    assert "REDIS_MAX_CONNECTIONS=10" in env_example_text
    assert "REDIS_MAX_CONNECTIONS: ${REDIS_MAX_CONNECTIONS:-10}" in api_section
    assert "REDIS_MAX_CONNECTIONS: ${REDIS_MAX_CONNECTIONS:-10}" in worker_section
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


def test_dockerfile_installs_git_for_sdk_agent_worktrees():
    content = Path("Dockerfile").read_text(encoding="utf-8")

    assert "apt-get install -y --no-install-recommends fontconfig fonts-noto-cjk git" in content


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


def test_compose_workspace_init_is_narrow_and_blocks_api_and_worker_until_success():
    import yaml

    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    init = compose["services"]["workspace-init"]

    assert init["user"] == "0:0"
    assert init["network_mode"] == "none"
    assert init["read_only"] is True
    assert init["restart"] == "no"
    assert init["cap_drop"] == ["ALL"]
    assert set(init["cap_add"]) == {"CHOWN", "DAC_READ_SEARCH", "SETUID", "SETGID"}
    assert init["entrypoint"] == ["python", "-m", "app.runtime.sandbox.workspace_permissions"]
    assert init["command"] == []
    assert init["volumes"] == ["ai_platform_sandbox_workspaces:/runtime-workspaces"]
    for service_name in ("api", "worker"):
        assert compose["services"][service_name]["depends_on"]["workspace-init"]["condition"] == "service_completed_successfully"


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
    assert "SANDBOX_CONTAINER_PROVIDER" in compose_text
    assert "SANDBOX_EXECUTOR_IMAGE" in compose_text
    assert "SANDBOX_CALLBACK_BASE_URL" in compose_text
    assert "SANDBOX_CALLBACK_TOKEN" in compose_text
    assert "SANDBOX_WORKSPACE_ROOT" in compose_text
    assert "/var/run/docker.sock:/var/run/docker.sock" in sandbox_text
    assert (
        "${SANDBOX_WORKSPACE_ROOT:-/tmp/ai-platform-sandbox-workspaces}:"
        "${SANDBOX_WORKSPACE_ROOT:-/tmp/ai-platform-sandbox-workspaces}"
    ) in sandbox_text
    assert "SANDBOX_HOST_WORKSPACE_ROOT" not in sandbox_text
    assert "ai_platform_sandbox_workspaces" in compose_text
    assert "SANDBOX_CONTAINER_PROVIDER: docker" in sandbox_text


def test_opensandbox_overlay_pins_governed_profile_and_requires_bridge_inputs():
    import yaml

    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    overlay = yaml.safe_load(OPENSANDBOX_COMPOSE_FILE.read_text(encoding="utf-8"))
    env_example = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")

    for service_name in ("api", "worker"):
        assert compose["services"][service_name]["environment"]["SANDBOX_SECURITY_PROFILE"] == "governed"
        environment = overlay["services"][service_name]["environment"]
        assert environment["SANDBOX_CONTAINER_PROVIDER"] == "opensandbox"
        assert environment["SANDBOX_SECURITY_PROFILE"] == "governed"
        for required in (
            "SANDBOX_EGRESS_PROOF_SIGNING_KEY",
            "SANDBOX_RUNTIME_SUBJECT",
            "OPENSANDBOX_DOMAIN",
            "OPENSANDBOX_PROTOCOL",
            "OPENSANDBOX_API_KEY",
            "OPENSANDBOX_EXECUTOR_IMAGE",
            "OPENSANDBOX_EXECUTOR_IMAGE_DIGEST",
            "OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_URL",
            "OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_TOKEN",
            "OPENSANDBOX_EXTERNAL_EGRESS_GATEWAY_POLICY_SUBJECT",
            "OPENSANDBOX_EXTERNAL_EGRESS_CALLBACK_BOUNDARY_SUBJECT",
            "OPENSANDBOX_EXTERNAL_EGRESS_CALLBACK_BASE_URL",
            "OPENSANDBOX_EXTERNAL_EGRESS_OPENAI_BASE_URL",
            "OPENSANDBOX_EXTERNAL_EGRESS_ANTHROPIC_BASE_URL",
        ):
            assert environment[required].startswith("${")
            assert ":?set " in environment[required]

    frontend = overlay["services"]["frontend"]
    assert frontend["environment"]["NGINX_ENVSUBST_TEMPLATE_DIR"] == "/etc/nginx/templates-opensandbox"
    assert frontend["ports"] == ["${AI_PLATFORM_S72_BRIDGE_PORT:-18443}:8443"]
    assert len(frontend["volumes"]) == 2
    assert set(overlay["services"]) == {"api", "worker", "frontend"}
    assert "SANDBOX_SECURITY_PROFILE=governed" in env_example
    assert "trusted_internal" not in env_example
    assert "OPENSANDBOX_TRUSTED_INTERNAL_" not in env_example


def test_opensandbox_internal_test_overlay_is_explicit_direct_and_has_no_gateway_contract():
    import yaml

    overlay = yaml.safe_load(OPENSANDBOX_INTERNAL_TEST_COMPOSE_FILE.read_text(encoding="utf-8"))

    assert set(overlay["services"]) == {"api", "worker"}
    for service_name in ("api", "worker"):
        environment = overlay["services"][service_name]["environment"]
        assert environment["DEPLOYMENT_ENVIRONMENT"] == "test"
        assert environment["SANDBOX_CONTAINER_PROVIDER"] == "opensandbox"
        assert environment["SANDBOX_SECURITY_PROFILE"] == "internal-test"
        assert environment["OPENSANDBOX_EXPECTED_NETWORK_MODE"] == "bridge"
        assert environment["OPENSANDBOX_INTERNAL_TEST_FORWARD_MODEL_CREDENTIALS"] == "true"
        assert environment["OPENSANDBOX_USE_SERVER_PROXY"] == "false"
        assert environment["OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_URL"] == ""
        assert environment["OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_TOKEN"] == ""
        assert "SANDBOX_EGRESS_PROOF_SIGNING_KEY" not in environment
        assert "volumes" not in overlay["services"][service_name]


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
    colocation_text = S72_COLOCATION_COMPOSE_FILE.read_text(encoding="utf-8")

    for expected in [
        "SANDBOX_CONTAINER_PROVIDER=opensandbox",
        "SANDBOX_SECURITY_PROFILE=governed",
        "SANDBOX_EXECUTOR_IMAGE=ai-platform:local",
        "SANDBOX_EXECUTOR_PUBLISHED_HOST=host.docker.internal",
        "SANDBOX_WORKSPACE_ROOT=/tmp/ai-platform-sandbox-workspaces",
        "SANDBOX_CALLBACK_BASE_URL=http://api.sandbox.internal:8020",
        "SANDBOX_EGRESS_POLICY_ENABLED=false",
        "SANDBOX_EGRESS_NETWORK_NAME=ai-platform-sandbox-egress-internal-v1",
        "SANDBOX_EGRESS_PROOF_SIGNING_KEY=replace_me_with_a_random_32_byte_minimum_value",
        "SANDBOX_EGRESS_PROOF_KEY_ID=current",
        "SANDBOX_EGRESS_PROOF_PREVIOUS_KEYS_JSON=",
        "SANDBOX_CALLBACK_HOST_GATEWAY=",
    ]:
        assert expected in env_example_text

    assert "SANDBOX_CONTAINER_PROVIDER=fake" not in env_example_text
    assert "SANDBOX_CALLBACK_TOKEN=change_me_sandbox_callback_token" not in env_example_text
    assert colocation_text.count("SANDBOX_CONTAINER_PROVIDER: opensandbox") == 2
    assert colocation_text.count("SANDBOX_SECURITY_PROFILE: governed") == 2
    assert colocation_text.count(
        "OPENSANDBOX_EXTERNAL_EGRESS_CALLBACK_BASE_URL: http://127.0.0.1:18043"
    ) == 2
    assert colocation_text.count(
        "OPENSANDBOX_EXTERNAL_EGRESS_OPENAI_BASE_URL: http://127.0.0.1:18043/openai/v1"
    ) == 2
    assert colocation_text.count(
        "OPENSANDBOX_EXTERNAL_EGRESS_ANTHROPIC_BASE_URL: http://127.0.0.1:18043/anthropic"
    ) == 2


def test_compose_passes_sandbox_egress_policy_env_to_api_and_worker():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")

    for service_name in ("api", "worker"):
        service_text = compose_service_text(compose_text, service_name)
        for expected in [
            "SANDBOX_EGRESS_POLICY_ENABLED: ${SANDBOX_EGRESS_POLICY_ENABLED:-false}",
            "SANDBOX_EGRESS_NETWORK_NAME: ${SANDBOX_EGRESS_NETWORK_NAME:-ai-platform-sandbox-egress-internal-v1}",
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
