# 管理员用量统计

`/usage` 通过治理导航进入，仅管理员可访问。页面消费实际模型增量账本的管理员投影，不读取 Run 绑定模型、旧 token 聚合或费用字段。

## 日期、搜索与明细

默认最近 30 个北京时间自然日，含今天；支持今天、近 7 天及自定义含首尾日期。按记录产生时间归窗。无效或倒序日期不发请求。搜索工号/姓名，切换搜索或日期回到第一页；全量排名由服务端按总 Token 降序决定，前端不重排当前页。每页 50 条，模型展开不分页。

页面使用既有 cookie-session transport：

- `GET /api/ai/admin/usage/users?start_date=…&end_date=…&q=…&limit=50&offset=…`
- `GET /api/ai/admin/usage/users/{user_id}/models?start_date=…&end_date=…`

模型明细展示实际模型、输入、缓存读取、缓存创建、输出、输入合计与总 Token。输入合计为前三项，总量为输入合计加输出，均使用接口投影。无 `user_id` 的组显示“未关联用户”且不可展开；无 `work_id` 不用内部用户 ID 冒充工号。

`meta.coverage` 是整个筛选窗口摘要，不是逐用户/逐模型状态。`reported_final` 表示已收到最终报告，不宣称提供方消耗完整；`partial` 展示已记录小计与缺失说明；`unavailable` 不把历史未采集或未知基线显示成零。已记录的 0 保留为 0。页面显示采集启用时间及覆盖原因，统计口径放在可展开说明中，不用于费用结算。

## 请求与界面所有权

`services/api/modelUsage.ts` 拥有 HTTP DTO 与 URL；`features/usage/` 拥有查询、展示和请求生命周期。查询变更卸载旧结果与展开内容，abort 列表/模型请求；effect 检查 signal，忽略不遵守 abort 的旧成功/失败回包。折叠模型后卸载其请求，重新展开重新查询。401 沿用统一登录恢复机制并显示重新登录提示，403 明确仅管理员可用，其他错误可重试。

Lobe Table/Pagination 负责表格与分页，页面复用主题和可访问标签；不新增主题存储或通用组件包装。分页、日期、搜索与展开分别有 owning regression。

## 检查和兼容处置

`test:run-monitor`（现有 runtime-control CI shard）同时运行 `modelUsage.test.ts` 与 `UsagePage.test.tsx`；Python `test_frontend_release_traceability.py` 保持准确脚本清单。Table inventory 覆盖用户及模型表格。没有新增工作流。

被替换的 disconnected 骨架及其旧测试已删除；没有保留虚构 adapter、mock 数据或回退路径。RunMonitor/RunPerformance 继续服务运行诊断，不是 usage 的兼容数据源。没有历史回填或被退役的后端路径。

JSDOM 验证不等于视觉验收。浏览器需独立检查明暗主题、390px 表格横向滚动、分页和展开、日期/搜索键盘及焦点；本地生产构建不等于后端或真实 PostgreSQL 验证。
