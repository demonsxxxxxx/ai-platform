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

create unique index if not exists idx_workspaces_tenant_scope
  on workspaces(tenant_id, id);

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
  catalog_generation bigint not null default 0,
  catalog_sync_attempt bigint not null default 0,
  catalog_sync_lease_expires_at timestamptz,
  catalog_revision bigint not null default 0,
  catalog_status text not null default 'legacy',
  catalog_unavailable_reason text not null default '',
  catalog_discovered_count integer not null default 0,
  catalog_selectable_count integer not null default 0,
  catalog_last_synced_at timestamptz,
  updated_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(tenant_id, name),
  check (transport in ('sse', 'streamable_http', 'sandbox')),
  check (status in ('active', 'disabled', 'deleted')),
  check (credential_state in ('not_configured', 'configured', 'platform_managed')),
  check (catalog_generation >= 0),
  check (catalog_sync_attempt >= 0),
  check (catalog_revision >= 0),
  check (catalog_status in ('legacy', 'refresh_required', 'syncing', 'available', 'no_tools', 'unavailable', 'disabled', 'deleted')),
  check (catalog_discovered_count >= 0),
  check (catalog_selectable_count >= 0)
);

create index if not exists idx_mcp_servers_tenant_status
  on mcp_servers(tenant_id, status, name);

alter table mcp_servers
  add column if not exists catalog_generation bigint not null default 0,
  add column if not exists catalog_sync_attempt bigint not null default 0,
  add column if not exists catalog_sync_lease_expires_at timestamptz,
  add column if not exists catalog_revision bigint not null default 0,
  add column if not exists catalog_status text not null default 'legacy',
  add column if not exists catalog_unavailable_reason text not null default '',
  add column if not exists catalog_discovered_count integer not null default 0,
  add column if not exists catalog_selectable_count integer not null default 0,
  add column if not exists catalog_last_synced_at timestamptz;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'mcp_servers_catalog_status_valid'
      and conrelid = 'mcp_servers'::regclass
  ) then
    alter table mcp_servers
      add constraint mcp_servers_catalog_status_valid
      check (catalog_status in ('legacy', 'refresh_required', 'syncing', 'available', 'no_tools', 'unavailable', 'disabled', 'deleted')) not valid;
  end if;
end
$$;

alter table mcp_servers
  validate constraint mcp_servers_catalog_status_valid;

create or replace function ai_platform_text_array_all_nonblank(input_values text[])
returns boolean
language sql
immutable
parallel safe
as $$
  select coalesce(
    bool_and(value is not null and btrim(value) <> ''),
    true
  )
  from unnest(input_values) as items(value)
$$;

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
  constraint tenant_capability_distributions_department_ids_nonblank
    check (ai_platform_text_array_all_nonblank(department_ids)),
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

update tenant_capability_distributions
set
  status = 'disabled',
  department_ids = array[]::text[],
  metadata_json = metadata_json || '{"legacy_scope_invalid":true}'::jsonb,
  updated_at = now()
where not ai_platform_text_array_all_nonblank(department_ids);

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'tenant_capability_distributions_department_ids_nonblank'
      and conrelid = 'tenant_capability_distributions'::regclass
  ) then
    alter table tenant_capability_distributions
      add constraint tenant_capability_distributions_department_ids_nonblank
      check (ai_platform_text_array_all_nonblank(department_ids)) not valid;
  end if;
end
$$;

alter table tenant_capability_distributions
  validate constraint tenant_capability_distributions_department_ids_nonblank;

create table if not exists tenant_capability_distribution_backfills (
  tenant_id text primary key references tenants(id),
  completed_at timestamptz
);

create table if not exists mcp_server_credentials (
  tenant_id text not null references tenants(id),
  server_name text not null,
  credential_fingerprint text not null default '',
  metadata_json jsonb not null default '{}'::jsonb,
  credential_envelope text not null default '',
  updated_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, server_name),
  foreign key (tenant_id, server_name) references mcp_servers(tenant_id, name)
);

alter table mcp_server_credentials
  add column if not exists credential_envelope text not null default '';

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

create table if not exists mcp_tool_catalog_entries (
  tool_id text primary key references mcp_tools(id),
  tenant_id text not null references tenants(id),
  server_name text not null,
  remote_tool_name text not null,
  catalog_generation bigint not null,
  schema_hash text not null,
  status text not null default 'disabled',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, server_name, remote_tool_name),
  foreign key (tenant_id, server_name) references mcp_servers(tenant_id, name),
  check (catalog_generation >= 0),
  check (status in ('active', 'disabled', 'stale', 'deleted'))
);

create index if not exists idx_mcp_tool_catalog_entries_server
  on mcp_tool_catalog_entries(tenant_id, server_name, status, remote_tool_name);

create table if not exists agents (
  id text primary key,
  tenant_id text not null references tenants(id),
  name text not null,
  agent_type text not null,
  description text not null default '',
  default_skill_id text references skills(id),
  status text not null default 'active',
  created_at timestamptz not null default now(),
  constraint uq_agents_tenant_id unique (tenant_id, id)
);

-- Older databases predate the composite tenant+agent authority used below.
do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'agents'::regclass and conname = 'uq_agents_tenant_id'
  ) then
    alter table agents add constraint uq_agents_tenant_id unique (tenant_id, id);
  end if;
end $$;

