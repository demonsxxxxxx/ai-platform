# ADR 0017: 沙箱资源按会话存活，执行权按 Attempt 授予

## Status

Accepted（方向，维护者 2026-11-16 确认）。实施在 #1956（Attempt 唯一结算）合并后开始。
在各切片落地前，现行合同仍以 [Sandbox Runtime](../architecture/sandbox-runtime-control-layer.md)、
[Provider continuity](../architecture/claude-provider-session-continuity.md) 与
[ADR 0016](0016-run-execution-settlement-and-reclamation.md) 为准。每个切片落地时同步修订这些 owning 文档。

本 ADR 修订 ADR 0016 的 D1（"运行在绑定精确 Run/Attempt 的受管沙箱内"）和 D5 正常完成顺序中的
"停止并释放资源"。D2 的三主体划分、D3、D4、D6–D10 不变。

## Context

当前一个沙箱资源的寿命等于一次 Attempt。同一会话的每一轮都要：

1. 创建沙箱，并在创建后和派发前两次完整探测就绪（endpoint/info/health/identity 各两次，#1961 F2）；
2. 把上一轮保存的工作区版本复制进新沙箱，并从 PostgreSQL 镜像恢复 Claude 原生会话；
3. 正常完成时先 stop provider，再扫描、复制、fsync 保存工作区，然后才能结算（#1961 F5）；
4. 停止时必须生成停止快照，并把续聊结算到可恢复状态，否则下一轮不能发送（#1899 一类问题）。

这些成本在每一轮的正常路径上发生，并由 6 张 `provider_session_*` 表、epoch、writer 校验、
停止快照结算和隔离对账共同维持正确性。

对照项目 LinkSense 让执行环境按会话存活、空闲 900 秒后回收，原生线程留在工作区，下一轮直接续接；
授权、凭据和能力每轮重新确认。详见 #1961 的对比。

## Decision

### D1 资源寿命与执行权分开

- **SandboxResource** 绑定 `(tenant_id, workspace_id, user_id, session_id)` 和一个**环境指纹**。
  它在 Run 结束后不销毁，进入 `idle`，供同一会话的下一轮复用。
- **执行权**仍按 Attempt 授予。每一轮照常：重新授权主体、Profile、Skill、模型和 MCP；
  冻结 ExecutionSpec；创建 Attempt；签发新的 `(run_id, attempt_id)` 回调令牌和 Attempt 绑定的模型凭据。
- 资源上只记录**当前 Attempt**。Attempt 进入终态时，资源的当前 Attempt 清空，旧令牌随即失效；
  回调授权仍是"HMAC 令牌 + 精确的当前绑定"，只是绑定对象从"每 Attempt 一条 lease"变为
  "资源上的 current_attempt_id + generation"。

### D2 环境指纹与失效条件

环境指纹由决定沙箱内容的不可变输入组成：镜像 digest、执行器版本、安全/网络 profile、
Profile 修订、选定 Skill 集合与版本、MCP 选择及其连接配置版本。

满足以下任一条件时，不复用，走现有冷启动路径（创建 + 工作区恢复 + 原生会话恢复）：

- 环境指纹变化；
- 空闲超过 `sandbox_session_idle_ttl_seconds`（默认 900 秒）；
- 健康探测失败、provider 状态与记录不一致，或资源处于 quarantine；
- 上一轮存在结果不明的外部效果（如 `mcp_execution_outcome_unknown`）；
- 容量回收（见 D5）。

模型凭据、回调令牌、MCP 凭据按 Attempt 签发，不进入环境指纹，变化不导致重建。

### D2a 每轮结束时清掉的东西

复用的只有**文件系统和执行器进程**。每轮结束、进入 `idle` 之前，执行器必须：

1. 终止本轮工具产生的全部子进程（按进程组），不允许后台进程跨轮存活；
2. 清除本轮的模型凭据、MCP 凭据和回调令牌；
3. 保留工作区文件与 Claude 原生会话文件。

### D3 状态机增加 `idle`

在 Sandbox Runtime 目标状态机上增加一个状态：

| 状态 | 含义 | 允许的下一状态 |
| --- | --- | --- |
| `idle` | 无当前 Attempt；文件系统与原生会话保留；不允许任何执行或投递操作 | `executing`（同会话新 Attempt 经 CAS 领取）、`releasing` |

`executing` 的下一状态增加 `idle`（本轮结算完成且满足 D2a）。
一个资源同一时刻最多一个当前 Attempt；同一会话的并发 Run 只有一个能领取，其余走冷启动或排队，
沿用现有会话内串行规则。

