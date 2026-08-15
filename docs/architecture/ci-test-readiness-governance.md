# CI, Test, and Readiness Governance

Status: normative repository contract  
Owner: platform architecture  
Authority baseline audited: `6c010079782afe30ada5f75c44600939f0381b13`  
Ledger refreshed: 2026-08-15

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
| `tests/test_lambchat_streaming_replay.py` | Entire module unconditionally skipped as retired PostgreSQL-poll transport. | delete | Completed in the isolated retirement batch; canonical SSE v2.1 tests remain. Publish only after the backend workflow writer is serialized. |
| 19 retired PG-poll tests in `tests/test_lambchat_frontend_compat.py` | A dynamic fixture skipped named retired tests while retaining their bodies. | delete | Completed in the isolated retirement batch; focused LambChat/SSE tests passed. Publish only after overlap audit. |
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
| `docs/release-evidence/**/*.json` | Historical JSON was scanned by readiness aggregators and could be mistaken for current proof. | exact-subject archive only | Subject binding is implemented for observability/release evidence; three invalid orphan entries are deleted. Archive retention authority remains pending. |
| frontend required aggregator | Two shell string checks existed; workflow self-test only asserted their text. | executable fail-closed contract | Completed: exact YAML trigger/needs parsing, duplicate-key rejection, no `continue-on-error`, and success/failure/skipped/cancelled/missing execution tests. |
| backend required aggregator | Required result and most workflow self-tests remain string/static checks. | same executable contract as frontend | Pending PR #1067 terminal state because the workflow and test paths overlap. |
| frontend static readiness builders under `tools/` | Foundation/Governance import CLI-owned modules; architecture has no backend `frontend` owner and forbids arbitrary new app-root modules. | explicit evidence input or authority-owned module | Decision pending. Do not create ad-hoc `app/frontend_*` modules or move CLI entrypoints into runtime code. |
| privileged/root/Windows-only tests | Host capability gates make these skip on ordinary runners. | external acceptance | Keep, but assign named privileged or Windows lanes before citing them as release proof. |

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
