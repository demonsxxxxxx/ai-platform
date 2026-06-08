# ai-platform G9 Observability Readiness

Date: 2026-06-08

This document records the current G9 Observability / Quality / Ops readiness
baseline. It is an operator readiness snapshot, not a gate-closure claim. G9
remains partial until latency percentiles, model-gateway pressure controls,
recorded capacity load-test evidence, error taxonomy dashboard acceptance,
golden-set evaluation, alert/SLO runtime acceptance, and trace/audit export
contracts have code, tests, docs, review, and runtime evidence.

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
| Alerts and exports | Admin Runtime overview projection, fail-closed capacity gate readiness verdict, and source-level `ai-platform.alert-slo-readiness.v1` rule template evidence for queue, database, worker, model gateway, sandbox, error-taxonomy, and capacity gates | Alert runtime dashboard and 211 acceptance, delivery-channel policy, runtime SLO calibration, trace/audit export contract, release evidence export location |

## Alert / SLO Rule Template Baseline

The source-level readiness snapshot now embeds
`ai-platform.alert-slo-readiness.v1` as
`observability_readiness.domains.alerts_and_exports.evidence.alert_slo_rules`.
This is a template-only baseline. It does not enable alert delivery, does not
raise production concurrency defaults, and does not close G9.

Template rules cover:

- `queue_depth_no_lease_progress`
- `database_pool_waiting_pressure`
- `worker_active_run_saturation`
- `model_gateway_timeout_spike`
- `sandbox_orphan_cleanup_regression`
- `error_taxonomy_spike`
- `capacity_load_evidence_missing`

Remaining alert blockers are runtime dashboard wiring, delivery-channel policy,
SLO threshold calibration from recorded runtime evidence, review, and 211 smoke.

## 211 Runtime Evidence - 2026-06-08

Commit `22dc9e61605d406f10669e4f91f4cb1a87e2094d` was synced to the 211
repo-local source target and deployed to API/worker with image
`ai-platform:22dc9e6-g9-readiness-taxonomy`. The image was built from the
repo-local services source target with OCI labels:

- `org.opencontainers.image.revision =
  22dc9e61605d406f10669e4f91f4cb1a87e2094d`
- `org.opencontainers.image.source = ai-platform/services/ai-platform`
- `ai-platform.source_target = services/ai-platform`

211 smoke evidence after deployment:

- `GET /api/ai/health` returned `{"status":"ok"}` from the API container.
- The 211 frontend proxy health path returned `{"status":"ok"}`.
- API and worker containers both ran
  `ai-platform:22dc9e6-g9-readiness-taxonomy` with matching revision labels.
- Container-local `python -m compileall -q app` passed for both API and worker.
- Admin Runtime overview returned the required operational sections:
  `capacity`, `database_pool`, `queue`, `admission`, `backpressure`,
  `sandbox`, `governance`, `observability`, and
  `observability_readiness`.
- `observability_readiness.schema_version` was
  `ai-platform.observability-readiness.v1`.
- `observability_readiness.error_taxonomy.schema_version` was
  `ai-platform.error-taxonomy.v1`.
- `observability.error_categories` was present as a dictionary.
- Ordinary-user access to `GET /api/ai/admin/runtime/overview` returned
  HTTP 403.
- A refined projection leak scan found no forbidden runtime markers for DSNs,
  Redis URLs, bearer/API tokens, raw storage keys, sandbox work directories,
  executor private payloads, callback tokens, provider tokens, client secrets,
  or object-storage keys. Guardrail text that says not to expose raw storage
  keys or private payloads was treated as documentation, not runtime data.
- Recent API/worker logs since deployment had no startup traceback, exception,
  error, failed import, pydantic, or syntax-error markers.

This runtime evidence proves the source-level G9 taxonomy/readiness projection
is deployed on 211. It does not close G9 because the remaining latency
percentiles, model-gateway concurrency/backpressure, recorded load-test
evidence, taxonomy dashboard acceptance, golden-set evaluation, alert runtime
acceptance, and export contracts are still open.

### Follow-up 211 Runtime Evidence - 2026-06-08, commit `be03c95`

Commit `be03c953e60489f1d27b8e6d1a0a770f11e48fb8` was later synced to the 211
repo-local source target and deployed to API/worker with image
`ai-platform:be03c95-frontend-provenance`. The image labels matched the same
commit and retained:

- `org.opencontainers.image.source = ai-platform/services/ai-platform`
- `ai-platform.source_target = services/ai-platform`

211 smoke evidence after deployment:

- `GET /api/ai/health` returned `{"status":"ok"}` from the API container.
- The 211 frontend proxy health path returned `{"status":"ok"}`.
- API and worker containers both ran
  `ai-platform:be03c95-frontend-provenance` with matching revision labels.
- Container-local `python -m compileall -q app` passed for both API and worker.
- Admin Runtime overview returned the required operational sections:
  `capacity`, `database_pool`, `queue`, `admission`, `backpressure`,
  `sandbox`, `governance`, `observability`, and
  `observability_readiness`.
- `capacity.schema_version` was `ai-platform.capacity-baseline.v1`, and
  `capacity.production_default_policy` remained
  `do_not_raise_without_recorded_load_test_evidence`.
- `governance.schema_version` was `ai-platform.governance-readiness.v1`.
- `observability_readiness.schema_version` was
  `ai-platform.observability-readiness.v1`.
- `observability_readiness.error_taxonomy.schema_version` was
  `ai-platform.error-taxonomy.v1`.
- `observability.error_categories` was present as a dictionary.
- Ordinary-user access to `GET /api/ai/admin/runtime/overview` returned
  HTTP 403.
- A refined projection leak scan found no forbidden runtime markers for DSNs,
  Redis URLs, bearer/API tokens, raw storage keys, sandbox work directories,
  executor private payloads, callback tokens, provider tokens, client secrets,
  or object-storage keys.
- Recent API/worker logs since deployment had no startup traceback, exception,
  error, failed import, module-not-found, or syntax-error markers.

This follow-up evidence proves the latest deployed main commit still exposes
the G9 readiness and error-taxonomy projections safely on 211. It still does
not close G9 because latency percentiles, model-gateway concurrency and
backpressure controls, recorded load-test evidence, dashboard acceptance,
golden-set evaluation, alert runtime acceptance, and export contracts remain
open.

## Gate Rule

Do not close G9 or raise production concurrency defaults from this readiness
projection alone. It makes missing observability work machine-readable and
visible in Admin Runtime, but it does not replace recorded load-test evidence,
golden-set evaluation, alert validation, taxonomy dashboard acceptance, or 211
deployment smoke.

Do not use this baseline to expand sandbox privilege, expose ordinary users to
multi-agent beta, or bypass G6/G7 governance gates.
