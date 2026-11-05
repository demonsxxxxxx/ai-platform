-- Context-owned fragment. Integration applies this after the native provider tables.
-- No entries/receipts are backfilled and no transcript or workspace is discarded.
alter table provider_session_epochs drop constraint if exists uq_provider_epoch_provider_id;
alter table provider_session_epochs add column if not exists source_epoch_id text;
alter table provider_session_epochs add column if not exists source_final_sequence bigint;
alter table provider_session_epochs add column if not exists coverage_schema_version text not null default 'ai-platform.conversation-authority.v2';
alter table provider_session_epochs add column if not exists recovery_json jsonb;
alter table provider_session_heads add column if not exists recovery_json jsonb;

alter table provider_session_epochs drop constraint if exists fk_provider_epoch_source;
alter table provider_session_epochs add constraint fk_provider_epoch_source foreign key (
  tenant_id, workspace_id, user_id, session_id, agent_id, engine, source_epoch_id
) references provider_session_epochs(tenant_id, workspace_id, user_id, session_id, agent_id, engine, id)
  deferrable initially deferred;
alter table provider_session_epochs drop constraint if exists chk_provider_epoch_source;
alter table provider_session_epochs add constraint chk_provider_epoch_source check (
  (source_epoch_id is null and source_final_sequence is null)
  or (source_epoch_id is not null and source_epoch_id <> id
      and source_final_sequence is not null and source_final_sequence >= 0)
);
alter table provider_session_epochs drop constraint if exists chk_provider_epoch_coverage_version;
alter table provider_session_epochs add constraint chk_provider_epoch_coverage_version check (
  coverage_schema_version in ('ai-platform.conversation-authority.v2', 'ai-platform.conversation-authority.v3')
);
create index if not exists idx_provider_epoch_provider_scope on provider_session_epochs(
  tenant_id, session_id, engine, provider_session_id
);
