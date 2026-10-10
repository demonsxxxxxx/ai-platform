# Agent 流式消息与最终交付架构

状态：PR #1651 的公开片段合同及 issue #1657 的 completed-block 来源实现。部署、真实 provider 时序、网络刷新和浏览器首字验收需要独立证据。
外层 SSE v4、授权、重放和 Run 终态继续由
[wire contract](../architecture/redis-streams-sse-wire-protocol.md)、
[execution control](../architecture/redis-streams-sse-execution-control.md) 和
[ADR 0013](../adr/0013-redis-stream-only-sse.md) 负责。

## 产品合同

已完成的安全 Assistant 文字块按片段增量显示，首次显示无需等待后续工具或 Result。后续同一 provider
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
私有 provider ID 留在 adapter。同一主消息的完成文字块与工具块共用消息片段身份。
现有 `message_id`、Run/Attempt、incarnation、sequence、租约、授权和 callback ACK 没有替代者。

首次 delta 引入 pending 片段，后续 delta 追加同一片段；分类只能引用已公开片段。
完成前允许 pending→answer、pending→work 和 answer→work；work→answer 拒绝。
同角色重复分类保持幂等。分类不修改已持久化行、事件 ID 或序号。
成功 `message.completed` 前所有片段必须已分类。完成后新增正文或分类、跨消息片段、孤立分类、
旧 delta 与新 part 混用、重复 source identity 均拒绝。

生成源是 `schemas/public_run_stream.v4.schema.json`；Python/TypeScript contracts 一起生成并检查。
不在旧 `message.delta` 增加隐藏字段，不用 `worktrace_` 或前端字符串猜测代替片段协议。

## SDK 来源、安全与顺序

Claude adapter 使用锁定 SDK 的完成 `AssistantMessage` 块，关闭
`include_partial_messages`。一个消息的文字块和工具块可以分别到达，使用相同 provider ID、
不同观察 UUID；完成块不是累计正文快照，也不保证携带 `stop_reason`。
主消息块是唯一公开文字来源；子 Agent 正文、Thinking、工具 JSON、raw `StreamEvent`
及 Result 正文不进入答案。子 Agent 工具 ID 仍登记用于主文字脱敏。

UUID、provider 来源归属与正文摘要留在本次 Attempt 的 adapter 身份表；精确重放不产生新
公开事实，冲突观察和已退役消息的新观察不被用于公开。不能驱逐仍可能重放的身份后把重放
当成新块，也不新增任意 128 块的 SDK 限额。来源 router 仅保留角色和文字存在标记，
不保留正文、raw 索引或分隔符对账。与已接受事实一样，角色记录保留到 Attempt 收尾，
合法多主消息续答按现有答案片段顺序保留。

每个新增后缀在任何公开 callback 前经过同一个 `PublicAnswerStreamGate`。
gate 保留必要的敏感短后缀及其来源跨度，后续释放仍属于原片段；已知 token 替换属于 token
起始来源。跨来源 sanitizer 转换无法证明输出归属时 fail closed，不能猜测归给首个片段。
终结时安全尾部释放，不把普通字母作为私有 token 的不完整前缀直接删掉。
动态工具身份注册继续检查已经发布的所有文字。单个公开 delta 至多 8192 code points，
callback 至多 100 个事件，缺失 ACK 停止后续发布。

工具块只分类其明确绑定的主消息来源；一个主消息可含多个工具 ID。
Result 处理成功、错误、取消、用量和会话终态，不补文字后缀、不创建 terminal-only 答案。
不因未收 raw block stop 而拒绝已完成 SDK 块，也不伪造 raw 结束帧。
非 Agent 的 `on_text` 兼容调用仍保留，先暂存完成块并在成功终态安全校验后收敛；
实时预览由 ACKed part callback 提供，不承诺 token-time 展示。Sandbox 在 part callback ACK 后抑制重复的
`assistant_delta` compatibility callback。

