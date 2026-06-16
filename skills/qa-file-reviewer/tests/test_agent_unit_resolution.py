import sys
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import run_qa_review as qa  # noqa: E402


def paragraph(text, *, paragraph_id="", logical_index=1, xml_index=None):
    return {
        "paragraph_id": paragraph_id,
        "text": text,
        "logical_index": logical_index,
        "xml_index": logical_index if xml_index is None else xml_index,
        "type": "paragraph",
        "location_kind": "paragraph",
        "document_zone": "body",
    }


def agent_issue(**overrides):
    payload = {
        "category": "en_language",
        "severity": "主要",
        "original": "Cilent",
        "anchor_quote": "Cilent",
        "issue": "Typographical error: `Cilent` should be `Client`.",
        "suggestion": "Replace `Cilent` with `Client`.",
        "evidence": "The source sentence contains `Cilent`.",
        "confidence": "high",
        "coverage_domain": "en_language",
        "review_basis": "agent_semantic",
        "requires_external_evidence": False,
        "external_evidence_type": "none",
        "comment_intent": "suggest_change",
    }
    payload.update(overrides)
    return payload


class AgentUnitIdResolutionTests(unittest.TestCase):
    def test_unit_id_disambiguates_repeated_anchor_and_uses_xml_locator(self):
        issue = qa.make_llm_issue(
            1,
            [
                paragraph("The Cilent sample met the criteria.", paragraph_id="p-00098", logical_index=98, xml_index=106),
                paragraph("A second Cilent value is listed.", paragraph_id="p-00099", logical_index=99, xml_index=107),
            ],
            agent_issue(unit_id="p-00098", paragraph_index="P404"),
        )

        self.assertIsNotNone(issue)
        self.assertEqual(issue["anchor_text"], "Cilent")
        self.assertEqual(issue["anchor_span"], {"start": 4, "end": 10, "unit": "char"})
        self.assertEqual(issue["anchor_locator"], "paragraph=106")
        self.assertEqual(issue["location"], "第98段（XML:106）")
        self.assertEqual(issue["unit_id"], "p-00098")
        self.assertEqual(issue["anchor_quote"], "Cilent")

    def test_unit_id_wrong_record_rejects_quote_without_broad_search(self):
        issue = qa.make_llm_issue(
            1,
            [
                paragraph("The Client sample met the criteria.", paragraph_id="p-00098", logical_index=98, xml_index=106),
                paragraph("The Cilent sample met the criteria.", paragraph_id="p-00099", logical_index=99, xml_index=107),
            ],
            agent_issue(unit_id="p-00098", paragraph_index="P99"),
        )

        self.assertIsNone(issue)

    def test_unit_id_requires_anchor_quote_even_when_original_matches(self):
        issue = qa.make_llm_issue(
            1,
            [
                paragraph("The Cilent sample met the criteria.", paragraph_id="p-00098", logical_index=98, xml_index=106),
            ],
            agent_issue(unit_id="p-00098", anchor_quote=""),
        )

        self.assertIsNone(issue)

    def test_unit_id_rejects_wrong_anchor_quote_without_original_or_evidence_fallback(self):
        issue = qa.make_llm_issue(
            1,
            [
                paragraph("The Cilent sample met the criteria.", paragraph_id="p-00098", logical_index=98, xml_index=106),
            ],
            agent_issue(
                unit_id="p-00098",
                anchor_quote="not present in this unit",
                original="Cilent",
                evidence="The source sentence contains criteria.",
            ),
        )

        self.assertIsNone(issue)

    def test_unit_id_rejects_inconsistent_original_and_anchor_quote(self):
        issue = qa.make_llm_issue(
            1,
            [
                paragraph(
                    "PAI批次 PAI BATCH and Commercial Batch are both listed.",
                    paragraph_id="p-00067",
                    logical_index=67,
                    xml_index=67,
                ),
            ],
            agent_issue(
                unit_id="p-00067",
                anchor_quote="PAI批次 PAI BATCH",
                original="Commercial Batch",
                issue="The issue cites one source phrase but anchors another phrase in the same unit.",
                suggestion="Change `PAI批次 PAI BATCH` to `PAI batches`.",
                evidence="The source unit contains `PAI批次 PAI BATCH` and `Commercial Batch`.",
            ),
        )

        self.assertIsNone(issue)

    def test_legacy_paragraph_index_resolution_is_only_allowed_when_explicitly_permitted(self):
        issue = qa.make_llm_issue(
            1,
            [
                paragraph("The control sample met the criteria.", logical_index=6),
                paragraph("The Cilent sample met the criteria.", logical_index=7),
            ],
            agent_issue(paragraph_index="P7"),
            require_unit_locator=False,
        )

        self.assertIsNotNone(issue)
        self.assertEqual(issue["anchor_text"], "Cilent")
        self.assertEqual(issue["anchor_locator"], "paragraph=7")
        self.assertEqual(issue["location"], "第7段（XML:7）")

    def test_new_agent_findings_without_unit_id_are_filtered(self):
        issue = qa.make_llm_issue(
            1,
            [
                paragraph("The control sample met the criteria.", logical_index=6),
                paragraph("The Cilent sample met the criteria.", logical_index=7),
            ],
            agent_issue(paragraph_index="P7"),
            require_unit_locator=True,
        )

        self.assertIsNone(issue)


if __name__ == "__main__":
    unittest.main()
