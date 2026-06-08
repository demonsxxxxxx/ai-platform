# ai-platform G9 Observability Readiness

Date: 2026-06-08

This document records the current G9 Observability / Quality / Ops readiness
baseline. It is an operator readiness snapshot, not a gate-closure claim. G9
remains partial until latency percentiles, model-gateway pressure controls,
recorded capacity load-test evidence, error taxonomy dashboard acceptance,
golden-set evaluation, alert thresholds, and trace/audit export contracts have
code, tests, docs, review, and runtime evidence.

Generate the current readiness snapshot from the repository root:

```powershell
python tools/observability_readiness.py --format markdown
python tools/observability_readiness.py --format json
```

The script intentionally does not print callback tokens, sandbox workspace
roots, gateway API keys, raw queue keys, object-storage internal identifiers,
executor-private data, raw runtime paths, real `.env` values, or secret-like
configuration.

## Admin Runtime Signal Path

Admins consume the live operational projection at:

```text
GET /api/ai/admin/runtime/overview
```

Current source exposes:

- `observability`: same-tenant token, cost, latency average/max, error count,
  artifact count, sanitized error type counts, public error taxonomy category
  counts, and sanitized recent failure aggregates.
- `capacity`: configured capacity ceiling and missing load-test gates.
- `database_pool`, `queue`, `admission`, and `backpressure`: bounded runtime
  pressure signals used by the #21 capacity evidence snapshot.
- `governance`: G6 Tool / Skill / Memory governance readiness.
- `observability_readiness`: G9 readiness domains, implemented controls, open
  gaps, and next checks.

All projections are admin-only, same-tenant, and designed as public/admin
operational projections. They must not become a path for executor-private data,
raw storage keys, sandbox work directories, gateway secrets, raw `.env` values,
or ordinary-user private content.

## Current G9 Status

| Domain | Implemented baseline | Remaining gap |
| --- | --- | --- |
| Runtime metrics | Admin Runtime observability summary, token/cost/latency/error counts, queue/admission/DB-pool backpressure summary, capacity runtime evidence capture | p50/p95/p99 latency for API, queue lease, worker, model, sandbox, artifact, cancel, retry, and resume; model-gateway request concurrency limit; recorded capacity load-test evidence |
| Error taxonomy | Formal `ai-platform.error-taxonomy.v1` contract, category mapping for executor/tool/sandbox/model-gateway/queue/database/memory/artifact/auth failures, Admin Runtime `error_categories`, run event error count projection, and redacted recent failure projection | Dashboard acceptance and 211/runtime evidence for taxonomy-driven operations |
| Quality evaluation | Run trace/audit linkage baseline | Golden-set eval run contract, quality score schema, office workflow acceptance dataset |
| Alerts and exports | Admin Runtime overview projection and fail-closed capacity gate readiness verdict | Alert rules and SLO thresholds, trace/audit export contract, release evidence export location |

## Gate Rule

Do not close G9 or raise production concurrency defaults from this readiness
projection alone. It makes missing observability work machine-readable and
visible in Admin Runtime, but it does not replace recorded load-test evidence,
golden-set evaluation, alert validation, taxonomy dashboard acceptance, or 211
deployment smoke.

Do not use this baseline to expand sandbox privilege, expose ordinary users to
multi-agent beta, or bypass G6/G7 governance gates.
