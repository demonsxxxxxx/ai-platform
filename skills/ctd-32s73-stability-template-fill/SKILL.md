---
name: ctd-32s73-stability-template-fill
description: >-
  Use this skill whenever the user asks to generate, fill, revise, validate, resume, or diagnose a CTD 3.2.S.7.3 stability-data Word section, including Chinese requests such as 填写/生成/重写/修订 `3.2.S.7.3 稳定性数据`, 生成申报资料稳定性章节, 把稳定性 Excel/报告/方案写入 Word 模板, 扩展批次表格, 保持 Word 模板格式不变, or 排查生成 DOCX 与模板不一致. Always drive the work through `scripts/skill_step.py` and its step response; do not bypass the step/FSM runtime for generation, validation, recovery, resume, or final-delivery decisions.
---

# CTD 3.2.S.7.3 稳定性数据模板填充

本 skill 将项目稳定性证据转换为可追溯的 CTD 3.2.S.7.3 Word 章节，并保持内置 DOCX 模板契约。它的核心执行规则已经固化在状态机和脚本中；`SKILL.md` 只保留入口、不变量和按需阅读导航。

## 必读入口

唯一公开入口是 `scripts/skill_step.py`。生成、恢复、验证、重新提交、最终交付判断都必须通过 step event/response 进行；不要直接调用内部副作用脚本。只读诊断例外是 `scripts/inspect_docx_structure.py`。

运行：

```powershell
python .\scripts\skill_step.py `
  --event .\path\to\step-event.json `
  --response-out .\outputs\run-001\step-response.json
```

运行后读取 `step-response.json`，只按同一 run 的 `allowed_next_events` 和 `required_artifacts` 准备下一轮输入。

## 不可绕过

- 一个用户任务只使用一个 `output_dir` / run ID；恢复时沿用 `allowed_next_events[0].output_dir`。
- 相对路径必须配合绝对 `project_root`，并只按 `project_root` 解析。
- `filled.docx` 的项目事实只能来自 `fact-packet.json`；模板、脚本和写作模式只能提供结构、样式、固定标签和表达框架。
- 基础事实抽取已迁移到 `reference-fact-extraction`：先用其 `ctd-32s73-stability` profile 生成 CTD-native `fact-packet.json`，再作为本 skill 的 `provided_artifacts.fact_packet` 提交。本 skill 不再接收或装配 `project_profile_facts`、`long_term_stability_facts`、`accelerated_stability_facts`、`stress_study_facts`。
- 子智能体不得直接提交 step event、不得创建独立 run、不得直接覆盖最终 `fact-packet.json`；只有 step runtime 可以按 `allowed_next_events` 消费或推进事实包。
- DOCX / XLSX 参考文件进入 `source-index` 时，runtime 使用内置直接索引器记录段落、表格、sheet 和行列预览，并在来源记录写入 `reader` 元数据；事实抽取仍必须回到允许来源或已审查中间产物确认。
- 默认禁止读取、引用或借鉴文件名以 `已完成的申报资料` 开头的文件和 Word 锁定文件 `~$...`；把它们登记为 forbidden/excluded。
- OCR token 只从 `PADDLEOCR_TOKEN` 读取，不写入仓库、日志、事实包或报告。允许来源中只要包含 PDF，step runtime 就会先复用已有 OCR 产物或调用内置 OCR 脚本；如果仍无法取得 OCR 产物，事实抽取阶段必须暂停，不得猜测 PDF 内容。
- `project_code` 只用于内部编号、批号和标准号；正文、题注、图题和表题的对外展示名称应优先取 `project_profile.product_name`，再回退到 `product_expression`，不要把内部项目号直接当成客户侧产品名。
- 正文里的表号和图号必须跟本项目最终渲染编号一致；不要把来源文件或内部草稿中的局部表号直接写进 `body_sections`。`validate_fact_packet.py` 会按段落校验表/图引用是否落在对应研究段的最终编号范围内。
- 只有 `step-response.json.delivery_status == "final_candidate"` 且状态为 `COMPLETED_FINAL` 时，才能把 `filled.docx` 称为最终候选。

## 按需读取

- `references/state-machine-workflow.md`：step event/response、恢复规则、合法下一步和 runtime guard。
- `references/fact-shard-agents.md`：legacy 说明；基础 fact shard 契约已迁移到 `reference-fact-extraction`。
- `references/fact-extraction.md`：来源优先级、source-index 用法、fact-packet 字段、批次/时间点/标记语义、趋势图和影响因素映射。
- `references/writing-patterns.md`：事实明确后，起草或修订 `body_sections` 正文结论。
- `references/writing-fact-cases.md`：正文事实场景复杂、模式选择不确定或需要“事实快照 → 段落写法”示例时读取。
- `references/template-contract.md`：当前 v2 模板、占位符、表格块、趋势图块、动态批次和 prototype 映射。
- `references/docx-template-fidelity.md`：维护模板、renderer 或诊断 DOCX 结构保真时读取。
- `references/validation.md`：最终验收、交付完整性、warning 解释和人工审查项。
- `references/example-*.json` 与 schema：仅作字段形状示例和外部集成检查；示例值不是项目事实。

