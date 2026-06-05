from pathlib import Path


def test_schema_declares_platform_fact_tables():
    schema = Path("app/schema.sql").read_text(encoding="utf-8")

    for table in [
        "tenants",
        "workspaces",
        "users",
        "agents",
        "skills",
        "tenant_workbench_skills",
        "mcp_tools",
        "tool_policies",
        "sessions",
        "messages",
        "memory_records",
        "run_context_snapshots",
        "runs",
        "run_events",
        "run_tool_permission_requests",
        "sandbox_leases",
        "files",
        "artifacts",
        "audit_logs",
    ]:
        assert f"create table if not exists {table}" in schema


def test_schema_seeds_first_agent_apps():
    schema = Path("app/schema.sql").read_text(encoding="utf-8")

    assert "qa-file-reviewer" in schema
    assert "'minimax-docx', 'Minimax DOCX'" in schema
    assert "baoyu-translate" in schema
    assert "ragflow-knowledge-search" in schema
    assert "ragflow_search" in schema
    assert "tenant_workbench_skills" in schema
    assert "'translate', 'default'" in schema
    assert "'document-review', 'default'" in schema
    assert "qa-word-review" in schema
    assert "sop-assistant" in schema
    assert "Legacy alias for qa-word-review" in schema
    assert "'qa-word-review', 'default', '文档审核', 'file'" in schema


def test_schema_enables_read_only_ragflow_mcp_tool_poc():
    schema = Path("app/schema.sql").read_text(encoding="utf-8")
    mcp_tool_seed = schema[schema.index("insert into mcp_tools"):schema.index("insert into agents")]

    assert "'ragflow-knowledge-search'" in mcp_tool_seed
    assert "'[\"ragflow_search\"]'::jsonb" in mcp_tool_seed
    assert "'active',\n    false,\n    'low'" in mcp_tool_seed
    assert "'disabled',\n    false,\n    'low'" not in mcp_tool_seed
    assert "insert into tool_policies" in mcp_tool_seed
    assert "('default', 'ragflow-knowledge-search', 'active', false, 'low', true" in mcp_tool_seed


def test_schema_seeds_internal_skill_dependencies_without_workbench_entry():
    schema = Path("app/schema.sql").read_text(encoding="utf-8")

    assert "'minimax-docx', 'Minimax DOCX', '0.1.0'" in schema
    assert "Internal Word document composition dependency used by first-party document Skills." in schema
    assert "('default', 'minimax-docx'" not in schema


def test_uploaded_files_can_be_created_before_sessions():
    schema = Path("app/schema.sql").read_text(encoding="utf-8")

    assert "session_id text," in schema
    assert "session_id text references sessions" not in schema
    assert "user_id text not null references users(id)" in schema


def test_schema_declares_run_copy_and_cancel_columns():
    schema = Path("app/schema.sql").read_text(encoding="utf-8")

    assert "copied_from_run_id text" in schema
    assert "cancel_requested_at timestamptz" in schema
    assert "cancel_requested_by text" in schema


def test_schema_declares_artifact_retention_columns():
    schema = Path("app/schema.sql").read_text(encoding="utf-8")

    assert "retention_policy text not null default 'standard_90d'" in schema
    assert "expires_at timestamptz" in schema
    assert "alter table artifacts add column if not exists retention_policy" in schema
    assert "alter table artifacts add column if not exists expires_at" in schema


def test_schema_declares_g2_control_plane_contract_columns():
    schema = Path("app/schema.sql").read_text(encoding="utf-8")

    assert "trace_id text not null" in schema
    assert "schema_version text not null default 'ai-platform.run.v1'" in schema
    assert "executor_schema_version text not null default 'ai-platform.executor-result.v1'" in schema
    assert "principal_roles jsonb not null default '[]'::jsonb" in schema
    assert "latency_ms integer" in schema
    assert "input_token_count integer not null default 0" in schema
    assert "output_token_count integer not null default 0" in schema
    assert "total_token_count integer not null default 0" in schema
    assert "estimated_cost_minor integer not null default 0" in schema
    assert "idx_runs_trace_id" in schema
    assert "schema_version text not null default 'ai-platform.event-envelope.v1'" in schema
    assert "sequence bigint not null default 0" in schema
    assert "visible_to_user boolean not null default true" in schema
    assert "manifest_version text not null default 'ai-platform.artifact-manifest.v1'" in schema
    assert "schema_version text not null default 'ai-platform.audit-event.v1'" in schema


