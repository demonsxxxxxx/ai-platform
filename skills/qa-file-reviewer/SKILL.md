---
name: qa-file-reviewer
description: Use when the user asks to review, QA check, compliance check, bilingual check, or add comments to a Word .docx document. Produces a reviewed Word document with visible comments and a concise findings summary.
---

# QA File Reviewer

Use this Skill when the user wants a Word `.docx` reviewed, checked, or returned with visible comments.

## Fast Path

Do not read all reference files before starting. For a normal review request, run the deterministic reviewer directly from the run workspace:

```bash
mkdir -p output
python .claude/skills/qa-file-reviewer/scripts/run_qa_review.py "<input.docx>" output --with-comments --original-filename "<input.docx>"
```

Pick `<input.docx>` from the files in the current workspace. Use the original uploaded filename as `--original-filename`.

## Output Contract

Save user-facing files under `output/`.

Expected outputs:

- `*_reviewed.docx`: reviewed Word document with comments, when the quality gate allows comment generation.
- `*_审核详细报告.txt`: review report, especially when the quality gate blocks Word comment generation.
- JSON files: diagnostics and audit data.

In the final response, summarize what was generated and mention if Word comment generation was blocked by validation.

## Dependency

This Skill depends on the platform-staged `minimax-docx` Skill. The platform should stage both:

```text
.claude/skills/qa-file-reviewer/
.claude/skills/minimax-docx/
```

If `.claude/skills/minimax-docx/docx_engine.py` is missing, report a platform staging error instead of trying to find files outside the workspace.

## Boundaries

- Work only inside the current run workspace.
- Do not access host paths outside the workspace.
- Do not call external QA POC services such as `8014`.
- Do not expose internal workspace paths in the final user-facing answer.
- Read `references/workflow_guide.md` only when the fast path fails or the user asks for a deeper explanation.
