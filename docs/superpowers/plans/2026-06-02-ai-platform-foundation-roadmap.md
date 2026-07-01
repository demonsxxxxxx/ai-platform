# ai-platform Foundation Roadmap

日期：2026-06-02

## 文档权威

本路线图只服务于 active product PRD
`docs/superpowers/specs/2026-06-10-ai-platform-product-prd-v2.md`，并参考
`docs/superpowers/specs/2026-06-11-ai-platform-tech-acceptance.md` 中的模块验收矩阵。

后续 ai-platform 计划只允许引用当前 PRD、当前路线图、仓库 guardrails（`docs/agent-rules/ai-platform-guardrails.md`）、真实代码和当次 211 运行证据。任何非当前主链路重新进入范围，必须先更新 PRD、本路线图和 guardrails。

本路线图不再追加非当前主链路、短期执行证据或临时服务说明。下方
legacy accumulated evidence 段落仅保留历史上下文；当前状态以 PRD v2、
technical acceptance、gate status、GitHub issue/PR 和最新 211 evidence 为准。

本路线图不维护已退出范围对象的名称清单。后续执行只按本文“当前主链路”和 P0 交付门槛推进；未列入当前主链路的入口、服务、端口、页面或候选方案，不作为计划依据。

## 当前阶段标记

S1 / Foundation Alpha 已作为 `380de6b` runtime subject 的 historical controlled
baseline 接受。闭合记录保存在
`docs/operations/ai-platform-foundation-alpha-closure.md`，当前路线图不再把该
历史 baseline 当作待完成阶段反复前冲。

当前 main/source 是否仍可宣称 current-source S1 complete，只看
`python tools\foundation_alpha_readiness.py --format json`。如果 readiness 报
`foundation_alpha_stage_complete=false`、
`foundation_alpha_stage_status=runtime_rollout_required`，或 blocker 包含
`foundation_runtime_concurrency_evidence`，则先进入 **S2-0 latest-main evidence
refresh**：更新 211 runtime，重跑 Foundation Runtime concurrency evidence 和
readiness，直到当前 source 不再被 runtime/concurrency evidence 阻塞。

S2+ 后续 gate 包括：治理/运维、真实 SDK skill 路径下的 sandbox hardening、
Skill 依赖和发布证据、Admin Runtime acceptance、capacity-upgrade recorded
evidence，以及受控的 SDK subagent fanout / G10 long-task 工作流证据。G8
平台级 multi-run orchestration 仍是 deferred parking-lot，不是当前
ordinary-user 产品路线。

S1 的边界仍然不变：不提高生产并发默认值，不开放 ordinary-user platform-level multi-run orchestration，
不宣称 Docker sandbox hardening，不开放部门 rollout，不默认启用长期跨会话
memory，不关闭 packaged frontend release，也不关闭 signed Skill/SBOM/license/
vulnerability evidence。

### 当前路线进展读法

- 状态边界：G8/B3 文档和 readiness 语义应固定为下列读法。旧 G8 普通用户平台级
  multi-run follow-up 不再作为 Foundation Alpha 顶层 `open_followups`；
  ordinary-user 平台级 multi-run orchestration 只保留在 blocked expansion /
  non-expansion invariant 中。机器可读 blocked expansion 名称是
  `ordinary_user_platform_multi_run_orchestration_exposure`；当前权威文档不再
  使用旧的泛化 multi-agent exposure 命名。
- B3 边界：`b3_10x4_sdk_subagents` source contract 是 SDK subagent
  fanout 容量证据，不是 G8 普通用户平台级 multi-run 产品曝光证据。
- 已部分推进但未完成：PR #296 已合入 GitHub `main`
  `ae6b7e52c656fd8296cf039834ce8d8559b01228`，它包含 PR #294 的 G7
  verifier-helper callback 默认修复，以及 PR #296 的文档状态清理；但 211
  API/worker 仍未部署到该 current-main runtime subject。最新只读 poll 显示
  211 source marker 已是
  `ae6b7e52c656fd8296cf039834ce8d8559b01228`，source snapshot 记录
  `source_tree_commit_sha=ae6b7e52c656fd8296cf039834ce8d8559b01228`、
  `runtime_subject_commit_sha=ae6b7e52c656fd8296cf039834ce8d8559b01228`、
  `source_tree_dirty=false`、`snapshot_source=codex_origin_main_archive_sync`；
  211 API 直连 `http://127.0.0.1:18000/api/ai/health` refused，前端代理
  `http://127.0.0.1:18001/api/ai/health` 返回 ok，但 API/worker runtime
  identity 已出现漂移：`ai-platform-api` 运行
  `ai-platform:df85a9f-issue183-contracts-runtime-only-v1`，source/runtime/OCI
  labels 指向 `df85a9fb3266aab92a2ca4122db06d4ec7a00175`；`ai-platform-worker`
  运行 `ai-platform:bd690f7-g7-b3-audit-runtime-only-v1`，source/runtime/OCI
  labels 指向 `bd690f72723080beeb820d07679da59d84c7913e`。所以这是
  current-main source sync，但不是 current-main API/worker runtime parity，不是
  G7 reviewed release-evidence，也不是 B3 load evidence。此前
  `g7-runtime-probe-20260701203418`
  命名 runtime-only formal verifier 在 211 通过，覆盖 platform/docker、
  callback stream、cancel stops container、resource-limit timeout cleanup、
  egress default-deny、non-privileged security options 和 8 个 verifier checks；
  但它仍只是 `d318f9f` named runtime-subject evidence，不是 reviewed local
  release-evidence entry，也不是 `ae6b7e5` current-main G7 / Foundation Runtime
  concurrency evidence。此前 `bd690f7` image 的 G7 探针已走到 Docker/resource-limit
  evidence，但 no-masq egress network 阻断 callback exception path，导致
  required callback evidence 缺失，所以只能作为 blocker diagnostic，不是
  reviewed G7 release-evidence。PR #294 已把 `codex/g8-b3-status-refresh`
  source/test 变更合入 `main`，merge commit 为
  `513cc5e2280c35218e7edf297b7f02494e82a164`；该变更把 211 sandbox evidence generator 的 Docker platform 默认
  callback path 修为 `0.0.0.0` bind +
  `http://host.docker.internal:{port}/callback` public URL，避免 no-masq
  host-gateway exception 只靠人工传参；该修复已随 PR #296 留在当前 GitHub
  `main`，但这仍只是 `merged` source progress，必须部署并在 211 重新跑
  formal verifier 后，才可能成为 reviewed G7 evidence。
- 仍未完成：reconcile 211 source/API/worker runtime identity、current-main G7 Docker sandbox hardening closure、B3
  operator-reviewed recorded load evidence、G9 Operations Beta acceptance、G10
  workflow-owner rollout，以及任何 ordinary-user 平台级 multi-run orchestration
  暴露。B3 最新 bounded `api_read_write_burst` probe 只是
  `probe_completed_not_gate_evidence`，evidence bundle 仍是
  `blocked_incomplete_load_test_evidence`，七个 recorded load-test gates 仍缺失。

因此当前下一步不是重开 G8，也不是把 B3 当作普通用户平台级 multi-run 产品曝光；
下一步是先用 `tools/g7_b3_completion_audit.py` 把 sanitized runtime
observation 和可选 capacity profile readiness 汇总成 fail-closed 阻塞清单，先
reconcile 211 source/API/worker runtime identity，把 API 和 worker runtime
images 对齐到包含 PR #294 G7 verifier-helper callback 默认修复的 selected
current-main runtime subject，并基于后续选定的新 runtime subject 重跑 reviewed
G7 sandbox evidence、smoke / Foundation Runtime concurrency evidence，并把
G7/B3 的证据边界继续保持为
`runtime pending` / `local partial`，直到真实运行证据闭合。该 audit 只是
控制/计划工件，不是 G7 runtime evidence 或 B3 load evidence。

## 2026-06-06 Gate-Based Roadmap Sync

本节按 GitHub issues #15/#16/#17 同步路线图职责：路线图只作为 product gates、当前状态、阻塞项与下一决策的工具；211 image hash、smoke 输出、逐 PR 执行证据后续应进入独立 release evidence 或 slice execution plan。本文后面的历史段落先保留为 legacy accumulated evidence，不再把新的短期执行流水继续追加成产品需求。

当前 gate 顺序：

1. G0-G1 Source Authority / Security Baseline：本地 source、211 source、repo-local deploy composition、runtime labels、公司 AD/auth/session、RBAC、tenant/workspace/user 边界、redaction 与 CI/verification 基线对齐。
2. G2-G4 Control Plane MVP：session、run、file、artifact、skill、tool、memory、event、audit 合同稳定；executor 只消费 platform payload，不定义平台 schema。
3. G5 Run Lifecycle / Worker Runtime V1：queue、lease、heartbeat、retry、dead-letter、cancel、resume、checkpoint、idempotency 可审计、可运营。
4. G6 Tool / Skill / Memory Governance：skill versioning、release policy、dependency policy、tool allow/deny/ask、memory retention、redaction、delete flow 可运营。
5. G7 Sandbox / Resource Hardening：Docker provider 生产验证、network/egress policy、runtime quota、orphan cleanup job、container security options 与 211 smoke 通过后，才允许扩大高风险 sandbox/tool。
6. G8 Platform Multi-Run Controlled Beta：旧标题曾写作 G8 Multi-Agent Controlled Beta，但当前权威读法是平台 parent/child ledger、dispatcher、handoff、child reconciliation、parent rollup、parent cancel 与 worker dispatcher 只作为历史受控切片和 deferred parking-lot 保留；执行层 agent/subagent 能力走 Claude Agent SDK，并作为一个 governed platform run 管理。当前开放问题是 SDK subagent fanout 的 capacity/governance/sandbox/model-gateway evidence，不是普通用户平台级 multi-run 产品曝光。
7. G9 Observability / Quality / Ops：Admin runtime、cost/token/latency metrics、error taxonomy、golden-set eval、trace/audit export 与 alert 进入 beta 前 gate。
8. G10 Internal Beta / Department Rollout：选择 1-2 个真实内网流程（如文档审查、翻译、SOP/RAG、长任务报告）明确运营 owner 后再放量。

当前优先级不是 Docker compose 一键启动或 package delivery，而是公司内网定制化后端 Agent 平台的可运营基础：AD/company auth 与 session、tenant/workspace/user 隔离、多租户高并发、DB connection pool、tenant-aware queue/quota、bounded queue metadata、tenant-aware worker maintenance、Admin Runtime/Observability、Memory/Context、Tool Permission、Agent Frontend 用户闭环。前端源码进入本仓库、backend/worker/frontend 同 commit versioning、monorepo 与多镜像交付进入路线图；compose 一键启动与 packaged delivery 只作为后续 milestone。

P2 Long Task / Multi-Agent Runtime 不再继续默认前冲。checkpoint、subagent、artifact tree、provenance、resume/cancel/retry 与多 agent 调度只能在前置 auth/session、tenant isolation、fair scheduling、quota/backpressure、observability 和 sandbox/tool risk gates 验收后继续扩大。执行层路线是 Claude Agent SDK；DeerFlow 只能吸收编排、拆解、上下文和报告模式，不能作为运行时、scheduler 或 control plane。

### Claude Agent SDK / DeerFlow Boundary And Long Task Product Contract

Issue #23 records the PRD boundary before deeper long-task, office-document,
artifact, and multi-agent UX work starts. Claude Agent SDK is the execution
kernel for skills, tools, artifacts, token/cost accounting, and agent/subagent
execution. Claude Code / Codex CLI remains a reference for workspace,
approval, event, skill, and bounded-context mechanics. DeerFlow is not a
second runtime to clone; it is absorbed only as a platform-level long-horizon
product contract and orchestration-pattern reference.

The later `Long Task Product Contract / Office Artifact Flow` gate must be
defined and reviewed before ordinary-user long-task rollout or any future G8
platform-level multi-run orchestration reopening. That gate covers:

- parent / child run decomposition and state ledger
- Claude Agent SDK agent/subagent tool enablement, progress stream, permission
  policy, and concurrency limits
- artifact ledger, preview, download, versioning, and reuse
- context pack, long-task context compression, resume, and replay
- cancel / retry / timeout semantics owned by the platform

This boundary prevents two drift paths: copying DeerFlow as a second control
plane/runtime, and treating Claude SDK executor-private logs, SDK-private
subagents, or artifact internals as sufficient platform facts. Platform RBAC,
tool policy, sandbox lease, artifact ACL, audit, redaction, replay, and
observability remain the source-of-truth contracts.

### G5 Tenant-Aware Concurrency Status

DB connection pool 是 issue #16 的第一个可独立闭环前置项：平台已在 `main` 建立 bounded async Postgres pool 替代每 transaction 直连，并把 allowlisted pool status 暴露到 admin-only runtime overview。2026-06-06 211 smoke 已验证 API/worker runtime label 与 source marker 匹配、API 与前端代理 health 正常、admin-only overview 返回 `database_pool.open=true` 且未暴露 DSN/password/secret/api key；后续 tenant-aware queue/quota 与 worker maintenance 可以在这个承载基础上推进。

当前未关闭的 G5 后续项仍包括：large queue bounded lookup 压力验证，以及多 tenant 并发压力测试。Tenant-aware queue lease、tenant-aware worker maintenance、active-run admission、bounded queue metadata、multi-agent child-run admission 与 P1 Admin Runtime admission/backpressure 已作为历史受控子切片通过 review、full pytest、main merge 和 211 smoke；它们不改变当前 PRD v2 路线：G8 平台级 multi-run orchestration 仍是 deferred parking-lot，当前容量问题是 SDK subagent fanout 的 B3 证据。

Foundation Runtime 10+ concurrent correctness now has a fail-closed evidence
gate named `foundation_runtime_concurrency_evidence` with schema
`ai-platform.foundation-runtime-concurrency.v1`. This gate is narrower than the
#21 production capacity gate: it proves the first-stage backend execution loop
under at least 2 tenants, multiple users/sessions, and 10+ concurrent run
creation, execution, cancel, and retry cases, while keeping production
concurrency increases blocked. Accepted evidence must include queue/admission
correctness, sandbox workspace/lease separation, artifact ACL denial across
users and tenants, exact tool-permission decision binding, pinned
`run_skill_snapshots`, replay safety, and memory/context isolation. The
memory/context portion must include per-run public context snapshot projections,
safe `context_pack_version` samples, and scope probes; legacy evidence that only
records `context_snapshot_count` is insufficient. Queue/admission claims require
real probe sample counts and provenance fields; sandbox claims require runtime
run-detail lease provenance instead of post-run lease probes; tool-permission
claims require negative decision-reuse probes for same-request, wrong-run,
same-tenant other-user, and cross-tenant reuse attempts; skill governance
claims require pinned snapshot binding samples. The 2026-06-14 `dff48fb` 211
rerun is retained as reviewed historical evidence, but current validation
blocks its concurrency entry because it lacks measured concurrency overlap,
`queue_probe_sample_count`, `runtime_run_detail` sandbox lease provenance, and
negative tool-permission reuse probes. The 2026-06-14 `5d3d7e2` PR #40 rerun
and the 2026-06-14 `79495bf` PR #40 refresh are retained as superseded reviewed
evidence after the merged-main refresh. The 2026-06-15 `380de6b` refresh is the
current accepted Foundation Alpha POC runtime evidence set and the current
accepted Foundation Runtime
concurrency evidence: it records coherent 211 source/runtime labels and
container markers, refreshed runtime POC smoke, Auth/RBAC smoke, governance
runtime smoke, release-evidence runtime acceptance, alert/trace export runtime
acceptance, and Foundation Runtime concurrency correctness. The concurrency
portion covers 12 concurrent cases, 2 tenants, 4 users, run
creation/execution/cancel/retry coverage, measured client timestamp overlap,
queue probe samples, runtime-run-detail sandbox lease provenance, public
context projections, pinned skill snapshot bindings, and negative
tool-permission reuse denial probes. This closes the Foundation Runtime
concurrency evidence gap for the `380de6b` runtime-relevant source and refreshes
the broader Foundation Alpha POC smoke/auth/governance evidence on the same
runtime subject. It does not broaden ordinary-user platform-level multi-run
orchestration exposure, does not claim Docker sandbox hardening, does not raise
production concurrency defaults, does not permit department rollout, and does
not close production readiness.

