# Lobe UI 组件接入与迁移合同

状态：生产调用方已接入 Lobe UI，通用控件迁移及本地回归已完成；6 个无消费者旧模块和 2 个旧直接依赖已退役，选择器测试已按当前组件重命名。组件接入和自动化检查不代表页面视觉验收；页面样式遵守 [LobeHub 视觉约束](lobehub-visual-guidelines.md)，具体样稿须经用户验收。控件源码合同以本文和[表格与选择器说明](shared-table-select-components.md)为准，验收结果须对应实际运行的候选。

## 采用成品组件库，减少项目维护的基础控件

通用 React UI 以 `@lobehub/ui` 为主库。普通按钮、字段、选择器、弹窗、表格和通知优先直接使用其公开组件，不再以项目自研原生控件加 CSS 作为统一路线。Ant Design 是 Lobe UI 的必要依赖，不作为业务页面平行选用的第二套主库。

锁定 `@lobehub/ui@5.61.0`，对应上游提交 `a5f87e9367149dba06a8f476c6e0082ec2287d4d`。升级时先复核本文涉及的公开导出、运行环境、主题及实际调用方，不使用浮动 `latest` 或 beta。

## 使用新入口，避免混入已弃用的旧组件

| 场景 | 首选公开入口与组件 | 迁移要点 |
| --- | --- | --- |
| 按钮、图标操作 | `@lobehub/ui/base-ui` 的 `Button`、`ActionIcon` | 保留 submit/button 语义、loading、disabled、危险操作和可访问名称 |
| 文本字段 | 同入口的 `Input`、`TextArea` | 保留现有受控值、标签关联、错误提示、只读和禁用状态 |
| 选择与开关 | 同入口的 `Select`、`Checkbox`、`Radio`、`Switch` | 保留值类型、表单意义、键盘操作和关闭后焦点恢复 |
| 新表单 | `@lobehub/ui/base-ui/form` 的 `Form`、`useForm` 等 FormKit API | 不使用 base barrel 中已标记迁移到 FormKit 的旧 Form；不借迁移重写业务提交协议 |
| 弹窗与侧栏 | `@lobehub/ui/base-ui` 的 `Modal`、`Drawer` | 验证嵌套弹层、焦点、滚动锁、取消和提交中关闭行为 |
| 管理列表 | 同入口的 `Table`、`Pagination` | 数据列映射、分页及选择状态由所属业务页面维护，不改变查询语义 |
| 菜单与提示 | 同入口的 `DropdownMenu`、`Popover`、`Tooltip`、`Tabs` | 使用库公开交互合同，不重新编写定位、Escape 和焦点状态机 |
| 通知与加载 | 同入口的 `toast`、`ToastHost`、`useToast`、`Spin`、`Skeleton` | 新入口已提供 Toast；不假设存在同名 `Message` API |
| AI 聊天交互 | 固定来源的 assistant-ui Elements 与公开 primitives/runtime | 按[聊天接入合同](assistant-ui-chat-adoption.md)组合 thread、message、composer、actions 和 suggestions；内部通用按钮、输入、弹窗仍使用 Lobe |

Root barrel 中部分组件仍然存在，但旧 Button、Modal 已标记 `@deprecated`。业务控件优先从 `/base-ui` 导入；Provider、国际化及其他只在 root/subpath 导出的能力按各自公开合同使用，不能将“禁止旧控件”误写为“禁止一切 root import”。不从包的私有源码路径导入。

不为每个库组件创建一层无额外职责的同名包装。允许有明确产品职责的业务组合，以及确有调用方合同需要的数据或渲染适配；适配层不得另写一套普通控件样式与交互。

`ErrorBoundary` 位于 Lobe Provider 外，恢复页保留不依赖该 Provider 的原生刷新按钮及其既有样式。错误恢复回归必须在没有 Lobe Provider 的情况下挂载。

## 接入条件与依赖归属

当前前端保留 React/React DOM `19.2.5`、TypeScript `~5.6.2`、Vite 6、Tailwind 3；Node 下限已对齐为 `>=22.22.0`。Lobe UI 及 peers 已写入包清单和锁文件：

