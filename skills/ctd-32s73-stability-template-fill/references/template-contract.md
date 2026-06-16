# 模板契约

本文件将内置 DOCX 模板定义为有版本的格式契约。它不定义项目事实。

## 目录

- `模板资产`：当前模板、快照和表格 prototype 文件。
- `基线结构`：空白模板段落、表格和占位符数量。
- `语义段落占位符`：正文整段替换槽位。
- `表格块占位符`：稳定性表和表目录生成规则。
- `趋势图块占位符`：趋势图和图目录生成规则。
- `Prototype 表格映射`：各 role 对应的 prototype 表格。
- `动态批次和删除规则`：批次数量、未开展试验和结构动作。
- `时间表头规则`、`编号规则`、`快照命令`：渲染细节和诊断命令。

## 模板资产

- 当前模板 ID：`ctd-32s73-stability-template-v2`
- 当前模板资产：`assets/templates/ctd-32s73-stability-template-v2.docx`
- 当前基线快照：`assets/templates/ctd-32s73-stability-template-v2.inspect.txt`
- 表格 prototype 资产：`assets/table-prototypes/ctd-32s73-table-prototypes.docx`
- prototype 快照：`assets/table-prototypes/ctd-32s73-table-prototypes.inspect.txt`

当用户要求填充 3.2.S.7.3 稳定性模板时，始终使用 v2 模板作为正文起点。表格 prototype DOCX 只作为表格样式、合并单元格和表头结构来源，不作为正文模板。

不要把已完成申报资料作为模板资产。已完成申报资料可能包含其他项目的事实，只能作为禁止来源。

## 基线结构

内置 v2 模板具有以下基线：

- 段落数：43
- 表格数：1（缩略语表；稳定性结果表由结构占位符生成）
- 内联形状数：0（趋势图由 pipeline 按事实包生成并插入）
- 空白模板中的占位符数量：
  - `XXX`：0
  - `IPXXX`：0
  - `单抗/原液`：7
  - `{{TABLE_DIRECTORY}}`：1
  - `{{FIGURE_DIRECTORY}}`：1
  - `{{LONG_TERM_TABLE_BLOCK}}`：1
  - `{{LONG_TERM_FIGURE_BLOCK}}`：1
  - `{{ACCELERATED_TABLE_BLOCK}}`：1
  - `{{ACCELERATED_FIGURE_BLOCK}}`：1
  - `{{STRESS_SECTION_BLOCKS}}`：1
  - 影响因素单项 summary / table block 占位符：空白模板中为 0，由 pipeline 根据实际开展试验动态生成
  - `已完成的申报资料`：0
  - `时间（小时）`：0

这些数量描述的是空白模板，不是已填充输出。已填充输出通常应将 `{{...}}`、`XXX`、`单抗/原液` 等占位符降为零，除非某个占位符被有意保留并在验证报告中明确说明理由。

## 语义段落占位符

申报级叙述段落使用整段语义占位符，事实包 `body_sections` 提供最终文本。占位符独占一个段落，以便替换时保留段落样式。内置 v2 直接包含：

- `{{LONG_TERM_INTRO}}`
- `{{LONG_TERM_TREND_INTRO}}`
- `{{LONG_TERM_TREND_SUMMARY}}`
- `{{ACCELERATED_INTRO}}`
- `{{ACCELERATED_TREND_INTRO}}`
- `{{ACCELERATED_TREND_SUMMARY}}`
- `{{STRESS_STUDY_INTRO}}`
- `{{FINAL_STABILITY_CONCLUSION}}`

以下影响因素单项语义占位符不再固定存在于空白模板中，而是由 `scripts/run_generation_pipeline.py` 在 `{{STRESS_SECTION_BLOCKS}}` 处按实际开展试验生成：

- `{{STRESS_LIGHT_REFERENCE}}`
- `{{STRESS_LIGHT_SUMMARY}}`
- `{{STRESS_AGITATION_SUMMARY}}`
- `{{STRESS_FREEZE_THAW_SUMMARY}}`
- `{{STRESS_HIGH_TEMPERATURE_SUMMARY}}`
- `{{STRESS_PH_SUMMARY}}`
- `{{STRESS_OXIDATION_SUMMARY}}`

已填充输出不得残留 `{{` 或 `}}`。

## 表格块占位符

