# File Reviewer - Prompt Templates

本文档只保留调用 agent 进行语义复核时的通用提示原则。当前不维护固定错词、固定句式、产品名组合或试验条件枚举模板。

## 调用 agent 语义复核

### 调度来源

- 由调用 agent 使用当前会话已可用的模型能力执行语义复核。
- `scripts/run_qa_review.py` 不读取模型环境变量，也不自动调用内置 LLM。
- 调用 agent 必须把结果写成 JSON 文件，并通过 `scripts/run_qa_review.py --agent-review-json <json>` 交回确定性 runner。
- 不得在 prompt、日志、报告或源码中输出密钥。

### Context v2 Reading Order

When `agent_context_manifest.json` exists, read the context package in this order:

1. Read every `agent_review_context.part-*.txt` or `agent_review_context.txt` listed in `context_parts`.
2. For each finding, choose the `### UNIT` whose `TEXT` contains the editable wrong source phrase.
3. Use `review_units.jsonl`, `review_blocks.jsonl`, and `bilingual_pairs.jsonl` only to verify neighbors, same-cell relations, block membership, locator safety, and evidence units.
4. Do not output findings that depend on external records, unsupported style preference, missing anchor, or broad table-schema inference.
5. Never expose `P...`, `XML:...`, or `T...R...C...` in user-facing `issue`, `evidence`, or `suggestion`.
6. For every ordinary issue, copy `anchor_quote` exactly from the chosen unit's `TEXT`.

The TXT context is the primary full-flow QA review material. Sidecar JSONL files are only for locator/evidence verification and machine checks, not for line-by-line agent review.

Review domains are not limited to bilingual comparison. Cover:

- Chinese wording only when it creates an objective typo, ambiguity, wrong term, missing symbol, wrong data, or compliance meaning change.
- English grammar/spelling only when the source phrase is objectively wrong or misleading.
- Bilingual consistency when Chinese/English values, objects, units, conditions, formulas, or procedure meaning disagree.
- Cross-section semantic consistency when the same document internally contradicts itself.
- Table data and formula consistency when the same row/cell/pair proves the defect.
- References, attachment names, project numbers, sample IDs, batch IDs, and section titles when the document itself proves the inconsistency.

### Full-Flow Review Scope

Do not limit review to bilingual comparison. Use the context package to review:

- Chinese source text: objective typos, missing words, wrong terms, ambiguous compliance meaning.
- English source text: objective spelling, grammar, missing symbol, wrong term, or misleading sentence errors.
- Bilingual consistency: mismatched values, units, conditions, objects, formulas, procedure meaning, references, titles.
- Table data: row/column contradictions, same-cell paired text, numeric values, units, tolerances, ranges.
- Formulas: missing absolute-value bars, wrong operators, inconsistent symbols, mismatched Chinese/English formulas.
- References and attachments: document number, version, record name, appendix title, table title.
- Project/sample/batch identifiers: document-internal contradictions only.
- Cross-section semantics: same-document contradictions that are directly proven by cited units.

For every non-global issue:

- `unit_id` must be the unit containing the editable wrong source phrase.
- `anchor_quote` must be copied exactly from that unit's `TEXT`.
- `evidence_unit_ids` may cite other units, but evidence units are not the Word comment insertion target.
- Do not expose internal locator hints such as `P...`, `XML:...`, or `T...R...C...`.

### 目标

- 以 `agent_context_manifest.json` 的 `context_parts` 列出的 `agent_review_context.part-*.txt` 或 `agent_review_context.txt` 为主材料分块阅读全文。
- 判断中文、英文、双语一致性、表格数据、公式、引用、项目号、样品/批号和跨段语义一致性问题。
- 只输出能被原文片段支撑的问题。
- 不做无证据润色。
- 不输出单纯语序偏好、表达风格偏好或公司未规定的日期格式偏好。
- 参考公司标准和可配置术语库时，必须说明证据来源；未配置的样本文档 literal 不得当作规则。
- `review_units.jsonl` 和 `bilingual_pairs.jsonl` 仅用于核对 `unit_id`、`pair_id` 或相邻上下文，不是主要审查材料。

