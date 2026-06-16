# 事实提取指南

本指南定义了填充 CTD 3.2.S.7.3 稳定性数据模板前必须提取的事实。模板只提供结构和格式；项目参考文件提供事实。抽取结果必须满足本 profile 的 `fact-packet-contract.md`；该文件是 CTD-native `fact-packet.json` 的字段契约，并与下游 `ctd-32s73-stability-template-fill` validator 对齐。字段级查找规则见 `field-extraction-map.md`；不要只靠字段名猜测要找什么数据。

最小事实包示例见 `profiles/ctd-32s73-stability/example-fact-packet.json`。示例仅用于说明字段形状和标记语义，不是项目事实来源，不得把其中的 IP000、批号、条件或检测结果复制到真实输出。

## 内容来源边界

事实抽取阶段采用 profile-first shard 模式：不要由单个抽取过程直接手写完整 `fact-packet.json`。先在 `PRIMARY_CONTEXT_SHARD_REQUIRED` 由通用 `reference_fact_extraction_agent` 按 `project_profile` 范围生成并由 `reference_fact_validation_agent` 校验 `fact-shards/project-profile-facts.json`；通过后，`DOMAIN_SHARDS_REQUIRED` 再由同一对通用 agent 基于已验证 profile 中的产品身份、样品类型、批号、批次角色、对照品批号和项目编号，分别生成并校验 `fact-shards/long-term-stability-facts.json`、`fact-shards/accelerated-stability-facts.json`、`fact-shards/stress-study-facts.json`。每个 shard 顶层必须带符合 `references/shard-agent-contract.md` 和本 profile `shard-agents.md` 的 `agent_provenance`，否则 `validate_fact_shards.py` 会拒绝。runtime 只把已验证分片装配为最终 `fact-packet.json`，供 renderer、正文、趋势图和验证器使用。

`fact-packet.json` 仍是 `filled.docx` 中项目事实的最终渲染契约。凡是会进入正文、题注、表注、趋势图题、结论段或结果表的数据和判断，都必须先在事实分片中表达，装配后出现在事实包中，并带有允许来源的证据定位或明确的人工审查项。

模板中的正文叙述应使用语义化整段占位符，例如 `{{LONG_TERM_INTRO}}`、`{{ACCELERATED_TREND_SUMMARY}}` 和 `{{FINAL_STABILITY_CONCLUSION}}`。事实包通过 `body_sections` 提供这些占位符的最终段落文本；pipeline 负责在表格渲染前把整段文本替换进模板。

`body_sections` 不再要求在初始事实抽取阶段一次完成。状态机会先校验结构化事实和表格数据，再校验趋势图数据，随后停在 `BODY_SECTIONS_REQUIRED`，生成 `body-sections-request.json`。起草 `body_sections` 前必须读取 `references/writing-patterns.md`，尤其是“真实结果稿经验沉淀”；事实场景复杂时再读取 `references/writing-fact-cases.md`。这些文件只用于段落组织、连接词、结论边界、事实取舍示例和反例识别，不是项目事实来源；正文中的产品名、批号、条件、时间点、结果、趋势、表号和图号仍必须来自当前事实包和允许来源。

模板、写作模式、脚本和项目专用 body/skeleton 只能提供结构、样式、固定标签、表位、题注编号和表达框架。不要把只存在于 `body-and-skeleton-filled.docx`、临时脚本、写作模式或旧申报资料中的项目事实当作可交付事实；如果起草时发现事实包缺项，先修订事实包并通过 step 重新提交。

## 目录

- `内容来源边界`：事实抽取先写并校验 profile 分片，再写 3 个研究分片，`filled.docx` 项目事实只能来自装配后的事实包。
- `source-index 中间层`：缺少事实包时如何先读来源索引。
- `来源优先级`、`项目身份`、`PDF / OCR 来源处理`：事实来源边界和证据定位规则。
- `长期稳定性`、`加速稳定性`、`影响因素 / 强制降解研究`：各研究类型需要提取的字段和判断规则。
- `fact-shard v1 契约` 和 `fact-packet v1 契约`：抽取分片、装配后的事实包顶层结构、表格渲染输入和字段规则。
- `批次数据模型`、`趋势图数据抽取`、`稳定性工作簿批次块扫描`、`冲突处理`：常见复杂场景的专项规则。

## source-index 中间层

当状态机停在 `PRIMARY_CONTEXT_SHARD_REQUIRED` 或 `DOMAIN_SHARDS_REQUIRED` 时，会先生成：

- `source-index.json`
- `source-index.md`
- `fact-extraction-request.json`

`source-index` 是 raw sources 到事实包之间的通用索引层，只负责把允许来源变成可追溯地图，不负责最终项目事实判断。它会记录 DOCX 段落和表格摘要、Excel workbook 的 sheet / 行列预览、内置 `reader` 元数据、已有 OCR manifest 和 combined Markdown、需要 OCR 的 PDF / 图片、以及被排除的 forbidden sources。允许来源中包含 PDF 时，PDF 必须先解析或取得已有 OCR 产物；不能因为 OCR 不是首选事实来源就跳过解析。

