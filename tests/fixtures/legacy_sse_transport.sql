-- Retired transport DDL from f63278717ad99f907ac0064d523d364fd22f1e47.
-- Pre-migration acceptance fixture only; never loaded by production.
-- Baseline core SHA256: 3bdfe305dd869bf5d904db4ac97870862c6f2d51b34c37ca8823c76ecbde89b4

create table if not exists sse_stream_rebuilds (
  id text primary key, tenant_id text not null, run_id text not null, attempt_id text not null,
  source_incarnation bigint not null, source_authorization_epoch bigint not null,
  origin_incarnation bigint not null, origin_authorization_epoch bigint not null,
  successor_incarnation bigint not null, successor_authorization_epoch bigint not null,
  source_authority_fingerprint text not null, source_cursor_sequence bigint not null,
  source_through_sequence bigint not null,
  successor_open_event_id text not null, successor_open_bytes text not null,
  successor_open_digest text not null,
  state text not null default 'building', claim_token_digest text not null,
  claim_expires_at timestamptz not null, item_count integer not null,
  built_through_sequence bigint not null default 0,
  receipt_entry_count integer,
  receipt_open_event_id text,
  receipt_terminal_event_id text,
  receipt_end_event_id text,
  receipt_last_redis_id text,
  receipt_last_envelope_bytes text,
  receipt_last_envelope_digest text,
  receipt_digest text,
  failure_code text,
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  constraint chk_sse_stream_rebuild_identity check (
    id <> '' and attempt_id <> '' and successor_open_event_id <> ''
    and successor_open_bytes <> ''
    and source_authority_fingerprint ~ '^[0-9a-f]{64}$'
    and successor_open_digest ~ '^[0-9a-f]{64}$'
    and claim_token_digest ~ '^[0-9a-f]{64}$'
  ),
  constraint chk_sse_stream_rebuild_authority check (
    source_incarnation > 0 and successor_incarnation > source_incarnation
    and source_authorization_epoch > 0
    and successor_authorization_epoch > source_authorization_epoch
  ),
  constraint chk_sse_stream_rebuild_origin check (
    origin_incarnation > 0 and origin_incarnation <= source_incarnation
    and origin_authorization_epoch > 0
    and origin_authorization_epoch <= source_authorization_epoch
  ),
  constraint chk_sse_stream_rebuild_progress check (
    source_cursor_sequence >= source_through_sequence
    and source_through_sequence > 0 and item_count > 0
    and built_through_sequence >= 0
    and built_through_sequence <= source_through_sequence
  ),
  constraint chk_sse_stream_rebuild_state check (
    state in ('building', 'ready', 'cutover', 'aborted', 'expired')
  ),
  constraint chk_sse_stream_rebuild_receipt check (
    (
      receipt_entry_count is null
      and receipt_open_event_id is null
      and receipt_terminal_event_id is null
      and receipt_end_event_id is null
      and receipt_last_redis_id is null
      and receipt_last_envelope_bytes is null
      and receipt_last_envelope_digest is null
      and receipt_digest is null
    )
    or (
      receipt_entry_count is not null
      and receipt_entry_count = item_count + 2
      and receipt_open_event_id is not null and receipt_open_event_id <> ''
      and receipt_terminal_event_id is not null and receipt_terminal_event_id <> ''
      and receipt_end_event_id is not null and receipt_end_event_id <> ''
      and receipt_last_redis_id is not null
      and receipt_last_redis_id ~ '^[0-9]+-[0-9]+$'
      and receipt_last_envelope_bytes is not null
      and receipt_last_envelope_bytes <> ''
      and receipt_last_envelope_digest is not null
      and receipt_last_envelope_digest ~ '^[0-9a-f]{64}$'
      and receipt_digest is not null
      and receipt_digest ~ '^[0-9a-f]{64}$'
    )
  ),
  constraint fk_sse_stream_rebuild_authority
    foreign key (tenant_id, run_id)
    references sse_stream_authorities(tenant_id, run_id)
);

