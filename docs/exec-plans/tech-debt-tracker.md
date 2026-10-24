# 技术债台账（tech-debt-tracker）

> 结构化库存，核对源码为起始 `origin/main f519d041f1bb155a788278abfd5e07becebe1767`（2026-10-13，北京时间）。当时的机械输入/分母见 [归档基线](completed/quality-baseline-2026-10-13/QUALITY_SCORE.md)；架构债、已确认缺陷与待核验覆盖深度分别说明，不从 import 或文件名直接推断运行故障/重复/无测试。原 Harness 专项已于 2026-10-24 停止，各批启动要求的历史记录见 [归档计划 §4.1](completed/harness-migration-2026-10.md#41-b3b5-启动前的接口与装配库存)；位置列以符号标注，2026-10-24 已在当前 main 核对仍存在。状态：待处理 / 处理中 / 已完成 / 已豁免（含理由）。

| ID | 标题 | 位置 | 类别 | 严重度 | 证据 | 修复建议 | 业务行为影响 | 关联批次 | 验收方式 | 状态 |
|---|---|---|---|---|---|---|---|---|---|---|
| TD-RUNS-001 | replay 用例中的 Conversations/Identity 具体写入依赖 | `app/runs/infrastructure/replay_postgres.py` 导入 `append_message`、`append_audit_log` | 架构债 | P1 | 直接依赖 message/audit persistence，当前 peer API 不提供等价 writer | 先列回放用例与协作者，复用 application/Bootstrap 装配；禁止 concrete adapter re-export | 目标为等价，需证明 | B3 | 完整 caller/selector 库存、同连接回滚/锁序/授权反例、退出 inventory | 待处理 |
| TD-RUNS-002 | replay 对 Skills/Streaming persistence 的依赖 | `app/runs/infrastructure/replay_postgres.py` 导入 `run_snapshots_postgres.*`、`append_event` | 架构债 | P1 | 同事务使用 Skill snapshot 与 event writer，不能只替换 import | 按最小 peer 能力与 caller-owned conn 定义契约，具体 repository 由 Bootstrap 注入 | 目标为等价，需证明 | B3 | 同上；检查 Skill pin/CAS 失败不留下部分写入 | 待处理 |
| TD-RUNS-003 | admission 对 Skills/Identity 内部能力的依赖 | `app/runs/infrastructure/capability_admission_postgres.py` 导入 `skills.infrastructure.*`、`identity.infrastructure.capability_distributions_postgres` | 架构债 | P1 | Skills resolution 与 Identity distribution/private error 依赖；MCP 已走 `app.mcp.api` | 查验现有 application 能力后按职责装配；保留授权复验和错误身份 | 目标为等价，需证明 | B4 | 原授权输入、同连接、拒绝/冲突回归及旧边退出；不迁回 MCP repository | 待处理 |
| TD-SKILLS-001 | catalog 的跨域与 legacy 装配依赖 | `app/skills/catalog.py` 导入 `identity.infrastructure.capability_distributions_postgres` 及根模块 | 架构债 | P1 | Identity 内部/legacy 依赖需梳理；同域 catalog_postgres import 合法且不证明重复实现 | 按 application/domain owner 分置契约，Bootstrap 注入；catalog 已依赖 api，避免反向导出形成环 | 目标为等价，需证明 | B5 | catalog 授权/不可用原因/manifest 回归、真实 caller 与启动导入检查 | 待处理 |
| TD-SANDBOX-001 | cleanup outcome 中的 event/audit 具体依赖 | `app/sandbox/infrastructure/leases_postgres.py` 的 `record_sandbox_runtime_cleanup_outcome` | 架构债 | P1 | 同一 supplied conn 写 event 和 audit | 复用 Sandbox application cleanup/现有 Bootstrap ledger 注入方式，保留原事务和失败语义 | 目标为等价，需证明 | B5 | 同连接及中途失败回滚、lease/Attempt 范围、outcome/错误保留与退出 inventory | 待处理 |

## 已完成与撤销的登记

- TD-GATE-004：已完成。`tools/check_documentation.py` 已在 backend preflight 以 `--base-ref` 运行，并由 `tests/test_check_documentation.py` 保护。
- TD-TEST-001/002、TD-GATE-002/003、TD-OBS-001：2026-10-24 随 Harness 专项停止撤销。它们是专项的门禁目标或"待核验"库存，登记时即注明未证明缺陷；以后发现具体未覆盖场景或门禁缺口，按普通 issue 登记实际证据。

- TD-GATE-001：已由 #1809 完成。合入的 main 中 `tools/architecture_governance.py` 的 `Finding.remediation`、JSON 与文本输出已有可执行修复方向；复用其 owning 回归，不再另建诊断层。

- TD-DOC-001/002：链接绑定固定提交且目标存在，文档已标注历史；保留依据，不安排删除。
- TD-SKILLS-002/TD-RUNS-004：相同 helper 导入及同域复用不是重复体证据，撤销重构任务。以后发现真实重复时重新提供行为/实现证据，不复用这些 ID。


## 当前源码库存

跨 context 内部导入边、热点文件等数量会随 main 变化，按需运行 `scripts/analysis/source_quality_inventory.py` 现测；2026-10-13 的快照（36 条边等）见 [归档基线](completed/quality-baseline-2026-10-13/QUALITY_SCORE.md)，不作为当前事实引用。上表只列已分析的首批，不代表全部剩余架构债。

## 优先级定义

| 级别 | 判定标准 | 处理时限 |
|---|---|---|
| P0 | 影响正确性/安全性/可用性；或阻塞后续改造 | 当前批次内必须解决 |
| P1 | 显著抬升维护成本或缺陷概率 | 计划内排期 |
| P2 | 整洁度/边际风险，可延后 | 有空再做，登记不丢 |
