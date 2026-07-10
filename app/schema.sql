create table if not exists tenants (
  id text primary key,
  name text not null,
  status text not null default 'active',
  created_at timestamptz not null default now()
);

create table if not exists workspaces (
  id text primary key,
  tenant_id text not null references tenants(id),
  name text not null,
  status text not null default 'active',
  created_at timestamptz not null default now()
);

create table if not exists users (
  id text primary key,
  tenant_id text not null references tenants(id),
  display_name text not null,
  email text,
  external_id text,
  status text not null default 'active',
  created_at timestamptz not null default now()
);

create table if not exists skills (
  id text primary key,
  name text not null,
  version text not null,
  description text not null default '',
  input_modes jsonb not null default '[]'::jsonb,
  output_modes jsonb not null default '[]'::jsonb,
  executor_type text not null,
  config_json jsonb not null default '{}'::jsonb,
  status text not null default 'active',
  created_at timestamptz not null default now()
);

create table if not exists skill_versions (
  id text primary key,
  skill_id text not null references skills(id),
  version text not null,
  content_hash text not null default '',
  description text not null default '',
  source_json jsonb not null default '{}'::jsonb,
  dependency_ids jsonb not null default '[]'::jsonb,
  status text not null default 'active',
  created_by text,
  created_at timestamptz not null default now(),
  unique(skill_id, version)
);

create index if not exists idx_skill_versions_skill_created on skill_versions(skill_id, created_at desc);

create table if not exists skill_release_policies (
  id text primary key,
  tenant_id text not null references tenants(id),
  skill_id text not null references skills(id),
  channel text not null default 'stable',
  current_version text not null,
  previous_version text,
  rollout_percent integer not null default 100,
  status text not null default 'active',
  promoted_by text,
  promoted_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(tenant_id, skill_id, channel),
  foreign key (skill_id, current_version) references skill_versions(skill_id, version),
  check (rollout_percent >= 0 and rollout_percent <= 100)
);

create index if not exists idx_skill_release_policies_skill on skill_release_policies(skill_id, channel, status);

create table if not exists user_skill_files (
  id text primary key,
  tenant_id text not null references tenants(id),
  user_id text not null references users(id),
  skill_id text not null references skills(id),
  file_path text not null,
  content_base64 text not null default '',
  size_bytes integer not null default 0,
  status text not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (file_path <> ''),
  check (size_bytes >= 0),
  check (status in ('active', 'deleted')),
  unique(tenant_id, user_id, skill_id, file_path)
);

create index if not exists idx_user_skill_files_user_skill
  on user_skill_files(tenant_id, user_id, skill_id, status, file_path);

create table if not exists tenant_workbench_skills (
  tenant_id text not null references tenants(id),
  skill_id text not null references skills(id),
  status text not null default 'active',
  visible_to_user boolean not null default true,
  created_at timestamptz not null default now(),
  primary key (tenant_id, skill_id)
);

create table if not exists mcp_servers (
  id text primary key,
  tenant_id text not null references tenants(id),
  name text not null,
  transport text not null default 'streamable_http',
  endpoint_redacted text not null default '',
  status text not null default 'active',
  is_system boolean not null default false,
  allowed_roles jsonb not null default '[]'::jsonb,
  role_quotas_json jsonb not null default '{}'::jsonb,
  department_ids text[] not null default array[]::text[],
  credential_state text not null default 'not_configured',
  credential_metadata_json jsonb not null default '{}'::jsonb,
  credential_fingerprint text not null default '',
  updated_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(tenant_id, name),
  check (transport in ('sse', 'streamable_http', 'sandbox')),
  check (status in ('active', 'disabled', 'deleted')),
  check (credential_state in ('not_configured', 'configured', 'platform_managed'))
);

create index if not exists idx_mcp_servers_tenant_status
  on mcp_servers(tenant_id, status, name);

