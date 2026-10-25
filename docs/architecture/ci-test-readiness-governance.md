# CI, Test, and Readiness Governance

Status: normative repository contract
Owner: platform architecture

## 1. Purpose

This contract separates source, test, package, deployment, and runtime proof. It
governs required GitHub checks, test ownership, runtime readiness, offline
verification, and retirement of obsolete checks. It is not a project status
ledger and does not record current pull requests, issue numbers, commits, or
pending cleanup work.

## 2. Evidence levels

Every report names only the level it actually proves:

| Level | Proves | Does not prove |
| --- | --- | --- |
| Source | code, schema, and static contract exist at an exact commit | test execution, deployment, or runtime behavior |
| Focused test | named tests passed against a named source tree | unrelated tests, packaged image, or deployed service |
| CI/build | named job commands passed for an exact workflow subject | rollout, production configuration, or external behavior |
| Packaged image | exact image built, inspected, and started under the named probe | production deployment or real provider acceptance |
| Deployment | exact image and configuration were applied to a named environment | end-user acceptance |
| Runtime | a named live subject returned observed runtime signals | broad release readiness or external acceptance |
| External acceptance | a documented actor completed a named end-to-end workflow | untested tenants, providers, or operating conditions |

Historical files under `docs/release-evidence/` prove only their recorded
subjects. They are never current merely because they remain in the checkout and
must not be scanned by a live health endpoint.

## 3. Test model

Each maintained test has one owner and one class:

- **unit**: deterministic and independent of network, database, Redis,
  containers, wall clock, or repository checkout;
- **contract**: stable API, event, projection, port, facade, or adapter contract;
- **integration**: a real PostgreSQL, Redis, object storage, SDK/provider, image,
  or concurrency boundary;
- **architecture/governance**: dependency, placement, workflow, and trusted-base
  policy; or
- **external acceptance**: a controlled-host or deployed end-to-end procedure.

A required integration test receives its real dependency and completes with zero
skips. A missing service is a failure, not a skip. Opt-in local integration may
skip only when its result is not cited as required evidence.

Delete a test when its production path is retired and it protects no current
wire, persistence, security, denial, concurrency, or compatibility behavior.
Move flat tests with their owning production slice instead of preserving a
historical file layout.

The normative local execution procedure is
[`../agent-rules/local-test-execution.md`](../agent-rules/local-test-execution.md).
Local checks provide developer feedback; required CI owns trusted merge
evidence.

## 4. Required CI topology

Required checks keep stable aggregate names while internal work runs in parallel:

```text
trusted governance (accepted base authority)

backend validation
|-- backend test shards
|-- PostgreSQL/Redis integration (real services; zero skips)
|-- Agent/Skill contracts (real PostgreSQL)
`-- packaged backend image verification
    `-- backend required
```

Rules:

1. Trusted governance executes accepted authority code against the exact range.
2. Candidate workflow tests are regression aids, never their own trust root.
3. Product tests do not wait for author-written review metadata.
4. Required jobs use exact source SHAs, locked dependencies, bounded timeouts,
   and ordinary failure propagation.
5. `continue-on-error`, hidden selectors, and required-service skips are
   forbidden.
6. A test appears in one required lane unless another lane verifies an
   independent subject.
7. Integration lanes run `tools/run_test_stage.py --require-zero-skips`, which
   fails the stage on any skipped test in its exact JUnit report.
8. Image jobs retain a stable successful result when an exact diff is
   `not_affected`; they do not disappear from branch protection.
9. CI success is CI evidence only.

### Run lifecycle acceptance evidence

The `lifecycle-control-chain` backend shard owns the complete
`tests/test_run_execution_lifecycle_integration.py` selector set. Two explicit,
non-overlapping runner stages execute that set in parallel. Each stage uses
PostgreSQL and Redis services, `--require-zero-skips`, a 1,800-second test
budget, and a 50-minute job budget. Both matrix results remain inside
`backend-tests`, which `backend required` already requires. Keep the workflow's
selector batches and zero-skip rule explicit; a missing selector, dependency, or
fixture is a failure. Each local runner's `evidence.json`, JUnit report, and
coverage JSON records its exact selector subset and checkout; both artifacts are
required for the logical shard to pass.

Each C-layer `control-chain.json` manifest binds the source commit, complete
non-ignored worktree digest, lockfile digest, inspected candidate image ID and
labels, observed Python/PostgreSQL/Redis/MinIO versions, and the sandbox profile
configured in the API container. The runtime-version, image-inspection,
source-binding, and profile JSON artifacts are content-addressed by the
manifest and uploaded with the shard evidence. C-layer execution records
`sdk_used: false`; its profile observation is configuration evidence, not an
attestation of enforcement or isolation.

Pull requests targeting `main`, `feat/n01-lifecycle-control-chain`, or
`feat/lifecycle-phase-*` run the same complete backend and frontend required
workflows. Do not add path filters or alter the required aggregate for these
stacked phase branches.

Real SDK/provider acceptance remains an L-layer maintainer action. Its report
uses `tests/support/lifecycle_candidate_evidence.py`; first capture the
checkout binding with `binding --repo-root .`, then build and inspect the exact
candidate image with labels for the source commit, complete non-ignored
worktree source digest, `uv.lock` digest, and dirty-build state. The report must
match that image ID and those labels, record observed Python, PostgreSQL, Redis,
MinIO, SDK, and provider versions, and bind an enforced security-profile ID and
digest. It must include redacted, content-addressed observations for image
inspection, provider acceptance, SDK invocation, callback receipt, committed
answer readback, selected-file download, confirmed stop/release, and
cross-sandbox native resume (R01 and R14).

