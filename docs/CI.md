# CI 配置决定

Status: active CI navigation and dated local observations; not deployment evidence.

## 1. 框架判定

- 判定结果：**其他类型**（非 Tauri）
- 判定证据：无 `src-tauri/` 目录；根无 `package.json`；无 `@tauri-apps/*` 依赖
- 判定日期：2026-10-10；2026-10-13（北京时间）修订核对
- 源码核对：起始 `origin/main f519d041f1bb155a788278abfd5e07becebe1767`，独立 `harnessing` 工作区；本页是配置导航/执行示例，不证明当前 GitHub 检查或部署已经通过

## 2. 现有 CI 现状（存量项目必填）

| 文件 | 触发条件 | 覆盖平台/内容 | 是否自动发布 | 备注 |
|---|---|---|---|---|
| ai-platform-backend.yml | PR→main、push main、workflow_dispatch | Linux 8 个完整文件分片 + Windows runtime 节点分片；preflight/compileall/SSE cutover + Agent/Skill 契约 + 镜像构建 + Trivy + 启动健康 + required 聚合 | 否 | PR 时按影响范围构建镜像；main push 不构建，镜像发布由 packaging 手动触发 |
| ai-platform-frontend.yml | PR→main、push main、workflow_dispatch | Windows 4 个 JS 测试分片、独立 Python 合同/投影审计、lint/build/trace + Linux 镜像/Trivy/provenance/启动健康 + required 聚合 | 否 | 同上 |
| ai-platform-packaging-publish.yml | 仅 dispatch（main，confirm_release=PUBLISH_MAIN）；合并 main 不触发 | 不可变镜像 + SBOM + cosign 签名/验证 + manifest + Compose 包 + 可选版本化 Release | 是（受控） | 无 PR 触发 |
| ai-platform-trusted-governance-v2.yml | pull_request_target（opened/synchronize/reopened/edited） | 受信基线 + PR head 验证 + Ruff + code_governance + architecture_governance | 否 | governance 是 authority object（candidate 不可放宽自身结果） |