create table if not exists tenant_capability_distributions (
  id text primary key,
  tenant_id text not null references tenants(id),
  capability_kind text not null,
  capability_id text not null,
  status text not null default 'active',
  visible_to_user boolean not null default true,
  scope_mode text not null default 'allowlist',
  department_ids text[] not null default array[]::text[],
  allowed_roles jsonb not null default '[]'::jsonb,
  metadata_json jsonb not null default '{}'::jsonb,
  updated_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, capability_kind, capability_id),
  check (capability_kind in ('skill', 'mcp_server')),
  check (status in ('active', 'disabled')),
  check (scope_mode in ('allowlist')),
  constraint tenant_capability_distributions_allowed_roles_array
    check (jsonb_typeof(allowed_roles) = 'array'),
  constraint tenant_capability_distributions_allowed_roles_strings
    check (
      not jsonb_path_exists(allowed_roles, '$[*] ? (@.type() != "string")')
      and not jsonb_path_exists(allowed_roles, '$[*] ? (@ == "")')
      and not jsonb_path_exists(
        allowed_roles,
        '$[*] ? (@.type() == "string" && @ like_regex "^\\s*$")'
      )
    )
);

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'tenant_capability_distributions_allowed_roles_array'
      and conrelid = 'tenant_capability_distributions'::regclass
  ) then
    alter table tenant_capability_distributions
      add constraint tenant_capability_distributions_allowed_roles_array
      check (jsonb_typeof(allowed_roles) = 'array') not valid;
  end if;
end
$$;

alter table tenant_capability_distributions
  validate constraint tenant_capability_distributions_allowed_roles_array;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'tenant_capability_distributions_allowed_roles_strings'
      and conrelid = 'tenant_capability_distributions'::regclass
  ) then
    alter table tenant_capability_distributions
      add constraint tenant_capability_distributions_allowed_roles_strings
      check (
        not jsonb_path_exists(allowed_roles, '$[*] ? (@.type() != "string")')
        and not jsonb_path_exists(allowed_roles, '$[*] ? (@ == "")')
        and not jsonb_path_exists(
          allowed_roles,
          '$[*] ? (@.type() == "string" && @ like_regex "^\\s*$")'
        )
      ) not valid;
  end if;
end
$$;

alter table tenant_capability_distributions
  validate constraint tenant_capability_distributions_allowed_roles_strings;

create table if not exists tenant_capability_distribution_backfills (
  tenant_id text primary key references tenants(id),
  completed_at timestamptz
);

create table if not exists mcp_server_credentials (
  tenant_id text not null references tenants(id),
  server_name text not null,
  credential_fingerprint text not null default '',
  metadata_json jsonb not null default '{}'::jsonb,
  updated_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, server_name),
  foreign key (tenant_id, server_name) references mcp_servers(tenant_id, name)
);

create table if not exists mcp_tools (
  id text primary key,
  server_id text not null,
  name text not null,
  description text not null default '',
  transport_type text not null default 'http',
  endpoint text not null default '',
  auth_mode text not null default 'none',
  allowed_tools jsonb not null default '[]'::jsonb,
  status text not null default 'disabled',
  write_capable boolean not null default false,
  risk_level text not null default 'low',
  visible_to_user boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists tool_policies (
  tenant_id text not null references tenants(id),
  tool_id text not null references mcp_tools(id),
  status text not null default 'disabled',
  write_capable boolean not null default false,
  risk_level text not null default 'low',
  visible_to_user boolean not null default true,
  reason text not null default '',
  updated_by text references users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, tool_id)
);

create index if not exists idx_tool_policies_tool on tool_policies(tool_id, tenant_id);

create table if not exists agents (
  id text primary key,
  tenant_id text not null references tenants(id),
  name text not null,
  agent_type text not null,
  description text not null default '',
  default_skill_id text references skills(id),
  status text not null default 'active',
  created_at timestamptz not null default now()
);

