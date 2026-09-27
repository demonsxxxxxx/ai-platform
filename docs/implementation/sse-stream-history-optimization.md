# SSE 流式输出与历史存储优化实施方案

状态：**候选方案**。本文是 `origin/main@f6d3f82348820271e0c496d8d2bb24eb9bc836d2` 的候选实施方案和验收合同，不是已部署状态证明。实现进度、测试结果和 review 结论属于当前任务或 PR；本文只记录稳定目标、原子切片、兼容处置和验收边界。

## 1. 已确认的产品取舍

长期历史**不要求逐个精确复现原始流式 chunk 边界**。长期需要保留的是：

- 完整且经过公开投影的最终回答；
- 仍属于产品历史的公开工作过程、附件、失败和取消事实；
- 足以证明正文完整性、来源身份、顺序范围和授权范围的 receipt/digest；
- Run、Attempt、lease、authorization epoch、stream incarnation 和审计事实。

这项取舍允许新流在生成公共事件 identity 前合并相邻正文增量，也允许未来在完成物化证明、保留窗口和消费者迁移后清理细碎正文 delta payload。它**不**授权：

- 删除或替换 provider-native SessionStore；
- 删除私有 Run diagnostics、审计、授权或 callback receipt；
- 仅凭 Run terminal status 删除正文；
- 在未确定保留期前执行不可逆物理删除；
- 创建第二套 Run、generation、terminal、cursor 或 transport authority。

初始实施只提供清理资格判定和 dry-run，不启用物理删除。保留期和删除开关由后续产品决定明确批准。

## 2. 目标与非目标

### 2.1 目标

1. SDK raw text-block framing、typed Assistant/Tool observations 和 Result lifecycle 对正文与公开工作过程的归属唯一、可验证且 fail closed；已通过 framing 与公开投影的 Assistant 文本保持 `message.delta`，只有显式授权的公开 summary 才能成为 `commentary.delta`。
2. 移除 reconciliation signal 在空 durable scan 与 `XREAD` 建立之间的漏唤醒窗口。
3. 终态立即停止“正在生成”，把后续阶段明确显示为“结果同步”，不把历史同步失败误报成 Run 失败。
4. 普通正文和自然语言标签使用产品 UI 字体；代码内容继续使用等宽字体；工作过程和最终回答有明确层级。
5. 在 v4 event identity、sequence、answer receipt 形成前，有界合并相邻、同身份、已公开投影的正文增量，减少新 `run_events` 行数。
6. 在现有成功终态事务内记录可验证的答案物化证明，为 history fast path 和未来 payload 清理提供条件。
7. 历史 fast path 只在完整性证明匹配时使用；否则继续 exact event hydration。
8. 盘点并迁移所有 delta payload 消费者，提供只读清理资格报告和 dry-run。
9. 保持 Redis Stream v4 唯一 live/replay transport 及现有 Run/Attempt/lease/authorization 边界。

### 2.2 非目标

- 不引入 LibreChat `GenerationJobManager`、conversation-level generation epoch、Redis Pub/Sub 或独立 job hash。
- 不采用 LambChat 的 Redis-first、Mongo-later 异步双写顺序。
- 不改变 SSE v4 schema/event names/cursor semantics，除非某个原子切片先修订本合同并单独审查。
- 不在本候选中配置生产 retention、执行物理删除、部署或访问 s72。
- 不回写历史回答中已经持久化的全角 Skill 名称。
- 不通过语言、标点、文本长度、正则或 prompt 合规推断 SDK 消息类别。

## 3. 当前权威与约束

- Redis Stream 是唯一 live/replay transport；PostgreSQL 保存 durable business/history facts。
- Callback route 在同一事务提交 canonical public `run_events` 和 callback receipt，事务提交后直接 append Redis，收到 exact ack 后才能发送下一批。
- `message.completed` 只携带 `{delta_count, text_length}`，不携带完整正文。
- Worker 使用 Attempt-bound answer receipt 从 durable v4 rows 重建并验证回答，然后保存最终 assistant message。
- 超过 message/result 持久化上限的正文可能只在 `run_events` 中完整存在，因此在建立新的完整物化存储前不得删除相应 payload。
- 历史事件还承载公开工作过程、附件和终态投影；答案完整性证明不等于整段历史完整性证明。
- 当前数据生命周期契约对 `run_events` 的物理 retention 采用 fail-safe disabled；本方案不能绕过它。
- Public projection 排除 hidden reasoning、raw SDK objects、Tool arguments/results、commands、private paths/IDs、storage keys、credentials 和 secrets。

详细规则继续由以下 owner 唯一拥有：

| 关注点 | Owner |
| --- | --- |
| v4 event model | [ADR 0012](../adr/0012-recoverable-agent-kernel-event-stream-v4.md) |
| Redis Stream-only transport | [ADR 0013](../adr/0013-redis-stream-only-sse.md) |
| v4 bytes/cursor/replay | [SSE wire](../architecture/redis-streams-sse-wire-protocol.md) |
| admission/publication/terminal hydration | [SSE execution control](../architecture/redis-streams-sse-execution-control.md) |
| Chat message/public projection | [Chat projection](../architecture/chat-run-lifecycle-and-public-error-projection.md) |
| persistence and deletion | [Data lifecycle](../architecture/single-enterprise-data-lifecycle.md) |
| test evidence | [Local test execution](../agent-rules/local-test-execution.md) |