create table if not exists sse_stream_rebuild_items (
  rebuild_id text not null, sequence bigint not null, event_id text not null,
  event_type text not null, canonical_envelope_bytes text not null,
  envelope_digest text not null, redis_id text,
  created_at timestamptz not null default clock_timestamp(),
  primary key (rebuild_id, sequence),
  constraint uq_sse_stream_rebuild_item_event unique (rebuild_id, event_id),
  constraint chk_sse_stream_rebuild_item check (
    sequence > 0 and event_id <> '' and event_type <> ''
    and canonical_envelope_bytes <> ''
    and envelope_digest ~ '^[0-9a-f]{64}$'
  ),
  constraint chk_sse_stream_rebuild_item_redis_id check (
    redis_id is null or redis_id ~ '^[0-9]+-[0-9]+$'
  ),
  constraint fk_sse_stream_rebuild_item_operation
    foreign key (rebuild_id) references sse_stream_rebuilds(id)
);

create table if not exists sse_terminal_publication_intents (
  id text primary key, tenant_id text not null, run_id text not null, attempt_id text not null,
  stream_incarnation bigint not null check (stream_incarnation > 0), schema_version text not null, projection_version text not null,
  terminal_event_id text not null, end_event_id text not null,
  terminal_payload_bytes text not null, terminal_payload_digest text not null, terminal_payload_size integer not null check (terminal_payload_size >= 0),
  end_payload_bytes text not null, end_payload_digest text not null, end_payload_size integer not null check (end_payload_size >= 0),
  emitted_at text not null,
  state text not null default 'pending' check (state in ('pending', 'published', 'superseded')),
  created_at timestamptz not null default clock_timestamp(), published_at timestamptz, updated_at timestamptz not null default clock_timestamp(),
  unique (tenant_id, run_id, attempt_id),
  foreign key (tenant_id, run_id) references runs(tenant_id, id)
);

alter table run_events add column if not exists stream_publication_state text;
alter table run_events add column if not exists stream_publication_attempts integer;
alter table run_events add column if not exists stream_publication_next_attempt_at timestamptz;
alter table run_events add column if not exists stream_publication_redis_id text;
alter table run_events add column if not exists stream_publication_last_error text;
alter table run_events add column if not exists stream_publication_claim_token text;
alter table run_events add column if not exists stream_publication_claim_expires_at timestamptz;

alter table run_events
  add constraint chk_run_events_stream_publication_state
    check (stream_publication_state is null or (stream_publication_state in ('pending', 'published', 'suppressed'))),
  add constraint chk_run_events_stream_publication_claim
    check ((stream_publication_claim_token is null and stream_publication_claim_expires_at is null)
      or (stream_publication_claim_token is not null and stream_publication_claim_expires_at is not null));

create index if not exists idx_run_events_stream_publication_retry on run_events(stream_publication_next_attempt_at asc, created_at asc, id asc) where visible_to_user = true and stream_publication_state = 'pending';
create index if not exists idx_run_events_stream_publication_claim on run_events(tenant_id, run_id, sequence asc, id asc) where visible_to_user = true and stream_publication_state = 'pending' and payload_json ? '__stream_v4';
create index if not exists idx_run_events_v4_due_scope on run_events(tenant_id, run_id, sequence asc) where visible_to_user = true and payload_json ? '__stream_v4' and stream_publication_state = 'pending';
create index idx_sse_stream_authority_pending on sse_stream_authorities(state, updated_at, tenant_id, run_id) where state = 'admission_pending';

-- Restore the baseline migration receipts in this isolated acceptance schema.
update schema_migrations set version = '2026.09.07.2', checksum_sha256 = '3bdfe305dd869bf5d904db4ac97870862c6f2d51b34c37ca8823c76ecbde89b4' where version = (select max(version) from schema_migrations);
update schema_index_migrations set target_version = '2026.08.30.1';
insert into schema_index_migrations(index_name, target_version, checksum_sha256, state, attempts, completed_at) values ('idx_run_events_stream_publication_retry', '2026.08.30.1', '0be4c0dcdbafaa27532eec8f098a9fd5b51013a7f6052bc43b1b59ac9e747633', 'ready', 1, clock_timestamp());
insert into schema_index_migrations(index_name, target_version, checksum_sha256, state, attempts, completed_at) values ('idx_run_events_stream_publication_claim', '2026.08.30.1', '41eb12172119ce5f079cdf7d45b3c8eac22339b1023d65b57b548648a3d014c9', 'ready', 1, clock_timestamp());
insert into schema_index_migrations(index_name, target_version, checksum_sha256, state, attempts, completed_at) values ('idx_run_events_v4_due_scope', '2026.08.30.1', '3e2dfb83566adb2bd19439956a5946f7d0efac13431068d66addd4eeb258166c', 'ready', 1, clock_timestamp());
