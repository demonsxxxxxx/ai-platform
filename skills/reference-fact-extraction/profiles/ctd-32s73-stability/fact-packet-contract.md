# CTD 3.2.S.7.3 稳定性 Fact-Packet 契约

本文件定义由 `ctd-32s73-stability` profile 产出的 CTD-native `fact-packet.json` 形状，并供下游 `ctd-32s73-stability-template-fill` skill 消费。

通用 `reference-fact-extraction` skill 也可以输出 `reference-fact-packet-v1`，但当 `output_mode=domain_native` 时，必须导出下述形状，确保 CTD renderer 和 validator 无需 schema 转换即可消费。字段级含义、来源优先级、证据要求和常见错误见 `field-extraction-map.md`；本契约只定义事实包形状，不定义完整抽取逻辑。

## 顶层契约

```json
{
  "schema_version": "ctd-32s73-fact-packet-v1",
  "project_profile": {},
  "sources": {},
  "long_term": {},
  "accelerated": {},
  "stress_study": {},
  "trend_charts": {},
  "body_sections": {},
  "docx_render_plan": {},
  "missing_evidence": [],
  "manual_review_items": [],
  "agent_provenance": {}
}
```

下游 Word renderer 使用的所有项目事实都必须出现在该事实包中。模板、示例、写作指南和既往已完成申报资料不提供事实。

## Shard 归属

| Shard | 归属章节 |
| --- | --- |
| `project_profile_facts` | `project_profile`, `sources`, `docx_render_plan`, `missing_evidence`, `manual_review_items` |
| `long_term_stability_facts` | `long_term`, `missing_evidence`, `manual_review_items` |
| `accelerated_stability_facts` | `accelerated`, `missing_evidence`, `manual_review_items` |
| `stress_study_facts` | `stress_study`, `missing_evidence`, `manual_review_items` |

Study shards 必须在已验证 `project_profile` shard 可用后再抽取。

## `project_profile`

最小结构：

```json
{
  "product_name": "",
  "product_expression": "",
  "sample_type": "",
  "project_code": "",
  "specification": "",
  "reference_standard_batch": "",
  "batches": [
    {
      "batch_no": "",
      "role": "",
      "sample_type": ""
    }
  ]
}
```

规则：

- `batches` 必须是列表，即使只有一个批次。
- 不要把批次压缩为 `main_batch`、`first_batch` 或任何用于渲染的单值驱动字段。
- `product_name` 是正文、题注、图题和表题的首选展示名称。
- `product_expression` 可以提供更具体的表达，例如 `[product]原液`。
- `project_code` 用于内部编号、批号/方法/质量标准引用；除非允许来源明确支持，否则不得作为客户侧产品名称。

## `sources`

最小结构：

```json
{
  "allowed": [
    {
      "source_id": "SRC-001",
      "path": "",
      "type": "docx | xlsx | xlsm | pdf | image | txt | md | json | csv | tsv",
      "status": "used | partially_used | unusable",
      "extraction_method": "text_layer | builtin_source_indexer | paddleocr_vl | manual_reviewed_intermediate",
      "reader": {},
      "evidence_refs": []
    }
  ],
  "forbidden": [
    {
      "path": "",
      "reason": ""
    }
  ],
  "ocr_manifests": []
}
```

规则：

- 以 `已完成的申报资料` 开头的文件和以 `~$` 开头的 Word 锁文件默认禁止。
- `sources.allowed[].status` 应为 `used`、`partially_used` 或 `unusable`。
- OCR 派生事实必须标明 OCR 方法、页码/表格/章节、已审查文本和审查状态。
- `source-index` preview 本身不足以作为证据；事实必须引用允许来源或已审查 OCR/中间产物。

## `long_term`

最小结构：