Validate the report against the same checkout and inspected image:

```bash
python tests/support/lifecycle_candidate_evidence.py validate <report.json> \
  --repo-root . --bundle-root <external-bundle> --image-id <inspected-sha256-id>
```

The image labels are `ai-platform.source-commit`,
`ai-platform.source-tree-sha256`, `ai-platform.uv-lock-sha256`, and
`ai-platform.build-dirty`; obtain the image ID from the same candidate with
`docker image inspect --format '{{.Id}}' <image-ref>`.

The validator rejects incomplete or mismatched evidence; it checks consistency
and artifact digests, not the authenticity of the operator or observations. An
authorized reviewer must inspect the redacted bundle's provenance. C-layer
Docker evidence records a deterministic runner and does not satisfy this L
acceptance. If no authorized real-provider run exists, keep L acceptance
pending; do not synthesize a successful report or expose credentials to
ordinary pull-request runners.

### Convergence checks and repair feedback

[Source architecture §12](source-code-architecture.md#12-executable-governance)
owns the executable architecture boundaries, new production code size limit,
and frozen-file ratchet. Reuse its trusted policy rather than creating another
size policy. In
`tools/code_governance.py`, Ruff violations block; file counts, line growth,
hotspots and test-volume signals remain advisory. Findings carry an additive
`remediation` field in JSON and a text repair hint. Fix the reported behavior and
rerun the same range; hints do not create exemptions or change exit status.

`tests/test_source_authority_docs.py`, already in the backend governance shard,
checks inline local file links from the authority index and its directly linked
Markdown owners. Missing files and resolved paths outside the repository fail
with a repair hint. URL fragments stay attached to the source link; this file
check does not validate anchor existence, external URLs, or document freshness.
It reuses the authority map, without crawling archives or introducing another
rules index. Local execution uses the existing test-stage runner.

Frontend Python contract jobs use the existing pinned uv to export `uv.lock`
with `--locked`, then install only their pytest/PyYAML subset and applicable
transitive dependencies under those exported constraints. Installation verifies
the selected distributions against the lock; Windows-only dependencies remain
selected by platform markers. This candidate dependency source is separate from
the accepted-base trusted governance dependency allowlist.

## 5. Runtime readiness boundary

Code in `app/` exposes readiness only when it observes the running process or an
authoritative runtime dependency. Valid signals include database/schema state,
Redis queues and leases, worker heartbeats, tenant-scoped operational summaries,
provider/container observations, enforced limits, and per-run control state.

The following stay outside live request paths:

- source-tree or Git-history scans;
- scans of `docs/release-evidence/`;
- source marker and project-closure discovery;
- Markdown report rendering;
- operator checklists or release packets; and
- static control inventories presented as current runtime facts.

Offline checks belong under `tools/` or `scripts/`. A runtime endpoint may link
to an operator command but does not execute or project its historical output.

### Retired offline gate reports

The Foundation Alpha, capacity load-gate, sandbox hardening, Office context,
governance, observability, tool-policy, Skill release and release-evidence
readiness reports, their evidence wrappers and POC verifiers were retired on
2026-10-24 without compatibility commands, together with their archived
evidence under `docs/release-evidence/`. They produced static or historical
summaries, not runtime observations. Their removal does not close any
acceptance gap or authorize a release.

What remains at runtime: Admin Runtime projects the configured capacity
baseline from `app/capacity_baseline.py` (configuration, not load-test proof),
and the Admin Skill release review reads `docs/release-evidence/skill-release/`
for `builtin` Skill versions. `tools/verify_poc_gate.py` stays as the optional
live diagnostic described in the release runbook. A new acceptance check must
observe a named live subject; do not restore a source-scanning report.

## 6. Retirement and ownership

When deleting a test or readiness surface:

1. inventory direct, dynamic, deployment, CLI, documentation, and external
   consumers;
2. identify the current runtime replacement or prove none exists;
3. name the replacement test, runtime signal, or operator command;
4. delete obsolete implementation, tests, selectors, and allowlist entries
   together; and
5. run focused tests plus applicable architecture and workflow checks.

Current work, owners, exceptions, and completion state belong in the active pull
request or task, not this durable contract. A new governance rule must replace
or consolidate an existing rule, name a deterministic detector and owner, and
show that a less restrictive test or platform control cannot protect the same
boundary.

## Cross-component architecture acceptance

The [system matrix](../acceptance/system-architecture-matrix.md) supplies
proposed falsifiable scenarios for the affected slice; it neither defines
another evidence hierarchy nor claims tests exist or have passed. Record
scenario ID, exact subject, concrete selector/command, injected failure,
expected state/identity/effects, deadline, result, and cleanup in the issue/PR.
Map each new scenario to a real owning test before claiming coverage.

A docs-only consolidation checks inventory, current-versus-proposed labels,
local links, unique requirement IDs, preserved detailed invariants and unchanged
runtime/config/schema scope. It does not claim runtime proof or require a
fabricated all-system test pass. For behavior changes, retain the existing
trusted-base governance and smallest owning regression/integration stages.
No new checker is a trusted merge gate merely because its author ran it.