## 4. 目标链路

```text
SDK raw text-block framing / typed observation / stop reason / parent tool identity
        ↓
adapter source classification（Assistant text → answer；显式授权 summary → commentary；Thinking/Tool raw omit）
        ↓
现有 public projector + cross-chunk sanitizer
        ↓
候选事件 identity 分配前，同一公开 answer source 的相邻正文有界合并
        ↓
v4 event identity / sequence / answer receipt
        ↓
PostgreSQL canonical rows + callback receipt（同事务）
        ↓
Redis Stream append → callback ack → frontend adapter/reducer
        ↓
成功终态事务验证 answer receipt、保存 final message、写物化证明
        ↓
history：证明匹配走 fast path；否则 exact event hydration
        ↓
批准的保留窗口后：仅 eligible delta payload 可进入 dry-run/未来清理
```

## 5. Change Contract

- **Owner:** Execution owns SDK typed-turn classification and terminal answer materialization；Streaming owns canonical event identity、durable callback rows 和 Redis publication；Chat owns history projection、terminal synchronization 和 ordinary-user rendering；Data lifecycle owns retention and physical deletion。
- **Bounded scope:** Claude SDK runner/projector/typed-event adapter and owning tests；executor reconciliation signal；public candidate producer and its owning buffer tests；Worker answer persistence and history projection；Chat terminal synchronization/work activity typography；data-lifecycle cleanup eligibility/dry-run and owning docs/tests。现有 callback buffer 仍负责 v2.1 exact retry、顺序和 transport batching，不在 S4 重新实现。
- **Preserved invariants:** tenant/user/workspace/session/Run/Attempt/lease/authorization/incarnation fences；Redis Stream-only live/replay；PostgreSQL durable facts；strict public projection；terminal Run state independent from transport delivery；exact hydration fail-closed fallback；no hidden reasoning or private executor data in public output；`AssistantAnswerReceipt v1`、`hydrate_required` terminal semantics 和现有 callback receipt identity 不被隐式改写。
- **Acceptance:** section 8 的行为和命令门禁全部通过；fresh-context independent review 对当前 diff 给出无 P0/P1 blocker；任何修复后重跑受影响门禁；最终由主控检查 diff、退休清单和残余风险。
- **Compatibility:** v4 wire 不变；history response shape 不变；旧 Run 和没有物化证明的 Run 继续 event hydration；不回写旧答案；cleanup 初期只 dry-run。
- **Rollback:** 每个原子切片可独立回退；fast path 可关闭并恢复 exact hydration；coalescing 可关闭而不改变已提交事件；在物理删除启用前不存在不可恢复的数据迁移。
- **Stop conditions:** 需要新事件类型、第二 transport/control plane、弱化 public projection、仅凭 terminal status 删除 payload、改变审计/SessionStore 生命周期、或出现未批准的 retention 窗口时停止并请求决策。

## 6. 原子任务与依赖

每个切片只有一个 writer；writer 完成后 fresh reviewer 独立检查，finding 由主控综合后交给单一 fix writer。切片通过后再进入有依赖的下一项。

| ID | 原子交付物 | 主要范围 | 前置 | 完成条件 |
| --- | --- | --- | --- | --- |
| S0 | 当前源/消费者/测试库存 | docs、symbols、callers、current diff | 无 | 固定 base；列出生产消费者、兼容面、测试和不在范围内的既有改动 |
| S1 | SDK typed-turn 归属 | SDK runner/projector、answer gate、tests、owning docs | S0 | 原始 Tool/Thinking 不进 answer；合法 Assistant text 不因 Tool lifecycle 被误分类；同 source 不重复；unknown framing fail closed；Result/Assistant lifecycle 测试通过 |
| S2 | Reconciliation cursor handshake | executor signal/reconciler、tests | S0 | signal 在 scan/XREAD 间到达不会漏；Redis unavailable 与 stop semantics 保留 |
| S3 | Terminal synchronization + UI 层级/字体 | `useAgent` terminal recovery、ChatMessage work activity、pill typography、tests/i18n | S0 | Run terminal 后停止生成态；同步态独立；同步失败不改 Run；工作过程/答案分层；computed font 验收 |
| S4 | 公共正文事件前 coalescing | executor public candidate producer、现有 callback buffer compatibility tests、docs | S1 | 仅同公开 answer source 相邻文本合并；barrier 立即 flush；receipt/sequence/idempotency 正确；event rows 实际减少且 callback transport 不二次拼接 |
| S5 | Answer materialization proof + history fast path | Worker answer persistence、DB schema/repository、history projection、frontend hydration、tests/docs | S1、S4 | 同一终态事务写完整证明；证明匹配 fast path；超长/失败/取消/旧 Run exact fallback；响应 shape 不变 |
| S6 | Cleanup eligibility + dry-run | data lifecycle owner、bounded scanner/status、tests/docs | S5 | 列出 eligible/ineligible 原因；不删除；引用与消费者库存完整；未批准 retention 无法启用清理 |
| S7 | 跨链验收 | backend/Redis/Postgres/frontend/browser | S1-S6 | section 8 全部通过；review findings disposition 完成；最终 diff 无意外表面 |

