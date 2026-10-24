# CI 本地观测记录（2026-10-13）

Status: archived observations from the stopped Harness migration, moved from `docs/CI.md`. Results bind to `f519d041` and the Windows environment of that session; they are not current CI evidence.

## 4.5 Windows 深路径与已观测的本地门禁

Git for Windows 在深层 pytest fixture 中会先把 `SHA:path` 作为歧义路径
做 stat；262 字符的候选可报 `Filename too long`，再被 architecture 工具包装成
`git_object_missing`。本轮用相同对象验证：命令级 `core.longpaths=true`、
显式 `git show SHA:path --` 或 `git cat-file blob SHA:path` 均能读出相同 blob。
测试使用下面的**进程级**配置，未改全局 Git、断言或 required checks：

```bash
GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.longpaths GIT_CONFIG_VALUE_0=true \
  "$harness_python" tools/run_test_stage.py \
  --stage harness-gates --timeout-seconds 300 --require-zero-skips \
  -- tests/test_code_governance.py tests/test_architecture_governance.py
```

2026-10-13 北京时间实际结果为 **173 passed、0 skipped**；固定输入是 f519d041，
含 candidate 自放宽/authority/source 身份、越层、冻结增长、动态 import 与 hard/advisory
反例。首次未配置 Windows longpaths 的检查失败，且外层 60 秒中断；该次不算成功。
先复现最小失败并核对对象，再重跑完整检查；不要改变门禁来适应错误的环境诊断。

其他本轮已观测检查：

| 范围 | 实际结果 | 证明范围 |
|---|---|---|
| Runs replay、Sandbox shared callback、ProfileDrive 生命周期、Runs persistence | 104 passed，0 skipped | owning 单元/注入边界，非真实 PG 并发或部署 |
| 源码测量与 source-authority 文档合同 | 21 passed（B1 文档交付后） | 5 个测量反例/边界 + 16 个现有文档合同 |
| Python 依赖 OSV 查询 | 85 包、0 已知漏洞、0 skipped | 当前 Windows test 环境；命令和原始 JSON 在 QUALITY_SCORE |

[QUALITY_SCORE](QUALITY_SCORE.md) 给出完整静态图、逐域/层分母及未测字段。
T9 文档自动负例/变更触发园丁与 T10 coverage 采集设施已在 B2 候选落实（§4.6）；
独立新文件大小/coverage 阈值、完整范围采样和 hook 处置仍按计划 §5.1 交付。
以上结果不能替代这些待验收项。runner 继续移除 PYTEST_*，新采集只由显式
`--coverage` 控制，不接受裸 pytest flags 或环境变量注入。

## 4.6 B2 文档门禁与显式 coverage

Backend preflight 新增第一方文档检查，从完整 PR base SHA / push-before SHA
解析与HEAD的共同祖先后比较该candidate改动；main-only新文档不归到落后分支。
保留原 required 聚合、source 验证与 trusted governance。
默认本地检查未提交和未跟踪文档；命令为：

```bash
"$harness_python" tools/check_documentation.py
```

检查 actual Markdown 链接/图片、heading anchor（含 Unicode、重复标题和显式 HTML id）。
文档状态与导航由作者负责，不要求固定 Status/状态 字段、行号或索引条目。代码示例不是链接，历史资料与完整母本会
明确列为 excluded。坏链接/坏 anchor/越出仓库、clean checkout
比较范围的合成反例由 tests/test_check_documentation.py 保护。
删除/移动目标仍须 owning caller inventory，外部网址不联网检查；本检查不证明文档
业务内容与运行状态相符。作者在文档变更时触发园丁修复，经正常 review 合并。

Coverage 的最小设施在 owning runner 内，依赖仅新增到 locked **test extra**：
pytest-cov 与 coverage；Python <=3.11 的 tomli 为平台标记下的传递依赖。
现有锁定包版本均未改变，生产依赖声明未添加 coverage。实际采样示例：

```bash
"$harness_python" tools/run_test_stage.py \
  --stage b2cov --timeout-seconds 180 --coverage --require-zero-skips \
  -- tests/test_runs_replay.py tests/test_sandbox_callback_authority.py \
     tests/test_profile_drive_transfer_lifecycle.py tests/test_runs_persistence.py \
     tests/test_authorized_skill_catalog.py tests/test_skill_worker_dispatch_authorization.py
```

本次 **132 passed、0 skipped**，报告分母核对覆盖全部 452 个 tracked app Python
文件，包括未导入 namespace。初次默认 namespace 分母漏两文件的报告和另一次长
stage 导致 WinError 206 的失败均不作为通过证据。最终 same-selector 采样的整数
分母与公式在 QUALITY_SCORE，原始产物仍归 runner 的 .pytest-tmp stage，不复制
第二套证据台账。

