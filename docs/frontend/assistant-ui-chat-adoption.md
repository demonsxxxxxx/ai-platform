# assistant-ui 聊天接入合同

AI 聊天交互使用 **assistant-ui**；系统通用控件和主题使用 **Lobe UI**。聊天中的输入框、按钮、弹窗、选择器仍用 Lobe 外观，通过 assistant-ui 的公开 primitives 组合行为。新交互先检查本合同及库的公开能力，不另起自研聊天状态机，也不并行引入 `@lobehub/ui/chat`。

## 当前版本与入口

仓库锁定 `@assistant-ui/react@0.15.16`，对应安装的 `@assistant-ui/core@0.3.15`。本次不升级依赖。业务代码从 `@assistant-ui/react` 公开入口导入；包内部源码只用于核验，不能成为生产 import。

`ChatView → AssistantUiProjection → useAssistantUiExternalStoreRuntime` 将平台消息和 Run 状态接入库。`useAgent` 仍拥有 SSE、历史恢复、Run/Attempt、取消回执和会话身份；平台的权限、公开投影和文件安全边界遵守[聊天投影](../architecture/chat-run-lifecycle-and-public-error-projection.md)、[SSE wire](../architecture/redis-streams-sse-wire-protocol.md)及[文本 part 合同](../implementation/streaming-message-parts-design.md)。

## 已接通的交互

| 用户操作 | assistant-ui 能力 | 平台/业务适配责任 |
| --- | --- | --- |
| 阅读消息与流式更新 | `ThreadPrimitive.Root`、`Unstable_MessageById`、`MessagePrimitive.Root` | 接收公开消息；保留 Virtuoso 的虚拟列表、滚动恢复和专业 part renderer |
| 输入、发送 | `ComposerPrimitive.Root/Input/Send` | `ChatInput` 注册完整提交：文本、选项、附件、显式 Skill；权限、上传中、重复提交门控沿用业务 owner |
| 停止生成 | `ComposerPrimitive.Cancel` | 打开现有确认框；仅确认后调用 Run 控制，收到取消回执后更新状态 |
| 选择开场问题 | `ThreadPrimitive.Suggestion`，`send={false}` | 写入当前会话草稿，用户确认发送；不隐式创建 Run |
| 复制用户消息/助手回答 | `MessageProvider`、`ActionBarPrimitive.Root/Copy` | 仅传入可复制的公开文本；助手仍通过 `selectAssistantCopyText` 选择最终回答，排除 work 和待确认 preview；HTTP 无 Clipboard API 时保留平台兼容路径 |
| 显示运行错误 | `ErrorPrimitive.Root` | 渲染平台已分类、翻译的安全文案；不使用默认 raw-error 文案 |
| 查看工具活动 | 现有公开 tool part renderer，位于消息框架内 | 保留操作白名单、授权显示名和状态；非展开状态是文本而非伪按钮，成功/失败/取消颜色由独立容器承担 |

`PlatformComposerBinding` 只连接上述 UI 与现有提交、草稿及停止确认，不创建新的服务端会话或 Run 状态。没有挂载输入器或没有发送权限时，原生发送同样不可用。

## 草稿、输入法与事件组合

持久草稿快照是跨布局重挂载、首次会话交接和失败恢复的权威。assistant-ui 会在派发时乐观清空输入；适配层只在平台接受提交且草稿 revision 未变化时清除快照。失败、上传中或权限拒绝不能丢失草稿；异步回执不能覆盖用户后续输入或其他会话的草稿。

使用 `ComposerPrimitive.Input asChild` 包住支持 ref/属性转发的 Lobe TextArea 组合。`submitMode="none"` 保留当前中文输入法、历史上下键、换行偏好及 slash 菜单；业务 Enter 和发送按钮统一经原生 form submit 进入 Composer。禁用库的自动粘贴附件处理，由同一平台上传 owner 处理粘贴与拖放，避免重复上传。

`asChild` 不是绕过 Lobe 样式合同的方式。通用控件外观遵守 [Lobe 接入合同](lobe-ui-adoption.md)，布局使用业务容器；必须检验最终 DOM、computed style、键盘、焦点和 Portal。

## 已核验但不启用的库能力

0.15.16 的公开组件还包含 Attachment、MessagePart、BranchPicker、编辑/重新生成、语音和工具交互等能力。能力存在不代表后端支持对应业务语义。

