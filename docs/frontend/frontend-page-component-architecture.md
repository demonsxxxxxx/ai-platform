# 前端页面与组件架构设计

状态：增量重构设计。第一切片的源码边界见第 4 节；其余切片仍为提议，不代表已完成迁移或部署。实施时更新所属合同和退役清单，验证结果保留在当前任务或 PR。

## 1. 目标与决定

当前优先改善 **Agent 聊天页**，不是重新设计全站视觉或更换技术栈。

采用“**按页面规划体验和验收，按业务职责复用组件，按事实来源维护状态**”的结构：

- 系统管理、目录和 Agent 配置页面以 **Lobe UI** 为通用控件库。
- Agent 和普通聊天的对话主体以 **assistant-ui Elements 成品源码**为主要来源，不把 assistant-ui 缩成 runtime，也不从 primitives 重新自创全部聊天展示。
- 聊天组件内部允许使用 Lobe 基础按钮、输入和浮层；这不让 Lobe chat 成为第二套聊天实现。
- 页面只装配已经明确职责的业务能力，不成为 Session、Run、上传或权限的第二个事实来源。
- 复用现有平台 SSE、提交回执、草稿、文件授权及专业渲染。保持单一消息快照、单一提交和上传路径、单一消息滚动控制。
- 以 LobeHub 的“共享组件 / 领域功能 / 薄页面”作为主要组织参照：跨页面可复用的组件放 `src/components/`；领域 UI、私有组件与业务编排可同域放 `features/`；路由入口只负责装配。这里讨论代码组织，不涉及公共包发布。
- 不换状态管理库，不增加第二 runtime；目录归位随职责迁移完成，不做只改路径、不改边界的全树搬迁。

### 文档责任

| 问题 | 所属文档 |
| --- | --- |
| 页面、功能组件、共享组件和状态如何组合；重构切片 | 本设计（分别标明已迁移边界与后续提议） |
| Lobe 版本、公开入口、Provider、通用控件行为 | [Lobe 接入合同](lobe-ui-adoption.md) |
| Elements 来源、固定版本、runtime、业务适配、能力限制 | [assistant-ui 接入合同](assistant-ui-chat-adoption.md) |
| 颜色、排版、几何、响应式和页面视觉验收 | [视觉约束](visual-guidelines.md) |
| 格式化、类型、lint 与检查级别 | [编码规范](../agent-rules/coding-standards.md) |
| 专家任务、身份、历史与产品语言 | [专家工作区](../architecture/expert-agent-service-workbench.md) |

本文不覆盖上述合同的现有规则。对话主体的视觉参照（固定版本的 ChatGPT Clone）及其阅读列、输入器、气泡规格由[视觉约束](visual-guidelines.md)第 7 节拥有；侧栏宽度、主题、文件栏等已确认要求不因该参照撤销，不引入多产品风格混搭。

## 2. 重构基线：已有 components，但共享与页面边界混杂

当前已有 `src/components/`，包含 `common`、`chat`、`agent`、`documents`、`layout`、`panels`、`lobe-ui` 等目录；同时 `src/features/` 也包含页面、业务编排及部分 UI。需要明确的是应用内组件的放置、命名和依赖方向，不是把组件抽成 npm 公共包。