### 6.1 S1：SDK typed-turn 归属

- 使用 SDK raw `content_block_start/delta/stop`、`message_id`/`uuid`、typed content blocks、`stop_reason`、`parent_tool_use_id` 和真实生命周期；typed `AssistantMessage` 不是 raw framing boundary。
- 已通过 raw framing、stateful sanitizer 和 public projector 的 Assistant `TextBlock` 文本，即使与 Tool lifecycle 交错，也继续进入 `message.delta`；Tool input/result、Thinking 和其他 raw 非文本 block 永不进入 answer。
- `commentary.delta` 只能由显式、授权、已公开投影的 producer summary 产生；不能因为文本出现在 Tool turn、Tool invocation 或 Result observation 中而自动改投 commentary。
- `AssistantMessage` 和 `ResultMessage` 只能补足同一权威正文的缺失 suffix，不能把同一 source 再追加一遍；不能用 Result 文本覆盖已确认的 public delta。
- 不兼容 source identity、raw framing 冲突或 incomplete framing fail closed；不增加 retract 协议。候选事件 identity 分配前的短暂有界等待只属于 S4 coalescer，不能把整轮 turn 缓冲成分类条件，也不能发布未经确认的 raw partial。

### 6.2 S2：Reconciliation cursor handshake

- durable scan 前用 signal Stream tail 初始化进程内 cursor；空 stream 从 `0-0` 开始。
- idle wait 使用显式 cursor `XREAD` 并推进 last ID；不再每轮使用 `"$"`。
- signal 只是 wake-up hint，durable DB receipt 仍是 authority。
- Redis unavailable、trim、cursor 初始化失败时回退 bounded durable polling；stop event 保持可响应。
- 增加 deterministic interleaving test：signal 恰在空 scan 完成和 XREAD 建立前到达。

### 6.3 S3：Terminal synchronization 与视觉呈现

- 接收可信 terminal 后立即结束生成/重连状态；history 尚未完成时进入单独的同步状态。
- exact Run hydration 只替换对应 Run segment；失败显示固定“结果暂未同步”，不把成功 Run 改成失败，不自动 resubmit。
- active reconnect、replay-gap recovery、terminal hydration 和 route replacement 均保留 owner/generation/abort fencing；terminal synchronization 退休 streaming owner 并清除 recovery owner 后，既有 status query 变 stale，新的 reconnect 也不会启动。
- Work activity active 时展开，terminal 后折叠；最终 answer 位于独立正文区域，不泄露 executor transcript。
- natural-language pill 去除不必要 `font-mono`；code/JSON/path 等代码表面保留等宽字体。
- 保留授权 Skill display name 的既有公开 sanitizer 和私有标识保护；本切片不改变 Skill 名称的 full-width/固定公开标签规则，也不回写历史。

### 6.4 S4：公共正文事件前 coalescing

初始参数作为代码常量或既有 owner 配置的一个明确值：最长约 50 ms，同时遵守现有单帧上限和 bounded queue。实现前测量并避免与现有 callback buffer 的 50 ms 窗口形成无收益的双重等待；如果两个窗口重复，优先由 public candidate producer 统一等待，而不是改变 callback buffer 的 exact retry 责任。

S4 的 coalescer 位于 public projector/sanitizer 之后、`ClaudeAgentEventCandidate` 分配 public event identity 之前。它只把同一公开 answer source 的相邻安全正文合成一个候选；它不在 callback worker、Redis publisher 或已提交 `run_events` 上拼接，也不改写已经分配 identity 的候选。raw provider identity 只用于内部 source fencing，不进入 public payload。

仅合并：

- 同一 tenant/Run/Attempt/incarnation、同一公开 `message_id` 和同一 logical answer source；
- 已通过 public projector/sanitizer，并且每个合并后的 `message.delta` 仍不超过 8,192 code points；
- 相邻且没有 raw source boundary、commentary、Tool、execution、artifact、policy 或 terminal barrier 的 `message.delta`。

以下事件立即 flush：source/message identity 变化、raw framing boundary 无法证明连续、非正文事件、`message.completed`、失败、取消、terminal、close 和 exact receipt barrier。`message.started` 必须先提交，`message.completed` 必须以最后一个已 flush 的 delta identity 作为 causation；coalescer 不得把 callback receipt、callback index 或 transport Redis ID 当成可重写的 source identity。

合并后才分配 public event identity、更新 `delta_count`/`text_length` 并生成该候选的 source receipt。现有 callback buffer 继续一候选一 callback item、负责顺序、exact retry 和 canonical bytes；它仍可把多个 callback item 作为 transport batch 发送，但不得再次连接正文。已经分配 identity、已入队 callback 或已提交的事件不可重写。重试必须发送相同 canonical bytes。

### 6.5 S5：答案物化证明与 fast path

不增加第二 terminal authority。证明由 Worker 在现有成功终态事务中、完成 answer receipt 验证并保存 final assistant message 时写入。优先将证明放在 owning message metadata；只有查询/约束证明确实需要时才增加独立表。证明是消息的完整性 receipt，不是新的 Run、Attempt、cursor、generation 或 terminal 状态。

