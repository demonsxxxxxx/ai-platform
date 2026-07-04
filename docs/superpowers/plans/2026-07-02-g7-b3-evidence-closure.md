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

## Historical cleanup outcome

Status remains `local partial`, not G7 complete, B3 complete, Foundation Alpha
complete, production-ready, `211 verified`, or `gate closable`.

This plan ledger records the 2026-07-03 `755e50e` cleanup slice and is now
historical for current-state naming. The current gate/runtime status moved to
PR #319 / post-PR #319 `a294727`; use
`docs/operations/ai-platform-gate-status.md` and
`docs/release-evidence/README.md` for current status.

2026-07-03 refresh: the then-current 211 source/runtime advanced to
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
diagnostic history. That slice remained `local partial`, because B3 recorded
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
  env-file label caveat and production auth rollout remain separate gates;
- current `a294727` Admin Runtime capacity visibility now reaches
  `blocked_missing_load_test_evidence`: the 2026-07-04 reviewed redacted entry at
  `docs/release-evidence/capacity-gate-readiness/a294727046024958c41b15f646512e68f3c04b47/2026-07-04-211-capacity-runtime-readiness-a294727.json`
  records HTTP `200`, all required Admin Runtime sections including `sandbox`,
  and host-side sandbox observation status `accepted`. The matching diagnostic
  entry at
  `docs/release-evidence/diagnostics/2026-07-04-211-b3-host-sandbox-observation-a294727.json`
  is diagnostic-only and does not mark B3 recorded evidence. The earlier
  `bbe23d5` and `61073b1` visibility entries remain retained as prior baseline
  evidence only;
- B3 operator-reviewed recorded load evidence, including all seven recorded
  load-test gates and the `b3_10x4_sdk_subagents` profile evidence;
- G7 operator status-upgrade approval. No approved status-upgrade artifact exists
  for current `a294727`; the historical `bbe23d5` operator status-review
  artifact is recorded but sets `status_upgrade_decision=not_approved_for_closure`.
  Current `a294727` canonical source-marker reconciliation is present, but
  legacy `/app/.codex-source-revision` and `/app/.source-commit` still show
  `28676df`; neither the canonical marker evidence nor the legacy marker caveat
  removes the G7 approval blocker or any B3 blocker.

Tasks 4 and 5 plus the draft-only operator evidence template bundle reduce B3
operator assembly friction only. The template bundle provides
`ai-platform.capacity-operator-evidence-template-bundle.v1` placeholders and
packet commands for all seven gates and the `b3_10x4_sdk_subagents` profile,
but every `TODO_OPERATOR_REVIEWED_` value must be replaced with real measured
evidence before packet builders can accept it. The directory-based fail-closed
batch assembler `tools/capacity_recorded_gate_batch_from_values.py` can then
read the filled `capacity-operator-inputs` directory, build the seven packet
results through the existing validators, and call the same all-gate batch
snapshot assembler; it still returns `blocked_incomplete_inputs` when any
gate/profile value is missing or unsafe. These helpers do not create recorded
load evidence, accept bounded probes as recorded gates, close B3, raise
production defaults, or upgrade the overall status beyond `local partial`.

2026-07-04 local follow-up status:

- [x] Directory-based B3 batch assembler now fails closed with structured JSON
  when the `b3_10x4_sdk_subagents` profile values file is missing; it no longer
  exits before emitting a batch snapshot result for that missing-profile path.
- [x] Operator template materialization docs now create
  `capacity-operator-inputs` before redirecting command output, so the
  PowerShell copy path is executable on a clean workspace.
- [x] Docs clarify that
  `ordinary_user_platform_multi_run_orchestration_exposure` is the route/status
  blocked-expansion name, while
  `ordinary_user_platform_multi_run_orchestration_enabled=false` is only the B3
  profile packet non-expansion boolean.
- [x] G7 completion audit now has a fail-closed future
  `--g7-status-upgrade-review-json` path: only an accepted
  `approved_for_g7_status_upgrade` artifact bound to the same runtime subject can
  remove the G7 status-upgrade blocker. Future approval evidence must use the
  route/status invariant
  `ordinary_user_platform_multi_run_orchestration_exposure=false`; the B3 packet
  boolean `ordinary_user_platform_multi_run_orchestration_enabled=false` is not
  accepted as a substitute. The approval still does not close B3, mark `211
  verified`, or make the overall gate closable.
- [x] Code-review follow-up tightened B3 audit input validation: truncated or
  fabricated capacity-profile readiness with empty `missing_*` lists now fails
  closed as inconsistent, while real complete readiness still only advances B3
  to `operator_review_required_before_default_change`.
- [x] Code-review follow-up preserves accepted host-side sandbox observation
  status through recorded-gate snapshot normalization as diagnostic provenance
  with `does_not_mark_b3_recorded_evidence=true` and `does_not_close_b3=true`;
  it does not turn host observation into recorded load evidence or B3 closure.
- [x] Recorded-gate packet and snapshot validators now reject diagnostic-only
  release evidence markers, including
  `ai-platform.release-evidence-diagnostic-entry.v1`,
  `diagnostic_only=true`,
  `diagnostic_only_not_reviewed_release_evidence`, and
  `does_not_mark_b3_recorded_evidence=true`. The G7/B3 audit CLI regression
  also verifies that passing the B3 diagnostic observation through
  `--reviewed-release-evidence-json` is ignored as reviewed evidence and keeps
  B3 blocked.
