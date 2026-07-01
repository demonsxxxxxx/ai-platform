# AI Platform Foundation Alpha Closure

Date: 2026-06-15

Status: Foundation Alpha historical baseline accepted for the `380de6b`
runtime subject.

This note is the compact delivery closure for S1 / Foundation Alpha. It does
not replace the PRD, technical acceptance matrix, gate status snapshot, or
release-evidence records. It points operators to the current accepted baseline
and records the boundaries that must not be expanded by reading S1 as
production readiness.

This note is not a claim that every later source revision is already verified by
the running runtime. For the current source tree, always run
`python tools\foundation_alpha_readiness.py --format json`. If it reports
`foundation_alpha_stage_complete=false`,
`foundation_alpha_stage_status=runtime_rollout_required`, or a
`foundation_runtime_concurrency_evidence` blocker, report the latest source as
requiring S2-0 runtime/concurrency/readiness refresh before making a
current-source S1 completion claim.

## Accepted Baseline

- Source baseline: `main` / `origin/main` at
  `3c06c5351517028111c18a365ff9a24ed22ffa33`.
- Runtime subject: `380de6bf9ffed5167f9bb2eaee8e63612a52c124`.
- Runtime image: `ai-platform:380de6b-merged-main-runtime`.
- Runtime image ID:
  `sha256:e36e4dfad072cdd12b841019db3ccbcdef4b63ccf5262869c994757fef5663f9`.
- Source relation: `runtime_current_for_runtime_relevant_source`.

For this accepted baseline, the running runtime did not exactly match the
closure source tree because that source included docs, tests, evidence, and
readiness-record updates after the runtime subject. The readiness summary for
that snapshot was therefore read as runtime-relevant source coverage, not exact
current-source runtime verification:

- `foundation_alpha_stage_complete=true`
- `stage_acceptance_blockers=[]`
- `runtime_relevant_source_verified_by_running_runtime=true`
- `current_source_verified_by_running_runtime=false`
- `controlled_poc_loop_verified_for_current_source=false`

## Closure Evidence

The operator-facing S1 summary is:

```powershell
python tools\foundation_alpha_readiness.py --format json
```

For the accepted baseline, it reports Foundation Alpha stage completion, no
stage acceptance blockers, clean current source, the current `380de6b` runtime
subject, and the reviewed 211 POC evidence set.

The Foundation Runtime concurrency verifier is:

```powershell
python tools\foundation_runtime_concurrency.py --evidence-json docs\release-evidence\foundation-runtime-concurrency\380de6bf9ffed5167f9bb2eaee8e63612a52c124-frc-main-20260615\2026-06-15-211-foundation-alpha-poc-380de6b-foundation-runtime-concurrency.json --format json
```

For the accepted baseline, it reports `verified=true` and
`status=verified_foundation_runtime_concurrency`. The evidence covers 12
concurrent cases, 2 tenants, 4 users, run creation, execution, cancel, retry,
queue/admission correctness, sandbox workspace and lease separation,
memory/context isolation, artifact ACL denial across users and tenants, exact
tool-permission decision binding, pinned skill snapshots, run playback safety,
and 48 denied negative tool-permission reuse probes.

The reviewed 211 evidence set lives under:

- `docs/release-evidence/foundation-alpha-poc/380de6bf9ffed5167f9bb2eaee8e63612a52c124/`
- `docs/release-evidence/foundation-runtime-concurrency/380de6bf9ffed5167f9bb2eaee8e63612a52c124-frc-main-20260615/`

## Issue And PR State

As of this closure snapshot, the S1 source-authority and foundation issues are
closed in GitHub: #15, #16, #17, #21, #22, #23, and #33.

The only open PR observed during this closure pass was draft PR #44,
`[codex] Add sandbox latency split source evidence`. It is a later sandbox or
observability follow-up and is not an S1 blocker.

## Explicit Non-Expansion Boundaries

Foundation Alpha means the internal controlled foundation loop is accepted. It
is not production readiness.

S1 completion does not:

- raise production concurrency defaults
- broaden ordinary-user platform-level multi-run orchestration exposure
- claim Docker sandbox hardening
- permit department rollout
- enable long-term cross-session memory by default
- close packaged frontend image release acceptance
- close signed Skill package, SBOM, license, or vulnerability evidence

The next work remains S2 governance/operations evidence, G7 sandbox hardening,
B3 SDK subagent fanout capacity evidence, G9 observability acceptance, and G10
workflow-owner rollout preparation. G8 platform-level multi-run orchestration
stays a deferred blocked expansion unless a future issue explicitly reopens it
with gate-specific evidence.

## Closure Checklist

| Requirement | Closure judgment |
| --- | --- |
| PRD, technical acceptance, roadmap, guardrails, gate status alignment | S1 aligned around Foundation Alpha and G0-G10 gate language. |
| 211 runtime evidence | Covered by the reviewed `380de6b` POC evidence set. |
| Source authority | Runtime-relevant source is covered; exact current-source runtime match remains false by design for docs/tests/evidence-only deltas. |
| Auth, RBAC, tenant isolation, redaction | Covered by current 211 Auth/RBAC and POC smoke evidence. |
| Control plane and projections | Covered by focused tests and reviewed POC smoke; keep under regression. |
| Queue, worker, and Foundation Runtime concurrency | Covered for controlled POC correctness; production capacity increases remain blocked. |
| Tool, skill, and memory governance | Fail-closed S1 baseline covered; production Skill release and longer memory policy remain follow-ups. |
| Sandbox | Fake provider remains local/test-only; Docker hardening remains G7. |
| Platform multi-run / SDK subagent | SDK agent/subagent behavior stays inside one governed platform run; platform-level multi-run orchestration is deferred and ordinary-user exposure remains blocked. |
| Frontend | Active public/admin projection safety is within S1; packaged frontend image release acceptance remains S2 delivery work. |
