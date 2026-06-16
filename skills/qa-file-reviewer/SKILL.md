---
name: qa-file-reviewer
description: Use when a Word document (.docx) needs company-standard QA review, semantic or bilingual checking, and a commented Word deliverable.
---

# DOCX QA Reviewer

## Contract

Input is one Word `.docx` file. The default user deliverable is one commented Word file:

- runtime/file key: `reviewed_docx`
- label: `批注 Word`
- filename pattern: `*_reviewed.docx`

Text reports and JSON files are internal diagnostics unless the caller explicitly asks for them. User-visible review findings must be represented in the commented Word file. New Word comment labels and reviewer-facing `issue` / `suggestion` text must be Chinese; exact source text in `原文` may remain in its original language.

SDK/sub-agent runtime failures, missing `*_agent_review.json` files, JSON parse errors, and schema errors are internal diagnostics. They must not be converted into Word comments or user-facing "human review" findings. Only source-backed document findings may enter the commented Word deliverable.

## Execution Model

Use the deterministic runner for parsing, hard rules, gate validation, comment insertion, and DOCX audit:

```bash
python scripts/run_qa_review.py <input.docx> <output_dir> --with-comments --original-filename "<original-name>"
```

When an outer agent or Claude Agent SDK executor performs semantic review, use the two-pass contract:

```bash
python scripts/run_qa_review.py <input.docx> <output_dir> --no-comments --keep-json-artifacts --original-filename "<original-name>"
python scripts/export_agent_review_context.py <output_dir>/document_map.json <output_dir>/agent_review_context.txt --context-version v2
python scripts/validate_agent_context_package.py <output_dir>/agent_context_manifest.json
# outer agent writes one or more *_agent_review.json files
python scripts/run_qa_review.py <input.docx> <output_dir> --with-comments --keep-json-artifacts --original-filename "<original-name>" --agent-review-json <agent-findings.json>
```

`scripts/run_qa_review.py` must not call an LLM or read model settings. It only validates and merges external agent findings supplied with `--agent-review-json`.

## Responsibility Split

| Layer | Responsibility |
| --- | --- |
| `run_qa_review.py` | DOCX parsing, `document_map.json`, deterministic checks, merge/dedupe, score, gate, comment plan, final `reviewed_docx`. |
| `qa_comment_adjudicator.py` | Pre-comment-plan adjudication: merge duplicate semantic findings, refine broad or synthetic anchors, and keep distinct same-paragraph issues on separate quoted source spans where possible. |
| `export_agent_review_context.py` | Locator-first paragraph/table context package for semantic agents. |
| `validate_pipeline_gate.py` | Hard gate before final Word output. |
| `add_word_comments_v3.py` | Preserve existing human comments, strip prior automated review comments, and insert new Word comments. |
| Calling agent / Claude Agent SDK | Optional Chinese, English, bilingual, and semantic review; writes structured JSON findings. |

Do not reintroduce embedded/default LLM review into `run_qa_review.py`. Model-driven review belongs outside the deterministic runner.

## Deterministic Branches

The runner always executes these required branches:

- `format`: page setup, footer, body/table font sizes, and table structure diagnostics. Format findings without stable insert anchors stay internal unless explicitly emitted as a `global_summary`.
- `project_number`: current project number and project-number residue, with reasonable reference-context exclusions.
- `content_consistency`: deterministic structural consistency checks such as broken references, group-number mismatches, third-language residue, and table-cell method abbreviation mismatches.
- `content_consistency` also includes weak-model safety checks for source-backed table-cell bilingual data mismatches, such as the same temperature condition using different `±` tolerances in Chinese and English.
- `content_consistency` includes narrow weak-model fallback checks only when the source document locally proves the defect, such as passive `were filter` -> `were filtered`, recurring release-data footnote grammar, repeated Chinese punctuation such as `；；` or `；。`, adjacent Chinese/English time mismatches in the same table cell, and document-dominant chemical formula confusables such as `I/l/1` swaps.
- Removed sample-shape deterministic fallbacks must not be reintroduced. Bilingual method, condition, object, protocol-title, or purpose-section mismatches should come from agent semantic findings or generalized source-local mechanisms, then pass evidence/anchor/actionability gates.
- `content_consistency` includes source-local formula checks for meaning-changing symbol loss, such as a Chinese absolute-value formula using `|A-B|` while the paired English formula drops the absolute-value bars.

These branches must appear in `branch_execution_manifest`; missing or failed required branches block final output.

## Agent Semantic Findings

Outer agents may add semantic findings with JSON files passed through `--agent-review-json`. Each file must be:

