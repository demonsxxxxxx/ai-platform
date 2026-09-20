create table if not exists schema_migrations (
  version text primary key,
  checksum_sha256 text not null,
  applied_at timestamptz not null default now()
);

create table if not exists schema_index_migrations (
  index_name text primary key,
  target_version text not null,
  checksum_sha256 text not null,
  state text not null check (state in ('building', 'ready', 'failed')),
  attempts integer not null default 0,
  last_error_code text,
  started_at timestamptz,
  completed_at timestamptz,
  updated_at timestamptz not null default now()
);

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
  metadata_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint chk_users_metadata_json_object check (jsonb_typeof(metadata_json) = 'object')
);

alter table users
  add column if not exists metadata_json jsonb not null default '{}'::jsonb;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'chk_users_metadata_json_object'
      and conrelid = 'users'::regclass
  ) then
    alter table users
      add constraint chk_users_metadata_json_object
      check (jsonb_typeof(metadata_json) = 'object') not valid;
  end if;
end
$$;

alter table users validate constraint chk_users_metadata_json_object;

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

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'mcp_servers_endpoint_not_persisted'
  ) then
    alter table mcp_servers
      add constraint mcp_servers_endpoint_not_persisted
      check (endpoint_redacted = '') not valid;
  end if;
end $$;

alter table mcp_servers
  validate constraint mcp_servers_endpoint_not_persisted;

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

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'mcp_tools_endpoint_not_persisted'
  ) then
    alter table mcp_tools
      add constraint mcp_tools_endpoint_not_persisted
      check (endpoint = '') not valid;
  end if;
end $$;

alter table mcp_tools
  validate constraint mcp_tools_endpoint_not_persisted;

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
create or replace function agent_profile_knowledge_source_ids_are_unique(source_ids jsonb)
returns boolean
language sql
immutable
strict
parallel safe
as $$
  select case
    when jsonb_typeof(source_ids) <> 'array' then false
    else not exists (
      select 1
      from jsonb_array_elements_text(source_ids) as source_ids_table(source_id)
      where source_id !~ '^[A-Za-z0-9_.:-]{1,160}$'
    ) and (
      select count(*) = count(distinct source_id)
      from jsonb_array_elements_text(source_ids) as source_ids_table(source_id)
    )
  end;
$$;

create or replace function agent_profile_knowledge_bindings_are_valid(
  source_ids jsonb,
  retrieval_profile_id text,
  bindings jsonb
)
returns boolean
language sql
immutable
parallel safe
as $$
  select case
    when jsonb_typeof(source_ids) <> 'array' or jsonb_typeof(bindings) <> 'array' then false
    when jsonb_array_length(bindings) = 0 then true
    when retrieval_profile_id is null then false
    when jsonb_array_length(bindings) <> jsonb_array_length(source_ids) then false
    else not exists (
      select 1
      from jsonb_array_elements(bindings) with ordinality as binding_rows(binding, position)
      where jsonb_typeof(binding) <> 'object'
         or not (binding ?& array[
              'source_id',
              'source_authorization_version',
              'ordinal',
              'required',
              'retrieval_profile_id',
              'retrieval_profile_revision'
            ])
         or binding - array[
              'source_id',
              'source_authorization_version',
              'ordinal',
              'required',
              'retrieval_profile_id',
              'retrieval_profile_revision'
            ] <> '{}'::jsonb
         or binding ->> 'source_id' is distinct from source_ids ->> (position - 1)::int
         or jsonb_typeof(binding -> 'source_authorization_version') <> 'number'
         or binding ->> 'source_authorization_version' !~ '^[1-9][0-9]*$'
         or binding -> 'ordinal' <> to_jsonb(position - 1)
         or binding -> 'required' <> 'true'::jsonb
         or binding ->> 'retrieval_profile_id' is distinct from retrieval_profile_id
         or jsonb_typeof(binding -> 'retrieval_profile_revision') <> 'number'
         or binding ->> 'retrieval_profile_revision' !~ '^[1-9][0-9]*$'
    )
  end;
$$;

create table if not exists agent_profile_revisions (
  tenant_id text not null references tenants(id),
  agent_id text not null,
  revision bigint not null check (revision > 0),
  revision_status text not null check (revision_status in ('draft', 'published', 'withdrawn')),
  name text not null,
  description text not null default '',
  starter_prompts jsonb not null default '[]'::jsonb,
  instructions text not null,
  skill_set jsonb not null,
  mcp_tool_ids jsonb not null default '[]'::jsonb,
  knowledge_enabled boolean not null default false,
  knowledge_source_ids jsonb not null default '[]'::jsonb,
  retrieval_profile_id text,
  knowledge_bindings jsonb not null default '[]'::jsonb,
  content_hash text not null,
  avatar_ref text not null check (avatar_ref in (
    'builtin:agent', 'builtin:assistant', 'builtin:document', 'builtin:research',
    'builtin:cartoon', 'builtin:emoji', 'builtin:pixel', 'builtin:portrait',
    'builtin:abstract', 'builtin:planet', 'builtin:clay', 'builtin:icon'
  )),
  avatar_seed text not null,
  market_tags jsonb not null default '[]'::jsonb,
  visibility text not null,
  allowed_department_ids jsonb not null,
  allowed_roles jsonb not null,
  allowed_user_ids jsonb not null,
  created_by text references users(id),
  created_at timestamptz not null default now(),
  published_by text references users(id),
  published_at timestamptz,
  published_from_revision bigint,
  withdrawn_from_revision bigint,
  constraint chk_agent_profile_revisions_visibility
    check (visibility in ('tenant', 'restricted')),
  constraint chk_agent_profile_knowledge_sources check (
    jsonb_typeof(knowledge_source_ids) = 'array'
    and jsonb_array_length(knowledge_source_ids) <= 8
    and not jsonb_path_exists(knowledge_source_ids, '$[*] ? (@.type() != "string")')
    and not jsonb_path_exists(knowledge_source_ids, '$[*] ? (@ == "")')
    and agent_profile_knowledge_source_ids_are_unique(knowledge_source_ids)
  ),
  constraint chk_agent_profile_knowledge_pair check (
    (jsonb_array_length(knowledge_source_ids) = 0 and retrieval_profile_id is null)
    or (jsonb_array_length(knowledge_source_ids) > 0 and retrieval_profile_id is not null)
  ),
  constraint chk_agent_profile_knowledge_bindings check (
    jsonb_typeof(knowledge_bindings) = 'array'
    and jsonb_array_length(knowledge_bindings) <= 8
    and jsonb_array_length(knowledge_bindings) in (
      0,
      jsonb_array_length(knowledge_source_ids)
    )
    and (
      revision_status <> 'published'
      or (
        knowledge_enabled
        and jsonb_array_length(knowledge_source_ids) > 0
        and jsonb_array_length(knowledge_bindings) = jsonb_array_length(knowledge_source_ids)
      )
      or (
        not knowledge_enabled
        and jsonb_array_length(knowledge_bindings) = 0
      )
    )
  ),
  constraint uq_agent_profile_revision_publication
    unique (tenant_id, agent_id, revision, content_hash, revision_status),
  constraint fk_agent_profile_revisions_tenant_agent
    foreign key (tenant_id, agent_id) references agents(tenant_id, id),
  primary key (tenant_id, agent_id, revision)
);

-- Existing pre-#701 tables do not gain columns from CREATE TABLE IF NOT EXISTS.
-- Add the canonical status before any Knowledge constraint references it; the
-- compatibility repair below deliberately needs this column to remain nullable.
alter table agent_profile_revisions
  add column if not exists revision_status text;
alter table agent_profile_revisions
  add column if not exists knowledge_source_ids jsonb not null default '[]'::jsonb;
-- Backfill only when the opt-in column is first introduced. Re-running the
-- canonical schema must never re-enable a deliberately disabled Agent.
do $$
begin
  if not exists (
    select 1
    from information_schema.columns
    where table_schema = current_schema()
      and table_name = 'agent_profile_revisions'
      and column_name = 'knowledge_enabled'
  ) then
    alter table agent_profile_revisions
      add column knowledge_enabled boolean not null default false;
    update agent_profile_revisions
    set knowledge_enabled = true
    where jsonb_array_length(knowledge_source_ids) > 0;
  end if;
end $$;
alter table agent_profile_revisions
  add column if not exists retrieval_profile_id text;
alter table agent_profile_revisions
  add column if not exists knowledge_bindings jsonb not null default '[]'::jsonb;
alter table agent_profile_revisions
  drop constraint if exists chk_agent_profile_knowledge_sources;
alter table agent_profile_revisions
  add constraint chk_agent_profile_knowledge_sources check (
    jsonb_typeof(knowledge_source_ids) = 'array'
    and jsonb_array_length(knowledge_source_ids) <= 8
    and not jsonb_path_exists(knowledge_source_ids, '$[*] ? (@.type() != "string")')
    and not jsonb_path_exists(knowledge_source_ids, '$[*] ? (@ == "")')
    and agent_profile_knowledge_source_ids_are_unique(knowledge_source_ids)
  );
alter table agent_profile_revisions
  drop constraint if exists chk_agent_profile_knowledge_pair;
alter table agent_profile_revisions
  add constraint chk_agent_profile_knowledge_pair check (
    (jsonb_array_length(knowledge_source_ids) = 0 and retrieval_profile_id is null)
    or (
      jsonb_array_length(knowledge_source_ids) > 0
      and retrieval_profile_id is not null
      and retrieval_profile_id = btrim(retrieval_profile_id)
      and retrieval_profile_id ~ '^[A-Za-z0-9_.:-]{1,160}$'
    )
  );
alter table agent_profile_revisions
  drop constraint if exists chk_agent_profile_knowledge_bindings;
alter table agent_profile_revisions
  add constraint chk_agent_profile_knowledge_bindings check (
    jsonb_typeof(knowledge_bindings) = 'array'
    and jsonb_array_length(knowledge_bindings) <= 8
    and octet_length(knowledge_bindings::text) <= 8192
    and jsonb_array_length(knowledge_bindings) in (
      0,
      jsonb_array_length(knowledge_source_ids)
    )
    and agent_profile_knowledge_bindings_are_valid(
      knowledge_source_ids,
      retrieval_profile_id,
      knowledge_bindings
    )
    and (
      revision_status <> 'published'
      or (
        knowledge_enabled
        and jsonb_array_length(knowledge_source_ids) > 0
        and jsonb_array_length(knowledge_bindings) = jsonb_array_length(knowledge_source_ids)
      )
      or (
        not knowledge_enabled
        and jsonb_array_length(knowledge_bindings) = 0
      )
    )
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
      )
      or (
        lifecycle_status <> 'published'
        and published_revision is null
        and published_hash is null
      )
    )
);

create index if not exists idx_agent_profiles_published
  on agent_profiles(tenant_id, published_revision desc)
  where lifecycle_status = 'published';

create table if not exists agent_profile_favorites (
  tenant_id text not null references tenants(id),
  user_id text not null references users(id),
  agent_id text not null,
  created_at timestamptz not null default now(),
  primary key (tenant_id, user_id, agent_id),
  foreign key (tenant_id, agent_id) references agents(tenant_id, id)
);

create index if not exists idx_agent_profile_favorites_user
  on agent_profile_favorites(tenant_id, user_id, created_at desc);

create table if not exists sessions (
  id text primary key,
  tenant_id text not null references tenants(id),
  workspace_id text not null references workspaces(id),
  user_id text references users(id),
  agent_id text not null,
  title text not null default '',
  title_source text not null default 'initial'
    check (title_source in ('initial', 'generated', 'user')),
  status text not null default 'active',
  purpose text not null default 'conversation'
    check (purpose in ('conversation', 'builder_test')),
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

alter table sessions add column if not exists title_source text not null default 'initial';
alter table sessions drop constraint if exists chk_sessions_title_source;
alter table sessions add constraint chk_sessions_title_source
  check (title_source in ('initial', 'generated', 'user'));

-- Provider continuity is executor-private and inherits Session deletion.
create unique index if not exists idx_sessions_provider_scope
  on sessions(tenant_id, workspace_id, user_id, id, agent_id);

create table if not exists model_gateway_revisions (
  revision bigint primary key,
  base_url text not null,
  api_key_ciphertext bytea not null,
  key_fingerprint text not null,
  active boolean not null default false,
  created_by text not null,
  created_at timestamptz not null default now(),
  constraint chk_model_gateway_revision_positive check (revision > 0),
  constraint chk_model_gateway_base_url check (length(base_url) between 1 and 2048),
  constraint chk_model_gateway_key_fingerprint check (key_fingerprint ~ '^[0-9a-f]{16}$')
);
create unique index if not exists uq_model_gateway_active
  on model_gateway_revisions(active) where active = true;

create table if not exists model_catalog_entries (
  model_id text primary key,
  upstream_model_id text not null unique,
  display_name text not null,
  provider text not null default 'custom',
  enabled boolean not null default false,
  upstream_available boolean not null default true,
  is_default boolean not null default false,
  display_order integer not null default 0,
  max_input_tokens bigint,
  max_output_tokens bigint,
  first_seen_revision bigint not null references model_gateway_revisions(revision),
  last_seen_revision bigint not null references model_gateway_revisions(revision),
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  constraint chk_model_catalog_id check (model_id ~ '^[A-Za-z0-9_.:-]{1,128}$'),
  constraint chk_model_catalog_upstream_id check (
    length(upstream_model_id) between 1 and 512
    and upstream_model_id = btrim(upstream_model_id)
  ),
  constraint chk_model_catalog_display_name check (length(display_name) between 1 and 160),
  constraint chk_model_catalog_default_enabled check (not is_default or enabled),
  constraint chk_model_catalog_token_limits check (
    (max_input_tokens is null and max_output_tokens is null)
    or (max_input_tokens is not null and max_output_tokens is not null
        and max_input_tokens between 1 and 10000000 and max_output_tokens between 1 and 10000000)
  )
);
create unique index if not exists uq_model_catalog_default
  on model_catalog_entries(is_default) where is_default = true;

create table if not exists runs (
  id text primary key,
  tenant_id text not null references tenants(id),
  workspace_id text not null references workspaces(id),
  session_id text not null references sessions(id),
  user_id text references users(id),
  agent_id text not null,
  execution_kind text not null default 'skill',
  skill_id text references skills(id),
  trace_id text not null default '',
  schema_version text not null default 'ai-platform.run.v1',
  executor_schema_version text not null default 'ai-platform.executor-result.v1',
  principal_roles jsonb not null default '[]'::jsonb,
  principal_department_id text not null default '',
  auth_source text,
  authz_policy_version integer not null default 1,
  authority_source text not null default '',
  authority_checked_at timestamptz,
  admitted_agent_profile_revision bigint,
  admitted_agent_profile_hash text,
  model_id text,
  model_value text,
  model_gateway_revision bigint,
  max_input_tokens bigint,
  max_output_tokens bigint,
  status text not null,
  input_json jsonb not null default '{}'::jsonb,
  context_snapshot_id text,
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
  ) references agent_profile_revisions(tenant_id, agent_id, revision),
  constraint chk_runs_execution_skill_identity check (
    (execution_kind = 'harness_chat' and skill_id is null)
    or (execution_kind = 'skill' and skill_id is not null)
  ),
  constraint chk_runs_model_token_limits check (
    (max_input_tokens is null and max_output_tokens is null)
    or (max_input_tokens is not null and max_output_tokens is not null
        and max_input_tokens between 1 and 10000000 and max_output_tokens between 1 and 10000000)
  )
);