稳定性结果表不再作为空白模板里的固定双批次表格存在。v2 使用结构占位符表示每个表格块：

| 占位符 | 渲染角色 |
| --- | --- |
| `{{TABLE_DIRECTORY}}` | 由 renderer 写入实际生成的表目录条目 |
| `{{LONG_TERM_TABLE_BLOCK}}` | `long_term` |
| `{{ACCELERATED_TABLE_BLOCK}}` | `accelerated` |
| `{{STRESS_SECTION_BLOCKS}}` | 由 pipeline 生成实际开展的影响因素小节和对应表格块占位符 |

`scripts/render_stability_tables.py` 按 DOCX 中占位符出现顺序处理表块。每个 `table_render_inputs[]` 记录根据 `role` 自动匹配对应表格块占位符；同一 `role` 有多个批次时，按输入顺序在同一占位符位置生成多张题注 + 表格 + 表注。

渲染器会把实际生成的 `role`、`batch_no`、`table_index`、`caption_no`、`caption` 和 prototype 来源写入 `table-render-manifest.json.generated_table_slots`。不再要求、生成或校验 `body-skeleton-manifest.json.table_slots`。

`{{STRESS_SECTION_BLOCKS}}` 会按实际 role 生成下列结构占位符；未开展的 role 不会物化到 DOCX：

| 动态表格块 | 渲染角色 |
| --- | --- |
| `{{STRESS_LIGHT_TABLE_BLOCK}}` | `light_stress` |
| `{{STRESS_AGITATION_TABLE_BLOCK}}` | `agitation_stress` |
| `{{STRESS_FREEZE_THAW_TABLE_BLOCK}}` | `freeze_thaw_stress` |
| `{{STRESS_HIGH_TEMPERATURE_TABLE_BLOCK}}` | `high_temperature_stress` |
| `{{STRESS_LOW_PH_TABLE_BLOCK}}` | `low_ph_stress` |
| `{{STRESS_HIGH_PH_TABLE_BLOCK}}` | `high_ph_stress` |
| `{{STRESS_OXIDATION_TABLE_BLOCK}}` | `oxidation_stress` |

## 趋势图块占位符

趋势图不再作为空白模板中的固定图片、图题或固定图目录存在。v2 使用结构占位符：

| 占位符 | 渲染规则 |
| --- | --- |
| `{{FIGURE_DIRECTORY}}` | pipeline 根据已生成的长期 / 加速趋势图题写入图目录条目 |
| `{{LONG_TERM_FIGURE_BLOCK}}` | 插入长期趋势图题和 PNG |
| `{{ACCELERATED_FIGURE_BLOCK}}` | 插入加速趋势图题和 PNG |

影响因素研究不生成趋势图块。事实包 `trend_charts.charts[]` 只能声明长期或加速趋势图；影响因素图谱 / 色谱图可作为来源引用写入正文，但不进入趋势图生成契约。

## Prototype 表格映射

以下表格索引是表格 prototype DOCX 中从零开始的 `python-docx` 表格索引，仅用于克隆表头、样式、合并单元格和初始行列结构。它们不是 v2 输出 DOCX 的目标表位。

| prototype 表格索引 | 角色 | 表头契约 |
| --- | --- | --- |
| `0` | 缩略语表 | 3 个普通列 |
| `1` / `2` | 长期稳定性 | 2 个左侧项目单元格 + 方法 + 标准 + 跨时间列的 `时间（月)` |
| `3` / `4` | 加速稳定性 | 2 个左侧项目单元格 + 方法 + 对照品 + 跨时间列的 `时间（月)` |
| `5` / `6` | 光照影响因素 | 左侧 `检测指标` 横向合并两列并纵向合并两行；跨时间列 |
| `7` / `8` | 振荡影响因素 | 同影响因素表头契约 |
| `9` / `10` | 反复冻融影响因素 | 同影响因素表头契约 |
| `11` / `12` | 高温影响因素 | 同影响因素表头契约 |
| `13` / `14` | 低 pH 影响因素 | 同影响因素表头契约 |
| `15` / `16` | 高 pH 影响因素 | 同影响因素表头契约 |
| `17` / `18` | 氧化影响因素 | 同影响因素表头契约 |

