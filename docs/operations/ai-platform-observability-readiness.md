# ai-platform G9 Observability Readiness

Date: 2026-06-09

This document records the current G9 Observability / Quality / Ops readiness
baseline. It is an operator readiness snapshot, not a gate-closure claim. G9
remains partial until latency percentile per-surface split and dashboard
acceptance, model-gateway pressure controls and recorded capacity load-test
evidence, error taxonomy dashboard acceptance, golden-set evaluation runtime
and 211 acceptance, alert/SLO runtime acceptance, trace/audit export runtime,
dashboard, and 211 acceptance, release evidence runtime export acceptance, and
release evidence retention runtime acceptance have code, tests, docs, review,
and runtime evidence.

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

- `observability`: same-tenant token, cost, latency average/max and latency
  percentiles p50/p95/p99, error count, artifact count, sanitized error type
  counts, public error taxonomy category counts, and sanitized recent failure
  aggregates.
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
| Runtime metrics | Admin Runtime observability summary, token/cost/latency/error counts, `latency_percentiles_p50_p95_p99_admin_projection`, queue/admission/DB-pool backpressure summary, config-visible model-gateway request limit status, capacity runtime evidence capture | `latency_percentile_per_surface_split_and_dashboard_acceptance` across API, queue lease, worker, model, sandbox, artifact, cancel, retry, and resume; enforced model-gateway request-limit/backpressure gate plus recorded model-gateway load-test evidence; recorded capacity load-test evidence |
| Error taxonomy | Formal `ai-platform.error-taxonomy.v1` contract, category mapping for executor/tool/sandbox/model-gateway/queue/database/memory/artifact/auth failures, Admin Runtime `error_categories`, run event error count projection, and redacted recent failure projection | Dashboard acceptance and 211/runtime evidence for taxonomy-driven operations |
| Quality evaluation | Run trace/audit linkage baseline, source-level `ai-platform.quality-golden-set-readiness.v1` contract, and `ai-platform.quality-score.v1` score schema | Golden-set evaluation runtime and 211 acceptance, office workflow acceptance dataset, quality threshold calibration, dashboard acceptance |
| Alerts and exports | Admin Runtime overview projection, fail-closed capacity gate readiness verdict, source-level `ai-platform.alert-slo-readiness.v1` rule template evidence for queue, database, worker, model gateway, sandbox, error-taxonomy, and capacity gates, source-level `ai-platform.alert-delivery-channel-policy.v1` as `alert_delivery_channel_policy_contract`, source-level `ai-platform.trace-audit-export-readiness.v1` as `trace_audit_export_contract`, plus source-level `ai-platform.release-evidence-readiness.v1` export-location contract and `ai-platform.release-evidence-retention-policy.v1` retention policy contract | Alert runtime dashboard and 211 acceptance, `alert_delivery_channel_runtime_acceptance`, runtime SLO calibration, `trace_audit_export_runtime_acceptance`, `trace_audit_export_dashboard_acceptance`, `trace_audit_export_211_acceptance`, `release_evidence_runtime_export_acceptance`, and `release_evidence_retention_runtime_acceptance` |

## Quality Golden-Set Contract Baseline

The source-level readiness snapshot now embeds
`ai-platform.quality-golden-set-readiness.v1` as
`observability_readiness.domains.quality_evaluation.evidence.quality_golden_set`.
This is a contract-only baseline. It does not enable eval runtime, does not read
ordinary-user private content, does not expose executor-only data, and does not
close G9.

The nested evidence contract is
`ai-platform.golden-set-eval-evidence-contract.v1` and records eval evidence at:

```text
quality_evaluation.golden_set_runs.<eval_run_id>
```

It defines five source-level scenario categories for office document revision,
meeting follow-up, terminology-preserving translation, SOP/RAG grounded answer,
and file-task artifact review. It also defines required score dimensions:
`task_success`, `instruction_following`, `context_grounding`,
`artifact_quality`, and `safety_and_redaction`.

