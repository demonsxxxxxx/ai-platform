# ai-platform Gate Status Snapshot

Date: 2026-06-11

This snapshot keeps the current PRD, foundation roadmap, guardrails, repository
code, 211 runtime, and issue-driven priorities aligned. It is not automatic
gate-closure evidence and must not be used to auto-close GitHub issues. Gate
closure still requires the
issue -> PR -> review -> merge -> 211 deploy/smoke -> close issue workflow in
`docs/agent-rules/github-issue-pr-workflow.md`.

## Foundation Alpha POC Smoke

Status: `211 verified for Foundation Alpha POC`, not production gate closure.

On 2026-06-11, the 211 API and worker were running
`ai-platform:3874281-foundation-alpha-poc` with source revision
`3874281276c84a418bd08bda56d7ea55b52970b7`. Runtime labels pointed to the
repo-local compose file under
`/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform`,
API health returned `ok`, and OpenAPI exposed
`/api/ai/artifacts/{artifact_id}/preview`.

The aggregate verifier `tools/verify_poc_gate.py` returned `ok: true` on 211
for the controlled POC loop: LambChat thin-shell frontend, same-origin API
health, public/admin projection boundary, company auth bridge, general chat run,
document review attachment run, artifact download isolation, artifact preview
isolation, playback with preview URL and no private payload leakage, company
login audit, and Admin capacity/backpressure fields. The reviewed, redacted
release-evidence entry is
`docs/release-evidence/foundation-alpha-poc/3874281276c84a418bd08bda56d7ea55b52970b7/2026-06-11-211-foundation-alpha-poc-smoke.json`.

This smoke does not close #21 capacity, G7 Docker sandbox hardening, ordinary
user multi-agent exposure, department rollout, release-evidence runtime export,
release-evidence retention runtime acceptance, or packaged frontend image
release acceptance.

## Current Gate Status

| Gate | Current status | Evidence now in repository | Remaining blocker before closure |
| --- | --- | --- | --- |
| G0-G1 Source Authority / Security Baseline | Foundation Alpha POC has fresh 211 source/deploy/runtime-label parity and company-login audit evidence; keep under regression. | PRD v2, tech acceptance matrix, roadmap, guardrails, source-authority tests, repo-local compose context, frontend source migration, redacted deploy templates, and 2026-06-11 POC release evidence. | Full issue/PR/review closure path and broader auth/session/RBAC/tenant/redaction regression are still required before production closure. |
| G2-G4 Control Plane MVP | Substantial coverage; keep under regression. | Session/run/file/artifact/skill/tool/memory/event/audit contracts, repositories, routes, schema indexes, and focused tests. | Full regression before PR/deploy, plus no executor-owned platform schema drift. |
| G5 Run Lifecycle / Worker Runtime V1 | Foundation Alpha POC verified queue/run/worker execution and Admin capacity/backpressure projection; not capacity-closed. | Tenant-aware queue lease, worker maintenance, active-run admission, bounded metadata, Admin Runtime capacity/backpressure projection, #20 roadmap closure notes, and 2026-06-11 POC verifier evidence. | #21 remains open: large queue bounded lookup pressure, multi-tenant load, and recorded seven-gate load evidence are still missing. Production defaults stay unchanged. |
| G6 Tool / Skill / Memory Governance | POC governance baseline is visible, but ordinary-user expansion remains blocked. | Tool policy taxonomy/history, public permission-card projection, skill release/dependency policy contracts, memory delete/retention/redaction/export readiness, office context-pack architecture readiness, governance readiness CLI, and POC runs using governed skills. | Legacy frontend route remap/policy enforcement, signed package or SBOM review evidence, dependency vulnerability/license evidence, runtime/Admin dashboard acceptance, and broader 211 acceptance. |
| G7 Sandbox / Resource Hardening | Blocked for high-risk expansion. | Fake provider remains local/test-only; capacity docs expose sandbox limits and missing hardening warnings. | Docker provider hardening, egress/quota policy, orphan cleanup, container security options, and Docker-capable 211 smoke. |
| G8 Multi-Agent Controlled Beta | Feature-flagged only. | Dispatcher and child-run admission work exists behind current controls. | Tenant-aware scheduling quota/backpressure, #21 capacity evidence, observability, sandbox, and tool governance gates must pass before ordinary-user exposure. |
| G9 Observability / Quality / Ops | Foundation Alpha POC release evidence exists; G9 remains partial. | Admin Runtime overview, capacity/governance/observability readiness docs and tools, error taxonomy/dashboard contracts, release-evidence contracts, trace/audit export contracts, frontend projection audit, and reviewed 211 POC smoke entry. | Runtime dashboard acceptance, recorded capacity evidence, model-gateway backpressure evidence, golden-set eval runtime, alert delivery/runtime calibration, trace/export 211 acceptance, and release-evidence runtime export/retention acceptance. |
| G10 Internal Beta / Department Rollout | Blocked. | Candidate internal workflows are named only as examples in roadmap. | Select 1-2 real internal workflow owners, complete prior gates, record cost/quality/audit/rollback evidence, and pass 211 acceptance. |

## Issue-Driven Thin Spots

| Issue area | Current judgment | Next closure action |
| --- | --- | --- |
| #17 frontend source migration | Source lives under `frontend/web` with projection audit, `ci:verify`, release traceability, GitHub Actions workflow, packaged frontend image definition, and 211 thin-shell POC smoke. | Run or refresh frontend install/lint/build when changing browser code; complete packaged frontend image smoke/release acceptance on 211 or another Docker-capable host. |
| #21 capacity baseline | Baseline plan, snapshot/verdict/profile tools, bounded probe harness, and Admin Runtime capacity/backpressure visibility exist. | Record approved load evidence for the seven gates before raising any production default. Until then every profile remains `do_not_raise_without_recorded_load_test_evidence`. |
| G6 governance | Source-level policies and readiness contracts exist. | Convert contracts into runtime/Admin dashboard acceptance and real reviewed Skill release evidence; keep long-term memory fail-closed. |
| G8/G10 expansion | Not a current implementation target. | Keep feature flags and do not broaden ordinary-user multi-agent exposure until G5/G6/G7/G9 gates are closed. |

## Frontend Projection Boundary

Browser-side code must consume same-origin ai-platform public or same-tenant
admin projections only. It must not read executor private payloads, raw storage
keys, sandbox work directories, raw runtime paths, secret-like values, raw
request payloads, raw decision payloads, or raw Skill staging paths. The active
projection audit and `frontend/web` CI gate are the repository-owned checks for
this boundary.

## Capacity Decision Boundary

Current defaults must not be increased without recorded load-test evidence.
The capacity tooling may generate baselines, dry-run plans, bounded probes,
evidence bundles, and fail-closed verdicts, but probes are not accepted as
recorded gate evidence until an operator-reviewed recorded gate snapshot
contains measured results, cleanup proof, stop-condition status, and deployed
commit binding.
