# LibreChat Reference-Derived UI Source Pin

This document records pinned LibreChat frontend references used by ai-platform
UI ports. It is provenance evidence, not a claim that complete upstream
directories are vendored into this repository.

## Upstream

| Field | Value |
| --- | --- |
| Repository | `https://github.com/danny-avila/LibreChat` |
| Commit | `21dc4a2ef490b86510e4b410fe8f78d52c1d9629` |
| License | MIT |
| Copyright | Copyright (c) 2026 LibreChat |
| Integration mode | `reference-derived` |
| Local UI module | `frontend/web/src/librechat-ui/` |

## Allowed Intake

The local module may reference pure UI structure, geometry, tokens, and
interaction affordances from the pinned commit:

- chat shell layout;
- sidebar and session-list density;
- composer surface structure;
- selector, command-menu, and chip affordances;
- artifact and right-panel tab patterns;
- loading, empty, unavailable, forbidden, degraded, and ready visual states;
- Agent Builder form, tool picker, and Marketplace card interaction patterns.

## Agent Builder Reference Scope

The Agent Builder port is reference-derived from these exact source paths. No
complete upstream `Agents` or `Tools` directory is vendored.

- `client/src/components/SidePanel/Agents/AgentPanel.tsx`
- `client/src/components/SidePanel/Agents/AgentConfig.tsx`
- `client/src/components/SidePanel/Agents/Instructions.tsx`
- `client/src/components/SidePanel/Agents/ModelPanel.tsx`
- `client/src/components/SidePanel/Agents/AgentFooter.tsx`
- `client/src/components/SidePanel/Agents/Tools/ToolsSection.tsx`
- `client/src/components/SidePanel/Agents/Tools/SkillsDialog.tsx`
- `client/src/components/SidePanel/Agents/Tools/ToolsMarketplaceDialog.tsx`
- `client/src/components/SidePanel/Agents/Tools/ToolRow.tsx`
- `client/src/components/SidePanel/Agents/Tools/ItemDialog/sections/McpSection.tsx`
- `client/src/components/SidePanel/Agents/Tools/ItemDialog/sections/SkillSection.tsx`
- `client/src/components/Agents/AgentCard.tsx`
- `client/src/components/Agents/AgentGrid.tsx`
- `client/src/components/Agents/AgentDetail.tsx`

## Forbidden Intake

The local module must not import or reimplement LibreChat backend authority:

- LibreChat API hooks or data-provider contracts;
- LibreChat auth, session, RBAC, or permission decisions;
- Mongo/message schema assumptions;
- provider endpoint or secret configuration;
- RAG/file-store permission logic.

All data, permissions, events, persistence, and backend projections remain
ai-platform-owned and must cross the `ChatWorkbenchAdapter` seam instead.

## Local Mapping

| ai-platform module | Role |
| --- | --- |
| `frontend/web/src/librechat-ui/source.ts` | Upstream commit, license, allowed scope, forbidden scope |
| `frontend/web/src/librechat-ui/adapter.ts` | ai-platform-owned adapter interface consumed by UI |
| `frontend/web/src/librechat-ui/surface.ts` | shell geometry and surface tokens |
| `frontend/web/src/librechat-ui/Shell.tsx` | chat shell layout and right-context toggle |
| `frontend/web/src/librechat-ui/Rail.tsx` | sidebar rail primitive |
| `frontend/web/src/librechat-ui/Panel.tsx` | expanded sidebar section primitive |
| `frontend/web/src/librechat-ui/SidePanel.tsx` | right context/artifact/run/permission panel |

Legacy `frontend/web/src/components/librechatShell/*` files are compatibility
re-exports only. Active workbench code must consume `frontend/web/src/librechat-ui/*`.

## Status Boundary

This reference-derived source pin and local module prove frontend UI-upstream
traceability only.
They do not close backend RBAC, MCP governance, marketplace write contracts,
department skill policy, approval flows, 211 deployment, or issue closure gates.