GitHub issue #20 已作为 G5 多租户高并发 gate 的收敛切片在 `main` commit `f5da825` 完成：在 configured fairness horizon 内关闭 tail-window quota leasing 造成的可运行租户饥饿、multi-agent child-run fanout 绕过 active-run admission、queue position / queued-run removal / admin enrichment 无界 Redis queued-list 扫描，以及高风险 review/model-reasoning 规则歧义。详细设计与执行计划分别在 `docs/superpowers/specs/2026-06-06-g5-tenant-aware-scheduling-admission-metadata-design.md` 与 `docs/superpowers/plans/2026-06-06-g5-tenant-aware-scheduling-admission-metadata.md`，211 smoke 与 issue closure 证据保留在对应执行计划和 GitHub issue，不继续追加为产品路线图流水账。

### G5 / G9 Capacity Baseline Status

Issue #21 is currently closed in GitHub, but the recorded capacity-upgrade evidence gate
still blocks any production concurrency default increase.
The first baseline step is now captured in
`docs/operations/ai-platform-capacity-baseline.md` and exposed through the
admin-only runtime overview as `capacity` with schema
`ai-platform.capacity-baseline.v1`. The projection records configured API,
worker, admission, DB pool, queue, sandbox, model gateway, and multi-agent
limits without printing DSNs, Redis URLs, raw queue keys, sandbox work
directories, storage keys, API keys, callback tokens, or executor private
payloads.

The repeatable #21 command manifest is generated by
`python tools/capacity_load_plan.py --format markdown --base-url <api-url>`.
It records an operator workflow, dry-run scenario commands, required evidence
fields, stop conditions, cleanup policy, and required Admin Runtime capture
sections (`capacity`, `database_pool`, `queue`, `admission`, `backpressure`,
`sandbox`, and `observability`) for the seven load-test gates without creating
runs, sending traffic bursts, mutating runtime state, or raising concurrency
defaults. The operator workflow requires start/end
`tools/capacity_runtime_evidence.py` captures, explicit operator approval for
the real load-harness step, cleanup proof, and a final
`tools/capacity_gate_readiness.py` verdict.
Each dry-run scenario now also carries a machine-readable
`recorded_gate_evidence_contract` with schema
`ai-platform.capacity-recorded-gate-evidence-contract.v1`, writing path
`load_test_evidence.gate_evidence.<gate>`, required recorded-evidence fields,
accepted cleanup/stop-condition statuses, and `does_not_raise_defaults = true`.
This contract reduces operator evidence drift. It does not raise production concurrency defaults or replace real load-test artifacts.

`python tools/capacity_evidence_snapshot.py --overview-json <admin-runtime-overview.json>
--commit-sha <deployed-commit>` now turns an operator-captured Admin Runtime
overview into a secret-safe capacity evidence snapshot. The snapshot binds
allowlisted live queue, admission, backpressure, DB-pool, sandbox, and
observability signals to a deployed commit while still marking load-test
evidence as missing until a real harness records the required gates.
`python tools/capacity_gate_readiness.py --snapshot-json <capacity-evidence-snapshot.json>`
then converts that snapshot into a fail-closed gate verdict, listing missing
Admin Runtime sections and missing recorded load-test gates without sending
load or changing defaults.
`python tools/capacity_profile_readiness.py --snapshot-json <capacity-evidence-snapshot.json>`
or `--readiness-json <capacity-gate-readiness.json>` now maps that fail-closed
verdict to the requested `conservative_internal`, `medium_team`, and
`high_capacity_1t` profile classes with schema
`ai-platform.capacity-profile-readiness.v1`. This profile catalog is an
operator-review aid only: missing or incomplete load-test evidence keeps every
profile at `do_not_raise_without_recorded_load_test_evidence`, and complete gate
evidence can advance only to `operator_review_required_before_default_change`,
not an automatic production default increase or safe concurrency-number claim.
`python tools/capacity_runtime_evidence.py --base-url <api-url> --user-id
<audit-user> --tenant-id <tenant> --roles admin --commit-sha <deployed-commit>
--runtime-profile <profile> --format json` now wraps the read-only Admin
Runtime capture, sanitized evidence snapshot, and gate verdict into one
operator command without printing the raw overview or secret values.
`python tools/capacity_bounded_load_harness.py --base-url <api-url> --gate
api_read_write_burst --requests <n> --concurrency <n> --format json` and
`python tools/capacity_bounded_load_harness.py --base-url <api-url> --gate
<any-seven-load-test-gate> --requests <n> --concurrency <n> --format json`
now add repository-owned bounded harness entrypoints with schema
`ai-platform.capacity-bounded-load-harness.v1`. The harness currently supports
all seven #21 load-test gates. It defaults to dry-run and only sends read-only
probe traffic when `--execute` is paired with
`--operator-acknowledgement send-bounded-load-without-default-raise`. The API
gate uses `/api/ai/health` plus
`/api/ai/admin/runtime/overview?include_maintenance_cleanup=false`; the other
six gates use only the same Admin Runtime overview with maintenance cleanup
disabled to observe admission, worker heartbeat, queue, retry/cancel pressure,
sandbox lease/container counts, DB-pool, backpressure, observability, and
model-gateway projection fields without creating runs, sending model calls,
reading gateway secrets, or triggering sandbox/container maintenance cleanup.
For every successful Admin Runtime overview response, the harness now requires
the baseline sections `capacity`, `database_pool`, `queue`, `admission`,
`backpressure`, `sandbox`, and `observability`; missing sections trigger
`admin_runtime_projection_sections_missing` and return only section names and
counts, not the raw projection body.
The model-gateway gate records only observed model-gateway projection field
paths, and missing required fields trigger
`model_gateway_projection_fields_missing` instead of a successful probe. The
taxonomy requirement is the `observability.error_categories` container itself,
so zero model-gateway errors do not fail the probe by themselves.
Its output is `probe_only_not_recorded`, `does_not_raise_defaults = true`, and
`does_not_mark_gate_recorded = true`; it is not accepted by `tools/capacity_gate_readiness.py` as recorded gate evidence.
Machine-readable doc check: the harness currently supports
`api_read_write_burst`, `run_creation_burst_by_tenant_and_user`,
`worker_processing_throughput`, `queue_depth_and_lease_latency`,
`cancel_retry_resume_under_load`, `sandbox_lease_creation_under_load`, and
`model_gateway_timeout_and_backpressure`.
`python tools/capacity_evidence_bundle.py --start-runtime-evidence-json
capacity-runtime-evidence-start.json --runtime-evidence-json
capacity-runtime-evidence-end.json --bounded-probe-json
capacity-bounded-load-harness-api-read-write-burst.json --cleanup-proof-json
capacity-cleanup-proof-api-read-write-burst.json --format markdown` now
assembles start/end runtime captures, the bounded probe, and cleanup proof into
a gap-first operator draft with schema
`ai-platform.capacity-evidence-bundle.v1`, target path
`load_test_evidence.gate_evidence.api_read_write_burst`, missing required
fields, a runtime window, cleanup proof status, and a readiness preview. The draft keeps
`recorded_gate_evidence_draft.status = draft_not_recorded`,
`probe_only_not_recorded`, and `does_not_mark_gate_recorded = true`; it is not
accepted by `tools/capacity_gate_readiness.py` as recorded gate evidence and
does not raise production concurrency defaults.
`tools/capacity_load_plan.py` includes that command as the
`assemble_evidence_bundle_draft` operator workflow step after cleanup proof is
recorded, so the machine-readable #21 plan now points operators from bounded
probe output to a draft missing-evidence bundle before the final fail-closed
gate verdict.
For release traceability, that generated command includes
`--start-runtime-evidence-json capacity-runtime-evidence-start.json` and
`--cleanup-proof-json capacity-cleanup-proof-api-read-write-burst.json` without
changing the draft-only, not-recorded gate status.
Cleanup proof is only accepted when the proof uses a safe relative
`evidence_ref` and explicitly verifies test-tenant, queue, sandbox lease,
temporary artifact, and generated document cleanup; raw/private storage paths
and executor-private payloads remain rejected from the draft bundle.
`python tools/capacity_recorded_gate_snapshot.py --runtime-evidence-json
capacity-runtime-evidence-end.json --recorded-gate-evidence-json
capacity-recorded-gate-evidence-api-read-write-burst.json --gate
api_read_write_burst --format json` now provides the next fail-closed operator
step, recorded in the generated workflow as `assemble_recorded_gate_snapshot`.
The input packet schema is
`ai-platform.capacity-recorded-gate-evidence.v1`; the output schema is
`ai-platform.capacity-recorded-gate-snapshot.v1`. The tool accepts only
operator-reviewed safe artifact refs or scalar measured values for every
required evidence field, requires `does_not_raise_defaults = true`, accepted
cleanup and stop-condition statuses, and no triggered stop conditions, then
returns a sanitized snapshot plus readiness preview. It does not convert the
`probe_only_not_recorded` bounded probe output into recorded evidence by
itself, does not raise production concurrency defaults, and still leaves
unrecorded gates missing until their own reviewed evidence packets are present.

The 2026-06-08 211 gate-readiness pass captured the admin-only runtime overview
from the deployed `f7c6b0d9114748fa249acb88da6584851c48aa96` image and ran the
latest local verifier against that snapshot. Required Admin Runtime sections
were present, but the verdict remained `blocked_missing_load_test_evidence`
with all seven load-test gates missing recorded evidence and
`production_default_decision =
do_not_raise_without_recorded_load_test_evidence`.
The verifier now also rejects superficial `recorded_gates` claims that do not
carry per-gate required evidence, cleanup proof, and stop-condition status. Such
snapshots return `blocked_incomplete_load_test_evidence` and remain fail-closed
for production defaults. The 2026-06-08 follow-up also rejects template or
placeholder evidence values such as `<commit_sha>`, `TODO`, `TBD`,
`placeholder`, `fill-me`, or `replace-me`, so copied load-test templates cannot
move #21 to operator review without real measured values or artifact
references.

This baseline does not claim a safe maximum concurrency number. Before raising
`MAX_ACTIVE_WORKER_RUNS`, `MAX_ACTIVE_RUNS_PER_USER`, DB pool size,
tenant/user queue quotas, sandbox container limits, model-gateway concurrency,
or SDK subagent fanout capacity inside governed platform runs, the target deployment profile still needs
recorded load-test evidence for API burst, run creation burst, worker
throughput, queue depth/lease latency, cancel/retry/resume, sandbox lease/cold
start, model-gateway timeout/backpressure, and cleanup. Until then, G5 stays
basic-operational but capacity-unproven, and G9 Observability/Ops remains a
pre-beta blocker.

Current source also exposes a machine-readable G9 readiness baseline through
`python tools/observability_readiness.py --format json` and the admin-only
runtime overview field `observability_readiness` with schema
`ai-platform.observability-readiness.v1`. This source-level projection records
implemented runtime-metric, error-taxonomy, quality-evaluation, and
alert/export baselines plus open gaps. Runtime metrics now include the
source-level Admin Runtime latency percentiles p50/p95/p99 projection
`latency_percentiles_p50_p95_p99_admin_projection`; 211 runtime smoke for
commit `a877f590b3cea611c1cde4b2e78f856597cb1894` accepted the projection on
2026-06-08, so G9 now keeps
`latency_percentile_per_surface_split_and_dashboard_acceptance` open for
per-surface latency splits and dashboard acceptance. The alert/export baseline now embeds
`ai-platform.alert-slo-readiness.v1` rule-template evidence for queue,
database, worker, model-gateway, sandbox, error-taxonomy, and capacity-gate
signals plus contract-only `ai-platform.alert-delivery-channel-policy.v1` as
`alert_delivery_channel_policy_contract`. The delivery policy allows only
Admin Runtime dashboard, release-evidence entry, and operator manual review
channels, keeps ordinary-user delivery disabled until G9 acceptance, and keeps
`alert_delivery_channel_runtime_acceptance` open. It does not close G9 and does
not replace recorded load-test evidence,
latency percentile per-surface/dashboard acceptance, model-gateway load-test evidence,
taxonomy dashboard acceptance, golden-set evaluation runtime and 211 acceptance, alert
runtime/dashboard/211 acceptance, trace/audit export runtime/dashboard/211
acceptance, or 211 deployment smoke.

The quality-evaluation baseline now embeds
`ai-platform.quality-golden-set-readiness.v1` as a contract-only source-level
readiness contract under `observability_readiness.domains.quality_evaluation`.
Its nested `ai-platform.golden-set-eval-evidence-contract.v1` records the
future evidence write path `quality_evaluation.golden_set_runs.<eval_run_id>`,
required eval fields, public context provenance, public artifact references,
redaction scan status, and operator review status. This does not close G9:
golden-set evaluation runtime and 211 acceptance remain open, along with office
workflow acceptance dataset approval, threshold calibration, dashboard
acceptance, review, and smoke evidence.

The alert/export baseline now also embeds
`ai-platform.trace-audit-export-readiness.v1` as
`trace_audit_export_contract` under
`observability_readiness.domains.alerts_and_exports`.
The standalone operator view is generated by
`python tools/trace_audit_export_readiness.py --format json` or
`--format markdown`. It records
`ai-platform.trace-audit-export-contract.v1` with future export evidence at
`audit.trace_exports.<export_id>`, limited to
`run_event_public_projection`, `audit_event_public_projection`,
`admin_runtime_observability_summary`, and `release_evidence_entry` sources.
This is contract-only and does not close G9. The remaining trace/audit export
gaps are `trace_audit_export_runtime_acceptance`,
`trace_audit_export_dashboard_acceptance`, and
`trace_audit_export_211_acceptance`, which stay open until runtime export,
dashboard review, redaction, and deployment evidence are proven.

The alert/export baseline now also embeds
`ai-platform.release-evidence-readiness.v1` as a source-level export-location
contract under `observability_readiness.domains.alerts_and_exports`. The
standalone operator view is generated by
`python tools/release_evidence_readiness.py --format json` or
`--format markdown`. It records `docs/release-evidence/` and
`docs/release-evidence/README.md` as the repository-owned release evidence
location, and defines `ai-platform.release-evidence-entry.v1` for future
reviewed, redacted entries at
`docs/release-evidence/<gate>/<commit_sha>/<evidence_id>.json`. This does not
close G9. The standalone export acceptance preflight is generated by
`python tools/release_evidence_export_acceptance.py --format json` or
`--format markdown`, and the same source-level readiness snapshot embeds
`ai-platform.release-evidence-export-acceptance.v1` as a safe reviewed-evidence
index precondition that excludes raw runtime payloads and older entries missing
runtime subject commits instead of treating them as current safe export rows;
it also excludes non-entry `skill-release/*` scaffold JSON from the G9 entry
validator. The accepted entry artifact kinds now include
`alert_trace_export_runtime_acceptance` for reviewed alert-delivery and
trace-export runtime acceptance records. That artifact kind is still bounded
release evidence and does not close G9 by itself.
The same source-level readiness snapshot now also embeds
`ai-platform.release-evidence-retention-policy.v1` as a contract-only
retention policy with a 180-day default, 30-day minimum, reviewed-delete
requirement, and reviewed/redacted-entry-only delete scope. The later
2026-06-12 `d4486ebf5a33ce23a632a69bcf07ef1220b61ea3` 211 runtime evidence
records reviewed runtime export, retention, and alert/trace export runtime
acceptance for the active Foundation Alpha POC runtime subject, so the narrower
`g9_runtime_export_and_retention_acceptance` and
`alert_delivery_and_trace_export_211_acceptance` blockers are no longer the
next Foundation Alpha slices for that runtime. The earlier `00e4e6b` and
`b96d02e232176bade455f2af2bc3080f8f372206` evidence remains historical. This
does not close G9; signed Skill package or SBOM review evidence,
ordinary-user legacy-route acceptance, packaged frontend image release
acceptance, review/PR closure, G9 Admin Runtime observability follow-ups, and
broader auth/session/RBAC regression remain separate blockers.