-- Agent Profile definitions are append-only. The shared agents row remains the
-- durable identity used by sessions/runs; this table is the sole authority for
-- mutable-looking definition state and preserves every saved/published revision.
create table if not exists agent_profile_revisions (
  tenant_id text not null references tenants(id),
  agent_id text not null,
  revision bigint not null check (revision > 0),
  -- ``status`` is the pre-#701 rollback visibility mirror and deliberately
  -- retains that binary's draft|published enum. Current code uses immutable
  -- ``revision_status`` and never derives lifecycle from this field.
  status text not null check (status in ('draft', 'published')),
  revision_status text not null check (revision_status in ('draft', 'published', 'withdrawn')),
  name text not null,
  description text not null default '',
  instructions text not null,
  model_id text not null,
  skill_id text not null references skills(id),
  skill_version text not null,
  mcp_tool_ids jsonb not null default '[]'::jsonb,
  content_hash text not null,
  avatar_ref text not null
    check (avatar_ref in ('builtin:agent', 'builtin:assistant', 'builtin:document', 'builtin:research')),
  category text not null
    check (category in ('general', 'support', 'writing', 'research', 'operations')),
  visibility text not null,
  allowed_department_ids jsonb not null,
  allowed_roles jsonb not null,
  allowed_user_ids jsonb not null,
  legacy_compatibility_write boolean not null default false,
  created_by text references users(id),
  created_at timestamptz not null default now(),
  published_by text references users(id),
  published_at timestamptz,
  published_from_revision bigint,
  withdrawn_from_revision bigint,
  constraint chk_agent_profile_revisions_visibility
    check (visibility in ('tenant', 'restricted')),
  constraint uq_agent_profile_revision_publication
    unique (tenant_id, agent_id, revision, content_hash, revision_status),
  constraint fk_agent_profile_revisions_tenant_agent
    foreign key (tenant_id, agent_id) references agents(tenant_id, id),
  primary key (tenant_id, agent_id, revision)
);

-- The aggregate is the only current-lifecycle authority. Revisions remain
-- append-only history, so a saved draft never accidentally replaces a live
-- publication and withdrawal can block new admissions without erasing replay.
create table if not exists agent_profiles (
  tenant_id text not null references tenants(id),
  agent_id text not null,
  lifecycle_status text not null check (lifecycle_status in ('draft', 'published', 'withdrawn')),
  latest_revision bigint not null check (latest_revision > 0),
  published_revision bigint,
  published_hash text,
  published_status text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint fk_agent_profiles_tenant_agent
    foreign key (tenant_id, agent_id) references agents(tenant_id, id),
  primary key (tenant_id, agent_id),
  constraint chk_agent_profiles_publication
    check (
      (
        lifecycle_status = 'published'
        and published_revision is not null
        and published_hash is not null
        and published_status = 'published'
      )
      or (
        lifecycle_status <> 'published'
        and published_revision is null
        and published_hash is null
        and published_status is null
      )
    )
);

create index if not exists idx_agent_profiles_published
  on agent_profiles(tenant_id, published_revision desc)
  where lifecycle_status = 'published';

create table if not exists sessions (
  id text primary key,
  tenant_id text not null references tenants(id),
  workspace_id text not null references workspaces(id),
  user_id text references users(id),
  agent_id text not null,
  title text not null default '',
  status text not null default 'active',
  admitted_agent_profile_revision bigint,
  admitted_agent_profile_hash text,
  next_run_generation bigint not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint fk_sessions_tenant_agent foreign key (tenant_id, agent_id)
    references agents(tenant_id, id),
  constraint fk_sessions_agent_profile_pin foreign key (
    tenant_id, agent_id, admitted_agent_profile_revision
  ) references agent_profile_revisions(tenant_id, agent_id, revision)
);

create table if not exists runs (
  id text primary key,
  tenant_id text not null references tenants(id),
  workspace_id text not null references workspaces(id),
  session_id text not null references sessions(id),
  user_id text references users(id),
  agent_id text not null,
  skill_id text not null references skills(id),
  trace_id text not null default '',
  schema_version text not null default 'ai-platform.run.v1',
  executor_schema_version text not null default 'ai-platform.executor-result.v1',
  principal_roles jsonb not null default '[]'::jsonb,
  principal_department_id text not null default '',
  auth_source text,
  admitted_agent_profile_revision bigint,
  admitted_agent_profile_hash text,
  status text not null,
  input_json jsonb not null default '{}'::jsonb,
  context_snapshot_id text,
  mcp_context_id text,
  session_generation bigint,
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
  cancel_requested_by text,
  permission_terminalization_target text,
  permission_terminalization_reason text not null default '',
  permission_terminalization_result_json jsonb not null default '{}'::jsonb,
  permission_terminalization_error_code text,
  permission_terminalization_error_message text,
  constraint fk_runs_tenant_agent foreign key (tenant_id, agent_id)
    references agents(tenant_id, id),
  constraint fk_runs_agent_profile_pin foreign key (
    tenant_id, agent_id, admitted_agent_profile_revision
  ) references agent_profile_revisions(tenant_id, agent_id, revision)
);

create index if not exists idx_runs_tenant_created on runs(tenant_id, created_at desc);
create index if not exists idx_runs_session_created on runs(session_id, created_at desc);
create index if not exists idx_runs_status on runs(status);
create unique index if not exists uq_runs_tenant_id on runs(tenant_id, id);

alter table runs add column if not exists trace_id text not null default '';
alter table runs add column if not exists schema_version text not null default 'ai-platform.run.v1';
alter table runs add column if not exists executor_schema_version text not null default 'ai-platform.executor-result.v1';
alter table runs add column if not exists principal_roles jsonb not null default '[]'::jsonb;
alter table runs add column if not exists principal_department_id text not null default '';
alter table runs add column if not exists auth_source text;
alter table runs add column if not exists mcp_context_id text;
alter table sessions add column if not exists admitted_agent_profile_revision bigint;
alter table sessions add column if not exists admitted_agent_profile_hash text;
create index if not exists idx_sessions_agent_conversation_history
  on sessions(
    tenant_id,
    user_id,
    agent_id,
    admitted_agent_profile_revision,
    updated_at desc,
    created_at desc,
    id desc
  )
  where status = 'active' and admitted_agent_profile_revision is not null;
alter table runs add column if not exists admitted_agent_profile_revision bigint;
alter table runs add column if not exists admitted_agent_profile_hash text;
alter table agent_profile_revisions add column if not exists published_from_revision bigint;
alter table agent_profile_revisions add column if not exists withdrawn_from_revision bigint;
alter table agent_profile_revisions add column if not exists revision_status text;
alter table agent_profile_revisions add column if not exists avatar_ref text;
alter table agent_profile_revisions add column if not exists category text;
alter table agent_profile_revisions add column if not exists visibility text;
alter table agent_profile_revisions add column if not exists allowed_department_ids jsonb;
alter table agent_profile_revisions add column if not exists allowed_roles jsonb;
alter table agent_profile_revisions add column if not exists allowed_user_ids jsonb;
alter table agent_profile_revisions add column if not exists legacy_compatibility_write boolean not null default false;
alter table agent_profiles add column if not exists published_status text;

