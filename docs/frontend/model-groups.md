# 模型分组前端接入

模型管理页的“分组管理”使用后端 `/api/ai/admin/model-groups` 原子接口：

- `GET /api/ai/admin/model-groups` 和 `GET /{group_id}` 读取管理投影；
- `POST /api/ai/admin/model-groups`、`PATCH /{group_id}`、`POST /{group_id}/retire` 管理业务组；
- `PUT /{group_id}/models` 与 `PUT /{group_id}/members` 按 revision 一次替换组模型/成员；模型配置页的批量添加/替换/移出使用 `POST /api/ai/admin/model-groups/models/batch`，先 `preview:true` 校验 before/after，再用同一组 revision 的 `preview:false` 原子提交；409 保留选中草稿并要求刷新详情。
- `GET/PUT /api/ai/admin/users/{user_id}/model-groups` 一次替换用户业务组与主组，清空成员必须明确确认回默认组；
- `/api/ai/models/groups` 和 `/api/agent/models/available?group_id=...` 是普通用户公开投影，不包含管理备注、权重、成员或存储信息。

对话选择器先选择一个公开业务组，进入或切组时选中该组的默认模型，用户可以再手动切换到同组其他模型。普通默认组不进入对话选择器；默认模型不可用时提示用户手动选择，不自动替换或跨组回退。失去组或模型资格时前端清空模型并阻止发送，服务端仍是最终准入 authority。新会话和续聊提交具体 `group_id`、`selection_mode=explicit`、`model_id`/`model`，不能用客户端场景绕过服务端授权。

## 无需重登录刷新

`ModelCatalogProvider.refreshModels` 每次先重新获取公开组授权，再读取当前组模型。打开模型菜单（含 `/model` 和 slash 菜单）、可见窗口 focus/回前台、管理员发布成功及同页组变更均复用此入口；不轮询。普通并发合并，发布强制替代旧请求。有效组/模型、草稿和已打开菜单保留；组或模型被撤销时清空并提示明确重选，不自动跳组或模型。空组/空模型仍可打开菜单恢复；临时网络错误保留同账号旧数据，401/403 清空授权投影。账号、租户、组切换和卸载通过 abort/owner generation 隔离迟到结果。服务端最终准入和已有 Run 冻结不变。

## 退役库存

旧“用户重新加载聊天页面后获取最新列表”提示和仅登录时加载目录的路径已由上述刷新入口替代；无新增逐模型 ACL 兼容路径。

旧 `ModelUserAccess` 逐模型员工 ACL 控件及其 mounted test 已删除。`modelAdminApi.publish` 不再发送 `is_default`、`access_mode` 或 `allowed_user_ids`；这些字段属于后端迁移只读输入，分组授权只通过上述分组接口完成。保留 `agentProfile` 的 `allowed_user_ids` 是专家发布的独立产品契约，不是模型目录 ACL。

模型目录发布测试覆盖旧字段缺席；分组 mounted test 覆盖目录加载、创建、drawer、模型/成员保存和原子请求形状。实际浏览器页面与真实后端联调由父owner验收。
