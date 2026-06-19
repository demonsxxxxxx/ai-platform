# ai-platform G6 Governance Readiness

Date: 2026-06-12

This document records the current G6 Tool / Skill / Memory Governance baseline.
It is an operator readiness snapshot, not a gate-closure claim. G6 remains
partial until frontend route policy enforcement/remap, release governance
evidence, quarantined inactive legacy source remap, packaged frontend image
delivery/release acceptance, full dashboard/visual acceptance, production
Docker sandbox hardening, and ordinary-user G9 acceptance are complete. The
reviewed `8e0389e` live worker-run evidence records executor context-pack 211
acceptance for the named #22 runtime gap, and PR #44 keeps reviewed 211 sandbox
latency split evidence for the named #22 cold-start runtime gap.

Generate the current readiness snapshot from the repository root:

```powershell
python tools/governance_readiness.py --format markdown
python tools/governance_readiness.py --format json
python tools/tool_policy_readiness.py --format markdown
python tools/tool_policy_readiness.py --format json
python tools/tool_policy_bulk_review_readiness.py --format markdown
python tools/tool_policy_bulk_review_readiness.py --format json
python tools/skill_release_readiness.py --format markdown
python tools/skill_release_readiness.py --format json
python tools/skill_release_dashboard_readiness.py --format markdown
python tools/skill_release_dashboard_readiness.py --format json
python tools/skill_release_readiness.py --review-template --skill-id <skill-id> --format json
python tools/skill_release_readiness.py --review-template --skill-id <skill-id> --format json --output skills/<skill-id>/ai-platform-skill-release-review.json
python tools/memory_erasure_readiness.py --format markdown
python tools/memory_erasure_readiness.py --format json
python tools/office_context_readiness.py --format markdown
python tools/office_context_readiness.py --format json
python tools/b1_memory_context_readiness.py --format markdown
python tools/b1_memory_context_readiness.py --format json
python tools/b2_sandbox_readiness.py --format markdown
python tools/b2_sandbox_readiness.py --format json
python tools/verify_governance_runtime_smoke.py --base-url http://127.0.0.1:8020 --commit-sha <source-tree-commit> --runtime-subject-commit-sha <runtime-subject-commit> --image <runtime-image>
```

The script intentionally does not print callback tokens, sandbox workspace
roots, raw queue keys, object-storage internal identifiers, executor-private
data, raw runtime paths, real `.env` values, or Skill staging paths.

As of 2026-06-08, `tools/governance_readiness.py` also embeds a bounded
frontend projection-audit evidence summary for operator use. The summary keeps
the Admin Runtime hot path lightweight, but the CLI output includes
`domains.frontend_projection.evidence.projection_audit.open_gap_details` with
route counts, route scopes, required remap/hide actions, and quarantined-source
samples. This makes the G6/G9 frontend blockers actionable without exposing raw
storage keys, executor-private payload names, sandbox workdirs, secret-like
values, or local machine paths in the governance readiness JSON.

## Admin Runtime Signal Path

Admins consume the live operational projection at:

```text
GET /api/ai/admin/runtime/overview
```

The overview now exposes:

- `capacity`: current configured capacity ceiling and missing load-test gates.
- `backpressure`: live admission, queue, and DB pool pressure reasons.
- `governance`: G6 readiness domains, implemented controls, open gaps, and
  next checks.
- `observability_readiness`: G9 readiness domains, implemented controls, open
  gaps, and next checks.

All projections are admin-only, same-tenant, and designed as public/admin
operational projections. They must not become a path for executor-private data
or secret-like runtime configuration.

## Current G6 Status