证明中的 `stream_answer_digest` 必须在 owner contract/test 中固定为以下稳定算法：`SHA-256(UTF-8("ai-platform.answer-body.v1") || 0x00 || 对每个按序 delta 追加 8-byte big-endian UTF-8 byte length || delta UTF-8 bytes)`，其中 `0x00` 是单个 NUL byte。它只能输入已接受、已公开投影的 delta 文本，不能取 raw SDK、Redis entry bytes、private payload 或未验证的 final text。由于现有 materialization 可能在保存 assistant message 前执行公开的消息清理，证明还必须记录 `persisted_body_digest`（对实际保存的正文使用同一长度分隔算法）和 `body_projection_version`；两个 digest 不得互相替代，差异必须按明确的公开清理政策处理。两个 digest 与 proof version 只作为物化证明 metadata，不加入 `AssistantAnswerReceipt v1`、v4 public event、SSE cursor 或现有 history response shape；若未来要改变 receipt 字段，必须另行修订 schema/receipt contract。

最小证明职责：

- proof/schema/projection version；
- tenant/Run/Attempt/incarnation/message identity；
- source receipt 的 `last_delta_event_id`、`delta_count`、`text_length`；
- 上述 canonical `stream_answer_digest`；
- 实际保存 assistant body 的 `persisted_body_digest` 和 `body_projection_version`；若两者不同，必须有明确的公开清理政策，不能宣称 source/body 字节等价；
- persisted message body 是否完整，而不是 `_ANSWER_BODY_REFERENCE`；
- artifact links/parts 是否独立投影，避免把 stream digest 与展示正文 digest 混用。

Fast path 只覆盖能够证明等价的成功 Run：先读取并校验当前 Run/Attempt/incarnation、terminal authority 和 proof，再确认 `persisted_body_digest` 对应的实际 final message body；只有 source/body 差异符合已批准的公开清理政策时，才从 final message 读取 answer，从 canonical public events/authorities 读取工作过程、附件和 terminal presentation。`run.succeeded`/`run.cancelled`/`run.failed` 的 `hydrate_required` 语义、同一 Run segment reconciliation 和 exact non-answer event hydration 不被 fast path 绕过；proof 不合成 Redis cursor，也不改变 SSE reconnect。以下情况继续 exact hydration：证明缺失/不匹配、digest 关系未证明、超长正文引用、失败/取消、交错或多 source 答案、旧 Run、任何读取不确定性。Fast path 只优化 terminal/history durable read，不成为第二终态或 transport authority。

### 6.6 S6：清理资格和 dry-run

Cleanup eligibility 至少要求：

- Run terminal 且 Attempt/incarnation 与物化证明一致；
- final message 保存完整正文；
- receipt、两个 digest、projection version、count/length 验证通过；
- callback retry、executor reconciliation 和 active history owner 不再依赖 payload；
- 所有普通用户、admin Run events、审计/导出、分享和兼容消费者有明确处置；
- 超过批准保留窗口；
- 不属于失败/取消 partial、超长引用、旧格式或不确定历史。

本阶段只提供 bounded dry-run/status：eligible count、ineligible reason counts、oldest/newest age 和 estimated bytes；指标/日志不包含正文、用户 ID、Run ID 或私有标识。没有批准的 retention 配置时，任何 mutation path 必须不可达并 fail closed。

未来物理清理单独修改 Data lifecycle contract、startup settings 和 reference-safe cleaner。不能把 payload 改成会被 strict projector 解释为非法 v4 event 的空对象；需要先定义清理后的合法读取表示和审计证明。

## 7. 退休与兼容处置

每个切片在 review 前完成以下库存：

- superseded production path；
- callers 和 compatibility consumers；
- tests/selectors；
- docs/config/settings；
- 保留面、owner 和退出证明；
- post-change absence/inventory command。

### 7.1 S1 已完成库存（SDK typed/raw projection）

S1 的替换范围仅限 Claude SDK typed/raw 正文归属与终态补全；不修改 SSE v4 wire、callback receipt、Redis transport、Run/Attempt/lease/incarnation authority。

