# ai-platform Gate Status Snapshot

Date: 2026-06-17

This snapshot keeps the current PRD, foundation roadmap, guardrails, repository
code, 211 runtime, and issue-driven priorities aligned. It is not automatic
gate-closure evidence and must not be used to auto-close GitHub issues. Gate
closure still requires the
issue -> PR -> review -> merge -> 211 deploy/smoke -> close issue workflow in
`docs/agent-rules/github-issue-pr-workflow.md`.

## Foundation Alpha POC Smoke

Status: 211 POC smoke refreshed for the merged #34-#39 S1 runtime subject;
not production gate closure.

Generate the current operator readiness summary with:

```powershell
python tools/foundation_alpha_readiness.py --format json
python tools/foundation_alpha_readiness.py --format markdown
```

The operator summary must be read through `runtime_source_relation`. When the
source tree is newer than the verified running API/worker image, it reports
`source_synced_runtime_pending` and sets
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

After later runtime-affecting main changes, latest-main readiness must be
refreshed again. A clean `origin/main` readiness run at
`8afc463ef6c55a5ae1d025f3c99c0b7d654b5fe4` on 2026-06-17 reported
`foundation_alpha_stage_complete=false`,
`foundation_alpha_stage_status=runtime_rollout_required`,
`runtime_relevant_source_verified_by_running_runtime=false`, and
`stage_acceptance_blockers=["foundation_runtime_concurrency_evidence"]`.
GitHub issue #65 tracks the required fresh 211 runtime rollout, smoke, and
Foundation Runtime concurrency evidence. The observed live 211 API/worker
runtime during that triage was `ai-platform:issue61-1390074` with labels
pointing to `139007466023956374f8353332d912c2c988fe10`; compose still recorded
an external legacy env-file path label instead of a fully reconciled repo-local
deploy-env authority, so the G0 source-authority caveat remains open. This does not raise production
concurrency defaults, close the separate seven-gate capacity-upgrade evidence
gate, claim Docker sandbox hardening, open platform-level multi-run
orchestration, enable long-term memory by default, or permit department
rollout.

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

The full a15c74f S2-0 POC smoke refresh includes reviewed, redacted entries for
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
The local readiness summary for later `origin/main` source now reports
`runtime_rollout_required_for_current_source=true`,
`runtime_relevant_source_verified_by_running_runtime=false`, and
`foundation_alpha_stage_complete=false`; issue #65 tracks the fresh current
subject rollout and rerun required before the latest source can be described as
runtime-verified.

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
ordinary-user multi-agent, does not claim Docker sandbox hardening, does not
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
evidence only; the `380de6b` evidence above is the active Foundation Alpha POC
reference, and the `79495bf`, `dff48fb`, and `ac9a86b` evidence is now retained
as superseded reviewed history.
The immediately superseded runtime image was `ai-platform:948179c-skill-release-scaffold`.

This smoke does not close the recorded capacity-evidence gate, G7 Docker sandbox hardening, ordinary
user multi-agent exposure, department rollout, alert delivery enablement,
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

