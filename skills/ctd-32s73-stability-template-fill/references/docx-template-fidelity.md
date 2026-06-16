# DOCX 模板保真指南

普通填充任务不应手写 DOCX 结构操作；把事实写入 `fact-packet.json`，再通过 `scripts/skill_step.py` 交给状态机、pipeline 和 renderer。本文仅在维护模板、维护 renderer、编写项目专用 body/skeleton hook，或诊断 DOCX 结构失败时读取。

## 总策略

使用“复制并填充”，不要“重建并套样式”。

正确方向：

- 从当前有效模板 `assets/templates/ctd-32s73-stability-template-v2.docx` 开始。
- 保留段落、运行、单元格和表格的 OOXML 属性。
- 克隆相同角色的邻近段落、表格、行或单元格。
- 只按事实包和模板契约调整时间点、数据行、题注和表注。
- 保存结构快照并让验证器检查代表性 `gridSpan`、`vMerge`、题注引用和模板差异。

避免：

- 从空白文档重建章节。
- 用 `doc.add_table()` 新建结果表。
- 删除所有表格行后用 `add_row()` 重建。
- 用 `cell.text = ""` 广泛重写合并单元格。
- 把合并后的 Word 表格当作普通二维数组。
- 在项目一次性脚本中复制通用表格渲染逻辑。

## 模板资产

- 当前正文模板：`assets/templates/ctd-32s73-stability-template-v2.docx`
- 当前模板契约：`references/template-contract.md`
- 当前基线快照：`assets/templates/ctd-32s73-stability-template-v2.inspect.txt`
- 表格 prototype：`assets/table-prototypes/ctd-32s73-table-prototypes.docx`

不要把已完成申报资料作为模板资产；它们默认是 forbidden source。

## 段落与单元格

替换段落文本时保留 `w:pPr`，优先克隆已有文本运行的 `w:rPr`。插入题注、表注、图题和正文段落时，克隆相同角色的邻近段落，而不是只设置样式名。

替换单元格文本时保留 `w:tcPr`、首段 `w:pPr` 和代表性运行属性。合并单元格需检查底层 OOXML：

- 物理单元格：`table._tbl.tr_lst[row].tc_lst`
- 横向合并：`w:gridSpan`
- 纵向合并：`w:vMerge`
- 表格网格：`w:tblGrid`

`table.cell(r, c)` 返回逻辑单元格，合并区域中的重复文本不能直接当作物理重复。

## 表格 renderer 边界

内部 `scripts/render_stability_tables.py` 负责：

- 从表格 prototype DOCX 克隆长期、加速和影响因素表头/样式。
- 在 v2 结构占位符处生成题注、表格、表注和表目录条目。
- 按 `table_render_inputs[]` 重建时间点二级表头、数据行、`gridSpan`、`vMerge` 和 `tblGrid`；固定表头文本必须来自表格 prototype，不由 renderer 重写。
- 在 `table-render-manifest.json.generated_table_slots` 记录实际 `role`、`batch_no`、`table_index`、`caption_no` 和 prototype 来源。

调用者不要传 `target_table_index`，也不要手工克隆/删除结果表来表达批次数。单批次收缩、双批次保持、三批及以上扩展均由 `table_render_inputs[]` 和 `docx_render_plan.render_scope` 表达。

## 影响因素表结构

影响因素研究表的顶层左侧表头为 `检测指标`，该单元格的横向/纵向合并以表格 prototype 为准。数据区左侧两列分别写入：

- 检测项目及方法
- 质量标准

顶层时间单位是固定表头文本，必须来自所选表格 prototype。若来源单位与 prototype 不一致，不要让 renderer 临时重写固定表头；应更换或修订 prototype，或记录人工复核阻塞。

## 诊断

结构异常时先看状态机产物：

- `workflow-events.jsonl`
- `workflow-state.json`
- `fact-packet-validation.json`
- `body-section-application.json`
- `stress-section-render.json`
- `table-render-manifest.json`
- `figure-render-manifest.json`
- `structure-snapshot.txt`
- `validation.json`
- `validation-report.md`

只读结构审计命令：

```powershell
python .\scripts\inspect_docx_structure.py `
  .\path\to\filled.docx `
  --caption-paragraphs --all-tables --max-rows 2
```

有意更改内置模板后，按 `references/template-contract.md` 的快照命令更新基线快照，并同步模板 ID 或契约说明。
