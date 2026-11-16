# Agent 流式消息与最终交付架构

状态：当前 Claude 原生增量与公开正文合同。普通助手文字优先承接 Claude Code CLI 的自然流式体验；原 work/answer 展示分类已退役。外层 SSE v4、授权、重放和 Run 终态继续由 [wire contract](../architecture/redis-streams-sse-wire-protocol.md)、[execution control](../architecture/redis-streams-sse-execution-control.md) 和 [ADR 0013](../adr/0013-redis-stream-only-sse.md) 负责。

## 产品合同

普通、安全的主助手文字按事件到达顺序逐 token 显示，不做 work/answer 语义分类。展示分为上方单一“执行过程”和下方结果正文：当后续实际工具或子任务事件到达时，其前方的说明文字归入执行过程；最后一次操作之后的文字继续流式显示在下方。此处的位置变化由实际操作边界触发，用户已选择保留即时流式而非等待一轮结束。统一过程折叠保留手动开合，后续文字、工具或终态不重置。

| 来源 | 展示与持久化 |
| --- | --- |
| 主会话 `StreamEvent` 的 `text_delta` | 经同一脱敏 gate 后立即生成 `message.delta`，追加正文 |
| SDK 完成 `AssistantMessage.TextBlock` | 校对同来源文字，避免重复；该来源没有文字增量时使用完成块 |
| Thinking、工具输入/原始结果、子 Agent 正文 | 不作为主助手公开文字；工具身份仍登记用于脱敏 |
| 工具、Skill、MCP、子任务活动 | 获准名称、状态、进度和耗时，独立于正文 |
| `ResultMessage` | 结束、错误、取消及用量事实，不回填正文 |
| 显式 `attach_file` | 平台验证、存储后的授权附件卡片 |

公开文字、工具完成、Run 成功和文件可下载是独立事实。失败或取消保留已公开安全文字，同时展示真实终态，不伪造成功 receipt。普通聊天不要求结构化输出、使用工具或交付文件。

## SDK 原生增量与去重

Claude adapter 使用锁定的 `claude-agent-sdk`，设置 `include_partial_messages=True`。SDK 底层 Claude Code CLI 的增量通过 `StreamEvent.event` 到达：`message_start` 绑定 provider 消息，`content_block_start/index` 识别文字块，`content_block_delta/text_delta` 追加文字。非文字事件不转换成正文。

一个完整非空内容块仍会产生 `AssistantMessage`；同一模型消息可有多个完成块及不同 UUID。adapter 用 native frame UUID 和消息/块归属去重，完成块只校对已收到的文字，不再重新追加。完成块先于对应 raw stop、或来源没有输出文字增量时，同样保持一次公开。相同文字出现在不同合法块不是重复。

消息/观察身份、冲突检测和摘要只保留在 Attempt 内，不进入公开 payload。`message.delta` 的事件身份包含 Run 与 Attempt，防止重试 Attempt 的首个 delta 与先前 Attempt 碰撞。缺少必要来源或冲突观察停止该公开投影，不新增身份猜测、Result 回填或自动重新执行工具。

不同 provider 消息之间只保留既有两个 LF 的段落边界；同消息块内保留原始文字与空白，不根据内容重新分组。前端只合并相邻文字增量，工具之间的文字保持独立顺序。

## 单一公开链与安全边界

每个新增后缀在任何公开事件 callback 前经过 `PublicAnswerStreamGate`。敏感值注册、跨 token 脱敏、未决短后缀与来源跨度保持；安全尾部在正常结束时释放。已知身份替换不会把工具原始数据变成正文。晚到私有身份若已泄露仍是独立安全失败。

逐 token 指接入真实模型增量，不承诺每个 token 单独形成浏览器帧；安全未决后缀和现有传输批处理仍可合并。单个公开 delta 最多 8192 code points，callback 最多 100 个事件，队列背压与 8 KiB 文本批次继续由现有 executor buffer 管理。缺 ACK 停止后续发布，工具和终态仍是 durable receipt barrier。

只有一条 `SDK → 公开事件 → callback → PostgreSQL/Redis → SSE → useAgent → useExternalStoreRuntime` 链。`on_text` 为具名 legacy direct callers 保留终态回调；它不承担 token-time 展示。Sandbox 接受 v4 文字后抑制重复 `assistant_delta`。

鉴权、租户/用户/Session/Run/Attempt/lease、权限准入、effectful outcome unknown 保护、SessionStore EOF/final_sequence、文件验证和持久化不变。工具失败不再按内容分类隐藏普通文字，也不能凭普通文字宣称工具执行成功。

私有 `ModelTextCheckpoint` 只观察实际 wire，记录有界计数和摘要，不包含正文；公开 delta 数量与私有 checkpoint 数量无须相等。

## 完成与 receipt

新 Claude producer 复用已有 `message.started → message.delta* → message.completed`，不新增 wire/schema/settings。`message.completed` 只含 `delta_count/text_length`，causation 指向最后一个 delta。`ai-platform.assistant-answer-receipt.v1` 对全部已公开有序文字结算，不再选择 work/answer。

