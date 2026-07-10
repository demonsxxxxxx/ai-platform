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
