-- Execution-owned fragment. Integration applies this after Runs input tables.
-- Started/reported facts are deliberately separate from verified outcomes.
create table if not exists execution_recovery_operations (
  operation_key text primary key,
  tenant_id text not null,
  workspace_id text not null,
  user_id text not null,
  session_id text not null,
  source_run_id text not null,
  source_attempt_id text not null references run_attempts(id) on delete cascade,
  source_generation bigint not null check (source_generation > 0),
  native_tool_use_id text not null,
  tool_identity text not null,
  tool_facts_json jsonb not null check (jsonb_typeof(tool_facts_json) = 'object'),
  public_summary text not null,
  reported_status text check (reported_status in ('completed', 'failed', 'unknown')),
  verified_outcome text not null default 'unknown'
    check (verified_outcome in ('unknown', 'succeeded', 'not_effective')),
  verified_kind text check (verified_kind in ('authoritative_query', 'manual_answer')),
  verified_reference text,
  created_at timestamptz not null default now(),
  verified_at timestamptz,
  foreign key (tenant_id, source_run_id) references runs(tenant_id, id) on delete cascade,
  unique (tenant_id, workspace_id, user_id, session_id, operation_key),
  unique (tenant_id, source_run_id, source_attempt_id, native_tool_use_id),
  check (operation_key ~ '^rop_[0-9a-f]{32}$'),
  check (char_length(native_tool_use_id) between 1 and 512
    and char_length(tool_identity) between 1 and 512
    and char_length(public_summary) between 1 and 1000),
  check ((verified_outcome = 'unknown' and verified_kind is null
           and verified_reference is null and verified_at is null)
      or (verified_outcome <> 'unknown' and verified_kind is not null
           and verified_reference is not null and verified_at is not null))
);

create table if not exists execution_recovery_invocations (
  tenant_id text not null,
  workspace_id text not null,
  user_id text not null,
  session_id text not null,
  run_id text not null,
  attempt_id text not null references run_attempts(id) on delete cascade,
  owner_generation bigint not null check (owner_generation > 0),
  invocation_id text not null,
  binding_kind text not null default 'native_invocation'
    check (binding_kind in ('native_invocation', 'source_operation')),
  tool_identity text not null,
  operation_key text not null,
  frozen_binding_ref text not null,
  public_summary text not null,
  question_id text,
  question_expires_at timestamptz,
  "authorization" text not null default 'pending'
    check ("authorization" in ('pending', 'hold', 'retry', 'current_invocation')),
  answer_input_id uuid,
  started boolean not null default false,
  closed boolean not null default false,
  close_reason text check (close_reason in ('cancelled', 'timeout', 'denied', 'query_failed')),
  created_at timestamptz not null default now(),
  primary key (tenant_id, run_id, attempt_id, invocation_id),
  foreign key (tenant_id, run_id) references runs(tenant_id, id) on delete cascade,
  foreign key (tenant_id, workspace_id, user_id, session_id, operation_key)
    references execution_recovery_operations(tenant_id, workspace_id, user_id, session_id, operation_key)
    on delete cascade,
  foreign key (tenant_id, run_id, attempt_id, question_id)
    references run_input_questions(tenant_id, run_id, attempt_id, question_id) on delete cascade,
  foreign key (tenant_id, run_id, answer_input_id)
    references run_inputs(tenant_id, run_id, input_id) on delete cascade,
  unique (tenant_id, run_id, attempt_id, question_id),
  check (char_length(invocation_id) between 1 and 512
    and char_length(tool_identity) between 1 and 512
    and char_length(frozen_binding_ref) between 1 and 512
    and char_length(public_summary) between 1 and 1000),
  check (closed = (close_reason is not null)),
  check ((question_id is null) = (question_expires_at is null)),
  check (("authorization" = 'pending' and answer_input_id is null)
      or ("authorization" <> 'pending' and answer_input_id is not null))
);

-- Existing bindings retain their original current-call meaning.
alter table execution_recovery_invocations add column if not exists binding_kind text
  not null default 'native_invocation'
  check (binding_kind in ('native_invocation', 'source_operation'));
