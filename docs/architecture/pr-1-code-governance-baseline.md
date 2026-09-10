# PR-1 Code Governance Baseline

本文件是大文件拆分工作的第一份可执行基线。它记录当前源码事实、拆分顺序、职责归属、回归门槛和尚未满足的验收条件。它不是部署或运行时验收证明。

## 1. 审计对象

| 项目 | 值 |
| --- | --- |
| 仓库 | `/Users/jiangxinlin/Documents/Codex/ai-platform` |
| 基线 commit | `2ba4e172598df2bae7aa03684da2e39c7b9fbf0a` |
| 分支 | `codex/pr1-mcp-registry-extraction` |
| 审计日期 | `2026-09-10` |
| PR head | 当前分支 `HEAD`（提交后用 `git rev-parse HEAD` 记录 exact SHA） |

当前工作树已有容量渲染、能力 schema 和 MCP registry 的拆分改动；已有未跟踪文件 `docs/SSE全链路排查与验收执行文档.md` 不属于本次拆分。

治理门禁按仓库既有顺序分两步执行：authority-only policy commit 为 `8c5e3ed4a6b2c0f51c64038acbd6b34160bca15e`，原始基线为 `2ba4e172598df2bae7aa03684da2e39c7b9fbf0a`，代码候选使用当前分支 `HEAD` 的 exact SHA。授权与代码迁移分别进入两个堆叠 PR，未纳入无关的未跟踪 SSE 文档。

## 2. 大文件清单与目标 owner

| 文件 | 基线规模（`2ba4e172...`） | 当前工作树规模 | 主要混合职责 | 第一目标 owner |
| --- | ---: | --- | --- |
| `app/repositories.py` | 8,435 行 | 8,040 行 | Skill、MCP、Capability、Run、Permission、Sandbox、File、Artifact、Audit | 各 bounded context 的 `infrastructure/postgres`，旧文件仅保留兼容 facade |
| `app/runtime/sandbox/container_provider.py` | 5,443 行 | 5,443 行 | Docker、OpenSandbox、workspace、egress、attestation、cleanup | `app/sandbox/infrastructure/providers` 与 SandboxRuntime ports |
| `app/worker.py` | 3,276 行 | 3,276 行 | queue admission、授权重验、executor、event、artifact、终态收敛 | `app/execution/application` 与 `app/streaming` |
| `app/routes/chat.py` | 2,628 行 | 2,628 行 | HTTP、admission、事务持久化、queue、SSE | `app/conversations/application` 与 route transport |
| `app/routes/runs.py` | 2,113 行 | 2,113 行 | run command、copy/retry/resume、cancel、projection | `app/runs/application` 与 route transport |
| `app/models.py` | 2,312 行 | 2,202 行 | Agent、Capability、Run、Chat、Auth、File、Admin DTO | 各 context 的 transport/application contracts |
| `frontend/web/src/hooks/useAgent.ts` | 3,246 行 | 3,246 行 | session、SSE、提交、重试、网络生命周期 | `useChatSessionHistory`、`useChatStream`、`useChatSubmission`、`useRunControlBinding` |
| `frontend/web/src/hooks/useAgent/eventProcessor.ts` | 1,247 行 | 1,247 行 | assistant、execution、sandbox、permission、artifact、todo 事件 | 按协议族拆分的 processor modules |

## 3. `repositories.py` 第一批拆分

### 已完成

MCP server registry 的生命周期 SQL 与 projection 现在集中在 [app/mcp/infrastructure/registry_postgres.py](/Users/jiangxinlin/Documents/Codex/ai-platform/app/mcp/infrastructure/registry_postgres.py)。运行时兼容模块 [app/mcp/infrastructure/postgres.py](/Users/jiangxinlin/Documents/Codex/ai-platform/app/mcp/infrastructure/postgres.py) 对 registry CRUD/credential 只保留动态合同的薄委托；它仍保留独立的 runtime target、tool lookup 和 server-entry 查询，这些属于另一条读取职责。

