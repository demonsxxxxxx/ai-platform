# ADR 0018: 事件回放以 PostgreSQL 为准，Redis 只负责实时

## Status

Accepted（方向，维护者 2026-11-16 确认）。从 #1964 的 `e919477e` 纳入并修订，
修订 [ADR 0013](0013-redis-stream-only-sse.md) 的 Redis 唯一回放与 Redis 成功后 ACK 两项决定。
本切片在 #1956 合并后实施；源代码、局部测试、CI、发布与 s72 验收不是同一种证据。
当前候选合同见 [SSE wire](../architecture/redis-streams-sse-wire-protocol.md) 与
[execution control](../architecture/redis-streams-sse-execution-control.md)。ACK 变更已在读端局部验证后实现，并通过真实 PG/Redis 局部测试；
这不是 CI、发布或 s72 验收证明。

不引入 Pub/Sub 扇出、PG 发布队列、后台发布者、第二套终态或继任者重建。
业务事实、授权、脱敏、Attempt 与终态归属保持不变。

## Context

公开事件和回执已经原子写入 `run_events`，现有 `project_public_v4` 能确定性投影。
Redis-only 回放却在裁剪、丢失时只能要求历史 hydrate；Redis 追加失败还会让已提交的回调返回
`503 callback_stream_unavailable`。回调前驱补发也是为 Redis 内的顺序引入的机制。

LinkSense 的持久事件表加低时延唤醒提供了设计参照。本项目只借鉴设计，不复制 CPAL 源码，
不替换 Claude SDK、公开正文协议或现有前端 reducer。

`run_event_cursors` 的每 Run 行锁持有到事务结束，序号分配与提交顺序一致。
`run_event_batches` 保护批次幂等；二者都不是仅服务 Redis 的可删状态。

## Decision

### D1 持久回放与控制帧

应用事件以 `run_events` 为唯一持久回放权威，身份为 `(run_id, sequence)`。
扫描仍经过 tenant、Run、current Attempt、incarnation、可见性与现有公开投影检查。

并非每个 SSE 控制帧都对应事件行：`stream.open` 来自已确认 authority 的 canonical
`open_payload_bytes`；`stream.end` 从已观察到的持久终态确定性派生并引用其 event ID。
两者都不带 SSE `id`，不能推进已消费进度。终态游标重连可重新派生 end，而不重复正文或终态。
未准入 authority 不能凭空生成 open。

### D2 扫描、公开与已消费进度分开

- 扫描序号包含私有、历史 non-v4 和其他不可公开的行。
- 公开序号仅表示网关发出的应用事件；公开序号不要求连续。
- 已消费游标只在浏览器 reducer 接受后推进，或来自完整消费后的授权历史固定快照。

PG 每页最多 100 行，按固定 high-water 升序读取；空公开页仍推进扫描并继续。
Redis 严格校验后的连续帧可以使用 `scanned_sequence + 1` 快路径，不是“已公开序号 + 1”。
跳号、空读、裁剪或不可用触发 PG 补读。私有行导致的公开空洞不是丢失。
Redis XREAD 有界（128 条、最多 5 秒）；不可用时每轮至少间隔 1 秒，避免忙循环。
PG 事务不跨网络发送或 Redis 等待。

### D3 游标与切换

应用帧 `id` 与 `Last-Event-ID` 为 `run_id:sequence`。完整授权历史的
`snapshot_max_sequence` 是续接起点；未消费完的分页快照不能用于跳过历史。
格式错误、跨 Run 或未来序号 fail closed。

有效旧 Redis 游标返回 HTTP 409 / `sse_cursor_snapshot_required`，由前端加载授权历史后续接，
不直接把旧游标解释为 PG 序号，也不静默视为零。自动恢复限同 incarnation；
跨 incarnation 仍由现有 message-owner fence 安全拒绝，可能只观察终态，本片不承诺完整迁移。
`stream_incarnation` 虽不再进入游标，仍参与 envelope、authority、Attempt 和 lease 校验。
本切片不退役这些授权边界。

### D4 回调 ACK

在 PG 读端、前端游标与终态恢复的局部验证后，回调成功条件收敛为业务事实、公开投影与回执事务提交成功。
Redis 追加在事务外使用 250 ms 超时预算做尽力尝试，传输失败或超时只记录日志，不改变已提交回执的成功。
因此不是零等待 ACK。
这不是“先返回响应再启动后台任务”；不新增后台发布者。

不能吞掉 `CancelledError`、投影、业务、授权、不可变回执冲突或数据库失败。工具屏障、批次/source digest 幂等、
Run/Attempt 权威与终态/usage 幂等保持不变。回调前驱补发在该切片删除，顺序由 PG 事实和补读保证。
Run 事件自身的直接发布仍保留。验收不变量由 execution control 定义，具体执行证据记录在 PR 中。

### D5 恢复边界与准入限制

已准入 Run 在 Redis 丢失时可以建立新的 SSE，补齐持久正文。Redis 裁剪或缺失不产生 gap。
当前产品没有 `run_events` 保留期删除路径；真正无法读取所需 PG 历史时 fail closed，
不虚构 retention ledger。未来若引入保留策略，须先定义可信 retained floor 与 gap/hydrate 验收。

**新 Run 准入仍依赖 Redis**：pending authority、Redis exact-open 与 PG confirmation
仍在 SDK dispatch 之前。替换 Redis 队列、放宽准入或新增 admission 扫描不属于本切片。
每连接授权 lease、续期与每帧截止检查不变；PG/授权失败不能延长本地权限。

## Consequences and acceptance

目标是已准入任务刷新、断线与 Redis 故障后正文可连续恢复，不重复，不再只等终态。
不承诺未实测的首字时延或 DB 容量改善；须观测补读负载与正常/故障延迟。

验收覆盖：固定 high-water、跨私有页、公开空洞、真实 PG 提交顺序、裁剪/缺失/停 Redis 后新 SSE、
跨用户与失权拒绝、旧游标授权历史交接、reducer 失败不推进游标、终态/end 重连及 ACK 故障分类。
局部测试不能替代 CI、精确发布包与 s72 外部验收。

## Retirement and retained owners

- 退役：浏览器 Redis-only resume 前置、Redis 游标作为浏览器持久进度、因 Redis 缺失只等终态的恢复路径。
- 已退役：回调前驱补发、仅由 Redis 传输失败触发的 callback 503。
- 保留：`run_event_cursors`（提交序号）、`run_event_batches`（幂等）、stream authority 与 lease（准入/授权）、
  incarnation（身份 fence）、Run/Attempt 与业务终态。不得按表名批量清理。
- 本切片没有 schema 删除，不纳入 #1964 的会话级沙箱或其他架构计划。

## Alternatives considered

增大 Redis 保留长度无法解决不可用；恢复 PG 发布队列会引入第二控制面；换 Pub/Sub 没有必要。
本切片保留严格 Redis 快路径，以持久 PG 补读承接恢复，而不是再建立一套执行或发布状态机。
