# CTD 3.2.S.7.3 稳定性 Profile 校验

本 profile 在通用参考资料事实抽取框架内保留 CTD 稳定性事实抽取规则。CTD-native 事实包应按 `fact-packet-contract.md` 校验；本文件记录校验策略和失败处理，`fact-packet-contract.md` 定义必需 JSON 形状。检查字段值是否来自正确来源且证据充分时，使用 `field-extraction-map.md`。

## 必需的 profile-first 门禁

必须先校验 `project_profile`，然后才能消费任何研究分片。研究分片必须使用已验证 profile 来筛选产品身份、样品类型、批号、批次角色、对照品批号和项目编号。

## 必需 CTD-native 事实包章节

CTD-native 导出必须遵循 `fact-packet-contract.md`，并包含：

- `schema_version`
- `project_profile`
- `sources`
- `long_term`
- `accelerated`
- `stress_study`
- `trend_charts`
- `body_sections`
- `docx_render_plan`
- `missing_evidence`
- `manual_review_items`
- `agent_provenance`

## Profile 特定检查

- 批次必须保持为列表，不得压缩为 `main_batch` 或 `first_batch` 等单值字段。
- 除非允许来源明确支持，否则 `project_code` 不是展示用产品名称。
- `long_term`、`accelerated` 和 `stress_study` 分片不得包含彼此的章节。
- `long_term` 和 `accelerated` 必须包含 `fact-packet-contract.md` 定义的 `planned_timepoints`、`completed_timepoints_by_batch` 和 `marker_semantics`。
- `docx_render_plan.render_scope` 必须包含 `source_batch_count`、`rendered_batches`、`omitted_batches` 和 `batch_structure_action`。
- `sources.allowed[].status` 应为 `used`、`partially_used` 或 `unusable`。
- 除非下游 CTD 工作流明确请求，基础事实抽取期间应延后 `trend_charts` 和已完成的 `body_sections`。
- `---` 表示计划中的未来时间点尚未到达或尚无结果。
- `N/A` 表示已完成时间点不适用、未安排，或该检项无结果。
- 稳定性表格输入必须按来源时间点保留行、来源表头、单位、方法、标准和值。
- 影响因素研究表必须保留试验名称、条件、时间点、检项、方法、质量标准、值，以及敏感性/控制结论。
- 除非用户明确批准不同来源策略，已完成申报资料和 Word 锁文件禁止作为项目事实来源。
- PDF/OCR 事实必须记录抽取方法、页码/表格/章节、已审查文本和审查状态。

## 警告处理

只有当警告不影响必需事实或下游渲染时，才可放行。无证据支持的值、未解决来源冲突、缺失批次身份，或未经核验的 OCR 派生表格值，应阻断消费，或出现在 `manual_review_items` 中。
