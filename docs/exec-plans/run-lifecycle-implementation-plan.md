# Run 生命周期节点实施方案

状态：Phase 0 实施中，后续迁移待评审。本文将 [ADR 0016](../adr/0016-run-execution-settlement-and-reclamation.md) 拆成可独立验收的节点。当前开工范围是 N00 库存、N01 测试夹具及 N02 首批故障基线；ADR 仍为提议，运行行为迁移、阶段启用和部署分别验收。节点编号是施工顺序，不是新的运行状态。

本次规划核对代码基线为 main 4a9e1d251de9fd2a79140faeb5f0bb3ea9ecd0dd，文档输入为 PR #1879 的 c1032bef0f2037caf4c446cabfd88f4a8eaa1b22。实际开工时重新核对当前源码、调用者和合同；测试结果、人员、制品和部署记录放在实施 PR。

详细行为继续由 [Runtime authorities](../architecture/runtime-authorities.md)、[Run lifecycle](../architecture/run-lifecycle-boundary.md)、[ExecutionSpec/Attempt](../architecture/execution-spec-and-attempt-lifecycle.md)、[Sandbox Runtime](../architecture/sandbox-runtime-control-layer.md)、[原生会话连续性](../architecture/claude-provider-session-continuity.md)、[SSE execution control](../architecture/redis-streams-sse-execution-control.md) 和 [ADR 0011](../adr/0011-durable-executor-terminalization.md) 拥有。每个行为变更节点同步修改其 owning contract。

本计划复用 [state-authority 计划](../implementation/state-authority-convergence.md) 中的准入恢复、操作身份和调用闭环，不建立另一套 submission、Attempt 或发布账本。涉及 [Harness 计划](completed/harness-migration-2026-10.md) B3–B5 的事务、身份和删除节点，继续核验其完整测试保护、D5 和独立审查前置；不能用本方案代替这些条件，也不把旧计划中的历史结果视为当前通过。

## 1. 评审建议与实施边界

| 待决事项 | 本方案建议 | 验收边界 |
|---|---|---|
| Q1 结算总期限 | 保留 ADR 0011 的临时错误持续退避；限制每次操作、并发和重试频率，监测年龄与实际进展 | 临时依赖恢复后原回执继续结算；不能按重试次数自动失败或重跑工具。告警不等于放弃结算 |
| Q2 展示 finalizing | 同意作为可选公开阶段，安排独立 U1 节点；Run 公开状态枚举暂不新增 | 仅由持久的结算进度推导，显示“正在整理结果”；不能靠客户端时间猜测，不能影响清理或终态 |
| Q3 waiting_input | 本轮不加入公开 Run 状态 | 保留现有交互能力；未来单独定义请求持久化、回答关联、授权、超时和恢复 |
| Q4 回归环境与门禁 | 本地/CI Linux Docker 跑真实依赖控制链；实际 SDK/provider 链路由维护者在真实环境执行 | CI 生命周期套件必须零跳过并进入现有 required 聚合；真实 Sandbox 证据是相关变更和阶段启用条件，缺失不得用 CI 替代 |

以上是后续 ADR 评审的实施选择。Phase 0 使用独立本地测试依赖；新增测试接入现有完整文件 owner，不更改 required 聚合策略。维护者已确认没有 s72，并要求先提交代码和 PR，实际 SDK/模型验收随后在真实环境执行；该项保持待验收。生产数据库迁移须单独验收。

范围包括请求接受到下一轮续接的完整生命周期。Pi/ACP 适配、换 Sandbox provider、引入 Temporal、队列载荷全面瘦身、保留策略和新网络隔离方案分别安排，不与本轮混合实施。

正常完成的顺序为：

~~~text
执行器结束观察 -> 原 Attempt 终态回执
                     -> 答案/原生会话校验、选定文件快照和对象准备
                     -> 原子提交业务结果
                          +-> 发布、历史回读、授权下载
                          +-> 停止、释放、孤儿对象补偿
~~~

两个提交后分支独立推进。没有浏览器连接也必须能清理。下一轮只依赖合法的已提交会话连续性和重新授权，不依赖上轮用户是否阅读；旧资源仍未释放时，容量与资源准入按现行规则处理。

取消/超时允许在业务结算之前发起 stop；失联和 stop 超时不是资源已停止的证据。取消后原生会话尾部追加只保留现行精确范围，不恢复普通模型、工具或读取权限。

## 2. 十二阶段的事实、操作与恢复

| 阶段 | 业务/运行 owner | 持久凭据及外部副作用 | 崩溃或未知结果的处理 |
|---|---|---|---|
| 1 准入 | Conversations/Runs 调用各授权 owner | principal、Profile/模型/Skill/MCP/文件授权与提交幂等；尚未接受时无执行副作用 | 确定拒绝与提交未知分开；不能因 HTTP/commit 响应丢失断言无 Run |
| 2 持久接受 | Conversations + Runs | submission、Session/用户消息、Context 绑定、queued Run，在现有事务边界内提交 | 按原身份读回；缺 Attempt 仍可由 Runs 结算确定失败 |
| 3 投递 | Execution，复用既有准入事实 | 目标持久投递意图、原 payload/message identity；Redis enqueue 是外部操作 | 原身份重投/读回；不会重新创建 Run、消息或上下文 |
| 4 执行准入 | Runs + Execution | 当前重新授权、冻结 ExecutionSpec、Attempt、owner/generation | 保持 Spec 不可变；重复交付与新 ordinal、新用户 retry 分开 |
| 5 沙箱准备 | Sandbox | 资源身份、Attempt 绑定、创建/准备凭据；provider create、文件 staging、凭据租约 | 创建成功但数据库未知仍有补偿责任；不能只删除本地记录 |
| 6 交付执行 | Execution + Sandbox | dispatch identity/intent、接受执行事实、reconciliation context；启动 Agent | 响应丢失先核对精确资源/任务；终态回执早到不被后来的 accepted 覆盖 |
| 7 执行 | 沙箱内 Engine；宿主各事实 owner | heartbeat、类型化事件、答案批次、SessionStore append receipts；模型/工具可能已有外部效果 | 浏览器断线不取消；旧 owner 回调拒绝；副作用未知不自动再执行 |
| 8 接收结果 | Sandbox 接收，Runs 使用 | 不可变 terminal receipt、来源、Attempt/代际、答案 receipt、最终原生会话序列 | 同身份同内容幂等；冲突拒绝；已保存结果不能被当作无主执行丢失 |
| 9 交付准备 | Sandbox + Execution + Artifacts/Object Lifecycle | 明确选中的文件、不可变快照、内容摘要、临时对象删除凭据；对象 put | 相同字节重试复用身份；未知提交先读回；未被业务事务接纳的对象可补偿 |
| 10 业务提交 | Runs 协调 Context/Artifacts/Streaming 等端口 | assistant、产物、provider coverage、Run/Attempt 终态、事件/审计同事务 | CAS 失效或事务失败不得留下部分公开结果；提交未知不能盲删对象或重跑执行 |
| 11 结果取得 | Streaming + 授权读取 + 前端 | 已提交答案/事件和产物是读取依据；Redis 通知是独立外部操作 | 发布失败走原读取/hydration；恢复只读结果，不重提任务；不建已读状态 |
| 12 资源回收/下一轮 | Sandbox/Object Lifecycle；Context + Execution | stop/release、对象补偿凭据；下一 Run 的当前授权及精确 native epoch | stop 未确认继续监督；原生历史缺失或脏状态按现有合同拒绝续接，不静默 empty_start |

