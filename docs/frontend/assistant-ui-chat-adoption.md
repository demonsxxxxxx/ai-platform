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
| `run-activity.tsx`、`tool-call.tsx`、`tool-error.tsx` | `ChatMessage/elements/RunActivity.tsx`、`ToolCall.tsx`、`ToolError.tsx`；MessageWorkActivity、ToolCallItem | 顺序、折叠、局部问题计数、明确生命周期状态；无 raw request/result fallback |
| `subagent-list.tsx`、`task-card.tsx`、`todo-list.tsx` | `ChatMessage/elements/TaskCard.tsx`、`SubagentList.tsx`、`TodoList.tsx`；SubagentBlocks、TodoBlock | 公开父子关系、三级递归与持久面板导航；只显示真实进度，Todo 保留平台三种状态 |
| `artifact-card.tsx` | `ChatMessage/elements/ArtifactCard.tsx`；MessagePartRenderer | 已登记公开产物和原授权下载/预览；不发明 registered/delivery_scope 字段或新准入条件 |
| `thread-list.tsx` | `components/panels/SidebarParts/ThreadList.tsx`、`components/panels/SidebarParts/sessionThreadListAdapter.ts`；SessionListContent、SessionItem | 受控行、Lobe 按钮、可访问未读；原专家/时间分组、loadMore、迟到改名删除隔离 |
| `option-list.tsx`、`question-flow.tsx` | `assistant-ui/elements/option-list.tsx`；RunQuestionCard、RunInputHistory | Lobe 控件、自由文本及受控 choice；移除 fulfilled 后内部 confirmed，不接工具审批 |
| `message-timing.tsx` | `assistant-ui/elements/message-timing.tsx`；ChatMessage | 平台提供的 token、耗时、模型、时间；缺数据隐藏，不估价、不冒充整个 context 占用 |

`ActivityErrorBoundary` 是平台新增的局部异常边界，只显示固定安全提示，公开 part 替换后允许恢复。上游 raw 工具明细、不匹配的默认 Continue/Discard、自动发送建议问题没有进入生产。开场建议仍先填草稿。

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

## 退役与具名保留

- LibreChat Composer/CommandMenu/Chips、ComposerModelPanel、MessageFrame 旧入口和 exports/对应 selectors 已替换，旧专用 CSS 清退。业务控制器继续在 ChatInput，不增加第二 form、上传器或提交路径。
- 旧 token 明细手写点击外部监听及 guard 退役，由 MessageTiming + Lobe Popover 承担。
- 活动旧展示壳由 Elements 接管；公开分类、顺序、折叠、面板导航、下载与预览 controller 保留。
- MarkdownContent 保留显式业务内容/图库适配；独立文档直接走共享 renderer，主消息正文不再使用旧直接 parser。
- LibreChatShell/SidePanel 继续拥有桌面聊天列/文件栏和移动 Lobe Drawer；ProfileDriveWorkspaceBrowser 保留双盘、懒加载、导入、拖放。历史 release evidence 和来源许可不改写。
- 升级检查真实消费者、旧入口/export、静态断言、脚本、依赖和文档；不得为旧 source regex 恢复被替换代码。

## 验证要求

复用 package.json / 前端 workflow，首次依赖接入执行 `ci:verify`。控制器/原生附件挂载、Markdown 独立预览、问答 receipt 和会话注册回归纳入 owning stages。脚本/selector 变化经 `tools/run_test_stage.py` 执行相应 Python 合同，文档经 `tools/check_documentation.py` 检查。

真实渲染使用 mock-backed fixture，覆盖明暗主题与 390/768/1440/1920、中文/IME、浮层与焦点、实际颜色/几何、富文本、附件、停止和失败；固定 1000 消息加长工具活动样本检查虚拟化及输入/滚动。合成证据不当作真实模型、CI、发布或部署证明，实际结果记录在本次交付证据中。

`route-layout-browser-smoke.mjs` 的 `chat-integration` 场景检查文件抽屉、问答回执、Mermaid、工具/子代理/产物和模型选择器；设置 `AI_PLATFORM_FRONTEND_URL` 指向本地 Vite，分别以 `AI_PLATFORM_ROUTE_LAYOUT_THEME=light`、`dark` 运行，默认覆盖四种宽度。

`node scripts/chat-performance-browser-check.mjs` 使用同一合成 fixture 的 `?performance=1` 模式：1000 条消息、50 条长工具活动和 20 次正文追加，一次预热后记录三次测量。脚本检查虚拟化挂载数量、完整最终文本、工具数量和底部可达性。对照两版时使用同一浏览器、依赖和输入，顺序运行并避开并行测试/构建；用 `AI_PLATFORM_ROUTE_LAYOUT_SMOKE_DIR` 分别保存结果，不以单次耗时推断提速。
