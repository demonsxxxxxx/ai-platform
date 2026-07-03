# ai-platform Gate Status Snapshot

Date: 2026-07-03

This snapshot is the single current gate/runtime status matrix for the current
PRD, foundation roadmap, guardrails, repository code, 211 runtime, and
issue-driven priorities. It is not automatic gate-closure evidence and must not
be used to auto-close GitHub issues. Gate closure still requires the
issue -> PR -> review -> merge -> 211 deploy/smoke -> close issue workflow in
`docs/agent-rules/github-issue-pr-workflow.md`.

Companion PRDs currently split the next-stage planning surface:
`docs/superpowers/specs/2026-06-18-ai-platform-backend-phased-prd.md` defines
backend B0-B6 sequencing and backend acceptance boundaries, while
`docs/superpowers/specs/2026-06-18-librechat-frontend-ui-absorption-prd.md`
defines the frontend UI absorption boundary. These are planning/source-authority
documents only. They do not provide 211 verification, runtime smoke, or gate
closure evidence by themselves.

## Current Reading Guide

At this status-sync slice, GitHub `main` already included PR #312 merge commit
`881493d042a522b343c9df2044bd3830fd02e62f`, and a later 211 rollout now binds
the repo-local source marker plus API/worker source/runtime labels to runtime
subject `755e50ea2ad08c2d4218ae5d8cc612970b19e2a4`. Fresh local Git readback
on 2026-07-03 showed `origin/main` at
`1230dbc64a39805d6492a60c2688a2fed31ef3d9` after a frontend-only merge, so the
`755e50e` runtime evidence must not be called latest clean `origin/main`
evidence. Current status must therefore be read as `755e50e` G7 runtime evidence
layered on top of PR #312 merged docs/test status-boundary progress, PR #311
source/docs/audit-boundary progress, the deployed PR #308 label-clean runtime
evidence, plus earlier reviewed `9c669761`, `decf33a`, `4805031`, `ae6b7e5`,
and `28676df` evidence.
It is not gate closure. The current `755e50e` dirty-runtime v2 G7 verifier and
same-subject Foundation Runtime concurrency evidence are both present, but this
does not make clean current `main` `211 verified`: B3 recorded load evidence is
still missing, operator status-upgrade approval is still absent, and the running
API/worker image is a dirty runtime-only local patch rather than a clean
current-main rollout.

PR #308 is merged into GitHub `main` at
`15903fdfe96ffcfba9daa1252741111017dcf832`; GitHub checks were successful,
`reviewDecision` remained empty, and the PR head before squash merge was
`cc213a995e7ba9808116095365949c4f14b6ac1c`. A later label-clean 211 readback
observed the repo-local source marker at
`15903fdfe96ffcfba9daa1252741111017dcf832` and API and worker running
`ai-platform:15903fd-g7-b3-label-clean-v2`, with API/worker canonical
source/runtime/OCI labels and legacy source alias labels bound to `15903fd`.
Direct API health on
`http://127.0.0.1:8020/api/ai/health` returned `{"status":"ok"}`, frontend proxy
health on `http://127.0.0.1:18001/api/ai/health` returned `{"status":"ok"}`,
and the frontend root on `http://127.0.0.1:18001/` returned HTTP `200`.

This is still not G7 or B3 closure. The earlier deployed-runtime G7 verifier run
`g7-current-main-9c66976-20260702145801` wrote diagnostic artifacts only:
`runtime_mode=executor`, `sandbox_provider=unknown`, `executed_task=false`, and
the generator message `[Errno 13] Permission denied: '[redacted-path]'`. A later
sudo-context `9c669761` explicit verifier-path run,
`g7-current-main-9c66976-sudo-20260702155816`, is wrapped as reviewed evidence in
`docs/release-evidence/g7-sandbox/9c669761bbb4bd719af64a341d361b7c3b3e380e/2026-07-02-211-g7-sandbox-runtime-hardening-9c669761.json`.
After the external runtime env was rebound and API/worker were recreated, the
2026-07-03 live-default run
`g7-live-env-hardening-9c669761-sudo-20260703091724` is wrapped in
`docs/release-evidence/g7-sandbox/9c669761bbb4bd719af64a341d361b7c3b3e380e/2026-07-03-211-g7-sandbox-live-env-hardening-9c669761.json`,
and the same-subject Foundation Runtime concurrency artifact is recorded at
`docs/release-evidence/foundation-runtime-concurrency/9c669761bbb4bd719af64a341d361b7c3b3e380e-frc-g7-b3-20260703/2026-07-03-211-foundation-alpha-poc-9c669761-foundation-runtime-concurrency.json`.
With those paired inputs, the G7 audit can reach
`status=candidate_evidence_requires_review`, `blocking_reasons=[]`, and
`required_next_steps=["complete operator status-upgrade review before claiming G7 closure or 211 verified status"]`
for runtime subject `9c669761`. The reviewed operator status-review artifact at
`docs/release-evidence/g7-status-review/9c669761bbb4bd719af64a341d361b7c3b3e380e/2026-07-03-211-g7-operator-status-review-9c669761.json`
records `status_upgrade_decision=not_approved_for_closure`, so this remains a
historical candidate status rather than G7 closure.

The deployed PR #308 runtime subject `15903fd` has a reviewed label-clean
live-default G7 hardening entry at
`docs/release-evidence/g7-sandbox/15903fdfe96ffcfba9daa1252741111017dcf832/2026-07-03-211-g7-sandbox-live-env-hardening-15903fd-label-clean.json`
and same-subject Foundation Runtime concurrency evidence at
`docs/release-evidence/foundation-runtime-concurrency/15903fdfe96ffcfba9daa1252741111017dcf832-frc-g7-b3-20260703/2026-07-03-211-foundation-alpha-poc-15903fd-foundation-runtime-concurrency.json`.
The G7 verifier summary recorded all eight checks passing for the deployed
`15903fd` live-default path, and the raw FRC evidence is sufficient for the
readiness builder to compute `verified_foundation_runtime_concurrency`. The
reviewed label-clean operator status-review artifact at
`docs/release-evidence/g7-status-review/15903fdfe96ffcfba9daa1252741111017dcf832/2026-07-03-211-g7-operator-status-review-15903fd-label-clean.json`
records `status=candidate_evidence_requires_review`,
`g7_runtime_blocking_reasons=[]`, and
`status_upgrade_decision=not_approved_for_closure`. There is still no reviewed
B3 capacity entry for `15903fd`; newer latest-status reading must use the
reviewed `755e50e` dirty-runtime v2 capacity visibility entry. That newer entry
is still fail-closed at `blocked_missing_admin_runtime_sections` because the
Admin Runtime capacity section `sandbox` is missing, and all seven B3 recorded
load gates plus the `b3_10x4_sdk_subagents` profile evidence are still missing.
Do not call G7 complete, B3 complete, Foundation Alpha complete,
production-ready, `211 verified`, or `gate closable`.

The later current runtime subject `755e50e` has a reviewed G7 live-env
hardening entry at
`docs/release-evidence/g7-sandbox/755e50ea2ad08c2d4218ae5d8cc612970b19e2a4/2026-07-03-211-g7-sandbox-live-env-hardening-755e50e.json`.
The 211 repo-local source marker, API/worker image ID, canonical
source/runtime/OCI labels, and legacy source alias labels bind to `755e50e`,
with `ai-platform.build-dirty=true` and rollout
`g7-b3-755e50e-principal-userid-fix-v2`. Fresh 211 readback still observed
legacy in-container marker files at `9c669761` and `28676df`, so this remains
source-authority caveated and not clean current-main `211 verified`. API health
on `127.0.0.1:8020`, the
frontend proxy health on `127.0.0.1:18001`, and the frontend root were healthy.
The current v2 G7 verifier summary recorded all eight checks passing for
`g7-live-env-hardening-755e50e-principal-userid-fix-v2-container-20260703115120`.
The earlier blocked FRC attempt at `/tmp/frc-755e50e-20260703T090109Z` is
superseded by same-subject FRC evidence in
`docs/release-evidence/foundation-runtime-concurrency/755e50ea2ad08c2d4218ae5d8cc612970b19e2a4-frc-g7-b3-20260703/`,
whose readiness file reports `verified_foundation_runtime_concurrency`,
`verified=true`, `failures=[]`, 12 concurrent requests/runs/sessions across 2
tenants and 4 users, and passed queue, sandbox, memory/context, artifact ACL,
tool permission, skill snapshot, and run playback checks. This advances the
current `755e50e` G7/FRC evidence set only to reviewed dirty-runtime candidate
evidence; it still does not close G7, B3, Foundation Alpha, #164, or clean
current-main `211 verified`.

Reviewed 211 runtime evidence for earlier subjects remains historical
same-subject evidence only. PR #304 runtime subject `decf33a`, PR #297 runtime
subject `4805031`, and reviewed `ae6b7e5` evidence have same-subject G7/FRC
records that can read `status=candidate_evidence_requires_review`,
`blocking_reasons=[]`, and
`required_next_steps=["complete operator status-upgrade review before claiming G7 closure or 211 verified status"]`
for their own runtime subjects. The reviewed `28676df` capacity visibility entry
and its patched-source workspace-owner diagnostic explain the PR #306 fix
direction. The `9c669761` live-default G7/FRC pair and the `15903fd`
label-clean live-default G7/FRC pair are now historical same-subject evidence.
The `755e50e` dirty-runtime v2 G7 verifier plus same-subject FRC success advance
the current evidence set only to reviewed dirty-runtime candidate evidence; they
do not provide approved G7 status upgrade, B3 recorded load evidence, clean
current-main `211 verified`, or #164 closure evidence.
GitHub issue #164 is already closed as historical B0 latest-main
runtime-refresh work; its closure history is not current G7/B3 closure
evidence.

Earlier current-live `4805031` observation remains historical operational
context: API/worker images, labels, and `SANDBOX_EXECUTOR_IMAGE` were observed
at `ai-platform:4805031-g7-b3-post-297-label-repair-v2`, so the captured
`4805031` G7 audit was no longer blocked by executor-image drift. A post-merge
read-only 211 poll observed API/worker still running
`ai-platform:decf33a-g7-b3-post-300-followup-v1`, with API/worker revision
labels bound to `decf33a` and `/api/ai/health` returning HTTP `200`; this
supports the historical current-source runtime rollout gap for `a9c78ef` rather
than closing it. The later `28676df`, `9c669761`, and `15903fd` rollouts changed
the running API/worker image identity and, for `15903fd`, the repo-local source
marker. At the `15903fd` slice, the 2026-07-03 label-clean readback confirmed
API/worker live defaults used
`SANDBOX_CONTAINER_PROVIDER=docker`,
`SANDBOX_EXECUTOR_IMAGE=ai-platform:15903fd-g7-b3-label-clean-v2`, and
`SANDBOX_EGRESS_POLICY_ENABLED=true`; this removed that subject's G7
executor-image drift and stale legacy alias blockers, but operator status-upgrade
approval and B3 recorded load evidence remain open.

The last repo-local full Foundation Alpha POC evidence set is still for PR #296
merge commit `ae6b7e52c656fd8296cf039834ce8d8559b01228`; it includes the PR #294
G7 verifier-helper callback default fix and the PR #296 document-state cleanup.
The synced 211 source snapshot for that subject records
`source_tree_commit_sha=ae6b7e52c656fd8296cf039834ce8d8559b01228`,
`runtime_subject_commit_sha=ae6b7e52c656fd8296cf039834ce8d8559b01228`,
`source_tree_dirty=false`, and
`snapshot_source=codex_origin_main_archive_sync`. The reviewed `ae6b7e5`
evidence set includes a repo-local G7 sandbox release-evidence entry wrapping
the verifier artifacts, a reviewed G7 hardening entry for the later formal
verifier path, a reviewed G7 live-env hardening entry for API/worker Docker
posture, a reviewed label-repair entry, runtime POC smoke, same-subject
Auth/RBAC, governance, release-evidence runtime acceptance, alert/trace runtime
acceptance, and Foundation Runtime concurrency evidence.

These entries are not full issue/gate closure or current-source `211 verified`.
For the reviewed `ae6b7e5` evidence set, the G7 audit reads
`status=candidate_evidence_requires_review`, `blocking_reasons=[]`, and
`required_next_steps=["complete operator status-upgrade review before claiming G7 closure or 211 verified status"]`.
For current `4805031`, the reviewed evidence pair remains useful historical
evidence: after passing
`docs/release-evidence/g7-sandbox/4805031fc3333ccbf38224172e4e85e21c0630bb/2026-07-02-211-g7-sandbox-live-env-hardening-4805031.json`
and
`docs/release-evidence/foundation-runtime-concurrency/4805031fc3333ccbf38224172e4e85e21c0630bb-frc-g7-b3-20260702/2026-07-02-211-foundation-alpha-poc-4805031-foundation-runtime-concurrency.json`
into the audit, the evidence-only G7 audit reads
`status=candidate_evidence_requires_review`
with `blocking_reasons=[]` and
`required_next_steps=["complete operator status-upgrade review before claiming G7 closure or 211 verified status"]`.
The newer current-live observation confirms the same operational reading for
the `4805031` runtime subject because the live sandbox executor image now
matches the reviewed `4805031` runtime image.
For PR #304 runtime evidence subject `decf33a`, the reviewed evidence pair is
`docs/release-evidence/g7-sandbox/decf33a017e0b97e2a2992f80e3ccdc19152c1f4/2026-07-02-211-g7-sandbox-live-env-hardening-decf33a.json`
and
`docs/release-evidence/foundation-runtime-concurrency/decf33a017e0b97e2a2992f80e3ccdc19152c1f4-frc-g7-b3-20260702/2026-07-02-211-foundation-alpha-poc-decf33a-foundation-runtime-concurrency.json`.
Passing those into the audit also reaches
`status=candidate_evidence_requires_review` with `blocking_reasons=[]`, but only
for the `decf33a` runtime evidence subject. PR #304 is now merged, but this
does not make the merge commit `a9c78ef` current-source runtime verified, does
not make post-`decf33a` commits 211 runtime evidence, and does not constitute
current G7/B3 closure evidence for the already-closed historical #164.
The compose external env-file label and local runtime-affecting source delta are
G0/source-authority and production-hardening boundaries; B3 seven-gate load
evidence remains the B3 blocker. The reviewed `ae6b7e5` POC/FRC evidence is POC
correctness / release-evidence evidence only and does not close G7/B3/G0,
production Docker sandbox hardening, or production readiness.