### 当前代码定位

| 责任 | 当前入口，实施时复核直接调用者 |
|---|---|
| 提交幂等与队列未知 | [chat_submissions](../../app/persistence/chat_submissions.py)、[queue_admission](../../app/conversations/application/queue_admission.py)、[chat route](../../app/routes/chat.py) |
| Spec/Attempt 创建与执行绑定 | [worker_dispatch_binding](../../app/runs/application/worker_dispatch_binding.py)、[worker_attempt_lifecycle](../../app/execution/application/worker_attempt_lifecycle.py)、[Attempt persistence](../../app/runs/infrastructure/postgres.py) |
| dispatch accepted/回调早到 | [Sandbox runtime](../../app/runtime/sandbox/runtime.py)、[Sandbox lease adapter](../../app/sandbox/infrastructure/leases_postgres.py)、[callback route](../../app/routes/runtime_callbacks.py) |
| 结算、结果与对象补偿 | [executor_reconciler](../../app/executor_reconciler.py)、[worker_result_commit](../../app/runs/application/worker_result_commit.py)、[artifact_storage](../../app/execution/application/artifact_storage.py)、[artifact persistence](../../app/persistence/artifacts.py) |
| 原生会话结算 | [provider_epochs.commit_provider_turn](../../app/context/infrastructure/provider_epochs.py)、[run_terminalization.persist_assistant_with_provider_coverage](../../app/runs/application/run_terminalization.py) |
| 取消、发布与清理 | [cancellation](../../app/runs/application/cancellation.py)、[worker_publication_v4](../../app/streaming/application/worker_publication_v4.py)、[runtime_cleanup](../../app/sandbox/application/runtime_cleanup.py) |

### N00 开工库存：当前实现与故障窗口

下表针对上述 c1032bef 源码；GitHub main 核对为 4a9e1d25，本地 `main` 分支引用不是本次基线。定位按符号复查，不将下表中的恢复缺口当作已实现能力。

| 窗口 / 相关场景 | 当前写入及直接入口 | 已持久事实、恢复责任和后续验收 |
|---|---|---|
| 接受前及无 Attempt；R02/R09 | `chat_stream` → `claim_chat_submission` / `finalize_chat_submission`；`runs.create_run` → `creation_postgres.create_run` | Chat keyed 请求以 tenant/user/submission 唯一；Run 先写 queued，消息/Context/模型绑定由原事务提交。确定入队失败由 Runs 结算，不能要求 Attempt 存在。retry/resume/copy 经 Runs 路由重新准入，不能一律按 Chat submission 恢复 |
| PG 接受至 Redis 未确认；R09 | `chat._attempt_chat_queue_admission` 注入 `enqueue_run_with_metadata`/readback；`runs.create_run`、`copy_run`、`_ensure_run_control_queue_admission` 使用 `enqueue_run` | Redis 身份为规范 payload SHA-256，submission 记录 queue message/ordinal。Profile replay 分支持 Run/Profile 锁做 Redis 准入，其他路径有独立提交窗口；不能笼统称所有 enqueue 都在事务外。N22/N23 仍需实现无人重试的持久恢复闭环 |
| 有 Attempt、尚无资源；R02/R07 | `worker_main.run_once` → `worker.process_run_payload` → `prepare_worker_run` → dispatch admission/binding → `start_worker_run_attempt` | 冻结 ExecutionSpec、queue attempt/message、owner kind/id/generation 同连接绑定；created → queued → claimed → running 由 DB 触发器约束。这里的 running 不证明 provider 已接受。Worker 内联失败/取消由 completion service 处理 |
| 资源已创建、交付未知；R07/R10/R11 | `SandboxRuntime.submit` → provider create/stage/execute → `record_sandbox_executor_accepted` | lease 存 provider/资源句柄、Attempt/代际；accepted 存 reconciliation context。远端已启动但 accepted 未写的窗口仍须 R10 实证，不能用“HTTP 失败”推导未执行。provider 创建成功、lease 提交未知的补偿责任保留 |
| 回执先到或等待结算；R05/R06/R08/R11/R12 | `runtime_callbacks.executor_callback` → `record_executor_callback`；probe 经 `_persist_probe_terminal` 写同一个 terminal repository | token/lease/Attempt 复核，batch 去重，首份 terminal JSON 不可变；回执缺 context 时 waiting_terminal，accepted 补齐后转 pending，不覆盖终态。claim token 决定结算认领，不是执行凭据；旧 owner 和冲突回执必须拒绝 |
| 结果准备到业务事务；R08/R12 | `reconcile_pending_executor_terminals_once` → `_collect_workspace_and_convert_result` → `reconcile_executor_terminal_result` → `WorkerResultCommitService.commit` | 只收集 response_files；对象写前 reserve cleanup，业务事务接纳对象、答案、assistant/coverage、Run/Attempt 终态与事件。对象 put 不属于 PG 事务；提交未知先核对，不能删除已接纳对象。临时依赖退避，authority 错误隔离 |
| 业务已提交，通知/资源未完成；R13 | `publish_run_event` 与 `mark_sandbox_executor_reconciliation_cleanup_pending` 后续各自推进；cleanup claim → provider stop → release | Redis 发布失败不回滚业务结果；reconciliation 组合中的 runtime lease release 是 no-op，物理回收交 Sandbox。失败 stop 保留责任，发布失败或浏览器离线不成为资源释放前置 |
| 取消/下一轮；R02/R03/R04/R14 | `runs.cancel_run` → cancellation use case/post-processor → `cleanup_cancelled_run_leases`；Context `claim_provider_lineage` / `prepare_provider_epoch` / `commit_provider_turn` | 取消持久意图与 stop 分离；有限尾部 append 保留原规则。下一轮校验 ready epoch、精确覆盖和当前授权；commit 仍要求 running、无取消和精确 writer generation。源 transcript 缺失不能静默重新开始 |

入口的文件定位见上表及 [原始状态库存](execution-chain-state-inventory.md)。当前 terminal wrapper 的生产调用者按 `rg` 扫描 `app/**/*.py`：

- [worker_attempt_lifecycle bootstrap](../../app/bootstrap/worker_attempt_lifecycle.py) 注入 complete/fail/cancel/progress 的 v4 wrapper；[worker_attempt_lifecycle application](../../app/execution/application/worker_attempt_lifecycle.py) 在同连接推进 Attempt 与 Run。
- [worker_main](../../app/worker_main.py) 的 queue reclaim、异常 fallback 和未完成 terminalization；[executor_reconciler](../../app/executor_reconciler.py) 的已验证永久结算失败与取消分支；两者调用 v4 terminalization/finish。
- [run_admission_terminalization](../../app/run_admission_terminalization.py) 由 Chat 新提交、重放/重投及 Runs 入队失败路径调用，覆盖没有 Attempt 的失败。
- [run_lifecycle bootstrap](../../app/bootstrap/run_lifecycle.py) 为 cancellation 注入 context progress；[cancellation](../../app/runs/application/cancellation.py) 自己拥有终态事件与提交后处理。合并包装时不能再追加第二份事件。

