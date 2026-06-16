import json
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


class ReviewUnitsContextTests(unittest.TestCase):
    def test_review_units_use_paragraph_id_and_skip_empty_text(self):
        from review_units import build_review_units

        units = build_review_units(
            {
                "paragraphs": [
                    {
                        "paragraph_id": "p-00098",
                        "logical_index": 98,
                        "xml_index": 106,
                        "document_zone": "table",
                        "table_index": 5,
                        "row_index": 9,
                        "cell_index": 1,
                        "text": "中文内容",
                    },
                    {
                        "paragraph_id": "p-empty",
                        "logical_index": 99,
                        "xml_index": 107,
                        "document_zone": "body",
                        "text": "   ",
                    },
                    {
                        "paragraph_id": "p-00100",
                        "logical_index": 100,
                        "xml_index": 108,
                        "document_zone": "body",
                        "text": "English content",
                    },
                ]
            }
        )

        self.assertEqual(len(units), 2)
        self.assertEqual(
            units[0],
            {
                "unit_id": "p-00098",
                "paragraph_id": "p-00098",
                "logical_index": 98,
                "xml_index": 106,
                "zone": "table",
                "table_index": 5,
                "row_index": 9,
                "cell_index": 1,
                "text_kind": "zh",
                "text": "中文内容",
            },
        )
        self.assertEqual(units[1]["unit_id"], "p-00100")
        self.assertEqual(units[1]["text_kind"], "en")

    def test_bilingual_pairs_detect_same_cell_and_nearby_adjacent_zh_to_en(self):
        from review_units import build_bilingual_pairs, build_review_units

        units = build_review_units(
            {
                "paragraphs": [
                    {
                        "paragraph_id": "p-00001",
                        "logical_index": 1,
                        "xml_index": 10,
                        "document_zone": "table",
                        "table_index": 2,
                        "row_index": 3,
                        "cell_index": 4,
                        "text": "目的",
                    },
                    {
                        "paragraph_id": "p-00002",
                        "logical_index": 2,
                        "xml_index": 11,
                        "document_zone": "table",
                        "table_index": 2,
                        "row_index": 3,
                        "cell_index": 4,
                        "text": "Purpose",
                    },
                    {
                        "paragraph_id": "p-00003",
                        "logical_index": 3,
                        "xml_index": 20,
                        "document_zone": "body",
                        "text": "范围",
                    },
                    {
                        "paragraph_id": "p-00004",
                        "logical_index": 4,
                        "xml_index": 22,
                        "document_zone": "body",
                        "text": "Scope",
                    },
                    {
                        "paragraph_id": "p-00005",
                        "logical_index": 5,
                        "xml_index": 30,
                        "document_zone": "body",
                        "text": "条件",
                    },
                    {
                        "paragraph_id": "p-00006",
                        "logical_index": 6,
                        "xml_index": 33,
                        "document_zone": "body",
                        "text": "Condition",
                    },
                ]
            }
        )

        pairs = build_bilingual_pairs(units)

        self.assertEqual(
            pairs,
            [
                {
                    "pair_id": "bp-00001",
                    "pair_type": "same_cell_adjacent",
                    "zh_unit_id": "p-00001",
                    "en_unit_id": "p-00002",
                    "zh_text": "目的",
                    "en_text": "Purpose",
                    "table_index": 2,
                    "row_index": 3,
                    "cell_index": 4,
                },
                {
                    "pair_id": "bp-00002",
                    "pair_type": "adjacent",
                    "zh_unit_id": "p-00003",
                    "en_unit_id": "p-00004",
                    "zh_text": "范围",
                    "en_text": "Scope",
                    "table_index": None,
                    "row_index": None,
                    "cell_index": None,
                },
            ],
        )

    def test_export_context_writes_compact_txt_and_sidecar_jsonl(self):
        from export_agent_review_context import export_context

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            document_map_path = root / "document_map.json"
            context_path = root / "agent_review_context.txt"
            document_map_path.write_text(
                json.dumps(
                    {
                        "paragraphs": [
                            {
                                "paragraph_id": "p-00098",
                                "logical_index": 98,
                                "xml_index": 106,
                                "document_zone": "table",
                                "table_index": 5,
                                "row_index": 9,
                                "cell_index": 1,
                                "text": "目的",
                            },
                            {
                                "paragraph_id": "p-00099",
                                "logical_index": 99,
                                "xml_index": 107,
                                "document_zone": "table",
                                "table_index": 5,
                                "row_index": 9,
                                "cell_index": 1,
                                "text": "Purpose",
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            export_context(document_map_path, context_path, max_chars=240000)

            context = context_path.read_text(encoding="utf-8")
            units = (root / "review_units.jsonl").read_text(encoding="utf-8")
            pairs = (root / "bilingual_pairs.jsonl").read_text(encoding="utf-8")

        self.assertIn("[u:p-00098 | P98 | XML:106 | table T5R9C1] 目的", context)
        self.assertIn('"unit_id":"p-00098"', units)
        self.assertIn('"text_kind":"zh"', units)
        self.assertIn('"pair_id":"bp-00001"', pairs)
        self.assertIn('"pair_type":"same_cell_adjacent"', pairs)


class ReviewContextCurrentContractTests(unittest.TestCase):
    def test_v1_context_includes_primary_unit_id_and_human_hints(self):
        from review_units import build_review_units, format_context_line

        units = build_review_units(
            {
                "paragraphs": [
                    {
                        "paragraph_id": "p-00098",
                        "logical_index": 98,
                        "xml_index": 106,
                        "document_zone": "table",
                        "table_index": 5,
                        "row_index": 9,
                        "cell_index": 1,
                        "text": "目的",
                    }
                ]
            }
        )

        line = format_context_line(units[0])

        self.assertIn("u:p-00098", line)
        self.assertIn("P98", line)
        self.assertIn("XML:106", line)
        self.assertIn("table T5R9C1", line)
        self.assertTrue(line.endswith("目的"))


if __name__ == "__main__":
    unittest.main()