alter table agent_profiles drop constraint if exists fk_agent_profiles_published_revision;
alter table agent_profiles drop constraint if exists fk_agent_profiles_current_publication;
alter table agent_profiles drop constraint if exists chk_agent_profiles_publication;
alter table agent_profiles drop constraint if exists agent_profiles_lifecycle_status_check;
alter table agent_profiles drop constraint if exists chk_agent_profiles_lifecycle_status;

alter table agent_profile_revisions drop constraint if exists agent_profile_revisions_status_check;
alter table agent_profile_revisions drop constraint if exists agent_profile_revisions_revision_status_check;
alter table agent_profile_revisions drop constraint if exists agent_profile_revisions_avatar_ref_check;
alter table agent_profile_revisions drop constraint if exists agent_profile_revisions_category_check;
alter table agent_profile_revisions drop constraint if exists chk_agent_profile_revisions_visibility;
alter table agent_profile_revisions drop constraint if exists agent_profile_revisions_visibility_check;
alter table agent_profile_revisions drop constraint if exists uq_agent_profile_revision_publication;

-- A NULL canonical status identifies a row created before #701. Preserve its
-- old tenant-visible behavior before repairing any explicit malformed value.
update agent_profile_revisions
set legacy_compatibility_write = true
where revision_status is null and visibility is null;

update agent_profile_revisions
set revision_status = case
  when status in ('draft', 'published', 'withdrawn') then status
  else 'withdrawn'
end
where revision_status is null
   or revision_status not in ('draft', 'published', 'withdrawn');

update agent_profile_revisions
set status = 'draft'
where status is null or status not in ('draft', 'published');

update agent_profile_revisions
set visibility = 'tenant'
where visibility is null;

update agent_profile_revisions
set visibility = 'restricted'
where visibility is not null and visibility not in ('tenant', 'restricted');

update agent_profile_revisions
set avatar_ref = 'builtin:agent'
where avatar_ref is null
   or avatar_ref not in ('builtin:agent', 'builtin:assistant', 'builtin:document', 'builtin:research');
update agent_profile_revisions
set category = 'general'
where category is null
   or category not in ('general', 'support', 'writing', 'research', 'operations');
update agent_profile_revisions
set allowed_department_ids = '[]'::jsonb
where allowed_department_ids is null or jsonb_typeof(allowed_department_ids) <> 'array';
update agent_profile_revisions
set allowed_roles = '[]'::jsonb
where allowed_roles is null or jsonb_typeof(allowed_roles) <> 'array';
update agent_profile_revisions
set allowed_user_ids = '[]'::jsonb
where allowed_user_ids is null or jsonb_typeof(allowed_user_ids) <> 'array';

-- No metadata defaults: omission is how the compatibility trigger recognizes
-- an old writer and inherits the existing ACL without broadening it.
alter table agent_profile_revisions alter column avatar_ref drop default;
alter table agent_profile_revisions alter column category drop default;
alter table agent_profile_revisions alter column visibility drop default;
alter table agent_profile_revisions alter column allowed_department_ids drop default;
alter table agent_profile_revisions alter column allowed_roles drop default;
alter table agent_profile_revisions alter column allowed_user_ids drop default;
alter table agent_profile_revisions alter column revision_status set not null;
alter table agent_profile_revisions alter column avatar_ref set not null;
alter table agent_profile_revisions alter column category set not null;
alter table agent_profile_revisions alter column visibility set not null;
alter table agent_profile_revisions alter column allowed_department_ids set not null;
alter table agent_profile_revisions alter column allowed_roles set not null;
alter table agent_profile_revisions alter column allowed_user_ids set not null;

alter table agent_profile_revisions add constraint agent_profile_revisions_status_check
  check (status in ('draft', 'published'));
alter table agent_profile_revisions add constraint agent_profile_revisions_revision_status_check
  check (revision_status in ('draft', 'published', 'withdrawn'));
alter table agent_profile_revisions add constraint agent_profile_revisions_avatar_ref_check
  check (avatar_ref in ('builtin:agent', 'builtin:assistant', 'builtin:document', 'builtin:research'));
alter table agent_profile_revisions add constraint agent_profile_revisions_category_check
  check (category in ('general', 'support', 'writing', 'research', 'operations'));
alter table agent_profile_revisions add constraint chk_agent_profile_revisions_visibility
  check (visibility in ('tenant', 'restricted'));
alter table agent_profile_revisions add constraint uq_agent_profile_revision_publication
  unique (tenant_id, agent_id, revision, content_hash, revision_status);

-- Repair corrupt aggregate state before deterministic reconciliation. Invalid
-- pointers withdraw fail closed; a later compatibility write cannot revive one.
update agent_profiles profiles
set published_status = 'published'
where profiles.lifecycle_status = 'published'
  and profiles.published_status is distinct from 'published'
  and exists (
    select 1
    from agent_profile_revisions revisions
    where revisions.tenant_id = profiles.tenant_id
      and revisions.agent_id = profiles.agent_id
      and revisions.revision = profiles.published_revision
      and revisions.content_hash = profiles.published_hash
      and revisions.revision_status = 'published'
  );

update agent_profiles profiles
set lifecycle_status = 'withdrawn',
    published_revision = null,
    published_hash = null,
    published_status = null,
    updated_at = now()
where profiles.lifecycle_status is null
   or profiles.lifecycle_status not in ('draft', 'published', 'withdrawn')
   or (
     profiles.lifecycle_status = 'published'
     and not exists (
       select 1
       from agent_profile_revisions revisions
       where revisions.tenant_id = profiles.tenant_id
         and revisions.agent_id = profiles.agent_id
         and revisions.revision = profiles.published_revision
         and revisions.content_hash = profiles.published_hash
         and revisions.revision_status = 'published'
     )
   );

update agent_profiles
set published_revision = null,
    published_hash = null,
    published_status = null,
    updated_at = now()
where lifecycle_status <> 'published'
  and (published_revision is not null or published_hash is not null or published_status is not null);

