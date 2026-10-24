# 前端视觉与样式约束

本文拥有前端视觉和样式的约束。组件接入见 [Lobe UI 接入合同](lobe-ui-adoption.md)，对话组件见 [assistant-ui 接入合同](assistant-ui-chat-adoption.md)，表格与 Select 见 [共享表格与 Select 合同](shared-table-select-components.md)。

本文分为参照与原则（第 1–2 节）、可执行规则（第 3–9 节）、已确定的决策（第 10 节）与验收（第 11 节）。规则以生产代码为准；如果代码与本文不一致，以本文为目标并在同一改动中修正。

## 1. 参照

- 视觉参照是 LobeHub 官方工作区。截图取自 `https://lobehub.com/img/hub/images/home/overview-cao-light.webp` 与 `…-dark.webp`（2026-10 核对）。截图显示：浅色侧栏与主区都是无色相的浅灰，主区白色圆角面板，主发送按钮为黑底白字；深色为近黑中性灰，没有暖色调。
- 灰阶与主色不是手工挑的值，而是 `@lobehub/ui` 在不传 `neutralColor` 与 `primaryColor` 时的默认输出（见第 3.2 节）。
- 参照的是结构和克制程度，不复制 LobeHub 的品牌、文案、标识或功能范围。
- 视觉上的 “克制” 指：灰阶承担大部分界面，颜色只来自头像、状态和少量品牌强调；边框优先于阴影；大面积装饰（渐变、发光、彩色大块背景）不进入工作区。

## 2. 原则

1. **一个来源**：颜色、圆角、字号的唯一来源是 `frontend/web/src/styles/base.css` 中的 `:root` 与 `.dark` 变量（`--theme-*`）。页面、组件和样稿只引用这些变量，不写新的颜色字面量。
2. **语义优先于色值**：使用 `--theme-text`、`--theme-border`、`--theme-danger` 这类语义名，不用 “看起来像灰” 的名字在组件里重新定义。
3. **无色相灰**：中性灰不带色相。不使用 slate（偏蓝）、stone（偏暖）、mauve（偏粉）等有色相的灰阶。
4. **边界说明分组**：容器只表达真实分组。不为装饰增加卡片套卡片。
5. **公开 API 优先**：Lobe 组件用 `size`、`variant`、`styles`、`classNames`、`className` 等公开入口调整，不靠全局元素选择器覆盖。
6. **移动端有完整内容**：390px 下正文、状态和主要动作都可见。不能靠隐藏列或裁切来 “适配”。
7. **验收有证据**：视觉改动必须附截图（浅色与深色、1440px 与 390px）。

## 3. 颜色与主题

### 3.1 单一来源与工具类

- 生产 token 在 `styles/base.css`：浅色 `:root`，深色 `.dark`。语义变量包括 `--theme-success|warning|info|danger-text`（文字用的加深同色相，见第 3.3 节）、`--theme-on-overlay` 与 `--theme-overlay-solid`（始终暗底的查看器用，与明暗主题无关），以及 `--theme-bg`、`--theme-bg-card`、`--theme-bg-sidebar`、`--theme-sidebar-*`、`--theme-border`、`--theme-border-strong`、`--theme-text`、`--theme-text-secondary|tertiary|quaternary`、`--theme-hover`、`--theme-primary*`、`--theme-danger|warning|success|info`（含 `-soft` 与 `-ring`）。
- Tailwind 工具类中的灰阶使用 `neutral-*`（Tailwind 内置的无色相灰阶），不使用 `stone-*` 或 `slate-*`。现有代码已全部替换。
- 新增颜色必须先在 `:root` 与 `.dark` 定义语义变量，再在组件中引用。不得在 `.tsx` 的 `style` 或 `.css` 的规则里直接写 `#xxxxxx`、`rgb()`、`rgba()`。
- 允许的例外（由 `scripts/check-style-rules.mjs` 强制执行，清单写在脚本的 `RENDER_EXCEPTIONS` 与 `LITERAL_EXCEPTIONS` 中）：
  1. `base.css` 的 token 定义块本身，以及 `utils/themeDom.ts`（浏览器 `theme-color` 元信息必须是字面色值）。
  2. 渲染调色板：`components/documents/previews/`（Word/Excel/PPT/PDF/CAD 等文档的渲染色）、`CodeMirrorViewer.tsx`、`syntax-highlight.css`、`excel-preview.css`、`MermaidDiagram.tsx`（图表画布）、`UsagePage.tsx`（数据图表分类色）、`AuthPage.tsx`（第三方品牌标识色）。
  3. 阴影：`box-shadow` / `text-shadow` / `filter` 与 `shadow-[…]` 工具类中的黑色系阴影颜色，属于阴影约定。
  4. 测试中用来断言 token 值的字符串。

