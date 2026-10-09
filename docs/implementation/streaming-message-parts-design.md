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

`ClaudeStreamProjector` 只接受完整 raw message/block framing。已通过索引和类型校验的空字符串 `text_delta` 是不发布文字的空操作；非字符串仍拒绝。typed `AssistantMessage`
可以先于 raw block stop 到达，不能替代 framing 边界。`AssistantAnswerTimeline` 是
raw/typed/Result 新增后缀的唯一对账来源，typed replay 不重新拼接全文。
来源 router 只持有分类和有界身份窗口，不为分类扣留整段正文。
空工具来源收尾时清除工具状态；raw-only 后续答案不依赖整轮是否见过 typed 文本。

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
