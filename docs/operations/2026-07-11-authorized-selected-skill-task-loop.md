# Authorized Selected-Skill Task Loop Backend Source Slice

Issue: #385

Authoritative base: `7ca8ae14405f52c327ef063f336ae9547b60bb05`

Status: `local partial`

- [x] Phase 0 - clean linked worktree, fetch/readback, authoritative docs and current execution paths inspected.
- [x] Phase 1 - public selected-Skill contract and implementation plan fixed below.
- [x] Phase 2 - TDD RED/GREEN for ordinary selected-Skill admission and durable creation-time provenance.
- [x] Phase 3 - TDD RED/GREEN for copy/retry/resume/existing child pin preservation and worker reauthorization.
- [x] Phase 4 - affected backend tests, compile, diff, secret/scope checks, and large-feature checklist passed.
- [ ] Phase 5 - fixed-SHA independent broad review, fixes, and re-review.
- [ ] Phase 6 - ready PR, exact-head review substitute and validation evidence, required CI.

This document records a backend source slice only. It does not claim frontend completion, B1-B6 closure, B2 sandbox closure, 211 verification, deployment, or a closable product gate.

## Public Contract

Both `POST /api/ai/runs` and the chat stream request accept the same optional ordinary-user selector:

```json
{
  "selected_skill": {
    "skill_id": "authorized-skill-id",
    "expected_version": "projection-version-or-content-hash"
  }
}
```

`selected_skill` is a distinct DTO and is not an alias for the existing raw `skill_id` field. For user-runnable Skill packages, every resolver invocation explicitly verifies the materialization invariant `version == content_hash`; `expected_version` therefore locks both the projected release version and the staged content hash. An authorized current Skill whose expected version is stale returns `409 skill_selection_stale` without returning the current version or hash. Unknown Skill, wrong tenant/department/role, hidden, disabled, missing distribution, unreleased version, and malformed dependency state expose the same ordinary-user error: `capability_not_authorized`.

The existing raw `skill_id` remains admin/internal-only. Existing fixed `capability_id` and default-capability behavior remain compatible. When `selected_skill` is present, the server retains the requested public agent identity but resolves the Skill without requiring `agents.default_skill_id == skill_id`; the agent must still belong to the tenant and be active.

## Deep Module Design

The repository authorization seam owns four operations:

1. Resolve a fixed agent/default Skill for the legacy capability path.
2. Resolve an active selected Skill for a tenant without the default-Skill equality constraint.
3. Apply the same Capability Distribution and MCP dependency authority to either resolution mode.
4. Validate `version == content_hash` and an exact client or source-run pin without consulting a newer release as an implicit upgrade.

Routes only choose the selection mode, provide the authenticated principal and normalized input, and persist the returned lock. They do not reproduce department, role, visibility, lifecycle, MCP, or release-version rules.

The create transaction orders work as follows:

1. Normalize caller input and reject server-owned/internal selectors.
2. Verify tenant workspace and every requested file through read-only admission.
3. Authorize Skill and MCP dependencies and validate the expected exact version.
4. Materialize exact Skill/dependency manifests and release decision.
5. Create session/run records.
6. Insert immutable `run_skill_snapshots` for every manifest with `allowed=true`, `staged=false`, `used=false`; the run input remains the durable exact file-content source. A later write may only update monotonic authorization/staging/usage flags when identity, version, hash, source, and dependencies match exactly.
7. Bind already-admitted files, write initial events/context, commit, then enqueue.

Worker dispatch reloads the locked run snapshot, rejects queue identity or immutable snapshot mismatch, reauthorizes current Skill distribution and MCP dependencies, and only then resolves an adapter or creates/stages runtime state. Denials write a sanitized error/event/audit and never call the adapter. Worker persistence must never overwrite historical identity/version/hash/source/dependencies.

## Provenance Semantics

Copy, retry, resume, and the existing child handoff path answer two separate questions:

- Current authorization: may the persisted owner execute this Skill and its MCP dependencies now?
- Provenance: which exact version, content hash, files, dependencies, and release decision must execute?

The first is reevaluated on every derived run. The second defaults to the source run's pinned manifests and release lock. Publishing v2, moving the current release from v1 to v2, or ordinarily deprecating v1 does not change or block a v1 replay. A disabled/security-revoked historical v1, an immutable snapshot mismatch, or a historical package that can no longer be materialized fails closed. A future explicit upgrade route, if added, must be a separate public action and is outside this slice.

Historical runs that do not contain an immutable `skill_version`, release decision, and exact Skill manifests cannot be safely replayed and therefore fail closed. They are not rematerialized from the current release. If product support for upgrading such a run is added later, it must use an explicit upgrade-to-current UX/API rather than copy, retry, resume, or child dispatch.

`tests/test_run_control_routes.py` is a controller-approved test-only scope expansion. Only the directly affected copy/retry/resume/dispatch fixtures and their shared provenance helper were updated to represent exact v1 identity and to assert that a current v2 authorization result does not change the queued v1 pin. No multi-agent product capability was added.