Required eval evidence fields include commit and dataset version, scenario ID,
eval run ID, evaluator version, sample/pass/fail counts, score summaries,
dimension scores, public context provenance, public artifact references,
redaction scan status, review status, and review timestamp.

The golden-set evaluation runtime and 211 acceptance remain open. The contract
does not close G9; it only prevents the quality/eval gate from staying vague
while runtime execution, dataset approval, threshold calibration, dashboard
acceptance, review, and smoke evidence are still missing.

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

The same snapshot embeds the contract-only
`ai-platform.alert-delivery-channel-policy.v1` as
`alert_delivery_channel_policy_contract`. Allowed channels are
`admin_runtime_dashboard`, `release_evidence_entry`, and
`operator_manual_review`; ordinary-user delivery remains
`disabled_until_g9_acceptance`. Payloads are limited to category, threshold,
and public projection references, and must not include executor-only data, raw
storage identifiers, sandbox runtime paths, or secret-like values.

Remaining alert blockers are runtime dashboard wiring,
`alert_delivery_channel_runtime_acceptance`, SLO threshold calibration from
recorded runtime evidence, review, and 211 smoke.

## Trace / Audit Export Contract Baseline

The source-level readiness snapshot now embeds
`ai-platform.trace-audit-export-readiness.v1` as
`observability_readiness.domains.alerts_and_exports.evidence.trace_audit_export`.
Generate the standalone contract from the repository root:

```powershell
python tools/trace_audit_export_readiness.py --format markdown
python tools/trace_audit_export_readiness.py --format json
```

The nested export contract is
`ai-platform.trace-audit-export-contract.v1` and records future reviewed export
evidence at:

```text
audit.trace_exports.<export_id>
```

Allowed event sources are `run_event_public_projection`,
`audit_event_public_projection`, `admin_runtime_observability_summary`, and
`release_evidence_entry`. Required fields include export ID, commit, tenant,
requester, request time, time range, filters, public artifact references,
redaction scan status, and review status.

This is a contract-only baseline. It does not export runtime data, does not
read executor-only data, raw storage identifiers, sandbox runtime paths, or
secret-like values, and does not close G9. Remaining blockers are
`trace_audit_export_runtime_acceptance`,
`trace_audit_export_dashboard_acceptance`, and
`trace_audit_export_211_acceptance`.

## Release Evidence Export Contract Baseline

The source-level readiness snapshot now embeds
`ai-platform.release-evidence-readiness.v1` as
`observability_readiness.domains.alerts_and_exports.evidence.release_evidence`.
Generate the standalone contract from the repository root:

```powershell
python tools/release_evidence_readiness.py --format markdown
python tools/release_evidence_readiness.py --format json
```

The export location is `docs/release-evidence/`, with index
`docs/release-evidence/README.md`. Future reviewed, redacted entries use
`ai-platform.release-evidence-entry.v1` and the write path:

```text
docs/release-evidence/<gate>/<commit_sha>/<evidence_id>.json
```

This is a location, entry-shape, and retention-policy contract only. The
retention policy schema is `ai-platform.release-evidence-retention-policy.v1`.
It sets a 180-day default retention period, a 30-day minimum, and requires
review before deleting only reviewed, redacted evidence entries. It does not
export evidence at runtime, does not store executor-only data, raw storage
identifiers, sandbox runtime paths, or secret-like values, and does not close
G9.

Remaining release-evidence blockers are
`release_evidence_runtime_export_acceptance` and
`release_evidence_retention_runtime_acceptance`.

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
is deployed on 211. It does not close G9 because latency percentile
per-surface split and dashboard acceptance, model-gateway
concurrency/backpressure, recorded load-test evidence, taxonomy dashboard
acceptance, golden-set evaluation, alert runtime acceptance, and export
contracts are still open.

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
not close G9 because latency percentile per-surface split and dashboard
acceptance, model-gateway concurrency and backpressure controls, recorded
load-test evidence, dashboard acceptance, golden-set evaluation, alert runtime
acceptance, and export contracts remain open.

### Follow-up 211 Runtime Evidence - 2026-06-08, commit `a877f59`