- `@lobehub/ui`：`5.61.0`。
- `antd`：`6.3.5`；`motion`：`12.43.0`。
- `@base-ui/react`：`1.8.0`，仅用于 Lobe 组合所需的原生 Field/跨表格 RadioGroup 合同。
- `@lobehub/fluent-emoji`：`^4.1.1`，当前锁定 `4.1.1`；原有 `@lobehub/icons` 保留。

现有 CI/镜像 Node `22.23.2` 满足下限；打包合同与运行环境说明同步维护。构建工具不因 UI 迁移附带升级。

## 沿用现有主题偏好和应用入口

保留 `src/contexts/ThemeContext.tsx` 作为浏览器主题偏好的权威，继续遵守[浏览器偏好合同](browser-local-preferences.md)：没有显式选择时跟随系统，用户显式选择后只写浏览器本地存储，既有 DOM class、系统栏和 meta 更新行为保持。

`App.tsx` 在现有 `ThemeProvider` 内挂载 `contexts/LobeUiProvider.tsx`。该组合读取 `useTheme()` 和当前 i18n 语言，向 Lobe `ConfigProvider -> ThemeProvider` 传入受控明暗状态、中文/英文资源及 motion；路由、认证和模型 Provider 保留原有职责。

Lobe ThemeProvider 自带 AntD App 和 Base UI portal host。`enableCustomFonts={false}`、`enableGlobalStyle={false}` 保留项目字体及 reset；字体通过 `--font-ui` 和公开 theme token 对齐，圆角 token 为 8。普通控件采用库的主题，业务 CSS 负责布局、响应式、品牌导航及专业内容。

使用中需要保留的库合同：

- `Button.type` 表示外观，表单按钮通过 `htmlType` 保留 submit/button 语义。忙碌状态使用 `loading`，库据此设置 `aria-busy`，不能仅传同名原生属性。图标和头像操作使用 `icon`，避免固定宽度与默认内边距挤压内容。目录项、会话项和 Skill 等多行按钮由业务 owner 显式设置 `height: auto` 和内容留白，不能继承普通按钮的固定高度。
- `Input`/`TextArea` 的根节点负责组合布局，原生字段尺寸和滚动样式通过公开 `classNames.input` / `styles.input` 设置。
- 高层 Select 的标签转发有边界；`LobeSelectField` 只负责调用方的可访问标签、布局和嵌套 Escape 隔离，选择交互仍由库拥有。
- 受控 `ModalRoot` 在关闭时会卸载子树。需要常驻的打开按钮放在 Root 外；没有库 Trigger 的弹层显式传入原按钮的 `finalFocus` 引用。
- 组合 Trigger 遇到单个 React 子元素时会克隆它；图标操作应传入实际 Button，不能让 SVG/span 成为伪按钮。
- 使用库 Portal；插件市场认证使用 `ModalRoot`、`ModalPortal` 与公开 Modal 组合，保持浮层位于主题容器内，密码前后缀通过 Input 的 `prefix/suffix` 组合；不保留手写 `document.body` Portal、遮罩按钮或 Tab/Escape/滚动锁。专业查看器自有 Portal 通过公开 `useAppElement` 接入主题范围。嵌套弹层、全屏和侧栏仍需实际检验定位与焦点。
- assistant-ui 的 `asChild` 仅组合能转发 DOM ref、事件和原生属性的 Lobe 控件；聊天语义由 assistant-ui 拥有，外观由 Lobe 公开 API 设置。禁止靠保留 Tailwind `className` 假定覆盖库根样式：定位/Grid 分区放在业务容器，颜色/尺寸使用公开 `style`、`styles`、`size`、`icon`。实际检查 computed style 和几何。
- `ToastHost` 是统一通知宿主。队列通知维持稳定 ID `chat-queue` 和持续显示，关闭/更新使用 Lobe toast 合同。

## 完成范围与退役条件

管理表格已由 MCP、模型、运行监控、运行性能、Skill 管理、工具参数及专家发布历史 7 个实际调用方直接使用 Lobe Table。普通单选通过 Lobe Select 接入；部门/角色、市场标签及聊天目录选择保留业务数据 owner，使用库 Select/Popover/AutoComplete/Modal 交互。