`tools/verify_release_evidence_runtime_acceptance.py` is now the focused
runtime-packaged verifier for that path. It may produce the safe
`ai-platform.release-evidence-runtime-acceptance.v1` summary and can feed
`observability_readiness` without raw `source_ref`, `evidence_ref`, storage key,
sandbox path, or secret-like payloads. Foundation Alpha readiness does not clear
the G9 release-evidence followup from a local verifier pass alone; it requires a
reviewed, redacted 211 runtime evidence entry for the same runtime subject.

The source-level capacity baseline now includes
`MODEL_GATEWAY_REQUEST_CONCURRENCY_LIMIT` as a model-gateway pressure-control
setting. Its default remains `0`, meaning no platform-level model gateway
request limit is enabled and production concurrency defaults are unchanged. If
a target profile sets a positive value, Admin Runtime reports it through
`capacity.limits.model_gateway` and `backpressure.model_gateway` as a
configured-only signal: enforced `request_concurrency_limit` remains empty and
`limit_enforcement` remains `not_implemented`. Source now also records the
contract-only `ai-platform.model-gateway-backpressure-policy.v1` baseline as
`model_gateway_backpressure_policy_contract`: it binds
`MODEL_GATEWAY_REQUEST_CONCURRENCY_LIMIT` to the required Admin Runtime fields
`capacity.limits.model_gateway`, `backpressure.model_gateway`, and
`observability.error_categories`, and keeps
`model_gateway_timeout_and_backpressure` as the required recorded load-test
gate before any production default decision. G9 therefore keeps the request
limit/enforcement gap plus recorded model-gateway load-test evidence open. This
is a visibility baseline only; it does not satisfy the recorded capacity-evidence gate or G9 without actual
request-path backpressure enforcement, real model-gateway timeout/backpressure
load evidence, and 211 acceptance, and it keeps
`production_default_policy = do_not_raise_without_recorded_load_test_evidence`.

The source-level error taxonomy contract is now `ai-platform.error-taxonomy.v1`.
Admin Runtime derives public `error_categories` from allowlisted error-type
counts for executor, tool, tool permission, sandbox, model gateway, queue,
database, memory/context, artifact, auth/policy, and unknown failures. The
same readiness baseline now embeds
`ai-platform.error-taxonomy-dashboard-readiness.v1` as
`error_taxonomy_dashboard_contract`; the standalone operator view is generated
by `python tools/error_taxonomy_dashboard_readiness.py --format json` or
`--format markdown`. Its nested
`ai-platform.error-taxonomy-dashboard-contract.v1` requires
`observability.error_categories`, `observability.error_types`,
`observability.recent_failures`, and
`observability_readiness.error_taxonomy`, with display limited to category
counts, definitions, trend windows, public recent-failure references, and
last-seen timestamps. This is contract-only and does not close G9. The
remaining taxonomy dashboard gaps are
`error_taxonomy_dashboard_runtime_acceptance`,
`error_taxonomy_dashboard_visual_acceptance`, and
`error_taxonomy_dashboard_211_acceptance`, which stay open until runtime
dashboard behavior, visual acceptance, redaction, review, and 211 evidence are
proven.

### G0 / G9 Frontend Source Traceability Status

Issue #17 is improved but still open. The migrated frontend source under
`frontend/web` now has a reusable release traceability CLI and
`tools/frontend_projection_audit.py` wired as the first `ci:verify` step.
Release traceability records the same git commit, package manager,
package/lockfile hashes, CI commands, and a deterministic static `dist/`
manifest without printing local paths, `.env` values, or secret-like data. The
static manifest records file count, total bytes, entry hashes, and a manifest
hash. It also requires `dist/ai-platform-build-provenance.json` before treating
an ignored `dist` directory as same-commit release evidence; missing, stale,
dirty, or unknown-dirty build provenance is reported as `built_unverified` with
blockers instead of being silently tied to the current backend/worker commit.
The same traceability CLI now also reports packaged frontend image definition
traceability through `frontend/web/Dockerfile`,
`frontend/web/nginx.conf.template`, and
`deploy/ai-platform/docker-compose.frontend.yml`, and fails closed if required
build provenance args, nginx upload/proxy controls, compose args, or packaged
delivery denylist checks regress. The GitHub Actions frontend workflow now also
contains a non-push packaged-image build/provenance job that builds
`ai-platform-frontend:${{ github.sha }}` with the current commit and verifies
`ai-platform-build-provenance.json` from inside the image without running Docker
compose or reading `.env`. Docker-capable runtime smoke and release acceptance
remain pending. `tools/frontend_packaged_runtime_smoke.py` now records the
fail-closed packaged frontend runtime smoke readiness contract
`ai-platform.frontend-packaged-runtime-smoke.v1`; its evidence contract is
`ai-platform.frontend-packaged-runtime-smoke-evidence.v1` with write path
`frontend_release.packaged_runtime_smoke.<commit_sha>`. The tool is
evidence-only, does not run Docker, and does not close G6, G9, or #21. The
2026-06-08 211 attempt for commit `305bc40` reached the extracted source and
current API/thin-shell health, but packaged image build failed before runtime
smoke because required `node:22-alpine` and `nginx:1.27-alpine` base image
metadata could not be pulled or found locally. The classified blockers are
`docker_registry_proxy_unreachable` and `base_image_pull_failed`; this is an
environment/build-host blocker, not release acceptance. A 2026-06-12 211
attempt for commit `83a500e` synced the current source archive, added the
frontend image `ai-platform.source-revision` label contract, and verified the
source with `python3 -m compileall -q app tools scripts`, but the Docker daemon
still used a stale registry proxy and failed before runtime smoke. BuildKit
could not resolve the Dockerfile frontend, and a no-syntax probe could not pull
`node:22-alpine`; `nginx:1.27-alpine` remains a required uncached base image for
the final packaged image. The verifier classified the redacted attempt as
`blocked_environment` with `docker_registry_proxy_unreachable` and
`base_image_pull_failed`, so it is not release acceptance and does not close
`packaged_frontend_image_release_acceptance`. The same packaged-frontend blocker
was later preserved as reviewed blocker evidence during the `6088d5d` refresh.
A later 2026-06-12 211 recheck
for then-active runtime subject `d4486eb` confirmed the same blocker after the
observability evidence loader rollout: source marker pointed to `d4486eb`,
frontend image sources were present, but the Docker daemon still could not pull
required base-image metadata, and no target `ai-platform-frontend:*` image was
cached. The reviewed redacted evidence is
`2026-06-12-211-foundation-alpha-poc-d4486eb-frontend-packaged-runtime-smoke-blocked.json`;
it is blocker evidence only and does not close
`packaged_frontend_image_release_acceptance`. A 2026-06-13 recheck for
runtime subject `18454a9` recorded the same `blocked_environment` status in
`2026-06-13-211-foundation-alpha-poc-18454a9-frontend-packaged-runtime-smoke-blocked.json`.
The projection
audit records the current production-source route inventory, active-browser
route inventory, active browser entry graph, active-browser legacy route policy
mapping, quarantined inactive legacy source findings, private/secret-like
projection term scan, `ci:verify` integration, legacy route policy mapping, and
remaining legacy enforcement/remap gaps. The active browser entry graph is
currently clear of forbidden private/secret-like projection terms, but active
legacy routes still require policy enforcement or ai-platform projection remap;
`projection:audit` exits 0 with status `pass_with_policy_gaps`. G6/G9 still
block ordinary-user frontend/governance rollout until active legacy routes are policy-gated or
remapped and quarantined legacy model/channel/envvar sources are remapped or
policy-gated.
This provides the traceability and audit base for backend/worker/frontend
same-commit review. `.github/workflows/ai-platform-frontend.yml` now runs
frontend install, `ci:verify`, release traceability, and the packaged-image
build/provenance gate on relevant source changes; GitHub Actions run
`27124531731` passed on commit
`1d8ba363f7f76b944e37b9003c2fef6998386fd1`, including both the frontend
projection/lint/build/trace job and the packaged image build/provenance job.
A 211 packaged-image build attempt for commit
`e8dc27f30f5d5302547090a2121923aed88e8201` reached the private repository
source but failed before the application build because the Docker build host
could not pull required registry/base-image metadata and lacked cached base
images. The remaining #17 source-ownership evidence is packaged frontend image
smoke/release acceptance on a Docker-capable host; the current trace records
the definition and CI contract fail-closed. It does not close legacy policy
enforcement / ai-platform projection remap.

The first frontend operator visibility loop is now present in `frontend/web`:
Settings includes an admin-only Admin Runtime Capacity section that consumes
only `GET /api/ai/admin/runtime/overview` and renders capacity, backpressure,
model gateway limit status, G6 governance gaps, and missing load-test evidence.
This reduces the G9 visual
gap but does not satisfy the recorded capacity-evidence gate because no load-test evidence has been recorded, and
does not close G6 because legacy env-var/model/channel/MCP route remap and
ordinary-user acceptance remain open.

### G6 Governance Readiness Status

G6 is partial and remains blocked for ordinary-user governance/frontend rollout. The current
baseline is recorded in
`docs/operations/ai-platform-governance-readiness.md`, exposed through
`tools/governance_readiness.py`, and included in the admin-only runtime
overview as `governance` with schema
`ai-platform.governance-readiness.v1`.

Implemented baseline controls include Admin tool policy inventory and audit,
user tool-permission request/decision flow, public permission-card projection,
Skill version registry, promote/rollback release policy, dependency policy
materialization, skill snapshot/release-decision locks, session-bound memory
records, user opt-out, Admin memory policy inventory, retention cleanup,
redaction, Admin redaction preview/audit, long-term cross-session memory fail-closed behavior,
delete/retention/export erasure evidence through
`tools/memory_erasure_readiness.py`,
source-level office context-pack architecture readiness through
`tools/office_context_readiness.py`,
source-level context-pack persistence/versioning as
`source_level_context_pack_persistence_and_versioning` with public
`context_pack_version` and `context_pack_generated_at`,
secret-safe Skill release readiness evidence through
`tools/skill_release_readiness.py` with schema
`ai-platform.skill-release-readiness.v1`,
contract-only Admin Skill release dashboard readiness through
`tools/skill_release_dashboard_readiness.py` with schema
`ai-platform.skill-release-dashboard-readiness.v1` and nested
`ai-platform.skill-release-dashboard-contract.v1`, recorded in governance
readiness as `admin_skill_release_dashboard_contract`,
pending Skill release review-manifest template generation through
`tools/skill_release_readiness.py --review-template --skill-id <skill-id>`,
external pending Skill release evidence scaffold generation through
`tools/skill_release_readiness.py --write-evidence-scaffold --skill-id <skill-id>
--evidence-root docs/release-evidence/skill-release` with schema
`ai-platform.skill-release-evidence-scaffold.v1` and
`external-release-evidence/<skill-id>/...` references that do not change Skill
content hashes or close G6 by themselves,
fail-closed review-manifest evidence-file validation that rejects empty,
placeholder, secret-like, unmatched, or still-pending scaffold
SBOM/license/vulnerability references when review manifests claim `passed` or
set review flags to true,
source-level `ai-platform.skill-dependency-review-policy.v1` contract
evidence for the required SBOM/signed-package, license-policy, and
vulnerability-scan categories and the `ai-platform.skill-release-review.v1`
review manifest schema fields `sbom_reviewed`, `license_policy_reviewed`, and
`vulnerability_reviewed`, source-level
`ai-platform.skill-signed-package-evidence-contract.v1` /
`skill_signed_package_evidence_contract` evidence for package artifact
reference, package digest, signature artifact reference, signer identity,
signing certificate or key reference, transparency log or attestation
reference, verification status, and review status,
frontend
release traceability, static `dist` release manifest with build-provenance
gate, frontend projection audit wired first into `ci:verify`, packaged frontend
image definition traceability, GitHub Actions frontend CI workflow with
non-push packaged-image build/provenance contract, active browser projection
audit clearance, active-browser legacy route policy audit, and quarantined
inactive legacy secret-like source reporting. Tool permission now also has a
source-level allow/ask/deny taxonomy evidence snapshot through
`tools/tool_policy_readiness.py`, tied to the current `evaluate_tool_policy()`
contract for active low/medium/high/write-capable cases and fail-closed
disabled registry or tenant-policy cases. Admin tool policy governance now also
has a bounded same-tenant change-history projection through
`GET /api/ai/admin/tool-policies/history`, backed by the existing
`admin.tool_policy.updated` audit log and allowlisted public policy fields.
The observability evidence loader slice was deployed to 211 on
2026-06-12 as `d4486ebf5a33ce23a632a69bcf07ef1220b61ea3` with image
`ai-platform:d4486eb-observability-evidence-loader`. API/worker source revision labels, the 211 source marker, OCI
revision labels, and image internal source marker pointed to `d4486eb`; API
health returned `ok`; and the repo-local compose labels were in use. Inherited
runtime-subject/runtime-rollout/source_revision alias labels and the compose
environment-file label still recorded prior rollout metadata. A focused
`tools/verify_governance_runtime_smoke.py` run returned `ok: true` and is
recorded as
`docs/release-evidence/foundation-alpha-poc/d4486ebf5a33ce23a632a69bcf07ef1220b61ea3/2026-06-12-211-foundation-alpha-poc-d4486eb-governance-runtime-smoke.json`.
The same `d4486eb` evidence directory now also records runtime POC, Auth/RBAC,
release-evidence runtime acceptance, alert/trace export runtime acceptance, and
packaged frontend blocker evidence for the current runtime subject. This
refresh verifies the controlled POC loop for `d4486eb` but does not close
Foundation Alpha.
The Admin bulk-review dashboard baseline is now split into a contract-only
readiness artifact through `tools/tool_policy_bulk_review_readiness.py` with
schema `ai-platform.tool-policy-bulk-review-readiness.v1` and nested
`ai-platform.tool-policy-bulk-review-dashboard-contract.v1`. Governance
readiness records this as `admin_policy_bulk_review_dashboard_contract`: the
contract binds the dashboard to the existing admin-only policy inventory,
history, and single-tool update routes; requires bounded inventory/history,
taxonomy summary, decision options, legacy-route gap summary, filters,
per-tool diff preview, update confirmation, and change-history drilldown; and
does not add a batch mutation API or expose private runtime data.

This does not close G6. Remaining blockers are policy enforcement or
ai-platform projection remap for legacy frontend admin/MCP/model/envvar/channel
surfaces, `admin_policy_bulk_review_runtime_acceptance`,
`admin_policy_bulk_review_visual_acceptance`,
`admin_policy_bulk_review_211_acceptance`, SBOM or signed-package release
evidence plus passed review manifests bound to matching evidence files,
real signed-package reviewed evidence and runtime/Admin acceptance, dependency vulnerability
and license evidence plus passed review manifests bound to matching evidence
files, runtime acceptance for the source-level skill dependency review policy,
`dependency_vulnerability_or_license_policy`,
`skill_dependency_review_policy_runtime_acceptance`,
`admin_skill_release_dashboard_runtime_acceptance`,
`admin_skill_release_dashboard_visual_acceptance`,
`admin_skill_release_dashboard_211_acceptance`,
runtime office context-pack persistence/versioning,
quarantined legacy frontend source remap, packaged frontend image smoke/release
acceptance on 211 or another Docker-capable host, and ordinary-user G9
acceptance. Do not use this baseline to expand sandbox privilege, raw Skill
selection, or ordinary-user platform-level multi-run orchestration exposure.

The Skill dependency-review runtime acceptance path is explicit but still open
until reviewed 211 evidence exists. `tools/skill_release_readiness.py` publishes
the `ai-platform.skill-dependency-review-runtime-acceptance.v1` contract, and
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
invariants that keep ordinary-user platform-level multi-run orchestration,
long-term cross-session memory, production concurrency defaults, and Docker sandbox hardening closed. Closing
this runtime gap does not close G6, signed package/SBOM review, dependency
vulnerability/license review, or Admin Skill release dashboard acceptance.