- **Superseded production behavior**：删除 fragment-local/self-generated `assistant_N` source、identity-less typed answer fallback、跨 source aggregate-prefix reconciliation、Result 创建 answer source、无 `message_start` 的 raw block 发布，以及把 Tool/Thinking 原文自动解释为公开正文的路径。`AssistantAnswerTimeline` 现在只接受已绑定 provider message/source/parent 的 raw/typed/Result 观察；非流式或完全未观察 raw lifecycle 的 terminal-only Result 是唯一显式 Result-only compatibility。
- **Updated callers/consumers**：`run_claude_agent_sdk()`、Sandbox executor callback bridge、Claude worker adapter、authorized Skill catalog、context continuity、SDK turn diagnostics 和 direct Claude event fixtures 均迁移到 distinct provider message ID、Assistant/raw/Result observation UUID、parent identity 与 stop-reason contract。普通用户正文仍只经现有 `message.delta` candidate、callback、durable v4 row 和 Redis Stream发布。
- **Tests/selectors**：owning projector、runner、direct events、worker adapter、Sandbox executor、authorized catalog、context continuity、turn diagnostics、SSE cutover、runtime callback 和 answer receipt selectors已覆盖；被修改或删除的断言仅针对 identity-less、unframed、Result-source invention 与 duplicate replay 等已退休错误行为。最后一次本地结果包括 projector `72 passed`、runner `199 passed`、installed SDK contract `1 passed`、Sandbox executor `190 passed`、turn diagnostics `20 passed`；Windows symlink权限和缺失 Redis URL仍是 evidence ceiling。
- **Docs/config/settings**：`streaming-message-parts-design.md` 已改为 strict framed-source contract；没有 v4 schema、generated contract、runtime setting、retention 或 deployment配置变化。
- **Retained compatibility**：PublicAnswerStreamGate 的跨 chunk sanitizer、terminal-only callback chunking、typed-only/no-raw compatibility、exact terminal hydration、v4 replay/gap、legacy/history `assistant_delta` readers与cutover guards继续保留。`assistant_delta` 当前 owner/inventory位于 `app/control_plane_contracts.py`、`app/execution/application/worker_failure_diagnostics.py`、`app/routes/lambchat_compat.py`、`app/run_projection.py`、`app/runs/application/admin_run_monitor.py`、`app/runtime/event_bridge.py`、`app/runtime/kernel_contracts.py`、`app/runtime/sandbox/event_normalizer.py`、`app/runtime/sandbox/executor_app.py`、`app/streaming/authority.py` 和 `app/worker.py`；S1 不新增第二 producer，退出证明仍由现有 v4 cutover contract/tests拥有。
- **Post-change absence proof**：
  - `rg -n 'assistant_[0-9]|result_identities|source_identity = \(\s*"typed"|_resolve_source|_new_source|legacy_counter|self\._streamed|self\._messages|provider_message_identity\([^)]*,|message_id.*==.*uuid|uuid.*==.*message_id' app/executors/claude_stream_projection.py app/executors/claude_agent_sdk_runner.py`：退出码 `1`，零匹配。
  - `rg -n 'ClaudeStreamTurn|queue_stream_turn|assistant_final|stream_projector\.accept\(raw_stream_event\)|answer_timeline\.accept_delta\(text\)' app tests`：退出码 `1`，零匹配。
  - `rg -l 'assistant_delta' app | sort`：仅返回上列 11 个明确保留的 compatibility/history/guard owners。

### 7.2 S2 已完成库存（reconciliation cursor handshake）

- **Superseded production behavior**：删除 reconciler 每次空 durable scan 后从 Redis `XREAD("$")` 开始等待的语义。进程现在先通过 `XREVRANGE count=1` 捕获 signal stream tail（空 stream 为 `0-0`），随后 durable scan，idle wait只接受显式进程内 cursor并在收到 entry 后推进。
- **Authority/compatibility**：signal仍只是无 tenant/run payload 的全局 wake-up hint；PostgreSQL terminal receipt与现有 claim fencing仍是唯一 reconciliation authority。retained publisher caller `app/routes/runtime_callbacks.py`、signal key、`MAXLEN 1024`、Run/Attempt/lease/incarnation、Redis Stream v4 transport和30秒 bounded durable polling保持不变。
- **Failure/stop disposition**：cursor初始化有5秒硬上限；初始化、malformed/sentinel response、Redis read或client close失败均放弃当前 cursor，跳过任何 uncursored XREAD，走 stop-aware bounded polling，并在下一轮 durable scan前重新初始化；worker shutdown原有 task cancellation与 stop event路径保留。
- **Tests/selectors**：`tests/test_executor_signals.py` 覆盖tail/empty初始化、数字stream ID校验、显式cursor读取/推进、Redis与close失败；`tests/test_executor_reconciler.py` 通过真实 signal cursor实现重现 signal恰在空 DB scan期间写入，并覆盖初始化超时及cursor不可用时不调用uncursored XREAD。owning stage结果为 `83 passed`。
- **Docs/config/settings**：无 schema、wire、Redis key、runtime setting、deployment或retention变化。
- **Post-change absence proof**：`rg -n '\{_EXECUTOR_RECONCILIATION_SIGNAL_KEY: "\$"\}|after_id: str = "\$"|stream_cursor.*"\$"' app/runtime/sandbox/executor_signals.py app/executor_reconciler.py` 退出码 `1`，零匹配；生产调用库存只包含 `app/executor_reconciler.py` 的一个显式 `cursor=signal_cursor` 调用。

### 7.3 S3 已完成库存（terminal synchronization 与视觉呈现）

