import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const workbenchSource = readFileSync(
  join(process.cwd(), "src/features/agent-builder/AgentBuilderWorkbench.tsx"),
  "utf8",
);
const controllerSource = readFileSync(
  join(process.cwd(), "src/features/agent-builder/agentBuilderController.ts"),
  "utf8",
);
const adapterSource = readFileSync(
  join(process.cwd(), "src/features/agent-builder/agentBuilderAdapter.ts"),
  "utf8",
);
const enterpriseFieldsSource = readFileSync(
  join(process.cwd(), "src/features/agent-builder/AgentBuilderEnterpriseFields.tsx"),
  "utf8",
);

test("server list and mutations are owned by the feature-local controller", () => {
  assert.match(controllerSource, /agentProfileApi/);
  assert.match(controllerSource, /this\.api\.listAdmin\(\)/);
  assert.match(controllerSource, /this\.api\.saveDraft\(/);
  assert.match(controllerSource, /this\.api\.publish\(/);
  assert.match(adapterSource, /expected_draft_revision/);
  assert.match(workbenchSource, /controller\.loadProfiles\(\)/);
  assert.match(workbenchSource, /controller\.saveActiveProfile\(currentCatalog\)/);
  assert.match(workbenchSource, /controller\.publishActiveProfile\(currentCatalog\)/);
});

test("workbench has explicit admin, loading, error, empty, and New Agent surfaces", () => {
  assert.match(workbenchSource, /data-agent-builder-access-denied/);
  assert.match(workbenchSource, /正在加载专家/);
  assert.match(workbenchSource, /workbench\.listError/);
  assert.match(workbenchSource, /当前没有服务端专家/);
  assert.match(workbenchSource, /新建专家/);
  assert.match(workbenchSource, /controller\.createNewAgent\(/);
});

test("publish stays fenced by a clean materialized draft with visible reasons", () => {
  assert.match(workbenchSource, /getAgentProfileSaveBlock/);
  assert.match(workbenchSource, /getAgentProfilePublishBlock/);
  assert.match(workbenchSource, /data-agent-builder-save-reason/);
  assert.match(workbenchSource, /data-agent-builder-publish-reason/);
  assert.match(workbenchSource, /disabled=\{interactionBusy \|\| publishBlock !== null\}/);
});

test("destructive server reload fences every editor interaction", () => {
  assert.match(
    workbenchSource,
    /interactionBusy = mutationBusy \|\| workbench\.destructiveReloadPending/,
  );
  assert.match(controllerSource, /this\.stateValue\.destructiveReloadPending/);
});

test("real lifecycle controls use the profile authority without fake handoff paths", () => {
  const featureProductionSource = [workbenchSource, controllerSource].join("\n");
  assert.doesNotMatch(featureProductionSource, /local-draft-[12]/);
  assert.doesNotMatch(featureProductionSource, /useAgent/);
  assert.doesNotMatch(featureProductionSource, /预览消息|打开对话运行|对话交接/);
  assert.doesNotMatch(featureProductionSource, /sessionApi|sendMessage|onHandoffReady/);
  assert.match(workbenchSource, /controller\.runActiveProfileTest\(message\)/);
  assert.match(workbenchSource, /controller\.unpublishActiveProfile\(\)/);
  assert.match(controllerSource, /this\.api\.runTest\(/);
  assert.match(controllerSource, /this\.api\.unpublish\(/);
  assert.doesNotMatch(featureProductionSource, /deactivate|handoff/i);
});

test("safe errors never render arbitrary Error.message", () => {
  assert.match(controllerSource, /error instanceof ApiRequestError/);
  assert.match(controllerSource, /SAFE_ERROR_CODE/);
  assert.doesNotMatch(controllerSource, /error\.message/);
  assert.doesNotMatch(workbenchSource, /error instanceof Error \? error\.message/);
});

test("builder keeps execution fields primary and collapses optional market metadata", () => {
  assert.match(enterpriseFieldsSource, /市场展示（可选）/);
  assert.match(enterpriseFieldsSource, /编辑市场卡片/);
  assert.doesNotMatch(enterpriseFieldsSource, /开场内容/);
  assert.match(enterpriseFieldsSource, /示例问题（可选）/);
  assert.match(enterpriseFieldsSource, /预期输出（可选）/);
  assert.match(enterpriseFieldsSource, /访问范围与数据说明（高级）/);
  assert.match(enterpriseFieldsSource, /<option value="tenant">全公司<\/option>/);
  assert.doesNotMatch(enterpriseFieldsSource, />全租户</);
  assert.match(workbenchSource, /一个专家固定一个主 Skill/);
  assert.match(workbenchSource, /专家开场白/);
  assert.match(workbenchSource, /专家指令/);
  assert.match(workbenchSource, /title="选择主 Skill"/);
});