The #22 office context-pack work now has source-level architecture readiness,
source-level context-pack persistence/versioning evidence, user-visible API
projection source tests, source-level execution-tier routing tests, and
frontend run-playback context provenance source tests, executor prompt-injection
tests, document-centric follow-up state source tests, plus a sandbox latency
split observability source contract:
`tools/office_context_readiness.py` defines bounded allowed context
sources, user-visible provenance fields, execution tiers, and non-goals, while
`source_level_context_pack_persistence_and_versioning` preserves a bounded
public `context_pack_version` fact alongside `context_pack_generated_at` in
source-level snapshot/projection paths. Source-level router tests route
lightweight writing to `sdk_only_writing`, document generation/review/translation
skills to `document_worker`, and explicit sandbox/script/browser work to
`heavy_sandbox` without starting Docker during routing. The sandbox latency split
contract separates lease acquisition, container cold start, healthcheck, executor
dispatch/model work, document processing, cleanup, and total runtime timings.
The document-centric follow-up state source tests record source-run artifact
linkage for copy, retry, and resume context snapshots while public context refs
only expose artifact count and bounded latest artifact version. Reviewed PR #44
release evidence now records 211 sandbox latency split acceptance for #22.
Executor context-pack 211 acceptance remains open until fresh live evidence
proves positive source-run artifact scope and public input-key redaction. This
still does not add a new database schema, enable long-term cross-session memory,
start Docker for lightweight tasks, or expand ordinary-user platform-level
multi-run orchestration exposure. The 211 sandbox runtime verifier now requires
hardening evidence for lease/workspace isolation, cleanup, resource timeout
fallback, failure fallback, and cached lease scope revalidation, so future
runtime smoke cannot omit the cached-lease scope-drift regression. This replaces
the older single "bounded office
context-pack product contract" blocker with explicit sandbox latency evidence
and a still-open executor context-pack 211 acceptance requirement.

The 2026-06-11 context provenance follow-up adds source-level public provenance
fields to created context snapshots and queued `context_snapshot` references:
`referenced_materials`, `used_context_summary`, `latest_artifact_version`,
`execution_tier`, `context_pack_version`, and `context_pack_generated_at`. These
fields expose counts, safe input keys, memory policy source/read flags, tier, an
optional manifest-supplied bounded public artifact version, bounded public
context-pack version, and generated time only; raw message/file/artifact/memory
IDs remain outside the public provenance fields and the owner-scoped context
snapshot API response, with source tests covering the user-visible API
projection. The scoped database row and worker lookup path still keep those IDs
internally to compute public counts. Executor private payloads, raw storage
keys, sandbox workdirs, and secret-like values remain outside the public projection. Worker execution
resolves existing context snapshots from the scoped DB row and regenerates
public provenance/counts rather than trusting queue copies or stored payload
provenance. This narrows the G6/#22 context output gap; PR #44 later records
superseded 211 executor context-pack evidence that no longer closes acceptance,
while long-term cross-session memory, production sandbox hardening, and
ordinary-user platform-level multi-run orchestration exposure remains blocked.

The document-centric follow-up state source slice now records source-run artifact
linkage for copy/retry/resume context snapshots. The platform stores source
artifact IDs internally in `included_artifact_ids` and exposes only
`referenced_materials.artifact_count` plus `latest_artifact_version` only when
the source artifact manifest supplies a safe public version. It does not invent
a version from artifact count. This source-tested control is covered by later
PR #44 source tests and sandbox latency split evidence, but executor
context-pack 211 acceptance remains open and it does not close any broader
G6/G9 gate or ordinary-user platform-level multi-run orchestration exposure.

The S2 sandbox runtime smoke path is now recorded as
`sandbox_runtime_smoke_contract` for `211_sandbox_latency_split_smoke`. The
contract uses `scripts/generate_sandbox_runtime_evidence_211.py` to generate
evidence and `scripts/verify_sandbox_runtime_211.py` to verify it on a
Docker-capable 211 host with `sudo -n docker`, preferring the already-local
cancel probe image `ai-platform:local`. The smoke evidence must include
`non_expansion_invariants` such as
`ordinary_user_high_risk_sandbox_allowed=false` and
`ordinary_user_multi_agent_allowed=false`. Reviewed PR #44 evidence now records
`sandbox_cold_start_latency_split_211_acceptance` for the controlled verifier
run; this still does not close Docker sandbox production hardening, G6/G9, or
ordinary-user sandbox/multi-agent expansion. Its hardening evidence must label
lease/workspace/cleanup checks as `live_platform_probe` and
timeout/failure/cached-lease checks as `source_regression_guard`.

The S2 office executor context-pack acceptance path is recorded as
`executor_context_pack_runtime_acceptance_contract` with schema
`ai-platform.executor-context-pack-runtime-acceptance.v1`. Its default
generator/verifier output records
`source_probe_evidence_strength=source_probe_on_target_runtime`, a binding
check that is not live worker-run acceptance. Closure requires
`required_live_evidence_strength=live_worker_run_payload` from
`scripts/generate_executor_context_pack_evidence_211.py --live-run-id <run_id>`
and `scripts/verify_executor_context_pack_211.py --run-id <run_id>
--require-live-run-payload`. It ties the remaining 211 worker acceptance to
`scripts/generate_executor_context_pack_evidence_211.py`,
`scripts/verify_executor_context_pack_211.py`,
`app.repositories.get_context_snapshot_for_worker`,
`app.context_builder.executor_context_pack_from_snapshot`,
`app.executors.claude_agent_sdk_runner._context_pack_prompt_section`, and the
worker prompt-injection path. Required live evidence includes
`live_worker_run_payload`, `run_row_loaded`, `context_snapshot_id_present`,
`scoped_context_snapshot_loaded`,
`worker_context_ref_rebuilt_from_db_snapshot`,
`prompt_includes_bounded_summary`, `prompt_includes_context_pack_version`,
`prompt_includes_context_pack_generated_at`, `raw_storage_identifiers_absent`,
`sandbox_runtime_paths_absent`, `executor_private_content_absent`,
`long_term_memory_read_false`, and
`source_run_artifact_scope_tenant_workspace_user_session`, and
`source_run_artifact_count_positive`, with fresh `generated_at` evidence and
explicit `source_functions` binding. Live evidence must also show public context
`input_keys` without `copied_from_run_id`, `source_run_id`, `parent_run_id`, or
`run_id`. Live evidence must carry those per-item booleans under the
verifier-checked `runtime_evidence` JSON section. Source-probe
evidence carries `does_not_close_211_acceptance=true` and
`runtime_acceptance_requires_real_run_payload=true`; the superseded PR #44 live
evidence carried `runtime_run_payload_verified=true` but does not satisfy the
current positive source-artifact and public input-key checks for the named #22
runtime gap. Its
`non_expansion_invariants` keep `ordinary_user_multi_agent_allowed=false`,
`ordinary_user_high_risk_sandbox_allowed=false`, and
`long_term_cross_session_memory_enabled=false`. `executor_context_pack_211_acceptance`
remains open for #22 and does not close G6/G9 or ordinary-user platform-level multi-run orchestration exposure.

Frontend packaged image release acceptance is also exposed through
governance readiness as `packaged_runtime_smoke_contract`, backed by
`tools/frontend_packaged_runtime_smoke.py` and schema
`ai-platform.frontend-packaged-runtime-smoke.v1`. Until accepted Docker-capable
host evidence exists, it remains `blocked_missing_runtime_evidence` with
`frontend_packaged_runtime_smoke_evidence_missing`, runtime policy
`docker_capable_host_only_no_local_windows_docker`, and the open
`frontend_packaged_image_delivery_and_release_acceptance` blocker. This does
not close G6/G9, #21 capacity, or packaged frontend release acceptance by
itself.

The current context public-summary verifier treats file-context provenance as
incomplete unless `file_count > 0` is paired with the safe
`used_context_summary.input_keys` value `attachments`. This is a public
presence signal only; file IDs, storage locators, raw upload keys, and sandbox
paths remain forbidden in public/admin projections and release evidence.

The Foundation Alpha readiness summary now also promotes reviewed 211
`context_snapshot_public_projection` smoke evidence into the G6 POC governance
domain. The promoted summary is intentionally bounded to status, referenced
material counts, raw-ID presence, forbidden-leak count, and summary source; it
does not echo raw message/file/artifact/memory IDs, executor private payloads,
storage locators, or workspace paths. Older smoke records without that runtime
projection stay fail-closed as `missing_context_snapshot_public_projection`;
records with file counts but no `attachments` input signal stay fail-closed as
`attachments_input_key` until refreshed by a current 211 smoke.

The 2026-06-08 frontend projection audit follow-up makes the remaining frontend
G6/G9 blockers machine-actionable through
`tools/frontend_projection_audit.py --format json` and
`tools/governance_readiness.py --format json`. The audit now emits
`open_gap_details` for the legacy production route remap/enforcement gap, the
15 active-browser legacy route policies that must be hidden or policy-gated
before ordinary-user acceptance, and 40 quarantined legacy source violations
that must be remapped or removed before rollout. This is visibility and
operator triage evidence only; it does not close G6 or G9.

### P1 Admin Runtime Admission / Backpressure

Status: merged on `main` at `d8c733e7eeaa6e11786fe13771b84b8f32a95292` and
deployed/smoked on 211 with runtime image
`ai-platform:d8c733e-p1-admin-runtime-backpressure`.

This slice extends the existing admin-only runtime overview with same-tenant
active-run admission pressure and a sanitized backpressure projection derived
from admission, queue insight, and the allowlisted DB pool status. It reports
per-user active-run saturation totals without returning run/session/skill/input
payload identifiers, and normalizes queue capacity/quota/sample plus DB waiting
pressure into operational fields for Admin Runtime.

The new `backpressure` projection is allowlisted and does not expose raw Redis
keys, raw queue payloads, runtime private payload, storage keys, sandbox work
directories, or secret-like markers. `worker_available` remains visible as a
queue state but is not emitted as a pressure reason. The route remains
admin-only, same-tenant, and fail-closed when queue inspection or sandbox
cleanup fails.

Local verification included focused Admin Runtime/repository/source-authority
tests at `135 passed`, compile, `git diff --check`, inherited-configuration
multi-agent review with no Critical or Important issues, and full pytest at
`1074 passed, 6 skipped, 2 warnings`. 211 verification confirmed API and
frontend health, API/worker source label parity for the deployed commit,
container compile, admin overview containing `admission` and `backpressure`,
ordinary-user overview returning `403`, and clean recent API/worker logs.

This slice does not add a frontend entry, change admission policy, open
sandbox/tool privilege, add a DB migration, or start Long Task / Multi-Agent
Runtime work. Detailed execution evidence remains in
`docs/superpowers/plans/2026-06-06-p1-admin-runtime-admission-backpressure.md`.

### G5 Tenant-Aware Queue Lease

Status: PR #18 opened from `feat/g5-tenant-aware-queue-lease` and deployed /
smoked on 211 with runtime image `ai-platform:4c7b3e2-g5-queue`.

This slice keeps the existing global Redis queue topology but adds bounded,
tenant/user-aware worker lease behavior when quota settings are enabled.
`QUEUE_TENANT_PROCESSING_LIMIT`, `QUEUE_USER_PROCESSING_LIMIT`,
`QUEUE_LEASE_SCAN_LIMIT`, and `QUEUE_INSIGHT_SCAN_LIMIT` are forwarded through
the non-secret deploy template and compose environment. When quota mode is
enabled, workers scan only the configured queued window and use a Redis Lua
script to atomically re-check processing capacity, recompute active
tenant/user counts from the processing list, validate the candidate index, move
the matched item into processing, and write processing/retry metadata plus
worker heartbeat. Invalid queued payload cleanup in quota mode is also
matched-index and atomic, preventing duplicate dead-letter writes during
concurrent workers.

Queue insight now reports quota limits, bounded scan sampling, and
tenant/user throttling pressure. Public queue insight is current-user scoped
and does not expose other same-tenant user ids; Admin Runtime/Admin Runs request
admin breakdown explicitly. Quota decisions ignore stale `processing_meta`
entries that no longer correspond to an item in the processing list, reducing
the risk of indefinite false throttling after orphan metadata.

Local verification recorded RED/GREEN coverage for tenant quota bypass, user
quota bypass, scan-bound idle behavior, invalid payload dead-lettering during
bounded scan, atomic Lua script usage, stale-meta quota immunity, public
projection user-id redaction, admin user-quota pressure reason, bounded insight
queued scan, worker setting propagation, compose/env forwarding, invalid-payload
scan-window shrink recovery, malformed Lua attempt metadata fallback, and
route-level public/admin projection selector arguments. Review-fix focused
verification passed with `172 passed`; compile passed with
`python -m compileall -q app tools scripts`; `git diff --check` exited 0 with
only CRLF normalization warnings; full local pytest passed with `1061 passed,
6 skipped, 2 warnings`.
Inherited-configuration multi-agent review first found quota atomicity,
duplicate raw/index deletion, invalid dead-letter race, unbounded insight scan,
public user-id leakage, missing user quota reason, and stale metadata risks.
Those were fixed with RED regression tests; follow-up review found no Critical
issues and one Important admin user-quota reason gap, which was fixed with an
admin projection regression test. Final inherited-configuration review found
two Important queue edge cases: scan-window index drift after invalid payload
cleanup and malformed Lua `attempts` metadata. Both were fixed with RED
regression tests. A projection review found missing route-level selector
assertions and misleading design wording; tests now pin public `user_id`
selectors and admin `include_user_breakdown=True`, and the design document
states that `throttling.users` is admin-only.

PR/deploy evidence: branch `feat/g5-tenant-aware-queue-lease` pushed to origin,
PR #18 created, commit `4c7b3e2de93bf5dddd76daa7029cb56c80df0787` synced to
211 source path `/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform`,
and source markers set to `4c7b3e2de93bf5dddd76daa7029cb56c80df0787` /
`g5-tenant-aware-queue-lease`. 211 runtime-only image build copied current
`pyproject.toml`, `app/`, `skills/`, `tools/`, `scripts/`, and
`docker-entrypoint.sh`, set image labels for the same revision/note, and
restarted API/worker through the repo-local compose file with
`sudo -n env AI_PLATFORM_IMAGE=ai-platform:4c7b3e2-g5-queue docker compose ...
up -d --no-build api worker`. 211 smoke verified API and frontend-proxy
`/api/ai/health` returned `{"status":"ok"}`, API/worker containers used
`ai-platform:4c7b3e2-g5-queue` with restart count `0` and matching labels, and
admin runtime overview returned queue quota capacity fields without secret,
private payload, or storage-key leakage. A container-local temporary Redis
probe under `ai-platform:smoke:g5-queue:1780721756` verified tenant quota skip,
user quota skip, invalid queued payload dead-letter plus continued leasing, and
public current-user queue projection redaction; cleanup confirmed
`TEMP_KEYS_LEFT=0`. Recent API/worker logs showed no traceback, exception,
permission-denied, or failed markers.

### G5 Tenant-Aware Worker Maintenance

Status: merged on `main` via PR #19 and deployed/smoked on 211 at
`7a9db83eba98c1b7263e3ffbb85ef39fecc5e2a4` with runtime image
`ai-platform:7a9db83-g5-worker-maint`.

This follow-up replaces the previous default-tenant memory retention worker
tick with a bounded rotating tenant/workspace scope cursor. The worker keeps
the existing enable/interval/limit settings, runs memory maintenance after
sandbox cleanup and before multi-agent dispatch and queue reclaim, and writes
same-tenant operational audit rows for affected scopes.

This slice does not change Memory policy behavior, does not enable
cross-session long-term memory, and does not add a new scheduler service. 211
smoke verified multi-scope cleanup, one-row-per-scope fairness under a bounded
limit, sanitized per-scope audit evidence, source/label parity, health, clean
logs, and smoke data cleanup. Detailed execution evidence remains in
`docs/superpowers/plans/2026-06-06-g5-tenant-aware-worker-maintenance.md`.

### G5 Active Run Admission

Status: merged on `main` at `cb20e3097f31419e5be5f1c608a20c7b3f7845a5` and
deployed/smoked on 211 with runtime image
`ai-platform:cb20e30-g5-active-run-admission`.

This slice serializes user-created active-run admission for create, chat,
copy, retry, and resume by acquiring a transaction-scoped Postgres advisory
lock over the structured `(tenant_id, user_id)` scope before counting
queued/running runs and inserting the next queued run. Copy-run now shares the
same admission gate and fails closed with HTTP 409 before creating or enqueuing
a copied run when `max_active_runs_per_user` is reached.