```json
{
  "agent_role": "semantic-agent",
  "issues": [
    {
      "category": "zh_language|en_language|bilingual_consistency|semantic_consistency",
      "severity": "关键|主要|次要",
      "unit_id": "p-00098",
      "anchor_quote": "exact source span inside the named unit",
      "paragraph_index": "legacy paragraph logical index, optional for compatibility",
      "original": "exact source span or local source sentence",
      "issue": "problem statement",
      "suggestion": "recommended correction",
      "evidence": "source-backed evidence",
      "evidence_unit_ids": ["p-00098"],
      "confidence": "high|medium|low"
    }
  ]
}
```

Use `agent_context_manifest.json` and every listed `agent_review_context.part-*.txt` or `agent_review_context.txt` as the main review material. `review_units.jsonl`, `review_blocks.jsonl`, and `bilingual_pairs.jsonl` are machine-readable indexes for verifying `unit_id`, neighboring units, same-cell units, `pair_id`, or adjacent context; they are not the primary material that agents should review line by line.

Agent review is full-flow QA. Bilingual comparison is one review domain only. The main review material is the context TXT listed by `agent_context_manifest.json`; sidecar JSONL files are for locator verification and machine checks.

Agent review is a full-flow review surface, not only bilingual comparison. Agents should cover Chinese, English, bilingual consistency, semantic contradictions, table data, formulas, references, project numbers, sample IDs, batch IDs, and section titles when the document itself proves the issue.

For non-global agent findings, `unit_id + anchor_quote` is the required primary locator contract. Findings without `unit_id` are filtered from user-visible Word output. `paragraph_index`, `P...`, `XML:...`, and `T...R...C...` are internal compatibility/readability hints only and must not replace `unit_id` as the primary locator. Do not expose those internal locators in user-visible `issue`, `evidence`, or `suggestion` fields.

Problem locators and evidence locators are separate. `unit_id + anchor_quote` identifies the source phrase where the Word comment can be inserted. `evidence_unit_ids`, neighboring units, same-cell units, and bilingual pairs may support the finding, but they are not the insertion target unless they are also the unit containing the editable wrong phrase.

Each issue should also include `coverage_domain`, `review_basis`, `requires_external_evidence`, `external_evidence_type`, and `comment_intent` when the executor can provide them. Findings that require external records, protocols, sample information, or LIMS data must be marked as check requests, not confirmed errors. When evidence depends on other units, include `evidence_unit_ids`; when a bilingual pair is useful context, `pair_id` or `problem_unit_id` may be included as auxiliary fields.

Only findings that pass the user-visible quality gate may become Word comments. Except for explicitly marked `global_summary` findings, every user-visible finding must have source-backed evidence, a stable exact anchor, and an actionable concise suggestion. If `anchor_quote` is missing or cannot be found inside the named `unit_id`, filter the finding and keep it internal; do not substitute `original`, `evidence`, or broad document search before writing a Word comment. Low-confidence, external-evidence-dependent, unstable-anchor, vague-suggestion, or schema-repaired but weak findings are filtered from the Word deliverable by default and retained only in internal diagnostics or skip counts.

Do not pass missing agent JSON files to the final merge command. If an expected reviewer produced no JSON, omit that `--agent-review-json` argument and keep the absence in logs/metadata only.

## Claude Agent SDK Pattern

If Claude Agent SDK is the executor, it may define or dispatch separate reviewers for:

- Structure and format: `structure`
- Chinese language: `zh_language`
- English language: `en_language`
- Bilingual consistency: `bilingual_consistency`
- Data/content consistency: `semantic_consistency`
- Risk classification: confirmed change vs. user check vs. external evidence required

This is an execution optimization, not a mandatory skill requirement. If the Agent tool is unavailable, the calling agent may perform the same review passes itself and still use `--agent-review-json`.

## Quality Rules

