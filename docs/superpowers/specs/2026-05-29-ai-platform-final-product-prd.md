# PRD: ai-platform 自研企业 Agent 平台最终总纲

## 0. 文档权威

本文件是 `ai-platform` 当前产品方向的唯一总纲 PRD。

后续实现、评审和汇报只能同时参考：

- 本 PRD。
- 当前路线图：`docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`。
- 仓库 guardrails。
- 当前真实代码。
- 当次 211 运行证据。

本 PRD 不保存非当前主链路、会话过程记录、短期执行证据或临时进程说明。上述内容只能进入当次执行报告，不得反向成为产品需求。

本 PRD 只描述当前产品目标、当前主链路和当前交付门禁。已经退出范围的服务、入口、端口、页面或会话候选方案，不以名称保留，也不作为负面清单反复出现。后续 Agent 判断范围时，只检查本 PRD 第 2 节、当前路线图、真实代码和当次 211 证据。

## 1. 产品目标

`ai-platform` 的目标是公司级自研 Agent 平台，不是接入或包装某一个开源项目。

核心原则：

- `ai-platform` 是唯一企业控制面和事实源。
- 外部项目只按模块吸收，不接管平台主数据。
- 普通用户使用 Agent，不直接选择 raw Skill。
- Admin / Developer 管理 Agent、Skills、MCP、模型、资源、审计和质量。
- 执行层可插拔，当前优先 Claude Agent SDK，后续可接 AgentScope、OpenAI Agents SDK、DeerFlow-like runtime。

一句话目标：自研 `ai-platform` 做企业底座，吸收成熟项目的优秀模块，去掉不适合企业多租户治理的部分。

## 2. 当前主链路

当前事实源：

- 本地代码：`webUI/services/ai-platform`
- 211 代码：`/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform`
- 后端 API：`ai-platform-api` 暴露 `8020`
- 前端入口：`http://10.56.0.211:18001/`

当前目标容器：

- `ai-platform-api`
- `ai-platform-worker`
- `ai-platform-postgres`
- `ai-platform-redis`
- `ai-platform-minio`

当前数据库事实源必须覆盖：

- tenant / workspace / user / role
- agent / session / message / run / run_step / run_event
- file / artifact / download authorization
- skill registry / tenant skill policy / skill snapshot
- MCP registry / tool policy / confirmation snapshot
- memory record / context snapshot / redaction log
- queue / lease / heartbeat / retry / dead-letter / cancel
- audit event / trace / quality metrics

任何没有列入当前主链路的服务、目录、端口或进程，都不是后续实现依据。

## 3. 外部项目吸收边界

| 项目 | 可吸收 | 不吸收 | 结论 |
| --- | --- | --- | --- |
| Poco Claw | 团队空间、run drawer、playback、artifact 产品形态、Claude SDK client pool、container pool、session queue | 企业事实源、未治理的多租户模型、Docker socket 安全半径、single-user fallback | 产品形态和执行工程参考 |
| AgentScope | Agent Service、Agent Skills、Workspace、Memory/Context、Permission allow/deny/ask、SSE replay、tool schema | 认证事实源、企业 RBAC、artifact ACL、审计主库、最终 Memory policy | runtime 和 Agent Skills 重点参考 |
| DeerFlow | 长任务 research/report、middleware chain、plan mode、subagents、artifact 输出链、MCP/search 工具链 | 多租户控制面、企业合规事实源、无治理 shell/MCP | 复杂任务 runtime 参考 |
| new-api | 统一模型网关、OpenAI-compatible 模型入口、模型路由 | 前端暴露模型 Key、每个应用各自接模型 | 模型网关层 |

外部项目只能进入 `agent frontend UX pattern`、`executor adapter`、`runtime pattern`、`skill package pattern`、`tool/memory design reference`，不能进入事实源核心。

## 4. 总体架构

```text
Enterprise User
  -> Agent Frontend
     -> ai-platform API
        -> Auth / Tenant / RBAC
        -> Agent / Session / Run
        -> Skill Registry / Skill Policy / Skill Staging
        -> MCP / Tool Policy
        -> ContextBuilder / Memory Policy
        -> Queue / Scheduler / Resource Manager
        -> Executor Adapter
           -> Claude Agent SDK
           -> AgentScope Adapter
           -> DeerFlow-like Runtime Adapter
           -> RAGFlow Adapter
           -> HTTP LLM Workflow
        -> Artifact Store
        -> Audit / Events / Observability
```

执行器只消费平台准备好的 payload 和 workspace，不反向定义平台主数据。