The server-owned multi-agent child handoff remains out of scope for this slice
and stays behind the later multi-agent runtime gate. 211 smoke proved the real
Postgres concurrency behavior with two transactions for the same tenant/user:
the second transaction waited on the advisory lock and then rejected with
`user_active_run_limit_exceeded` after the first transaction committed its
queued run. Smoke data cleanup left zero rows, API/frontend health passed, and
recent API/worker logs were clean. Detailed execution evidence remains in
`docs/superpowers/plans/2026-06-06-g5-active-run-admission.md`.

## 当前主链路

- 本地代码：当前 `ai-platform` 仓库根目录
- 前端入口：`http://10.56.0.211:18001/`
- 前端方向：企业 Agent 前端作为当前用户入口，所有事实源请求落到 `ai-platform` API。
- 后端 API：`ai-platform-api:8020`
- 211 后端代码：`/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform`
- 211 部署编排：`/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform`
- 平台容器：`ai-platform-api`、`ai-platform-worker`
- 平台依赖：`ai-platform-redis`、`ai-platform-postgres`、`ai-platform-minio`

## 当前决策

真实 Long Task / Multi-Agent Runtime 是最终目标，但不作为当前基础切片的第一落点，也不越过 2026-06-06 gate-based sync 中的 tenant-aware scheduler/quota、observability、sandbox/tool risk gates。

当前优先四个基础 P0：

1. Memory / Context。
2. MCP / Tool Permission。
3. Event / Playback Contract。
4. Sandbox Lease / Workspace。

原因：没有可复现 context、工具权限、事件回放和 sandbox lease，长任务与多 Agent 缺少审计、恢复和权限边界。

## P0.1 Memory / Context

目标：

- 每次 run 保存 executor context 边界。
- memory record 受 tenant/workspace/user/session/agent 约束。
- 跨 session 长期记忆默认不开放。

交付门槛：

- `run_context_snapshots`、`memory_records`、`memory_policies` schema。
- Context Snapshot API。
- Memory Record API。
- Memory record create 必须绑定 session；缺失 session 的写入默认拒绝。
- Admin policy set API。
- ContextBuilder 接入 run、chat、copy-run、resume、replay 与 worker-side refresh。
- 普通用户 soft delete 与 Admin same-tenant soft delete。
- retention cleanup。
- secret-like redaction pipeline。
- 普通用户 public projection 与 Admin operational projection 不返回 private payload。

后续硬化：

- 后台调度化 cleanup。
- configurable redaction policy。
- 更广业务 payload 覆盖审计。

### P1 Memory / Context Management

P1 management closure adds ordinary-user memory policy update and admin policy
inventory projections on top of the existing session-bound memory records,
admin retention cleanup, and redaction pipeline. Cross-session long-term memory
remains fail-closed; frontend work should consume only these public/admin
projections.

## P0.2 MCP / Tool Permission

目标：

- 工具调用先经过平台 request/decision 合同。
- 写工具无确认不得执行。
- request、decision、audit 和 run event 可追溯。

交付门槛：

- `run_tool_permission_requests` schema。
- Tool permission request API。
- Tool permission decision API。
- request / decision run events。
- decision audit log。
- RAGFlow read-only registry gate。
- Claude SDK unsafe Bash pre-tool permission hook。
- write-capable business tools 接入同一 gate。
- allow_once 消费/expiry 与 allow_for_run fingerprint 语义。
- permission decision lookup 必须匹配 exact tool_call_id 或稳定 request fingerprint，不能只取同 run/tool/action 的最新决策。

当前 source-level evidence 已覆盖 worker MCP 与 Claude SDK tool hook 的 exact
decision lookup：`allow_once` / `deny` 绑定 exact `tool_call_id`，
`allow_for_run` 绑定稳定 request fingerprint（`input_sha256` 或
`command_sha256`）。这不替代 211 runtime acceptance、普通用户确认卡视觉验收或
legacy frontend route remap。

后续硬化：

- tool schema validation。
- read-only/write risk policy。
- 真正 pause/resume 或 retry 语义。
- 普通用户确认卡和 Admin policy 闭环。

## P0.3 Event / Playback Contract

目标：

- run event 支持按序 replay。
- SSE reconnect 不重复 final message。
- 普通用户和 Admin 使用同一 event store 的不同 projection。

交付门槛：

- `run_events.sequence` schema。
- `list_run_events(after_sequence, limit)`。
- `GET /runs/{run_id}/events?after_sequence=&limit=`。
- `GET /runs/{run_id}/events/stream?after_sequence=` 首包 replay。
- `GET /runs/{run_id}/playback?after_sequence=&limit=` 返回 `ai-platform.run-playback.v1`。
- artifact card 只暴露 allowlist lineage。
- checkpoint/subagent 事件进入标准事件集合、runtime validator 和 stage map。
- 前端消费 playback 公开投影，不读取 executor private payload。

后续硬化：

- playback 产品化。
- Artifact/Office preview allowlist route: closed by the P1 Tool Permission /
  Agent Frontend V1 hardening follow-up below.
- 更广事件消费。
- 文件任务浏览器 UI 验收。

## P0.4 Sandbox Lease / Workspace

目标：

- sandbox workspace 和 container lifecycle 可租约、可续租、可释放、可审计。
- fake provider 不等于生产安全可用。
- Admin 能看到 stale/orphan/released lease。

交付门槛：

- `sandbox_leases` schema。
- run 级 sandbox lease create/renew/release API。
- Admin Runtime lease projection。
- run cancel / Admin cancel 释放 active sandbox lease。
- expired DB lease cleanup。
- Docker provider resource limit contract。
- provider orphan cleanup safety。
- run cancel / Admin cancel 先停止匹配 scope 标签的 live sandbox container，stop 成功后再 release DB lease；stop 失败时 DB lease 保持 active 以便重试，且不信任用户可控 lease payload 决定 stop 目标。

后续硬化：

- network/egress policy enforcement。
- 真实 orphan container cleanup job。
- 真实 container stop/remove smoke。
- 生产 Docker provider 在独立受控节点验证。

## Agent Frontend V1

目标：

- 企业 Agent 前端继续服务当前 `ai-platform` API。
- 登录只保留公司账号入口。
- 用户可见 chat stream、run event、artifact card、tool permission card、run drawer/playback。
- artifact download/preview 走平台鉴权和 allowlist。
- 文件任务完成浏览器 UI 验收。

交付门槛：

- 不得新增与当前发布入口并行的本地前端入口。
- `/api/*` 请求落到 `ai-platform-api:8020`。
- 普通用户投影不暴露 raw skill、storage key、runtime path、request payload、decision payload 或 sandbox `work_dir`。
- 普通用户投影中 `agent_id` 也不得作为 raw skill id 旁路。
- 企业 Agent 前端纳入正式发布编排。

### 2026-06-06 Frontend Source Ownership

Status: source migration landed for the current #17 slice under
`frontend/web`. The imported React/Vite source is the key-file hash-matched
LambChat POC frontend snapshot used by the 211 thin-shell entry. The current
runtime entry remains `http://10.56.0.211:18001/` served by
`tools/serve_lambchat_thin_shell.py`; no backend scheduling, sandbox,
auth/session, DB schema, or compose one-command delivery behavior is changed by
this source import.

This advances G0/G9 source ownership and same-commit reviewability, but it does
not close full Agent Frontend V1 rollout. Legacy LambChat admin/model/MCP,
persona, and sandbox-oriented panels remain imported source and require
ai-platform public/admin projection audit or product gating before ordinary-user
rollout. Detailed contract, multi-image direction, and remaining risks are
recorded in `frontend/web/README.md` and
`docs/frontend/ai-platform-frontend-migration.md`.
G8 platform-level multi-run orchestration and G10 workflow-owner rollout work
are not implemented by this migration.

## 后续顺序

1. Source Authority / Security Baseline 与公司内网 auth/session、tenant/workspace/user 隔离。
2. Tenant-aware concurrency / fair scheduling / DB pool / bounded queue metadata。
3. Admin Runtime / Observability / Quality / Ops。
4. Memory / Context Management 与 Tool Permission / Agent Frontend V1 用户闭环。
5. Long Task / Multi-Agent Runtime controlled beta。

### P1 Admin Runtime Overview Snapshot

Status: merged on `main` via PR #1 and deployed/smoked on 211.

The first P1 operational slice adds an admin-only overview contract for queue,
run status, sandbox lease/container state, and basic observability aggregates.
It is intentionally smaller than the full Observability / Quality dashboard and
does not start Long Task / Multi-Agent Runtime.

### P1 Memory / Context Management

Status: backend management slice merged on `main` via PR #2, PR #3, and
PR #4, then deployed/smoked on 211 at
`7f0a1133736f509be9d24a3b86eb03b2bbf5ead6`. The P1 frontend visible
management entry was deployed through the existing 211 thin shell at
`frontend-dist-ai-platform-20260605-114824`.

The P1 user/admin management baseline is closed for the current slice. It adds
ordinary-user memory policy self-management, user-visible memory record
management on the existing `/memory` route, admin same-tenant policy and record
inventory projections, admin-triggered retention cleanup, public projection
redaction, and `long_term_memory_enabled = false` fail-closed behavior. The 211
smoke verified frontend `/memory`, health, ordinary-user policy get/update,
public `document-review` memory record create/list/delete routing, user opt-out
blocking writes with empty record projection, admin inventory access,
ordinary-user admin denial, and admin retention cleanup. Backend scheduled
cleanup was not part of that baseline. That baseline left configurable
redaction policy as a follow-up hardening gate for the full Memory / Context
PRD gate; cross-session long-term memory is not permitted.

Backend scheduled cleanup was later closed as a P1 hardening follow-up on
`main` at `3fbab85ac9005279ccea26943366ca2dfb69b266`. The slice adds a
bounded worker-side expired-memory retention tick that runs after sandbox
cleanup and before queue reclaim/lease, uses default tenant/workspace scope,
respects enable/interval/limit settings, and writes only operational audit
evidence when expired records are deleted. Local verification recorded RED
coverage for missing worker cleanup, focused memory coverage with `154 passed`,
deployment-template coverage for compose env forwarding, and full pytest with
`996 passed, 6 skipped, 2 warnings`. Inherited-configuration review found no
Critical worker behavior issues; accepted feedback ensured docs were tracked
and worker cleanup settings were exposed through the deployment template and
worker compose environment.

The 211 deployment uses image `ai-platform:3fbab85ac900`,
`sha256:9e44eb075fdf8f3b226ff9510eea8fa7062d8e0c8eed59ac820bcc0a40cb9c18`,
with labels
`ai-platform.source-revision=3fbab85ac9005279ccea26943366ca2dfb69b266` and
`ai-platform.source_note=p1-memory-retention-worker-cleanup`. API and worker
were recreated with the repo-local compose file under
`/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform`;
API health and frontend proxy health returned `{"status":"ok"}`. The 211 smoke
seeded one expired default-tenant memory record, ran the worker maintenance
path, verified soft-delete and `worker.memory.retention.cleanup` audit evidence
with no content/private marker leakage, verified the worker cleanup env knobs,
checked clean recent API/worker logs, and removed all smoke rows. Configurable
redaction policy was still the remaining Memory / Context hardening follow-up
at that point, and cross-session long-term memory remained fail-closed.

Configurable redaction policy was later closed as a P1 Memory / Context
hardening follow-up on `main` at
`5d11ea61fbb9d441dc3a5a52545a29430ade83e6`. The slice adds
`memory_policies.redaction_mode` with `standard` and `strict` modes, preserves
the default standard behavior, rejects invalid request modes, treats invalid
stored modes as `strict`, and applies strict write-time redaction to memory
record content/metadata plus policy reason projection/audit. Local verification
recorded RED coverage for the missing contract, focused coverage with
`170 passed`, `python -m compileall -q app tools scripts`, full pytest with
`1005 passed, 6 skipped, 2 warnings`, and `git diff --check` exit 0.
Inherited-configuration review found no remaining Critical, Important, or
Minor findings after the fail-safer stored-mode and strict reason-redaction
fixes.

The 211 deployment uses image `ai-platform:5d11ea61fbb9`,
`sha256:bd50bcd53d6553783489b1250beb7f5c7b7ac7e054e35e3ef9e19b6ad97ed012`,
with labels
`ai-platform.source-revision=5d11ea61fbb9d441dc3a5a52545a29430ade83e6` and
`ai-platform.source_note=p1-memory-redaction-policy`. API and worker were
recreated with the repo-local compose file, while the existing 211 runtime
environment file was read without printing or copying secret values. API health
and frontend proxy health returned `{"status":"ok"}`. The 211 smoke applied
schema, verified invalid `redaction_mode` requests return 422, set user and
admin strict policies, created and listed a strict memory record, confirmed DB
storage/audit/projections had zero raw synthetic provider/JWT marker leakage
and 13 `[redacted-secret]` markers, checked clean recent API/worker logs, and
removed smoke rows from `audit_logs`, `messages`, `memory_records`,
`memory_policies`, `sessions`, and `users`.

### P1 Tool Permission / Agent Frontend V1

Status: tenant tool policy admin controls merged on `main` via PR #7, followed
by `05e92292831bcc42c1843981e4294b20e970c0fc` to serialize live permission
decision expiry events before writing run event JSON. The backend was deployed
to 211 with image label `ai-platform.source-revision=05e92292831bcc42c1843981e4294b20e970c0fc`.

This slice closes the current P1 tool permission and frontend projection
baseline. It adds an admin-only same-tenant tool policy inventory/update
contract, default read-only RAGFlow tenant policy seeding, fail-closed hidden
behavior when tenant policy is missing, `expires_at` decision projection, and
frontend handling for pending/decided tool permission cards across live events
and history replay. The 211 smoke verified admin list/update/audit, ordinary
user admin denial, missing-policy fail-closed projection, live request/decision
with event card `expires_at`, redaction of private request/decision payloads,
existing thin-shell frontend root/API health, dist API boundary, and LambChat
compatibility API gates. This does not open high-risk write tools or start P2
Long Task / Multi-Agent Runtime.

Artifact preview allowlist was later closed as a P1 Tool Permission / Agent
Frontend V1 hardening follow-up on `main` at
`12b6ce07a90e9bf03ec45c0989217e6de08c815f`. The slice adds authenticated
`/api/ai/artifacts/{artifact_id}/preview`, a shared PDF/Office MIME allowlist,
inline `Cache-Control: no-store` / `X-Content-Type-Options: nosniff` preview
responses, admin fallback audit `admin_artifact_previewed`, and artifact card
/ playback `preview_url` projection only for allowlisted content types. Local
verification recorded TDD RED for the missing route/projection, focused route
coverage with `149 passed`, compile success, and full pytest with
`1010 passed, 6 skipped, 2 warnings`. Inherited-configuration review found no
Critical issues; accepted feedback added `nosniff`, ordinary-user non-owner 404
coverage, tighter admin audit assertions, and playback preview projection
coverage. The 211 runtime-only deployment used image `ai-platform:12b6ce0`
(`sha256:61979e19395246efbb0952fba305eb868137812467eec1117fe49c20b561ab24`)
with labels
`ai-platform.source-revision=12b6ce07a90e9bf03ec45c0989217e6de08c815f` and
`ai-platform.source_note=p1-artifact-preview-allowlist`. The 211 smoke verified
API and frontend proxy health, OpenAPI route exposure, source label parity,
owner DOCX preview 200 with inline/no-store/nosniff headers, non-owner preview
404, ZIP preview 415 without opening preview, admin preview audit, playback
`preview_url` allowlist projection, no `storage_key` / private payload leakage,
clean recent API/worker logs, and smoke data cleanup.