### 输出约束

模型必须返回 JSON object：

```json
{
  "schema_version": "qa-file-reviewer.agent-review.v1",
  "issues": [
    {
      "category": "zh_language|en_language|bilingual_consistency|semantic_consistency",
      "severity": "关键|主要|次要",
      "unit_id": "p-00098",
      "anchor_quote": "该 unit 内的原文精确片段",
      "paragraph_index": "兼容字段，可选",
      "original": "原文精确片段",
      "evidence_quote": "可在原文中找到的证据片段，优先与 original 相同或相邻",
      "issue": "中文问题说明；即使原文为英文，也用中文描述问题",
      "suggestion": "中文修改建议；可保留英文原文或替换文本作为代码样式片段",
      "evidence": "判断依据",
      "evidence_unit_ids": ["p-00098"],
      "confidence": "high|medium|low"
    }
  ]
}
```

推荐文件名：

- `zh_agent_review.json`
- `en_agent_review.json`
- `bilingual_semantic_agent_review.json`

调用 agent 应严格校验：

- `category` 只能是 `zh_language`、`en_language`、`bilingual_consistency`、`semantic_consistency`。
- `severity` 只能是 `关键`、`主要`、`次要`。
- `confidence` 只能是 `high`、`medium`、`low`。
- context v2 中，`unit_id` 必须直接来自 `context_parts` 文本里的 `### UNIT <unit_id>` 标题；不要自行发明或省略。非全文问题缺少 `unit_id` 会被过滤出用户可见 Word 批注。legacy v1 的 `[u:<unit_id> | ...]` 行头仅用于旧上下文兼容，不是 v2 主定位来源。
- `anchor_quote` 必须是该 `unit_id` 对应 `TEXT` 块内可逐字定位的源文本，用于生成最终 Word 批注的落点。
- `review_units.jsonl`、`bilingual_pairs.jsonl` 是机器索引/辅助材料；先读 TXT，再用 JSONL 核对，不要把 JSONL 当成主要审查文本。
- `original` 或 `evidence_quote/evidence` 中必须包含能在文档中逐字或去空白后匹配的源文本片段；不要只在 `issue` 或 `suggestion` 里引用原文。
- `issue` 和 `suggestion` 必须面向中文用户书写。英文来源问题可以在 `original`、`anchor_quote` 或建议中的替换片段保留英文，但不要把 “Missing space...” / “Replace...” 这类英文说明直接放入用户可见字段。
- 表格问题可在内部使用 `P571 (T7R5C3)` 这类段落和单元格线索，但它们只是兼容或可读提示；不得只写 `P...`、`XML:...` 或 `T...R...C...` 作为主定位契约，也不得把这些内部 locator 写入用户可见的 `issue`、`evidence` 或 `suggestion`。
- 双语脚注或注释行被拆成相邻段落时，应优先给出实际问题所在语言的精确原文；如果段号引用了配对中文行，`original` 仍必须写英文问题原文。
- 不要因为文档一部分是双语、一部分是单语，就输出“中文/英文缺少对应内容”。只有同一局部双语结构、明确模板要求或已验证公司标准能证明该处必须双语时，才报告缺译/缺少对应项。
- 科学单位中的等价字形可以按原文输出，例如 `µm` / `μm`，runner 会归一化匹配；不要因此改成模糊描述。
- `suggestion` 必须是可直接执行的中文动作，例如“改为...”“补充...”“删除...”“统一为...”。“请核对”“建议优化”“Consider rephrasing” 这类空泛建议会被过滤。
- 如果判断结果是“无需修改 / No change needed”，不要输出为 issue。
- 不要输出纯大小写风格、标题大小写、表达更自然、awkward phrasing 等润色类问题，除非能引用运行器明确提供的公司标准、模板要求或术语表。
- 不要仅凭模型记忆或自己写出的“approved terminology / 受控术语表 / SOP requires”作为受控来源。除非运行器明确提供了已验证术语/模板来源，否则官方缩写全称、品牌、物料、注册名称等外部权威断言应作为外部核对项或过滤。
- 非法枚举、缺失 `original/issue/suggestion`、JSON 解析失败或 schema 无法校验属于内部诊断，不得写入 Word 批注；锚点不稳、低置信、证据不足或建议空泛的真实文档问题也默认过滤出 Word，仅保留内部跳过分类。
- `medium` 置信度问题不得包含“可能/疑似/似乎/may be/might be/possibly”等推测词；这类输出应省略，不要交给用户复核。
- 不论标记为 `high` 还是 `medium`，只要问题说明或依据使用“可能被误解/可能会/may be interpreted/could be interpreted”等推测性表达，默认不要输出为可见问题。
- 公式、百分号、单位和符号类语义判断属于高风险项；除非来自明确公司标准或配置规则，或能给出稳定原文锚点和确定性内部矛盾，否则不要输出为 Word 批注。
- 同一表格单元格或同一中英文对照段内，若同一温度条件出现不同 `±` 容差（例如中文与英文数值不一致），这是单文件内部矛盾；有精确原文时应标为 `requires_external_evidence=false`、`review_basis=single_doc_internal`、`comment_intent=suggest_change`，不要标成外部核对项。
- 被动语态 `were filter` / `was filter` 这类可由原文直接判断的英文语法错误，属于可见问题；如果输出，必须给出精确原文和直接修改建议。
- `data reference to the releasing data` 这类脚注英文错误，若原文可见且不依赖外部记录，应作为修改建议，不要写成外部核对项。
- 不要用 `evidence` 叙述中的其他片段替代 `original` 的实际问题锚点。例如问题针对英文公式时，不能把中文公式片段作为插入锚点。
- `2025.10`、`2025-10`、`2025年10月`、`October 2025` 等年月格式可按中英文场景并存；
  除非公司规则明确要求统一，否则不要报告为日期格式问题。
