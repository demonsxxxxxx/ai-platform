# 技术债台账（tech-debt-tracker）

> 结构化库存，核对源码为起始 `origin/main f519d041f1bb155a788278abfd5e07becebe1767`（2026-10-13，北京时间）。完整机械输入/分母见 [QUALITY_SCORE](../QUALITY_SCORE.md)；架构债、已确认缺陷与待核验覆盖深度分别说明，不从 import 或文件名直接推断运行故障/重复/无测试。各批启动要求见 [修订计划 §4.1](active/harness-migration.md#41-b3b5-启动前的接口与装配库存)，全流程要求处置见 §5.1。状态：待处理 / 处理中 / 已完成 / 已豁免（含理由）。

| ID | 标题 | 位置 | 类别 | 严重度 | 证据 | 修复建议 | 业务行为影响 | 关联批次 | 验收方式 | 状态 |
|---|---|---|---|---|---|---|---|---|---|---|
| TD-RUNS-001 | replay 用例中的 Conversations/Identity 具体写入依赖 | `app/runs/infrastructure/replay_postgres.py:17-18` | 架构债 | P1 | 直接依赖 message/audit persistence，当前 peer API 不提供等价 writer | 先列回放用例与协作者，复用 application/Bootstrap 装配；禁止 concrete adapter re-export | 目标为等价，需证明 | B3 | 完整 caller/selector 库存、同连接回滚/锁序/授权反例、退出 inventory | 待处理 |
| TD-RUNS-002 | replay 对 Skills/Streaming persistence 的依赖 | `app/runs/infrastructure/replay_postgres.py:38-42` | 架构债 | P1 | 同事务使用 Skill snapshot 与 event writer，不能只替换 import | 按最小 peer 能力与 caller-owned conn 定义契约，具体 repository 由 Bootstrap 注入 | 目标为等价，需证明 | B3 | 同上；检查 Skill pin/CAS 失败不留下部分写入 | 待处理 |
| TD-RUNS-003 | admission 对 Skills/Identity 内部能力的依赖 | `app/runs/infrastructure/capability_admission_postgres.py:5,11-13,20-24` | 架构债 | P1 | Skills resolution 与 Identity distribution/private error 依赖；MCP 已走 `app.mcp.api` | 查验现有 application 能力后按职责装配；保留授权复验和错误身份 | 目标为等价，需证明 | B4 | 原授权输入、同连接、拒绝/冲突回归及旧边退出；不迁回 MCP repository | 待处理 |
| TD-SKILLS-001 | catalog 的跨域与 legacy 装配依赖 | `app/skills/catalog.py:11-33` | 架构债 | P1 | Identity 内部/legacy 依赖需梳理；同域 catalog_postgres import 合法且不证明重复实现 | 按 application/domain owner 分置契约，Bootstrap 注入；catalog 已依赖 api，避免反向导出形成环 | 目标为等价，需证明 | B5 | catalog 授权/不可用原因/manifest 回归、真实 caller 与启动导入检查 | 待处理 |
| TD-SANDBOX-001 | cleanup outcome 的 application owner 迁移候选 | `app/sandbox/application/runtime_cleanup.py`、`app/bootstrap/sandbox_cleanup.py` | 架构债 | P1 | 同一 supplied conn 顺序写 event 和 audit | 具体 SandboxCleanupOutcomes 复用既有 port；Bootstrap 晚绑定双 writer，旧 SQL adapter 业务体已移除 | 离线字段/控制流等价，真实 PG 待验 | B5 | 同连接及中途失败真实回滚、锁/取消/重试、lease/Attempt 范围、实际 diff 独审 | 候选；未关闭，待真实事务验收 |
| TD-TEST-001 | 后端测试覆盖深度待核验 | 计划 §3.5 的实现→测试映射 | 测试库存，未证明缺陷 | P2 | product_events/session_actions/session projection/tracing/Sandbox 等已有直接消费者用例；整体/分支覆盖率未测 | 先审实际输入、异常、并发分支，只对确认遗漏登记具体场景并复用 owning selector | 本项不改生产行为 | B6 | 每个新增用例对应具体缺口且故意破坏实现时失败；不按测试文件名判缺口 | 待处理 |
| TD-TEST-002 | 前端测试覆盖深度待核验 | `components/workbench/WorkbenchRightPanel.tsx`、`WorkbenchProjectionPages.tsx`；`components/documents/DocumentPreview.tsx`（均在 frontend/web/src） | 测试库存，未证明缺陷 | P2 | Workbench 有 source-contract 断言，DocumentPreview 有真实 mounted 生命周期用例；原 ModelSelector 路径未找到 | 使用计划 §3.5 正确路径及现有测试，只补具体遗漏；区分 source/mounted/端到端 | 本项不改生产行为 | B7 | 实现/消费者路径存在、现有 selector 可达、具体交互/迟到回调等缺口的红测 | 待处理 |
| TD-GATE-002 | 新文件独立大小 hard gate 待基线评估 | `architecture-policy.json`、`tools/code_governance.py:19-27` | 门禁范围 | P2 | 已有冻结增长 hard gate 和数量/净增/热点 advisory；不能说完全无文件大小治理 | 分别测现有范围；如需新 hard gate，先确认可复算阈值与豁免，复用现有 owning 工具 | 若改变阻断范围须单独说明 | B2 | 冻结/新文件正反例及 hard/advisory 区分，不削弱现有 required checks | 待处理 |
| TD-GATE-003 | 完整 coverage 基线/阈值与可信阻断尚缺 | `tools/run_test_stage.py`、workflow_test_inventory、CI workflows | 门禁范围 | P1 | 174局部用例/452源文件分母；330候选测试中86无字面引用、4node-only；真实PG/Redis探针7skip且exit6 | 逐项处置完整scope并供应隔离真实设施/平台条件，再定D5；不从26.07%局部样本设全套阈值，不削弱no-skip | 保留旧CLI/v1/required；增加两个确认遗漏的Runs CI入口，无生产变更 | B2 | 完整scope/依赖/skip绑定基线与获准阈值反例；§5.5列退出条件 | 处理中：本地采集通过、scope/依赖阻塞 |
| TD-GATE-004 | 文档门禁候选待独立review/CI验收 | `tools/check_documentation.py`、backend preflight、local-test-execution | 文档门禁 | P2 | checker回归保护本地link/anchor；状态和导航由编辑负责，固定历史/代码例正确排除；preflight比较完整base SHA，作者变更触发正常修复 | 在既有review流程核验candidate preflight；目标删除/移动继续独立caller inventory，不把外链/语义新鲜度当已检测 | 保留existing required/trusted与单一产品authority | B1/B2 | owning反例/local无finding已通过；独立review/GitHub CI待验证 | 处理中：本地实现已验证 |
| TD-OBS-001 | Run elapsed/SDK timing 观测需求待复核 | `app/worker.py`、`app/platform/tracing.py`、`tests/test_terminal_stage_timing.py` | 观测需求，未证明缺陷 | P2 | PR #1691 已修归零；当前 main 已有 terminal timing，用现行指标判断剩余需求 | 先定义未满足的问题和指标来源，确认后才立项拆字段 | 本项不改生产行为 | 未排期 | 当前 owning 指标/用例映射及明确需求；源码不能证明部署状态 | 待处理 |

## 已完成与撤销的登记

- TD-GATE-001：已由 #1809 完成。合入的 main 中 `tools/architecture_governance.py` 的 `Finding.remediation`、JSON 与文本输出已有可执行修复方向；复用其 owning 回归，不再另建诊断层。

- TD-DOC-001/002：链接绑定固定提交且目标存在，文档已标注历史；保留依据，不安排删除。
- TD-SKILLS-002/TD-RUNS-004：相同 helper 导入及同域复用不是重复体证据，撤销重构任务。以后发现真实重复时重新提供行为/实现证据，不复用这些 ID。


## 全量静态库存与当前有界观察

后端静态图记录 36 条跨 context 内部导入边。上表 Runs/Skills/Sandbox 是已分析的首批，其他边（含 Agent Apps 的 13 条）保留在生成库存中待用例/兼容性评审，不能据此一次批量迁移或宣称只有五处剩余债。局部/type-only imports 也在静态图中；已核对的 OpenSandbox 环含函数内延迟导入，不登记为启动故障。

| 项 | 当前证据与边界 |
|---|---|
| 依赖已知漏洞 | B1 Windows/Python test 环境85包OSV为0已知/0跳过；B2新增coverage test依赖的刷新TLS超时，不包含在旧报告，不覆盖Linux/Node/业务安全 |
| Callback scope/锁序 | shared Sandbox authority + Bootstrap，Run-before-Attempt、persisted tenant、唯一 lease/Attempt/token-generation 回归通过 |
| Replay / cleanup | 当前 principal、精确 pins、malformed transport 前置拒绝、event/audit supplied connection 的 owning 用例通过；未证明真实 PG 并发/回滚 |
| ProfileDrive | 当前读取/总操作时限及取消/关闭/首错保护已实现，生命周期用例通过；撤销 read=None 疑问 |
| 完整业务安全与部署 | 未验收；没有依据宣布 P0 清零或线上通过 |

2026-10-10 密钥/SQL/logger 的模式抽样保留为历史扫描范围，不再作为当前源码安全结论；B2有界coverage样本/采集与T9候选已实现，完整suite阈值、独立review/CI及实际运行验收仍pending。

## 优先级定义

| 级别 | 判定标准 | 处理时限 |
|---|---|---|
| P0 | 影响正确性/安全性/可用性；或阻塞后续改造 | 当前批次内必须解决 |
| P1 | 显著抬升维护成本或缺陷概率 | 计划内排期 |
| P2 | 整洁度/边际风险，可延后 | 有空再做，登记不丢 |