### 3.2 当前灰阶（与 LobeHub 默认值对齐）

数值来自 `@lobehub/ui` 的 `lightAlgorithm` / `darkAlgorithm`，不传 `neutralColor` 与 `primaryColor`：

| 语义 | 浅色 | 深色 |
| --- | --- | --- |
| 页面背景 `--theme-bg` | `#ffffff` | `#000000` |
| 工作区画布 / 卡片 `--theme-workbench-canvas`、`--theme-bg-card` | `#ffffff` | `#0d0d0d` |
| 侧栏 `--theme-bg-sidebar` | `#f8f8f8` | `#000000` |
| 次级填充 `--theme-sidebar-panel-muted` | `#eeeeee` | `#1a1a1a` |
| 边框 `--theme-border` | `#e3e3e3` | `#202020` |
| 强边框 `--theme-border-strong` | `#cccccc` | `#333333` |
| 正文 `--theme-text` | `#080808` | `#ffffff` |
| 次要文字 `--theme-text-secondary` | `#666666` | `#aaaaaa` |
| 辅助文字 `--theme-text-tertiary` | `#999999` | `#6f6f6f` |
| 主动作 `--theme-primary`（墨色） | `#222222` | `#eeeeee` |
| 主动作悬停 `--theme-primary-hover` | `#333333` | `#ffffff` |
| 成功 `--theme-success` | `#379d4a` | `#c4f042` |
| 信息 `--theme-info` | `#0072f5` | `#60b1ff` |
| 警告 `--theme-warning` | `#ee9e0b` | `#ffb224` |
| 错误 `--theme-danger` | `#ec5e41` | `#f4416c` |
| 错误文字 `--theme-danger-text` | `#b93a22` | `#f4416c` |

`--theme-*-soft` 与 `--theme-*-ring` 使用同一色相的半透明值，不另起色相。

### 3.3 字体与颜色的组合

- 正文、标题和次要文字必须使用语义变量。同一界面内只有一套灰。
- 语义状态色只用于状态标签、提示和图标，不用于大面积背景。
- 浅色文字 `#666666` 与 `#999999` 的对比度不足以承载重要信息。重要信息用 `--theme-text`；`--theme-text-tertiary` 只用于装饰性或可忽略的说明。
- 错误颜色分两档：图标、标签与背景用 `--theme-danger`；作为文字出现的错误信息用 `--theme-danger-text`（浅色 `#b93a22`，在白色、`#f8f8f8` 与 `#eeeeee` 上对比度分别为 5.69、5.36、4.90，满足 WCAG AA）。文字用法不得直接写 `text-[var(--theme-danger)]`。

## 4. 排版、间距与圆角

- 字体复用 `--font-ui`，不额外请求外部字体。中文回退保留 PingFang SC、Microsoft YaHei。
- 字号：正文 14px，辅助 12px，页标题桌面 26px、手机 24px，区块标题 16px；聊天正文 14–15px，行高 1.6–1.8。
- 间距以 4px 为基础，常用 8 / 12 / 16 / 24 / 32px。桌面页面左右 32px，手机 16px。
- 圆角：控件 8px，独立内容块 12px，聊天输入器 16px。同类控件不各自选择圆角。