- `onReload` 表示重新生成助手消息，不能映射到 SSE 重连；已移除该错误映射。连接恢复仍使用现有 `ChatConnectionStatus`/平台 reconnect，`onRefetchThread` 只刷新历史。
- 编辑、分支切换、重新生成需要平台的分支/Run 契约和回归后才可启用；当前不展示无效操作。
- 不启用客户端 tool invocation，不把 MCP 工具授权或执行迁入浏览器。
- 当前附件需要已授权上传、ProfileDrive 导入、可取消上传、预览 URL 与 session scope。保留 `ChatInputAttachments`、`useFileUpload` 和消息附件 renderer；后续使用 Attachment primitives 时必须接入同一控制器并证明不会双重上传或泄露内部字段。
- 不因引入语音、反馈、导出等库能力而虚构存储/API 合同。

## 文件面板布局与滚动

`LibreChatShell` 是聊天区与文件区的几何 owner。视口至少 1280px 时，打开文件区得到同一行的聊天列和 18–20rem 右栏；关闭后聊天占满。桌面 Grid 中只有聊天列和文件栏，遮罩不能成为 Grid 子项。文件入口占用独立的顶部空间，不覆盖第一条消息。

小于 1280px 时文件区使用受控 Lobe Drawer，遮罩、Escape、焦点陷阱和滚动锁由库提供；关闭后焦点回到入口按钮。`LibreChatSidePanel` 内部滚动不提升为整页滚动。文件树、个人/公共盘、导入/拖放与授权仍由 `ProfileDriveWorkspaceBrowser` 拥有。专业文档结果面板、代码编辑器、全局导航和任务历史保留各自布局与生命周期。

## 兼容与退役清单

| 表面 | 当前 disposition / 移除条件 |
| --- | --- |
| 手写 form 的直接提交、手写用户复制计时器、助手复制按钮、开场问题手动赋值 | 已替换为上述原生 primitives；调用方和 owning tests 同步更新，不保留第二套活跃入口 |
| `onReload → reconnect` | 已移除；重新生成和重连不互相冒充 |
| Lobe Button 充当文件遮罩/Grid 子项 | 已移除；手机 Drawer 与桌面 aside 分开组合 |
| `ChatInput` 与 `ChatInputToolbar` 文件名/props | 保留为平台业务适配入口，当前消费者是 `ChatView`；可在全部草稿、命令、附件及 Skill 消费者迁完时移除，不能再扩展平行 submit runtime |
| Virtuoso / `useMessageScroll` | 保留为长历史、自动跟随和滚动恢复 owner；用库 Viewport 替换前须证明大历史性能及当前滚动回归等价，不叠加第二个纵向滚动 owner |
| `ChatMessage` / Markdown、artifact、文档 renderer | 保留公开 part 语义和专业交互；assistant-ui 负责消息框架及 action，库默认 renderer 不能绕过平台投影 |
| `ChatInputAttachments` / 上传与 ProfileDrive 控制器 | 保留上述授权、取消、URL 生命周期和草稿范围；以同等 owning 回归作为后续替换条件 |
| 历史 LibreChat/release-evidence 文档 | 保留历史来源；当前聊天行为以本文为准 |

## 验证与上游依据

变更至少覆盖 `test:sse` 中的键盘、草稿重挂载、原生 message actions、公开投影与终端恢复，及受影响的 `test:profile-drive`、文件栏布局和文档预览测试。脚本及 selector 变更还需运行根 Python 前端合同。实际浏览器用合成数据验证桌面/390px、明暗主题的输入、复制、工具状态颜色、文件栏几何、Drawer 焦点和内部滚动；测试通过不代表真实模型或部署完成。

当前 API 的核验来自安装版公开类型与实现，并交叉参考上游文档：

- [ExternalStoreRuntime](https://www.assistant-ui.com/docs/runtimes/custom/external-store)
- [Composer primitives](https://www.assistant-ui.com/docs/api-reference/primitives/composer)
- [Message actions](https://www.assistant-ui.com/docs/api-reference/primitives/action-bar)
- [上游 external-store 文档源码](https://github.com/assistant-ui/assistant-ui/blob/main/apps/docs/content/docs/runtimes/custom/external-store.mdx)

上游 main 文档可能随版本变化；升级时以锁定版本的公开导出及实际调用方回归复核本表。