生成 `project_profile_facts` 时，先用 `source-index.md` 快速定位候选段落、表格和工作表，再回到允许来源或已审查 OCR 中间产物确认事实。生成 3 个研究分片时，必须同时读取 `fact-extraction-request.json.validated_primary_context`，用已验证批号和批次角色过滤稳定性数据候选范围。不要把 `source-index` 中的预览文本直接当成完整证据；关键事实仍需保留 `source_file`、页码 / 表号 / sheet / 行列或段落索引等证据引用。装配器只合并已验证分片，不推断新事实。

## 来源优先级

以下优先级只适用于用户允许使用的当前项目来源文件。已完成申报资料默认不属于事实来源，也不属于默认可读取资料。

按以下顺序使用允许来源：

1. 稳定性结果和趋势：稳定性报告或稳定性数据工作簿。
2. 影响因素 / 强制降解结论：影响因素研究报告。
3. 研究条件和计划时间点：方案；如报告有更新，以报告为准。
4. 产品身份和批次角色：项目基本信息表。

同一事实同时存在于 Word / Excel 和 PDF OCR 时，优先使用 Word / Excel 或其他可直接解析的结构化来源；PDF OCR 作为交叉核对、补充来源或唯一来源时的证据。原因是 OCR 结果可能存在行列错位、字符识别和页码映射误差。若只能从 PDF OCR 取得事实，必须在来源记录中标明 `extraction_method: paddleocr_vl`、页码 / 表号、复核状态和必要的人工审查项。

已完成申报资料只有在用户明确批准且未列为 forbidden source 时才可读取，并且只能作为结构、措辞或常见模式参考；绝不能作为新项目事实来源。用户禁止使用或该文件已进入 forbidden sources 时，不要打开、读取或借鉴该文件，也不要读取对应的 Word 临时锁文件。

## 项目身份

提取：

- 客户产品名称。
- 样品类型：原液、DS、单抗、裸抗、制剂、成品等。
- 正文中的产品表达方式，例如 `[product]原液`。
- 项目编号 / 内部编号，如需要。
- 规格、浓度、装量或剂型 / 包装呈现形式。
- 对照品批号。
- 批次列表。
- 批次角色：非临床、临床、工程、工艺验证等。

规则：

- 标题、正文、题注、表格和图题中的样品类型必须一致。
- 将模板中的 `单抗/原液` 等术语替换为实际样品类型。
- 客户侧产品名优先使用 `product_name`；`product_expression` 只在需要更具体的表达方式时作为补充，`project_code` 只用于内部编号、批号、标准号和方法号，不要直接拿它充当正文中的产品展示名。
- 除非项目来源文件支持，否则不要把内部文件标题当作产品名称。

## PDF / OCR 来源处理

如果参考文件是 PDF 或图片，先判断来源类型：

- 文字层 PDF：优先抽取文本和表格，并保留页码、章节名、表号和抽取工具记录。
- 扫描 PDF / 图片：通过项目允许的 OCR 工具抽取 Markdown、图片和 manifest。允许来源中包含 PDF 时，runtime 会在生成 `source-index` 前复用已有 OCR manifest，或在 `PADDLEOCR_TOKEN` 可用时调用内置 OCR 脚本；不要直接调用 skill 内部 OCR 脚本。
- 混合 PDF：文字层用于正文事实，OCR 只补扫描页、图片型表格或无法直接抽取的页面。

规则：

- 如果 `source-index.json` 将 PDF / 图片标为 `ocr_required`，先取得项目允许的 OCR 中间产物后再写事实包；不要猜测扫描件内容。PDF 未解析完成时，即使 Word / Excel 已有候选事实，也不能把该 PDF 简单标为未使用后继续。
- 不要把 OCR token 写入仓库、事实包、证据摘要、验证报告或生成脚本；只通过环境变量传入。
- OCR 输出的 `combined.md` 是中间证据，不是最终事实包。抽取事实时必须保留 `source_file`、`page_num`、`section_or_table`、`extracted_text` 和人工复核状态。
- OCR 表格必须检查表头、行列错位、合并单元格、单位、脚注、批号和时间点。不能直接把未复核 OCR 表格批量写入 DOCX。
- 稳定性结果表中，数值、`N/A`、`---`、`<` / `≤` 等限度符号和单位容易识别错误；必须与上下文、表头和可接受标准交叉核对。
- 如 PDF 中存在图片化色谱图或图谱页，通常只记录图谱引用和页码；不要尝试从图像曲线读数，除非用户明确要求并在验证报告中标记为人工审查。
- 如果 OCR 页缺失、页面顺序异常、表格被拆分、中文 / 英文混排错位或关键数值不可信，应写入缺失证据报告或人工审查项。

推荐 OCR 事实记录结构：

