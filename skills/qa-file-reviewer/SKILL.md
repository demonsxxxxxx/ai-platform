---
name: qa-file-reviewer
description: Use when the user asks to review, deep-review, platform-review, QA check, compliance check, bilingual check, or add comments to a Word .docx document. Produces a reviewed Word document with visible comments and a concise findings summary.
---

# QA File Reviewer

Use this Skill when the user wants a Word `.docx` reviewed, checked, or returned with visible comments.

## Required References

Before starting any request that means `审核` / `深度审核` / `平台全权审核`, read these files first:

- `references/workflow_guide.md`
- `references/branch_agents.md`
- `references/agent-output-acceptance-examples.md`
- `references/prompt_templates.md`
- `references/review-schema.md`

Do not skip these reads for the default path.

## Request Routing

- Requests that say `审核`, `深度审核`, `平台全权审核`, `QA check`, `compliance check`, or `bilingual check` use the full semantic review path by default.
- The default path is Claude Agent SDK `Agent` multi-review on top of deterministic context preparation.
- Do not keep fast deterministic review as the default path.
- Run deterministic preparation first, then dispatch reviewer agents, then merge them, then generate the final commented Word output.
- Use deterministic-only review only when the user explicitly requests a fast deterministic review.
- Downgrade from the default path only when the Agent tool is unavailable in the current runtime, or an explicit Agent invocation proves unavailable for this run. When that happens, record a downgrade reason before continuing.

## Default Review Path

This is the default path for review work. The default path is not the deterministic-only runner.

1. Prepare deterministic context in the run workspace:

```bash
mkdir -p output
python .claude/skills/qa-file-reviewer/scripts/run_qa_review.py "<input.docx>" output --no-comments --keep-json-artifacts --original-filename "<input.docx>"
```

2. Export compact deterministic context for reviewers:

```bash
python .claude/skills/qa-file-reviewer/scripts/export_agent_review_context.py output/document_map.json output/agent_review_context.txt
```

3. Record the routing decision before dispatch:

```bash
python .claude/skills/qa-file-reviewer/scripts/record_agent_review_decision.py output/agent_routing_record.json --mode claude_agent_multi_review --requested-review deep_review --agent-tool-available true --final-reviewer-completed false
```

4. Use Claude Agent SDK `Agent` or subagent tooling to dispatch at least these reviewers. Each reviewer must return a JSON shard that follows `references/prompt_templates.md` and `references/review-schema.md`.

- `qa-structure-reviewer` -> `output/structure_agent_review.json`
- `qa-zh-language-reviewer` -> `output/zh_agent_review.json`
- `qa-en-language-reviewer` -> `output/en_agent_review.json`
- `qa-bilingual-reviewer` -> `output/bilingual_agent_review.json`
- `qa-data-consistency-reviewer` -> `output/data_consistency_agent_review.json`
- `qa-risk-classifier` -> `output/risk_classifier_agent_review.json`

5. After all required reviewer shards are present, run the final merge reviewer. The final merge reviewer consumes the deterministic context plus all shard JSON files, removes duplicates, applies risk-classifier vetoes, and emits one merged semantic review file:

- `qa-final-merge-reviewer` -> `output/final_merge_agent_review.json`

After the final merge reviewer completes, update the routing record with every completed reviewer and `--final-reviewer-completed true`:

```bash
python .claude/skills/qa-file-reviewer/scripts/record_agent_review_decision.py output/agent_routing_record.json --mode claude_agent_multi_review --requested-review deep_review --agent-tool-available true --completed-reviewer qa-structure-reviewer --completed-reviewer qa-zh-language-reviewer --completed-reviewer qa-en-language-reviewer --completed-reviewer qa-bilingual-reviewer --completed-reviewer qa-data-consistency-reviewer --completed-reviewer qa-risk-classifier --final-reviewer-completed true
```

6. Merge the final semantic review back into the deterministic runner and generate the user-visible Word output:

```bash
python .claude/skills/qa-file-reviewer/scripts/run_qa_review.py "<input.docx>" output --with-comments --keep-json-artifacts --original-filename "<input.docx>" --agent-review-json output/final_merge_agent_review.json
```

7. In the final response, summarize generated files, whether semantic multi-review ran, and whether any downgrade happened.

## Downgrade Rules

- Only when the user explicitly requests a fast deterministic review may you skip reviewer agents by choice.
- Only when the Agent tool is unavailable may the default path be automatically downgraded.
- Missing reviewer shards without a recorded downgrade reason is incomplete work, not a successful fast path.
- When downgraded, record the reason in `output/agent_routing_record.json`:

```bash
python .claude/skills/qa-file-reviewer/scripts/record_agent_review_decision.py output/agent_routing_record.json --mode fast_deterministic_downgrade --requested-review deep_review --agent-tool-available false --reason "Agent tool unavailable in current runtime."
```

- After recording the downgrade reason, run the deterministic reviewer directly:

```bash
python .claude/skills/qa-file-reviewer/scripts/run_qa_review.py "<input.docx>" output --with-comments --keep-json-artifacts --original-filename "<input.docx>"
```

- Mention the downgrade reason in logs and in the user-facing summary.

## Output Contract

Save user-facing files under `output/`.

Expected outputs:

- `*_reviewed.docx`: reviewed Word document with comments, when the quality gate allows comment generation.
- `*_审核详细报告.txt`: review report, especially when the quality gate blocks Word comment generation.
- `agent_routing_record.json`: routing and downgrade record for the current run.
- `final_merge_agent_review.json`: merged semantic reviewer output for the default path.
- JSON diagnostics and audit files: `document_map.json`, `comment_plan.json`, `review_result.json`, `validation_report.json`, and reviewer shard JSON when kept for audit.

In the final response, summarize what was generated and mention if Word comment generation was blocked by validation or if semantic review was downgraded.

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
- Do not claim a deep/platform review succeeded if the required reviewer shards or the final merge reviewer are missing.
- Keep deterministic preparation and semantic reviewer dispatch separate: `run_qa_review.py` stays deterministic and never calls an LLM itself.
