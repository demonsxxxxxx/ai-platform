from functools import lru_cache
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


OBJECT_DELETE_LEGACY_ENV_SUPPORTED_UNTIL = "2026-10-31"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(
        default="postgresql://ai_platform:ai_platform_dev_password@localhost:54329/ai_platform"
    )
    database_pool_min_size: int = Field(default=1)
    database_pool_max_size: int = Field(default=10)
    database_pool_timeout_seconds: float = Field(default=10.0)
    database_pool_max_waiting: int = Field(default=100)
    database_pool_close_timeout_seconds: float = Field(default=5.0)
    redis_url: str = Field(default="redis://localhost:63799/0")
    redis_max_connections: int = Field(default=64, ge=1)
    datastore_readiness_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    queue_key_prefix: str = Field(default="ai-platform:runs")

    s3_endpoint_url: str = Field(default="http://localhost:9009")
    s3_access_key_id: str = Field(default="ai_platform_minio")
    s3_secret_access_key: str = Field(default="ai_platform_minio_password")
    s3_bucket: str = Field(default="ai-platform-artifacts")
    s3_region: str = Field(default="us-east-1")

    sandbox_workspace_root: str = Field(default="/tmp/ai-platform-sandbox-workspaces")
    sandbox_container_provider: str = Field(default="fake")
    # Production remains governed. The explicit internal-test profile exists
    # only for bounded functional acceptance against the official service.
    sandbox_security_profile: Literal["governed", "internal-test"] = Field(
        default="governed"
    )
    sandbox_executor_image: str = Field(default="ai-platform-executor:dev")
    sandbox_executor_browser_image: str = Field(default="")
    sandbox_executor_published_host: str = Field(default="127.0.0.1")
    sandbox_callback_base_url: str = Field(default="http://127.0.0.1:8000")
    sandbox_callback_token: str = Field(default="")
    sandbox_egress_policy_enabled: bool = Field(default=False)
    sandbox_egress_network_name: str = Field(
        default="ai-platform-sandbox-egress-internal-v1"
    )
    sandbox_egress_proof_signing_key: str = Field(default="")
    sandbox_egress_proof_key_id: str = Field(default="current")
    sandbox_egress_proof_previous_keys_json: str = Field(default="")
    ai_platform_runtime_commit: str = Field(default="")
    sandbox_callback_host_gateway: str = Field(default="host.docker.internal")
    sandbox_container_start_timeout_seconds: int = Field(default=30)
    sandbox_cleanup_timeout_seconds: int = Field(default=30)
    sandbox_executor_health_timeout_seconds: int = Field(default=60)
    sandbox_executor_dispatch_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    opensandbox_domain: str = Field(default="127.0.0.1:8080")
    opensandbox_protocol: str = Field(default="http")
    opensandbox_api_key: str = Field(default="")
    opensandbox_use_server_proxy: bool = Field(default=False)
    opensandbox_internal_test_forward_model_credentials: bool = Field(default=False)
    opensandbox_request_timeout_seconds: float = Field(default=30.0)
    opensandbox_timeout_seconds: int = Field(default=1800)
    opensandbox_executor_image: str = Field(default="")
    opensandbox_executor_entrypoint: str = Field(
        default="/app/docker-entrypoint.sh uvicorn"
    )
    opensandbox_workspace_mount_enabled: bool = Field(default=True)
    opensandbox_startup_io_probe_enabled: bool = Field(default=True)
    opensandbox_allowed_egress_hosts: str = Field(default="")
    sandbox_runtime_subject: str = Field(default="")
    opensandbox_external_egress_capability_url: str = Field(default="")
    opensandbox_external_egress_capability_token: str = Field(default="")
    opensandbox_external_egress_gateway_policy_subject: str = Field(default="")
    opensandbox_external_egress_callback_boundary_subject: str = Field(default="")
    opensandbox_external_egress_callback_base_url: str = Field(default="")
    opensandbox_external_egress_openai_base_url: str = Field(default="")
    opensandbox_external_egress_anthropic_base_url: str = Field(default="")
    opensandbox_executor_image_digest: str = Field(default="")
    opensandbox_expected_network_mode: Literal["none", "bridge"] = Field(default="none")
    sandbox_max_active_ephemeral_containers: int = Field(default=2)
    sandbox_max_active_persistent_containers: int = Field(default=1)
    max_active_runs_per_user: int = Field(default=3)
    max_active_worker_runs: int = Field(default=10)
    queue_tenant_processing_limit: int = Field(default=0)
    queue_user_processing_limit: int = Field(default=0)
    queue_lease_scan_limit: int = Field(default=50)
    queue_insight_scan_limit: int = Field(default=500)
    queue_lease_visibility_timeout_seconds: int = Field(default=900)
    queue_metadata_fallback_scan_limit: int = Field(default=500)
    queue_dead_letter_max_entries: int = Field(default=1000, ge=1, le=100000)
    worker_heartbeat_ttl_seconds: float = Field(default=60.0)
    worker_maintenance_interval_seconds: float = Field(default=30.0)
    stale_run_reconciliation_seconds: int = Field(default=900, ge=60, le=86400)
    stale_run_reconciliation_limit: int = Field(default=20, ge=1, le=50)
    stale_run_reconciliation_fence_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    sandbox_lease_ttl_seconds: int = Field(default=1800, ge=60, le=86400)
    worker_concurrency: int = Field(default=10)
    memory_retention_worker_cleanup_enabled: bool = Field(default=True)
    memory_retention_worker_cleanup_interval_seconds: float = Field(default=300.0)
    memory_retention_worker_cleanup_limit: int = Field(default=200)
    data_retention_worker_cleanup_enabled: bool = Field(default=True)
    data_retention_worker_cleanup_interval_seconds: float = Field(default=300.0, ge=1.0)
    artifact_retention_cleanup_limit: int = Field(default=50, ge=1, le=200)
    object_delete_batch_limit: int = Field(default=50, ge=1, le=200)
    object_delete_max_attempts: int = Field(
        default=5,
        ge=1,
        le=100,
        validation_alias=AliasChoices(
            "object_delete_max_attempts",
            "OBJECT_DELETE_MAX_ATTEMPTS",
            "artifact_object_delete_max_attempts",
            "ARTIFACT_OBJECT_DELETE_MAX_ATTEMPTS",
        ),
    )
    object_delete_retry_base_seconds: int = Field(
        default=60,
        ge=1,
        le=3600,
        validation_alias=AliasChoices(
            "object_delete_retry_base_seconds",
            "OBJECT_DELETE_RETRY_BASE_SECONDS",
            "artifact_object_delete_retry_base_seconds",
            "ARTIFACT_OBJECT_DELETE_RETRY_BASE_SECONDS",
        ),
    )
    object_delete_retry_cap_seconds: int = Field(
        default=3600,
        ge=1,
        le=86400,
        validation_alias=AliasChoices(
            "object_delete_retry_cap_seconds",
            "OBJECT_DELETE_RETRY_CAP_SECONDS",
            "artifact_object_delete_retry_cap_seconds",
            "ARTIFACT_OBJECT_DELETE_RETRY_CAP_SECONDS",
        ),
    )
    memory_physical_purge_limit: int = Field(default=50, ge=1, le=200)
    memory_physical_purge_grace_days: int = Field(default=7, ge=1, le=3650)
    run_event_retention_days: int = Field(default=0, ge=0)
    context_snapshot_retention_days: int = Field(default=0, ge=0)
    audit_retention_days: int = Field(default=0, ge=0)
    message_retention_days: int = Field(default=0, ge=0)
    file_retention_days: int = Field(default=0, ge=0)
    run_event_stream_max_heartbeats: int = Field(default=3600)
    deployment_environment: Literal["development", "test", "production"] = Field(
        default="development"
    )
    default_tenant_id: str = Field(default="default")
    default_workspace_id: str = Field(default="default")
    cors_allow_origins: str = Field(
        default="http://localhost:9527,http://127.0.0.1:9527"
    )
    trusted_principal_secret: str = Field(default="")
    frontend_poc_auth_enabled: bool = Field(default=False)
    browser_public_launchpad_lingxi_url: str | None = Field(default=None)
    browser_public_launchpad_sop_url: str | None = Field(default=None)
    browser_public_launchpad_word_translate_url: str | None = Field(default=None)
    browser_public_launchpad_word_review_url: str | None = Field(default=None)
    existing_auth_base_url: str = Field(default="")
    existing_user_info_base_url: str = Field(default="")
    existing_auth_timeout_seconds: float = Field(default=15.0)
    ai_admin_work_ids: str = Field(default="")
    ai_session_secret: str = Field(default="")
    ai_session_cookie_name: str = Field(default="ai_platform_session")
    ai_session_cookie_secure: bool = Field(default=False)
    ai_session_max_age_seconds: int = Field(
        default=24 * 60 * 60, ge=24 * 60 * 60, le=24 * 60 * 60
    )
    company_authority_freshness_seconds: int = Field(
        default=24 * 60 * 60, ge=24 * 60 * 60, le=24 * 60 * 60
    )
    auth_context_secret: str = Field(default="")
    auth_context_cookie_name: str = Field(default="ai_platform_auth_context")
    auth_context_cookie_secure: bool = Field(default=False)
    auth_context_max_age_seconds: int = Field(
        default=24 * 60 * 60, ge=24 * 60 * 60, le=24 * 60 * 60
    )
    auth_context_lease_seconds: int = Field(default=90)
    artifact_default_retention_days: int = Field(default=90)

    # The user JWT remains short-lived; the same rotating keyring also seals
    # write-only static MCP connection headers stored by the registry.
    mcp_context_encryption_keys_json: str = Field(default="")
    mcp_context_encryption_key: str = Field(default="")
    mcp_context_current_key_id: str = Field(default="current")
    mcp_context_ttl_seconds: int = Field(default=300, ge=1, le=300)
    mcp_context_lease_seconds: int = Field(default=1800, ge=1, le=1800)
    mcp_relay_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    mcp_relay_max_response_bytes: int = Field(default=1024 * 1024, ge=1024, le=16 * 1024 * 1024)

    llm_gateway_provider: str = Field(default="openai_compatible")
    model_gateway_request_concurrency_limit: int = Field(default=0)
    openai_base_url: str = Field(default="")
    openai_api_key: str = Field(default="")
    openai_model: str = Field(default="deepseek-v4-flash")
    anthropic_base_url: str = Field(default="")
    anthropic_auth_token: str = Field(default="")
    anthropic_model: str = Field(default="deepseek-v4-flash")
    claude_agent_model: str = Field(default="deepseek-v4-flash")
    default_model_id: str = Field(default="")
    model_catalog_json: str = Field(default="")
    claude_agent_sdk_enabled: bool = Field(default=False)
    claude_agent_sdk_timeout_seconds: float = Field(default=0.0)
    claude_agent_sdk_max_turns: int = Field(default=128)
    claude_agent_sdk_effort: str = Field(default="xhigh")
    claude_agent_sdk_max_thinking_tokens: int = Field(default=16384)
    claude_agent_allowed_tools: str = Field(default="Read,Glob,LS")
    claude_agent_disallowed_tools: str = Field(default="Write,Edit,NotebookEdit")
    claude_agent_permission_mode: str = Field(default="dontAsk")
    claude_agent_workspace_root: str = Field(
        default="/tmp/ai-platform-agent-workspaces"
    )
    claude_agent_sdk_skills: str = Field(default="")
    platform_skills_root: str = Field(default="skills")
    skill_staging_subdir: str = Field(default=".claude/skills")
    public_skill_file_overlay_max_bytes: int = Field(default=262144)

    @property
    def artifact_object_delete_max_attempts(self) -> int:
        """Deprecated Python alias for the shared object-deletion setting."""

        return self.object_delete_max_attempts

    @artifact_object_delete_max_attempts.setter
    def artifact_object_delete_max_attempts(self, value: int) -> None:
        self.object_delete_max_attempts = value

    @property
    def artifact_object_delete_retry_base_seconds(self) -> int:
        """Deprecated Python alias for the shared object-deletion setting."""

        return self.object_delete_retry_base_seconds

    @artifact_object_delete_retry_base_seconds.setter
    def artifact_object_delete_retry_base_seconds(self, value: int) -> None:
        self.object_delete_retry_base_seconds = value

    @property
    def artifact_object_delete_retry_cap_seconds(self) -> int:
        """Deprecated Python alias for the shared object-deletion setting."""

        return self.object_delete_retry_cap_seconds

    @artifact_object_delete_retry_cap_seconds.setter
    def artifact_object_delete_retry_cap_seconds(self, value: int) -> None:
        self.object_delete_retry_cap_seconds = value

    @model_validator(mode="before")
    @classmethod
    def apply_legacy_object_delete_batch_fallback(cls, values: Any) -> Any:
        if (
            isinstance(values, dict)
            and "object_delete_batch_limit" not in values
            and "artifact_retention_cleanup_limit" in values
        ):
            values = dict(values)
            values["object_delete_batch_limit"] = values[
                "artifact_retention_cleanup_limit"
            ]
        return values

    @field_validator(
        "browser_public_launchpad_lingxi_url",
        "browser_public_launchpad_sop_url",
        "browser_public_launchpad_word_translate_url",
        "browser_public_launchpad_word_review_url",
        mode="before",
    )
    @classmethod
    def validate_browser_public_launchpad_url(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("browser_public_launchpad_url_must_be_string")
        candidate = value.strip()
        if not candidate:
            return None
        if len(candidate) > 2048 or any(character.isspace() for character in candidate):
            raise ValueError("browser_public_launchpad_url_invalid")
        try:
            parsed = urlsplit(candidate)
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("browser_public_launchpad_url_invalid") from exc
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or "=" in parsed.fragment
        ):
            raise ValueError("browser_public_launchpad_url_invalid")
        return candidate

    @field_validator(
        "existing_auth_base_url",
        "existing_user_info_base_url",
        mode="before",
    )
    @classmethod
    def validate_private_upstream_base_url(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("private_upstream_url_must_be_string")
        candidate = value.strip()
        if not candidate:
            return ""
        if len(candidate) > 2048 or any(character.isspace() for character in candidate):
            raise ValueError("private_upstream_url_invalid")
        try:
            parsed = urlsplit(candidate)
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("private_upstream_url_invalid") from exc
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("private_upstream_url_invalid")
        return candidate

    @model_validator(mode="after")
    def validate_single_enterprise_identity_boundary(self) -> "Settings":
        if self.default_tenant_id != "default":
            raise ValueError("default_tenant_id_must_be_default_deployment_scope")
        if self.object_delete_retry_cap_seconds < self.object_delete_retry_base_seconds:
            raise ValueError("object_delete_retry_cap_below_base")
        if self.sandbox_security_profile == "internal-test" and not (
            self.deployment_environment == "test"
            and self.sandbox_container_provider == "opensandbox"
            and self.opensandbox_expected_network_mode == "bridge"
        ):
            raise ValueError("internal_test_opensandbox_profile_invalid")
        if self.opensandbox_internal_test_forward_model_credentials:
            if not (
                self.deployment_environment == "test"
                and self.sandbox_container_provider == "opensandbox"
                and self.sandbox_security_profile == "internal-test"
                and self.opensandbox_expected_network_mode == "bridge"
            ):
                raise ValueError("internal_test_model_credential_forwarding_invalid")
            if not self.openai_api_key.strip() or not self.anthropic_auth_token.strip():
                raise ValueError("internal_test_model_credentials_required")
        if self.deployment_environment == "production":
            if self.frontend_poc_auth_enabled:
                raise ValueError("frontend_poc_auth_forbidden_in_production")
            if not self.trusted_principal_secret.strip():
                raise ValueError("trusted_principal_secret_required_in_production")
            unsupported_retention = {
                "run_events": self.run_event_retention_days,
                "context_snapshots": self.context_snapshot_retention_days,
                "audit": self.audit_retention_days,
                "messages": self.message_retention_days,
                "files": self.file_retention_days,
            }
            if any(days > 0 for days in unsupported_retention.values()):
                raise ValueError("unsupported_retention_policy_in_production")
            missing_private_upstreams = sorted(
                field_name
                for field_name in (
                    "existing_auth_base_url",
                    "existing_user_info_base_url",
                )
                if not str(getattr(self, field_name)).strip()
            )
            if missing_private_upstreams:
                raise ValueError(
                    "private_upstream_url_required_in_production:"
                    + ",".join(missing_private_upstreams)
                )
            if not (
                self.mcp_context_encryption_keys_json.strip()
                or self.mcp_context_encryption_key.strip()
            ):
                raise ValueError("mcp_context_encryption_key_required_in_production")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
