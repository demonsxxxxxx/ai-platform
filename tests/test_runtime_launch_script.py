from __future__ import annotations

from pathlib import Path


def test_company_auth_defaults_match_webui_production_backend():
    settings_text = Path("app/settings.py").read_text(encoding="utf-8")
    compose_text = Path("../../deploy/ai-platform/docker-compose.yml").read_text(encoding="utf-8")

    assert 'existing_auth_base_url: str = Field(default="http://10.56.0.25:7263")' in settings_text
    assert 'existing_user_info_base_url: str = Field(default="http://10.56.0.25:5166")' in settings_text
    assert "EXISTING_AUTH_BASE_URL:-http://10.56.0.25:7263" in compose_text
    assert "EXISTING_USER_INFO_BASE_URL:-http://10.56.0.25:5166" in compose_text


def test_run_api_with_deploy_env_derives_database_and_s3_settings():
    script = Path("tools/run_api_with_deploy_env.sh")

    text = script.read_text(encoding="utf-8")

    assert 'PORT="${AI_PLATFORM_PORT:-8020}"' in text
    assert "Default: 8020" in text
    assert "18080" not in text
    assert 'DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:${POSTGRES_PORT}/${POSTGRES_DB}"' in text
    assert 'S3_ENDPOINT_URL="http://localhost:${MINIO_API_PORT}"' in text
    assert 'S3_ACCESS_KEY_ID="${MINIO_ROOT_USER}"' in text
    assert 'S3_SECRET_ACCESS_KEY="${MINIO_ROOT_PASSWORD}"' in text
    assert "--check-env" in text
    assert "sed -E 's/=.*/=SET/'" in text


def test_dockerfile_can_start_sandbox_executor_app():
    content = Path("Dockerfile").read_text(encoding="utf-8")

    assert "EXPOSE 8020" in content
    assert "EXPOSE 8020 18000" not in content
    assert "APP_MODULE" in content
    assert "app.runtime.sandbox.executor_app:create_executor_app" in content
    assert "APP_PORT" in content
    assert "docker-entrypoint.sh" in content
    assert 'CMD ["uvicorn"]' in content


def test_docker_entrypoint_validates_runtime_env():
    content = Path("docker-entrypoint.sh").read_text(encoding="utf-8")

    assert "case \"$APP_MODULE\"" in content
    assert "app.main:create_app" in content
    assert "app.runtime.sandbox.executor_app:create_executor_app" in content
    assert 'if [ "${1:-}" = "uvicorn" ]' in content
    assert 'exec "$@"' in content
    assert "exec \"$@\"" in content


def test_compose_exposes_sandbox_runtime_configuration():
    compose_text = Path("../../deploy/ai-platform/docker-compose.yml").read_text(encoding="utf-8")
    sandbox_text = Path("../../deploy/ai-platform/docker-compose.sandbox.yml").read_text(encoding="utf-8")

    for service_name in ["api:", "worker:"]:
        assert service_name in compose_text

    assert "SANDBOX_CONTAINER_PROVIDER" in compose_text
    assert "SANDBOX_EXECUTOR_IMAGE" in compose_text
    assert "SANDBOX_CALLBACK_BASE_URL" in compose_text
    assert "SANDBOX_CALLBACK_TOKEN" in compose_text
    assert "SANDBOX_WORKSPACE_ROOT" in compose_text
    assert "/var/run/docker.sock:/var/run/docker.sock" in sandbox_text
    assert (
        "${SANDBOX_HOST_WORKSPACE_ROOT:-/tmp/ai-platform-sandbox-workspaces}:"
        "${SANDBOX_WORKSPACE_ROOT:-/tmp/ai-platform-sandbox-workspaces}"
    ) in sandbox_text
    assert "ai_platform_sandbox_workspaces" in compose_text
    assert "SANDBOX_CONTAINER_PROVIDER: docker" in sandbox_text


def test_compose_does_not_mount_docker_socket_by_default():
    compose_text = Path("../../deploy/ai-platform/docker-compose.yml").read_text(encoding="utf-8")
    sandbox_text = Path("../../deploy/ai-platform/docker-compose.sandbox.yml").read_text(encoding="utf-8")

    assert "/var/run/docker.sock:/var/run/docker.sock" not in compose_text
    assert "/var/run/docker.sock:/var/run/docker.sock" in sandbox_text
    assert "ai_platform_sandbox_workspaces:/tmp/ai-platform-sandbox-workspaces" not in sandbox_text


def test_compose_requires_non_empty_sandbox_callback_token():
    compose_text = Path("../../deploy/ai-platform/docker-compose.yml").read_text(encoding="utf-8")

    assert "SANDBOX_CALLBACK_TOKEN: ${SANDBOX_CALLBACK_TOKEN:?set SANDBOX_CALLBACK_TOKEN}" in compose_text