| 当前共享面 | 代表路径 | 处置 |
| --- | --- | --- |
| 第三方基础控件 | `@lobehub/ui/base-ui`、`contexts/LobeUiProvider.tsx` | 直接使用公开 API，保留唯一主题、国际化与 Portal 接入 |
| 有合同的基础控件适配 | `components/lobe-ui/LobeSelectField.tsx` | 保留可访问标签、布局和嵌套 Escape 适配，不包装全部 Lobe 控件 |
| 应用级产品组合 | `components/common/ConfirmDialog.tsx`、`ErrorBoundary.tsx`、`SelectionActionPopover.tsx` | 保留危险操作、故障恢复和选择动作等真实职责 |
| 文件与专业渲染 | `common/AttachmentCard.tsx`、`ImageViewer.tsx`、`DeferredCodeMirrorViewer.tsx`、`components/documents/` | 保留授权、安全 URL、预览及专业格式处理；移动时完整迁移其直接消费者 |
| 聊天成品与接入 | `components/chat/assistant-ui/elements/`、`AssistantUiProjection.tsx`、`externalStoreRuntime.ts` | Elements 是本地维护的固定来源源码；runtime 来自 npm，不把两者混为一个包 |
| Agent 活动 Elements | `components/chat/ChatMessage/elements/` | 保留工具、Todo、子任务、产物等平台公开投影适配，核对与上游的必要差异 |

`components/common` 现存内容同时包含查看器、附件、Skill 卡片、主题和错误恢复，职责较杂；`components/layout/AppContent` 还容纳聊天业务编排。当前不是缺少组件目录，而是目录名与实际责任没有充分对应。目标是让可独立复用的 UI 回到 `components/<责任>/`，页面业务编排回到 `features/<业务>/`；不是认定这些组件冗余。

`ConfirmDialog`、`LobeSelectField`、`AttachmentCard` 和 `AssistantUiProjection` 有明确产品、安全或库合同，不能按“包装层”统一删除。现有 Lobe `./es/*` 已由安装版本 exports 暴露；不能只看路径深就认定为私有导入，升级时仍按接入合同核对。

### 共享聊天调用关系

```text
AgentWorkspaceRoute ─┐
                    ├─ ChatAppContent
普通聊天入口 ────────┘       ├─ useAgent / Session / 模型 / 能力
                            ├─ Agent feature hooks：绑定 / 首发 / 展示 slots
                            └─ ChatView
                                 ├─ AssistantUiProjection / ExternalStoreRuntime
                                 ├─ Virtuoso → ChatMessage → Elements / 公开业务 part
                                 ├─ ChatInput → Composer → 平台提交与上传
                                 └─ 文件 / 预览 / 工作区外壳
```

Agent 与普通聊天不是两个 runtime；当前已共用消息、输入器、附件和滚动。Agent 差异集中在身份与只读状态、首发绑定、开场、任务历史以及 Skill/MCP 门控。

### 改动前热点与证据边界

2026-11-02（北京时间）在独立 checkout 的 `682e8fbfd96cffa75af5149d1727e7563c368f20` 运行 `scripts/analysis/source_quality_inventory.py`：前端生产源文件 **443** 个、生产行数 **90,912**，测试文件 **296** 个、测试支持文件 **7** 个。工具统计的是被 Git 跟踪的源文件，测试数量不是覆盖率。

| 文件 | 行数 | 已核对的职责混合 |
| --- | ---: | --- |
| `src/hooks/useAgent.ts` | 3,698 | Session、历史、SSE、提交恢复、Run 控制与终态同步 |
| `src/components/layout/AppContent/ChatAppContent.tsx` | 1,454 | 普通能力配置、专家身份/首发、路由、Session 与页面装配 |
| `src/components/layout/AppContent/ChatView.tsx` | 1,274 | 消息列表、预览生命周期、文件导入、外部导航与 Composer 装配 |
| `src/components/chat/ChatInput.tsx` | 1,170 | 草稿 scope、上传、命令/选择器、问答门控与停止确认 |

大小只定位热点；下面的重构依据是职责与变化原因，不是机械行数目标。主目录另有未提交的 MCP/聊天展示修改，本设计不复制或覆盖这些修改，也不把独立 checkout 当作它们的完整候选。

## 3. 页面规划与组件能力

