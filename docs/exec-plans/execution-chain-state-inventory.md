# 执行链路状态盘点（提案，待评审）

状态：提案。本文是重构前的事实盘点和候选方向，不是已批准的合同；现行行为仍以
[Runtime authorities](../architecture/runtime-authorities.md)、
[Run lifecycle](../architecture/run-lifecycle-boundary.md) 与
[ExecutionSpec and RunAttempt](../architecture/execution-spec-and-attempt-lifecycle.md) 为准。
盘点基线：`main` `4a9e1d25`（2026-10-23）。

## 1. 为什么做

近 60 天 `main` 上修复类提交约是功能类的 10 倍（10 月 fix 124 / feat 13）。被修复提交改动最多的
生产文件几乎都在执行链路上：`claude_agent_sdk_runner.py` 58 次、`executor_app.py` 19 次、
`container_provider.py` 17 次、`runtime_callbacks.py` 17 次、`claude_agent_worker.py` 17 次、
`executor_reconciler.py` 16 次。修复标题的高频主题是 sandbox、stream、终态、租约、回执、取消、超时。
核心 9 个文件约 2 万行。修复方式多为“再加一道 fence / 证据 / 错误码”，很少删除状态或路径。

本文回答三个问题：一个任务同时存在哪些状态；终态可以从哪些路径到达；哪些状态、路径与命名重复或不一致。

## 2. 一个沙箱任务的主路径

Claude 类任务由 `decide_execution_boundary`（`app/execution_boundary.py:105`）判定为真实沙箱执行，
主路径是“异步交付、回执收尾”：

1. 提交：路由创建 `runs`（`status='queued'`）与 `run_attempts`，写入 Redis 队列（`app/queue.py`）。
2. 领取：Worker `run_once`（`app/worker_main.py:884`）以带 owner token 的队列租约领取，
   `ClaudeAgentWorkerAdapter.submit_run`（`app/executors/claude_agent_worker.py:381`）创建
   `sandbox_leases` 并把任务交给沙箱执行器；成功交付后结果为 `running`，Worker 立即 ack 队列。
3. 执行：沙箱内 `executor_app`（`app/runtime/sandbox/executor_app.py`）运行 Claude Agent SDK，
   通过 HTTP 回调上报进度与终态。
4. 回执：`record_executor_callback`（`app/routes/runtime_callbacks.py:180`）把终态写入
   `sandbox_leases.executor_terminal_json`，并置 `executor_reconciliation_status='pending'`。
5. 对账：`executor_reconciler` 认领租约，回收 workspace 与产物，转换结果，经
   `reconcile_executor_terminal_result`（`app/bootstrap/executor_terminal_reconciliation.py:24`）与
   Worker 终态完成服务写入 Runs 终态。
6. 收尾：释放租约、清理容器、更新流与 provider 会话谱系。

## 3. 一个任务同时持有的状态维度

| # | 存储位置 | 字段 | 取值 | 约束 |
|---|---|---|---|---|
| 1 | Postgres `runs` | `status` | `queued` `running` `succeeded` `failed` `cancelled` `dead_letter`；读取时另接受 `canceled` | **无 CHECK 约束** |
| 2 | Postgres `runs` | `terminalization_target` 及 `terminalization_*`、`cancel_requested_at` | 空、`cancel_requested`、`failed`、`cancelled` | 隐藏的“正在收尾”子状态 |
| 3 | Postgres `run_attempts` | `status`、`owner_generation`、`lease_expires_at`、`last_heartbeat_at` | `created` `queued` `claimed` `running` `cancel_requested` `expired` `succeeded` `failed` `cancelled` | **触发器强制迁移表**（`app/schema.sql:1010`），并投影 Run 状态 |
| 4 | Redis | `queued` / `processing` / `processing_meta` / `retry_meta` / `dead_letter` / `queued_run_index` / `reconciliation_fence_*` / `worker_heartbeat` 等 11 类键 | 排队、已租约、重试、死信、对账 fence | Lua 脚本与 owner token |
| 5 | Postgres `sandbox_leases` | `status` | `active` `released` `quarantined` | 无 CHECK |
| 6 | Postgres `sandbox_leases` | `executor_status` | `pending` `accepted` `running` `completed` `failed` `cancelled` | CHECK |
| 7 | Postgres `sandbox_leases` | `executor_reconciliation_status` 及 claim token、`retry_not_before`、两个尝试计数 | `waiting_terminal` `pending` `claimed` `retry` `cleanup_pending` `finalized` `failed` | CHECK |
| 8 | 沙箱执行器 | 任务状态 | `idle` `running` `completed`/`succeeded` `failed` `cancelled`/`canceled` `callback_failed` | 同义词并存（`app/runs/domain/diagnostics.py:82`） |
| 9 | 其他关联表 | `provider_turn_receipts`、`provider_session_append_receipts`、`run_event_terminal_drains`、`sse_authority_leases` | 会话回执、流终态排空、流权威租约 | 各自独立 |

