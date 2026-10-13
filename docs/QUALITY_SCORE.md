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

## Refresh and repair

The author of a relevant source migration refreshes the inventory and explains
changed denominators/edges in the PR. Review follows the normal
[delivery workflow](agent-rules/github-issue-pr-workflow.md); generated output cannot
approve its own checker or relax required gates. The changed-document checker runs
in candidate backend preflight against a full comparison SHA. Its link/status/index
negative tests and author-triggered repair procedure live with
[local test execution](agent-rules/local-test-execution.md#documentation-gardening).
T9 candidate source validation is implemented; independent review and observed CI
remain pending. The check does not replace deletion/rename consumer inventory,
validate external URLs, or establish semantic document freshness.