create index if not exists idx_runs_tenant_created on runs(tenant_id, created_at desc);
create index if not exists idx_runs_session_created on runs(session_id, created_at desc);
create index if not exists idx_runs_status on runs(status);
create unique index if not exists uq_runs_tenant_id on runs(tenant_id, id);

create table if not exists run_diagnostics (
  diagnostic_id text primary key,
  tenant_id text not null,
  run_id text not null,
  schema_version text not null,
  revision bigint not null default 1,
  payload_json jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint fk_run_diagnostics_run foreign key (tenant_id, run_id)
    references runs(tenant_id, id),
  constraint chk_run_diagnostics_identity check (
    diagnostic_id <> '' and tenant_id <> '' and run_id <> ''
  ),
  constraint chk_run_diagnostics_revision check (revision > 0),
  constraint chk_run_diagnostics_payload check (
    jsonb_typeof(payload_json) = 'object'
    and payload_json ? 'schema_version'
    and payload_json->>'schema_version' is not null
    and payload_json->>'schema_version' = schema_version
    and octet_length(payload_json::text) <= 147456
  ),
  unique (tenant_id, run_id)
);

create table if not exists run_attempts (
  id text primary key,
  tenant_id text not null,
  run_id text not null,
  ordinal integer not null,
  status text not null,
  owner_kind text not null,
  owner_id text not null,
  owner_generation bigint not null default 1,
  queue_message_id text,
  queue_attempt_id text not null,
  execution_spec_schema_version text not null,
  execution_spec_json jsonb not null,
  execution_spec_canonical_json text not null,
  execution_spec_sha256 text not null,
  lease_expires_at timestamptz,
  last_heartbeat_at timestamptz,
  started_at timestamptz,
  finished_at timestamptz,
  terminal_reason text not null default '',
  error_code text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint fk_run_attempts_run foreign key (tenant_id, run_id)
    references runs(tenant_id, id),
  constraint chk_run_attempts_ordinal check (ordinal > 0),
  constraint chk_run_attempts_owner_generation check (owner_generation > 0),
  constraint chk_run_attempts_status check (
    status in (
      'created', 'queued', 'claimed', 'running', 'cancel_requested', 'expired',
      'succeeded', 'failed', 'cancelled'
    )
  ),
  constraint chk_run_attempts_owner_kind check (
    owner_kind in ('queue_worker', 'reconciler', 'operator')
  ),
  constraint chk_run_attempts_required_identity check (
    id <> ''
    and owner_id <> ''
    and queue_attempt_id <> ''
    and execution_spec_schema_version <> ''
    and (queue_message_id is null or queue_message_id <> '')
  ),
  constraint chk_run_attempts_spec_json check (
    jsonb_typeof(execution_spec_json) = 'object'
    and execution_spec_json->>'schema_version' = execution_spec_schema_version
  ),
  constraint chk_run_attempts_spec_canonical_json check (
    execution_spec_canonical_json <> ''
    and execution_spec_canonical_json::jsonb = execution_spec_json
  ),
  constraint chk_run_attempts_spec_sha256 check (
    execution_spec_sha256 ~ '^[0-9a-f]{64}$'
    and execution_spec_sha256 = encode(
      sha256(convert_to(execution_spec_canonical_json, 'UTF8')),
      'hex'
    )
  ),
  constraint chk_run_attempts_terminal_time check (
    (
      status in ('succeeded', 'failed', 'cancelled')
      and finished_at is not null
    ) or (
      status not in ('succeeded', 'failed', 'cancelled')
      and finished_at is null
    )
  ),
  unique (tenant_id, run_id, ordinal),
  unique (tenant_id, run_id, queue_attempt_id),
  constraint uq_run_attempts_tenant_run_id unique (tenant_id, run_id, id)
);

create unique index if not exists uq_run_attempts_one_open
  on run_attempts(tenant_id, run_id)
  where status in (
    'created', 'queued', 'claimed', 'running', 'cancel_requested', 'expired'
  );
create index if not exists idx_run_attempts_run_created
  on run_attempts(tenant_id, run_id, ordinal desc);
create index if not exists idx_run_attempts_lease_reconcile
  on run_attempts(lease_expires_at asc, tenant_id, run_id, id)
  where status in ('claimed', 'running', 'cancel_requested', 'expired');

create or replace function ai_platform_guard_run_attempt_transition()
returns trigger
language plpgsql
as $$
declare
  projected_run_status text;
begin
  if tg_op = 'INSERT' then
    if new.status <> 'created'
       or new.owner_generation <> 1
       or new.queue_message_id is not null
       or new.lease_expires_at is not null
       or new.last_heartbeat_at is not null
       or new.started_at is not null
       or new.finished_at is not null
       or new.terminal_reason <> ''
       or new.error_code is not null then
      raise exception 'run_attempt_initial_state_invalid' using errcode = '23514';
    end if;
    perform 1
    from runs
    where tenant_id = new.tenant_id
      and id = new.run_id
      and status = 'queued'
      and new.execution_spec_json->>'tenant_id' = new.tenant_id
      and new.execution_spec_json->>'run_id' = new.run_id
      and workspace_id = new.execution_spec_json->>'workspace_id'
      and user_id = new.execution_spec_json->>'user_id'
      and session_id = new.execution_spec_json->>'session_id'
      and agent_id = new.execution_spec_json->>'agent_id'
      and execution_kind = new.execution_spec_json->>'execution_kind'
      and skill_id is not distinct from nullif(
        new.execution_spec_json->>'skill_id',
        ''
      )
    for update;
    if not found then
      raise exception 'run_attempt_parent_state_invalid' using errcode = '23514';
    end if;
    return new;
  end if;
  if old.status in ('succeeded', 'failed', 'cancelled')
     and new is distinct from old then
    raise exception 'run_attempt_terminal_immutable' using errcode = '23514';
  end if;
  if new.id is distinct from old.id
     or new.tenant_id is distinct from old.tenant_id
     or new.run_id is distinct from old.run_id
     or new.ordinal is distinct from old.ordinal
     or new.queue_attempt_id is distinct from old.queue_attempt_id
     or new.execution_spec_schema_version is distinct from old.execution_spec_schema_version
     or new.execution_spec_json is distinct from old.execution_spec_json
     or new.execution_spec_canonical_json is distinct from old.execution_spec_canonical_json
     or new.execution_spec_sha256 is distinct from old.execution_spec_sha256
     or new.created_at is distinct from old.created_at then
    raise exception 'run_attempt_identity_immutable' using errcode = '23514';
  end if;
  if new.queue_message_id is distinct from old.queue_message_id
     and not (
       old.status = 'created'
       and new.status = 'queued'
       and old.queue_message_id is null
       and new.queue_message_id is not null
     ) then
    raise exception 'run_attempt_queue_identity_immutable' using errcode = '23514';
  end if;
  if new.status is not distinct from old.status then
    if new.owner_generation is not distinct from old.owner_generation
       and new.owner_kind is not distinct from old.owner_kind
       and new.owner_id is not distinct from old.owner_id then
      return new;
    end if;
    if old.status = 'cancel_requested'
       and new.owner_kind = 'reconciler'
       and new.owner_generation = old.owner_generation + 1
       and (
         new.owner_kind is distinct from old.owner_kind
         or new.owner_id is distinct from old.owner_id
       ) then
      return new;
    end if;
    raise exception 'run_attempt_owner_transition_invalid' using errcode = '23514';
  end if;
  if new.owner_generation is distinct from old.owner_generation + 1 then
    raise exception 'run_attempt_owner_generation_invalid' using errcode = '23514';
  end if;
  if new.status = 'expired' and new.owner_kind <> 'reconciler' then
    raise exception 'run_attempt_expiry_reconciler_required' using errcode = '23514';
  end if;
  if not (
    (old.status = 'created' and new.status in ('queued', 'cancelled'))
    or (old.status = 'queued' and new.status in ('claimed', 'cancelled'))
    or (
      old.status = 'claimed'
      and new.status in ('running', 'cancel_requested', 'failed', 'expired')
    )
    or (
      old.status = 'running'
      and new.status in ('cancel_requested', 'succeeded', 'failed', 'expired')
    )
    or (old.status = 'cancel_requested' and new.status = 'cancelled')
    or (old.status = 'expired' and new.status in ('failed', 'cancelled'))
  ) then
    raise exception 'run_attempt_transition_invalid' using errcode = '23514';
  end if;
  projected_run_status := case
    when new.status in ('created', 'queued') then 'queued'
    when new.status in ('claimed', 'running', 'cancel_requested', 'expired') then 'running'
    else new.status
  end;
  update runs
  set status = projected_run_status,
      started_at = case
        when projected_run_status = 'running' then coalesce(started_at, now())
        else started_at
      end,
      finished_at = case
        when projected_run_status in ('succeeded', 'failed', 'cancelled')
          then coalesce(finished_at, now())
        else finished_at
      end
  where tenant_id = new.tenant_id
    and id = new.run_id
    and (
      (projected_run_status = 'queued' and status = 'queued')
      or (projected_run_status = 'running' and status in ('queued', 'running'))
      or (
        projected_run_status in ('succeeded', 'failed', 'cancelled')
        and status in ('queued', 'running', projected_run_status)
      )
    );
  if not found then
    raise exception 'run_attempt_parent_transition_conflict' using errcode = '23514';
  end if;
  return new;
end $$;

drop trigger if exists trg_run_attempt_transition_guard on run_attempts;
create trigger trg_run_attempt_transition_guard
before insert or update on run_attempts
for each row execute function ai_platform_guard_run_attempt_transition();

create or replace function ai_platform_guard_run_attempt_heartbeat_monotonicity()
returns trigger
language plpgsql
as $$
begin
  if old.last_heartbeat_at is not null
     and (
       new.last_heartbeat_at is null
       or new.last_heartbeat_at < old.last_heartbeat_at
     ) then
    raise exception 'run_attempt_heartbeat_regression' using errcode = '23514';
  end if;
  if old.lease_expires_at is not null
     and (
       new.lease_expires_at is null
       or new.lease_expires_at < old.lease_expires_at
     ) then
    raise exception 'run_attempt_lease_expiry_regression' using errcode = '23514';
  end if;
  return new;
end $$;

drop trigger if exists trg_run_attempt_heartbeat_monotonicity_guard on run_attempts;
create trigger trg_run_attempt_heartbeat_monotonicity_guard
before update on run_attempts
for each row execute function ai_platform_guard_run_attempt_heartbeat_monotonicity();

alter table runs add column if not exists trace_id text not null default '';
alter table runs add column if not exists execution_kind text not null default 'skill';
do $$
begin
  if exists (
    select 1
    from pg_attribute
    where attrelid = 'runs'::regclass
      and attname = 'skill_id'
      and attnotnull
  ) then
    alter table runs alter column skill_id drop not null;
  end if;
  if not exists (
    select 1
    from pg_constraint
    where conrelid = 'runs'::regclass
      and conname = 'chk_runs_execution_skill_identity'
  ) then
    alter table runs add constraint chk_runs_execution_skill_identity check (
      (execution_kind = 'harness_chat' and skill_id is null)
      or (execution_kind = 'skill' and skill_id is not null)
    );
  end if;
end
$$;
alter table runs add column if not exists schema_version text not null default 'ai-platform.run.v1';
alter table runs add column if not exists executor_schema_version text not null default 'ai-platform.executor-result.v1';
alter table runs add column if not exists principal_roles jsonb not null default '[]'::jsonb;
alter table runs add column if not exists principal_department_id text not null default '';
alter table runs add column if not exists auth_source text;
alter table runs add column if not exists authz_policy_version integer not null default 1;
alter table runs add column if not exists authority_source text not null default '';
alter table runs add column if not exists authority_checked_at timestamptz;
alter table sessions add column if not exists admitted_agent_profile_revision bigint;
alter table sessions add column if not exists admitted_agent_profile_hash text;
alter table sessions add column if not exists purpose text not null default 'conversation';
alter table sessions drop constraint if exists chk_sessions_purpose;
alter table sessions drop constraint if exists sessions_purpose_check;
alter table sessions add constraint chk_sessions_purpose
  check (purpose in ('conversation', 'builder_test'));
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
alter table runs add column if not exists model_id text;
alter table runs add column if not exists model_value text;
alter table runs add column if not exists model_gateway_revision bigint;
alter table runs add column if not exists max_input_tokens bigint;
alter table runs add column if not exists max_output_tokens bigint;
alter table model_catalog_entries add column if not exists max_input_tokens bigint;
alter table model_catalog_entries add column if not exists max_output_tokens bigint;
do $$ begin
  if not exists (select 1 from pg_constraint where conrelid = 'runs'::regclass and conname = 'chk_runs_model_token_limits') then
    alter table runs add constraint chk_runs_model_token_limits check (
      (max_input_tokens is null and max_output_tokens is null)
      or (max_input_tokens is not null and max_output_tokens is not null
          and max_input_tokens between 1 and 10000000 and max_output_tokens between 1 and 10000000)
    );
  end if;
  if not exists (select 1 from pg_constraint where conrelid = 'model_catalog_entries'::regclass and conname = 'chk_model_catalog_token_limits') then
    alter table model_catalog_entries add constraint chk_model_catalog_token_limits check (
      (max_input_tokens is null and max_output_tokens is null)
      or (max_input_tokens is not null and max_output_tokens is not null
          and max_input_tokens between 1 and 10000000 and max_output_tokens between 1 and 10000000)
    );
  end if;
