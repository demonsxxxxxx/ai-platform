export const LIBRECHAT_UI_SOURCE = {
  repository: "https://github.com/danny-avila/LibreChat",
  commit: "21dc4a2ef490b86510e4b410fe8f78d52c1d9629",
  license: "MIT",
  copyright: "Copyright (c) 2026 LibreChat",
  integrationMode: "reference-derived",
  sourcePaths: [
    "client/src/components/UnifiedSidebar/UnifiedSidebar.tsx",
    "client/src/components/UnifiedSidebar/Sidebar.tsx",
    "client/src/components/UnifiedSidebar/ExpandedPanel.tsx",
    "client/src/components/Chat/Input/ChatForm.tsx",
    "client/src/components/SidePanel/Nav.tsx",
    "client/src/components/Artifacts/*",
    "client/src/components/SidePanel/Agents/AgentPanel.tsx",
    "client/src/components/SidePanel/Agents/AgentConfig.tsx",
    "client/src/components/SidePanel/Agents/Instructions.tsx",
    "client/src/components/SidePanel/Agents/ModelPanel.tsx",
    "client/src/components/SidePanel/Agents/AgentFooter.tsx",
    "client/src/components/SidePanel/Agents/Tools/ToolsSection.tsx",
    "client/src/components/SidePanel/Agents/Tools/SkillsDialog.tsx",
    "client/src/components/SidePanel/Agents/Tools/ToolsMarketplaceDialog.tsx",
    "client/src/components/SidePanel/Agents/Tools/ToolRow.tsx",
    "client/src/components/SidePanel/Agents/Tools/ItemDialog/sections/McpSection.tsx",
    "client/src/components/SidePanel/Agents/Tools/ItemDialog/sections/SkillSection.tsx",
    "client/src/components/Agents/AgentCard.tsx",
    "client/src/components/Agents/AgentGrid.tsx",
    "client/src/components/Agents/AgentDetail.tsx",
  ],
  referenceScope: [
    "reference-derived chat shell geometry and composer affordances",
    "reference-derived Agent Builder form, tool picker, and marketplace interaction patterns",
    "reference-derived Agent card, grid, and detail geometry",
  ],
  forbiddenScope: [
    "LibreChat API hooks",
    "LibreChat auth/session/RBAC decisions",
    "Mongo/message schema assumptions",
    "provider endpoint or secret configuration",
    "RAG/file-store permission logic",
  ],
} as const;

export const LIBRECHAT_UI_REFERENCE_NOTICE =
  "ai-platform uses a reference-derived port of the pinned LibreChat frontend paths; data, permissions, events, sessions, and persistence remain ai-platform-owned.";
