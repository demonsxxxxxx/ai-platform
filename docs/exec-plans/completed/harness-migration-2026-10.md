# Harness 工程化改造计划

> 已停止，未竣工归档（2026-10-24）。B1/B2 产物（AGENTS 入口、ARCHITECTURE、core-beliefs、QUALITY_SCORE、文档检查器、本地 hooks、coverage 采集）已在 main 常驻；B3–B7 未执行，剩余架构债以 [技术债台账](../tech-debt-tracker.md) 为准。本文中的分支、工作区、授权和行号只描述 2026-10-19 当次会话与当时基线，不构成当前授权或源码事实。

## 0. 元信息

- 项目 / 仓库：ai-platform
- 分支 / 工作区：`fix/main-contract-convergence-20261019`，独立修复工作区；原 feature 工作区及其未提交改动保留
- 计划版本 / 日期：v1.5 / 2026-10-19（北京时间）
- 状态：**全范围修复已获授权；既有 B1/B2 设施已进入 main。本轮先补齐完整测试基线、D5 门槛和 hooks，再按前置条件推进 B3–B7；完成与验收分别记录。**
- 施工图：[归档副本](harness-rules-blueprint.md)（turn-project 版，保留全文）；固定母本与 MIT 归属见 [来源记录](harness-rules-provenance.md)
- 本轮修复基线：`main f853a8a8cdbc4542319d8c244cefad9fb54aaeec`。既有库存基线 `f519d041f1bb155a788278abfd5e07becebe1767` 的测量仍是历史样本，不证明本轮候选或部署状态。
- 历史证据范围：2026-10-13 的 tracked 库存、依赖图、B3–B5 有界合同比对、B1 85 包 OSV、B2 88 设施回归及 132/174 用例 coverage 样本。当时 330 个候选测试文件中 86 个无 workflow 字面引用、4 个仅 node 引用；PG/Redis 探针 7 skip、exit 6，依赖审计刷新 TLS 超时。当前完整范围、真实依赖与门槛须重新测量，不能沿用这些数值作验收。
- 执行方式：主会话单一 writer；只读 Scout 提供分域证据，主会话复核。高风险独立 review 和正式运行验收仍按现有流程取得，不由探查报告替代。
- 约束归属：施工流程采用 Harness 规则；产品架构、事务、公共投影和发布继续由 [文档 authority 索引](../../README.md) 指向的合同拥有。路径与术语复用 §5.2 的现有 owner，不另建平行架构权威。

### 2026-10-19 已确认的修复范围与政策

- 范围包含 Runtime GET 只读、文档对齐、决策记录与 DingTalk 部门方案集成，以及完整 RunAttempt 迁移、保留策略、诊断 PRD 和 Harness B3–B7。各自详细产品契约继续由现有 owner 维护。
- 已认证根路径和登录默认入口保留 `/apps`；裸 `/chat` 进入专家市场。移除项目的 fake provider“仅供测试”规则，不新增环境禁令；已有要求真实 Sandbox 的执行边界保持。
- 五类保留配置默认仍为 `0`。配置非零时，只清理到期、已退役且没有有效引用的数据；保留可查看会话历史、可重放 Run 的依赖和关联审计。阻断原因须可见，文件对象继续走现有删除 outbox；本轮不启用真实数据清理。
- D5 以完整实际测试范围和真实依赖基线为前置。整体及现有高风险 owner 域的 statement/branch coverage 不得回退；新文件大小上限由实际规模测量确定并记录依据，不从历史局部样本推定，不放宽既有门禁。
- 交付版本化 pre-commit 快速静态/文档检查与 pre-push 受影响检查；完整验证仍由 CI 负责。仅在隔离工作区验证，不修改全局 Git 或原工作区配置。
- 普通任务沿用 PR/ADR/契约记录；本专项继续使用本计划和既定阶段验收。先完成完整范围与保护，再做事务/身份/删除迁移；每批更新 caller、测试、selector 和退役处置。
- 实现与验证之后，用户已授权提交当前候选、推送修复分支并创建 PR；未完成验收的候选以草稿交付。授权不包含合并或部署。源码、CI、测试包与正式环境证据分别记录；正式发布仍须单独授权。

## 1. 项目概况

- 技术栈：FastAPI + psycopg（binary/pool）/PostgreSQL + Redis（后端，`app/`）；React/Vite/pnpm（前端，`frontend/web/`）；Python>=3.11 + uv 管理锁定依赖
- 构建方式：后端 `uv` + Dockerfile；前端 `corepack pnpm` + Dockerfile
- 运行方式：API `app.main:create_app()`；Worker `python -m app.worker_main`；开发 `corepack pnpm run dev`
- 部署方式：不可变 Docker/Compose 发布包 `python3 deploy.py --env-file ...`（README.md:15-30）
- 当前测试与 CI 现状：4 个 GitHub workflow；后端 CI 8 个 pytest 分片 + 镜像构建 + Trivy + 聚合 required 门禁；前端 4 个 JS 分片 + 独立 Python 合同/投影审计 + lint/type-check/build，保留完整本地 `pnpm ci:verify`（当前拓扑见 [CI 配置](../../CI.md)）；trusted-governance（Ruff + code/architecture governance）；packaging-publish（SBOM + cosign + manifest）
- 本仓库「记录系统」基础：`docs/README.md` 是 authority index；架构约束已由 `tools/architecture_governance.py` + `architecture-policy.json` 机械执行

## 2. 框架判定与 CI 现状（§3.2）

- 判定结果：**其他类型**（非 Tauri）
- 判定证据：无 `src-tauri/`；根无 `package.json`；无 `@tauri-apps/*` 依赖（2026-10-10 实测 ls/grep）
- 现有 workflow 清单：