```yaml
pdf_extractions:
  - source_file:
    extraction_method: text_layer | paddleocr_vl
    manifest_path:
    pages:
      - page_num:
        section_or_table:
        facts:
          - field:
            value:
            evidence_text:
            confidence: high | needs_review | unusable
            review_note:
```

## 长期稳定性

本节先抽取正文结论和表格渲染所需事实；趋势图契约在基础事实包通过校验后的 `TREND_CHARTS_REQUIRED` 状态补齐。若来源数据已显示明显趋势，可先在正文事实和 `trend_conclusion` 中记录，但不要跳过后续趋势图校验。

从方案和数据 / 报告中提取：

- 研究条件：温度、允差、避光要求，以及相关时的湿度。
- 计划月份。
- 实际时间点列。
- 各批次已完成月份。
- 产品批号和批次角色。
- 起始日期或贮存日期，如证据需要。
- 检测项目。
- 检测方法。
- 可接受标准。
- 每个时间点的结果。
- `N/A`、空白、待测值、尚未到达时间点的值和脚注。
- 关键属性的趋势序列。
- 趋势结论，以及结果是否符合标准。
- 色谱图 / 原始稳定性图谱引用。
- 趋势图配置所需的图号、图题、横轴时间、纵轴标签、批次标签和数值序列。

已完成月份规则：

- 只有关键检测项目具有实际的非空、非 `N/A`、非占位符结果时，该时间点才算已完成。
- 计划时间点如果没有结果，不算已完成。
- 不同批次可能已完成月份不同；应分别说明每个批次。
- 如果报告明确写明已完成月份，可以使用，但仍需与数据表交叉核对。

结果表标记规则：

- `---` 表示该计划时间点尚未进行到，样品在持续考察中。
- `N/A` 表示该时间点无此检项、未安排该检项或该检项不适用。
- 对来源表中已明确写出的 `N/A`、`---`、脚注或文字说明，按来源原样保留并在事实包或验证报告中记录含义。
- 对来源表中的空白结果单元格，不要直接渲染为空白。先确定该批次长期研究的已完成月份；若该列月份大于已完成月份，渲染为 `---`；若该列月份小于或等于已完成月份，渲染为 `N/A`，并视为该时间点无此检项 / 未安排检项。
- 不要只按检测项目名称或固定行号推断 `N/A`。例如效价、内毒素、微生物限度等项目可能在部分已完成时间点无结果，也可能在未来计划时间点尚未开展；必须结合时间点和已完成月份判断。

## 加速稳定性

提取：

- 加速条件：温度、湿度、避光要求。
- 各批次计划月份。
- 各批次实际时间点。
- 各批次已完成月份。
- 是否有某批次考察时间长于其他批次。
- 检测项目、方法、标准和结果。
- 趋势方向：稳定、上升、下降、波动或明显变化。
- 任何变化是否仍在标准范围内。
- 趋势图配置所需的图号、图题、横轴时间、纵轴标签、批次标签和数值序列。

规则：

- 不要在未检查趋势方向的情况下写“均稳定”。
- 如果数据只到 3 个月而方案计划到 6 个月，应写“已完成3个月，将持续考察至6个月”。
- 如果研究已完成计划时长，应删除或修改“将持续考察”。
- 加速结果表适用与长期结果表相同的 `---` / `N/A` 语义：空白来源值在已完成时间点内渲染为 `N/A`，在已完成月份之后的计划时间点渲染为 `---`。表注应同时解释 `---` 和 `N/A`，除非该表确实不存在未来未到时间点。

## 影响因素 / 强制降解研究

仅提取本章节中针对该样品类型实际开展的试验。

对每个试验提取：

- 试验名称。
- 批次列表。
- 条件。
- 时间点和时间单位。
- 是否使用共用 0 天结果。
- 具体检验项目；渲染影响因素表时必须保留为 `item`，不要只保留合并后的显示文本。
- 检测方法；如来源提供，应保留为 `method`，渲染时可与 `item` 合并到第一列“检测项目及方法”。
- 质量标准；渲染影响因素表时写入第二列“质量标准”。如果来源明确说明影响因素研究不设可接受标准，可记录为 `不设定可接受标准`；如果来源没有证据，不要臆造标准。
- 按批次和时间点列出的结果。
- 主要变化及幅度。
- 无明显变化的检测项目。
- 敏感性 / 耐受性判断。
- 控制建议。
- 原始图谱 / 色谱图引用。

填充模板前，还要为每个影响因素试验建立表格映射记录：

```yaml
stress_table_mapping:
  - test_name:
    template_table_role:
    source_table_or_paragraph:
    batches:
      - batch_no:
        source_result_rows:
        template_table_index:
        time_unit:
        time_points:
        shared_day0: true/false
        rows:
          - item:
            method:
            acceptance:
            quality_standard:
            indicator_method:  # 可选显示文本；缺失时由 item + method 合成第一列“检测项目及方法”
            timepoint_values:
            note:
    conclusion_source:
    fill_status: complete | partial | not_performed | missing_evidence
```

