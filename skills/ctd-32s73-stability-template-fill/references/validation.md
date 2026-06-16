# 验证指南

最终验证由 `scripts/skill_step.py` 触发的内部状态机编排。不要直接调用验证器或内部状态机来推进生成、恢复或交付判断；脚本实现是验证事实源，本文只保留验收原则和人工复核边界。

## 最终交付门禁

只有同时满足以下条件，才能把 `filled.docx` 称为最终候选：

- `step-response.json.delivery_status` 为 `final_candidate`
- `workflow-summary.json.state` 为 `COMPLETED_FINAL`
- `workflow-summary.json.final` 为 `true`
- `validation_passed` 为 `true`
- `blocking_reasons` 为空

如果停在 `FACT_PROJECT_PROFILE_REQUIRED`、`FACT_STUDY_SHARDS_REQUIRED`、`FACT_EXTRACTION_REQUIRED`、`FACT_PACKET_REVISION_REQUIRED`、`MISSING_EVIDENCE_RECOVERY_REQUIRED`、`TREND_CHARTS_REQUIRED`、`TREND_CHARTS_REVISION_REQUIRED`、`BODY_SECTIONS_REQUIRED`、`BODY_SECTIONS_REVISION_REQUIRED`、`BODY_SKELETON_REQUIRED` 或 `COMPLETED_INTERMEDIATE`，当前 DOCX 只能称为中间产物。

## 交付前读取

回复用户前读取：

- `step-response.json`
- `workflow-summary.json`
- `artifact-index.md`
- `validation-report.md`
- `validation.json`

默认 `artifact_profile=delivery` 时，`filled.docx`、`validation-report.md`、`evidence-summary.md` 在 `delivery/`；事实包、manifest、结构快照和验证 JSON 多在 `_audit/`；过程日志在 `_debug/`。不要假设所有文件都平铺在 run 根目录。

## 必需交付包

最终候选至少应能定位到：

- 已填充 DOCX
- `source-index.json` 和 `source-index.md`
- `fact-packet.json`
- `fact-packet-validation.json`
- `trend-charts-validation.json`
- `body-sections-validation.json`
- `evidence-summary.md`
- `validation-report.md`
- `validation.json`
- 结构快照
- `table-render-manifest.json`
- `generation-manifest.json`

条件性产物：

- 使用 PDF/OCR 证据时，保留 OCR manifest、combined Markdown 或人工复核记录。
- 生成趋势图时，保留 `trend-charts-request.json`、`trend-charts-validation.json`、`trend-chart-input.json`、`trend-chart-manifest.json`、`figure-render-manifest.json` 和 PNG 目录。
- 生成正文语义段落时，保留 `body-sections-request.json` 和 `body-sections-validation.json`。
- 存在影响因素研究时，保留 `stress-section-render.json`。
- 存在缺失或不确定事实时，保留 missing-evidence 报告和人工审查项。

## 内容验收

生成内容不得残留未解释的：

- `XXX`
- `IPXXX`
- `{{` / `}}`
- 应具体化时仍出现的 `单抗/原液`
- 已完成申报资料来源名称，除非该处明确说明已排除
- 模板备选词，例如 `略微上升/略微下降/波动`
- 表格 prototype 固定时间单位与来源单位不一致，例如日制氧化数据套用了 `时间（小时）` prototype

生成内容应覆盖：

- 所有目标批次，或在 `omitted_batches` 中明确说明未渲染原因。
- 正确产品表达方式和样品类型。
- 长期和加速稳定性章节。
- 来源支持的影响因素研究章节；未开展的影响因素小节不得残留。
- 表注中 `---` / `N/A` 解释与表格实际标记一致。
- 趋势图引用与实际图题和 manifest 一致。

## 来源追溯

项目事实必须能追溯到 `fact-packet.json` 的字段、`source_ref`、`sources.allowed`、`missing_evidence` 或 `manual_review_items`。模板、写作模式、脚本和 `body_skeleton_docx` 不能引入事实包外的产品名称、批号、条件、时间点、结果、趋势、图表编号或结论。

证据摘要应说明：

- 当前有效模板路径和模板 ID。
- 使用的是内置模板。
- 每个允许来源的使用状态。
- 每个禁止来源的排除状态。
- OCR 来源的抽取方式和复核状态。
- 各批次已完成月份、来源冲突、批号规范化和人工确认项。

## 结构验收

验证器会自动输出占位符、章节、题注引用、表格结构、模板对比、趋势图 manifest、脚手架状态和证据摘要检查。人工复核重点看：

- 表/图编号和正文引用是否一致。
- 单批次收缩是否删除了不存在的第二批表格、题注和引用。
- 三批及以上扩展是否在对应角色块位置连续生成，而不是追加到文档末尾。
- 长期/加速表时间点和数据行是否与 `table-render-manifest.json` 一致。
- 趋势图数量是否覆盖长期 / 加速表中按批次和时间点整理、且已有 `0 月 + 后续时间点` 数值结果的检项；人工完成稿常见为长期 9~10 张、加速 9~10 张，不能只生成代表性单图。
- 影响因素表左侧表头 `检测指标` 是否保持 prototype 合并结构；数据区左侧两列分别承载检测项目及方法、质量标准。
- 新插入题注、表注、图题和代表性段落是否保持模板样式。

模板比对 warning 不一定代表失败。单批次收缩、多批次扩展、动态影响因素小节、调整时间点列或插入趋势图都可能造成合法差异；这类差异应在事实包、step options 或验证报告中解释。固定表头文本差异不应由 renderer 产生。

## Warning 处理

不要忽略 `validation.json` 的 warning。对每项 warning 做三选一处理：

- 修正事实包、正文骨架或输入来源后重新提交 step。
- 如果是业务允许的模板差异，写入 `expected_warnings` 并在验证报告解释原因。
- 如果需要人工处理，列入 `manual_review_items` 或最终说明。

常见人工审查项：

- 后续时间点关键检测项目不完整。
- 可疑数值或来源冲突。
- OCR 表格行列错位、页码缺失、单位或数值不确定。
- 来源批号拼写不一致。
- 缺失影响因素研究结论。
- 依赖外部 3.2.R.4 材料的图谱引用。
- 需要人工刷新的 Word 域、目录、表目录或图目录。

## 接受标准

输出可接受的前提：

- 事实可追溯至允许项目来源。
- 内容检查通过。
- 代表性结构检查通过。
- 模板契约检查通过，或偏离已明确记录并可解释。
- 无隐藏的脚手架状态或已知格式回退。
- 未解决来源问题均明确列为人工审查项。
