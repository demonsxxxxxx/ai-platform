# Agent Output Acceptance Examples

本文档定义 `qa-file-reviewer` 在 Claude Agent SDK 多 agent 路径下的验收示例。

目标不是放宽审核，而是在子 agent 输出“轻微不规范”时，尽量归一化并保留有效问题；只有无法可靠修复时，才降级为 `needs_user_check`。

## 全局验收前提

- 用户输入：单个合法 `.docx`
- 用户输出：唯一主交付 `reviewed_docx`
- 内部中间产物可存在，但 history/download 默认只暴露 `reviewed_docx`
- 外部证据依赖、锚点不稳、低置信、证据不足或建议空泛的真实文档问题，默认过滤出 Word，仅保留内部跳过原因；显式全文级 `global_summary` 例外。
- 缺失 agent JSON、JSON 解析失败、schema 无法修复和 SDK/sub-agent 执行错误只进入内部诊断，不得写成 Word 批注

## 示例 1：枚举同义值自动归一化

输入 agent JSON：

```json
{
  "agent_role": "bilingual_consistency",
  "issues": [
    {
      "category": "bilingual_consistency",
      "severity": "主要",
      "paragraph_index": "P60",
      "original": "Environment Health Safety Department",
      "issue": "英文缺少连接词 and。",
      "suggestion": "改为 Environment, Health and Safety Department。",
      "evidence": "EHS 通常展开为 Environment, Health and Safety。",
      "confidence": "high",
      "coverage_domain": "bilingual_consistency",
      "review_basis": "agent_semantic",
      "requires_external_evidence": false,
      "external_evidence_type": "none",
      "comment_intent": "suggest_edit"
    }
  ]
}
```

预期归一化：

- `agent_role`: 可保留原值
- `category`: `bilingual_consistency`
- `coverage_domain`: `bilingual`
- `comment_intent`: `suggest_change`
- 其余字段不变

预期结果：

- `review_result.json.issues` 至少 1 条
- 该条 issue `status=confirmed`
- `comment_plan.json` 出现 1 条“建议修改”批注
- `reviewed_docx` 中该问题为可见批注，不进入 `human_review_queue`

## 示例 2：自由文本 basis/type 保守回退

输入 agent JSON：

```json
{
  "agent_role": "structure",
  "issues": [
    {
      "category": "format",
      "severity": "次要",
      "paragraph_index": "P99",
      "original": "文件起草、审核、批准按照表3执行。",
      "issue": "R 符号未定义。",
      "suggestion": "补充 R 的含义说明。",
      "evidence": "正文只解释了 W、A、***。",
      "confidence": "high",
      "coverage_domain": "structure_and_format",
      "review_basis": "文档结构一致性和逻辑连贯性标准",
      "requires_external_evidence": false,
      "external_evidence_type": "",
      "comment_intent": "suggest_edit"
    }
  ]
}
```

预期归一化：

- `category`: 回退到可接受的通用语义类别，不得保留非法枚举
- `coverage_domain`: 回退到 `structure`
- `review_basis`: 回退到 `single_doc_internal` 或 `agent_semantic`
- `comment_intent`: `suggest_change`

预期结果：

- 如果锚点稳定，则生成 `confirmed` issue 和“建议修改”批注
- 如果锚点不稳定，则默认过滤出 Word；只有显式全文级 `global_summary` 可写入文末全局审核意见。
- 不允许仅因为非法枚举就直接丢进全量人工审核

## 示例 3：轻微 JSON 语法错误可修复

输入 agent JSON 片段：

```json
{
  "agent_role": "en_language",
  "issues": [
    {
      "category": "en_language",
      "severity": "主要",
      "paragraph_index": "P60",
      "original": "Environment Health Safety Department",
      "issue": "英文缺少连接词。",
      "suggestion": "建议修改为"Environment, Health and Safety Department"",
      "evidence": "原文为"Environment Health Safety Department"。",
      "confidence": "high",
      "coverage_domain": "en_language",
      "review_basis": "agent_semantic",
      "requires_external_evidence": false,
      "external_evidence_type": "none",
      "comment_intent": "suggest_change"
    }
  ]
}
```

预期修复：

- 仅针对明显的字符串内裸引号做转义修复
- 修复后 JSON 可被解析
- 若修复后字段都合法，则继续进入正常 issue 映射

预期结果：

- `review_result.json` 中保留该 issue，而不是整份 `en_agent_review.json` 直接报 `JSONDecodeError`
- 若锚点稳定，则写成 `confirmed`
- 若锚点不稳，则默认过滤出 Word；只有显式全文级 `global_summary` 可写成文末全局审核意见。

## 示例 4：无法可靠归一化时只进入内部诊断

输入 agent JSON：

```json
{
  "agent_role": "custom-agent",
  "issues": [
    {
      "category": "foo_bar",
      "severity": "一般",
      "paragraph_index": "",
      "original": "",
      "issue": "这里可能有问题。",
      "suggestion": "",
      "evidence": "",
      "confidence": "maybe",
      "coverage_domain": "unknown",
      "review_basis": "自由文本说明",
      "requires_external_evidence": "不确定",
      "external_evidence_type": "公司模板",
      "comment_intent": "fix_it"
    }
  ]
}
```

预期结果：

- 不自动升级为 `confirmed`
- 进入内部诊断 metadata / manifest error
- 不进入 `human_review_queue` 的用户可见路径
- `comment_plan.json` 不生成包含 schema 错误、JSON 文件名、内部路径或异常信息的批注

## 示例 5：最终用户交付契约

无论上面哪种场景，最终都必须满足：

- `/api/review/history` 只暴露 `file_key=reviewed_docx`
- 下载文件为可打开的 Word
- `review_result.json.artifacts.reviewed_docx` 指向真实文件
- `commenting.actual_total >= commenting.expected_total`
- 若 `semantic_agent_review=false` 且 `semantic_agent_review_attempted=true`，运行诊断必须保留在 `review_result.json` / logs；Word 中只保留真实文档审核发现，不写“语义审核未完整完成”类内部状态批注