平台工具准入、生命周期回执、effectful outcome、文件校验和终态 fence 保持独立。
已知证据拒绝关闭后续文字发布；已持久化安全预览不因未来工具失败而消失。
SessionStore 最后刷新、消息读取关闭和工具控制回调收敛仍在业务完成之前；资源回收独立。

私有 `ModelTextCheckpoint` 的真实模型代理 raw wire 消费者保留。SDK observer 只在实际收到
raw 时据实记录；正常 partial 关闭路径不产生 SDK raw checkpoint。缺记录表示未采集，
不能推断 SDK 没有文字，也不能用 typed 块伪造 raw framing 或完整覆盖。

### #1657 来源退役清单

| 精确来源 | 处置与替代 |
| --- | --- |
| `app/executors/claude_stream_projection.py` 全文件 | 删除；其 projector/timeline 没有其他生产调用者，完成 SDK 块由 adapter 直接公开，不保留第二套 raw/typed/Result 协议权威 |
| `tests/test_claude_stream_projection.py` 全文件 | 随唯一生产实现退役；新 `tests/test_claude_typed_blocks.py` 验证完成块、多消息、作用域、重放、脱敏、终态及历史/管理页事实一致性 |
| backend CI 的旧 projection 文件 selector 及 owning selector 断言 | 均改为 `tests/test_claude_typed_blocks.py`，不扩大为目录或全仓 pytest |
| `AssistantTextSourceBuffer` 的 timeline owner/binding、分隔符删除、Result 专用入口、重复计数和角色窗口淘汰 | 删除；SDK 观察身份表拥有重放约束，router 保留 Attempt 角色元数据，公共 part reducer 拥有片段间分隔符 |
| `test_exact_timeline_replay_does_not_reopen_or_change_owner` | 退役 timeline 约束；SDK UUID 精确重放、冲突和 129 块后重放由新 runner 用例验证 |
| `test_separator_conflict_does_not_mutate_source_state` | 退役不再产生的 synthetic separator；实际前导换行和长正文保留、来源原子性用例继续保留 |
| SDK 私有 raw checkpoint 与历史 runtime diagnostic schema/reader | 保留现有真实 wire 消费者及历史兼容；不把“无当前公开消费者”误当成“无历史数据消费者” |

## 最终答案与 receipt v2

`ai-platform.assistant-answer-receipt.v2` 保留五个字段：`schema_version`、`message_id`、
`delta_count`、`text_length`、`last_delta_event_id`。只选最终 role=answer 的非空片段，
按首次观察顺序组合；不同 provider 片段间插入两个 LF，同一片段内不插入额外分隔符。
`delta_count` 是选中 delta 事件数，`text_length` 包括片段间两个 LF，last delta 为账本顺序中
最后一个选中 source event ID。`message.completed` 的计数、长度和 causation 必须一致。
SDK 成功与公开答案存在分开：缺正文、work-only、缺身份或局部投影异常不改写 SDK 成功，
不生成假正文、假 receipt 或自动重试。真实 SDK 错误/取消、回调 ACK、权限、工具凭据冲突、
会话 EOF/mirror/final_sequence 和文件校验失败继续拒绝业务成功。提供 receipt 时仍完整校验
所有身份、长度和互斥字段。无答案的成功在管理端显示 unknown/incomplete；保留空 assistant
provider-coverage 锚点但不发布“答案已就绪”。成功流式 Sandbox 的 inline `message` 为空。

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

## 管理端公开回答读取

Run Monitor 通过 Streaming API 的 `project_persisted_assistant_text_messages` 读取已持久化
公开事实。读取器要求同租户、Run 和受授权 Attempt，分别按 Attempt、incarnation、epoch、
message 分组；每条事实继续经过既有公开 v4 投影，再复用
`reduce_assistant_text_part_message`。只有完成计数、长度与 causation 一致的最终 answer
选择进入管理员 `worker_execution.messages` 与 `response`，work 不成为答案。
组装后仍执行整段脱敏，避免跨 delta 的敏感串泄漏；内部身份不进入浏览器展示字段。