运行时状态语义以 `scripts/workflow_states.py` 为准；事实包校验以 `scripts/validate_fact_packet.py` 为准；DOCX 验证以 `scripts/validate_generated_docx.py` 为准。

## 操作闭环

1. 构造 `ctd-32s73-step-event-v1`：包含用户消息、绝对 `project_root`、同一 `output_dir`、允许来源、禁止来源和当前已准备好的被动产物。
2. 运行 `scripts/skill_step.py`。
3. 读取 `step-response.json` 和 `workflow-summary.json`。
4. 如果状态可恢复，只准备 `required_artifacts` 指定的产物，并用 `allowed_next_events` 继续。
5. 重复直到 `delivery_status=final_candidate`，或状态机给出不可恢复阻塞。

## 状态动作速查

| 状态 | Agent 动作 |
| --- | --- |
| `FACT_EXTRACTION_REQUIRED` / legacy `FACT_PROJECT_PROFILE_REQUIRED` / legacy `FACT_STUDY_SHARDS_REQUIRED` | 运行 `reference-fact-extraction` 的 `ctd-32s73-stability` profile，提交其 CTD-native `fact-packet.json`。 |
| `FACT_PACKET_REVISION_REQUIRED` | 读取 `fact-packet-validation.json`，修订上游 fact-packet 或补充 recovery evidence 后重新提交同一 `fact_packet`。 |
| `MISSING_EVIDENCE_RECOVERY_REQUIRED` | 读取 `fact-packet-revision-routing.json` 判断缺失事实所属区域；只让对应子代理补查允许来源，把每轮回补写入 `missing_evidence_recovery.attempts`，再由 coordinator 提交更新后的事实包；不要猜测。 |
| `TREND_CHARTS_REQUIRED` | 读取 `trend-charts-request.json`；在已通过校验的正文/表格事实包基础上补充 `trend_charts`，再提交同一 `fact-packet.json`。 |
| `TREND_CHARTS_REVISION_REQUIRED` | 读取 `trend-charts-validation.json`，修订趋势图覆盖率、分组、图号、文件名和序列数据后重新提交事实包。 |
| `BODY_SECTIONS_REQUIRED` | 读取 `body-sections-request.json`、`writing-patterns.md`，必要时读取 `writing-fact-cases.md`；基于已验证事实和趋势图契约补齐 `body_sections`，再提交同一 `fact-packet.json`。 |
| `BODY_SECTIONS_REVISION_REQUIRED` | 读取 `body-sections-validation.json`，修订正文完整性、来源追溯、写作模式引用和模板残留后重新提交事实包。 |
| `BODY_SKELETON_REQUIRED` | 读取 `body-skeleton-request.json`、`writing-patterns.md`，必要时读取 `writing-fact-cases.md`；优先补齐事实包 `body_sections`，必要时提交项目专用 `body_skeleton_docx`。 |
| `COMPLETED_INTERMEDIATE` | 不能交付；若 response 给出恢复事件，在同一 `output_dir` 继续。 |
| `COMPLETED_FINAL` | 复核 response、summary、validation report 和 artifact index；`filled.docx` 可作为最终候选。 |

## 事实包边界

生成初始事实时先运行 `reference-fact-extraction` 的 `ctd-32s73-stability` profile，取得 CTD-native `fact-packet.json` 后提交给本 skill。修订渲染契约时读取 `references/example-fact-packet.json`。事实包至少保留这些顶层键：

```text
schema_version
project_profile
sources
long_term
accelerated
stress_study
trend_charts
body_sections
docx_render_plan
missing_evidence
manual_review_items
agent_provenance
```

关键建模边界：

- 批次必须是列表，不因模板默认表位截断、补造或单值化。
- 区分计划时间点、已完成时间点、`---` 和 `N/A`。
- `source-index` 只是来源地图，不是事实包。
- 长期、加速和影响因素表统一使用各自章节的 `table_render_inputs[]`；不要写 `target_table_index`。
- 正文、题注、表注、趋势结论和图表引用需要项目事实时，先写入事实包并保留来源追溯。

## 交付复核

回复用户前，读取：

- `step-response.json`
- `workflow-summary.json`
- `artifact-index.md`
- `validation-report.md`

默认 `artifact_profile=delivery` 时，用户交付文件在 `delivery/`，审计追溯文件在 `_audit/`，调试和过程文件在 `_debug/`。如果流程未最终完成，说明当前状态、阻塞原因、下一步所需 artifact，以及当前 DOCX 为什么只是中间产物。
