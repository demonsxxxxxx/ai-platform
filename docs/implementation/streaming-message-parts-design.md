# Agent 流式消息与最终交付架构

状态：PR #1651 的源码合同。部署、真实 provider 时序、网络刷新和浏览器首字验收需要独立证据。
外层 SSE v4、授权、重放和 Run 终态继续由
[wire contract](../architecture/redis-streams-sse-wire-protocol.md)、
[execution control](../architecture/redis-streams-sse-execution-control.md) 和
[ADR 0013](../adr/0013-redis-stream-only-sse.md) 负责。

## 产品合同

安全 Assistant 文字按增量显示，首次显示无需等待工具或 Result。后续同一 provider
消息出现工具时，服务端按明确的片段身份把该消息文字分类为工作过程。前端移动展示归组，
账本保留原始 delta 及后续分类事实。答案开始显示时收起此前工作，后续工作活动出现时展开；
同一展示阶段的普通 token 更新保留用户手动选择。

| 来源 | 公开展示 | 最终答案 |
| --- | --- | --- |
| 已脱敏 Assistant 文本，尚未分类 | 正文预览 | 不进入最终复制或 receipt |
| 分类为 answer 的 Assistant 片段 | 正文 | 进入最终复制与 receipt v2 |
| 分类为 work 的 Assistant 片段 | 可折叠工作过程 | 不进入最终答案 |
| 既有 `message.delta` | 保持既有答案语义 | receipt v1 |
| 既有 `commentary.delta` | 保持既有摘要或 worktrace 展示 | 不进入 receipt |
| 工具、Skill、MCP、子任务活动 | 获准的名称、状态、进度和耗时 | 不是答案或执行成功证明 |
| 显式 `attach_file` | 平台验证后的附件卡片 | 独立文件交付 |
| Thinking、原始工具输入/结果、凭据、私有运行路径 | 不公开 | 不进入文本候选 |

公开文字、工具完成、Run 成功和文件可下载是独立事实。失败或取消保留已公开的安全文字，
同时展示真实终态；不生成成功答案回执。普通聊天不要求 `structured_output`。

## 参考来源