create table if not exists sessions (
  id text primary key,
  tenant_id text not null references tenants(id),
  workspace_id text not null references workspaces(id),
  user_id text references users(id),
  agent_id text not null references agents(id),
  title text not null default '',
  status text not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists runs (
  id text primary key,
  tenant_id text not null references tenants(id),
  workspace_id text not null references workspaces(id),
  session_id text not null references sessions(id),
  user_id text references users(id),
  agent_id text not null references agents(id),
  skill_id text not null references skills(id),
  trace_id text not null default '',
  schema_version text not null default 'ai-platform.run.v1',
  executor_schema_version text not null default 'ai-platform.executor-result.v1',
  principal_roles jsonb not null default '[]'::jsonb,
  principal_department_id text not null default '',
  auth_source text,
  status text not null,
  input_json jsonb not null default '{}'::jsonb,
  result_json jsonb not null default '{}'::jsonb,
  error_code text,
  error_message text,
  latency_ms integer,
  input_token_count integer not null default 0,
  output_token_count integer not null default 0,
  total_token_count integer not null default 0,
  estimated_cost_minor integer not null default 0,
  queued_at timestamptz,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz not null default now(),
  copied_from_run_id text references runs(id),
  cancel_requested_at timestamptz,
  cancel_requested_by text
);

create index if not exists idx_runs_tenant_created on runs(tenant_id, created_at desc);
create index if not exists idx_runs_session_created on runs(session_id, created_at desc);
create index if not exists idx_runs_status on runs(status);

alter table runs add column if not exists trace_id text not null default '';
alter table runs add column if not exists schema_version text not null default 'ai-platform.run.v1';
alter table runs add column if not exists executor_schema_version text not null default 'ai-platform.executor-result.v1';
alter table runs add column if not exists principal_roles jsonb not null default '[]'::jsonb;
alter table runs add column if not exists principal_department_id text not null default '';
alter table runs add column if not exists auth_source text;
alter table runs add column if not exists copied_from_run_id text references runs(id);
alter table runs add column if not exists cancel_requested_at timestamptz;
alter table runs add column if not exists cancel_requested_by text;
alter table runs add column if not exists latency_ms integer;
alter table runs add column if not exists input_token_count integer not null default 0;
alter table runs add column if not exists output_token_count integer not null default 0;
alter table runs add column if not exists total_token_count integer not null default 0;
alter table runs add column if not exists estimated_cost_minor integer not null default 0;

create index if not exists idx_runs_trace_id on runs(trace_id);
create unique index if not exists idx_runs_context_scope
  on runs(tenant_id, workspace_id, user_id, session_id, id);
create unique index if not exists idx_sessions_run_scope
  on sessions(tenant_id, workspace_id, user_id, id, agent_id);

do $$
begin
  if exists (
    select 1
    from runs
    left join sessions on sessions.id = runs.session_id
    where sessions.id is null
    limit 1
  ) then
    raise exception 'runs_session_not_found';
  end if;
  if exists (
    select 1
    from runs
    join sessions on sessions.id = runs.session_id
    where sessions.tenant_id is distinct from runs.tenant_id
       or sessions.workspace_id is distinct from runs.workspace_id
       or sessions.user_id is distinct from runs.user_id
       or sessions.agent_id is distinct from runs.agent_id
    limit 1
  ) then
    raise exception 'runs_session_scope_mismatch';
  end if;
end $$;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'fk_runs_session_scope'
      and conrelid = 'runs'::regclass
  ) then
    alter table runs
      add constraint fk_runs_session_scope
      foreign key (tenant_id, workspace_id, user_id, session_id, agent_id)
      references sessions(tenant_id, workspace_id, user_id, id, agent_id);
  end if;
end $$;

create table if not exists run_steps (
  id text primary key,
  tenant_id text not null references tenants(id),
  run_id text not null references runs(id),
  step_key text not null,
  step_kind text not null,
  status text not null,
  title text not null default '',
  role text,
  sequence integer not null default 0,
  payload_json jsonb not null default '{}'::jsonb,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(tenant_id, run_id, step_key)
);

create index if not exists idx_run_steps_run_sequence on run_steps(run_id, sequence, created_at);

