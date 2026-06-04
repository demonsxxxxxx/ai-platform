# 审核结果 Schema

## 目标

统一所有审核分支的内部输出，避免聚合、去重、批注和后续处理时字段不一致。用户默认主交付是带批注 Word（`reviewed_docx` / `*_reviewed.docx`）；本 schema 仅内部使用，不是最终用户交付物。

## 顶层结构

```json
{
  "document": "document.docx",
  "review_time": "2026-03-30T12:00:00",
  "reviewer": "Claude AI QA Reviewer",
  "review_scope": "full",
  "branch": "merge",
  "branch_execution_manifest": [
    {
      "branch": "format",
      "agent_role": "format-agent",
      "status": "completed",
      "issue_count": 3,
      "duration_ms": 1200,
      "retry_count": 0,
      "error": ""
    }
  ],
  "human_review_queue": [
    {
      "issue_id": "issue-0008",
      "branch": "content_consistency",
      "reason": "anchor_unstable",
      "location": "4.4.4.2 英文部分"
    }
  ],
  "commenting": {
    "policy": "preserve_existing",
    "existing_total": 0,
    "added_total": 0,
    "failed_total": 0,
    "expected_total": 0,
    "actual_total": 0
  },
  "summary": {
    "total_issues": 0,
    "user_visible_issue_count": 0,
    "diagnostic_issue_count": 0,
    "by_severity": {
      "关键": 0,
      "主要": 0,
      "次要": 0
    },
    "by_type": {
      "格式": 0,
      "项目号": 0,
      "大模型全文审核": 0
    },
    "quality_score": {
      "score": 100,
      "grade": "A",
      "penalties": [],
      "coverage": {
        "required_branches": ["format", "project_number", "content_consistency"],
        "executed_branches": ["format", "project_number", "content_consistency"],
        "semantic_agent_review": false
      }
    }
  },
  "artifacts": {
    "document_map": "",
    "comment_plan": "",
    "reviewed_docx": "",
    "validation_report": "",
    "docx_audit_report": ""
  },
  "internal_diagnostics": [],
  "issues": []
}
```

## 顶层字段约定

- `review_scope`：本次审核范围，建议使用 `full`、`format_only`、`language_only`、`bilingual_only`、`content_only`、`project_number_only`、`section_sampling`。
- `branch`：当前结果来源分支，脚本内置分支使用 `format`、`project_number`、`content_consistency`、`merge`、`human_review`；调用 agent 在脚本外追加语义复核时可使用 `llm_full_review` 兼容标识。
- `branch_execution_manifest`：分支执行对账清单，`format`、`project_number`、`content_consistency` 必须全部出现；缺失任一分支不得输出最终结果。`llm_full_review` 不再是脚本必选分支。
- `branch_execution_manifest[].skip_categories`：调用 agent findings 被过滤时的内部分桶统计，常见值包括 `filtered_external`、`filtered_external_veto`、`filtered_low_confidence`、`filtered_low_value`、`filtered_quality_gate`、`anchor_failure_should_retry`、`schema_invalid`。该字段只用于质量复盘，不代表用户可见问题。
- `branch_execution_manifest[].deduped_issue_count`：进入批注计划前被跨 agent 合并的重复语义问题数量，用于确认去重是否减少人工复核负担。
- `human_review_queue`：人工复核清单字段（不是人审处理系统）；仅承载被明确允许保留的真实文档核对项。定位不稳定、证据不足、低置信、建议空泛或需要外部资料的问题默认过滤出 Word，只保留内部诊断或跳过统计。
- `commenting`：批注对账信息，必须保留旧批注的统计结果。
- `artifacts`：内部产物路径或标识，默认不对外交付。
- `artifacts.document_map`：来自 `minimax-docx review-map` 的统一文档坐标产物。
- `artifacts.comment_plan`：写批注前的专用输入，只包含通过质量门禁的 Word 批注项。
- `artifacts.reviewed_docx`：默认用户主交付文件路径，对应 runtime/file key `reviewed_docx`，展示标签 `批注 Word`。
- `artifacts.docx_audit_report`：`minimax-docx audit` 的结构校验报告。
- `summary.total_issues` / `summary.user_visible_issue_count`：只统计用户可见、可交付的问题，即会进入 `comment_plan.json` 的锚定问题或显式 `global_summary`。
- `summary.diagnostic_issue_count`：统计被保留用于内部审计、但不应出现在普通报告或 Word 中的问题。
- `summary.quality_score`：确定性质量评分，按问题严重度、人工复核项、分支失败和批注定位扣分；用于审核质量参考，不替代人工最终判定。
- `internal_diagnostics`：保留低置信、无稳定锚点、低价值或其他内部诊断项。它们可用于复盘，但不得进入普通详细报告的问题清单、console 总问题数或 `summary.total_issues`。
- `issues`：完整审计列表，可包含用户可见项和内部诊断项；用户界面或普通报告不得直接把该列表当作最终问题清单。

## 单条问题字段