| 文件 | 触发 | 覆盖 | 自动发布 | 本地可复现 |
|---|---|---|---|---|
| ai-platform-backend.yml | PR→main / push main / dispatch | preflight + compileall + SSE cutover + 8 pytest 分片 + 镜像/Trivy/启动健康 + required 聚合 | 否 | 大部分可（uv sync + pytest 分片命令）；Docker/Trivy 需 Docker |
| ai-platform-frontend.yml | PR→main / push main / dispatch | Windows JS 分片 + 独立 Python 合同/投影审计 + lint/build/trace + 镜像/Trivy/来源验证 + required 聚合 | 否 | 完整本地 pnpm ci:verify；Windows/Linux runner 不完全等价 |
| ai-platform-packaging-publish.yml | push main + dispatch（需 confirm_release） | 镜像构建推送 + SBOM + cosign 签名验证 + manifest + Compose 包 + 可选 GitHub Release | 是（受控） | manifest/打包脚本可；签名/推送不可离线复现 |
| ai-platform-trusted-governance-v2.yml | pull_request_target（4 事件） | 受信基线校验 + Ruff + code_governance + architecture_governance | 否 | 可（pinned 依赖 + 相同命令） |

- 按 §3.4：跳过 Tauri 三问，不生成打包流水线；现有 CI 只审计不改动

## 3. 现状诊断（阶段 1 扫描结果）

### 3.1 架构现状

**声明策略与机械执行**：`architecture-policy.json` 划分 bounded contexts，跨 context 仅 `api.py`/`events.py` 公开；`tools/architecture_governance.py` 对 Git range 做 AST 导入分析，含禁用模块名（common/utils/services 等）与 approved_root_modules 白名单。

**符合边界的抽样（正面）**：
- `app/execution/application/worker_skill_evidence.py:6` 经 `app.skills.api` 访问 Skills
- `app/execution/application/worker_failure_diagnostics.py:10-11` 经 `app.runs.api`、`app.sandbox.api`
- `app/execution/application/model_selection.py:9` 经 `app.runs.api`

**当前跨 context 依赖（架构债，未据此证明运行故障）**：
- `app/runs/infrastructure/replay_postgres.py:17-18,38-42` 直连 Conversations message writer、Identity audit writer、Skills snapshot persistence 和 Streaming event writer。
- `app/runs/infrastructure/capability_admission_postgres.py:5,11-13,20-24` 直连 Skills/Identity infrastructure；MCP 查询已通过 `app.mcp.api`，不再登记为内部导入。
- `app/skills/catalog.py:11-33` 依赖 Identity infrastructure、同域 Skills infrastructure 及 legacy 根模块；同域实现依赖不属于跨 context 越界。
- Cleanup outcome 候选已由 `app/sandbox/application/runtime_cleanup.py` 的 `SandboxCleanupOutcomes` 拥有；Bootstrap 注入 Identity/Streaming writer，旧 SQL adapter 编排已删除。真实 PG 事务、锁及取消验收未完成，TD-SANDBOX-001 保持开放。

本次 [全量静态库存](quality-baseline-2026-10-13/source-quality-inventory.json) 解析 452 个 app Python 文件、423 个前端生产文件和测试文件索引，0 解析失败；得到 **36 条跨 context 内部导入边**，其中 Agent Apps 13 条、Runs 16 条。完整位置与分母见 [QUALITY_SCORE](quality-baseline-2026-10-13/QUALITY_SCORE.md)，B3–B5 只是首批 bounded scope，不是全部剩余架构债。静态图包含局部/条件/type-only import，不单独证明运行故障。

现有 `api.py` 未导出上述全部持久化函数，且 [source architecture](../../architecture/source-code-architecture.md) 禁止公共 API 暴露具体 infrastructure adapter。B3–B5 应先梳理用例与装配边界，复用已有 application 能力和 Bootstrap 注入方式；不能批量 re-export 数据库函数来让 import 检查变绿。

### 3.2 复杂度热点

| 路径 | 行数 | 本轮处置 |
|---|---:|---|
| `app/executors/claude_agent_sdk_runner.py` | 4565 | SDK 集成热点，先追行为/消费者，不按长度拆分 |
| `app/runtime/sandbox/container_provider.py` | 4490 | 继续服从既有冻结增长门禁和 provider 合同 |
| `app/queue.py` | 2611 | 队列/lease/fencing 验收保持独立 |
| `app/routes/chat.py` | 2340 | admission 与公开投影消费者先核对 |
| `frontend/web/src/hooks/useAgent.ts` | 3660 | 前端生命周期热点，mounted/source 证据分开 |

行数由标准库脚本从固定基线重新测量；完整前后端 top 10 在生成 JSON 中。行数和函数 span 是定位信号，不能自动推导复杂度缺陷。Architecture governance 对冻结文件实施增长检查，code governance 的数量/净增/热点提示仍与新文件独立大小 hard gate 区分。

### 3.3 复用与重复证据

`replay_postgres.py:27-31` 已复用同域 `capability_admission_postgres` helper。多个模块导入同一 helper、`skills/catalog.py` 与 `skills/api.py` 同时存在，均不足以证明重复实现。撤销原 TD-SKILLS-002/TD-RUNS-004 重复登记；新重复项须列出两段实际实现、相同输入行为及为何必须一同修改。

### 3.4 历史引用处置

[State authority implementation plan](../../implementation/state-authority-convergence.md) 明确区分固定样本与当前实现。`tool_permission_lifecycle.py`、`repositories.py` 的链接绑定 `2ba4e172598df2bae7aa03684da2e39c7b9fbf0a`；目标在该提交存在，不是 main 的本地死链。保留历史依据，撤销 TD-DOC-001/002 及原 D2 删除决策。后续文档债只登记不可解析目标或将历史行为误标为当前合同的具体条目。

### 3.5 实现到测试的映射（覆盖深度待查）

