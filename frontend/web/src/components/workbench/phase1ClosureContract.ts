export const PHASE1_CLOSURE_ROUTES = [
  "/apps",
  "/chat",
  "/skills",
  "/marketplace",
  "/mcp",
  "/channels",
  "/shared/:shareId",
] as const;

export const PHASE1_COMPOSER_COMMANDS = [
  "/skill",
  "$",
  "/mcp",
  "/agent",
  "/model",
  "/file",
  "/context",
] as const;

export const PHASE1_FAIL_CLOSED_SURFACES = [
  "department-skill-policy",
  "mcp-lifecycle",
  "share-acl-create",
  "channel-import-projection",
  "context-selector",
] as const;

export const PHASE1_CLOSURE_SCREENSHOTS = [
  "login.png",
  "apps.png",
  "chat-empty.png",
  "chat-slash-menu.png",
  "chat-dollar-skills.png",
  "chat-selected-skill-chip.png",
  "chat-model-selector.png",
  "chat-file-chip.png",
  "skills.png",
  "marketplace.png",
  "mcp.png",
  "channels.png",
  "shared-denied.png",
  "ordinary-admin-denied.png",
  "admin-governance.png",
] as const;

export const PHASE1_FORBIDDEN_VISUAL_MARKERS = [
  "LambChat",
  "lambchat.com",
  "gradient-orb",
  "hero-card",
  "nested-card",
] as const;
