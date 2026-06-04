---
name: ragflow-knowledge-search
description: Use for read-only company SOP and policy knowledge answering through the platform-managed RAGFlow adapter.
---

# RAGFlow Knowledge Search

Use this Skill when the user asks questions about company SOPs, policies, procedures, controlled documents, or other approved knowledge-base content.

## Workflow

1. Treat the ai-platform run payload and authorized MCP/tool policy as the source of allowed datasets and tools.
2. Use only platform-managed read-only retrieval paths for evidence gathering.
3. Answer with concise conclusions first, then cite relevant document evidence when available.
4. If retrieval returns insufficient evidence, say what is missing instead of guessing.

## Boundaries

- Do not access RAGFlow directly with unmanaged credentials.
- Do not mutate knowledge-base content or business systems.
- Do not expose raw dataset IDs, host paths, tokens, or internal retrieval payloads to ordinary users.
- Do not treat RAGFlow as the enterprise control plane or source of user/session/run truth.