end $$;
alter table agent_profile_revisions add column if not exists published_from_revision bigint;
alter table agent_profile_revisions add column if not exists withdrawn_from_revision bigint;
alter table agent_profile_revisions add column if not exists revision_status text not null default 'withdrawn';
alter table agent_profile_revisions add column if not exists starter_prompts jsonb not null default '[]'::jsonb;
alter table agent_profile_revisions add column if not exists skill_set jsonb not null default '[]'::jsonb;
alter table agent_profile_revisions add column if not exists avatar_ref text not null default 'builtin:agent';
alter table agent_profile_revisions add column if not exists avatar_seed text not null default '';
alter table agent_profile_revisions add column if not exists market_tags jsonb not null default '[]'::jsonb;
alter table agent_profile_revisions add column if not exists visibility text not null default 'restricted';
alter table agent_profile_revisions add column if not exists allowed_department_ids jsonb not null default '[]'::jsonb;
alter table agent_profile_revisions add column if not exists allowed_roles jsonb not null default '[]'::jsonb;
alter table agent_profile_revisions add column if not exists allowed_user_ids jsonb not null default '[]'::jsonb;

alter table agent_profiles drop constraint if exists fk_agent_profiles_published_revision;
alter table agent_profiles drop constraint if exists fk_agent_profiles_current_publication;
alter table agent_profiles drop constraint if exists chk_agent_profiles_publication;
alter table agent_profiles drop constraint if exists agent_profiles_lifecycle_status_check;
alter table agent_profiles drop constraint if exists chk_agent_profiles_lifecycle_status;

alter table agent_profile_revisions drop constraint if exists agent_profile_revisions_status_check;
alter table agent_profile_revisions drop constraint if exists agent_profile_revisions_revision_status_check;
alter table agent_profile_revisions drop constraint if exists agent_profile_revisions_avatar_ref_check;
alter table agent_profile_revisions drop constraint if exists chk_agent_profile_revisions_visibility;
alter table agent_profile_revisions drop constraint if exists agent_profile_revisions_visibility_check;
alter table agent_profile_revisions drop constraint if exists uq_agent_profile_revision_publication;

alter table agent_profile_revisions alter column revision_status drop default;
alter table agent_profile_revisions alter column skill_set drop default;
alter table agent_profile_revisions alter column avatar_ref drop default;
alter table agent_profile_revisions alter column avatar_seed drop default;
alter table agent_profile_revisions alter column visibility drop default;
alter table agent_profile_revisions alter column allowed_department_ids drop default;
alter table agent_profile_revisions alter column allowed_roles drop default;
alter table agent_profile_revisions alter column allowed_user_ids drop default;

alter table agent_profile_revisions add constraint agent_profile_revisions_revision_status_check
  check (revision_status in ('draft', 'published', 'withdrawn'));
alter table agent_profile_revisions add constraint agent_profile_revisions_avatar_ref_check
  check (avatar_ref in (
    'builtin:agent', 'builtin:assistant', 'builtin:document', 'builtin:research',
    'builtin:cartoon', 'builtin:emoji', 'builtin:pixel', 'builtin:portrait',
    'builtin:abstract', 'builtin:planet', 'builtin:clay', 'builtin:icon'
  ));
alter table agent_profile_revisions add constraint chk_agent_profile_revisions_visibility
  check (visibility in ('tenant', 'restricted'));
alter table agent_profile_revisions add constraint uq_agent_profile_revision_publication
  unique (tenant_id, agent_id, revision, content_hash, revision_status);

drop index if exists idx_agent_profile_revisions_published;
create index idx_agent_profile_revisions_published
  on agent_profile_revisions(tenant_id, agent_id, revision desc)
  where revision_status = 'published';

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
  if not exists (select 1 from pg_constraint where conrelid = 'runs'::regclass and conname = 'fk_runs_model_gateway_revision') then
    alter table runs add constraint fk_runs_model_gateway_revision
      foreign key (model_gateway_revision) references model_gateway_revisions(revision);
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

create table if not exists run_skill_materializations (
  tenant_id text not null references tenants(id),
  run_id text not null references runs(id),
  skill_id text not null references skills(id),
  materialization_sha256 text not null check (materialization_sha256 ~ '^[0-9a-f]{64}$'),
  manifest_json jsonb not null,
  created_at timestamptz not null default now(),
  primary key (tenant_id, run_id, skill_id),
  foreign key (tenant_id, run_id, skill_id)
    references run_skill_snapshots(tenant_id, run_id, skill_id) on delete cascade
);

create index if not exists idx_run_skill_materializations_run
  on run_skill_materializations(tenant_id, run_id);

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
  conversation_authority_json jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_run_context_snapshots_run
  on run_context_snapshots(tenant_id, run_id, created_at desc);
create unique index if not exists idx_run_context_snapshots_scope_binding
  on run_context_snapshots(tenant_id, workspace_id, user_id, session_id, run_id, id);

alter table run_context_snapshots add column if not exists conversation_authority_json jsonb;

-- The interim #1397 binding layout was never shipped in main. An existing
-- experimental database requires an explicit data disposition, not silent reuse.
do $$
begin
  if to_regclass('provider_session_bindings') is not null then
    raise exception 'provider_session_legacy_binding_requires_disposition';
  end if;
end $$;

create table if not exists conversation_context_checkpoints (
  id text primary key,
  tenant_id text not null, workspace_id text not null, user_id text not null,
  session_id text not null, agent_id text not null,
  predecessor_checkpoint_id text,
  source_snapshot_id text not null,
  range_start_created_at timestamptz, range_start_id text,
  range_end_created_at timestamptz, range_end_id text,
  through_session_generation bigint not null check (through_session_generation > 0),
  covered_message_count bigint not null default 0 check (covered_message_count >= 0),
  covered_turn_count bigint not null default 0 check (covered_turn_count >= 0),
  source_sha256 text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
  summary_text text,
  summary_sha256 text,
  summary_schema_version text not null,
  summary_prompt_version text not null,
  model_id text not null, model_value text not null,
  model_gateway_revision bigint not null check (model_gateway_revision > 0),
  max_input_tokens bigint not null check (max_input_tokens > 0),
  max_output_tokens bigint not null check (max_output_tokens > 0),
  build_key_sha256 text not null check (build_key_sha256 ~ '^[0-9a-f]{64}$'),
  state text not null check (state in ('building', 'ready', 'failed')),
  owner_run_id text not null,
  builder_lease_id text,
  lease_not_after timestamptz,
  input_tokens bigint not null default 0 check (input_tokens >= 0),
  output_tokens bigint not null default 0 check (output_tokens >= 0),
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  constraint fk_context_checkpoint_session foreign key (
    tenant_id, workspace_id, user_id, session_id, agent_id
  ) references sessions(tenant_id, workspace_id, user_id, id, agent_id) on delete cascade,
  constraint fk_context_checkpoint_owner_scope foreign key (
    tenant_id, workspace_id, user_id, session_id, owner_run_id
  ) references runs(tenant_id, workspace_id, user_id, session_id, id) on delete cascade,
  constraint fk_context_checkpoint_source_scope foreign key (
    tenant_id, workspace_id, user_id, session_id, owner_run_id, source_snapshot_id
  ) references run_context_snapshots(tenant_id, workspace_id, user_id, session_id, run_id, id)
    on delete cascade,
  constraint fk_context_checkpoint_predecessor foreign key (
    tenant_id, workspace_id, user_id, session_id, agent_id, predecessor_checkpoint_id
  ) references conversation_context_checkpoints(
    tenant_id, workspace_id, user_id, session_id, agent_id, id
  ) on delete cascade,
  constraint chk_context_checkpoint_ready check (
    state <> 'ready' or (covered_message_count > 0 and covered_turn_count > 0
      and summary_text is not null and summary_text <> ''
      and summary_sha256 ~ '^[0-9a-f]{64}$'
      and range_start_created_at is not null and range_start_id is not null
      and range_end_created_at is not null and range_end_id is not null)
  ),
  unique (tenant_id, workspace_id, user_id, session_id, agent_id, id),
  unique (tenant_id, workspace_id, user_id, session_id, agent_id, build_key_sha256)
);

create table if not exists provider_session_heads (
  tenant_id text not null, workspace_id text not null,
  user_id text not null, session_id text not null,
  agent_id text not null, engine text not null,
  current_epoch_id text,
  next_epoch_number bigint not null default 1 check (next_epoch_number >= 1),
  active_run_id text, active_attempt_id text, updated_at timestamptz not null default now(),
  constraint chk_provider_head_engine check (engine = 'claude'),
  constraint chk_provider_head_writer check (
    (active_run_id is null and active_attempt_id is null)
    or active_run_id is not null
  ),
  constraint pk_provider_session_heads primary key (tenant_id, session_id, engine),
  constraint uq_provider_head_scope unique (tenant_id, workspace_id, user_id, session_id, agent_id, engine),
  constraint fk_provider_head_session foreign key (
    tenant_id, workspace_id, user_id, session_id, agent_id
  ) references sessions(tenant_id, workspace_id, user_id, id, agent_id) on delete cascade
);

create table if not exists provider_session_epochs (
  id text primary key, tenant_id text not null, workspace_id text not null,
  user_id text not null, session_id text not null, agent_id text not null,
  engine text not null,
  epoch_number bigint not null check (epoch_number >= 1),
  provider_session_id uuid not null,
  state text not null check (state in ('bootstrapping', 'ready', 'active', 'dirty', 'closed')),
  next_sequence bigint not null default 1 check (next_sequence >= 1),
  entry_count bigint not null default 0 check (entry_count >= 0),
  transcript_bytes bigint not null default 0 check (transcript_bytes >= 0),
  coverage_source_sha256 text check (coverage_source_sha256 ~ '^[0-9a-f]{64}$'),
  coverage_through_generation bigint,
  coverage_message_count bigint not null default 0 check (coverage_message_count >= 0),
  writer_run_id text, writer_attempt_id text, writer_owner_generation bigint,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(), closed_at timestamptz,
  constraint chk_provider_epoch_writer check (
    (writer_run_id is null and writer_attempt_id is null and writer_owner_generation is null)
    or (writer_run_id is not null and writer_attempt_id is not null and writer_owner_generation > 0)
  ),
  constraint chk_provider_epoch_coverage check (
    (coverage_source_sha256 is null and coverage_through_generation is null and coverage_message_count = 0)
    or (coverage_source_sha256 is not null and coverage_through_generation > 0)
  ),
  constraint fk_provider_epoch_head foreign key (
    tenant_id, workspace_id, user_id, session_id, agent_id, engine
  ) references provider_session_heads(tenant_id, workspace_id, user_id, session_id, agent_id, engine)
    on delete cascade,
  constraint uq_provider_epoch_scope unique (
    tenant_id, workspace_id, user_id, session_id, agent_id, engine, id
  ),
  constraint uq_provider_epoch_number unique (tenant_id, session_id, engine, epoch_number),
  constraint uq_provider_epoch_provider_id unique (provider_session_id)
);

alter table provider_session_heads drop constraint if exists fk_provider_head_current_epoch;
alter table provider_session_heads add constraint fk_provider_head_current_epoch foreign key (
  tenant_id, workspace_id, user_id, session_id, agent_id, engine, current_epoch_id
) references provider_session_epochs(tenant_id, workspace_id, user_id, session_id, agent_id, engine, id)
  deferrable initially deferred;

create table if not exists provider_session_entries (
  id text primary key,
  tenant_id text not null, workspace_id text not null, user_id text not null,
  session_id text not null, agent_id text not null, engine text not null check (engine = 'claude'),
  epoch_id text not null, subpath text not null default '',
  sequence bigint not null check (sequence >= 1),
  sdk_entry_uuid text, entry_json jsonb not null,
  created_at timestamptz not null default now(),
  constraint fk_provider_entry_epoch foreign key (
    tenant_id, workspace_id, user_id, session_id, agent_id, engine, epoch_id
  ) references provider_session_epochs(
    tenant_id, workspace_id, user_id, session_id, agent_id, engine, id
  ) on delete cascade,
  constraint uq_provider_entry_global_sequence unique (epoch_id, sequence)
);
create unique index if not exists uq_provider_entry_sdk_uuid
  on provider_session_entries(epoch_id, subpath, sdk_entry_uuid)
  where sdk_entry_uuid is not null and sdk_entry_uuid <> '';
create index if not exists idx_provider_entry_view
  on provider_session_entries(epoch_id, subpath, sequence);

create table if not exists provider_session_append_receipts (
  epoch_id text not null references provider_session_epochs(id) on delete cascade,
  expected_sequence bigint not null check (expected_sequence >= 1),
  batch_sha256 text not null check (batch_sha256 ~ '^[0-9a-f]{64}$'),
  entry_count integer not null check (entry_count > 0),
  last_sequence bigint not null check (last_sequence = expected_sequence + entry_count - 1),
  run_id text not null, attempt_id text not null, owner_generation bigint not null check (owner_generation >= 1),
  created_at timestamptz not null default now(), primary key (epoch_id, expected_sequence)
);