| Domain | Implemented baseline | Remaining gap |
| --- | --- | --- |
| Tool permission | Admin tool policy inventory, tenant-scoped policy update audit, bounded admin change-history projection through `GET /api/ai/admin/tool-policies/history`, user request/decision flow, exact `tool_call_id` / stable request-fingerprint decision lookup source tests, fail-closed risk/write policy evaluation, public permission-card projection, audit-visible legacy route policy mapping, secret-safe allow/ask/deny taxonomy evidence through `tools/tool_policy_readiness.py`, platform-registered-MCP-only policy evidence with ordinary-user custom MCP disabled, and contract-only Admin bulk-review dashboard readiness through `tools/tool_policy_bulk_review_readiness.py` / `admin_policy_bulk_review_dashboard_contract` | Policy enforcement or ai-platform projection remap for legacy frontend admin/MCP/model/envvar/channel surfaces, plus `admin_policy_bulk_review_runtime_acceptance`, `admin_policy_bulk_review_visual_acceptance`, and `admin_policy_bulk_review_211_acceptance` |
| Skill governance | Version registry, promote/rollback release policy, dependency policy materialization, skill snapshot and release-decision lock, secret-safe skill release readiness snapshot, pending review-manifest template entrypoint, source-level `ai-platform.skill-dependency-review-policy.v1` contract, source-level `ai-platform.skill-signed-package-evidence-contract.v1` / `skill_signed_package_evidence_contract`, source-level validation for signed-package evidence JSON, and contract-only Admin Skill release dashboard readiness through `tools/skill_release_dashboard_readiness.py` / `admin_skill_release_dashboard_contract` | SBOM or signed-package release evidence plus reviewed manifests, dependency vulnerability/license evidence, `skill_dependency_review_policy_runtime_acceptance`, plus `admin_skill_release_dashboard_runtime_acceptance`, `admin_skill_release_dashboard_visual_acceptance`, and `admin_skill_release_dashboard_211_acceptance` |
| Memory governance | Session-bound records, ordinary-user opt-out, Admin policy inventory, retention cleanup, redaction, Admin redaction preview/audit route, long-term memory fail-closed, delete/retention/export/redaction-preview erasure evidence snapshot through `tools/memory_erasure_readiness.py`, source-level office context-pack contract/readiness through `tools/office_context_readiness.py`, B1 memory/context readiness rollup through `tools/b1_memory_context_readiness.py`, source-level context-pack persistence/versioning through `source_level_context_pack_persistence_and_versioning`, context snapshot public provenance projection with `context_pack_version` and `context_pack_generated_at`, user-visible context provenance API projection source tests, frontend run-playback context provenance projection source tests, source-level office execution-tier router tests, executor context-pack prompt injection source tests, document-centric follow-up state source tests, reviewed B1 `211_memory_enabled_document_workflow_smoke` evidence, B1 merged-source runtime evidence review for `75ab69b`, B1 rollback boundary local operator contract, reviewed `8e0389e` 211 executor context-pack evidence, the source-level sandbox cold-start latency split observability contract, and reviewed PR #44 211 sandbox cold-start latency split evidence. | Full G6/G9 dashboard/visual acceptance, long-term cross-session memory policy closure, production Docker sandbox hardening, packaged frontend acceptance, and ordinary-user rollout acceptance |
| Frontend projection | Source migrated into `frontend/web`, `ci:verify`, GitHub Actions frontend workflow, release traceability CLI, static `dist` manifest with build-provenance same-commit gate, packaged frontend image definition traceability, non-push CI packaged-image build/provenance contract, `tools/frontend_projection_audit.py`, projection audit wired as the first frontend `ci:verify` step, public/admin projection audit baseline, machine-readable legacy route policies, active-browser legacy route policy audit, active browser entry graph clear of forbidden private/secret-like projection terms, inactive legacy secret-like sources quarantined, Profile env-var surface removed from the active browser entry graph, Settings includes an admin-only capacity/backpressure/governance section fed only by `GET /api/ai/admin/runtime/overview`, 211 frontend acceptance for the Admin Runtime section at commit `f579155f3ec0ac7e37dd7b525f8eab27f7fd2e35` | Quarantined inactive legacy model/channel/envvar sources need ai-platform projection remap, ordinary-user G9 acceptance for legacy admin/MCP/model/envvar/channel routes, packaged frontend image smoke and release acceptance on 211 or another Docker-capable host |

The frontend projection evidence now records three current structured blockers:
all legacy production routes still need policy enforcement or ai-platform
projection remap, the active browser entry graph still references 15 legacy
route policies that must be hidden or policy-gated before ordinary-user G9
acceptance, and 40 quarantined legacy source violations remain outside the
active entry graph but must be remapped or removed before rollout.

## Source Readiness Evidence

`tools/tool_policy_readiness.py` now records the offline allow/ask/deny policy
taxonomy for MCP tools with schema `ai-platform.tool-policy-readiness.v1`. The
snapshot is tied to the current `evaluate_tool_policy()` contract for active
tool cases and records six operator-readable cases: one low-risk read-only
auto-allow case, three ask cases for medium/high risk or write-capable tools,
and two deny cases for disabled registry or disabled tenant policy. It also
records decision options `allow_once`, `allow_for_run`, and `deny`, plus the
policy contract that disabled tools, missing tenant policy, expired decisions,
or explicit deny fail closed. The same snapshot now records the MCP registry
contract: tool usage is limited to platform-registered MCP tools, tenant policy
is same-tenant scoped, unregistered tools deny, and ordinary-user custom MCP is
not allowed. The admin-only
Source tests now bind worker MCP and Claude SDK tool permission reuse to exact
`tool_call_id` semantics for `allow_once` / `deny` and stable request
fingerprints (`input_sha256` or `command_sha256`) for `allow_for_run`; callers
use the explicit `get_exact_tool_permission_decision` repository API instead of
a broad latest-decision lookup. `GET /api/ai/admin/tool-policies/history`
projection now reads the existing
same-tenant audit log for `admin.tool_policy.updated`, applies a bounded limit,
and returns only allowlisted public policy fields. This removes the previous
taxonomy-evidence gap and narrows the history-view gap from the G6 source
baseline. `tools/tool_policy_bulk_review_readiness.py` now records a
contract-only Admin review dashboard baseline with schema
`ai-platform.tool-policy-bulk-review-readiness.v1` and nested
`ai-platform.tool-policy-bulk-review-dashboard-contract.v1`. The contract binds
the dashboard to the existing admin-only same-tenant policy inventory, history,
and single-tool update routes; requires bounded inventory/history, taxonomy
summary, decision options, legacy-route gap summary, filters, per-tool diff
preview, update confirmation, and change-history drilldown; and keeps raw
registry connection details, credentials, tool request bodies, sandbox working
directories, and executor-private runtime data out of the public/admin
projection. This source-level exact-decision evidence does not close G6 by
itself; runtime, visual, 211, and legacy frontend projection acceptance remain
open.

