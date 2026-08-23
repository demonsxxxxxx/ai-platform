# CI, Test, and Readiness Governance

Status: normative repository contract
Owner: platform architecture
Authority baseline audited: `6c010079782afe30ada5f75c44600939f0381b13`
Ledger refreshed: 2026-08-23

## 1. Purpose

This contract prevents source assertions, optional tests, historical evidence,
and live health from being presented as the same kind of proof. It governs:

- GitHub Actions required checks and trusted governance;
- unit, contract, integration, architecture, and external-acceptance tests;
- runtime health/readiness projections under `app/`;
- offline readiness, evidence generation, and release verification under
  `tools/` or `scripts/`; and
- retirement of obsolete tests and evidence readers.

The source architecture remains authoritative for package ownership and
deletion proof. This document adds the CI/test/readiness-specific rules.

## 2. Evidence levels

Every report and review MUST name exactly which level it proves:

| Level | Proves | Does not prove |
| --- | --- | --- |
| Source | code, schema, and static contract exist at an exact commit | test execution, deployment, or runtime behavior |
| Focused test | named tests passed against a named source tree | unrelated tests, packaged image, or deployed service |
| CI/build | named job commands passed for an exact workflow subject | rollout, production configuration, or external behavior |
| Packaged image | exact image built, inspected, and started under the named probe | production deployment or real provider acceptance |
| Deployment | exact image and configuration were applied to a named environment | workflow correctness or end-user acceptance |
| Runtime | a named live subject returned observed runtime signals | broad release readiness or external acceptance |
| External acceptance | a documented actor completed a named end-to-end workflow | untested tenants, providers, or operating conditions |

Historical JSON under `docs/release-evidence/` is immutable review input for
its recorded subject. It MUST NOT be scanned by a live health endpoint and MUST
NOT be treated as evidence for the current commit merely because it is present
in the checkout.

## 3. Target test model

The target layout is defined in the source architecture. Until flat tests move
with their owners, each test still has one explicit class:

- **unit**: deterministic, no network, database, Redis, container, clock, or
  repository checkout dependency;
- **contract**: stable API/event/projection/port/facade behavior, with no claim
  about a concrete external adapter;
- **integration**: real PostgreSQL, Redis, object storage, SDK/provider, image,
  or concurrency behavior;
- **architecture/governance**: dependency, placement, registry, workflow, and
  immutable-authority enforcement; and
- **external acceptance**: controlled-host or deployed workflow evidence,
  executed outside ordinary unit/contract shards.

An integration test selected by required CI MUST receive its real dependency
and MUST finish with zero skipped tests. A missing required service is a CI
failure, not a skip. Opt-in local integration tests may skip only when they are
not being used as required evidence.

Tests MUST NOT be retained merely to preserve a historical implementation.
Delete a test when its production path is formally retired and the test only
asserts that retired path. Keep or rewrite the test when it protects a current
wire, persistence, security, denial, concurrency, or compatibility contract.

The normative local execution procedure is
[`../agent-rules/local-test-execution.md`](../agent-rules/local-test-execution.md).
It governs explicit selection, worktree binding, basetemp allocation, locking,
timeouts, process-tree cleanup, and local evidence reports. It does not change
the test classes or evidence levels owned by this architecture contract, and it
does not replace immutable pre-push authority or required CI.

## 4. Required CI topology

The backend required result is an aggregation boundary, not a place to hide
test selection:

```text
trusted governance (base authority; candidate code not executed)

backend validation
  -> streaming PostgreSQL/Redis integration (real services; zero skips)
      -> backend test shards
  -> Agent/Skill contracts (real PostgreSQL)
  -> packaged backend image
      -> backend required
```

Rules:

1. Trusted governance continues to execute the accepted authority implementation
   and protects the required workflow contract.
2. Candidate workflow self-tests are regression aids. They are not a substitute
   for the trusted workflow because a candidate can otherwise edit both a
   workflow and its string assertions.
3. Required jobs MUST use exact source SHAs, locked dependencies, bounded
   timeouts, and ordinary failure propagation. `continue-on-error`, hidden test
   selectors, and silent service skips are forbidden.
