---
name: baoyu-translate
description: Use when the user asks to translate, localize, polish, or bilingual-review text or documents.
---

# Baoyu Translate

Use this Skill for translation and bilingual polishing tasks.

## Workflow

1. Identify the source language, target language, and requested quality level from the user message.
2. Preserve domain terminology, document structure, and filenames when files are present.
3. For document outputs, save generated user-facing files under `output/`.

## Boundaries

- Work only inside the current run workspace.
- Do not access host paths outside the workspace.
- Do not call unmanaged external services.
- Prefer concise terminology notes when translation choices are ambiguous.