A named G7/B3 runtime-only formal verifier run previously passed on 211 as
`g7-runtime-probe-20260701203418` for
`ai-platform:d318f9f-g7-b3-runtime-only-v1` /
`d318f9f6a68b4c17e221eb32705b3f31d349227a`: the formal evidence recorded
`runtime_mode=platform`, `sandbox_provider=docker`, running/completed callbacks,
cancel-stops-container, resource-limit timeout cleanup, egress default-deny
evidence, non-privileged security options, and all eight verifier checks
passing. That is named `d318f9f` runtime-subject evidence only. It is not a
reviewed local `docs/release-evidence/b2-sandbox/...` entry and not reviewed
`bd690f7` or current-main `ae6b7e5` G7 release evidence. A current-main
one-shot G7 verifier run also produced `/tmp` artifacts for
`g7-current-main-ae6b7e5-20260701172910` against
`ai-platform:ae6b7e5-g7-current-main-runtime-only-v1`: runtime probe results,
sandbox evidence, and verifier summary. The sandbox evidence records
`runtime_mode=platform`, `sandbox_provider=docker`, `executed_task=true`,
`callback_auth=token`, and `cancel_stops_container=true`; the verifier summary
has all eight checks passing. Those artifacts are now wrapped in the repo-local
reviewed release-evidence entry
`docs/release-evidence/g7-sandbox/ae6b7e52c656fd8296cf039834ce8d8559b01228/2026-07-01-211-g7-sandbox-runtime-smoke-ae6b7e5.json`.
That upgrades the `/tmp` artifacts to reviewed `ae6b7e5` G7 sandbox runtime
smoke evidence only; it does not close G7, B3, G0, Foundation Alpha, production
Docker sandbox hardening, production readiness, or `211 verified` by itself.
At capture time, that smoke record still carried a stale alias-label follow-up.
The old alias values pointed at
`bd690f72723080beeb820d07679da59d84c7913e`, which is retained as historical
root-cause evidence, not as a current-source canonical mismatch.
The later reviewed label-repair entry
`docs/release-evidence/g7-sandbox/ae6b7e52c656fd8296cf039834ce8d8559b01228/2026-07-01-211-g7-runtime-identity-label-repair-ae6b7e5.json`
records `ai-platform:ae6b7e5-g7-b3-label-repair-v1` with canonical labels,
legacy aliases, and API/worker in-container markers all repaired to `ae6b7e5`.
The label-repair image ID is
`sha256:59d9c73fe449fd3285aa88bc38dcc1aa6b96a4569ed4b9d447773c9fea0f5140`.
That clears only `stale_runtime_alias_label_mismatch`; it does not close the
external env-file label, current-source POC/readiness, or B3 load-evidence
blockers.
A later then-current-main formal hardening verifier run
`g7-current-main-label-repair-probe-20260701201919` used the label-repair image
`ai-platform:ae6b7e5-g7-b3-label-repair-v1` and is now wrapped in the reviewed
repo-local release-evidence entry
`docs/release-evidence/g7-sandbox/ae6b7e52c656fd8296cf039834ce8d8559b01228/2026-07-01-211-g7-sandbox-runtime-hardening-ae6b7e5.json`.
That verifier summary recorded all eight checks passing, including explicit
resource-limit cleanup, egress default-deny with scoped callback exception, and
non-privileged security options. This improves reviewed `ae6b7e5` G7
hardening evidence, but it proves the explicit verifier path only. A newer
live-env hardening verifier run
`g7-live-env-hardening-ae6b7e5-20260702045743` is now wrapped in
`docs/release-evidence/g7-sandbox/ae6b7e52c656fd8296cf039834ce8d8559b01228/2026-07-02-211-g7-sandbox-live-env-hardening-ae6b7e5.json`.
That run records live API/worker sandbox posture with
`SANDBOX_CONTAINER_PROVIDER=docker`,
`SANDBOX_EXECUTOR_IMAGE=ai-platform:ae6b7e5-g7-b3-label-repair-v1`, and
`SANDBOX_EGRESS_POLICY_ENABLED=true`; its verifier summary has all eight checks
passing, including resource-limit cleanup, egress default-deny with scoped
callback exception, and non-privileged security options. This clears the older
live executor-image and egress-policy blockers. It moves G7 to
`candidate_evidence_requires_review` rather than closure: the G7 runtime blocker
list is empty only after the later reviewed overrides are applied, and operator
status-upgrade review is still required before any G7 closure or `211 verified`
claim. The external env-file label, current local runtime-affecting source
rollout gap, and B3 load evidence remain separate G0/source-authority,
production-hardening, and B3 non-closure boundaries.
The reviewed `ae6b7e5` Foundation Runtime concurrency evidence is recorded separately at
`docs/release-evidence/foundation-runtime-concurrency/ae6b7e52c656fd8296cf039834ce8d8559b01228-frc-g7-b3-20260702/2026-07-02-211-foundation-alpha-poc-ae6b7e5-foundation-runtime-concurrency.json`,
with readiness status `verified_foundation_runtime_concurrency`, 12 concurrent
requests/sessions/runs, 2 tenants, 4 users, `client_case_timestamps`
concurrency, all seven Foundation Runtime checks passed, and cleanup verified.
That FRC entry is paired `ae6b7e5` Foundation Runtime POC correctness
evidence only; it does not close B3, raise production defaults, claim Docker
sandbox hardening, broaden ordinary-user platform-level multi-run
orchestration exposure, or make #164 `gate closable`. A prior
`bd690f7` G7
probe attempt reached Docker/resource-limit evidence with
`ai-platform:bd690f7-g7-b3-audit-runtime-only-v1`, but it did not produce
accepted formal G7 evidence because the no-masq egress network blocked the
callback exception path, leaving required callback evidence absent. PR #294
merged `codex/g8-b3-status-refresh` into `main` at
`513cc5e2280c35218e7edf297b7f02494e82a164`; that source/test change fixes the 211 sandbox
evidence generator defaults so Docker platform probes bind the callback receiver
on `0.0.0.0` and publish `http://host.docker.internal:{port}/callback` unless
the operator explicitly overrides callback settings. The fix is now part of
current GitHub `main` through PR #296 and has now been exercised by the
then-current-main one-shot verifier run and wrapped into the reviewed repo-local G7
sandbox release-evidence entry named above. PR #297 then advanced G7/B3
evidence-closure tooling and merged at
`4805031fc3333ccbf38224172e4e85e21c0630bb`; that merge is currently observed
in the API/worker runtime image and now has reviewed same-subject G7 live-env
hardening evidence plus same-subject Foundation Runtime concurrency evidence.
The previous clean-source readiness command reported
`source_synced_runtime_pending_followups_open` for the then-selected `bd690f7`
source/runtime subject; after the PR #296 merge, current-main source sync,
then-current-main runtime-only rollout, `ae6b7e5` G7 one-shot verifier,
`ae6b7e5` runtime POC smoke, and `ae6b7e5` FRC evidence, the merged source
changes are deployed/evidence progress only, not clean-source gate closure.
The reviewed `ae6b7e5` Foundation Alpha POC evidence set is recorded under
`docs/release-evidence/foundation-alpha-poc/ae6b7e52c656fd8296cf039834ce8d8559b01228/`
and includes same-subject runtime POC smoke, Auth/RBAC smoke, governance
runtime smoke, release-evidence runtime acceptance, and alert/trace export
runtime acceptance. Fresh local readiness may select those `ae6b7e5` entries
plus the `ae6b7e5` Foundation Runtime concurrency entry as reviewed historical
evidence and report `stage_acceptance_blockers=[]`, but still reports
`foundation_alpha_stage_status=runtime_rollout_required` because current source
and runtime observations are newer than the reviewed `ae6b7e5` evidence
subject. This is a source-rollout/dirty-tree gap, not a reason to reopen G8 or
treat B3 as ordinary-user platform-level multi-run product exposure.

The G8/B3 status boundary should be read as follows:

- G8 is a deferred parking-lot for platform-owned parent/child multi-run
  orchestration. It is not a current ordinary-user product route and is not a
  Foundation Alpha `open_followups` item.
- Historical G8 exposure wording in old evidence/follow-up keys is
  misleading as current status. It must be read as ordinary-user
  platform-owned parent/child multi-run orchestration exposure, not as SDK
  subagent availability or B3 capacity evidence.
- B3 is the capacity evidence track for selected Claude Agent SDK subagent
  fanout profiles, currently `b3_10x4_sdk_subagents`.
- B3 profile evidence must not be treated as ordinary-user platform-level
  multi-run orchestration exposure evidence, and it must not raise production
  defaults or close B3 without operator-reviewed recorded evidence. The latest
  bounded B3 sweep covered all seven harness gates, and every probe remained
  explicitly `probe_completed_not_gate_evidence` with
  `load_test_evidence_status = probe_only_not_recorded`,
  `does_not_mark_gate_recorded = true`, `sent_requests = 10`, and
  `stop_condition_status = passed`. The current verifier interpretation still
  reports `blocked_missing_load_test_evidence` with `missing_sections=[]`; all
  seven recorded load-test gates and the B3 profile evidence remain missing.

Next closure work is operational, not semantic: use the reviewed 2026-07-03
`755e50e` dirty-runtime v2 live-env G7 evidence plus same-subject Foundation
Runtime concurrency evidence as the current dirty-runtime G7 input, keep earlier
`15903fd`, `9c669761`, `ae6b7e5`, `4805031`, and `decf33a` G7/FRC evidence as
historical reviewed inputs, and treat the current `755e50e` status-upgrade
review as absent/not recorded. The reviewed label-clean `15903fd` operator
status-review artifact remains a historical local-partial record because it
explicitly sets `status_upgrade_decision=not_approved_for_closure`, even though
its own G7 runtime blocking list is empty.
Before any status upgrade, `tools/g7_b3_completion_audit.py` can summarize the current
sanitized runtime observation plus optional reviewed release-evidence entries,
Foundation Runtime concurrency evidence, and capacity profile readiness into a
fail-closed G7/B3 blocker list. If an older runtime observation is used, it must
be merged with the later reviewed label-repair, live-env hardening, and FRC
evidence for the same runtime subject before reading blockers; otherwise stale
`stale_runtime_alias_label_mismatch`, fake-provider, or missing-FRC observations
will overstate the current G7 gap. That audit is a planning/control artifact,
not runtime or load evidence by itself. The current dirty-runtime `755e50e`
readback observed API/worker image
`ai-platform:755e50e-g7-b3-principal-userid-fix-v2`, live executor image
`ai-platform:755e50e-g7-b3-principal-userid-fix-v1`,
`SANDBOX_CONTAINER_PROVIDER=docker`, `SANDBOX_EGRESS_POLICY_ENABLED=true`, and
source/runtime labels bound to `755e50e`; legacy in-container source marker
files still read `9c669761` / `28676df`. The latest current dirty-runtime G7
audit can reach `candidate_evidence_requires_review` with `blocking_reasons=[]`
only when paired with the reviewed `755e50e` G7 entry and same-subject FRC
evidence. It still does not close G7 because operator status-upgrade approval is
absent/not recorded for `755e50e`, the runtime image is `build-dirty=true`, and
clean current-main `211 verified` evidence is missing. The source-authority/
local-rollout and B3 load-evidence boundaries are tracked separately, with the
current Admin Runtime capacity-section gap recorded as its own fail-closed B3
visibility blocker. A fresh read-only 211 capacity-runtime
snapshot now exists for the PR #304 runtime subject `decf33a`: API/worker
and the 211 source marker were read back at
`decf33a017e0b97e2a2992f80e3ccdc19152c1f4`, the capacity capture used
`/api/ai/admin/runtime/overview?include_maintenance_cleanup=false`, returned
HTTP `200`, reported all required Admin Runtime sections present, and produced
`readiness.status=blocked_missing_load_test_evidence`. The reviewed, redacted
repository evidence entry is
`docs/release-evidence/capacity-gate-readiness/decf33a017e0b97e2a2992f80e3ccdc19152c1f4/2026-07-02-211-capacity-runtime-readiness-decf33a.json`.
Its
`missing_load_test_gates` list still contains all seven recorded gates, its
`profile_evidence` is empty, and
`production_default_decision=do_not_raise_without_recorded_load_test_evidence`.
This supersedes the earlier `4805031` capacity-pending/HTTP-500 observation for
the currently running `decf33a` runtime subject only. It is not current-main
`211 verified` for merge commit `a9c78ef` and does not close B3.
The newer reviewed `755e50e` capacity visibility entry is now the latest B3
visibility record for current-status reading, and it remains
`blocked_missing_admin_runtime_sections` because the Admin Runtime capacity
section `sandbox` is missing, with all seven recorded load-test gates and the
B3 profile evidence still absent.
Until those checks and evidence close, do not call the current source `211
verified`, `gate closable`, B3 complete, G7 complete, Foundation Alpha
complete, or production-ready.

## Foundation Alpha POC Smoke

Status: latest reviewed 211 POC smoke remains useful historical
runtime-subject evidence; it is not current-main runtime verification and not
production gate closure.

Generate the current operator readiness summary with:

```powershell
python tools/foundation_alpha_readiness.py --format json
python tools/foundation_alpha_readiness.py --format markdown
```

The operator summary must be read through `runtime_source_relation` and
`decision.stage_acceptance_blockers`, not through top-level follow-up names
alone. When the source tree is newer than the verified running API/worker image
and the newer source includes runtime-affecting changes, readiness reports a
runtime-rollout-required state and sets
`current_source_verified_by_running_runtime=false`. That state means the
controlled POC loop evidence remains useful for the recorded runtime subject,
but current source commits still need rollout and smoke evidence before being
treated as runtime-verified.

When it reports `runtime_current_for_runtime_relevant_source`, the latest
running runtime subject still covers all runtime-affecting source. This state is
only valid when `runtime_affecting_dirty_paths` and
`runtime_affecting_changes_since_runtime_subject` are empty. In that case,
docs/tests/evidence/readiness record changes may be newer than the image, so
`current_source_exact_runtime_commit_match=false` can coexist with
`runtime_relevant_source_verified_by_running_runtime=true`. It must not be read
as exact current-source runtime verification:
`current_source_verified_by_running_runtime` and
`controlled_poc_loop_verified_for_current_source` stay false until the running
image matches the current source tree.

Top-level `open_followups` is an operator queue for current follow-up work; it
must not be read as the complete list of blocked expansions. Ordinary-user
platform-level multi-run orchestration remains blocked under
`operator_context.blocked_expansions` as
`ordinary_user_platform_multi_run_orchestration_exposure`; the decision flag
also stays `ordinary_user_multi_agent_allowed=false` as a legacy negative
invariant only. This is intentionally not a current Foundation Alpha follow-up
item.

The 211 source directory is often a synced archive rather than a Git worktree.
For source-only docs/tests/evidence syncs, write a local-only
`.ai-platform-source-snapshot.json` next to `.ai-platform-source-revision` so
`tools/foundation_alpha_readiness.py` can prove the runtime-affecting delta is
empty. Missing, stale, or malformed snapshot markers intentionally fail closed.
For normal clean GitHub checkouts where a runtime subject came from a squashed
PR-branch commit that may not exist locally, the committed source-runtime
relation manifest at
`docs/release-evidence/foundation-alpha-poc/source-runtime-relation-manifest.json`
is the fallback source of truth. The readiness tool may use that manifest only
when it matches the current source tree, or when any newer local delta after the
manifest is runtime-neutral.

S1 post-merge 211 verification requirements: after the #34-#39 stack is merged
under the recorded review exception, do not use earlier 211 health or
historical release evidence as closure evidence. The 211 source snapshot
directory is not a Git worktree, so verification must explicitly bind
`.ai-platform-source-revision` and `.ai-platform-source-snapshot.json` to the
merged source tree commit. It must also prove the repo-local deploy composition,
container image labels, runtime subject, source tree commit, and release-evidence
runtime subject all describe the same merged runtime subject. The readiness JSON
must show the S1 G6 evidence fields `governed_skill_runs`,
`mcp_tool_permission_runtime_controls`, and `memory_context_controls`. The
post-merge evidence must keep
`ordinary_user_multi_agent_allowed=false`, `production_claim_allowed=false`,
`docker_sandbox_hardened_claim_allowed=false`, and
`capacity_default_increase_allowed=false`. GitHub `reviewDecision` stayed empty
for #34-#39; closure uses the explicitly recorded project exception rather than
claiming independent review.

Reviewed S2-0 smoke evidence for runtime subject
`a15c74f0fe98914a893ab7ea784c6be941e0cd71` was recorded on 2026-06-17. Source
and API/worker were advanced to that commit with image
`ai-platform:a15c74f-s2-g6-tool-policy-v2` and image ID
`sha256:034743395992439d3c7370a465ccfe6013975b5243723727b678ef6aa89a2def`.
The 211 repo-local source marker, container source marker, source revision
label, OCI revision label, runtime-subject label, and source_revision alias
label point to `a15c74f`; API health returned `ok`; and compose labels point to
the repo-local deploy composition. This S2-0 refresh used a runtime-only rebase
workaround after package-index access blocked a full Docker build, so it is
valid reviewed runtime evidence for the `a15c74f` subject but not production
release-path closure. At capture time it removed the
`runtime_rollout_required` condition for that runtime-relevant source and kept
the previously closed `g9_runtime_export_and_retention_acceptance` and
`alert_delivery_and_trace_export_211_acceptance` blockers closed for the
runtime-smoke scope. The 2026-06-17 Foundation Runtime concurrency rerun with a
600s run timeout produced verified evidence for 12 concurrent cases/sessions/runs
across 2 tenants and 4 users for the same `a15c74f` subject.

After later runtime-affecting main changes and the backend productization docs
baseline, latest-main readiness had to be refreshed again. GitHub issue #72
recorded a 2026-06-18 211 runtime rollout, smoke, and Foundation Runtime
concurrency evidence set for runtime subject
`569887369e0358f08a408c473395521b22c8e0a7`, using image
`ai-platform:5698873-b0-runtime-rebase` and image ID
`sha256:0892ce8f3ec2b233b60c8f4e1f2d6b7e45570338eb16bfcec8d2d62dcd58dddd`.
The API and worker containers were restarted through the existing 211 compose
stack with the explicit image override, `/api/ai/health` returned `ok`, and the
container/image labels matched the `5698873` source/runtime subject. The refresh
used the approved runtime-only rebase workaround after a full Docker build
stalled during dependency installation. The 211 compose labels point to the
repo-local deploy composition but still record a legacy external runtime env-file
label instead of a fully reconciled repo-local deploy-env authority, so the G0
source-authority caveat remains open. This #72 evidence is now superseded by the
2026-06-19 #98 current-subject B0 refresh for `14808bc`, but remains reviewed
history for the `5698873` runtime subject. This does not raise production concurrency
defaults, close the separate seven-gate capacity-upgrade evidence gate, claim
Docker sandbox hardening, broaden ordinary-user platform-level multi-run
orchestration exposure, enable long-term memory by default, or permit
department rollout.

