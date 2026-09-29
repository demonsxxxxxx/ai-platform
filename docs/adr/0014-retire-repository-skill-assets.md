# ADR: Retire Repository Skill Assets

- Status: Accepted

## Decision

Retire the six repository-provided Skill asset packages under `skills/`:

- `general-chat`
- `qa-file-reviewer`
- `minimax-docx`
- `ragflow-knowledge-search`
- `ctd-32s73-stability-template-fill`
- `reference-fact-extraction`

The API and worker no longer use a repository filesystem Skill source. Runtime
materialization uses the authorized database Skill version and its immutable
snapshot only. The Admin builtin synchronization endpoint, repository asset
copying in release images, and builtin-only runtime configuration are retired.

The dedicated `qa-file-reviewer` controlled executor, its invocation-evidence
producer, fixed document-review routing, and name-derived Word artifact
requirement are also retired. Authorized uploaded Skills use the same SDK
execution path, including uploaded packages that reuse a historical Skill name.
The runtime mount inspection supports the current SDK path only. The multiuser
and memory-context verifiers default to the general Agent and do not provision
or select the retired QA capability. An explicit
executor artifact requirement remains enforceable; Skill availability or list
order does not create one.

Schema-seeded rows, historical Run identities, immutable snapshots, audit rows,
and redaction mappings remain readable for compatibility. Repository-backed
versions are inactive; each tenant's workbench, distribution, Agent Profile, and
stable release policy are disabled when the current release or any selectable
previous rollout track points to a repository version. Tenant surfaces remain
active only when the runtime-admitted current version and any selectable previous
version resolve to runnable uploaded packages. Uploaded package rows and the
global catalog entry needed to manage them are retained.

## Consequences

Runs whose historical source has no complete immutable snapshot fail closed with
`skill_version_not_materializable`; the worker does not reconstruct them from a
repository directory. New deployments do not expose the retired builtins or the
RAGFlow builtin tool policy.

Historical profile metadata remains readable from persisted snapshot JSON.
Current execution accepts SDK-hook Skill invocation receipts only; the retired
controlled-runner evidence source and missing-profile fallback are deleted.
Retired controlled profiles are not translated into SDK execution profiles for replay.
Schema seeds, applied migrations, and public redaction mappings retain their
historical identities.

New requests use the supplied current Agent identity and explicit Skill selection.
Public aliases do not map back to retired Agent names or inject a default Skill;
SOP keywords do not select a fixed knowledge Skill. The unused filesystem pin
builder and builtin version lookup stub are deleted.

Dependency validation uses declared IDs and complete immutable dependency pins.
Root and dependency versions each require current tenant distribution authority;
there are no public/internal Skill name lists, name-derived tool grants, or
name-derived MCP requirements. MCP execution accepts Server-qualified references
only; the RAGFlow bare-ID dispatch and metadata exceptions are deleted.
New tenant distribution backfill uses existing
explicit assignments and does not grant a fixed set of builtin Skill names.
Admin dependency responses no longer expose the obsolete public/internal name
classification fields.


No migration physically deletes historical Skill or Run data. A future data
retention change must be handled as a separate, explicitly authorized migration.

## Rollback and compatibility

Post-migration image rollback is operationally unsupported but is not fenced by
the migration ledger: the predecessor row and checksum remain present, so the
immediate older binary may pass its own schema-version check. Starting that
binary would reintroduce retired filesystem code against database authority that
has already changed, and must not be treated as an automatic rollback path.

Supported recovery keeps the database and immutable historical receipts in place
and rolls forward with a corrected image or migration. Re-enabling a capability
requires a separate authorized migration that selects a complete uploaded
immutable version, restores its tenant distribution, and records the release
decision; repository assets are not reconstructed from historical rows. Before
schema application, the normal release rollback may return to the prior image
because no retirement state has yet been committed.
