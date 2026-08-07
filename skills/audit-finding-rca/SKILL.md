---
name: audit-finding-rca
description: Use when the user asks to analyze customer audit findings, GMP/CDMO/MAH audit observations, RCA, CAPA, corrective actions, preventive actions, or to scan/fill an Excel audit finding workbook with Chinese root-cause analysis and remediation plans.
---

# Audit Finding RCA

Use this Skill to turn customer audit findings into professional Chinese RCA/CAPA responses for a biopharma CDMO context, or to batch-fill an uploaded Excel workbook.

## Decision

- If the user pasted one or a few findings, use **Conversation Mode**.
- If the user uploaded or named an `.xlsx` workbook, use **Excel Mode**.
- If the user asks to learn from feedback or a preferred answer, write the reusable learning to `output/audit-finding-rca-experience-notes.md`; do not modify the staged Skill source.

## Conversation Mode

1. Extract each finding's description, cited regulation, affected area, severity, and owner boundary when present.
2. Read `references/writing_patterns.md`. Read `references/regulatory_basics.md` and `references/rca_methods.md` when regulations or method selection matter.
3. Classify the finding as file/SOP, validation, environmental monitoring, computerized system/data integrity, equipment/facility, or other.
4. Produce RCA using the appropriate pattern:
   - Prefer a concise causal chain for single-factor findings.
   - Use 4M1E/5-Why for multi-factor or system findings.
   - Distinguish CDMO responsibility from MAH/customer responsibility.
5. Produce 2-5 CAPA actions. Each action must include what changes, owner role/department, expected timing, and verification or follow-up.
6. If facts are missing, state the assumption inside the answer and keep the output usable.

Completion criterion: every finding has a root cause that reaches an actionable system/process level, and every CAPA item is concrete enough to assign and verify.

## Excel Mode

Work only inside the current run workspace. Put user-facing outputs under `output/`.

1. Scan the workbook:

```bash
python .claude/skills/audit-finding-rca/scripts/fill_excel.py --scan --excel "<input.xlsx>" --output-json output/audit-findings-scan.json
```

2. Read `output/audit-findings-scan.json` and generate `output/rca_data.json` as:

```json
{
  "2": {
    "rca": "根本原因分析文本",
    "capa": "纠正与预防措施文本"
  }
}
```

3. Fill the workbook:

```bash
python .claude/skills/audit-finding-rca/scripts/fill_excel.py --fill --excel "<input.xlsx>" --data output/rca_data.json --output-dir output
```

4. Report the generated workbook path and filled row counts.

Completion criterion: the original workbook is unchanged; the output workbook exists under `output/`; rows with existing RCA/CAPA text were not overwritten.

## Output Style

- Write RCA/CAPA in Chinese unless the user requests bilingual output.
- Use professional audit-response language: factual, restrained, defensible.
- Do not use empty phrases such as "加强管理" or "提高意识" unless followed by a specific document/process/training/control change.
- Avoid self-incriminating words listed in `references/writing_patterns.md`.

## References

- `references/writing_patterns.md`: preferred RCA/CAPA wording, reusable audit-response patterns, and prohibited phrasing.
- `references/regulatory_basics.md`: GMP, ICH, EU GMP, and NMPA MAH quick references.
- `references/rca_methods.md`: 4M1E, 5-Why, and method selection.
- `references/capa_report_template.md`: full CAPA report structure when the user asks for a formal report.

## Boundaries

- Do not access host paths outside the run workspace.
- Do not hard-code local desktop paths or real environment paths.
- Do not overwrite source Excel files.
- Do not expose internal staging paths in the final answer.
