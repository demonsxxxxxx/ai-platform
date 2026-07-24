# PR Code Governance v1

## Purpose And Scope

This rule keeps ai-platform changes issue-sized and makes growth in known hot
files explicit. Phase 2A adds a local, deterministic evaluator without changing
product behavior, deployment contracts, runtime behavior, dependencies, CI, or
repository root configuration.

The module seam is intentionally small:

```text
python tools/code_governance.py check \
  --base-ref <full-40-hex-commit> \
  --head-ref <full-40-hex-commit> \
  --format text|json
```

Callers supply two exact commits and consume one result. The module hides Git
diff parsing, rename/delete handling, path roles, subsystem classification, line
counts, policy limits, exception validation, changed-file Ruff execution, stable
rendering, and exit codes. This is the code-governance seam; callers must not
reimplement these rules.

Exit codes are part of the interface:

- `0`: every active Phase 2A gate passed; any exception matched exactly.
- `2`: the range was evaluated and has one or more active violations.
- `3`: the request or environment could not be evaluated, including an invalid
  ref/range, Git failure, malformed exception contract, or malformed CLI input.

Both refs must be full 40-hex commit IDs. The base must be an ancestor of the
head. JSON output uses schema `ai-platform.code-governance-report.v1` and sorted
keys, paths, commands, and violations so identical repository state produces
identical output.

## Enforced Phase 2A Policy

The evaluator classifies changed paths as behavior-changing production,
move-only production, test, or non-production. A delete is behavior-changing
for file/subsystem accounting but contributes negative net LOC and does not need
a new test mirror. A pure Git rename with zero additions and deletions is
move-only; it is reported separately and excluded from behavior file, net-LOC,
subsystem, and mirror gates. A rename with content changes is a behavior change.

Default policy:

| Gate | Limit |
| --- | --- |
| Behavior-changing production files | `<= 12` |
| Net behavior-changing production LOC | `< 800` |
| Normal production subsystems | `< 2` |
| Production file above 1500 lines | `<= 100` net growth |
| Functional production file above 3000 lines | `<= 0` net growth |
| Test file above 2500 lines | `<= 100` net growth |

The line threshold applies when either the base or head version is above the
threshold. Functional source covers Python, TypeScript/JavaScript, shell, SQL,
CSS, Go, Rust, Java, C, and C++ suffixes. Documentation, common image assets,
tests, and the exception contract are not production paths.

For this repository, a production subsystem is deliberately conservative:

- `app/runtime/sandbox/*` is one subsystem;
- another `app` directory such as `app/routes/*` or `app/executors/*` is one
  subsystem;
- `frontend/web/src/<directory>/*`, `deploy/<directory>/*`, and
  `skills/<directory>/*` each form one subsystem;
- other paths use their top-level directory; root files are separate root
  responsibilities.

Because normal work is `< 2` subsystems, a behavior-changing range normally
stays within one subsystem. Cross-subsystem work requires an exact exception or
an explicitly narrower decomposition.

### Test Responsibility Mirror

A functional production file with added lines must have a changed test whose
responsibility stem mirrors the production stem. Examples:

- `app/queue_payload_validation.py` -> `tests/test_queue_payload_validation.py`
- `app/runtime/sandbox/container_provider.py` ->
  `tests/test_sandbox_container_provider.py`
- `frontend/web/src/sessionActions.ts` -> a changed `sessionActions.test.ts`

The rule is about responsibility, not directory symmetry. The evaluator
normalizes `test_`, `_test`, `.test`, `.spec`, and punctuation, then requires an
equal or clearly containing stem. A production delete or a no-addition change
does not create a new mirror obligation. This prevents a hotspot from growing
through an unrelated omnibus test while allowing established repository naming.

### Changed-Python Ruff Gate

For every added, copied, renamed, or modified Python destination in the range,
the evaluator constructs one sorted command:

```text
python -m ruff check -- <changed-python-paths...>
```

No changed Python path means the gate is not applicable. When Python paths are
present, Ruff must be importable by the active Python interpreter and the
command must exit zero. Missing Ruff is fail-closed and is not exceptionable.
Phase 2A intentionally does not add Ruff as a dependency or change
`pyproject.toml`; the invoking environment owns tool provisioning.