create table if not exists run_skill_snapshots (
  id text primary key,
  tenant_id text not null references tenants(id),
  run_id text not null references runs(id),
  skill_id text not null references skills(id),
  skill_version text not null,
  content_hash text not null default '',
  source_json jsonb not null default '{}'::jsonb,
  dependency_ids jsonb not null default '[]'::jsonb,
  allowed boolean not null default false,
  staged boolean not null default false,
  used boolean not null default false,
  used_skills_source text not null default '',
  inferred_used boolean not null default false,
  created_at timestamptz not null default now(),
  unique(tenant_id, run_id, skill_id)
);

alter table run_skill_snapshots add column if not exists used_skills_source text not null default '';
alter table run_skill_snapshots add column if not exists inferred_used boolean not null default false;

create index if not exists idx_run_skill_snapshots_run on run_skill_snapshots(tenant_id, run_id);

create table if not exists messages (
  id text primary key,
  tenant_id text not null references tenants(id),
  session_id text not null references sessions(id),
  run_id text references runs(id),
  role text not null,
  content text not null,
  metadata_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists memory_records (
  id text primary key,
  tenant_id text not null references tenants(id),
  workspace_id text not null references workspaces(id),
  user_id text not null references users(id),
  agent_id text not null references agents(id),
  session_id text not null,
  record_type text not null,
  content text not null,
  metadata_json jsonb not null default '{}'::jsonb,
  status text not null default 'active',
  expires_at timestamptz,
  deleted_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

do $$
begin
  update memory_records
  set agent_id = 'general-agent',
      updated_at = now()
  where agent_id is null;

  insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title, status)
  select distinct
    'ses_memory_legacy_' || substr(md5(tenant_id || ':' || workspace_id || ':' || user_id || ':' || agent_id), 1, 24),
    tenant_id,
    workspace_id,
    user_id,
    agent_id,
    'Legacy memory records',
    'active'
  from memory_records
  where session_id is null
  on conflict (id) do nothing;

  update memory_records
  set session_id = 'ses_memory_legacy_' || substr(md5(tenant_id || ':' || workspace_id || ':' || user_id || ':' || agent_id), 1, 24),
      updated_at = now()
  where session_id is null;

  insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title, status)
  select distinct
    memory_records.session_id,
    memory_records.tenant_id,
    memory_records.workspace_id,
    memory_records.user_id,
    memory_records.agent_id,
    'Legacy memory records',
    'active'
  from memory_records
  left join sessions on sessions.id = memory_records.session_id
  where sessions.id is null
  on conflict (id) do nothing;
end $$;

do $$
begin
  if exists (select 1 from memory_records where agent_id is null limit 1) then
    raise exception 'memory_records_agent_id_null';
  end if;
  if exists (select 1 from memory_records where session_id is null limit 1) then
    raise exception 'memory_records_session_id_null';
  end if;
end $$;

alter table memory_records alter column agent_id set not null;
alter table memory_records alter column session_id set not null;

do $$
begin
  if exists (
    select 1
    from memory_records
    left join sessions on sessions.id = memory_records.session_id
    where sessions.id is null
    limit 1
  ) then
    raise exception 'memory_records_session_not_found';
  end if;
  if exists (
    select 1
    from memory_records
    join sessions on sessions.id = memory_records.session_id
    where sessions.tenant_id is distinct from memory_records.tenant_id
       or sessions.workspace_id is distinct from memory_records.workspace_id
       or sessions.user_id is distinct from memory_records.user_id
       or sessions.agent_id is distinct from memory_records.agent_id
    limit 1
  ) then
    raise exception 'memory_records_session_scope_mismatch';
  end if;
end $$;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'fk_memory_records_session'
      and conrelid = 'memory_records'::regclass
  ) then
    alter table memory_records
      add constraint fk_memory_records_session
      foreign key (session_id) references sessions(id);
  end if;
  if not exists (
    select 1
    from pg_constraint
    where conname = 'fk_memory_records_session_scope'
      and conrelid = 'memory_records'::regclass
  ) then
    alter table memory_records
      add constraint fk_memory_records_session_scope
      foreign key (tenant_id, workspace_id, user_id, session_id, agent_id)
      references sessions(tenant_id, workspace_id, user_id, id, agent_id);
  end if;
end $$;