-- Reconcile missing aggregates and later old-backend appends on every deploy.
-- Withdrawn aggregates stay withdrawn. Existing current pointers move only to
-- a later compatibility publication that inherited tenant visibility.
with revision_facts as (
  select
    tenant_id,
    agent_id,
    max(revision) as latest_revision,
    max(revision) filter (where revision_status = 'published') as latest_published_revision,
    max(revision) filter (where revision_status = 'withdrawn') as latest_withdrawn_revision
  from agent_profile_revisions
  group by tenant_id, agent_id
), reconciliation as (
  select
    facts.tenant_id,
    facts.agent_id,
    facts.latest_revision,
    facts.latest_published_revision,
    facts.latest_withdrawn_revision,
    candidate.revision as published_revision,
    candidate.content_hash as published_hash
  from revision_facts facts
  left join agent_profiles existing
    on existing.tenant_id = facts.tenant_id and existing.agent_id = facts.agent_id
  left join lateral (
    select revision, content_hash
    from agent_profile_revisions candidate_row
    where candidate_row.tenant_id = facts.tenant_id
      and candidate_row.agent_id = facts.agent_id
      and candidate_row.revision_status = 'published'
      and not exists (
        select 1
        from agent_profile_revisions withdrawal
        where withdrawal.tenant_id = candidate_row.tenant_id
          and withdrawal.agent_id = candidate_row.agent_id
          and withdrawal.revision_status = 'withdrawn'
          and withdrawal.revision > candidate_row.revision
      )
      and (
        existing.agent_id is null
        or (
          existing.lifecycle_status <> 'withdrawn'
          and candidate_row.legacy_compatibility_write
          and candidate_row.revision > existing.latest_revision
          and candidate_row.visibility = 'tenant'
        )
      )
    order by candidate_row.revision desc
    limit 1
  ) candidate on true
)
insert into agent_profiles(
  tenant_id, agent_id, lifecycle_status, latest_revision, published_revision,
  published_hash, published_status
)
select
  tenant_id,
  agent_id,
  case
    when latest_withdrawn_revision is not null
      and (
        latest_published_revision is null
        or latest_withdrawn_revision > latest_published_revision
      ) then 'withdrawn'
    when published_revision is not null then 'published'
    else 'draft'
  end,
  latest_revision,
  case
    when latest_withdrawn_revision is not null
      and (
        latest_published_revision is null
        or latest_withdrawn_revision > latest_published_revision
      ) then null
    else published_revision
  end,
  case
    when latest_withdrawn_revision is not null
      and (
        latest_published_revision is null
        or latest_withdrawn_revision > latest_published_revision
      ) then null
    else published_hash
  end,
  case
    when published_revision is not null
      and not (
        latest_withdrawn_revision is not null
        and (
          latest_published_revision is null
          or latest_withdrawn_revision > latest_published_revision
        )
      ) then 'published'
    else null
  end
from reconciliation
on conflict (tenant_id, agent_id) do update
set latest_revision = greatest(agent_profiles.latest_revision, excluded.latest_revision),
    lifecycle_status = case
      when agent_profiles.lifecycle_status = 'withdrawn'
        or excluded.lifecycle_status = 'withdrawn' then 'withdrawn'
      when excluded.published_revision is not null then 'published'
      else agent_profiles.lifecycle_status
    end,
    published_revision = case
      when agent_profiles.lifecycle_status = 'withdrawn'
        or excluded.lifecycle_status = 'withdrawn' then null
      when excluded.published_revision is not null then excluded.published_revision
      else agent_profiles.published_revision
    end,
    published_hash = case
      when agent_profiles.lifecycle_status = 'withdrawn'
        or excluded.lifecycle_status = 'withdrawn' then null
      when excluded.published_revision is not null then excluded.published_hash
      else agent_profiles.published_hash
    end,
    published_status = case
      when agent_profiles.lifecycle_status = 'withdrawn'
        or excluded.lifecycle_status = 'withdrawn' then null
      when excluded.published_revision is not null then 'published'
      else agent_profiles.published_status
    end,
    updated_at = now()
where row(
    agent_profiles.latest_revision,
    agent_profiles.lifecycle_status,
    agent_profiles.published_revision,
    agent_profiles.published_hash,
    agent_profiles.published_status
  ) is distinct from row(
    greatest(agent_profiles.latest_revision, excluded.latest_revision),
    case
      when agent_profiles.lifecycle_status = 'withdrawn'
        or excluded.lifecycle_status = 'withdrawn' then 'withdrawn'
      when excluded.published_revision is not null then 'published'
      else agent_profiles.lifecycle_status
    end,
    case
      when agent_profiles.lifecycle_status = 'withdrawn'
        or excluded.lifecycle_status = 'withdrawn' then null
      when excluded.published_revision is not null then excluded.published_revision
      else agent_profiles.published_revision
    end,
    case
      when agent_profiles.lifecycle_status = 'withdrawn'
        or excluded.lifecycle_status = 'withdrawn' then null
      when excluded.published_revision is not null then excluded.published_hash
      else agent_profiles.published_hash
    end,
    case
      when agent_profiles.lifecycle_status = 'withdrawn'
        or excluded.lifecycle_status = 'withdrawn' then null
      when excluded.published_revision is not null then 'published'
      else agent_profiles.published_status
    end
  );

-- Synchronize the old-reader mirror after every reconciliation. Exactly the
-- current tenant-visible publication remains status='published'.
with desired as (
  select
    revisions.tenant_id,
    revisions.agent_id,
    revisions.revision,
    case
      when revisions.revision_status = 'published'
        and revisions.visibility = 'tenant'
        and exists (
          select 1
          from agent_profiles profiles
          where profiles.tenant_id = revisions.tenant_id
            and profiles.agent_id = revisions.agent_id
            and profiles.lifecycle_status = 'published'
            and profiles.published_revision = revisions.revision
            and profiles.published_hash = revisions.content_hash
            and profiles.published_status = 'published'
        ) then 'published'
      else 'draft'
    end as desired_status
  from agent_profile_revisions revisions
)
update agent_profile_revisions revisions
set status = desired.desired_status
from desired
where revisions.tenant_id = desired.tenant_id
  and revisions.agent_id = desired.agent_id
  and revisions.revision = desired.revision
  and revisions.status is distinct from desired.desired_status;

alter table agent_profiles add constraint chk_agent_profiles_lifecycle_status
  check (lifecycle_status in ('draft', 'published', 'withdrawn'));