前端 JS 分片直接执行 `package.json` 中既有的 14 个测试阶段，每个 Runner 保持 `--test-concurrency=2` 和原有进程隔离；`test:sse` 的第二次 Runner 同样保留。分片按 [main 基线运行](https://github.com/demonsxxxxxx/ai-platform/actions/runs/39191073843) 的阶段耗时均衡，纯测试历史耗时约为每片 161–164 秒，不含环境准备或排队。实际提速以新候选的 CI 结果为准。

Python 合同/投影审计、JS 分片、lint/build/trace 和镜像并行启动；构建 Job 的 Python 依赖仅供产物来源记录使用。`frontend required` 必须接收四类 Job 的成功结果，失败、取消、跳过或缺失均阻断；矩阵不因其他分片失败而取消剩余分片。`tests/test_frontend_ci_shards.py` 校验分片与完整本地 `ci:verify` 阶段清单完全一致，无遗漏或重复分配；原有跨阶段文件重复保持原状。`ci:verify` 仍是本地完整串行复现入口，不新增测试运行器，也不复制测试文件选择器。

配置覆盖 lint/test/build、镜像扫描及发布来源验证。源码中有这些 job 不等于检查已执行，也不证明 coverage、文档新鲜度、hook、结构化日志等要求已满足；已停止的 Harness 专项当时的逐项处置见 [归档计划 §5.1](exec-plans/completed/harness-migration-2026-10.md#51-必需要求处置与验收索引)。已交付版本化 pre-commit/pre-push，使用方式见 [本地 hooks](agent-rules/local-test-execution.md#versioned-local-git-hooks)。未新增 commit-msg/Conventional Commits 机械门禁。

### 完整 Python 文件库存与平台补充

后端八个 Linux 分片、Agent/Skill 和前端 Python 合同共同拥有全部 `tests/**/test_*.py`。
`test_every_backend_test_file_has_one_complete_ci_owner` 对当前源码集合检查遗漏、
失效路径、重复 owner 和只选部分节点的文件；新测试必须同时加入其已有 owning lane。
原五个 node-only 文件已迁入单一 lane 的完整文件选择，不保留旧零散选择器。

Windows runtime 分片只补充 Linux 无法执行的两个 Job Object、六个 host-bind
和两个 junction 场景。它复用 `backend-tests` 矩阵并进入原 `backend required` 聚合，
不改变受信聚合拓扑。该分片的服务 image 表达式为空，不启动 Linux 服务容器。
真实 MCP/Capability Distribution PostgreSQL 测试使用对应的专用 DSN，不能借用
已有数据库容器但漏传连接配置而产生 skip。

后端和 Agent/Skill 均使用既有 `tools/run_test_stage.py`、600 秒 stage deadline
及显式 branch coverage；每个分片上传自己的 evidence、JUnit 和 coverage JSON。
PG/Redis 必需分片和 Windows 分片保持 zero-skips；sandbox-runtime 与 release-governance-authority
仍可能报告另一个平台的 skip。真实 OpenSandbox 凭据隔离是独立 governed 环境验收，
其未执行状态不能由普通 CI 的 skip 或镜像构建成功替代。覆盖率汇总/ratchet
只有在完整所需验收证据闭合后才能作为 D5 门槛接受。

## 3. 交互确认记录

不适用（非 Tauri 项目，按施工图 §3.4 跳过三问）。

## 4. 落地说明

- 非 Tauri，跳过 desktop 平台三问及新建 desktop Release 流水线；现有发布继续遵守 [release runbook](operations/release-operations-runbook.md)，不因外部模板改写。
- 本地 pytest 必须遵守 [local test execution](agent-rules/local-test-execution.md)；选择实际 owning tests，不能运行无 stage/selector 的裸命令。

### 4.1 环境与文档测试

以下 Git Bash 示例从仓库根运行，要求项目 `.venv` 已按锁文件准备。缺依赖时依据 owning 文档准备 `uv lock --check` / `uv sync --locked --extra test`，不把缺环境当通过。

```bash
if test -f .venv/Scripts/python.exe; then
  harness_python=.venv/Scripts/python.exe
else
  harness_python=.venv/bin/python
fi
"$harness_python" tools/run_test_stage.py \
  --stage harness-docs \
  --timeout-seconds 300 \
  -- tests/test_source_authority_docs.py
```

这是 source-authority 文档合同测试，不是新台账事实的自动证明。新增文档还须核对本地链接、引用符号、selector 与 CLI 参数；不为固定措辞/行数新建永久测试。处理[技术债台账](exec-plans/tech-debt-tracker.md)中的迁移时，owning selectors 在 PR 中按实际 caller 选择。

### 4.2 已提交范围的本地治理诊断

准备好上面的 `harness_python` 后运行；`origin/main` 必须是已核对的受信远端引用，base/head 固定为完整提交。

```bash
harness_base_ref="$(git merge-base origin/main HEAD)"
harness_head_ref="$(git rev-parse HEAD)"
"$harness_python" tools/code_governance.py check \
  --base-ref "$harness_base_ref" --head-ref "$harness_head_ref" --format text
"$harness_python" tools/architecture_governance.py check \
  --authority-ref "$harness_base_ref" \
  --base-ref "$harness_base_ref" --head-ref "$harness_head_ref" --format text
```

这些工具读取 Git 提交范围，**不包含未提交/未跟踪文件**。当前 HEAD 等于 base 时结果只证明空范围，不能作为本次未跟踪文档或未来代码候选的验收。本地 candidate 工具调用只作诊断；正式可信门禁仍按 [trusted workflow](../.github/workflows/ai-platform-trusted-governance-v2.yml) 从独立受信基线执行，不能用待审 candidate 修改后的检查器自批准。

### 4.3 独立受信基线的 workflow 合同校验

仅在已有正确受信 base checkout 时执行；本例不创建/覆盖 worktree。`GOVERNANCE_BASE_WORKTREE` 指向其目录，实际 base 必须与待验 PR 的 accepted base 一致，缺少这个条件便保留未验证。

```bash
: "${GOVERNANCE_BASE_WORKTREE:?请提供已验证受信基线 checkout 的目录}"
harness_base_root="$(git -C "$GOVERNANCE_BASE_WORKTREE" rev-parse --show-toplevel)"
harness_head_root="$(git rev-parse --show-toplevel)"
harness_base_ref="$(git -C "$harness_base_root" rev-parse HEAD)"
harness_head_ref="$(git rev-parse HEAD)"
"$harness_python" -P "$harness_base_root/tools/trusted_governance.py" validate \
  --base-root "$harness_base_root" --head-root "$harness_head_root" \
  --base-ref "$harness_base_ref" --head-ref "$harness_head_ref"
```

完整治理判定仍包含对同一 base/head 的 code/architecture 检查，`validate` 单独通过不代表两项也通过。依赖版本与 source identity 沿用 owning workflow，而非本页另定。

### 4.4 其他现有检查与证据边界

- 前端从仓库根：`corepack pnpm --dir frontend/web install --frozen-lockfile`，再执行 `corepack pnpm --dir frontend/web run ci:verify`。不要在根目录直接运行缺少 package.json 的 pnpm script。
- 编译：`"$harness_python" -m compileall -q app tools scripts`；SSE 防回退：`"$harness_python" tools/check_sse_runtime_cutover.py`。
- Docker/Trivy/cosign/registry/GitHub Release 需要相应环境和授权；源码、CLI help、CI、镜像、部署及端到端证据各自区分。
- 阶段 3 补查修复指引、文件大小 hard/advisory 范围、覆盖率阈值/门禁、hook 与文档/豁免要求；完整矩阵留在计划 §5.1，未执行项不得标“已满足”。

### 4.5 Windows 深路径与已观测的本地门禁

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

### 4.6 B2 文档门禁与显式 coverage

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

### 4.7 完整范围库存与关键域补采

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
