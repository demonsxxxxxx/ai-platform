# Capability Distribution V1 Backend Rescue Design

## Status

- Design approved on 2026-07-10.
- Implementation source: `origin/main` at `124a09c39290bb3bf39d9b13bd2fa1bd632a5040`.
- Historical implementation source: selected behavior from `097d839..6a77c37` only.
- Delivery shape: one strictly focused backend PR from an isolated worktree.
- Deployment and 211 verification are explicitly outside this task.

## Goal

Restore the Capability Distribution V1 backend on current `main` without merging
the stale implementation branch. Administrators must be able to distribute
Skills and MCP servers to departments. Ordinary users may only discover and use
capabilities distributed to their department and role. Authorization is checked
both when a run is enqueued and again by the worker immediately before execution.

## Rescue Strategy

The old branch is more than one hundred commits behind current `main` and contains
unrelated sandbox, release, readiness, and working-tree changes. No historical
commit is safe to cherry-pick as a unit.

The rescue therefore uses these rules:

1. Reimplement the final intended behavior against current `main` interfaces.
2. Extract only Capability Distribution V1 file hunks and tests from the final
   `6a77c37` tree as reference.
3. Preserve current queue payload, run-control, Marketplace, MCP policy, and
   frontend contracts unless this design explicitly changes them.
4. Keep schema, resolver, routes, run snapshots, worker checks, and audit changes
   in one atomic PR because partial deployment would create an unsafe authority
   split.

## Scope

The PR includes only:

- `tenant_capability_distributions` schema and idempotent legacy backfill
- repository APIs for distribution reads and writes
- one shared Skill/MCP authorization resolver
- Admin Capability Distribution read, update, and toggle APIs
- Skill catalog, Skill detail, Marketplace, MCP directory, MCP detail, and MCP
  tool visibility cutover
- shared Skill/MCP lifecycle write authorization where those writes change
  tenant-wide availability
- enqueue-time authorization and a persisted run authorization snapshot
- worker-time Skill and MCP reauthorization against current distribution state
- MCP executor registration from the resolver-authorized server and tool set
- audit records for management writes, administrator bypass, and explicit denial
- focused schema, repository, route, enqueue, worker, registration, and audit tests

## Explicit Exclusions

The PR must not include:

- a merge or broad cherry-pick from the historical branch
- sandbox provider, cancel, lease, or execution-tier changes
- Release Authority workflows, deployment scripts, compose, image, or Ruleset work
- B1, B2 readiness, B3, or runtime acceptance changes
- frontend implementation changes
- unrelated repository, observability, payload-redaction, or uploaded-skill catalog
  changes from the old branch
- root working-tree changes or any unknown dirty file
- 211 access, deployment, image build, container replacement, or smoke evidence

## Canonical Authority

`tenant_capability_distributions` is the sole department and role distribution
authority after cutover.

Legacy fields are migration inputs only:

- `tenant_workbench_skills` seeds Skill distribution rows.
- `mcp_servers.department_ids` and `mcp_servers.allowed_roles` seed MCP server
  distribution rows.
- Later reads and writes do not use those fields as fallback authority.

Capability lifecycle remains separate. A capability must pass both its registry
lifecycle checks and the distribution resolver.

## Canonical Semantics

- Supported explicit distribution kinds are `skill` and `mcp_server`.
- An `mcp_tool` inherits the distribution of its parent `mcp_server`.
- `department_ids = []` means tenant-wide distribution.
- A non-empty `department_ids` value is a department allowlist.
- `allowed_roles = []` adds no role restriction.
- A non-empty `allowed_roles` value is a role allowlist.
- Role names are normalized before comparison.
- Ordinary access requires lifecycle, visibility, status, department, and role
  checks to pass.
- A missing distribution row fails closed after cutover.
- AI administrators may discover, use, and manage across departments through an
  explicit, auditable bypass.
- Session-local composer toggles can only remove already-authorized capabilities;
  they cannot add or distribute capabilities.

## Data Model and Backfill

The schema adds `tenant_capability_distributions` with:

- a stable primary key
- `tenant_id`, `capability_kind`, and `capability_id`
- `status` and `visible_to_user`
- `scope_mode`, `department_ids`, and `allowed_roles`
- `metadata_json` and `updated_by`
- timestamps
- a unique constraint on tenant, kind, and capability ID
- checks for supported kinds, statuses, and scope modes

Backfill is explicit and idempotent:

1. Existing tenant Skill availability seeds `skill` rows.
2. Existing MCP server scope fields seed `mcp_server` rows.
3. Re-running initialization must not overwrite administrator-managed
   distribution rows.
4. Every SQL placeholder and binding is covered by migration tests.
5. After backfill, missing rows remain unauthorized rather than falling back to
   a legacy source.

## Resolver

One pure resolver accepts:

