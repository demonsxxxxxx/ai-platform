# Shard Agent 契约

工作流请求事实分片或修订后的事实分片时使用本参考文件。协调器负责 step events；抽取和校验角色只负责生成或审查 shard JSON。

## 执行模式

正式事实抽取和核验只接受：

- `subagent`：使用独立 agent/thread 执行抽取，并使用独立校验轮次。

核心 skill 对所有 profile 和 shard 使用同一对通用角色：`reference_fact_extraction_agent` 负责抽取，`reference_fact_validation_agent` 负责独立核验。profile 通过 request 传入 shard 范围、字段、依赖和校验规则；不要为 CTD、法律、临床、金融或某个 shard 定义新的必需 agent 身份。

`same_thread_separated_pass` 仅可作为本地调试或能力诊断记录，不得用于提交 passing shard，也不得进入最终 `fact-packet.json`。如果当前环境没有 subagent 工具，不要抽取正式 shard，不要把 `validation_status` 写成 `passed`，应暂停并报告 `SUBAGENT_REQUIRED`。

在 `agent_provenance.execution_mode` 中诚实记录执行模式。正式可消费分片必须记录为 `subagent`。

## 共享输入

每个抽取或校验轮次都读取：

- 工作流 request 文件；
- `source-index.json` 或 `source-index.md`；
- request 中列出的允许来源文件或已审查 OCR 产物；
- `references/generic-fact-extraction.md`；
- `references/source-boundaries.md`；
- active profile 的 `extraction.md`、`shard-agents.md` 和 `validation.md`。

当 profile 声明了主上下文分片时，依赖分片角色还要读取已验证的主上下文分片。

## 抽取轮次

1. 只读取归属范围所需的来源区域。
2. 只抽取 active profile 为该 shard 声明的章节。
3. 为重要取值、判断、排除项和推导结论保留证据引用。
4. 将未知、冲突或无证据支持的事实放入 `missing_evidence`、`manual_review_items` 或 `conflicts`。
5. 不要写入其他 shard 的章节，也不要写完整的 `fact-packet.json`。
6. 将分片保存到 runtime 请求的路径。

## 校验轮次

校验角色在设置 passing 状态前，必须重新打开已保存分片和证据。至少检查：

- shard 只包含声明归属的章节；
- 必需章节存在且非空；
- 引用来源均为允许来源；
- 禁止来源仍被排除；
- 证据支持抽取值；
- 推导事实引用输入事实和推导逻辑；
- 缺失证据和人工审查项已显式记录；
- profile 特定校验规则通过。

如果必需检查失败，不要把该分片作为 passing 提交。

## Provenance 形状

每个 shard 顶层必须包含 `agent_provenance`：

```yaml
agent_provenance:
  execution_mode: subagent
  extraction_agent: reference_fact_extraction_agent
  validation_agent: reference_fact_validation_agent
  validation_status: passed | passed_with_warnings
  extraction_completed_at: "2026-06-01T08:00:00Z"
  validation_completed_at: "2026-06-01T08:05:00Z"
  source_materials_reviewed:
    - workflow request file
    - source-index.md
    - allowed source filename or OCR manifest
  validation_checks:
    - section ownership checked
    - source traceability checked
    - profile validation checked
  validation_notes: "Short note for warnings or same-thread fallback."
```

`passed_with_warnings` 只有在警告明确记录且不阻断证据追溯或下游消费时才可接受。

可选的 gap analysis 或 packet review agent 只能作为辅助审查使用；它们不是 passing shard 或 `fact-packet.json` 的必需 provenance 身份。