After further runtime-affecting main changes through `029aa0e`, GitHub issue
#98 refreshed B0 current-main runtime evidence again on 2026-06-19. The 211 API
and worker ran image `ai-platform:14808bc-issue98-runtime-only-v1` with image ID
`sha256:f9acc854357a195e132286fdff8ad601b95c554f92230bdb3d1aa2a55bf22c24`, and
the image labels, container source marker, source snapshot, runtime subject,
runtime source marker, and `/api/ai/health` aligned to
`14808bc8335b5f70e770a55db713b8faf4bfa664`. The reviewed smoke set covers
runtime POC smoke, Auth/RBAC smoke, Admin Runtime governance smoke,
release-evidence runtime acceptance, and alert/trace export runtime acceptance.
The first Foundation Runtime concurrency attempt with the default 240s per-run
timeout remained blocked by queued cases under `workers_busy` /
`active_run_limit_saturated`; the retained diagnostic is capacity input for B3,
not closure evidence. The rerun with a 600s per-run timeout produced verified
Foundation Runtime concurrency evidence for 12 concurrent cases/sessions/runs
across 2 tenants and 4 users, covering run creation, execution, cancel, retry,
queue/admission, sandbox workspace, memory/context, artifact ACL, tool
permission, skill snapshots, and playback. This refresh removes the #98
current-subject `foundation_runtime_concurrency_evidence` readiness blocker for
the `14808bc` runtime-relevant source. It does not raise production concurrency
defaults, close the seven-gate B3 capacity evidence gate, claim Docker sandbox
hardening, broaden ordinary-user platform-level multi-run orchestration
exposure, enable long-term memory by default, permit department rollout, or
close the deploy-env-file/source authority caveat.

After the B3 SDK subagent profile-readiness contract landed in `dde1749`,
GitHub issue #104 refreshed B0 runtime-relevant evidence again on 2026-06-19.
The 211 API and worker ran image `ai-platform:dde1749-issue104-runtime-only-v1`
with image ID
`sha256:95366a442aad0c34597e1eaaf034abe97f99ecf3116a126ff86eee33c332b926`,
and the image labels, container source marker, source snapshot, runtime subject,
runtime source marker, and `/api/ai/health` aligned to
`dde1749c256eaa7e0819c98e0debe6084e73cba2`. The reviewed smoke set covers
runtime POC smoke, Auth/RBAC smoke, Admin Runtime governance smoke,
release-evidence runtime acceptance, and alert/trace export runtime acceptance.
The Foundation Runtime concurrency rerun produced verified evidence for 12
concurrent cases/sessions/runs across 2 tenants and 4 users, covering run
creation, execution, cancel, retry, queue/admission, sandbox workspace,
memory/context, artifact ACL, tool permission, skill snapshots, playback, and
cleanup. This refresh removes the #104 current-subject
`foundation_runtime_concurrency_evidence` readiness blocker for the `dde1749`
runtime-relevant source. It used the runtime-only rebase workaround and does
not raise production concurrency defaults, close the seven-gate B3 capacity
evidence gate, claim Docker sandbox hardening, broaden ordinary-user
platform-level multi-run orchestration exposure, enable long-term memory by
default, permit department rollout, or close the deploy-env-file/source
authority caveat.

After the backend stage closure evidence consumers landed in `75ab69b`, GitHub
issue #112 refreshed B0 runtime-relevant evidence again on 2026-06-19. The 211
API and worker ran image `ai-platform:75ab69b-issue112-runtime-only-v1` with
image ID
`sha256:a16d5112c6e8fd912dc87b6c65d2e42db8c48e8c9ba1939802915ef3611d27dc`,
and the image labels, container source marker, source snapshot, runtime subject,
runtime source marker, and `/api/ai/health` aligned to
`75ab69b939d0bf13987ac044ce0dc498f5eab999`. The reviewed smoke set covers
runtime POC smoke, Auth/RBAC smoke, Admin Runtime governance smoke,
release-evidence runtime acceptance, and alert/trace export runtime acceptance.
The Foundation Runtime concurrency rerun produced verified evidence for 12
concurrent cases/sessions/runs across 2 tenants and 4 users, covering run
creation, execution, cancel, retry, queue/admission, sandbox workspace,
memory/context, artifact ACL, tool permission, skill snapshots, playback, and
cleanup. This refresh removes the #112 current-subject
`foundation_runtime_concurrency_evidence` readiness blocker for the `75ab69b`
runtime-relevant source. It used the runtime-only rebase workaround and does
not raise production concurrency defaults, close the seven-gate B3 capacity
evidence gate, claim Docker sandbox hardening, broaden ordinary-user
platform-level multi-run orchestration exposure, enable long-term memory by
default, permit department rollout, or close the deploy-env-file/source
authority caveat. The #104 `dde1749`
refresh remains reviewed history for its runtime subject.

After the B3 operator snapshot contract landed in `87528bf`, GitHub issue #124
refreshed B0 latest-main evidence again on 2026-06-19. The 211 API and worker
ran image `ai-platform:87528bf-issue124-runtime-only-v2` with image ID
`sha256:e8b9d59e31e350b8576bffa58cba23ac2617d60ab1def9844a1038a5facf6a5c`,
and the image labels, container source marker, source snapshot, runtime subject,
runtime source marker, and `/api/ai/health` aligned to
`87528bf30609092c3c4e947bdca477768af3f8e5`. The reviewed smoke set covers
runtime POC smoke, Auth/RBAC smoke, Admin Runtime governance smoke,
release-evidence runtime acceptance, and alert/trace export runtime acceptance.
The Foundation Runtime concurrency rerun produced verified evidence for 12
concurrent cases/sessions/runs across 2 tenants and 4 users, covering run
creation, execution, cancel, retry, queue/admission, sandbox workspace,
memory/context, artifact ACL, tool permission, skill snapshots, playback, and
cleanup. This refresh removes the #124 current-subject
`foundation_runtime_concurrency_evidence` readiness blocker for the `87528bf`
latest-main source. It used the runtime-only rebase workaround and does not
raise production concurrency defaults, close the seven-gate B3 capacity evidence
gate, claim Docker sandbox hardening, broaden ordinary-user platform-level
multi-run orchestration exposure, enable long-term memory by default, permit
department rollout, or close the deploy-env-file/source authority caveat. The #112 `75ab69b` refresh
remains reviewed history for its runtime subject.

After PR #137 landed at current `main`
`4039e4bd870d99201da4fc0f002f76f2b5c4a892`, GitHub issue #138 refreshed B0
current-source evidence on 2026-06-20. The 211 API and worker ran image
`ai-platform:4039e4b-issue138-runtime-only-v1` with image ID
`sha256:1ce0b329f89d8d85affd309c1df6b0a5953eae0f3c74e8c05084e039cc619c5c`,
and the image labels, container source marker, source snapshot, runtime subject,
runtime source marker, and `/api/ai/health` align to
`4039e4bd870d99201da4fc0f002f76f2b5c4a892`. The reviewed smoke set covers
runtime POC smoke, Auth/RBAC smoke, Admin Runtime governance smoke,
release-evidence runtime acceptance, and alert/trace export runtime acceptance.
The Foundation Runtime concurrency rerun produced verified evidence for 12
concurrent sessions/runs across 2 tenants and 4 users, covering run creation,
execution, cancel, retry, queue/admission, sandbox workspace, memory/context,
artifact ACL, tool permission, skill snapshots, playback, and cleanup. This
refresh removes the #138 current-subject `foundation_runtime_concurrency_evidence`
readiness blocker for the `4039e4b` runtime-relevant source. It used an isolated
archive snapshot and runtime-only rebase workaround; the configured 211 source
path remains dirty and untouched, and the compose environment-file label still
records the external env-file caveat. This is reviewed 211 runtime-subject
evidence only for the narrow #138 B0 runtime-relevant evidence scope; it is not
current-source `211 verified` status and does not close G7/B3 or #164, close G0
source authority, raise production concurrency defaults, claim Docker sandbox
hardening, or enable ordinary-user platform-level multi-run orchestration
exposure.

After PR #171 merged into `main`
`dab7dbc30a43725d58a8a6b2ddb8e52888f303b5`, GitHub issue #164 recorded B0
runtime-subject evidence on 2026-06-22. The 211 API and worker ran image
`ai-platform:dab7dbc-b0-smoke-verifier-v2` with image ID
`sha256:da92722aaa60775958c1939c53068a88fed52fac9d934d5f6d4fc67c6774f0d6`,
and the image labels, container source marker, runtime subject, runtime source
marker, and `/api/ai/health` align to
`dab7dbc30a43725d58a8a6b2ddb8e52888f303b5`. The reviewed smoke set covers
runtime POC smoke, Auth/RBAC smoke, Admin Runtime governance smoke,
release-evidence runtime acceptance, and alert/trace export runtime acceptance.
The runtime POC smoke includes `word_review_attachment_chat` with
`run.status=succeeded` and `summary_source=chat_stream`, plus
`governed_skill_runs.verified=true` for pinned `baoyu-translate` and
`qa-file-reviewer` Skill snapshots. The Foundation Runtime concurrency rerun
produced verified evidence for 12 concurrent requests/sessions/runs across 2
tenants and 4 users, covering run creation, execution, cancel, retry,
queue/admission, sandbox workspace, memory/context, artifact ACL, tool
permission, skill snapshots, playback, fixture preparation, and cleanup. This
refresh removes the #164 current-subject `foundation_runtime_concurrency_evidence`
gap for the `dab7dbc` runtime subject. It used a runtime-only marker rebase
workaround; the configured 211 source path remains a dirty synced source, and
the compose environment-file label still records the external env-file caveat.
This records reviewed 211 runtime-subject evidence for #164 and is superseded
by the post-PR #173 `d94d274` refresh below for current-main #164 closure
evaluation. It does not constitute current G7/B3 closure evidence for the
already-closed historical #164, does not close G0 source
authority, does not close B1/B2/B3 product gates, does not raise production
concurrency defaults, does not claim Docker sandbox hardening, and does not
enable ordinary-user platform-level multi-run orchestration exposure.

After PR #173 merged into `main`
`d94d274b48eaf076dda74d56312a1ada094edaff`, #164 received a post-merge
current-main 211 rollout and evidence refresh on 2026-06-22. The 211 source
snapshot marker, runtime subject, runtime source marker, OCI revision label,
hyphen/underscore source revision labels, and `/api/ai/health` align to
`d94d274`; the legacy dotted `ai-platform.source.revision` label still records
`dab7dbc` as a runtime-only rebase caveat. The API and worker ran image
`ai-platform:d94d274-issue164-post-pr173-runtime-only-v1` with image ID
`sha256:70552c8bbe14db5b46003a33157ea9d0383d9150a44bb9e94b036ec8642ebd25`.
The first official B0 smoke used a 240s word-review wait and failed only
`governed_skill_runs` and `word_review_attachment_chat` while the fresh
`qa-file-reviewer` run was still running. A direct post-window DB check showed
that run later succeeded with `reviewed_docx` and `report_txt` artifacts and a
`qa-file-reviewer` `used=true` snapshot from `platform_controlled_runner`.
The accepted current-main smoke rerun used a 420s word-review wait and produced
`ok=true`, governed Skill snapshots for `baoyu-translate` and
`qa-file-reviewer`, a succeeded `word_review_attachment_chat` run with reviewed
DOCX evidence, playback download/preview projection, and no private projection
leaks. Auth/RBAC, Admin Runtime governance, release-evidence runtime
acceptance, and alert/trace export runtime acceptance also passed for the same
runtime subject.

Foundation Runtime concurrency for the same `d94d274` runtime subject first
blocked with a 600s per-run timeout because worker capacity was saturated and
only 5 runs/sessions completed before queued/running cases timed out. The
retained diagnostic records worker-capacity pressure for B3/capacity planning.
The accepted rerun used a 1200s per-run timeout and verified 12 concurrent
requests/sessions/runs across 2 tenants and 4 users, covering run creation,
execution, cancel, retry, queue/admission, sandbox workspace, memory/context,
artifact ACL, tool permission, skill snapshots, playback, fixture preparation,
and cleanup. This removed the #164 current-subject
`foundation_runtime_concurrency_evidence` blocker for `d94d274`, and is now
superseded by the 2bc3a35 runtime-subject refresh below. It remains controlled B0
runtime history only: it does not close production readiness, G0
source-authority env-layout reconciliation, Docker sandbox hardening,
ordinary-user platform-level multi-run orchestration exposure, packaged
frontend image acceptance, department rollout, or production concurrency
default increases.

After PR #180 merged into `main`
`2bc3a357605fca7a9bf63b1d69ba89ba70fc406f`, #164 received a 211 runtime and
evidence refresh for that runtime subject on 2026-06-22. The 211 API and worker run image
`ai-platform:2bc3a35-main-runtime-only-v1` with image ID
`sha256:ce979b612e2d81e935f186e6613bb4f41a80d964fd8e84d7232e6531887b8ccc`.
The runtime source marker, runtime subject, OCI revision label,
hyphen/underscore source revision labels, and `/api/ai/health` align to
`2bc3a35`; the legacy dotted `ai-platform.source.revision` label still records
`dab7dbc` as a runtime-only rebase caveat. The official B0 smoke used a 420s
word-review wait and produced `ok=true`, governed Skill snapshots for
`baoyu-translate` and `qa-file-reviewer`, a succeeded
`word_review_attachment_chat` run with reviewed DOCX evidence, upload
attachment worker-start evidence, playback download/preview projection, and no
private projection leaks. Auth/RBAC, Admin Runtime governance, release-evidence
runtime acceptance, and alert/trace export runtime acceptance also passed for
the same runtime subject.

Foundation Runtime concurrency for the same `2bc3a35` runtime subject verified
12 concurrent requests/sessions/runs across 2 tenants and 4 users, covering run
creation, execution, cancel, retry, queue/admission, sandbox workspace,
memory/context, artifact ACL, tool permission, skill snapshots, playback,
fixture preparation, and cleanup. This removes the #164
`foundation_runtime_concurrency_evidence` blocker for the named `2bc3a35`
runtime subject. This is now superseded by the post-PR #182 `a4bded0`
runtime-subject refresh below and remains controlled B0 runtime history only.

After PR #206 merged into `main` at
`e4c0e9d0298c684df369afecd29ec902fcc2221d`, #164 received a 211 runtime and
evidence refresh for that runtime subject on 2026-06-24. The 211 API and worker
run image `ai-platform:e4c0e9d-issue164-post-pr206-runtime-only-v2` with image ID
`sha256:22ad0ef518d0ae206c349e8b647d801dfeab0d019f9155f35b033e3bda306090`.
The runtime source marker, runtime subject, OCI revision label,
hyphen/underscore source revision labels, and `/api/ai/health` align to
`e4c0e9d`. The official B0 smoke used a 420s word-review wait and produced
`ok=true`, governed Skill snapshots for `baoyu-translate` and
`qa-file-reviewer`, a succeeded `word_review_attachment_chat` run
`run_b2c6a437bfb14ac7b880e1c5ac780dad` with reviewed DOCX/report evidence,
upload attachment worker-start evidence, context public
projection, company-login audit evidence, and no private projection leaks. Auth/RBAC, Admin Runtime
governance, release-evidence runtime acceptance, and alert/trace export runtime
acceptance also passed for the same runtime subject.

Foundation Runtime concurrency refresh for the same `e4c0e9d` runtime subject
verified 12 concurrent requests/sessions/runs across 2 tenants and 4 users
after the formal rerun. It covered run creation, execution, cancel, retry,
queue/admission, sandbox workspace, memory/context,
artifact ACL, tool permission, skill snapshots, playback, fixture preparation,
and cleanup, with readiness status `verified_foundation_runtime_concurrency`.
This removes the `foundation_runtime_concurrency_evidence` blocker for the
named `e4c0e9d` runtime subject only. Docker sandbox hardening, ordinary-user
platform-level multi-run orchestration exposure, packaged frontend image acceptance, department rollout,
and B3 capacity evidence remain open. This is controlled Foundation Alpha POC
runtime evidence only: it does not constitute current G7/B3 closure evidence
for the already-closed historical #164, does not close G0 source authority,
does not close B1/B2/B3 product gates, does not raise
production concurrency defaults, does not claim Docker sandbox hardening, and
does not enable ordinary-user platform-level multi-run orchestration exposure.

After later current-branch changes through `96f27bb9bc8e415faddada2cec0fbfb6ecdcf92c`,
the latest reviewed B0 runtime-relevant smoke evidence is runtime subject
`96f27bb9bc8e415faddada2cec0fbfb6ecdcf92c`, recorded on 2026-06-30. The 211
API and worker ran image `ai-platform:96f27bb-b0-current-source-runtime-only-v2`
with image ID
`sha256:2640a006b4995bc01ebba965dc6b5b22be1bd28f6babc4b5a9bee7c91ce71e17`.
The formal B0 smoke produced `ok=true`, governed Skill snapshots for
`baoyu-translate` and `qa-file-reviewer`, a succeeded
`word_review_attachment_chat` run
`run_4273b61f1462499daccce16d775f643f` with reviewed DOCX/report evidence,
context public projection, artifact download/preview isolation, and no private
projection leaks. Auth/RBAC, Admin Runtime governance, release-evidence runtime
acceptance, and alert/trace export runtime acceptance also passed for the same
runtime subject.

