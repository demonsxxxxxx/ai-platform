# ADR 0018: 事件回放以 PostgreSQL 为准，Redis 只负责实时

## Status

Accepted（方向，维护者 2026-11-16 确认）。修订 [ADR 0013](0013-redis-stream-only-sse.md) 中
"Redis Stream 是唯一的实时与回放通道"和"回调在 Redis 追加成功后才确认"两条决定。
实施在 #1956 合并后开始；在各切片落地前，现行合同仍以 ADR 0013、
[SSE wire](../architecture/redis-streams-sse-wire-protocol.md) 与
[SSE execution control](../architecture/redis-streams-sse-execution-control.md) 为准。

ADR 0013 中其余决定保持不变：不引入 Pub/Sub 扇出、PostgreSQL 发布队列、后台发布者或继任者重建；
业务事实、授权、脱敏与终态归属不变。

## Context

ADR 0013 之后，每条公开事件已经在回调事务里完整写入 `run_events`（含 `sequence` 与可见性），
`project_public_v4` 可以从这一行确定性地得到浏览器看到的同一个 envelope。
但浏览器只能从 Redis Stream 回放，因此：

1. **流被裁剪或丢失**时只能发 `stream.gap`；前端做完授权历史加载后，若没有旧游标，
   只能轮询 Run 状态直到终态，活动任务中途的正文一直不显示（#1961 S2）。
2. **Redis 追加失败**时，即使 PostgreSQL 已提交事实和回执，回调仍返回
   `503 callback_stream_unavailable`，执行器重试同一批次；展示通道的故障反过来卡住执行（#1961 S3/F4）。
3. 为保证 Redis 内的顺序，回调要先补发前一条已提交事件，再追加本批次。

对照项目 LinkSense 用一张持久事件表作为回放权威，Redis Pub/Sub 只负责唤醒，心跳也触发一次补读；
通道丢消息只影响延迟，不影响正确性。

`run_events.sequence` 由每个 Run 一行的 `run_event_cursors` 分配，分配语句持有该行的行锁直到事务提交，
因此**提交顺序等于序号顺序**：读到序号 N 时，所有小于 N 的事件都已提交或永不出现。
这让"按序号从 PostgreSQL 补读"天然具有一致切点。

## Decision

### D1 回放权威

一个 Run 的公开事件以 `run_events` 为唯一回放权威，事件身份是 `(run_id, sequence)`。
SSE 网关发出的每一帧都对应一行已提交的 `run_events`，经现有 `project_public_v4` 投影与脱敏。

### D2 Redis 的角色

Redis Stream 保留为低时延的实时通道：

- 网关收到的 Redis 条目若序号正好是"已发送序号 + 1"，直接转发；
- 序号有空洞、Redis 条目缺失、流被裁剪或 Redis 不可用时，网关按序号从 `run_events` 补读，再继续；
- 即使没有任何 Redis 条目，网关也在心跳间隔内补读一次，保证通道静默时不卡住。

Redis 不再决定"浏览器最终能看到什么"，只决定"多快看到"。

### D3 游标

SSE `id` 与 `Last-Event-ID` 改为 `run_id:sequence`。重连时网关从该序号之后补读，然后接上实时。
授权历史接口返回的活动 Run 快照序号（现有的 `snapshot.max`）就是实时续接的起点，
历史与实时之间不再需要 Redis 游标衔接。

`stream_incarnation` 在新游标下不再参与续接；它的退役在实施切片中与 wire 合同一起决定。

### D4 回调确认

回调在 PostgreSQL 提交事实与回执后即返回成功。随后尽力追加 Redis；失败只记指标与日志，
不再返回 `503 callback_stream_unavailable`，执行器也不因此重试。
追加前补发前驱事件的逻辑删除：顺序由序号保证，空洞由网关补读。

工具回执屏障、批次幂等、Run/Attempt 权威、取消与终态的回执检查保持不变。

### D5 `stream.gap` 的范围

活动 Run 不再因 Redis 裁剪或丢失而产生 `stream.gap`。`stream.gap` 只保留给
"请求的序号早于 PostgreSQL 仍保留的最早事件"（数据保留策略删除了历史事件）这一种情况，
此时前端按现有方式加载授权历史。

### D6 读负载

补读按 `(tenant_id, run_id, sequence)` 现有索引做范围查询，每次有界（批量上限沿用历史分页）。
网关只在序号空洞、重连和心跳时补读；连续的实时帧不访问 PostgreSQL。

## Consequences

- 活动任务刷新、新开页面、Redis 故障或流被裁剪后，正文都能从断点继续，不再只轮询到终态。
- 展示通道故障不再影响执行与回执；执行器少一类重试。
- 游标格式变化是 wire 变更，前后端要在同一切片中切换；旧的 Redis 游标在切换后按"无游标"处理，
  从授权历史快照续接。
- 网关增加有界的 PostgreSQL 读；需观测补读次数与延迟。

## Implementation plan

前置：#1956 合并（它改动了回调、SSE 路由与前端 useAgent）。

| 切片 | 内容 | 退役 |
| --- | --- | --- |
| 1 网关补读 | SSE 网关按序号从 `run_events` 补读空洞，心跳补读一次；Redis 连续帧直接转发 | 活动 Run 因裁剪/丢失产生的 `stream.gap` |
| 2 游标与续接 | `run_id:sequence` 游标；历史快照序号作为实时起点；前端 reducer 按序号去重 | 前端"gap 后轮询到终态"分支；Redis 游标续接逻辑 |
| 3 回调确认 | PostgreSQL 提交后即确认；Redis 追加尽力而为 | `503 callback_stream_unavailable`、前驱补发、执行器对此的重试 |
| 4 清理 | 评估 `stream_incarnation`、`run_event_terminal_drains`、`run_event_batches` 等仅服务于 Redis 续接的状态 | 逐项列出退役或保留理由 |

每个切片在 PR 中提供退役清单，并同步修订 SSE wire 与 execution control 合同。

### 验收

- 长任务中把 Redis 流裁剪到只剩最后几条，刷新页面：正文从头到当前连续出现，不重复，之后继续实时。
- 运行中停掉 Redis：工具继续执行、回调返回成功；恢复 Redis 或仅靠心跳补读，页面补齐全部正文。
- 正常网络下首字时延与改动前相同（连续帧不访问 PostgreSQL）。
- 无权限、切换用户或失去 Run 授权时，补读与实时一样被拒绝；私有事件不出现在补读结果中。
- 前后端版本切换：旧游标重连被当作无游标处理，从历史快照续接，不报错。

## Alternatives considered

- **保留 Redis 唯一回放，只增大保留长度**：降低裁剪概率，但 Redis 不可用时仍会卡住回调，问题未消除。
- **恢复 PostgreSQL 发布队列与后台发布者（ADR 0012 路线）**：增加第二个发布控制面，ADR 0013 已否决，本 ADR 不采用。
- **改用 Pub/Sub 唤醒（LinkSense 的做法）**：Redis Stream 已经提供同等的唤醒能力，并且能直接转发连续帧，不必替换。