create table if not exists provider_turn_receipts (
  id text primary key, tenant_id text not null, workspace_id text not null, user_id text not null,
  session_id text not null, agent_id text not null, engine text not null check (engine = 'claude'),
  epoch_id text not null, run_id text not null, attempt_id text not null,
  execution_spec_sha256 text not null check (execution_spec_sha256 ~ '^[0-9a-f]{64}$'),
  bootstrap_source_sha256 text check (bootstrap_source_sha256 ~ '^[0-9a-f]{64}$'),
  start_sequence bigint not null check (start_sequence >= 1),
  final_sequence bigint check (final_sequence is null or final_sequence >= start_sequence), user_message_id text,
  assistant_message_id text, prior_coverage_sha256 text check (prior_coverage_sha256 ~ '^[0-9a-f]{64}$'),
  committed_coverage_sha256 text check (committed_coverage_sha256 ~ '^[0-9a-f]{64}$'),
  state text not null check (state in ('writing', 'commit_pending', 'committed', 'failed')),
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  constraint fk_provider_turn_epoch foreign key (
    tenant_id, workspace_id, user_id, session_id, agent_id, engine, epoch_id
  ) references provider_session_epochs(
    tenant_id, workspace_id, user_id, session_id, agent_id, engine, id
  ) on delete cascade,
  unique (tenant_id, run_id, attempt_id)
);

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
  payload_digest text not null default '', projection_version text not null default 'legacy-run-event-v1',
  item_count integer not null default 0 check (item_count >= 0), first_source_sequence integer, through_source_sequence integer,
  callback_received_at timestamptz not null default now(),
  durable_committed_at timestamptz,
  unique (tenant_id, run_id, attempt_id, batch_id), foreign key (tenant_id, run_id) references runs(tenant_id, id)
);

alter table run_event_batches add column if not exists payload_digest text not null default '';
alter table run_event_batches add column if not exists projection_version text not null default 'legacy-run-event-v1';
alter table run_event_batches add column if not exists item_count integer not null default 0;
alter table run_event_batches add column if not exists first_source_sequence integer;
alter table run_event_batches add column if not exists through_source_sequence integer;

create table if not exists run_event_terminal_drains (
  tenant_id text not null,
  run_id text not null,
  attempt_id text not null,
  batch_id text not null,
  primary key (tenant_id, run_id, attempt_id), foreign key (tenant_id, run_id) references runs(tenant_id, id)
);

create table if not exists sse_stream_authorities (
  tenant_id text not null, run_id text not null, attempt_id text not null,
  design_id text not null, projection_version text not null, tenant_scope text not null,
  stream_incarnation bigint not null check (stream_incarnation > 0), state text not null default 'admission_pending' check (state in ('admission_pending', 'confirmed', 'degraded', 'terminal')),
  open_event_id text not null, open_payload_bytes text not null, open_payload_digest text not null,

  authorization_epoch bigint not null default 1 check (authorization_epoch > 0), revocation_state text not null default 'active' check (revocation_state in ('active', 'committed', 'effective')),
  admission_created_at timestamptz not null default clock_timestamp(), admission_confirmed_at timestamptz, degraded_at timestamptz,
  revocation_committed_at timestamptz, revocation_effective_at timestamptz, updated_at timestamptz not null default clock_timestamp(),
  constraint chk_sse_stream_authority_open_format check (
    open_event_id <> '' and open_payload_bytes <> '' and open_payload_digest ~ '^[0-9a-f]{64}$'
  ),
  constraint chk_sse_stream_authority_pending_confirmation check (
    (state = 'admission_pending' and admission_confirmed_at is null)
    or (state <> 'admission_pending' and admission_confirmed_at is not null)
  ),
  primary key (tenant_id, run_id), foreign key (tenant_id, run_id) references runs(tenant_id, id)
);

create unique index if not exists uq_sse_stream_authority_attempt_incarnation
  on sse_stream_authorities(tenant_id, run_id, attempt_id, stream_incarnation);

update sse_stream_authorities
set admission_confirmed_at = coalesce(
  admission_confirmed_at,
  admission_created_at,
  updated_at,
  clock_timestamp()
)
where state <> 'admission_pending'
  and admission_confirmed_at is null;

update sse_stream_authorities
set admission_confirmed_at = null
where state = 'admission_pending'
  and admission_confirmed_at is not null;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'chk_sse_stream_authority_open_format'
      and conrelid = 'sse_stream_authorities'::regclass
  ) then
    alter table sse_stream_authorities
      add constraint chk_sse_stream_authority_open_format
      check (open_event_id <> '' and open_payload_bytes <> '' and open_payload_digest ~ '^[0-9a-f]{64}$') not valid;
  end if;
  if not exists (
    select 1 from pg_constraint
    where conname = 'chk_sse_stream_authority_pending_confirmation'
      and conrelid = 'sse_stream_authorities'::regclass
  ) then
    alter table sse_stream_authorities
      add constraint chk_sse_stream_authority_pending_confirmation
      check ((state = 'admission_pending' and admission_confirmed_at is null)
        or (state <> 'admission_pending' and admission_confirmed_at is not null)) not valid;
  end if;
end $$;

alter table sse_stream_authorities validate constraint chk_sse_stream_authority_open_format;
alter table sse_stream_authorities validate constraint chk_sse_stream_authority_pending_confirmation;

create table if not exists sse_authority_leases (
  id text primary key, tenant_id text not null, run_id text not null,
  api_instance_id text not null, connection_id text not null, authorization_epoch bigint not null check (authorization_epoch > 0),
  lease_not_after timestamptz not null, closed_at timestamptz, close_reason text,
  created_at timestamptz not null default clock_timestamp(), updated_at timestamptz not null default clock_timestamp(),
  unique (tenant_id, run_id, api_instance_id, connection_id),
  foreign key (tenant_id, run_id) references sse_stream_authorities(tenant_id, run_id)
);

create index if not exists idx_sse_authority_leases_expiry
  on sse_authority_leases(tenant_id, run_id, authorization_epoch, lease_not_after)
  where closed_at is null;

-- Old producers must be stopped and the explicit retirement command committed.
do $$
declare
  migration_schema text := current_schema();
  legacy_column_count integer;
begin
  select count(*) from pg_attribute
  where attrelid = 'run_events'::regclass and not attisdropped
    and attname = any(array[
      'stream_publication_state', 'stream_publication_attempts',
      'stream_publication_redis_id', 'stream_publication_last_error',
      'stream_publication_claim_token', 'stream_publication_claim_expires_at',
      'stream_publication_next_attempt_at'
    ])
  into legacy_column_count;
  if legacy_column_count not in (0, 7) then
    raise exception 'legacy_sse_retirement_required';
  end if;
  if legacy_column_count > 0
     or to_regclass(format('%I.sse_terminal_publication_intents', migration_schema)) is not null
     or to_regclass(format('%I.sse_stream_rebuilds', migration_schema)) is not null
     or to_regclass(format('%I.sse_stream_rebuild_items', migration_schema)) is not null then
    if exists (select 1 from sse_stream_authorities where revocation_state <> 'effective')
       or exists (select 1 from sse_authority_leases where closed_at is null) then
      raise exception 'legacy_sse_retirement_required';
    end if;
  end if;
  if exists (
    select 1 from run_events
    where (payload_json -> '__stream_v4') ?| array['publication_state','publication_attempts','suppression_reason']
  ) then
    raise exception 'legacy_sse_retirement_required';
  end if;
  if legacy_column_count > 0 then
    if exists (
      select 1 from run_events where stream_publication_state is not null
        or stream_publication_attempts is not null
        or stream_publication_redis_id is not null
        or stream_publication_last_error is not null
        or stream_publication_claim_token is not null
        or stream_publication_claim_expires_at is not null
        or stream_publication_next_attempt_at is not null
    ) then
      raise exception 'legacy_sse_retirement_required';
    end if;
  end if;
  if to_regclass(format('%I.sse_terminal_publication_intents', migration_schema)) is not null then
    if exists (select 1 from sse_terminal_publication_intents where state = 'pending') then
      raise exception 'legacy_sse_retirement_required';
    end if;
  end if;
  if to_regclass(format('%I.sse_stream_rebuilds', migration_schema)) is not null then
    if exists (select 1 from sse_stream_rebuilds where state in ('building','ready')) then
      raise exception 'legacy_sse_retirement_required';
    end if;
  end if;
  execute format('drop table if exists %1$I.sse_stream_rebuild_items, %1$I.sse_stream_rebuilds, %1$I.sse_terminal_publication_intents', migration_schema);
  execute format('drop index if exists %I.idx_sse_stream_authority_pending', migration_schema);
end $$;

alter table run_events
  drop column if exists stream_publication_state,
  drop column if exists stream_publication_attempts,
  drop column if exists stream_publication_next_attempt_at,
  drop column if exists stream_publication_redis_id,
  drop column if exists stream_publication_last_error,
  drop column if exists stream_publication_claim_token,
  drop column if exists stream_publication_claim_expires_at;

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
  provider_renewed_at timestamptz,
  provider_expires_at timestamptz,
  executor_status text not null default 'pending',
  executor_heartbeat_at timestamptz,
  executor_terminal_json jsonb,
  executor_terminal_received_at timestamptz,
  executor_reconciliation_context_json jsonb,
  executor_reconciliation_status text not null default 'waiting_terminal',
  executor_reconciliation_claim_token text,
  executor_reconciliation_claimed_at timestamptz,
  executor_reconciliation_attempt_count integer not null default 0,
  executor_terminal_reconciliation_attempt_count integer not null default 0,
  executor_reconciliation_error text not null default '',
  executor_reconciled_at timestamptz,
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
alter table sandbox_leases add column if not exists provider_renewed_at timestamptz;
alter table sandbox_leases add column if not exists provider_expires_at timestamptz;
alter table sandbox_leases add column if not exists executor_status text not null default 'pending';
alter table sandbox_leases add column if not exists executor_heartbeat_at timestamptz;
alter table sandbox_leases add column if not exists executor_terminal_json jsonb;
alter table sandbox_leases add column if not exists executor_terminal_received_at timestamptz;
alter table sandbox_leases add column if not exists executor_reconciliation_context_json jsonb;
alter table sandbox_leases add column if not exists executor_reconciliation_status text not null default 'waiting_terminal';
alter table sandbox_leases add column if not exists executor_reconciliation_claim_token text;
alter table sandbox_leases add column if not exists executor_reconciliation_claimed_at timestamptz;
alter table sandbox_leases add column if not exists executor_reconciliation_attempt_count integer not null default 0;
alter table sandbox_leases add column if not exists executor_terminal_reconciliation_attempt_count integer not null default 0;
alter table sandbox_leases add column if not exists executor_reconciliation_error text not null default '';
alter table sandbox_leases add column if not exists executor_reconciled_at timestamptz;
alter table sandbox_leases drop constraint if exists chk_sandbox_leases_executor_status;
alter table sandbox_leases add constraint chk_sandbox_leases_executor_status
  check (executor_status in ('pending', 'accepted', 'running', 'completed', 'failed', 'cancelled'));
alter table sandbox_leases drop constraint if exists chk_sandbox_leases_executor_reconciliation_status;
alter table sandbox_leases add constraint chk_sandbox_leases_executor_reconciliation_status
  check (executor_reconciliation_status in ('waiting_terminal', 'pending', 'claimed', 'retry', 'finalized', 'failed'));
create index if not exists idx_sandbox_leases_attempt
  on sandbox_leases(tenant_id, run_id, attempt_id, status);

-- Rollback for the additive OpenSandbox renewal observations (after callers retire):
-- alter table sandbox_leases drop column if exists provider_renewed_at;
-- alter table sandbox_leases drop column if exists provider_expires_at;

-- Rollback for the additive async execution columns:
-- alter table sandbox_leases drop constraint if exists chk_sandbox_leases_executor_status;
-- alter table sandbox_leases drop column if exists executor_terminal_received_at;
-- alter table sandbox_leases drop column if exists executor_terminal_json;
-- alter table sandbox_leases drop column if exists executor_heartbeat_at;
-- alter table sandbox_leases drop column if exists executor_status;
-- drop index if exists idx_sandbox_leases_attempt;
-- alter table sandbox_leases drop column if exists attempt_id;
-- alter table sandbox_leases drop column if exists runtime_handle_verified_at;
-- alter table sandbox_leases drop column if exists runtime_workspace_container_path;
-- alter table sandbox_leases drop column if exists runtime_executor_url;
-- alter table sandbox_leases drop column if exists runtime_container_name;
-- alter table sandbox_leases drop column if exists runtime_container_id;

create table if not exists file_upload_sessions (
  id text primary key,
  tenant_id text not null references tenants(id),
  workspace_id text not null references workspaces(id),
  user_id text not null references users(id),
  session_id text,
  file_id text not null unique,
  original_name text not null,
  content_type text not null,
  expected_size_bytes bigint not null check (expected_size_bytes > 0),
  part_size_bytes bigint not null check (part_size_bytes > 0),
  part_count integer not null check (part_count > 0),
  storage_key text not null unique,
  upload_id text not null unique,
  state text not null default 'pending',
  expires_at timestamptz not null,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  check (state in ('pending', 'completing', 'completed', 'aborted', 'expired'))
);

create index if not exists idx_file_upload_sessions_scope
  on file_upload_sessions(tenant_id, workspace_id, user_id, state, expires_at);

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
  lifecycle_state text not null default 'active',
  delete_requested_at timestamptz,
  deleted_at timestamptz,
  created_at timestamptz not null default now()
);