## Implementation Plan

### Task 1: Contract and selected resolver

Files: `app/models.py`, `app/repositories.py`, `tests/test_repositories.py`.

- [x] Add failing DTO and repository tests for selected authorization, explicit `version == content_hash`, stale `409 skill_selection_stale`, raw-selector separation, fixed/default compatibility, and MCP denial; existing Capability Distribution tests remain the authority coverage for tenant/department/role/hidden/disabled/missing policy.
- [x] Run focused tests with fresh `.pytest-tmp/selected-skill-*-red-*` children and record the expected failures.
- [x] Add `SelectedSkillRequest`, a selected-Skill resolver, and shared authorization implementation while retaining the fixed resolver interface.
- [x] Run the same focused tests green.

### Task 2: Runs/chat creation and durable provenance

Files: `app/routes/runs.py`, `app/routes/chat.py`, `app/repositories.py`, `tests/test_routes.py`, `tests/test_chat_routes.py`.

- [x] Add failing route tests proving the shared DTO, stale `409` without current-version leakage, non-enumerating forged denial, workspace/file/Skill/MCP admission before writes, required file behavior, fixed/admin compatibility, and creation-time immutable `run_skill_snapshots`.
- [x] Run the focused RED tests with fresh `.pytest-tmp/selected-skill-routes-red-*` children.
- [x] Implement the common route selection and snapshot persistence calls without changing the queue schema.
- [x] Run route/repository tests green.

### Task 3: Derived runs and worker dispatch

Files: `app/repositories.py`, `app/routes/runs.py`, `app/worker.py`, `tests/test_repositories.py`, `tests/test_routes.py`, `tests/test_worker.py`.

- [x] Add failing tests for source v1 replay after current v2, deprecated v1 replay, disabled/security-revoked v1 denial, copy/retry/resume/existing child current reauthorization plus exact pin, immutable snapshot conflict, post-enqueue revoke/version/hash/MCP denial, no stage/no adapter, cancel coexistence, and denial side-effect control.
- [x] Run focused RED tests with fresh `.pytest-tmp/selected-skill-worker-red-*` children.
- [x] Reuse the source execution snapshot for derived runs and reauthorize worker dispatch through the selected resolver plus durable pin checks.
- [x] Run the focused tests green.

### Task 4: Closure evidence

Files: all changed backend/tests and this document.

- [x] Run affected backend tests only, each pytest invocation using a fresh unique child under `.pytest-tmp/` (`tests/test_run_control_routes.py`: `129 passed`; complete six-file affected suite: `682 passed`).
- [ ] Run `python -m compileall -q app tools scripts` and `git diff --check`.
- [ ] Verify changed-file scope, no secrets/real `.env`/personal paths, public docstrings, happy/error coverage, and milestone documentation state.
- [ ] Commit and push a fixed head, dispatch an independent broad review against base/head, fix all Critical/Important findings, and re-review the new fixed SHA.
- [ ] Open a ready PR linked to #385, post exact-head review substitute and validation evidence comments, and observe required CI.

## PostgreSQL Gate

No PostgreSQL integration test was added or run because this source slice's ordering and immutable-write contracts are covered by existing repository/route transaction fakes. No DSN-backed PostgreSQL pass or clean skip is claimed. If a future property requires a real database, its test must be guarded by an explicit DSN environment variable.

## Local Verification Evidence

- `tests/test_run_control_routes.py`: `129 passed` using `.pytest-tmp/selected-skill-run-control-20260711-02`.
- Selected resolver materialization invariant: RED (`DID NOT RAISE`), then GREEN (`2 passed`) using fresh resolver-specific children.
- Chat raw internal selector: RED (`DID NOT RAISE`), then GREEN; complete chat slice `53 passed`.
- Complete affected suite: `682 passed` using `.pytest-tmp/selected-skill-affected-20260711-02`.
- `python -m compileall -q app tools scripts`: exit 0.
- `git diff --check`: exit 0.
- Changed-file allowlist and added-line secret/real `.env`/personal-path scan: pass.

Large-feature self-review:

- [x] No secrets, real `.env` values, personal paths, schema, frontend, deploy, CI, or 211 changes were added.
- [x] New public DTO and repository functions have docstrings.
- [x] Happy paths and stable error/denial paths have focused tests.
- [x] This Phase document records the source-slice status; no milestone or runtime gate is claimed closed.

Diff summary: this source slice adds one nested ordinary-user selected-Skill contract shared by runs and chat, reuses Capability Distribution and MCP admission without opening raw selectors, persists immutable creation-time Skill provenance, and makes worker and replay paths reauthorize current access while retaining the exact historical pin. It intentionally fails closed for legacy runs without immutable provenance and leaves explicit upgrade-to-current behavior outside this change.