Worker 用当前授权 Attempt 的已持久化事实重建正文，核对数量、长度、最后 source event、消息归属与完成顺序，不信任 executor inline message 或 Result。成功流式 Sandbox inline `message` 仍为空。

管理端通过 Streaming 的同一授权 message ledger 投影读取新 delta 与旧 part 消息，按 Attempt/incarnation/epoch/message 分组；只将完整验证的正文作为 completed response。失败/取消的公开前缀由 Chat owning reader 保留。`answer_projection` 的 available/incomplete/invalid/unknown 仅说明持久化覆盖，不改变 Run 状态。

## 前端、复制与历史

所有合法公开 text 都是普通正文，生成中及同步中可复制当前已收到的内容。工具原始输入/结果和私有状态仍不进入复制。目录和任务通知使用同一公开文字选择，不依赖 `pending/answer/work`。

`parts`、持久化和 receipt 仍保持事件顺序，显示层只按实际工具/子任务边界分区，不改写 text role 或消息。一个 message 只有一个稳定 keyed 的“执行过程”折叠，包含所有显式活动及最后一个工具/子任务之前的说明文字；过程内部按原事件顺序渲染。最后一个操作之后的正文及授权附件留在下方，没有操作的普通回答不生成空过程。新 live 过程默认展开，历史默认收起，后续更新不自动重设手动状态。routine execution phase 的既有隐藏规则保留，不把原始工具数据变成可见说明。

Live、replay、gap 和 terminal hydrate 使用同一条消息链。旧 `message.part.delta/classified` 继续校验 owner/schema/游标和闭合状态，但 role 仅是历史兼容 annotation，不再移动、隐藏文字或排除复制。历史内容与新后缀保持同 Run，不能覆盖下一轮。

终态精确历史 hydrate 与刷新历史均从同一 Run 的服务端 `run_started.timestamp` 到终态 `done.timestamp`（Run `finished_at`）计算 `Message.runDurationMs`。成功、失败、取消均可保留耗时；缺少有效起止或时间倒置时保持未知，不用浏览器时钟、工具时间相加、局部 `execution_process.elapsed_ms` 或 SDK `Message.duration` 替代。已结束任务在总“执行过程”标题旁常显耗时，收起过程仍可读；无过程的普通回答在正文上方显示，不创建空折叠。运行中不展示最终耗时。任务耗时以完整秒向下取整，使用紧凑 `55s`、`50m55s`、`1h13m5s` 格式；分、小时进位，小时出现后保留分秒（如 `1h0m0s`），不再显示小数或毫秒。

## 文件交付

`attach_file` 继续验证工作区、Skill 边界、数量和预算后记录有序 descriptor。只有显式清单中的文件进入 Artifact 存储和授权下载；不从正文或工作目录扫描推断交付物。文字流与文件清单独立。

## 退役和兼容处置

| 旧表面 | 处置与 owner |
| --- | --- |
| `include_partial_messages=False`、忽略所有 StreamEvent 正文 | 删除，原生文字增量进入现有 gate |
| `AssistantTextSourceBuffer` 角色/retire/select 状态 | 删除，现 owner 只核对 native delta 与完成块、精确 replay |
| `ClaudeSdkAgentEventAdapter.accept_part_text/classify_text_part` 及 part receipt 生成 | 删除，新 producer 仅使用既有 ordered delta/receipt v1 |
| 正文 pending/work/answer 显示、复制和恢复门控 | 删除，旧 role 字段仅供兼容读取 |
| 正文驱动自动开合、result-first 移位、连续成功工具二次分组及每操作独立折叠 | 删除，使用单一 message 级过程折叠，实际工具边界归档说明，用户开合状态稳定 |
| 既有 part schema、闭合事件 decoder、PostgreSQL part ledger/index、receipt v2 reader | Streaming/Worker/Chat 只读历史兼容；数据库已存在这些事实，不改写、不删除；受支持历史清除或显式迁移证明前不移除 |
| 非 Claude ordered delta producer、receipt v1 和旧 commentary/worktrace 历史 | 保留原 owner 和语义；旧 worktrace 仍是显式摘要活动，不混入新正文 |
| `assistant_delta` 公开历史 fallback、`assistant_final` | 继续退役，不恢复 |

本次不增加依赖、配置开关、语义分类器、备用身份或 Result 自动补答系统。更新 backend/frontend 后才获得一致展示；部署和正式发布仍按 release runbook。回退 producer 时保留能读取 part/v2 历史的 readers 与 schema/index readiness，不回退到不兼容旧镜像。

## 验收

Owning 回归覆盖：增量在完成块/Result 前可见、frame/typed replay、text/tool 交错、相同文字不同块、完成块校对、主/子 scope、跨 token 脱敏、callback 拒绝、错误/取消保留已接受文字、effectful unknown 防重复、receipt/history/admin 一致性。前端覆盖历史分类兼容、过程内部事件顺序、按操作边界归档说明、单一折叠和独立结果、手动选择、复制与下一 Run 隔离。

类型检查、构建、源码库存、挂载回归和合成浏览器不替代真实 provider pacing、代理刷新、s72 部署及端到端验收；分别报告实际证据。
