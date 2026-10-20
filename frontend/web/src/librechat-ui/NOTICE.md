# LibreChat UI Reference Notice

ai-platform uses LibreChat as a pinned UI reference for shell geometry, sidebar
density, selector affordances, Agent Builder forms, tool pickers, Marketplace
cards, and right-panel interaction patterns. Composer, command menu and context
chips now use assistant-ui Elements under `components/chat/assistant-ui/elements`.
This is a reference-derived port, not a vendored copy of LibreChat directories.

- Upstream repository: https://github.com/danny-avila/LibreChat
- Pinned reference commit: `21dc4a2ef490b86510e4b410fe8f78d52c1d9629`
- License: MIT, Copyright (c) 2026 LibreChat
- Local module: `frontend/web/src/librechat-ui/`

Only reference-derived UI structure and styling may live in this module. Data
fetching, auth/session handling, RBAC, MCP authorization, persistence,
provider configuration, secrets, and backend event contracts remain
ai-platform-owned.
