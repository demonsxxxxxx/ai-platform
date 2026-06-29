export const LIBRECHAT_UI_SOURCE = {
  repository: "https://github.com/danny-avila/LibreChat",
  commit: "9e74cc0e57b395926122bd4062c1fcedc48ed465",
  license: "MIT",
  sourcePaths: [
    "client/src/components/UnifiedSidebar/UnifiedSidebar.tsx",
    "client/src/components/UnifiedSidebar/Sidebar.tsx",
    "client/src/components/UnifiedSidebar/ExpandedPanel.tsx",
    "client/src/components/Chat/Input/ChatForm.tsx",
    "client/src/components/SidePanel/Nav.tsx",
    "client/src/components/Artifacts/*",
  ],
  vendoredScope: [
    "chat shell layout",
    "sidebar/session list geometry",
    "composer region styling",
    "selector/chip surface contracts",
    "artifact/right panel tabs",
    "loading/empty/error state affordances",
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
  "ai-platform visually tracks the pinned LibreChat frontend commit while data, permissions, events, and persistence remain ai-platform-owned.";
