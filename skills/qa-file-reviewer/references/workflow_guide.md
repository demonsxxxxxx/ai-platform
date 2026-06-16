# File Reviewer - 工作流指南

本文档只描述当前真实流程，并补充 skill 默认工作流约束。`run_qa_review.py` 仍然只做确定性预审、聚合、门禁和 Word 批注；语言、双语、数据一致性和风险分类等语义复核必须由调用 agent 在脚本外调度，再通过 `--agent-review-json` 合并。

## 默认工作流判定

- 用户请求 `审核`、`深度审核`、`平台全权审核`、`QA check`、`compliance check`、`bilingual check` 时，默认都按深度审核处理。
- 深度审核 / 平台全权审核必须强制走 Claude Agent SDK `Agent` 多 reviewer 路径。
- 默认流程先准备 deterministic context，再派发 reviewer，再收齐 shards/JSON，再跑 final merge reviewer，再回到 deterministic runner 生成 `reviewed_docx`。
- deterministic-only 不是默认主路径。
- 只有两种情况允许不走默认主路径：
  - 用户显式要求 `fast deterministic review`
  - Agent 工具在当前 runtime 不可用，且已留下降级记录

## 主流程

```text
输入 DOCX
  -> run_qa_review.py --no-comments --keep-json-artifacts
  -> 生成 document_map.json / review_result.json / comment_plan.json
  -> export_agent_review_context.py
  -> record_agent_review_decision.py (mode=claude_agent_multi_review)
  -> Claude Agent SDK Agent / subagent 派发多 reviewer
     -> qa-structure-reviewer
     -> qa-zh-language-reviewer
     -> qa-en-language-reviewer
     -> qa-bilingual-reviewer
     -> qa-data-consistency-reviewer
     -> qa-risk-classifier
  -> 收齐 reviewer shards JSON
  -> qa-final-merge-reviewer 合并 shards + deterministic context
  -> run_qa_review.py --with-comments --agent-review-json output/final_merge_agent_review.json
  -> validate_pipeline_gate.py
  -> 默认写回 Word 批注并输出 *_reviewed.docx
```

## 输入

- 一个 Word 文件（主要是 `input.docx`）
- `references/company-review-standards.md`
- `references/review-schema.md`
- `references/branch_agents.md`
- `references/rule-source-boundaries.md`
- `references/prompt_templates.md`
- `*_agent_review.json` reviewer shard 或 merge shard

说明：

- `prompt_templates.md` 描述每个 reviewer shard 与 final merge reviewer 的通用约束。
- `minimax-docx` 仍只承担本地 `review-map` 解析和 `audit` 结构校验，不代表调用大模型。

## 输出

默认交付：

- `reviewed_docx`（展示标签：`批注 Word`）
- 文件名模式：`*_reviewed.docx`

内部产物（默认不对外交付）：

- txt 审核报告（仅内部排查、失败辅助或管理员诊断）
- `document_map.json`
- `comment_plan.json`
- `review_result.json`
- `validation_report.json`
- `docx_audit_report.json`
- `agent_review_context.txt`
- reviewer shard JSON
- `final_merge_agent_review.json`
- `agent_routing_record.json`

可选行为：

- 仅在显式 `--no-comments` 时跳过批注写回（只用于 deterministic 准备阶段，不是默认用户交付路径）

## 职责分工

- `format`：格式硬规则
- `project_number`：项目号 / 模板残留识别
- `content_consistency`：断裂引用、组别编号、第三语言字符集残留、同格双语方法缩写遗漏等结构模式
- Claude Agent SDK reviewers：结构/格式、中文、英文、双语一致性、数据一致性、风险分类
- `qa-final-merge-reviewer`：汇总 reviewer shards，负责跨 shard 去重、冲突归并、risk veto 应用、全局语义整合
- `merge` / `gate`：聚合、门禁、人工复核清单维护

## 门禁

- `format`、`project_number`、`content_consistency` 都必须有执行状态。
- 深度审核 / 平台全权审核时，必须至少收齐以下 reviewer shard：结构/格式、中文、英文、双语一致性、数据一致性、风险分类。
- 缺任一必需 reviewer shard 时，不得假装深度审核已完成；要么补齐，要么降级并记录降级原因。
- 输出前必须执行 `scripts/validate_pipeline_gate.py <review_json>`。
- 定位不稳、证据不足、低置信、建议空泛或依赖外部资料的问题默认过滤，不进入 Word 批注；只在内部诊断、跳过计数或日志中保留原因。
- `comment_plan.json` 只包含通过质量门禁的用户可见项：普通问题必须有原文依据、稳定锚点和清晰建议；只有显式 `global_summary` 全文级问题允许追加到文末全局审核意见。
- 当前没有失败分支自动重跑；Agent 不可用时只能显式降级。

## 降级与日志

- 降级记录必须先写 `agent_routing_record.json`，包含：
  - `requested_review`
  - `execution_mode`
  - `agent_tool_available`
  - `downgrade_reason`
  - `required_reviewers`
  - `completed_reviewers`
- 降级原因至少写清：
  - Agent 工具不可用
  - 用户显式要求 deterministic-only
  - 哪些 reviewer 未执行
- 降级原因只进入内部 JSON / logs 和最终摘要；不得变成 Word 批注。

## 文档口径

- 当前流程以确定性职责分支、Claude Agent SDK 多 reviewer 语义审核、门禁和人工复核清单为核心。
- `run_qa_review.py` 不读取模型配置、不直接调用 LLM；Claude Agent SDK 或其他外层执行器只负责生成可验证的 agent JSON。
- 样本文档只用于 fixture 和泛化验证；不得把单个样本中的 literal 错误直接写成源码规则。
- 内部 JSON 是中间产物，默认不作为用户交付物。
- 用户主交付是 `reviewed_docx`（`批注 Word`）；txt 不是默认主交付。为降低人工复核成本，低质量或证据不足的问题不得为了“可见”而写入 Word。
- `summary.quality_score` 是确定性评分：按问题严重度、人工复核项、分支失败和批注定位扣分。
