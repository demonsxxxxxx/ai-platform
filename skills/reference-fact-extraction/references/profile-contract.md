# Profile 契约

Profile 为通用事实抽取工作流定义领域含义。通用核心提供机制；profile 定义抽取什么、如何划分分片，以及如何校验输出。

## 必需 profile 字段

`profile.json` 应包含：

```json
{
  "profile_id": "example-profile",
  "display_name": "Example Profile",
  "profile_version": "v1",
  "supported_source_types": ["docx", "xlsx", "pdf", "txt", "json", "csv", "tsv", "image"],
  "default_forbidden_source_patterns": ["~$*"],
  "primary_context_shard": "profile",
  "schema_versions": {
    "generic_fact_shard": "reference-fact-shard-v1",
    "generic_fact_packet": "reference-fact-packet-v1",
    "domain_fact_packet": "example-domain-packet-v1"
  },
  "shards": [],
  "output_modes": ["generic", "domain_native"]
}
```

## Shard 声明

每个 shard 声明其归属范围、依赖和必需章节。agent 身份由通用核心统一提供，不放在 profile 中：

```json
{
  "shard_type": "project_profile",
  "label": "project-profile",
  "artifact_key": "project_profile_facts",
  "path_name": "project-profile-facts",
  "depends_on": [],
  "allowed_sections": ["project_profile", "sources", "missing_evidence", "manual_review_items"],
  "required_sections": ["project_profile", "sources"]
}
```

校验器会拒绝包含其他 shard 所属事实章节的分片。正式分片的 `agent_provenance.extraction_agent` 必须是 `reference_fact_extraction_agent`，`agent_provenance.validation_agent` 必须是 `reference_fact_validation_agent`。

## 输出模式

- `generic`：导出 `reference-fact-packet-v1`，领域事实放在 `facts` 下。
- `domain_native`：按 profile 的 `domain_native_output` 导出 profile 特定的事实包形状，以兼容下游。

`generic` 是输出模式/事实包形状，不是 `profile_id`。工作流必须由上游显式提供 `profile_id`；缺失时不得选择、推断或回退到 `generic` 或任何其他 profile。

一个 profile 可以同时支持两种模式。CTD profile 同时支持 generic 和 `ctd-32s73-fact-packet-v1` 输出。

`domain_native_output` 可声明：

- `top_level_sections`：native fact-packet 必须包含的顶层字段；通用装配器按该列表机械生成输出。
- `deferred_sections`：基础事实包阶段应保留为空或延后处理的默认章节。
- `section_sources`：可选映射，声明 native 顶层字段来自 generic packet 的哪个路径，例如 `facts.project_profile`、`sources`、`agent_provenance`。

如果 profile 需要领域深度校验，可在 profile 中声明 `validation_profile`，由 validator 分派到对应的 profile-specific 检查；通用核心不得通过硬编码字段名来装配 native 输出。

## 应放在 profile 中的内容

以下内容放在 profile 中，而不是通用核心中：

- 领域字段名；
- 受控词表；
- 来源优先级规则；
- shard 名称和依赖；
- 必需领域事实；
- 单位和标记语义；
- 领域特定校验规则；
- 到下游模板或系统的导出映射。

## 应放在通用核心中的内容

以下内容保持通用：

- source manifest 和 index 形状；
- 允许/禁止来源机制；
- fact shard 生命周期；
- provenance 要求；
- 独立核验要求；
- 装配机制；
- 通用 schema 校验；
- 缺失证据和冲突显式暴露；
- 通用抽取/核验 agent 身份：`reference_fact_extraction_agent` 和 `reference_fact_validation_agent`。