对齐 Aivory 的文字先显示、工具出现后归组、正文出现时收起过程和复制正文体验。
核验基线为 `hjxwz123/Aivory@d3fe91dd2fb69dffb27f31b85043feeb22e2a860`：
[增量与工具归组](https://github.com/hjxwz123/Aivory/blob/d3fe91dd2fb69dffb27f31b85043feeb22e2a860/src/store/conversations.ts)、
[过程展示](https://github.com/hjxwz123/Aivory/blob/d3fe91dd2fb69dffb27f31b85043feeb22e2a860/src/components/chat/reasoning-trace.tsx)。
本项目继续使用自己的平台授权、公开脱敏、公共账本、receipt 和历史恢复边界。

## 版本化片段事实

外层 envelope 仍是 SSE v4；闭合 schema 新增两个明确的事件族成员：

| 事件 | 闭合 payload |
| --- | --- |
| `message.part.delta` | `schema_version="ai-platform.assistant-text-part.v1"`, `part_id`, `delta` |
| `message.part.classified` | 相同 `schema_version`, `part_id`, `role="answer"或"work"` |

`part_id` 是 adapter 生成的稳定 opaque SafeRef，绑定一个已验证 provider 消息。
私有 provider ID 留在 adapter。split typed 文本和工具、raw/typed 重放共用正确的消息片段身份。
现有 `message_id`、Run/Attempt、incarnation、sequence、租约、授权和 callback ACK 没有替代者。

首次 delta 引入 pending 片段，后续 delta 追加同一片段；分类只能引用已公开片段。
完成前允许 pending→answer、pending→work 和 answer→work；work→answer 拒绝。
同角色重复分类保持幂等。分类不修改已持久化行、事件 ID 或序号。
成功 `message.completed` 前所有片段必须已分类。完成后新增正文或分类、跨消息片段、孤立分类、
旧 delta 与新 part 混用、重复 source identity 均拒绝。

生成源是 `schemas/public_run_stream.v4.schema.json`；Python/TypeScript contracts 一起生成并检查。
不在旧 `message.delta` 增加隐藏字段，不用 `worktrace_` 或前端字符串猜测代替片段协议。

## SDK 来源、安全与顺序

`ClaudeStreamProjector` 保留严格 raw message/block framing；同消息重叠块、缺 stop、
错误索引或类型仍拒绝。消息闭合前只发布经过脱敏的 pending 预览。局部 raw 异常
隔离当前消息，已 ACK 的安全片段分类为 work，不能进入答案或 receipt；后续 typed
或 Result 不修补它。仅不同 provider ID、主 Agent、完整 raw framing 的后续消息可恢复。
如果最终仍处于隔离状态，或者 ACK、脱敏、身份/正文对账、工具证据或 SDK 终态失败，
仍走对应失败路径。没有伪造 stop，也没有采用未证实的不同索引重叠容错策略。

锁定 SDK `0.2.130` 的 parser 原样传递 `StreamEvent.event`，typed 消息保留 provider
message ID、观察 UUID 和 `parent_tool_use_id`；这证明身份字段映射，不证明线上事故
帧的来源或完整性。官方 [流式文档](https://code.claude.com/docs/en/agent-sdk/streaming-output)
说明 typed 消息按非空内容块交付，可能先于 raw stop；不是整条消息的终态快照。
因此 raw 模式的 answer 分类等待 raw message_stop，高层消息没有成为新的回填权威。
子 Agent 文字不进入主答案；其工具身份仍登记到既有脱敏 gate，工具活动继续由
获准生命周期回调管理。raw 状态只属于当前主消息，不按 index 跨消息或 Agent 共享。

已通过索引和类型校验的空字符串 `text_delta` 是不发布文字的空操作；非字符串仍拒绝。typed `AssistantMessage`
可以先于 raw block stop 到达，不能替代 framing 边界。`AssistantAnswerTimeline` 是
raw/typed/Result 新增后缀的唯一对账来源，typed replay 不重新拼接全文。
来源 router 只持有分类和有界身份窗口，不为分类扣留整段正文。
重复的 typed 来源计数预校验、无消费方的 raw 索引来源集合和仅供测试读取的
partial 标志已移除；raw 全来源列表只用于计数，改用既有 completed 索引集合限额。
Timeline 的身份、重放及前缀一致性校验保留，用于保护已 ACK 的公开事实，不是另一套工具协议权威。
空工具来源收尾时清除工具状态；raw-only 后续答案不依赖整轮是否见过 typed 文本。

本次字段审计仅覆盖这条投影路径；下列内部状态不写入历史，公开 part/receipt 字段保持兼容。

| 字段或状态 | 来源与实际消费行为 | 处置及兼容性 |
| --- | --- | --- |
| provider message ID / observation UUID / parent ID | SDK parser；runner、router、Timeline 用于主/子作用域、raw/typed 绑定及重放识别 | 保留；不能替代公开 message ID |
| text source identity / generations | Projector；Timeline 绑定正文、block stop 和 typed 来源 | 保留，防止不同消息或来源错误合并 |
| stop reason / message open | raw/typed 生命周期；runner 选择 work/answer 并验证终态 | 保留；raw 模式须等 message_stop 才选择答案 |
| completed block indexes | 已验证 raw stop；Projector 拒绝索引复用并限制来源数量 | 保留；替代只被计数的 `_raw_sources` 列表 |
| `_raw_sources_by_index` / `_partial_emitted` | 前者只写不读；后者只有测试读取 | 删除；实际文字输出断言保留，无历史消费方 |
| `validate_typed_text_source_count` | runner 预校验，随后来源绑定重复调用同一窗口验证 | 删除预校验入口；`typed_text_source_identity` 保留验证 |
| first failure shape / preceding frames | Projector；runner 私有、有界、无正文诊断 | 保留；成功分支经既有诊断保存通道留存，从公开结果剥离，不改管理端接口 |

未来替换 SDK 的最小接缝是 executor 适配层：负责 provider 消息/块身份、Agent
作用域和终态向既有安全答案 gate、part 分类及 receipt 合同的映射。Claude raw
framing 和 raw/typed/Result 对账细节留在 `app/executors`，不成为平台公开字段。
现有 sandbox runtime 诊断归一化与 Worker 的 SDK 诊断投影仍耦合既有 SDK schema，
替换时需在适配层映射；不因本次修复改变通用持久化、前端或新建插件框架。

每个新增后缀在任何公开 callback 前经过同一个 `PublicAnswerStreamGate`。
gate 保留必要的敏感短后缀及其来源跨度，后续释放仍属于原片段；已知 token 替换属于 token
起始来源。跨来源 sanitizer 转换无法证明输出归属时 fail closed，不能猜测归给首个片段。
终结时安全尾部释放，不把普通字母作为私有 token 的不完整前缀直接删掉。
动态工具身份注册继续检查已经发布的所有文字。单个公开 delta 至多 8192 code points，
callback 至多 100 个事件，缺失 ACK 停止后续发布。

工具 block/stop 标记只分类其明确绑定的来源，Thinking 和工具 JSON 不成为文字。
Result 只补经过验证的同来源后缀。明确的 terminal-only 兼容来源仍有自己的身份；
工具回合后的独立 Result 答案只在现有严格边界允许时产生新片段，不能把工具原文重新算作答案。
非 Agent 的 `on_text` 兼容调用仍保留；Sandbox 在 part callback ACK 后抑制重复的
`assistant_delta` compatibility callback。

平台工具准入、生命周期回执、effectful outcome、文件校验和终态 fence 保持独立。
已知证据拒绝关闭后续文字发布；已持久化安全预览不因未来工具失败而消失。
SessionStore 最后刷新、消息读取关闭和工具控制回调收敛仍在业务完成之前；资源回收独立。

## 最终答案与 receipt v2

`ai-platform.assistant-answer-receipt.v2` 保留五个字段：`schema_version`、`message_id`、
`delta_count`、`text_length`、`last_delta_event_id`。只选最终 role=answer 的非空片段，
按首次观察顺序组合；不同 provider 片段间插入两个 LF，同一片段内不插入额外分隔符。
`delta_count` 是选中 delta 事件数，`text_length` 包括片段间两个 LF，last delta 为账本顺序中
最后一个选中 source event ID。`message.completed` 的计数、长度和 causation 必须一致。
没有答案的 artifact-only 成功不伪造 receipt。成功流式 Sandbox 的 inline `message` 为空。

Worker 从当前授权 Attempt 的持久化 started/delta/classified/completed 事实重建，
不信任执行器返回正文或自行声明的选择。普通 append 在上游 Run fence 下以索引查询
message lifecycle、片段首个 delta/最新分类和 source identity；完成及 receipt 阶段完整验证账本，
包括历史损坏。schema `2026.10.09.1` 添加 message/part/source lookup 索引及 readiness 检查，
不改写既有公共事件或答案行。

## 前端、复制与历史

TextPart 的 `public_part_id` 和 `text_role` 明确表达片段身份和 pending/answer/work。
正文预览包含 pending+answer；工作过程包含 work；最终复制只含 answer，旧无标记正文保持兼容。
assistant-ui、目录和通知使用相同角色选择。分类精确更新对应片段，不清空整个消息。

实时、Redis replay、PostgreSQL history、gap 与 terminal hydrate 使用相同事实语义。
历史保留 part payload、公开事件身份、序号、消息和 incarnation；不跨片段或分类边界合并。
非法、foreign、orphan 分类使恢复失败，不能静默丢弃后推进水位或误判完成。
历史同 ID 的内容和角色优先于旧 live 状态，仅补确实缺失的安全文字、附件与活动。
恢复仍绑定原 Run，不能覆盖下一轮。终态卡片与已公开安全文本并存。

## 文件交付

`attach_file` 继续验证工作区路径、Skill 边界、数量和预算后记录有序 descriptor。
只有显式清单中的文件进入 Artifact 存储与授权下载。工作目录扫描、临时 JSON、日志和脚本
不自动成为交付物；终态校验失败不发布尚未验证的产物。文字流与文件清单独立。

## 兼容、退役与发布

本次替换 Claude 来源分类后整段发布和 worktrace 生产路径，以及对应等待分类的断言。
旧 `message.delta`/receipt v1、旧 commentary/worktrace 历史的明确兼容 owner 是 Streaming decoder、
Worker receipt reader 和 Chat reducer。现有非 Claude producer 的旧 delta API 保留。
旧 `assistant_delta` 公开历史 reader、成功 terminal-body fallback、`assistant_final` 仍已退役。
来源的全文缓冲、重复 typed 拼接和前端仅凭 streaming 切换折叠的旧路径退出。

producer、schema/decoder、Worker、history 和 frontend 必须协调发布。旧客户端对未知新事件
fail closed，需要更新；没有运行时协商、双流或 silent fallback。新行和 v2 receipt 存在期间必须
保留新版 readers。回滚 producer 要保留这些 readers 和当前 schema/index readiness，不能直接
回滚到无法读取新事实的旧镜像；原有 release runbook 的迁移约束继续适用。
schema/index ledger 升级后，旧镜像的精确 readiness 检查会拒绝新增索引合同；
历史数据仍保留，但旧镜像不作为本次升级的二进制回滚目标。

## 验收边界

受影响测试覆盖首次安全 prefix 在 message stop 前 ACK、typed/raw replay、空工具状态、
raw-only 后续答案、split text/tool、Result suffix、跨来源脱敏、callback 拒绝、最终 receipt、
真实 PostgreSQL 分批写入和 Redis replay、front live/gap/terminal hydrate、复制与折叠阶段。
测试须在锁定依赖下重新执行。隔离测试和源码审查不证明真实 provider、代理刷新或浏览器 paint；
部署后的 External Acceptance 仍须按固定 commit/image 验收。
