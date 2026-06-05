# Memory Redaction Policy Design

## Goal

Close the remaining P1 Memory / Context hardening gate for configurable redaction policy without weakening the existing fail-closed memory posture.

## Context

The PRD requires Memory UI, tenant/user opt-out, retention cleanup, and configurable redaction policy before the Memory / Context gate can be treated as complete. Current code already stores memory policies, enforces `long_term_memory_enabled = false`, redacts memory content and metadata with a fixed helper, and has worker/admin retention cleanup evidence on 211. The remaining gap is that memory redaction behavior is not policy-controlled.

## Design

Add a policy field named `redaction_mode` with two allowed values:

- `standard`: default mode. Preserve current redaction behavior for secret assignments, bearer tokens, secret-like tokens, sensitive metadata keys, and email addresses.
- `strict`: apply all `standard` redaction and additionally redact raw provider-style credentials and JWT-like tokens that are not attached to a sensitive key.

No `off`, `none`, custom regex, or user-provided pattern mode is allowed in this slice. Unknown request values fail at the API schema boundary and again at the repository boundary. Invalid stored values are treated as `strict` so dirty legacy rows cannot downgrade write-time or projection redaction.

## Data Flow

1. `memory_policies.redaction_mode` defaults to `standard`.
2. User and admin policy update routes accept `redaction_mode` and pass it to `repositories.set_memory_policy`.
3. Effective policy reads and admin policy inventory project the mode as an operational policy name only.
4. Memory record creation reads the effective policy and passes `redaction_mode` into `repositories.create_memory_record`.
5. Repository write-time redaction applies `standard` or `strict` before inserting `memory_records.content` and `metadata_json`.
6. Policy reason audit/projection redaction uses the selected policy mode.
7. Output projections continue to redact legacy rows using the default public projection path; no private payload is exposed.

## Security Boundaries

- Long-term cross-session memory remains fail-closed.
- Strict mode can only add redaction, not remove any existing redaction.
- Invalid stored modes fail safer as `strict`, while default policies explicitly use `standard`.
- Admin projections remain same-tenant and operational; they expose `redaction_mode` but not pattern internals or private payload.
- Ordinary-user projections expose only public policy fields and redacted memory content/metadata.
- Audit payloads include `redaction_mode` as a policy name and continue to redact free-text reasons.

## Tests

Focused tests must cover:

- Schema default, migration column, and check constraint for `redaction_mode`.
- Repository default policy returns `standard`; invalid or blank stored modes project as `strict`.
- Repository upsert/list/select persistence and invalid-mode rejection.
- Strict-mode memory write redacts raw provider/JWT tokens before insert.
- User and admin policy routes accept/project/audit `redaction_mode`, and strict policy reasons redact raw provider/JWT markers.
- Invalid redaction mode is rejected before repository writes.

## 211 Acceptance

After local tests and review, deploy the updated image to 211, set a strict memory policy for smoke data, create a memory record containing a raw strict-mode marker, verify the stored/projection/audit output has no marker leakage, then remove all smoke rows.