alter table agent_profiles add constraint chk_agent_profiles_publication
  check (
    (
      lifecycle_status = 'published'
      and published_revision is not null
      and published_hash is not null
      and published_status = 'published'
    )
    or (
      lifecycle_status <> 'published'
      and published_revision is null
      and published_hash is null
      and published_status is null
    )
  );

alter table agent_profiles add constraint fk_agent_profiles_current_publication
  foreign key (tenant_id, agent_id, published_revision, published_hash, published_status)
  references agent_profile_revisions(tenant_id, agent_id, revision, content_hash, revision_status);

drop index if exists idx_agent_profile_revisions_published;
create index idx_agent_profile_revisions_published
  on agent_profile_revisions(tenant_id, agent_id, revision desc)
  where revision_status = 'published';

-- Supported rollback keeps this migrated schema in place while a pre-#701
-- application binary runs. Removing these columns/triggers requires database
-- restore authority; it is not an in-place application rollback. The BEFORE
-- trigger recognizes the old INSERT signature, serializes with current
-- lifecycle writers, inherits the existing ACL, and mints max(revision)+1
-- instead of overwriting a colliding history row.
create or replace function agent_profile_legacy_insert_compatibility()
returns trigger
language plpgsql
as $$
declare
  source_row agent_profile_revisions%rowtype;
  aggregate_lifecycle text;
  next_revision bigint;
  legacy_publication_allowed boolean := false;
begin
  if new.revision_status is not null then
    return new;
  end if;

  perform pg_advisory_xact_lock(
    hashtextextended('agent-profile:' || new.tenant_id || ':' || new.agent_id, 0)
  );
  new.revision_status := case
    when new.status in ('draft', 'published', 'withdrawn') then new.status
    else 'withdrawn'
  end;
  new.legacy_compatibility_write := true;

  if exists (
    select 1
    from agent_profile_revisions existing
    where existing.tenant_id = new.tenant_id
      and existing.agent_id = new.agent_id
      and existing.revision = new.revision
  ) then
    select coalesce(max(existing.revision), 0) + 1
    into next_revision
    from agent_profile_revisions existing
    where existing.tenant_id = new.tenant_id and existing.agent_id = new.agent_id;
    new.revision := next_revision;
  end if;

  select existing.*
  into source_row
  from agent_profile_revisions existing
  where existing.tenant_id = new.tenant_id and existing.agent_id = new.agent_id
  order by existing.revision desc
  limit 1;

  new.avatar_ref := coalesce(new.avatar_ref, source_row.avatar_ref, 'builtin:agent');
  if new.avatar_ref not in ('builtin:agent', 'builtin:assistant', 'builtin:document', 'builtin:research') then
    new.avatar_ref := 'builtin:agent';
  end if;
  new.category := coalesce(new.category, source_row.category, 'general');
  if new.category not in ('general', 'support', 'writing', 'research', 'operations') then
    new.category := 'general';
  end if;
  new.visibility := coalesce(new.visibility, source_row.visibility, 'tenant');
  if new.visibility not in ('tenant', 'restricted') then
    new.visibility := 'restricted';
  end if;
  new.allowed_department_ids := coalesce(
    new.allowed_department_ids,
    source_row.allowed_department_ids,
    '[]'::jsonb
  );
  if jsonb_typeof(new.allowed_department_ids) <> 'array' then
    new.allowed_department_ids := '[]'::jsonb;
  end if;
  new.allowed_roles := coalesce(new.allowed_roles, source_row.allowed_roles, '[]'::jsonb);
  if jsonb_typeof(new.allowed_roles) <> 'array' then
    new.allowed_roles := '[]'::jsonb;
  end if;
  new.allowed_user_ids := coalesce(new.allowed_user_ids, source_row.allowed_user_ids, '[]'::jsonb);
  if jsonb_typeof(new.allowed_user_ids) <> 'array' then
    new.allowed_user_ids := '[]'::jsonb;
  end if;

  select profiles.lifecycle_status
  into aggregate_lifecycle
  from agent_profiles profiles
  where profiles.tenant_id = new.tenant_id and profiles.agent_id = new.agent_id;
  if aggregate_lifecycle is null then
    select case
      when max(history.revision) filter (where history.revision_status = 'withdrawn') is not null
        and (
          max(history.revision) filter (where history.revision_status = 'published') is null
          or max(history.revision) filter (where history.revision_status = 'withdrawn')
            > max(history.revision) filter (where history.revision_status = 'published')
        ) then 'withdrawn'
      when max(history.revision) filter (where history.revision_status = 'published') is not null
        then 'published'
      else 'draft'
    end
    into aggregate_lifecycle
    from agent_profile_revisions history
    where history.tenant_id = new.tenant_id and history.agent_id = new.agent_id;
  end if;
  legacy_publication_allowed := (
    new.revision_status = 'published'
    and new.visibility = 'tenant'
    and aggregate_lifecycle <> 'withdrawn'
  );
  if new.revision_status = 'published' and not legacy_publication_allowed then
    new.revision_status := 'draft';
  end if;
  new.status := case
    when legacy_publication_allowed then 'published'
    else 'draft'
  end;

  if new.revision_status = 'published'
     and new.published_from_revision is not null
     and exists (
       select 1
       from agent_profile_revisions existing
       where existing.tenant_id = new.tenant_id
         and existing.agent_id = new.agent_id
         and existing.revision_status = 'published'
         and existing.published_from_revision = new.published_from_revision
     ) then
    new.published_from_revision := null;
  end if;
  return new;
end $$;

create or replace function agent_profile_legacy_insert_reconcile()
returns trigger
language plpgsql
as $$
declare
  fallback_lifecycle text;
  fallback_published_revision bigint;
  fallback_published_hash text;
