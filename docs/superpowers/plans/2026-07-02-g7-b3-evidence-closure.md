# G7/B3 Evidence Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for inline execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move G7 and B3 toward documented closure without overclaiming status labels.

**Status:** Historical execution plan plus evidence ledger. Do not treat this
file as the current G7/B3/G8 status source; current status lives in
`docs/operations/ai-platform-gate-status.md`. New work should append here only
when it directly executes this plan, not to preserve general status snapshots.

**Architecture:** G7 progress evidence is repository-owned reviewed release evidence wrapping already captured 211 named runtime-subject verifier artifacts. B3 closure remains evidence-driven: only operator-reviewed recorded load-test gate snapshots and the B3 SDK subagent fanout profile can move B3 beyond `local partial`.

**Tech Stack:** Python evidence tooling, Markdown gate docs, repository JSON release-evidence entries, targeted pytest.

## Global Constraints

- Keep status labels distinct: `local partial`, `PR ready`, `reviewed`, `merged`, `211 verified`, and `gate closable`.
- Do not claim G7 complete, B3 complete, Foundation Alpha complete, production-ready, or `211 verified` without current evidence.
- Do not embed secrets, raw environment values, Docker socket paths, host workdirs, callback tokens, or raw Docker inspect payloads.
- 211 checks are read-only unless the specific task requires deployment; use `python3` on 211 and `sudo -n docker` for Docker reads.
- B3 requires all seven recorded load-test gates and `b3_10x4_sdk_subagents` profile evidence before closure.

---

### Task 1: Wrap current-main G7 verifier artifacts

**Files:**
- Create: `docs/release-evidence/g7-sandbox/ae6b7e52c656fd8296cf039834ce8d8559b01228/2026-07-01-211-g7-sandbox-runtime-smoke-ae6b7e5.json`
- Modify: `docs/release-evidence/README.md`
- Modify: `tests/test_g7_b3_completion_audit.py`

**Interfaces:**
- Consumes: `.pytest-tmp/evidence/g7-current-main-ae6b7e5-20260701172910/*.json`
- Produces: `ai-platform.release-evidence-entry.v1` with `artifact_kind=211_sandbox_runtime_smoke`

- [x] Write/update failing tests proving the audit accepts reviewed G7 evidence but does not close G7/B3.
- [x] Create the reviewed, redacted release-evidence entry with source/runtime/current-main binding and stale-label followups.
- [x] Add the entry to `docs/release-evidence/README.md` reviewed entries.
- [x] Run G7/B3 audit tests and release-evidence export acceptance.

### Task 1b: Wrap current-main G7 formal hardening artifacts