The P1 Agent Frontend V1 file-task acceptance gate was hardened on `main` at
`542eb0c3be835be65bf83a44024b544ed272cf55`. The verifier now checks
artifact preview owner/cross-user isolation for allowlisted PDF/Office
artifacts, response `Content-Type`, `Cache-Control: no-store`,
`Content-Disposition: inline`, and `X-Content-Type-Options: nosniff`. The
Word review file-task smoke also fetches run playback and requires the
current `reviewed_docx` artifact card itself to expose exact platform
download and preview URLs while rejecting `storage_key`, private payload keys,
tenant-private paths, and runtime-private markers in playback. Local TDD
covered missing preview security headers, unallowlisted preview response MIME,
missing preview projection, unrelated artifact preview false positives, and
private payload marker leakage. Local verification passed `19` POC gate tests,
`49` focused frontend/playback compatibility tests, compile, and full pytest
with `1017 passed, 6 skipped, 2 warnings`. On 211 the updated verifier was
synced as a tool-only source update without Docker rebuild; API and frontend
proxy health were `ok`, the aggregate POC gate returned `ok: true`, artifact
preview isolation checked one allowlisted DOCX artifact with owner `200` and
cross-user `404`, Word review file-task playback returned contract
`ai-platform.run-playback.v1` with three artifact cards, one preview URL, and
matched download/preview counts for the produced `reviewed_docx` artifact.
The smoke-created runs, sessions, files, artifacts, events, messages, context
snapshots, skill snapshots, permission rows, and recent synthetic audit rows
were then cleaned by exact ID and verified with zero remaining run/session/file
/ artifact/event/message rows. Recent API/worker logs showed only expected
smoke requests and no new exception output.

### P2 Run Provenance Snapshot

Status: started as a read-only P2 foundation slice and deployed/smoked on 211
at `e99c299a726e76840ed66e2a7479ca5bc71ed21c`. This adds the
`ai-platform.run-provenance.v1` owner-scoped projection for existing run steps,
checkpoint ids, subagent ids, and artifact lineage. The 211 smoke verified API
health, OpenAPI route exposure, owner-scoped ordinary-user provenance
projection, checkpoint/subagent/artifact linkage, sensitive-field redaction,
and smoke data cleanup. It does not start high-risk tool execution, Docker
sandbox expansion, or autonomous multi-agent scheduling.

### P2 Run Control Readiness Snapshot

Status: started as a read-only P2 foundation slice and deployed/smoked on 211
at `d068e32d96332f1802c2236bccb1ae104d3810c5`. This adds the
`ai-platform.run-control-readiness.v1` owner-scoped projection for existing
run cancel, copy/resume, checkpoint reuse, and future retry readiness. The 211
smoke verified API health, OpenAPI route exposure, same-tenant ordinary-user
readiness response, checkpoint resume availability, raw skill/runtime/private
marker redaction, and smoke data cleanup. It does not start retry scheduling,
autonomous multi-agent dispatch, high-risk tool execution, or new sandbox
behavior.

### P2 Resume Manifest Snapshot

Status: started as a read-only P2 foundation slice and deployed/smoked on 211
at `ae575871f4a296d0bf5541e17a08dfde6e37c712`. This adds the
`ai-platform.run-resume-manifest.v1` owner-scoped projection for copied-run
checkpoint reuse intent, authorized source run linkage, pending reuse counts,
rerun counts, and public-safe step dependencies. The 211 smoke verified API
health, OpenAPI route exposure, image labels, same-tenant ordinary-user
manifest response, unauthorized source run hiding, raw skill/runtime/private
marker redaction, and smoke data cleanup. It does not start retry scheduling,
autonomous multi-agent dispatch, high-risk tool execution, or new sandbox
behavior.

### P2 Checkpoint Materialization Audit Snapshot

Status: started as a read-only P2 foundation slice and deployed/smoked on 211
at `2a268dedcd835d001928b24dc5e1f8fb8782855a`. This adds the
`ai-platform.run-checkpoint-audit.v1` owner-scoped projection for existing
checkpoint reusable-output state, artifact materialization, producer linkage
gaps, unsafe checkpoint id redaction, and uncheckpointed reusable step gaps.
The local verification passed focused route tests, route/source-authority
tests, compile, and full pytest with a repository-local pytest temp directory.
The 211 smoke verified API health, OpenAPI route exposure, image labels,
ordinary-user public projection counts/redaction, admin same-owner checkpoint
visibility, unauthorized user 404, API logs, worker logs, and smoke data
cleanup. This does not start retry scheduling, autonomous multi-agent dispatch,
high-risk tool execution, or new sandbox behavior.

### P2 Run Retry Request

Status: first write-side P2 run lifecycle control merged on `main` at
`49d8b51add842754dc4d46995679d1939bcebb7a`, deployed to 211, and smoked.
This adds owner-scoped `POST /api/ai/runs/{run_id}/retry` for failed or
dead-letter runs. It creates a copied queued run through the existing
copy-run/context/queue path, records `retry_requested`, `run_retry_created`,
and `run.retry` audit evidence, seeds retry-created steps with
`seeded_from = retry_run`, and preserves checkpoint reuse intent.

Local verification for the slice recorded focused route/source-authority
coverage, compile, and full pytest: `904 passed, 6 skipped, 2 warnings`.
Inherited-configuration reviews found no Critical issues. Important findings
around active-run/idempotency, stale source capability fail-closed behavior,
and concurrent same-source retry idempotency were fixed with regression tests,
including a source-run `FOR UPDATE` lock before active-retry lookup.

The 211 deployment uses image
`sha256:e4693329919b1b083cd1f24326d58a8b337d8fcf5db493fab63d4a673c2a3456`
for both `ai-platform-api` and `ai-platform-worker`, with
`ai-platform.source-revision=49d8b51add842754dc4d46995679d1939bcebb7a` and
`ai-platform.source_note=p2-run-retry-request`. The 211 smoke verified API
health, OpenAPI route exposure, owner retry success, repeated active retry
`409 retry_already_active`, active source `409 status_not_retryable`,
other-user `404 run_not_found`, retry events/audit, context snapshot
`source = retry_run`, queue payload context `source = retry_run`,
checkpoint reuse step seeding, code hash parity with local files, and DB/Redis
smoke data cleanup. This does not start automatic retry policy scheduling,
autonomous subagent dispatch, high-risk tool execution, or new sandbox
behavior.

### P2 Resume Checkpoint Lineage Materialization

Status: merged directly on `main` at
`9b85c008fb5493fca55b00aaa339af9b399fe030` with PR #8 opened for review/tracking,
deployed to 211, and smoked. This extends copied/retry run resume metadata with
safe per-step checkpoint lineage while preserving the existing
`resume.completed_step_outputs` map. Seeded copied steps and executor
`agent_step_reused` events now carry safe `checkpoint_id`, `source_step_id`, and
source-run linkage so checkpoint audit/provenance can keep a stable graph after
copy, retry, and resume. Completed multi-agent file-skill steps also materialize
safe checkpoint ids before copy/retry, while copy strips user-controlled
`resume` metadata, ordinary create-run and chat-stream input cannot inject
server-owned resume state, and chained reuse preserves the original producer
lineage.

Focused local coverage verifies repository resume metadata, copied-run step
seeding, adapter checkpoint reuse events, and existing checkpoint audit
projection behavior. The 211 deployment uses image
`sha256:209fa533f1f08f8cf3b76a92d23b2b4e699fa2128cb99bf11652cfd60d26cb13`
with labels
`ai-platform.source-revision=9b85c008fb5493fca55b00aaa339af9b399fe030` and
`ai-platform.source_note=p2-resume-checkpoint-lineage`. The 211 smoke verified
API health, container labels, worker step `source_step_id` backfill,
server-owned resume checkpoint materialization, forged resume stripping,
seeded copied-step checkpoint lineage, route-level resume strip helpers, and DB
smoke data cleanup. This does not start automatic retry policy scheduling,
autonomous subagent dispatch, high-risk tool execution, or new sandbox behavior.

### P2 Resume Run Request

Status: write-side P2 resume lifecycle control merged on `main` at
`2ce4af6a333dcdefe2fe848e3abd235e66e28ce8`, deployed to 211, and smoked.
This adds owner-scoped `POST /api/ai/runs/{run_id}/resume` for non-active
source runs with reusable checkpoint output. It creates a copied queued run
through the existing copy-run/context/queue path, records `resume_requested`,
`run_resume_created`, and `run.resume` audit evidence, seeds resume-created
steps with `seeded_from = resume_run`, and updates run-control readiness so the
resume action points to the explicit `/resume` endpoint instead of generic
copy.

Focused local coverage verifies readiness href alignment, owner-scoped resume
creation, reusable checkpoint-output gating, active source rejection,
same-source active child idempotency with `resume_already_active`, resume
events/audit, context snapshot `source = resume_run`, queue payload context
`source = resume_run`, and existing copy/retry controls. This does not start
automatic retry policy scheduling, autonomous subagent dispatch, high-risk tool
execution, or new sandbox behavior.

Local verification recorded `python -m compileall -q app tools scripts`,
focused `tests/test_run_control_routes.py tests/test_source_authority_docs.py`
with `74 passed`, and full pytest with `916 passed, 6 skipped, 2 warnings`.
Inherited-configuration review found no Critical, Important, or Minor issues.

The 211 deployment uses image
`sha256:29e34949c39e770b0b45f5cd4195f4a81ae39456a698b0647fd2e9be7c7c30c4`
for both `ai-platform-api` and `ai-platform-worker`, with
`ai-platform.source-revision=2ce4af6a333dcdefe2fe848e3abd235e66e28ce8` and
`ai-platform.source_note=p2-resume-run-request`. The 211 smoke verified API
health, OpenAPI route exposure, owner resume success, repeated active resume
`409 resume_already_active`, active source `409 active_run`, no-checkpoint
source `409 no_checkpoint_outputs`, other-user `404 run_not_found`, readiness
href `/resume`, resume events/audit, context snapshot `source = resume_run`,
queue payload context `source = resume_run`, checkpoint reuse step seeding,
code hash parity with local files, worker restart, and DB/Redis smoke data
cleanup. Runtime-only Docker rebasing hit Docker `max depth exceeded`, so the
deployment flattened the current healthy API container with `docker export` /
`docker import` before rebuilding the runtime-only image.

### P2 Artifact Tree Operational Projection

Status: merged on `main` at `99ddbc7c114086bb3a4525941cd9dc844ceaebb2`,
deployed to 211, and smoked. This hardens the existing read-only
`ai-platform.run-provenance.v1` projection with deterministic artifact graph
edges, operational artifact tree parent metadata, and artifact-level lineage
gaps for frontend/admin visualization. It keeps `/runs/{run_id}/provenance`
owner-scoped and reuses the existing public projections instead of adding a
parallel endpoint.

The slice adds output-side fail-closed graph-id validation for artifact-derived
`source_step_id`, `checkpoint_id`, and `subagent_id`, so unsafe upstream lineage
cannot enter `artifact_tree`, graph `edges`, or public `lineage`. Local TDD
coverage verifies normal step/checkpoint/subagent/artifact edges, artifact-only
producer gaps, dirty-lineage redaction, and no leakage of raw skill ids,
storage keys, runtime private payloads, command hashes, or runtime paths.
Inherited-configuration review found one Important output-validation issue; it
was fixed with a regression test.

Local verification recorded `python -m compileall -q app tools scripts`,
focused `tests/test_event_playback_routes.py tests/test_source_authority_docs.py`
with `20 passed`, and full pytest with `919 passed, 6 skipped, 2 warnings`.

The 211 deployment uses image
`sha256:3a73c47dbd86408d8a8cb7bb5a887e37f9a9148fab45711348c34ba67e56f81f`
for both `ai-platform-api` and `ai-platform-worker`, with
`ai-platform.source-revision=99ddbc7c114086bb3a4525941cd9dc844ceaebb2` and
`ai-platform.source_note=p2-artifact-tree-operational-projection`. The 211
smoke verified API health, OpenAPI route exposure, source hash parity,
ordinary-user artifact tree parent metadata, deterministic graph edges,
artifact-only producer gaps, raw skill/storage/runtime/private/hash redaction,
clean API/worker logs, and DB/Redis smoke data cleanup. This remains read-only
and does not start autonomous subagent dispatch, high-risk tool execution,
retry scheduling, or new sandbox behavior.

### P2 Multi-Agent Historical Controlled Slices

The following P2 multi-agent sections are retained as legacy accumulated
evidence for controlled internal slices. They do not override the current PRD
v2 route: Claude Agent SDK remains the execution layer, SDK subagents are
governed inside one platform run, and ordinary-user platform-level multi-run
orchestration stays blocked until a future G8 gate is explicitly reopened with
capacity, governance, sandbox, model-gateway, artifact/event, cost, and
rollback evidence. These historical controlled slices do not reopen G8 and do
not represent ordinary-user platform-level multi-run product exposure.

### P2 Multi-Agent Dependency Readiness Projection

Status: merged on `main` at
`ad229abf7ab7e793e6431e6aaad77b644963ff8d` and deployed/smoked on 211.
This extends the existing owner-scoped
`ai-platform.run-control-readiness.v1` projection with read-only multi-agent
dependency readiness for explicitly `execution_mode = multi_agent` runs. It
combines configured `multi_agent_steps` and recorded `run_steps` to expose
public-safe step readiness, dependency statuses, ready/blocked counts, hidden
dependency counts, and a dispatch gate that remains fail-closed with
`runtime_dispatch_not_enabled`.

The slice does not enqueue work, start autonomous subagents, open sandbox/tool
access, or change worker scheduling. It keeps non-multi-agent runs
backward-compatible by returning `multi_agent = null`, only exposes the
public `multi_agent` execution mode enum, and blocks hidden/unsafe dependencies
instead of treating them as absent. Inherited-configuration review found
execution-mode redaction, hidden dependency, and non-multi-agent compatibility
issues; each was fixed with regression tests.

Local verification recorded `python -m compileall -q app tools scripts`,
focused `tests/test_run_control_routes.py tests/test_routes.py
tests/test_source_authority_docs.py` with `144 passed`, and full pytest with
`923 passed, 6 skipped, 2 warnings`.

The 211 deployment uses image
`sha256:a81f94e07aeea4572905f99fab65460c075a1f2fab1a2fdb5f74fb5835d42f29`
for both `ai-platform-api` and `ai-platform-worker`, with
`ai-platform.source-revision=ad229abf7ab7e793e6431e6aaad77b644963ff8d` and
`ai-platform.source_note=p2-multi-agent-readiness-projection`. The 211 smoke
verified API health, OpenAPI route exposure for
`/api/ai/runs/{run_id}/control/readiness`, API/worker label parity, clean
recent API/worker logs, owner-scoped public-safe readiness counts for an
explicitly marked historical multi-agent dependency chain, with no
ordinary-user platform-level multi-run product exposure, fail-closed dispatch reason
`runtime_dispatch_not_enabled`, hidden dependency blocking/redaction for an
unsafe `qa-file-reviewer` dependency, redaction of raw skill/resource/sandbox
runtime fields, and DB/Redis smoke data cleanup. This remains a read-only
control-plane projection and does not open autonomous subagent dispatch,
high-risk tool execution, retry scheduling, or new sandbox behavior.

### P2 Runtime Typed Callback Events

Status: deployed on 211 as a P2 runtime auditability slice via PR #9 and main
commit `963c245a0404fef6109e78107aa179ba10e99ab3`. This extends the internal
sandbox executor callback contract so a trusted runtime callback can include
validated `AgentEvent` entries such as `checkpoint_created`,
`subagent_started`, `subagent_completed`, and `agent_step_completed`. The
runtime callback route persists those events through the existing
`EVENT_STAGE_MAP`, keeping checkpoint, subagent, agent-step, runtime, message,
and control stages consistent with the worker-side event bridge.

This slice keeps the callback endpoint internal and callback-token protected.
It preserves existing `new_message`, `state_patch`, and terminal status
compatibility mapping, keeps `admin_only` events hidden from ordinary users,
and does not enqueue work, start autonomous subagents, open sandbox/tool
access, or change worker scheduling.

211 deployment evidence: API and worker were recreated from image
`sha256:3a119bfb77e07e84e959cef9d3338293fdcfb3b32b5d68f0e1cc411e99f30d47`
with labels `ai-platform.source-branch=main`,
`ai-platform.source-revision=963c245a0404fef6109e78107aa179ba10e99ab3`, and
`ai-platform.source_note=p2-runtime-typed-callback-events`. The 211 smoke
verified `/api/ai/health`, OpenAPI exposure, callback response
`{"accepted": true, "event_count": 5}`, persisted stages
`executor`, `checkpoint`, `subagent`, `agent`, and `browser`, typed event
payload source `executor_callback`, ordinary-user projection containing only
`checkpoint_created`, `subagent_started`, and `agent_step_completed`, hidden
`admin_only` browser snapshot data, and DB smoke cleanup with zero remaining
events for the smoke run.

