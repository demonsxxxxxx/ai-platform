# 通用事实抽取工作流

本参考文件定义领域中立的工作流，用于将来源文件转换为已验证事实包。

## 产物层级

1. `source-manifest.json`
   - 记录所有允许和禁止输入。
   - 包含文件类型、路径、可选 hash、来源状态和来源边界说明。

2. `source-index.json` / `source-index.md`
   - 将允许来源映射为 chunk、段落、表格、sheet、行、页、OCR manifest 和其他定位信息。
   - 帮助抽取角色快速找到候选证据。
   - 除非它指向已审查的中间产物，否则它本身不足以作为证据。

3. `fact-shards/*.json`
   - 每个分片拥有 active profile 声明的一个边界清晰的抽取范围。
   - 分片只包含自己负责的章节，以及共享的审查字段。
   - 每个重要事实都应带有证据引用；没有证据的内容应列为缺失或需要审查。

4. `verification/*.json`
   - 记录针对分片或已装配事实包的独立核验轮次。
   - 核验者检查来源支撑、章节归属、缺失证据、冲突和 profile 特定规则。

5. `fact-packet.json`
   - 只能从已校验分片装配。
   - 是下游写作、渲染、分析或交付 skill 唯一获准消费的事实输入。

## Profile-first 工作流

Profile 可以定义 `primary_context_shard`。如果存在，应先抽取并校验该分片，再处理依赖分片。依赖分片必须使用已验证主上下文来筛选来源材料，避免跨项目或跨实体污染。

工作流必须基于上游显式提供的 `profile_id` 运行；通用核心不负责在缺失 profile 时选择、推断或回退到任何 profile。

Profile 决定 shard 顺序、字段边界、证据规则和领域校验；agent 身份保持通用。正式抽取始终由 `reference_fact_extraction_agent` 执行，独立核验始终由 `reference_fact_validation_agent` 执行。

示例：`ctd-32s73-stability` profile 会先抽取 `project_profile`，再抽取 `long_term`、`accelerated` 和 `stress_study` 分片。

## 通用事实规则

一个事实只有同时满足以下条件时才可被消费：

- 位于 active shard 允许的章节中；
- 有直接证据支撑，或带有声明清楚的推导过程；
- 已经过独立核验；
- 不被未解决的更高优先级证据反驳；
- 通过通用 schema 和 active profile 规则校验。

如果这些条件不满足，应把该项记录到 `missing_evidence`、`manual_review_items` 或 `conflicts`，而不是编造取值。

## 装配规则

装配是机械动作。装配器可以：

- 复制已校验分片中的归属章节；
- 合并共享的 `missing_evidence` 和 `manual_review_items`；
- 保留 provenance 和输入 hash；
- 在 profile 声明时按 `domain_native_output.top_level_sections`、`section_sources` 和 `deferred_sections` 导出 profile 原生事实包形状。

当工作流暂停请求 fact shard 时，step response 会通过 `gates.subagent` 暴露正式消费门禁；`fact-extraction-request.json` 会重复记录同一 subagent gate。没有 subagent 能力时，不要提交 passing shard，应报告 `SUBAGENT_REQUIRED`。

装配器不得推断新事实、填补缺失值、规范化无证据支持的值，或静默解决冲突。
