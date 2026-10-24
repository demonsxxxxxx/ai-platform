# CI

Status: active CI navigation; not evidence that any check ran or that a build is deployed.

Workflow 文件是唯一配置来源；本页只说明拓扑和本地复现方式。证据规则见
[CI governance](architecture/ci-test-readiness-governance.md)，本地 pytest 规则见
[local test execution](agent-rules/local-test-execution.md)，发布见
[release runbook](operations/release-operations-runbook.md)。

## Workflows

| 文件 | 触发条件 | 覆盖内容 | 是否自动发布 | 备注 |
|---|---|---|---|---|
| ai-platform-backend.yml | PR→main、push main、workflow_dispatch | Linux 8 个完整文件分片 + Windows runtime 节点分片；preflight/compileall/SSE cutover + Agent/Skill 契约 + 镜像构建 + Trivy + 启动健康 + required 聚合 | 否 | PR 时按影响范围构建镜像；main push 不构建，镜像发布由 packaging 手动触发 |
| ai-platform-frontend.yml | PR→main、push main、workflow_dispatch | Windows 4 个 JS 测试分片、独立 Python 合同/投影审计、lint/build/trace + Linux 镜像/Trivy/provenance/启动健康 + required 聚合 | 否 | 同上 |
| ai-platform-packaging-publish.yml | 仅 dispatch（main，confirm_release=PUBLISH_MAIN）；合并 main 不触发 | 不可变镜像 + SBOM + cosign 签名/验证 + manifest + Compose 包 + 可选版本化 Release | 是（受控） | 无 PR 触发 |
| ai-platform-trusted-governance-v2.yml | pull_request_target（opened/synchronize/reopened/edited） | 受信基线 + PR head 验证 + Ruff + code_governance + architecture_governance | 否 | governance 是 authority object（candidate 不可放宽自身结果） |

前端 JS 分片直接执行 `package.json` 中既有的 14 个测试阶段，每个 Runner 保持 `--test-concurrency=2` 和原有进程隔离；`test:sse` 的第二次 Runner 同样保留。

Python 合同/投影审计、JS 分片、lint/build/trace 和镜像并行启动；构建 Job 的 Python 依赖仅供产物来源记录使用。`frontend required` 必须接收四类 Job 的成功结果，失败、取消、跳过或缺失均阻断；矩阵不因其他分片失败而取消剩余分片。`tests/test_frontend_ci_shards.py` 校验分片与完整本地 `ci:verify` 阶段清单完全一致，无遗漏或重复分配。`ci:verify` 是本地完整串行复现入口。