```json
{
  "id": "issue-0001",
  "rule_id": "FMT-HDR-001",
  "type": "格式",
  "branch": "format",
  "agent_role": "format-agent",
  "subtype": "header_version_mismatch",
  "document_zone": "header",
  "location_kind": "paragraph",
  "location": "第3段 / 表1 / 页眉 / 页脚",
  "anchor_locator": "paragraph=3",
  "anchor_span": {
    "start": 8,
    "end": 14,
    "unit": "char"
  },
  "anchor_text": "原文片段",
  "original": "原文片段",
  "issue": "问题描述",
  "severity": "主要",
  "severity_reason": "页眉版本号与正文版本号不一致",
  "occurrence_count": 1,
  "threshold": 0,
  "aggregation_scope": "single",
  "suggestion": "修改建议",
  "comment_text": "发现：页眉版本号与正文版本号不一致。\n原文：页眉版本号 V1.0；正文版本号 V1.1。\n建议：同步页眉版本号与正文版本号。",
  "evidence": "用于判定的原文上下文",
  "evidence_source": "header",
  "match_method": "exact",
  "preexisting_comment_count": 0,
  "comments_added": 1,
  "confidence": "high",
  "status": "confirmed",
  "comment_visibility": "word_comment",
  "requires_external_evidence": false,
  "external_evidence_type": "none",
  "coverage_domain": "format",
  "review_basis": "company_standard",
  "comment_intent": "suggest_change",
  "source": "qa-file-reviewer",
  "source_agent": "format-agent",
  "notes": "可选补充说明"
}
```

## 字段约束

- `rule_id`：必须能映射回公司标准中的具体检查项，建议使用稳定编号。
- `type`：必须是明确分类，不要写成模糊描述。
- `document_zone`：必须能表达文档所在区域，建议使用 `title_page`、`header`、`footer`、`toc`、`body`、`table`、`figure`、`appendix`、`record`、`metadata`。
- `location`：必须能定位到文档中的具体位置。
- `location_kind`：建议使用 `paragraph`、`table`、`header`、`footer`、`property`、`figure` 或 `unknown`。
- `anchor_locator`：用于批注落点的稳定定位信息，必须尽量具体。
- `anchor_span`：用于批注落点的精确文本范围，建议记录 `start`、`end` 和 `unit=char`；优先于 `anchor_text`。
- `anchor_text`：用于落批注的原文锚点，不要使用改写文本。锚点匹配应归一化等价引号和常见单位字形，例如 `µm` / `μm`。
- `original`：保留原文，不要替换成修订后文本。
- `issue`：只描述问题本身，不混入建议。
- `severity`：只允许 `关键`、`主要`、`次要`。
- `suggestion`：必须可执行，避免空泛表达。
- `comment_text`：用于 Word 批注的实际文案，应使用短中文标签表达发现、原文和建议；长格式证据和重复示例必须压缩；不要写入 `P342` 这类内部段落号、内部分类、JSON 文件名、SDK 错误、堆栈或 `review_basis` 枚举。
- `evidence`：必须保留足够上下文，支持复核。
- `match_method`：建议使用 `span`、`exact`、`contains` 或 `inference`；`fallback` 仅可用于候选探索，不允许作为自动批注落点。
- `preexisting_comment_count`：该锚点已有批注数量，保留旧批注，不要覆盖。
- `comments_added`：本次针对该问题新增的批注数量。
- 当 `comments_added > 0` 时，`status` 必须为 `confirmed` 或 `needs_user_check`，且必须有稳定 `anchor_span`、`anchor_locator` 和 `anchor_text`；`match_method=inference/fallback` 不允许原位自动批注。
- `status`：只使用 `confirmed` 或 `needs_user_check`。
- `comment_visibility`：默认 `word_comment`。只有通过质量门禁的问题才允许保持 `word_comment`。
- `requires_external_evidence`：布尔值。需要原始记录、方案、样品信息或 LIMS 数据才能判断时必须为 `true`。
- `external_evidence_type`：允许 `none`、`record`、`protocol`、`sample_info`、`lims`、`other`。
- 品牌、供应商、产品名、设备型号或官方拼写类断言，如果没有配置术语库或文档内部主写法支撑，也视为外部依据依赖项，不得仅凭模型记忆进入 Word 批注。
- `coverage_domain`：允许 `format`、`structure`、`zh_language`、`en_language`、`bilingual`、`data_consistency`、`terminology`、`external_check`。
- `review_basis`：允许 `company_standard`、`single_doc_internal`、`agent_semantic`、`external_required`。
- `comment_intent`：允许 `suggest_change`、`request_check`、`global_summary`。当 `requires_external_evidence=true` 时必须使用 `request_check`，不得写成确定错误。
- `branch`：问题所属职责分支。
- `agent_role`：历史兼容字段，表示职责分支标识（字段名沿用，不表示独立 agent 系统）。
- `source`：技能层来源，建议使用 `qa-file-reviewer`。
- `source_agent`：历史兼容字段，表示来源职责分支标识。
- `needs_user_check` 问题只有在明确允许用户可见时才同步写入 `human_review_queue` 和批注写回列表；常规低质量、不稳定或外部依据依赖项默认过滤。