AI 聊天交互采用 assistant-ui，内部通用控件及文档、编辑器外围采用 Lobe；[聊天接入合同](assistant-ui-chat-adoption.md)列出已接通能力和保留的业务适配。9 个页面/面板的 115 个手写加载占位已使用 Lobe Skeleton，业务布局和响应式尺寸保持。Run/SSE、草稿、附件 URL、安全下载和专业渲染仍由平台及各业务 owner 拥有。Markdown/XLSX 表格是 renderer 的语义结构；代码、图形、文档查看器的缩放、图层和几何不是通用管理表格。

以下旧文件已删除：`common/EnterpriseSelect.tsx`、`Checkbox.tsx`、`Table.tsx`、`Pagination.tsx`、`LoadingSpinner.tsx` 和 `skill/Toggle.tsx`；旧导出和生产消费者均已移除，无兼容入口；旧分页、旧 Select 的专用 CSS 和旧 Checkbox 的 `check-pop` 动画也已删除。选择器挂载测试已重命名为 `common/__tests__/LobeSelectField.mounted.test.tsx`。`@radix-ui/react-select` 与 `react-hot-toast` 的直接依赖及对应锁文件 importer 记录已移除；库存回归断言这些旧文件、旧测试文件名和直接依赖保持缺席。

本次清理另外移除无生产调用者的 `skill/SkillCard.tsx`、`panels/AvailableSkillsPanel.tsx`、`chat/ChatInputHelpMenu.tsx`、`chat/ChatInputShortcuts.tsx` 和 `auth/AuthLayout.tsx`。Skill 页面继续由 `SkillsHubPanel → SkillsPanel → SkillsList → SkillManagementTable` 承担；权限、只读隐藏写操作、归档及私有文件内容不展示的回归归到现行 table/panel。登录仍直接使用 `AuthPage`，实际聊天和侧栏快捷键处理、当前 locale/CSS 均保留。旧 `components/librechatShell` 的五个转发文件无兼容入口；真实布局回归迁到 `librechat-ui/__tests__`，公开 `librechat-ui/index.ts` 与上游来源声明不变。

`rehype-highlight` 的旧直接依赖和强制 chunk 条目退出。聊天、assistant-ui 与文档 Markdown 继续共用现有 KaTeX 插件和 DeferredCodeMirrorViewer 代码渲染 owner；Mermaid、`syntax-highlight.css` 以及外部粘贴 HTML 的 `hljs` 识别不在清理范围。锁文件由仓库指定 pnpm 重算，不以手工删除传递依赖代替一致性验证；本段不宣称包体收益、真实页面或部署验收。

保留的传递依赖链为 `@assistant-ui/react@0.15.29 → radix-ui@1.8.1 → @radix-ui/react-select@2.4.1`，由现有聊天 UI runtime 的依赖合同拥有；项目业务代码不直接使用它，也不手工删除它所需的锁文件条目。`frontend/web/THIRD_PARTY_NOTICES.md` 保留已退役适配代码的 MIT 来源许可，不再将旧适配描述为当前实现。

专业查看器保留业务外壳：`ToolResultPanel` / `EditorSidebar` 拥有主内容让位、宽度拖拽、移动手势和面板历史；`SkillForm` 保留同一编辑状态下的全屏模式，退出按钮通过公开 `style` 固定在可见的右上角；Mermaid 查看器保留缩放、平移及图形几何。这些 Portal 通过公开 `useAppElement` 进入 Lobe 主题宿主，工具结果只由面板自身创建一次 Portal。Markdown/XLSX 的语义表格与 Provider 外的 `ErrorBoundary` 刷新按钮仍是明确例外，不恢复旧通用控件。

普通图标按钮使用公开 `icon` 属性；已退役 `btn-icon`、`btn-secondary`、`btn-danger`、`enterprise-form-input` / `enterprise-form-textarea` 及旧企业图标按钮 CSS。记忆表单尺寸改用库的 `size`，仅 `ErrorBoundary` 继续使用 `btn-primary`。共享 `ConfirmDialog` 使用公开 Modal 组合，保留加载时禁止关闭和危险操作，并将焦点还给打开前的控件；Word 中断操作复用它，服务端取消确认仍由审核业务 owner 判断。

