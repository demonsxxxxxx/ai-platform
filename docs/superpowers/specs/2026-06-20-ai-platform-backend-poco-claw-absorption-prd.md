# Backend poco-claw Absorption PRD

> Status: backend-only absorption contract for one PR.
>
> Scope: absorb the useful backend ideas from poco-claw `c66da3cf`
> (`0.5.7`, merged PR #145 on 2026-06-16) into ai-platform planning and
> acceptance boundaries. This PRD does not copy code, add dependencies, or
> change runtime behavior.
>
> Frontend UI absorption remains outside this PRD.

## 1. Source Authority And Evidence Basis

This PRD is backend-only. It is a companion to
`docs/superpowers/specs/2026-06-18-ai-platform-backend-phased-prd.md` and the
active product PRD v2. It does not replace either document.

poco-claw is a reference source, not ai-platform authority. ai-platform remains
the authority for tenant/workspace/user identity, RBAC, source authority,
release evidence, sandbox policy, worker queue behavior, file/artifact ACL,
exact tool permission, Skill release evidence, Admin Runtime projections, and
stage closure.

The poco-claw reference was reviewed at commit
`c66da3cf3ae34b471e5f3a4b8e74bd95c3413283`. The useful delta is:

| Area | poco-claw evidence | ai-platform use |
| --- | --- | --- |
| Persistent runtime | `backend/app/services/persistent_runtime_service.py`, `executor_manager/app/services/runtime_idle_service.py`, `executor_manager/app/services/container_pool.py`, and `executor_manager/app/services/run_pull_service.py` | Planning input for B2 sandbox runtime lifecycle and B3 capacity/cost control. |
| Internal executor-manager authentication | `backend/app/api/v1/internal_persistent_runtimes.py` uses `require_internal_token`; executor-manager client paths call backend internal runtime APIs. | Planning input for internal worker/sandbox/API trust boundaries. |
| Session share backend contract | `backend/app/services/session_share_service.py` creates share, fork, and channel-import flows with snapshot payloads and sanitized config. | Planning input for backend share/fork/export contracts after B5 file/artifact ACL is credible. |
| File reference backend contract | poco-claw's chat input file references show the UI need for stable file references. | Backend requirement input for file namespace, selected-file snapshots, and artifact ACL. |
| Skill reference backend contract | poco-claw's `/` and `$` skill reference model maps UI references into skill IDs. | Backend requirement input for immutable Skill version resolution and run snapshots. |
| Group-level skill selection | poco-claw skills page can batch toggle grouped skills. | Backend requirement input for Skill grouping, visibility, policy, and audit. |

OpenAI Codex manual guidance used for this one-PR work:

| Official source | Rule applied here |
| --- | --- |
| OpenAI Codex manual: Worktrees | Use an isolated worktree for this PR. |
| OpenAI Codex manual: Custom instructions with AGENTS.md | Follow repository `AGENTS.md` and project PRD authority. |
| OpenAI Codex manual: Review | Record review evidence before merge. |
| OpenAI Codex manual: Subagents | Use subagents only for bounded review or read-only analysis. |

Single-PR rule: Keep one branch and one pull request for this issue. Do not
split this absorption PRD into multiple PRs. If the scope grows beyond planning
and tests, stop and open follow-up issues instead of expanding this PR.

## 2. Absorption Scope

This PRD absorbs concepts, not code. No poco-claw code is copied or vendored by
this PRD. No runtime dependency is introduced by this PRD. No platform-level
multi-run agent harness is introduced. Claude Agent SDK remains the execution
layer.

Backend absorption means:

1. Record which poco-claw concepts are useful.
2. Map each concept to an ai-platform backend stage.
3. Preserve ai-platform authority and status labels.
4. Define the minimum acceptance evidence before any future implementation may
   claim progress.
5. Mark frontend-only ideas as backend contract inputs only.

The following poco-claw ideas are accepted as backend planning inputs:

| Concept | Accepted backend interpretation |
| --- | --- |
| persistent runtime registry | ai-platform may later model long-lived sandbox/worker runtime identity, but it must bind through platform-owned run, tenant/workspace/user, sandbox_leases, and audit. |
| idle timeout | A future runtime lifecycle must define when a runtime can move from active to warm idle or stopped without losing required run evidence. |
| keepalive | Operator or workflow keepalive must be bounded, auditable, and denied to ordinary users unless a stage gate explicitly allows it. |
| sleep | Runtime sleep must stop or release compute without deleting audit, run, artifact, or lease evidence. |
| stale runtime detection | Missing worker/sandbox/container state must become an Admin Runtime state and release-evidence caveat, not silent success. |
| runtime-to-container binding | Any runtime-to-container or runtime-to-sandbox relation must be derived from platform-owned IDs, never user payload. |
| internal executor-manager authentication | Worker, sandbox, executor, and internal backend APIs need internal-token or equivalent trust boundaries plus deny-path tests. |
| session share backend contract | Share/fork/import can be planned only as redacted backend snapshots with ACL, provenance, and rollback. |
| file reference backend contract | File references must resolve to ai-platform file IDs, selected-file snapshots, retention, and file/artifact ACL. |
| skill reference backend contract | Skill references must resolve to released immutable versions and pinned run snapshots, not mutable names. |
| group-level skill selection | Skill groups require backend visibility, policy, audit, and dependency evidence before frontend batch toggle is product authority. |

## 3. Backend Capability Mapping

| Stage | poco-claw concept | ai-platform authority | First useful issue shape |
| --- | --- | --- | --- |
| B1 | Session share/fork can carry conversation and run context. | Memory/context policy, context snapshots, run_context_snapshots, tenant/workspace/user, redaction, export/delete posture. | Define share/fork context snapshot rules without long-term memory defaults. |
| B2 | Persistent runtime lifecycle, sleep, stale runtime detection, runtime-to-container binding. | sandbox_leases, sandbox provider, worker queue, callback tokens, Admin Runtime sandbox projection, release evidence. | Design governed runtime lifecycle around platform-owned leases, cleanup, cancel, and failure projection. |
| B3 | Persistent runtimes and keepalive reduce cold start but can hide capacity pressure. | worker queue, admission/backpressure, model gateway, token/cost ledger, event/artifact volume, sandbox pressure, Admin Runtime. | Extend `b3_10x4_sdk_subagents` evidence to include persistent-runtime pressure and keepalive stop conditions. |
| B4 | Skill reference and group-level skill selection. | Skill release evidence, immutable version, dependency evidence, pinned run snapshots, visibility and audit. | Define backend API projection for referenced Skill IDs, group state, and release eligibility. |
| B5 | File reference and session share artifact export. | file/artifact ACL, preview/download policy, exact tool permission, retention, redaction, selected-file run snapshot. | Prove file reference resolution and share artifact projection cannot bypass ACL or redaction. |
| B6 | Share/fork/import supports collaboration and operations beta. | Admin Runtime, trace/export, release evidence, workflow owner signoff, rollback, support model. | Only after B1-B5, define workflow-owner share/fork acceptance for selected internal workflows. |

B0 is a prerequisite watch item, not a poco-claw absorption target. If current
source/runtime evidence is stale, B0 blocks all runtime claims before any B1-B6
implementation can be called `211 verified`.

## 4. Acceptance Gates

The following gates must exist before any future implementation may claim more
than `local partial`.

| Gate | Required evidence before status can advance |
| --- | --- |
| Runtime lifecycle planning | PRD or design records active, warm idle, sleeping, stale, stopped, and deleted states; each state names owner, transition, rollback, and projection rules. |
| Runtime lifecycle local contract | Tests prove runtime state cannot be derived from user payload; missing worker/sandbox/container state is classified as stale or failed; cleanup cannot delete audit evidence. |
| Runtime lifecycle 211 evidence | 211 smoke records source/runtime identity, sandbox/worker state, launch, keepalive if allowed, sleep or stop, stale detection, cleanup, redaction, and reviewed release evidence. |
| Internal auth local contract | Worker/sandbox/internal API calls require internal auth and deny missing, wrong, expired, or cross-tenant tokens. |
| Skill reference local contract | Backend resolves skill references to released immutable versions, records used-skill snapshots, and denies disabled/unreviewed/mutable versions. |
| Skill group local contract | Backend records group membership, policy, visibility, audit, dependency evidence, and batch state changes without allowing frontend-only authority. |
| File/share local contract | File references and share snapshots preserve tenant/workspace/user/run ownership, selected-file identity, redaction state, retention, and ACL denial. |
| Capacity evidence | The 10 sessions x peak 4 SDK subagents profile remains B3 evidence work and must include persistent-runtime pressure if that lifecycle is enabled. |

Status label rules:

- `local partial`: docs, tests, or source contracts exist.
- `PR ready`: one PR contains issue link, verification, and boundary statement.
- `reviewed`: review evidence is recorded and findings are resolved or
  explicitly tracked.
- `merged`: the single PR lands on main.
- `211 verified`: named runtime evidence exists for the named source/runtime
  subject.
- `gate closable`: issue, PR, review, verification, runtime evidence where
  required, rollback, and caveats are complete.

## 5. Non-Goals And Rejection Rules

Reject these interpretations:

| Rejected claim | Reason |
| --- | --- |
| A docs-only absorption PR cannot create `211 verified` status. | Runtime claims require named source/runtime evidence. |
| A reference implementation cannot close B2 sandbox hardening. | B2/G7 needs ai-platform sandbox leases, provider hardening, resource/egress/security evidence, and reviewed 211 proof. |
| Persistent runtime design cannot raise worker defaults. | B3 capacity must prove load, backpressure, model-gateway, sandbox, token/cost, events/artifacts, cleanup, and rollback first. |
| A slash or skill reference UI pattern cannot define backend Skill release authority. | Backend Skill release authority is immutable version, release decision, dependency evidence, pinned snapshot, and audit. |
| Session sharing design cannot expose public artifacts without B5 ACL and redaction evidence. | Share/fork/import must preserve tenant/workspace/user/run/file/artifact ACL and redaction. |
| The 10 sessions x peak 4 SDK subagents profile remains B3 evidence work. | SDK subagent capability exists, but production capacity and governance remain unproven until measured. |
| A poco-claw internal API pattern cannot replace ai-platform auth. | ai-platform must enforce company auth, internal token, tenant isolation, RBAC, and audit. |
| A persistent runtime container label cannot become source authority. | Source authority still comes from local main, 211 source, deploy composition, image labels, runtime health, and release evidence. |

## 6. Single-PR Delivery Contract

This issue must be completed in one PR:

1. One branch: `codex/backend-poco-absorption-prd`.
2. One PR against `main`.
3. One backend-only PRD file.
4. One focused document-contract test file or focused extension to an existing
   document-contract test.
5. No frontend implementation.
6. No backend runtime behavior change.
7. No dependency addition.
8. No code copied from poco-claw.
9. No separate PR for review fixes; review fixes must land on the same PR
   branch.

If follow-up implementation is needed, create issues from this PRD after this
PR is merged. Do not smuggle implementation into the absorption PR.

## 7. Review And Verification Requirements

Before the PR is considered `PR ready`, run:

```powershell
python -m pytest tests\test_backend_poco_absorption_prd.py -q --basetemp .pytest-tmp\poco-absorption-green
python -m pytest tests\test_backend_phased_prd.py tests\test_source_authority_docs.py tests\test_backend_poco_absorption_prd.py -q --basetemp .pytest-tmp\poco-absorption-docs
python -m compileall -q app tools scripts
git diff --check
```

Before merge, record review evidence on the same PR. Review can be GitHub PR
review, formal review comment, or an evidence-backed review summary if formal
`reviewDecision` remains empty. Subagent review does not replace GitHub PR
review evidence. It can only help find issues before the PR is marked ready.

Use subagents only for bounded review or read-only analysis. Do not delegate
write, deploy, remote runtime, or long-running operational work unless the
delegation path is confirmed to inherit the same permission posture as the main
session.

PR body must state:

1. The poco-claw commit reviewed.
2. The OpenAI Codex manual basis used: Worktrees, Custom instructions with
   AGENTS.md, Review, and Subagents.
3. The backend-only scope.
4. The single-PR boundary.
5. The verification commands and observed results.
6. The claim boundary: this PR can be `merged` and `reviewed`, but not
   `211 verified` or `gate closable` because it is docs/test only.