一个正常结束的沙箱任务至少要让第 1、3、4、5、6、7 维各自走到终点；任何一维停在中间，
都需要后台路径兜底。

## 4. 终态可以从哪些路径到达

| 路径 | 入口 | 触发条件 | 写入 |
|---|---|---|---|
| P1 执行器回调 | `routes/runtime_callbacks.py:180` `record_executor_callback` | 执行器上报终态 | 租约终态 JSON，交给 P3 |
| P2 执行器探测 | `executor_reconciler.py:687` `probe_suspect_executor_tasks_once` | 回调缺失或执行器可疑 | `_persist_probe_terminal`，交给 P3 |
| P3 终态对账 | `executor_reconciler.py:868` `reconcile_pending_executor_terminals_once` | 租约 `pending`/`retry` | Runs 终态；失败走隔离或清理（`:241`、`:365`、`:384`、`:401`） |
| P4 Worker 内联完成 | `run_once` → `process_run_payload` → Worker 终态完成服务 | 交付前失败、早期失败、非沙箱执行器 | Runs 终态 |
| P5 Worker 异常兜底 | `worker_main.py:696` `_terminalize_escaped_process_exception` | 处理过程逃逸异常 | Runs 失败或 `dead_letter` |
| P6 过期回收 | `worker_main.py:397` `reconcile_stale_runs_for_worker` + `stage_stale_run_reconciliation` | 租约/心跳过期且队列 fence 可得 | `stale_run_interrupted` 失败或取消 |
| P7 用户/管理员取消 | `runs/application/cancellation.py` | 取消请求 | 先写 `cancel_requested`，由执行器或 P3/P6 推进到 `cancelled` |
| P8 入队失败 | `mark_run_enqueue_failed` | 入队失败 | Runs 失败 |
| P9 队列回收 | Worker 维护阶段 `queue_reclaim` | Redis 处理中消息失主 | 重新入队或死信 |
| P10 沙箱清理 | 维护阶段 `sandbox_cleanup` 与对账器 `cleanup` 循环 | 过期租约 | 租约释放或隔离 |

后台循环：对账器同时运行 `read_signals`、`reconcile`、`cleanup`、`probe` 四个循环
（`executor_reconciler.py:1052`）；Worker 维护阶段有 `sandbox_cleanup`、`memory_cleanup`、
`data_retention`、`file_upload_session_cleanup`、`queue_reclaim`、`stale_run_reconciliation`，
并按 `integrated` / `execution` / `finalizer` 三种角色裁剪（`worker_main.py:605`）。

本清单基线记录了 Runs 终态写入外围的两套同构包装：
`provider_terminalization.py` 的 `*_with_context` 与 `terminalization_v4.py` 的 `*_with_v4`。
N12 当前候选已将 provider lineage 与 v4 终态行组合到
`app/runs/application/run_terminalization.py`，取消请求仍由独立 `append_terminal` 唯一追加终态行。

## 5. 错误码

- 失败类代码字面量约 1000 个（后缀 `_invalid` 331、`_unavailable` 89、`_failed` 87、`_missing` 86、
  `_mismatch` 85……），执行链路相关前缀（`run`、`provider`、`executor`、`sandbox`、`execution`、
  `claude`、`model`、`queue`、`stream`）合计数百个。
- 对外映射（`app/runs/domain/public_terminal.py`）：101 个内部码收敛到 35 个公开码，设计合理；
  未列入映射的执行链路码（如 `executor_protocol_invalid`、`executor_reported_failure`、
  `worker_process_exception`、`lease_expired_max_attempts`、`stale_run_interrupted`）统一落成
  `run_failed`，管理员只能从诊断区分。

## 6. 发现的重复与不一致