begin
  if not new.legacy_compatibility_write then
    return null;
  end if;

  if new.revision_status = 'published' and new.status = 'published' then
    insert into agent_profiles(
      tenant_id, agent_id, lifecycle_status, latest_revision,
      published_revision, published_hash, published_status
    )
    values (
      new.tenant_id, new.agent_id, 'published', new.revision,
      new.revision, new.content_hash, 'published'
    )
    on conflict (tenant_id, agent_id) do update
    set lifecycle_status = 'published',
        latest_revision = greatest(agent_profiles.latest_revision, excluded.latest_revision),
        published_revision = excluded.published_revision,
        published_hash = excluded.published_hash,
        published_status = 'published',
        updated_at = now()
    where agent_profiles.lifecycle_status <> 'withdrawn';

    if exists (
      select 1
      from agent_profiles profiles
      where profiles.tenant_id = new.tenant_id
        and profiles.agent_id = new.agent_id
        and profiles.lifecycle_status = 'published'
        and profiles.published_revision = new.revision
        and profiles.published_hash = new.content_hash
    ) then
      update agent_profile_revisions revisions
      set status = case when revisions.revision = new.revision then 'published' else 'draft' end
      where revisions.tenant_id = new.tenant_id
        and revisions.agent_id = new.agent_id
        and revisions.revision_status = 'published';
    else
      update agent_profile_revisions
      set status = 'draft'
      where tenant_id = new.tenant_id and agent_id = new.agent_id and revision = new.revision;
    end if;
  else
    select history.revision, history.content_hash
    into fallback_published_revision, fallback_published_hash
    from agent_profile_revisions history
    where history.tenant_id = new.tenant_id
      and history.agent_id = new.agent_id
      and history.revision_status = 'published'
      and not exists (
        select 1
        from agent_profile_revisions withdrawal
        where withdrawal.tenant_id = history.tenant_id
          and withdrawal.agent_id = history.agent_id
          and withdrawal.revision_status = 'withdrawn'
          and withdrawal.revision > history.revision
      )
    order by history.revision desc
    limit 1;
    if fallback_published_revision is not null then
      fallback_lifecycle := 'published';
    elsif exists (
      select 1
      from agent_profile_revisions history
      where history.tenant_id = new.tenant_id
        and history.agent_id = new.agent_id
        and history.revision_status = 'withdrawn'
    ) then
      fallback_lifecycle := 'withdrawn';
    else
      fallback_lifecycle := 'draft';
    end if;
    insert into agent_profiles(
      tenant_id, agent_id, lifecycle_status, latest_revision,
      published_revision, published_hash, published_status
    )
    values (
      new.tenant_id, new.agent_id, fallback_lifecycle, new.revision,
      fallback_published_revision, fallback_published_hash,
      case when fallback_published_revision is not null then 'published' else null end
    )
    on conflict (tenant_id, agent_id) do update
    set lifecycle_status = case
          when excluded.lifecycle_status = 'withdrawn' then 'withdrawn'
          else agent_profiles.lifecycle_status
        end,
        latest_revision = greatest(agent_profiles.latest_revision, excluded.latest_revision),
        published_revision = case
          when excluded.lifecycle_status = 'withdrawn' then null
          else agent_profiles.published_revision
        end,
        published_hash = case
          when excluded.lifecycle_status = 'withdrawn' then null
          else agent_profiles.published_hash
        end,
        published_status = case
          when excluded.lifecycle_status = 'withdrawn' then null
          else agent_profiles.published_status
        end,
        updated_at = now();
  end if;
  return null;
end $$;

drop trigger if exists trg_agent_profile_legacy_insert_compatibility on agent_profile_revisions;
create trigger trg_agent_profile_legacy_insert_compatibility
before insert on agent_profile_revisions
for each row execute function agent_profile_legacy_insert_compatibility();

drop trigger if exists trg_agent_profile_legacy_insert_reconcile on agent_profile_revisions;
create trigger trg_agent_profile_legacy_insert_reconcile
after insert on agent_profile_revisions
for each row execute function agent_profile_legacy_insert_reconcile();

-- Add composite tenant+agent authority and profile-pin constraints for existing
-- installations after all referenced tables and columns are present.
do $$
begin
  if not exists (select 1 from pg_constraint where conrelid = 'agent_profile_revisions'::regclass and conname = 'fk_agent_profile_revisions_tenant_agent') then
    alter table agent_profile_revisions add constraint fk_agent_profile_revisions_tenant_agent
      foreign key (tenant_id, agent_id) references agents(tenant_id, id);
  end if;
  if not exists (select 1 from pg_constraint where conrelid = 'sessions'::regclass and conname = 'fk_sessions_tenant_agent') then
    alter table sessions add constraint fk_sessions_tenant_agent
      foreign key (tenant_id, agent_id) references agents(tenant_id, id);
  end if;
  if not exists (select 1 from pg_constraint where conrelid = 'sessions'::regclass and conname = 'fk_sessions_agent_profile_pin') then
    alter table sessions add constraint fk_sessions_agent_profile_pin
      foreign key (tenant_id, agent_id, admitted_agent_profile_revision)
      references agent_profile_revisions(tenant_id, agent_id, revision);
  end if;
  if not exists (select 1 from pg_constraint where conrelid = 'runs'::regclass and conname = 'fk_runs_tenant_agent') then
    alter table runs add constraint fk_runs_tenant_agent
      foreign key (tenant_id, agent_id) references agents(tenant_id, id);
  end if;
  if not exists (select 1 from pg_constraint where conrelid = 'runs'::regclass and conname = 'fk_runs_agent_profile_pin') then
    alter table runs add constraint fk_runs_agent_profile_pin
      foreign key (tenant_id, agent_id, admitted_agent_profile_revision)
      references agent_profile_revisions(tenant_id, agent_id, revision);
  end if;
end $$;

drop index if exists idx_agent_profile_revisions_published_from_draft;
create unique index idx_agent_profile_revisions_published_from_draft
  on agent_profile_revisions(tenant_id, agent_id, published_from_revision)
  where revision_status = 'published' and published_from_revision is not null;
-- Existing rows deliberately remain unordered (NULL generation): timestamps and
-- UUIDs are not a valid historical run-creation authority.
alter table sessions add column if not exists next_run_generation bigint not null default 0;
alter table runs add column if not exists context_snapshot_id text;
alter table runs add column if not exists session_generation bigint;
alter table runs add column if not exists copied_from_run_id text references runs(id);
alter table runs add column if not exists cancel_requested_at timestamptz;
alter table runs add column if not exists cancel_requested_by text;
alter table runs add column if not exists permission_terminalization_target text;
alter table runs add column if not exists permission_terminalization_reason text not null default '';
alter table runs add column if not exists permission_terminalization_result_json jsonb not null default '{}'::jsonb;
alter table runs add column if not exists permission_terminalization_error_code text;
alter table runs add column if not exists permission_terminalization_error_message text;
alter table runs add column if not exists latency_ms integer;
alter table runs add column if not exists input_token_count integer not null default 0;
alter table runs add column if not exists output_token_count integer not null default 0;
alter table runs add column if not exists total_token_count integer not null default 0;
alter table runs add column if not exists estimated_cost_minor integer not null default 0;