| 实现 | 已存在的测试入口与具体证据 | 本批处置 |
|---|---|---|
| `app/product_events.py` | [test_contract.py](../../../tests/test_contract.py)：`test_harness_initial_events_contain_no_skill_fact`、`test_skill_initial_events_keep_exact_skill_fact` | 已有行为用例，不能登记“无同名测试=无覆盖” |
| `app/session_actions.py` | [test_lambchat_frontend_compat.py](../../../tests/test_lambchat_frontend_compat.py)：`test_session_action_service_enforces_tenant_owner_admin_and_terminal_delete` | 核对权限/删除分支后才登记缺失场景 |
| `app/chat_session_projection.py` | [test_chat_sessions.py](../../../tests/test_chat_sessions.py)：直接调用 `session_response`，覆盖 avatar 和 retired Agent 投影 | 复用既有投影用例 |
| `app/platform/tracing.py`、`public_payload.py` | [test_terminal_stage_timing.py](../../../tests/test_terminal_stage_timing.py)、[test_run_lifecycle.py](../../../tests/test_run_lifecycle.py)、[test_public_answer_stream.py](../../../tests/test_public_answer_stream.py) | 已有直接消费者测试，不代表所有分支已覆盖 |
| `app/runtime/sandbox/workspace_manager.py`、`runtime.py` | [test_sandbox_workspace_manager.py](../../../tests/test_sandbox_workspace_manager.py)、[test_sandbox_runtime.py](../../../tests/test_sandbox_runtime.py) | 核对真实调用和异常路径 |
| `app/agent_apps/` | [test_worker.py](../../../tests/test_worker.py)、[test_agent_profiles.py](../../../tests/test_agent_profiles.py) 使用 admission/Profile 能力 | 目录级覆盖率未测，不作无测试结论 |
| `frontend/web/src/components/documents/DocumentPreview.tsx` | [DocumentPreviewLifecycle.mounted.test.tsx](../../../frontend/web/src/components/documents/__tests__/DocumentPreviewLifecycle.mounted.test.tsx)：挂载真实组件，覆盖 A→B→关闭→C、布局 lease、迟到关闭和 auth revocation | 先复用现有 mounted 测试 |
| `frontend/web/src/components/workbench/WorkbenchRightPanel.tsx`、`WorkbenchProjectionPages.tsx` | [workbenchVisualClosure.test.ts](../../../frontend/web/src/components/workbench/__tests__/workbenchVisualClosure.test.ts) 的 source-contract 断言 | 路径已更正；source 检查不等同于交互测试 |

原 `components/agent/ModelSelector.tsx` 路径未找到，已移出测试缺口清单；如需审查模型选择，先定位现行实现及消费者。B6/B7 仅对已确认的未覆盖输入、异常或并发场景补测试，并给出破坏实现后失败的证据，不以文件名或测试数量作为完成标准。

### 3.6 安全与可靠性（明确作用域的证据）

- 已准备本工作区锁定环境：`uv sync --locked --extra test --python 3.13`，未改变锁文件。OSV 对其中 85 个包查询得到 0 个已知漏洞、0 个跳过；[原始报告](quality-baseline-2026-10-13/dependency-audit.json) 和命令在 QUALITY_SCORE。此前 PyPI TLS 超时不算成功；本结果不覆盖 Linux 镜像、Node 或业务输入安全。
- 当前 [ProfileDrive adapter](../../../app/files/infrastructure/profile_drive.py:53) 已有 60 秒默认读取时限、1800 秒默认绝对总时限、有限资源关闭预算，以及流式读取/下游背压/取消的 owning 回归。撤销 `read=None` 的 D3 疑问，不据此宣称所有第三方 close 都有硬墙钟上限。
- [Sandbox callback authority](../../../app/sandbox/application/callback_authority.py:55) 在同一 caller 连接上 Run-before-Attempt，从 persisted Run 派生 tenant，检查 session、终态、唯一 lease、Attempt 与 token generation；[Bootstrap](../../../app/bootstrap/sandbox_callbacks.py) 负责具体 query 注入。新基线已包含 #1801，不能按旧 route-owned guard 制定迁移。
- [Run lifecycle](../../../app/runs/application/lifecycle.py:56) 通过明确的 persistence/event/audit ports 工作，[Bootstrap](../../../app/bootstrap/run_lifecycle.py:27) 注入实际 adapters；[Copy route](../../../app/routes/runs.py:1154) 和 retry/resume 的公共编排在 caller transaction 内完成当前 principal/Profile 复验、child 和 operation receipt，提交后才进入队列。
- 固定基线f519d041的accepted路径曾在记录acceptance后直接return，存在terminal-first/accepted-later的新wake窗口；准备PR时合入的main c389fe01（#1802）已在提交后对pending终态回执有界发布wake（当前runtime.py:886–898）。本Harness PR不修改该生产flow，早期源码缺口和历史部署慢Run仍不能直接建立因果。
- 2026-10-10 密钥/SQL/logger 模式抽样仅是历史观察；当前全仓业务安全、真实 PostgreSQL 回滚/并发、provider/host enforcement 和部署状态没有完成验收。没有依据宣布 P0 清零。

## 4. 改造批次划分

| 批次 | 覆盖业务域 | 主要目标 | 依赖前置 | 回滚方案 |
|---|---|---|---|---|
| B1 | 文档 | 查验当前链接/状态，建立可复算 QUALITY_SCORE 基线；保留 §3.4 历史引用 | 阶段 1 库存及需求处置确认 | 纯文档提交，revert 独立提交 |
| B2 | 门禁 | 修复指引、T1–T10 缺口核验、文档/覆盖率门禁及 hook/豁免处置 | §5.1；未观测执行不能标为通过 | 按门禁切小提交，保留原 required checks |
| B3 | Runs replay | 用例、message/Skill/event/audit 协作者与 Bootstrap 装配收敛 | §4.1 接口库存、回滚/原子性测试先就绪 | 小提交回放；有数据/协议变化另列迁移处置 |
| B4 | Runs admission | Skills/Identity 准入依赖收敛，保留现有 MCP API 和授权复验 | B3 模式验证及本批 §4.1 库存 | 同上；不放宽 authority 或删除复验 |
| B5 | Sandbox cleanup、Skills catalog | 按职责迁移 cleanup writer 与 catalog 依赖/装配 | B3/B4；本批循环导入及 caller 库存 | 按两条职责分别提交和回滚 |
| B6 | 后端测试 | 用 §3.5 现有用例映射登记具体未覆盖场景；补 owning regression | 测试保护在受影响重构之前或同批具备 | 独立测试提交；保护未就绪不启动重构 |
| B7 | 前端测试 | 同上，区分 source-contract、mounted、端到端证据 | 现行路径与消费者已核实 | 同上 |

