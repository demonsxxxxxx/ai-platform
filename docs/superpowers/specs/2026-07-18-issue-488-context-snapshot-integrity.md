# Issue #488: Context snapshot member integrity

## Scope and compatibility

This slice hardens the existing `repositories.create_context_snapshot` seam.
It does not change the schema, public request/response contract, retrieval-time
authorization, redaction, worker behavior, or long-term Memory semantics.
Existing executor callers retain their current keyword arguments; the repository
does not trust caller-supplied workspace, session, user, or agent values when it
persists the snapshot.

## Invariants

- The target run is authorized by tenant and user, then supplies the canonical
  tenant/workspace/user/session/agent scope for the inserted snapshot.
- Each request list is normalized to non-empty text identifiers, de-duplicated
  for validation, and bounded. A duplicate, malformed, or oversized batch is
  rejected before SQL with one generic conflict code.
- Every message, file, artifact, and memory record must be eligible in the
  target run's canonical scope. Prior-run material remains eligible when its
  source run belongs to the same canonical session and agent. Artifacts must be
  unexpired; memories must be active, undeleted, unexpired, and owned by the
  canonical agent.
- Member validation and snapshot insert are one PostgreSQL statement. The
  statement inserts only when every requested member is eligible; it otherwise
  returns no snapshot. This avoids a validate-then-insert TOCTOU seam.
- The manual route retains its existing authorized-run 404 behavior, maps a
  repository member failure to one non-oracular `context_snapshot_material_invalid`
  response, and appends the event only after a successful snapshot insert.

## Failure and verification plan

Invalid member categories deliberately share one error mapping; callers cannot
learn which member, tenant, workspace, run, agent, status, or expiry rule failed.
The surrounding transaction rolls back if the repository raises, so no snapshot
or event is committed for mixed batches. Focused repository tests inspect the
single statement and generic failure behavior; route tests prove the generic
mapping and absence of event creation. Verification is limited to focused tests,
`compileall`, and `git diff --check`.