## 5. 断点

- 允许的断点只有成对的标准界限（与 Tailwind 对齐）：`639/640`（手机/中等）、`767/768`（应用外壳折叠）、`1023/1024`、`1279/1280`、`1535/1536`（宽屏）。`max-width` 与 `min-width` 必须成对，不另起数值。
- 历史非标准值已并入标准界限：480 → 640，1200 与 1360 → 1280，1440 → 1536，760 → 767，520 → 639，1080 → 1023，1100 与 1180 → 1279。这些是有意的布局变化，只影响对应宽度区间。

## 6. 组件与布局

- Button、ActionIcon、Input、TextArea、Select、Tabs、Switch、Table、Drawer、Tooltip、Toast 使用 Lobe UI 公开组件。不手写同类交互，不增加无职责的同名包装。
- `LobeUiProvider` 不传入 `customTheme.neutralColor` 与 `customTheme.primaryColor`。库默认值已与 `--theme-*` 对齐；如需改色，先改 `base.css` 再同步本文第 3.2 节。
- 页面 CSS 只拥有布局、响应式、内容排版和真实业务分组。
- **不写全局元素选择器**（`button`、`input`、`svg`、`table` 等）。历史例外见第 8 节。
- 表格移动端不要用 `display: none` 隐藏列。Lobe/antd 仍会为隐藏列保留 `<col>` 宽度，表格右侧会留出空白。应在 JS 中按屏幕宽度决定列数组（`matchMedia` + 条件展开），见第 9 节。
- 表格列宽由业务 owner 设置。不要用固定总宽 `scroll.x` 强制横向滚动来代替布局决策。
- 搜索、筛选、主题切换与弹层必须可用。没有实现的能力不伪装成可点击按钮。

### 6.1 布局基线

| 项目 | 基线 |
| --- | --- |
| 外壳 | 桌面侧栏 224px，主区域细边框与 12px 圆角；顶栏 56px；窄屏侧栏收进库 Drawer，不永久占用一条 52px rail。对话工作区例外见第 6.3 节 |
| 内容宽度 | 目录和管理内容约束最大 1200px；用量统计靠左铺满可用内容区，沿用桌面 32px / 窄屏 16px 内边距，不居中限宽；聊天正文与输入器对齐，最大 800px。对话工作区例外见第 6.3 节 |
| 控件 | 普通控件采用库默认尺寸；普通表单约 36–40px，紧凑工具条约 32px；窄屏主要触控按钮至少 40px |
| 图标 | 常规 16–20px；图标按钮使用 ActionIcon 或 Button 的 icon 合同，禁止固定窄宽度却保留文本按钮 padding |

### 6.2 三类页面遵守同一套层级

- **目录页：标题、查找、内容。** 公司导航、专家市场和插件市场以内容发现为主。标题和一句简洁说明后接搜索/筛选，下面直接进入内容列表。卡片最多一层主体边框；名称、能力说明、主要动作清晰，次要统计退后。分类、收藏、排序不各包一层大容器。
- **管理页：对象列表与必要操作。** 技能、MCP、模型、运行、用户、角色和记忆使用一致的页标题、工具条与数据密度。连接/治理摘要不压过主要列表。状态标签保持单行和图标完整，不挤压为竖排。
- **任务工作区：内容与输入优先。** 专家对话、SOP 和 Word 审核以当前任务为中心。聊天正文不整体套大卡片；用户消息可使用轻背景。正文、附件和输入器对齐。上传或任务输入应进入首屏，统计不抢占位置。会话历史按需展开，避免两条宽侧栏同时挤压主内容。

用量页及展开的模型明细不展示采集提示条、采集详情或统计口径长说明；保留统计、排行、实际模型数据与必要的错误/无记录状态，不将未采集记录补成零。日期、搜索、四项汇总和用量构成采用紧凑布局，排名表格占剩余工作区；数据区内部滚动，表头和独立分页页脚保持可见，不让每页 50 行把整页拉长。页脚左侧显示条目范围，右侧页码及静态“50条/页”。窄屏保留横向滚动，不压缩为逐字竖排；低高度窗口允许页面滚动保证全部操作可达。