alter table files add column if not exists lifecycle_state text not null default 'active';
alter table files add column if not exists delete_requested_at timestamptz;
alter table files add column if not exists deleted_at timestamptz;
alter table files drop constraint if exists chk_files_lifecycle_state;
alter table files add constraint chk_files_lifecycle_state
  check (lifecycle_state in ('active', 'delete_pending', 'deleted'));

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
  lifecycle_state text not null default 'active',
  delete_requested_at timestamptz,
  deleted_at timestamptz,
  created_at timestamptz not null default now()
);

alter table artifacts add column if not exists trace_id text not null default '';
alter table artifacts add column if not exists manifest_version text not null default 'ai-platform.artifact-manifest.v1';
alter table artifacts add column if not exists retention_policy text not null default 'standard_90d';
alter table artifacts add column if not exists expires_at timestamptz;
alter table artifacts add column if not exists lifecycle_state text not null default 'active';
alter table artifacts add column if not exists delete_requested_at timestamptz;
alter table artifacts add column if not exists deleted_at timestamptz;
alter table artifacts alter column run_id drop not null;
alter table artifacts drop constraint if exists chk_artifacts_run_owner;
update artifacts
set manifest_json = manifest_json || jsonb_build_object(
      'retention_artifact_cleanup', true,
      'deletion_owner_run_id', run_id
    ),
    run_id = null
where run_id is not null and lifecycle_state in ('delete_pending', 'deleted');
alter table artifacts drop constraint if exists chk_artifacts_lifecycle_state;
alter table artifacts add constraint chk_artifacts_lifecycle_state
  check (lifecycle_state in ('active', 'delete_pending', 'deleted'));
alter table artifacts add constraint chk_artifacts_run_owner
  check (
    (run_id is not null and lifecycle_state = 'active')
    or (
      run_id is null
      and lifecycle_state = 'delete_pending'
      and manifest_json @> '{"provisional_reconciliation_cleanup":true}'::jsonb
      and nullif(manifest_json ->> 'expected_run_id', '') is not null
    )
    or (
      run_id is null
      and lifecycle_state in ('delete_pending', 'deleted')
      and manifest_json @> '{"retention_artifact_cleanup":true}'::jsonb
      and nullif(manifest_json ->> 'deletion_owner_run_id', '') is not null
    )
  );

create table if not exists object_deletion_outbox (
  id text primary key,
  tenant_id text not null references tenants(id),
  target_type text not null default 'artifact',
  artifact_id text references artifacts(id),
  file_id text references files(id),
  storage_key text not null,
  state text not null default 'pending',
  attempts integer not null default 0,
  lease_generation bigint not null default 0,
  available_at timestamptz not null default now(),
  leased_at timestamptz,
  receipt_at timestamptz,
  dead_letter_at timestamptz,
  reconcile_required boolean not null default false,
  last_error_code text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, artifact_id)
);
alter table object_deletion_outbox add column if not exists target_type text not null default 'artifact';
alter table object_deletion_outbox add column if not exists file_id text references files(id);
alter table object_deletion_outbox add column if not exists lease_generation bigint not null default 0;
alter table object_deletion_outbox alter column artifact_id drop not null;
alter table object_deletion_outbox drop constraint if exists object_deletion_outbox_file_id_fkey;
alter table object_deletion_outbox add constraint object_deletion_outbox_file_id_fkey
  foreign key (file_id) references files(id);
alter table object_deletion_outbox add column if not exists dead_letter_at timestamptz;
alter table object_deletion_outbox add column if not exists reconcile_required boolean not null default false;
alter table object_deletion_outbox drop constraint if exists object_deletion_outbox_state_check;
alter table object_deletion_outbox drop constraint if exists chk_object_deletion_outbox_state;
alter table object_deletion_outbox drop constraint if exists chk_object_deletion_outbox_target_state;
update object_deletion_outbox
set state = case state
  when 'pending' then 'file_pending'
  when 'processing' then 'file_processing'
  when 'failed' then 'file_failed'
  when 'dead_letter' then 'file_dead_letter'
  when 'deleted' then 'file_deleted'
  else state
end
where target_type = 'file'
  and state in ('pending', 'processing', 'failed', 'dead_letter', 'deleted');
alter table object_deletion_outbox add constraint chk_object_deletion_outbox_state
  check (state in (
    'pending', 'processing', 'failed', 'dead_letter', 'deleted',
    'file_pending', 'file_processing', 'file_failed', 'file_dead_letter', 'file_deleted'
  ));
alter table object_deletion_outbox drop constraint if exists chk_object_deletion_outbox_target;
alter table object_deletion_outbox add constraint chk_object_deletion_outbox_target
  check (
    (target_type = 'artifact' and artifact_id is not null and file_id is null)
    or (target_type = 'file' and artifact_id is null and file_id is not null)
  );
alter table object_deletion_outbox add constraint chk_object_deletion_outbox_target_state
  check (
    (
      target_type = 'artifact'
      and state in ('pending', 'processing', 'failed', 'dead_letter', 'deleted')
    )
    or (
      target_type = 'file'
      and state in (
        'file_pending', 'file_processing', 'file_failed', 'file_dead_letter', 'file_deleted'
      )
    )
  );

create table if not exists platform_secret_records (
  id text primary key,
  tenant_id text not null references tenants(id),
  purpose text not null,
  ciphertext bytea not null,
  key_version text not null,
  fingerprint text not null,
  status text not null default 'active',
  created_by text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint chk_platform_secret_purpose
    check (purpose in ('knowledge_provider')),
  constraint chk_platform_secret_fingerprint
    check (fingerprint ~ '^[0-9a-f]{16}$'),
  constraint chk_platform_secret_status
    check (status in ('active', 'revoked')),
  unique (tenant_id, id)
);

create table if not exists knowledge_connections (
  id text primary key,
  tenant_id text not null references tenants(id),
  name text not null,
  provider_key text not null,
  status text not null default 'draft',
  active_revision_id text,
  active_catalog_sync_id text,
  candidate_revision_id text,
  lifecycle_epoch bigint not null default 0,
  last_authenticated_check_at timestamptz,
  last_complete_sync_at timestamptz,
  safe_failure_code text,
  create_operation_id text not null,
  create_request_hash text not null,
  created_by text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint chk_knowledge_connection_name
    check (length(name) between 1 and 120 and name = btrim(name)),
  constraint chk_knowledge_connection_provider
    check (provider_key in ('ragflow')),
  constraint chk_knowledge_connection_status
    check (status in ('draft', 'checking', 'cataloging', 'active', 'unavailable', 'disabled')),
  constraint chk_knowledge_connection_epoch check (lifecycle_epoch >= 0),
  constraint chk_knowledge_connection_create_hash
    check (create_request_hash ~ '^[0-9a-f]{64}$'),
  constraint chk_knowledge_connection_active_pair check (
    (active_revision_id is null) = (active_catalog_sync_id is null)
  ),
  unique (tenant_id, id),
  unique (tenant_id, name),
  unique (tenant_id, create_operation_id)
);

create table if not exists knowledge_connection_revisions (
  id text primary key,
  tenant_id text not null references tenants(id),
  connection_id text not null,
  revision bigint not null,
  provider_key text not null,
  base_url text not null,
  secret_ref text not null,
  operation_id text not null,
  transport_policy_json jsonb not null default '{}'::jsonb,
  content_hash text not null,
  checked_at timestamptz,
  check_status text not null default 'pending',
  created_by text not null,
  created_at timestamptz not null default now(),
  constraint fk_knowledge_revision_connection foreign key (tenant_id, connection_id)
    references knowledge_connections(tenant_id, id),
  constraint fk_knowledge_revision_secret foreign key (tenant_id, secret_ref)
    references platform_secret_records(tenant_id, id),
  constraint chk_knowledge_revision_positive check (revision > 0),
  constraint chk_knowledge_revision_provider check (provider_key in ('ragflow')),
  constraint chk_knowledge_revision_base_url check (length(base_url) between 1 and 2048),
  constraint chk_knowledge_revision_transport check (
    jsonb_typeof(transport_policy_json) = 'object'
    and octet_length(transport_policy_json::text) <= 4096
  ),
  constraint chk_knowledge_revision_hash check (content_hash ~ '^[0-9a-f]{64}$'),
  constraint chk_knowledge_revision_check_status
    check (check_status in ('pending', 'passed', 'failed')),
  unique (tenant_id, connection_id, revision),
  unique (tenant_id, connection_id, operation_id),
  unique (tenant_id, connection_id, id),
  unique (tenant_id, id)
);

create table if not exists knowledge_catalog_syncs (
  id text primary key,
  tenant_id text not null references tenants(id),
  connection_id text not null,
  connection_revision_id text not null,
  operation_id text not null,
  requested_by text not null,
  retry_of_sync_id text,
  purpose text not null,
  status text not null default 'requested',
  lease_owner text,
  lease_generation bigint not null default 0,
  lease_expires_at timestamptz,
  provider_cursor text,
  observed_count integer not null default 0,
  page_count integer not null default 0,
  candidate_digest text,
  safe_failure_code text,
  requested_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz,
  constraint fk_knowledge_sync_connection foreign key (tenant_id, connection_id)
    references knowledge_connections(tenant_id, id),
  constraint fk_knowledge_sync_revision foreign key (
    tenant_id, connection_id, connection_revision_id
  ) references knowledge_connection_revisions(tenant_id, connection_id, id),
  constraint chk_knowledge_sync_purpose
    check (purpose in ('manual_active_refresh', 'candidate_activation')),
  constraint chk_knowledge_sync_status check (status in (
    'requested', 'enumerating', 'committing', 'succeeded', 'failed',
    'cancelled', 'reconcile_required'
  )),
  constraint chk_knowledge_sync_generation check (lease_generation >= 0),
  constraint chk_knowledge_sync_counts check (observed_count >= 0 and page_count >= 0),
  constraint chk_knowledge_sync_digest check (
    candidate_digest is null or candidate_digest ~ '^[0-9a-f]{64}$'
  ),
  unique (tenant_id, connection_id, operation_id),
  unique (tenant_id, connection_id, id),
  unique (tenant_id, id)
);

create table if not exists knowledge_connection_check_receipts (
  tenant_id text not null,
  connection_id text not null,
  connection_revision_id text not null,
  operation_id text not null,
  status text not null default 'checking',
  lease_owner text,
  lease_generation bigint not null default 1,
  lease_expires_at timestamptz,
  safe_failure_code text,
  requested_by text not null,
  requested_at timestamptz not null default now(),
  completed_at timestamptz,
  primary key (tenant_id, connection_id, operation_id),
  constraint fk_knowledge_check_connection foreign key (tenant_id, connection_id)
    references knowledge_connections(tenant_id, id),
  constraint fk_knowledge_check_revision foreign key (
    tenant_id, connection_id, connection_revision_id
  ) references knowledge_connection_revisions(tenant_id, connection_id, id),
  constraint chk_knowledge_check_status
    check (status in ('checking', 'passed', 'failed', 'reconcile_required')),
  constraint chk_knowledge_check_generation check (lease_generation > 0)
);

create table if not exists knowledge_catalog_sync_observations (
  tenant_id text not null,
  sync_id text not null,
  lease_generation bigint not null,
  provider_resource_id text not null,
  provider_name text not null,
  provider_metadata_json jsonb not null default '{}'::jsonb,
  record_digest text not null,
  primary key (tenant_id, sync_id, lease_generation, provider_resource_id),
  constraint fk_knowledge_observation_sync foreign key (tenant_id, sync_id)
    references knowledge_catalog_syncs(tenant_id, id) on delete cascade,
  constraint chk_knowledge_observation_generation check (lease_generation > 0),
  constraint chk_knowledge_observation_identity check (
    length(provider_resource_id) between 1 and 512
  ),
  constraint chk_knowledge_observation_name check (length(provider_name) between 1 and 240),
  constraint chk_knowledge_observation_metadata check (
    jsonb_typeof(provider_metadata_json) = 'object'
    and octet_length(provider_metadata_json::text) <= 8192
  ),
  constraint chk_knowledge_observation_digest check (record_digest ~ '^[0-9a-f]{64}$')
);

create table if not exists knowledge_sources (
  id text primary key,
  tenant_id text not null references tenants(id),
  connection_id text not null,
  provider_resource_id text not null,
  provider_name text not null,
  display_name text,
  description text,
  status text not null default 'pending_review',
  authorization_version bigint not null default 1,
  provider_metadata_json jsonb not null default '{}'::jsonb,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  last_complete_sync_id text,
  last_seen_connection_revision_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint fk_knowledge_source_connection foreign key (tenant_id, connection_id)
    references knowledge_connections(tenant_id, id),
  constraint fk_knowledge_source_sync foreign key (
    tenant_id, connection_id, last_complete_sync_id
  ) references knowledge_catalog_syncs(tenant_id, connection_id, id),
  constraint fk_knowledge_source_revision foreign key (
    tenant_id, connection_id, last_seen_connection_revision_id
  ) references knowledge_connection_revisions(tenant_id, connection_id, id),
  constraint chk_knowledge_source_identity check (
    length(provider_resource_id) between 1 and 512
    and provider_resource_id = btrim(provider_resource_id)
  ),
  constraint chk_knowledge_source_name check (
    length(provider_name) between 1 and 240 and provider_name = btrim(provider_name)
  ),
  constraint chk_knowledge_source_display_name check (
    display_name is null or length(display_name) between 1 and 240
  ),
  constraint chk_knowledge_source_description check (
    description is null or length(description) <= 1000
  ),
  constraint chk_knowledge_source_status
    check (status in ('pending_review', 'active', 'disabled', 'missing')),
  constraint chk_knowledge_source_authorization_version check (authorization_version > 0),
  constraint chk_knowledge_source_metadata check (
    jsonb_typeof(provider_metadata_json) = 'object'
    and octet_length(provider_metadata_json::text) <= 8192
  ),
  unique (tenant_id, id),
  unique (tenant_id, connection_id, provider_resource_id)
);