### 4.1 B3–B5 启动前的接口与装配库存

| 模块 / 现有调用形状 | 必须保留的输入、输出和错误 | 候选责任与装配 | 已知 caller / owning tests |
|---|---|---|---|
| `replay_postgres.copy_run_as_new_task(conn, *, tenant_id, user_id, run_id)`，返回 row 或 `None` | 同一个 `conn` 上完成授权、Run/message/Skill snapshot/event/audit 写入；保留其他 replay 入口的幂等与错误分类 | Runs application replay 用例协调最小 peer 能力；具体 adapter 留在 infrastructure，由 Bootstrap 装配 | [Copy route](../../../app/routes/runs.py:1168)、[Retry/Resume 同一编排](../../../app/routes/runs.py:1425)、replay 内部 retry/resume→copy（:331/:404）；[test_runs_replay.py](../../../tests/test_runs_replay.py)、[test_run_control_routes.py](../../../tests/test_run_control_routes.py)、test_routes.py 与 test_issue_511_session_context_gen2.py 的 string monkeypatch |
| `capability_admission_postgres.authorize_replay_run_capabilities(conn, *, ...)`，返回授权 snapshot | 保留 tenant/Agent/Skill/pin、部门/角色/permissions 输入、确定拒绝/冲突异常及现有复验 | Runs 准入用例复用 Skills/Identity application 能力；不得将私有 repository helper re-export 到 peer API | [prepare_copied_run_for_queue](../../../app/routes/runs.py:673)、[replay helper](../../../app/runs/infrastructure/replay_postgres.py:162)；test_runs_replay.py、test_routes.py、test_run_control_routes.py、test_run_file_continuity_routes.py 的 monkeypatch/授权用例 |
| `leases_postgres.record_sandbox_runtime_cleanup_outcome(conn, *, ...) -> None` | 原 `conn` 上写 event 与 audit，保留失败状态、租约范围、safe payload 与首错 | Sandbox application cleanup 协调已注入的 ledger 能力；复用 [bootstrap/sandbox_cleanup.py](../../../app/bootstrap/sandbox_cleanup.py) 与 [bootstrap/run_lifecycle.py](../../../app/bootstrap/run_lifecycle.py) 装配模式 | [cleanup service](../../../app/sandbox/application/runtime_cleanup.py:412) 的两个 settlement 点（:412/:495）、[admin lease route](../../../app/routes/sandbox_leases.py:221)；[test_runs_persistence.py](../../../tests/test_runs_persistence.py)、test_run_control_routes.py、test_sandbox_runtime_control_layer.py |
| `skills.catalog.resolve_authorized_skill_catalog(conn, *, binding, department_id, roles, permissions, pinned_manifests=None, skill_set=None)`，返回 `AuthorizedSkillCatalogResolution` | 保留作用域 binding、授权、不可用原因、已锁定 manifest 及错误身份；不授予新增能力 | Skills application 能力/纯类型按稳定 owner 放置，Bootstrap 注入 Identity/Skills persistence；现有 catalog 已依赖 skills.api，不能回向 re-export 造成循环 | [bootstrap/skills.py:81](../../../app/bootstrap/skills.py:81) 注入 resolve_catalog port，非直接调用；[test_authorized_skill_catalog.py](../../../tests/test_authorized_skill_catalog.py)、test_worker.py 的 fake port；API/import 与授权回归同批保护 |

引用库存覆盖当前 tracked app/tests 的直接调用、属性/port 引用、导入及等值 string monkeypatch；不宣称构造字符串、反射或传递调用全部可由静态 AST 解析。B4 当前完整 kwargs 为 `tenant_id, agent_id, skill_id, pinned_version, pinned_executor_type, skill_manifests, normalized_input, principal_department_id, principal_roles, is_admin, permissions`；cleanup kwargs 为 `tenant_id, run_id, trace_id, user_id, requested_by_role, reason, status, lease_ids, failures`，均保留 caller 的原连接。

表格是已知入口库存，不是新接口已获批准的声明。每批先在 PR 中列出**完整签名、返回值/异常语义、现有能力复用、全部调用方、monkeypatch/selector、装配和退出项**，再实施。拟迁移签名必须保持 caller-owned `conn`；不得内部新开、提交或回滚独立事务，锁序、CAS/generation、授权范围与提交后发布顺序均需保留。

先复用现有 `RunLifecycleService` 的 event/audit ports 和已有 application 能力；只有确有 peer 消费者且现有契约不足时才新增最小接口。禁止 API 透传 concrete adapter、只为 import 变绿而加包装，或按同域 import 推断重复。移除旧实现须同步处理 caller、测试、selector、文档与兼容 disposition，按 [PR workflow](../../agent-rules/github-issue-pr-workflow.md) 给出 absence/inventory 证据。

受影响 owning tests 必须在重构前或同批证明：相同连接/锁序；中途失败及 CAS 丢失时整体回滚；错误身份、tenant/Attempt 范围和公开投影不变；API 导入/启动无环。既有 happy-path 测试通过不能单独证明行为守恒。实际 selector 与 observed result 留在 PR；收益不足的批次可保持待排期，不虚称已收敛。

## 5. 五阶段任务拆解

### 阶段 1 · 库存、证据与计划确认

- 主控补全源码/配置/依赖图、循环边、风险/测试库存及分域批次；抽样、未测和推断分别标明。产物为本文、台账与 [CI 说明](../../CI.md)。
- 验收：每项有可复核位置和场景；每批有 owner 角色、前置、退出/回滚与可机械检查的标准；未改业务代码。满足施工图阶段 1 清单并取得计划确认后进入阶段 2。

### 阶段 2 · 文档对齐（B1）