create index if not exists idx_memory_records_scope
  on memory_records(tenant_id, workspace_id, user_id, agent_id, session_id, created_at desc);
create index if not exists idx_memory_records_expired_cleanup
  on memory_records(expires_at asc, created_at asc, tenant_id, workspace_id, id)
  where status = 'active'
    and deleted_at is null
    and expires_at is not null;

create table if not exists worker_maintenance_cursors (
  cursor_key text primary key,
  tenant_id text,
  workspace_id text,
  updated_at timestamptz not null default now()
);

create table if not exists memory_policies (
  id text primary key,
  tenant_id text not null references tenants(id),
  workspace_id text not null references workspaces(id),
  user_id text not null references users(id),
  agent_id text,
  memory_enabled boolean not null default true,
  long_term_memory_enabled boolean not null default false,
  retention_days integer not null default 90,
  redaction_mode text not null default 'standard',
  reason text not null default '',
  updated_by text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint chk_memory_policies_long_term_disabled check (long_term_memory_enabled = false),
  constraint chk_memory_policies_redaction_mode check (redaction_mode in ('standard', 'strict')),
  check (retention_days >= 1 and retention_days <= 3650)
);

alter table memory_policies add column if not exists redaction_mode text not null default 'standard';

update memory_policies
set long_term_memory_enabled = false
where long_term_memory_enabled = true;

update memory_policies
set redaction_mode = 'strict'
where redaction_mode is null or redaction_mode not in ('standard', 'strict');

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'chk_memory_policies_long_term_disabled'
      and conrelid = 'memory_policies'::regclass
  ) then
    alter table memory_policies
      add constraint chk_memory_policies_long_term_disabled check (long_term_memory_enabled = false);
  end if;
end $$;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'chk_memory_policies_redaction_mode'
      and conrelid = 'memory_policies'::regclass
  ) then
    alter table memory_policies
      add constraint chk_memory_policies_redaction_mode check (redaction_mode in ('standard', 'strict'));
  end if;
end $$;

create index if not exists idx_memory_policies_scope
  on memory_policies(tenant_id, workspace_id, user_id, agent_id, updated_at desc);
create index if not exists idx_memory_policies_workspace_updated
  on memory_policies(tenant_id, workspace_id, updated_at desc, created_at desc);
create index if not exists idx_memory_policies_workspace_user_updated
  on memory_policies(tenant_id, workspace_id, user_id, updated_at desc, created_at desc);
create index if not exists idx_memory_policies_workspace_agent_updated
  on memory_policies(tenant_id, workspace_id, agent_id, updated_at desc, created_at desc);

create table if not exists run_context_snapshots (
  id text primary key,
  tenant_id text not null references tenants(id),
  workspace_id text not null references workspaces(id),
  user_id text not null references users(id),
  session_id text not null references sessions(id),
  run_id text not null references runs(id),
  trace_id text not null default '',
  schema_version text not null default 'ai-platform.context-snapshot.v1',
  context_kind text not null default 'executor',
  included_message_ids jsonb not null default '[]'::jsonb,
  included_file_ids jsonb not null default '[]'::jsonb,
  included_artifact_ids jsonb not null default '[]'::jsonb,
  included_memory_record_ids jsonb not null default '[]'::jsonb,
  redaction_summary_json jsonb not null default '{}'::jsonb,
  payload_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_run_context_snapshots_run
  on run_context_snapshots(tenant_id, run_id, created_at desc);

do $$
begin
  if exists (
    select 1
    from run_context_snapshots
    left join runs on runs.id = run_context_snapshots.run_id
    where runs.id is null
    limit 1
  ) then
    raise exception 'run_context_snapshots_run_not_found';
  end if;
  if exists (
    select 1
    from run_context_snapshots
    join runs on runs.id = run_context_snapshots.run_id
    where runs.tenant_id is distinct from run_context_snapshots.tenant_id
       or runs.workspace_id is distinct from run_context_snapshots.workspace_id
       or runs.user_id is distinct from run_context_snapshots.user_id
       or runs.session_id is distinct from run_context_snapshots.session_id
    limit 1
  ) then
    raise exception 'run_context_snapshots_run_scope_mismatch';
  end if;
end $$;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'fk_run_context_snapshots_run_scope'
      and conrelid = 'run_context_snapshots'::regclass
  ) then
    alter table run_context_snapshots
      add constraint fk_run_context_snapshots_run_scope
      foreign key (tenant_id, workspace_id, user_id, session_id, run_id)
      references runs(tenant_id, workspace_id, user_id, session_id, id);
  end if;
