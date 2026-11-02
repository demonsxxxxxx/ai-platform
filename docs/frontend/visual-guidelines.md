# 前端视觉与样式约束

本文是前端视觉和样式规则的唯一来源，包括颜色、排版、几何、断点、页面布局和视觉验收。组件接入见 [Lobe UI 接入合同](lobe-ui-adoption.md)，对话组件见 [assistant-ui 接入合同](assistant-ui-chat-adoption.md)，表格与 Select 见 [共享表格与 Select 合同](shared-table-select-components.md)。

结构：参照与原则（第 1–2 节）、主题来源（第 3 节）、排版与几何（第 4 节）、断点（第 5 节）、页面布局（第 6 节）、对话界面规格（第 7 节）、装饰与例外（第 8–9 节）、常见错误（第 10 节）、已确定的决策（第 11 节）、机器检查（第 12 节）和验收（第 13 节）。规则以本文为目标；生产代码与本文不一致时，在同一改动中修正其中一方，不能让两份事实并存。

## 1. 参照

| 区域 | 参照 | 采用的内容 |
| --- | --- | --- |
| 普通聊天与专家聊天的对话主体 | assistant-ui **ChatGPT Clone**，固定到 `727a02dc3fe5286ded479cb792f48acbff336cd7` 的 [`apps/docs/components/pages/examples/chatgpt.tsx`](https://github.com/assistant-ui/assistant-ui/blob/727a02dc3fe5286ded479cb792f48acbff336cd7/apps/docs/components/pages/examples/chatgpt.tsx)（[说明](https://www.assistant-ui.com/examples/chatgpt)） | 阅读列宽度、空会话布局、用户气泡、助手正文流、输入器外形和操作栏尺寸（第 7 节） |
| 灰阶、主色和通用控件 | `@lobehub/ui` 在不传 `neutralColor` / `primaryColor` 时的默认输出 | 无色相灰阶、墨色主动作、控件尺寸（第 3 节） |
| 目录、管理和任务页面 | 本项目已有布局基线 | 第 6 节 |

- 参照的是结构、尺寸和克制程度，不复制任何产品的品牌、文案、标识或功能范围。
- ChatGPT Clone 只是视觉参照。它的运行环境（AI SDK、Assistant Cloud、云端附件、语音）不进入本项目；接线规则见 assistant-ui 接入合同。
- 示例里没有平台能力支撑的按钮（语音、分享、点赞、重新生成、编辑、分支）不随外观一起引入。见第 7.4 节。
- “克制”指：灰阶承担大部分界面，颜色只来自头像、状态和少量强调；边框优先于阴影；工作区不使用大面积渐变、发光或彩色大块背景。

## 2. 原则

1. **一个来源**：颜色、字体、圆角和聊天几何的值只在 `frontend/web/src/styles/base.css` 的 `:root` 与 `.dark` 中定义（第 3 节）。组件只引用变量，不写新的字面量。
2. **语义优先于色值**：使用 `--theme-text`、`--theme-border`、`--theme-danger` 这类语义名，不在组件里给 “看起来像灰” 的值重新起名。
3. **无色相灰**：中性灰不带色相，不使用 slate、stone、mauve 等有色相灰阶。
4. **边界说明分组**：容器只表达真实分组，不为装饰套卡片。
5. **公开 API 优先**：Lobe 组件通过 `size`、`variant`、`styles`、`classNames`、`className` 调整，不用全局元素选择器覆盖。
6. **移动端内容完整**：390px 下正文、状态和主要动作都可见，不靠隐藏列或裁切“适配”。
7. **验收有证据**：视觉改动附实际页面截图（第 13 节）。

## 3. 主题来源

### 3.1 一份值，两个消费者

```text
styles/base.css (:root / .dark)
 ├─ CSS 变量 → 本地 Elements、页面布局、Tailwind 任意值类
 └─ 与 Lobe 主题输出逐项一致（由 themeContract 检查）→ Lobe 控件、浮层、Portal
ThemeContext（浏览器偏好）→ html.dark class 与 LobeUiProvider appearance
```

- `base.css` 是首屏即加载的静态样式，主题值在这里定义，避免页面渲染后再修正颜色。
- Lobe 控件不读取 CSS 变量，而是由 `LobeUiProvider` 的主题配置计算颜色。`src/styles/themeContract.ts` 记录两者的对应关系与 Provider 的 token 覆盖；`src/styles/__tests__/themeContract.test.ts` 用 Lobe 实际算法计算浅色和深色 token，逐项比较 `base.css`。任何一侧单独改动都会失败。
- 需要改主色或灰阶时：同时修改 `base.css` 与 `themeContract.ts` 中传给 Lobe 的配置，使检查重新通过，并更新第 3.3 节表格。不允许只改一侧。
- 明暗偏好仍由 `ThemeContext` 维护，见[浏览器偏好合同](browser-local-preferences.md)。

### 3.2 变量与工具类

- 生产 token 在 `styles/base.css`：浅色 `:root`，深色 `.dark`。语义变量包括 `--theme-bg`、`--theme-bg-card`、`--theme-bg-sidebar`、`--theme-sidebar-*`、`--theme-border`、`--theme-border-strong`、`--theme-text`、`--theme-text-secondary|tertiary|quaternary`、`--theme-hover`、`--theme-primary*`、`--theme-danger|warning|success|info`（含 `-soft`、`-ring`、`-text`）、`--theme-on-overlay`、`--theme-overlay-solid`。
- 几何与字体变量：`--font-ui`、`--radius-control`、`--radius-block`、`--radius-composer`、`--radius-user-bubble`、`--chat-column-max`（第 4、7 节）。
- Tailwind 的 `font-sans` 指向 `var(--font-ui)`；灰阶工具类只用 `neutral-*`。
- 新增颜色先在 `:root` 与 `.dark` 定义语义变量，再在组件中引用。`.tsx` 的 `style` 与 `.css` 规则里不直接写 `#xxxxxx`、`rgb()`、`rgba()`。
- 允许的例外（由 `scripts/check-style-rules.mjs` 强制，清单在脚本的 `RENDER_EXCEPTIONS` 与 `LITERAL_EXCEPTIONS`）：
  1. `base.css` 的 token 定义块，以及 `utils/themeDom.ts`（浏览器 `theme-color` 元信息必须是字面色值）。
  2. 渲染调色板：`components/documents/previews/`、`CodeMirrorViewer.tsx`、`syntax-highlight.css`、`excel-preview.css`、`MermaidDiagram.tsx`、`UsagePage.tsx`、`AuthPage.tsx`。
  3. 阴影：`box-shadow` / `text-shadow` / `filter` 与 `shadow-[…]` 中的黑色系阴影。
  4. 测试中断言 token 值的字符串。

### 3.3 当前色值（等于 Lobe 默认输出）

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

`-soft` 与 `-ring` 使用同色相的半透明值，不另起色相。

### 3.4 颜色组合

- 正文、标题和次要文字使用语义变量；同一界面只有一套灰。
- 语义状态色只用于状态标签、提示和图标，不用于大面积背景。
- 浅色 `#666666`、`#999999` 对比度不足以承载重要信息；重要信息用 `--theme-text`，`--theme-text-tertiary` 只用于可忽略的说明。
- 错误分两档：图标、标签、背景用 `--theme-danger`；错误文字用 `--theme-danger-text`（浅色在白、`#f8f8f8`、`#eeeeee` 上对比度 5.69、5.36、4.90，满足 WCAG AA）。文字不直接写 `text-[var(--theme-danger)]`。

## 4. 排版、间距与圆角

- 字体统一使用 `--font-ui`（Inter 优先，中文由 `system-ui` 回退到系统字体），不请求外部字体。Lobe 通过 `fontFamily: var(--font-ui)`、Tailwind 通过 `font-sans` 使用同一字体。
- 字号：正文 14px，辅助 12px，页标题桌面 26px、手机 24px，区块标题 16px。聊天正文见第 7 节。
- 间距以 4px 为基础，常用 8 / 12 / 16 / 24 / 32px。桌面页面左右 32px，手机 16px。
- 圆角分四档，同类元素不各自选择：

| 变量 | 值 | 用于 |
| --- | --- | --- |
| `--radius-control` | 8px（同 Lobe `borderRadius` token） | 按钮、输入框、选择器等普通控件 |
| `--radius-block` | 12px | 独立内容块、卡片、主区面板 |
| `--radius-user-bubble` | 22px | 聊天用户消息气泡 |
| `--radius-composer` | 28px | 聊天输入器外框（含问答模式、移动端键盘弹起） |

## 5. 断点

- 只用成对的标准界限（与 Tailwind 对齐）：`639/640`、`767/768`（应用外壳折叠）、`1023/1024`、`1279/1280`、`1535/1536`。`max-width` 与 `min-width` 必须成对，不另起数值。
- 历史非标准值已并入标准界限：480 → 640，1200 与 1360 → 1280，1440 → 1536，760 → 767，520 → 639，1080 → 1023，1100 与 1180 → 1279。

## 6. 组件与页面布局

- Button、ActionIcon、Input、TextArea、Select、Tabs、Switch、Table、Drawer、Tooltip、Toast 使用 Lobe UI 公开组件；不手写同类交互，不增加无职责的同名包装。
- 页面 CSS 只拥有布局、响应式、内容排版和真实业务分组；**不写全局元素选择器**（历史例外见第 9 节）。
- 表格移动端不用 `display: none` 隐藏列（`<col>` 仍占宽），在 JS 中按屏幕宽度决定列数组（`matchMedia` + 条件展开）。列宽由业务 owner 设置，不用固定总宽 `scroll.x` 代替布局决策。
- 搜索、筛选、主题切换与弹层必须可用；没有实现的能力不伪装成可点击按钮。

### 6.1 布局基线

| 项目 | 基线 |
| --- | --- |
| 外壳 | 桌面侧栏 224px，主区域细边框与 `--radius-block` 圆角；顶栏 56px；窄屏侧栏收进库 Drawer。对话工作区例外见第 7.1 节 |
| 内容宽度 | 目录和管理内容最大 1200px；用量统计靠左铺满可用内容区（桌面 32px / 窄屏 16px 内边距）。对话阅读列见第 7 节 |
| 控件 | 库默认尺寸；普通表单约 36–40px，紧凑工具条约 32px；窄屏主要触控按钮至少 40px |
| 图标 | 常规 16–20px；图标按钮使用 ActionIcon 或 Button 的 `icon` 合同 |

### 6.2 三类页面共用同一套层级

- **目录页：标题、查找、内容。** 公司导航、专家市场和插件市场以内容发现为主：标题和一句说明，接搜索/筛选，下面直接是内容列表。卡片最多一层主体边框；名称、能力说明、主要动作清晰，次要统计退后。
- **管理页：对象列表与必要操作。** 技能、MCP、模型、运行、用户、角色和记忆使用一致的页标题、工具条与数据密度。状态标签保持单行和图标完整。管理页保持任务布局，不改成居中聊天阅读列；与聊天共享的是字体、颜色、控件、圆角层级和浮层行为。
- **任务工作区：内容与输入优先。** 专家对话、SOP 和 Word 审核以当前任务为中心。上传或任务输入进入首屏，统计不抢占位置；会话历史按需展开，避免两条宽侧栏同时挤压主内容。

用量页及展开的模型明细不展示采集提示条或统计口径长说明，保留统计、排行、实际模型数据与必要的错误/无记录状态，不把未采集记录补成零。日期、搜索、四项汇总和用量构成紧凑排列，排名表格占剩余工作区；数据区内部滚动，表头和分页页脚保持可见。页脚左侧显示条目范围，右侧页码及静态“50条/页”。窄屏保留横向滚动；低高度窗口允许页面滚动。

## 7. 对话界面规格

适用于普通聊天与专家聊天共用的对话主体（`ChatView` → Thread / Message / Composer Elements）。数值来自第 1 节固定示例，中文字号、窄屏宽度与触控尺寸是本项目的适配决定。

### 7.1 外壳

- 主侧栏展开 272px，收起/窄屏沿用 52px slim rail，不改为 Drawer。侧栏和外侧间距由 `librechat-ui/surface.ts` 拥有。
- 文件栏沿用现有 Shell：桌面默认 360px，可拖动或键盘调整到 280–520px；移动端 Drawer。文件栏与较窄的阅读列共存，各自拥有空间。
- 顶栏把公开身份信息与右侧上下文按钮放在同一行、垂直居中；身份文字左对齐，长简介单行省略并保留完整提示。桌面聊天标题栏与文件盘工具栏共用 Shell 的 56px 行高，底边贯通，竖向分隔线在交点接齐；搜索位于下一行，文件区不额外套整栏卡片边框。
- 侧栏管理组标题与导航图标左缘对齐、折叠箭头靠右；管理员管理组默认展开，跨页保留显式折叠选择，齿轮设置独立。
- 这些对话例外不外溢到目录、管理或专家市场页面。

### 7.2 阅读列与消息

| 项目 | 规格 |
| --- | --- |
| 阅读列 | `--chat-column-max` = 48rem（768px），水平居中，左右 8px 内边距。开场、正文、状态提示、建议、骨架与输入器全部对齐这一列；窄屏随可用宽度收缩 |
| 范围 | 48rem 只约束阅读列和输入器；整个工作区、侧栏、文件栏和专业预览不受它限制 |
| 助手回答 | 正文流，不套气泡或卡片；16px，行高 1.7（与共享 Markdown 渲染器一致） |
| 用户消息 | 右对齐气泡，`--radius-user-bubble`（22px），浅填充 `--theme-bg-sidebar`，16px，内边距 10px × 16px；桌面最大 70%，窄屏（≤639px）最大 90% |
| 回答操作栏 | 紧随回答正文的图标按钮；保留键盘与触屏可访问性 |

### 7.3 输入器

| 项目 | 规格 |
| --- | --- |
| 外形 | `--radius-composer`（28px），细边框 `--theme-border`，聚焦时边框加深；只用轻阴影 |
| 宽度 | 与阅读列相同（`--chat-column-max`） |
| 高度 | 按内容和工具栏自然撑高，不设整盒最小高度；输入区 48px 起、最高 192px 后内部滚动，整盒不超过 `min(52dvh, 420px)` |
| 结构 | 上方输入区，下方紧凑工具栏：左侧附件、模型、工具与 Skill 入口，右侧发送或停止 |
| 图标按钮 | Lobe 圆形 `size="large"` 按钮，同一尺寸同时满足桌面与触控 |
| 文字 | 16px（同时避免 iOS 聚焦缩放） |
| 空会话 | 欢迎内容与输入器在可用区中部偏上，快捷任务紧随其上；专家开场显示已发布 Profile 描述 |
| 有消息时 | 输入器在聊天区底部，桌面底部留 24px，手机保留安全区 |
| 问答模式 | 任务表面替换普通控件，外框仍是同一输入器圆角与宽度；自然高度，超长时视口约束内滚动 |

### 7.4 只显示有能力的操作

- 无语音能力时不显示麦克风和语音模式按钮。
- 分享、点赞反馈、重新生成、编辑旧消息、分支切换、导出，必须有完整平台协议后才显示；示例中这些图标只是展示结构。
- 当前回答操作为复制与用量统计（见 assistant-ui 接入合同）。

## 8. 渐变、阴影与动效

- 工作区（聊天、目录、管理页）默认无阴影，只在浮层和输入器使用轻阴影。
- 不在工作区使用大面积渐变或发光边框；认证页（`styles/auth.css`）的氛围渐变是唯一允许的装饰性渐变。
- 动效尊重 `prefers-reduced-motion`。

## 9. `!important` 与历史例外

- `!important` 只用于覆盖第三方库注入的样式，或覆盖布局 owner 控制的 portal/侧栏偏移；新增时在同一行注释理由。
- 登记的历史例外（不得扩大）：
  1. `base.css` 中 `button` 的 `-webkit-tap-highlight-color`（仅 `max-width: 639px`）。
  2. `base.css` 中 `input[type="radio"]` 与 `-webkit-autofill` 系列规则。
  3. `base.css` 中 Toast 和侧栏偏移的 `!important`。
- 存量 `!important` 与 `style={{}}` 内联样式由 `frontend/web/scripts/style-rules-baseline.json` 记录，只降不升（第 12 节）。

## 10. 重复出现的错误（新代码必须避免）

| 问题 | 为什么不对 | 正确做法 |
| --- | --- | --- |
| 同一色值、圆角或栏宽在 CSS、Tailwind 类和组件里各写一份 | 改一处漏一处，深浅色和尺寸漂移 | 引用第 3、4、7 节的变量 |
| 只改 `base.css` 主色或灰阶，不改 Lobe 配置 | Lobe 控件和浮层颜色不跟随 | 两侧一起改，`themeContract` 检查通过（第 3.1 节） |
| 有色相灰阶（`stone-*`、`slate-*`、`mauve`） | 与无色相灰阶不一致 | `neutral-*` 与 `--theme-*` |
| 彩色主按钮 | 与墨色主动作冲突 | 主动作用 `--theme-primary` |
| 断点各自为政 | 边界处样式跳变 | 只用第 5 节断点 |
| 手机端 `display:none` 隐藏表格列 | `<col>` 仍占宽 | 条件展开列数组 |
| 视觉样式写在 `style={{}}` | 无法随主题与断点统一管理 | 语义类或 CSS 模块；动态数值（如进度宽度）可保留 |
| 激活态用内联颜色 | 与 Lobe 类优先级冲突 | `className` 切换 `…Active` 类，选择器限定在所属容器 |
| 全局元素选择器（`button`、`input`） | 影响所有 Lobe 组件 | 公开 API 或页面类名 |
| 照搬示例的空按钮 | 用户点了没有结果 | 第 7.4 节 |

## 11. 已确定的决策

1. **对话主体参照 ChatGPT Clone**（固定提交见第 1 节），阅读列 48rem、输入器 28px、用户气泡 22px。取代此前以 LobeHub 工作区为聊天主视觉、阅读列 1216px（76rem）、输入器最小高度 104px、输入器 16/20px 两种圆角的规则。用户气泡保留浅填充而非示例的墨色填充，墨色只留给主动作。
2. **灰阶为无色相灰**，采用 Lobe 默认值（第 3.3 节）；Tailwind 灰阶统一为 `neutral-*`。
3. **主动作为墨色**：浅色 `#222222`，深色 `#eeeeee`。
4. **深色背景为近黑中性灰**：页面 `#000000`，工作区 `#0d0d0d`。
5. **Lobe 不传 `neutralColor` / `primaryColor`**，与 `base.css` 的对应由 `themeContract` 自动检查，不再靠人工对齐。

## 12. 机器检查

- `pnpm run check:style-rules`。本地 `ci:verify` 在 projection 审计之后、单元测试之前运行；CI 在 “Verify frontend lint and build” 步骤中于 lint 之后、build 之前运行。
  - 零容忍（新增即失败）：非测试 TSX/TS 使用 Tailwind 原始色系类（含透明度写法）；非测试 TSX/TS 出现色值字面量（第 3.2 节例外除外）；CSS 出现色值字面量（`--theme-*` 定义块与阴影除外）；`@media` 断点不在第 5 节集合内。
  - 棘轮（每个文件不得超过基线）：`style={{` 内联样式数量与 `!important` 数量。减少后用 `node scripts/check-style-rules.mjs --update-baseline` 更新基线，并在改动中说明。
  - 不检查测试文件（`__tests__/`、`*.test.*`）。
- `src/styles/__tests__/themeContract.test.ts`（`test:browser-preferences` 阶段）：`base.css` 与 Lobe 主题输出的颜色、圆角和字体逐项一致，Tailwind `font-sans` 指向 `--font-ui`。

## 13. 验收

- 视觉改动：浅色与深色、1440px 与 390px 四组截图附在 PR 中。共享聊天布局首次改动另加 768px 与 1920px；后续局部改动只扩展到实际受影响的场景。
- 零视觉变化的重构：截图像素比对为 0 差异才算零变化。有意的视觉变化：人工检查截图，在 PR 中说明变化范围。
- 对话布局改动同时检查：中文输入法确认不误发送；Enter、Shift+Enter、斜杠菜单不重复提交；首条消息建会话后草稿与附件保留；问答结束后普通草稿恢复；上翻历史时流式输出不抢回底部；文件栏打开时输入器、菜单和正文可用。
- 样稿（`style-preview.html`，`src/features/design-preview/`）是本地示例数据页，不挂载认证、模型或业务 Provider，不进入生产路由；颜色直接引用 `--theme-*`。样稿批准与实际页面验收分开。
- 手机端：正文和动作不被裁切，表头不逐字竖排，主动作容易找到。
- 库交互：键盘焦点可见，抽屉按 Escape 关闭并回到触发点。
- 浏览器脚本（`scripts/lobe-style-preview-browser-smoke.mjs`、`scripts/route-layout-browser-smoke.mjs`）需要本地 Chrome；未运行时在报告中注明。
