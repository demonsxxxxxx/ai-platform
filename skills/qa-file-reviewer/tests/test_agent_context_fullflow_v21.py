import sys
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


class AgentContextFullFlowV21Tests(unittest.TestCase):
    def test_table_formula_reference_units_receive_fullflow_domains(self):
        from review_units import build_review_units_v2

        document_map = {
            "paragraphs": [
                {
                    "paragraph_id": "p-00001",
                    "logical_index": 1,
                    "xml_index": 1,
                    "document_zone": "body",
                    "style_name": "Heading 1",
                    "text": "7 检测结果",
                },
                {
                    "paragraph_id": "p-00002",
                    "logical_index": 2,
                    "xml_index": 2,
                    "document_zone": "table",
                    "table_index": 1,
                    "row_index": 2,
                    "cell_index": 1,
                    "text": "回收率 Recovery = |A-B| / C × 100%",
                },
                {
                    "paragraph_id": "p-00003",
                    "logical_index": 3,
                    "xml_index": 3,
                    "document_zone": "body",
                    "text": "见 MV(C)-IP312-P-027-R04《偏差列表》。",
                },
            ]
        }

        units = {unit["unit_id"]: unit for unit in build_review_units_v2(document_map)}

        self.assertIn("section_title", units["p-00001"]["review_domains"])
        self.assertIn("formula", units["p-00002"]["review_domains"])
        self.assertIn("numeric_consistency", units["p-00002"]["review_domains"])
        self.assertIn("reference", units["p-00003"]["review_domains"])

    def test_context_text_renders_block_before_unit_locator_cards(self):
        from export_agent_review_context import render_context_v21

        units = [
            {
                "unit_id": "p-00010",
                "block_id": "b-00001",
                "zone": "table",
                "location_hint": "table T1R2C1",
                "text_kind": "mixed",
                "review_domains": ["table_data", "bilingual_consistency"],
                "neighbor_unit_ids": [],
                "same_cell_unit_ids": [],
                "text": "mass error",
            }
        ]
        blocks = [
            {
                "block_id": "b-00001",
                "block_kind": "table",
                "section_hint": "7 检测结果",
                "review_domains": ["table_data", "bilingual_consistency"],
                "unit_ids": ["p-00010"],
            }
        ]

        text = render_context_v21(units, blocks, part_number=1, total_parts=1)

        self.assertLess(text.index("## BLOCK b-00001"), text.index("### UNIT p-00010"))
        self.assertIn("Review scope: Chinese, English, bilingual consistency", text)
        self.assertIn("Required problem locator: unit_id + anchor_quote", text)
        self.assertIn("anchor_quote must be copied from TEXT exactly", text)


if __name__ == "__main__":
    unittest.main()
