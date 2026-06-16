---
name: reference-fact-extraction
description: >-
  当用户需要从参考资料/来源文件中抽取结构化事实、生成或校验带引用的事实包、把 PDF/Word/Excel/文本转换为有证据支撑的 JSON、核验抽取事实是否被来源支持，或为下游写作、渲染、分析及其他 skill 准备已验证事实时，使用本 skill。遇到“事实抽取”“生成事实包”“提取带来源的 JSON”“从参考资料整理证据”“validate extracted facts”“build a source-grounded fact packet”“诊断缺失/冲突证据”等请求时应触发。不要用本 skill 直接生成最终 Word/PDF/幻灯片交付物；本 skill 负责产出这些交付物所需的已验证事实。
---

# 参考资料事实抽取

本 skill 将允许使用的参考文件转换为结构化、来源可追溯、并经过独立核验的事实包。它是一个通用抽取框架：核心层负责来源边界、证据、事实分片、核验、校验和装配；领域 profile 负责定义特定领域中“事实”的含义。

## 公开入口

执行工作流时，只使用 `scripts/skill_step.py` 作为公开运行入口。内部脚本属于实现细节，不应直接调用来推进工作流状态。

```powershell
python .\scripts\skill_step.py `
  --event .\path\to\step-event.json `
  --response-out .\outputs\run-001\step-response.json
```

每次运行后，读取响应文件，并且只按照同一次运行响应中的 `allowed_next_events` 和 `required_artifacts` 继续。

## 核心不变量

- 事实只能来自允许的来源文件，或来自允许来源派生并已审查的 OCR/中间产物。
- 模板、示例、写作指南、脚本和历史输出默认只提供结构；除非用户明确标记为允许的事实来源，否则不得提供项目事实。
- `source-index` 是导航地图，不是事实包，本身也不足以作为证据。
- 没有证据就没有事实：缺失或歧义信息应进入 `missing_evidence`、`manual_review_items` 或 `conflicts`。
- 没有核验就不能消费：参考文件抽取出的事实必须先经过独立核验，才能被下游 skill 使用。
- 正式事实抽取必须使用 `subagent` 分离抽取和核验。核心 skill 固定使用通用 `reference_fact_extraction_agent` 和 `reference_fact_validation_agent`；profile 只定义 shard、字段、证据和校验规则，不定义领域专用 agent 名。没有 subagent 能力时，不得产出可消费 `fact-packet.json`；应暂停并报告 `SUBAGENT_REQUIRED`。
- 装配器只合并已校验通过的分片；不得推断或新建项目事实。
- 领域语义归 profile 所有。通用核心不得硬编码 CTD、法律、临床、金融或其他领域含义。

## 工作流

1. 在 source manifest 中登记允许来源和禁止来源。
2. 从允许文件生成 `source-index.json` / `source-index.md`。
3. 使用上游 event 显式提供的 `profile_id`。缺失时停止/失败，不得自动选择、猜测或回退到 `generic`；`generic` 只可作为已声明 profile 的输出模式，不能作为 profile fallback。
4. 如果 profile 定义了主上下文分片，先抽取该分片。
5. 核验并校验主上下文分片。
6. 使用已验证的上下文抽取依赖的领域分片。
7. 核验并校验每个分片。
8. 从已校验分片装配 `fact-packet.json`。
9. 校验事实包，并生成审计/核验报告。

## Profile（领域配置）

Profile 位于 `profiles/<profile-id>/`，用于定义领域特定的抽取要求。

- `profiles/ctd-32s73-stability/`：第一个领域 profile，来自 CTD 3.2.S.7.3 稳定性事实抽取工作流。

`generic` 可以作为 profile 显式声明的输出模式，表示导出通用 `reference-fact-packet-v1` 形状；它不是 `profile_id`，也不得作为缺失 profile 时的回退。

创建或修改 profile 前，先阅读 `references/profile-contract.md`。

## Subagents

本 skill 按 Claude Code sub-agents 文件格式在 `agents/` 目录内捆绑两个正式角色：

- `agents/reference_fact_extraction_agent.md`：通用事实分片抽取 agent。只负责从允许来源抽取当前请求的 shard，并写入证据、缺失证据、冲突和 provenance。
- `agents/reference_fact_validation_agent.md`：通用独立核验 agent。重新打开已保存 shard / fact-packet 及来源证据，检查来源边界、字段归属、profile 规则和消费门禁。

正式事实抽取不得用同一轮对话同时完成抽取和核验。需要可消费事实时，先委托 extraction subagent 生成 shard，再委托 validation subagent 独立核验。

## 参考文件

- `references/generic-fact-extraction.md`：完整工作流和产物模型。
- `references/source-boundaries.md`：允许/禁止来源处理，以及 OCR/索引规则。
- `references/profile-contract.md`：profile JSON 契约和输出模式。
- `references/shard-agent-contract.md`：抽取/核验角色契约和来源追溯结构。
- `references/validation.md`：校验、冲突和消费门禁。

## 输出边界

主要输出是已验证的 `fact-packet.json`。下游 skill 可以基于该事实包渲染 Word 文档、报告、图表或 API payload，但本 skill 自身不得在渲染过程中引入新事实，也不应直接写最终交付物。
