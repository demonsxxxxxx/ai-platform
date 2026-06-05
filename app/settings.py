from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(default="postgresql://ai_platform:ai_platform_dev_password@localhost:54329/ai_platform")
    redis_url: str = Field(default="redis://localhost:63799/0")
    queue_key_prefix: str = Field(default="ai-platform:runs")

    s3_endpoint_url: str = Field(default="http://localhost:9009")
    s3_access_key_id: str = Field(default="ai_platform_minio")
    s3_secret_access_key: str = Field(default="ai_platform_minio_password")
    s3_bucket: str = Field(default="ai-platform-artifacts")
    s3_region: str = Field(default="us-east-1")

    runtime_211_base_url: str = Field(default="")
    sandbox_workspace_root: str = Field(default="/tmp/ai-platform-sandbox-workspaces")
    sandbox_container_provider: str = Field(default="fake")
    sandbox_executor_image: str = Field(default="ai-platform-executor:dev")
    sandbox_executor_browser_image: str = Field(default="")
    sandbox_executor_published_host: str = Field(default="127.0.0.1")
    sandbox_callback_base_url: str = Field(default="http://127.0.0.1:8000")
    sandbox_callback_token: str = Field(default="")
    sandbox_container_start_timeout_seconds: int = Field(default=30)
    sandbox_executor_health_timeout_seconds: int = Field(default=60)
    sandbox_max_active_ephemeral_containers: int = Field(default=2)
    sandbox_max_active_persistent_containers: int = Field(default=1)
    max_active_runs_per_user: int = Field(default=3)
    max_active_worker_runs: int = Field(default=3)
    worker_heartbeat_ttl_seconds: float = Field(default=60.0)
    memory_retention_worker_cleanup_enabled: bool = Field(default=True)
    memory_retention_worker_cleanup_interval_seconds: float = Field(default=300.0)
    memory_retention_worker_cleanup_limit: int = Field(default=200)
    multi_agent_dispatch_lease_ttl_seconds: int = Field(default=900)
    run_event_stream_max_heartbeats: int = Field(default=3600)
    default_tenant_id: str = Field(default="default")
    default_workspace_id: str = Field(default="default")
    cors_allow_origins: str = Field(
        default="http://localhost:9527,http://127.0.0.1:9527,http://10.56.0.211:8080,http://10.56.0.211:18001"
    )
    trusted_principal_secret: str = Field(default="")
    frontend_poc_auth_enabled: bool = Field(default=False)
    existing_auth_base_url: str = Field(default="http://10.56.0.25:7263")
    existing_user_info_base_url: str = Field(default="http://10.56.0.25:5166")
    existing_auth_timeout_seconds: float = Field(default=15.0)
    ai_admin_work_ids: str = Field(default="")
    ai_session_secret: str = Field(default="")
    ai_session_cookie_name: str = Field(default="ai_platform_session")
    ai_session_cookie_secure: bool = Field(default=False)
    ai_session_max_age_seconds: int = Field(default=8 * 60 * 60)
    artifact_default_retention_days: int = Field(default=90)

    llm_gateway_provider: str = Field(default="openai_compatible")
    openai_base_url: str = Field(default="")
    openai_api_key: str = Field(default="")
    openai_model: str = Field(default="deepseek-v4-flash")
    anthropic_base_url: str = Field(default="")
    anthropic_auth_token: str = Field(default="")
    anthropic_model: str = Field(default="deepseek-v4-flash")
    claude_agent_model: str = Field(default="deepseek-v4-flash")
    claude_agent_sdk_enabled: bool = Field(default=False)
    claude_agent_sdk_timeout_seconds: float = Field(default=120.0)
    claude_agent_sdk_max_turns: int = Field(default=12)
    claude_agent_allowed_tools: str = Field(default="Read,Glob,LS")
    claude_agent_disallowed_tools: str = Field(default="Write,Edit,NotebookEdit")
    claude_agent_permission_mode: str = Field(default="dontAsk")
    claude_agent_workspace_root: str = Field(default="/tmp/ai-platform-agent-workspaces")
    claude_agent_sdk_skills: str = Field(default="")
    platform_skills_root: str = Field(default="skills")
    skill_staging_subdir: str = Field(default=".claude/skills")
    enable_legacy_runtime211_fallback: bool = Field(default=False)

    ragflow_api_url: str = Field(default="")
    ragflow_api_key: str = Field(default="")
    ragflow_default_dataset_id: str = Field(default="")
    ragflow_timeout_seconds: float = Field(default=30.0)
    ragflow_top_k: int = Field(default=3)
    ragflow_similarity_threshold: float = Field(default=0.2)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