- 语序只有造成明确歧义、语法错误、错译或合规含义变化时才报告；不要做单纯顺句润色。

### 输出边界

- `original` 必须能在原文中找到。
- `anchor_quote` 必须能在命名的 `unit_id` 内找到；找不到时必须过滤该 finding，不允许全文搜索兜底、跨段搜索或文末追加后再写 Word 批注。
- `issue` 和 `suggestion` 中的引号不会被当作原文证据；弱模型不能靠建议文本制造锚点。
- 仅表达风格不同但没有公司标准或明确证据支撑时，不报告为问题。
- 中低置信、锚点不稳、证据不足或建议空泛的问题默认不进入 Word；只有显式设置 `comment_intent: "global_summary"` 的全文级问题可追加为全局审核意见。
- 单个样本文档中的错词、错译或固定句式只能作为上下文线索，不能作为自动规则来源。

## 可见批注验收标准

进入 Word 的每条非全文问题必须同时满足：

- 有可在单文件原文中定位的 `anchor_span`、`anchor_locator` 和 `anchor_text`。
- 表格内问题若有 `P... (T...R...C...)` 坐标且原文片段存在于该单元格，视为兼容定位提示；首选仍是 `unit_id + anchor_quote`。
- 相邻双语脚注中，若 `original` 精确出现在紧邻的配对语言段落，也视为可稳定定位。
- `original` 或 `evidence_quote/evidence` 支撑问题判断，不能是“见原文”“同上”等泛称。
- Word 批注文案使用 `发现`、`原文`、`建议` 三段；`发现` 合并问题和依据的用户可读判断，避免重复展开。批注不得包含 JSON 文件名、内部路径、SDK 错误、堆栈信息、内部枚举字段或 `P342` 这类内部定位号。
- 不依赖方案、记录、LIMS、样品台账等外部资料；外部依赖项默认过滤，不增加人工复核成本。
- 不是纯润色、语序偏好、日期格式偏好、大小写偏好或无依据的 GMP/法规状态判断。
- 锚点不能是 `and`、`the`、`high`、`solution`、`report` 这类过短泛词；必须输出足够长的原文片段。