覆盖功能回归含控制环境/config 隔离、零执行文件、namespace、缺报告/非法计数/
不完整范围、源码变化，以及原有 failure/zero-skip/timeout/descendant cleanup。
本轮 B2 grouped regressions **88 passed、0 skipped**。未运行 GitHub Actions、
Linux/真实依赖全链，也未设置 coverage 阈值。配置/本地通过不等于独立 review
或可信 merge evidence；该 workflow 改动按既有高风险 delivery contract 审查。

## 4.7 完整范围库存与关键域补采

源码测量脚本新增 `workflow_test_inventory`，从四份 workflow 的 run/test_files
字段收集**字面**选择器，绑定文件摘要并区分完整文件和 named node、tracked 与
本轮新文件。它是库存，不是新的 required gate，也不声称间接/动态调用已被解析。
当前 candidate 为328 tracked + 2新测试文件；244有字面引用、86未出现、4文件
只有named-node引用、0不存在的字面目标。完整路径在生成JSON，不能把旧8分片
清单或这些数量直接称为全量验收。

已核实 test_run_lifecycle.py 的7个独立用例及 test_run_lifecycle_postgres.py
的6个recording-connection用例没有直接CI选择器，现加入既有run-control-contracts
分片并更新 owning workflow assertion。前者的MemoryPersistence/service被其他
用例复用，后者文件名不代表真实PG；本次没有据文件名删除/归类所有86个路径。

本地缺真实测试DSN、Redis URL和可用常用端口；Docker/WSL/PostgreSQL/Redis命令
不在PATH。实际必需集成探针：

```bash
"$harness_python" tools/run_test_stage.py \
  --stage required-deps --timeout-seconds 120 --require-zero-skips \
  -- tests/test_sandbox_persistence.py::test_executor_acceptance_terminal_order_converges_in_postgres \
     tests/test_streaming_v4_redis_integration.py::test_real_redis_rejects_v4_append_to_legacy_v3_phase_without_mutation
```

结果为7 skipped、退出码6、required_dependency_missing，**未通过**。不能用fake
provider、本组mock连接或去掉zero-skip来补全该证据；本轮没有远程检查/容器部署。

可完成的关键域补采保留前六文件并加入六个现行用例入口：

```bash
"$harness_python" tools/run_test_stage.py \
  --stage corecov --timeout-seconds 180 --coverage --require-zero-skips \
  -- tests/test_runs_replay.py tests/test_sandbox_callback_authority.py \
     tests/test_profile_drive_transfer_lifecycle.py tests/test_runs_persistence.py \
     tests/test_authorized_skill_catalog.py tests/test_skill_worker_dispatch_authorization.py \
     tests/test_run_lifecycle.py tests/test_run_lifecycle_postgres.py \
     tests/test_run_attempt_application.py tests/test_run_attempt_repository.py \
     tests/test_runs_worker_locked_authorization.py tests/test_runs_worker_dispatch_admission.py
```

结果174 passed、0 skipped，452 app源文件分母；样本语句26.07%、分支7.97%，
Runs样本语句48.66%、分支18.98%。这是更大的unit/contract样本，仍非全部关键域
或完整suite。CI/库存本批39 owning回归通过；D5须在86路径/named-node处置和真实
依赖/平台验收完成后确定，当前不设置假阈值。

## 5. 变更历史

| 日期 | 变更 | 原因 |
|---|---|---|
| 2026-10-10 | 初版：框架判定 + 配置抽样 | 阶段 1 草案 |
| 2026-10-13 | 修正 stage/selector、base/head/authority/root 必需参数和执行目录；区分配置与已观测结果 | 审查 2、3 处置；全文要求见计划 §5.1 |
| 2026-10-13 | 固定 harnessing/f519d041 基线，记录 173 个治理与 104 个边界用例、OSV 范围及 Windows longpaths 前置 | B1/B2 核验；未把 T9/T10 pending 项当完成 |
| 2026-10-13 | 新增 changed-document preflight 与 owning runner 显式 coverage；记录 88 个设施回归及 132 用例有界采样 | B2 候选；全量/关键域阈值与独立 review 保持待验收 |
| 2026-10-13 | 补机器可复算workflow/test范围、两个Runs生命周期CI入口和174用例补采；实测required依赖缺失 | B2完整范围前置核验；不将86待处置路径或7 skip静默豁免 |
