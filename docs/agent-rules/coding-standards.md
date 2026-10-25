# 编码规范与硬约束

状态：当前仓库编码规范的唯一入口。本文拥有命名、类型、常量、错误与日志的编码规则，以及所有编码约束的执行级别索引。

包放置、分层与依赖方向归 [Source architecture](../architecture/source-code-architecture.md)；证据等级与 CI 拓扑归 [CI governance](../architecture/ci-test-readiness-governance.md)；页面视觉归 [视觉规范](../frontend/lobehub-visual-guidelines.md)。本文只索引这些规则的执行方式，不复述它们的细节。

## 1. 约束级别

| 级别 | 含义 | 处理方式 |
| --- | --- | --- |
| **阻断** | 由 CI 或受信治理机械检查，违反即失败 | 修复代码；不得通过放宽检查、增加跳过或弱化断言让候选通过 |
| **棘轮** | 现有存量被记录，只允许减少 | 新增违例失败；修好存量后同步下调基线 |
| **提示** | 工具输出 advisory，不改变退出码 | 在 PR 中说明取舍 |
| **评审** | 没有可靠检测器，靠独立 review | 按 [PR 模板](../../.github/PULL_REQUEST_TEMPLATE.md) 填写并由审查者核验 |

新规则先找可靠检测器再落地；能写成工具检查的规则不在文档里重复描述细节。

## 2. 硬约束总表

### 2.1 后端结构（受信治理，`tools/architecture_governance.py` + [`architecture-policy.json`](../../architecture-policy.json)）

只评估 base→head 的新增或改动面，不认证存量遗留代码已合规。

| 规则 | 级别 | 要点 |
| --- | --- | --- |
| 跨 context 只经 `api.py` / `events.py` | 阻断 | `cross_domain_internal_import`；遗留调用方迁移也只能经这两个边界 |
| 分层依赖 | 阻断 | `domain` 只用标准库与 `kernel`；`application` 不碰具体适配器；`transport` 只可用 fastapi/pydantic/starlette，不导入 infrastructure、platform 或遗留根模块；具体实现由 `bootstrap` 注入 |
| context 代码不依赖遗留根模块 | 阻断 | `layer_dependency_forbidden`：`app.auth`、`app.routes.*`、`app.models` 等不能被 context 内代码导入 |
| 不扩展遗留目录 | 阻断 | `unapproved_app_package` / `unapproved_app_root_module`：`app/routes/` 与 `app/` 根下不新建模块 |
| 新模块必须分层 | 阻断 | `unlayered_domain_module` |
| 禁用名称 | 阻断 | 模块名不得为 `common`、`helpers`、`manager`、`models`、`repositories`、`service`、`services`、`utils`；不得含交付标签 `acceptance`、`b1`、`b2`、`b5`、`b6`、`phase2`、`poc`、`readiness` |
| 新生产文件 ≤ 800 行 | 阻断 | `new_production_file_size`，覆盖后端、tools 与前端功能源码，不含测试/文档/数据 |
| 冻结热点文件不增长 | 阻断 | `frozen_hot_file_growth`：名单内文件不得超过 base 行数与策略上限 |
| 结构化日志 | 阻断 | `structured_logging_required`：新日志消息为固定事件名，动态值放 `extra`，不用 f-string/拼接/`%`/`.format` |
| 生产注册表 | 阻断 | 键为字面量、值为声明的零参构造器，禁止测试替身与动态导入 |
| 受治理常量只有一个定义 | 阻断 | `governed_symbols` 中的跨边界字面量只能由 owner 定义 |
| 例外 | 阻断 | 例外文件须绑定 base/head、路径、owner、原因、到期（≤30 天）与移除条件；`non_exemptible_codes` 不可豁免 |

### 2.2 代码量信号（受信治理，`tools/code_governance.py`）

| 规则 | 级别 | 要点 |
| --- | --- | --- |
| 改动的 Python 文件通过 Ruff | 阻断 | 以 `ruff check --isolated` 运行默认规则集（E4、E7、E9、F）；仓库不提供 Ruff 配置 |
| 生产净增 ≥ 800 行、生产文件数、热点增长、测试量比例 | 提示 | 输出 remediation，不改变退出码 |

### 2.3 前端（`frontend/web`，前端 CI 与本地 `ci:verify`）

| 规则 | 级别 | 要点 |
| --- | --- | --- |
| TypeScript 严格模式 | 阻断 | `strict`、`noUnusedLocals`、`noUnusedParameters`；`pnpm run build` 先执行 `tsc -b` |
| ESLint | 阻断 + 棘轮 | `eslint . --max-warnings <基线>`：错误直接失败；警告总数只许减少，修掉警告后同步下调 `package.json` 中的基线 |
| 语义色与断点 | 阻断 | `check-style-rules.mjs`：禁止原始调色板类、源码与 CSS 中的字面颜色（用 `--theme-*`）、非标准 `@media` 断点 |
| 内联 `style={{}}` 与 `!important` | 棘轮 | 按文件对比 `scripts/style-rules-baseline.json`，只许减少 |
| UI 库与聊天组件 | 评审 | 见 [前端指令](../../frontend/web/AGENTS.md)：Lobe UI 为通用组件库，assistant-ui Elements 为聊天框架，不引入平行库 |

### 2.4 测试与提交