| Gate | Current status | Evidence now in repository | Remaining blocker before closure |
| --- | --- | --- | --- |
| G0-G1 Source Authority / Security Baseline | Reviewed S2-0 runtime smoke exists for `a15c74f`, but latest-main `8afc463` readiness is back to `runtime_rollout_required`; keep under regression and do not claim current-source closure. | PRD v2, technical acceptance matrix, roadmap, guardrails, source-authority tests, repo-local compose context, frontend source migration, redacted deploy templates, 2026-06-17 `a15c74f` POC release evidence, 2026-06-17 `a15c74f` Foundation Runtime concurrency evidence, superseded 2026-06-16 `8e0389e` evidence, and 2026-06-15 `380de6b` historical baseline evidence. | GitHub #65 fresh 211 runtime rollout/smoke/FRC evidence, full issue/PR/review closure path, production auth rollout, exact current-source runtime match when runtime-affecting code changes again, and release-path/deployment-layout reconciliation are still required before production closure. |
| G2-G4 Control Plane MVP | Substantial coverage; keep under regression. | Session/run/file/artifact/skill/tool/memory/event/audit contracts, repositories, routes, schema indexes, and focused tests. | Full regression before PR/deploy, plus no executor-owned platform schema drift. |
| G5 Run Lifecycle / Worker Runtime V1 | Foundation Alpha POC verified queue/run/worker execution and Admin capacity/backpressure projection exist; the latest clean source still requires current-subject Foundation Runtime concurrency evidence under #65; not capacity-closed. | Tenant-aware queue lease, worker maintenance, active-run admission, bounded metadata, Admin Runtime capacity/backpressure projection, #20 roadmap closure notes, 2026-06-17 `a15c74f` POC verifier evidence, 2026-06-17 `a15c74f` verified Foundation Runtime concurrency evidence, superseded 2026-06-16 `8e0389e` failed-closed FRC diagnostics, superseded 2026-06-17 `8e0389e` verified Foundation Runtime concurrency evidence, and 2026-06-15 `380de6b` historical accepted concurrency evidence. | GitHub #65 current-subject Foundation Runtime concurrency evidence, #21 is currently closed in GitHub but recorded seven-gate load evidence, large queue bounded lookup pressure, worker parallelism/capacity profiling, and multi-tenant load evidence are still missing. Production defaults stay unchanged. |
| G6 Tool / Skill / Memory Governance | Admin Runtime governance projection now has focused 211 smoke evidence for the POC runtime, and Foundation readiness records `memory_context_controls` with `session_scoped_memory=true`, `ordinary_user_opt_out=true`, `retention_cleanup=true`, `delete_redaction=true`, `public_admin_projection_safe=true`, `long_term_cross_session_memory_fail_closed=true`, exact tool-permission decision lookup source tests, admin bulk-review source-route runtime-control tests, Admin Skill release dashboard source-route runtime-control tests, source-level context-pack persistence/versioning, user-visible context provenance API projection source tests, frontend run-playback context provenance projection source tests, document-centric follow-up state source tests, reviewed `8e0389e` 211 executor context-pack evidence, and reviewed PR #44 211 sandbox latency split evidence. G6 remains partial and ordinary-user expansion remains blocked. | Tool policy taxonomy/history, exact tool-permission decision lookup source tests, admin bulk-review source-route runtime-control tests, Admin Skill release dashboard source-route runtime-control tests, public permission-card projection, skill release/dependency policy contracts, memory delete/retention/redaction/export readiness, office context-pack architecture readiness, `source_level_context_pack_persistence_and_versioning`, `context_pack_version`, `context_pack_generated_at`, context snapshot public provenance projection contract, user-visible context provenance API projection source tests, frontend run-playback context provenance projection source tests, document-centric follow-up state source tests, `8e0389e` executor context-pack runtime evidence, PR #44 `office-context-runtime` sandbox latency evidence, governance readiness CLI, POC runs using governed skills, and 2026-06-15 380de6b governance runtime smoke evidence. | Legacy frontend route remap/policy enforcement, signed package or SBOM review evidence, dependency vulnerability/license evidence, admin bulk-review visual acceptance, admin bulk-review 211 acceptance, Admin Skill release visual acceptance, Admin Skill release 211 acceptance, long-term cross-session memory policy closure, ordinary-user G8/G10 exposure controls, production Docker sandbox hardening, packaged frontend acceptance, and broader 211 acceptance. |
| G7 Sandbox / Resource Hardening | Blocked for high-risk expansion. | Fake provider remains local/test-only; capacity docs expose sandbox limits and missing hardening warnings. | Docker provider hardening, egress/quota policy, orphan cleanup, container security options, and Docker-capable 211 smoke. |
| G8 Multi-Agent Controlled Beta | Deferred parking-lot for platform-owned multi-run orchestration. SDK-internal agent/subagent behavior stays inside one governed platform run. | Historical dispatcher and child-run admission work exists behind controls but is not the current product route. | Reopen only with a focused issue, tenant quota/backpressure design, event/artifact/cancel semantics, and no ordinary-user exposure before prior gates. |
| G9 Observability / Quality / Ops | Reviewed release-evidence runtime acceptance and alert/trace runtime acceptance exist for `a15c74f`; latest-main current-source runtime acceptance must be refreshed under #65; G9 remains partial for Operations Beta. | Admin Runtime overview, capacity/governance/observability readiness docs and tools, error taxonomy/dashboard contracts, release-evidence contracts, reviewed 211 release-evidence runtime export/retention acceptance for `a15c74f`, reviewed 211 alert/trace export runtime acceptance for `a15c74f`, trace/audit export contracts, frontend projection audit, and reviewed 211 POC smoke entry. | S2/G9 closure still requires #65 current-subject smoke where relevant, runtime dashboard acceptance, recorded capacity evidence, model-gateway backpressure evidence, golden-set eval runtime, alert delivery enablement/runtime calibration, and remaining Admin Runtime observability follow-ups. |
| G10 Internal Beta / Department Rollout | Blocked. | Candidate internal workflows are named only as examples in roadmap. | Select 1-2 real internal workflow owners, complete prior gates, record cost/quality/audit/rollback evidence, and pass 211 acceptance. |

