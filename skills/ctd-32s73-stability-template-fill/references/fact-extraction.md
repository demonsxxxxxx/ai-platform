# 事实包接入指南

本 skill 不再从参考文件抽取基础事实，也不再维护 fact shard 抽取/装配契约。基础事实的唯一上游是 `reference-fact-extraction` skill 的 `ctd-32s73-stability` profile。

## 上游职责

先运行 `reference-fact-extraction`：

- `profile_id`: `ctd-32s73-stability`
- 默认输出模式：`domain_native`
- handoff artifact：CTD-native `fact-packet.json`
- schema：`ctd-32s73-fact-packet-v1`

上游负责来源登记、source-index、project profile 和研究事实分片抽取、独立 subagent 校验、分片校验、CTD-native fact-packet 装配和基础事实包校验。相关契约维护在：

- `../reference-fact-extraction/profiles/ctd-32s73-stability/extraction.md`
- `../reference-fact-extraction/profiles/ctd-32s73-stability/fact-packet-contract.md`
- `../reference-fact-extraction/profiles/ctd-32s73-stability/validation.md`

## 本 skill 职责

本 skill 只消费上游产出的 `provided_artifacts.fact_packet`，并继续：

1. 校验 fact-packet 是否满足 CTD 3.2.S.7.3 渲染契约。
2. 要求补充并校验 `trend_charts`。
3. 要求补充并校验 `body_sections`。
4. 渲染 DOCX、生成图表、验证最终章节。

`trend_charts` 和 `body_sections` 在上游基础事实包中应保持 empty/deferred；本 skill 会在 `TREND_CHARTS_REQUIRED` 与 `BODY_SECTIONS_REQUIRED` 阶段补齐。

## Step 提交方式

基础事实阶段只接受：

```json
"provided_artifacts": {
  "fact_packet": "<reference-fact-extraction-output>/fact-packet.json"
}
```

不要向本 skill 提交 `project_profile_facts`、`long_term_stability_facts`、`accelerated_stability_facts` 或 `stress_study_facts`；这些属于上游 skill 的内部审计 artifact。若提交旧 shard artifact，`skill_step.py` 会拒绝事件并提示迁移到 `reference-fact-extraction`。

## 事实包基本边界

`fact-packet.json` 至少应包含：

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

项目事实必须能追溯到 `fact-packet.json` 的字段、`source_ref`、`sources.allowed`、`missing_evidence` 或 `manual_review_items`。模板、写作模式、脚本和 `body_skeleton_docx` 不能引入事实包外的产品名称、批号、条件、时间点、结果、趋势、图表编号或结论。