create table if not exists knowledge_source_acl_versions (
  tenant_id text not null references tenants(id),
  source_id text not null,
  authorization_version bigint not null,
  visibility text not null,
  operation_id text not null,
  content_hash text not null,
  created_by text not null,
  created_at timestamptz not null default now(),
  primary key (tenant_id, source_id, authorization_version),
  constraint fk_knowledge_acl_source foreign key (tenant_id, source_id)
    references knowledge_sources(tenant_id, id),
  constraint chk_knowledge_acl_version check (authorization_version > 0),
  constraint chk_knowledge_acl_visibility check (visibility in ('enterprise', 'restricted')),
  constraint chk_knowledge_acl_hash check (content_hash ~ '^[0-9a-f]{64}$'),
  unique (tenant_id, source_id, operation_id)
);

create table if not exists knowledge_source_update_receipts (
  tenant_id text not null,
  source_id text not null,
  operation_id text not null,
  request_hash text not null,
  requested_by text not null,
  created_at timestamptz not null default now(),
  primary key (tenant_id, source_id, operation_id),
  constraint fk_knowledge_source_update_receipt foreign key (tenant_id, source_id)
    references knowledge_sources(tenant_id, id),
  constraint chk_knowledge_source_update_hash check (request_hash ~ '^[0-9a-f]{64}$')
);

create table if not exists knowledge_source_acl_departments (
  tenant_id text not null,
  source_id text not null,
  authorization_version bigint not null,
  department_id text not null,
  primary key (tenant_id, source_id, authorization_version, department_id),
  constraint chk_knowledge_acl_department_id check (
    department_id = btrim(department_id) and department_id <> ''
    and length(department_id) <= 160
  ),
  constraint fk_knowledge_acl_department_version foreign key (
    tenant_id, source_id, authorization_version
  ) references knowledge_source_acl_versions(tenant_id, source_id, authorization_version)
);

create table if not exists knowledge_source_acl_roles (
  tenant_id text not null,
  source_id text not null,
  authorization_version bigint not null,
  role_id text not null,
  primary key (tenant_id, source_id, authorization_version, role_id),
  constraint chk_knowledge_acl_role_id check (
    role_id = btrim(role_id) and role_id <> '' and length(role_id) <= 160
  ),
  constraint fk_knowledge_acl_role_version foreign key (
    tenant_id, source_id, authorization_version
  ) references knowledge_source_acl_versions(tenant_id, source_id, authorization_version)
);

create table if not exists knowledge_source_acl_users (
  tenant_id text not null,
  source_id text not null,
  authorization_version bigint not null,
  user_id text not null,
  primary key (tenant_id, source_id, authorization_version, user_id),
  constraint chk_knowledge_acl_user_id check (
    user_id = btrim(user_id) and user_id <> '' and length(user_id) <= 160
  ),
  constraint fk_knowledge_acl_user_version foreign key (
    tenant_id, source_id, authorization_version
  ) references knowledge_source_acl_versions(tenant_id, source_id, authorization_version)
);

create table if not exists knowledge_connection_lifecycle_receipts (
  tenant_id text not null,
  connection_id text not null,
  lifecycle_epoch bigint not null,
  state text not null,
  active_revision_id text,
  active_catalog_sync_id text,
  operation_id text not null,
  requested_by text not null,
  created_at timestamptz not null default now(),
  primary key (tenant_id, connection_id, lifecycle_epoch),
  constraint fk_knowledge_receipt_connection foreign key (tenant_id, connection_id)
    references knowledge_connections(tenant_id, id),
  constraint fk_knowledge_receipt_revision foreign key (
    tenant_id, connection_id, active_revision_id
  ) references knowledge_connection_revisions(tenant_id, connection_id, id),
  constraint fk_knowledge_receipt_sync foreign key (
    tenant_id, connection_id, active_catalog_sync_id
  ) references knowledge_catalog_syncs(tenant_id, connection_id, id),
  constraint chk_knowledge_receipt_epoch check (lifecycle_epoch > 0),
  constraint chk_knowledge_receipt_state check (state in ('active', 'unavailable', 'disabled')),
  constraint chk_knowledge_receipt_active_pair check (
    (state = 'disabled' and active_revision_id is null and active_catalog_sync_id is null)
    or (state <> 'disabled' and active_revision_id is not null and active_catalog_sync_id is not null)
  ),
  unique (tenant_id, connection_id, operation_id)
);

create or replace function ai_platform_guard_knowledge_connection_lifecycle_receipt_immutable()
returns trigger
language plpgsql
as $$
begin
  raise exception 'knowledge_connection_lifecycle_receipt_immutable' using errcode = '23514';
end $$;

drop trigger if exists trg_knowledge_connection_lifecycle_receipt_immutable
  on knowledge_connection_lifecycle_receipts;
create trigger trg_knowledge_connection_lifecycle_receipt_immutable
before update or delete on knowledge_connection_lifecycle_receipts
for each row execute function ai_platform_guard_knowledge_connection_lifecycle_receipt_immutable();

create table if not exists knowledge_retrieval_profiles (
  id text not null,
  revision bigint not null,
  name text not null,
  mode text not null,
  top_k_per_source integer not null,
  candidate_pool_size integer not null,
  score_threshold double precision not null,
  fusion_strategy text not null,
  rrf_constant integer not null,
  final_top_k integer not null,
  per_source_timeout_ms integer not null,
  overall_timeout_ms integer not null,
  cancellation_grace_ms integer not null,
  max_retries_per_source integer not null,
  retry_backoff_base_ms integer not null,
  retry_backoff_cap_ms integer not null,
  retry_jitter_ratio double precision not null,
  max_parallel_sources integer not null,
  max_query_bytes integer not null,
  max_chunk_bytes integer not null,
  max_total_evidence_bytes integer not null,
  status text not null,
  content_hash text not null,
  created_at timestamptz not null default now(),
  primary key (id, revision),
  constraint chk_knowledge_retrieval_profile_identity check (
    id = btrim(id) and length(id) between 1 and 160 and revision > 0
  ),
  constraint chk_knowledge_retrieval_profile_name check (
    name = btrim(name) and length(name) between 1 and 160
  ),
  constraint chk_knowledge_retrieval_profile_mode
    check (mode = 'deterministic'),
  constraint chk_knowledge_retrieval_profile_result_bounds check (
    top_k_per_source between 1 and 20
    and candidate_pool_size between 20 and 4096
    and candidate_pool_size >= top_k_per_source
    and score_threshold >= 0 and score_threshold <= 1
    and fusion_strategy = 'rrf'
    and rrf_constant > 0
    and final_top_k between 1 and 20
  ),
  constraint chk_knowledge_retrieval_profile_time_bounds check (
    per_source_timeout_ms between 100 and 30000
    and overall_timeout_ms between 100 and 60000
    and cancellation_grace_ms between 0 and 2000
  ),
  constraint chk_knowledge_retrieval_profile_retry_bounds check (
    max_retries_per_source between 0 and 3
    and retry_backoff_base_ms between 10 and 1000
    and retry_backoff_cap_ms between 10 and 5000
    and retry_backoff_cap_ms >= retry_backoff_base_ms
    and retry_jitter_ratio >= 0 and retry_jitter_ratio <= 0.5
  ),
  constraint chk_knowledge_retrieval_profile_budget_bounds check (
    max_parallel_sources between 1 and 8
    and max_query_bytes between 1 and 16384
    and max_chunk_bytes between 1 and 16384
    and max_total_evidence_bytes between 1 and 131072
  ),
  constraint chk_knowledge_retrieval_profile_status
    check (status in ('active', 'disabled')),
  constraint chk_knowledge_retrieval_profile_hash
    check (content_hash ~ '^[0-9a-f]{64}$')
);

insert into knowledge_retrieval_profiles(
  id, revision, name, mode, top_k_per_source, candidate_pool_size,
  score_threshold, fusion_strategy, rrf_constant, final_top_k,
  per_source_timeout_ms, overall_timeout_ms, cancellation_grace_ms,
  max_retries_per_source, retry_backoff_base_ms, retry_backoff_cap_ms,
  retry_jitter_ratio, max_parallel_sources, max_query_bytes, max_chunk_bytes,
  max_total_evidence_bytes, status, content_hash
) values (
  'krp_default', 1, '平台标准检索', 'deterministic', 8, 1024,
  0.45, 'rrf', 60, 8, 8000, 12000, 250, 1, 100, 1000,
  0.2, 4, 16384, 16384, 131072, 'active',
  '7c2cef7efef2a50a3446ed8d56cbae8fb3d998700f828fabbffebf979a8edd73'
)
on conflict (id, revision) do nothing;

do $$
begin
  if not exists (
    select 1
    from knowledge_retrieval_profiles
    where id = 'krp_default'
      and revision = 1
      and name = '平台标准检索'
      and mode = 'deterministic'
      and top_k_per_source = 8
      and candidate_pool_size = 1024
      and score_threshold = 0.45
      and fusion_strategy = 'rrf'
      and rrf_constant = 60
      and final_top_k = 8
      and per_source_timeout_ms = 8000
      and overall_timeout_ms = 12000
      and cancellation_grace_ms = 250
      and max_retries_per_source = 1
      and retry_backoff_base_ms = 100
      and retry_backoff_cap_ms = 1000
      and retry_jitter_ratio = 0.2
      and max_parallel_sources = 4
      and max_query_bytes = 16384
      and max_chunk_bytes = 16384
      and max_total_evidence_bytes = 131072
      and status = 'active'
      and content_hash = '7c2cef7efef2a50a3446ed8d56cbae8fb3d998700f828fabbffebf979a8edd73'
  ) then
    raise exception 'knowledge_retrieval_profile_seed_conflict' using errcode = '23514';
  end if;
end $$;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'run_attempts'::regclass
      and conname = 'uq_run_attempts_tenant_run_id'
  ) then
    alter table run_attempts add constraint uq_run_attempts_tenant_run_id
      unique (tenant_id, run_id, id);
  end if;
end $$;

create table if not exists run_knowledge_snapshots (
  tenant_id text not null,
  run_id text not null,
  agent_id text not null,
  profile_revision bigint not null,
  profile_content_hash text not null,
  retrieval_profile_id text not null,
  retrieval_profile_revision bigint not null,
  sources_json jsonb not null,
  principal_policy_version integer not null,
  authorized_at timestamptz not null,
  content_hash text not null,
  created_at timestamptz not null default now(),
  primary key (tenant_id, run_id),
  constraint fk_run_knowledge_snapshot_run foreign key (tenant_id, run_id)
    references runs(tenant_id, id),
  constraint fk_run_knowledge_snapshot_agent_profile foreign key (
    tenant_id, agent_id, profile_revision
  ) references agent_profile_revisions(tenant_id, agent_id, revision),
  constraint fk_run_knowledge_snapshot_retrieval_profile foreign key (
    retrieval_profile_id, retrieval_profile_revision
  ) references knowledge_retrieval_profiles(id, revision),
  constraint chk_run_knowledge_snapshot_profile_hash
    check (profile_content_hash ~ '^[0-9a-f]{64}$'),
  constraint chk_run_knowledge_snapshot_sources check (
    jsonb_typeof(sources_json) = 'array'
    and jsonb_array_length(sources_json) between 1 and 8
    and octet_length(sources_json::text) <= 16384
  ),
  constraint chk_run_knowledge_snapshot_versions check (
    profile_revision > 0
    and retrieval_profile_revision > 0
    and principal_policy_version > 0
  ),
  constraint chk_run_knowledge_snapshot_hash
    check (content_hash ~ '^[0-9a-f]{64}$'),
  constraint uq_run_knowledge_snapshot_fence
    unique (tenant_id, run_id, content_hash)
);

create table if not exists knowledge_retrieval_attempts (
  id text primary key,
  tenant_id text not null,
  run_id text not null,
  attempt_id text not null,
  generation bigint not null,
  snapshot_hash text not null,
  status text not null default 'requested',
  source_count integer not null,
  result_count integer not null default 0,
  evidence_count integer not null default 0,
  provider_retry_count integer not null default 0,
  duration_ms integer,
  safe_failure_code text,
  cancel_requested_at timestamptz,
  terminal_digest text,
  started_at timestamptz,
  deadline_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint fk_knowledge_retrieval_attempt_run foreign key (tenant_id, run_id)
    references runs(tenant_id, id),
  constraint fk_knowledge_retrieval_attempt_run_attempt foreign key (
    tenant_id, run_id, attempt_id
  ) references run_attempts(tenant_id, run_id, id),
  constraint fk_knowledge_retrieval_attempt_snapshot foreign key (
    tenant_id, run_id, snapshot_hash
  ) references run_knowledge_snapshots(tenant_id, run_id, content_hash),
  constraint chk_knowledge_retrieval_attempt_status check (
    status in ('requested', 'retrieving', 'succeeded', 'no_evidence', 'failed', 'cancelled')
  ),
  constraint chk_knowledge_retrieval_attempt_generation check (generation > 0),
  constraint chk_knowledge_retrieval_attempt_counts check (
    source_count between 1 and 8
    and result_count between 0 and 160
    and evidence_count between 0 and 20
    and evidence_count <= result_count
    and provider_retry_count between 0 and 24
  ),
  constraint chk_knowledge_retrieval_attempt_duration check (
    duration_ms is null or duration_ms between 0 and 120000
  ),
  constraint chk_knowledge_retrieval_attempt_failure_code check (
    (
      status in ('requested', 'retrieving', 'succeeded', 'cancelled')
      and safe_failure_code is null
    )
    or (status = 'no_evidence' and safe_failure_code = 'knowledge_no_evidence')
    or (
      status = 'failed'
      and safe_failure_code in (
        'knowledge_access_denied',
        'knowledge_binding_invalid',
        'knowledge_connection_invalid',
        'knowledge_connection_unavailable',
        'knowledge_profile_invalid',
        'knowledge_provider_rejected',
        'knowledge_provider_transient',
        'knowledge_query_invalid',
        'knowledge_response_invalid',
        'knowledge_retrieval_timeout',
        'knowledge_source_disabled',
        'knowledge_source_missing'
      )
    )
  ),
  constraint chk_knowledge_retrieval_attempt_digest check (
    terminal_digest is null or terminal_digest ~ '^[0-9a-f]{64}$'
  ),
  constraint chk_knowledge_retrieval_attempt_timeline check (
    (status = 'requested' and started_at is null and deadline_at is null and completed_at is null)
    or (
      status = 'retrieving'
      and started_at is not null and deadline_at is not null and deadline_at >= started_at
      and completed_at is null
    )
    or (
      status in ('succeeded', 'no_evidence', 'failed', 'cancelled')
      and started_at is not null and deadline_at is not null and completed_at is not null
      and completed_at >= started_at
      and (
        (status in ('succeeded', 'no_evidence') and completed_at <= deadline_at)
        or (
          status = 'failed'
          and (
            (safe_failure_code = 'knowledge_retrieval_timeout' and completed_at >= deadline_at)
            or (safe_failure_code <> 'knowledge_retrieval_timeout' and completed_at < deadline_at)
          )
        )
        or (
          status = 'cancelled'
          and cancel_requested_at is not null
          and cancel_requested_at <= deadline_at
          and completed_at >= cancel_requested_at
        )
      )
    )
  ),
  constraint chk_knowledge_retrieval_attempt_terminal_receipt check (
    (
      status in ('requested', 'retrieving')
      and terminal_digest is null and duration_ms is null
    )
    or (
      status in ('succeeded', 'no_evidence', 'failed', 'cancelled')
      and terminal_digest is not null and duration_ms is not null
    )
  ),
  constraint chk_knowledge_retrieval_attempt_terminal_counts check (
    (status = 'succeeded' and evidence_count > 0)
    or (status = 'no_evidence' and evidence_count = 0)
    or status in ('requested', 'retrieving', 'failed', 'cancelled')
  ),
  constraint uq_knowledge_retrieval_attempt_fence
    unique (tenant_id, run_id, attempt_id, generation),
  constraint uq_knowledge_retrieval_attempt_identity
    unique (tenant_id, run_id, id)
);