- 文档 owner 核对短入口、架构/设计/产品索引和开发/调试/发布说明，保留历史来源；抽样至少 3 份合同与代码比对。现有 `docs/README.md`、source architecture、ADR 和 runbook 为等价复用候选，不照搬外部 Types/Providers taxonomy。
- 明确 `ARCHITECTURE.md` 等模板路径的映射及是否需要索引，不另写冲突合同。建立 `docs/QUALITY_SCORE.md`，逐域/层列测量范围、分母、命令、基线和差距；差异范围 governance 报告不能充当全仓质量分数。
- 落实文档 link 检查及园丁任务的 owner、触发、修复流程；状态与索引说明由编辑负责，验证坏链接确能失败。路径等价复用或其他范围调整须获计划确认。

### 阶段 3 · 门禁审计与缺口交付（B2）

- 门禁 owner 按 §5.1 逐项验证现有检测，补齐真实缺口；更新 CI 说明中的完整命令、失败含义和修复指引。非 Tauri 三问/desktop Release 不适用，保留现有发布权威。
- hook、coverage、豁免记录及结构/文档门禁未落实的项不得标“已有流水线=完成”。每个新增/复用门禁用合成违规证明能阻断；报告 advisory 与 hard gate 的区别。
- 验收包括干净环境安装→构建→测试、越层/文件大小/非结构化日志/死链负例以及可执行修复信息。豁免必须有原因、期限和独立审查，不能改弱 required checks。

### 阶段 4 · 分域实施（B3→B4→B5）

- 各 context owner 先完成 §4.1 及 owning regression，主控顺序整合共享文件。B6/B7 保护随所属改动同批交付；不得等阶段 5 才保护新重构。
- 每批提供同输入行为/异常回放、事务/授权/并发反例、前后依赖边及有证据的重复/复杂度变化；完成兼容退出、absence/inventory 与合同同步。
- bug 修复、业务变更与纯重构分开处置；行为改变触及的新边界另立 bounded Change Contract。计数下降、import 变更或旧测试全过均不单独作为完成证明。

### 阶段 5 · 测试完善与门禁验收（B6/B7）

- 测试 owner 以 §3.5 映射确认具体遗漏，补关键用户旅程、空值/超限/并发/超时/部分失败/幂等场景，区分单元、真实依赖集成与端到端职责及运行时长上限。
- 测量整体及关键域覆盖率；在阶段 3 确定非虚构基线与阈值，接入不可绕过的覆盖率/结构门禁。阈值、工具或必需依赖未就绪时保持 pending，不默认豁免。
- 抽样至少 3 个新增用例在故意破坏实现时失败；P0 修复逐项有 owning regression，P0 清零且 P1 有处置/排期。
- 完整约定测试范围连续 3 次通过；后端沿用显式 selector 分片和 worktree 锁串行执行，各轮分片并集覆盖约定全量；前端执行约定完整入口。必需真实依赖用 `--require-zero-skips`，超时/skip/缺依赖不算完成。发现 flaky 先修根因并记录，不用永久 deselect 或削弱断言过关。

### 收官 · §14 归档与常驻约束移交

五阶段全部验收后，主控逐条执行施工图 §14.3–§14.6：一次性流程移入 `docs/exec-plans/completed/harness-<YYYY-MM-DD>.md`；长效约束交给现有 owning 合同、AGENTS 或可靠门禁，保持单一 authority。更新索引/消费者，确认移除施工图入口后仅凭常驻来源仍能遵守全部硬约束，再由人确认收官并单独提交。母本来源保持可追溯；施工图不得提前删除或永久作为第二套规则加载。

### 5.1 必需要求处置与验收索引

“已有候选”只表示可复用源码，不表示本次已跑过或已满足；阶段 3 必须核对完整检测范围和负例。任何裁剪、模板路径替代或阈值决策均记录理由及批准，未决定的项不能静默移除。

| 施工图要求 | 现有候选 / 待落实动作 | 角色 / 阶段 | 完成证据 |
|---|---|---|---|
| C1–C7、§1.4、§7/9 | 计划确认、分域串行、小提交、行为变更独立登记及回滚；仅保留获准任务范围 | 主控 / 各阶段 | 明确确认、变更及回滚/兼容处置；不代替 GitHub review |
| §2/阶段 2：短 AGENTS、架构/设计/产品地图 | 复用 owning 索引/合同，模板角色映射落实见§5.2；QUALITY_SCORE 可复算 | 文档 owner / 2 | 入口/索引可导航、3 份合同比对、逐域/层测量，不复制平行规则 |
| T1 文件大小 | 冻结文件增长 hard gate + code-governance 热点 advisory 已有；新文件 hard gate 阈值待基线/决策 | 门禁 owner / 3 | 分别实测增长/新文件违规；阈值及豁免有依据 |
| T2 结构化日志 | 查验现行 tracing/safe logging 及消费者；补窄而可靠的非结构化日志 detector | Platform/门禁 owner / 3 | 非结构化负例失败、safe logging 用例通过；未查验不标全仓合规 |
| T3 Schema/类型命名 | 复用 architecture policy/owning schema，核对未覆盖命名要求 | 各 context/门禁 owner / 3 | 命名正反例、检测范围清楚 |
| T4/T5 边界解析、禁止未校验字段探测 | 复用实际边界 codec/validator/owning 测试，补具体遗漏 | 各 context / 3–5 | 无效 shape/identity/数值被拒、合法输入通过；不以 grep 证明安全 |
| T6 重复逻辑 | 对实际重复体做前后行为/度量核验，同域共享 import 不算重复 | 各 context / 4 | 两段重复证据与收敛后 caller/测试闭环 |
| T7 依赖方向 | 复用 architecture policy、governance 和现有反例测试 | 门禁 owner / 3–4 | 越层违规被拒、合法边通过，B3–B5 事务与导入检查 |
| T8 可靠性 | 逐路径查验 timeout/retry/cancellation/cleanup owner；保护既有错误和身份 | Execution/Sandbox/Platform / 3–5 | 超时、部分失败、取消、接管等 owning 回归，不从有限抽样推全仓通过 |
| T9、§2.4 文档防腐 | link 检查与状态/索引说明的编辑责任及园丁流程；现有 source-authority 测试只覆盖其实际职责 | 文档/门禁 owner / 2–3 | 坏链接负例失败，园丁修复经正常 review |
| T10 覆盖率、阶段 3/5 | 整体+关键域基线/阈值和覆盖门禁待落实 | 测试/门禁 owner / 3、5 | 可复算报告、阈值违规被挡，不把测试数当覆盖率 |
| 阶段 3：基础门禁、hook、豁免 | 现有 lint/type/build/test/依赖镜像扫描按实际工作流复用；pre-commit/pre-push、豁免期限与可复现性补查 | 门禁 owner / 3 | 干净环境完整链、完整命令及负例；不以“更强”免除其他要求 |
| 阶段 4/5：测试分层、故障注入、连续全量 | 分片复用 runner、真实依赖/no-skip、端到端核心旅程和 ≥3 反例 | 测试 owner / 4–5 | 保护先就绪、3 次完整范围通过、P0 回归/清零与 P1 处置 |
| §6 会话纪律、§8 反馈/熵管理 | 版本一致性、提交约定、提示词记录隐私、review 和文档园丁的具体映射待确认；私有 prompt 不写入共享仓库 | 主控/文档 owner / 2–3 | 兼容现有隐私/发布合同，获准后落实；工具或 review 结果不自授权合并 |
| §11 DoD、§14 收官 | 逐条核对完整 DoD 后才归档/迁移长效约束，保留母本归属与来源 | 主控 / 收官 | 每条处置及移除施工图入口的自检、人确认；未完成不宣布竣工 |