### P2 Multi-Agent Dispatch Ledger

Status: deployed on 211 as a historical, admin-only platform multi-run ledger
slice behind controls via PR #10 and main commit
`07ef6e77bd6a7c0d3be1393a8aef5c7bb2665c7c`. This adds the admin-only
`POST /api/ai/runs/{run_id}/multi-agent/dispatch/claims` contract for claiming
safe ready multi-agent steps without starting autonomous scheduling.

The slice updates the existing run-control readiness projection so ordinary
owners see dispatch fail closed with `admin_only_dispatch`, while an owner with
an admin role can see a `ready_steps_available` dispatch gate for active runs
that have safe ready steps. Terminal runs stay disabled with
`run_not_dispatchable`, and runs with only unsafe ready dependencies stay
disabled with `no_safe_ready_steps`. The claim route records the claimed step
as `running`, appends a hidden `agent_step_started` event, and appends
`run.multi_agent.dispatch.claim` audit evidence. Unsafe step keys or
dependencies are rejected before writes with `409 unsafe_step_reference`.

Local verification recorded `python -m compileall -q app tools scripts`,
focused `tests/test_run_control_routes.py tests/test_control_plane_contracts.py
tests/test_source_authority_docs.py tests/test_event_playback_routes.py` with
`112 passed`, and full pytest with `937 passed, 6 skipped, 2 warnings`.
Inherited-configuration multi-agent review found one Important issue where
terminal runs could advertise dispatch despite claim rejection; it was fixed
with a regression test, and the follow-up review found no blockers. The review
tool did not expose explicit model or reasoning-effort fields, so this was
recorded as inherited-configuration review rather than an explicit model gate.

The 211 deployment uses image
`sha256:22cd7528cebe3027495813593548e6c1b8c5e0469de425c5d692a7c8f50bc4bb`
for both `ai-platform-api` and `ai-platform-worker`, with
`ai-platform.source-revision=07ef6e77bd6a7c0d3be1393a8aef5c7bb2665c7c` and
`ai-platform.source_note=p2-multi-agent-dispatch-ledger`. The 211 smoke
verified API health, OpenAPI route exposure, API/worker label parity,
owner-scoped ordinary readiness reason `admin_only_dispatch`, owner admin-role
readiness reason `ready_steps_available`, successful `code` step claim,
`run_steps.status = running` with `dispatch_state = claimed`, hidden
`agent_step_started` event visibility to admin and absence from ordinary-user
event projection, `run.multi_agent.dispatch.claim` audit persistence, unsafe
dependency claim `409 unsafe_step_reference`, clean recent API logs, quiet
worker logs, and DB smoke data cleanup with zero remaining smoke runs,
sessions, users, events, and audits.

This does not start an autonomous scheduler, subagent worker process, sandbox
expansion, high-risk tool execution, new DB migration, or new frontend entry.
It only establishes the admin-controlled dispatch ledger required before
future checkpoint/resume/subagent orchestration can be made operational.

### P2 Multi-Agent Dispatch Lease Cleanup

Status: deployed on 211 as the bounded cleanup follow-up to the dispatch
ledger via PR #11 and main commit
`42f2064af1449ba307e7a090c7ec30db3f39ea97`. This adds lease metadata to
admin multi-agent dispatch claims and exposes admin-only
`POST /api/ai/admin/runtime/multi-agent/dispatch/cleanup` to reclaim expired
claimed steps back to `pending` before an autonomous subagent scheduler exists.

The slice keeps lease state in `run_steps.payload_json` and writes
`run.multi_agent.dispatch.expire` audit rows. Cleanup is same-tenant,
admin-only, uses Python-side ISO timestamp parsing instead of SQL casts, skips
malformed/future leases, scans past unreclaimable candidates until the requested
number of actual expired claims is reclaimed or candidates are exhausted, uses
`skip locked`, and clears stale `dispatch_expired_at` when a step is claimed
again.

Local verification recorded the review RED tests (`3 failed` before safe
timestamp parsing and stale marker cleanup), second-review RED tests
(`3 failed` before batch scanning and `skip locked`), final focused coverage
with `194 passed`, `python -m compileall -q app tools scripts` exit 0,
`git diff --check` exit 0 with only an `app/settings.py` CRLF/LF warning, and
full pytest with `942 passed, 6 skipped, 2 warnings`. Inherited-configuration
review found malformed timestamp, parent-run-status, stale
`dispatch_expired_at`, candidate-window, and `skip locked` issues; all were
fixed with regression tests, and final review reported no Critical, Important,
or Minor findings. The review tool did not expose explicit model or
reasoning-effort fields, so this remains recorded as inherited-configuration
review.

The 211 source marker is
`42f2064af1449ba307e7a090c7ec30db3f39ea97`. The deployed image for both
`ai-platform-api` and `ai-platform-worker` is
`sha256:32d0c21156c67a28041814f3b64bf497fceec5da831cbe05875d7bb56f4728d6`,
with labels
`ai-platform.source-revision=42f2064af1449ba307e7a090c7ec30db3f39ea97` and
`ai-platform.source_note=p2-multi-agent-dispatch-lease-cleanup`. Remote Docker
build was attempted on 211 but the legacy builder failed because the host could
not fetch `setuptools>=40.8.0`; because this slice has no dependency changes,
deployment used the previous healthy image as a base, copied the new source
tree into `/app`, restored executable entrypoint permissions and image
entrypoint/CMD metadata, committed the image with source labels, and recreated
API/worker with compose.

The 211 smoke verified `/api/ai/health`, OpenAPI exposure for both
`/api/ai/admin/runtime/multi-agent/dispatch/cleanup` and
`/api/ai/runs/{run_id}/multi-agent/dispatch/claims`, ordinary-user cleanup
`403 not_ai_admin`, admin cleanup `200` with `expired_count = 1`, reclaimed
step `status = pending` and `dispatch_state = expired`, audit action
`run.multi_agent.dispatch.expire`, API/worker label parity, clean runtime
startup logs, and smoke tenant DB cleanup with zero remaining rows in
`audit_logs`, `run_steps`, `runs`, `sessions`, `agents`, `users`,
`workspaces`, and `tenants`.

This does not start an autonomous scheduler, child run handoff, queue enqueue,
subagent worker process, sandbox/tool privilege expansion, frontend entry, or
DB migration.

### P2 Multi-Agent Controlled Child Run Handoff

Status: deployed on 211 as the controlled child-run bridge after the dispatch
ledger and lease cleanup via PR #12 and main commit
`ea33e7e4016565f166f6462de5a610f67bdcbf65`. This adds the admin-only
`POST /api/ai/runs/{run_id}/multi-agent/dispatch/claims/{dispatch_id}/handoff`
contract for turning one active claimed dispatch step into one queued child run.

The slice keeps claim and handoff separate. The handoff requires an active
parent run, a matching `dispatch_state = claimed` step with an unexpired lease,
and rejects duplicate or inactive claims before enqueue. The child run keeps the
parent owner, session, workspace, agent, and skill identity instead of using the
admin caller identity. Server code rebuilds `resume` and
`multi_agent_dispatch` from validated step rows, strips user-controlled values,
updates the parent step to `dispatch_state = handed_off`, writes hidden parent
and visible child events, and records `run.multi_agent.dispatch.handoff` audit
evidence.

Local verification recorded RED route tests (`4 failed`) and RED repository
tests (`3 failed`) before implementation, final route handoff tests with
`4 passed`, repository handoff tests with `3 passed`, focused route/repository
source-authority coverage with `177 passed`, `python -m compileall -q app tools
scripts` exit 0, `git diff --check` exit 0, and full pytest with `949 passed,
6 skipped, 2 warnings`. Inherited-configuration review found no Critical or
Important issues; one Minor documentation progress issue was fixed before PR.
The review tool did not expose explicit model or reasoning-effort fields, so
this remains recorded as inherited-configuration review rather than an explicit
model gate.

The 211 deployment uses image
`sha256:22d253246e9e09eff7e58589a823224b90429e88624336211afe62e8c53c864d`
for both `ai-platform-api` and `ai-platform-worker`, with
`ai-platform.source-revision=ea33e7e4016565f166f6462de5a610f67bdcbf65`,
`ai-platform.source-branch=main`, and
`ai-platform.source_note=p2-multi-agent-child-handoff`. Remote compile passed
with `python3 -m compileall -q app tools scripts`, and API/worker were
recreated with compose using the existing 211 runtime `.env` path without
printing or copying secret values.

The 211 smoke verified `/api/ai/health`, OpenAPI exposure via `/openapi.json`,
ordinary-user handoff `403 admin_required`, admin claim `200` with
`ai-platform.multi-agent-dispatch-claim.v1`, admin handoff `200` with
`ai-platform.multi-agent-dispatch-handoff.v1`, child run `queued` with
`copied_from_run_id` pointing to the parent, parent owner/session preservation,
parent step `dispatch_state = handed_off` with matching child run id, context
and queue source `multi_agent_dispatch_handoff`, server-owned dispatch metadata,
stripping of forged user `resume` and `multi_agent_dispatch`, dependency resume
materialization from the succeeded `plan` step, `run.multi_agent.dispatch.handoff`
audit persistence, Redis queued-payload cleanup, typed Redis scan with zero
smoke tenant matches, DB cleanup with zero remaining smoke rows, API/worker
label parity, API health, and worker recovery after the smoke window.

This does not start an autonomous scheduler, polling subagent dispatcher, new
worker process, sandbox/tool privilege expansion, frontend entry, or DB
migration. It only establishes a bounded admin-controlled handoff primitive
needed before broader resume/cancel/retry and multi-agent orchestration.

### P2 Multi-Agent Child Completion Reconciliation

Status: deployed on 211 as the terminal child-run reconciliation follow-up to
controlled handoff via PR #13 and main commit
`5c69397b913649d664321265a8265abe27103068`. This adds internal repository and
worker reconciliation so a server-owned handed-off child run that reaches
`succeeded`, `failed`, or `cancelled` updates the parent dispatch step terminal
state, dependency readiness, checkpoint lineage, hidden event evidence, and
audit evidence.

The slice validates the child run from persisted DB state before mutating the
parent step: same tenant, child `copied_from_run_id`, persisted terminal child
status, server-owned `multi_agent_dispatch`, matching parent step id,
`dispatch_id`, `dispatch_child_run_id`, and `dispatch_state = handed_off`.
Success writes public-safe `output`, `checkpoint_id`, and `source_step_id`.
Failure and cancellation write public-safe error metadata with unsafe
`error_code` fallback values such as `child_run_failed` or
`child_run_cancelled`. Stale or already reconciled parent steps return without
event or audit side effects.

Local verification recorded repository reconciliation tests with `3 passed`,
worker terminal hook tests with `6 passed`, related regression coverage with
`237 passed`, review regression tests with `3 passed`, `python -m compileall -q
app tools scripts` exit 0, `git diff --check` exit 0, and full pytest with
`961 passed, 6 skipped, 2 warnings`. Inherited-configuration review first found
two Important issues around persisted child terminal status validation and
unsafe parent-step `error_code` copying; both were fixed with RED regression
tests and follow-up review reported no Critical, Important, or Minor findings.
The review tool did not expose explicit model or reasoning-effort fields, so
this remains recorded as inherited-configuration review rather than an explicit
model gate.

The 211 source marker is
`5c69397b913649d664321265a8265abe27103068`. The deployed image for both
`ai-platform-api` and `ai-platform-worker` is
`sha256:669fb12bc7242775cfceee0768f47793472c8da8a78de7ad857a3462a0e6a640`,
with labels
`ai-platform.source-revision=5c69397b913649d664321265a8265abe27103068`,
`ai-platform.source-branch=main`, and
`ai-platform.source_note=p2-multi-agent-child-reconciliation`. Remote source
compile passed with `python3 -m compileall -q app tools scripts`; container
compile passed with `python -m compileall -q app tools scripts`. Because this
slice has no dependency changes, 211 deployment used the current healthy image
as the base, copied the synced runtime source into a new runtime-only image,
restored executable entrypoint permissions, and recreated API/worker with the
repo-local compose file while reading the existing 211 runtime `.env` path
without printing or copying secret values.

The 211 smoke verified `/api/ai/health`, API/worker label parity, host and
container SHA256 parity for `app/repositories.py` and `app/worker.py`, successful
child reconciliation to parent step `succeeded/completed` with safe
`checkpoint_id` and `source_step_id`, failed child reconciliation to parent step
`failed/failed` with unsafe error code fallback `child_run_failed`, two hidden
`multi_agent_dispatch_reconciled` events, two
`run.multi_agent.dispatch.reconcile` audit rows, no private marker leakage in
parent payload/event/audit evidence, clean recent API/worker logs, and DB smoke
cleanup with zero remaining rows in `audit_logs`, `run_events`, `run_steps`,
`runs`, `sessions`, `agents`, `users`, `workspaces`, and `tenants`.

This does not start an autonomous scheduler, polling subagent dispatcher, new
worker process, sandbox/tool privilege expansion, frontend entry, or DB
migration. It only closes the terminal-state reconciliation gap after controlled
child handoff so future resume/cancel/retry and multi-agent orchestration can
read consistent parent dispatch state.

### P2 Multi-Agent Parent Cancel Propagation

Status: implemented locally as the parent-cancel propagation follow-up to
controlled child handoff and terminal child reconciliation. The slice adds
same-tenant, server-owned child cancellation when an owner or admin cancels a
multi-agent parent run. Eligible children must be active `queued` or `running`
runs with `copied_from_run_id` pointing to the parent, server-owned
`multi_agent_dispatch.parent_run_id`, and a matching handed-off parent step
whose payload names the same child run id.

Queued child runs are marked `cancelled`, their open steps are cancelled, and
their queued Redis payloads are removed by the route after the DB transaction
commits. Running child runs keep `running` status with
`cancel_requested_at/by` set so workers observe the child run's own cancel
flag. Active parent or child sandbox leases returned by DB state are stopped
and released by route cleanup grouped by each lease's real `run_id`; admin
cleanup preserves `requested_by_role = admin`. Queue cleanup and sandbox
cleanup are both attempted before the route reports failure: sandbox failures
return `sandbox_runtime_cleanup_failed`, while queue-only cleanup failures
return `queue_cleanup_failed`.

Local verification recorded RED/GREEN coverage for server-owned queued and
running child propagation, ordinary copied-run exclusion, owner/admin queued
child Redis removal, owner/admin child sandbox release by child run id, sandbox
failure after queued child cleanup, queue cleanup failure before child sandbox
cleanup, and event/audit payload redaction for raw command, runtime path,
storage key, and secret-like values. Focused route verification passed with
`109 passed`; `python -m compileall -q app tools scripts` exited 0;
`git diff --check` exited 0; full local pytest passed with `969 passed, 6
skipped, 2 warnings`. Inherited-configuration multi-agent review first found
two Important ordering issues around queued child Redis cleanup versus sandbox
cleanup. Both were fixed with RED regression tests, and follow-up review found
no Critical or Important issues. The review tool did not expose explicit model
or reasoning-effort fields, so this remains recorded as inherited-configuration
review rather than an explicit model gate.

Status: deployed on 211 through PR #14 and main commit
`037ac4d9166cc31c420560e1a584f5a429bb46ac`. The deployed image for both
`ai-platform-api` and `ai-platform-worker` is
`sha256:8c4ae14528d6b139954e0e02f8d8e56de4d2e8956197e25c655586a3aca1a5d3`,
with labels
`ai-platform.source-revision=037ac4d9166cc31c420560e1a584f5a429bb46ac`,
`ai-platform.source-branch=main`, and
`ai-platform.source_note=p2-multi-agent-parent-cancel-propagation`. Remote
source compile passed with `python3 -m compileall -q app tools scripts`;
container compile passed for both API and worker with
`python -m compileall -q app tools scripts`. The runtime-only compose rollout
used the repo-local compose file and the existing 211 runtime `.env` path
without printing or copying secret values. Because 211 `sudo` does not preserve
the leading shell environment assignment, the effective compose command used
`sudo -n env AI_PLATFORM_IMAGE=ai-platform:037ac4d9166c docker compose ...`.