create index if not exists idx_runs_trace_id on runs(trace_id);
create unique index if not exists idx_runs_session_generation
  on runs(tenant_id, session_id, session_generation)
  where session_generation is not null;
create unique index if not exists idx_runs_context_scope
  on runs(tenant_id, workspace_id, user_id, session_id, id);
create unique index if not exists idx_sessions_run_scope
  on sessions(tenant_id, workspace_id, user_id, id, agent_id);

-- A durable, principal-scoped record for one client chat mutation.  It is
-- deliberately separate from runs/messages: a rejected request has no run,
-- and a response can be lost after the run transaction commits.
create table if not exists chat_submissions (
  tenant_id text not null references tenants(id),
  user_id text not null references users(id),
  submission_id uuid not null,
  workspace_id text,
  request_fingerprint_sha256 text not null,
  state text not null,
  submission_disposition text,
  rejection_code text,
  -- This optional pointer must not impose a global schema rule on uploads or
  -- other pre-session rows; the submission resolver validates it by scope.
  session_id text,
  run_id text references runs(id),
  outcome_json jsonb not null default '{}'::jsonb,
  queue_position integer,
  queue_admission_ordinal bigint,
  queue_message_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, user_id, submission_id)
);

create index if not exists idx_chat_submissions_scope_updated
  on chat_submissions(tenant_id, user_id, updated_at desc);
create index if not exists idx_chat_submissions_run
  on chat_submissions(tenant_id, run_id)
  where run_id is not null;

do $$
begin
  if exists (
    select 1
    from sessions
    left join workspaces
      on workspaces.tenant_id = sessions.tenant_id
     and workspaces.id = sessions.workspace_id
    where workspaces.id is null
    limit 1
  ) then
    raise exception 'sessions_workspace_tenant_scope_mismatch';
  end if;
  if exists (
    select 1
    from runs
    left join workspaces
      on workspaces.tenant_id = runs.tenant_id
     and workspaces.id = runs.workspace_id
    where workspaces.id is null
    limit 1
  ) then
    raise exception 'runs_workspace_tenant_scope_mismatch';
  end if;
end $$;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'fk_sessions_workspace_scope'
      and conrelid = 'sessions'::regclass
  ) then
    alter table sessions
      add constraint fk_sessions_workspace_scope
      foreign key (tenant_id, workspace_id)
      references workspaces(tenant_id, id);
  end if;
  if not exists (
    select 1
    from pg_constraint
    where conname = 'fk_runs_workspace_scope'
      and conrelid = 'runs'::regclass
  ) then
    alter table runs
      add constraint fk_runs_workspace_scope
      foreign key (tenant_id, workspace_id)
      references workspaces(tenant_id, id);
  end if;
end $$;

-- Rollback for the additive workspace guard:
-- alter table runs drop constraint if exists fk_runs_workspace_scope;
-- alter table sessions drop constraint if exists fk_sessions_workspace_scope;
-- drop index if exists idx_workspaces_tenant_scope;

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
create unique index if not exists idx_run_context_snapshots_scope_binding
  on run_context_snapshots(tenant_id, workspace_id, user_id, session_id, run_id, id);

-- A populated pre-#511 database can adopt a physical binding only when both
-- legacy JSON mirrors already agree and name the exact scoped executor row.
-- All other legacy rows deliberately remain null/display-only: timestamps and
-- UUIDs are not a substitute authority.  This is safe to apply repeatedly and
-- leaves old application versions able to write their nullable mirror fields.
update runs
set context_snapshot_id = runs.input_json->>'context_snapshot_id'
from run_context_snapshots context_snapshot
where runs.context_snapshot_id is null
  and coalesce(runs.input_json->>'context_snapshot_id', '') <> ''
  and runs.input_json->>'context_snapshot_id'
      = runs.input_json->'context_snapshot'->>'context_snapshot_id'
  and context_snapshot.id = runs.input_json->>'context_snapshot_id'
  and context_snapshot.tenant_id = runs.tenant_id
  and context_snapshot.workspace_id = runs.workspace_id
  and context_snapshot.user_id = runs.user_id
  and context_snapshot.session_id = runs.session_id
  and context_snapshot.run_id = runs.id
  and context_snapshot.context_kind = 'executor';

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

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'fk_runs_context_snapshot_scope'
      and conrelid = 'runs'::regclass
  ) then
    alter table runs
      add constraint fk_runs_context_snapshot_scope
      foreign key (tenant_id, workspace_id, user_id, session_id, id, context_snapshot_id)
      references run_context_snapshots(tenant_id, workspace_id, user_id, session_id, run_id, id)
      deferrable initially deferred;
  end if;
end $$;

create or replace function ai_platform_prevent_context_snapshot_rebind()
returns trigger
language plpgsql
as $$
begin
  if old.context_snapshot_id is not null
     and new.context_snapshot_id is distinct from old.context_snapshot_id then
    raise exception 'runs_context_snapshot_id_immutable';
  end if;
  if new.context_snapshot_id is not null
     and coalesce(new.input_json->>'context_snapshot_id', '')
         is distinct from new.context_snapshot_id then
    raise exception 'runs_context_snapshot_input_mismatch';
  end if;
  if new.context_snapshot_id is not null
     and coalesce(new.input_json->'context_snapshot'->>'context_snapshot_id', '')
         is distinct from new.context_snapshot_id then
    raise exception 'runs_context_snapshot_ref_mismatch';
  end if;
  return new;
end;
$$;