**Files:**
- Create: `docs/release-evidence/g7-sandbox/ae6b7e52c656fd8296cf039834ce8d8559b01228/2026-07-01-211-g7-sandbox-runtime-hardening-ae6b7e5.json`
- Modify: `docs/release-evidence/README.md`
- Modify: `docs/operations/ai-platform-gate-status.md`
- Modify: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`
- Modify: `app/g7_b3_completion_audit.py`
- Test: `tests/test_g7_b3_completion_audit.py`
- Test: `tests/test_source_authority_docs.py`

**Interfaces:**
- Consumes: `.pytest-tmp/g7-current-main-label-repair-probe-20260701201919/*.json`
- Produces: `ai-platform.release-evidence-entry.v1` hardening evidence for the explicit verifier path only.

- [x] Add a reviewed, redacted formal hardening release-evidence entry.
- [x] Supersede the old live `SANDBOX_EXECUTOR_IMAGE=ai-platform:local` and `SANDBOX_EGRESS_POLICY_ENABLED=false` blockers with reviewed live-env evidence.
- [x] Update status docs so G7 hardening progress is visible without claiming G7, B3, G0, Foundation Alpha, production readiness, `211 verified`, or `gate closable`.

### Task 1c: Wrap current-main G7 live-env hardening artifacts

**Files:**
- Create: `docs/release-evidence/g7-sandbox/ae6b7e52c656fd8296cf039834ce8d8559b01228/2026-07-02-211-g7-sandbox-live-env-hardening-ae6b7e5.json`
- Modify: `docs/release-evidence/README.md`
- Modify: `docs/operations/ai-platform-gate-status.md`
- Modify: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`
- Modify: `app/g7_b3_completion_audit.py`
- Test: `tests/test_g7_b3_completion_audit.py`
- Test: `tests/test_source_authority_docs.py`

**Interfaces:**
- Consumes: `.pytest-tmp/g7-live-env-hardening-ae6b7e5-20260702045743/*.json`
- Produces: `ai-platform.release-evidence-entry.v1` live-env hardening evidence for current-main API/worker defaults.

- [x] Add a reviewed, redacted live-env hardening release-evidence entry.
- [x] Record live `SANDBOX_CONTAINER_PROVIDER=docker`, `SANDBOX_EXECUTOR_IMAGE=ai-platform:ae6b7e5-g7-b3-label-repair-v1`, and `SANDBOX_EGRESS_POLICY_ENABLED=true` without raw secrets or host paths.
- [x] Keep G7/B3 non-closure boundaries: external env-file, current-main POC/readiness, B3 load evidence, and operator status-upgrade review remain open.

### Task 2: Audit B3 recorded load evidence

**Files:**
- Modify: `docs/operations/ai-platform-gate-status.md`
- Modify: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`
- Test: `tests/test_source_authority_docs.py`

**Interfaces:**
- Consumes: `docs/release-evidence`, capacity baseline docs, and any recorded capacity snapshots if present.
- Produces: clear status text for whether B3 remains blocked or has evidence.

- [x] Search current repo and 211 evidence locations for accepted `ai-platform.capacity-recorded-gate-evidence.v1` and `ai-platform.capacity-operator-reviewed-recorded-snapshot-contract.v1` evidence.
- [x] If all seven gates and B3 profile evidence exist, wire them into readiness. If not, document the exact missing gates and keep B3 blocked.
- [x] Run capacity-profile and source-authority targeted tests.

### Task 3: Final verification

**Files:**
- No additional files unless tests identify gaps.

- [x] Run targeted tests for changed evidence/docs/audit paths.
- [x] Run `python -m compileall -q app tools scripts`.
- [x] Run `git diff --check`.
- [x] Report exact remaining blockers without marking the persistent goal complete unless G7 and B3 evidence both prove closure.

### Task 4: Add B3 seven-gate batch recorded snapshot assembly

**Files:**
- Modify: `app/capacity_baseline.py`
- Modify: `tools/capacity_recorded_gate_snapshot.py`
- Modify: `tests/test_capacity_baseline.py`
- Modify: `docs/operations/ai-platform-capacity-baseline.md`

**Interfaces:**
- Consumes: one end-runtime evidence snapshot plus seven operator-reviewed
  `ai-platform.capacity-recorded-gate-evidence.v1` packets, one for each
  required B3 load-test gate.
- Produces: one sanitized
  `ai-platform.capacity-recorded-gate-snapshot.v1` batch output with
  `status=recorded_gate_batch_input_accepted` only when every gate packet is
  present and accepted.

- [x] Add failing tests for accepting all seven gate packets plus B3 profile evidence in one batch.
- [x] Add failing tests for rejecting a batch that misses any required gate without partially recording evidence.
- [x] Implement `build_capacity_recorded_gate_batch_snapshot` and keep the existing single-gate path compatible.
- [x] Extend `tools/capacity_recorded_gate_snapshot.py` so repeated `--recorded-gate-evidence-json` inputs trigger batch assembly.
- [x] Document the batch command and non-closure boundary.

### Task 5: Add B3 recorded-gate packet input guard

**Files:**
- Modify: `app/capacity_baseline.py`
- Create: `tools/capacity_recorded_gate_evidence_packet.py`
- Modify: `tests/test_capacity_baseline.py`
- Modify: `docs/operations/ai-platform-capacity-baseline.md`

**Interfaces:**
- Consumes: operator-reviewed measured values for one required B3 load-test gate.
- Produces: `ai-platform.capacity-recorded-gate-evidence-packet-result.v1`
  with a nested `ai-platform.capacity-recorded-gate-evidence.v1` packet only
  when input values are safe and are not bounded probe output.

- [x] Add failing tests proving the packet builder creates a snapshot-compatible recorded-gate packet.
- [x] Add failing tests proving bounded probe output is rejected instead of promoted to recorded evidence.
- [x] Add failing tests proving unsafe values are rejected without echoing private content.
- [x] Add the packet builder CLI to the generated operator workflow before snapshot assembly.
- [x] Document the packet command and non-closure boundary.

### Task 6: Reject direct probe-marked recorded-gate packets

**Files:**
- Modify: `app/capacity_baseline.py`
- Modify: `tests/test_capacity_baseline.py`
- Modify: `docs/operations/ai-platform-capacity-baseline.md`

**Interfaces:**
- Consumes: direct `ai-platform.capacity-recorded-gate-evidence.v1` packets
  passed to `tools/capacity_recorded_gate_snapshot.py`.
- Produces: fail-closed rejection when a direct packet still carries
  `load_test_evidence_status=probe_only_not_recorded` or
  `does_not_mark_gate_recorded=true`, even if the nested measured fields look
  complete.

- [x] Add a failing regression test proving direct probe-marked packets were accepted before the guard.
- [x] Reject top-level bounded-probe markers inside `_capacity_recorded_gate_evidence_packet`.
- [x] Add CLI coverage for `tools/capacity_recorded_gate_snapshot.py` direct packet rejection.
- [x] Document that hand-rewrapped probe output cannot be promoted through the direct packet path.

## Current cleanup outcome

Status remains `local partial`, not G7 complete, B3 complete, Foundation Alpha
complete, production-ready, `211 verified`, or `gate closable`.

2026-07-03 refresh: current 211 source/runtime has advanced to
`755e50ea2ad08c2d4218ae5d8cc612970b19e2a4`. The repo-local source marker,
API/worker image ID, source/runtime/OCI labels, and legacy source alias labels
bind to `755e50e`; fresh 211 readback still observed legacy in-container marker
files at `9c669761` and `28676df`, so G0/source-authority closure and clean
current-main `211 verified` remain unavailable. Fresh local Git readback on 2026-07-03 also
showed `origin/main` at `1230dbc64a39805d6492a60c2688a2fed31ef3d9`
after a frontend-only merge, so `755e50e` must be treated as dirty-runtime
evidence rather than latest clean `origin/main` runtime evidence. API/worker are
running dirty runtime-only local patch image
`ai-platform:755e50e-g7-b3-principal-userid-fix-v2`, and API health on
`127.0.0.1:8020`, frontend proxy health, and frontend root were healthy. The
755e50e dirty-runtime v2 live-env verifier
`g7-live-env-hardening-755e50e-principal-userid-fix-v2-container-20260703115120`
is wrapped as reviewed G7 runtime evidence at
`docs/release-evidence/g7-sandbox/755e50ea2ad08c2d4218ae5d8cc612970b19e2a4/2026-07-03-211-g7-sandbox-live-env-hardening-755e50e.json`;
all eight verifier checks passed.

This still does not close status. Same-subject FRC evidence is now recorded at
`docs/release-evidence/foundation-runtime-concurrency/755e50ea2ad08c2d4218ae5d8cc612970b19e2a4-frc-g7-b3-20260703/`, with readiness status
`verified_foundation_runtime_concurrency`, `verified=true`, `failures=[]`, and
12 concurrent requests/runs/sessions across 2 tenants and 4 users. The earlier
`/tmp/frc-755e50e-20260703T090109Z` `queue_payload_invalid` attempt is superseded
diagnostic history. Current status remains `local partial`, because B3 recorded
load evidence, `b3_10x4_sdk_subagents` profile evidence, approved G7
status-upgrade review, and clean current-main `211 verified` evidence are still
missing. A later read-only B3 capacity visibility capture for the same `755e50e`
runtime subject is recorded at
`docs/release-evidence/capacity-gate-readiness/755e50ea2ad08c2d4218ae5d8cc612970b19e2a4/2026-07-03-211-capacity-runtime-readiness-755e50e.json`:
Admin Runtime returned HTTP `200`, but readiness stayed
`blocked_missing_admin_runtime_sections` because the no-cleanup capture could not
provide valid sandbox container observation, so the `sandbox` evidence section is
treated as missing for B3; all seven recorded load-test gates and profile
evidence are still absent. This is B3
visibility only and does not close B3.

G8 wording is now treated as a status-boundary cleanup issue. The old ordinary-user
multi-agent exposure machine name is misleading for current status because it
collapses two different things: ordinary-user platform-owned parent/child
multi-run product exposure and Claude Agent SDK internal subagent fanout
capacity. Current authority is `G8 Deferred Platform Multi-Run Gate`, a
deferred parking-lot for platform-owned parent/child multi-run orchestration.
Historical G8 beta wording and historical evidence fields are legacy evidence
names only, not current status names. B3 remains the SDK subagent fanout
capacity evidence track for `b3_10x4_sdk_subagents`; it is not ordinary-user
platform-level multi-run product exposure and does not reopen or close G8.

The 2026-07-02 211 bounded B3 sweep covered all seven harness gates, but every
probe is still `probe_completed_not_gate_evidence` /
`probe_only_not_recorded`, with `does_not_mark_gate_recorded = true`. The
current verifier interpretation keeps `missing_sections=[]` but still reports
`blocked_missing_load_test_evidence`; these probes do not become B3 recorded
gate evidence.

A later read-only 211 capacity runtime capture for PR #304 runtime subject
`decf33a017e0b97e2a2992f80e3ccdc19152c1f4` returned Admin Runtime HTTP `200`
on `/api/ai/admin/runtime/overview?include_maintenance_cleanup=false` with all
required sections present, but its readiness still reports
`blocked_missing_load_test_evidence`, all seven recorded gates missing,
`profile_evidence={}`, and
`production_default_decision=do_not_raise_without_recorded_load_test_evidence`.
This updates B3 visibility for the `decf33a` runtime subject only. PR #304 is
now merged at `a9c78efa812efe96b0366011a0c731cb11eb0099`, but this evidence
does not prove current-main `211 verified` for that merge commit and does not
close B3.

Audit cleanup note: old sanitized runtime observations must be merged with the
later reviewed label-repair, live-env hardening, and Foundation Runtime
concurrency evidence for the same runtime subject before blockers are read. The
audit tool accepts those reviewed evidence entries as explicit overrides so stale
alias, fake-provider, or missing-FRC observations do not reappear as current
G7 blockers.

Remaining blockers before any status upgrade:

- G0/source-authority and production-hardening boundaries: external runtime
  env-file label caveat and current local runtime-affecting source rollout gap;
- current `755e50e` Admin Runtime capacity visibility remains
  `blocked_missing_admin_runtime_sections` because sandbox container observation
  is unavailable/degraded and therefore does not count as valid B3 `sandbox`
  evidence;
- B3 operator-reviewed recorded load evidence, including all seven recorded
  load-test gates and the `b3_10x4_sdk_subagents` profile evidence;
- G7 operator status-upgrade review. Latest audit reads
  `status=candidate_evidence_requires_review`, `blocking_reasons=[]`, and
  `required_next_steps=["complete operator status-upgrade review before claiming G7 closure or 211 verified status"]`
  for G7 after reviewed label-repair, live-env hardening, and FRC overrides.

Tasks 4 and 5 reduce B3 operator assembly friction only. They do not create
recorded load evidence, accept bounded probes as recorded gates, close B3,
raise production defaults, or upgrade the overall status beyond `local
partial`.