The 211 smoke verified API and frontend proxy `/api/ai/health`, API/worker label
parity, owner cancel and admin cancel through the live HTTP routes, same-tenant
server-owned queued child runs becoming `cancelled`, running child runs keeping
`running` with `cancel_requested_at` set, forged copied child runs remaining
untouched, queued Redis payload removal by the route, active fake sandbox lease
release after cleanup, open child step cancellation, `requested_by_role` evidence
for both owner and admin propagation, no private marker / `private_payload` /
`storage_key` / secret-like value leakage in event or audit payloads, clean
recent API/worker logs, `queued = 0`, `processing = 0`, and smoke tenant cleanup
with zero remaining tenant rows. The default 211 compose still uses
`SANDBOX_CONTAINER_PROVIDER=fake` and does not mount the Docker socket, so this
smoke did not expand sandbox/tool privileges or claim Docker-provider container
stop evidence.

This does not start an autonomous scheduler, polling subagent dispatcher, new
worker process, sandbox/tool privilege expansion, frontend entry, or DB
migration.

### P2 Multi-Agent Parent Terminal Rollup

Status: deployed on 211 as the parent lifecycle closure follow-up to child
handoff, child terminal reconciliation, and parent cancel propagation via main
commit `01291efcf0444e3a885225d9e8e11d6863667684`.

This slice finalizes a same-tenant multi-agent parent run once all persisted
server-owned parent steps are terminal and no active handed-off child run
remains. Parent `result_json`, hidden `multi_agent_parent_finalized` event, and
audit payloads expose only public-safe step summaries and counts. Child
reconciliation invokes the rollup only after a parent-step update succeeds, so
stale or forged child relationships do not finalize the parent run.

Local TDD verification recorded RED failures for the new event taxonomy,
missing repository helper, and missing reconciliation hook, then GREEN coverage
for successful parent finalization, failed/cancelled rollup status, active-child
and non-multi-agent blocking, open dispatch-state blocking, missing configured
step blocking, ordinary copied-run exclusion, skip-locked parent selection,
stale reconciliation exclusion, and public multi-agent snapshot redaction. An
inherited-configuration multi-agent review identified parent/child lock-order
risk, incomplete configured-step identity checks, and copied-run exclusion gaps;
the follow-up fixes now use skip-locked parent selection, reject copied parent
runs, and require configured step keys to be unique and persisted. A follow-up
inherited-configuration review identified a remaining cancel/reconciliation
race where `skip locked` could skip the parent while cancel held the row lock;
owner and admin cancel paths now perform an in-transaction parent finalize pass
after cancel propagation, and tests cover that compensation plus duplicate /
malformed configured step blocking. A final inherited-configuration review then
identified a sibling child reconciliation race; the worker now performs a
bounded post-commit parent rollup retry in a fresh transaction after successful
child terminal reconciliation, including early terminal worker failures that
reconcile a child before returning. A final inherited-configuration review found
that exception-based early terminal exits could roll back the worker transaction
before the nominal post-commit retry; early terminal paths now return normally
from the transaction block so the retry runs after commit. The same review pass
also found hidden active children could be missed through a parent-step join and
hash-like fingerprint values could survive in allowed parent step fields; the
active-child check now uses the child run's server-owned dispatch metadata
directly, and parent step summaries drop hash-like values even under otherwise
allowed keys. Present-but-non-list `multi_agent_steps` now fail closed, and
cancel responses adopt the terminal parent status when the in-transaction
compensation finalizes the parent. Final inherited-configuration re-review
reported no Critical or Important findings; the only Minor note was that older
multi-agent dispatch event taxonomy cleanup remains a separate non-blocking
follow-up. Focused verification passed with `18 passed`; worker terminal focused
verification passed with `12 passed`; affected suites passed with `272 passed`;
full local pytest passed with
`983 passed, 6 skipped, 2 warnings`; `python -m compileall -q app tools scripts`
and `git diff --check` both exited 0. `git diff --check` only emitted
CRLF-to-LF normalization warnings for three touched files. Verification used
fresh `.pytest-tmp` child basetemp directories.

The 211 deployment uses image
`sha256:bd0b8ea243bbf35c39e8ccada017bdf8b676d836a2e2db6a84520f51a48f33e3`
for both `ai-platform-api` and `ai-platform-worker`, with
`ai-platform.source-revision=01291efcf0444e3a885225d9e8e11d6863667684` and
`ai-platform.source_note=p2-multi-agent-parent-terminal-rollup`. The 211 source
markers match the same revision and note. The 211 smoke verified API and thin
shell health, API/worker label parity, a live parent run rolling up to
`succeeded` after controlled child handoff and terminal reconciliation, parent
counts `total = 2`, `succeeded = 2`, hidden
`multi_agent_parent_finalized` event persistence, `run.multi_agent.parent.finalize`
audit persistence, ordinary-user event projection excluding the hidden parent
finalized event, redaction of private payload, storage key, runtime path, and
command fingerprint markers from parent result/event/audit/public projections,
clean recent API/worker logs, and DB smoke data cleanup with zero remaining
rows in `audit_logs`, `run_events`, `run_steps`, `runs`, `sessions`, and
`users`.

This does not start an autonomous scheduler, polling subagent dispatcher, new
worker process, sandbox/tool privilege expansion, frontend entry, or DB
migration.

### P2 Multi-Agent Dispatch Tick

Status: deployed on 211 as the bounded runtime orchestration follow-up to
dispatch claim, child handoff, child terminal reconciliation, parent cancel
propagation, and parent terminal rollup via main commit
`c35c7f0a891062e8f636ba9a834a16ca9e3830f6`.

This slice adds admin-only
`POST /api/ai/runs/{run_id}/multi-agent/dispatch/tick` for one safe ready
multi-agent parent step. The route locks the parent run, reads current
readiness, skips non-ready configured or recorded steps, chooses the first ready
step that passes the existing safe dispatch claim validation, records the claim
through a conditional insert/update that cannot overwrite a concurrent
non-pending dispatch state, immediately creates the server-owned child
run through the existing handoff path, prepares the child queue payload through
the existing copied-run context snapshot path, commits DB state, and then
enqueues the child run. The response contract is
`ai-platform.multi-agent-dispatch-tick.v1` and returns only operational ids,
queue insight, and claim/handoff event/audit ids.

Local TDD coverage verifies ordinary-user `403 admin_required`, no-ready
`409 no_ready_steps`, unsafe-ready `409 no_safe_ready_steps`, and successful
claim + handoff + enqueue sequencing for the next safe ready step. Review-driven
coverage also verifies unsafe configured ready keys including forbidden aliases,
private payload terms, hash-like values, invalid path-like ids, and stale
non-pending claim races. Focused verification for dispatch tick plus existing
claim/handoff routes passed with `15 passed`; affected route/source-authority
tests passed with `194 passed`; full local pytest passed with
`991 passed, 6 skipped, 2 warnings`; `python -m compileall -q app tools scripts`
and `git diff --check` both exited 0.

The 211 deployment uses image
`sha256:93d40379aadf0276a6690eaf010541a6da78b6c889a7329a12b6d82d825d99a1`
for both `ai-platform-api` and `ai-platform-worker`, tagged
`ai-platform:c35c7f0a8910`, with
`ai-platform.source-revision=c35c7f0a891062e8f636ba9a834a16ca9e3830f6` and
`ai-platform.source_note=p2-multi-agent-dispatch-tick`. 211 verification
confirmed `GET /api/ai/health` returned `{"status":"ok"}`, OpenAPI exposed
`POST /api/ai/runs/{run_id}/multi-agent/dispatch/tick`, and both API/worker
containers were running the new tag.

The 211 smoke created a temporary multi-agent parent with one succeeded
dependency and one ready `code` step, called the tick route as admin, and
verified the v1 response contract, queued child run, Redis queue payload,
handoff state on the parent step, claim/handoff run events, claim/handoff audit
rows, ordinary-user projection hiding hidden control events, API/worker logs
without recent error lines, and cleanup with zero remaining smoke DB rows or
Redis queue payloads.

This does not start an autonomous scheduler, polling subagent dispatcher, new
worker process, sandbox/tool privilege expansion, frontend entry, or DB
migration.

### P2 Multi-Agent Worker Dispatcher

Status: deployed on 211 as the bounded worker-side follow-up to admin dispatch
tick via main commit `92bef5c6e196bcbe4bc563e3ad50d1d96a629d7d`.

This slice adds a disabled-by-default worker maintenance dispatcher that can
advance safe ready steps for server-marked top-level multi-agent parent runs.
Workers park top-level multi-agent parents with a server-owned top-level
`input_json.multi_agent_dispatch.orchestration_state = awaiting_dispatch`
marker when the feature flag is enabled. The worker maintenance pass then runs
after sandbox and memory cleanup but before queue lease reclaim, scans only
same-tenant running top-level parents with that marker, reuses the existing
readiness, safe claim, controlled child handoff, copied-run queue preparation,
and Redis enqueue path, and bounds each pass by interval and limit settings.

The dispatcher fails closed when disabled, when interval or limit settings are
malformed or non-finite, when a candidate has no safe ready step, or when a
claim race has already moved the step out of `pending`. Redis enqueue failure
after a committed child handoff is compensated in the database by failing the
child run, resetting the parent step to `pending`, and writing hidden event and
audit evidence instead of crashing the worker loop.

Ordinary-user public projections strip user-controlled `resume` and
`multi_agent_dispatch` metadata plus dispatch claim/handoff control fields from
run, event, step, and chat message surfaces. The committed deploy defaults keep
the worker dispatcher disabled unless a controlled runtime explicitly enables
`MULTI_AGENT_DISPATCH_WORKER_ENABLED`.

Inherited-configuration multi-agent review reported no Critical, Important, or
Minor findings after the second review-fix pass. Fresh local verification for
the final source passed with `python -m compileall -q app tools scripts`,
affected dispatcher/repository/worker tests at `179 passed`, and full pytest at
`1035 passed, 6 skipped, 2 warnings`.

The first 211 smoke exposed a real schema compatibility bug: the dispatcher
candidate and park-marker SQL referenced `runs.updated_at`, but the current
schema and 211 runtime `runs` table only have `queued_at` and `created_at`.
That was fixed in `92bef5c6e196bcbe4bc563e3ad50d1d96a629d7d` with a regression
test that fails if the dispatcher SQL references `runs.updated_at`.

The 211 deployment uses image
`sha256:31847f637656f0456adcd92a965454cbd05f128ed2e3434cada50162d3af7e9a`
for both `ai-platform-api` and `ai-platform-worker`, tagged
`ai-platform:92bef5c`, with
`ai-platform.source-revision=92bef5c6e196bcbe4bc563e3ad50d1d96a629d7d` and
`ai-platform.source_note=p2-multi-agent-worker-dispatcher`. The 211 source
markers match the same revision and note. Because the final fix changed only
runtime source and not dependencies, the final image was rebuilt as a runtime
rebase from the earlier dispatcher image by replacing `/app/app`; the container
entrypoint, dependency layer, and skills layer remained from the already-built
base image.

The 211 smoke temporarily enabled the dispatcher only for a one-off container
process, created a temporary parked multi-agent parent with a succeeded `plan`
dependency and ready `code` step, dispatched the ready step, verified the child
run was queued, removed one Redis queue payload before worker consumption,
verified claim and handoff audit rows, verified ordinary step/event projections
did not expose dispatch control fields, and cleaned all smoke DB rows to zero.
Final health returned `{"status":"ok"}`, the worker could import
`app.multi_agent_dispatcher`, the default deployed dispatcher flag remained
`False`, and recent API/worker logs had no error markers.

This does not add a new public frontend entry, expose executor private payload,
open sandbox/tool privilege, add a new worker process, or introduce a DB
migration.

### P2 Multi-Agent Event Taxonomy Cleanup

Status: deployed on 211 as the event-contract cleanup follow-up to the
multi-agent worker dispatcher via main commit
`ca072c4c9de6ae2cd2e6c3d796adcacc05ceeb37`.

This slice closes the non-blocking event taxonomy follow-up left after parent
terminal rollup and worker dispatcher. It preserves persisted event names while
adding the deployed multi-agent runtime events to `STANDARD_EVENT_TYPES`:
`multi_agent_dispatch_handoff`, `run_multi_agent_child_created`,
`multi_agent_dispatch_enqueue_failed`, `multi_agent_dispatch_reconciled`, and
`multi_agent_dispatch_parent_parked`. Ordinary-user projection maps the visible
child-created event to `run_child_created`; admin projections keep raw
operational event names.

Review-driven redaction coverage also strips root-level `parent_run_id` from
ordinary-user payloads, matching existing dispatch id, parent step id, copied
parent run id, and server-owned dispatch metadata filtering. Hidden dispatch
control events remain hidden.

Local TDD verification recorded RED failures for the missing taxonomy entries
and missing ordinary-user event alias, then GREEN coverage for the event
contract and public projection. Inherited-configuration review found two
Important gaps: missing `multi_agent_dispatch_parent_parked` taxonomy coverage
and potential root-level `parent_run_id` leakage in visible child-created
payloads. Both were fixed with RED regression tests. Follow-up
inherited-configuration review found no Critical or Important issues; the only
Minor plan-staging note was fixed.

Focused verification passed with `3 passed` for the review-fix contract tests,
`19 passed` for control-plane/projection coverage, and `13 passed` for
worker/dispatch focused coverage. Pre-commit verification passed with
`python -m compileall -q app tools scripts`, full local pytest at
`1036 passed, 6 skipped, 2 warnings`, and `git diff --check` exit 0. The first
full pytest attempt used `.pytest-tmp` directly and failed during setup because
Windows could not remove stale unreadable pytest temp children; the successful
full run used a fresh child directory under `.pytest-tmp`.

The 211 deployment uses image
`sha256:10cf326e20d0c9313d924acbecfe6335a60d50ccac1da93c627b06aa3d4ff598` for
both `ai-platform-api` and `ai-platform-worker`, tagged
`ai-platform:ca072c4`, with
`ai-platform.source-revision=ca072c4c9de6ae2cd2e6c3d796adcacc05ceeb37` and
`ai-platform.source_note=p2-multi-agent-event-taxonomy-cleanup`. The 211 source
markers match the same revision and note. Because this slice changed only
runtime source and docs, deployment used a runtime-only rebase from the
previous healthy image and copied `/app/app`; no dependency layer, sandbox
provider, or compose default changed.

The 211 smoke verified backend health and frontend proxy health returned
`{"status":"ok"}`, API and worker label parity, in-container `app` compile for
both containers, worker import of `app.multi_agent_dispatcher`, and the
container event taxonomy reporting `True` for
`multi_agent_dispatch_handoff`, `run_multi_agent_child_created`,
`multi_agent_dispatch_enqueue_failed`, `multi_agent_dispatch_reconciled`,
`multi_agent_dispatch_parent_parked`, and `multi_agent_parent_finalized`. The
in-container projection smoke verified ordinary users see
`run_child_created`, admin users keep `run_multi_agent_child_created`, and
ordinary-user payloads strip dispatch id, copied parent run id, root
`parent_run_id`, and parent step id. Recent API/worker logs had no error
markers.

This does not rename persisted events, add a DB migration, add a frontend
entry, expose executor private payload, open sandbox/tool privilege, add a new
worker process, or change dispatcher enablement defaults.

## 禁止项

- 不得新增与当前主链路并行的本地前端入口。
- 不让任何非 `ai-platform` 后端或外部项目成为平台事实源。
- 不因为 memory 表存在就默认开放跨用户或跨 session 记忆。
- 不因为 sandbox lease API 存在就默认允许生产高风险 Docker 任务。
- 不允许未授权写工具产生外部副作用。
- 不在 PRD 或路线图中保留非当前主链路、临时服务或短期执行证据。
- 不在 PRD 或路线图中维护已退出范围对象的名称清单。