不要只把影响因素结论写进正文而保留表格占位符。若来源报告有表格，应逐表映射到光照、振荡、反复冻融、高温、低 pH、高 pH、氧化等 `table_render_inputs[]` 角色。若某一试验不是目标样品类型实际开展的试验，不得把它写入 `included_tests` 或对应 `table_render_inputs[]`；pipeline 会在 `{{STRESS_SECTION_BLOCKS}}` 处只生成实际开展的小节、summary 占位符和表格块占位符。

如果只能完成部分映射，应在 `fill_status` 和缺失证据报告中列明：哪个试验、哪个批次、哪个时间点或哪个检项缺少证据。

常见影响因素试验：

- 光照：温度、湿度、照度、近紫外（如有）、持续时间。
- 振荡：温度、转速、避光要求、持续时间。
- 反复冻融：冻结温度、融化条件、循环次数。
- 低 pH / 高 pH：目标 pH、酸 / 碱、是否调回 pH、持续时间。
- 氧化：氧化剂类型和浓度、温度 / 湿度、持续时间。
- 高温：温度 / 湿度 / 避光要求、持续时间。

规则：

- 低 pH 和高 pH 必须分别提取并分别描述。
- 氧化必须写明氧化剂及浓度。
- 反复冻融耐受不代表可以无限次冻融；最终建议通常仍应减少反复冻融。
- 影响因素研究通常不设可接受标准；除非来源文件明确如此，不要把它写成符合质量标准的研究。

## fact-shard v1 契约

初始事实抽取阶段按两段提交分片，而不是单个完整事实包：先提交并校验 `project_profile_facts`，再基于已验证 profile 提交 3 个研究分片。

```yaml
schema_version: ctd-32s73-fact-shard-v1
shard_type: project_profile | long_term | accelerated | stress_study
agent_provenance:
  execution_mode: subagent
  extraction_agent: reference_fact_extraction_agent
  validation_agent: reference_fact_validation_agent
  validation_status: passed
  extraction_completed_at: "2026-05-31T08:00:00Z"
  validation_completed_at: "2026-05-31T08:05:00Z"
  source_materials_reviewed:
    - fact-extraction-request.json
    - source-index.md
  validation_checks:
    - section ownership checked
    - source traceability checked
    - batch/profile alignment checked
facts:
  # 只写本 agent 拥有的字段
missing_evidence: []
manual_review_items: []
```

分片边界：

- `project_profile_facts` 只写 `project_profile`、`sources`、`docx_render_plan`、`missing_evidence`、`manual_review_items`。
- `long_term_stability_facts` 只写 `long_term`、`missing_evidence`、`manual_review_items`。
- `accelerated_stability_facts` 只写 `accelerated`、`missing_evidence`、`manual_review_items`。
- `stress_study_facts` 只写 `stress_study`、`missing_evidence`、`manual_review_items`。
- `long_term_stability_facts`、`accelerated_stability_facts` 和 `stress_study_facts` 必须读取 `fact-extraction-request.json.validated_primary_context`，用其中的产品名、样品类型、批号、批次角色、对照品批号和项目编号定位数据；发现来源中批号不在 profile 范围内时，不要纳入研究分片，除非来源明确说明它属于同一项目并在 `manual_review_items` 标出。
- `agent_provenance.extraction_agent` 必须是 `reference_fact_extraction_agent`，`agent_provenance.validation_agent` 必须是 `reference_fact_validation_agent`；`execution_mode` 必须是 `subagent`，否则 `validate_fact_shards.py` 会拒绝该分片；`source_materials_reviewed` 和 `validation_checks` 必须是非空列表；`validation_status` 必须是 `passed` 或 `passed_with_warnings`。
- 分片不得写 `trend_charts` 或 `body_sections` 的完成文本；这两个阶段在基础事实包装配和校验通过后再做。
- 分片之间不要互相补事实。发现其他领域缺失时，写入本分片 `manual_review_items` 或交给对应抽取 agent。
- runtime 会先运行 `validate_fact_shards.py` 生成聚合校验报告 `fact-shard-validation.json`，通过后再运行 assembler 生成 `fact-packet.json` 和 `fact-packet-assembly-report.json`。

## fact-packet v1 契约

起草、渲染和验证前必须先由已验证分片装配出 `fact-packet.json`。它是事实抽取、DOCX 渲染、趋势图生成和验证报告之间的数据交换契约；字段可以扩展，但不得省略 v1 必需顶层键，也不要用项目临时字段替代契约字段。状态机先校验表格、批次和渲染计划等基础事实，通过后进入趋势图补充和校验，再进入 `body_sections` 正文补充和校验。需要快速确认字段形状时，先对照 `profiles/ctd-32s73-stability/example-fact-packet.json`。

正文和骨架编辑也受这个契约约束。项目专用 `body_skeleton_docx` 可以重排章节、删减模板备选句、调整题注和表位，但不能引入事实包外的产品名称、批次、条件、时间点、结果、趋势、图表编号或结论。

