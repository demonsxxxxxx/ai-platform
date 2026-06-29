# LibreChat UI Source Pin

This document records the pinned LibreChat frontend source used as the UI
upstream for ai-platform chat shell convergence.

## Upstream

| Field | Value |
| --- | --- |
| Repository | `https://github.com/danny-avila/LibreChat` |
| Commit | `9e74cc0e57b395926122bd4062c1fcedc48ed465` |
| License | MIT |
| Local UI module | `frontend/web/src/librechat-ui/` |

## Allowed Intake

The local module may track pure UI structure, geometry, tokens, and interaction
affordances from the pinned commit:

- chat shell layout;
- sidebar and session-list density;
- composer surface structure;
- selector, command-menu, and chip affordances;
- artifact and right-panel tab patterns;
- loading, empty, unavailable, forbidden, degraded, and ready visual states.

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

This source pin and local module prove frontend UI-upstream traceability only.
They do not close backend RBAC, MCP governance, marketplace write contracts,
department skill policy, approval flows, 211 deployment, or issue closure gates.