## 5. 用户角色

### 5.1 普通用户

- 进入当前发布入口后直接使用 Agent。
- 可以发起聊天、上传文件、查看 artifact、下载授权产物。
- 不直接管理 raw Skill、MCP server、executor、sandbox 或模型 Key。
- 高风险工具、写入工具、歧义文件任务必须经过确认卡。

### 5.2 Admin / Developer

- 管理 Agent、Skill、Skill 版本、tenant policy、MCP/tool policy。
- 查看 run、event、artifact、memory、sandbox、queue、audit 的运营投影。
- 执行灰度、回滚、禁用、审计和质量评估。
- 不能绕过 tenant/workspace/user 边界读取私有 payload。

## 6. 模块合同

### 6.1 企业身份与租户控制面

目标合同：

- 使用现有公司账号密码/VDI 认证入口。
- 所有 session/run/file/artifact 绑定 tenant/user/workspace。
- 普通用户默认 `user` 角色。
- Admin / Developer 权限来自企业身份或平台配置。

门禁：

- 登录、刷新、当前用户、principal header、Admin 权限判断可验证。
- tenant/workspace/user/role 合同稳定。
- Admin 页面能说明权限来源。

禁止：

- 前端伪造权限。
- 跨 tenant 读取 run、file、artifact、memory 或 audit。

### 6.2 企业 Agent 前端与用户体验

目标合同：

- 普通用户打开当前发布入口即可使用企业 Agent。
- 企业 Agent 前端只调用 `ai-platform` API。
- `/api/*` 由当前发布入口代理到 `ai-platform-api:8020`。
- 聊天、会话、流式、文件、artifact、permission card、run drawer/playback 都以平台公开投影为准。
- 登录只保留公司账号入口。

当前前端合同：

- `/api/auth/login`
- `/api/auth/me`
- `/api/auth/refresh`
- `/api/chat/stream`
- `/api/sessions/*`
- `/api/upload/*`
- `/api/ai/runs/{run_id}/playback`
- `/api/ai/runs/{run_id}/tool-permissions/{request_id}/decision`
- `/api/ai/artifacts/{artifact_id}/download`
- `/api/ai/artifacts/{artifact_id}/preview`

门禁：

- 不暴露 raw payload、manifest、storage key、runtime path、`.claude/skills`、`work_dir`、command fingerprint 或 executor 私有 payload。
- Artifact/Office preview 必须有 allowlist。
- 文件任务必须完成浏览器 UI 验收。
- 企业 Agent 前端必须纳入正式发布编排。

禁止：

- 不得新增与当前发布入口并行的本地前端入口。
- 让非 `ai-platform` 后端成为事实源。
- 前端保存或展示 worker 路径、storage key 或 executor 私有 payload。

### 6.3 Chat / Intent Routing / Confirmation

目标合同：

- 用户只说自然语言。
- 系统识别普通 chat、文件审核、翻译、SOP、复杂 Agent 任务。
- 普通 chat 不确认。
- 歧义文件任务和高风险工具调用必须确认后执行。

门禁：

- 规则路由和 LLM router 都只输出结构化 intent。
- confirmation card 来源于真实 planning / dry-run。
- routing、confirmation、decision 都写入 run event。

禁止：

- 让普通用户手动选择 raw Skill 代替平台路由。
- 未确认就执行高风险写入工具。

### 6.4 Agent App 与 Skills

目标合同：

- Agent 是用户面对的能力。
- Skill 是平台托管的能力包。
- Worker 根据平台策略把 allowed Skills stage 到 run workspace 的 `.claude/skills`。
- Agent 根据 `SKILL.md` 描述自主激活。
- Run 保存 used skill、版本、snapshot 和 provenance。

门禁：

- Skill registry、version、release policy、rollback、upload、dependency policy 可审计。
- Staging 不能越过 workspace。
- 禁用 Skill 后，新 run 不再 stage；已完成 run 保留 snapshot。
- executor-native used-skill event 纳入 Admin 展示。

禁止：

- 用户上传 Skill 后绕过 Admin policy 直接执行。
- 让 executor 自己决定平台 Skill 事实。
- 泄漏 raw skill id、staging path、dependency private payload。

### 6.5 Executor Adapter 与执行内核

目标合同：

- 所有执行器通过统一 `RunPayload -> ExecutorResult/events/artifacts` 接入。
- Claude Agent SDK 是当前主执行层。
- RAGFlow 是 read-only 知识检索执行层。
- AgentScope、OpenAI Agents SDK、DeerFlow-like runtime 只能通过 adapter 接入。

