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
