# LibreChat UI Upstream Notice

ai-platform tracks the LibreChat frontend as a UI upstream for shell geometry,
sidebar density, composer surface structure, selector/chip affordances, and
right-panel interaction patterns.

- Upstream repository: https://github.com/danny-avila/LibreChat
- Pinned commit: `9e74cc0e57b395926122bd4062c1fcedc48ed465`
- License: MIT
- Local module: `frontend/web/src/librechat-ui/`

Only pure UI structure and styling may live in this module. Data fetching,
auth/session handling, RBAC, MCP authorization, persistence, provider
configuration, secrets, and backend event contracts remain ai-platform-owned.
