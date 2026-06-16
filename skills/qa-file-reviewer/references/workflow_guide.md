# File Reviewer - 工作流指南

本文档只描述当前真实流程。当前脚本实现由 `format`、`project_number`、`content_consistency` 三个确定性职责分支组成。语言、双语和语义复核由调用 agent 在脚本外自行调度，并通过 `--agent-review-json` 合并。默认用户交付为带批注 Word（`*_reviewed.docx`）。

## 主流程

```text
输入 DOCX
  -> (可选) Claude Agent SDK 外层执行器触发
  -> minimax-docx review-map（本地 DOCX/OOXML 解析引擎，不是 LLM 调用）
  -> 生成 document_map.json
  -> scripts/run_qa_review.py 确定性预审
  -> 执行确定性职责分支
     -> format
     -> project_number
     -> content_consistency
  -> (可选) export_agent_review_context.py --context-version v2
     -> agent_context_manifest.json
     -> agent_review_context.part-*.txt 或 agent_review_context.txt
     -> review_units.jsonl
     -> review_blocks.jsonl
     -> bilingual_pairs.jsonl
     -> agent_context_metrics.json
  -> (可选) validate_agent_context_package.py agent_context_manifest.json
  -> (可选) 调用 agent / Claude Agent SDK 语义复核并输出 *_agent_review.json
  -> run_qa_review.py --agent-review-json 合并语义 findings
  -> 汇总结果
  -> 生成 branch_execution_manifest
  -> validate_pipeline_gate.py
  -> 默认写回 Word 批注并输出 *_reviewed.docx
  -> txt 审核报告仅内部排查
  -> 默认不对外交付内部 JSON
```

## 输入

- 一个 Word 文件（主要是 `input.docx`）
- `references/company-review-standards.md`
- `references/review-schema.md`
- `references/branch_agents.md`
- `references/rule-source-boundaries.md`
- `*_agent_review.json`（可选，调用 agent 语义复核输出）

说明：

- `prompt_templates.md` 只描述调用 agent 进行语义复核时的通用约束，不代表“整个审核靠单一 prompt 完成”。
- 调用 agent 时，以 `agent_context_manifest.json` 列出的 `agent_review_context.part-*.txt` 或 `agent_review_context.txt` 为主要阅读材料；`review_units.jsonl`、`review_blocks.jsonl`、`bilingual_pairs.jsonl` 只是机器索引/辅助信息，不是主要审查材料。
- Agent review is full-flow semantic QA. Bilingual comparison is one review domain, not the whole workflow. The agent-facing review surface is the context package, and the returned JSON must separate problem location from evidence location.
- The v2 context package may use the `locator-v2.2` profile. This profile keeps all v2 filenames and CLI arguments, but improves the TXT review surface with block cards, unit cards, section paths, review domains, and metrics.
- `minimax-docx` 在当前流程中承担本地 `review-map` 解析和 `audit` 结构校验，不代表调用 MiniMax 大模型。若要替换它，必须同步替换 `document_map.json` 生成和 DOCX audit 两个运行契约，不能只删除文档说明。

## 输出

默认交付：

- `reviewed_docx`（展示标签：`批注 Word`）
- 文件名模式：`*_reviewed.docx`

内部产物（默认不对外交付）：

- txt 审核报告（仅内部排查、失败辅助或管理员诊断）
- `document_map.json`
- `comment_plan.json`
- `validation_report.json`
- `docx_audit_report.json`
- `agent_context_manifest.json`
- `agent_review_context.txt`
- `agent_review_context.part-*.txt`
- `review_units.jsonl`
- `review_blocks.jsonl`
- `bilingual_pairs.jsonl`
- `agent_context_metrics.json`
- `*_agent_review.json`
- 其他内部 JSON 中间产物

可选行为：

- 仅在显式 `--no-comments` 时跳过批注写回（不作为默认用户交付路径）

## 职责分工

- `format`：格式硬规则
- `project_number`：项目号 / 模板残留识别
- `content_consistency`：断裂引用、组别编号、第三语言字符集残留、同格双语方法缩写遗漏等结构模式
- 调用 agent：中文、英文、双语、语义全文复核，不属于脚本内置分支
- `merge` / `gate`：聚合、门禁、人工复核清单维护

## 门禁

- `format`、`project_number`、`content_consistency` 都必须有执行状态。
- 输出前必须执行 `scripts/validate_pipeline_gate.py <review_json>`。
- 调用 agent 的主定位契约是 `unit_id + anchor_quote`。非全文 agent finding 缺少 `unit_id` 时直接过滤。`paragraph_index`、`P...`、`XML:...`、`T...R...C...` 仅用于兼容或可读提示，不能代替主契约。
- Problem location and evidence location are separate: `unit_id + anchor_quote` chooses the editable phrase for Word insertion; `evidence_unit_ids` only proves the issue and must not become the insertion target.
- 定位不稳、证据不足、低置信、建议空泛或依赖外部资料的问题默认过滤，不进入 Word 批注；只在内部诊断、跳过计数或日志中保留原因。
- 规则化审核不能一刀切过滤。字号、页脚、页边距、连续多个空格、表格单元格前导缩进等有公司标准或精确原文依据的问题，若存在稳定锚点，应作为用户可见问题；只有缺少锚点、只能属性推断或属于统一风格偏好的项目才保留为内部诊断。
- 若 `anchor_quote` 不在命名 `unit_id` 内，必须过滤该 finding；不允许全文搜索兜底、跨段搜索或文末追加后再写 Word 批注。
- Agent 不得靠自述“approved terminology / 受控术语表 / SOP requires”制造受控来源；官方缩写全称、品牌、物料、注册名称等外部权威断言只有在运行器提供并验证术语/模板来源时才能进入 Word。
- `comment_plan.json` 只包含通过质量门禁的用户可见项：普通问题必须有原文依据、稳定锚点和清晰建议；只有显式 `global_summary` 全文级问题允许追加到文末全局审核意见。
- 写回 Word 时保留人工批注并剥离旧自动审核批注；重传已审核过的 `*_reviewed.docx` 不应把历史自动问题混入本次结果。
- 用户可见批注中的 `发现` 和 `建议` 必须是中文；英文原文只保留在 `原文` 或替换片段中。
- 当前没有失败分支自动重跑。

## 文档口径

- 当前流程以确定性职责分支、门禁和人工复核清单为核心。
- 语言、双语、语义、表格数据、公式、引用、项目号、样品/批号等全流程审核由调用 agent 自行调度，输出 JSON 后通过 `--agent-review-json` 交给 runner 校验、合并和写批注，不能写成脚本内置 LLM 自动分支。
- 调用 agent 先读 context package 中的 TXT，再引用 `unit_id` 和 `anchor_quote` 回传问题；脚本再用 `review_units.jsonl`、`review_blocks.jsonl`、`bilingual_pairs.jsonl` 与 `document_map.json` 做确定性锚定。
- `run_qa_review.py` 不读取模型配置、不直接调用 LLM；Claude Agent SDK 或其他外层执行器只负责生成可验证的 agent JSON。
- 样本文档只用于 fixture 和泛化验证；不得把单个样本中的 literal 错误直接写成源码规则。
- 内部 JSON 是中间产物，默认不作为用户交付物。
- 用户主交付是 `reviewed_docx`（`批注 Word`）；txt 不是默认主交付。为降低人工复核成本，低质量或证据不足的问题不得为了“可见”而写入 Word。
- `summary.quality_score` 是确定性评分：按问题严重度、人工复核项、分支失败和批注定位扣分。