- [x] 2026-07-04 read-only 211 source check confirmed the target source path is
  a git-archive snapshot at `.ai-platform-source-revision=61073b1`, API/worker
  still run `ai-platform:61073b1-g7-b3-clean-main-v1`, `/api/ai/health`
  returns `{"status":"ok"}`, and the remote tools set does not yet include the
  local draft helpers `capacity_operator_evidence_template_bundle.py` or
  `capacity_recorded_gate_batch_from_values.py`. Therefore a remote B3 operator
  assembly run still requires merging/syncing this local tooling before it can
  be executed on 211.
- [x] Batch assembly now rejects a reviewed release-evidence index entry passed
  as `--runtime-evidence-json` with
  `runtime_evidence_release_entry_not_supported`. Operators must pass the raw
  `ai-platform.capacity-runtime-evidence.v1` output containing the nested
  `ai-platform.capacity-evidence-snapshot.v1`, or the raw snapshot itself.
- [x] 2026-07-04 local verification covered
  `tests/test_capacity_baseline.py`,
  `tests/test_g7_b3_completion_audit.py`,
  `tests/test_source_authority_docs.py`,
  `tests/test_release_evidence_export_acceptance.py`, and
  `tests/test_backend_phased_prd.py` (`218 passed`), plus
  `python -m compileall -q app tools scripts`,
  `python tools\release_evidence_export_acceptance.py --format json` reporting
  `status=ready_for_operator_review blocked=0 safe=256 excluded=106`,
  host sandbox observation missing/malformed JSON CLI regressions, and
  `git diff --check`.
- [x] 2026-07-04 local completion-audit reproduction used the reviewed
  `61073b1` G7/FRC/capacity evidence set and reported
  `audit_status=blocked_missing_g7_b3_completion_evidence`,
  `status_label=local partial`, `g7=candidate_evidence_requires_review`,
  `b3=blocked`, `missing_gates=7`, and `missing_profile=8`; the current G7
  status-upgrade review remained `not_accepted` /
  `not_approved_for_closure`. This reproduces the documented blockers; it is
  not new release evidence and does not close G7/B3.
- [x] 2026-07-04 211 post-PR #317 runtime refresh advanced the live runtime to
  `bbe23d53d14398378b4870de4cbf4bec0b045193` with API/worker image
  `ai-platform:bbe23d5-g7-b3-post-317-runtime-only-v1`; direct API health
  returned `{"status":"ok"}`. The bbe23d5 G7 live-env verifier
  `g7-live-env-hardening-bbe23d5-post-317-20260704151940` passed all eight
  verifier checks, and same-subject Foundation Runtime concurrency evidence
  verified 12 concurrent requests/runs/sessions across 2 tenants and 4 users.
  Reviewed evidence entries were added under
  `docs/release-evidence/g7-sandbox/bbe23d53d14398378b4870de4cbf4bec0b045193/`,
  `docs/release-evidence/foundation-runtime-concurrency/bbe23d53d14398378b4870de4cbf4bec0b045193-frc-g7-b3-20260704/`,
  and
  `docs/release-evidence/g7-status-review/bbe23d53d14398378b4870de4cbf4bec0b045193/`.
  This moves the bbe23d5 G7 runtime evidence set to
  `candidate_evidence_requires_review`, not closure.
- [x] 2026-07-04 bbe23d5 capacity visibility is recorded at
  `docs/release-evidence/capacity-gate-readiness/bbe23d53d14398378b4870de4cbf4bec0b045193/2026-07-04-211-capacity-runtime-readiness-bbe23d5.json`.
  Admin Runtime returned HTTP `200`, all required sections including `sandbox`
  were observed, and readiness improved to `blocked_missing_load_test_evidence`.
  All seven recorded gates and the `b3_10x4_sdk_subagents` profile evidence are
  still missing, so this does not close B3 or raise defaults.
- [x] 2026-07-04 source-authority caveat: bbe23d5 repo-local source marker and
  image labels are current, but API/worker in-container source marker files
  still show `61073b1`; therefore the current slice is not `211 verified`, not
  G0 source-authority closure, and not `gate closable`.
- [x] 2026-07-04 post-PR #319 source-marker fix advanced the current live
  runtime to `a294727046024958c41b15f646512e68f3c04b47` with API/worker image
  `ai-platform:a294727-g7-b3-source-marker-fix-v1`. Repo-local marker, source
  snapshot, image labels, and canonical API/worker in-container marker
  `/app/.ai-platform-source-revision` bind to `a294727`; legacy
  `/app/.codex-source-revision` and `/app/.source-commit` still show `28676df`.
  Direct API health and frontend proxy health returned `{"status":"ok"}`. The G7 verifier
  `g7-live-env-hardening-a294727-source-marker-fix-20260704170251` passed all
  eight checks and the capacity visibility entry
  `2026-07-04-211-capacity-runtime-readiness-a294727.json` reports
  `blocked_missing_load_test_evidence`.
- [ ] B3 still requires real operator-reviewed values for all seven recorded
  load-test gates and the `b3_10x4_sdk_subagents` profile before closure.
- [ ] G7 still requires a future approved operator status-upgrade decision
  before any G7 closure or `gate closable` claim; `211 verified` may only be
  used for the narrow source-marker/runtime-health slice, not overall G7/B3
  closure.
