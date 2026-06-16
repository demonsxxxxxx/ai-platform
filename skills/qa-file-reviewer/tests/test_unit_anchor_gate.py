import sys
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import qa_comment_adjudicator as adjudicator  # noqa: E402
import validate_pipeline_gate as gate  # noqa: E402


def visible_agent_issue(**overrides):
    payload = {
        "id": "issue-0001",
        "branch": "llm_full_review",
        "agent_role": "llm-full-review-agent",
        "rule_id": "LLM-SEM-001",
        "type": "大模型全文审核",
        "location": "第5段（XML:5）",
        "document_zone": "body",
        "severity": "主要",
        "comment_text": "问题：存在可见问题\n原文：Alpha issue\n建议：Replace the source phrase.",
        "evidence": "The source unit contains Alpha issue.",
        "comment_visibility": "word_comment",
        "requires_external_evidence": False,
        "external_evidence_type": "none",
        "coverage_domain": "data_consistency",
        "review_basis": "single_doc_internal",
        "comment_intent": "suggest_change",
        "status": "confirmed",
        "original": "Alpha issue",
        "issue": "The cited unit has an exact source-backed issue.",
        "suggestion": "Replace the source phrase.",
        "match_method": "span",
        "comments_added": 1,
        "anchor_span": {"start": 0, "end": len("Alpha issue"), "unit": "char"},
        "anchor_locator": "paragraph=5",
        "anchor_text": "Alpha issue",
        "anchor_quote": "Alpha issue",
        "source": "qa-file-reviewer-agent-review",
        "source_agent": "semantic_consistency",
    }
    payload.update(overrides)
    return payload