| 页面或区域 | 页面承担的职责 | 主要组件来源 | 必须保留的边界 |
| --- | --- | --- | --- |
| 公司导航、Agent 市场/详情 | 内容发现、选择、任务入口 | Lobe + 产品目录组件 | 服务端发布状态、可见性与访问权限 |
| Agent Builder/配置发布 | 受控编辑、验证反馈、保存/发布操作 | Lobe + Profile 编辑组件 | 私有指令、修订、发布与授权仍在平台 |
| 系统管理（用户/模型/MCP/Skill/运行） | 列表、筛选、表单与管理操作 | Lobe | 不借 UI 替换改变请求与权限合同 |
| **Agent 聊天页** | 专家身份、开场、任务历史、只读/可用状态及下列聊天区域的装配 | **assistant-ui 对话主体** + Agent 产品组合 | 先确认 Session 身份；首发先建会话并绑定；同一运行与提交链 |
| 普通聊天页 | 普通开场和允许的模型/能力设置 | 与 Agent 页共享的 assistant-ui 聊天组件 | 不引入第二份消息、上传、草稿或 runtime |
| 文件/产物侧区 | 文件选择、授权导入、预览和专业内容 | 现有文件/文档组件，Lobe 通用动作 | 文件授权、来源、对象 URL 与迟到回执隔离 |
| 全局导航、账号与设置 | 跨页面导航和浏览器偏好 | 共享外壳 + Lobe | 既有主题/侧栏偏好 owner 不变 |

### Agent 聊天页的组件清单

| 区域 | 能力及成品来源 | 平台适配 |
| --- | --- | --- |
| 对话正文 | Thread、Message、Markdown、Message Actions | 同一公开消息快照、顺序、复制范围与安全链接 |
| 输入与附件 | Composer、Send/Cancel、Attachments、模型选择组合 | 完整提交事务、草稿 revision、上传限制、Skill/MCP 门控、IME |
| 执行活动 | RunActivity、ToolCall、ToolError | 平台公开类别和状态，不显示 raw args/result，不负责执行工具 |
| 计划与子任务 | TodoList、TaskCard、SubagentList | 只显示已有真实计划/状态；不启动子代理、不伪造进度 |
| 任务问答 | OptionList、QuestionFlow | 逐题本地草稿、最后整批提交、稳定 input_id、受理/解决/完成有区别 |
| 交付与预览 | ArtifactCard、文件/图片展示 | 产物登记、授权下载与专业预览，不根据模型文字认定文件存在 |
| 等待与恢复 | EmptyState、LoadingState、ConnectionState、Suggestions | 一处生成反馈、原重连流程、服务端后续建议及发送门控 |

当前消息动作主要启用复制与统计。编辑、重新生成、分支、导出及工具审批不因使用成品而自动开启；它们须有对应平台协议。本设计不新增这些能力。

## 4. 目标结构：清楚的责任边界，不是新框架

下表的边界优先通过现有文件中的纯函数、hook 和组合点实现。只有真实职责需要独立演进时才新增文件；名字是责任说明，不要求每行建一个类或 Context。

### 开源参照与采用边界

选择一个主要参照，而不是从不同仓库各拼一套目录规范：