### 6.3 对话工作区的获批例外

通用对话与专家工作区共用一条主侧栏，已获批使用比目录/管理页更大的几何，其他页面与全局主题不变：

- 主侧栏展开宽 272px，收起/窄屏沿用现有 52px slim rail，不改为 Drawer。
- 正文与输入器共用一条最大 1216px（76rem）栏宽，替代原 960px（60rem）上限以减少宽屏留白；开场、正文、状态提示、骨架与输入器对齐该栏，窄屏按可用宽度收缩。普通输入器最小高度 104px，多行增高、视口上限与问答模式不变。
- 侧栏及外侧间距由 `librechat-ui/surface.ts` 拥有，正文与输入器宽度由现有 Elements 及其消费者持有。这些对话例外不替代 224px / 800px 基线，也不外溢到目录、管理或专家市场页面。
- 顶栏把公开身份信息与右侧上下文按钮放在同一行、垂直居中，不为按钮另起一行；身份文字左对齐，长简介单行省略并保留完整提示。
- 侧栏管理组标题与导航图标左缘对齐、折叠箭头靠右，不采用按钮默认居中布局。管理员管理组默认展开，跨页保留显式折叠选择，齿轮设置独立。

## 7. 渐变、阴影与动效

- 工作区（聊天、目录、管理页）默认无阴影，只在浮层、输入器上使用轻阴影。
- 不在工作区使用大面积渐变或发光边框。
- 认证页（`styles/auth.css`）的氛围渐变是唯一允许的装饰性渐变，且只限于认证页。
- 动效尊重 `prefers-reduced-motion`。

## 8. `!important` 与历史例外

- `!important` 只允许用于覆盖第三方库注入的样式，或覆盖由布局 owner 控制的 portal/侧栏偏移（例如 `base.css` 中的 Toast 与预览侧栏偏移）。新增时须在同一行注释理由。
- 登记的历史例外（不得扩大）：
  1. `base.css` 中 `button` 的 `-webkit-tap-highlight-color`（仅 `max-width: 639px`）。
  2. `base.css` 中 `input[type="radio"]` 与 `-webkit-autofill` 系列规则。
  3. `base.css` 中 Toast 和侧栏偏移的 `!important`。
- 存量 `!important` 与 `style={{}}` 内联样式由基线文件 `frontend/web/scripts/style-rules-baseline.json` 记录，只允许下降，不允许增加（见第 12 节）。

## 9. 重复出现的代码问题（新代码必须避免）

以下问题曾在整改中反复出现，已写入约束：

| 问题 | 为什么不对 | 正确做法 |
| --- | --- | --- |
| 同一色值在 CSS 与组件里各写一份（样稿曾复制一整套灰阶） | 深浅色易漂移，改一处漏一处 | 只引用 `--theme-*` 变量（第 3.1 节） |
| 用有色相的灰阶（`stone-*`、`slate-*`、`mauve`） | 与 LobeHub 灰阶不一致，深浅色之间色温不统一 | 使用 `neutral-*` 与 `--theme-*`（第 3.1 节） |
| 把品牌色写成青绿或其它彩色主按钮 | 与主动作墨色冲突，多套主色并行 | 主动作用 `--theme-primary`（墨色） |
| 断点数值各自为政（`639/640` 重叠、`767/768`、`max-width:640` 与 `min-width:640` 同时存在） | 边界处样式跳变，难以排查 | 只用第 5 节的断点 |
| 手机端用 `display:none` 隐藏表格列 | `<col>` 仍占宽，右侧留白 | 在 JS 中条件展开列数组（`LobeStylePreview.tsx` 的模型表） |
| 视觉样式写在 `style={{}}` 里（生产有 66 个 `.tsx` 文件） | 无法被主题切换与断点统一管理，且覆盖顺序不可控 | 样式进入 CSS 模块或语义类；动态数值（如进度宽度）可保留 |
| 导航/按钮的激活态用内联颜色 | 与 Lobe 的类优先级冲突，只能靠内联压过 | 用 `className` 切换 `…Active` 类，选择器限定在所属容器内（样稿侧栏已改） |
| 测试断言生产 CSS 的字面色值 | 换 token 时测试会因字面量变化失败 | 改 token 时同步更新测试中的断言 |
| 全局元素选择器（`button`、`input`） | 影响所有 Lobe 组件，难以回滚 | 用公开 API 或页面自己的类名 |
| 认证页渐变被当成工作区范式 | 工作区出现多余装饰 | 渐变只留在 `auth.css` |