## 去重规则

- 确定性规则问题只有在 `rule_id`、`type`、`document_zone`、`location_kind`、`location`、`anchor_locator`、`original` 和 `evidence` 同时一致时才去重。
- 调用 agent 语义问题按“同一位置 + 同一错误词/同一修复目标/同一语义意图”去重，即使不同 agent 给出的 `anchor_text` 不同，也只保留一条最清晰的用户可见批注。
- 调用 agent 对跨多行/多表的同类重复问题应聚合为代表性批注，避免同一问题逐行增加人工复核负担。只有同一源侧错误和同一修复目标相同的问题才可跨位置聚合。
- 确定性检查与调用 agent 对同一位置、同一语义缺陷的重复报告也必须去重；温度允差、重复脚注语法、重复标点或 dominant-term 混淆等可按语义合并，不要求锚点完全相同。
- 若同一位置、同一语义意图被 risk classifier 或其他 agent 标为 `requires_external_evidence=true` / `comment_intent=request_check`，该分类结果会否决其他 agent 的同意图确定性批注，避免把需要外部依据的问题包装成确认错误。例外：高置信、精确锚定、仅凭当前文档即可证明的中英/方法/条件矛盾，允许作为内部不一致问题进入 Word，建议应写成“统一/修订/核对后统一”，不得伪装成外部事实断言。
- `anchor_text` 不得是短泛词或结构词（例如 `than`、`Table`、`data`、`条件下`）。裸数字锚点不得只命中更长数值或带 `%` 的相邻值；无法重定位到真实源单元格时必须过滤。
- 确定性兜底规则与调用 agent 报告同一位置、同一或高度重叠原文锚点时，保留确定性规则项，避免弱模型重复表述增加人工复核成本。
- 同一确定性语法问题在重复脚注或重复说明行中多次出现时，应聚合为一条代表性问题，并在 `evidence` 中保留重复位置摘要；调用 agent 对其他重复位置的同一脚注语法报告也应与该代表性问题去重。
- 不同问题可位于同一段落或同一表格单元格；如果错误词、证据和修复目标不同，不要因为位置相同而合并。

## 调用 agent 语义复核输出约束

- 调用 agent 在脚本外执行语义复核时，结构化输出必须是 JSON object，顶层包含 `issues` 数组。
- `category`、`severity`、`confidence` 必须严格使用约定枚举；建议同时提供 `coverage_domain`、`review_basis`、`requires_external_evidence`、`external_evidence_type`、`comment_intent`。
- 首次 JSON 语法错误可触发一次只修复 JSON 语法的重试；重试仍失败、文件缺失、JSON 解析失败或 schema 无法校验时，只进入内部诊断，不进入用户可见 Word 批注。
- 无法稳定锚定原文、低置信、证据不足或建议空泛的问题默认过滤出 Word，并保留内部跳过原因。
- 表格单元格内的高置信问题可以使用 `P571 (T7R5C3)` 这类段落 + 表格坐标作为稳定位置；如果 `document_map.json` 中存在对应 table/row/cell 和精确原文片段，应进入批注计划而不是被当成锚点失败。
- 相邻双语脚注可作为稳定位置重试范围：若 agent 锚定中文脚注段落，但 `original` 精确出现在紧邻英文脚注段落，应将批注写入英文原文，而不是暴露或丢弃为人工复核项。
- 同一表格单元格内同一温度条件出现不同 `±` 容差，属于可由单文件判断的内部数据不一致；不得仅因可能影响试验条件就自动改为外部证据项。
- 对少数高置信、可单文档判断的模式允许确定性检查，例如被动语态 `were filter` 应为 `were filtered`、脚注 `data reference to the releasing data` 的 release-data 表达错误、重复标点、同一表格单元格内的双语数据不一致，以及由文档主流写法证明的短化学式 I/l/1 混淆。
- 化学式大小写或易混字符这类短 token 客观错误，如果原文可精确锚定、建议明确且文档内部 dominant 写法可证明，不按“大小写风格偏好”过滤。
- `human_review_queue` 只承载真实文档问题的人工核对项；不得把 SDK/sub-agent 运行错误、`FileNotFoundError`、内部路径或 JSON 文件名转换成 Word 批注。
- 批注写回阶段只处理通过质量门禁的 `comment_visibility=word_comment` 问题；只有显式 `global_summary` 全文级问题可追加到文末“全局审核意见”。
- `needs_user_check` 可以作为 Word 批注写入，但必须是明确允许用户可见的真实文档核对项，且只能写成核对请求，不得写成确定错误。
- 单纯语序偏好、表达风格偏好，或 `2025.10` / `October 2025` 这类公司允许并存的年月格式，
  不应进入最终问题清单。

## 输出优先级

1. `关键`
2. `主要`
3. `次要`