- **Superseded production behavior**：可信 terminal 到达后不再继续显示 `isStreaming`/“生成中”直到 exact history 返回，也不再把 terminal history I/O 伪装成 transport reconnect。`isSynchronizing` 现在是仅属于前端消息投影的短暂状态；完成或固定 unavailable 结果都会清除它。共享 `CollapsiblePill` 不再对自然语言标签强制 `font-mono`。
- **Authority/compatibility**：Run terminal、terminal event receipt、business sequence、Redis cursor/incarnation 与 stream generation 的既有 fence 保持不变；exact Run history 仍在 terminal receipt 提交前完成。同步期间保留当前 Run owner 以完成 terminal settle，但清除可见生成、loading 和 reconnect presentation；退休的 streaming message owner 使普通 reconnect 的在途 status query 与已开始的 SSE continuation 变 stale，清除的 replay recovery owner 同样阻止 gap recovery continuation 及其 active history hydrate 写入，同一 terminal hydration owner 也拒绝新 reconnect；route/auth/new-generation replacement 继续 abort 并使旧 continuation 无权写入。若 settle 时 incarnation 已变化，候选 terminal 不被接受，当前 Run 回到可恢复状态。
- **UI hierarchy**：work activity active 时默认展开，terminal 同步开始即因 `isStreaming=false` 折叠；公开 answer text 以 `data-message-answer-content` 独立于 `data-message-work-activity` disclosure 渲染。commentary、artifact、actionable status 和 authorized Skill display sanitizer 的现有边界不变；code/JSON/path renderers 的 `font-mono` 保留。
- **Failure disposition**：history 失败只追加既有 `terminal_result_unavailable` 固定 warning，保留已公开正文与原 terminal outcome，不改成 `run_failed`、不 resubmit。同步期间 composer fail closed，避免在旧 Run terminal receipt 尚未 settle 时替换其 owner。
- **Tests/selectors**：`test:sse` 当前结果为核心/adapter/component `283 passed`，routed lifecycle selector `14 passed`；新增 pending sync、同步期间拒绝新 reconnect、在途 SSE/reconnect/replay-gap recovery/active hydrate owner 退休、deferred terminal cursor acceptance、success replacement、success+history-failure、work/answer hierarchy、non-interactive sync status 和 natural-language pill typography regressions。`tsc -b --pretty false` 与 focused ESLint 通过。Vite/Tailwind browser computed-style probe 确认自然语言为 `Inter, system-ui, Avenir, Helvetica, Arial, sans-serif`，保留的代码表面为 `ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace`；两份 fresh independent `codex/gpt-5.6-sol` review 均无 P0/P1/P2 finding，并以 `Merge verdict: OK` 结束。
- **Docs/config/settings**：无 v4 schema、generated contract、history response、backend runtime setting、deployment或retention变化；仅新增前端内部 `Message.isSynchronizing` 与中文显示文案。
- **Post-change absence/inventory proof**：`rg -n 'font-mono min-w-0 truncate overflow-hidden' frontend/web/src/components/common/CollapsiblePill.tsx` 退出码 `1`、零匹配；non-interactive terminal sync 仅通过显式 `CollapsiblePill` 选项使用 status element，既有可展开/工具 pill 的 button contract 保持；`rg -n 'isSynchronizing|synchronizingResult|data-message-(work-activity|answer-content)' frontend/web/src` 只返回内部消息状态、Chat owner/rendering、i18n与对应 tests。

### 7.4 S4 已完成库存（public answer coalescing）

- **Superseded production behavior**：已通过 framing 和 stateful public sanitizer 的每个细碎正文片段不再立即分配 `message.delta` identity；重复的 executor callback 50 ms 等待窗已移除。唯一约 50 ms coalescing 窗口现在由 `PublicAnswerCoalescer` 在 candidate producer 侧拥有，发生在 public sanitizer 之后、`ClaudeSdkAgentEventAdapter.accept_answer_text()` 之前。
- **Boundary/authority**：coalescer 实例只属于一个 runner invocation，因此 tenant/Run/Attempt/incarnation 与公开 answer message owner 固定；只在 exact logical source identity 相同时合并相邻正文。raw/typed source 或 message boundary、Assistant turn、Task/Tool/policy/commentary/artifact/execution candidate、Result、失败、取消、timeout、close 与 terminal receipt barrier 都先 flush。单个 delta 上限为 8,192 Unicode code points；`message.started` 仍由 adapter 在首个 delta 前生成，`message.completed.causation_event_id` 仍引用最后一个已接受 delta。
- **Callback/compatibility**：`_MessageDeltaCallbackBuffer` 仍按原队列串行化、保持每个已分配 candidate 的 event identity/canonical bytes、backpressure、exact retry、顺序与 transport batching；它只把 callback items 放进同一 transport batch，不连接或改写 `payload.delta`。Redis Stream、durable v4 rows、callback/answer receipt schema、Run/Attempt/lease/incarnation authority、history response 与 frontend reducer 均未修改。
- **Failure disposition**：callback rejection 立即 seal adapter 和 coalescer；取消或 timeout 先取消在途 consumer/callback，再 flush 尚未开始 identity allocation 的 pending safe text。已经进入 emit 的文本可能已分配 immutable candidate identity，因此取消不会重发；coalescer 明确返回未确认而不是误报 flush 成功。timer callback 异常在下一 owner barrier 重抛，但 failure/cancel/timeout cleanup 不会让该异常覆盖原有终态结论。已经分配 identity、已入队或已提交的 candidate 不由 coalescer 重试或改写。
- **Tests/selectors**：coalescer owning tests `72 passed`；Claude event integration `49 passed`；完整 SDK runner `199 passed`，terminal-precedence review-fix 后三组 fresh combined stage 为 `320 passed`；Sandbox executor `190 passed`；worker adapter/runtime callback/answer receipt chain `206 passed, 4 skipped`（Windows `WinError 1314` symlink 权限）；architecture governance `277 passed`。覆盖同 source 合并、source barrier、timer/close 路径、timer callback 异常重抛、8,192 多字节 code-point 上限、callback rejection seal、fragmented raw answer、completion causation、receipt count/length/last identity、Tool boundary、取消/timeout、immutable retry 与 callback ordering；review-fix selectors另覆盖 timer emit 在途取消返回未确认、timer `on_text` 异常仍返回 structured runner error，以及该异常不覆盖已知 cancelled Result。最终两份 fresh independent `codex/gpt-5.6-sol` review 均无 P0/P1/P2 finding，并精确以 `Merge verdict: OK` 结束。
- **Docs/config/settings**：无 v4 schema、generated contract、receipt schema、Redis key、history shape、runtime setting、deployment或retention变化；coalescing 使用 owner 内代码常量，executor callback buffer 不再增加第二个固定等待窗。
- **Post-change inventory**：生产 `accept_answer_text()` caller 仅为 runner 中 coalescer emit callback；callback buffer 中保留的 `_merge_message_delta_callbacks()` 只合并事件列表且不修改 event/payload；没有 callback worker、Redis publisher、`run_events` 或 frontend coalescing 路径。
  - `rg -n "PublicAnswerCoalescer|_PUBLIC_ANSWER_COALESCE_SECONDS" app frontend`：仅返回 `app/executors/public_answer_stream.py` 的 owner 定义和 `app/executors/claude_agent_sdk_runner.py` 的 producer 接线。
  - `rg -n "accept_answer_text\\(" app`：仅返回 adapter 定义和 runner coalescer emit callback 的一个生产调用。
  - `rg -n "PublicAnswerCoalescer|_PUBLIC_ANSWER_COALESCE_SECONDS" app/runtime app/streaming app/run_event_repository.py frontend`：退出码 `1`，零匹配，证明 callback worker、Redis/durable publication 与 frontend 没有第二 coalescer。

