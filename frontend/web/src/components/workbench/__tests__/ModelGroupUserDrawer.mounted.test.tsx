import assert from "node:assert/strict";
import test, { after } from "node:test";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom.ts";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot.tsx";
import { modelGroupsApi } from "../../../services/api/modelGroups.ts";

const dom = installSelectTestDom();
after(() => dom.close());

const user = { user_id: "user-a", display_name: "用户 A", status: "active", session_count: 0, run_count: 0, queued_run_count: 0, running_run_count: 0, succeeded_run_count: 0, failed_run_count: 0, cancelled_run_count: 0 };
const groups = [{ id: "group-a", code: "sales", name: "销售组", type: "business" as const, enabled: true, revision: 3, remark: "", sort_order: 1, model_count: 1, member_count: 1 }];

test("user model-group drawer preserves primary selection, fallback confirmation and abort ownership", async () => {
  const React = await import("react");
  const { ModelGroupUserDrawer } = await import("../ModelGroupUserDrawer.tsx");
  const original = { ...modelGroupsApi };
  const calls: Array<{ group_ids: string[]; primary_group_id: string | null; confirm_default_fallback: boolean }> = [];
  modelGroupsApi.listAdmin = async () => ({ groups, version: "model-groups.v1" });
  modelGroupsApi.userMemberships = async () => ({ user_id: user.user_id, revision: 3, groups: [{ id: "group-a", code: "sales", name: "销售组", enabled: true, revision: 3, is_primary: true }], primary_group_id: "group-a", default_implicit: false });
  modelGroupsApi.setUserMemberships = async (_userId, payload) => { calls.push(payload); return { user_id: user.user_id, revision: 4, group_ids: payload.group_ids, primary_group_id: payload.primary_group_id }; };
  const container = dom.document.createElement("div");
  const root = await createLobeTestRoot(container);
  let closed = 0;
  try {
    await React.act(async () => root.render(React.createElement(ModelGroupUserDrawer, { user, onClose: () => { closed += 1; } })));
    for (let index = 0; index < 20 && !container.querySelector("[data-user-model-groups]"); index += 1) await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    assert.ok(container.querySelector("[data-model-group-user-checkbox=group-a]"));
    assert.match(container.textContent ?? "", /主组/);
    const checkbox = container.querySelector<HTMLElement>("[data-model-group-user-checkbox=group-a]");
    assert.ok(checkbox);
    await React.act(async () => checkbox.click());
    assert.match(container.textContent ?? "", /默认组自动归属/);
    const confirm = container.querySelector<HTMLElement>('[data-model-group-fallback-confirmation]');
    assert.ok(confirm);
    await React.act(async () => confirm.click());
    const save = [...container.querySelectorAll("button")].find((button) => button.textContent?.includes("保存分组"));
    assert.ok(save);
    await React.act(async () => save.click());
    assert.deepEqual(calls, [{ group_ids: [], primary_group_id: null, expected_revision: 3, confirm_default_fallback: true }]);
    assert.equal(closed, 1);
  } finally {
    await React.act(async () => root.unmount());
    Object.assign(modelGroupsApi, original);
  }
});