class UnitAnchorGateTests(unittest.TestCase):
    def test_adjudicator_refines_broad_precomputed_unit_anchor_span(self):
        paragraph_text = "Intro text. Alpha defect should be corrected."
        precomputed_span = {"start": 0, "end": len(paragraph_text), "unit": "char"}
        issue = {
            "id": "issue-0001",
            "branch": "llm_full_review",
            "rule_id": "LLM-SEM-001",
            "unit_id": "p-00005",
            "location": "第5段（XML:5）",
            "paragraph_index": "5",
            "anchor_locator": "paragraph=5",
            "anchor_span": dict(precomputed_span),
            "anchor_text": paragraph_text,
            "anchor_quote": "Alpha defect",
            "original": "Alpha defect",
            "issue": "The cited unit contains `Alpha defect`.",
            "suggestion": "Replace `Alpha defect` with the approved wording in the cited unit.",
            "evidence": "The cited unit contains `Alpha defect`.",
            "comments_added": 1,
            "match_method": "span",
            "status": "confirmed",
        }
        record = {
            "paragraph_id": "p-00005",
            "logical_index": 5,
            "xml_index": 5,
            "text": paragraph_text,
        }

        adjudicated = adjudicator.adjudicate_issues([issue], [record])

        self.assertEqual(len(adjudicated), 1)
        self.assertEqual(
            adjudicated[0]["anchor_span"],
            {"start": paragraph_text.find("Alpha defect"), "end": paragraph_text.find("Alpha defect") + len("Alpha defect"), "unit": "char"},
        )
        self.assertEqual(adjudicated[0]["anchor_text"], "Alpha defect")
        self.assertEqual(adjudicated[0]["match_method"], "span")

    def test_adjudicator_preserves_specific_precomputed_unit_anchor_span(self):
        paragraph_text = "Intro text. Alpha defect should be corrected."
        start = paragraph_text.find("Alpha defect")
        precomputed_span = {"start": start, "end": start + len("Alpha defect"), "unit": "char"}
        issue = {
            "id": "issue-0001",
            "branch": "llm_full_review",
            "rule_id": "LLM-SEM-001",
            "unit_id": "p-00005",
            "location": "第5段（XML:5）",
            "paragraph_index": "5",
            "anchor_locator": "paragraph=5",
            "anchor_span": dict(precomputed_span),
            "anchor_text": "Alpha defect",
            "anchor_quote": "Alpha defect",
            "original": "Alpha defect",
            "issue": "The cited unit contains `Alpha defect`.",
            "suggestion": "Replace `Alpha defect` with the approved wording in the cited unit.",
            "evidence": "The cited unit contains `Alpha defect`.",
            "comments_added": 1,
            "match_method": "span",
            "status": "confirmed",
        }
        record = {
            "paragraph_id": "p-00005",
            "logical_index": 5,
            "xml_index": 5,
            "text": paragraph_text,
        }

        adjudicated = adjudicator.adjudicate_issues([issue], [record])

        self.assertEqual(len(adjudicated), 1)
        self.assertEqual(adjudicated[0]["anchor_span"], precomputed_span)
        self.assertEqual(adjudicated[0]["anchor_text"], "Alpha defect")

    def test_adjudicator_does_not_refine_unit_anchor_outside_anchor_quote(self):
        paragraph_text = "PAI批次 PAI BATCH and Commercial Batch are both listed."
        start = paragraph_text.find("PAI批次 PAI BATCH")
        issue = {
            "id": "issue-0001",
            "branch": "llm_full_review",
            "rule_id": "LLM-SEM-001",
            "unit_id": "p-00067",
            "location": "第67段（XML:67）",
            "paragraph_index": "67",
            "anchor_locator": "paragraph=67",
            "anchor_span": {"start": start, "end": start + len("PAI批次 PAI BATCH"), "unit": "char"},
            "anchor_text": "PAI批次 PAI BATCH",
            "anchor_quote": "PAI批次 PAI BATCH",
            "original": "Commercial Batch",
            "issue": "The issue quotes `Commercial Batch` but the unit contract anchors `PAI批次 PAI BATCH`.",
            "suggestion": "Change `PAI批次 PAI BATCH` to `PAI batches`.",
            "evidence": "The cited unit contains `Commercial Batch`.",
            "comments_added": 1,
            "match_method": "span",
            "status": "confirmed",
        }
        record = {
            "paragraph_id": "p-00067",
            "logical_index": 67,
            "xml_index": 67,
            "text": paragraph_text,
        }

        adjudicated = adjudicator.adjudicate_issues([issue], [record])

        self.assertEqual(len(adjudicated), 1)
        self.assertEqual(adjudicated[0]["anchor_text"], "PAI批次 PAI BATCH")
        self.assertEqual(adjudicated[0]["anchor_span"], {"start": start, "end": start + len("PAI批次 PAI BATCH"), "unit": "char"})

    def test_gate_rejects_visible_agent_unit_issue_without_anchor_span(self):
        errors = []
        warnings = []

        gate.validate_issues(
            [
                visible_agent_issue(
                    unit_id="p-00005",
                    anchor_span=None,
                )
            ],
            errors,
            warnings,
        )

        self.assertTrue(any("unit_id=p-00005" in item and "anchor_span" in item for item in errors))

    def test_gate_rejects_visible_agent_unit_issue_without_anchor_quote(self):
        errors = []
        warnings = []

        gate.validate_issues(
            [
                visible_agent_issue(
                    unit_id="p-00005",
                    anchor_quote="",
                )
            ],
            errors,
            warnings,
        )

        self.assertTrue(any("unit_id=p-00005" in item and "anchor_quote" in item for item in errors))

    def test_gate_rejects_visible_agent_issue_without_unit_id(self):
        errors = []
        warnings = []

        gate.validate_issues(
            [
                visible_agent_issue(
                    unit_id="",
                    anchor_quote="Alpha issue",
                    anchor_span={"start": 0, "end": len("Alpha issue"), "unit": "char"},
                    anchor_locator="paragraph=5",
                    anchor_text="Alpha issue",
                    match_method="span",
                )
            ],
            errors,
            warnings,
        )

        self.assertTrue(any("visible agent issue" in item and "unit_id" in item for item in errors))

    def test_gate_rejects_visible_agent_global_summary_without_unit_id(self):
        errors = []
        warnings = []

        gate.validate_issues(
            [
                visible_agent_issue(
                    unit_id="",
                    comment_intent="global_summary",
                    location_kind="global",
                    location="全文审核意见",
                    anchor_quote="Alpha issue",
                    anchor_span={"start": 0, "end": len("Alpha issue"), "unit": "char"},
                    anchor_locator="paragraph=5",
                    anchor_text="Alpha issue",
                    match_method="span",
                )
            ],
            errors,
            warnings,
        )

        self.assertTrue(any("visible agent issue" in item and "unit_id" in item for item in errors))

    def test_gate_rejects_visible_agent_unit_issue_when_anchor_quote_does_not_match_anchor_text(self):
        errors = []
        warnings = []

        gate.validate_issues(
            [
                visible_agent_issue(
                    unit_id="p-00005",
                    anchor_quote="Wrong text",
                    anchor_text="Alpha issue",
                )
            ],
            errors,
            warnings,
        )

        self.assertTrue(any("unit_id=p-00005" in item and "anchor_quote" in item for item in errors))

    def test_gate_accepts_refined_anchor_text_inside_broader_anchor_quote(self):
        errors = []
        warnings = []

        gate.validate_issues(
            [
                visible_agent_issue(
                    unit_id="p-00005",
                    anchor_quote="Alpha issue in part one",
                    anchor_text="Alpha issue",
                )
            ],
            errors,
            warnings,
        )

        self.assertEqual(errors, [])

    def test_gate_accepts_visible_agent_unit_issue_with_span_match_method(self):
        errors = []
        warnings = []

        gate.validate_issues(
            [
                visible_agent_issue(
                    unit_id="p-00005",
                    anchor_span={"start": 0, "end": len("Alpha issue"), "unit": "char"},
                    anchor_locator="paragraph=5",
                    anchor_text="Alpha issue",
                    match_method="span",
                )
            ],
            errors,
            warnings,
        )

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