This removes the previous coarse Admin dashboard blocker from the source
baseline and replaces it with explicit runtime, visual, and 211 acceptance
gaps: `admin_policy_bulk_review_runtime_acceptance`,
`admin_policy_bulk_review_visual_acceptance`, and
`admin_policy_bulk_review_211_acceptance`. It does not close G6, does not add a
batch mutation API, and does not permit ordinary-user access to policy
internals. Route enforcement/remap for legacy frontend surfaces, Admin
dashboard acceptance, and 211 runtime smoke remain required before gate closure.

## 211 Acceptance Evidence

On 2026-06-12, `tools/verify_governance_runtime_smoke.py` returned `ok: true`
on the 211 API for runtime subject commit
`d4486ebf5a33ce23a632a69bcf07ef1220b61ea3` and image
`ai-platform:d4486eb-observability-evidence-loader`. The smoke verified ordinary-user
Admin Runtime denial, same-tenant admin access, governance schema
`ai-platform.governance-readiness.v1`, required tool/skill/memory governance
domains, tool policy taxonomy and bulk-review signals, skill release/dashboard
signals with `dashboard_contract` trimmed from the overview projection, memory
fail-closed/context-provenance/office-context signals, and no forbidden
projection terms in the reviewed summary. The 211 source marker, source snapshot, source revision labels, OCI revision
labels, and image internal source marker pointed to
`d4486ebf5a33ce23a632a69bcf07ef1220b61ea3`, and the compose config label pointed to the repo-local 211 deploy
composition. Runtime-subject, runtime-rollout, and source_revision alias labels
still carried prior rollout metadata, and the compose environment-file label
still recorded the old external env-file path. The reviewed release evidence
entry is
`docs/release-evidence/foundation-alpha-poc/d4486ebf5a33ce23a632a69bcf07ef1220b61ea3/2026-06-12-211-foundation-alpha-poc-d4486eb-governance-runtime-smoke.json`.

This smoke records the focused Admin Runtime governance projection proof for
the current `d4486eb` Foundation Alpha POC runtime subject. It does not close
ordinary-user confirmation-card UX, full Admin dashboard/visual acceptance,
signed-package/SBOM review evidence, dependency vulnerability/license evidence,
office context-pack persistence/versioning, 211 executor context-pack
acceptance, execution-tier router acceptance, frontend context provenance
acceptance, stale runtime labels, or broader production governance rollout.

On 2026-06-12, `tools/verify_governance_runtime_smoke.py` returned `ok: true`
on the 211 API for runtime subject commit
`b96d02e232176bade455f2af2bc3080f8f372206` and image
`ai-platform:b96d02e-release-evidence-runtime-acceptance`. The smoke verified ordinary-user
Admin Runtime denial, same-tenant admin access, governance schema
`ai-platform.governance-readiness.v1`, required tool/skill/memory governance
domains, tool policy taxonomy and bulk-review signals, skill release/dashboard
signals with `dashboard_contract` trimmed from the overview projection, memory
fail-closed/context-provenance/office-context signals, and no forbidden
projection terms in the reviewed summary. The 211 API and worker labels matched
the runtime subject, and the compose config label pointed to the repo-local 211
deploy composition. The reviewed release evidence entry is
`docs/release-evidence/foundation-alpha-poc/b96d02e232176bade455f2af2bc3080f8f372206/2026-06-12-211-foundation-alpha-poc-b96d02e-governance-runtime-smoke.json`.

This smoke records only the focused Admin Runtime governance projection proof
for the Foundation Alpha POC and the release-evidence-runtime-acceptance
rollout. It does
not close ordinary-user confirmation-card UX, full Admin dashboard/visual
acceptance, signed-package/SBOM review evidence, dependency
vulnerability/license evidence, office context-pack persistence/versioning, 211
executor context-pack acceptance, execution-tier router acceptance, frontend
context provenance acceptance, or broader production governance rollout.

The immediately superseded `948179c73734aa61ed764fb3485f5415fca8f193`
governance smoke remains historical reviewed evidence for the
skill-release-scaffold rollout at
`docs/release-evidence/foundation-alpha-poc/948179c73734aa61ed764fb3485f5415fca8f193/2026-06-12-211-foundation-alpha-poc-948179c-governance-runtime-smoke.json`.

On 2026-06-12, `tools/verify_governance_runtime_smoke.py` returned `ok: true`
on the 211 API for runtime subject commit
`2384e19dcac2e39fbcf9c27dc990f5774d391422` and image
`ai-platform:2384e19-context-source-provenance`, using operator verifier source
`820669037978237182ecd2fd27c2ffa10a953c0b`. The smoke verified ordinary-user
Admin Runtime denial, same-tenant admin access, governance schema
`ai-platform.governance-readiness.v1`, required tool/skill/memory governance
domains, tool policy taxonomy and bulk-review signals, skill release/dashboard
signals with `dashboard_contract` trimmed from the overview projection, memory
fail-closed/context-provenance/office-context signals, and no forbidden
projection terms in the reviewed summary. The synced 211 source snapshot marked
the new verifier as runtime-neutral and declared no runtime-affecting delta from
the running `2384e19` image. The reviewed release evidence entry is
`docs/release-evidence/foundation-alpha-poc/2384e19dcac2e39fbcf9c27dc990f5774d391422/2026-06-12-211-foundation-alpha-poc-2384e19-governance-runtime-smoke.json`.