The committed source-runtime relation manifest records `96f27bb` as both source
tree and runtime subject with no runtime-affecting delta. Current local
readiness must still be read through `runtime_source_relation`: it may report
`runtime_current_for_runtime_relevant_source` with
`runtime_relevant_source_verified_by_running_runtime=true`, and
`runtime_rollout_required_for_current_source=false`, even when the clean current
source can still be ahead of the running image by runtime-neutral
docs/evidence/tests commits. This removes the `runtime_rollout_required` state
for the current runtime-relevant source without claiming exact current-source
runtime verification.

Foundation Runtime concurrency is now recorded for the `96f27bb` runtime
subject. The 2026-06-30 current-subject verifier produced
`verified_foundation_runtime_concurrency` with 12 concurrent
requests/sessions/runs across 2 tenants and 4 users, covering run creation,
execution, cancel, retry, queue/admission, sandbox workspace, memory/context,
artifact ACL, tool permission, skill snapshots, playback, fixture preparation,
and cleanup. This removes `foundation_runtime_concurrency_evidence` as a
current B0 blocker for the named `96f27bb` runtime subject. The `96f27bb` smoke
and current-subject FRC evidence together still do not make #164 `gate
closable`, do not close G0 source authority, do not close B1/B2/B3/B4 product
gates, do not raise production concurrency defaults, do not claim Docker sandbox
hardening, do not enable ordinary-user platform-level multi-run orchestration
exposure, do not close packaged frontend image acceptance, do not permit
department rollout, and do not claim production readiness.

The immediately superseded B0 runtime-subject refresh is
`c3d6525d8980c43ce9d13a2fd9016bbe61597327`. It remains reviewed
runtime-subject history only because the B0 runtime-relevant evidence has since
been refreshed to `96f27bb`.

The next superseded B0 runtime-subject refresh is
`442aa39ca81f2daf00c49747c53491ab978d84b7`. It remains reviewed
runtime-subject history only because the B0 runtime-relevant evidence has since
been refreshed past `442aa39`.

The next superseded #164 runtime-subject refresh is
`e4c0e9d0298c684df369afecd29ec902fcc2221d` from PR #206. It remains reviewed
runtime-subject history only because the B0 runtime-relevant evidence has since
been refreshed past `e4c0e9d`.

The next superseded #164 runtime-subject refresh is
`e7558cc6a86a378ee747eb02ae9a4496ca40041a` from PR #202. It remains reviewed
runtime-subject history only because the live 211 API/worker runtime has since
been refreshed past `e7558cc`.

The next superseded #164 runtime-subject refresh is
`17dc3ae111cd1538fcf1412558d9d19b93f5b918` from PR #199. It remains reviewed
runtime-subject history only because the live 211 API/worker runtime has since
been refreshed past `17dc3ae`.

The next superseded #164 runtime-subject refresh is
`0a9e70a41f2e86afce2be2294b21d2f5651d448d` from PR #196. It remains reviewed
runtime-subject history only because the live 211 API/worker runtime has since
been refreshed past `0a9e70a`.

The next superseded #164 runtime-subject refresh is
`df85a9fb3266aab92a2ca4122db06d4ec7a00175` from PR #187. It remains reviewed
runtime-subject history only.

The next superseded #164 runtime-subject refresh is
`a4bded07852933ad77f830a24c7d18763ecf519b`, #164 received a 211 runtime and
evidence refresh for that runtime subject on 2026-06-23. The 211 API and worker
run image `ai-platform:a4bded0-main-runtime-only-v2` with image ID
`sha256:543a980f54690b77393088df658c9eeebc7084c7ee59fd013d44a8fc56d99c1d`.
The runtime source marker, runtime subject, OCI revision label,
hyphen/underscore source revision labels, and `/api/ai/health` align to
`a4bded0`. The official B0 smoke used a 420s word-review wait and produced
`ok=true`, governed Skill snapshots for `baoyu-translate` and
`qa-file-reviewer`, a succeeded `word_review_attachment_chat` run with reviewed
DOCX evidence, upload/chat status `200`, context public projection, and no
private projection leaks. Auth/RBAC, Admin Runtime governance, release-evidence
runtime acceptance, and alert/trace export runtime acceptance also passed for
the same runtime subject.

Foundation Runtime concurrency for the same `a4bded0` runtime subject verified
12 concurrent requests/sessions/runs across 2 tenants and 4 users, covering run
creation, execution, cancel, retry, queue/admission, sandbox workspace,
memory/context, artifact ACL, tool permission, skill snapshots, playback,
fixture preparation, and cleanup. This removes the #164
`foundation_runtime_concurrency_evidence` blocker for the named `a4bded0`
runtime subject. The latest readiness status is `211_verified_followups_open`
in the 211 post-FRC capture before this evidence/docs commit. After committing
reviewed evidence docs, repository readiness must be read through
`runtime_source_relation` and may report
`runtime_current_for_runtime_relevant_source` until the evidence commit itself
is deployed; that state is not #164 closure. The separate
`ordinary_user_acceptance_for_quarantined_legacy_routes` follow-up remains open
alongside Docker sandbox hardening and ordinary-user platform-level multi-run orchestration exposure.
This is controlled B0 runtime evidence only: it does not make #164 `gate
closable`, does not close G0 source authority, does not close B1/B2/B3 product
gates, does not raise production concurrency defaults, does not claim Docker
sandbox hardening, and does not enable ordinary-user platform-level multi-run orchestration exposure.

The prior Foundation Alpha historical baseline remains
`380de6bf9ffed5167f9bb2eaee8e63612a52c124`. On 2026-06-15, source and
API/worker were advanced to that commit with image
`ai-platform:380de6b-merged-main-runtime` and image ID
`sha256:e36e4dfad072cdd12b841019db3ccbcdef4b63ccf5262869c994757fef5663f9`.
The 211 repo-local source marker, container source marker, source revision
label, OCI revision label, runtime-subject label, and source_revision alias
label point to `380de6b`; API health returned `ok`; and compose labels point to
the repo-local deploy composition. Compose still uses an existing external
runtime env file through `--env-file` without copying or printing values, so
this evidence does not by itself close production or G0 deployment-layout
follow-ups.

PR #40 later advanced the live 211 API/worker runtime to
`5d3d7e2207d625817d193898c22d29d2f487fa4b` with image
`ai-platform:5d3d7e2-foundation-runtime-concurrency-pr40` for the focused
Foundation Runtime concurrency rerun. The 211 source marker, API/worker OCI
revision labels, and `ai-platform.runtime_subject` labels matched `5d3d7e2`,
and `/api/ai/health` returned `ok`. This runtime evidence is deliberately
narrower than the dff48fb broader POC smoke/auth/governance set: it refreshes
the Foundation Runtime concurrency gate after verifier hardening, but does not
replace the broader POC evidence set or close production/G0 deployment-layout
follow-ups.

PR #40 then refreshed the live 211 API/worker runtime again to
`79495bf4954017351db6d19494a16099fe2ee0bf` with image
`ai-platform:79495bf-foundation-runtime-concurrency-pr40` for the current-head
Foundation Runtime concurrency rerun. The 211 source marker, container source
markers, source revision labels, source_revision aliases, runtime-subject
labels, OCI revision labels, and `/app/.ai-platform-source-snapshot.json`
matched `79495bf`; `/api/ai/health` returned `ok`; and source plus API/worker
container compile checks passed. A later 211 broad evidence refresh on the same
runtime subject passed runtime POC smoke, Auth/RBAC smoke, Admin Runtime
governance smoke, release-evidence runtime acceptance, and alert/trace export
runtime acceptance. This replaces both the `5d3d7e2` focused concurrency
evidence and the broader `dff48fb` POC smoke/auth/governance evidence as the
current PR #40 Foundation Alpha POC evidence set, but it still does not close
production/G0 deployment-layout follow-ups.

The full 442aa39 B0 POC smoke refresh includes
reviewed, redacted entries for runtime POC smoke, Auth/RBAC smoke, Admin Runtime
governance smoke, release-evidence runtime acceptance, and alert/trace export
runtime acceptance:
`docs/release-evidence/foundation-alpha-poc/442aa39ca81f2daf00c49747c53491ab978d84b7/2026-06-30-211-foundation-alpha-poc-442aa39-runtime-poc-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/442aa39ca81f2daf00c49747c53491ab978d84b7/2026-06-30-211-foundation-alpha-poc-442aa39-auth-rbac-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/442aa39ca81f2daf00c49747c53491ab978d84b7/2026-06-30-211-foundation-alpha-poc-442aa39-governance-runtime-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/442aa39ca81f2daf00c49747c53491ab978d84b7/2026-06-30-211-foundation-alpha-poc-442aa39-release-evidence-runtime-acceptance.json`, and
`docs/release-evidence/foundation-alpha-poc/442aa39ca81f2daf00c49747c53491ab978d84b7/2026-06-30-211-foundation-alpha-poc-442aa39-alert-trace-export-runtime-acceptance.json`.
The latest verified but superseded Foundation Runtime concurrency entries are
`docs/release-evidence/foundation-runtime-concurrency/845faf7ed0eba11fb8d90fd59048d1c752acc61c-frc-b0-20260630/2026-06-30-211-foundation-alpha-poc-845faf7-foundation-runtime-concurrency.json`,
`docs/release-evidence/foundation-runtime-concurrency/845faf7ed0eba11fb8d90fd59048d1c752acc61c-frc-b0-20260630/2026-06-30-211-foundation-alpha-poc-845faf7-foundation-runtime-concurrency-readiness.json`,
and
`docs/release-evidence/foundation-runtime-concurrency/845faf7ed0eba11fb8d90fd59048d1c752acc61c-frc-b0-20260630/2026-06-30-211-foundation-alpha-poc-845faf7-foundation-runtime-concurrency-summary.md`.
This set is retained as superseded B0 history after the later `96f27bb`
runtime-subject refresh. It does not support the current B0
`runtime_current_for_runtime_relevant_source` claim, does not provide current
`foundation_runtime_concurrency_evidence`, and does not prove #164 closure.

The superseded e4c0e9d #164 runtime-subject B0 POC smoke refresh includes reviewed,
redacted entries for runtime POC smoke, Auth/RBAC smoke, Admin Runtime
governance smoke, release-evidence runtime acceptance, and alert/trace export
runtime acceptance:
`docs/release-evidence/foundation-alpha-poc/e4c0e9d0298c684df369afecd29ec902fcc2221d/2026-06-24-211-foundation-alpha-poc-e4c0e9d-runtime-poc-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/e4c0e9d0298c684df369afecd29ec902fcc2221d/2026-06-24-211-foundation-alpha-poc-e4c0e9d-auth-rbac-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/e4c0e9d0298c684df369afecd29ec902fcc2221d/2026-06-24-211-foundation-alpha-poc-e4c0e9d-governance-runtime-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/e4c0e9d0298c684df369afecd29ec902fcc2221d/2026-06-24-211-foundation-alpha-poc-e4c0e9d-release-evidence-runtime-acceptance.json`, and
`docs/release-evidence/foundation-alpha-poc/e4c0e9d0298c684df369afecd29ec902fcc2221d/2026-06-24-211-foundation-alpha-poc-e4c0e9d-alert-trace-export-runtime-acceptance.json`.
The paired Foundation Runtime concurrency entries are
`docs/release-evidence/foundation-runtime-concurrency/e4c0e9d0298c684df369afecd29ec902fcc2221d-frc-b0-20260624/2026-06-24-e4c0e9d-foundation-runtime-concurrency.json`
and
`docs/release-evidence/foundation-runtime-concurrency/e4c0e9d0298c684df369afecd29ec902fcc2221d-frc-b0-20260624/2026-06-24-e4c0e9d-foundation-runtime-concurrency-readiness.json`.
Together these support `211_verified_followups_open` for the named runtime
subject, not #164 closure.

The superseded a4bded0 #164 runtime-subject B0 POC smoke refresh includes reviewed,
redacted entries for runtime POC smoke, Auth/RBAC smoke, Admin Runtime
governance smoke, release-evidence runtime acceptance, and alert/trace export
runtime acceptance:
`docs/release-evidence/foundation-alpha-poc/a4bded07852933ad77f830a24c7d18763ecf519b/2026-06-23-211-foundation-alpha-poc-a4bded0-runtime-poc-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/a4bded07852933ad77f830a24c7d18763ecf519b/2026-06-23-211-foundation-alpha-poc-a4bded0-auth-rbac-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/a4bded07852933ad77f830a24c7d18763ecf519b/2026-06-23-211-foundation-alpha-poc-a4bded0-governance-runtime-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/a4bded07852933ad77f830a24c7d18763ecf519b/2026-06-23-211-foundation-alpha-poc-a4bded0-release-evidence-runtime-acceptance.json`, and
`docs/release-evidence/foundation-alpha-poc/a4bded07852933ad77f830a24c7d18763ecf519b/2026-06-23-211-foundation-alpha-poc-a4bded0-alert-trace-export-runtime-acceptance.json`.
The a4bded0 2026-06-23 Foundation Runtime concurrency rerun is accepted
Foundation Runtime concurrency evidence for the named `a4bded0` runtime subject:
`docs/release-evidence/foundation-runtime-concurrency/a4bded07852933ad77f830a24c7d18763ecf519b-frc-b0-20260623/2026-06-23-a4bded0-foundation-runtime-concurrency.json`.

The superseded 2bc3a35 #164 runtime-subject B0 POC smoke refresh includes reviewed,
redacted entries for runtime POC smoke, Auth/RBAC smoke, Admin Runtime
governance smoke, release-evidence runtime acceptance, and alert/trace export
runtime acceptance:
`docs/release-evidence/foundation-alpha-poc/2bc3a357605fca7a9bf63b1d69ba89ba70fc406f/2026-06-22-211-foundation-alpha-poc-2bc3a35-runtime-poc-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/2bc3a357605fca7a9bf63b1d69ba89ba70fc406f/2026-06-22-211-foundation-alpha-poc-2bc3a35-auth-rbac-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/2bc3a357605fca7a9bf63b1d69ba89ba70fc406f/2026-06-22-211-foundation-alpha-poc-2bc3a35-governance-runtime-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/2bc3a357605fca7a9bf63b1d69ba89ba70fc406f/2026-06-22-211-foundation-alpha-poc-2bc3a35-release-evidence-runtime-acceptance.json`, and
`docs/release-evidence/foundation-alpha-poc/2bc3a357605fca7a9bf63b1d69ba89ba70fc406f/2026-06-22-211-foundation-alpha-poc-2bc3a35-alert-trace-export-runtime-acceptance.json`.
The 2bc3a35 2026-06-22 Foundation Runtime concurrency rerun is accepted
Foundation Runtime concurrency evidence for the named `2bc3a35` runtime subject:
`docs/release-evidence/foundation-runtime-concurrency/2bc3a357605fca7a9bf63b1d69ba89ba70fc406f-frc-b0-20260622/2026-06-22-2bc3a35-foundation-runtime-concurrency.json`.

The superseded d94d274 #164 post-PR #173 B0 POC smoke refresh includes reviewed,
redacted entries for runtime POC smoke, Auth/RBAC smoke, Admin Runtime
governance smoke, release-evidence runtime acceptance, and alert/trace export
runtime acceptance:
`docs/release-evidence/foundation-alpha-poc/d94d274b48eaf076dda74d56312a1ada094edaff/2026-06-22-211-foundation-alpha-poc-d94d274-runtime-poc-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/d94d274b48eaf076dda74d56312a1ada094edaff/2026-06-22-211-foundation-alpha-poc-d94d274-auth-rbac-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/d94d274b48eaf076dda74d56312a1ada094edaff/2026-06-22-211-foundation-alpha-poc-d94d274-governance-runtime-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/d94d274b48eaf076dda74d56312a1ada094edaff/2026-06-22-211-foundation-alpha-poc-d94d274-release-evidence-runtime-acceptance.json`, and
`docs/release-evidence/foundation-alpha-poc/d94d274b48eaf076dda74d56312a1ada094edaff/2026-06-22-211-foundation-alpha-poc-d94d274-alert-trace-export-runtime-acceptance.json`.
The d94d274 2026-06-22 Foundation Runtime concurrency rerun is accepted
Foundation Runtime concurrency evidence for the named `d94d274` runtime subject:
`docs/release-evidence/foundation-runtime-concurrency/d94d274b48eaf076dda74d56312a1ada094edaff-frc-b0-20260622/2026-06-22-d94d274-foundation-runtime-concurrency.json`.

The superseded dab7dbc #164 B0 POC smoke refresh includes reviewed, redacted entries for
runtime POC smoke, Auth/RBAC smoke, Admin Runtime governance smoke,
release-evidence runtime acceptance, and alert/trace export runtime acceptance:
`docs/release-evidence/foundation-alpha-poc/dab7dbc30a43725d58a8a6b2ddb8e52888f303b5/2026-06-22-211-foundation-alpha-poc-dab7dbc-runtime-poc-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/dab7dbc30a43725d58a8a6b2ddb8e52888f303b5/2026-06-22-211-foundation-alpha-poc-dab7dbc-auth-rbac-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/dab7dbc30a43725d58a8a6b2ddb8e52888f303b5/2026-06-22-211-foundation-alpha-poc-dab7dbc-governance-runtime-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/dab7dbc30a43725d58a8a6b2ddb8e52888f303b5/2026-06-22-211-foundation-alpha-poc-dab7dbc-release-evidence-runtime-acceptance.json`, and
`docs/release-evidence/foundation-alpha-poc/dab7dbc30a43725d58a8a6b2ddb8e52888f303b5/2026-06-22-211-foundation-alpha-poc-dab7dbc-alert-trace-export-runtime-acceptance.json`.
The dab7dbc 2026-06-22 Foundation Runtime concurrency rerun is accepted
Foundation Runtime concurrency evidence for the named `dab7dbc` runtime subject:
`docs/release-evidence/foundation-runtime-concurrency/dab7dbc-frc-b0-20260622/2026-06-22-dab7dbc-foundation-runtime-concurrency.json`.
After PR #165 merged as `e8e8a0a4dc31b01f5c36ce4b8652165454a2867c`, 211 API and
worker were restarted on `ai-platform:e8e8a0a-issue164-runtime-only-v1` and
four reviewed, redacted partial B0 entries passed for Auth/RBAC, Admin Runtime
governance, release-evidence runtime acceptance, and alert/trace export runtime
acceptance. These records are useful 211 evidence for #164 but are not a full
B0 closure set: the `e8e8a0a` runtime still lacks a passing runtime POC smoke
entry and successful Foundation Runtime concurrency evidence. A 2026-06-21
container-side model-gateway probe returned HTTP 402 `Insufficient Balance` for
both `deepseek-v4-flash` and `deepseek-v4-pro`, so readiness must keep reporting
`runtime_rollout_required` / `foundation_runtime_concurrency_evidence` until the
gateway is restored and successful evidence is recorded:
`docs/release-evidence/foundation-alpha-poc/e8e8a0a4dc31b01f5c36ce4b8652165454a2867c/2026-06-21-211-foundation-alpha-poc-e8e8a0a-auth-rbac-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/e8e8a0a4dc31b01f5c36ce4b8652165454a2867c/2026-06-21-211-foundation-alpha-poc-e8e8a0a-governance-runtime-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/e8e8a0a4dc31b01f5c36ce4b8652165454a2867c/2026-06-21-211-foundation-alpha-poc-e8e8a0a-release-evidence-runtime-acceptance.json`, and
`docs/release-evidence/foundation-alpha-poc/e8e8a0a4dc31b01f5c36ce4b8652165454a2867c/2026-06-21-211-foundation-alpha-poc-e8e8a0a-alert-trace-export-runtime-acceptance.json`.
The full 87528bf #124 B0 POC smoke refresh is retained as superseded reviewed
history and includes reviewed, redacted entries for
runtime POC smoke, Auth/RBAC smoke, Admin Runtime governance smoke,
release-evidence runtime acceptance, and alert/trace export runtime acceptance:
`docs/release-evidence/foundation-alpha-poc/87528bf30609092c3c4e947bdca477768af3f8e5/2026-06-19-211-foundation-alpha-poc-87528bf-runtime-poc-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/87528bf30609092c3c4e947bdca477768af3f8e5/2026-06-19-211-foundation-alpha-poc-87528bf-auth-rbac-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/87528bf30609092c3c4e947bdca477768af3f8e5/2026-06-19-211-foundation-alpha-poc-87528bf-governance-runtime-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/87528bf30609092c3c4e947bdca477768af3f8e5/2026-06-19-211-foundation-alpha-poc-87528bf-release-evidence-runtime-acceptance.json`, and
`docs/release-evidence/foundation-alpha-poc/87528bf30609092c3c4e947bdca477768af3f8e5/2026-06-19-211-foundation-alpha-poc-87528bf-alert-trace-export-runtime-acceptance.json`.
The 87528bf 2026-06-19 Foundation Runtime concurrency rerun is retained as
superseded accepted Foundation Runtime concurrency evidence for this prior
latest-main source:
`docs/release-evidence/foundation-runtime-concurrency/87528bf30609092c3c4e947bdca477768af3f8e5-frc-b0-20260619/2026-06-19-211-foundation-alpha-poc-87528bf-foundation-runtime-concurrency.json`.
The full 75ab69b #112 B0 POC smoke refresh is retained as superseded reviewed
history and includes reviewed, redacted entries for
runtime POC smoke, Auth/RBAC smoke, Admin Runtime governance smoke,
release-evidence runtime acceptance, and alert/trace export runtime acceptance:
`docs/release-evidence/foundation-alpha-poc/75ab69b939d0bf13987ac044ce0dc498f5eab999/2026-06-19-211-foundation-alpha-poc-75ab69b-runtime-poc-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/75ab69b939d0bf13987ac044ce0dc498f5eab999/2026-06-19-211-foundation-alpha-poc-75ab69b-auth-rbac-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/75ab69b939d0bf13987ac044ce0dc498f5eab999/2026-06-19-211-foundation-alpha-poc-75ab69b-governance-runtime-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/75ab69b939d0bf13987ac044ce0dc498f5eab999/2026-06-19-211-foundation-alpha-poc-75ab69b-release-evidence-runtime-acceptance.json`, and
`docs/release-evidence/foundation-alpha-poc/75ab69b939d0bf13987ac044ce0dc498f5eab999/2026-06-19-211-foundation-alpha-poc-75ab69b-alert-trace-export-runtime-acceptance.json`.
The 75ab69b 2026-06-19 Foundation Runtime concurrency rerun is accepted
Foundation Runtime concurrency evidence for this runtime-relevant source:
`docs/release-evidence/foundation-runtime-concurrency/75ab69b939d0bf13987ac044ce0dc498f5eab999-frc-b0-20260619/2026-06-19-211-foundation-alpha-poc-75ab69b-foundation-runtime-concurrency.json`.
The full dde1749 #104 B0 POC smoke refresh is retained as superseded reviewed
history and includes reviewed, redacted entries for
runtime POC smoke, Auth/RBAC smoke, Admin Runtime governance smoke,
release-evidence runtime acceptance, and alert/trace export runtime acceptance:
`docs/release-evidence/foundation-alpha-poc/dde1749c256eaa7e0819c98e0debe6084e73cba2/2026-06-19-211-foundation-alpha-poc-dde1749-runtime-poc-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/dde1749c256eaa7e0819c98e0debe6084e73cba2/2026-06-19-211-foundation-alpha-poc-dde1749-auth-rbac-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/dde1749c256eaa7e0819c98e0debe6084e73cba2/2026-06-19-211-foundation-alpha-poc-dde1749-governance-runtime-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/dde1749c256eaa7e0819c98e0debe6084e73cba2/2026-06-19-211-foundation-alpha-poc-dde1749-release-evidence-runtime-acceptance.json`, and
`docs/release-evidence/foundation-alpha-poc/dde1749c256eaa7e0819c98e0debe6084e73cba2/2026-06-19-211-foundation-alpha-poc-dde1749-alert-trace-export-runtime-acceptance.json`.
The dde1749 2026-06-19 Foundation Runtime concurrency rerun is accepted
Foundation Runtime concurrency evidence for this runtime-relevant source:
`docs/release-evidence/foundation-runtime-concurrency/dde1749c256eaa7e0819c98e0debe6084e73cba2-frc-b0-20260619/2026-06-19-211-foundation-alpha-poc-dde1749-foundation-runtime-concurrency.json`.
The full 14808bc #98 B0 POC smoke refresh is retained as superseded reviewed
history and includes reviewed, redacted entries for
runtime POC smoke, Auth/RBAC smoke, Admin Runtime governance smoke,
release-evidence runtime acceptance, and alert/trace export runtime acceptance:
`docs/release-evidence/foundation-alpha-poc/14808bc8335b5f70e770a55db713b8faf4bfa664/2026-06-19-211-foundation-alpha-poc-14808bc-runtime-poc-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/14808bc8335b5f70e770a55db713b8faf4bfa664/2026-06-19-211-foundation-alpha-poc-14808bc-auth-rbac-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/14808bc8335b5f70e770a55db713b8faf4bfa664/2026-06-19-211-foundation-alpha-poc-14808bc-governance-runtime-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/14808bc8335b5f70e770a55db713b8faf4bfa664/2026-06-19-211-foundation-alpha-poc-14808bc-release-evidence-runtime-acceptance.json`, and
`docs/release-evidence/foundation-alpha-poc/14808bc8335b5f70e770a55db713b8faf4bfa664/2026-06-19-211-foundation-alpha-poc-14808bc-alert-trace-export-runtime-acceptance.json`.
The 14808bc 2026-06-19 Foundation Runtime concurrency rerun is accepted
Foundation Runtime concurrency evidence for this runtime-relevant source:
`docs/release-evidence/foundation-runtime-concurrency/14808bc8335b5f70e770a55db713b8faf4bfa664-frc-b0-20260619/2026-06-19-211-foundation-alpha-poc-14808bc-foundation-runtime-concurrency.json`.
The retained 240s blocked diagnostic for the same subject is capacity input for
B3, not B0 closure evidence:
`docs/release-evidence/foundation-runtime-concurrency/14808bc8335b5f70e770a55db713b8faf4bfa664-frc-b0-20260619/2026-06-19-211-foundation-alpha-poc-14808bc-foundation-runtime-concurrency-blocked-240s.json`.
The full 5698873 #72 B0 POC smoke refresh is retained as superseded reviewed
history and includes reviewed, redacted entries for
runtime POC smoke, Auth/RBAC smoke, Admin Runtime governance smoke,
release-evidence runtime acceptance, and alert/trace export runtime acceptance:
`docs/release-evidence/foundation-alpha-poc/569887369e0358f08a408c473395521b22c8e0a7/2026-06-18-211-foundation-alpha-poc-5698873-runtime-poc-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/569887369e0358f08a408c473395521b22c8e0a7/2026-06-18-211-foundation-alpha-poc-5698873-auth-rbac-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/569887369e0358f08a408c473395521b22c8e0a7/2026-06-18-211-foundation-alpha-poc-5698873-governance-runtime-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/569887369e0358f08a408c473395521b22c8e0a7/2026-06-18-211-foundation-alpha-poc-5698873-release-evidence-runtime-acceptance.json`, and
`docs/release-evidence/foundation-alpha-poc/569887369e0358f08a408c473395521b22c8e0a7/2026-06-18-211-foundation-alpha-poc-5698873-alert-trace-export-runtime-acceptance.json`.
The 5698873 2026-06-18 Foundation Runtime concurrency rerun is retained as
superseded accepted Foundation Runtime concurrency evidence for the prior
runtime subject:
`docs/release-evidence/foundation-runtime-concurrency/569887369e0358f08a408c473395521b22c8e0a7-frc-b0-20260618/2026-06-18-211-foundation-alpha-poc-5698873-foundation-runtime-concurrency.json`.
The full de12191 #65 S2-0 POC smoke refresh is retained as superseded reviewed
history and includes reviewed, redacted entries for
runtime POC smoke, Auth/RBAC smoke, Admin Runtime governance smoke,
release-evidence runtime acceptance, and alert/trace export runtime acceptance:
`docs/release-evidence/foundation-alpha-poc/de12191b3b79b7c72e6bc2cd18f7f9ae2726f53b/2026-06-18-211-foundation-alpha-poc-de12191-runtime-poc-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/de12191b3b79b7c72e6bc2cd18f7f9ae2726f53b/2026-06-18-211-foundation-alpha-poc-de12191-auth-rbac-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/de12191b3b79b7c72e6bc2cd18f7f9ae2726f53b/2026-06-18-211-foundation-alpha-poc-de12191-governance-runtime-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/de12191b3b79b7c72e6bc2cd18f7f9ae2726f53b/2026-06-18-211-foundation-alpha-poc-de12191-release-evidence-runtime-acceptance.json`, and
`docs/release-evidence/foundation-alpha-poc/de12191b3b79b7c72e6bc2cd18f7f9ae2726f53b/2026-06-18-211-foundation-alpha-poc-de12191-alert-trace-export-runtime-acceptance.json`.
The de12191 2026-06-18 Foundation Runtime concurrency rerun is retained as
superseded accepted Foundation Runtime concurrency evidence for the prior
runtime subject:
`docs/release-evidence/foundation-runtime-concurrency/de12191b3b79b7c72e6bc2cd18f7f9ae2726f53b-frc-s2-0-20260618/2026-06-18-211-foundation-alpha-poc-de12191-foundation-runtime-concurrency.json`.
The full a15c74f S2-0 POC smoke refresh is retained as superseded reviewed
history and includes reviewed, redacted entries for
runtime POC smoke, Auth/RBAC smoke, Admin Runtime governance smoke,
release-evidence runtime acceptance, and alert/trace export runtime acceptance:
`docs/release-evidence/foundation-alpha-poc/a15c74f0fe98914a893ab7ea784c6be941e0cd71/2026-06-17-211-foundation-alpha-poc-a15c74f-runtime-poc-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/a15c74f0fe98914a893ab7ea784c6be941e0cd71/2026-06-17-211-foundation-alpha-poc-a15c74f-auth-rbac-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/a15c74f0fe98914a893ab7ea784c6be941e0cd71/2026-06-17-211-foundation-alpha-poc-a15c74f-governance-runtime-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/a15c74f0fe98914a893ab7ea784c6be941e0cd71/2026-06-17-211-foundation-alpha-poc-a15c74f-release-evidence-runtime-acceptance.json`, and
`docs/release-evidence/foundation-alpha-poc/a15c74f0fe98914a893ab7ea784c6be941e0cd71/2026-06-17-211-foundation-alpha-poc-a15c74f-alert-trace-export-runtime-acceptance.json`.
The a15c74f 2026-06-17 Foundation Runtime concurrency rerun is accepted
Foundation Runtime concurrency evidence for this runtime subject:
`docs/release-evidence/foundation-runtime-concurrency/a15c74f0fe98914a893ab7ea784c6be941e0cd71-frc-s2-0-20260617/2026-06-17-211-foundation-alpha-poc-a15c74f-foundation-runtime-concurrency.json`.
The prior 8e0389e 2026-06-16 Foundation Runtime concurrency rerun is retained as
failed-closed diagnostics:
`docs/release-evidence/foundation-runtime-concurrency/8e0389ea621a57f3ded2044e410943cc0d298571-frc-s2-0-20260616/2026-06-16-211-foundation-alpha-poc-8e0389e-foundation-runtime-concurrency-readiness-blocked.json`.
The 8e0389e 2026-06-17 rerun is retained as superseded accepted Foundation
Runtime concurrency evidence for the prior runtime subject:
`docs/release-evidence/foundation-runtime-concurrency/8e0389ea621a57f3ded2044e410943cc0d298571-frc-s2-0-20260617/2026-06-17-211-foundation-alpha-poc-8e0389e-foundation-runtime-concurrency.json`.
The local readiness summary can report `runtime_current_for_runtime_relevant_source`
only when the current source has no runtime-affecting delta after the `96f27bb`
source-runtime relation manifest and reviewed evidence. After later
runtime-affecting source changes merge into `main`, readiness must instead
report a runtime rollout requirement such as `source_synced_runtime_pending` /
`runtime_rollout_required` until fresh current-source or runtime-relevant
evidence is recorded. The prior e4c0e9d, e7558cc, 17dc3ae, 0a9e70a, a4bded0,
2bc3a35, d94d274, dab7dbc, 4039e4b, 87528bf, 75ab69b, and #112/#124/#138
evidence are retained as superseded reviewed history and must not be used for
current B0 latest-main claims after newer runtime-affecting source changes.

The full 380de6b POC evidence refresh includes reviewed, redacted entries for
runtime POC smoke, Auth/RBAC smoke, Admin Runtime governance smoke,
release-evidence runtime acceptance, alert/trace export runtime acceptance, and
Foundation Runtime concurrency correctness:
`docs/release-evidence/foundation-alpha-poc/380de6bf9ffed5167f9bb2eaee8e63612a52c124/2026-06-15-211-foundation-alpha-poc-380de6b-runtime-poc-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/380de6bf9ffed5167f9bb2eaee8e63612a52c124/2026-06-15-211-foundation-alpha-poc-380de6b-auth-rbac-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/380de6bf9ffed5167f9bb2eaee8e63612a52c124/2026-06-15-211-foundation-alpha-poc-380de6b-governance-runtime-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/380de6bf9ffed5167f9bb2eaee8e63612a52c124/2026-06-15-211-foundation-alpha-poc-380de6b-release-evidence-runtime-acceptance.json`,
`docs/release-evidence/foundation-alpha-poc/380de6bf9ffed5167f9bb2eaee8e63612a52c124/2026-06-15-211-foundation-alpha-poc-380de6b-alert-trace-export-runtime-acceptance.json`, and
`docs/release-evidence/foundation-runtime-concurrency/380de6bf9ffed5167f9bb2eaee8e63612a52c124-frc-main-20260615/2026-06-15-211-foundation-alpha-poc-380de6b-foundation-runtime-concurrency.json`.
This refresh verifies the controlled POC loop and cross-user/cross-tenant
artifact download and preview denial for the 380de6b runtime subject. The local
readiness summary requires the reviewed/redacted aggregate runtime smoke's
company-login audit before treating the broader auth/session/RBAC/tenant/redaction regression as covered: `company_login_audit_verified=true`,
`ordinary_company_login_audit_count=12`, and
`admin_company_login_audit_count=36`. It keeps
`ordinary_user_multi_agent_allowed=false`, `production_claim_allowed=false`,
`docker_sandbox_hardened_claim_allowed=false`, and
`capacity_default_increase_allowed=false`; this remains S1 controlled-POC
evidence, not production gate closure. The packaged frontend blocker evidence
is retained as S2 delivery follow-up evidence, not as an independent Foundation
Alpha S1 stage blocker.

The prior `dff48fb` broad POC evidence refresh is retained as historical
Foundation Alpha POC evidence after the later broad refreshes superseded it:
`docs/release-evidence/foundation-alpha-poc/dff48fbd454704af64871c039c59d396d8f9aaf7/2026-06-14-211-foundation-alpha-poc-dff48fb-runtime-poc-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/dff48fbd454704af64871c039c59d396d8f9aaf7/2026-06-14-211-foundation-alpha-poc-dff48fb-auth-rbac-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/dff48fbd454704af64871c039c59d396d8f9aaf7/2026-06-14-211-foundation-alpha-poc-dff48fb-governance-runtime-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/dff48fbd454704af64871c039c59d396d8f9aaf7/2026-06-14-211-foundation-alpha-poc-dff48fb-release-evidence-runtime-acceptance.json`, and
`docs/release-evidence/foundation-alpha-poc/dff48fbd454704af64871c039c59d396d8f9aaf7/2026-06-14-211-foundation-alpha-poc-dff48fb-alert-trace-export-runtime-acceptance.json`.

The superseded `ac9a86b` refresh is retained as historical Foundation Alpha POC
evidence:
`docs/release-evidence/foundation-alpha-poc/ac9a86bbea14a28748867cade8d80b2f9ff420ec/2026-06-13-211-foundation-alpha-poc-ac9a86b-runtime-poc-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/ac9a86bbea14a28748867cade8d80b2f9ff420ec/2026-06-13-211-foundation-alpha-poc-ac9a86b-auth-rbac-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/ac9a86bbea14a28748867cade8d80b2f9ff420ec/2026-06-13-211-foundation-alpha-poc-ac9a86b-governance-runtime-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/ac9a86bbea14a28748867cade8d80b2f9ff420ec/2026-06-13-211-foundation-alpha-poc-ac9a86b-release-evidence-runtime-acceptance.json`, and
`docs/release-evidence/foundation-alpha-poc/ac9a86bbea14a28748867cade8d80b2f9ff420ec/2026-06-13-211-foundation-alpha-poc-ac9a86b-alert-trace-export-runtime-acceptance.json`.
The immediately superseded `cbbfaff` refresh is retained as historical
Foundation Alpha POC evidence and still carries the packaged frontend blocker:
`docs/release-evidence/foundation-alpha-poc/cbbfaff9de9f7d18c7524bf6335d35dbf09fbd55/2026-06-13-211-foundation-alpha-poc-cbbfaff-runtime-poc-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/cbbfaff9de9f7d18c7524bf6335d35dbf09fbd55/2026-06-13-211-foundation-alpha-poc-cbbfaff-auth-rbac-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/cbbfaff9de9f7d18c7524bf6335d35dbf09fbd55/2026-06-13-211-foundation-alpha-poc-cbbfaff-governance-runtime-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/cbbfaff9de9f7d18c7524bf6335d35dbf09fbd55/2026-06-13-211-foundation-alpha-poc-cbbfaff-release-evidence-runtime-acceptance.json`,
`docs/release-evidence/foundation-alpha-poc/cbbfaff9de9f7d18c7524bf6335d35dbf09fbd55/2026-06-13-211-foundation-alpha-poc-cbbfaff-alert-trace-export-runtime-acceptance.json`, and
`docs/release-evidence/foundation-alpha-poc/cbbfaff9de9f7d18c7524bf6335d35dbf09fbd55/2026-06-13-211-foundation-alpha-poc-cbbfaff-frontend-packaged-runtime-smoke-blocked.json`.
Foundation Runtime 10+ concurrent correctness is now tracked as a separate
fail-closed evidence gate named `foundation_runtime_concurrency_evidence` with
schema `ai-platform.foundation-runtime-concurrency.v1`. It must cover at least
2 tenants, multiple users/sessions, and 10+ concurrent run creation, execution,
cancel, and retry cases. The evidence must prove queue/admission correctness,
sandbox workspace/lease separation, artifact download and preview cross-user
and cross-tenant denial, exact tool-permission decision binding, pinned
`run_skill_snapshots`, replay safety, and memory/context isolation. The
memory/context check must include public context snapshot projections for each
run, safe `context_pack_version` samples, and scope probes, not only a raw
`context_snapshot_count`. Queue/admission claims require real probe sample
counts and provenance fields; sandbox claims require runtime run-detail lease
provenance rather than post-run lease probes; tool-permission claims require
negative decision-reuse probes for same-request, wrong-run, same-tenant
other-user, and cross-tenant reuse attempts; skill governance claims require
pinned snapshot binding samples. The 2026-06-14 `dff48fb` 211 rerun is retained
as reviewed historical evidence, but current validation blocks it because it
lacks measured concurrency overlap, `queue_probe_sample_count`,
`runtime_run_detail` sandbox lease provenance, and negative tool-permission
reuse probes. The 2026-06-14 `5d3d7e2` PR #40 rerun and the 2026-06-14
`79495bf` PR #40 rerun are retained as superseded reviewed evidence after the
merged-main refresh. The 2026-06-15 `380de6b` rerun is the historical accepted
Foundation Runtime concurrency baseline evidence: readiness status is
`verified_foundation_runtime_concurrency`, it records 12 concurrent cases,
2 tenants, 4 users, 12 run/session samples, measured client timestamp overlap,
queue probe sample count 12, runtime-run-detail sandbox lease provenance,
12 public context projection samples, 12 pinned snapshot binding samples, and
48 denied negative tool-permission reuse probes. This closes only the
Foundation Runtime concurrency evidence gap for the historical `380de6b` runtime-relevant
source. It does not raise production concurrency defaults, does not open
ordinary-user platform-level multi-run orchestration exposure, does not claim Docker sandbox hardening, does not
permit department rollout, and does not replace the broader Foundation Alpha
POC smoke/auth/governance evidence set.

The superseded runtime subject commit
`cbbfaff9de9f7d18c7524bf6335d35dbf09fbd55` remains historical reviewed evidence
for the pre-S1 merged runtime refresh and packaged frontend blocker evidence.
Its reviewed, redacted smoke evidence entries are under
`docs/release-evidence/foundation-alpha-poc/cbbfaff9de9f7d18c7524bf6335d35dbf09fbd55/`.

The earlier superseded runtime subject commit
`18454a9ccd890dd6b9636a04604b6a100cba31e7` remains historical reviewed evidence
for the cross-tenant artifact isolation slice and historical evidence only. Its
reviewed, redacted smoke evidence entries are under
`docs/release-evidence/foundation-alpha-poc/18454a9ccd890dd6b9636a04604b6a100cba31e7/`.

The earlier superseded runtime subject commit
`00e4e6b950709439850749fe26af9c0943f6a07c` remains historical reviewed evidence for the skill-release
pending-evidence hardening slice. Its reviewed, redacted smoke evidence entries
are under
`docs/release-evidence/foundation-alpha-poc/00e4e6b950709439850749fe26af9c0943f6a07c/`.

The earlier superseded runtime subject commit
`6088d5d179c422a6d753e1b77079410503e58925` was synced to the 211 source
target and the 211 API and worker ran
`ai-platform:6088d5d-alert-trace-acceptance` with image ID
`sha256:c8585918ccaeb4f9128c2c9301c8f8ac0d0c40002dc5b4febcafa2813b28bedf`.
The `ai-platform.source-revision`, `ai-platform.runtime-subject`, and
`org.opencontainers.image.revision` labels all pointed to
`6088d5d179c422a6d753e1b77079410503e58925`. API health returned `ok`; the
compose config used the repo-local deploy composition while container labels
still recorded the old external env-file layout; and the
aggregate verifier `tools/verify_poc_gate.py` returned `ok: true` on 211 for
the controlled context public-projection and alert-trace-runtime-acceptance
rollout slice. That smoke verified `summary_source=chat_stream`, safe
`input_keys=["attachments","message"]` for a file-backed run, memory policy
source, execution tier, generated-at presence, and no raw material IDs or
forbidden projection leaks. The current verifier now also requires a safe
`context_pack_version`, so this historical smoke must be refreshed before it
can satisfy the current context public-summary contract. The reviewed, redacted
release-evidence entry is
`docs/release-evidence/foundation-alpha-poc/6088d5d179c422a6d753e1b77079410503e58925/2026-06-12-211-foundation-alpha-poc-6088d5d-runtime-poc-smoke.json`.

The focused Auth/RBAC verifier `tools/verify_auth_rbac_smoke.py` also returned
`ok: true` on 211 against `6088d5d`. It verified unauthenticated auth
rejection, trusted platform principal projection, invalid gateway secret
rejection, ordinary-user Admin Runtime denial, and same-tenant Admin Runtime
access for an admin smoke principal. The reviewed, redacted Auth/RBAC evidence
entry is
`docs/release-evidence/foundation-alpha-poc/6088d5d179c422a6d753e1b77079410503e58925/2026-06-12-211-foundation-alpha-poc-6088d5d-auth-rbac-smoke.json`.

The focused governance verifier
`tools/verify_governance_runtime_smoke.py` returned `ok: true` on 211 against
the same `6088d5d` runtime subject. It verified ordinary-user Admin Runtime
denial, same-tenant admin access, G6 governance schema
`ai-platform.governance-readiness.v1`, required tool/skill/memory governance
domains, tool policy taxonomy and bulk-review signals, skill release/dashboard
signals with `dashboard_contract` trimmed from the overview projection, memory
fail-closed/context-provenance/office-context signals, and no forbidden
projection terms in the reviewed summary. The reviewed, redacted governance
smoke evidence entry is
`docs/release-evidence/foundation-alpha-poc/6088d5d179c422a6d753e1b77079410503e58925/2026-06-12-211-foundation-alpha-poc-6088d5d-governance-runtime-smoke.json`.

The runtime-packaged release evidence verifier
`tools/verify_release_evidence_runtime_acceptance.py` returned `ok: true`
inside the `6088d5d` API container. It verified the safe reviewed index with
`safe_entry_count=30`, `blocked_entry_count=0`, `excluded_entry_count=18`, and
the review-first retention policy. The reviewed, redacted runtime acceptance
evidence entry is
`docs/release-evidence/foundation-alpha-poc/6088d5d179c422a6d753e1b77079410503e58925/2026-06-12-211-foundation-alpha-poc-6088d5d-release-evidence-runtime-acceptance.json`.

The alert/trace export runtime acceptance verifier
`tools/verify_alert_trace_export_runtime_acceptance.py` returned `ok: true`
inside the same `6088d5d` API container. It verified ordinary-user Admin
Runtime denial, same-tenant admin observability access, alert rule/template
exposure, alert delivery policy intentionally not enabled, and trace export
contract sources limited to reviewed public/admin summaries. The reviewed,
redacted runtime acceptance evidence entry is
`docs/release-evidence/foundation-alpha-poc/6088d5d179c422a6d753e1b77079410503e58925/2026-06-12-211-foundation-alpha-poc-6088d5d-alert-trace-export-runtime-acceptance.json`.

This rollout used the repo-local 211 deploy composition while reusing the
existing external runtime env file without printing or copying secret values.
It used a runtime-only image rebased from the previous healthy image because
dependencies did not change. Treat that as a deployment workaround, not the
preferred release path.

The earlier superseded runtime subject commit
`948179c73734aa61ed764fb3485f5415fca8f193` remains historical reviewed
evidence for the skill-release-scaffold slice. Its reviewed, redacted smoke
evidence entries are
`docs/release-evidence/foundation-alpha-poc/948179c73734aa61ed764fb3485f5415fca8f193/2026-06-12-211-foundation-alpha-poc-948179c-skill-release-scaffold-smoke.json`,
`docs/release-evidence/foundation-alpha-poc/948179c73734aa61ed764fb3485f5415fca8f193/2026-06-12-211-foundation-alpha-poc-948179c-auth-rbac-smoke.json`, and
`docs/release-evidence/foundation-alpha-poc/948179c73734aa61ed764fb3485f5415fca8f193/2026-06-12-211-foundation-alpha-poc-948179c-governance-runtime-smoke.json`.

The earlier superseded runtime subject commit
`b7689d0cbc6fa3913de47aea3aded1036f0ea0ae` remains historical reviewed
evidence for the context public-projection slice. Its reviewed, redacted smoke
evidence entries are
`docs/release-evidence/foundation-alpha-poc/b7689d0cbc6fa3913de47aea3aded1036f0ea0ae/2026-06-12-211-foundation-alpha-poc-b7689d0-context-public-projection-smoke.json`
and
`docs/release-evidence/foundation-alpha-poc/b7689d0cbc6fa3913de47aea3aded1036f0ea0ae/2026-06-12-211-foundation-alpha-poc-b7689d0-auth-rbac-smoke.json`.

The earlier superseded runtime subject commit
`2384e19dcac2e39fbcf9c27dc990f5774d391422` remains historical reviewed
evidence for the context source-provenance and governance slices. Its reviewed,
redacted smoke evidence entries are
`docs/release-evidence/foundation-alpha-poc/2384e19dcac2e39fbcf9c27dc990f5774d391422/2026-06-12-211-foundation-alpha-poc-2384e19-context-source-provenance-smoke.json`
and
`docs/release-evidence/foundation-alpha-poc/2384e19dcac2e39fbcf9c27dc990f5774d391422/2026-06-12-211-foundation-alpha-poc-2384e19-context-source-provenance-auth-rbac-smoke.json`.

The focused governance verifier
`tools/verify_governance_runtime_smoke.py` returned `ok: true` on 211 against
the same running runtime subject. It verified ordinary-user Admin Runtime denial,
same-tenant admin access, G6 governance schema
`ai-platform.governance-readiness.v1`, required tool/skill/memory governance
domains, tool policy taxonomy and bulk-review signals, skill release/dashboard
signals with `dashboard_contract` trimmed from the overview projection, memory
fail-closed/context-provenance/office-context signals, and no forbidden
projection terms in the reviewed summary. The verifier source was synced at
`820669037978237182ecd2fd27c2ffa10a953c0b`; the API/worker runtime image
remained `ai-platform:2384e19-context-source-provenance`, and the synced source
snapshot declared no runtime-affecting delta from `2384e19`. The reviewed,
redacted governance smoke evidence entry is
`docs/release-evidence/foundation-alpha-poc/2384e19dcac2e39fbcf9c27dc990f5774d391422/2026-06-12-211-foundation-alpha-poc-2384e19-governance-runtime-smoke.json`.

This 2384e19 rollout used a runtime-only image rebased from the previous
healthy image because a full Docker build stalled on dependency installation.
Treat that as a deployment workaround, not the preferred release path. The
repo-local 211 deploy directory still lacks a committed or copied real `.env`;
compose used the existing external runtime env path without printing or copying
secret values.

Immediately before that slice, runtime subject commit
`e274d78b21c22fdf4f56a8cf8b31a0480d42c22f` was synced to the 211 runtime
subject and the 211 API and worker ran
`ai-platform:e274d78-g9-runtime-readiness-tools` with image ID
`sha256:a8873641808cbf15f919a12a2d4a540a2cbf309557a15f8f832e0dbb0801f4ab`.
Both runtime source labels pointed to
`e274d78b21c22fdf4f56a8cf8b31a0480d42c22f`. Runtime labels pointed to the
repo-local 211 deploy composition, API health returned `ok`, container-side
`python -m compileall -q app tools scripts` passed for API and worker, and the container-side
`tools/release_evidence_export_acceptance.py` preflight returned
`ready_for_operator_review` with `safe_entry_count=16`, `blocked_entry_count=0`,
and `excluded_entry_count=3`.

The aggregate verifier `tools/verify_poc_gate.py` returned `ok: true` on 211
for the controlled POC loop: LambChat thin-shell frontend, same-origin API
health, public/admin projection boundary, company auth bridge, general chat run,
document review attachment run, artifact download isolation, artifact preview
isolation, playback with preview URL and no private payload leakage, company
login audit, Admin capacity/backpressure fields, and context snapshot public
projection with `summary_source=chat_stream`, safe `input_keys=["message"]`,
memory policy source, execution tier, generated-at presence, and no raw
material IDs. The current verifier now also requires a safe
`context_pack_version`, and requires `attachments` in `input_keys` whenever
`file_count > 0`, so this historical smoke must be refreshed before it can
satisfy the current context public-summary contract.
`tools/foundation_alpha_readiness.py` promotes that context projection into the
G6 evidence summary and fails closed as
`missing_context_snapshot_public_projection` when an older smoke record lacks
it, or as `attachments_input_key` when file-context provenance lacks the
attachment signal, or as `context_pack_version` when the projection lacks the
public context-pack version. The reviewed, redacted release-evidence entry is
`docs/release-evidence/foundation-alpha-poc/e274d78b21c22fdf4f56a8cf8b31a0480d42c22f/2026-06-12-211-foundation-alpha-poc-e274d78-runtime-readiness-tools-smoke.json`.

The focused Auth/RBAC verifier `tools/verify_auth_rbac_smoke.py` also returned
`ok: true` on 211 against the same runtime. The refreshed 2026-06-12 smoke used
runtime subject `e274d78`. It verified unauthenticated `/api/auth/me`
returns 401, platform `/api/ai/auth/me` returns the trusted principal with
tenant match, invalid gateway secret access to `/api/ai/auth/me` fails with
403, ordinary trusted principals are denied from Admin Runtime with 403, admin
trusted principals can read the required same-tenant Admin Runtime sections with
200, and the projection scan did not find private or secret-like values. The
PR #26 verifier fix allows legitimate Admin Runtime observability/readiness
metric text such as token/cost/error summaries while continuing to fail closed
on secret-like keys and credential-shaped values. The
reviewed, redacted Auth/RBAC evidence entry is
`docs/release-evidence/foundation-alpha-poc/e274d78b21c22fdf4f56a8cf8b31a0480d42c22f/2026-06-12-211-foundation-alpha-poc-e274d78-auth-rbac-smoke.json`.

Earlier smoke evidence for
`e274d78b21c22fdf4f56a8cf8b31a0480d42c22f`,
`a63dbbd0b474cce3702b3485e6589f86155cf5aa`,
`d95107da2b5691781518bdbb8c4e5e76409869f3`,
`458f6056dd0fa533162e780a303d79ce1b3d0eec`,
`9b02836262fb0f238a7f90b9705bf39a8b298158`,
`cdc09ba8867d91e8db76570fbf158e6d082da7cf`,
`8f454696be0e9c532fa86bc61ef353e4d3dec4f8`,
`faa7ad6aa61637cbcdf3a22ce81de119762e96bf`,
`a3f1d739e12686cba2e0b309de26a4e1127bd3a5`,
`8c0cffca63bc747fad0a5771f209acc8a608ab9e`,
`bf20432f9889efa8b367afdf512c641068ba30bc`, and
`3874281276c84a418bd08bda56d7ea55b52970b7` remains retained as historical
evidence only; the `380de6b` evidence above is the historical Foundation Alpha
baseline, the `96f27bb` / B0 evidence is the latest reviewed runtime-subject
reference, the `845faf7` Foundation Runtime concurrency evidence is retained as
superseded non-current-subject B0 history after the later current-subject
FRC refresh, and the
`c3d6525`, `442aa39`, `f67986a`, `e4c0e9d`, `e7558cc`, `17dc3ae`, `0a9e70a`, `df85a9f`, `a4bded0`,
`2bc3a35`, `d94d274`, `dab7dbc`, `4039e4b`, `87528bf`, `75ab69b`, `79495bf`,
`dff48fb`, and `ac9a86b` evidence is now retained as superseded reviewed
history. The #164 runtime-subject evidence scope now sits behind the newer
`96f27bb` B0 runtime-subject refresh but still carries G0
source-authority caveats because the compose env-file label still points to the
external env-file path.
The immediately superseded runtime image was `ai-platform:948179c-skill-release-scaffold`.

This smoke does not close the recorded capacity-evidence gate, G7 Docker sandbox hardening, ordinary-user
platform-level multi-run orchestration exposure, department rollout, alert delivery enablement,
signed Skill package or SBOM review evidence, G9 Admin Runtime observability
partial follow-ups, or packaged frontend image release acceptance. Signed Skill
package or SBOM review evidence remains a G6/S2 production Skill release
follow-up; Foundation Alpha S1 acceptance requires the current fail-closed
production release posture, governed pinned snapshots, and reviewed POC
governance evidence rather than treating that production release evidence as an
independent S1 stage blocker. Packaged frontend image release acceptance remains
an S2 delivery follow-up; S1 frontend acceptance is limited to active browser
public/admin projection safety and reproducible source checks or exact blockers.

On 2026-06-12, commit `83a500ef082a47db0a01b4fb9679e67bf2b24fc4` was synced to
the 211 source target for the packaged frontend image slice. The source archive
included the frontend image `ai-platform.source-revision` label contract and
passed `python3 -m compileall -q app tools scripts` on 211. The packaged
frontend runtime smoke did not reach image runtime: the Docker daemon still had
a stale registry proxy, BuildKit could not resolve the Dockerfile frontend, and
a no-syntax probe could not pull `node:22-alpine`; `nginx:1.27-alpine` also
remains required for the final image. The verifier classified the redacted
attempt as `blocked_environment` with `docker_registry_proxy_unreachable` and
`base_image_pull_failed`, with no closed evidence items. This is 211-verified
blocker evidence only; it is not packaged frontend image release acceptance.

During the superseded `cbbfaff9de9f7d18c7524bf6335d35dbf09fbd55` runtime
refresh, the packaged frontend blocker was rechecked on 211. The source marker
pointed to `cbbfaff`, the frontend Dockerfile and repo-local frontend compose
overlay were present, and the Docker daemon still could not resolve required
base-image metadata through the registry proxy. No target
`ai-platform-frontend:*` image was cached. The reviewed, redacted blocker
evidence entry is
`docs/release-evidence/foundation-alpha-poc/cbbfaff9de9f7d18c7524bf6335d35dbf09fbd55/2026-06-13-211-foundation-alpha-poc-cbbfaff-frontend-packaged-runtime-smoke-blocked.json`.
It records `blocked_environment` with registry-proxy/base-image resolution
failure, has no closed evidence items, and still does not close
`packaged_frontend_image_release_acceptance`. That remains a frontend delivery
follow-up for S2 instead of a standalone S1 stage blocker.

## Current Gate Status

The table below is a gate/evidence matrix, not a replacement for the Current
Reading Guide above. Rows that mention `96f27bb` describe reviewed 2026-06-30
runtime-subject evidence retained in the repository. For this 2026-07-03
snapshot, GitHub `main` had advanced to the PR #312 status-sync baseline
`881493d042a522b343c9df2044bd3830fd02e62f`, while the 211 repo-local source
marker and 211 API/worker canonical runtime image labels remained at
`15903fdfe96ffcfba9daa1252741111017dcf832`. Direct API health returns
`{"status":"ok"}` on `http://127.0.0.1:8020/api/ai/health`, frontend proxy
health returns `{"status":"ok"}` on `http://127.0.0.1:18001/api/ai/health`, and
the frontend root returns HTTP `200` on `http://127.0.0.1:18001/`. The named
`g7-runtime-probe-20260701203418` formal verifier evidence remains historical
`d318f9f`, the `ae6b7e5` one-shot G7
verifier artifacts for
`g7-current-main-ae6b7e5-20260701172910` are wrapped in reviewed repo-local G7
sandbox release evidence, the later `ae6b7e5` formal hardening evidence is
wrapped in reviewed repo-local G7 evidence, the newer `ae6b7e5` live-env
hardening evidence records Docker provider, `ae6b7e5` executor image, and
egress policy enabled for API/worker defaults, the later `ae6b7e5`
label-repair evidence clears the stale runtime alias mismatch, `ae6b7e5` FRC
evidence is recorded as Foundation Runtime POC correctness evidence, and B3
follow-ups remain open. A reviewed `4805031` G7 live-env hardening entry now
wraps `g7-live-env-hardening-4805031-20260702023507` for the running
API/worker posture, and same-subject `4805031` Foundation Runtime concurrency
evidence is now recorded. Together they leave G7 at
`candidate_evidence_requires_review`, pending operator status-upgrade review.
PR #304 runtime subject `decf33a` also has same-subject reviewed G7/FRC
evidence. The newer reviewed B3 capacity visibility entry for the current
`755e50e` dirty-runtime subject supersedes the historical `28676df` capacity
visibility record for latest-status reading; both remain fail-closed visibility
only, with `755e50e` still at `blocked_missing_admin_runtime_sections`. The
`28676df` G7 verifier path first failed because host Python lacked `pydantic`
(`No module named 'pydantic'`),
then exposed the executor workspace ownership bug under `cap_drop=["ALL"]`;
the patched-source diagnostic run passed all eight checks
(`g7-current-main-28676df-workspace-user-fix-20260702135351`) and led to
PR #306. After PR #306 deployed `ai-platform:9c66976-g7-b3-workspace-owner-v1`,
the earlier deployed-runtime G7 verifier run
`g7-current-main-9c66976-20260702145801` still did not produce passing
evidence: it recorded `executed_task=false`, `sandbox_provider=unknown`, and
`[Errno 13] Permission denied: '[redacted-path]'`. That artifact is
diagnostic-only. A later sudo-context run,
`g7-current-main-9c66976-sudo-20260702155816`, passed all eight checks and is
recorded as reviewed `9c669761` explicit verifier-path G7 hardening evidence.
The later `9c669761` live-default G7 and same-subject FRC entries reached the
historical candidate-evidence boundary, but the operator status-review decision
was `not_approved_for_closure`. PR #308 then deployed `ai-platform:15903fd-g7-b3-main-runtime-only-v1`, and the
later label-clean rollout moved API/worker to
`ai-platform:15903fd-g7-b3-label-clean-v2`; reviewed label-clean `15903fd`
live-default G7 and same-subject FRC evidence now exist. The later `755e50e`
dirty-runtime v2 read-only capacity entry is the current reviewed B3 visibility
record and remains fail-closed because the `sandbox` Admin Runtime section and
all recorded load-test gates are missing. The current status remains `local
partial` because G7 status-upgrade approval and B3 recorded load evidence are
still missing.
A reviewed `ae6b7e5` Foundation Alpha POC evidence
set for `ae6b7e5` is also recorded and selected by readiness, including
runtime POC smoke, Auth/RBAC smoke, governance runtime smoke, release-evidence
runtime acceptance, alert/trace runtime acceptance, and FRC evidence. Readiness
still stays at `runtime_rollout_required` because current source and runtime
observations are newer than the reviewed `ae6b7e5` evidence subject.
The `96f27bb` source-runtime relation is historical evidence only for that
runtime subject.
For PR #304 runtime subject `decf33a`, the evidence-only audit for `decf33a`
can reach `candidate_evidence_requires_review` with `blocking_reasons=[]`; this
is retained as same-subject historical evidence and does not close the current
`15903fd` source/runtime subject.