- tenant ID
- department ID
- normalized roles
- AI administrator status
- capability kind and ID
- registry lifecycle state
- distribution state, inherited from the parent for an MCP tool
- intent: `discover`, `use`, or `manage`

It returns an explicit decision containing authorization, visibility, bypass,
reason, and the resolved department and role scope. Routes, enqueue logic,
worker checks, and MCP registration consume the same decision vocabulary.

Failure behavior is fail-closed:

- hidden or unauthorized list entries are omitted
- unauthorized ordinary-user detail and tool discovery return `404`
- explicit unauthorized execution returns `403 capability_not_authorized`
- management requests require AI administrator authority
- denials do not reveal cross-department capability details to ordinary users

## Read and Write Cutover

### Skills and Marketplace

Public Skill and Marketplace projections include only resolver-authorized Skills.
Known names do not bypass authorization for detail, files, or execution.

Shared lifecycle operations that change tenant-visible capability state require
AI administrator authority. Existing ordinary-user Marketplace install or update
flows that do not perform shared distribution management remain available; the
rescue must not broaden the historical patch into an unrelated Marketplace lock.

### MCP

MCP list, detail, and tool discovery all resolve the same parent server
distribution. Lifecycle and existing tool risk/write policy remain additional
gates, not replacements for distribution authorization.

MCP registration for execution starts from the resolver-authorized server set.
An unauthorized server or inherited tool must not enter staged configuration,
executor payloads, or the registered Claude tool set even if its raw name is
known.

### Admin API

The backend adds AI-admin-only endpoints to:

- list distributions
- read one Skill or MCP server distribution
- replace department, role, visibility, and status settings
- toggle distribution status

Writes validate capability existence and kind before persistence and emit an
audit record containing actor context and target scope.

## Run Authorization Lifecycle

Queue payload schemas remain unchanged. Authorization context is persisted in
the database run record, not added as caller-controlled Redis payload data.

At enqueue time:

1. Resolve requested Skills and MCP servers against current distribution.
2. Reject an unauthorized reference before creating or enqueueing the run.
3. Persist a normalized snapshot containing department, roles, auth source, and
   authorized capability references.

At worker time:

1. Lock and load the persisted run.
2. Restore authorization context from the locked run record.
3. Re-fetch current Skill and MCP distributions.
4. Reject execution if a capability was disabled, hidden, or redistributed
   after enqueue.
5. Build MCP registration only after successful reauthorization.

Child runs inherit the parent authorization snapshot fields required to preserve
the same tenant, department, role, and authentication context. They do not rely
on mutable request payload fields for authorization.

## Audit

Distribution writes, administrator bypass, and explicit execution denials use
the existing audit repository seam and dotted action names. Relevant metadata
includes:

- capability kind and ID
- intent and decision reason
- actor department and normalized roles
- target department and role scopes
- scope mode
- administrator bypass flag
- run ID when the decision belongs to enqueue or worker execution

Audit writes must not include secrets, raw credentials, private executor payloads,
or real environment values.

## Test Strategy

The focused gate includes:

- schema creation, constraints, SQL bindings, and idempotent backfill
- same-department allow
- cross-department deny
- role allow and role deny with normalized role comparison
- disabled and hidden capability deny
- missing distribution fail-closed
- administrator management and audited bypass
- ordinary-user management deny
- Skill and Marketplace list/detail cutover
- MCP list/detail/tools inheritance cutover
- enqueue rejection before run creation
- worker rejection when authorization changes after enqueue
- worker Skill reauthorization and worker MCP reauthorization
- child-run authorization snapshot inheritance
- MCP registered tool set contains only resolver-authorized servers and tools
- existing MCP tool risk/write policy remains enforced after distribution allow

Verification before PR includes:

- `python -m compileall -q app tools scripts`
- changed-scope pytest with workspace-local `--basetemp .pytest-tmp`
- relevant route and worker integration slices
- `git diff --check`
- independent sub-agent review, with Critical and Important findings fixed and
  re-reviewed
- required GitHub checks on the final PR head

Routine full-repository pytest is not part of this task.

## Baseline Evidence

Before implementation on `124a09c`:

- compile check passed
- the selected backend baseline produced `623 passed, 3 skipped, 1 failed`
- the sole failure was
  `test_cancel_run_ignores_user_controlled_sandbox_container_payload`
- that sandbox cancel test also failed when run alone and is therefore a
  pre-existing current-main failure outside this PR scope

The rescue will not modify sandbox source to hide that baseline. Capability
Distribution verification will report its own focused results separately.

## Acceptance Boundary

This task may claim backend implementation, review, CI, and PR completion only
after fresh evidence exists for the final PR head.

It must not claim:

- 211 verified
- deployed
- browser accepted
- B1, B2, or B3 runtime accepted
- department rollout complete in a running environment

Those claims remain owned by the Release Authority deployment chain.