Commit `a877f590b3cea611c1cde4b2e78f856597cb1894` was synced to the 211
repo-local source target and deployed to API/worker with image
`ai-platform:a877f59-g9-latency-percentiles`. The image labels matched the
same commit:

- `org.opencontainers.image.revision =
  a877f590b3cea611c1cde4b2e78f856597cb1894`
- `ai-platform.source-revision =
  a877f590b3cea611c1cde4b2e78f856597cb1894`
- `ai-platform.source_target = services/ai-platform`

211 smoke evidence after deployment:

- `GET /api/ai/health` returned `{"status":"ok"}` from the API container.
- API and worker containers both ran
  `ai-platform:a877f59-g9-latency-percentiles` with matching revision labels.
- Container-local `python -m compileall -q app tools scripts` passed for both
  API and worker.
- Admin Runtime overview returned HTTP 200 for an admin trusted principal and
  HTTP 403 for an ordinary user.
- `observability.latency_ms` exposed the allowlisted keys `avg`, `max`, `p50`,
  `p95`, and `p99`.
- `observability_readiness.domains.runtime_metrics.implemented` contained
  `latency_percentiles_p50_p95_p99_admin_projection`.
- A refined projection leak scan found no forbidden runtime markers for DSNs,
  Redis URLs, bearer/API tokens, raw storage keys, sandbox work directories,
  executor private payloads, callback tokens, provider tokens, client secrets,
  or object-storage keys. Guardrail/readiness text that says not to expose
  secret-like data was treated as policy documentation, not runtime data.
- Recent API/worker logs since deployment had no startup traceback, exception,
  error, failed import, pydantic, module-not-found, permission, entrypoint, or
  syntax-error markers.

This follow-up evidence closes the narrower 211 acceptance item for the
source-level latency percentiles p50/p95/p99 admin projection. It still does
not close G9: the remaining runtime-metrics blocker is
`latency_percentile_per_surface_split_and_dashboard_acceptance`, along with
model-gateway concurrency/backpressure enforcement and recorded load-test
evidence.

## Model Gateway Capacity Control Baseline

Current source now includes `MODEL_GATEWAY_REQUEST_CONCURRENCY_LIMIT` as a
config-visible model gateway request concurrency setting. The default `0` keeps
the platform-level limit disabled and keeps
`model_gateway_request_concurrency_limit` as an open G9 runtime-metrics gap.
The readiness snapshot also records
`model_gateway_backpressure_policy_contract` through the source-level
`ai-platform.model-gateway-backpressure-policy.v1` contract-only baseline. That
contract requires Admin Runtime to expose `capacity.limits.model_gateway`,
`backpressure.model_gateway`, and
`observability.error_categories`, and keeps
`model_gateway_timeout_and_backpressure` as the required recorded load-test
gate before any production default decision.
When a target deployment profile sets a positive value, the source-level
readiness snapshot reports that configured signal but still keeps
`model_gateway_request_concurrency_limit`,
`model_gateway_request_concurrency_limit_enforcement`, and
`model_gateway_capacity_load_test_evidence` open. G9 stays partial until the
request path actually enforces model-gateway backpressure and the
timeout/backpressure load-test gate has recorded evidence.

Admin Runtime exposes this as `capacity.limits.model_gateway` and
`backpressure.model_gateway`. The projection carries only provider identity,
the configured request limit, explicit `not_implemented` enforcement state, and
the capacity evidence status. It must not expose model gateway URLs, API keys,
bearer tokens, executor private payloads, storage keys, sandbox work directories, or
real `.env` values.

The model-gateway policy keeps
`production_default_policy = do_not_raise_without_recorded_load_test_evidence`
and does not close G9.

## Gate Rule

Do not close G9 or raise production concurrency defaults from this readiness
projection alone. It makes missing observability work machine-readable and
visible in Admin Runtime, but it does not replace recorded load-test evidence,
golden-set evaluation, alert validation, taxonomy dashboard acceptance, or 211
deployment smoke.

Do not use this baseline to expand sandbox privilege, expose ordinary users to
multi-agent beta, or bypass G6/G7 governance gates.