create table if not exists knowledge_evidence (
  tenant_id text not null,
  run_id text not null,
  retrieval_attempt_id text not null,
  evidence_id text not null,
  source_id text not null,
  provider_document_id text not null,
  provider_chunk_id text,
  title text not null,
  content text not null,
  content_sha256 text not null,
  provider_score double precision not null,
  fused_rank integer not null,
  position_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  primary key (tenant_id, run_id, evidence_id),
  constraint fk_knowledge_evidence_run foreign key (tenant_id, run_id)
    references runs(tenant_id, id),
  constraint fk_knowledge_evidence_attempt foreign key (
    tenant_id, run_id, retrieval_attempt_id
  ) references knowledge_retrieval_attempts(tenant_id, run_id, id),
  constraint fk_knowledge_evidence_source foreign key (tenant_id, source_id)
    references knowledge_sources(tenant_id, id),
  constraint chk_knowledge_evidence_identity check (
    length(evidence_id) between 1 and 160
    and length(provider_document_id) between 1 and 512
    and (provider_chunk_id is null or length(provider_chunk_id) between 1 and 512)
  ),
  constraint chk_knowledge_evidence_title check (
    octet_length(convert_to(title, 'UTF8')) <= 512
  ),
  constraint chk_knowledge_evidence_content check (
    octet_length(convert_to(content, 'UTF8')) between 1 and 16384
  ),
  constraint chk_knowledge_evidence_content_hash check (
    content_sha256 ~ '^[0-9a-f]{64}$'
    and content_sha256 = encode(sha256(convert_to(content, 'UTF8')), 'hex')
  ),
  constraint chk_knowledge_evidence_score check (
    provider_score > '-Infinity'::double precision
    and provider_score < 'Infinity'::double precision
  ),
  constraint chk_knowledge_evidence_rank check (fused_rank between 1 and 20),
  constraint chk_knowledge_evidence_position check (
    jsonb_typeof(position_json) in ('object', 'array')
    and octet_length(position_json::text) <= 8192
  ),
  constraint uq_knowledge_evidence_attempt_rank
    unique (tenant_id, run_id, retrieval_attempt_id, fused_rank)
);

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'messages'::regclass
      and conname = 'uq_messages_tenant_run_id'
  ) then
    alter table messages add constraint uq_messages_tenant_run_id
      unique (tenant_id, run_id, id);
  end if;
end $$;

create table if not exists knowledge_citations (
  id text not null,
  tenant_id text not null,
  run_id text not null,
  message_id text not null,
  retrieval_attempt_id text not null,
  retrieval_generation bigint not null,
  evidence_id text not null,
  ordinal integer not null,
  source_id text not null,
  document_ref text not null,
  chunk_ref text,
  title text not null,
  excerpt text not null,
  content_sha256 text not null,
  score double precision not null,
  position_json jsonb not null default '{}'::jsonb,
  operation_id text not null,
  citation_set_hash text not null,
  created_at timestamptz not null default now(),
  primary key (tenant_id, run_id, id),
  constraint fk_knowledge_citation_message foreign key (tenant_id, run_id, message_id)
    references messages(tenant_id, run_id, id),
  constraint fk_knowledge_citation_attempt foreign key (
    tenant_id, run_id, retrieval_attempt_id
  ) references knowledge_retrieval_attempts(tenant_id, run_id, id),
  constraint fk_knowledge_citation_evidence foreign key (tenant_id, run_id, evidence_id)
    references knowledge_evidence(tenant_id, run_id, evidence_id),
  constraint fk_knowledge_citation_source foreign key (tenant_id, source_id)
    references knowledge_sources(tenant_id, id),
  constraint chk_knowledge_citation_identity check (
    length(id) between 1 and 160
    and length(message_id) between 1 and 160
    and length(evidence_id) between 1 and 160
    and length(document_ref) between 1 and 512
    and (chunk_ref is null or length(chunk_ref) between 1 and 512)
    and length(operation_id) between 1 and 256
  ),
  constraint chk_knowledge_citation_generation check (retrieval_generation > 0),
  constraint chk_knowledge_citation_ordinal check (ordinal between 1 and 20),
  constraint chk_knowledge_citation_title check (
    octet_length(convert_to(title, 'UTF8')) <= 512
  ),
  constraint chk_knowledge_citation_excerpt check (
    octet_length(convert_to(excerpt, 'UTF8')) between 1 and 2048
  ),
  constraint chk_knowledge_citation_content_hash check (
    content_sha256 ~ '^[0-9a-f]{64}$'
  ),
  constraint chk_knowledge_citation_set_hash check (
    citation_set_hash ~ '^[0-9a-f]{64}$'
  ),
  constraint chk_knowledge_citation_score check (
    score > '-Infinity'::double precision and score < 'Infinity'::double precision
  ),
  constraint chk_knowledge_citation_position check (
    jsonb_typeof(position_json) in ('object', 'array')
    and octet_length(position_json::text) <= 2048
  ),
  constraint uq_knowledge_citation_message_ordinal
    unique (tenant_id, run_id, message_id, ordinal),
  constraint uq_knowledge_citation_message_evidence
    unique (tenant_id, run_id, message_id, evidence_id)
);

create or replace function ai_platform_guard_knowledge_retrieval_profile_immutable()
returns trigger
language plpgsql
as $$
begin
  if new.id is distinct from old.id
     or new.revision is distinct from old.revision
     or new.name is distinct from old.name
     or new.mode is distinct from old.mode
     or new.top_k_per_source is distinct from old.top_k_per_source
     or new.candidate_pool_size is distinct from old.candidate_pool_size
     or new.score_threshold is distinct from old.score_threshold
     or new.fusion_strategy is distinct from old.fusion_strategy
     or new.rrf_constant is distinct from old.rrf_constant
     or new.final_top_k is distinct from old.final_top_k
     or new.per_source_timeout_ms is distinct from old.per_source_timeout_ms
     or new.overall_timeout_ms is distinct from old.overall_timeout_ms
     or new.cancellation_grace_ms is distinct from old.cancellation_grace_ms
     or new.max_retries_per_source is distinct from old.max_retries_per_source
     or new.retry_backoff_base_ms is distinct from old.retry_backoff_base_ms
     or new.retry_backoff_cap_ms is distinct from old.retry_backoff_cap_ms
     or new.retry_jitter_ratio is distinct from old.retry_jitter_ratio
     or new.max_parallel_sources is distinct from old.max_parallel_sources
     or new.max_query_bytes is distinct from old.max_query_bytes
     or new.max_chunk_bytes is distinct from old.max_chunk_bytes
     or new.max_total_evidence_bytes is distinct from old.max_total_evidence_bytes
     or new.content_hash is distinct from old.content_hash
     or new.created_at is distinct from old.created_at then
    raise exception 'knowledge_retrieval_profile_immutable' using errcode = '23514';
  end if;
  return new;
end $$;

drop trigger if exists trg_knowledge_retrieval_profile_immutable on knowledge_retrieval_profiles;
create trigger trg_knowledge_retrieval_profile_immutable
before update on knowledge_retrieval_profiles
for each row execute function ai_platform_guard_knowledge_retrieval_profile_immutable();

create or replace function ai_platform_guard_run_knowledge_snapshot_immutable()
returns trigger
language plpgsql
as $$
declare
  source_value jsonb;
  source_keys text[];
  source_ids text[] := array[]::text[];
  source_id_value text;
  source_ordinal integer := 0;
begin
  if tg_op = 'UPDATE' and new is distinct from old then
    raise exception 'run_knowledge_snapshot_immutable' using errcode = '23514';
  end if;
  if tg_op = 'INSERT' then
    if jsonb_typeof(new.sources_json) is distinct from 'array'
       or jsonb_array_length(new.sources_json) not between 1 and 8 then
      raise exception 'run_knowledge_snapshot_sources_invalid' using errcode = '23514';
    end if;
    for source_value in
      select items.value
      from jsonb_array_elements(new.sources_json) with ordinality as items(value, ordinal)
      order by items.ordinal
    loop
      if jsonb_typeof(source_value) is distinct from 'object' then
        raise exception 'run_knowledge_snapshot_sources_invalid' using errcode = '23514';
      end if;
      select array_agg(keys.key order by keys.key)
      into source_keys
      from jsonb_object_keys(source_value) as keys(key);
      if source_keys is distinct from array[
        'connection_catalog_sync_id',
        'connection_id',
        'connection_lifecycle_epoch',
        'connection_revision',
        'connection_revision_id',
        'ordinal',
        'provider_resource_id',
        'required',
        'source_authorization_version',
        'source_id'
      ]::text[] then
        raise exception 'run_knowledge_snapshot_sources_invalid' using errcode = '23514';
      end if;
      if jsonb_typeof(source_value -> 'source_id') is distinct from 'string'
         or jsonb_typeof(source_value -> 'connection_id') is distinct from 'string'
         or jsonb_typeof(source_value -> 'connection_revision_id') is distinct from 'string'
         or jsonb_typeof(source_value -> 'connection_catalog_sync_id') is distinct from 'string'
         or jsonb_typeof(source_value -> 'provider_resource_id') is distinct from 'string'
         or jsonb_typeof(source_value -> 'required') is distinct from 'boolean'
         or (source_value -> 'required') is distinct from 'true'::jsonb
         or jsonb_typeof(source_value -> 'source_authorization_version') is distinct from 'number'
         or jsonb_typeof(source_value -> 'connection_revision') is distinct from 'number'
         or jsonb_typeof(source_value -> 'connection_lifecycle_epoch') is distinct from 'number'
         or jsonb_typeof(source_value -> 'ordinal') is distinct from 'number'
         or (source_value ->> 'source_authorization_version') !~ '^[1-9][0-9]{0,17}$'
         or (source_value ->> 'connection_revision') !~ '^[1-9][0-9]{0,17}$'
         or (source_value ->> 'connection_lifecycle_epoch') !~ '^[1-9][0-9]{0,17}$'
         or (source_value ->> 'ordinal') !~ '^[0-7]$'
         or (source_value ->> 'ordinal')::integer <> source_ordinal then
        raise exception 'run_knowledge_snapshot_sources_invalid' using errcode = '23514';
      end if;
      if (source_value ->> 'source_id') is distinct from btrim(source_value ->> 'source_id')
         or octet_length(source_value ->> 'source_id') not between 1 and 160
         or (source_value ->> 'source_id') ~ '[[:cntrl:]]'
         or (source_value ->> 'connection_id') is distinct from btrim(source_value ->> 'connection_id')
         or octet_length(source_value ->> 'connection_id') not between 1 and 160
         or (source_value ->> 'connection_id') ~ '[[:cntrl:]]'
         or (source_value ->> 'connection_revision_id') is distinct from btrim(source_value ->> 'connection_revision_id')
         or octet_length(source_value ->> 'connection_revision_id') not between 1 and 160
         or (source_value ->> 'connection_revision_id') ~ '[[:cntrl:]]'
         or (source_value ->> 'connection_catalog_sync_id') is distinct from btrim(source_value ->> 'connection_catalog_sync_id')
         or octet_length(source_value ->> 'connection_catalog_sync_id') not between 1 and 160
         or (source_value ->> 'connection_catalog_sync_id') ~ '[[:cntrl:]]'
         or (source_value ->> 'provider_resource_id') is distinct from btrim(source_value ->> 'provider_resource_id')
         or octet_length(source_value ->> 'provider_resource_id') not between 1 and 512
         or (source_value ->> 'provider_resource_id') ~ '[[:cntrl:]]' then
        raise exception 'run_knowledge_snapshot_sources_invalid' using errcode = '23514';
      end if;
      source_id_value := source_value ->> 'source_id';
      if source_id_value = any(source_ids) then
        raise exception 'run_knowledge_snapshot_sources_invalid' using errcode = '23514';
      end if;
      source_ids := array_append(source_ids, source_id_value);
      source_ordinal := source_ordinal + 1;
    end loop;
  end if;
  return new;
end $$;