- Report only source-backed issues. Do not create style-only rewrites.
- Do not report pure wording preferences, date-format preferences, or harmless bilingual order differences unless company rules require them.
- Keep objective rule-based findings when they are backed by company standards or exact source text, such as confirmed font-size mismatches and repeated/leading whitespace with a stable anchor. These are not the same as style preferences.
- Filter Chinese sentence-flow polish such as "衔接不自然", "句子结构堆叠", or "语意不清" when it does not change data, method, condition, object, or compliance meaning.
- Treat brand, vendor, product-name, equipment-model, abbreviation full-name, or official spelling claims as external unless backed by a runner-verified approved terminology source or document-internal dominant spelling; model memory and agent-written claims such as "approved terminology says..." are not enough for a Word-visible correction.
- Treat packaging-material, reagent, consumable, and other official/material-name rewrites as external unless backed by a runner-verified approved terminology source, controlled glossary, or explicit company-template rule. Do not surface model-only rewrites such as "统一表述为..." as Word comments.
- Downgrade isolated punctuation, spacing, and sentence-boundary issues to minor severity unless they change data, method, condition, DS/DP meaning, or another quality-critical fact.
- Filter low-value punctuation-style findings such as mixed comma/enumeration punctuation when they do not change data, method, condition, or meaning. Keep objective punctuation errors such as duplicated semicolons or missing sentence boundaries.
- Filter generic bilingual missing-counterpart claims such as "中文/英文缺少对应内容" when they are inferred only from a nearby single-language paragraph or from a half-bilingual document shape. Only report missing translation/counterpart issues when an approved template or the same local bilingual structure proves that the counterpart is required and the exact insertion target is stable.
- Filter spacing consistency preferences, but do not filter exact repeated-space defects (`two or more consecutive spaces`, table-cell leading indentation, or duplicated blanks) when the source span is stable and the suggestion is a direct cleanup.
- Filter low-value table-of-contents or title spacing preferences unless an approved company template rule explicitly requires the change.
- Filter cross-cell percentage-symbol, spacing, or heading-numbering consistency claims unless the same source cell/heading proves a concrete typo or an approved company template rule explicitly requires the change.
- Filter cross-document/table format-unification claims such as mixed presentation variants unless the cited source span itself contains a concrete typo, missing character, missing space, data/method/condition contradiction, or approved template requirement.
- Filter minor English sentence-initial capitalization or punctuation polish unless it changes a data, method, condition, abbreviation, proper noun, or compliance meaning.
- Filter title-case or first-letter capitalization preferences for table headings unless an approved company template rule explicitly requires the change.
- If multiple reviewers report the same source span with the same correction target, keep one user-visible Word comment; do not make the user resolve duplicate restatements.
- If multiple reviewers report the same paragraph/table cell and same correction target with different anchors, merge them when they cite the same error term; keep the clearest source-backed comment.
- If the same recurring defect appears across many table rows or result paragraphs, aggregate it instead of inserting row-by-row comments, but only when the repeated defect has the same source-side error and correction target.
- If deterministic checks and agent review report the same location and same semantic defect with different anchors, still merge them. Same-cell temperature tolerance mismatches and repeated release-data note grammar are examples of mergeable source-local defects.
- Low-value format findings such as row-count-inferred repeated table headers or cosmetic table spacing/indentation remain internal diagnostics unless an approved template rule and stable insertion anchor make them Word-visible.
- Do not escalate repeated turbidity wording or `less urbidity` findings to critical severity unless they create an independently critical quality, safety, or release-impact contradiction; normally keep them at major or minor severity depending on data impact.
- If risk classification marks the same location and same semantic concern as `requires_external_evidence=true` or `comment_intent=request_check`, treat that as a veto for other agent restatements of the same concern; filter it from Word and keep only internal skip diagnostics. Exception: a high-confidence, exactly anchored, single-document internal contradiction with a direct correction target must not be hidden only because the risk classifier asked to verify source records.
- If a deterministic weak-model fallback and an agent report the same or highly overlapping source span at the same location, keep the deterministic issue and drop the duplicate agent restatement.
- If the same deterministic grammar problem appears in many repeated note lines, aggregate it into one representative comment with repeated locations in evidence instead of inserting many identical comments. If an agent reports the same recurring note grammar at another repeated location, still keep only the deterministic aggregate.
- Treat compact chemical formula case/confusable fixes as objective source-local issues only when the document itself proves a dominant spelling or formula form; do not rely on model memory or a one-off typo list.
- Treat unit-definition polish such as `Lux: lumen per square meter` -> a fuller explanatory definition as low-value style unless the document itself proves a data, unit, or meaning contradiction.
- Treat table cross-column claims such as one cell `N/A` versus neighboring cells `106%/109%/108%` as external or row-schema-dependent unless the same cell or adjacent bilingual pair proves the mismatch directly; filter them from Word rather than placing a comment on the numeric cell.
- Reject short generic anchors such as single function words or structural labels (`than`, `Table`, `data`, `条件下`, etc.) for model findings. If a numeric anchor such as `109` only matches inside a neighboring `109%` value, retry the true adjacent source cell or filter it; do not place a comment on the already-correct value.
- Visible anchors must be the actual source-side text to edit. Do not anchor a comment to an evidence word, a correct comparison phrase, a generic term, a section label, or the replacement target from the suggestion. If the actual erroneous phrase cannot be found, filter the finding instead of placing a misleading comment.
- If a short anchor such as `less` can be safely refined to a nearby exact source error phrase like `less urbidity standard solution 1`, anchor the full erroneous phrase; otherwise filter the short-anchor finding.
- Suggestions must be single-action and clear. Filter findings that ask the reviewer to choose between alternatives, such as `... or ...` / `either ... or ...`, unless an upstream approved template rule has already selected the exact replacement.
- Prefer precise in-place anchors. For v2 agent findings, the Word writer must use the precomputed `anchor_span` in the named `unit_id` and must not relocate by searching `original`, `evidence`, `issue`, or `suggestion`. Broad or conflicting agent anchors are filtered unless adjudication can refine them inside the same `unit_id` and original `anchor_quote`. Do not create document-end comments for ordinary property, section, footer, or anchor-failed findings; keep them internal unless explicitly marked `global_summary`.
- For exact source-local typo findings outside the v2 agent locator path, allow one conservative re-anchor when the cited location is wrong but the misspelled source phrase is unique in the document. Do not use this for v2 agent findings, external brand, vendor, product, or model-name spelling claims.
- Do not discard an otherwise anchored source-local typo only because the model explanation contains hedging language such as "appears to"; evaluate the exact source span and direct correction first. Keep filtering speculative semantic, data, protocol, or external-evidence claims.
- Treat table row/cell coordinates such as `T7R5C3` plus paragraph indexes such as `P571` as legacy compatibility hints when `document_map.json` contains the matching table cell. In context v2, they are not a substitute for `unit_id + anchor_quote`.
- Filter electronic SOP/form signature placeholder column complaints, such as a header containing `签字/日期` and `复核/日期` while data rows only contain business columns, unless a runner-verified approved template rule proves those columns must be filled in the electronic draft.
- Treat paired adjacent bilingual note lines as a stable local retry target only for legacy/non-v2 locator repair. In context v2, if the exact English source span is in the adjacent unit, the agent must name that adjacent `unit_id` and copy the English `anchor_quote`.
- Normalize equivalent source glyphs used in scientific units, especially `µm` and `μm`, before deciding an otherwise exact source span is unanchorable.
- Before writing `comment_plan.json`, run adjudication so final Word-visible issues are deduplicated and broad/synthetic anchors are refined against real `document_map.json` paragraph text.
- Word comments should use a compact Chinese visible template: `发现`, `原文`, `建议`. The `发现` and `建议` fields must be Chinese even when the source problem is English; keep exact English source text under `原文`. Keep each visible field short enough for Word review, especially long format examples and repeated section/table examples. Do not expose internal paragraph/map locators such as `P342`, internal labels such as `大模型全文审核`, `single_doc_internal`, JSON filenames, SDK errors, or stack traces in the comment text.
- Internal skipped findings should be categorized for audit, for example `filtered_external`, `filtered_low_confidence`, `filtered_low_value`, `filtered_quality_gate`, `anchor_failure_should_retry`, and `schema_invalid`. These categories are diagnostics only and must not become Word comments.
- Do not hardcode sample-document literals into source rules.
- Rules must come from company standards, approved configurable rule/term sources, or generalized structural patterns.
- `needs_user_check` is reserved for real document findings that are explicitly allowed to remain user-visible. Routine low-confidence, external-evidence-dependent, unstable-anchor, or vague findings must be filtered instead of increasing the user's manual review burden.

