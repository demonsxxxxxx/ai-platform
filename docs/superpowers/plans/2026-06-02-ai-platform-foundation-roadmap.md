# ai-platform Foundation Roadmap

日期：2026-06-02

## 文档权威

本路线图只服务于 `docs/superpowers/specs/2026-05-29-ai-platform-final-product-prd.md`。

后续 ai-platform 计划只允许引用当前 PRD、当前路线图、仓库 guardrails（`docs/agent-rules/ai-platform-guardrails.md`）、真实代码和当次 211 运行证据。任何非当前主链路重新进入范围，必须先更新 PRD、本路线图和 guardrails。

本路线图不保存非当前主链路、短期执行证据或临时服务说明。

本路线图不维护已退出范围对象的名称清单。后续执行只按本文“当前主链路”和 P0 交付门槛推进；未列入当前主链路的入口、服务、端口、页面或候选方案，不作为计划依据。

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

真实 Long Task / Multi-Agent Runtime 是最终目标，但不作为当前基础切片的第一落点。

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
- Artifact/Office preview。
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

## 后续顺序

1. Foundation hardening + Agent Frontend V1。
2. Long Task / Multi-Agent Runtime。
3. Observability / Quality。

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
cleanup and configurable redaction policy remain follow-up hardening gates for
the full Memory / Context PRD gate; they are not claimed complete and do not
permit cross-session long-term memory.

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

## 禁止项

- 不得新增与当前主链路并行的本地前端入口。
- 不让任何非 `ai-platform` 后端或外部项目成为平台事实源。
- 不因为 memory 表存在就默认开放跨用户或跨 session 记忆。
- 不因为 sandbox lease API 存在就默认允许生产高风险 Docker 任务。
- 不允许未授权写工具产生外部副作用。
- 不在 PRD 或路线图中保留非当前主链路、临时服务或短期执行证据。
- 不在 PRD 或路线图中维护已退出范围对象的名称清单。