### 5.2 文档角色等价映射（B1 已落地）

本轮“继续”和主会话接管批准覆盖以下文档/库存工作；模板目录采用现有 owner 路径，未替换产品合同或新增发布权威。

| Harness 角色 | 本仓库落点与处置 |
|---|---|
| 短 AI 入口 | 现有根 AGENTS；CLAUDE 继续只指向同一来源 |
| ARCHITECTURE | 新 [ARCHITECTURE](../../../ARCHITECTURE.md) 仅作导航，详细规则仍归 source/system/runtime contracts |
| docs/index | 复用 [docs/README](../../README.md)，保持一项要求一个详细 owner |
| core-beliefs | 新 [core-beliefs](../../design-docs/core-beliefs.md)，原则摘要全部链接既有合同 |
| 设计/决策记录 | 复用 docs/architecture 与 docs/adr，不搬动历史固定链接 |
| 产品/语言图谱 | 复用 CONTEXT 与 workbench、Profile、Conversation owning contracts |
| QUALITY_SCORE | 新 [QUALITY_SCORE](quality-baseline-2026-10-13/QUALITY_SCORE.md)，逐域/层公式、N/A、范围、输入/生成器摘要与未测项 |
| 开发/测试/调试 | 复用 README、local-test-execution 与 owning regression；新增本轮测量脚本及最小回归 |
| CI / 发布 | CI.md 导航 + 现有 workflows/runbook；未新增 desktop 发布流程 |
| 生成事实 | docs/generated 源码库存/依赖审计；均为 dated observations，不是 authority |
| 计划/债务 | 本文与同级 tech-debt-tracker，完成后按 §14 归档 |
| 提示词/会话记录 | 不写真实私有 prompt、环境或 credentials；只保留获准的脱敏任务事实 |

T9 的自动坏链接反例与作者触发修复在 B2 候选中已落实；T10 已能显式采集 app 语句/分支，完整测试范围/阈值与可信阻断仍未完成。这些不因候选配置或样本覆盖率存在就标记阶段验收通过。本批新增诊断/文档、runner 选项和 backend preflight；无 superseded production surface。无 coverage 的 CLI/v1 证据保持兼容，required/trusted 检查不删除；B3–B7 的接口/测试/selector 退出仍在真正迁移时处理。

### 5.3 本轮门禁核验结果与具体未闭环项

本节记录 2026-10-13 B2 批次的观测及当时缺口；本轮授权和执行顺序以 §0、§6 为准。

证据在 f519d041 固定环境下取得：code/architecture 173 用例、risk baseline 104 用例及 measurement/source-authority 21 用例，均 0 失败/跳过。Ruff 检查新增分析脚本/测试通过；生成库存可重复复算。它们只覆盖各自 selector，不等于阶段 3/5 全部通过。

| 要求 | 当前处置 | 进入后续步骤的具体条件 |
|---|---|---|
| T1 大小 | 既有冻结增长 hard 与数量/LOC/hot advisory 反例已通过 | 新文件独立 hard 阈值、豁免与 D5 仍待确定 |
| T2 日志 | 现行 safe logging/测时用例为复用候选，未证明全仓非结构化 detector | 实际检测范围与合成违规负例，不能机械包一层 logger |
| T3 命名/schema | authority/policy/schema 闭合与不能自放宽的反例通过 | 新 DTO/Schema 命名要求与现有 contract 对照，未覆盖部分单独交付 |
| T4/T5 输入 | 当前 malformed manifest、exact pin、租户/session/Attempt/generation 范围拒绝用例通过 | 改动边界的具体反例/consumer 完整，不能由本组推全仓安全 |
| T6 重复 | 未确认新的重复体，撤销的登记不恢复 | 如迁移须列两个实际相同实现与同输入行为证据 |
| T7 方向 | 新 private-peer、局部/动态 import、candidate 自放宽与合法边反例通过 | B3–B5 新 API/Bootstrap 必须满足同连接/退出 inventory，减少 import 不能代替原子性 |
| T8 可靠性 | ProfileDrive 超时/取消/首错/关闭及共享 callback fence 的 owning 回归通过 | 真实 PG 并发/回滚、provider/host enforcement 保留未验证，不部署补证 |
| T9 文档 | 新 check_documentation + full-base-diff backend preflight；13 个 checker 反例/正例、workflow contract、正常作者触发修复流程 | 本地零 findings；CI 执行与独立 review 尚未观测；删除/移动仍须 caller inventory，外部网址/语义新鲜度不由它证明 |
| T10 覆盖 | runner显式coverage、452源文件分母；174用例局部样本，330候选测试的字面选择器库存绑定workflow摘要 | 86路径/named-node处置及真实依赖（本地7skip/exit6）完成后才能定D5；没有全套或阈值通过证据 |
| hook / full chain | workflow 源码已列入 CI 导航；本轮不是干净环境全链/三轮全量 | 版本化 hook 处置、Linux/Node/image 检查和独立 review 按正常流程补证 |

