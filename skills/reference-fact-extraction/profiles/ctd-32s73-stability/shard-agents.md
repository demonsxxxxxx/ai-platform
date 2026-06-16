# 事实分片角色契约

工作流请求 `project_profile_facts`、研究事实分片或修订后的事实分片时，使用本参考文件。协调器负责 step events；通用抽取和校验 subagent 只生成或审查 shard JSON。

## 执行模式

正式 CTD 事实抽取和核验只接受：

- `subagent`：使用独立 agent/thread 执行抽取，并使用独立校验轮次。抽取统一由 `reference_fact_extraction_agent` 执行，核验统一由 `reference_fact_validation_agent` 执行；每个角色只接收 request 文件、允许来源和当前 shard 范围。

`same_thread_separated_pass` 仅可作为本地调试或能力诊断记录，不得用于提交 passing shard，也不得进入最终 `fact-packet.json`。如果当前环境没有 subagent 工具，不要抽取正式 shard，不要把 `validation_status` 写成 `passed`，应暂停并报告 `SUBAGENT_REQUIRED`。

在 `agent_provenance.execution_mode` 中记录模式。除非确实由独立 agent/thread 执行角色，否则不要声称 `subagent`；正式可消费分片必须记录为 `subagent`。

## 共享输入

每个抽取或校验轮次都读取：

- `fact-extraction-request.json`
- `source-index.json` 或 `source-index.md`
- request 中列出的允许来源文件或已审查 OCR 产物
- `references/generic-fact-extraction.md`

Study shard 角色还要读取 `fact-extraction-request.json.validated_primary_context`，并将其作为批次/产品身份过滤器。Revision 角色还要读取相关校验报告，通常是 `fact-packet-validation.json` 或 shard validation report。

## Shard 角色映射

| 产物 | 抽取角色 | 校验角色 | 归属事实 |
| --- | --- | --- | --- |
| `project_profile_facts` | `reference_fact_extraction_agent` 按 project profile 范围抽取 | `reference_fact_validation_agent` 按 project profile 规则核验 | `project_profile`, `sources`, `docx_render_plan`, `missing_evidence`, `manual_review_items` |
| `long_term_stability_facts` | `reference_fact_extraction_agent` 按 long-term 范围抽取 | `reference_fact_validation_agent` 按 long-term 规则核验 | `long_term`, `missing_evidence`, `manual_review_items` |
| `accelerated_stability_facts` | `reference_fact_extraction_agent` 按 accelerated 范围抽取 | `reference_fact_validation_agent` 按 accelerated 规则核验 | `accelerated`, `missing_evidence`, `manual_review_items` |
| `stress_study_facts` | `reference_fact_extraction_agent` 按 stress-study 范围抽取 | `reference_fact_validation_agent` 按 stress-study 规则核验 | `stress_study`, `missing_evidence`, `manual_review_items` |

## 抽取轮次

1. 读取共享输入，并且只读取归属范围所需的来源区域。
2. 只抽取自己负责的事实。不要写其他 shard 的章节、`trend_charts`、已完成 `body_sections` 或完整 `fact-packet.json`。
3. 为产品身份、批次、条件、时间点、行、结果值、标准、结论和排除项保留证据引用。
4. 将未知、冲突或无证据支持的事实放入 `missing_evidence` 或 `manual_review_items`；不要编造值。
5. 将分片保存到 `allowed_next_events[].provided_artifacts` 请求的准确路径。

## 校验轮次

校验角色必须在把 `validation_status` 设为 passing 前，重新打开已保存分片和来源证据。至少检查：

- shard 只包含允许章节；
- 必需章节存在且非空；
- 产品名称、样品类型、批号、批次角色、对照品和项目编号与已验证 profile 一致；
- 每个纳入来源均为允许来源，且每个禁止来源仍被排除；
- 研究表格保留所有来源确认批次、时间点、`N/A`、`---`、方法、标准和来源引用；
- 缺失证据和人工审查项是显式记录的，而不是隐藏在猜测值里。

如果任何必需检查失败，不要将 shard 作为 passing 提交。修正抽取，或为协调器留下阻断项。

## Provenance 形状

每个 shard 必须包含：

```yaml
agent_provenance:
  execution_mode: subagent
  extraction_agent: reference_fact_extraction_agent
  validation_agent: reference_fact_validation_agent
  validation_status: passed | passed_with_warnings
  extraction_completed_at: "2026-05-31T08:00:00Z"
  validation_completed_at: "2026-05-31T08:05:00Z"
  source_materials_reviewed:
    - fact-extraction-request.json
    - source-index.md
    - "allowed source filename or OCR manifest"
  validation_checks:
    - section ownership checked
    - source traceability checked
    - batch/profile alignment checked
  validation_notes: "Short note for warnings or same-thread fallback; omit only when fully clean."
```

`validation_status: passed_with_warnings` 只有在警告已明确记录且不阻断渲染或证据追溯时才可接受。