This smoke records only the focused Admin Runtime governance projection proof for
the Foundation Alpha POC. It does not close ordinary-user confirmation-card UX,
full Admin dashboard/visual acceptance, signed-package/SBOM review evidence,
dependency vulnerability/license evidence, execution-tier router acceptance,
frontend context provenance acceptance, PR #44 office context runtime evidence,
or broader production governance rollout.

On 2026-06-08, the 211 source snapshot and API/worker runtime were updated to
commit `f579155f3ec0ac7e37dd7b525f8eab27f7fd2e35` with image
`ai-platform:f579155-g6-readiness`. The 211 frontend entry served the
new `SettingsPanel-BMSHWN-7.js` chunk, and that chunk contained the Admin
Runtime capacity/governance section plus the
`/api/ai/admin/runtime/overview` client route. A container-local Admin Runtime
smoke returned admin HTTP 200, ordinary-user HTTP 403, capacity schema
`ai-platform.capacity-baseline.v1`, governance schema
`ai-platform.governance-readiness.v1`, seven load-test gates, and no scanned
forbidden private projection terms. The frontend release traceability CLI now
records a deterministic static `dist/` manifest and build provenance gate:
`dist` is same-commit only when `dist/ai-platform-build-provenance.json`
matches a known current git commit, a clean dirty state, and package/lockfile
hashes; missing, stale, dirty, or unknown-dirty provenance is reported as
`built_unverified` instead of being silently tied to the current commit. The
CLI also reports packaged frontend image definition traceability through
`frontend/web/Dockerfile`, `frontend/web/nginx.conf.template`, and
`deploy/ai-platform/docker-compose.frontend.yml`, and fails closed if required
build provenance args, nginx upload/proxy controls, compose args, or packaged
delivery denylist checks regress. The repository also has
`.github/workflows/ai-platform-frontend.yml`, which runs frontend dependency
install, `ci:verify`, frontend release traceability, and a non-push packaged
image build/provenance check for relevant source changes. The image job builds
with `AI_PLATFORM_BUILD_COMMIT=${{ github.sha }}` and
`AI_PLATFORM_BUILD_DIRTY=false`, then reads
`ai-platform-build-provenance.json` from the image to verify same-commit
frontend provenance without Docker compose or `.env` inputs. GitHub Actions run
`27104398690` passed on commit
`11ab56c660385f6790964af3d5bd60e3d4431ff2` for the earlier source workflow.
A later 211 packaged-image build attempt reached the private repository source
but failed before application build because the Docker build host could not
pull required registry/base-image metadata and did not have the required base
images cached. Therefore packaged frontend image release acceptance remains a
separate Docker-capable-host release gate and is not closed by this baseline.