必需顶层键：

```yaml
schema_version: ctd-32s73-fact-packet-v1
project_profile:
  customer_name:
  product_name:
  product_expression:
  sample_type:
  project_code:
  specification:
  reference_standard_batch:
  batches:
    - batch_no:
      role:
      sample_type:
      source_ref:
  batches_by_sample_type:
    原液: []

sources:
  allowed:
    - path:
      type: docx | xlsx | pdf | image | other
      extraction_method: direct_text | workbook | text_layer_pdf | paddleocr_vl | manual
      reader: source-index.docx.python-docx | source-index.xlsx.openpyxl | paddleocr_vl | manual
      status: used | partially_used | unusable
      evidence_refs: []
  forbidden:
    - path:
      reason:
      status: excluded
  ocr_manifests: []

long_term:
  condition:
  planned_timepoints: []
  completed_timepoints_by_batch: {}
  marker_semantics:
    blank_within_completed_timepoint: N/A
    blank_after_completed_timepoint: ---
    preserve_explicit_source_markers: true
  tables_by_batch: {}
  table_render_inputs: []
  trend_series: []
  trend_conclusion:
  chromatogram_reference:

accelerated:
  condition:
  planned_timepoints: []
  completed_timepoints_by_batch: {}
  marker_semantics:
    blank_within_completed_timepoint: N/A
    blank_after_completed_timepoint: ---
    preserve_explicit_source_markers: true
  tables_by_batch: {}
  table_render_inputs: []
  trend_series: []
  trend_conclusion:
  chromatogram_reference:

stress_study:
  included_tests: []
  omitted_tests: []
  tables_by_test: {}
  result_summary_by_test: {}
  sensitivity_by_test: {}
  control_recommendations: []
  final_conclusion:
  table_render_inputs: []

trend_charts:
  charts: []
  status: deferred_until_body_table_validation

body_sections: {}  # 初始事实抽取阶段可暂空；BODY_SECTIONS_REQUIRED 阶段补齐如下结构

body_sections_during_body_stage:
  long_term_intro:
    placeholder: "{{LONG_TERM_INTRO}}"
    text:
    source_refs: []
    writing_pattern_refs: ["真实结果稿经验沉淀.LONG_TERM_INTRO.ongoing"]
  accelerated_intro:
    placeholder: "{{ACCELERATED_INTRO}}"
    text:
    source_refs: []
    writing_pattern_refs: ["真实结果稿经验沉淀.ACCELERATED_INTRO.stable"]

docx_render_plan:
    template_id: ctd-32s73-stability-template-v2
    template_path: assets/templates/ctd-32s73-stability-template-v2.docx
  body_prose_action: body_sections | project_specific_rewrite | builtin_placeholder_only
  render_scope:
    source_batch_count:
    rendered_batches: []
    omitted_batches: []
    batch_structure_action: shrink | keep | expand
  batch_count_action: keep | shrink | expand  # 兼容别名；必须与 render_scope.batch_structure_action 一致
  table_actions: []
  caption_actions: []
  manual_refresh_items: []

missing_evidence: []
manual_review_items: []
agent_provenance:
  project_profile:
    execution_mode: subagent
    extraction_agent: reference_fact_extraction_agent
    validation_agent: reference_fact_validation_agent
    validation_status: passed
    source_materials_reviewed:
      - fact-extraction-request.json
      - source-index.md
      - 项目基本信息来源
    validation_checks:
      - section ownership checked
      - source traceability checked
      - batch/profile alignment checked
  long_term:
    execution_mode: subagent
    extraction_agent: reference_fact_extraction_agent
    validation_agent: reference_fact_validation_agent
    validation_status: passed
    source_materials_reviewed:
      - fact-extraction-request.json
      - validated project_profile
      - 长期稳定性来源
    validation_checks:
      - section ownership checked
      - source traceability checked
      - table render inputs checked
  accelerated:
    execution_mode: subagent
    extraction_agent: reference_fact_extraction_agent
    validation_agent: reference_fact_validation_agent
    validation_status: passed
    source_materials_reviewed:
      - fact-extraction-request.json
      - validated project_profile
      - 加速稳定性来源
    validation_checks:
      - section ownership checked
      - source traceability checked
      - table render inputs checked
  stress_study:
    execution_mode: subagent
    extraction_agent: reference_fact_extraction_agent
    validation_agent: reference_fact_validation_agent
    validation_status: passed
    source_materials_reviewed:
      - fact-extraction-request.json
      - validated project_profile
      - 影响因素研究来源
    validation_checks:
      - section ownership checked
      - source traceability checked
      - included and omitted tests checked
```

表格渲染输入必须使用统一结构。长期、加速和影响因素表都写入各自章节的 `table_render_inputs`，不要另造并行格式：