## Output Gate

Before final delivery:

- `validate_pipeline_gate.py` must pass.
- `comment_plan.json` must contain only user-visible Word comments that pass the quality gate: confirmed anchored findings plus explicitly marked `global_summary` findings. Internal diagnostics and filtered low-quality findings must not appear as Word comments.
- `review_result.json.summary.total_issues`, console totals, and the text report issue list must count only user-visible deliverable issues. Filtered or internal-only diagnostics may remain under `internal_diagnostics` / diagnostic counts, but must not appear as ordinary user problems.
- Existing human Word comments must be preserved. Prior automated review comments authored by the QA/file review system are stripped before inserting the current run's comments, so re-uploaded `*_reviewed.docx` files do not mix stale findings with fresh results.
- `docx_audit_report.json` must show the generated DOCX is structurally valid.
- `review_result.json.summary.quality_score` records deterministic score, grade, penalties, and whether semantic agent review was merged.

Read these references only when needed:

- `references/review-schema.md` for JSON fields and artifact contract.
- `references/workflow_guide.md` for detailed flow.
- `references/branch_agents.md` for branch boundaries.
- `references/commenting_workflow.md` for Word comment behavior.
- `references/rule-source-boundaries.md` for rule-source restrictions.
- `references/company-review-standards.md` for approved company review rules.
- `references/prompt_templates.md` for external semantic-agent output constraints.
- `references/agent-output-acceptance-examples.md` when changing agent JSON acceptance, repair, or filtering behavior.