**下一可执行切片**是界定完整 coverage 的 owning selectors/真实依赖，再交付 D5 阈值、可信覆盖阻断和尚未完成的 T1/T2/hook 处置。T9/T10 设施候选已交付，不再登记“缺插件/无采集接口”。B3–B5 暂不启动。原生独立 reviewer 运行仍因 chord 依赖阻塞；主会话接管授权没有产生独立审查证据，也不授权绕过既有高风险 review。

### 5.4 B2 变更合同与观测限制

本节保留 2026-10-13 批次的变更与授权边界，不授予本轮提交、推送或部署权限。

- Owner：repository documentation 与 local-test-stage；达到 backend preflight/required 边界，执行现有高风险 CI review 合同。
- Change：文档 detector 检查真实 rendered Markdown、本地 link/anchor；状态/导航说明不设固定字段、行号或索引门禁；coverage 是 runner 显式可选项，默认行为/v1 envelope 不变。只在 test extra 增加 pytest-cov/coverage（tomli 带 Python 版本 marker），已有包版本不升级。
- Invariants：显式 selector、worktree lock、PYTEST_* 净化、stage deadline、Windows Job、原始失败/skip/timeout 分类保持。Coverage 清理自己的控制环境、使用 stage-owned config/data/report，完整 app 文件分母、Python source 前后 digest 和报告摘要绑定证据。无效/缺失/缺源范围的报告不能把测试绿转成成功证据。
- Regression：88 个 grouped owning 用例全通过；包括 namespace 分母 red→green、调用者 omit/fail-under 环境拒入、缺/坏报告、source mutation、zero-skips、覆盖开启的超时/子进程关闭；checker 的坏链接与固定历史合法反例可重复。
- Observation：最终同六 selector 的 b2cov 132 passed/0 skips，452 文件分母。先前450文件的报告和WinError206失败报告被排除；Windows SQLite 采用原生长路径、stage保持短名。完整测试/真实依赖未采集，样本百分比不是门槛。
- Retirement/compatibility：无 superseded production path 或已删除 CLI/selector；手工文档检查说明更新为 owning detector，旧“无插件/接口”说明已修正。required aggregate 与 trusted authority unchanged；新候选不会自批准 merge。
- Stop/review：用户本轮明确要求创建PR，授权提交/推送并创建草稿；仍不授权合并、部署、放宽required或提前运行B3–B5。独立review/GitHub CI尚待实际结果；新增test依赖OSV更新TLS超时，原85包报告仅保留B1历史范围。

### 5.5 完整suite前置核验与局部补采

本节是 2026-10-13 的固定来源记录；当前候选须重新取得完整范围与实测基线。

- 范围：新增inventory复用已有PyYAML，只读取workflow的run/test_files字面引用并记录摘要；不会执行candidate命令、动态解析测试或新增trusted gate。当前328 tracked +2本轮新增测试，244有字面引用、86待处置、4仅node引用；完整清单在generated JSON，不能将八分片称完整330文件suite。
- 具体修复：已核对test_run_lifecycle.py的7个独立测试及test_run_lifecycle_postgres.py的6个recording-connection测试，将两文件放入既有run-control-contracts分片，更新owning assertion。MemoryPersistence/service的间接复用未被误算作这7个测试已执行；postgres文件名未被误算为真实DB集成。
- 必需依赖：Docker/WSL/PG/Redis命令不在PATH、测试DSN/URL未配、常用本地端口关闭。实际PG terminal-order + Redis v4节点因缺配置产生7skip，runner返回exit6/required_dependency_missing。既有CI声明PG16/Redis7.4不能代替本轮实际结果；本轮未访问远程或安装OS设施。
- 已做补采：corecov十二显式文件174 passed/0 skips，覆盖452 app文件分母；app样本语句26.07%/分支7.97%，Runs样本48.66%/18.98%，仅局部unit/contract。CI/库存本批39用例通过。完整scope、真实PG rollback/concurrency和Redis运行仍待验收。
- D5退出条件：86文件与4个node-only范围逐项核对现行消费者/责任，给出真实required lane或有证明的retirement/acceptance owner；供应隔离的真实依赖和平台条件，按同runner/no-skip采完整约定并集，再决定阈值及阻断反例。不能靠永久deselect、假provider或目录名称使候选“全绿”。
- 兼容/风险：无删除生产/CLI/测试/selector，只扩大两个已确认的CI入口并增加库存字段；source envelope保留，独立review/GitHub CI未观测。原始执行证据归runner，不复制台账；三轮全量与B3–B5依然pending。

### 5.6 2026-10-19 当前实现与验收缺口