end $$;

create table if not exists run_events (
  id text primary key,
  tenant_id text not null references tenants(id),
  run_id text not null references runs(id),
  trace_id text not null default '',
  schema_version text not null default 'ai-platform.event-envelope.v1',
  sequence bigint not null default 0,
  event_type text not null,
  stage text not null,
  message text not null default '',
  severity text not null default 'info',
  visible_to_user boolean not null default true,
  error_code text,
  latency_ms integer,
  input_token_count integer not null default 0,
  output_token_count integer not null default 0,
  total_token_count integer not null default 0,
  estimated_cost_minor integer not null default 0,
  payload_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_run_events_run_created on run_events(run_id, created_at);

alter table run_events add column if not exists trace_id text not null default '';
alter table run_events add column if not exists schema_version text not null default 'ai-platform.event-envelope.v1';
alter table run_events add column if not exists sequence bigint not null default 0;
alter table run_events add column if not exists severity text not null default 'info';
alter table run_events add column if not exists visible_to_user boolean not null default true;
alter table run_events add column if not exists error_code text;
alter table run_events add column if not exists latency_ms integer;
alter table run_events add column if not exists input_token_count integer not null default 0;
alter table run_events add column if not exists output_token_count integer not null default 0;
alter table run_events add column if not exists total_token_count integer not null default 0;
alter table run_events add column if not exists estimated_cost_minor integer not null default 0;

create index if not exists idx_run_events_run_sequence on run_events(tenant_id, run_id, sequence);

create table if not exists run_tool_permission_requests (
  id text primary key,
  tenant_id text not null references tenants(id),
  workspace_id text not null references workspaces(id),
  user_id text not null references users(id),
  session_id text not null references sessions(id),
  run_id text not null references runs(id),
  trace_id text not null default '',
  tool_id text not null,
  tool_call_id text not null,
  action text not null default 'execute',
  risk_level text not null default 'low',
  write_capable boolean not null default false,
  status text not null default 'pending',
  decision text,
  reason text not null default '',
  request_payload_json jsonb not null default '{}'::jsonb,
  decision_payload_json jsonb not null default '{}'::jsonb,
  expires_at timestamptz,
  decided_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(tenant_id, run_id, tool_call_id)
);

create index if not exists idx_run_tool_permission_requests_run
  on run_tool_permission_requests(tenant_id, run_id, created_at desc);
create index if not exists idx_run_tool_permission_requests_inbox
  on run_tool_permission_requests(tenant_id, user_id, status, created_at desc);

create table if not exists sandbox_leases (
  id text primary key,
  tenant_id text not null references tenants(id),
  workspace_id text not null references workspaces(id),
  user_id text not null references users(id),
  session_id text not null references sessions(id),
  run_id text not null references runs(id),
  trace_id text not null default '',
  sandbox_mode text not null,
  provider text not null default 'fake',
  status text not null default 'active',
  browser_enabled boolean not null default false,
  resource_limits_json jsonb not null default '{}'::jsonb,
  user_visible_payload_json jsonb not null default '{}'::jsonb,
  lease_payload_json jsonb not null default '{}'::jsonb,
  heartbeat_at timestamptz,
  expires_at timestamptz,
  released_at timestamptz,
  release_reason text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_sandbox_leases_run
  on sandbox_leases(tenant_id, run_id, created_at desc);
create index if not exists idx_sandbox_leases_status
  on sandbox_leases(tenant_id, status, expires_at);

create table if not exists files (
  id text primary key,
  tenant_id text not null references tenants(id),
  workspace_id text not null references workspaces(id),
  user_id text not null references users(id),
  session_id text,
  run_id text references runs(id),
  original_name text not null,
  content_type text not null,
  size_bytes bigint not null,
  storage_key text not null unique,
  sha256 text not null,
  created_at timestamptz not null default now()
);

create table if not exists artifacts (
  id text primary key,
  tenant_id text not null references tenants(id),
  run_id text not null references runs(id),
  trace_id text not null default '',
  artifact_type text not null,
  label text not null,
  content_type text not null,
  storage_key text not null unique,
  size_bytes bigint not null,
  manifest_version text not null default 'ai-platform.artifact-manifest.v1',
  manifest_json jsonb not null default '{}'::jsonb,
  retention_policy text not null default 'standard_90d',
  expires_at timestamptz,
  created_at timestamptz not null default now()
);

alter table artifacts add column if not exists trace_id text not null default '';
alter table artifacts add column if not exists manifest_version text not null default 'ai-platform.artifact-manifest.v1';
alter table artifacts add column if not exists retention_policy text not null default 'standard_90d';
alter table artifacts add column if not exists expires_at timestamptz;

create table if not exists audit_logs (
  id text primary key,
  tenant_id text not null references tenants(id),
  user_id text,
  action text not null,
  target_type text not null,
  target_id text not null,
  trace_id text,
  schema_version text not null default 'ai-platform.audit-event.v1',
  payload_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

alter table audit_logs add column if not exists trace_id text;
alter table audit_logs add column if not exists schema_version text not null default 'ai-platform.audit-event.v1';
create index if not exists idx_audit_logs_tool_policy_history
  on audit_logs(tenant_id, target_type, action, target_id, created_at desc, id desc);
create index if not exists idx_audit_logs_tool_policy_history_latest
  on audit_logs(tenant_id, target_type, action, created_at desc, id desc);

insert into tenants(id, name)
values ('default', 'Default Tenant')
on conflict (id) do nothing;

insert into workspaces(id, tenant_id, name)
values ('default', 'default', 'Default Workspace')
on conflict (id) do nothing;

insert into skills(id, name, version, description, input_modes, output_modes, executor_type)
values
  ('qa-file-reviewer', 'QA Word Review', '0.1.0', 'Review Word documents and return commented Word artifacts.', '["docx"]'::jsonb, '["reviewed_docx", "findings_json"]'::jsonb, 'claude-agent-worker'),
  ('minimax-docx', 'Minimax DOCX', '0.1.0', 'Internal Word document composition dependency used by first-party document Skills.', '["docx"]'::jsonb, '["docx"]'::jsonb, 'claude-agent-worker'),
  ('baoyu-translate', 'Baoyu Translate', '0.1.0', 'Translate Word documents and return translated Word artifacts.', '["docx"]'::jsonb, '["translated_docx"]'::jsonb, 'claude-agent-worker'),
  ('general-chat', 'General Chat Agent', '0.1.0', 'General chat agent executed by Claude Agent worker.', '["chat"]'::jsonb, '["answer"]'::jsonb, 'claude-agent-worker'),
  ('ragflow-knowledge-search', 'RAGFlow Knowledge Search', '0.1.0', 'Query company knowledge base with scoped citations.', '["chat"]'::jsonb, '["answer", "citations"]'::jsonb, 'ragflow')
on conflict (id) do update set
  name = excluded.name,
  version = excluded.version,
  description = excluded.description,
  input_modes = excluded.input_modes,
  output_modes = excluded.output_modes,
  executor_type = excluded.executor_type,
  status = excluded.status;

insert into skill_versions(id, skill_id, version, content_hash, description, source_json, dependency_ids, status, created_by)
values
  ('skv_seed_general_chat_0_1_0', 'general-chat', '0.1.0', '0.1.0', 'Schema-seeded baseline for General Chat Agent.', '{"kind":"schema-seed"}'::jsonb, '[]'::jsonb, 'active', 'schema'),
  ('skv_seed_qa_file_reviewer_0_1_0', 'qa-file-reviewer', '0.1.0', '0.1.0', 'Schema-seeded baseline for QA Word Review.', '{"kind":"schema-seed"}'::jsonb, '["minimax-docx"]'::jsonb, 'active', 'schema'),
  ('skv_seed_minimax_docx_0_1_0', 'minimax-docx', '0.1.0', '0.1.0', 'Schema-seeded baseline for internal DOCX composition dependency.', '{"kind":"schema-seed"}'::jsonb, '[]'::jsonb, 'active', 'schema'),
  ('skv_seed_baoyu_translate_0_1_0', 'baoyu-translate', '0.1.0', '0.1.0', 'Schema-seeded baseline for Baoyu Translate.', '{"kind":"schema-seed"}'::jsonb, '[]'::jsonb, 'active', 'schema'),
  ('skv_seed_ragflow_knowledge_search_0_1_0', 'ragflow-knowledge-search', '0.1.0', '0.1.0', 'Schema-seeded baseline for RAGFlow Knowledge Search.', '{"kind":"schema-seed"}'::jsonb, '[]'::jsonb, 'active', 'schema')
on conflict (skill_id, version) do nothing;

insert into tenant_workbench_skills(tenant_id, skill_id, status, visible_to_user)
values
  ('default', 'general-chat', 'active', true),
  ('default', 'qa-file-reviewer', 'active', true),
  ('default', 'baoyu-translate', 'active', true),
  ('default', 'ragflow-knowledge-search', 'active', true)
on conflict (tenant_id, skill_id) do nothing;

insert into mcp_tools(id, server_id, name, description, transport_type, endpoint, auth_mode, allowed_tools, status, write_capable, risk_level, visible_to_user)
values
  (
    'ragflow-knowledge-search',
    'ragflow',
    'RAGFlow 知识库检索',
    'Read-only company knowledge search tool. User registration of arbitrary MCP servers is disabled.',
    'http',
    '',
    'platform-managed',
    '["ragflow_search"]'::jsonb,
    'active',
    false,
    'low',
    true
  )
on conflict (id) do update set
  server_id = excluded.server_id,
  name = excluded.name,
  description = excluded.description,
  transport_type = excluded.transport_type,
  endpoint = excluded.endpoint,
  auth_mode = excluded.auth_mode,
  allowed_tools = excluded.allowed_tools,
  status = excluded.status,
  write_capable = excluded.write_capable,
  risk_level = excluded.risk_level,
  visible_to_user = excluded.visible_to_user;

insert into tool_policies(tenant_id, tool_id, status, write_capable, risk_level, visible_to_user, reason)
values
  ('default', 'ragflow-knowledge-search', 'active', false, 'low', true, 'Schema-seeded read-only RAGFlow tool policy for the default tenant.')
on conflict (tenant_id, tool_id) do nothing;

insert into agents(id, tenant_id, name, agent_type, description, default_skill_id, status)
values
  ('translate', 'default', '文档翻译', 'file', 'Legacy alias for baoyu-translate. Hidden from LambChat mode selection.', 'baoyu-translate', 'inactive'),
  ('document-review', 'default', '文档审核', 'file', 'Legacy alias for qa-word-review. Hidden from LambChat mode selection.', 'qa-file-reviewer', 'inactive'),
  ('general-agent', 'default', '通用聊天 Agent', 'chat', 'General company chat agent backed by ai-platform sessions and Claude Agent SDK worker.', 'general-chat', 'active'),
  ('qa-word-review', 'default', '文档审核', 'file', 'Upload Word documents and generate reviewed Word artifacts.', 'qa-file-reviewer', 'active'),
  ('baoyu-translate', 'default', '文档翻译', 'file', 'Upload Word documents and generate translated Word artifacts.', 'baoyu-translate', 'active'),
  ('sop-assistant', 'default', 'SOP 助手', 'chat', 'Answer SOP questions with RAGFlow citations.', 'ragflow-knowledge-search', 'active')
on conflict (id) do update set
  tenant_id = excluded.tenant_id,
  name = excluded.name,
  agent_type = excluded.agent_type,
  description = excluded.description,
  default_skill_id = excluded.default_skill_id,
  status = excluded.status;