### D4 正常完成与停止

- **正常完成**：`保存终态证据 → 收集产物 → 提交业务结果 → 进入 idle`。
  不再在正常路径上 stop provider 和保存工作区版本。
- **取消/超时**：`持久取消意图 → 撤销执行权 → 中断当前轮 → 结算 → 进入 idle`。
  沙箱和原生会话仍在，下一轮直接续接，不需要停止快照结算。
  中断后执行器无法确认已停写，或宽限期内未退出时，资源转入现有强停与 quarantine 路径。
- **产物交付不变**：本轮选定的成果文件仍在结算时写入对象存储并登记，与资源寿命无关。

### D5 回收与容量

- 空闲到期、容量不足或指纹失效时，资源进入 `releasing`：**先保存一次工作区版本**，再 stop 与释放。
  保存失败不丢已登记的成果；保存结果不明时保留资源并重试，不静默丢弃工作区。
- 每个 Worker/宿主设置空闲资源上限，超出时按最久未用回收。
- Worker 重启或进程丢失后，以 provider 的实际状态为准对账：能证明归属且健康的空闲资源可继续复用，
  否则回收。

### D6 原生会话

热路径上，Claude 原生会话文件留在沙箱内，下一轮由 SDK 原生 `resume` 续接。
PostgreSQL 中的 `provider_session_*` 镜像只服务冷路径（资源已回收后的恢复）。
镜像的写入时机与表结构在后续切片中收缩，由 Provider continuity 合同重新定义。

## Consequences

- 隔离粒度从"每轮"变为"每会话"：同一会话后续轮次能看到前几轮留下的文件——这与现在
  "复制恢复工作区"可见的内容相同；新增的差异只有执行器进程跨轮存活，由 D2a 约束。
- 空闲资源占用宿主内存与磁盘，需按 D5 设上限并观测。
- 资源身份不再一对一对应 Attempt，`sandbox_leases` 需要迁移（D1 的 current_attempt_id）。
  历史行保持可读，并可按精确身份清理。

## Implementation plan

前置：#1956 合并；#1953 的计时口径修正（否则无法对比）。

| 切片 | 内容 | 退役 |
| --- | --- | --- |
| 0 基线 | 同模型、同输入、同工具，测第二轮的首字时延、工作区复制字节、停止到可再发送的时间、空闲内存 | 无 |
| 1 资源与绑定 | `sandbox_leases` 增加会话绑定、环境指纹、`current_attempt_id`、generation 与 `idle`；回调授权改为校验当前绑定 | 每 Attempt 一条 lease 的写入路径 |
| 2 热路径复用 | 同会话命中 `idle` 资源时跳过创建、双重探测、工作区与原生会话恢复；执行器实现 D2a | 正常路径上的重复就绪探测 |
| 3 收尾与停止 | 正常完成和取消改为进入 `idle`；工作区保存移到回收时 | 正常路径的 stop+save 屏障、热路径停止快照结算 |
| 4 回收与容量 | 空闲 TTL、上限、LRU、重启对账 | 无 |
| 5 镜像收缩 | `provider_session_*` 降为冷路径备份，收缩写入与表 | 热路径上的逐条镜像与 writer 校验 |

切片 1–3 在一个临时配置开关后上线，用于对比验收；验收通过后删除开关和旧路径，不长期保留两套实现。

### 验收

- 第二轮首字时延、复制字节、停止到可再发送时间相对切片 0 明显下降。
- 旧 Attempt 的令牌在其终态后被拒绝；上一轮的工具子进程在下一轮不存在。
- Profile、Skill、MCP 选择或镜像变化时走冷启动；模型凭据轮换不触发重建。
- 停止后立即发送下一轮：同一原生会话续接，不出现 `writer_unverified`、`recovery_candidate`。
- 空闲到期回收前保存工作区；回收后的下一轮经冷路径恢复到相同文件内容。
- Worker 重启后，空闲资源被正确复用或回收，不产生孤儿或重复资源。

## Alternatives considered

- **用户级共享环境**（LinkSense 的做法）：同一用户所有会话共用一个 Worker。隔离弱于会话级，不采用。
- **保持每轮新建，只优化复制与探测**：能减少一部分成本，但停止快照、续聊结算和镜像恢复仍在每轮正常路径上，不能消除 #1899 一类问题的根源。
- **永不回收**：资源占用不可控，且执行器版本升级无法生效。
