import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

function read(path: string): string {
  return readFileSync(join(root, path), "utf8");
}

test("department skill policy is rendered as a fail-closed group toggle row", () => {
  assert.equal(
    existsSync(
      join(root, "src/components/governance/GroupAvailabilityToggleRow.tsx"),
    ),
    true,
  );

  const toggle = read("src/components/governance/GroupAvailabilityToggleRow.tsx");
  const availability = read("src/components/governance/groupAvailability.ts");

  assert.match(toggle, /data-group-toggle-ui/);
  assert.match(toggle, /department-skill-policy/);
  assert.match(toggle, /backed,/);
  assert.match(toggle, /aria-disabled=\{disabled\}/);
  assert.match(availability, /backed === false/);
  assert.match(availability, /state:\s*"unavailable"/);
});

test("skills and marketplace surfaces expose department availability controls", () => {
  const skillsHub = read("src/components/panels/SkillsHubPanel.tsx");
  const marketplace = read("src/components/panels/MarketplacePanel.tsx");
  const mcp = read("src/components/panels/MCPPanel.tsx");

  assert.match(skillsHub, /GroupAvailabilityToggleRow/);
  assert.match(skillsHub, /skills\.marketplace\.departmentAvailability/);
  assert.match(skillsHub, /data-phase1c-surface="skills-hub"/);
  assert.match(marketplace, /GroupAvailabilityToggleRow/);
  assert.match(marketplace, /skills\.marketplace\.departmentAvailability/);
  assert.match(marketplace, /data-phase1c-surface="marketplace"/);
  for (const source of [skillsHub, marketplace, mcp]) {
    assert.doesNotMatch(source, /skill-theme-shell|glass-shell/);
    assert.match(source, /bg-\[var\(--theme-bg\)\]/);
  }
});