版本化 pre-commit/pre-push 的使用方式见 [本地 hooks](agent-rules/local-test-execution.md#versioned-local-git-hooks)。没有 commit-msg/Conventional Commits 机械门禁。

### 后端测试文件归属

后端八个 Linux 分片、Agent/Skill 和前端 Python 合同共同拥有全部 `tests/**/test_*.py`。
`test_every_backend_test_file_has_one_complete_ci_owner` 对当前源码集合检查遗漏、
失效路径、重复 owner 和只选部分节点的文件；新测试必须同时加入其已有 owning lane。

Windows runtime 分片只补充 Linux 无法执行的 Job Object、host-bind 和 junction 场景。
它复用 `backend-tests` 矩阵并进入原 `backend required` 聚合，不启动 Linux 服务容器。
真实 MCP/Capability Distribution PostgreSQL 测试使用对应的专用 DSN，不能借用
已有数据库容器但漏传连接配置而产生 skip。

后端和 Agent/Skill 均使用 `tools/run_test_stage.py`、600 秒 stage deadline
及显式 branch coverage；每个分片上传自己的 evidence、JUnit 和 coverage JSON。
PG/Redis 必需分片和 Windows 分片保持 zero-skips；sandbox-runtime 与 release-governance-authority
仍可能报告另一个平台的 skip。真实 OpenSandbox 凭据隔离是独立 governed 环境验收，
其未执行状态不能由普通 CI 的 skip 或镜像构建成功替代。没有 coverage 阈值门禁。

## Local reproduction

以下 Git Bash 示例从仓库根运行，要求项目 `.venv` 已按锁文件准备（`uv sync --locked --extra test`，uv 版本以 `pyproject.toml` 为准）。缺依赖不算通过。

```bash
if test -f .venv/Scripts/python.exe; then
  harness_python=.venv/Scripts/python.exe
else
  harness_python=.venv/bin/python
fi
"$harness_python" tools/run_test_stage.py \
  --stage source-authority-docs \
  --timeout-seconds 300 \
  -- tests/test_source_authority_docs.py
```

选择实际 owning tests，不运行无 stage/selector 的裸 pytest。需要 app 行/分支覆盖时给 runner 加 `--coverage`；必需真实依赖的 stage 加 `--require-zero-skips`。

### 文档链接检查

```bash
"$harness_python" tools/check_documentation.py
```

默认检查未提交和未跟踪的第一方文档：Markdown 链接/图片、heading anchor（含 Unicode、重复标题和显式 HTML id）。`docs/release-evidence/`、`docs/agent-rules/history/` 和 `docs/exec-plans/completed/` 是历史根，明确列为 excluded。Backend preflight 在 CI 中用 `--base-ref` 比较 PR 改动。本检查不证明文档内容与运行状态相符。

### 已提交范围的治理诊断

`origin/main` 必须是已核对的受信远端引用，base/head 固定为完整提交。

```bash
harness_base_ref="$(git merge-base origin/main HEAD)"
harness_head_ref="$(git rev-parse HEAD)"
"$harness_python" tools/code_governance.py check \
  --base-ref "$harness_base_ref" --head-ref "$harness_head_ref" --format text
"$harness_python" tools/architecture_governance.py check \
  --authority-ref "$harness_base_ref" \
  --base-ref "$harness_base_ref" --head-ref "$harness_head_ref" --format text
```

这些工具读取 Git 提交范围，**不包含未提交/未跟踪文件**。HEAD 等于 base 时结果只证明空范围。本地调用只作诊断；正式可信门禁按 [trusted workflow](../.github/workflows/ai-platform-trusted-governance-v2.yml) 从独立受信基线执行，不能用待审 candidate 修改后的检查器自批准。

### 独立受信基线的 workflow 合同校验

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

完整治理判定仍包含对同一 base/head 的 code/architecture 检查，`validate` 单独通过不代表两项也通过。

### 其他检查

- 前端从仓库根：`corepack pnpm --dir frontend/web install --frozen-lockfile`，再执行 `corepack pnpm --dir frontend/web run ci:verify`。根目录没有 package.json。
- 编译：`"$harness_python" -m compileall -q app tools scripts`；SSE 防回退：`"$harness_python" tools/check_sse_runtime_cutover.py`。
- 源码库存（跨 context 导入边、热点文件等）：`"$harness_python" scripts/analysis/source_quality_inventory.py`，输出到 stdout，需要时现测，不使用旧快照。
- Docker/Trivy/cosign/registry/GitHub Release 需要相应环境和授权；源码、CLI help、CI、镜像、部署及端到端证据各自区分。

### Windows 深路径

Git for Windows 在深层 pytest fixture 中可能报 `Filename too long`，再被 architecture 工具包装成 `git_object_missing`。用进程级配置运行，不改全局 Git、断言或 required checks：

```bash
GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.longpaths GIT_CONFIG_VALUE_0=true \
  "$harness_python" tools/run_test_stage.py \
  --stage governance --timeout-seconds 300 --require-zero-skips \
  -- tests/test_code_governance.py tests/test_architecture_governance.py
```

2026-10 Harness 专项期间的本地观测结果已归档到 [CI 观测记录](exec-plans/completed/quality-baseline-2026-10-13/ci-observations.md)。