- `_mcp_server_projection`；
- `list_mcp_server_registry`；
- `list_tenant_mcp_server_registry`；
- `list_mcp_server_registry_names`；
- `upsert_mcp_server_registry`；
- `toggle_mcp_server_registry`；
- `delete_mcp_server_registry`；
- `record_mcp_server_credential`。

[app/repositories.py](/Users/jiangxinlin/Documents/Codex/ai-platform/app/repositories.py) 只保留这些符号的 identity alias。route 和现有 `app.mcp.api` 调用路径保持兼容，旧文件不再拥有 MCP registry 的 SQL 实现。

### 第一批验收标准

| 标准 | 当前状态 | 证据 |
| --- | --- | --- |
| registry lifecycle owner 唯一 | 已满足（限本批符号） | registry adapter 统一 legacy catalog 与 runtime dynamic 合同；旧 runtime 模块只做 CRUD/credential 薄委托 |
| 旧导入路径可用 | 已满足 | `app.repositories.*` 仍导出原符号 |
| SQL/锁/异常语义保持 | 已验证当前 fixture | `tests/test_repositories.py` 中 MCP registry 断言包含 SQL、冲突、删除、凭据和 catalog projection |
| MCP route 兼容 | 已验证 | `tests/test_mcp_repository.py tests/test_mcp_routes.py`：56 passed |
| compileall | 已验证 | `app/repositories.py` 与新 adapter 通过 |
| Ruff | 已验证 | changed-file 列表全部通过 |
| 真实 PostgreSQL 语义 | 未完成 | 当前测试为 fake connection；需要真实 PG 事务/锁证据 |
| facade 删除 | 未完成 | 仍需迁移内部 callers、完成 import inventory 和 deletion proof |

### 后续批次

1. `app/repositories.py:457-1013`：Skill catalog / user Skill file overlay，目标 `app/skills/infrastructure/postgres`。
2. `app/repositories.py:1210-1906`：Capability distribution projection、lock、backfill 和 lifecycle；迁移前必须先解决旧 facade monkeypatch 注入点，目标 `app/capabilities/infrastructure/postgres`。
3. `app/repositories.py:2068-2399`：Run capability authorization、replay 和 MCP scope，目标 `app/runs/application` + `app/mcp/api`，不得把授权决策留在 repository。
4. `app/repositories.py:3052-3782`：session/run/event primitives，目标 `app/conversations/infrastructure/postgres`、`app/runs/infrastructure/postgres`、`app/streaming/infrastructure/postgres`。
5. `app/repositories.py:3783-4963`：tool permission lifecycle，目标 `app/runs/application` + `app/runs/infrastructure/postgres`。

每批都必须遵循：冻结旧边界测试 → 创建 canonical owner → replay 输入/输出和副作用 → facade alias → 迁移 callers → deletion proof。不能复制两份可写实现，也不能在拆分 PR 中同时改变 policy、schema 或 wire contract。

## 4. 治理门禁

当前仓库已有门禁：

- `tools/code_governance.py`：生产净增达到 800 LOC 时产生 advisory；超过 1,500 行的生产文件净增长上限为 100 LOC；超过 3,000 行的功能文件净增长上限为 0；Ruff 必须通过。
- `tools/architecture_governance.py`：必须使用完整 40 位 `authority/base/head`，并从 authority 对应的 checker 源运行。
- `docs/architecture/source-code-architecture.md`：禁止新的全局 dumping module；旧 repository 只能作为临时、无逻辑 facade；跨 domain 通过 `api.py`；adapter 不得成为业务 authority。
- `docs/architecture/run-lifecycle-boundary.md`：`app/repositories.py` 不是 Runs lifecycle owner；新 Runs lifecycle code 不得继续写入该文件。

本轮拆分不得把旧 facade 继续变成新业务 owner。例外必须绑定 exact base/head、owner、理由、scope hash 和 expiry；当前 exception 的日期尚未到期，但 authority/base 与当前基线不匹配，属于 stale binding，不能作为本轮通过依据。

## 5. 回归测试映射

