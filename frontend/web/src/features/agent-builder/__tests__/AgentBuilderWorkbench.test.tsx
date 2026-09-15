import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import type { PublicSkillResponse } from "../../../types";
import { filterSkillCatalog } from "../skillSetSearch";

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
const avatarPickerSource = readFileSync(
  join(process.cwd(), "src/features/agent-builder/AgentAvatarPicker.tsx"),
  "utf8",
);

function skill(
  name: string,
  description: string,
  tags: string[] = [],
): PublicSkillResponse {
  return {
    name,
    expected_version: "1.0.0",
    input_modes: ["chat"],
    requires_file: false,
    description,
    tags,
    enabled: true,
    source: "marketplace",
    files: {},
    file_count: 0,
    installed_from: "marketplace",
    is_published: true,
    marketplace_is_active: true,
  };
}

test("Skill Set search matches names, descriptions, and tags", () => {
  const skills = [
    skill("qa-file-reviewer", "审核 Word 文档", ["合规"]),
    skill("ragflow-knowledge-search", "检索知识库", ["搜索"]),
    skill("audit-finding-rca", "分析审计发现", ["质量"]),
  ];

  assert.deepEqual(
    filterSkillCatalog(skills, "合规").map(({ name }) => name),
    ["qa-file-reviewer"],
  );
  assert.deepEqual(
    filterSkillCatalog(skills, "知识库").map(({ name }) => name),
    ["ragflow-knowledge-search"],
  );
  assert.deepEqual(
    filterSkillCatalog(skills, "audit").map(({ name }) => name),
    ["audit-finding-rca"],
  );
  assert.deepEqual(filterSkillCatalog(skills, "不存在"), []);
});

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

test("workbench has explicit admin, loading, error, empty, and New Expert surfaces", () => {
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
  assert.match(workbenchSource, /controller\.unpublishActiveProfile\(publishedRevision\)/);
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

test("expert management follows the directory and first-class configuration layout", () => {
  assert.match(workbenchSource, /专家管理/);
  assert.match(workbenchSource, /专家目录/);
  assert.match(workbenchSource, /aria-label="搜索专家"/);
  assert.match(workbenchSource, /AGENT_DIRECTORY_PAGE_SIZE/);
  assert.match(workbenchSource, /paginatedProfiles\.map/);
  assert.match(workbenchSource, /<Pagination/);
  assert.match(workbenchSource, /AgentIdentityAvatar/);
  assert.match(workbenchSource, /完成下面 3 项即可保存/);
  assert.match(workbenchSource, /Agent\.md 初始指令/);
  assert.match(workbenchSource, /data-agent-builder-agent-md/);
  assert.match(enterpriseFieldsSource, /data-agent-builder-market-settings/);
  assert.match(enterpriseFieldsSource, /市场展示/);
  assert.match(enterpriseFieldsSource, /对话启动问题/);
  assert.match(enterpriseFieldsSource, /启动问题（可选）/);
  assert.doesNotMatch(enterpriseFieldsSource, /对话开场|示例问题（可选）|预期输出（可选）/);
  assert.doesNotMatch(enterpriseFieldsSource, /data-agent-builder-input-settings/);
  assert.match(enterpriseFieldsSource, /data-agent-builder-access-settings/);
  assert.match(enterpriseFieldsSource, /访问范围（高级）/);
  assert.match(enterpriseFieldsSource, /<option value="tenant">全公司<\/option>/);
  assert.doesNotMatch(enterpriseFieldsSource, />全租户</);
  assert.match(workbenchSource, /Agent SDK 根据任务上下文自主决定/);
  assert.match(workbenchSource, /title="配置 Skill Set"/);
  assert.match(workbenchSource, /aria-label="搜索 Skill"/);
  assert.match(workbenchSource, /按名称、描述或标签搜索/);
  assert.match(workbenchSource, /filteredSkills/);
  assert.match(workbenchSource, /没有匹配的 Skill/);
  assert.match(workbenchSource, /line-clamp-2/);
  assert.match(enterpriseFieldsSource, /AgentAvatarPicker/);
  assert.match(enterpriseFieldsSource, /role="combobox"/);
  assert.match(enterpriseFieldsSource, /role="listbox"/);
  assert.match(enterpriseFieldsSource, /filterMarketTagSuggestions/);
  assert.doesNotMatch(enterpriseFieldsSource, /<datalist/);
  assert.match(avatarPickerSource, /头像风格/);
  assert.match(avatarPickerSource, /<select/);
  assert.match(avatarPickerSource, /aria-label="选择头像风格"/);
  assert.match(avatarPickerSource, /AGENT_AVATAR_STYLE_OPTIONS\.map/);
  assert.match(avatarPickerSource, /换一批/);
  assert.doesNotMatch(enterpriseFieldsSource, /头像种子/);
  assert.doesNotMatch(enterpriseFieldsSource, /支持输入|支持文件类型|文件格式/);
});