每个角色的第 1 个渲染表默认克隆对应第一个 prototype；第 2 个及之后渲染表克隆对应第二个 prototype。渲染后只能改写“时间”下的二级时间点表头和数据区内容；固定表头文本来自表格 prototype。时间点变化时仍必须维护顶层时间单元格的 `gridSpan`、`tblGrid`、数据行数和必要的纵向合并。

## 动态批次和删除规则

批次数量完全由 `fact-packet.json` 中的 `table_render_inputs[]` 决定：

- 单批次：每个角色只生成该批次的一张表，不生成空白第二批表。
- 双批次：同一角色生成两张表。
- 三批及以上：同一角色在对应表格块占位符位置连续生成多张表，不追加到文档末尾。
- 未开展或未渲染的影响因素试验：该角色没有 `table_render_inputs[]` 时，占位符被删除，不生成题注、表格或表目录条目。
- 未开展的影响因素正文小节不得生成。`scripts/run_generation_pipeline.py` 会根据 `stress_study.included_tests` 和 `stress_study.table_render_inputs[].role` 在 `{{STRESS_SECTION_BLOCKS}}` 处只生成实际开展的小节、summary 占位符和表格块占位符。例如没有 `oxidation_stress` 且 `included_tests` 未列出氧化时，最终 DOCX 中不应出现“氧化试验结果”小节。
- pH 小节按组处理：`low_ph_stress` 或 `high_ph_stress` 任一存在时保留 pH 小节；两者均不存在时删除 pH 小节及低 / 高 pH 表格块占位符。

正文中的表格引用必须与 renderer 的编号规则一致：按模板中表格块占位符顺序和每个块内 `table_render_inputs[]` 顺序连续编号，并且要落在该段对应的研究组或试验角色范围内。正文、题注、表注和表目录不得引用未生成的表号，也不要把来源文件里的局部表号直接带入正文。表题和图题中的对外产品展示名应优先使用 `project_profile.product_name`，再回退到 `product_expression`；`project_code` 只保留给批号、方法号和内部标识，不作为客户侧标题的首选展示名。

## 时间表头规则

固定表头文本来自表格 prototype，renderer 不得重写“检测项目”“检测方法”“可接受标准”“检测指标”或顶层时间单位等固定文案。`table_render_inputs[].time_header.points` 只用于写入“时间”下的二级时间点表头。

长期和加速表的 prototype 使用月份表头。影响因素研究表的 prototype 按角色提供固定单位：

- 光照：通常为 `时间（天）`
- 振荡：通常为 `时间（天）`
- 反复冻融：通常为 `时间（循环）`
- 高温：通常为 `时间（天）`
- 低 pH / 高 pH：通常为 `时间（天）`
- 氧化：按所选 prototype 固定表头

如果来源单位与所选 prototype 顶层时间单位不一致，不要让 renderer 临时改表头文本；应更换或修订表格 prototype，或在 `manual_review_items` 中记录阻塞。更改时间点后，renderer 必须更新第二表头行、顶层时间单元格 `gridSpan`、`w:tblGrid` 和题注中的相关表达。正文说明和图题由 `body_sections`、趋势图配置和事实包字段驱动，不得从表格默认值推断。

## 编号规则

空白模板不再预置固定趋势图编号。图号来自事实包 `trend_charts.charts[].figure_no`，pipeline 只将长期和加速趋势图写入 DOCX，并由验证器核对 `trend-chart-manifest.json`、DOCX 图题和正文引用的一致性。

## 快照命令

有意更改内置模板后，重新生成基线快照：

```powershell
python .\scripts\inspect_docx_structure.py `
  .\assets\templates\ctd-32s73-stability-template-v2.docx `
  --caption-paragraphs --all-tables --max-rows 2 --placeholders "XXX,IPXXX,单抗/原液,已完成的申报资料,时间（小时）,{{TABLE_DIRECTORY}},{{FIGURE_DIRECTORY}},{{LONG_TERM_TABLE_BLOCK}},{{LONG_TERM_FIGURE_BLOCK}},{{ACCELERATED_TABLE_BLOCK}},{{ACCELERATED_FIGURE_BLOCK}},{{STRESS_SECTION_BLOCKS}},{{STRESS_LIGHT_TABLE_BLOCK}},{{STRESS_OXIDATION_TABLE_BLOCK}}" `
  > .\assets\templates\ctd-32s73-stability-template-v2.inspect.txt
```
