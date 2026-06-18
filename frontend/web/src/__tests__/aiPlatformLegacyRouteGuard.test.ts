import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

function readSource(relativePath: string): string {
  return readFileSync(new URL(relativePath, import.meta.url), "utf8");
}

const legacyRouteChecks: Array<{
  relativePath: string;
  bannedPatterns: RegExp[];
}> = [
  {
    relativePath: "../pwaRouting.ts",
    bannedPatterns: [/["'`]\/ws["'`]/, /["'`]\/human["'`]/, /["'`]\/tools["'`]/],
  },
  {
    relativePath: "../hooks/useWebSocket.ts",
    bannedPatterns: [/new WebSocket\(/, /\/ws\b/],
  },
  {
    relativePath: "../hooks/useApprovals.ts",
    bannedPatterns: [/\/human(?:\/|`|["'])/],
  },
  {
    relativePath: "../hooks/useAgent/eventHandlers.ts",
    bannedPatterns: [/\/human\/\$\{/],
  },
  {
    relativePath: "../hooks/useAgent/historyLoader.ts",
    bannedPatterns: [/\/human\/\$\{/],
  },
  {
    relativePath: "../components/panels/ApprovalPanel.tsx",
    bannedPatterns: [/\/human\/\$\{/],
  },
  {
    relativePath: "../hooks/useTools.ts",
    bannedPatterns: [/\$\{API_BASE\}\/tools/, /\$\{API_BASE\}\/mcp/],
  },
  {
    relativePath: "../components/profile/tabs/ProfileToolsTab.tsx",
    bannedPatterns: [/\$\{API_BASE\}\/tools/, /\$\{API_BASE\}\/mcp/],
  },
];

test("active ai-platform source does not contain legacy LambChat runtime routes", () => {
  const violations: string[] = [];

  for (const check of legacyRouteChecks) {
    const source = readSource(check.relativePath);
    for (const pattern of check.bannedPatterns) {
      if (pattern.test(source)) {
        violations.push(`${check.relativePath}: ${pattern}`);
      }
    }
  }

  assert.deepEqual(violations, []);
});

test("profile modal does not expose the legacy env-var surface", () => {
  const profileModalSource = readSource("../components/profile/ProfileModal.tsx");

  assert.doesNotMatch(profileModalSource, /ProfileEnvVarsTab/);
  assert.doesNotMatch(profileModalSource, /ProfileToolsTab/);
  assert.doesNotMatch(profileModalSource, /toolsTab/);
  assert.doesNotMatch(profileModalSource, /envvars/);
  assert.doesNotMatch(profileModalSource, /envVars\.title/);
});

test("profile and chat tool controls do not activate legacy MCP endpoints", () => {
  const useToolsSource = readSource("../hooks/useTools.ts");
  const profileToolsSource = readSource(
    "../components/profile/tabs/ProfileToolsTab.tsx",
  );

  assert.doesNotMatch(useToolsSource, /authenticatedRequest/);
  assert.doesNotMatch(useToolsSource, /\/mcp/);
  assert.doesNotMatch(profileToolsSource, /authenticatedRequest/);
  assert.doesNotMatch(profileToolsSource, /\/mcp/);
});

test("profile preferences do not activate legacy agent config endpoints", () => {
  const preferencesSource = readSource(
    "../components/profile/tabs/ProfilePreferencesTab.tsx",
  );

  assert.doesNotMatch(preferencesSource, /agentConfigApi/);
  assert.doesNotMatch(preferencesSource, /\/api\/agent\/config/);
  assert.doesNotMatch(preferencesSource, /getUserPreference/);
  assert.doesNotMatch(preferencesSource, /setUserPreference/);
  assert.match(preferencesSource, /agentApi\.list/);
  assert.match(preferencesSource, /authApi\.updateMetadata/);
});

test("active notification surfaces are read-only in Phase 1", () => {
  const headerSource = readSource(
    "../components/layout/AppContent/Header.tsx",
  );
  const dialogSource = readSource(
    "../components/notification/NotificationDialog.tsx",
  );
  const bannerSource = readSource(
    "../components/notification/NotificationBanner.tsx",
  );

  for (const source of [headerSource, dialogSource, bannerSource]) {
    assert.doesNotMatch(source, /notificationApi\.dismiss/);
    assert.doesNotMatch(source, /\/api\/notifications\/admin/);
    assert.doesNotMatch(source, /\/api\/notifications\/\$\{[^}]+\}\/dismiss/);
  }

  assert.match(headerSource, /notificationApi[\s\S]*?\.getActive/);
  assert.match(dialogSource, /notificationApi[\s\S]*?\.getActive/);
  assert.match(bannerSource, /notificationApi[\s\S]*?\.getActive/);
});

test("Phase 1 tab content does not activate backend-missing management panels", () => {
  const tabContentSource = readSource(
    "../components/layout/AppContent/TabContent.tsx",
  );

  for (const bannedPanel of [
    "SkillsHubPanel",
    "MarketplacePanel",
    "UsersPanel",
    "RolesPanel",
    "SettingsPanel",
    "MCPPanel",
    "FeedbackPanel",
    "ChannelPanel",
    "AgentConfigPanel",
    "ModelPanel",
    "RevealedFilesPanel",
    "NotificationPanel",
    "PersonaPlazaPanel",
  ]) {
    assert.doesNotMatch(tabContentSource, new RegExp(bannedPanel));
  }

  assert.match(tabContentSource, /Phase2UnavailablePanel/);
  assert.match(tabContentSource, /AdminRuntimePanel/);
  assert.match(tabContentSource, /Phase1SkillsGovernancePanel/);
  assert.match(tabContentSource, /Phase1ToolPolicyPanel/);
  assert.match(tabContentSource, /Phase1AgentAppsPanel/);
  assert.match(tabContentSource, /Phase1ModelCatalogPanel/);
  assert.match(tabContentSource, /Phase1NotificationsPanel/);
});

test("chat composer skill selector uses agent projections instead of legacy skill APIs", () => {
  const chatAppContent = readSource(
    "../components/layout/AppContent/ChatAppContent.tsx",
  );

  assert.doesNotMatch(chatAppContent, /useSkills\(/);
  assert.doesNotMatch(chatAppContent, /Permission\.SKILL_READ/);
  assert.doesNotMatch(chatAppContent, /toggleSkill:\s*toggleSessionSkill/);
  assert.doesNotMatch(
    chatAppContent,
    /personaSkillNames:\s*sessionConfig\.personaSnapshot\?\.skill_names/,
  );
  assert.doesNotMatch(
    chatAppContent,
    /enabledSkills:\s*sessionConfig\.personaSnapshot[\s\S]*?skill_names/,
  );
  assert.match(chatAppContent, /buildSkillOptionsFromAgents/);
  assert.match(
    chatAppContent,
    /buildSkillOptionsFromAgents\(agents,\s*currentAgent\)/,
  );
});

test("chat route does not activate Phase 2 persona or feedback APIs", () => {
  const chatAppContent = readSource(
    "../components/layout/AppContent/ChatAppContent.tsx",
  );
  const chatViewSource = readSource(
    "../components/layout/AppContent/ChatView.tsx",
  );
  const chatMessageSource = readSource(
    "../components/chat/ChatMessage/index.tsx",
  );
  const useAgentSource = readSource("../hooks/useAgent.ts");

  assert.doesNotMatch(chatAppContent, /usePersonaPresets/);
  assert.doesNotMatch(chatAppContent, /onUsePersonaPreset/);
  assert.doesNotMatch(chatAppContent, /personaPresets=/);
  assert.doesNotMatch(chatAppContent, /getPersonaPresetId/);

  assert.match(chatViewSource, /onUsePersonaPreset\s*=\s*undefined/);
  assert.match(chatViewSource, /personaPresets\s*=\s*\[\]/);
  assert.doesNotMatch(chatViewSource, /onUsePersonaPreset:/);

  assert.doesNotMatch(useAgentSource, /feedbackApi/);
  assert.doesNotMatch(useAgentSource, /canReadFeedback/);
  assert.doesNotMatch(useAgentSource, /feedbackPromise/);
  assert.doesNotMatch(useAgentSource, /Permission\.FEEDBACK_/);

  assert.match(chatViewSource, /onForkMessage=\{undefined\}/);
  assert.doesNotMatch(chatViewSource, /sessionApi\.forkMessage/);
  assert.doesNotMatch(chatMessageSource, /FeedbackButtons/);
  assert.doesNotMatch(chatMessageSource, /ShareButton/);
  assert.doesNotMatch(chatMessageSource, /ShareDialog/);
  assert.doesNotMatch(chatMessageSource, /feedbackApi/);
  assert.doesNotMatch(chatMessageSource, /Permission\.SESSION_SHARE/);
});

test("public share route is fail-closed in Phase 1", () => {
  const appSource = readSource("../App.tsx");

  assert.doesNotMatch(appSource, /components\/share\/SharedPage/);
  assert.doesNotMatch(appSource, /<SharedPage/);
  assert.match(appSource, /PublicShareUnavailablePage/);
  assert.match(appSource, /path="\/shared\/:shareId"/);
});

test("global app shell does not activate legacy settings APIs", () => {
  const settingsContextSource = readSource("../contexts/SettingsContext.tsx");
  const welcomePageSource = readSource("../components/chat/WelcomePage.tsx");
  const profileInfoSource = readSource(
    "../components/profile/tabs/ProfileInfoTab.tsx",
  );
  const contactAdminDialogSource = readSource(
    "../components/common/ContactAdminDialog.tsx",
  );

  assert.doesNotMatch(settingsContextSource, /hooks\/useSettings/);
  assert.doesNotMatch(settingsContextSource, /useSettings\(/);
  assert.doesNotMatch(settingsContextSource, /settingsApi/);
  assert.doesNotMatch(settingsContextSource, /\/api\/settings/);
  assert.match(settingsContextSource, /modelPublicApi[\s\S]*?\.listAvailable/);

  for (const source of [
    welcomePageSource,
    profileInfoSource,
    contactAdminDialogSource,
  ]) {
    assert.doesNotMatch(source, /useSettings/);
    assert.doesNotMatch(source, /getSettingValue/);
    assert.doesNotMatch(source, /ADMIN_CONTACT_/);
    assert.doesNotMatch(source, /WELCOME_SUGGESTIONS/);
  }
});

test("active branded shell does not expose upstream LambChat authority links", () => {
  const profileModalSource = readSource(
    "../components/profile/ProfileModal.tsx",
  );
  const helpMenuSource = readSource(
    "../components/chat/ChatInputHelpMenu.tsx",
  );

  for (const source of [profileModalSource, helpMenuSource]) {
    assert.doesNotMatch(source, /github\.com\/clivia\/LambChat/);
    assert.doesNotMatch(source, /yanyutin753\.github\.io\/LambChat/);
  }
});

test("Phase 1 remap panels consume ai-platform projections instead of legacy management APIs", () => {
  const phase1ProjectionApi = readSource(
    "../services/api/phase1Projection.ts",
  );
  const phase1Panels = readSource(
    "../components/panels/phase1ProjectionPanels.tsx",
  );

  for (const source of [phase1ProjectionApi, phase1Panels]) {
    assert.doesNotMatch(source, /\/api\/skills/);
    assert.doesNotMatch(source, /\/api\/github/);
    assert.doesNotMatch(source, /\/api\/mcp/);
    assert.doesNotMatch(source, /\/api\/admin\/mcp/);
    assert.doesNotMatch(source, /\/api\/agent\/config/);
    assert.doesNotMatch(source, /\/api\/notifications\/admin/);
  }

  assert.match(phase1ProjectionApi, /\/api\/ai\/admin\/skills/);
  assert.match(phase1ProjectionApi, /\/api\/ai\/admin\/tool-policies/);
  assert.match(phase1ProjectionApi, /\/api\/ai\/agent-apps/);
  assert.match(phase1ProjectionApi, /\/api\/agents/);
  assert.match(phase1ProjectionApi, /modelPublicApi\.listAvailable/);
  assert.match(phase1ProjectionApi, /\/api\/notifications\/active/);
});

test("Phase 1 skills governance does not auto-run admin skill sync on projection load", () => {
  const phase1ProjectionApi = readSource(
    "../services/api/phase1Projection.ts",
  );
  const phase1Panels = readSource(
    "../components/panels/phase1ProjectionPanels.tsx",
  );

  const projectionLoader =
    /async listSkillGovernanceProjection\(\): Promise<SkillGovernanceProjection> \{[\s\S]*?\n\s{2}\},/.exec(
      phase1ProjectionApi,
    )?.[0] ?? "";
  const skillsPanelLoader =
    /const loadSkillsProjection = useCallback\([\s\S]*?\n\s{2}\);/.exec(
      phase1Panels,
    )?.[0] ?? "";

  assert.match(projectionLoader, /listPublicAgents/);
  assert.match(projectionLoader, /listAgentApps/);
  assert.match(projectionLoader, /getAdminSkill/);
  assert.doesNotMatch(projectionLoader, /syncBuiltinSkills/);
  assert.match(skillsPanelLoader, /listSkillGovernanceProjection/);
  assert.doesNotMatch(skillsPanelLoader, /syncBuiltinSkills/);
  assert.match(phase1Panels, /handleSyncBuiltinSkills/);
  assert.match(phase1Panels, /onClick=\{handleSyncBuiltinSkills\}/);
});

test("Phase 1 tab content keeps unsupported surfaces behind the policy placeholder", () => {
  const tabContentSource = readSource(
    "../components/layout/AppContent/TabContent.tsx",
  );
  const placeholderSource = readSource(
    "../components/layout/AppContent/Phase2UnavailablePanel.tsx",
  );

  assert.match(tabContentSource, /getSurfacePolicy\(activeTab\)/);
  assert.match(tabContentSource, /phase2-unavailable/);
  assert.doesNotMatch(placeholderSource, /authFetch/);
  assert.doesNotMatch(placeholderSource, /API_BASE/);
  assert.doesNotMatch(placeholderSource, /services\/api/);
  assert.doesNotMatch(placeholderSource, /useEffect/);
});

test("Phase 1 routes and menus consume the shared surface policy", () => {
  const appSource = readSource("../App.tsx");
  const headerSource = readSource("../components/layout/AppContent/Header.tsx");
  const sidebarSource = readSource("../components/panels/SessionSidebar.tsx");
  const sessionListContentSource = readSource(
    "../components/panels/SidebarParts/SessionListContent.tsx",
  );
  const userMenuSource = readSource("../components/layout/UserMenu.tsx");

  assert.match(appSource, /getRoutePermissions/);
  assert.match(appSource, /routePermissions\("models"\)/);
  assert.doesNotMatch(headerSource, /user\?\.permissions\?\.includes/);
  assert.doesNotMatch(headerSource, /Permission\.SESSION_SHARE/);
  assert.doesNotMatch(headerSource, /ShareDialog/);
  assert.doesNotMatch(
    appSource,
    /path="\/models"[\s\S]*?<ProtectedRoute>[\s\S]*?<ModelsPage/s,
  );
  assert.match(sidebarSource, /canShowSurfaceInNavigation/);
  assert.match(userMenuSource, /canShowSurfaceInNavigation/);
  assert.doesNotMatch(sidebarSource, /canManageUsers/);
  assert.doesNotMatch(userMenuSource, /canReadMCP/);
  assert.doesNotMatch(sessionListContentSource, /navigate\("\/persona"\)/);
  assert.doesNotMatch(sessionListContentSource, /navigate\("\/files"\)/);
  assert.doesNotMatch(sessionListContentSource, /personaPresets\.title/);
  assert.doesNotMatch(sessionListContentSource, /fileLibrary\.title/);
});

test("Phase 1 sidebar keeps project and session organization write actions hidden", () => {
  const sessionListContentSource = readSource(
    "../components/panels/SidebarParts/SessionListContent.tsx",
  );
  const sessionSidebarSource = readSource(
    "../components/panels/SessionSidebar.tsx",
  );
  const projectItemSource = readSource("../components/sidebar/ProjectItem.tsx");
  const sessionItemSource = readSource("../components/sidebar/SessionItem.tsx");

  assert.doesNotMatch(sessionListContentSource, /FolderPlus/);
  assert.doesNotMatch(sessionListContentSource, /onOpenNewProjectModal/);
  assert.doesNotMatch(sessionListContentSource, /onNewSessionInProject/);
  assert.doesNotMatch(sessionListContentSource, /onMoveToProject/);
  assert.doesNotMatch(sessionSidebarSource, /NewProjectModal/);
  assert.doesNotMatch(sessionSidebarSource, /handleDeleteProject/);
  assert.doesNotMatch(sessionSidebarSource, /handleMoveSession/);
  assert.doesNotMatch(projectItemSource, /projectApi/);
  assert.doesNotMatch(projectItemSource, /ProjectMenu/);
  assert.doesNotMatch(projectItemSource, /data-project-drop/);
  assert.doesNotMatch(sessionItemSource, /SessionMenu/);
  assert.doesNotMatch(sessionItemSource, /draggable/);
});

test("Phase 1 sidebar does not render unsupported project filtered session lists", () => {
  const sessionListContentSource = readSource(
    "../components/panels/SidebarParts/SessionListContent.tsx",
  );
  const sessionSidebarSource = readSource(
    "../components/panels/SessionSidebar.tsx",
  );

  assert.doesNotMatch(sessionListContentSource, /ProjectItem/);
  assert.doesNotMatch(sessionListContentSource, /favoritesProject/);
  assert.doesNotMatch(sessionListContentSource, /isSidebarProject/);
  assert.doesNotMatch(sessionSidebarSource, /useProjectManager/);
  assert.doesNotMatch(sessionSidebarSource, /loadProjects/);
  assert.doesNotMatch(sessionSidebarSource, /useProjectSessionList/);
  assert.doesNotMatch(sessionSidebarSource, /project_id=none/);
  assert.match(sessionSidebarSource, /useSessionList/);
});

test("Phase 1 sidebar header does not expose legacy LambChat brand authority", () => {
  const constantsSource = readSource("../constants/index.ts");
  const sessionListContentSource = readSource(
    "../components/panels/SidebarParts/SessionListContent.tsx",
  );

  assert.doesNotMatch(constantsSource, /LambChat/);
  assert.doesNotMatch(constantsSource, /Yanyutin753/);
  assert.doesNotMatch(sessionListContentSource, /GITHUB_URL/);
  assert.doesNotMatch(sessionListContentSource, /href=\{GITHUB_URL\}/);
});

test("public branded shells do not depend on upstream GitHub authority", () => {
  const sources = [
    "../components/auth/AuthPage.tsx",
    "../components/landing/components/CTASection.tsx",
    "../components/landing/components/Footer.tsx",
    "../components/landing/components/HeroSection.tsx",
  ].map(readSource);

  for (const source of sources) {
    assert.doesNotMatch(source, /GITHUB_URL/);
    assert.doesNotMatch(source, /viewOnGitHub/);
    assert.doesNotMatch(source, /Yanyutin753/);
  }
});

test("Memory panel admin projection uses effective permissions instead of role names", () => {
  const memoryPanelSource = readSource(
    "../components/panels/MemoryPanel/index.tsx",
  );

  assert.doesNotMatch(memoryPanelSource, /MEMORY_ADMIN_ROLES/);
  assert.doesNotMatch(memoryPanelSource, /roleCanUseAdminMemory/);
  assert.doesNotMatch(memoryPanelSource, /user\?\.roles/);
  assert.match(memoryPanelSource, /Permission\.ADMIN_STATUS/);
  assert.match(memoryPanelSource, /Permission\.SETTINGS_MANAGE/);
});