### 7.5 S5 已完成库存（answer materialization proof 与 safe history fast path）

- **Superseded production behavior**：成功 Run 不再把 answer receipt 仅当作一次正文重建输入；Worker 在同一成功终态事务中验证 Attempt-bound v4 rows、保存 assistant message 后写入 message metadata proof。旧消息、缺 proof、超长 reference body、失败/取消、交错 source 或任意 mismatch 继续走 exact history hydration。
- **Proof/authority**：proof schema `ai-platform.answer-materialization-proof.v1` 固定 tenant/Run/Attempt/incarnation/authorization epoch/message、receipt last delta source ID、durable last delta row ID/count/length、last sequence/time、`answer_source_count`、interleaving、canonical stream/body digest、body projection version/policy、complete-body flag 和 artifact projection。stream digest 使用 `SHA-256(UTF-8("ai-platform.answer-body.v1") || 0x00 || 8-byte big-endian UTF-8 byte length || delta UTF-8 bytes)`；body digest 使用同一 length-delimited 算法对实际保存正文。proof 同时复制到成功终态 `result_json`，history fast path 要求 message metadata proof 与 terminal proof 完全一致；proof 不进入 AssistantAnswerReceipt v1、v4 event、cursor 或 history response。
- **Fast path**：`/api/sessions/{session_id}/events?compact_message_chunks=true` 先验证当前 terminal StreamAuthority、唯一 assistant message、proof set/schema/version、receipt/digest/body/source/interleaving/artifact facts；通过后 SQL 排除 `message.delta` rows，使用 durable 最后 delta row identity（保留 source receipt separately）和 sequence 生成现有 `message:chunk` shape，其余 work/terminal/artifact events 仍按原 public projector 处理。任一可选 proof read、authority read 或 proof validation 异常都回退 exact event history，不影响用户历史可读性。
- **Compatibility/retirement**：history response shape、frontend schema、Run/Attempt/incarnation authority、Redis transport 和 durable rows 保持不变；`answer_body_source=run_events_v4` reference compatibility 保留但不具备 fast-path proof eligibility。没有第二 terminal/cursor authority，也没有 payload deletion 或 retention mutation。
- **Tests/selectors**：answer receipt/proof stage `22 passed`；worker adapter/Sandbox/runtime callback chain `376 passed, 4 skipped`；LambChat compatibility stage `86 passed`，另有一个 locked-base frontend terminal-catalog mismatch（缺 `tool_execution_outcome_unconfirmed`）与 S5 无关；新增 proof 校验覆盖 complete body、single source、authority/incarnation、terminal-proof tamper/reference fallback 条件。
- **Post-change inventory**：`rg -n "answer_materialization_proof|verify_answer_materialization|excluded_event_types=\(\"message.delta\"\)" app` 仅返回 execution proof owner、history fast-path owner 与对应 tests；`rg -n "DELETE|UPDATE.*payload|retention" app/execution/application/worker_answer_persistence.py app/routes/lambchat_compat.py` 不返回 mutation path。

### 7.6 后续切片库存

预期退休候选：

- cleanup dry-run 中仍未 proof-backed 的 retained delta payload eligibility scan。

保留：

- exact event hydration fallback；
- Redis Stream v4 replay/gap；
- strict projector/reducer、cursor/incarnation fencing；
- callback receipt 和 durable public rows；
- 超长/失败/取消/旧历史事件；
- private SessionStore、diagnostics、audit、authorization 和 artifact facts。

## 8. 验收合同