| Gate | Current status | Evidence now in repository | Remaining blocker before closure |
| --- | --- | --- | --- |
| G0-G1 Source Authority / Security Baseline | Reviewed B0 runtime-relevant smoke and Foundation Runtime concurrency evidence are retained for `96f27bb`. The current 211 repo-local source marker and API/worker canonical runtime labels bind to runtime subject `755e50ea2ad08c2d4218ae5d8cc612970b19e2a4`; direct API health on `8020`, frontend proxy health, and frontend root were healthy. This is still not G0 closure: `755e50e` has reviewed dirty-runtime v2 G7 evidence and same-subject verified FRC evidence, but B3 recorded load evidence remains missing, G7 status-upgrade approval is absent, the API/worker image is `build-dirty=true`, legacy in-container marker files still read `9c669761` / `28676df`, and the external env-file label caveat / external env-file source-authority review plus production auth rollout evidence remain separate gates. Historical same-subject evidence for `15903fd`, `9c669761`, `decf33a`, `4805031`, `ae6b7e5`, `28676df`, and older subjects remains useful but does not make clean current `main` `211 verified`. | PRD v2, backend phased PRD, technical acceptance matrix, roadmap, guardrails, source-authority tests, repo-local compose context, frontend source migration, redacted deploy templates, PR #312 merge commit `881493d042a522b343c9df2044bd3830fd02e62f`, PR #311 merge commit `40691c01d64d6cd604dd94e6fc24ee6babdf0cad`, reviewed dirty-runtime v2 `755e50e` G7 live-env hardening entry `2026-07-03-211-g7-sandbox-live-env-hardening-755e50e.json`, verified `755e50e` FRC entry `2026-07-03-211-foundation-alpha-poc-755e50e-foundation-runtime-concurrency.json`, reviewed `15903fd` G7/FRC/status-review entries, reviewed `9c669761` G7/FRC/status-review entries, reviewed `decf33a`, `4805031`, and `ae6b7e5` same-subject evidence, reviewed `28676df` capacity visibility plus G7 diagnostic history, older retained evidence, and issue #164 evidence. | Keep current status at `local partial`; record B3 load evidence, obtain a future approved G7 status-upgrade decision, promote/rebuild the principal user-id fix into clean current-main runtime evidence before any clean `211 verified` claim, complete external env-file/source-authority review, reconcile legacy in-container source marker files, and complete production auth rollout evidence before any G0 closure claim. |
| G2-G4 Control Plane MVP | Substantial coverage; keep under regression. | Session/run/file/artifact/skill/tool/memory/event/audit contracts, repositories, routes, schema indexes, and focused tests. | Full regression before PR/deploy, plus no executor-owned platform schema drift. |
| G5 Run Lifecycle / Worker Runtime V1 | Foundation Alpha POC queue/run/worker execution, Admin capacity/backpressure projection, and Foundation Runtime concurrency evidence are retained for `96f27bb`; runtime POC smoke, Auth/RBAC, governance, release/alert acceptance, and Foundation Runtime concurrency evidence are also recorded for reviewed `ae6b7e5`. The 2026-07-02 `ae6b7e5` verified Foundation Runtime concurrency evidence proves Foundation Runtime POC correctness for 12 concurrent requests/sessions/runs but does not close B3 capacity, does not raise defaults, and does not remove the separate current-source rollout requirement for newer runtime-affecting local changes. | Tenant-aware queue lease, worker maintenance, active-run admission, bounded metadata, Admin Runtime capacity/backpressure projection, #20 roadmap closure notes, reviewed `ae6b7e5` runtime POC smoke entry `2026-07-02-211-foundation-alpha-poc-ae6b7e5-runtime-poc-smoke.json`, `ae6b7e5` FRC evidence entry `2026-07-02-211-foundation-alpha-poc-ae6b7e5-foundation-runtime-concurrency.json`, 2026-07-02 `ae6b7e5` Auth/RBAC/governance/release/alert evidence, 2026-06-30 `96f27bb` POC verifier evidence, 2026-06-30 `96f27bb` verified Foundation Runtime concurrency evidence, 2026-06-30 `c3d6525` POC verifier evidence retained as superseded B0 history, 2026-06-30 `c3d6525` verified Foundation Runtime concurrency evidence retained as superseded B0 history, 2026-06-30 `442aa39` POC verifier evidence retained as superseded B0 history, 2026-06-30 `845faf7` verified Foundation Runtime concurrency evidence retained as superseded non-current-subject FRC, 2026-06-29 `f67986a` POC verifier evidence, 2026-06-29 `f67986a` verified Foundation Runtime concurrency evidence, superseded 2026-06-24 `e4c0e9d` POC verifier evidence, superseded 2026-06-24 `e4c0e9d` verified Foundation Runtime concurrency evidence, superseded 2026-06-24 `e7558cc` POC verifier evidence, superseded 2026-06-24 `17dc3ae` POC verifier evidence, superseded 2026-06-23 `0a9e70a` POC verifier evidence, superseded 2026-06-23 `df85a9f` POC verifier evidence, superseded 2026-06-23 `a4bded0` POC verifier evidence, superseded 2026-06-22 `2bc3a35` POC verifier evidence, superseded 2026-06-22 `d94d274` POC verifier evidence, superseded 2026-06-22 `dab7dbc` POC verifier evidence, superseded 2026-06-20 `4039e4b` POC verifier evidence, superseded 2026-06-19 `87528bf` POC verifier evidence, superseded 2026-06-19 `75ab69b` POC verifier evidence, superseded 2026-06-19 `dde1749` POC verifier evidence, superseded 2026-06-19 `14808bc` POC verifier evidence, superseded 2026-06-19 `14808bc` 240s blocked FRC diagnostic, superseded 2026-06-18 `5698873` evidence, superseded 2026-06-18 `de12191` evidence, superseded 2026-06-17 `a15c74f` evidence, superseded 2026-06-16 `8e0389e` failed-closed FRC diagnostics, superseded 2026-06-17 `8e0389e` verified Foundation Runtime concurrency evidence, and 2026-06-15 `380de6b` historical accepted concurrency evidence. | Keep #21/B3 capacity evidence, large queue bounded lookup pressure, worker parallelism/capacity profiling, and multi-tenant load evidence separate from #164. #21 is currently closed in GitHub but the B3 capacity evidence remains incomplete. Production defaults stay unchanged. |
| G6 Tool / Skill / Memory Governance | Admin Runtime governance projection now has focused 211 smoke evidence for the POC runtime, and Foundation readiness records `memory_context_controls` with `session_scoped_memory=true`, `ordinary_user_opt_out=true`, `retention_cleanup=true`, `delete_redaction=true`, `public_admin_projection_safe=true`, `long_term_cross_session_memory_fail_closed=true`, exact tool-permission decision lookup source tests, admin bulk-review source-route runtime-control tests, Admin Skill release dashboard source-route runtime-control tests, source-level context-pack persistence/versioning, user-visible context provenance API projection source tests, frontend run-playback context provenance projection source tests, document-centric follow-up state source tests, reviewed `8e0389e` 211 executor context-pack evidence, and reviewed PR #44 211 sandbox latency split evidence. G6 remains partial and ordinary-user governance/frontend rollout remains blocked. | Tool policy taxonomy/history, exact tool-permission decision lookup source tests, admin bulk-review source-route runtime-control tests, Admin Skill release dashboard source-route runtime-control tests, public permission-card projection, skill release/dependency policy contracts, memory delete/retention/redaction/export readiness, office context-pack architecture readiness, `source_level_context_pack_persistence_and_versioning`, `context_pack_version`, `context_pack_generated_at`, context snapshot public provenance projection contract, user-visible context provenance API projection source tests, frontend run-playback context provenance projection source tests, document-centric follow-up state source tests, `8e0389e` executor context-pack runtime evidence, PR #44 `office-context-runtime` sandbox latency evidence, governance readiness CLI, POC runs using governed skills, and 2026-06-15 380de6b governance runtime smoke evidence. | Legacy frontend route remap/policy enforcement, signed package or SBOM review evidence, dependency vulnerability/license evidence, admin bulk-review visual acceptance, admin bulk-review 211 acceptance, Admin Skill release visual acceptance, Admin Skill release 211 acceptance, long-term cross-session memory policy closure, ordinary-user platform-level multi-run orchestration exposure controls, production Docker sandbox hardening, packaged frontend acceptance, and broader 211 acceptance. |
| G7 Sandbox / Resource Hardening | local partial. Current 211 API/worker images run `ai-platform:755e50e-g7-b3-principal-userid-fix-v2` with image ID `sha256:f1b50184d0093c1622a035c2a49d6bcc93d4335e2565474ee40d695efc4e3d6f`, `build-dirty=true`, rollout `g7-b3-755e50e-principal-userid-fix-v2`, and `SANDBOX_EXECUTOR_IMAGE=ai-platform:755e50e-g7-b3-principal-userid-fix-v1`; the repo-local source marker, canonical source/runtime/OCI labels, and legacy source alias labels bind to `755e50e`, while legacy in-container source marker files still read `9c669761` / `28676df`. Reviewed dirty-runtime v2 `755e50e` live-env G7 hardening evidence exists and the verifier passed all eight checks, including platform Docker execution, callback stream, cancel cleanup, resource limits, egress policy, security options, and no-secret leakage. Same-subject FRC evidence is now verified for 12 concurrent requests/runs/sessions across 2 tenants and 4 users. It is not G7 closure because B3 load evidence is missing, operator status-upgrade approval is absent, and the current runtime is dirty patch evidence rather than clean current-main `211 verified` evidence. | Historical B2 sandbox release-evidence; named `d318f9f` formal verifier observation; reviewed G7 entries for `ae6b7e5`, `4805031`, `decf33a`, `9c669761`, `15903fd`, and dirty-runtime v2 `755e50e`; reviewed FRC entries for same-subject `ae6b7e5`, `4805031`, `decf33a`, `9c669761`, `15903fd`, and `755e50e`; the superseded blocked 755e50e FRC attempt `/tmp/frc-755e50e-20260703T090109Z`; reviewed status-review entries for `9c669761` and `15903fd`; `28676df` capacity visibility plus workspace-owner diagnostic history; merged verifier-helper callback default tests; and capacity docs exposing sandbox limits and missing hardening warnings. | Obtain a future approved G7 status-upgrade decision, promote/rebuild the principal user-id fix into clean current-main runtime evidence before any clean `211 verified` claim, keep G0/source-authority and B3 evidence gates separate, reconcile legacy in-container source marker files, and keep B3 blocked until recorded load evidence exists. |
| G8 Deferred Platform Multi-Run Gate | Deferred parking-lot for platform-owned parent/child multi-run orchestration. Historical evidence may mention the old title "G8 Multi-Agent Controlled Beta"; current status must not use that beta title and must not be read as ordinary-user platform-level multi-run product exposure. SDK-internal Agent/subagent fanout stays inside one governed platform run, and the current question is B3 deployment capacity for SDK subagent fanout rather than a G8 product route. This is a blocked expansion, not a current Foundation Alpha `open_followups` item. | Historical dispatcher and child-run admission work exists behind controls but is not the current product route; Claude Agent SDK remains the execution-layer route for SDK Agent/subagent fanout patterns. | Do not build a separate agent harness as the next step. Reopen only after prior gates have recorded tenant quota/backpressure, model-gateway pressure, sandbox pressure, artifact/event volume, token/cost accounting, rollback, and no ordinary-user platform-level multi-run orchestration exposure evidence. |
| G9 Observability / Quality / Ops | Reviewed release-evidence runtime acceptance and alert/trace runtime acceptance are retained for `96f27bb`; G9 remains partial for Operations Beta and current-main evidence still needs refresh where gate-specific reviewed evidence matters. | Admin Runtime overview, capacity/governance/observability readiness docs and tools, error taxonomy/dashboard contracts, release-evidence contracts, reviewed 211 release-evidence runtime export/retention acceptance for `96f27bb`, reviewed 211 alert/trace export runtime acceptance for `96f27bb`, superseded `c3d6525`, `442aa39`, `f67986a`, `e4c0e9d`, `e7558cc`, `17dc3ae`, `0a9e70a`, `df85a9f`, `a4bded0`, `2bc3a35`, `d94d274`, `dab7dbc`, `4039e4b`, and `87528bf` G9 runtime evidence, trace/audit export contracts, frontend projection audit, and reviewed 211 POC smoke entry. | S2/G9 closure still requires runtime dashboard acceptance, recorded capacity evidence, model-gateway backpressure evidence, golden-set eval runtime, alert delivery enablement/runtime calibration, and remaining Admin Runtime observability follow-ups. |
| G10 Internal Beta / Department Rollout | Blocked. | Candidate internal workflows are named only as examples in roadmap. | Select 1-2 real internal workflow owners, complete prior gates, record cost/quality/audit/rollback evidence, and pass 211 acceptance. |