## Issue-Driven Thin Spots

| Issue area | Current judgment | Next closure action |
| --- | --- | --- |
| #17 frontend source migration | Source lives under `frontend/web` with projection audit, `ci:verify`, release traceability, GitHub Actions workflow, packaged frontend image definition, and 211 thin-shell POC smoke. | Run or refresh frontend install/lint/build when changing browser code; complete packaged frontend image smoke/release acceptance on 211 or another Docker-capable host. |
| #21 capacity baseline | GitHub issue #21 is currently closed, but baseline plan, snapshot/verdict/profile tools, bounded probe harness, and Admin Runtime capacity/backpressure visibility remain a capacity-upgrade evidence gate; bounded probes now fail closed when successful Admin Runtime overview responses miss required baseline sections. | Record approved load evidence for the seven gates before raising any production default. Until then every profile remains `do_not_raise_without_recorded_load_test_evidence`. |
| G6 governance | Source-level policies and readiness contracts exist, source-level context-pack persistence/versioning records `context_pack_version` / `context_pack_generated_at`, frontend run-playback context provenance has source tests, admin bulk-review source-route runtime-control tests and Admin Skill release dashboard source-route runtime-control tests are recorded, the Admin Runtime governance projection has a focused 211 smoke, reviewed `8e0389e` live evidence records executor context-pack acceptance, and PR #44 records reviewed 211 sandbox latency split evidence. | Keep reviewed executor context-pack and PR #44 sandbox runtime evidence under regression, convert contracts into full dashboard/visual acceptance, add real reviewed Skill release evidence, record admin bulk-review 211 acceptance and Admin Skill release 211 acceptance, and keep long-term cross-session memory fail-closed. |
| G8/G10 expansion | Not a current implementation target. | Keep feature flags and do not broaden ordinary-user G8/G10 exposure until G5/G6/G7/G9 gates are closed. |

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
ordinary-user G8/G10 exposure, long-term memory policy, packaged frontend
acceptance, or production readiness.

Frontend packaged delivery now has the same fail-closed visibility through
`packaged_runtime_smoke_contract`, sourced from
`tools/frontend_packaged_runtime_smoke.py` with schema
`ai-platform.frontend-packaged-runtime-smoke.v1`. Current governance readiness
keeps it at `blocked_missing_runtime_evidence` with
`frontend_packaged_runtime_smoke_evidence_missing`, runtime policy
`docker_capable_host_only_no_local_windows_docker`, and the remaining
`frontend_packaged_image_delivery_and_release_acceptance` blocker. This does
not close packaged frontend release acceptance, G6/G9, or #21 capacity.

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