test("skills and marketplace remain catalog shells when backend enablement is unavailable", () => {
  const skillsHub = read("src/components/panels/SkillsHubPanel.tsx");
  const skillsPanel = read("src/components/panels/SkillsPanel/index.tsx");
  const marketplace = read("src/components/panels/MarketplacePanel.tsx");

  assert.doesNotMatch(skillsHub, /if\s*\(!enableSkills\)\s*{\s*return/);
  assert.doesNotMatch(skillsPanel, /if\s*\(!enableSkills\)\s*{\s*return/);
  assert.match(skillsHub, /skillsProjectionDegraded/);
  assert.match(skillsHub, /data-skill-catalog-shell/);
  assert.match(skillsHub, /data-marketplace-catalog-shell/);
  assert.match(skillsHub, /data-frontend-governance-state/);
  assert.match(skillsPanel, /governedUnavailable/);
  assert.doesNotMatch(skillsPanel, /!enableSkills/);
  assert.match(marketplace, /governedUnavailable/);
  assert.match(marketplace, /data-marketplace-catalog-shell/);
  assert.match(marketplace, /data-marketplace-unavailable-shell/);
  assert.match(marketplace, /data-marketplace-filter-shell/);
  assert.match(marketplace, /data-marketplace-placeholder-list/);
  assert.doesNotMatch(marketplace, /return\s*<WorkbenchUnavailableState/);
});

test("marketplace fail-closed copy is written for ordinary company users", () => {
  const marketplace = read("src/components/panels/MarketplacePanel.tsx");
  const zh = JSON.parse(read("src/i18n/locales/zh.json"));
  const en = JSON.parse(read("src/i18n/locales/en.json"));

  assert.match(marketplace, /data-marketplace-ordinary-user-copy/);
  assert.match(marketplace, /marketplace\.emptyDepartmentCatalog/);
  assert.match(marketplace, /marketplace\.requestAccess/);
  for (const source of [
    marketplace,
    JSON.stringify(zh.marketplace),
    JSON.stringify(zh.skillsHub),
    JSON.stringify(zh.skills.marketplace),
    JSON.stringify(en.marketplace),
    JSON.stringify(en.skillsHub),
    JSON.stringify(en.skills.marketplace),
  ]) {
    assert.doesNotMatch(source, /backend authority/i);
    assert.doesNotMatch(source, /policy placeholders/i);
    assert.doesNotMatch(source, /projection/i);
    assert.doesNotMatch(source, /投影/);
    assert.doesNotMatch(source, /后端合约/);
  }
});

test("skills marketplace cards use restrained workbench tiles instead of gradient cards", () => {
  const baseCard = read("src/components/common/SkillBaseCard.tsx");
  const marketplaceCard = read(
    "src/components/panels/MarketplacePanel/SkillCard.tsx",
  );
  const marketplace = read("src/components/panels/MarketplacePanel.tsx");

  assert.doesNotMatch(baseCard, /rounded-2xl|rounded-3xl/);
  assert.doesNotMatch(baseCard, /linear-gradient/);
  assert.doesNotMatch(baseCard, /scb__banner/);
  assert.doesNotMatch(marketplaceCard, /nameToGradient/);
  assert.doesNotMatch(marketplaceCard, /gradient=\{gradient\}/);
  assert.doesNotMatch(marketplace, /rounded-2xl|rounded-3xl/);
  assert.match(baseCard, /rounded-lg/);
});

test("mcp lifecycle governance remains visible but not writable", () => {
  const mcp = read("src/components/panels/MCPPanel.tsx");

  assert.match(mcp, /data-phase1c-surface="mcp"/);
  assert.match(mcp, /data-fail-closed-surface="mcp-lifecycle"/);
  assert.match(mcp, /lifecycleAvailability/);
  assert.match(mcp, /mcp\.credentialsUnavailable/);
  assert.match(mcp, /data-mcp-directory-shell/);
  assert.doesNotMatch(mcp, /deleteServer\(|createServer\(|updateCredentials\(/);
});

test("admin feedback and notification panels use shared enterprise primitives", () => {
  const feedback = read("src/components/panels/FeedbackPanel.tsx");
  const notifications = read("src/components/panels/NotificationPanel.tsx");
  const componentsCss = read("src/styles/components.css");

  for (const utility of [
    "enterprise-modal-backdrop",
    "enterprise-modal-shell",
    "enterprise-form-input",
    "enterprise-icon-button",
    "enterprise-empty-state",
  ]) {
    assert.match(componentsCss, new RegExp(`\\.${utility}`));
  }

  for (const source of [feedback, notifications]) {
    assert.match(source, /enterprise-modal-backdrop/);
    assert.match(source, /enterprise-modal-shell/);
    assert.match(source, /enterprise-empty-state/);
    assert.doesNotMatch(source, /shadow-xl/);
    assert.doesNotMatch(source, /fixed inset-0 z-50 bg-black\/50/);
    assert.doesNotMatch(source, /glass-card/);
    assert.doesNotMatch(source, /ChatGPT style/i);
  }

  assert.match(notifications, /enterprise-form-input/);
  assert.match(notifications, /enterprise-form-textarea/);
  assert.match(feedback, /enterprise-code-chip/);
});

test("authenticated admin surfaces avoid legacy glass and heavy modal styling", () => {
  const activeSurfaceFiles = [
    "src/components/panels/UsersPanel.tsx",
    "src/components/panels/RolesPanel.tsx",
    "src/components/panels/SettingsPanel.tsx",
    "src/components/panels/JsonSchemaEditor.tsx",
    "src/components/panels/SystemHealthSection.tsx",
    "src/components/panels/AdminRuntimeCapacitySection.tsx",
    "src/components/panels/AgentPanel/AgentConfigPanel.tsx",
    "src/components/panels/AgentPanel/shared/ProviderSelect.tsx",
    "src/components/panels/AgentPanel/shared/RoleSelector.tsx",
    "src/components/panels/AgentPanel/shared/ToggleSwitch.tsx",
    "src/components/panels/AgentPanel/tabs/GlobalAgentTab.tsx",
    "src/components/panels/AgentPanel/tabs/RolesAgentTab.tsx",
    "src/components/panels/MemoryPanel/index.tsx",
    "src/components/panels/MemoryPanel/MemoryFilter.tsx",
    "src/components/panels/MemoryPanel/MemoryEditor.tsx",
    "src/components/common/ConfirmDialog.tsx",
    "src/components/common/DeleteProjectDialog.tsx",
    "src/components/common/AboutDialog.tsx",
    "src/components/common/ContactAdminDialog.tsx",
    "src/components/common/SelectionActionPopover.tsx",
    "src/components/panels/NewProjectModal.tsx",
    "src/components/panels/SessionSidebar.tsx",
    "src/components/panels/SkillsPanel/BatchActionBar.tsx",
  ];

  for (const path of activeSurfaceFiles) {
    const source = read(path);
    assert.doesNotMatch(source, /glass-card|glass-card-subtle/, path);
    assert.doesNotMatch(source, /shadow-xl|shadow-2xl/, path);
    assert.doesNotMatch(source, /bg-black\/(?:40|50)/, path);
    assert.doesNotMatch(source, /rounded-2xl|rounded-3xl/, path);
    assert.doesNotMatch(source, /border-\[var\(--glass-border\)\]/, path);
    assert.doesNotMatch(
      source,
      /bg-\[var\(--glass-bg(?:-subtle|-hover)?\)\]/,
      path,
    );
  }
});

test("governed marketplace and MCP hooks fail closed before calling APIs", () => {
  const marketplaceHook = read("src/hooks/useMarketplace.ts");
  const mcpHook = read("src/hooks/useMcp.ts");
  const skillsHook = read("src/hooks/useSkills.ts");
  const skillsList = read("src/components/panels/SkillsPanel/SkillsList.tsx");
  const skillCard = read("src/components/skill/SkillCard.tsx");

  for (const apiName of [
    "installSkill",
    "updateSkill",
    "openPreview",
    "readPreviewFile",
    "createAndPublish",
    "updateMarketplaceSkill",
    "activateSkill",
    "deleteSkill",
    "loadMarketplaceSkillForEdit",
  ]) {
    assert.match(
      marketplaceHook,
      new RegExp(`const ${apiName} = useCallback[\\s\\S]*?if \\(!enabled\\)`),
      `${apiName} must guard enabled=false before marketplace API calls`,
    );
  }

  for (const apiName of [
    "getServer",
    "createServer",
    "updateServer",
    "deleteServer",
    "toggleServer",
    "importServers",
    "exportServers",
    "promoteServer",
    "demoteServer",
  ]) {
    assert.match(
      mcpHook,
      new RegExp(`const ${apiName} = useCallback[\\s\\S]*?if \\(!enabled\\)`),
      `${apiName} must guard enabled=false before MCP API calls`,
    );
  }

  for (const apiName of [
    "getSkill",
    "getFullSkill",
    "createSkill",
    "updateSkill",
    "deleteSkill",
    "toggleSkill",
    "batchDeleteSkills",
    "batchToggleSkills",
    "toggleCategory",
    "toggleAll",
    "uploadSkill",
    "previewZipSkills",
    "previewGitHubSkills",
    "installGitHubSkills",
    "publishToMarketplace",
  ]) {
    assert.match(
      skillsHook,
      new RegExp(`const ${apiName} = useCallback[\\s\\S]*?if \\(!enabled\\)`),
      `${apiName} must guard enabled=false before skills API calls`,
    );
  }

  assert.match(
    skillsHook,
    /const toggleCategory = useCallback\(\s*async \(_category: SkillSource, nextEnabled: boolean\): Promise<boolean> => {\s*if \(!enabled\) return false;/,
    "toggleCategory must guard hook-level enabled before using target state",
  );
  assert.match(
    skillsHook,
    /const toggleAll = useCallback\(\s*async \(nextEnabled: boolean\): Promise<boolean> => {\s*if \(!enabled\) return false;/,
    "toggleAll must guard hook-level enabled before using target state",
  );
  assert.match(skillsHook, /effectivePermissions/);
  assert.match(skillsList, /canImportSkills/);
  assert.match(skillsList, /canEditSkills/);
  assert.match(skillsList, /canCreateSkills/);
  assert.match(skillsList, /canBatchSkills/);
  assert.match(skillsList, /canManageSkills/);
  assert.match(skillCard, /hasWriteActions/);
  assert.match(skillCard, /canEdit/);
  assert.doesNotMatch(
    skillsList,
    /disabled=\{governedUnavailable \|\| !canWrite\}/,
  );
  assert.doesNotMatch(skillCard, /disabled=\{!canWrite\}/);
  assert.doesNotMatch(skillCard, /disabled=\{!canDelete\}/);
});

test("read-only skills catalog removes write controls instead of showing disabled placeholders", () => {
  const skillsList = read("src/components/panels/SkillsPanel/SkillsList.tsx");
  const skillCard = read("src/components/skill/SkillCard.tsx");
  const batchActionBar = read(
    "src/components/panels/SkillsPanel/BatchActionBar.tsx",
  );
  const skillsPanel = read("src/components/panels/SkillsPanel/index.tsx");

  assert.match(skillsList, /canImportSkills/);
  assert.match(skillsList, /canEditSkills/);
  assert.match(skillsList, /canCreateSkills/);
  assert.match(skillsList, /canBatchSkills/);
  assert.match(skillsList, /canManageSkills/);
  assert.match(skillsList, /\{canBatchSkills && filteredSkills\.length > 0 &&/);
  assert.match(skillsList, /\{canImportSkills && \(/);
  assert.match(skillsList, /\{canCreateSkills && \(/);
  assert.doesNotMatch(
    skillsList,
    /disabled=\{governedUnavailable \|\| !canWrite\}/,
  );

  assert.match(skillCard, /hasWriteActions/);
  assert.match(skillCard, /footer=\{\s*hasWriteActions/);
  assert.match(skillCard, /\{canWrite && \(/);
  assert.match(skillCard, /\{canEdit && \(/);
  assert.match(skillCard, /\{canDelete && \(/);
  assert.doesNotMatch(skillCard, /disabled=\{!canWrite\}/);
  assert.doesNotMatch(skillCard, /disabled=\{!canEdit\}/);
  assert.doesNotMatch(skillCard, /disabled=\{!canDelete\}/);

  assert.match(batchActionBar, /canWrite: boolean/);
  assert.match(batchActionBar, /canDelete: boolean/);
  assert.match(batchActionBar, /\{canWrite && \(/);
  assert.match(batchActionBar, /\{canDelete && \(/);
  assert.match(skillsPanel, /skillFileWriteBacked = false/);
  assert.match(skillsPanel, /skillImportBacked = false/);
  assert.match(skillsPanel, /skillBatchWriteBacked = true/);
  assert.match(skillsPanel, /canWrite=\{canWrite && !isGovernedUnavailable\}/);
  assert.match(
    skillsPanel,
    /canEdit=\{canEditSkills && !isGovernedUnavailable\}/,
  );
  assert.match(
    skillsPanel,
    /canCreate=\{canCreateSkills && !isGovernedUnavailable\}/,
  );
  assert.match(
    skillsPanel,
    /canImport=\{canImportSkills && !isGovernedUnavailable\}/,
  );
  assert.match(
    skillsPanel,
    /canBatch=\{canBatchSkills && !isGovernedUnavailable\}/,
  );
  assert.match(
    skillsPanel,
    /canDelete=\{canDeleteSkill && !isGovernedUnavailable\}/,
  );
});

test("skills phase one backed operations match PR177 public contracts", () => {
  const skillsPanel = read("src/components/panels/SkillsPanel/index.tsx");
  const skillApi = read("src/services/api/skill.ts");
  const skillsList = read("src/components/panels/SkillsPanel/SkillsList.tsx");

  assert.match(skillApi, /async batchToggle/);
  assert.match(skillApi, /\/batch\/toggle/);
  assert.match(skillApi, /async batchDelete/);
  assert.match(skillApi, /\/batch\/delete/);
  assert.match(skillApi, /async toggle/);
  assert.match(skillApi, /\/toggle/);
  assert.match(skillsPanel, /skillBatchWriteBacked = true/);
  assert.match(skillsList, /\{canBatchSkills && filteredSkills\.length > 0 &&/);
  assert.doesNotMatch(skillsPanel, /skillBatchWriteBacked = false/);
});

test("marketplace hides direct write governance until backend contracts exist", () => {
  const marketplace = read("src/components/panels/MarketplacePanel.tsx");
  const marketplaceCard = read(
    "src/components/panels/MarketplacePanel/SkillCard.tsx",
  );

  assert.match(marketplace, /marketplaceDirectWriteBacked = false/);
  assert.match(marketplace, /canCreateInMarketplace/);
  assert.match(marketplace, /canInstall/);
  assert.match(marketplace, /Permission\.SKILL_WRITE/);
  assert.match(marketplace, /Permission\.MARKETPLACE_READ/);
  assert.doesNotMatch(
    marketplace,
    /const canWrite =\s*hasAnyPermission\(\[Permission\.MARKETPLACE_PUBLISH\]\)/,
  );
  assert.match(marketplaceCard, /canInstall/);
  assert.doesNotMatch(marketplaceCard, /canWrite/);
});