## 10. 已确定的决策

1. **灰阶为无色相灰**：采用 LobeHub 默认值（第 3.2 节）。Tailwind 灰阶统一为 `neutral-*`，取代 `stone-*` 与 `slate-*`。
2. **主动作为墨色**：浅色 `#222222`，深色 `#eeeeee`。青绿 `#0f766e` 不再用于品牌或主动作。
3. **深色背景为近黑中性灰**：页面 `#000000`，工作区 `#0d0d0d`，与 LobeHub 深色参照一致。
4. **不传 `neutralColor` / `primaryColor`**：让 Lobe 库使用其默认值，`--theme-*` 与之对齐。

本次改动是有意的视觉变化：浅色主按钮由青绿改为墨色，深色由暖石灰改为近黑中性灰，侧栏与边框随之变化。变化已用截图人工检查；因此这次不适用第 11 节的像素比对。

## 12. 机器检查

- 命令：`pnpm run check:style-rules`。本地 `ci:verify` 在项目 projection 审计之后、单元测试之前运行它；CI 在 “Verify frontend lint and build” 步骤中于 lint 之后、build 之前运行它。
- 零容忍规则（任何新增即失败）：
  1. 非测试的 TSX/TS 中使用 Tailwind 原始色系类（`red`、`amber`、`blue`、`emerald`、`stone`、`slate`、`white`、`black` 等，包括 `bg-white/10` 之类的透明度写法）。
  2. 非测试的 TSX/TS 中出现色值字面量（`#xxxxxx`、`rgb()`、`rgba()`），第 3.1 节的例外除外。
  3. CSS 中出现色值字面量，`--theme-*` 定义块与阴影声明除外。
  4. `@media` 使用的断点不在第 5 节的允许集合内。
- 棘轮规则（每个文件不得超过基线）：`style={{` 内联样式数量，以及 `!important` 数量。当前基线为内联样式 241、`!important` 59（合入 main 时把 #1866、#1867 已合并代码的 3 处计入基线）。减少后用 `node scripts/check-style-rules.mjs --update-baseline` 更新基线，并在改动中说明。
- 检查范围不包括测试文件（`__tests__/`、`*.test.*`）。

## 11. 验收

- 每次视觉改动：浅色与深色，1440px 与 390px，共四组截图，附在 PR 中。
- 零视觉变化的重构：截图做像素比对，结果为 0 差异才算零变化。有意的视觉变化：截图人工检查，并在 PR 中说明变化范围。
- 样稿（`style-preview.html`，`src/features/design-preview/`）是本地示例数据页，不挂载认证、模型或业务 Provider，不加入生产路由。样稿颜色直接引用 `--theme-*`，不再维护自己的灰阶。样稿批准与实际页面验收分开进行。
- 手机端检查：没有被裁切的正文和动作，没有逐字竖排的表头，主动作容易找到。
- 库交互检查：键盘焦点可见，抽屉按 Escape 关闭并回到触发点。
- 脚本检查（`scripts/lobe-style-preview-browser-smoke.mjs`）需要本地 Chrome 与端口 5186；未运行时要在报告中注明。