## Strict Versioned Exception Contract

An exception is optional and lives at the exact head commit as
`.code-governance-exception.json`. The filename is fixed. The only accepted
schema is `ai-platform.code-governance-exception.v1`:

```json
{
  "schema_version": "ai-platform.code-governance-exception.v1",
  "expires_on": "2026-08-15",
  "owner": "named-team-or-owner",
  "reason": "specific bounded reason",
  "violations": [
    {
      "code": "test_responsibility_mirror",
      "path": "app/example.py"
    },
    {
      "code": "production_subsystem_count",
      "path": null
    }
  ]
}
```

Validation is exact and fail-closed:

- top-level keys and each violation key must match the schema exactly;
- schema version, non-empty owner/reason, non-expired ISO date, non-empty unique
  violation entries, and POSIX paths are required;
- every requested `(code, path)` must match a current violation exactly;
- unused, stale, duplicate, expired, malformed, or unknown exception entries
  make evaluation exit `3`;
- `ruff_unavailable` and `ruff_failed` cannot be excepted.

The contract grants no standing waiver. Its expiry and exact matching ensure a
future clean range cannot silently inherit an obsolete exception.

## Phase Status And Verification

| Phase | State | Evidence / next gate |
| --- | --- | --- |
| 2A evaluator and CLI | implemented locally | `tools/code_governance.py` |
| 2A policy tests | implemented locally | focused `tests/test_code_governance.py` |
| 2A governance rule | this document | exact interface, limits, exceptions, reserved gates |
| 2A CI wiring | intentionally out of scope | Phase 2B issue-sized integration |
| 2B typed payload gate | reserved, not enforced | define authoritative typed payload inventory and false-positive model |
| 2B taxonomy gate | reserved, not enforced | define allowed constructors/categories and compatibility policy |
| 2B release-authority seam | pending | first reversible split with focused contract tests; no behavior/deploy change |
| 2B later hotspots | pending | separate issues for `container_provider`, `worker`, Claude adapter, repositories |

Phase 2A is verified with the narrowest local evidence:

```text
python -m compileall -q app tools scripts
python -m pytest tests/test_code_governance.py -q --basetemp .pytest-tmp/<fresh-child>
python tools/code_governance.py check --base-ref <full-base> --head-ref <full-head> --format json
git diff --check <full-base>..HEAD
git diff --name-only <full-base>..HEAD
```

The CLI smoke exits `2` when Ruff is not installed and Python paths changed,
because the gate is fail-closed. That is policy evidence, not a product/runtime
failure. A Ruff-provisioned current-main integration environment must rerun the
same exact range before PR readiness.

## Phase 2B Issue-Sized Route

Phase 2B must preserve the same small interface and add rules behind the seam,
not expose classification mechanics to CI or callers.

1. Provision and wire the evaluator in CI with exact base/head SHAs. Keep Ruff
   availability separate from rule logic and prove exit `0/2/3` handling.
2. Define and enforce typed payload constraints from the authoritative queue,
   callback, executor, and projection contracts. Do not enforce string-search
   approximations.
3. Define and enforce error-taxonomy construction from the authoritative
   taxonomy module, including approved compatibility adapters. Do not report
   documentation occurrences as code violations.
4. Split `tools/release_authority.py` and its focused tests at one deep seam:
   keep CLI/orchestration as caller interface and move one coherent policy or
   evidence responsibility behind it. Preserve command behavior, report schema,
   release/deployment contracts, and recovery runbook wording; verify through
   the existing interface before and after the move.
5. Open separate issue-sized governance slices for
   `app/runtime/sandbox/container_provider.py`, `app/worker.py`,
   `app/executors/claude_agent_worker.py`, and `app/repositories.py`. For each,
   first identify one responsibility seam and its mirrored tests, then perform a
   reversible move-only extraction before any behavior change.

No Phase 2B item is implied complete by this document. In particular, typed
payload and taxonomy fields in the report are explicit `phase_2b_not_enforced`
reservations, never a claim of enforcement.