```yaml
table_render_inputs:
  - role: long_term | accelerated | light_stress | agitation_stress | freeze_thaw_stress | high_temperature_stress | low_ph_stress | high_ph_stress | oxidation_stress
    batch_no:
    caption_hint: 可选；通常由 renderer 根据表格块顺序自动编号
    caption:
    source_refs: []
    time_header:
      label: 可选；仅用于来源追溯/复核，renderer 不用它改写 prototype 固定表头
      points: []
    rows:
      - group:
        item:  # 长期 / 加速：具体检项；影响因素：具体检验项目，必须保留
        method:  # 来源提供时保留；影响因素表可与 item 合成第一列
        indicator_method:  # 影响因素表可选显示文本；若缺失，由 item + method 合成“检测项目及方法”
        acceptance:  # 长期 / 加速：质量标准；影响因素表第二列“质量标准”，无标准且来源明确时写“不设定可接受标准”
        quality_standard:
        values:
          "0":
            value:
            marker: value | N/A | --- | source_marker | missing
            source_ref:
          "3":
            value:
            marker:
            source_ref:
        note:
```

`values` 中可以为了兼容渲染器保留简单字符串值，但事实包必须能追溯每个 `N/A` / `---` 的判定。推荐写成对象，后续生成 `table-render-input.json` 时再降级为渲染器需要的字符串矩阵。

字段规则：

- `schema_version` 必须等于 `ctd-32s73-fact-packet-v1`。
- 批次事实必须保留为列表：`project_profile.batches` 和必要时的 `batches_by_sample_type`。不要用 `ds_batch`、`main_batch`、`first_batch` 等单值字段驱动正文、题注或表格渲染。
- `sources.allowed` 必须列出用户允许的每个来源，并说明 `used`、`partially_used` 或 `unusable`；`sources.forbidden` 必须列出已完成申报资料和锁文件等禁止来源，并标为 `excluded`。
- `planned_timepoints` 表示方案计划时间点，`completed_timepoints_by_batch` 表示每批实际已有结果的时间点，二者不要混用。
- `marker_semantics` 固定表达空白来源值的转换规则：已完成时间点内无结果为 `N/A`，已完成时间点之后的计划时间点为 `---`。
- `docx_render_plan.render_scope.source_batch_count` 必须等于 `project_profile.batches` 的来源确认批次数；所有来源批次必须出现在 `rendered_batches` 或带原因的 `omitted_batches` 中。`batch_structure_action` 按本次渲染批次数确定：1 批为 `shrink`，2 批为 `keep`，3 批及以上为 `expand`。
- 阶段门禁是硬约束：基础事实包校验会拒绝非空 `trend_charts.charts[]` 和已写好的 `body_sections.*.text`；趋势图校验阶段仍会拒绝已写好的 `body_sections.*.text`。
- `body_sections` 存放模板整段占位符的最终文本；键名使用小写 snake_case，默认占位符为 `{{KEY_IN_UPPERCASE}}`，也可显式写 `placeholder`。初始事实抽取阶段可先保留为空对象；进入 `BODY_SECTIONS_REQUIRED` 后，每个 `text` 必须可由事实包其他字段和 `source_refs` 支撑，并用 `writing_pattern_refs` 标记采用的写作模式或“真实结果稿经验沉淀”槽位。正文中的表号和图号必须跟本项目最终渲染编号一致，不能把来源文件里的局部表号 / 图号直接搬进正文；长期 / 加速段落只能引用对应研究组的渲染编号，影响因素段落只能引用对应 role 的渲染编号。
- `docx_render_plan.body_prose_action` 推荐写 `body_sections`：正文、题注、表注、图表引用和总结段落通过语义槽位整段替换。骨架阶段不提前替换正文语义槽位，pipeline 会在表格渲染前生成 `body-sections-applied.docx`。没有 `body_sections` 时默认视为 `project_specific_rewrite` 并进入 `BODY_SKELETON_REQUIRED`；只有明确用于烟测或用户接受模板占位替换时，才可写 `builtin_placeholder_only`。
- 每个 `table_render_inputs[]` 记录必须有 `role`、`batch_no`、`time_header.points`、`rows`；不要再写 `target_table_index`。renderer 根据 `role` 匹配 v2 模板中的表格块占位符，并在 `table-render-manifest.json.generated_table_slots` 中记录实际 `table_index`、`caption_no` 和题注。
- 影响因素表的每个 `rows[]` 记录必须保留具体检验项目 `item`、质量标准 `acceptance` / `quality_standard` 和各时间点结果 `values`；`method` 来源存在时也要保留。`indicator_method` 只是 Word 第一列的显示文本，可由 `item` 和 `method` 派生，不应替代结构化检验项目。
- `docx_render_plan.table_actions` 记录批次数量扩展 / 收缩、删除未开展试验、生成表块、更新正文引用等结构动作。实际表位由 renderer 自动生成；正文中的表号必须按模板表格块顺序和同角色输入顺序预先写入 `body_sections`，并由验证器按段落检查是否引用了该段对应研究组的最终题注编号，而不只是“引用的编号是否存在”。
- 缺失或冲突事实写入 `missing_evidence` 或 `manual_review_items`，不要用猜测值填充契约字段。