drop trigger if exists trg_run_knowledge_snapshot_immutable on run_knowledge_snapshots;
create trigger trg_run_knowledge_snapshot_immutable
before insert or update on run_knowledge_snapshots
for each row execute function ai_platform_guard_run_knowledge_snapshot_immutable();

create or replace function ai_platform_guard_knowledge_retrieval_attempt_transition()
returns trigger
language plpgsql
as $$
begin
  if tg_op = 'INSERT' then
    return new;
  end if;
  if old.status in ('succeeded', 'no_evidence', 'failed', 'cancelled')
     and new is distinct from old then
    raise exception 'knowledge_retrieval_attempt_terminal_immutable' using errcode = '23514';
  end if;
  if new.id is distinct from old.id
     or new.tenant_id is distinct from old.tenant_id
     or new.run_id is distinct from old.run_id
     or new.attempt_id is distinct from old.attempt_id
     or new.generation is distinct from old.generation
     or new.snapshot_hash is distinct from old.snapshot_hash
     or new.source_count is distinct from old.source_count
     or (
       (new.started_at is distinct from old.started_at
        or new.deadline_at is distinct from old.deadline_at)
       and not (
         old.status = 'requested' and new.status = 'retrieving'
         and old.started_at is null and old.deadline_at is null
         and new.started_at is not null and new.deadline_at is not null
       )
     )
     or new.created_at is distinct from old.created_at then
    raise exception 'knowledge_retrieval_attempt_identity_immutable' using errcode = '23514';
  end if;
  if new.status is distinct from old.status
     and not (
       (old.status = 'requested' and new.status in ('retrieving', 'failed', 'cancelled'))
       or (
         old.status = 'retrieving'
         and new.status in ('succeeded', 'no_evidence', 'failed', 'cancelled')
       )
     ) then
    raise exception 'knowledge_retrieval_attempt_transition_invalid' using errcode = '23514';
  end if;
  return new;
end $$;

drop trigger if exists trg_knowledge_retrieval_attempt_transition on knowledge_retrieval_attempts;
create trigger trg_knowledge_retrieval_attempt_transition
before update on knowledge_retrieval_attempts
for each row execute function ai_platform_guard_knowledge_retrieval_attempt_transition();

create or replace function ai_platform_guard_knowledge_evidence_immutable()
returns trigger
language plpgsql
as $$
begin
  if new is distinct from old then
    raise exception 'knowledge_evidence_immutable' using errcode = '23514';
  end if;
  return new;
end $$;

drop trigger if exists trg_knowledge_evidence_immutable on knowledge_evidence;
create trigger trg_knowledge_evidence_immutable
before update on knowledge_evidence
for each row execute function ai_platform_guard_knowledge_evidence_immutable();

create or replace function ai_platform_guard_knowledge_citation_immutable()
returns trigger
language plpgsql
as $$
begin
  raise exception 'knowledge_citation_immutable' using errcode = '23514';
end $$;

drop trigger if exists trg_knowledge_citation_immutable on knowledge_citations;
create trigger trg_knowledge_citation_immutable
before update or delete on knowledge_citations
for each row execute function ai_platform_guard_knowledge_citation_immutable();

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'knowledge_connections'::regclass
      and conname = 'fk_knowledge_connection_active_revision'
  ) then
    alter table knowledge_connections add constraint fk_knowledge_connection_active_revision
      foreign key (tenant_id, id, active_revision_id)
      references knowledge_connection_revisions(tenant_id, connection_id, id);
  end if;
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'knowledge_connections'::regclass
      and conname = 'fk_knowledge_connection_candidate_revision'
  ) then
    alter table knowledge_connections add constraint fk_knowledge_connection_candidate_revision
      foreign key (tenant_id, id, candidate_revision_id)
      references knowledge_connection_revisions(tenant_id, connection_id, id);
  end if;
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'knowledge_connections'::regclass
      and conname = 'fk_knowledge_connection_active_sync'
  ) then
    alter table knowledge_connections add constraint fk_knowledge_connection_active_sync
      foreign key (tenant_id, id, active_catalog_sync_id)
      references knowledge_catalog_syncs(tenant_id, connection_id, id);
  end if;
end $$;

create index if not exists idx_knowledge_connections_status
  on knowledge_connections(tenant_id, status, name, id);
create index if not exists idx_knowledge_syncs_connection
  on knowledge_catalog_syncs(tenant_id, connection_id, requested_at desc, id desc);
create unique index if not exists uq_knowledge_syncs_one_active_connection
  on knowledge_catalog_syncs(tenant_id, connection_id)
  where status in ('requested', 'enumerating', 'committing');
create index if not exists idx_knowledge_sources_catalog
  on knowledge_sources(tenant_id, connection_id, status, provider_name, id);
create unique index if not exists uq_knowledge_retrieval_attempt_one_open
  on knowledge_retrieval_attempts(tenant_id, run_id)
  where status in ('requested', 'retrieving');
create index if not exists idx_knowledge_retrieval_attempt_due
  on knowledge_retrieval_attempts(deadline_at, tenant_id, run_id, id)
  where status = 'retrieving';
create index if not exists idx_knowledge_evidence_attempt
  on knowledge_evidence(tenant_id, run_id, retrieval_attempt_id, fused_rank);
create index if not exists idx_knowledge_citations_message
  on knowledge_citations(tenant_id, message_id, ordinal);

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
  ('qa-file-reviewer', 'QA Word Review', '0.1.0', 'Review Word documents and return commented Word artifacts.', '["docx"]'::jsonb, '["result_docx", "result_json"]'::jsonb, 'claude-agent-worker'),
  ('minimax-docx', 'Minimax DOCX', '0.1.0', 'Internal Word document composition dependency used by first-party document Skills.', '["docx"]'::jsonb, '["docx"]'::jsonb, 'claude-agent-worker'),
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
  ('skv_seed_qa_file_reviewer_0_1_0', 'qa-file-reviewer', '0.1.0', '0.1.0', 'Schema-seeded baseline for QA Word Review.', '{"kind":"schema-seed"}'::jsonb, '["minimax-docx"]'::jsonb, 'active', 'schema'),
  ('skv_seed_minimax_docx_0_1_0', 'minimax-docx', '0.1.0', '0.1.0', 'Schema-seeded baseline for internal DOCX composition dependency.', '{"kind":"schema-seed"}'::jsonb, '[]'::jsonb, 'active', 'schema'),
  ('skv_seed_ragflow_knowledge_search_0_1_0', 'ragflow-knowledge-search', '0.1.0', '0.1.0', 'Schema-seeded baseline for RAGFlow Knowledge Search.', '{"kind":"schema-seed"}'::jsonb, '[]'::jsonb, 'active', 'schema')
on conflict (skill_id, version) do nothing;

insert into tenant_workbench_skills(tenant_id, skill_id, status, visible_to_user)
values
  ('default', 'qa-file-reviewer', 'active', true),
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
  auth_mode = excluded.auth_mode,
  allowed_tools = excluded.allowed_tools,
  status = excluded.status,
  write_capable = excluded.write_capable,
  risk_level = excluded.risk_level,
  visible_to_user = excluded.visible_to_user
where mcp_tools.endpoint = '';

insert into tool_policies(tenant_id, tool_id, status, write_capable, risk_level, visible_to_user, reason)
values
  ('default', 'ragflow-knowledge-search', 'active', false, 'low', true, 'Schema-seeded read-only RAGFlow tool policy for the default tenant.')
on conflict (tenant_id, tool_id) do nothing;

insert into agents(id, tenant_id, name, agent_type, description, default_skill_id, status)
values
  ('document-review', 'default', '文档审核', 'file', 'Legacy alias for qa-word-review. Hidden from LambChat mode selection.', 'qa-file-reviewer', 'inactive'),
  ('general-agent', 'default', '通用聊天 Agent', 'chat', 'General company chat backed by the governed Harness without a Skill identity.', null, 'active'),
  ('qa-word-review', 'default', '文档审核', 'file', 'Upload Word documents and generate reviewed Word artifacts.', 'qa-file-reviewer', 'active'),
  ('sop-assistant', 'default', 'SOP 助手', 'chat', 'Answer SOP questions with RAGFlow citations.', 'ragflow-knowledge-search', 'active')
on conflict (id) do update set
  tenant_id = excluded.tenant_id,
  name = excluded.name,
  agent_type = excluded.agent_type,
  description = excluded.description,
  default_skill_id = excluded.default_skill_id,
  status = excluded.status;

update agents
set status = 'inactive'
where id in ('translate', 'baoyu-translate')
   or default_skill_id = 'baoyu-translate'
   or exists (
     select 1
     from agent_profiles current_profile
     join agent_profile_revisions current_revision
       on current_revision.tenant_id = current_profile.tenant_id
      and current_revision.agent_id = current_profile.agent_id
      and current_revision.revision = current_profile.published_revision
      and current_revision.content_hash = current_profile.published_hash
      and current_revision.revision_status = 'published'
     where current_profile.tenant_id = agents.tenant_id
       and current_profile.agent_id = agents.id
       and current_profile.lifecycle_status = 'published'
       and current_revision.skill_set @> '[{"skill_id": "baoyu-translate"}]'::jsonb
   );

update tenant_workbench_skills
set status = 'disabled', visible_to_user = false
where skill_id = 'baoyu-translate';

update tenant_capability_distributions
set status = 'disabled', visible_to_user = false
where capability_kind = 'skill' and capability_id = 'baoyu-translate';

update skills
set status = 'inactive'
where id = 'baoyu-translate';

-- Agent Profile hard cut: consolidate the authoring/public contract and retire
-- every physical compatibility field and trigger in one release transaction.
drop trigger if exists trg_agent_profile_legacy_insert_compatibility on agent_profile_revisions;
drop trigger if exists trg_agent_profile_aa_name_only_skill_set_prepare on agent_profile_revisions;
drop trigger if exists trg_agent_profile_zz_name_only_skill_set_finalize on agent_profile_revisions;
drop trigger if exists trg_agent_profile_legacy_insert_reconcile on agent_profile_revisions;
drop function if exists agent_profile_legacy_insert_compatibility();
drop function if exists agent_profile_name_only_skill_set_prepare();
drop function if exists agent_profile_name_only_skill_set_finalize();
drop function if exists agent_profile_legacy_insert_reconcile();

alter table agent_profiles drop constraint if exists fk_agent_profiles_current_publication;
alter table agent_profiles drop constraint if exists chk_agent_profiles_publication;
alter table agent_profiles drop column if exists published_status;
alter table agent_profiles add constraint chk_agent_profiles_publication
check (
  (lifecycle_status = 'published' and published_revision is not null and published_hash is not null)
  or
  (lifecycle_status <> 'published' and published_revision is null and published_hash is null)
);

alter table agent_profile_revisions drop constraint if exists uq_agent_profile_revision_publication;
alter table agent_profile_revisions drop constraint if exists uq_agent_profile_revision_content;
alter table agent_profile_revisions drop constraint if exists agent_profile_revisions_status_check;
alter table agent_profile_revisions drop constraint if exists agent_profile_revisions_avatar_ref_check;
alter table agent_profile_revisions drop constraint if exists chk_agent_profile_revisions_skill_set;
alter table agent_profile_revisions drop constraint if exists chk_agent_profile_revisions_lists;
alter table agent_profile_revisions drop constraint if exists chk_agent_profile_revisions_avatar_seed;
alter table agent_profile_revisions drop constraint if exists agent_profile_revisions_avatar_style_ref_check;
alter table agent_profile_revisions drop constraint if exists agent_profile_revisions_category_check;
alter table agent_profile_revisions
  drop column if exists status,
  drop column if exists welcome_message,
  drop column if exists capability_summary,
  drop column if exists recommended_tasks,
  drop column if exists supported_input_types,
  drop column if exists supported_file_types,
  drop column if exists expected_outputs,
  drop column if exists permissions_and_data_access_notice,
  drop column if exists model_id,
  drop column if exists skill_id,
  drop column if exists skill_version,
  drop column if exists avatar_style_ref,
  drop column if exists avatar_asset_id,
  drop column if exists category,
  drop column if exists market_tag,
  drop column if exists legacy_compatibility_write;

alter table agent_profile_revisions
  alter column skill_set drop default,
  alter column avatar_seed drop default;
alter table agent_profile_revisions
  add constraint agent_profile_revisions_avatar_ref_check
  check (avatar_ref in (
    'builtin:agent', 'builtin:assistant', 'builtin:document', 'builtin:research',
    'builtin:cartoon', 'builtin:emoji', 'builtin:pixel', 'builtin:portrait',
    'builtin:abstract', 'builtin:planet', 'builtin:clay', 'builtin:icon'
  )),
  add constraint chk_agent_profile_revisions_skill_set
  check (jsonb_typeof(skill_set) = 'array' and jsonb_array_length(skill_set) > 0),
  add constraint chk_agent_profile_revisions_lists
  check (
    jsonb_typeof(starter_prompts) = 'array'
    and jsonb_typeof(mcp_tool_ids) = 'array'
    and jsonb_typeof(market_tags) = 'array'
    and jsonb_typeof(allowed_department_ids) = 'array'
    and jsonb_typeof(allowed_roles) = 'array'
    and jsonb_typeof(allowed_user_ids) = 'array'
  ),
  add constraint chk_agent_profile_revisions_avatar_seed
  check (btrim(avatar_seed) <> '');
alter table agent_profile_revisions
  add constraint uq_agent_profile_revision_content
  unique (tenant_id, agent_id, revision, content_hash);
alter table agent_profiles
  add constraint fk_agent_profiles_current_publication
  foreign key (tenant_id, agent_id, published_revision, published_hash)
  references agent_profile_revisions(tenant_id, agent_id, revision, content_hash);

update tenant_workbench_skills
set status = 'disabled', visible_to_user = false
where skill_id = 'general-chat';

update tenant_capability_distributions
set status = 'disabled', visible_to_user = false
where capability_kind = 'skill' and capability_id = 'general-chat';

update skills
set status = 'inactive'
where id = 'general-chat';