窄屏管理表格由 MCP 与运行性能 owner 显式设置列宽及横向滚动；Skill 详情所在单列使用 `minmax(0, 1fr)`，治理状态标签不换行。SOP、Word 使用现有语义主题变量，Word 页头使用 `review-panel-header` 避免继承全局 sticky 样式，手机统计与上传区保持首屏任务优先。相关几何由实际浏览器回归验证，不用页面隐藏溢出来替代内容可达性。

源码库存检查须对应实际调用方和 renderer 例外，不能为保留旧断言而恢复旧组件。组件替换需完成 owning 回归、中文与可访问名称、禁用/提交状态、键盘与焦点、窄屏布局和实际渲染检查。

## 验收与证据

实际挂载测试使用共享真实 JSDOM 和 `createLobeTestRoot`，复用生产主题、国际化及 Portal Provider。SSR 投影检查通过 `createLobeServerTestRoot` 加载真实 motion ConfigProvider，不额外增加 DOM 包装。Node loader 仅适配 rc-table 的 CJS 默认导出，不替换 UI 实现。测试文件并发限制为 2，避免加载组件库的多个 V8 进程同时耗尽本机资源。

- `corepack pnpm run test:browser-preferences`
- `corepack pnpm run test:control-plane-ui`
- `corepack pnpm run test:model-admin`
- `corepack pnpm run test:run-monitor`
- `corepack pnpm run test:document-previews`
- `corepack pnpm run smoke:route-layout`（本地合成 API；补齐新组件需要的实际交互检查）
- ESLint、`tsc -b`、`corepack pnpm run build`；应用级依赖/样式接入使用现有 `ci:verify` 验证整体影响。

迁移已通过本地前端 14 个测试阶段、ESLint、类型检查和生产构建。退役变更复用未受影响的迁移证据，补充文件/直接依赖缺席断言，并复验锁文件一致性、选择器及文档预览 owning 回归和构建。桌面 1440px、移动端 390px 的明暗主题合成 API 浏览器矩阵共 32 个场景通过；部门 Escape/焦点返回、MCP 抽屉与嵌套编辑器、头像及多行专家目录另有针对性浏览器检查。本地验证不作为部署或真实模型执行证据。

必须检查实际页面，不将 source inventory 或 JSDOM 通过当作视觉验收。重点包括窄卡片尺寸、表格横向滚动、嵌套弹层、portal 主题、焦点恢复、中文、无新增外部字体请求，以及 Markdown/XLSX 预览/下载回归。资源体积比较使用相同构建条件，不能在安装前承诺 tree-shaking 或包体结果。

## 可复查的上游依据

以下链接固定到已核验候选，实施升级时重新核对：

- [npm 5.61.0：版本、engines、peers 和 exports](https://registry.npmjs.org/@lobehub/ui/5.61.0)
- [新组件公开导出，含 Table 与 Toast](https://github.com/lobehub/lobe-ui/blob/a5f87e9367149dba06a8f476c6e0082ec2287d4d/src/base-ui/index.ts)
- [FormKit 公开入口](https://github.com/lobehub/lobe-ui/blob/a5f87e9367149dba06a8f476c6e0082ec2287d4d/src/base-ui/FormKit/index.ts)
- [ThemeProvider：字体、GlobalStyle、内部 App 和 portal host](https://github.com/lobehub/lobe-ui/blob/a5f87e9367149dba06a8f476c6e0082ec2287d4d/src/ThemeProvider/ThemeProvider.tsx)
- [旧 Button 的弃用标记](https://github.com/lobehub/lobe-ui/blob/a5f87e9367149dba06a8f476c6e0082ec2287d4d/src/Button/Button.tsx)
- [旧 Modal 的弃用标记](https://github.com/lobehub/lobe-ui/blob/a5f87e9367149dba06a8f476c6e0082ec2287d4d/src/Modal/Modal.tsx)