这里列出当前执行入口和依赖注入组合；各迁移节点仍需按实际 diff 复查间接函数引用、schema/readiness、脚本和历史 reader，不能用正则命中数代替完整退役清单。

### N00 当前预算、时钟与恢复 owner

| 责任 | 当前默认值及来源 | 时钟 / 接管边界 |
|---|---|---|
| queue lease 与 Worker | [Settings](../../app/settings.py)：visibility 900s，扫描 50；heartbeat TTL 60s，maintenance 30s；stale Run 900s、扫描 20、fence 300s | Redis queue owner/CAS 与 PG Attempt heartbeat 各守原权限；dispatch detach 后 ACK 不等于执行结束 |
| sandbox 创建/dispatch/资源 | Settings：create 30s、executor health 60s、dispatch 30s、lease TTL 1800s、cleanup 30s；OpenSandbox request 30s、lifetime 1800s | 本地单次 I/O 预算与 provider/PG lease 到期不同；取消等待不证明远端已停止 |
| executor 心跳及总时间 | [create_executor_app](../../app/runtime/sandbox/executor_app.py)：heartbeat 15s；执行预算读取冻结请求 `resource_limits.max_seconds` | 单调时钟控制单次等待；不把 SDK 默认 timeout=0 解释为所有任务无截止时间 |
| probe 与 terminal reconciliation | [executor_reconciler](../../app/executor_reconciler.py)：心跳 stale 45s、probe client 10s、连续失败阈值 3；batch 1、claim stale 300s、work 240s、idle 30s、fallback 2s | candidate 读 PG 持久时间，认领后复核 claim；provider 不可达不是权威 not_found；已有回执走结算 |
| 暂时结算失败 | [retry_sandbox_executor_reconciliation](../../app/sandbox/infrastructure/executor_reconciliation_postgres.py)：`now() + least(30, greatest(1, attempt_count))` 秒 | PG `retry_not_before` 持久化；没有放弃总期限，不重跑 Agent。cleanup 单独 claim/槽位，stop 成功后才能 release |