### 8.1 后端行为

- 原始 Thinking/Tool blocks 不进入 answer；已通过 framing 与 public gate 的 Assistant text 即使与 Tool lifecycle 交错仍进入 `message.delta`；只有显式授权的 producer summary 才能进入 commentary。
- partial + Assistant + Result 不重复；typed observation 不是 raw framing boundary；unknown identity/stop reason 或 incomplete framing fail closed。
- coalescing 前后最终 Unicode 文本逐字符相同；只能在候选 identity 分配前合并同一公开 answer source；单帧 bound、顺序、causation 和 answer receipt 一致；现有 callback buffer 不二次拼接正文。
- callback retry、Redis failure 和 duplicate batch 不增加、丢失或重排正文。
- terminal publication 不能越过 pending body callback。
- signal scan/wait interleaving 在一次 idle cycle 内唤醒；Redis unavailable 仍由 DB scan 收敛。
- 物化证明与 message、receipt、Attempt/incarnation 精确绑定；canonical stream/stored-body digests 使用 proof/body projection version，差异未经政策证明时走 fallback，且不写入 v4 receipt/schema；tamper/mismatch 走 fallback 或 fail closed。
- cleanup dry-run 不执行 DELETE/UPDATE payload，未批准 retention 无 mutation 入口。

### 8.2 前端行为

- Live、reconnect、history 和 terminal hydration 收敛到同一 ChatMessage Run segment。
- terminal 到达后生成状态结束；同步状态单独呈现；同步失败保留已接受正文并不改变 Run status。
- route/session replacement 取消旧 owner 的 reconnect/hydration；旧 callback 不污染新会话。
- 工作过程与最终答案层级清楚；active 展开、terminal 折叠；answer/artifacts/actionable errors 不进入折叠区。
- ordinary prose 和 Skill natural-language label 的 computed font 不是 monospace；code surface 保持 monospace。
- 既有 Skill sanitizer、内部/未授权 Skill 的固定公开标签和历史不回写规则保持不变。
- 文本没有重叠、截断异常或移动端溢出。

### 8.3 观测与性能

记录有界阶段时间：

- SDK safe text ready → callback queued/flush → PostgreSQL commit → Redis append/ack；
- browser receive → adapter/reducer accept；
- SDK terminal → terminal callback durable → reconciler claim → Run terminal commit；
- terminal frame/status observed → history synchronization complete。

跨进程时钟未校准时分别报告阶段时长，不直接相减。比较同环境、同 workload 的候选与 locked base：public answer event rows、callback requests、database bytes/latency、Redis entries、history query rows/bytes 和浏览器 terminal convergence。目标是证明减少而非预设百分比。

### 8.4 检查入口

所有 Python tests 使用 `python tools/run_test_stage.py` 和显式 selectors，工作区本地 basetemp/JUnit 由 runner 管理。按受影响范围至少包括：

- SDK runner/projector/public answer tests；
- sandbox callback delivery、runtime callback、answer receipt tests；
- executor signal/reconciler tests；
- LambChat history compatibility and v4 PostgreSQL/Redis integration；
- frontend event processor、SSE connection、history loader、terminal recovery 和 ChatMessage renderer tests；
- ESLint、TypeScript build、schema/generated contract checks；
- `git diff --check`、Python compile/Ruff；
- Playwright desktop/mobile：live stream、终态同步、离页返回、字体 computed style、无重叠；
- 可用真实 Redis/PostgreSQL integration 必须 `--require-zero-skips`；缺失依赖是 evidence ceiling，不是通过。

## 9. 多 Agent 执行协议

- 一个共享候选 worktree：`C:/w/sse-stream-history-optimization`。
- 同一时刻最多一个 writer；所有 reviewer/validator fresh-context、只读。
- 每个 writer 的输入包含本方案、固定 base、允许文件范围、非目标、验收命令和 stop conditions。
- 每个 writer 返回 changed files、实现/未实现、命令及退出码、测试结果、残余风险和需要主控决定的问题。
- 每个原子切片后至少有 correctness/authority、tests/recovery、simplicity/retirement 三个 review 角度；UI 切片再加 user-flow/accessibility，存储切片再加 data lifecycle/privacy。
- Reviewer 只报告有源码/测试/契约证据的 P0/P1/P2 finding，并给出 `BLOCK` 或 `OK` verdict。
- 主控综合 finding 后只派一个 fix writer；修复影响非平凡时再次 fresh review，最多三轮。
- 未批准的 schema/wire/retention/删除/部署/merge 决策必须停止并上报，子 Agent 不自行决定。
- 子 Agent 不提交、推送、创建 PR、合并、部署或访问 s72。

## 10. 当前未决决策

物理删除前仍需用户确认：

1. 完整物化后正文 delta payload 的保留期（候选：30 天；也可永久保留）。
2. 首次物理清理是否只覆盖成功且 final message 完整的 Run；本文推荐“是”。
3. Admin Run events/审计导出是否需要保留逐 delta 内容，还是仅保留 identity/receipt/digest 和合并后的公开正文；长期无需逐 chunk 重放的产品选择倾向后者，但仍需确认管理员合规要求。

在这些问题确认前，S6 只能完成 dry-run 和消费者迁移，不能执行物理删除。
