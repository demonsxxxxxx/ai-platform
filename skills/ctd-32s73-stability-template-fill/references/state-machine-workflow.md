# CTD 3.2.S.7.3 Step 状态机工作流

`scripts/skill_step.py` 是唯一公开入口。Agent 只能提交用户消息、输出目录、允许来源、禁止来源和当前状态允许的被动产物；状态跳转、恢复、校验和最终交付判断由 step runtime 和内部 FSM 决定。

## 运行时事实源

- 状态、终态、可恢复态和允许 artifact：`scripts/workflow_states.py`
- step event/response 校验与拒绝逻辑：`scripts/skill_step.py`
- 内部 FSM 编排：`scripts/run_state_machine_workflow.py`
- 下游副作用脚本门禁：`scripts/runtime_guard.py`

不要直接调用内部副作用脚本。`scripts/inspect_docx_structure.py` 仅用于只读结构审计。

## 职责边界

基础事实抽取已迁移到 `reference-fact-extraction`。本 skill 不再接收 fact shard，也不再支持 `fact_extractor` hook。

- 上游：`reference-fact-extraction` + `ctd-32s73-stability` profile 生成 CTD-native `fact-packet.json`。
- 本 skill：消费 `provided_artifacts.fact_packet`，校验渲染契约，补趋势图，补正文，渲染 DOCX，最终验证。

## 单 run 不变式

一个用户任务只能绑定一个 `output_dir` / run ID。首次 event 指定的 `output_dir` 是该任务的唯一工作目录；所有后续 fact packet、正文骨架、缺失证据回补、验证告警处理和 pipeline 重跑都必须复用同一个目录。

如果 step response 停在可恢复暂停态，必须使用 `allowed_next_events[0].output_dir` 继续。不要因验证失败、缺少事实包、缺少正文骨架或进入 `COMPLETED_INTERMEDIATE` 而创建派生 run。

## Step Event

必要规则：

- `schema_version` 必须是 `ctd-32s73-step-event-v1`。
- `output_dir` 必填。
- 相对 `output_dir`、来源、hook 和 artifact 路径必须配合绝对 `project_root`。
- `provided_artifacts` 只接受 `fact_packet` 和后续必要的 `body_skeleton_docx`。
- 基础事实输入必须是上游生成的 CTD-native `fact_packet`；不要提交 `project_profile_facts`、`long_term_stability_facts`、`accelerated_stability_facts` 或 `stress_study_facts`。
- 不允许提交 `action`、`submit`、`finalize`、`render`、`validate`、`state`、`next_state` 等动作或跳转字段。

支持的 hooks：

- `recovery_hook`
- `body_skeleton_hook`

支持的 options：`artifact_profile`、`allow_intermediate`、`disallow_hour_time`、`max_recovery_attempts`、`min_tables` / `max_tables`、`expected_batch` / `expected_batches`、`expected_warning` / `expected_warnings`、`until`。

## 合法下一步

| 当前状态 | 允许提交 |
| --- | --- |
| `FACT_EXTRACTION_REQUIRED` / legacy `FACT_PROJECT_PROFILE_REQUIRED` / legacy `FACT_STUDY_SHARDS_REQUIRED` | `provided_artifacts.fact_packet`，来自 `reference-fact-extraction` 的 CTD-native `fact-packet.json` |
| `FACT_PACKET_REVISION_REQUIRED` | 修订后的 `provided_artifacts.fact_packet` |
| `MISSING_EVIDENCE_RECOVERY_REQUIRED` | 记录 recovery attempts 后的 `provided_artifacts.fact_packet` |
| `TREND_CHARTS_REQUIRED` | 补充 `trend_charts` 后的 `provided_artifacts.fact_packet` |
| `TREND_CHARTS_REVISION_REQUIRED` | 按 `trend-charts-validation.json` 修订后的 `provided_artifacts.fact_packet` |
| `BODY_SECTIONS_REQUIRED` | 补充 `body_sections` 后的 `provided_artifacts.fact_packet` |
| `BODY_SECTIONS_REVISION_REQUIRED` | 按 `body-sections-validation.json` 修订后的 `provided_artifacts.fact_packet` |
| `BODY_SKELETON_REQUIRED` | `provided_artifacts.body_skeleton_docx` |
| `COMPLETED_INTERMEDIATE` | 仅当 response 给出恢复事件时，提交同一 run 的 `body_skeleton_docx` |
| `COMPLETED_FINAL` | 不再提交；进入交付复核 |

如果 event 中的 artifact 与当前状态不匹配，`skill_step.py` 会拒绝事件、写入 `step_event_rejected`，并且不会推进内部状态机。

## Agent 责任

1. 始终先运行 `skill_step.py`，再读取 `step-response.json`。
2. 只按 `allowed_next_events` 继续，并保持同一 `output_dir`。
3. 基础事实缺失时，先运行 `reference-fact-extraction` 的 `ctd-32s73-stability` profile，并把其 `fact-packet.json` 作为 `provided_artifacts.fact_packet` 提交。
4. 在 `MISSING_EVIDENCE_RECOVERY_REQUIRED` 只搜索允许来源，并记录 `missing_evidence_recovery.attempts`。
5. 在 `TREND_CHARTS_REQUIRED` 读取 `trend-charts-request.json`，基于已通过基础校验的 `table_render_inputs` 补充 `trend_charts`，再提交同一个事实包；此阶段仍不得提交已写好的 `body_sections.*.text`。
6. 在 `TREND_CHARTS_REVISION_REQUIRED` 按 `trend-charts-validation.json` 修订趋势图覆盖率、分组、图号、文件名和 series/panels。
7. 在 `BODY_SECTIONS_REQUIRED` 读取 `body-sections-request.json` 和 `writing-patterns.md`；事实场景复杂时读取 `writing-fact-cases.md`。补齐 `body_sections.*.text/source_refs/writing_pattern_refs` 后提交同一个事实包。
8. 在 `BODY_SECTIONS_REVISION_REQUIRED` 按 `body-sections-validation.json` 修订正文，不要绕过正文门禁进入 DOCX 渲染。
9. 在 `BODY_SKELETON_REQUIRED` 读取 `writing-patterns.md`；优先通过事实包 `body_sections` 表达正文，只有项目布局确需 DOCX 编辑时提交 `body_skeleton_docx`。
10. 不把任何中间产物称为最终申报章节。

## 输出与定位

每轮核心状态产物：`workflow-state.json`、`workflow-events.jsonl`、`workflow-summary.json`、`step-config.json`、`step-response.json`。

生成/验证产物按状态出现：

- `source-index.json` / `source-index.md`
- `fact-packet-request.json`
- `fact-packet.json`
- `fact-packet-validation.json`
- `trend-charts-request.json`
- `trend-charts-validation.json`
- `body-sections-request.json`
- `body-sections-validation.json`
- `body-skeleton-request.json`
- `body-skeleton-report.json`
- `stress-section-render.json`
- `body-section-application.json`
- `table-render-manifest.json`
- `trend-chart-manifest.json`
- `figure-render-manifest.json`
- `generation-manifest.json`
- `validation.json`
- `validation-report.md`
- `artifact-index.md`

默认 `artifact_profile=delivery` 会把交付文件、审计文件和调试文件分层整理；最终路径以 `artifact-index.md`、`workflow-state.json.artifacts` 和 `step-response.json.artifacts` 为准。