`tools/skill_release_readiness.py` now records a secret-safe offline evidence
snapshot for repository-owned Skills with schema
`ai-platform.skill-release-readiness.v1`. The snapshot reads only the
repository `skills/` inventory, Skill front matter, sanitized evidence file
basenames, content hashes, and the platform dependency policy. It does not output
Skill body content, absolute paths, `.env` values, staging paths, callback
tokens, sandbox workspace roots, or executor-private data. The current source
inventory contains seven Skills, five public workbench Skills, two internal
dependency Skills, and two declared dependency edges:
`qa-file-reviewer -> minimax-docx` and
`ctd-32s73-stability-template-fill -> reference-fact-extraction`. Those
dependencies are allowed by policy. The same snapshot records source-bound
pending SBOM, license, and vulnerability scaffold evidence for all seven Skills,
but review manifests remain pending and do not close G6. A missing or empty Skill inventory fails closed as
`skill_inventory_missing_or_empty`; blocked dependency-policy details fail
closed as `skill_dependency_policy_blocked`; and SBOM/license/vulnerability
filenames do not clear release governance unless an explicit review manifest
marks the evidence as passed and its `evidence_files` entries are non-empty,
non-placeholder, secret-safe, and matched to actual Skill evidence files.
The CLI can also write external pending review inputs under the repository
release-evidence tree with
`python tools/skill_release_readiness.py --write-evidence-scaffold --skill-id <skill-id> --evidence-root docs/release-evidence/skill-release --format json`.
That scaffold uses schema `ai-platform.skill-release-evidence-scaffold.v1`,
binds SBOM, license-policy, vulnerability, and pending review-manifest
references to `external-release-evidence/<skill-id>/...`, and keeps
`status = pending_review`; it is an operator handoff artifact and does not
change the Skill content hash or close G6 by itself.
If an operator prematurely changes the review manifest to `passed` or sets
review flags to true while the referenced SBOM/license/vulnerability files still
carry scaffold markers such as `pending_review` or `review_required`, readiness
fails closed with category-specific evidence-not-reviewed errors.
The same snapshot now embeds the source-level
`ai-platform.skill-dependency-review-policy.v1` contract. That contract binds
the required review manifest schema to `ai-platform.skill-release-review.v1`,
requires the `sbom_reviewed`, `license_policy_reviewed`, and
`vulnerability_reviewed` flags, requires SBOM/signed-package, license-policy,
and vulnerability-scan evidence categories, and keeps evidence references
matched to the Skill inventory while rejecting placeholder or secret-like
references. It also embeds the source-level
`ai-platform.skill-signed-package-evidence-contract.v1` as
`skill_signed_package_evidence_contract`. The contract defines required signed
package evidence fields such as package artifact reference, package digest,
signature artifact reference, signer identity, signing certificate or key
reference, transparency log or attestation reference, verification status, and
review status, while keeping evidence references bounded to relative or
artifact references. Source-level runtime validation now accepts only safe
signed-package wrapper JSON (`ai-platform-signed-package-evidence.json` or
`signed-package-evidence.json`) with the required fields, a 64-character
SHA-256 digest, final verified/reviewed statuses, and relative or `artifact://`
references. Raw cosign, in-toto, SLSA, or signature files are not accepted as
direct review evidence; the wrapper JSON must reference those artifacts through
its bounded attestation/signature fields.
This still does not close G6 without real reviewed evidence and runtime/Admin
acceptance.
`tools/skill_release_dashboard_readiness.py` now records a contract-only Admin
Skill release dashboard baseline with schema
`ai-platform.skill-release-dashboard-readiness.v1` and nested
`ai-platform.skill-release-dashboard-contract.v1`. Governance readiness records
this as `admin_skill_release_dashboard_contract`: the contract binds the
dashboard to existing admin-only Skill inventory, sync, upload, diff, promote,
and rollback routes; requires inventory summary, dependency policy, release
review evidence summary, version diff summary, promote/rollback policy, runtime
materialization status, filters, diff preview, confirmation controls, and review
evidence drilldown; and does not expose raw package internals, staging paths,
secret material, private runtime payloads, or sandbox working directories.
This replaces the previous coarse dashboard acceptance blocker with explicit
runtime, visual, and 211 acceptance gaps.
Therefore G6 remains blocked by
`signed_skill_package_or_sbom_release_gate` and
`dependency_vulnerability_or_license_policy`, plus real signed-package reviewed
evidence, `skill_dependency_review_policy_runtime_acceptance`,
`admin_skill_release_dashboard_runtime_acceptance`,
`admin_skill_release_dashboard_visual_acceptance`, and
`admin_skill_release_dashboard_211_acceptance` until the source-level policy and
dashboard are accepted through runtime/Admin release evidence. Ordinary users
must still not be exposed to raw Skill selection or staging internals.

The Skill dependency-review runtime acceptance path is now explicit but still
open until reviewed 211 evidence exists. `tools/skill_release_readiness.py`
publishes the
`ai-platform.skill-dependency-review-runtime-acceptance.v1` contract, and
`tools/verify_governance_runtime_smoke.py` emits the nested
`skill_dependency_review_policy_runtime_acceptance` runtime payload while
keeping the top-level verifier schema
`ai-platform.governance-runtime-smoke.v1` for existing POC evidence consumers.
Operators must wrap that output with
`tools/wrap_foundation_alpha_evidence.py --gate "G6 Skill Release / Dependency Governance"`
and store the reviewed, redacted entry under
`docs/release-evidence/skill-release-runtime/<runtime-subject>/`. Readiness
accepts only reviewed entries with artifact kind
`skill_dependency_review_policy_runtime_acceptance`, verifier
`tools/verify_governance_runtime_smoke.py`, passed redaction scan, required
verifier checks, required Admin Runtime projection checks, and non-expansion
invariants that keep ordinary-user multi-agent, long-term cross-session memory,
production concurrency defaults, and Docker sandbox hardening closed. Closing
this runtime gap does not close G6, signed package/SBOM review, dependency
vulnerability/license review, or Admin Skill release dashboard acceptance.

The same CLI can now generate a pending review-manifest template:
`python tools/skill_release_readiness.py --review-template --skill-id <skill-id>
--format json`. By default this writes only to stdout; operators must pass
`--output skills/<skill-id>/ai-platform-skill-release-review.json` to create a
file. The template schema is `ai-platform.skill-release-review.v1`, starts with
`status = pending`, keeps `sbom_reviewed`, `license_policy_reviewed`, and
`vulnerability_reviewed` false, and includes required evidence/checklist fields.
This entrypoint does not close the release gate by itself. A Skill remains
blocked until real SBOM evidence, license-policy evidence,
vulnerability-scan evidence, and a completed `passed` review manifest are
present, reviewed, and explicitly bound to those real evidence files. Empty
review evidence arrays, copied template placeholders, secret-like evidence
paths, or references that do not match the Skill evidence inventory keep the
readiness verdict fail-closed.
Signed-package evidence now has a source-level contract and validation in code
and tests, but it remains fail-closed until real package evidence and passed
review manifests are available and accepted through the runtime/Admin release
evidence path.

