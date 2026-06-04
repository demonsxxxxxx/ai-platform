# File Reviewer - 职责分支边界

本文档定义 `qa-file-reviewer` 的当前职责拆分。

## 总原则

- 每个职责分支只负责自己的判断边界。
- 当前代码真实能稳定产出的确定性问题，来自 `format`、`project_number` 和 `content_consistency`。
- 中文、英文、双语和语义问题主要来自调用 agent 在脚本外的语义复核，并通过 `--agent-review-json` 进入统一合并、门禁和批注链路。
- 聚合层负责去重、门禁、人工复核清单维护和 Word 批注可见性。
- 定位不稳、低置信、建议空泛或外部证据依赖的问题默认过滤出 Word，只在内部诊断、跳过计数或日志中保留原因，避免增加人工复核成本。

## 职责清单

| 职责分支 | 当前职责 | 当前能力边界 |
| --- | --- | --- |
| `format` | 格式硬规则审核 | 只覆盖已实装页面、页脚、字号、表格格式规则。 |
| `project_number` | 实体识别与模板残留检查 | 只看项目号和相关引用语境，不扩展为通用实体识别。 |
| `content_consistency` | 高置信结构一致性检查 | 只覆盖可泛化结构模式，不维护样本文档 literal 规则。 |
| 调用 agent 语义复核 | 结构、中文、英文、双语、数据一致性、风险分类 | 不属于 `run_qa_review.py` 内置 LLM 分支；必须输出结构化 JSON，并由 runner 校验、锚定、合并。 |
| `merge` / `gate` | 聚合、门禁、人工复核 | 负责汇总、门禁和交付控制。 |

## 确定性职责

### `format`

当前只覆盖：

- 页边距
- A4 纸张 / 装订线 / 页眉页脚距离
- 页脚固定文本
- 页脚 8pt
- 正文 12pt
- 表格内容 10.5pt
- 大表重复表头
- 表内双语说明的多余空格或缩进

不宣称覆盖：

- 全量字体族校验
- 行距、段前段后、标题层级等完整版式校验
- 开放式“审美型”格式判断

### `project_number`

当前只检查：

- 当前项目号之外的项目号残留
- 模板残留式项目号误用
- 合理引用语境排除

不扩展为：

- 产品名词典
- 项目名录库
- 模板名录库

### `content_consistency`

当前只检查可跨文档泛化的结构模式：

- 断裂引用
- 同一表格单元内中英文检测项的方法缩写遗漏
- 正文组别编号超出表格方案组别
- 第三语言字符集模板残留

不扩展为：

- 某个样本文档的固定句子
- 某个产品名或试验条件组合
- 某个模板页完整字符串
- 未经配置确认的术语错译清单

## 调用 agent 语义复核职责

### 调用 agent 语义复核

- 当前由调用 agent 或 Claude Agent SDK 在脚本外统一承担结构、中文、英文、双语、数据一致性和风险分类审核。
- 不维护固定中文错词、固定英文短语、双语关键词枚举或产品级样例规则。
- 调用 agent 输出 `*_agent_review.json` 后，由 `run_qa_review.py --agent-review-json` 合并为兼容分支 `llm_full_review`。
- 高/中置信、可稳定锚定、有原文依据且建议清晰的问题可写入“建议修改”批注；低置信、锚点不稳、外部证据依赖或建议空泛的问题默认不写入 Word。只有显式 `global_summary` 全文级问题允许无原位锚点并追加到文末全局审核意见。
- 缺失 agent JSON、JSON 解析失败、非法 schema、SDK/sub-agent 执行失败和内部路径只记录到内部诊断，不进入 `human_review_queue` 的用户可见批注路径。
- 内部通过 `category` 区分：
  - `zh_language`
  - `en_language`
  - `bilingual_consistency`
  - `semantic_consistency`
- Claude Agent SDK 推荐分支：
  - `qa-structure-reviewer`：目录、页码提示、页眉页脚、重复标题行、格式结构。
  - `qa-zh-language-reviewer`：中文错别字、漏字、多字、标点、歧义。
  - `qa-en-language-reviewer`：英文拼写、语法、术语、大小写。
  - `qa-bilingual-reviewer`：中英文缺译、错译、术语映射、DS/DP 等双语一致性。
  - `qa-data-consistency-reviewer`：数字、百分号、单位、时间点、正文与表格内部一致性。
  - `qa-risk-classifier`：区分 `suggest_change`、`request_check`、外部证据依赖。

## 聚合 / 复核职责

- 汇总所有分支结果。
- 只在 `rule_id`、位置和证据一致时去重。
- 维护 `branch_execution_manifest`。
- 维护 `human_review_queue`（人工复核清单）。
- 维护 `comment_plan.json`，确保只有通过质量门禁的用户可见问题进入批注 Word。
- 维护确定性 `summary.quality_score`。
- 在输出前运行 `scripts/validate_pipeline_gate.py <review_json>`。
- 门禁失败时停止交付，不假装“部分成功”。

## 能力边界

- `project_number` 不是通用实体识别引擎。
- 调用 agent 必须输出结构化结果，并接受锚点、置信度和人工复核约束。
- 调用 agent 对原始记录、方案、样品信息或 LIMS 才能判断的问题只能输出 `requires_external_evidence=true` 和 `comment_intent=request_check`，不得判定为确定错误。
- 当前不维护固定错词、产品名、试验条件或句式规则库。
- 任何新增规则必须能追溯到公司标准、可配置术语库，或已证明可泛化的结构模式。
- 单个样本文档中发现的问题只能进入 fixture、调用 agent 提示约束或人工复核，不得直接硬编码进源码。
