# Task 1: Distribution Authority Foundation Report

## Status

Implemented the sole tenant capability distribution authority, pure access
resolver, legacy insert-only backfill, and controlled distribution CRUD.

## TDD Evidence

### RED: resolver

Command:

```powershell
python -m pytest tests/test_capability_distribution.py -q -p no:cacheprovider --basetemp .pytest-tmp\capdist-task1-red
```

Result: exit 1, 9 failed. Every failure was the expected missing
`app.capability_distribution` module.

### RED: schema and repository

Command:

```powershell
python -m pytest tests/test_schema.py tests/test_repositories.py -q -p no:cacheprovider -k "capability_distribution" --basetemp .pytest-tmp\capdist-task1-repo-red
```

Result: exit 1, 4 failed. The expected failures were the missing distribution
table and missing repository interfaces.

### GREEN

Commands:

```powershell
python -m pytest tests/test_capability_distribution.py tests/test_schema.py tests/test_repositories.py -q -p no:cacheprovider -k "capability_distribution" --basetemp .pytest-tmp\capdist-task1-green
python -m pytest tests/test_capability_distribution.py tests/test_schema.py tests/test_repositories.py -q -p no:cacheprovider -k "capability_distribution" --basetemp .pytest-tmp\capdist-task1-green-final
```

Results: first GREEN run exited 0 with 13 passed, 158 deselected. Final GREEN
run exited 0 with 14 passed, 158 deselected after adding explicit inherited
`mcp_tool` coverage.

## Implementation

- `app/capability_distribution.py`: pure dataclasses, normalized-role resolver,
  ordered fail-closed decisions, and stable audit payload.
- `app/schema.sql`: authoritative `tenant_capability_distributions` table with
  tenant uniqueness and kind/status/scope checks.
- `app/repositories.py`: two-statement insert-only legacy backfill, normalized
  read projections, and tenant-scoped list/get/upsert/toggle operations.
- Tests cover required resolver denials, admin bypass, schema constraints,
  insert-only/idempotent backfill SQL and bindings, projection normalization,
  and controlled not-found behavior.

## Verification

```powershell
python -m compileall -q app tools scripts
git diff --check
```

Both commands exited 0. No sandbox, release, frontend, deploy, B1/B2/B3, or
211 paths were accessed or modified.

## Self-Review

- [x] No secrets, real environment values, or personal paths in task files.
- [x] Public dataclasses and functions have docstrings.
- [x] Tests include allowed behavior and multiple denial/error behaviors.
- [x] No milestone closure requires a changelog or roadmap update.
- [x] Backfill has exactly two insert-only statements and matching bindings.
- [x] Reads use `tenant_capability_distributions` after lazy backfill.

## Commit

`feat: add capability distribution authority`

## Concerns

No known concerns. This task deliberately establishes authority and resolution
only; route, enqueue, and worker consumers remain later-task work.

## Review Fix: Distribution Authority Tightening

Two Important review findings were closed in the Task 1 boundary:

- `mcp_tool` decisions now require an explicit non-empty
  `mcp_server:<parent_id>` inheritance source before lifecycle or administrator
  checks can allow the caller-supplied effective distribution.
- Both tenant-mapped and built-in public Skill backfill branches now require
  `skills.status = 'active'`; existing placeholder bindings are unchanged and
  asserted exactly.

### RED: MCP tool inheritance

Command:

```powershell
python -m pytest tests/test_capability_distribution.py -q -p no:cacheprovider -k "mcp_tool" --basetemp .pytest-tmp\capdist-task1-review-inheritance-red
```

Observed output:

```text
.FFFF                                                                    [100%]
4 failed, 1 passed, 9 deselected in 0.15s
```

All four failures returned `allowed` instead of the expected
`distribution_inheritance_missing` for missing, blank, whitespace-only, or
non-MCP inheritance sources.

### GREEN: MCP tool inheritance

Command:

```powershell
python -m pytest tests/test_capability_distribution.py -q -p no:cacheprovider -k "mcp_tool" --basetemp .pytest-tmp\capdist-task1-review-inheritance-green
```

Observed output:

```text
.....                                                                    [100%]
5 passed, 9 deselected in 0.07s
```

### RED: active Skill backfill

Command:

```powershell
python -m pytest tests/test_repositories.py -q -p no:cacheprovider -k "capability_distribution_backfill" --basetemp .pytest-tmp\capdist-task1-review-skill-backfill-red
```

Observed output:

```text
F                                                                        [100%]
assert 0 == 2
1 failed, 138 deselected in 0.88s
```

The failing count proved that neither Skill backfill branch filtered on the
global active catalog status.

### GREEN: active Skill backfill

Command:

```powershell
python -m pytest tests/test_repositories.py -q -p no:cacheprovider -k "capability_distribution_backfill" --basetemp .pytest-tmp\capdist-task1-review-skill-backfill-green
```

Observed output:

```text
.                                                                        [100%]
1 passed, 138 deselected in 0.31s
```

### Final focused verification

Command:

```powershell
python -m pytest tests/test_capability_distribution.py tests/test_schema.py tests/test_repositories.py -q -p no:cacheprovider -k "capability_distribution" --basetemp .pytest-tmp\capdist-task1-review-green
```

Observed output:

```text
..................                                                       [100%]
18 passed, 158 deselected in 0.35s
```

Additional verification:

```powershell
python -m compileall -q app tools scripts
git diff --check
```

Both commands exited 0 with no output. The focused fix commit subject is
`fix: tighten capability distribution authority`.

### Review Fix Concerns

No known Task 1 concerns. Resolving each MCP tool to its parent server row and
supplying the effective inherited distribution remains explicitly scoped to
Task 4.