## Issue-Driven Thin Spots

| Issue area | Current judgment | Next closure action |
| --- | --- | --- |
| #17 frontend source migration | Source lives under `frontend/web` with projection audit, `ci:verify`, release traceability, GitHub Actions workflow, packaged frontend image definition, and 211 thin-shell POC smoke. | Run or refresh frontend install/lint/build when changing browser code; complete packaged frontend image smoke/release acceptance on 211 or another Docker-capable host. |
| #21 capacity baseline / #122/#144 B3 source contract | GitHub issue #21 is currently closed, but baseline plan, snapshot/verdict/profile tools, bounded probe harness, Admin Runtime capacity/backpressure visibility, and the B3 operator-reviewed recorded snapshot source contract remain a capacity-upgrade evidence gate. The contract schema is `ai-platform.capacity-operator-reviewed-recorded-snapshot-contract.v1` for `b3_10x4_sdk_subagents`, the 10 sessions x peak 4 SDK subagents/session profile. It requires profile evidence bound to `target_profile_id = b3_10x4_sdk_subagents`, an allowlisted `evidence_source = platform_runtime_profile`, `observed_concurrent_sessions >= 10`, `observed_peak_sdk_subagents_per_session >= 4`, `sdk_subagent_fanout_measurement_ref`, and non-expansion flags `production_concurrency_defaults_raised = false`, `safe_concurrency_claimed = false`, and `ordinary_user_platform_multi_run_orchestration_enabled = false`. Historical snapshots may still import the legacy alias `ordinary_user_multi_agent_enabled = false`; the readiness path normalizes it to the canonical platform-level multi-run flag. This means no ordinary-user platform-level multi-run orchestration exposure and is not evidence that B3 is a G8 product route. The latest reviewed `755e50e` read-only capacity runtime evidence records Admin Runtime HTTP `200`, schema `ai-platform.capacity-runtime-evidence.v1`, nested gate readiness `blocked_missing_admin_runtime_sections`, and missing Admin Runtime capacity section `sandbox`; earlier reviewed visibility records could read nested gate readiness `blocked_missing_load_test_evidence` when Admin Runtime sections were present but recorded load evidence was absent. There is no new reviewed B3 capacity entry for `15903fd` beyond the newer `755e50e` visibility record. The latest bounded B3 sweep covered all seven harness load-test gates as probes only. The separate operator profile review evidence still has eight required fields: `runtime_source_identity_and_image_labels`, `tenant_user_skill_mix`, `token_cost_ledger`, `event_artifact_volume`, `sandbox_pressure_and_cleanup`, `latency_p50_p95_p99`, `error_budget_and_dead_letters`, and `rollback_plan_and_stop_conditions`. Every probe remained `probe_completed_not_gate_evidence`, `probe_only_not_recorded`, and `does_not_mark_gate_recorded = true`, so all recorded load-test gates and every required `b3_10x4_sdk_subagents` profile evidence field are still missing. Contract flags remain `does_not_raise_defaults = true`, `does_not_claim_safe_concurrency = true`, `does_not_enable_ordinary_user_platform_multi_run_orchestration = true`, and `does_not_close_b3_gate = true`. This is source/runtime visibility plus source contract only, not B3 closure. | Record approved load evidence for the seven gates before raising any production default, including SDK Agent/subagent fanout pressure for workflows where each session may invoke subagents. Until then every profile remains `do_not_raise_without_recorded_load_test_evidence`; #122/#144 do not raise production defaults, do not close B3, and must not be used as ordinary-user platform-level multi-run orchestration exposure evidence. This source/runtime visibility does not raise production defaults and does not close B3. |
| G6 governance | Source-level policies and readiness contracts exist, source-level context-pack persistence/versioning records `context_pack_version` / `context_pack_generated_at`, frontend run-playback context provenance has source tests, admin bulk-review source-route runtime-control tests and Admin Skill release dashboard source-route runtime-control tests are recorded, the Admin Runtime governance projection has a focused 211 smoke, reviewed `8e0389e` live evidence records executor context-pack acceptance, and PR #44 records reviewed 211 sandbox latency split evidence. | Keep reviewed executor context-pack and PR #44 sandbox runtime evidence under regression, convert contracts into full dashboard/visual acceptance, add real reviewed Skill release evidence, record admin bulk-review 211 acceptance and Admin Skill release 211 acceptance, and keep long-term cross-session memory fail-closed. |
| G8 / G10 expansion | Not a current platform-orchestration implementation target. SDK subagent execution can be used inside governed platform runs, but broad ordinary-user platform-level multi-run exposure needs a future reopened G8 gate. | Keep feature flags and do not broaden ordinary-user platform-level multi-run orchestration exposure until G5/G6/G7/G9 gates are closed and B3 SDK subagent fanout capacity evidence exists. |