`response` 与消息列表共享一次投影，选择序号最新的非空 answer；最新空消息不会遮盖
已经确认的答案。旧 `message.delta`/带正文的历史 completed、commentary 和只有
`assistant_delta` 的管理员历史保留原有只读兼容语义；出现可见 v4 消息时抑制
`assistant_delta` 镜像，part 与旧 delta 混用的 lifecycle 不走旧答案路径。
管理端的独立最新消息选择器退役。

`answer_projection` 仅说明监控读取事实：available、incomplete、invalid 或 unknown，
附未完成和校验失败的消息数量。缺少完成事实（包括 work-only）不确认最终答案，
非法分类或计数冲突不静默修复；缺少已采集证据时原因未知。它不证明 SDK 没有正文、
聊天持久化成功或截图中某条 Run 的根因，也不改变 Run 状态。私有 SDK/result、工具返回
和生产重放不能作为回答回填来源。历史公开安全预览继续由 Chat owning reader 展示。

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

受影响测试覆盖 completed-block 在后续工具/Result 前 ACK、typed UUID replay、合法多工具、
raw overlap/缺 stop 不控制正文、空工具状态、text/tool 穿插、Result 私有正文不回填、
跨来源脱敏、callback 拒绝、最终 receipt、空答 SDK 成功与真实安全失败分离、
真实 PostgreSQL 分批写入和 Redis replay、front live/gap/terminal hydrate、复制与折叠阶段。
测试须在锁定依赖下重新执行。隔离测试和源码审查不证明真实 provider、代理刷新或浏览器 paint；
部署后的 External Acceptance 仍须按固定 commit/image 验收。

### #1657 typed-only test retirement inventory

以下旧 runner 用例声明第二套 raw/Result 文字权威，精确名称及替代验收如下。
`tests/test_claude_typed_blocks.py` 和 completed-source routing 接管正文验收。权限、callback ACK、
SessionStore、能力回执和晚到工具冲突保持独立。