do $$
begin
  if not exists (
    select 1
    from pg_trigger
    where tgname = 'trg_runs_context_snapshot_immutable'
      and tgrelid = 'runs'::regclass
  ) then
    create trigger trg_runs_context_snapshot_immutable
      before update of context_snapshot_id, input_json on runs
      for each row execute function ai_platform_prevent_context_snapshot_rebind();
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

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'fk_run_events_run_scope'
      and conrelid = 'run_events'::regclass
  ) then
    alter table run_events
      add constraint fk_run_events_run_scope
      foreign key (tenant_id, run_id) references runs(tenant_id, id);
  end if;
end $$;

create table if not exists run_event_cursors (
  tenant_id text not null,
  run_id text not null,
  next_sequence bigint not null default 1 check (next_sequence > 0),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, run_id), foreign key (tenant_id, run_id) references runs(tenant_id, id)
);

create table if not exists run_event_batches (
  id text primary key,
  tenant_id text not null,
  run_id text not null,
  attempt_id text not null, batch_id text not null,
  event_ids_json jsonb not null default '[]'::jsonb,
  first_sequence bigint, through_sequence bigint,
  callback_received_at timestamptz not null default now(),
  durable_committed_at timestamptz,
  unique (tenant_id, run_id, attempt_id, batch_id), foreign key (tenant_id, run_id) references runs(tenant_id, id)
);

create table if not exists run_event_terminal_drains (
  tenant_id text not null,
  run_id text not null,
  attempt_id text not null,
  batch_id text not null,
  primary key (tenant_id, run_id, attempt_id), foreign key (tenant_id, run_id) references runs(tenant_id, id)
);

do $$
declare
  unique_index_present boolean;
  repair_needed boolean;
begin
  select exists (
    select 1 from pg_index indexes
    where indexes.indexrelid = to_regclass(format('%I.%I', current_schema(), 'uq_run_events_tenant_run_sequence'))
      and indexes.indrelid = 'run_events'::regclass
      and indexes.indisunique and indexes.indisvalid
  ) into unique_index_present;
  select not unique_index_present or exists (
    select 1 from run_events group by tenant_id, run_id
    having min(sequence) < 1 or count(*) <> count(distinct sequence)
  ) into repair_needed;

  if repair_needed then
    lock table run_events in share row exclusive mode;
    select exists (
      select 1 from pg_index indexes
      where indexes.indexrelid = to_regclass(format('%I.%I', current_schema(), 'uq_run_events_tenant_run_sequence'))
        and indexes.indrelid = 'run_events'::regclass
        and indexes.indisunique and indexes.indisvalid
    ) into unique_index_present;
    select not unique_index_present or exists (
      select 1 from run_events group by tenant_id, run_id
      having min(sequence) < 1 or count(*) <> count(distinct sequence)
    ) into repair_needed;

    if repair_needed then
      if not unique_index_present then drop index if exists uq_run_events_tenant_run_sequence; end if;
      with affected_groups as (
        select tenant_id, run_id from run_events group by tenant_id, run_id
        having min(sequence) < 1 or count(*) <> count(distinct sequence)
      ), ranked as (
        select events.id,
               row_number() over (
                 partition by events.tenant_id, events.run_id
                 order by events.sequence asc, events.created_at asc, events.id asc
               ) as replacement_sequence
        from run_events events
        join affected_groups using (tenant_id, run_id)
      )
      update run_events events set sequence = -ranked.replacement_sequence from ranked where events.id = ranked.id;

      update run_events set sequence = -sequence where sequence < 0;
    end if;
  end if;
end $$;

create unique index if not exists uq_run_events_tenant_run_sequence on run_events(tenant_id, run_id, sequence);

insert into run_event_cursors(tenant_id, run_id, next_sequence)
select tenant_id, run_id, coalesce(max(sequence), 0) + 1 from run_events group by tenant_id, run_id
on conflict (tenant_id, run_id) do update set next_sequence = excluded.next_sequence, updated_at = now()
where run_event_cursors.next_sequence < excluded.next_sequence;

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
alter table run_tool_permission_requests add column if not exists expires_at timestamptz;
create index if not exists idx_run_tool_permission_requests_pending_expiry
  on run_tool_permission_requests(tenant_id, expires_at asc, created_at asc, id)
  where status = 'pending';

create table if not exists sandbox_leases (
  id text primary key,
  tenant_id text not null references tenants(id),
  workspace_id text not null references workspaces(id),
  user_id text not null references users(id),
  session_id text not null references sessions(id),
  run_id text not null references runs(id),
  attempt_id text,
  trace_id text not null default '',
  sandbox_mode text not null,
  provider text not null default 'fake',
  status text not null default 'active',
  browser_enabled boolean not null default false,
  resource_limits_json jsonb not null default '{}'::jsonb,
  user_visible_payload_json jsonb not null default '{}'::jsonb,
  lease_payload_json jsonb not null default '{}'::jsonb,
  runtime_container_id text,
  runtime_container_name text,
  runtime_executor_url text,
  runtime_workspace_container_path text,
  runtime_handle_verified_at timestamptz,
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

alter table sandbox_leases add column if not exists attempt_id text;
alter table sandbox_leases add column if not exists runtime_container_id text;
alter table sandbox_leases add column if not exists runtime_container_name text;
alter table sandbox_leases add column if not exists runtime_executor_url text;
alter table sandbox_leases add column if not exists runtime_workspace_container_path text;
alter table sandbox_leases add column if not exists runtime_handle_verified_at timestamptz;
create index if not exists idx_sandbox_leases_attempt
  on sandbox_leases(tenant_id, run_id, attempt_id, status);

-- Rollback for the additive runtime handle columns:
-- drop index if exists idx_sandbox_leases_attempt;
-- alter table sandbox_leases drop column if exists attempt_id;
-- alter table sandbox_leases drop column if exists runtime_handle_verified_at;
-- alter table sandbox_leases drop column if exists runtime_workspace_container_path;
-- alter table sandbox_leases drop column if exists runtime_executor_url;
-- alter table sandbox_leases drop column if exists runtime_container_name;
-- alter table sandbox_leases drop column if exists runtime_container_id;

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
  ('ragflow-knowledge-search', 'RAGFlow Knowledge Search', '0.1.0', 'Query company knowledge base with scoped citations through the platform-managed MCP tool.', '["chat"]'::jsonb, '["answer", "citations"]'::jsonb, 'claude-agent-worker')
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