- 2026-10-20 同步 main 后，候选库存为 348 个 Python 测试文件。所有文件在既有 Linux 分片、Agent/Skill 或前端 Python 合同中各有一个完整文件 owner；Windows 平台节点另行补充。库存缺失、重复或只选择部分 node 会被 workflow 回归拒绝。PR #1850 的 CI 已启动，完整结果待实际验收。
- f853 app 源码的完整 Linux 选择范围已执行，结果与 owner 覆盖率见 [当前测量](quality-baseline-2026-10-13/QUALITY_SCORE.md#2026-10-19-full-linux-selection-observation)。12 个平台/真实 Sandbox 跳过仍逐项处置；原始失败已通过同输入 fixture/tool 修复重跑，不能当作 untouched-main 全绿。
- T1 已按 f853 的实际文件规模采用新生产代码文件 800 行上限；现有冻结文件不增长和受信 authority 保持。T2 已实现差异范围内可识别标准 logger 的窄结构化检测，保留未改日志及既有安全适配器；不是全仓日志格式迁移。两者的正反例及既有治理测试已通过；使用最终 Windows runner 的 architecture/code/source-authority 合组为 219 passed、0 skipped。
- 版本化 hooks 已交付精确 staged/pushed 快照检查，测试在临时仓库执行；无全局或原工作区 Git 配置修改。Windows 深临时路径问题经最小用例 red→green 修复，完整 runner 回归及 Foundation 用例通过。
- 候选全量首次为 10632 passed、58 failed、12 skipped。部门数字 authority fixture、replay mock 签名、Linux hook fixture 符号链接和旧 README 断言已修正；本地 12 文件回归为 824 passed、1 个 hook fixture 失败，修复后 hook 9 项复测通过。该证据不替代候选 Linux 全量重跑。
- 真实 OpenSandbox 凭据隔离仍未运行：独立 root 临时环境授权已取消。随后 SSH 用户更新测试目录被 UID 所有权拒绝，使用既有测试 UID 更新的授权也被取消；不继续写该远端副本或启动补测。本地独立工作继续。
- D5 的完整基线验收、覆盖率可信比较/阻断、整批独立审查与三轮全量仍未完成；B3–B5 事务/身份及保留策略删除迁移继续遵守前置条件。诊断 D09 已把三个离线报告及调用链迁入工具。
- 诊断父观察链已实现 SDK/Executor 首次捕获、callback/probe receipt 与同 Attempt/lease 调和关联、管理 HTTP/ZIP 保留、旧记录只读兼容及预算保护。独立审查发现的三个 P1 均经 red→green 修复并复核；最新 SDK/reconciler/诊断/HTTP 四文件 435 passed、0 skipped，前端 Run Monitor 36 passed，构建及浅深色桌面/390px 四个真实浏览器合成场景通过。Windows 仍有四个 owning 符号链接用例因权限跳过；新链路的真实 PostgreSQL 验收未执行。
- 派发版本快照已定位到 Docker 构建标记、运行时配置和既有 accepted reconciliation context，尚未接入新的 Run/Attempt 持久快照与查询；该持久化接缝随 D5 前置恢复后推进。真实 PostgreSQL 故障链、20 并发诊断详情/ZIP 性能及完整诊断 PRD 验收仍待完成。

## 6. 待人工决策清单

范围与政策已按 2026-10-19 的确认更新；剩余实测数值和验收条件分别列明。

| 编号 | 问题 | 当前决定 | 影响范围 |
|---|---|---|---|
| D1 | 完整改造范围与现有合同映射 | 2026-10-19 已授权完成本计划及关联修复；复用 §5.2 的 owner，阶段前置和独立审查保持 | 实现与验证，以及随后获准的当前候选提交、推送和草稿 PR；不授权合并或部署 |
| D3 | 已撤销：当前 ProfileDrive 读取与总时限均已实现 | 原抽样来自旧源码；以后只按具体失败场景立项 | 无新增可靠性修改 |
| D4 | latency_ms 与现行 terminal timing 的观测需求 | 按现有诊断 PRD 的字段、单位、缺失及性能目标核验，不另建一套指标 | 诊断 owning 实现与验收 |
| D5 | 新文件大小及整体/关键域 coverage 门槛 | 已批准完整实测基线不回退；新生产文件 800 行上限已按 f853 规模确定。完整 coverage 验收与可信阻断仍待完成 | 阶段 3；旧 330/86/4 库存与局部百分比不得当作新基线 |
| D6 | 本地 hooks | 版本化 pre-commit 快速静态/文档及 pre-push 受影响检查已实现并在临时仓库验证；CI 保留完整验证 | 隔离工作区验证；不改全局 Git 或原工作区 |

原 D2 已撤销：固定历史链接目标存在且已标记历史。范围与政策已确认；实际基线、阈值正反例和阶段完成证据仍须取得，不能以授权代替验收。

## 7. 进度与决策日志

| 日期 | 阶段 | 动作 | 关联 | 决策与理由 |
|---|---|---|---|---|
| 2026-10-10 | 1 | 安装施工图、抽样扫描、产出三件套草案 | 本文档 | 仅新增文档；抽样不等于全量验收 |
| 2026-10-13 | 1 | 修正审查 1–5：接口/事务库存、命令、必需要求/归档、历史引用和测试映射 | v1.1 | 用户授权修正文档；未启动重构、提交或发布 |
| 2026-10-13 | 1–2 | 从已 fetch 的 origin/main 建独立 harnessing，用户批准主会话接管失败 scout；源码/依赖图与 OSV 审计落盘，完成角色导航和 QUALITY_SCORE | v1.2 / f519d041 | 机械全仓与有界深查区分；母本保留，生产代码未修改 |
| 2026-10-13 | 3 核验 | 既有 code/architecture 173 用例，0 失败/跳过；Windows 深路径先复现失败，再用命令级 longpaths 验证 | CI §4.5 / QUALITY_SCORE | 没有改测试断言、治理策略或全局 Git 配置；当时T9/T10未闭环 |
| 2026-10-13 | 3 B2 | T9 detector/preflight、T10 runner采集 + locked test extra，88 grouped regression/132采样通过 | v1.3 / CI §4.6 | 分母452文件，样本非完整suite；独立review/CI、D5阈值与新test依赖审计仍待补证 |
| 2026-10-13 | 3 B2范围 | scope机器库存、两Runs CI入口和174用例补采；必需PG/Redis探针exit6 | v1.4 / CI §4.7 | 86路径与named-node待处置；本地真实设施缺失，不削弱no-skip或虚构阈值 |
| 2026-10-13 | PR交付 | 用户授权创建草稿PR；保留原Harness提交并合入main 11b7f521以满足受信祖先约束，补落后分支merge-base回归 | 本PR / 基线快照仍为f519d041 | 不放宽trusted检查，不改原feature工作区；新main已有#1802修复，旧测量是固定历史范围 |
