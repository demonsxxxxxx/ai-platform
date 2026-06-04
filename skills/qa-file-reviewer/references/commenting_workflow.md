# 批注写入工作流

## 使用场景

当用户默认接收 Word 批注版（`*_reviewed.docx`）或要在保留旧批注的前提下追加当前问题时，使用本工作流。输入来自多个职责分支合并后的问题清单，而不是单一顺序审核结果。

## 前置要求

- 默认生成批注版 Word（file key: `reviewed_docx`，标签：`批注 Word`）；仅当用户明确表示不需要时，才不要直接生成带批注文件
- 批注输入来自 `comment_plan.json`，只包含通过质量门禁的用户可见项：普通项必须有原文依据、稳定锚点和清晰建议；全文级 `global_summary` 可作为全局审核意见追加
- 普通详细报告、console 总数和 `review_result.json.summary.total_issues` 只统计用户可见/可交付问题；过滤项和 internal diagnostics 只能进入内部审计字段或诊断计数，不进入“问题清单”
- 审核结果需包含 `issues`、`branch`、`agent_role`、`location`、`original` 或 `anchor_text`、`anchor_span`、`document_zone`、`severity`、`suggestion`、`comment_text`

## 写入顺序

1. 保留源文档已有批注，不删除、不重写。
2. 优先使用稳定锚点定位：
   - `anchor_span`
   - `anchor_locator`
   - 表格坐标（例如 `P571 (T7R5C3)`，需能在 `document_map.json` 找到对应 table/row/cell）
   - 段落号
   - `anchor_text` / `original`
   - `location` + `document_zone`
   - 锚点匹配应容忍中英文引号和常见科学单位字形等价差异，例如 `"已作废"` 与 `“已作废”`、`µm` 与 `μm` 不应导致原位批注降级为文末批注。
3. 找不到稳定锚点、低置信、证据不足或建议空泛的问题默认过滤出 Word；只有显式全文级 `global_summary` 可追加为全局审核意见。
4. SDK/sub-agent 分支未完整执行、缺失 agent JSON、JSON 解析失败、schema 失败、内部路径和异常栈只属于内部诊断，不得写入 Word 批注。
5. 每条不同问题单独生成一条批注；同一原文、同一位置、同一修复目标的跨 agent 重复表述只保留一条用户可见批注。同一段落/表格单元格内，若不同 agent 使用了不同锚点但指向同一错误词、同一语义意图或同一修复目标，也应合并；但同一单元格内温度不一致和英文语法错误这类不同意图问题必须保留为独立批注。
6. risk-classifier 或任一 agent 将同一位置、同一语义意图标记为 `requires_external_evidence=true` 或 `comment_intent=request_check` 时，其他 agent 的同意图确定性表述不得继续进入 Word；应记为 `filtered_external_veto` 等内部诊断。但高置信、精确锚定、仅凭当前文档即可证明的中英/方法/条件矛盾，不应只因为风险分类要求核对原始记录而被隐藏。
7. 宽段落锚点与已有窄锚点冲突时，应先尝试稳定存在于原文的 source-side 短语；不得使用建议里的目标改写文本作为原文锚点。短泛词锚点（如 `than`、`Table`、`data`、`条件下`）必须过滤。裸数字锚点若只命中相邻已带 `%` 的值，应重试真实源单元格，否则过滤。
   - 可见批注的 `anchor_text` 必须是实际待改原文，不得是证据词、正确对照词、章节编号、泛词或建议中的替换目标。若只能定位到这些弱锚点，应进入内部诊断而不是 Word。
8. 相邻双语脚注允许局部重定位：当 agent 给出中文脚注段号，但英文问题原文在紧邻的英文脚注段落中，应定位到该英文原文片段，而不是作为 `anchor_failure_should_retry` 过滤。
9. 确定性检查与 agent 报告同一位置、同一或高度重叠原文锚点时，只保留一条批注，避免重复；即使锚点不同，只要同一位置同一语义缺陷和同一修复目标相同也应合并。确定性检查只覆盖可由当前文档局部证明的通用缺陷，如温度允差、重复脚注语法、重复标点或 dominant-term 混淆。
10. 同一确定性语法问题在重复脚注中多次出现时，聚合为一条代表性批注，并在依据中列出重复位置；agent 在其他重复脚注位置重新报告同一语法问题，也应合并到该代表性批注。调用 agent 对多处重复的同源错误也应聚合为代表性批注，不逐行插入。
11. 建议必须是单一、可执行的改法；`... or ...`、`either ... or ...` 或让审核人二选一的建议默认过滤，除非上游已根据批准模板明确选定唯一替换文本。
12. `comment_text` 使用短中文标签，默认不超过三行：`发现`、`原文`、`建议`。`发现` 合并问题判断和依据，`原文` 保留可追溯片段，`建议` 给出直接动作。长格式证据、重复节信息和表格示例必须压缩到适合 Word 批注阅读的长度；不得暴露 `P342` 这类 document_map 段落号、JSON 文件名、内部路径、SDK 错误、堆栈信息、`single_doc_internal` 等内部诊断字段。
13. 被过滤的 agent findings 必须保留内部分类，例如 `filtered_external`、`filtered_external_veto`、`filtered_low_confidence`、`filtered_low_value`、`filtered_quality_gate`、`anchor_failure_should_retry`、`schema_invalid`，用于质量复盘，不写入 Word。

## 生成后校验

批注版 Word 生成后必须执行文档有效性校验：

- `word/document.xml`、`word/comments.xml`、`word/_rels/document.xml.rels`、`[Content_Types].xml` 可解析
- `commentRangeStart`、`commentRangeEnd`、`commentReference` 数量匹配
- 文档可重新打开

## 对账字段

输出对账结果：

- `existing_comments`
- `comments_added`
- `comments_failed`
- `comments_expected`
- `comments_actual`

## 命令

```bash
python scripts/add_word_comments_v3.py <input.docx> <review.json> <output.docx>
```