| 拆分边界 | 最小 focused suite |
| --- | --- |
| MCP registry | `tests/test_repositories.py -k mcp_server_registry`、`tests/test_mcp_repository.py`、`tests/test_mcp_routes.py` |
| Skill/capability persistence | `tests/test_repositories.py` 对应 capability/skill 条目、`tests/test_capability_distribution_routes.py`、`tests/test_authorized_skill_catalog.py` |
| Run/session/event | `tests/test_repositories.py`、`tests/test_run_control_routes.py`、`tests/test_streaming_repository.py`、`tests/test_streaming_postgres.py` |
| Worker/executor | `tests/test_worker.py`、`tests/test_claude_agent_sdk_runner.py`、`tests/test_claude_agent_worker_adapter.py` |
| Frontend event state | `useAgentRoutedSession.test.tsx`、`eventProcessor.test.ts`、真实 mounted route tests |

Fake Redis、Fake PostgreSQL、Fake SDK 或 fake sandbox 只能证明 source-level behavior。真实依赖缺失时结果必须标记为 `EVIDENCE_BLOCKED`，不能写成 runtime acceptance。

## 6. 本轮执行记录

```text
.venv/bin/python -m compileall -q app tools scripts
pass

.venv/bin/python -m pytest tests/test_repositories.py tests/test_mcp_repository.py tests/test_mcp_routes.py tests/test_capacity_baseline.py tests/test_capability_distribution_routes.py tests/test_department_directory.py -q --basetemp .pytest-tmp/pr1-current3
590 passed, 2 skipped, 1 warning

.venv/bin/python -m pytest tests/test_mcp_repository.py tests/test_mcp_routes.py -q --basetemp .pytest-tmp/mcp-split
56 passed, 1 warning

.venv/bin/python -m pytest tests/test_architecture_governance.py -q --basetemp .pytest-tmp/architecture-pr1
277 passed in 105.33s

.venv/bin/python -m ruff check --isolated -- <changed Python files>
All checks passed

base=$(git rev-parse 2ba4e172598df2bae7aa03684da2e39c7b9fbf0a); head=$(git rev-parse HEAD); .venv/bin/python -P tools/code_governance.py check --base-ref "$base" --head-ref "$head" --format text
本轮结果：PASS；production files=8；production added LOC=942；production net LOC=211；test added LOC=2；Ruff=pass；advisories=none；violations=none

.venv/bin/python -P tools/architecture_governance.py check --authority-ref 8c5e3ed4a6b2c0f51c64038acbd6b34160bca15e --base-ref 8c5e3ed4a6b2c0f51c64038acbd6b34160bca15e --head-ref "$(git rev-parse HEAD)" --format text
本轮结果：PASS；findings=none
```

提交 PR 前必须重新执行：

```text
.venv/bin/python -m ruff check --isolated -- <changed Python files>
.venv/bin/python -m compileall -q app tools scripts
python -P <authority-worktree>/tools/code_governance.py check --base-ref <40-hex-base> --head-ref <40-hex-head>
python -P <authority-worktree>/tools/architecture_governance.py check --authority-ref <40-hex-authority> --base-ref <40-hex-base> --head-ref <40-hex-head>
```

## 7. 当前未完成项

- `repositories.py` 尚未达到“薄 facade”目标，当前只完成 MCP registry 第一批。
- `app/platform/capability_contracts.py` 和 `app/platform/capacity_rendering.py` 是本轮在现有 policy 允许范围内落位的过渡 owner；后续应在 capability/capacity bounded context 正式纳入 policy 后迁移，避免 platform 长期承载产品 DTO 或运营文案。
- 尚未建立每个迁移符号的完整 caller inventory、runtime entrypoint 检查和 deletion proof。
- 尚未运行真实 PostgreSQL/Redis、Docker/OpenSandbox 或浏览器 SSE 验收。
- 已形成两个正式本地提交并完成 exact authority/base/head 门禁；推送或创建 PR 前仍需在目标 CI/authority worktree 重新执行同样检查。