`tools/memory_erasure_readiness.py` now records code/test evidence for
ordinary-user session-scoped soft delete, admin same-tenant soft delete, admin
retention cleanup, worker retention cleanup across scopes, ordinary-user export
excluding deleted/expired rows, admin export using an operator projection
without content/metadata, no content/metadata returning in delete repository
tests, delete/cleanup audit allowlists, the admin-only
`POST /api/ai/admin/memory/redaction/preview` route, and the source-level
context snapshot public provenance projection contract. The preview route validates
same-tenant admin scope, returns only redacted preview fields, and writes an
audit payload that records policy scope, mode, change booleans, and redacted
reason without sample content or metadata. This does not close memory
governance by itself: office workflow context continuity now has source-level
context-pack persistence/versioning, user-visible context provenance API
projection source tests, frontend run-playback context provenance projection
source tests, source-level execution-tier routing tests, document-centric
follow-up state source tests, reviewed `8e0389e` 211 executor context-pack
evidence, the sandbox latency split observability source contract, and reviewed
PR #44 211 sandbox cold-start latency split evidence. The old PR #44 executor
context-pack 211 evidence is retained as superseded history because the
historical live run had no source artifact count and predates the public
input-key leakage guard.

`tools/b1_memory_context_readiness.py` now records the B1 backend
memory/context rollup with schema `ai-platform.b1-memory-context-readiness.v1`.
It aggregates the current memory-erasure readiness and office context-pack
readiness into the backend stage `B1 memory/context usable`, reports status
`runtime_acceptance_recorded`, and keeps the B1 stage status label `local
partial`. Governance readiness embeds this rollup under
`domains.memory_governance.evidence.b1_memory_context_readiness`, keeps
`211_memory_enabled_document_workflow_smoke` out of G6 open gaps after the
reviewed 211 smoke is recorded, and consumes repo-local #75 closure evidence from
`docs/release-evidence/backend-stage-closures/b1-memory-context/2026-06-18-issue75-b1-closure.json`
to close only `b1_issue_review_and_closure_evidence`. The current merged-source
runtime evidence review boundary is computed dynamically by
`tools/b1_memory_context_readiness.py`; the 2026-06-19 #114 refresh records
reviewed B1 smoke evidence for runtime subject
`75ab69b939d0bf13987ac044ce0dc498f5eab999`, and the readiness classifier
reports no runtime-affecting delta from that subject to current source
`3874fdfbd5331a2974d411450323f98f2e228bfc`. That closes only
`b1_runtime_evidence_review_against_merged_source` for the current
runtime-relevant source. If later runtime-affecting source changes land after
the recorded `75ab69b` B1 smoke, the readiness output must reopen that boundary
until fresh B1 runtime evidence is recorded. The memory export boundary
`b1_memory_export_boundary` is recorded as a closed local contract through
memory-erasure readiness controls, including
`ordinary_user_export_excludes_deleted_and_expired_records`,
`ordinary_user_export_requires_session_scope_and_enabled_policy`, and
`admin_export_operator_projection_without_content_or_metadata`. The rollback
boundary `b1_rollback_boundary` is recorded as a local operator contract for
disabling selected workflow memory policy, disabling context-pack injection,
pausing retention cleanup when needed, running a reduced B1 deny-path smoke, and
recording the source/runtime subject plus residual caveats before restoring
runtime/config state. The runtime smoke layer
can report `211 verified` for the selected memory-enabled document workflow,
but the B1 stage itself remains `local partial` and is not `gate closable`.
The repo-local #75 closure evidence records issue-level closure for the selected
governed document workflow slice only. The #114 smoke reused the running
`ai-platform:75ab69b-issue112-runtime-only-v1` runtime and records the caveat
that the 211 service checkout remains dirty/behind and the container internal
source marker still reports older `dde1749` metadata; therefore the B1 evidence
does not close G0 source authority, production readiness, Docker sandbox
hardening, ordinary-user multi-agent exposure, long-term cross-session memory by
default, department rollout, or production concurrency default increases.
`tools/verify_b1_memory_context_workflow.py` is the reusable verifier entrypoint
for that smoke and emits schema
`ai-platform.b1-memory-context-workflow-smoke.v1`; passing it records workflow
runtime evidence only and does not by itself close B1 because issue review and
merged-source runtime evidence review still have to be recorded.