| 参照 | 可核对的上游做法 | 本项目采用的部分 |
| --- | --- | --- |
| **LobeHub：主要代码组织参照** | [Code Ownership](https://github.com/lobehub/lobehub/blob/canary/AGENTS.md) 规定薄路由组合 `features`，业务 UI 和逻辑按领域归属；[目录说明](https://github.com/lobehub/lobehub/blob/canary/docs/development/basic/folder-structure.mdx) 区分可复用 `components`、业务 `features`、可复用 hooks、服务和状态 | 共享组件与领域 UI 分开；页面不成为庞大业务容器；不照搬其 Next.js/SPA 多端目录或状态技术栈 |
| **assistant-ui：聊天组件专项参照** | [Elements / Primitives](https://www.assistant-ui.com/docs/primitives) 先选带样式 Elements；[Thread](https://www.assistant-ui.com/docs/primitives/thread) 建议默认从成品 Thread 开始，缺少所需布局时才用 primitives | 先复用聊天成品，再保留必要平台 adapter；沿用项目已有固定来源与命名，不另造一套互动组件 |
| LobeHub：局部代码归组实例 | [CommandMenu](https://github.com/lobehub/lobehub/blob/canary/src/features/CommandMenu/README.md) 将入口、业务 hook、类型、样式与私有 `components` 放在同一 feature；[测试文档](https://github.com/lobehub/lobehub/blob/canary/docs/development/basic/test.mdx) 展示测试靠近被测代码 | 功能私有 UI/hook/type/style/test 就近维护；这是实例，不要求所有功能复制相同文件数量或使用相同样式工具 |

LobeHub 使用共享项目组件优先的习惯值得采用：先找已有产品组件，再用 Lobe 公开控件；不增加无职责的控件包装。本项目仍以现有 Lobe 接入合同为准，不照搬其 antd 平行选择或新建 `helpers/utils/services` 顶层目录。

### 目录与组件放置规范

以下示意复用现有目录，描述收敛方向，不是已经搬迁的清单。不预设新增 `components/ui/`：仅更名 `common` 不会改善职责，不另建没有明确责任的通用收纳层。

```text
src/
  App.tsx / appRouteManifest.ts    # 保留现有路由入口，薄装配
  components/                     # 跨页面/功能复用的组件
    common/                       # 暂留已有通用交互，不继续混入业务面板
    lobe-ui/                      # 必要的库合同适配
    chat/                         # 普通/专家共用的聊天组件与现有集成
      assistant-ui/               # 固定来源 Elements、runtime adapters
    agent/                        # 跨功能复用的 Agent 身份等组件
    documents/                    # 附件、文件和专业预览
    layout/                       # 通用外壳和区域布局
  features/                       # 按业务归组 UI、页面编排和私有逻辑
    agent-market/                 # 包含市场与专家任务的现有功能入口
    agent-builder/
    ai-applications/
    usage/
  hooks/                          # 已有跨功能状态能力，如 useAgent
  contexts/                       # 已有主题、认证等跨树入口
```

其他现存业务目录不因未画入图中就被删除。独立 React 组件不必一律提升到全局 `components`：仅服务某个 feature 的 UI 可以放在该 feature 内，真正跨页面复用且接口稳定后再提升；一个组件内部的短片段可留在同文件。

| 放置问题 | 本项目采用的规则 |
| --- | --- |
| 已有跨业务基础交互 | 保留 `common/ConfirmDialog`、`ErrorBoundary` 的真实职责；不复制 Lobe Button/Input/Table，不为改目录名增加转发 |
| 第三方适配 | `components/lobe-ui` 只保留真实 API、可访问性与焦点合同差异；assistant-ui 集成继续归属聊天 owner |
| 跨功能领域组件 | 普通/专家共享消息与 Composer 放 `components/chat`；通用 Agent 身份放 `components/agent`；文件查看器放 `components/documents`，不按页面复制 |
| 仅属于一个功能的 UI | 可保留在 `features/<业务>`，复杂功能可有自己的 `components/`；UI 与业务 hook 分工明确，不要求全部搬到顶层 `components` |
| 页面编排 | 路由薄入口 → feature 组合与数据连接 → 共享/私有 UI；共享组件不得反向依赖某个 Route/Page |
| 局部 hook/type/style/test | 只服务一个功能时靠近该功能；跨功能才复用既有公共 owner。测试继续兼容现有 `__tests__` 和 owning stage，不为模仿仓库改名全体测试 |
| 命名与导入 | 保留组件 PascalCase、hook `useXxx` 和现有目录命名；简单组件用具名文件，复杂组件可用目录。跨目录仅使用真正需要的公开入口，局部直接导入，不铺设多层 barrel 或转发组件 |

初步归位候选：`common/AttachmentCard`、图片/视频查看器 → 现有 `documents` 文件/预览责任；`common/SkillBaseCard` → 已有 Skill 组件 owner；`layout/AppContent` 的业务协调 → 对应 feature 或状态 owner，真正布局仍留 `layout`。这些不是无条件搬迁指令：先确认复用范围及直接/动态/测试/脚本消费者，再迁入唯一 owner，更新导入、CSS、选择器和文档并删除旧入口；若保留兼容入口，列出具体消费者和退出条件。第一切片只迁移下述 Agent 专属边界，不扩入文件/预览或 Skill 归位。

### 责任边界

| 边界 | 承担什么 | 不承担什么 |
| --- | --- | --- |
| Agent 页面组合（现有 `features/agent-market/`） | 该 Agent 的身份、开场、任务历史与可用/只读展示 | SSE、第二份消息与单独 Composer |
| Agent 会话绑定与首发（`features/agent-market/useAgentConversationBinding.ts`、`useAgentFirstSend.ts`） | 路由/Profile/Session 对齐、首发 single-flight、operation ID 与草稿交接；共享历史与消息加载仍调用现有 `useAgent` | 普通模型目录与通用控件 |
| 共享聊天表面（现有 `components/chat/`） | Thread、消息、Composer、执行内容、问答和产物组合 | 路由准入、服务端权限与持久事实 |
| assistant-ui 集成（现有 `components/chat/assistant-ui/`） | 平台模型与 runtime 的转换、提交/附件 adapter、固定 Elements 来源 | 再建 Session/SSE/上传/历史存储 |
| 文件与预览生命周期（现有文件/预览 owner） | 授权文件读取、导入、预览、scope 变化与异步结果隔离 | 确定 Run 成功或解释未经授权路径 |
| 应用内共享交互 | 确认、故障恢复、可访问性适配等已有跨页面合同 | 全量转发 Lobe API、聊天事实与全部业务状态 |
| 外壳组合（现有 workbench/layout） | 导航、区域和响应式容器，接收 header/thread/composer/panel slots | Profile/Session 查询、工具执行、预览授权 |

### 第一切片：Agent 专属协调与展示归位

`features/agent-market/useAgentFirstSend.ts` 持有 `getOrCreateAgentConversationOperationId`、`clearAgentConversationOperationId`、`ensureAgentConversationForFirstSend`、`submitAgentFirstMessageSingleFlight` 及 creation/submission refs、generation、错误和 draft handoff。`useAgentConversationBinding.ts` 持有恢复状态、权威 Session 身份校验、迟到结果隔离与能力/Transcript 门控。`ChatAppContent` 调用它们并保持原有路由重置和恢复副作用顺序，不再定义这些 helpers、状态或旧导出。

保留的提交合同：operation ID 在响应丢失重试中稳定、创建及提交 single-flight、创建结果的 Agent/Session 身份校验、`loadHistory` 绑定成功后才发送、await 前后 generation 校验、仅 accepted 后清理 operation ID，以及首发草稿与附件交接。`AgentWorkspaceRoute` 继续拥有路由身份读取，`useAgent` 继续拥有消息/历史/SSE，没有第二套执行链。

Agent 标题、开场和建议位于 `AgentChatPresentation.tsx`，由 `useAgentChatPresentation.tsx` 组合。共享 `components/chat/chatPresentation.ts` 声明身份、header、opening、starter prompts 和 slash-command 能力接口；`ChatView` 只消费 `presentation`，不导入 Agent feature 或完整 Profile。普通聊天未传该接口时仍使用原 `WelcomePage` 和通用标题。Agent 建议继续使用同一 assistant-ui runtime 的 `Suggestion send={false}` 填入草稿，不直接发送。

直接回归迁入 `features/agent-market/__tests__/agentConversationBinding.test.tsx`，保留稳定 operation ID、并发首发、绑定失败、迟到结果和存储不可用场景；`agentFirstSend.mounted.test.tsx` 覆盖真实 hook 的绑定等待、canonical identity 草稿/附件交接、accepted-only 清理、重试及新任务/身份变化/卸载隔离。两者由既有 `test:agent-market` wildcard 选择，原 layout 测试路径已移除。Agent routed-session、market binding、Composer 和普通聊天 mounted 回归保留。

退役范围包括 `ChatAppContent` 的上述函数/refs/旧导出，以及 `ChatView.agentEmptyProfile` 和内嵌 Agent 展示；页面与合成 fixture 均改用同一 presentation 入口，无兼容转发。现有 DOM selectors、类名、样式和按钮行为保留，相关 source assertions 改查新 owner；单条内联样式基线从 `ChatView` 等量迁到展示组件，不放宽棘轮。文件/预览和输入器的其余职责留给后续切片。

### 依赖与接口原则

1. 路由/页面 → 业务组合 → 成品组件/适配 → 第三方公开 API；成品展示不反向导入页面装配。
2. 共享聊天消费收敛后的消息、输入与动作接口，不导入 Agent 路由。页面专属行为通过已有参数/slot 传入。
3. 不为减少 props 数量创建全站“万能 Context”。只有现有控制器产生且共同变化的字段才形成具名类型，例如提交状态与动作；类型化分组必须删除旧平铺字段，不保留两套等价入口。
4. 受控值、异步请求状态、授权能力与显示状态区分开；“不在运行”不等于成功，“取消已受理”不等于任务已取消。
5. assistant-ui runtime 转换和业务 renderer 的两种形态可以保留：`MessageParts.tsx` 当前用 `PartByIndex` 将同一快照的 tool/data 指回业务组件。**存在两个 switch 不等于已有双重 UI 或双重状态源。** 收敛可见性、顺序、稳定身份和文本角色等确实重复的决策即可，不为统一外观再造平行消息模型。
6. 跨组件树的预览 host 如需共享状态，应由实际 auth/session scope 拥有其生命周期。先证明其消费者和恢复行为，再考虑从 singleton 迁入作用域；不逐个盲目替换为局部 state。

## 5. 代码风格应改变什么

这里的“代码风格”指可读性和可维护性，不改变页面颜色、字体或几何。

### 保留的基础

- TypeScript `strict`、未使用变量/参数检查、现有 ESLint 与 hooks 检查。
- Lobe 公开组件 API、固定 assistant-ui 来源、唯一主题 token、现有样式棘轮。
- 动态数值及 Lobe 明确要求的 `styles`/`style` 入口；不能为了零内联样式而破坏库尺寸、Portal 或交互合同。
- `ConfirmDialog` 等有明确安全/产品职责的组合，不因“薄”而删除。

### 推荐变化

| 变化 | 当前依据 | 最小实施方式 |
| --- | --- | --- |
| 统一格式化 | `coding-standards.md` 已记录没有统一 formatter；Elements/组件中存在多行 JSX、handler 挤在单行的写法 | 倾向采用 Prettier 处理前端格式，保留 ESLint 检查语义；本设计不安装。按现有规范先独立确认全仓格式化范围，再单独提交一次性格式差异与检查，不与功能重构混合 |
| 缩小 controller/组件职责 | 上述聊天热点分别混合身份、请求、文件、布局和交互 | 先抽真实状态机或副作用 owner，再保留一处组合入口；不按行数拆成相互传 refs 的碎片 |
| 类型表达真实模式 | `ChatViewProps`、`ChatInputProps` 平铺大量可选配置/回调；Agent/plain/question 等组合依赖调用方自律 | 在实际边界形成具名能力与状态类型；对互斥的模式采用可辨识联合；不一次重写全部类型，也不降低 strict |
| 共享入口明确 | `common` 同时容纳文件、查看器、Skill 与基础交互，barrel 与直接导入并存 | 随责任迁移选定 owner 和直接消费者；仅为真实跨功能接口保留公开入口，禁止为每个文件加一层转发 |
| 检查依赖边界 | 当前 ESLint 有基础、hooks、类型 warning，没有前端层间 import 限制 | 先在现有 ESLint 使用精确路径 restriction 保护已迁出的模块；不全局启用未经库存的禁令，不另建架构检查平台 |
| 降低测试对源码布局的绑定 | workbench/source 测试读取组件文件并匹配类名、固定路径与内部函数 | 用真实 mounted 回归和页面检查证明行为；source 断言保留安全边界、入口退役/依赖禁用等适合静态验证的规则。迁移当次更新 package scripts、Python 合同和浏览器 selector |

优先级是目录职责、组件接口、格式化与测试可维护性；不建议借此换 React/Vite、升级 Tailwind、换状态库、启用整套高噪声 lint 或把每个 state 包成 hook。

## 6. 破坏性重构的推荐顺序

下面是重构先后次序，不是已确认线上严重故障等级。目标是打破内部耦合，**不是**破坏用户会话、公开 API 或数据兼容。

| 顺序 | 切片与收益 | 保留与退役要求 |
| --- | --- | --- |
| 1 | **Agent 页面与共享聊天装配解耦**：第 4 节的源码已归入 Agent feature；共享 `ChatView` 通过 presentation slots 接收标题、身份与开场 | 保留共享 Thread/Composer 和现有路由/消息 owner；身份未确认不得展示旧内容；首次建会话幂等、先绑定再发送与草稿交接不变。实际入口和对应回归迁移后删旧函数/导出与状态，不建第二 Agent runtime |
| 2 | **文件/预览与 Transcript 解耦**：将 `ChatView` 的文件读取/import/迟到回执与预览生命周期按现有职责收敛，视图只装配 slots 与已确认状态 | 保留授权、scope、图片图库、外部文件/Run 深链与 Virtuoso；只有全部 host/恢复消费者迁移后才退役旧 store/副作用 |
| 3 | **输入器内部收敛**：复用 `composerSubmission.ts`、`attachmentBinding.ts` 等 owner，将命令/能力面板、草稿 scope 与问答/停止门控从 JSX 装配中分开 | 一处 form、提交 binding 和上传队列；IME、上传中禁止发送、uncertain 回执、问答恢复原草稿/附件均保留；删旧 handler 与专用样式 |
| 4 | **`useAgent` 内部按状态生命周期拆分**：历史、提交恢复、stream/reconcile、Run control 使用已有子模块，外部继续一个 facade | 不把同一组 cursor/generation/refs 复制给多个 hook；一个 SSE owner、一个提交 owner；分切片回归后移除旧内部实现 |
| 5 | **应用内共享层与样式收尾**：有 owner 的文件/查看器/Skill 组件归位，清掉确认无消费者的包装与 barrel，归并受影响的 CSS | 每个删除先查生产、测试、动态入口、scripts、docs 和 selector；保留具名消费者需要的兼容。不要把有职责包装误判为冗余 |

格式化应独立于以上行为切片先后落地，不在一个大提交里同时改布局、状态和缩进。AgentApplicationRoute、RunMonitorPanel 等其他热点另按业务目标处理，不扩入本轮 Agent 聊天页。

### 每个切片的完成条件

- 在真实页面入口只剩一个新实现；旧路径、exports、测试选择器、样式和说明已迁移或给出具名保留消费者。
- 若仅改变内部结构，不改变外观，使用相同数据的渲染比较；有意改变页面时按现有视觉合同验收，不将样稿批准当成页面验收。
- 没有为了维持旧 source regex 恢复旧代码，没有新增第二套消息/上传/提交/滚动控制。
- 更新该切片所属文档与 PR 的 Retirement / Compatibility Disposition；本文不代替实际库存和检查证据。

## 7. 验收场景与检查选择

| 场景 | 必须观察的结果 | 优先复用的检查 |
| --- | --- | --- |
| Agent 新任务及历史绑定 | 并发首发只建一次；错误 Agent/Session 不展示旧内容；撤回后历史只读；草稿交接不丢失 | Agent workspace/market binding 与 routed Session 回归 |
| 普通聊天共享消费者 | 同组模型、附件、发送、历史恢复与停止不因 Agent 页面重构改变 | `test:sse`、模型/上传 owning mounted 回归 |
| Run 任务问答 | 普通输入不可交互；逐题草稿，最终整批一次提交；受理后恢复原草稿/附件，停止同一路径 | Run inputs、question/Composer mounted 回归 |
| 混合消息与终态 | 正文、tool、todo、subagent、artifact 顺序/身份一致；复制只有完整公开答案；失败/取消/同步区分 | runtime/parts/projection/terminal 回归与实际混合消息 fixture |
| 文件与预览的迟到结果 | 切换账号/Session/路由后，旧 import、上传、预览、下载回执不污染新页面 | 文件、预览、外部导航与身份切换 owning 回归 |
| Agent 页面实际质量 | 开场、输入、长执行活动、问答、产物与失败可达；主题、390px/桌面、低高度、焦点/滚动按受影响风险检查 | 现有 route-layout browser smoke、专业预览、1000 消息性能 fixture |

执行入口和证据复用遵守[变更验证](../agent-rules/change-verification.md)、[前端指令](../../frontend/web/AGENTS.md)和[本地测试](../agent-rules/local-test-execution.md)。不因为拆文件就新建整套测试平台。增加页面导出/测试脚本或 selector 时，同时盘点根 Python 合同、CI/package scripts 与浏览器脚本。

本设计的文档检查不证明功能迁移、页面视觉、实时模型、CI 或部署验收。任何协议、权限、数据或终态含义变化都超出内部结构重构，需要单独说明并按现有高风险流程评审。

## 8. 为什么选择这条路线

- **每页独立一套基础库**的优点是表面边界简单，但 Agent 和普通聊天已共享运行链，会复制 Composer、消息与交互修复；不采用。
- **全部从 primitives 自研**灵活，但扩大样式和无障碍维护，背离成品组件优先的目标；不采用。
- **所有 UI 都堆进 `components/common` 或 `panels`**容易集中查找，但不能区分基础交互、领域展示与页面编排；不继续扩大这种收纳方式，按第 4 节逐步归位。
- **页面装配 + 共享聊天能力 + 平台状态适配**保留现有可靠数据链，同时允许 Agent 页独立收敛，迁移可以逐条删除旧路径；采用。

外部参考只支撑责任划分，不作为本项目目录、依赖或视觉要求：

- [LobeHub Code Ownership](https://github.com/lobehub/lobehub/blob/canary/AGENTS.md)、[目录说明](https://github.com/lobehub/lobehub/blob/canary/docs/development/basic/folder-structure.mdx)：本设计第 4 节的主要组织参照；共享组件、领域功能、薄页面与状态/请求职责分离。
- [Dify UI package boundary](https://github.com/langgenius/dify/blob/main/packages/dify-ui/AGENTS.md)：基础控件不依赖路由和业务状态。
- [OpenHands Agent Canvas](https://github.com/openhands/openhands/blob/main/docs/architecture.md)：组合聊天与工作区，前端不执行 Agent 动作。
- [LangChain Agent Chat UI module graph](https://github.com/langchain-ai/agent-chat-ui/blob/main/_autodocs/README.md)：页面组合 Thread、Stream、Artifact；不是已确认基于 assistant-ui 的项目。
- [assistant-ui Elements 与 Primitives](https://www.assistant-ui.com/docs/primitives)：先使用带样式成品，缺少所需交互时才下降到 primitives。

外部资料来自官方仓库的 main/canary 文档索引，未绑定同一发布提交。它们说明组织思路，不证明外部每个实际页面或本项目当前页面已经达到视觉质量。