| 规则 | 级别 | 要点 |
| --- | --- | --- |
| 每个后端测试文件只属于一条 CI lane | 阻断 | `test_every_backend_test_file_has_one_complete_ci_owner`；新测试同时加入其 owning lane |
| 必需集成零跳过 | 阻断 | `tools/run_test_stage.py --require-zero-skips`；缺依赖是失败，不是跳过 |
| 本地 pytest 经 runner | 评审 | 见 [本地测试执行](local-test-execution.md) |
| 本地 hooks | 阻断（本地） | `.githooks/pre-commit`：空白、改动 Python 的 Ruff、改动前端的 ESLint、文档链接；`pre-push` 追加类型检查与受影响测试 |
| 退役与兼容处置 | 评审 | 行为变更须在 PR 填写退役清单并附缺席/库存检查结果 |

## 3. 命名

### 3.1 包与模块

- 包和模块名用 `snake_case`，描述一个业务概念或适配器职责，例如 `run_admission.py`、`profile_revision.py`、`postgres_run_repository.py`。
- 不建泛化收纳模块（见 §2.1 禁用名称）；context 内也不鼓励。
- 交付阶段、工单号和证据标签不得命名生产模块，除非该词是长期产品概念。
- 一个模块只有一个变化原因。即使当前 PR 不拆分热点模块，也不得往里加入第二个业务领域。
- 模块名必须和它现在的职责一致。历史名称暂时无法更改时（例如 TD-CONV-001 中的 `app/routes/lambchat_compat.py`），在模块文档字符串里写明现行用途。

### 3.2 类型与操作

| 含义 | 命名 |
| --- | --- |
| 领域概念 | 名词，如 `RunAttempt`、`ProfileRevision` |
| 传输输入/输出 | `...Request`、`...Response` |
| 应用意图 | 写为 `...Command`，读为 `...Query` |
| 用例 | 明确动词，如 `AdmitRun`、`PublishProfile` |
| 持久化行 | `...Record` |
| 安全读模型 | `...Projection` |
| 应用端口 | 能力名词，如 `RunRepository`、`EventPublisher`、`Clock` |
| 具体适配器 | 技术 + 端口，如 `PostgresRunRepository`、`RedisEventPublisher` |
| 业务决策 owner | `...Authority`，仅限唯一权威策略 |
| 兼容边界 | `<subject>_compat.py` 或 `compat/<subject>/`，仅用于真实兼容层 |
| 稳定错误 | 类型化错误 + boundary 拥有的小写下划线 `code` |

函数名用精确动词；`process`、`handle`、`manage`、`execute`、`data`、`payload`、`item` 需要带边界限定词。异步函数不加 `_async` 后缀。

字段后缀：标识 `_id`、SHA-256 `_sha256`、字节数 `_bytes`、时长带单位如 `_seconds`、时间戳 `_at`。单位或身份不明的字段不得跨边界。

## 4. 配置、常量与硬编码

- 环境解析与进程默认值属于 `bootstrap.settings`；领域代码接收类型化配置，不直接读环境变量。
- 端点 URL、凭据、端口、模型/提供方选择、保留窗口和部署相关的工作区值不得写死在路由或业务逻辑中。
- 稳定协议标识和业务常量只能在 owner context 中定义为代码常量；其他 context 导入其公开常量或推导投影，不复制字面量。
- 跨 context 的协议/业务字面量必须由 owner 的 `api.py` 或 `events.py` 导出唯一具名符号；需要机械保护的加入 `governed_symbols`。
- 改变授权、租户/工作区范围、提供方、模型或持久化目标的默认值必须在准入/配置处显式给出，并在生产中失败即关闭；兼容默认值需要具名的遗留消费者。
- 提供方、解析器、Skill、模型和执行器映射只有一个规范注册表，并配一致性测试，不建平行的手写字典。

## 5. 错误、投影与日志

- 领域/应用错误携带稳定机器 `code` 和安全的结构化事实；HTTP 状态、SSE 形态与兼容措辞在 transport 映射。
- 原始数据库错误、SDK 异常、命令、密钥、存储键和私有路径不得进入公开投影。
- 普通用户投影必须刻意小于管理员或内部记录；直接序列化持久化记录不是投影策略。
- 新日志使用固定事件名，动态值放入 `extra`（§2.1）。不记录凭据、真实环境值或私有提示词。

## 6. 规则的增改

1. 先说明要防止的缺陷类别和真实发生过的案例。
2. 找到可靠检测器并放进已有 owning 工具（`architecture_governance`、`code_governance`、`check-style-rules.mjs`、ESLint 配置或 owning 测试），不新增平行检查入口。
3. 存量违例多时用棘轮：记录基线，只阻断新增。
4. 在 §2 总表登记级别与位置；删除或合并被取代的旧规则和文字。
5. 受信治理规则在 base 接受后才生效，候选 PR 不能放宽自己的检查。

## 7. 已知缺口

| 缺口 | 现状 | 方向 |
| --- | --- | --- |
| 无统一格式化 | Python 与前端都没有强制的格式化工具 | 若引入，需一次性全仓格式化并单独提交，再加入检查 |
| Ruff 只跑默认规则 | 受信治理以 `--isolated` 运行，忽略仓库配置 | 扩展规则需同时调整 `code_governance` 并对存量做棘轮 |
| 遗留目录迁移只能整树搬迁 | §2.1 两条规则叠加，遗留模块须连同依赖闭包一起迁入 context | 按 TD-CONV-001 叶子优先逐个迁移；是否为 HTTP 边缘层提供迁移期落点需另立 ADR |