初始基础事实必须通过 profile-first 的 4 个 `fact-shards/*.json` 提交：`PRIMARY_CONTEXT_SHARD_REQUIRED` 先提交 `project-profile-facts.json`，`DOMAIN_SHARDS_REQUIRED` 再提交长期、加速、影响因素 / 强制降解 3 个研究分片，由 runtime 装配 `fact-packet.json`。基础事实包通过校验后，下游 CTD 模板填充状态机会停在 `TREND_CHARTS_REQUIRED`，生成 `trend-charts-request.json`，此后再通过下一轮 step event 的 `provided_artifacts.fact_packet` 提交补充趋势图后的同一个事实包。趋势图校验通过后会停在 `BODY_SECTIONS_REQUIRED`，生成 `body-sections-request.json`，再要求提交补充正文后的同一个事实包。不要绕过 step 直接进入 DOCX 渲染。

当事实包基础校验、趋势图校验和正文校验都通过后，内部状态机会先生成或接收 `body-and-skeleton-filled.docx`，再把它交给通用生成管线。正文不是 `XXX` 级别替换：长期、加速、影响因素、图表引用和总结段落应在 `BODY_SECTIONS_REQUIRED` 阶段按 `references/writing-patterns.md` 写入 `body_sections`。内置 `scripts/generate_body_skeleton.py` 只应用旧式文本占位符并保留语义/结构占位符；`scripts/run_generation_pipeline.py` 随后生成 `trend-chart-manifest.json`，在 `{{STRESS_SECTION_BLOCKS}}` 处生成实际开展的影响因素小节，再生成 `body-sections-applied.docx`。之后由内部表格渲染器在表格块占位符处生成题注、表格、表注和表目录条目，最后写入长期 / 加速趋势图块和图目录，并保存 `stress-section-render.json`、`body-section-application.json`、`table-render-manifest.json`、`figure-render-manifest.json`、结构快照、验证报告和 `generation-manifest.json`。

## 批次数据模型

批次事实必须保留为列表，不要压缩成单个字段。

模板原始只有两个批次表块，不代表项目只支持两个批次。事实层永远保留所有来源确认批次；渲染层通过 `docx_render_plan.render_scope` 声明本次渲染批次和结构动作。未渲染的来源批次必须进入 `omitted_batches` 并写明原因，不能静默丢弃。

推荐结构：

```yaml
project_profile:
  batches_by_sample_type:
    原液:
      - batch_no: IP315AADCC20250601
        role: non_gmp
      - batch_no: IP315AADCC20250902
        role: gmp
```

不要使用 `ds_batch`、`main_batch`、`first_batch` 等单值字段驱动正文、题注或表格渲染。这类字段最多只能作为兼容旧脚本的别名，并且不能覆盖批次列表。

如果来源表中同时给出 non-GMP 和 GMP 批次，两个批次都属于目标样品类型的候选批次。渲染时应遍历目标样品类型的批次列表，并根据稳定性工作簿中是否存在对应 batch block 决定填实际结果、`N/A`、`---` 或缺失证据说明。

## 趋势图数据抽取

趋势图应在 `TREND_CHARTS_REQUIRED` 状态从同一个事实包或派生的 `trend-chart-input.json` 生成。不要从已填充 DOCX 反向抽取趋势图数据，也不要在初始事实抽取阶段跳过正文/表格校验去直接补图。

事实层只维护图表契约：图号、文件名、图题、坐标轴标题、批次序列和原始 `x` / `y` 值。`trend_charts` 必须是对象，`charts` 必须是列表。点过滤、图形绘制、PNG 输出和 manifest 由内部趋势图生成器负责；当 `trend_charts.charts` 非空时，step 触发的生成管线会在应用 `body_sections` 之前写出 `trend-chart-input.json`、`trend-charts/` 和 `trend-chart-manifest.json`，并在表格渲染后把长期 / 加速趋势图写入 `{{LONG_TERM_FIGURE_BLOCK}}`、`{{ACCELERATED_FIGURE_BLOCK}}` 和 `{{FIGURE_DIRECTORY}}`。

影响因素研究不生成趋势图。不要把光照、振荡、冻融、高温、pH、氧化等影响因素图谱 / 色谱图写入 `trend_charts.charts[]`；这些材料只能作为来源引用或正文说明。

推荐输入结构：

```json
{
  "style": "seaborn-v0_8-whitegrid",
  "dpi": 180,
  "charts": [
    {
      "figure_no": 1,
      "study_key": "long_term",
      "filename": "figure-01.png",
      "type": "line",
      "title": "长期条件 SEC 单体纯度稳定性趋势图",
      "ylabel": "Monomer %",
      "xlabel": "Month",
      "series": [
        {"label": "批次1", "x": [0, 3, 6], "y": ["98.5", "98.7", "---"]},
        {"label": "批次2", "x": [0, 3, 6], "y": ["98.4", "98.6", "---"]}
      ]
    },
    {
      "figure_no": 2,
      "study_key": "accelerated",
      "filename": "figure-02.png",
      "type": "two_panel",
      "title": "效价稳定性趋势图",
      "xlabel": "Month",
      "panels": [
        {"title": "hACVR2A", "series": [{"label": "批次1", "x": [0, 3], "y": [93, "N/A"]}]},
        {"title": "hACVR2B", "series": [{"label": "批次1", "x": [0, 3], "y": [105, "N/A"]}]}
      ]
    }
  ]
}
```