门禁：

- `RunPayload` / `ExecutorResult` schema version 固化。
- executor capability registry 可查询。
- cancel、resume、checkpoint、artifact、event 语义统一。

禁止：

- 让 executor 反向定义平台 schema。
- 未经 allowlist 的 executor_type 进入 run 创建链路。

### 6.6 Queue / Scheduler / Run Lifecycle

目标合同：

- 长任务不阻塞 HTTP。
- run 有 queued、running、succeeded、failed、cancelled、dead-letter 等状态。
- lease、heartbeat、retry、dead-letter、cancel、copy-run 由后端统一控制。

门禁：

- lease 状态持久化或可审计。
- retry/dead-letter policy 可配置并写 audit。
- per-tenant / per-user / per-agent 并发策略可用。
- checkpoint/resume 支持长任务。

禁止：

- worker 崩溃后无限重排队。
- 取消 run 后继续产生外部副作用。

### 6.7 Files / Artifacts / Object Storage

目标合同：

- 用户上传文件进入平台文件系统。
- 执行结果进入 artifact store。
- 下载、预览必须走平台鉴权。
- 前端不能看到 worker 路径。

门禁：

- artifact manifest schema 版本化。
- preview/thumbnail allowlist。
- retention / TTL。
- 文件级 DLP、病毒扫描、敏感信息扫描。
- artifact lineage 标明来源 Skill/版本/模型，但不泄漏 raw id/path/key。

禁止：

- 直接暴露 MinIO key、worker path 或 local runtime path。
- artifact card 展示 executor private payload。

### 6.8 Sandbox / Workspace / Resource Management

目标合同：

- 每个 run 有隔离 workspace。
- sandbox workspace 和 container lifecycle 可租约、可续租、可释放、可审计。
- fake provider 只用于测试，不代表生产安全可用。
- 生产 Docker provider 必须在独立受控节点验证。

门禁：

- Docker provider 资源限制覆盖 memory、CPU、pids、disk。
- network/egress policy 真实可证，不能用破坏 callback/health probe 的全网络关闭冒充。
- orphan container cleanup job、container stop/remove smoke、Admin Runtime 投影可验证。
- 每 tenant/user runtime 配额可用。

禁止：

- 将 fake provider 当成生产 sandbox。
- 清理 foreign tenant 或 running sandbox container。
- 未授权 sandbox/tool 执行高风险任务。

### 6.9 MCP / Tool Permission

目标合同：

- MCP/tool 是平台受控能力。
- Tool 调用有 allow / deny / ask。
- 高风险写入必须确认或 Admin 授权。
- request、decision、audit 和 run event 可追溯。

门禁：

- `tool_policies` / decision consumption / expiry 语义完整。
- write-capable business tools 接入同一 gate。
- allow_once 与 tool_call_id 绑定。
- allow_for_run 与稳定 fingerprint 绑定。
- 公开投影不暴露 raw command 或 fingerprint。

禁止：

- 未授权写工具产生外部副作用。
- 将 raw command、secret、credential、fingerprint 暴露给普通用户。

### 6.10 Memory / Context

目标合同：

- 平台统一构建 executor context。
- 每次 run 保存 context snapshot。
- 长期记忆受 tenant/user/session/agent policy 管理。
- 记忆可审计、可删除、可禁用。

门禁：

- ContextBuilder 覆盖 run、chat、copy-run、resume、replay 与 worker-side refresh。
- Memory UI、tenant/user opt-out、retention cleanup、configurable redaction policy 可用。
- 普通用户 public projection 和 Admin operational projection 都不返回 secret-like payload 或 executor private payload。
- 跨 session 长期记忆默认 fail-closed，除非 policy 和审批链完整。

禁止：

- 因为 memory 表存在就默认开放跨用户或跨 session 记忆。
- 审计日志保存原始 secret、memory content 或 executor private payload。

### 6.11 RAG / SOP 助手

目标合同：

- RAGFlow 只作为 read-only 知识检索工具。
- SOP 助手作为 Agent/Skill 暴露给用户。
- 检索、引用、答案和权限走平台事件和审计。

门禁：

- dataset/KB 映射进入 tenant/workspace policy。
- 检索结果引用可追踪。
- RAG 工具调用进入 tool permission 和 run event。

禁止：

- RAGFlow 接管平台身份、权限、session 或 artifact 事实源。

### 6.12 Observability / Quality / Audit

目标合同：

