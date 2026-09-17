import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

function read(path: string): string {
  return readFileSync(join(root, path), "utf8");
}

test("skills and MCP surfaces avoid obsolete department availability placeholders", () => {
  const skillsHub = read("src/components/panels/SkillsHubPanel.tsx");
  const mcp = read("src/components/panels/MCPPanel.tsx");

  assert.match(skillsHub, /data-phase1c-surface="skills-hub"/);
  assert.doesNotMatch(skillsHub, /GroupAvailabilityToggleRow/);
  assert.doesNotMatch(skillsHub, /department-skill-policy/);
  for (const source of [skillsHub, mcp]) {
    assert.doesNotMatch(source, /skill-theme-shell|glass-shell/);
    assert.match(
      source,
      /bg-\[var\(--theme-workbench-canvas\)\]|workbenchSurface\.(?:page|statePage)/,
    );
  }
});

test("skills remain a catalog shell when backend enablement is unavailable", () => {
  const skillsHub = read("src/components/panels/SkillsHubPanel.tsx");
  const skillsPanel = read("src/components/panels/SkillsPanel/index.tsx");

  assert.doesNotMatch(skillsHub, /if\s*\(!enableSkills\)\s*{\s*return/);
  assert.doesNotMatch(skillsPanel, /if\s*\(!enableSkills\)\s*{\s*return/);
  assert.match(skillsHub, /const \[catalogState, setCatalogState\]/);
  assert.match(skillsHub, /catalogReadPending/);
  assert.match(skillsHub, /catalogPermissionDenied:\s*catalogState\.permissionDenied/);
  assert.match(skillsHub, /projectionError:\s*catalogState\.projectionError/);
  assert.match(skillsHub, /effectivePermissions:\s*catalogState\.effectivePermissions/);
  assert.match(skillsHub, /onCatalogStateChange=\{handleCatalogStateChange\}/);
  assert.match(skillsHub, /data-skill-catalog-shell/);
  assert.doesNotMatch(skillsHub, /MarketplacePanel|data-marketplace-catalog-shell/);
  assert.match(skillsHub, /buildFrontendGovernanceSmokeAttributes\(governanceState\)/);
  assert.match(skillsPanel, /governedUnavailable/);
  assert.match(skillsPanel, /effectivePermissions:\s*actions\.effectivePermissions/);
  assert.doesNotMatch(skillsPanel, /!enableSkills/);
});

test("mcp lifecycle governance exposes the backed admin lifecycle within role boundaries", () => {
  const mcp = read("src/components/panels/MCPPanel.tsx");

  assert.match(mcp, /data-phase1c-surface="mcp"/);
  assert.match(mcp, /lifecycleAvailability/);
  assert.match(mcp, /mcp\.lifecycleGovernance/);
  assert.doesNotMatch(mcp, /data-mcp-summary-status/);
  assert.doesNotMatch(mcp, /summaryGridFour/);
  assert.doesNotMatch(mcp, /mcp\.admin\.credentialsSummary/);
  assert.doesNotMatch(mcp, /mcp\.credentialsGovernance/);
  assert.doesNotMatch(mcp, /mcp\.credentialsUnavailable/);
  assert.doesNotMatch(mcp, /mcp\.lifecycleUnavailable/);
  assert.match(mcp, /data-mcp-directory-shell/);
  assert.match(mcp, /isPermissionError\(error\)/);
  assert.match(mcp, /enabled: !permissionDenied/);
  assert.doesNotMatch(mcp, /hasAnyPermission\(\[Permission\.MCP_READ\]\)/);
  assert.match(mcp, /canManageMcp && !mcpGovernance\.governedUnavailable/);
  assert.match(mcp, /data-mcp-admin-controls/);
  assert.match(mcp, /deleteServer\(/);
  assert.match(mcp, /createServer\(/);
  assert.doesNotMatch(mcp, /updateCredentials\(/);
});

test("authenticated admin surfaces avoid legacy glass and heavy modal styling", () => {
  const activeSurfaceFiles = [
    "src/components/panels/RolesPanel.tsx",
    "src/components/panels/MemoryPanel/index.tsx",
    "src/components/common/ConfirmDialog.tsx",
    "src/components/common/ContactAdminDialog.tsx",
    "src/components/common/SelectionActionPopover.tsx",
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

test("memory workbench excludes retired legacy CRUD companions", () => {
  const retiredFiles = [
    "DeleteModal.tsx",
    "DetailModal.tsx",
    "MemoryEditor.tsx",
    "MemoryFilter.tsx",
    "constants.ts",
    "useRelativeTime.ts",
  ];
  const memoryPanel = read("src/components/panels/MemoryPanel/index.tsx");

  for (const file of retiredFiles) {
    const moduleSpecifier = file.replace(/\.tsx?$/, "");
    assert.equal(
      existsSync(join(root, "src/components/panels/MemoryPanel", file)),
      false,
      file,
    );
    assert.doesNotMatch(
      memoryPanel,
      new RegExp(`["']\\./${moduleSpecifier}(?:\\.tsx?)?["']`),
    );
  }
});

test("shared workbench support surfaces use enterprise tokens instead of legacy glass", () => {
  const sources = new Map([
    [
      "PanelSkeletons",
      read("src/components/skeletons/PanelSkeletons.tsx"),
    ],
    ["MemoryPanel", read("src/components/panels/MemoryPanel/index.tsx")],
    ["ApprovalPanel", read("src/components/panels/ApprovalPanel.tsx")],
  ]);

  const forbiddenPatterns = [
    /glass-card/,
    /rounded-xl|rounded-2xl|rounded-3xl/,
    /var\(--glass-/,
    /\bbg-white\b/,
    /\bdark:bg-stone-(?:700|800|900|950)\b/,
    /\btext-stone-(?:700|800|900)\b/,
  ];

  for (const [name, source] of sources) {
    assert.match(
      source,
      /panel-card|enterprise-form-input|enterprise-subtle-panel|btn-icon/,
      `${name} should depend on the shared enterprise workbench vocabulary`,
    );
    for (const pattern of forbiddenPatterns) {
      assert.doesNotMatch(source, pattern, name);
    }
  }
});

test("governed MCP and Skill hooks fail closed before calling APIs", () => {
  const mcpHook = read("src/hooks/useMcp.ts");
  const toolsHook = read("src/hooks/useTools.ts");
  const skillsHook = read("src/hooks/useSkills.ts");
  const skillsList = read("src/components/panels/SkillsPanel/SkillsList.tsx");
  const skillCard = read("src/components/skill/SkillCard.tsx");

  for (const apiName of [
    "getServer",
    "createServer",
    "updateServer",
    "deleteServer",
    "toggleServer",
  ]) {
    assert.match(
      mcpHook,
      new RegExp(`const ${apiName} = useCallback[\\s\\S]*?if \\(!enabled\\)`),
      `${apiName} must guard enabled=false before MCP API calls`,
    );
  }
  assert.match(mcpHook, /getServer[\s\S]*?encodeURIComponent\(name\)/);
  assert.match(mcpHook, /toggleServer[\s\S]*?encodeURIComponent\(name\)[\s\S]*?\/toggle/);

  for (const apiName of [
    "getSkill",
    "getFullSkill",
    "updateSkill",
    "deleteSkill",
    "toggleSkill",
    "batchDeleteSkills",
    "batchToggleSkills",
    "toggleCategory",
    "toggleAll",
    "uploadSkill",
    "previewZipSkills",
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
  assert.doesNotMatch(skillsHook, /publishToMarketplace|isPublishing/);
  assert.doesNotMatch(
    read("src/services/api/skill.ts"),
    /\/api\/skills\/.*\/publish|publishToMarketplace/,
  );
  assert.match(
    toolsHook,
    /if \(!hookEnabled\) \{[\s\S]*?setCatalog\(\{ generation, status: "empty", \.\.\.EMPTY_CATALOG \}\);[\s\S]*?return;/,
  );
  assert.match(
    toolsHook,
    /authenticatedRequest\(`\/api\/mcp\/chat-tools\$\{query\}`\)/,
  );
  assert.match(toolsHook, /if \(!rawResponse\.ok\) throw new Error\("chat_mcp_catalog_request_failed"\)/);
  assert.match(
    toolsHook,
    /const payload: unknown = await rawResponse\.json\(\);[\s\S]*?parseChatMcpCatalogResponse\(payload\)/,
  );
  assert.match(toolsHook, /publishChatMcpCatalogFailure\(current, generation\)/);
  assert.match(
    toolsHook,
    /const authorizedIds = new Set\(tools\.map\(\(tool\) => tool\.name\)\);[\s\S]*?filter\(\(toolId\) => authorizedIds\.has\(toolId\)\)/,
  );
  assert.doesNotMatch(
    toolsHook,
    /mcpApi\.|void agentIdRef\.current;\s*setTools\(\[\]\);/,
  );
  assert.match(skillsList, /canImportSkills/);
  assert.match(skillsList, /canEditSkills/);
  assert.match(skillsList, /canBatchSkills/);
  assert.match(skillsList, /const uploadAction = canImportSkills/);
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
  assert.match(skillsList, /canBatchSkills/);
  assert.match(skillsList, /const uploadAction = canImportSkills/);
  assert.match(skillsList, /\{uploadAction \? \(/);
  assert.doesNotMatch(skillsList, /canCreateSkills|onCreate/);
  assert.doesNotMatch(
    skillsList,
    /disabled=\{governedUnavailable \|\| !canWrite\}/,
  );

  assert.match(skillCard, /hasWriteActions/);
  assert.match(skillCard, /footer=\{\s*hasWriteActions/);
  assert.match(skillCard, /\{canWrite && \(/);
  assert.match(skillCard, /\{canEdit && \(/);
  assert.match(skillCard, /\{canDelete && \(/);
  assert.doesNotMatch(skillCard, /onPublish|publishToMarketplace|republish/);
  assert.doesNotMatch(skillCard, /disabled=\{!canWrite\}/);
  assert.doesNotMatch(skillCard, /disabled=\{!canEdit\}/);
  assert.doesNotMatch(skillCard, /disabled=\{!canDelete\}/);

  assert.match(batchActionBar, /canWrite: boolean/);
  assert.match(batchActionBar, /canDelete: boolean/);
  assert.match(batchActionBar, /\{canWrite && \(/);
  assert.match(batchActionBar, /\{canDelete && \(/);
  assert.match(skillsPanel, /skillFileWriteBacked = true/);
  assert.match(skillsPanel, /skillImportBacked = true/);
  assert.match(skillsPanel, /skillBatchWriteBacked = true/);
  assert.match(skillsPanel, /canWrite=\{canWrite && !isGovernedUnavailable\}/);
  assert.match(
    skillsPanel,
    /canEdit=\{canEditSkills && !isGovernedUnavailable\}/,
  );
  assert.doesNotMatch(skillsPanel, /canCreateSkills|onCreate=\{/);
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

test("skills phase one backed operations match current public contracts", () => {
  const skillsPanel = read("src/components/panels/SkillsPanel/index.tsx");
  const skillApi = read("src/services/api/skill.ts");
  const skillsList = read("src/components/panels/SkillsPanel/SkillsList.tsx");
  const skillsActions = read(
    "src/components/panels/SkillsPanel/useSkillsActions.ts",
  );
  const zipUploadModal = read(
    "src/components/panels/SkillsPanel/ZipUploadModal.tsx",
  );

  assert.match(skillApi, /async batchToggle/);
  assert.match(skillApi, /\/batch\/toggle/);
  assert.match(skillApi, /async batchDelete/);
  assert.match(skillApi, /\/batch\/delete/);
  assert.match(skillApi, /async toggle/);
  assert.match(skillApi, /\/toggle/);
  assert.doesNotMatch(skillApi, /async updateFile/);
  assert.match(skillApi, /async uploadZip/);
  assert.match(skillApi, /async adminReviewSkillVersion/);
  assert.match(skillApi, /async adminPromoteSkillVersion/);
  assert.match(skillApi, /authFetch<unknown>/);
  assert.doesNotMatch(skillApi, /async previewGitHub|async installGitHub/);
  assert.match(skillsPanel, /skillFileWriteBacked = true/);
  assert.match(skillsPanel, /skillImportBacked = true/);
  assert.match(skillsPanel, /skillBatchWriteBacked = true/);
  assert.match(skillsList, /onSelectAll=\{onSelectAll\}/);
  assert.match(
    skillsActions,
    /initialZipSkillSelection\([\s\S]*?zipTargetSkillName/,
  );
  assert.match(skillsActions, /canAdminUploadSkills = isAiAdminUser\(user\)/);
  assert.doesNotMatch(skillsActions, /handleCreate|createSkill/);
  assert.doesNotMatch(skillApi, /async create\(data: SkillCreate\)/);
  assert.doesNotMatch(skillsActions, /Permission\.SKILL_ADMIN/);
  assert.match(zipUploadModal, /const backedCount = zipSkills\.filter\(\(s\) => s\.already_exists\)\.length/);
  assert.match(zipUploadModal, /canSelectZipSkill\([\s\S]*?targetSkillName/);
  assert.match(zipUploadModal, /!skill\.already_exists && !adminRelease/);
  assert.doesNotMatch(skillsPanel, /skillBatchWriteBacked = false/);
});

test("role plaza stays reachable without claiming missing backend projection", () => {
  const rolesPanel = read("src/components/panels/RolesPanel.tsx");
  const roleGovernanceApi = read("src/services/api/roleGovernance.ts");
  const zh = JSON.parse(read("src/i18n/locales/zh.json"));

  assert.match(rolesPanel, /data-role-plaza-shell/);
  assert.doesNotMatch(rolesPanel, /data-role-plaza-backend-gap/);
  assert.doesNotMatch(rolesPanel, /roles\.plaza\.backendGap/);
  for (const source of [
    rolesPanel,
    JSON.stringify(zh.roles?.plaza ?? {}),
  ]) {
    assert.doesNotMatch(source, /backendGap/);
    assert.doesNotMatch(source, /public projection/i);
    assert.doesNotMatch(source, /backend gap/i);
    assert.doesNotMatch(source, /投影/);
  }
  assert.match(rolesPanel, /resolveRoleGovernanceState/);
  assert.match(rolesPanel, /roleGovernanceApi\.getOverview/);
  assert.match(
    rolesPanel,
    /buildFrontendGovernanceSmokeAttributes\(roleGovernance\.pageState\)/,
  );
  assert.doesNotMatch(rolesPanel, /data-frontend-governance-state="ready"/);
  assert.doesNotMatch(rolesPanel, /roleDirectoryBacked:\s*false/);
  assert.match(roleGovernanceApi, /\/api\/role-governance\/overview/);
  assert.match(roleGovernanceApi, /\/api\/role-governance\/requests/);
  assert.match(roleGovernanceApi, /\/api\/role-governance\/approvals/);
  assert.match(roleGovernanceApi, /\/api\/role-governance\/audit/);
  assert.match(rolesPanel, /Permission\.ROLE_READ/);
  assert.match(rolesPanel, /Permission\.ROLE_REQUEST/);
  assert.match(rolesPanel, /Permission\.ROLE_MANAGE/);
});