规则：

- 每条 series 的 `x` 和 `y` 必须来自同一批次、同一检项、同一研究条件。
- 不要用未来计划时间点补零；无法作图的点按来源值保留给脚本过滤。
- 趋势图只用于长期稳定性和加速稳定性；影响因素研究即使正文说“关注变化趋势”，也不生成趋势图。
- 趋势图纳入由 agent 在 `TREND_CHARTS_REQUIRED` 阶段判断，不由 pipeline 自动补图。判断标准是数据形态：同一研究条件下，某一检项按批次和时间点整理，并且至少一个批次有 `0 月 + 后续时间点` 的数值型结果，就应在 `trend_charts.charts[]` 中声明一张趋势图。
- 生成粒度为“研究条件 + 检项”一张图，批次作为图中的多条曲线；不要按批次拆成多张图，也不要只画代表性单图。
- 判断“数值型结果”时以来源表实际结果为准，可包含百分比、含量、浓度、pH、活性、比值、渗透压、DAR、杂质等数值；纯定性结果或仅限度符号结果不应作为趋势点。
- 即使研究未完成全部计划月份，只要已有 `0 月 + 后续时间点` 的数值型结果，也应生成趋势图；未来未到时间点保留为 `---` / `N/A`，由绘图脚本过滤。
- 如果长期或加速数据存在符合条件的数值检项，事实包不得只保留空的 `trend_charts.charts[]`；若某检项不生成趋势图，必须在 `manual_review_items` 中写明来源支持的原因或数据缺口。
- 效价等双指标图可使用 `type: two_panel`，每个 panel 单独提供 series。
- 图号、文件名、正文图题和 DOCX 中替换的图片顺序必须一致；生成后保存 `trend-chart-manifest.json` 供验证。
- `body_sections` 可使用 manifest 驱动令牌：`{{LONG_TERM_TREND_FIGURE_RANGE}}`、`{{ACCELERATED_TREND_FIGURE_RANGE}}`、`{{TREND_FIGURE_RANGE}}`、对应的 `..._FIGURE_REFS` 和 `..._FIGURE_TITLES`。pipeline 会在 `body-section-application.json.trend_token_counts` 记录实际替换情况。
- 每个 chart 推荐提供 `study_key` / `group` / `section`，取值仅限 `long_term` 或 `accelerated`。缺省时 pipeline 会从图题中的“长期”“加速”等关键词推断分组；若事实包声明或图题显示为影响因素研究，校验会报错。

## 稳定性工作簿批次块扫描

稳定性工作簿的一个 sheet 可能包含多个批次数据块。不要只解析每个 sheet 的第一个批次。

对于 `原液长期`、`原液加速`、`DS`、`长期`、`加速` 等目标 sheet：

1. 扫描整张 sheet，定位所有包含 `产品批号` / `Product Batch No.` 的行。
2. 对每个批号行，读取该行中的批号作为一个 batch block 的 `batch_no`。
3. 每个 batch block 从该批号行开始，到下一个批号行之前结束；没有下一个批号行时，到当前表格区域或 sheet 末尾结束。
4. 在每个 block 内分别提取研究条件、开始日期、时间点、检测项目、可接受标准和结果值。
5. 将每个 block 写入 `tables_by_batch[batch_no]`，并分别计算 `completed_months_by_batch[batch_no]`。
6. 不要用单个 `ds_batch` 字段替代批次列表；渲染正文、题注和表格时必须遍历批次列表。

若基本信息表列出某批次，稳定性工作簿也在后续 block 中提供该批次数据，则必须抽取该 block。IP315 类似场景中，`原液长期` 和 `原液加速` sheet 的第二个 block 可能包含 `IP315AADCC20250902` 及其实际结果，不能因第一个 block 已解析完成就停止扫描。

如果基本信息表列出批次但工作簿中没有对应 block，应保留批号并将结果状态标记为 `missing_results`；如果工作簿有 block，则不得标记为缺失。

## 冲突处理

- 如果产品名称或批次在不同文件中不一致，应标记并与项目基本信息表和报告交叉核对。
- 如果方案和报告中的条件不同，已报告结果应使用报告条件。
- 如果来源批号存在拼写错误，只有在另一个项目来源支持时才可规范化，并记录该规范化。
- 如果报告结论与表格数值冲突，创建缺失证据或人工确认备注。
- 不要混用原料药和制剂工作表，除非目标章节明确要求如此。