def test_schema_declares_p0_memory_tool_event_and_sandbox_contracts():
    schema = Path("app/schema.sql").read_text(encoding="utf-8")

    assert "create table if not exists memory_records" in schema
    assert "agent_id text not null references agents(id)" in schema
    assert "session_id text not null" in schema
    assert "ses_memory_legacy_" in schema
    assert "update memory_records" in schema
    assert "raise exception 'memory_records_agent_id_null'" in schema
    assert "raise exception 'memory_records_session_id_null'" in schema
    assert "raise exception 'memory_records_session_not_found'" in schema
    assert "raise exception 'memory_records_session_scope_mismatch'" in schema
    assert "alter table memory_records alter column agent_id set not null" in schema
    assert "alter table memory_records alter column session_id set not null" in schema
    assert "fk_memory_records_session" in schema
    assert "fk_memory_records_session_scope" in schema
    assert "create index if not exists idx_memory_records_scope" in schema
    assert "create table if not exists memory_policies" in schema
    assert "long_term_memory_enabled boolean not null default false" in schema
    assert "check (long_term_memory_enabled = false)" in schema
    assert "update memory_policies" in schema
    assert "set long_term_memory_enabled = false" in schema
    assert "where long_term_memory_enabled = true" in schema
    assert "alter table memory_policies" in schema
    assert "add constraint chk_memory_policies_long_term_disabled" in schema
    assert "conrelid = 'memory_policies'::regclass" in schema
    assert "create index if not exists idx_memory_policies_scope" in schema
    assert "create index if not exists idx_memory_policies_workspace_updated" in schema
    assert "on memory_policies(tenant_id, workspace_id, updated_at desc, created_at desc)" in schema
    assert "create index if not exists idx_memory_policies_workspace_user_updated" in schema
    assert "on memory_policies(tenant_id, workspace_id, user_id, updated_at desc, created_at desc)" in schema
    assert "create index if not exists idx_memory_policies_workspace_agent_updated" in schema
    assert "on memory_policies(tenant_id, workspace_id, agent_id, updated_at desc, created_at desc)" in schema
    assert "create table if not exists run_context_snapshots" in schema
    assert "schema_version text not null default 'ai-platform.context-snapshot.v1'" in schema
    assert "included_memory_record_ids jsonb not null default '[]'::jsonb" in schema
    assert "create index if not exists idx_run_events_run_sequence" in schema
    assert "create table if not exists run_tool_permission_requests" in schema
    assert "create table if not exists tool_policies" in schema
    assert "primary key (tenant_id, tool_id)" in schema
    assert "references mcp_tools(id)" in schema
    assert "create index if not exists idx_tool_policies_tool" in schema
    assert "unique(tenant_id, run_id, tool_call_id)" in schema
    assert "create table if not exists sandbox_leases" in schema
    assert "heartbeat_at timestamptz" in schema
    assert "expires_at timestamptz" in schema


def test_schema_declares_runs_session_scope_guard():
    schema = Path("app/schema.sql").read_text(encoding="utf-8")

    assert "create unique index if not exists idx_sessions_run_scope" in schema
    assert "raise exception 'runs_session_not_found'" in schema
    assert "raise exception 'runs_session_scope_mismatch'" in schema
    assert "sessions.user_id is distinct from runs.user_id" in schema
    assert "fk_runs_session_scope" in schema
    assert "foreign key (tenant_id, workspace_id, user_id, session_id, agent_id)" in schema
    assert "references sessions(tenant_id, workspace_id, user_id, id, agent_id)" in schema


def test_schema_declares_context_snapshot_run_scope_guard():
    schema = Path("app/schema.sql").read_text(encoding="utf-8")

    assert "create unique index if not exists idx_runs_context_scope" in schema
    assert "raise exception 'run_context_snapshots_run_not_found'" in schema
    assert "raise exception 'run_context_snapshots_run_scope_mismatch'" in schema
    assert "runs.user_id is distinct from run_context_snapshots.user_id" in schema
    assert "fk_run_context_snapshots_run_scope" in schema
    assert "foreign key (tenant_id, workspace_id, user_id, session_id, run_id)" in schema
    assert "references runs(tenant_id, workspace_id, user_id, session_id, id)" in schema


def test_schema_scope_guards_use_null_safe_identity_comparisons():
    schema = Path("app/schema.sql").read_text(encoding="utf-8")

    assert "sessions.user_id <> runs.user_id" not in schema
    assert "sessions.user_id <> memory_records.user_id" not in schema
    assert "runs.user_id <> run_context_snapshots.user_id" not in schema
    assert "is distinct from" in schema