S2 sandbox runtime smoke now has a source-level
`sandbox_runtime_smoke_contract` for `211_sandbox_latency_split_smoke`. The
contract points operators to
`scripts/generate_sandbox_runtime_evidence_211.py` and
`scripts/verify_sandbox_runtime_211.py`, uses `sudo -n docker`, prefers the
already-local cancel probe image `ai-platform:local`, and requires
`non_expansion_invariants` including
`ordinary_user_high_risk_sandbox_allowed=false` and
`ordinary_user_multi_agent_allowed=false`. Reviewed PR #44 evidence now records
`sandbox_cold_start_latency_split_211_acceptance` for the controlled verifier
run; this still does not close Docker sandbox hardening, G6/G9, or ordinary-user
sandbox expansion. Its hardening section must separate `live_platform_probe` evidence
for lease/workspace/cleanup from `source_regression_guard` evidence for
timeout/failure/cached-lease behavior.

The office executor context-pack path now also has
`executor_context_pack_runtime_acceptance_contract` with schema
`ai-platform.executor-context-pack-runtime-acceptance.v1`. Its default
generator/verifier output records
`source_probe_evidence_strength=source_probe_on_target_runtime`, a binding
check that is not live worker-run acceptance. Closure requires
`required_live_evidence_strength=live_worker_run_payload` from
`scripts/generate_executor_context_pack_evidence_211.py --live-run-id <run_id>`
followed by `scripts/verify_executor_context_pack_211.py --run-id <run_id>
--require-live-run-payload`. It anchors the 211 acceptance to
`scripts/generate_executor_context_pack_evidence_211.py`,
`scripts/verify_executor_context_pack_211.py`,
`app.repositories.get_context_snapshot_for_worker`,
`app.context_builder.executor_context_pack_from_snapshot`,
`app.executors.claude_agent_sdk_runner._context_pack_prompt_section`, and the
worker prompt-injection path. Accepted evidence must prove
`live_worker_run_payload`, `run_row_loaded`, `context_snapshot_id_present`,
`scoped_context_snapshot_loaded`,
`worker_context_ref_rebuilt_from_db_snapshot`,
`prompt_includes_bounded_summary`, `prompt_includes_context_pack_version`,
`prompt_includes_context_pack_generated_at`, `raw_storage_identifiers_absent`,
`sandbox_runtime_paths_absent`, `executor_private_content_absent`, and
`source_run_artifact_scope_tenant_workspace_user_session`, with fresh
`generated_at` evidence and explicit `source_functions` binding. Live evidence
must carry those per-item booleans under the verifier-checked
`runtime_evidence` JSON section. Source-probe
evidence still carries `does_not_close_211_acceptance=true` and
`runtime_acceptance_requires_real_run_payload=true`; the superseded PR #44 live
evidence carried `runtime_run_payload_verified=true` but does not satisfy the
current `source_run_artifact_count_positive` and public input-key redaction
checks for the named #22 runtime gap. The reviewed `8e0389e` live evidence for
`run_a618c52ee5c148a185254b68e1c81b9e` now satisfies the current verifier with
`live_worker_run_payload`, `artifact_count=2`, `file_count=1`, public
`input_keys=["attachments","message"]`, positive source-run artifact scope, and
no secret leakage. Its invariants keep
`ordinary_user_multi_agent_allowed=false`,
`ordinary_user_high_risk_sandbox_allowed=false`, and
`long_term_cross_session_memory_enabled=false`. `executor_context_pack_211_acceptance`
is recorded as closed for the named #22 runtime gap only; it does not close G6/G9,
ordinary-user platform-level multi-run orchestration exposure, long-term memory
policy, packaged frontend acceptance, or production readiness.

Frontend packaged delivery now has the same fail-closed visibility through
`packaged_runtime_smoke_contract`, sourced from
`tools/frontend_packaged_runtime_smoke.py` with schema
`ai-platform.frontend-packaged-runtime-smoke.v1`. Current governance readiness
keeps it at `blocked_missing_runtime_evidence` with
`frontend_packaged_runtime_smoke_evidence_missing`, runtime policy
`docker_capable_host_only_no_local_windows_docker`, formal frontend Compose
runtime required, and the remaining
`frontend_packaged_image_delivery_and_release_acceptance` blocker. This does
not close packaged frontend release acceptance, G6/G9, #156, or #21 capacity.

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
Bounded probes are allowed to fail closed on missing Admin Runtime projection
sections; that improves operator safety but still does not count as recorded
load-test evidence.