| Retired case in `tests/test_claude_agent_sdk_runner.py` | Replacement behavior |
|---|---|
| `test_sdk_reconciles_complete_assistant_suffix_once` | completed-block append and exact UUID replay |
| `test_sdk_preserves_visible_delta_when_complete_assistant_body_differs` | raw prose has no public authority |
| `test_sdk_complete_assistant_body_waits_for_terminal_suffix` | completed-block ACK before Result |
| `test_sdk_nonstreaming_result_only_uses_explicit_result_source` | result-only SDK success has no public receipt |
| `test_sdk_nonstreaming_accepts_installed_optional_message_identities` | missing typed identity withholds projection without changing SDK success |
| `test_sdk_nonstreaming_reused_message_id_creates_new_observation_source` | same provider accepts distinct completed block UUIDs; exact replay is suppressed |
| `test_sdk_nonstreaming_tool_only_assistant_uses_result_source` | work-only SDK success has no invented answer |
| `test_sdk_sandbox_tool_only_assistant_rejects_result_without_raw_answer_source` | work-only SDK success has no invented answer |
| `test_sdk_sandbox_tool_only_turn_retires_previous_answer_binding` | eligible main sources preserve all answer parts |
| `test_sdk_sandbox_server_tool_only_turn_retires_previous_answer_binding` | SDK ServerToolUse does not invent a text owner or erase prior answer |
| `test_sdk_sandbox_empty_typed_turn_retires_previous_answer_binding` | empty completed observation does not erase previous answer |
| `test_sdk_sandbox_raw_suffix_after_typed_prefix_is_not_lost` | raw prose has no public authority |
| `test_sdk_sandbox_typed_end_turn_conflicts_with_raw_tool_use_stop` | raw framing cannot poison typed answer |
| `test_sdk_streamed_result_only_uses_explicit_result_source_without_raw_lifecycle` | result-only SDK success has no public receipt |
| `test_sdk_streamed_terminal_only_accepts_missing_optional_result_uuid` | optional Result UUID has no public text authority |
| `test_sdk_waits_to_classify_split_assistant_text_before_later_tool_block` | completed blocks share provider owner and later tool classifies work |
| `test_sdk_tool_narration_terminal_fallback_keeps_only_answer` | Result body never fills answer or removes narration |
| `test_sdk_raw_only_tool_turn_narration_uses_work_trace` | raw prose has no public authority |
| `test_sdk_projects_answer_candidates_before_turn_boundary` | completed-block ACK before subsequent tool and Result |
| `test_sdk_conflicting_result_keeps_terminal_body` | Result body has no public authority |
| `test_sdk_raw_observation_identity_failure_is_validation_unless_upstream_fails` | raw framing cannot poison typed answer; actual SDK errors still fail |
| `test_sdk_result_prefix_comparison_preserves_trailing_space` | completed typed text preserves whitespace without Result comparison |
| `test_sdk_result_replaces_body_for_selected_empty_assistant` | empty SDK success has no fabricated answer |
| `test_sandbox_stream_empty_text_deltas_preserve_success` | raw-only and empty SDK success has no fabricated answer |
| `test_sdk_raw_frame_failure_retains_only_first_fixed_shape` | raw projector retired; historical diagnostics reader remains |
| `test_sdk_overlapping_tool_starts_fail_closed_with_structural_history` | overlap/missing stop raw noise cannot poison typed answer |
| `test_sandbox_publishes_closed_raw_source_before_result_without_terminal_replay` | completed-block ACK before Result |
| `test_sandbox_stream_ignores_server_tool_result_before_public_text` | typed completed text alone is public input |
| `test_sandbox_stream_ignores_complete_tool_use_block_before_safe_text` | legal multi-tool typed observations and text remain independent |
| `test_sandbox_stream_duplicate_stop_preserves_visible_prefix` | raw duplicate stop cannot poison typed answer |
| `test_sandbox_stream_duplicate_raw_observation_is_not_republished` | typed UUID exact replay cannot republish |
| `test_sandbox_stream_failure_discards_pending_private_token_prefix` | sanitizer pending private prefix is never published on SDK error |
| `test_sdk_keeps_visible_prefix_and_terminal_body_after_stream_failure` | raw noise cannot poison typed answer; no Result repair |
| `test_stream_failure_before_publication_recovers_terminal_body` | raw noise cannot poison typed answer; no Result repair |

routing owner 的 `test_raw_only_part_is_acknowledged_before_message_stop` 由完成块 ACK 顺序替代。
空成功合同改为验证 SDK 成功且无公开 receipt；已提供的正文、receipt 和产物仍须通过完整校验。
旧测试夹具不再从 raw stop/Result 正文创建 AssistantMessage，正常与安全用例使用明确完成块。
历史 raw checkpoint/diagnostic readers 与完整原生会话生命周期继续保留。
晚到私有标识与已经 ACK 的文字发生真实泄露时，安全失败独立于投影已关闭状态和首次诊断
标签；关闭的 gate 继续检测已公开标识，不恢复或重发正文。

`tests/test_sandbox_executor_app.py` 的 terminal-only batching 用例改为明确 completed-block
来源；保留超过 100 项事件和 8192 字节分批验收。三种 tool checkpoint 结局（成功、上游错误、
取消）继续逐项核对实际 raw 正文摘要与第 1/128/256/270 个 checkpoint；删除 raw checkpoint
数量必须等于公开 ACK 数量的旧对账假设，公开 part 单独验证完成块来源及错误后的不可公开性。
SDK worker 的正常消息夹具将误设的 child scope 改为主消息；独立子 Agent 私有正文排除用例
保持有效。旧 on_text 回调在终态安全门后交付；live 预览继续通过已 ACK 的公开 part 事实验证。
`test_claude_agent_events.py` 的 tool-only 用例改为 SDK 成功且无公开 receipt/正文，仍完整验证
工具、子 Agent 活动与标识脱敏；普通 receipt 用例修正误设的 child scope，legacy inline
用例明确提供完成块且 Result 为私有不同正文。`test_context_prompt_continuity.py` 的正常
公开消息也改为 main scope，Context 工具权限、作用域、脱敏和关闭后 callback 拒绝断言保留。