Harness 前置复核：当前 [计划 §5.6](completed/harness-migration-2026-10.md#56-2026-10-19-当前实现与验收缺口) 仍记录 D5、完整基线/可信覆盖阻断等未闭环。本切片新增测试和库存，不改变事务、身份或删除字段；不能据此宣布 B3–B5 或后续生命周期迁移已满足前置条件。

## 3. 所有节点共同保留的不变量

1. 作用域精确到 tenant/workspace/user/Session/Run/Attempt；资源操作另绑定不可变资源身份。只读/写入都不能跨作用域。
2. 已接受输入和 ExecutionSpec 不因恢复重编译、重新授权或新 Profile 发布而被改写；当前权限检查不扩权。
3. 提交幂等重放、队列重投、内部新 Attempt、用户 retry/copy 的新 Run 是不同操作，分别遵守原有身份与准入。
4. 旧 owner、过期 claim 和迟到回执不能覆盖新事实；数据库 CAS 不被称为外部工具效果 exactly-once。
5. 终态回执保持原内容与身份；重复同内容可确认，冲突不可采用 last-write-wins。探测只能提交有来源的观察。未证明 Run/Attempt/claim 归属的回执走拒绝或现有隔离路径，不能据此把引用的 Run 写成失败。
6. assistant、产物接纳、provider coverage、Run/Attempt 终态与终态事件保持同一业务事务；终态后重复提交不增加答案/产物/终态事实。
7. 原生会话提交仍检查 running、无取消、精确 writer generation、完整序列/coverage；结算普通进度不提升执行代际。
8. 取消尾部 SessionStore append 只允许现有合法 writer 的限定排空；不能加载历史、调用工具或在接管/终态后继续追加。
9. 已确认或未知外部效果不被包装为可安全自动重试；SDK/工具失败根因不被后续文件回收错误覆盖。
10. 业务终态、通知、读取和资源释放独立；未证明 stop/not_found 不得标 released，清理失败不改写成功结果。
11. 对象存储写入有先行补偿责任，提交未知先核对原事实；不得删除已授权产物或不属于本 Attempt 的资源。
12. 所有扫描、认领、缓冲与外部 I/O 有容量和单次预算；取消 await 不等于实际 I/O 停止，仍在途工作占用原容量直到结束。

每个场景检查适用的中间不变量，而非只检查最终 status。被故意阻断的暂时结算/清理允许保持待处理，但必须有唯一合法责任、持久重试依据和恢复后的进展。

## 4. 测试层次、门禁与证据

### 4.1 已有能力与缺口

[backend workflow](../../.github/workflows/ai-platform-backend.yml) 已有 PG、Redis 和部分 shard 的 MinIO 服务；backend required 汇总 backend-tests 等结果。sandbox-runtime shard 没有 PG，且当前不要求零跳过，不能把它的成功当成完整生命周期实测。

可复用的真实依赖入口包括 [Worker heartbeat PG/Redis](../../tests/test_worker_heartbeat_postgres_redis_integration.py)、[frozen receipt PG](../../tests/test_frozen_receipt_spec_postgres.py)、[Sandbox persistence](../../tests/test_sandbox_persistence.py)、[Sandbox cleanup PG](../../tests/test_sandbox_cleanup_postgres.py)、[native recovery PG](../../tests/test_provider_session_recovery_postgres.py)、[provider images PG/MinIO](../../tests/test_provider_session_images_postgres.py)、[streaming PG](../../tests/test_streaming_v4_postgres_integration.py)。

这些文件分别验证自己的边界，不等于十四场景已经端到端通过。例如 [test_run_lifecycle_postgres](../../tests/test_run_lifecycle_postgres.py) 使用 recording Connection，不能按文件名算真实数据库证据；native recovery 的真实 SDK restore 也不能代替真实沙箱跨进程续接。

[真实 OpenSandbox 凭据测试](../../tests/test_opensandbox_live_credential_isolation.py) 需要显式环境开关；现有 backend workflow 未为它建立必过的真实 provider 环境。本次没有运行上述套件。

### 4.2 两层验收

| 层次 | 环境与实际入口 | 证明范围 | 必须保留的限制 |
|---|---|---|---|
| C：CI 控制链 | 隔离 Linux Docker；真实 PG/Redis/MinIO；实际 API、Worker、callback、结算和清理编排；确定性测试执行器经实际传输接入 | 事务、重复交付、并发、进程崩溃与可控故障恢复 | 测试执行器不是实际 Claude/OpenSandbox；不得声称证明 SDK 工具、gVisor、真实模型或 provider 停止 |
| L：真实执行链 | 维护者提供的独立测试栈；候选镜像、实际 provider、实际 SDK/受管工具及授权模型代理 | 真正的接受执行、回调、停止、文件下载、跨沙箱 native_resume；必要时浏览器验证 | 记录实际 provider/安全 profile；Docker 验收不能代表 OpenSandbox/gVisor，不扩大网络隔离承诺 |

C 层新增套件走现有 run_test_stage.py、明确 selectors、零跳过和现有证据格式。[整链路测试](../../tests/test_run_execution_lifecycle_integration.py) 由 `lifecycle-control-chain` shard 全文件拥有。正常答案、选中文件、交付后 Worker 重启、终态落库后 ACK 丢失四个场景均通过生产提交 API 创建 Run，再经过实际 Worker、Docker executor、callback、结算、MinIO 下载和资源回收。准入前仅构造合成身份与已发布模型；session、Run、Attempt、lease、会话回执和答案均由生产路径创建。

N02 首批在同一夹具增加两个 C 层切片。R02 的 queued 取消在 Worker 启动前走真实 owner API，检查跨用户拒绝、唯一取消事实、队列及索引清理，以及没有 Attempt、沙箱或业务结果。R05 持续在真实终态 callback 写库前返回 503；先证明答案与原生会话追加已落库、终态回执尚未存在，且执行器状态接口保留结果，再由重启后的 Worker 按现行心跳过期与探测规则恢复同一回执并完成文件交付和回收。该场景保留生产 callback 重试和 probe 时限。交付后停止 Worker 的屏障同时要求 runner 已进入且 reconciliation context 已持久化。

[Docker 夹具](../../tests/support/run_lifecycle_docker.py) 为每场景创建独立 PG/Redis/MinIO、网络、工作区卷和 API/Worker；使用候选源码构建镜像，确定性 runner 替代 SDK query，并显式记录 `sdk_used=false`。Worker 与 API 共用网络 namespace，以访问只包含 API 和 executor 的 owned internal bridge；这与可选 Docker Compose overlay 的控制拓扑一致。终态 ACK 故障在真实 API 成功响应后截断 HTTP body，保存指定 batch 的故障与去重重试证据。资源不存在断言在 fixture teardown 前执行，清理器按精确测试标签删除残留，保存独立清理清单。

同文件保留两个接收边界场景，从合法的 seeded running Attempt 验证旧代际拒绝、重复终态和 callback 屏障处退出。该层需要 `AI_PLATFORM_S0A_SCHEMA_TEST_DSN` 指向专用测试数据库，并为每场景创建独占 Redis，因为 callback 还写固定 reconciliation signal key；仅 queue prefix 不足以隔离。CI 提供 PG/Redis 镜像，完整 Docker 栈复用现有 CI 的 `bitnamilegacy/minio:2025.4.22-debian-12-r1`，按需拉取并使用镜像默认初始化入口。Docker、依赖缺失直接失败。

这些用例建立 C 层夹具及 N02 首批切片；实际通过结果以 runner evidence 和 PR 为准。R02 的执行中取消、回执后取消和 stop 失败，R05 的探测不可达及迟到冲突，以及 N02 其余故障变体继续待验收。L 层实际 SDK/provider、跨沙箱 native_resume 由维护者在提交 PR 后于真实环境执行；N01 两层验收、十四场景和 Phase 0 尚未完成。

新文件由一个 CI shard 全文件拥有；该 shard 测试预算 1200 秒、job 预算 30 分钟，其他 shard 保留原预算。已有测试不为新门禁重复跑进第二个 shard。现有全量 required checks 保持，C 层纳入 backend required；首版不靠狭窄路径过滤绕过未知影响。依赖缺失、未收集测试、skip 或错误制品均不能返回通过。门禁要以一个故意破坏不变量的候选证明能够阻断，随后恢复候选。

每个生命周期行为 PR 必过 C 层适用回归及现有 required。触及 provider、SDK、回调/代际、取消停止、文件交付或 native continuity 的 PR，还需相应 L 层证据；Phase 2/3/4 的启用候选跑完整十四场景。文档 PR 只做文档检查。

L 层由维护者在独立环境执行，不给普通不受信 PR runner 注入真实模型和环境凭据。远端操作沿用 SSH MCP 和 release runbook；进入部署/启动前核对授权、测试栈、资源配额与隔离。若专用自动化尚未建成，保留明确的运行验收缺口，不能制造绿色 check。

### 4.3 十四个场景及故障点

表中 C/L 是目标验收层，不是已有通过声明。测试使用屏障、受控代理或独立进程故障注入，不用仅靠 sleep 的时序猜测，也不增加可被普通请求开启的生产故障接口。

| ID | 场景/精确注入点 | 必须观察的结果 | 层 |
|---|---|---|---|
| R01 | 正常任务；无附件和有选定附件两变体 | 一个业务结果；答案可回读、附件按选定顺序下载且摘要一致；会话覆盖成立，资源最终 released | C/L |
| R02 | queued、执行中、回执后/结算前分别取消；再加入 stop 失败 | 准入/取消优先级符合现行合同；无新扩权；合法尾部追加边界保持；重复取消幂等；失败 stop 留责任，确认停止后才 released | C/L |
| R03 | 活跃长工具、心跳中断和执行总时限分开注入 | 不以浏览器/队列 ACK 判失主；单次超时不制造无界替代工作；停止未知不报已释放，未知效果不自动重跑 | C/L |
| R04 | 杀死实际容器；另注入 provider 暂时不可达 | 权威不存在与探测失败分开；已存在终态回执优先进入结算；旧资源结果不能污染后继 Attempt | C/L |
| R05 | 丢终态 callback，保留执行器状态接口 | 探测精确任务生成一次合法回执并结算；探测不可达时不编造成功/不存在，迟到回调按冲突规则处理 | C/L |
| R06 | 同 batch/终态重复，内容冲突，旧 generation 三变体 | 相同事实幂等且 ACK 可重复；冲突拒绝，旧 owner 写入失败；终态、答案、产物不重复 | C/L |
| R07 | Worker 已交付、退出前后 kill/restart | 原任务继续由合法执行者或接收事实恢复；调度 Worker 消失不导致直接重跑工具；认领可恢复 | C/L |
| R08 | 选定文件收集失败、对象服务短暂失败；原执行结果分别成功/失败/取消 | 按 owning 合同区分永久交付失败与可重试结算；原失败/取消根因保留；依赖恢复后结算，无未选定文件泄露 | C/L |
| R09 | PG 接受后尚未 enqueue 时 API 崩溃；Redis 已写但 enqueue ACK 丢失 | 无客户端重试也可恢复同一 Run；明确拒绝与未知分开；不重复消息/Run/执行授权 | C/L |
| R10 | Executor 已接受，dispatch HTTP ACK 丢失；accepted 记录提交失败 | 先核对同一 dispatch/resource；不得当作未执行再启动；恢复仍指向原 Attempt，未知效果禁止盲重试 | C/L |
| R11 | 终态 receipt 在 accepted/context 记录之前提交 | 后来的 accepted 保留终态，补全可结算条件并唤醒；不能重置 claim/outcome；丢通知仍能扫描推进 | C/L |
| R12 | 对象 put 后业务事务回滚；另做 commit 成功但响应丢失 | 前者不出现部分 assistant/coverage/公开产物且临时对象可补偿；后者按原事实读回，不误删已接纳对象 | C/L |
| R13 | 业务提交后 Redis/通知持续失败且浏览器离线 | Run 结果不回滚、不重跑；资源仍可释放；恢复访问后历史/附件一致，同一 Run 不追加第二条答案 | C/L |
| R14 | 同 Session 连续两轮，换沙箱；再注入 transcript 缺失及当前权限变化 | 新 Run/Attempt 采用精确 native_resume；当前授权重新生效；dirty/missing 不静默 empty_start，不沿用旧凭据/工具权限 | C/L |

R02/R06/R07/R10 的并发变体至少使用两个控制进程。每次保留阶段快照：submission/调度意图、Run、Attempt 与 generation、执行接受/终态回执、结算 claim、资源/cleanup、答案/产物/coverage、队列与流观察。输出仅合成身份和受控诊断；不导出凭据或私有原生正文。

“基线建立”允许发现失败；“迁移准入”要求受影响不变量已通过。已知失败必须有 owner、根因范围及处置，不能只建 issue 后继续破坏同一边界。修复该失败的独立节点可以从红到绿推进。环境不足单列未验证，不能用 xfail/skip 降低门槛。

## 5. 节点依赖和交付要求

每节点可由一个或多个有明确闭环的小 PR 完成。表述中的 owner 是代码责任方；实际执行人和评审人在实施 PR 填写。所有节点都保留公共投影、租户边界及现行 mandatory checks。

依赖主线：

~~~text
N00 -> N01 -> N02 -> N03
  -> N10 -> N11
  -> N12、N13（共享文件顺序集成）
  -> N20 -> N21
  -> N22 -> N23
N21 + N23 + N12 + N13 -> N30 -> N31 -> N32 -> N33
N31 + N33 -> N40 -> N41 -> N42 -> N43
N40 -> U1（可选展示，不阻塞核心迁移）
~~~

Phase 1 各节点在其相关回归和既有前置具备后可独立交付；不会因为其他无关场景缺环境而自动批准，也不要求等待无关工作。Phase 2 起依赖 ADR 正式评审及本节点 Change Contract。并行仅适用于不重叠的文件/责任，不能产生两个同域 writer。

### Phase 0：建立可复现的基线

**N00：全链路事实与操作清单 — Runs/Execution 主责，Context/Sandbox/Streaming/Artifacts 复核。**

- 产出：沿第 2 节补全实际入口、完整调用者、持久表/字段、授权/claim、事务与外部 I/O、现有 deadline/配置及恢复责任；核对现有 Harness 前置的当前证据。
- 验收：十四场景每个故障点均有负责处理的 owner；无 Attempt、无 lease、accepted 未确认、回执已到、结果已提交、资源未释放六类窗口均能定位；明确 API/Worker/reconciler 的全部终态调用者。
- 边界/回退：只读库存和文档；不将目标表/接口描述为已实现。不改原始历史方案或业务数据。

**N01：控制链与真实链测试夹具 — 测试/Execution/Sandbox owner。依赖 N00。**

- 产出：实际 API/Worker/callback 子进程、独立 schema/Redis 实例及 prefix/bucket/资源命名，精确故障屏障、进程 restart 和清理夹具；分别建立 C 与 L 入口。
- 验收：R01 在两层分别跑通；夹具能证明 kill 的是目标进程、丢的是指定 ACK；失败后只清理本测试资源。未完成 provider I/O 仍有 owner，不由测试 teardown 掩盖泄漏。
- 边界/回退：C 层替身只替换声明的模型/执行器边界；PG/Redis/MinIO、真实传输及生产编排不可被 stub 掉。回退夹具不影响应用；新测试文件按现有单一 selector owner 纳入。

**N02：十四场景基线与缺陷闭环 — 各 owning regression owner。依赖 N01。**

- 产出：R01–R14 的固定输入、故障点、预期中间事实与最终读回；记录当前失败、局部替身、未验证环境。补测与基线缺陷修复分开说明。
- 验收：所有适用不变量能被断言；至少用旧 owner 放行、重复终态写入、通知失败阻塞清理三种受控破坏验证测试会失败，再恢复。按既有 Harness 规则提供适用的完整范围证据，不以局部通过替代。
- 边界/回退：不得先修改结果预期来掩盖基线缺陷；缺陷修复必须有对应红到绿回归。基线建立不自动授予后续迁移准入。

**N03：接入门禁与候选证据 — CI/测试 owner。依赖 N02 及 CI 独立审查。**

- 产出：C 层套件纳入现有 backend-tests/required，强制零跳过；L 层候选构建、隔离运行、证据读取流程，并标明触发边界和未就绪项。
- 验收：漏 selector、依赖缺失、skip、场景失败、错误 source/image 的负例不能返回通过；成功报告绑定精确源码/锁文件/镜像、实际依赖版本及安全 profile。
- 回退：只能撤回本节点新增 job/入口，保留原 required；不能用回滚配置声称已获得 L 层证据。真实环境自动化缺失时保留明确人工验收入口与待验收状态。
- 2026-10-24 决定：仓库要求每个测试文件由某个分片拥有，`backend required` 依赖全部分片，C 层 `lifecycle-control-chain` 分片随 C 层测试 PR 接入即成为必过检查，不为其修改 CI 所有权合同。依据：首个候选在 CI 中 8 个场景全部通过、零跳过，确定性 runner 不调用真实模型，分片约 7 分钟、与现有分片并行。附带条件：接入后前 20 次 CI 运行中出现与代码无关的失败按最高优先级修复，不以重跑掩盖；本节点其余验收（受控破坏验证、门禁负例、证据绑定）照常完成，接入必过不等于 N03 完成。

### Phase 1：分开处理定义、约束与写入责任

**N10：Run 词表及历史读取归一化 — Runs domain owner。依赖相关 N02/N03 证据。**

- 范围：[terminalization](../../app/runs/domain/terminalization.py)、[public_terminal](../../app/runs/domain/public_terminal.py)、[retry](../../app/runs/domain/retry.py)、[submission_resolution](../../app/conversations/application/submission_resolution.py) 及实际 consumers。
- 产出：按用途定义唯一 Run 分类与公开归一化入口；显式记录 dead_letter 在各操作中的含义。Attempt、执行器、SDK 协议各自保留词表，不把不同集合强行合并。
- 验收：六处候选集合逐一确认语义后才合并；历史别名读回和 retry 资格不变，所有当前生产者发规范值；未知值不能变成功。R01/R02/R06/R09/R14 与旧历史 fixtures 通过。
- 回退：仅源码等价变更可 revert；本节点不重写历史数据，不删除外部/持久协议别名。

**N11：runs.status 数据审计与数据库约束 — Runs persistence/schema owner。依赖 N10。**

- 范围：[schema.sql](../../app/schema.sql)、[schema_migrations](../../app/schema_migrations.py)、readiness 与 owning migration tests；风险按 schema 变更处理。
- 产出：存量值/NULL/未知值审计，约束允许集合及数据处置；明确是否允许旧写者。若需要改历史行，单列迁移，不猜测未知值；设计受控锁预算与失败退出。
- 验收：空库、历史别名、非法值、重复升级、错误同名约束、旧写者以及 readiness 检查在真实 PG 中验证；新 writer 与触发器均可合法写入，非法值被拒。审计失败不得安装后继续启动。
- 回退：只回到明确支持约束/数据格式的版本；已规范化历史不靠 down migration 猜回。保留数据，执行 release runbook 的 schema 回退要求。

**N12：终态组合服务收敛 — Runs application + Context/Streaming owner。依赖 N10 及事务回归。**

- 范围：Runs application 的 `run_terminalization` 组合服务、bootstrap/run_lifecycle、Worker completion、取消事件 writer 及全部调用者；不改变终态胜出规则。
- 产出：一次完整终态提交组合，明确 Attempt、lineage、terminal v4、audit 的唯一执行位置；迁移取消路径已有独立 append_terminal，防止漏写或双写。
- 验收：正常、前置失败、无 Attempt 失败、取消竞争、重复完成、CAS 丢失和事务回滚分别通过；同一 Run 终态/答案/产物不重复；commit 后 Redis 失败仍可读取。
- 退役/回退：删除被替代 wrapper 及仅服务它的装配/测试；保留真正不同的 provider coverage 逻辑。无 schema/wire 变化时可源码回退，不恢复两套活跃组合。

**N13：Sandbox 租约单一写入责任 — Sandbox application/infrastructure owner。依赖 N00、相关事务/清理回归。**

- 范围：sandbox/infrastructure/leases_postgres、sandbox/infrastructure/executor_reconciliation_postgres、Sandbox public API、bootstrap 与 renew/release/callback/reconciler consumers；`platform/postgres/sandbox_leases.py` 已删除。
- 产出：写入策略归 Sandbox 应用能力，SQL 留其 infrastructure；platform 只保留通用连接能力。逐个保持 caller connection、Run-before-Attempt 锁序及真实资源 stop-before-release。
- 验收：R02/R05/R06/R07/R11/R13；公开租约 scope、续租过期规则、claim、失败 stop 责任和审计保持；架构检查拒绝 peer 直接使用新私有 repository。
- 退役/回退：旧 writer 被迁移后删除；不得复制 SQL 保留两个活跃写方。单独迁移模块所有权，不在本节点改表名、状态图、provider I/O 锁策略。

### Phase 2：明确事件接收与投递交接

**N20：统一身份接收框架，保留类型化处理 — Sandbox + Context/Streaming owner。依赖 ADR 评审、N12/N13。**

- 产出：沿现有 callback authority 整合接收框架；心跳、进度、答案 batch、SessionStore append、terminal 分类型分预算、幂等键和冲突规则。不是把所有消息塞进公开 run_events。
- 验收：R02/R05/R06/R11/R14；保持现有 HTTP/ACK 语义、大小/顺序约束与取消尾部限定。普通执行回调不能借用 append 尾部权限。
- 回退/退役：优先保持现有 URL/wire 与已部署 producer；确需版本变化则先写兼容矩阵。禁止顺手把 callback ACK 改成仅数据库持久化确认，或恢复 SSE 发布 outbox。

**N21：执行交付意图与接受事实 — Execution + Sandbox owner。依赖 N20。**

- 产出：在原 Run/Attempt/资源范围记录 dispatch 意图及稳定身份，明确拒绝/接受/未知的证据；accepted 与 terminal-first 的合并规则，不创建第二个业务状态机。
- 验收：R07/R10/R11；远端已启动但 ACK/本地 commit 丢失时按原 dispatch 核对，不能重启执行；早到回执不可被 accepted 覆盖。provider 创建成功而 lease commit 未确认的补偿也有定位。
- 回退：未知交付必须由能理解该记录的版本恢复；先排空或交接再关闭新 reader。持久记录本身不证明外部执行 exactly-once。

**N22：持久投递记录与历史映射 — Conversations/Runs 事实 owner，Execution 调度 owner。依赖 N00、ADR 评审。**

- 产出：优先复用 submission/Run 可重建事实；只有现有字段不足才增加 outbox。覆盖 keyed/unkeyed Chat、直接 Run、retry/resume/copy 的已接受任务；首次记录不要求 Attempt 已存在。
- 验收：原 Run 与调度意图同事务成立；重复提交不新建事实；保存原消息身份/版本，不能从最新 Profile 重建旧意图；撤权/取消/已有执行时不能授予再执行权。启用持久投递时先映射缺少 intent 的 queued 历史 Run：只从其原 `input_json`、context snapshot、Chat submission 或 Run-control operation 证明身份；存在缺失、冲突或无法证明的记录时 API 启动失败，要求先作有限处置，不留下永久 pending。
- 回退：schema 先展开，writer 默认不启用；保留已写意图及历史读取。不得同时建独立 submission 状态账本或 SSE publication outbox。

**N23：投递恢复与至少一次传输 — Execution owner。依赖 N22 及 R09/claim 并发保护。**

- 产出：短事务 scoped claim -> 原身份投递/读回 -> claim-fenced receipt；持久 next_retry、公平扫描、进展指标；协调现行 stale-run/fence，不提前删除它们。
- 验收：API commit 后崩溃无需浏览器可恢复；Redis 已写未记账、双 scanner+HTTP retry、旧 I/O 晚到、撤权、取消与 stale 回收竞争均不重复 Run/工具执行；只重试投递。响应与紧随的 readback 都暂时失败但 Attempt 已创建时，scanner 只能用原 payload/message identity 做 readback，不能再次 enqueue；无真实队列 receipt 时保持 pending 并退避，不可伪造完成。receipt-owner 事务失败时须回滚 intent 与 Chat 投影，旧 claim 保持 claim-fenced 至过期后由 scanner 重试；receipt 与 Chat queued/queue identity 同事务提交。过期 claim 可由 scanner 接管，旧 claim 仍不能写入；取消且无 Attempt 的 Run 不得进入队列 I/O。
- 回退：先停止领取并处理在途命令，再由旧兼容路径消费未完成意图；关闭扫描器不等于 Redis 请求已撤销。保留原 queue owner token/CAS。

N22/N23 历史恢复使用独立 enable 前 preflight/backfill；回填不重新读取 Profile、不创建新 Attempt，也不以新队列 identity 覆盖已存在 Redis receipt。Run status/schema 版本 `2026.10.25.1`、delivery intent `2026.10.25.2` 和 Sandbox dispatch `2026.10.25.3` 各自保留。

### Phase 3：结算投影与监督迁移

**N30：Attempt 结算数据设计及展开 — Runs 主责，Sandbox/Context/Artifacts 共同评审。依赖 N12/N13/N21/N23。**

- 产出：逐字段定义 receipt、dispatch、结算 phase/claim/retry/result 与资源字段的归属；选择 Attempt 上字段或所属明细表，仍只有原 Attempt ledger；schema/readiness/index 同步展开。
- 验收：新增普通结算进度不调用执行状态 transition，不改变现有 generation；running、取消、writer generation、native coverage 的前置保持；资源 stop/release claim 不迁成执行权。
- 回退：新增字段只作影子且无业务读者时可停写；保留已保存字段。若确需改变执行状态/原生会话条件，先追加明确设计、兼容和负例，不能在此顺手放宽。

**N31：影子写入、历史回填与语义比对 — Runs/Sandbox persistence owner。依赖 N30。**

- 产出：现有 writer 仍唯一权威，在同一受控事务生成新投影；shadow 不调度、不发事件、不决定终态。历史数据按精确 Attempt 映射，含 released lease 的未结算回执及 cleanup_pending/failed。
- 验收：R01–R14、双控制进程、一次进程重启、至少覆盖最长适用 lease/claim/retry 周期的两次到期扫描；可比记录逐项一致且覆盖数明确。未知/冲突记录不得从分母隐去；阻止其切读并指定读取/修复责任。
- 比对：身份、原回执摘要、accepted 合并、结算含义、取消/generation、next-retry 和 cleanup 归属；不要求不同表示的时间戳字节相等。影子写失败造成的事务影响也必须测量。
- 回退：停止 shadow 写；旧事实未被修改。回填不执行旧任务、不重写 Spec 或原 terminal receipt。

当前 N30/N31 投影切片（schema `2026.10.25.5`）为原 Attempt 添加一对一 settlement detail，并提供最多 800 条/页的显式回填与页级语义比对。回填只插入、不覆盖；缺失、unknown、conflict 在各页计数中保留。页级结果和稳定快照的计数/最高 key 均不证明全量语义一致，也不得授权切读。shadow writer 由 `RUN_ATTEMPT_SETTLEMENT_SHADOW_ENABLED` 显式启用，默认关闭；旧 Sandbox writer仍是唯一业务权威。

同事务 hook 与锁序：

| Hook | 受影子化的源字段 | 锁序/边界 |
|---|---|---|
| Attempt 创建、Sandbox lease 创建 | Attempt 状态/代际快照、source lease identity | lease shadow 在插入前按 Run → lease id → Attempt 获取源锁；Attempt 创建仍遵守现有 Run owner |
| Sandbox accepted 与 terminal receipt、dispatch intent | Dispatch intent recovery context、accepted context、receipt identity/content/digest/generation、phase/attempt count | writer 在改源行前按 Run → lease id → Attempt 锁定；intent recovery context 与 accepted semantics 分开，detail 经 Runs API 写入 |
| terminal reconciliation/probe claim 与 retry | 仅 terminal reconciliation 投影 claim/token/time、retry clock/count；无回执的 probe claim 不投影 settlement token/phase | 先读有界候选，再按 Attempt key 排序锁 Run → lease id → Attempt，最后按候选 lease ID 执行 claim 并刷新 detail；单条 retry 在更新前取得相同源锁序 |
| Run/Attempt cancel、stale recovery 与 terminalize | Run/Attempt 状态、结果、取消与 owner-generation 快照 | 先取得 Run → lease id → Attempt 源锁，再调用 lifecycle persistence 并刷新 detail；detail 写入本身不迁移 Attempt、不增加 generation |
| executor heartbeat、cleanup claim、lease release | heartbeat 时间与 Sandbox 资源/清理事实 | 不写 settlement detail；清理状态由 Sandbox lease 保持权威，比较时仍纳入全分母 cleanup 统计 |
| cleanup pending、cleanup finalize / reconciliation failed | 仅当底层 reconciliation status 改变 settlement phase 时刷新；cleanup claim/retry 字段仍不投影 | 更新前由原 Sandbox writer持有其 lease；hook 只读 source facts 并写 detail，不再请求 Run/Attempt 锁 |

调用方事务中的 shadow SQL 失败会使源写与 detail 一起回滚。页级 compare 保持 page scope。`compare_snapshot` 只在 caller transaction 为 `REPEATABLE READ` 或 `SERIALIZABLE` 时扫描全部分页，提供观测结果，不提供切换证明。未来 N40 必须在新事务重新执行全量语义比对，并在切换提交前 fence `runs`、`run_attempts`、`sandbox_leases` 和 `sandbox_execution_dispatches` 的相关 writer；仅校验 Attempt 数量或最高 key 不能证明投影内容未改变。

**N32：统一监督调度的只读决策阶段 — Execution scheduler + 各决策 owner。依赖 N21/N23/N31。**

- 产出：逐类 deadline、时钟来源、候选索引/上限、互斥规则、外部 I/O 预算和恢复 action。复用 Worker 生命周期，不新增独立进程部署前提；结算与 bulk cleanup 有独立容量。
- 验收：影子监督输出候选动作但不执行；与当前 owner 比对。已有 terminal receipt 不产生 lost；队列 ACK 后旧队列 expiry 不产生误杀；取消竞争按现行优先级裁决。
- 可测预算：到期候选在两次正常扫描周期内被观察；积压至少超过一个 batch，验证老记录/到期 retry 不饥饿、活动 I/O 不超过已配置槽位。异常依赖期间记录无进展而非伪造完成。
- 回退：关闭影子监督，不影响当前路径。时间阈值沿用已验证配置；调整阈值是独立行为变更。

**N33：逐责任域切换监督执行 — Execution/Runs/Sandbox owner。依赖 N32 和相应 L 层验收。**

- 顺序：先接管已验证的投递补偿调度，再处理派发/执行丢失与取消宽限，最后整合结算唤醒、资源/孤儿维护调度。每个责任域单独切片；不是一次替换全部循环。
- 切换：停止旧域领取、确认或交接其在途 claim、启用新域；多副本通过 PG claim/CAS 工作。候选选择和最终写入复核一致，不能靠每进程布尔开关保证 fleet 互斥。
- 验收：R02–R07/R09–R13，双副本和进程崩溃；旧处理者迟到写回拒绝，远端在途 stop 用精确资源身份/幂等保障。长清理不阻塞结算，永久/暂时错误及取消权限语义不变。
- 回退：仅在旧版本能读取当前事实且已排空/交接新 claim 后切回，保留新持久记录；无法证明这一点则停止启用并修复，不同时开启两套裁决。

当前监督基础切片（schema `2026.10.25.7`）新增 `execution_supervision_domains` 与在途 claim 记录，四个责任域均初始化为 `legacy`。Worker maintenance 读取持久候选并按既有 deadline/owner 预算分类，只记录 typed candidate，不调用候选动作；delivery 依照实际 store 资格区分 enqueue recovery、Attempt receipt readback 与 suppression，不读取旧队列 lease expiry；probe 使用取消与普通探测各自的公平游标，并各取配置的每 lane 扫描预算，执行 capacity 独立。现有 delivery、probe、settlement、sandbox cleanup 扫描无论本地 `execution_supervision_enabled` 开关状态，都会先读取持久域状态并登记 PostgreSQL domain claim；默认 `legacy` 仍调用原 action owner，`draining` 阻止新 claim，激活要求旧 claim 全部显式排空，claim 过期本身不会释放在途槽位，版本变更后旧 claim 不再 current。该开关只控制本副本是否可在 `unified` 模式执行；schema 不可用时 Worker readiness/startup 失败关闭。四域仍默认 legacy，shadow 开关默认关闭；代码接线不构成真实环境验收，也不授权生产切换。

N33 执行入口使用现有 Worker maintenance 和 executor reconciler 循环，没有增加部署进程或第二份裁决逻辑。每个责任域继续使用自身的批量上限、I/O 超时、轮询/唤醒路径和容量；统一 action 的活动调用在结束前保留 PG claim，调用方取消时先等待 action 收尾再释放。双副本 PostgreSQL 状态迁移回归覆盖单域 `legacy → draining → unified → draining → legacy`，包括旧模式拒绝、在途容量占用及关闭后恢复。该测试是本地 PG 证据，不代表真实 Worker/Redis/Docker 或生产 fleet 的外部验收；如果真实 PG 零跳过检查未执行或失败，须保留为切片退出项。claim 过期不能自动释放在途槽位；恢复只能由人工输入精确 fence 与 stop 证据，平台记录输入但不自动验真，也不把 domain TTL 解释为外部 exactly-once。

### Phase 4：切读、停写与退役

**N40：结算读取切换到 Attempt 所属数据 — Runs/Execution owner。依赖 N31/N33。**

- 产出：按明确的存储版本/迁移标记选择 reader；回调、恢复、finalizer、诊断查询使用同一映射。不能按“新字段为空就随便读旧字段”掩盖数据缺失。
- 验收：十四场景与历史已释放但仍待结算记录；Spec、claim、答案/产物/coverage 与 Run/Attempt 仍原子结算，暂时依赖故障重试不重复结果；不改变 commit_provider_turn 的授权条件。
- 回退：旧 writer 尚保持兼容投影且差异为零时才能切回旧 reader；不能回滚到忽略新 receipt/claim 的程序。

**N41：停止旧列写入并确认全部消费者迁出 — Runs/Sandbox owner。依赖 N40。**

- 产出：全部活跃 writer/readers、动态导入、导出、监控、脚本、测试、schema/index/readiness 清单；旧列停止写入，必要历史读取明确 owner 与退出条件。
- 验收：进程/fleet 清单证明旧 writer 不再运行；在途 dispatch、waiting_terminal、claimed/retry、cleanup_pending/failed 都有当前 owner。观察覆盖相关最长操作生命周期和一次重启，不能仅以“最近无写日志”为证。
- 回退：停写后新事实不再有完整旧投影，不能无条件切回；先用经过验证的重建/排空程序建立兼容，或仅前向修复。保留数据列到 N43。

**N42：退役 Redis 业务对账 fence — Execution queue owner + Runs reviewer。依赖 N23/N33/N40/N41。**

- 产出：逐键列出旧业务裁决、替代 PG 事实/检查点、生产者、读者、TTL 和删除条件；保留 queue delivery owner token、原子 ACK/重投/限流以及 SSE Stream 的独立控制。
- 验收：旧 lease/v1-v2 消费矩阵、PG commit 后投递前崩溃、重复/迟到 enqueue、旧 reclaim 与新接管竞争；当前执行权只能由持久 authority 授予。R03/R07/R09/R10 及真实 PG/Redis 竞态必过。
- 回退/退役：先停用已替代逻辑再处理旧键；只清理明确测试/部署命名空间的目标键，不作 Redis 通配删除。旧版本依赖已删 fence 时不属于可回滚版本。

**N43：删除旧执行/对账列并完成退役 — schema/Sandbox/Runs owner。依赖 N41/N42。**

- 产出：逐列处置，保留资源 lifecycle/stop/release 字段；包含历史 receipt、context、claims、attempt counters、retry time、诊断与索引的去向。更新 schema、readiness、历史读取和所有 owning 文档。
- 准入：无旧 writer、无仍依赖旧列的 reader；所有未完成责任已迁移/排空，无法映射的历史记录有经评审的保留载体；备份恢复演练和最低兼容镜像已验证。
- 验收：旧库升级、历史读取、重复升级、新数据写入与十四场景；生产旧入口/SQL 引用清单归零，历史 ADR/迁移 SQL/禁止回退测试的命中分类保留。完整运行清理与下载/续接验收闭合。
- 回退：这是破坏性 schema 变更，不支持简单二进制回退；使用已演练的停写、备份恢复或前向修复程序，并声明会影响哪些新数据。不要自动执行 destructive down migration。

### U1：可选的“正在整理结果”展示

依赖 N40 的稳定持久进度；由 Runs 公开投影、Streaming 和前端 owner 共同完成。先复用现有 phase 能力；如涉及新字段/事件，按现有 schema、validator、history、hydration、renderer 合同一起演进。保持 queued/running/terminal 状态枚举不变。

验收：阶段来自当前 Run/Attempt 的合法持久进度；断线刷新仍一致；永久失败覆盖该阶段；等待结算不显示假成功或假百分比。已完成但下载失败是读取错误，不能把 Run 改回 finalizing。不显示 SDK/claim/私有错误内容。此节点可单独回退展示，不改变结算，也不阻塞 N41–N43。

## 6. 每节点的验收记录和停止条件

实施 PR 用现有模板记录以下内容，不另建长期状态账本：

- 节点及精确 base/head；变更前后一个可观察行为；实际 owner/评审人。
- 完整调用者、持久字段、事务与外部 I/O；保留/删除/兼容的 production、tests/selectors、docs/config 清单。
- 实际执行的命令、场景 ID、数据范围、环境、通过/失败/skip；代码、CI、镜像、运行和浏览器证据分别标明。
- 每个不变量的直接观察：例如终态行/事件计数、产物摘要、原生序列覆盖、旧 owner 拒绝、stop 结果和 released 写入顺序。队列消息数量不是外部效果 exactly-once 证明。
- 最低兼容版本、停止新领取/排空/交接步骤、历史数据读取、恢复演练及不能回退的边界。

出现跨作用域写入、旧 owner 获权、重复副作用、回执被覆盖、原生会话静默重置、未确认 stop 却释放、部分业务事务提交或缺少必要真实依赖证据时，停止受影响节点的启用。按 owning regression 修复或明确处置；不弱化门禁、删断言或用影子数据替代权威。

以下是已有工具的调用形状，不表示本次执行过运行回归；新增 selectors 在 N01/N03 落地后填入实际名称：

~~~bash
python tools/run_test_stage.py --stage lifecycle-owner-regression \
  --timeout-seconds 600 --require-zero-skips -- \
  tests/test_worker_heartbeat_postgres_redis_integration.py \
  tests/test_frozen_receipt_spec_postgres.py
~~~

该示例只证明所选两个边界，不能标为十四场景通过。运行需配置隔离的真实依赖，连接凭据只由环境提供，不进入文档或结果日志。部署与远端实际执行继续由 [release runbook](../operations/release-operations-runbook.md) 管理。

当前实施单元为 N00–N01：已补当前入口/故障窗口/预算库存，C 层连接准入、Worker、结算、产物、回收，并加入 Worker 重启与 ACK 丢失故障注入。实际命令结果以 runner evidence 和实施 PR 为准；L 层按维护者安排留待真实环境。后续由 N02 的事实基线决定修复与迁移准入，不能因为局部测试通过就宣布 Phase 0 或 ADR 验收完成。