4. A test appears in one required execution lane unless duplication is an
   explicit independent-subject check.
5. GitHub check success is reported as CI evidence only.

Required pytest integration lanes write JUnit XML and run
`tools/require_zero_junit_skips.py` against that exact report. A missing,
malformed, empty, or skipped report fails the lane.

Packaged image jobs remain required aggregation inputs on every pull request,
but the expensive build, vulnerability scan, provenance, and startup steps run
only when the exact base/head diff changes an input copied by that image, the
owning workflow, or its Linux image contract. An unaffected pull request reports
`not_affected` from the successful image job; it does not skip the job. Pushes to
`main` and manual workflow dispatches always build both immutable images so every
release commit retains image, source-marker, and admission evidence. The shared
`tools/ci_image_scope.py` path inventory is executable contract authority and is
covered by the backend release-governance shard.

## 5. Runtime readiness boundary

Code in `app/` may expose readiness only when it observes the running process or
its authoritative runtime dependencies. Appropriate runtime signals include:

- database pool state and schema readiness;
- Redis queue depth, worker heartbeat, leases, and backpressure;
- tenant-scoped run/error/latency/token summaries;
- selected provider/container observations;
- current admission limits and actually enforced capacity; and
- per-run control readiness and sandbox executor readiness evidence.

The following do not belong in a live request path:

- recursive source-tree or Git history scans;
- scans of `docs/release-evidence/`;
- source marker checks and issue-closure discovery;
- Markdown report rendering;
- operator checklists, release packets, or product-beta declarations; and
- a static list of controls described as implemented without observing them.

Those capabilities move to `tools/` or `scripts/`. A runtime endpoint may link
to a documented offline command, but it MUST NOT execute the offline audit or
project its historical result as current health.

## 6. Unified disposition ledger

This table is the reviewed inventory for the audited baseline. `Baseline fact`
describes what was observed at the authority commit. `Disposition` is the
approved target. `Completion proof / remaining exit` prevents a decision from
being reported as completed before the executable evidence exists.

