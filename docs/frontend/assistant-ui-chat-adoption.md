# assistant-ui 聊天接入合同

聊天界面优先采用 assistant-ui Elements 成品源码，通用控件、主题和浮层使用 Lobe UI。平台继续拥有消息、会话、提交回执、文件授权和执行状态。完整选型、128 项目录处置及验收要求见 [issue #1849](https://github.com/demonsxxxxxx/ai-platform/issues/1849)。

## 固定版本与来源

| 项目 | 当前版本/来源 |
| --- | --- |
| runtime | `@assistant-ui/react@0.15.29` |
| Markdown primitive | `@assistant-ui/react-markdown@0.14.23` |
| 锁文件中的 core / store / tap | `0.3.29 / 0.3.21 / 0.9.23`，由 runtime 传递引入 |
| Elements 源码 | assistant-ui `727a02dc3fe5286ded479cb792f48acbff336cd7` |
| 通用控件与工具链 | Lobe `5.61.0`、React `19.2.5`、Tailwind 3、TS 5.6、Vite 6 保持 |
| 显式 content Markdown | 直接依赖解析为 react-markdown `9.1.0`；primitive 使用 `10.1.0` |

`@assistant-ui/ui` 是上游 private 源码包，不作为 npm 依赖安装。npm 升级不会更新已引入的 Elements；升级时分别审查公开 API、锁文件和下表的源码差异。完整 MIT 许可保留于[第三方声明](../../frontend/web/THIRD_PARTY_NOTICES.md)。不引入第二套 shadcn 通用控件、云端会话副本或仅为示例外观所需的新渲染依赖。

## 一条数据链与各自的事实来源

`Claude SDK / SessionStore → 平台公开事件 → SSE v4 / 历史恢复 → useAgent → ExternalStoreRuntime → Elements`。

- `useAgent` 和原 Run 控制器拥有身份、权限、Run/Attempt/lease、游标、重连、回放、取消及终态。UI 不凭 SSE EOF、Promise fulfilled 或动画结束判断任务成功。
- `ChatView` 将合并授权附件后的 `displayMessages` 同时交给 runtime、图库和 Virtuoso。消息通过公开 `getExternalStoreMessages` 读取 runtime 绑定快照；布局与 part 投影共用 `getAssistantUiDisplayParts` 的顺序。
- `PlatformComposerBinding` 连接实际输入器、完整提交事务和附件 adapter。无输入器、无权限、上传中或重复提交时，原生 Send 同样受平台条件约束。
- `SessionSidebar` 从原 session source 派生 `ExternalStoreThreadListAdapter`，经 `ChatAppContent → ChatView` 接入同一 runtime。专家分组、分页、未读、路由和修改回执保留原 owner，没有第二次拉取或第二个会话缓存。
- 原生 tool/data parts 只含获准字段或空占位。业务活动从同一公开快照取 props，禁止客户端 tool execute/addToolResult、原始 args/result、私有命令/路径和隐藏思考。安全正文中用户需要的代码、JSON 和项目路径正常显示。

平台合同见[聊天终态与公开投影](../architecture/chat-run-lifecycle-and-public-error-projection.md)、[SSE wire](../architecture/redis-streams-sse-wire-protocol.md)、[文本 part](../implementation/streaming-message-parts-design.md)和[Claude 连续性](../architecture/claude-provider-session-continuity.md)。Claude Clone 是视觉示例；Managed Agents 的托管 session/sandbox 不替代本项目 Python SDK 与平台执行链。

## 源码与生产消费者对应

上游路径均位于固定提交的 `packages/ui/src/components/react/assistant-ui/elements/`。下表聊天路径相对 `frontend/web/src/components/chat/`；带 `components/` 前缀的路径相对 `frontend/web/src/`。只取实际使用的组合，不复制未启用的操作或完整示例应用。

| 上游来源 | 本地 owner / 生产消费者 | 适配与升级检查 |
| --- | --- | --- |
| `thread.aui.tsx` | `components/chat/assistant-ui/elements/thread.tsx`、`assistant-ui/MessageActions.tsx`；Projection、ChatView、ChatMessage、UserMessageBubble | 消息/操作结构、完整公开回答复制、中文与 Lobe 控件；不导入 Viewport 自动滚动或 Edit/Reload/Branch/Export |
| `composer.tsx`、`thread.aui.tsx` | `assistant-ui/elements/composer.tsx`、`composer-model-picker.tsx`、`composer-chips.tsx`；ChatInput、Toolbar、Selectors、SlashCommandMenu | native form/Input/Send/Cancel、受控菜单和 chips；原 slash/Skill/MCP/模型/键盘控制器；Lobe Portal、公开 style、Tailwind 3 |
| `empty-state.tsx`、`loading-state.tsx`、`connection-state.tsx` | 同名 Elements；WelcomePage、MessageParts、ChatConnectionStatus | 单一等待/生成/同步反馈，重连保持原入口；不添加无接口的 Retry/Continue |
| `markdown-text.tsx` | `assistant-ui/elements/markdown-text.tsx`、`ChatMessage/MarkdownRenderer.tsx` | primitive、memoized slots、CodeHeader；共享 GFM/breaks/math/KaTeX、安全链接/图片、CodeMirror/Mermaid、表格导出和标题锚点 |
| `attachment.aui.tsx`、`message-attachment.tsx`、`file.tsx` | `components/common/AttachmentCard.tsx`、`ChatInputAttachments.tsx`、`assistant-ui/MessageAttachments.tsx` | 上传卡和已发送文件展示；安全图片 URL、取消/删除、专业预览；上传只由 useFileUpload 执行 |
| `run-activity.tsx`、`tool-call.tsx`、`tool-error.tsx` | `ChatMessage/elements/RunActivity.tsx`、`ToolCall.tsx`、`ToolError.tsx`；MessageWorkActivity、ToolCallItem | 顺序、折叠、局部问题计数、明确生命周期状态；正常技术阶段不逐条展示，执行中保留最新有意义进展，连续成功工具合并为可展开记录且默认隐藏耗时；无 raw request/result fallback |
| `subagent-list.tsx`、`task-card.tsx`、`todo-list.tsx` | `ChatMessage/elements/TaskCard.tsx`、`SubagentList.tsx`、`TodoList.tsx`；SubagentBlocks、TodoBlock | 公开父子关系、三级递归与持久面板导航；只显示真实进度，Todo 保留平台三种状态 |
| `artifact-card.tsx` | `ChatMessage/elements/ArtifactCard.tsx`；MessagePartRenderer | 已登记公开产物和原授权下载/预览；不发明 registered/delivery_scope 字段或新准入条件 |
| `thread-list.tsx` | `components/panels/SidebarParts/ThreadList.tsx`、`components/panels/SidebarParts/sessionThreadListAdapter.ts`；SessionListContent、SessionItem | 受控行、Lobe 按钮、可访问未读；原专家/时间分组、loadMore、迟到改名删除隔离 |
| `option-list.tsx`、`question-flow.tsx` | `assistant-ui/elements/option-list.tsx`；RunQuestionCard、RunInputHistory | Lobe 控件、自由文本及受控 choice；移除 fulfilled 后内部 confirmed，不接工具审批 |
| `message-timing.tsx` | `assistant-ui/elements/message-timing.tsx`；ChatMessage | 平台提供的 token、耗时、模型、时间；缺数据隐藏，不估价、不冒充整个 context 占用 |
| `follow-up-suggestions.aui.tsx` | `assistant-ui/elements/follow-up-suggestions.tsx`；`assistant-ui/FollowUpSuggestionsGate.tsx`、`assistant-ui/useFollowUpSuggestions.ts`；ChatView | Lobe 公开 Button 外观、横向滚动与边缘渐隐、ResizeObserver；建议正文只来自平台服务端生成，条目数、草稿/附件/发送门控由平台适配 |

`ActivityErrorBoundary` 是平台新增的局部异常边界，只显示固定安全提示，公开 part 替换后允许恢复。上游 raw 工具明细、不匹配的默认 Continue/Discard 没有进入生产。开场建议仍先填草稿。

## 模型生成的后续建议

`POST /api/ai/chat/follow-up-suggestions`（`conversations` transport，装配见 `bootstrap/follow_up_suggestions.py`）只接受 `session_id` 与 `run_id`，由服务端按当前 principal 重新授权，绝不接受浏览器提供的消息正文、模型或端点。目标 Run 必须是该 Session 当前代次、状态 `succeeded`、其后没有更新的用户消息，且存在公开助手正文；否则返回 404 且不调用模型。

输入只包含该 Run 收敛前的近期公开 `user`/`assistant` 正文（经 `platform.public_payload` 脱敏，单轮与总长有界），不含 draft、附件原始内容、工具 raw 数据或 Profile 私有指令。生成走目标 Run 自身的冻结模型快照（`runs.model_id/model_value/model_gateway_revision`）经 Execution 所属的受控上游适配器，单次非流式、`tools: []`、输出上限 256 token；该模型被禁用/下线时不回退到其他模型，而是返回空。

输出必须是至多 3 条、每条不超过 60 字的去重中文问题，非法类型、超长或空结果一律丢弃，不把 provider 错误或私密原文回给用户。同一 tenant/user/session/run 经 Redis 单飞声明与 10 分钟缓存合并并发和回放，失败不缓存空结果。前端只在“本轮观察到的完成且成功、发送可用、草稿为空且无附件”时请求一次；账号、会话或轮次变化立即 abort 并清空旧回执，失败不标主 Run 失败。点击经原有 `PlatformComposerBinding.send` 与 `composerSubmission` 事务发送，因此仍受原附件/权限/提交锁约束。

退役与兼容：本能力无替代的上游自动发送实现，静态 starter prompts 仍只是填充草稿（`send={false}`），两者不互换。上游 demo 的 props 型 suggestions API 不是本仓库 pin 版本；升级 assistant-ui 时须复核 `ExternalStoreAdapter.suggestions`、`ThreadPrimitive.Suggestion` 与 `useAuiState` 的 `thread.suggestions`。

专家新任务直接把已发布 Profile 的 `description` 原文作为开场正文，不新增配置。描述为空时不补默认问候；有历史消息时不重复展示。开场内容不创建消息、Session 或 Run，也不进入模型输入、持久历史或复制内容。

专家新任务直接把已发布 Profile 的 `description` 原文作为开场正文，不新增配置。描述为空时不补默认问候；有历史消息时不重复展示。开场内容不创建消息、Session 或 Run，也不进入模型输入、持久历史或复制内容。

## 开场布局与模型就绪状态

普通新对话的欢迎语、快捷任务和输入框沿用同一条 1216px 内容栏，快捷任务与输入框对齐，输入框紧随任务入口，不再靠 `mt-auto` 分离到页面底部。有消息时仍使用原底部 Composer slot，桌面底部留 24px，手机保留安全区；专家开场、历史加载及待答任务表面保持各自原有入口。布局不增加第二个 Composer 或草稿 owner。

模型就绪提示由 ChatAppContent 的当前目录/选择状态派生，随 ChatView → ChatInput 进入工具栏，在模型选择器旁使用 `--theme-danger-text` 显示提示；无可用模型时提示“当前没有可用模型，请联系管理员申请。”，不再在页面底部另放模型错误。未选可用模型时输入和附件草稿仍保留，发送按钮、Enter 与原生 Composer 注册共同阻止发送。聊天输入区不提供模型组配置入口，所有用户都只看到状态提示。加载、读取失败、授权失效和默认模型失效保留各自原因，不自动切组或用第一项回退。服务器模型准入、自动唯一组绑定与冻结 Run 快照未变。

## 提交、附件与迟到回执

`composerSubmission.ts` 是 native 发送与键盘提交共用的事务：保留文本、模型选项、授权附件和显式 Skill，以及 draft revision、selectedSkillRevision 和首次会话 handoff。仅对应请求 accepted 才清理其内容；后续编辑或清空不被旧回执覆盖。scope revision 隔离账号/会话，旧发送、上传、取消回执不修改新页面。

assistant-ui 0.15.29 会恢复被 `MessageNotSentError` 拒绝的原生草稿及附件。适配层不能在抛出该错误前再次恢复同一文本，否则会重复拼接；已变化的草稿继续由平台快照恢复。

`useFileUpload` 保留唯一队列、并发、压缩、去重、类型/数量/大小验证、重试和取消/迟到成功清理。task handle 暴露同一任务的 File 身份、完成结果和状态。Composer 选取、粘贴、区域拖放先经该控制器验证，再通过一个 AttachmentAdapter 启动同一任务。完整发送仍使用平台授权引用；原生 CompleteAttachment 仅有显示元数据和空 content。页面级拖放、ProfileDrive 导入和既有引用由原 owner 管理并以受控 props 展示，不伪装为 File，不创建另一上传路径。

`SessionImageGalleryProvider` 是会话图片唯一 host。`AttachmentPreviewHost`、`RevealPreviewHost`、专业文档/编辑器各有独立职责；图片继续经过安全 URL 与授权 blob 策略，对象 URL 生命周期不交给示例组件。

## 执行、连接、同步与动画独立

| 事实 | 展示与操作 |
| --- | --- |
| 等待首段 / 正在追加 | 一处 GenerationLoader；只有当前追加且未收敛的 assistant 正文允许平滑 |
| Run 仍运行而连接中断 | 保留正文，使用原 reconnect；不映射为 message reload/regenerate |
| 取消请求 acknowledged | 表示请求已受理；等待权威取消终态，保留确认框与 RunControlLifecycle |
| Run 已结束而内容仍同步 | runtime 保留终态，metadata 独立标记同步；正文完整显示，暂不显示复制 |
| 失败、取消、结果不可用或历史 | 显示已接受的完整文本和安全分类，不继续动画 |
| 工具失败 / 拒绝 / 取消未确认 | 明确状态决定颜色/图标；running=false 不代表成功 |

普通聊天正文只由 MarkdownTextPrimitive 解析和平滑，外层 Text smooth 关闭，defer 固定。减少动态效果时立即显示完整文本；动画不改变最终答案或复制来源。显式内容的文档预览、公开 work/subagent 内容及专业摘要使用共享 MarkdownRenderer，不依赖 message-part context；插件和安全组件与聊天入口共用，每段内容只解析一次。独立文档保留 500,000 字符截断。react-markdown 9.x/10.x 暂留对应真实消费者；统一版本前须验证这些消费者、插件、类型和专业预览。

Virtuoso/useMessageScroll 保持大历史、返回底部、Run/文件深链和大纲定位；没有第二个 Viewport 滚动写入者，也不引入 TanStack 虚拟器。

## 会话与问答

会话 adapter 仅派生列表并委托原操作，身份/source 失效后的回调不能更新新 owner。改名复用原 API，删除仍先确认；消息历史与会话列表分别分页，已加载项过滤不称作全量搜索。

Run 问答只使用一个 RunInputsController：单选、多选、自由文本、补充文本、默认值、deadline、uncertain 与历史回执保持。answered 表示已受理，resolved 表示问题已处理，均不代表 Run 完成。uncertain 重试使用原 input_id 和答案，前端倒计时不提交默认答案。OptionList choice 来自平台，不使用工具 addResult 或批准接口。

一个待答复输入仍是“一个 question_id 下的整批问题”（服务端 `canonicalize_answers` 要求答案键集合等于该批全部问题，provider `_native_answers` 也需要整批答案才返回一次工具结果）。因此多问交互采用约定的协议 A：**逐题本地草稿，最后整批一次提交**。RunQuestionCard 用一处编号进度一次只显示一题，上一题/下一题只在本组件内部移动草稿，不写服务器、不标记受理；最后一题的“提交答复”才通过原 `submitAnswers` 发送整批答案和唯一稳定的 `input_id`。只有服务端实际受理回执才把该批显示为“答案已提交，等待当前任务继续”，与“问题已处理”和 Run 完成都不同。Enter 只在答复自由文本框内作为推进/提交快捷方式，且跳过 IME 组合（`isComposing`/`keyCode 229`）；选项、上一题和重试按钮保留原生键盘行为，Shift+Enter 保持换行。

待答期间，`ChatInput` 的 `taskSurface` **替换**普通输入控件，而不是在它上面堆叠第二个输入区；普通 Composer、草稿和附件 owner 保持挂载但不可交互，原表单发送、文件拖放和隐藏输入入口均被门控。题目受理或关闭后恢复原草稿与附件，不重新创建空输入器。问答页脚的停止入口仍经过同一确认框与 RunControlLifecycle。

问答表面按内容自然展开：常规 2–4 个短选项、题目及操作无需内层滚动即可完整展示；选择题的“填写其他答案”按需展开，纯文本题默认显示输入框，返回题目或改用选项不会销毁已填的其他答案。编号进度只显示一处，最后一题才提交整批。很长的题目或小视口保留一个视口约束的任务表面滚动回退，不截断问题、隐藏选项或缩小文字来伪装无需滚动。普通输入器原有高度与上传约束不变。

已受理答复显示简短状态，完整答复与关闭、超时默认、未处理历史都默认折叠在“历史答复”入口中，包括没有当前开放 Run 时。展开后全部历史 Run 的标题和回执共用一个有界滚动区；分页增加只增加内部滚动长度，不挤占当前问题或原输入器。历史失败/重试和分页入口保留，展开状态不跨会话/Run 或新题目沿用。

## 退役与具名保留

- LibreChat Composer/CommandMenu/Chips、ComposerModelPanel、MessageFrame 旧入口和 exports/对应 selectors 已替换，旧专用 CSS 清退。业务控制器继续在 ChatInput，不增加第二 form、上传器或提交路径。
- 普通 chat 与 expert 的模型组选择入口及其 props/callback 已移除，无兼容消费者。ModelCatalogProvider 每次刷新只接受服务器返回的唯一启用归属组；默认组仅在可信专家入口可用，普通 chat 仍拒绝；多组、禁用或未授权均拒绝猜测/回退，归属仅由管理员修改。focus/visibility 与模型菜单刷新重新加载当前归属；历史 Session 的 group_id 不再重定向当前归属，Run 的冻结快照不变。保留同组模型选择和显式 group_id/model_id/model 提交；默认模型按服务器指定 ID 解析，缺失时不选第一项。
- Composer 附件使用中性主题卡片、完整文件名提示及常显移除/取消按钮，长名省略、多文件横向滚动。消息附件、授权、上传状态及唯一上传控制器不变。
- Composer 上传附件只由 `ChatInputAttachments` 展示；逐附件镜像 `file` chip 及其专用删除分支、文案已退役，无兼容消费者。已发送文件和正文归入同一用户消息容器，继续沿用原文件预览和授权入口。
- 旧 token 明细手写点击外部监听及 guard 退役，由 MessageTiming + Lobe Popover 承担。
- 活动旧展示壳由 Elements 接管；公开分类、顺序、折叠、面板导航、下载与预览 controller 保留。
- MarkdownContent 保留显式业务内容/图库适配；独立文档直接走共享 renderer，主消息正文不再使用旧直接 parser。
- LibreChatShell/SidePanel 继续拥有桌面聊天列/文件栏和移动 Lobe Drawer；ProfileDriveWorkspaceBrowser 保留双盘、懒加载、导入、拖放。历史 release evidence 和来源许可不改写。
- 升级检查真实消费者、旧入口/export、静态断言、脚本、依赖和文档；不得为旧 source regex 恢复被替换代码。
- Run 问答逐题化替换了旧的独立问答区与“全题同表”卡片：独立区总标题 `当前任务输入`、卡片外框/阴影、内层 `<form>` 及其 submit 按钮不再存在，历史分组标题仍保留在共享滚动区内，生产消费者只剩 `ChatInput` 的 `taskSurface` slot（由 `ChatView` 传入 `RunInputHistory`）。`data-question-mode`、`composer-ordinary-controls`、`data-run-question-card`/`-index`/`-marker`/`-progress`/`-advance`/`-previous`/`-other-toggle`/`-other-clear`/`-batch-note`/`-stop` 是当前模式与问题选择器；旧 `data-run-question-filled` 双重进度、始终展开的其他答案输入、问答与普通输入同时显示的布局已退役，无兼容消费者。`data-run-input-history-disclosure`/`-toggle`/`-panel` 拥有默认折叠和唯一历史滚动，`data-run-input-receipts` 只作内容分组；`data-run-input-history`/`data-run-input-history-run`/`data-run-input-entry` 保留原记录范围与历史含义。`fieldset:last-child textarea`、`button[type="submit"]` 和 `data-run-input-history` 上的 30dvh 上限已无消费者，浏览器脚本与回归已改用上述新选择器。服务器合同、存储、超时默认答案与 provider 交互未变，因此没有旧的兼容分支需要保留。

## 验证要求

复用 package.json / 前端 workflow，首次依赖接入执行 `ci:verify`。控制器/原生附件挂载、Markdown 独立预览、问答 receipt 和会话注册回归纳入 owning stages。脚本/selector 变化经 `tools/run_test_stage.py` 执行相应 Python 合同，文档经 `tools/check_documentation.py` 检查。

真实渲染使用 mock-backed fixture，覆盖明暗主题与 390/768/1440/1920、中文/IME、浮层与焦点、实际颜色/几何、富文本、附件、停止和失败；固定 1000 消息加长工具活动样本检查虚拟化及输入/滚动。合成证据不当作真实模型、CI、发布或部署证明，实际结果记录在本次交付证据中。

`route-layout-browser-smoke.mjs` 的 `chat-integration` 场景检查文件抽屉、问答回执、Mermaid、工具/子代理/产物和模型选择器；设置 `AI_PLATFORM_FRONTEND_URL` 指向本地 Vite，分别以 `AI_PLATFORM_ROUTE_LAYOUT_THEME=light`、`dark` 运行，默认覆盖四种宽度。

`node scripts/chat-performance-browser-check.mjs` 使用同一合成 fixture 的 `?performance=1` 模式：1000 条消息、50 条长工具活动和 20 次正文追加，一次预热后记录三次测量。脚本检查虚拟化挂载数量、完整最终文本、工具数量和底部可达性。对照两版时使用同一浏览器、依赖和输入，顺序运行并避开并行测试/构建；用 `AI_PLATFORM_ROUTE_LAYOUT_SMOKE_DIR` 分别保存结果，不以单次耗时推断提速。

`node scripts/composer-trigger-browser-check.mjs` 复用合成聊天 fixture，以真实鼠标按下/抬起事件验证桌面模型按钮、桌面及窄屏设置齿轮的再次点击关闭、重新打开、Escape 和外部点击关闭。独立按钮的开关不再被库的 `outside-press` 抢先处理，其他外部点击仍由库关闭。手机模型选择器保留模态 Drawer 的关闭入口。脚本通过 `AI_PLATFORM_FRONTEND_URL` 指向本地 Vite；仅 DOM `.click()` 不覆盖该指针事件时序。

`node scripts/follow-up-suggestions-browser-check.mjs` 用 `generic-chat-preview` 合成 fixture（屏幕可见“合成预览”标识，后续建议接口由 fixture 内固定文案模拟）验证：恢复历史不请求也不显示、条数/标签/几何、点击一次→新一轮→旧建议被替换、非空草稿隐藏。设置 `AI_PLATFORM_FRONTEND_URL` 指向本地 Vite、`AI_PLATFORM_FOLLOW_UP_SMOKE_DIR` 保存截图与 `report.json`。该证据只证明前端与合成接线，不证明真实模型输出或后端授权。