`tools/office_context_readiness.py` now records the source-level #22
context-pack readiness contract for office-heavy workflows with schema
`ai-platform.office-context-pack-readiness.v1`. The contract keeps lightweight
writing/rewrite/summarization/translation follow-up work in the
`sdk_only_writing` tier by default, models document generation/conversion as a
separate `document_worker` tier, and reserves `heavy_sandbox` for script,
browser, risky tool, or complex multi-tool work. It defines allowed context
sources, user-visible context provenance fields, and forbidden projection terms
without reading `.env` values, raw storage keys, sandbox workdirs,
executor-private payloads, or secret-like runtime paths. The current
`run_context_snapshots.payload_json` and queued `context_snapshot` references now
carry source-level public provenance fields for `referenced_materials`,
`used_context_summary`, `latest_artifact_version`, `execution_tier`, and
`context_pack_version` / `context_pack_generated_at`; those fields contain
counts, safe input keys, memory policy source/read flags, tier, an optional
manifest-supplied bounded public artifact version, a bounded public context-pack
version, and generated time rather than raw message/file/artifact/memory IDs,
and are covered by source-level API projection tests. The
owner-scoped context snapshot API response no longer returns `included_*_ids`;
those IDs stay in the scoped database row and worker lookup path only to compute
public counts. Worker-side executor `context_ref` reconstruction resolves
the scoped DB snapshot by id and regenerates public provenance/counts instead of
trusting queue copies or stored payload provenance. The source tree now also has
a bounded `ai-platform.executor-context-pack.v1` executor prompt contract and
Claude SDK prompt-injection tests that pass only the generated safe summary, not
raw storage keys, sandbox workdirs, executor-private payloads, or long-term
memory reads. The implemented control
`source_level_context_pack_persistence_and_versioning`: it preserves the safe
public `context_pack_version` fact in source-level snapshot/projection paths
without adding a new database schema. Source-level tests now route lightweight
writing to `sdk_only_writing`, document generation/review/translation skills to
`document_worker`, and explicit sandbox/script/browser work to `heavy_sandbox`
without starting Docker during routing. Frontend run playback now has
source-level context provenance projection tests, but full S2 frontend/runtime
acceptance still depends on packaged frontend image evidence. Reviewed `8e0389e`
release evidence records executor context-pack 211 acceptance for #22, and
reviewed PR #44 release evidence records 211 sandbox latency split acceptance
for #22. The executor context-pack evidence proves positive source-run artifact
scope and public input keys free of `copied_from_run_id`, `source_run_id`,
`parent_run_id`, and `run_id`. This still does not enable long-term
cross-session memory or start Docker for lightweight office tasks. It does not
expand ordinary-user G8/G10 exposure.
The source-level sandbox latency split observability contract separates lease
acquisition, container cold start, healthcheck, executor dispatch/model work,
document processing, cleanup, and total runtime timings. Reviewed PR #44
evidence records the 211 sandbox latency split for the named #22 cold-start
evidence gap, while production Docker sandbox hardening remains open. The 211
sandbox runtime verifier now also requires sandbox hardening
evidence for lease/workspace isolation, cleanup, resource timeout fallback,
failure fallback, and cached lease scope revalidation before accepting the
runtime smoke payload.

The same source snapshot exposes `sandbox_runtime_smoke_contract` for
`211_sandbox_latency_split_smoke`. Operators must generate evidence with
`scripts/generate_sandbox_runtime_evidence_211.py`, verify it with
`scripts/verify_sandbox_runtime_211.py`, use `sudo -n docker` on 211, and prefer
the already-local cancel probe image `ai-platform:local`. The evidence must
include `non_expansion_invariants` with
`ordinary_user_high_risk_sandbox_allowed=false` and
`ordinary_user_multi_agent_allowed=false`. Reviewed PR #44 evidence now records
`sandbox_cold_start_latency_split_211_acceptance` for the controlled verifier
run; this still does not close Docker sandbox production hardening, G6, or G9.
`tools/b2_sandbox_readiness.py` now records the B2 backend real-sandbox rollup
with schema `ai-platform.b2-sandbox-readiness.v1`. It reports
`status=runtime_acceptance_recorded`, keeps the stage status label `local
partial`, records the reviewed 211 Docker/equivalent smoke evidence, and
consumes repo-local #89 closure evidence from
`docs/release-evidence/backend-stage-closures/b2-sandbox/2026-06-18-issue89-b2-closure.json`
to close only `b2_issue_review_and_closure_evidence`. This rollup proves the
issue-scoped governed SDK skill execution sandbox-smoke loop; it does not close
the broader B2/G7 production hardening gate, does not treat
`SANDBOX_CONTAINER_PROVIDER=fake` as production proof, does not permit user
payload provider selection, and does not allow unrestricted Docker socket
exposure as a default trust boundary. A generated 211 smoke that has not been
wrapped, redacted, reviewed, and attached as release evidence still stays `local
partial`; `211 verified` begins only after the reviewed evidence gate is
recorded. The B2 rollup reports the current verifier/generator contract for
`admin_or_allowlist_only=true`, `hardening.evidence_class`, generated timings,
hardening sections, callback/cancel evidence, and non-expansion invariants.
Resource-limit policy evidence, egress-policy evidence, and security-option
evidence remain PRD B2/G7 requirements that are not yet verifier-checked and
must not be treated as current verifier output. The rollback-assumption evidence
is now a source/operator contract with status
`recorded_source_operator_contract`: it names operator rollback steps,
preconditions, failure conditions, and required after-rollback evidence while
keeping resource-limit, egress, and security-option hardening open. Rollback
assumptions are not Docker sandbox production hardening, not ordinary-user
high-risk sandbox exposure, and not B2/G7 gate closure; the contract closes only
`rollback_assumptions_evidence`.
The #120 source policy contract names the required controls and runtime evidence
for resource limits, egress policy, and security options. It records source
contracts only: `resource_limits_policy_evidence`, `egress_policy_evidence`, and
`security_options_evidence` stay broader B2/G7 hardening inputs until reviewed
runtime evidence closes `resource_limits_runtime_hardening_evidence`,
`egress_runtime_hardening_evidence`, and
`security_options_runtime_hardening_evidence` (plain names:
resource_limits_runtime_hardening_evidence, egress_runtime_hardening_evidence,
and security_options_runtime_hardening_evidence). This does not claim Docker
sandbox production hardening, does not expose high-risk sandbox behavior to
ordinary users, does not enable ordinary-user multi-agent exposure, and does not
raise production concurrency defaults.

The
hardening section must label lease/workspace/cleanup checks as
`live_platform_probe` and timeout/failure/cached-lease checks as
`source_regression_guard`, so source-regression coverage is not confused with
live runtime proof.

