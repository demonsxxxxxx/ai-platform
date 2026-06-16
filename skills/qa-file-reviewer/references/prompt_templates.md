# File Reviewer - Prompt Templates

本文档只保留调用 agent 进行语义复核时的通用提示原则。当前不维护固定错词、固定句式、产品名组合或试验条件枚举模板。

深度审核 / 平台全权审核时，默认使用 Claude Agent SDK `Agent` 派发多个 reviewer shard，再由 final merge reviewer 汇总；不是单一 prompt 跑完整个审核。

## 调用 agent 语义复核

### 调度来源

- 由调用 agent 使用当前会话已可用的模型能力执行语义复核。
- `scripts/run_qa_review.py` 不读取模型环境变量，也不自动调用内置 LLM。
- 调用 agent 必须把结果写成 JSON 文件，并通过 `scripts/run_qa_review.py --agent-review-json <json>` 交回确定性 runner。
- 不得在 prompt、日志、报告或源码中输出密钥。
- risk classifier shard 必须独立产出，不能与普通语言 reviewer 混成一份自由文本总结。
- final merge reviewer 必须消费全部 reviewer shard 和 deterministic context，再输出唯一 merged JSON。

### 目标

- 按段落分块阅读全文。
- 判断中文、英文、双语一致性和跨段语义一致性问题。
- 只输出能被原文片段支撑的问题。
- 不做无证据润色。
- 不输出单纯语序偏好、表达风格偏好或公司未规定的日期格式偏好。
- 参考公司标准和可配置术语库时，必须说明证据来源；未配置的样本文档 literal 不得当作规则。

### 输出约束

模型必须返回 JSON object：

```json
{
  "schema_version": "qa-file-reviewer.agent-review.v1",
  "issues": [
    {
      "category": "zh_language|en_language|bilingual_consistency|semantic_consistency",
      "severity": "关键|主要|次要",
      "paragraph_index": "段落编号",
      "original": "原文精确片段",
      "evidence_quote": "可在原文中找到的证据片段，优先与 original 相同或相邻",
      "issue": "问题说明",
      "suggestion": "修改建议",
      "evidence": "判断依据",
      "confidence": "high|medium|low"
    }
  ]
}
```

推荐文件名：

- `zh_agent_review.json`
- `en_agent_review.json`
- `bilingual_semantic_agent_review.json`
- `structure_agent_review.json`
- `data_consistency_agent_review.json`
- `risk_classifier_agent_review.json`
- `final_merge_agent_review.json`

调用 agent 应严格校验：

- `category` 只能是 `zh_language`、`en_language`、`bilingual_consistency`、`semantic_consistency`。
- `severity` 只能是 `关键`、`主要`、`次要`。
- `confidence` 只能是 `high`、`medium`、`low`。
- `original` 或 `evidence_quote/evidence` 中必须包含能在文档中逐字或去空白后匹配的源文本片段；不要只在 `issue` 或 `suggestion` 里引用原文。
- 表格问题应保留段落和单元格线索，例如 `P571 (T7R5C3)`；不要只写“表5某处”，否则无法稳定插入批注。
- 双语脚注或注释行被拆成相邻段落时，应优先给出实际问题所在语言的精确原文；如果段号引用了配对中文行，`original` 仍必须写英文问题原文。
- 科学单位中的等价字形可以按原文输出，例如 `µm` / `μm`，runner 会归一化匹配；不要因此改成模糊描述。
- `suggestion` 必须是可直接执行的动作，例如“改为...”“补充...”“删除...”或英文 `Change ... to ...`、`Unify to ...`、`Insert a space ...`。“请核对”“建议优化”“Consider rephrasing” 这类空泛建议会被过滤。
- 如果判断结果是“无需修改 / No change needed”，不要输出为 issue。
- 不要输出纯大小写风格、标题大小写、表达更自然、awkward phrasing 等润色类问题，除非能引用公司标准、模板要求或术语表。
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
- `issue` 和 `suggestion` 中的引号不会被当作原文证据；弱模型不能靠建议文本制造锚点。
- 仅表达风格不同但没有公司标准或明确证据支撑时，不报告为问题。
- 中低置信、锚点不稳、证据不足或建议空泛的问题默认不进入 Word；只有显式设置 `comment_intent: "global_summary"` 的全文级问题可追加为全局审核意见。
- 单个样本文档中的错词、错译或固定句式只能作为上下文线索，不能作为自动规则来源。

## 可见批注验收标准

进入 Word 的每条非全文问题必须同时满足：

- 有可在单文件原文中定位的 `anchor_span`、`anchor_locator` 和 `anchor_text`。
- 表格内问题若有 `P... (T...R...C...)` 坐标且原文片段存在于该单元格，视为可稳定定位。
- 相邻双语脚注中，若 `original` 精确出现在紧邻的配对语言段落，也视为可稳定定位。
- `original` 或 `evidence_quote/evidence` 支撑问题判断，不能是“见原文”“同上”等泛称。
- Word 批注文案使用 `发现`、`原文`、`建议` 三段；`发现` 合并问题和依据的用户可读判断，避免重复展开。批注不得包含 JSON 文件名、内部路径、SDK 错误、堆栈信息、内部枚举字段或 `P342` 这类内部定位号。
- 不依赖方案、记录、LIMS、样品台账等外部资料；外部依赖项默认过滤，不增加人工复核成本。
- 不是纯润色、语序偏好、日期格式偏好、大小写偏好或无依据的 GMP/法规状态判断。
- 锚点不能是 `and`、`the`、`high`、`solution`、`report` 这类过短泛词；必须输出足够长的原文片段。