| # | 问题 | 证据 |
|---|---|---|
| F1 | Run 终态集合各自定义 | `executor_reconciler.py:66`、`run_control_readiness.py:17`、`runs/domain/run_performance.py:17`、`runs/domain/terminalization.py:9`、`sandbox/application/callback_authority.py:8`（多 `canceled`）、`runs/domain/attempt_lifecycle.py:26` |
| F2 | `runs.status` 无数据库约束，取值靠代码约定 | `app/schema.sql` 中 `runs` 无 status CHECK；`run_attempts` 有完整迁移触发器 |
| F3 | 死信 4 种拼写，共约 140 处 | `dead_letter` 115、`dead_lettered` 13、`dead-letter` 10、`dead-lettered` 2；`runs/infrastructure/creation_postgres.py:26` 同时接受三种 |
| F4 | 取消 2 种拼写 | `normalize_run_status`（`runs/domain/public_terminal.py:4`）、执行器状态同时接受 `cancelled`/`canceled` |
| F5 | 执行器与 Run 用不同的成功词 | 执行器 `completed`（亦接受 `succeeded`），Run `succeeded`；`claude_agent_worker.py:92` 需要 `{"completed", "succeeded"}` |
| F6 | 同一张表两个写入方 | `sandbox_leases` 由 `platform/postgres/sandbox_leases.py`（1116 行）与 `sandbox/infrastructure/leases_postgres.py`（292 行）分别写入；前者位于 `platform` 层却承载 Sandbox 业务状态 |
| F7 | 终态包装层重复（基线） | 基线中的 `provider_terminalization.py` 与 `terminalization_v4.py` 同构；当前候选由 `run_terminalization.py` 组合 |
| F8 | “是否结束”有多个独立判断者 | P1–P10 中至少 P2、P3、P5、P6、P9、P10 会在不同进程、以不同 fence（Redis 队列租约、对账 fence、`owner_generation`、租约 claim token）独立推进终态 |
| F9 | 一个沙箱任务需要 6 个维度各自收敛 | 第 3 节维度 1、3、4、5、6、7；竞态与“卡住的任务”多发生在维度之间 |
| F10 | 执行链路内部码对外统一为 `run_failed` | 第 5 节；用户与客服无法区分超时、执行器崩溃、回执丢失 |

## 7. 候选简化方向（待评审，按收益/风险排序）

1. **统一状态词表**（低风险）：在 Runs domain 定义唯一的 Run/Attempt/执行器终态集合与拼写；
   删除 F1、F3、F4、F5 的本地副本与同义词，读取历史数据时在一处归一化；为 `runs.status` 加 CHECK。
2. **合并包装层**（低风险）：F7 的当前候选由 Runs application 的 `run_terminalization.py` 一次协调终态后副作用（provider 谱系、v4 行）。
3. **租约单一写入方**（中风险）：F6 收敛到 Sandbox 上下文，`platform` 层只保留连接与通用原语。
4. **终态单一裁决入口**（高收益、高风险）：回调、探测、过期回收、异常兜底只产生“终态候选”，
   由对账器一个入口按固定优先级裁决并推进所有维度；Worker 与维护阶段不再直接写终态。
   需要先有第 8 节的端到端回归。
5. **减少维度**（高收益、高风险，需 ADR）：评估 `sandbox_leases` 三套状态能否合并为一个状态机，
   以及 Redis 队列状态能否不再参与终态判断（只负责投递）。
6. **公开错误更细**（低风险，产品决策）：把超时、执行器崩溃、回执丢失映射为不同公开码（F10）。

## 8. 前置条件：真实链路回归

在做第 7 节第 4、5 项之前，需要一组跑真实 Worker、沙箱执行器与回调的端到端场景：
正常完成、用户取消、执行超时、沙箱容器崩溃、回调丢失（只能由探测发现）、重复回调、
Worker 在交付后崩溃、对账器在回收 workspace 时失败。每个场景断言第 3 节各维度的最终值，
而不只是 Run 状态。没有这组回归，重构与后续修复都缺乏可靠依据。

## 9. 本盘点的局限

- 基于静态阅读与代码检索，没有运行真实链路；各路径的触发频率未从线上数据测量。
- 第 3 节只列与终态相关的维度，未覆盖流事件序列、provider 会话内容与产物存储的完整状态。
- 错误码按字面量和后缀统计，包含校验与非执行链路代码，只用于量级判断。