```json
{
  "condition": "",
  "planned_timepoints": [],
  "completed_timepoints_by_batch": {
    "BATCH-NO": []
  },
  "marker_semantics": {
    "blank_within_completed_timepoint": "N/A",
    "blank_after_completed_timepoint": "---"
  },
  "tables_by_batch": {},
  "table_render_inputs": [],
  "trend_series": [],
  "trend_conclusion": "",
  "chromatogram_reference": []
}
```

规则：

- `planned_timepoints` 必须是列表。
- `completed_timepoints_by_batch` 必须将每个批号映射到其已完成时间点。
- `marker_semantics` 必须解释 `N/A` 和 `---` 的转换规则。
- `---` 表示计划中的未来时间点尚未到达或尚无结果。
- `N/A` 表示已完成时间点无此检项、未安排该检项或不适用。
- `table_render_inputs[]` 是主要的 renderer-facing 表格结构。不要使用 `target_table_index`。

## `accelerated`

最小结构与 `long_term` 相同：

```json
{
  "condition": "",
  "planned_timepoints": [],
  "completed_timepoints_by_batch": {
    "BATCH-NO": []
  },
  "marker_semantics": {
    "blank_within_completed_timepoint": "N/A",
    "blank_after_completed_timepoint": "---"
  },
  "tables_by_batch": {},
  "table_render_inputs": [],
  "trend_series": [],
  "trend_conclusion": "",
  "chromatogram_reference": []
}
```

除非允许来源明确定义不同规则，否则使用与长期稳定性相同的 `N/A` / `---` 语义。

## `stress_study`

最小结构：

```json
{
  "included_tests": [],
  "omitted_tests": [],
  "tables_by_test": {},
  "result_summary_by_test": {},
  "sensitivity_by_test": {},
  "control_recommendations": [],
  "final_conclusion": "",
  "table_render_inputs": []
}
```

规则：

- 只纳入针对该样品类型实际开展的试验。
- `omitted_tests` 即使为空也应为列表。
- 保留试验名称、条件、时间点、检项、方法、质量标准、结果和结论边界。
- 不要把多个独立影响因素试验合并成无法追溯的摘要。

## 基础抽取阶段的延后章节

除非下游 CTD 工作流明确请求对应阶段，基础事实抽取不应完成趋势图或正文段落。

使用：

```json
{
  "trend_charts": {
    "charts": [],
    "status": "deferred_until_trend_charts_required"
  },
  "body_sections": {}
}
```

下游 CTD skill 后续负责趋势图生成/校验和正文段落生成/校验。

## `docx_render_plan`

最小结构：

```json
{
  "template_id": "ctd-32s73-stability-template-v2",
  "render_scope": {
    "source_batch_count": 0,
    "rendered_batches": [],
    "omitted_batches": [],
    "batch_structure_action": "keep | shrink | expand"
  },
  "table_actions": [],
  "caption_actions": [],
  "manual_refresh_items": []
}
```

规则：

- `source_batch_count` 是所有来源确认批次的数量。
- `rendered_batches` 列出应出现在渲染文档中的批次。
- `omitted_batches` 即使无省略批次也必须是列表。每个省略批次需要 `batch_no` 和 `reason`。
- `batch_structure_action` 必须是 `keep`、`shrink` 或 `expand`。
- 渲染批次数少于模板默认批次容量时使用 `shrink`，多于模板默认容量时使用 `expand`。只有模板结构不变时使用 `keep`。
- `table_actions`、`caption_actions` 和 `manual_refresh_items` 应为列表。

## `agent_provenance`

事实包级 provenance 应为每个 shard 保留一项：

```json
{
  "project_profile": {},
  "long_term": {},
  "accelerated": {},
  "stress_study": {}
}
```

每项应包含 `shard-agents.md` 定义的抽取/校验 provenance 形状，包括 extraction agent、validation agent、validation status、execution mode、reviewed materials 和 validation checks。

## 审查字段

`missing_evidence` 和 `manual_review_items` 必须是顶层列表。未知、无证据支持、冲突或低置信度项目应在这里显式记录，而不是隐藏在猜测值中。