def test_schema_adds_trace_column_before_trace_index_for_existing_databases():
    schema = Path("app/schema.sql").read_text(encoding="utf-8")

    add_column = "alter table runs add column if not exists trace_id"
    create_index = "create index if not exists idx_runs_trace_id on runs(trace_id)"

    assert add_column in schema
    assert create_index in schema
    assert schema.index(add_column) < schema.index(create_index)


def test_schema_adds_run_event_sequence_before_sequence_index_for_existing_databases():
    schema = Path("app/schema.sql").read_text(encoding="utf-8")

    add_column = "alter table run_events add column if not exists sequence"
    create_index = "create index if not exists idx_run_events_run_sequence on run_events(tenant_id, run_id, sequence)"

    assert add_column in schema
    assert create_index in schema
    assert schema.index(add_column) < schema.index(create_index)


def test_general_chat_seed_uses_platform_owned_claude_worker_not_poco_fact_source():
    schema = Path("app/schema.sql").read_text(encoding="utf-8")

    assert "'general-chat', 'General Chat Agent'" in schema
    assert "'general-chat', 'General Chat Agent', '0.1.0', 'General chat agent executed by Claude Agent worker." in schema
    assert "'general-chat', 'General Chat Agent', '0.1.0', 'General chat agent executed by embedded Poco runtime kernel." not in schema


def test_schema_declares_run_skill_snapshots():
    schema = Path("app/schema.sql").read_text(encoding="utf-8")

    assert "create table if not exists run_skill_snapshots" in schema
    assert "skill_id text not null references skills(id)" in schema
    assert "skill_version text not null" in schema
    assert "content_hash text not null default ''" in schema
    assert "source_json jsonb not null default '{}'::jsonb" in schema
    assert "dependency_ids jsonb not null default '[]'::jsonb" in schema
    assert "allowed boolean not null default false" in schema
    assert "staged boolean not null default false" in schema
    assert "used boolean not null default false" in schema
    assert "used_skills_source text not null default ''" in schema
    assert "inferred_used boolean not null default false" in schema
    assert "unique(tenant_id, run_id, skill_id)" in schema
    assert "idx_run_skill_snapshots_run" in schema


def test_schema_declares_skill_versions():
    schema = Path("app/schema.sql").read_text(encoding="utf-8")

    assert "create table if not exists skill_versions" in schema
    assert "skill_id text not null references skills(id)" in schema
    assert "version text not null" in schema
    assert "content_hash text not null default ''" in schema
    assert "source_json jsonb not null default '{}'::jsonb" in schema
    assert "dependency_ids jsonb not null default '[]'::jsonb" in schema
    assert "unique(skill_id, version)" in schema
    assert "idx_skill_versions_skill_created" in schema


def test_schema_declares_skill_release_policies():
    schema = Path("app/schema.sql").read_text(encoding="utf-8")

    assert "create table if not exists skill_release_policies" in schema
    assert "tenant_id text not null references tenants(id)" in schema
    assert "skill_id text not null references skills(id)" in schema
    assert "channel text not null default 'stable'" in schema
    assert "current_version text not null" in schema
    assert "previous_version text" in schema
    assert "rollout_percent integer not null default 100" in schema
    assert "foreign key (skill_id, current_version) references skill_versions(skill_id, version)" in schema
    assert "unique(tenant_id, skill_id, channel)" in schema
    assert "idx_skill_release_policies_skill" in schema


def test_schema_seeds_builtin_skill_versions_without_exposing_internal_dependencies():
    schema = Path("app/schema.sql").read_text(encoding="utf-8")

    assert "insert into skill_versions" in schema
    skill_version_seed = schema[schema.index("insert into skill_versions"):]
    assert "'qa-file-reviewer', '0.1.0'" in schema
    assert "'minimax-docx', '0.1.0'" in schema
    assert "'general-chat', '0.1.0'" in schema
    assert "'baoyu-translate', '0.1.0'" in schema
    assert "'ragflow-knowledge-search', '0.1.0'" in schema
    assert "on conflict (skill_id, version) do nothing" in skill_version_seed
    assert "do update set" not in skill_version_seed.split("insert into tenant_workbench_skills", 1)[0]
    assert "'[\"minimax-docx\"]'::jsonb" in schema
    assert "('default', 'minimax-docx'" not in schema
