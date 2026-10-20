# Source quality baseline

Status: dated source observation and measurement guide; this is not a conformance,
coverage, security or deployment certificate.

Owner: repository quality; architectural decisions remain with the
[authority index](README.md) and [source architecture](architecture/source-code-architecture.md).

Baseline: **2026-10-13 (Beijing time), `f519d041f1bb155a788278abfd5e07becebe1767`**,
branch `harnessing`. The inventory reads tracked working-tree source; its report
records HEAD, tracked app changes, the app byte digest, policy digest and generator
digest. Untracked app code and the new analysis/test files are outside this baseline.
There were no tracked app changes when measured.

## Reproduce

From the worktree root, prepare the locked test environment as described in
[CI](CI.md#41-环境与文档测试). Source parsing uses the standard library; workflow
selection inventory reuses the already-locked PyYAML dependency. The script never
imports product code:

```bash
"$harness_python" scripts/analysis/source_quality_inventory.py
```

The checked-in [JSON inventory](generated/source-quality-inventory.json) is generated
output. Regenerate it from the same command when intentionally refreshing this
baseline. Parse errors give exit code 2 and mean that the inventory is incomplete.
The owning check is [test_source_quality_inventory.py](../tests/test_source_quality_inventory.py).

## Scope and denominators

- Source size: tracked `app/**/*.py`; frontend tracked `.ts/.tsx/.js/.jsx` under
  `frontend/web/src`. Physical lines include blanks/comments; Python function
  counts include methods and nested functions. Function span is not cyclomatic
  complexity.
- Context layout percentage: canonical `domain/application/infrastructure/transport`
  directories plus `api.py/events.py/registry.py`, divided by non-`__init__.py`
  implementation modules **inside that context**. Root legacy/composition code is
  outside this denominator. A valid path does not prove correct responsibilities
  or allowed dependency direction.
- Public peer percentage: unique resolved static cross-context import edges to
  `api`/`events`, divided by all such peer edges. Duplicate statements for a module
  pair count once. Conditional, local and type-only imports are included; dynamic
  imports, string monkeypatches and transitively exported attributes are not.
  This indicator does not replace the stronger trusted architecture checker.
- Layer scores use the same peer-edge formula grouped by the source layer. Root
  context modules are grouped as `public` or `legacy`. Empty denominators are N/A.
- Direct test-import files are static references from tracked `tests/**/test_*.py`.
  They are navigation evidence, not executed test counts or coverage. Frontend
  test files follow `.test.`/`.spec.`; other `__tests__` code is test support.

## Whole tracked-source inventory

| Measure | Observed value |
| --- | ---: |
| Backend Python files | 452 |
| Backend physical lines | 137,067 |
| Backend function/method definitions | 4,486 |
| Backend test files | 328 |
| Frontend production source files | 423 |
| Frontend production physical lines | 94,689 |
| Frontend test files | 271 |
| Frontend test support files | 3 |
| Resolved cross-context internal import edges | 36 |
| Parse failures | 0 |

## Context indicators

These 0–100 percentages measure the stated structural facts. They are not an
aggregate quality grade. `object_lifecycle` is a declared target with no measured
implementation modules; N/A does not require creating empty scaffolding.

| Context | Modules | Layout % | Peer edges | Internal peer edges | Public peer % | Direct test-import files |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| agent_apps | 11 | 81.8 | 15 | 13 | 13.3 | 13 |
| artifacts | 4 | 100.0 | 0 | 0 | N/A | 4 |
| context | 17 | 82.4 | 0 | 0 | N/A | 27 |
| conversations | 6 | 100.0 | 0 | 0 | N/A | 14 |
| execution | 40 | 100.0 | 13 | 1 | 92.3 | 43 |
| files | 11 | 100.0 | 1 | 1 | 0.0 | 17 |
| identity | 11 | 100.0 | 0 | 0 | N/A | 31 |
| mcp | 17 | 94.1 | 0 | 0 | N/A | 14 |
| object_lifecycle | 0 | N/A | 0 | 0 | N/A | 0 |
| runs | 46 | 100.0 | 27 | 16 | 40.7 | 81 |
| sandbox | 13 | 100.0 | 2 | 2 | 0.0 | 40 |
| skills | 34 | 64.7 | 3 | 3 | 0.0 | 40 |
| streaming | 21 | 90.5 | 3 | 0 | 100.0 | 40 |

## Layer indicators

Module counts below are the 231 non-initializer context modules; they exclude
legacy root, `bootstrap`, `platform`, `kernel` and other out-of-context packages.
Peer scores do not evaluate within-context layer imports or external libraries.

| Source layer | Modules | Peer edges | Internal peer edges | Public peer % |
| --- | ---: | ---: | ---: | ---: |
| domain | 47 | 0 | 0 | N/A |
| application | 80 | 17 | 1 | 94.1 |
| infrastructure | 64 | 33 | 25 | 24.2 |
| transport | 6 | 0 | 0 | N/A |
| public | 14 | 2 | 0 | 100.0 |
| legacy within contexts | 20 | 12 | 10 | 16.7 |

## Investigated signals

The report contains every resolved backend static import edge, all 36 internal
peer edges, and the ten largest backend/frontend files. The original B3–B5 scope
is a bounded first migration, not the complete remaining architectural inventory.
Agent Apps contributes 13 of the 36 internal peer edges; additional edges require
owning use-case and compatibility analysis before being scheduled.

The sampled static cycle is
`docker_governed_network -> errors -> opensandbox.startup -> opensandbox_policy -> docker_governed_network`
under `app.platform.sandbox` / `app.runtime.sandbox`. The startup-to-policy import
is **inside** `renew_opensandbox_lifetime` (line 279), so the static graph does not
establish a startup import failure. The report exposes importer-to-imported
orientation, protected by a three-module cycle regression.

Three active source contracts were compared with their implementations at the
baseline: Runs lifecycle ports and Bootstrap ledger injection; Sandbox shared
callback authority with Run-before-Attempt locking, persisted tenant, exact lease
and token-generation checks; caller-owned replay transaction plus current principal
reauthorization before child creation and queue admission. Source locations and
owning selectors are recorded in the [active plan](exec-plans/active/harness-migration.md).

## Security and verification observations

The [dependency audit](generated/dependency-audit.json) queried **OSV** for the
85 packages in the B1 locked Windows/Python test environment, **before** the B2
coverage extra was added.
It found **0 known vulnerabilities and 0 skipped packages**, on 2026-10-13 Beijing
time, using ephemeral `pip-audit` with `--strict`. A preceding PyPI service request
failed during TLS handshake; that attempt is not success evidence. The OSV result
covers this installed environment including test dependencies; it does not audit
Linux image packages, Node dependencies, container/host enforcement or application
input handling. No dependency was automatically upgraded. B2 added only test-extra pytest-cov,
coverage, and the Python-version-marked tomli lock entry; existing package versions
stayed unchanged. The attempted OSV refresh for the updated installed environment
failed with a TLS handshake timeout, so the retained 85-package report does **not**
cover the new coverage dependencies.

```bash
HTTPS_PROXY=http://127.0.0.1:7897 uvx --python 3.13 pip-audit \
  --path .venv/Lib/site-packages --strict --vulnerability-service osv \
  --progress-spinner off --timeout 5 --desc off --format json
```

The proxy is a local Windows execution detail, not repository/deployment config;
on another machine supply its own verified network access. Never disable TLS
verification to complete the audit.

Owning measurement and source-authority checks passed: **21 tests, 0 skips**
(5 analysis regressions and 16 document-contract checks), plus **173 governance
regressions, 0 skips** and **104 scoped replay/callback/transfer/persistence tests,
0 skips**. These disjoint selected suites total 298 cases; they are not the complete
328-file backend test suite. The cycle orientation case was first observed failing,
then passed after correction. Windows Git deep-path failures were reproduced and
resolved with command-scoped longpaths, without changing assertions or gate policy.
The observed commands and scope are in [CI §4.5](CI.md#45-windows-深路径与已观测的本地门禁).

| Required measurement | Disposition |
| --- | --- |
| Whole-backend line/branch coverage | Full-suite selection unmeasured; only the explicitly scoped six-file sample above |
| Critical-context line/branch coverage | Sample rows above; complete owning selectors/real dependencies still needed |
| Frontend coverage and interaction completeness | Unmeasured; existing mounted/source tests have different scopes |
| Linux/container and Node vulnerability baseline | Unmeasured in this worktree |
| Three consecutive complete test rounds | Not run; Stage 5 acceptance remains pending |
| New-file size and coverage blocking thresholds | D5 pending; no threshold invented from these indicators |
| Deployed behavior and real concurrency | Not checked; local/source observations do not establish it |

## B2 scoped coverage observation

Status: dated sample, 2026-10-13 Beijing time. The runner now has explicit app
statement/branch instrumentation, but the full backend suite was not executed.
The six selectors in [CI §4.6](CI.md#46-b2-文档门禁与显式-coverage) passed **132 cases,
0 skips** with pytest-cov 7.1.0 / coverage 7.16.2 on Windows/Python 3.13.2.
The report denominator was checked against all **452** tracked app Python files,
including unimported namespace packages. Imported definitions may count as executed
lines; these percentages do not prove assertion quality or complete test selection.

| Scope under this same six-file sample | Executed / executable statements | Statement % | Executed / possible branches | Branch % |
| --- | ---: | ---: | ---: | ---: |
| All app files | 13,809 / 54,341 | 25.41 | 1,253 / 17,596 | 7.12 |
| runs | 1,738 / 4,305 | 40.37 | 128 / 1,454 | 8.80 |
| skills | 1,263 / 3,574 | 35.34 | 304 / 1,340 | 22.69 |
| sandbox | 422 / 1,868 | 22.59 | 76 / 712 | 10.67 |
| files | 362 / 651 | 55.61 | 41 / 166 | 24.70 |
| identity | 264 / 576 | 45.83 | 5 / 114 | 4.39 |
| streaming | 1,145 / 2,839 | 40.33 | 21 / 854 | 2.46 |

Each percentage is 100 times its integer numerator divided by denominator;
context rows sum file summaries under `app/<context>/`, not unrelated root modules.
The raw term report's combined percentage is not the separate statement/branch
percentage. This sample includes mocked/contract boundaries, not real PostgreSQL
or provider/host validation. It must not set D5's full/critical-suite thresholds.

Raw JSON, coverage data/config and JUnit remain in the runner-owned stage. Its
evidence binds selectors, HEAD, the candidate runner digest, the app/tests Python
source digest before/after execution, exact report digest and file count. The
current candidate is uncommitted above f519d041; HEAD alone does not identify the
new runner/test code. This page is the derived quality observation, not a copied
second run-evidence ledger.

The namespace omission regression was observed red before enabling coverage's
existing namespace-package option. The earlier 450-file report is superseded for
coverage measurement; the first 452-file attempt failed on WinError 206 and is
not successful evidence. Short stage `b2cov` passed the unchanged selector set.
Owning B2 runner/checker/workflow/document/inventory regressions passed **88 cases,
0 skips**, including real timeout and descendant cleanup with coverage enabled.

## Full-suite selection and real-dependency disposition

Status: pre-commit candidate inventory snapshot, 2026-10-13 Beijing time; not observed CI.
The generated `workflow_test_inventory` field reconciles tracked test filenames,
current untracked owning tests, and literal Python selectors from workflow `run` /
`test_files` fields. Workflow content digests identify the uncommitted candidate;
HEAD alone does not identify its modified CI or analysis script.

| Selection fact | Current candidate |
| --- | ---: |
| Tracked backend test files | 328 |
| New untracked owning test files | 2 |
| Candidate Python test files | 330 |
| Candidate files with literal workflow references | 244 |
| Tracked files without literal workflow references | 86 |
| Missing literal target files | 0 |
| Files referenced only by named nodes | 4 |

The two new files are the document checker and source inventory regressions. Before
adding the two direct Runs lifecycle files, 88 tracked files lacked literal workflow
references. `test_run_lifecycle.py` and `test_run_lifecycle_postgres.py` are now in the
existing `run-control-contracts` lane; its owning workflow test was updated together.
The latter uses an injected recording connection, not a real PostgreSQL service.
The former's memory helpers are consumed by `test_executor_receipt_settlement.py`;
that does not collect its seven standalone test functions.

All remaining paths and exact workflow references are in the generated JSON, not a
second handwritten selector list. The four node-only files are `test_execution_spec`,
`test_lambchat_frontend_compat`, `test_s0a_schema_postgres` and `test_schema`. A named
node reference does not select every function in its file. This static inventory
cannot prove dynamic/indirect execution, node existence, retirement, or test quality.
An unreferenced filename is a selection-disposition question, not evidence of missing
production coverage or a reason to delete the test.

Consequently, the existing eight backend shards plus Agent/Skill/Frontend contracts
cannot yet be labelled the complete 330-file candidate suite. Before defining D5,
assign every candidate test/current node to a real owning lane or document a proved
retirement/acceptance owner; retain required services and platform conditions. Do not
silently exclude the 86 files or use a path count as test coverage.

Local facility checks found no Docker/WSL/PostgreSQL/Redis commands on PATH, no
configured `AI_PLATFORM_S0A_SCHEMA_TEST_DSN` / `AI_PLATFORM_SSE_REDIS_TEST_URL`, and
closed local ports 54329, 5432 and 6379. A bounded real-dependency attempt used
`test_sandbox_persistence.py::test_executor_acceptance_terminal_order_converges_in_postgres`
and `test_streaming_v4_redis_integration.py::test_real_redis_rejects_v4_append_to_legacy_v3_phase_without_mutation`.
The runner reported **7 skips, exit 6, required_dependency_missing**. This is failed
required evidence; no fake provider or mocked connection substitutes for it.
The existing CI facility uses PostgreSQL 16 / Redis 7.4 where declared, but no
GitHub run or remote host was authorized/observed in this work.

## Extended local critical-boundary sample

The twelve explicitly selected files in [CI §4.7](CI.md#47-完整范围库存与关键域补采)
passed **174 tests, zero skips**. Compared with the earlier six-file sample, this
adds lifecycle, Attempt application/repository and locked/dispatch authorization
contracts. It still does not run all critical-domain tests or real PostgreSQL/Redis.
All 452 app source files remain in its denominator.

| Scope in this twelve-file sample | Executed / executable statements | Statement % | Executed / possible branches | Branch % |
| --- | ---: | ---: | ---: | ---: |
| All app files | 14,167 / 54,341 | 26.07 | 1,402 / 17,596 | 7.97 |
| runs | 2,095 / 4,305 | 48.66 | 276 / 1,454 | 18.98 |

Other context numerators in the earlier sample table are unchanged. These values
are source-linked observations, not a full-suite threshold proposal. Complete
selection disposition plus real-dependency/platform evidence is the explicit exit
condition; D5 remains pending rather than inventing a ratchet from these samples.

## 2026-10-19 full Linux selection observation

Status: complete Linux selector inventory executed; required acceptance remains
incomplete. The source baseline is `f853a8a8cdbc4542319d8c244cefad9fb54aaeec`.
The repaired baseline preserves every app source file and overlays four files:
`tools/release_backend_flatten.py`, `tests/test_release_backend_flatten.py`,
`tests/test_sandbox_container_provider.py`, and `tests/test_run_performance_postgres.py`.
These repairs address archive replacement detection/fixtures, provider test-state
isolation, and the synthetic model-revision foreign key. It is not an untouched-main
passing claim.

The 344-file selection ran on s72 in isolated test containers with Python 3.13.14
and locked dependencies, real PostgreSQL/Redis/MinIO, no Docker socket, and no
production data access: **10,620 passed, 12 skipped, zero failures**, 1,331.77 seconds.
Evidence is in runner stage `20261019T090554-8-e93495bf/base-fixed`; its app/tests
source and report bindings remain in the original evidence envelope. The 466-file
app denominator includes unexecuted files.

| Scope under this Linux selection | Covered / executable statements | Statement % | Covered / possible branches | Branch % |
| --- | ---: | ---: | ---: | ---: |
| All app files | 49,266 / 55,437 | 88.87 | 14,076 / 18,000 | 78.20 |
| runs | 4,173 / 4,573 | 91.25 | 1,182 / 1,528 | 77.36 |
| skills | 3,156 / 3,574 | 88.30 | 1,037 / 1,340 | 77.39 |
| sandbox | 1,595 / 1,870 | 85.29 | 568 / 716 | 79.33 |
| files | 499 / 651 | 76.65 | 97 / 166 | 58.43 |
| identity | 515 / 593 | 86.85 | 88 / 114 | 77.19 |
| streaming | 2,564 / 2,839 | 90.31 | 642 / 854 | 75.18 |
| execution | 3,510 / 3,992 | 87.93 | 1,108 / 1,460 | 75.89 |

Owner rows use `app/<owner>/`, as in the earlier samples; they do not silently
assign legacy root modules to a context. Each percentage is computed from the
separate integer totals, not the combined terminal coverage percentage.

Ten Windows-only cases also passed with coverage on the same repaired f853
source, stage `20261019T125750-11508-54f36f7e/win-base`. Its app/tests source and
runner bindings match the Linux report; both have the same 466 files, 55,437
statements and 18,000 branches. Unioning executable line and branch identities
adds 16 covered statements and 9 branches: **49,282 / 55,437 (88.90%) statements**
and **14,085 / 18,000 (78.25%) branches**. Percentages are not added or averaged.
The context rows above are unchanged by this supplement.

The network-guard case passed on the exact f853 GitHub backend run
`39205219808`, job `117906583655`; its coverage is not included in that union.
The real governed OpenSandbox credential-isolation case remains unexecuted:
the existing deployment is internal-test/bridge, and the isolated privileged
test-environment request was cancelled. The union is an observation of the
available runs, not a completed required no-skip baseline or an adopted floor.

After the 2026-10-20 main sync, the candidate has 348 test files, each with one
complete-file CI owner; Windows platform nodes are additional coverage. The earlier
346-file candidate's first Linux execution reported
**10,632 passed, 58 failed, 12 skipped**. The identified fixture/caller and hook
repairs have local owning checks, but candidate Linux full-suite verification has
not been repeated after those changes. Updating the remote test directory hit an
ownership denial and the follow-up test-owner request was cancelled. No current
coverage floor or passed ratchet is claimed from that failed candidate run.

T1 is now a separate 800-line new-production-file gate based on f853 measurements:
371/411 backend modules and 398/419 frontend modules already fit it. Existing
production moves keep their prior identity; existing frozen-growth checks and
base-owned exception controls remain. T2 and local hooks have owning positive and
negative tests. Trusted coverage comparison, independent review, real Sandbox
acceptance, and three complete passing rounds remain pending.

## Refresh and repair

The author of a relevant source migration refreshes the inventory and explains
changed denominators/edges in the PR. Review follows the normal
[delivery workflow](agent-rules/github-issue-pr-workflow.md); generated output cannot
approve its own checker or relax required gates. The changed-document checker runs
in candidate backend preflight against a full comparison SHA. Its link
negative tests and author-triggered repair procedure live with
[local test execution](agent-rules/local-test-execution.md#documentation-gardening).
T9 candidate source validation is implemented; independent review and observed CI
remain pending. The check does not replace deletion/rename consumer inventory,
validate external URLs, or establish semantic document freshness.

## 2026-10-18: invocation-scoped input materialization candidate

The Context file materializer validates the whole requested set in a private
sibling staging directory before publishing any input bytes. Sandbox owns the
anchored filesystem publication helper, supplied by bootstrap composition. Each
published file is a complete same-device hard link created without replacement;
the existing `inputs/` directory and unrelated files retain their identity.
This is per-file atomic publication, not simultaneous whole-batch visibility.

Every long storage, copy, read, and validation operation uses the existing
physical-completion capability, injected by bootstrap together with the batch
factory so Context does not import platform infrastructure. Cancellation and timeout wait for physical
completion before cleanup. Rollback checks the recorded inode identity, preserves
observed replacement files, and reports cleanup failures without replacing the
primary error. No cross-process fence, crash transaction, or guaranteed deletion
under filesystem failure is claimed. Secure publication requires POSIX no-follow
directory descriptors and same-device hard links; unsupported filesystems fail
closed rather than falling back to direct final-file copies.

Retirement: direct final-file copy/write and per-file validation/publication
interleaving are removed. The bytes-backed storage seam remains for identified
worker test doubles; both branches share the batch publisher. Fault injection
moves to private writes/publication. The owning regressions live in the already
selected `test_context_file_continuity.py`; no CI selector is weakened. Storage
API, XLSX predicate, opaque-file admission, ordered metadata, and relative paths
remain compatible. The source-only candidate is not deployment evidence.

Separate follow-up: worker workspace teardown still precedes provider reuse
admission. It must prove the prior consumer is stopped before deleting an existing
workspace. Invocation-local publication does not solve that older lifecycle gap,
and does not add a persistent orphan-stage recovery protocol.