- 记录 token、cost、latency、tool count、artifact count、error type、quality score。
- Admin 能按 tenant/user/agent/skill/executor 查看质量与成本。
- Audit 与 event 使用同一 run trace 关联。

门禁：

- golden set / eval run。
- trace export。
- error taxonomy。
- Admin quality dashboard。

禁止：

- 质量评估读取普通用户不可见私有 payload。

## 7. Release Gates

| Gate | 通过标准 | 未通过时阻断 |
| --- | --- | --- |
| G0 Source Authority | 本地、211、文档指向同一代码源和主链路 | 停止 implementation plan |
| G1 Security MVP | CORS/auth/header/RBAC/tool deny-by-default 正确 | 不开放普通用户入口 |
| G2 Control Plane | session/run/file/artifact/audit contract 稳定 | 不接新 executor |
| G3 Queue Lifecycle | lease/retry/dead-letter/cancel/idempotency 可用 | 不做长任务 |
| G4 Skills Governance | allowed/staged/used/version/dependency/snapshot 可审计 | 不迁移更多 Skills |
| G5 Memory/Context MVP | context snapshot、memory policy、redaction、delete 可审计 | 禁止跨 session 记忆 |
| G6 MCP/Tool Permission | tool allow/deny/ask/read-only/dangerous fail-closed | 禁止写工具 |
| G7 Event/Playback Contract | typed event、SSE replay、ordinary/admin redaction 可用 | 不做复杂前端长任务展示 |
| G8 Sandbox Lease | lease 边界清晰，资源隔离可证 | 禁止高风险 sandbox/tool |
| G9 Agent Frontend V1 | 企业 Agent 前端、stream、artifact、permission、playback、file task UI 可验收 | 不开放普通用户试用 |
| G10 Long Task / Multi-Agent | checkpoint、subagent、artifact tree、真实 provenance 可审计 | 仅保留单 agent/短任务 |
| G11 Beta | 多用户并发、成本、质量、审计可运营 | 回退内部试用 |

## 8. 实施顺序

1. Source Authority。
2. Security / Policy MVP。
3. Control Plane contract。
4. Queue / Run Lifecycle。
5. Skills Governance。
6. Memory / Context。
7. MCP / Tool Permission。
8. Event / Playback。
9. Sandbox Lease / Workspace。
10. Agent Frontend V1。
11. Long Task / Multi-Agent Runtime。
12. Observability / Quality。

## 9. 边界探测清单

每次实现计划必须覆盖相关边界：

- 非 admin 访问 admin route。
- 跨 tenant 读取 run/file/artifact/memory。
- 上传文件后跨用户下载。
- 禁用 Skill 后新 run staging。
- Tool write 无 decision。
- allow_once 重放。
- SSE reconnect 重放。
- memory policy 禁用后读取。
- sandbox lease 过期续租。
- run cancel 后 artifact 或 tool side effect。
- executor 返回 private payload。
- artifact preview 请求非 allowlist 类型。
- admin audit 读取普通用户 secret。

## 10. 非目标

- 让任一外部项目成为后端事实源。
- 普通用户自管理 MCP server、raw Skill、模型 Key 或 sandbox。
- 绕过企业账号和 tenant/workspace/user 边界。
- 未授权写工具产生外部副作用。
- 将 fake sandbox 当成生产隔离。
- 在 PRD 中保存非当前主链路、会话过程记录、临时服务或短期执行证据。
- 在 PRD 或路线图中维护已退出范围对象的名称清单。

## 11. Review Gate

每个开发阶段必须至少经过：

- 主 agent 实现。
- 独立 review agent 审查。
- 针对 review 结果修复。
- 本地 focused verification。
- 211 smoke 或用户指定环境验证。
- 汇报剩余风险。

Review 重点：

- 安全边界。
- 多租户边界。
- 工具写入边界。
- artifact / memory / event redaction。
- sandbox 资源与清理。
- source authority 是否回退。

## 12. PRD 自检

本 PRD 固定以下结论：

- `ai-platform` 是最终自研产品和唯一企业事实源。
- 外部项目只按模块吸收。
- 普通用户面对 Agent，不面对 raw Skill。
- Admin / Developer 负责治理与审计。
- Memory、Tool Permission、Event/Playback、Sandbox Lease 是长任务和多 Agent 的前置基础。
- Long Task / Multi-Agent Runtime 必须在 Foundation 和 Agent Frontend V1 关键门禁后推进。
- 真实进度以当前代码、DB、容器和当次验证报告为准，不写入 PRD。