| Surface | Baseline fact | Disposition | Completion proof / remaining exit |
| --- | --- | --- | --- |
| `tests/test_lambchat_streaming_replay.py` | Entire module unconditionally skipped as retired PostgreSQL-poll transport. | delete | Completed in the current cleanup branch; canonical SSE v2.1 tests remain. Publish only after the backend workflow writer is serialized. |
| 19 retired PG-poll tests in `tests/test_lambchat_frontend_compat.py` | A dynamic fixture skipped named retired tests while retaining their bodies. | delete | Completed in the current cleanup branch; focused LambChat/SSE tests passed. Publish only after overlap audit. |
| retired endpoint half of `test_public_lifecycle_singletons_match_builder_sse_reconnect_and_exact_history` | Patched a removed `asyncio.sleep` PostgreSQL-poll seam and failed on the unchanged baseline; its pure history-projection assertions remained valid. | shrink | Completed: removed the obsolete endpoint fixture and retained lifecycle singleton, ordering, and private-payload filtering coverage. |
| stale `raising=False` patches for `_PARENT_ROLLUP_RETRY_DELAY_SECONDS` and `_authorize_persisted_run_for_queue` | The production attributes no longer existed; the autouse fixtures silently created inert attributes that no production path read. | delete test seams | Completed: removed both inert patches and the now-unused stub; complete worker and run-control route suites passed. |
| `test_installed_claude_agent_sdk_02130_contract` version mismatch | The project and lock file require exact SDK `0.2.130`, but the contract test skipped when another version was installed. | fail closed | Completed in the test: version drift is now an assertion failure. Required-workflow ownership remains pending #1067 terminal state. |
| `tests/test_streaming_postgres.py` | Selected by backend required, but all six tests skip when `AI_PLATFORM_S0A_SCHEMA_TEST_DSN` is absent; the workflow does not provide it. | required PostgreSQL integration | Pending PR #1067 terminal state. Provide a real service/DSN and enforce the generated report with `tools/require_zero_junit_skips.py`. |
| real-Redis test in `tests/test_streaming_redis.py` | Selected by backend required, but skips when `AI_PLATFORM_SSE_REDIS_TEST_URL` is absent; the workflow does not provide it. | required Redis integration | Pending PR #1067 terminal state. Provide Redis and enforce the generated report with `tools/require_zero_junit_skips.py`. |
| PostgreSQL interleaving test in `tests/test_repositories.py` | Selected by backend required but skips without `AI_PLATFORM_S0A_SCHEMA_TEST_DSN`. | required PostgreSQL integration | Move into the same real-service lane; do not treat the general repository shard as integration proof until then. |
| `tests/test_agent_profiles_postgres.py` | Required job provisions PostgreSQL and missing DSN in GitHub Actions raises instead of skipping. | retain required integration | Current positive reference for fail-closed integration topology. |
| other `*_postgres.py` opt-in suites | Schema, retention, streaming-schema, persistence-limit, capability-distribution, and MCP PostgreSQL suites are not selected by any workflow. | explicit external acceptance or owned CI lane | Pending owner/lane assignment. They MUST NOT be cited as CI evidence while unselected. |
| `app/b5_file_tool_readiness.py` and `app/b6_operations_beta_readiness.py` | No route/worker/deploy caller; only CLI/tests; payloads explicitly deny runtime/beta claims. | move to `tools/` | Completed in the isolated offline-readiness batch; main publication is serialized with its other paths. |
| `app/run_control_readiness.py` | Current per-run projection is used by the Runs API. | keep runtime | Retain focused route and projection coverage. |
| `app/runtime/sandbox/readiness_evidence.py` | Typed executor readiness is emitted by the live sandbox/provider path. | keep runtime | Retain provider/runtime tests; this is not historical release evidence. |
| runtime portion of `app/capacity_baseline.py` | Admin Runtime consumes current limits/backpressure. | keep runtime, split offline plans | Load plans and evidence packets remain a separate future migration. |
| `app/governance_readiness.py`, `app/observability_readiness.py` | Mixed runtime projection with source/history/offline aggregation. | stop live offline scans, then split owners | Admin Runtime decoupling is complete in the isolated batch; remaining module ownership is pending. |
| Foundation runtime-concurrency latest-any fallback | Selected unrelated historical failed evidence for display when no verified subject matched. | delete | Completed: selector and misleading repository-snapshot test removed; 101 focused tests passed. |
| Foundation release-evidence latest-any fallback | With unknown source, could select the newest pair from any historical commit. | delete | Completed: fallback removed and unknown-source regression now preserves the configured fail-closed subject. |
| Foundation secret-command archive test | A test scanned only the five JSON files under historical commit `c701974f…`; it could never protect evidence generated for a new subject. | delete snapshot test; test the producer | Completed in source: removed the fixed-archive test and made the evidence wrapper redact secret environment assignments and secret flags, with a producer-level regression. |
| POC company-login audit partial-gate bypass | Unused `--allow-missing-auth-audit` changed a missing ordinary/admin company-login audit into `ok=true`; a second unscoped query also projected the latest login payload from any source. | delete bypass and unscoped evidence | Completed in source: the company-login gate is always fail-closed, performs only the scoped query, and its regression proves legacy/unscoped login rows are neither queried nor projected. |
| `docs/release-evidence/**/*.json` | Historical JSON was scanned by readiness aggregators and could be mistaken for current proof. | exact-subject archive only | Subject binding is implemented for observability/release evidence; three invalid orphan entries are deleted. Archive retention authority remains pending. |
| Office context runtime evidence scan | Valid-looking historical entries could close current gaps without matching the audited Git subject; duplicates were selected by path traversal order. | exact-subject, timestamp-ordered selection | Completed in source: unknown/mismatched subjects fail closed and same-subject candidates use validated `captured_at`. Runtime acceptance remains unclaimed. |
| frontend required aggregator | Two shell string checks existed; workflow self-test only asserted their text. | executable fail-closed contract | Completed: exact YAML trigger/needs parsing, duplicate-key rejection, no `continue-on-error`, and success/failure/skipped/cancelled/missing execution tests. |
| frontend workflow traceability dependency | Adding a selected governance test changed the controlled pytest command, but the indirect release-traceability consumer was not selected by immutable responsibility tests. | explicit responsibility closure | Completed for the current command inventory; future frontend workflow changes must run the complete traceability file as a focused suite. |
| frontend release traceability workflow scan | The traceability tool accepted required command text anywhere in the YAML, including comments or an unrelated job, and did not reject duplicate mapping keys. | semantic job/step contract | Completed in source: parse with a duplicate-key-rejecting loader, bind each command and exact named step to its owning job, and cover wrong-job/comment/duplicate-key failures. |
| packaging publication workflow parser | Workflow tests used `yaml.BaseLoader`, which silently accepted duplicate job, permission, or step keys; several security/version/run-attempt assertions searched the entire YAML text rather than their owning steps. | semantic, duplicate-key-rejecting test authority | Completed in source: frontend and packaging workflow tests share one strict YAML loader; packaging has an executable duplicate-job regression and binds registry login, Buildx/Syft/Trivy/Cosign, signature verification, and artifact names to exact parsed steps. |
| backend-owned static contract suites duplicated in the frontend workflow | `test_backend_ci_workflow.py`, packaging publication, release manifest, release authority, and runtime launch tests were executed by both required workflows even though both trigger on every pull request. | single backend owner | Completed in source: removed the five suites from the frontend command while the frontend workflow self-test proves that every suite remains selected by the backend workflow. CI execution remains unclaimed until published. |
| Linux frontend healthcheck failure injection | The Windows frontend job collected the GNU `stat` test and skipped it on every required run. | move to required Linux lane | Completed in source: the test has its own Linux module and the required `frontend-image` Ubuntu job selects it explicitly. CI execution remains unclaimed until published. |
| backend required aggregator | Required result and most workflow self-tests remain string/static checks. | same executable contract as frontend | Pending PR #1067 terminal state because the workflow and test paths overlap. |
| frontend static readiness builders under `tools/` | Foundation/Governance import CLI-owned modules; architecture has no backend `frontend` owner and forbids arbitrary new app-root modules. | explicit evidence input or authority-owned module | Decision pending. Do not create ad-hoc `app/frontend_*` modules or move CLI entrypoints into runtime code. |
| privileged/root/Windows-only tests | Host capability gates make these skip on ordinary runners. | external acceptance | Keep, but assign named privileged or Windows lanes before citing them as release proof. |
| `app/b2_sandbox_readiness.py`, its CLI, and its dedicated test module | The 1,390-line builder scanned historical JSON and Git/source deltas, rendered a report, and exited successfully even when its status was blocked. It had no route, worker, startup, deployment, release-authority, or direct required-CI gate; its tests primarily supplied their own evidence fixtures. | delete report-only closure | Completed in this cleanup slice. Preserve `scripts/generate_sandbox_runtime_evidence.py`, `scripts/verify_sandbox_runtime.py`, typed live readiness evidence, provider/runtime tests, producer-level redaction tests, and immutable historical evidence. The obsolete workflow selector and root-module allowlist entry are removed with the report. |

## 7. Retirement procedure

For each test or readiness deletion:

1. fix the exact main commit and enumerate all direct, dynamic, deploy, CLI,
   documentation, and external-consumer surfaces;
2. state the current product/runtime path, or prove none exists;
3. name the replacement test, runtime signal, or operator command;
4. delete the obsolete implementation and its obsolete-only tests together;
5. remove architecture allowlist entries and stale workflow selectors;
6. run focused tests plus architecture/workflow governance; and
7. report Source, focused tests, CI, deployment, runtime, and external
   acceptance separately.

If a route, provider, persisted state, public import, or external automation is
involved, apply the stronger deletion proof from the source architecture.

## 8. Completion criteria for this governance program

This program is complete only when:

- required integration suites cannot pass by skipping missing dependencies;
- no live route scans source control or historical release evidence;
- maintained offline readiness generators live outside `app/`;
- obsolete tests and selectors are removed rather than permanently skipped;
- every required test belongs to a named lane with one owner;
- required aggregators execute tested failure paths for every dependency result;
- workflow self-tests protect structure without acting as their own trust root;
- historical evidence has a bounded archive/retention authority; and
- the final ledger has no unowned `audit and assign` entries.