Office context-pack 211 acceptance is now represented by
`executor_context_pack_runtime_acceptance_contract` with schema
`ai-platform.executor-context-pack-runtime-acceptance.v1`. The default
generator mode produces source-probe evidence with
`source_probe_evidence_strength=source_probe_on_target_runtime`; this is a
binding check, not accepted 211 closure evidence. Accepted 211 evidence must
carry `required_live_evidence_strength=live_worker_run_payload` from a real
worker-run payload. The closure path must generate evidence with
`scripts/generate_executor_context_pack_evidence_211.py --live-run-id <run_id>`
and verify it with `scripts/verify_executor_context_pack_211.py --run-id
<run_id> --require-live-run-payload`. It ties acceptance to
`scripts/generate_executor_context_pack_evidence_211.py`,
`scripts/verify_executor_context_pack_211.py`,
`app.repositories.get_context_snapshot_for_worker`,
`app.context_builder.executor_context_pack_from_snapshot`,
`app.executors.claude_agent_sdk_runner._context_pack_prompt_section`, and the
worker prompt-injection path. Accepted runtime evidence must include
`live_worker_run_payload`, `run_row_loaded`, `context_snapshot_id_present`,
`scoped_context_snapshot_loaded`,
`worker_context_ref_rebuilt_from_db_snapshot`,
`prompt_includes_bounded_summary`, `prompt_includes_context_pack_version`,
`prompt_includes_context_pack_generated_at`, `raw_storage_identifiers_absent`,
`sandbox_runtime_paths_absent`, `executor_private_content_absent`,
`long_term_memory_read_false`, and
`source_run_artifact_scope_tenant_workspace_user_session`, and
`source_run_artifact_count_positive`, with fresh `generated_at` evidence and
explicit `source_functions` binding. Live evidence must also show public
context `input_keys` without `copied_from_run_id`, `source_run_id`,
`parent_run_id`, or `run_id`. Live evidence must carry those per-item booleans
under the verifier-checked `runtime_evidence` JSON section. Source-probe
evidence carries `does_not_close_211_acceptance=true` and
`runtime_acceptance_requires_real_run_payload=true`; the superseded PR #44 live
evidence carried `runtime_run_payload_verified=true` but does not satisfy the
current positive source-artifact and public input-key checks for the named #22
runtime gap. The reviewed `8e0389e` live evidence for
`run_a618c52ee5c148a185254b68e1c81b9e` satisfies the verifier with
`live_worker_run_payload`, `artifact_count=2`, `file_count=1`, public
`input_keys=["attachments","message"]`, positive source-run artifact scope, and
no secret leakage.
`non_expansion_invariants` keep `ordinary_user_multi_agent_allowed=false`,
`ordinary_user_high_risk_sandbox_allowed=false`,
`lightweight_office_tasks_start_sandbox_by_default=false`,
`long_term_cross_session_memory_enabled=false`, and ordinary users limited to
public projections. `executor_context_pack_211_acceptance` is closed only for
the named #22 runtime gap and does not close G6 or G9.

Frontend projection readiness now also exposes
`packaged_runtime_smoke_contract` from
`tools/frontend_packaged_runtime_smoke.py` with schema
`ai-platform.frontend-packaged-runtime-smoke.v1`. With no accepted runtime
evidence it remains `blocked_missing_runtime_evidence` with
`frontend_packaged_runtime_smoke_evidence_missing`, runtime policy
`docker_capable_host_only_no_local_windows_docker`, and the remaining
`frontend_packaged_image_delivery_and_release_acceptance` gap. This does not
close G6/G9, #21 capacity, or packaged frontend release acceptance by itself.

On 2026-06-08, commit `f7c6b0d9114748fa249acb88da6584851c48aa96` was synced to
the 211 repo-local source target and deployed to API/worker with image
`ai-platform:f7c6b0d-g6-memory-redaction-preview`. API and worker labels both
reported the same `org.opencontainers.image.revision`, and
`GET /api/ai/health` returned `{"status":"ok"}`. A 211 route smoke for
`POST /api/ai/admin/memory/redaction/preview` verified admin HTTP 200,
ordinary-user HTTP 403, invalid redaction mode HTTP 422, audit action
`admin.memory.redaction.previewed`, and no response leakage of private payload,
storage key, sandbox workdir, object-storage URL, skill package key, provider
token, bearer token, or client-secret markers. The smoke used the existing 211
runtime `.env` path through compose without printing or copying secret values.
Local source readiness for the same commit reports no missing memory erasure
evidence markers. The former bounded context-pack product-contract gap is now
split into source-level context-pack persistence/versioning plus explicit 211
executor and 211 sandbox latency acceptance gaps. Document-centric follow-up
state is now source-tested for copy, retry, and resume context snapshots, and
cached Docker sandbox lease reuse is source-tested to fail closed on
tenant/workspace/user/session label drift, but both still need runtime evidence
before #22 closure.

## Gate Rule

Do not close G6 until the remaining gaps above have code, focused tests,
documentation, review, and 211 smoke evidence where runtime behavior is
involved. Do not use this baseline to expand sandbox privilege, expose ordinary
users to raw Skill selection, or broaden multi-agent beta.
