from __future__ import annotations

from pathlib import Path

DEPLOY_DIR = Path("deploy/ai-platform")
COMPOSE_FILE = DEPLOY_DIR / "docker-compose.yml"
SANDBOX_COMPOSE_FILE = DEPLOY_DIR / "docker-compose.sandbox.yml"
ENV_EXAMPLE_FILE = DEPLOY_DIR / ".env.example"
TARGET_211_DEPLOY_ENV = "/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform/.env"
STALE_211_DEPLOY_ENV = "/home/xinlin.jiang/ai-platform-phaseb/deploy/ai-platform/.env"


def test_company_auth_defaults_match_webui_production_backend():
    settings_text = Path("app/settings.py").read_text(encoding="utf-8")
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    env_example_text = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")

    assert 'existing_auth_base_url: str = Field(default="http://10.56.0.25:7263")' in settings_text
    assert 'existing_user_info_base_url: str = Field(default="http://10.56.0.25:5166")' in settings_text
    assert "EXISTING_AUTH_BASE_URL:-http://10.56.0.25:7263" in compose_text
    assert "EXISTING_USER_INFO_BASE_URL:-http://10.56.0.25:5166" in compose_text
    assert "EXISTING_AUTH_BASE_URL=http://10.56.0.25:7263" in env_example_text
    assert "EXISTING_USER_INFO_BASE_URL=http://10.56.0.25:5166" in env_example_text
    assert "EXISTING_AUTH_BASE_URL=http://10.56.0.211" not in env_example_text


def test_cors_defaults_include_current_211_frontend_origin():
    settings_text = Path("app/settings.py").read_text(encoding="utf-8")
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    env_example_text = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")
    current_origin = "http://10.56.0.211:18001"

    assert current_origin in settings_text
    assert compose_text.count(current_origin) == 2
    assert current_origin in env_example_text


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


def test_run_api_with_deploy_env_derives_database_and_s3_settings():
    script = Path("tools/run_api_with_deploy_env.sh")

    text = script.read_text(encoding="utf-8")

    assert TARGET_211_DEPLOY_ENV in text
    assert STALE_211_DEPLOY_ENV not in text
    assert 'PORT="${AI_PLATFORM_PORT:-8020}"' in text
    assert "Default: 8020" in text
    assert "18080" not in text
    assert 'DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:${POSTGRES_PORT}/${POSTGRES_DB}"' in text
    assert 'S3_ENDPOINT_URL="http://localhost:${MINIO_API_PORT}"' in text
    assert 'S3_ACCESS_KEY_ID="${MINIO_ROOT_USER}"' in text
    assert 'S3_SECRET_ACCESS_KEY="${MINIO_ROOT_PASSWORD}"' in text
    assert "--check-env" in text
    assert "sed -E 's/=.*/=SET/'" in text


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
        "QUEUE_TENANT_PROCESSING_LIMIT": "0",
        "QUEUE_USER_PROCESSING_LIMIT": "0",
        "QUEUE_LEASE_SCAN_LIMIT": "50",
        "QUEUE_INSIGHT_SCAN_LIMIT": "500",
    }
    for name, default in expected_settings.items():
        assert f"{name}={default}" in env_example_text
        assert f"{name}: ${{{name}:-{default}}}" in api_section
        assert f"{name}: ${{{name}:-{default}}}" in worker_section


def test_poc_gate_default_env_path_matches_repo_local_211_deploy():
    text = Path("tools/verify_poc_gate.py").read_text(encoding="utf-8")

    assert f'DEFAULT_DEPLOY_ENV = "{TARGET_211_DEPLOY_ENV}"' in text
    assert STALE_211_DEPLOY_ENV not in text


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


def test_dockerfile_packages_release_evidence_for_runtime_readiness():
    content = Path("Dockerfile").read_text(encoding="utf-8")

    assert "COPY docs/release-evidence /app/docs/release-evidence" in content
    assert "COPY tools /app/tools" in content
    assert "COPY scripts /app/scripts" in content
    assert "COPY docs /app/docs" not in content


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


def test_compose_exposes_sandbox_runtime_configuration():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    sandbox_text = SANDBOX_COMPOSE_FILE.read_text(encoding="utf-8")

    for service_name in ["api:", "worker:"]:
        assert service_name in compose_text

    assert compose_text.count("context: ../..") == 2
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


def test_compose_does_not_mount_docker_socket_by_default():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    sandbox_text = SANDBOX_COMPOSE_FILE.read_text(encoding="utf-8")

    assert "/var/run/docker.sock:/var/run/docker.sock" not in compose_text
    assert "/var/run/docker.sock:/var/run/docker.sock" in sandbox_text
    assert "ai_platform_sandbox_workspaces:/tmp/ai-platform-sandbox-workspaces" not in sandbox_text


def test_compose_requires_non_empty_sandbox_callback_token():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    env_example_text = ENV_EXAMPLE_FILE.read_text(encoding="utf-8")

    assert "SANDBOX_CALLBACK_TOKEN: ${SANDBOX_CALLBACK_TOKEN:?set SANDBOX_CALLBACK_TOKEN}" in compose_text
    assert "SANDBOX_CALLBACK_TOKEN=change_me_sandbox_callback_token" in env_example_text


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
