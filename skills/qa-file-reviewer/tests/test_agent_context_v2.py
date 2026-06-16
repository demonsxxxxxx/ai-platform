import json
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


class AgentContextV2Tests(unittest.TestCase):
    def test_review_units_include_neighbors_and_same_cell_units(self):
        from review_units import build_review_units_v2

        document_map = {
            "paragraphs": [
                {"paragraph_id": "p-00001", "logical_index": 1, "xml_index": 10, "document_zone": "body", "text": "标题"},
                {"paragraph_id": "p-00002", "logical_index": 2, "xml_index": 11, "document_zone": "table", "table_index": 1, "row_index": 1, "cell_index": 1, "text": "中文结果"},
                {"paragraph_id": "p-00003", "logical_index": 3, "xml_index": 12, "document_zone": "table", "table_index": 1, "row_index": 1, "cell_index": 1, "text": "English result"},
            ]
        }

        units = build_review_units_v2(document_map)
        by_id = {unit["unit_id"]: unit for unit in units}

        self.assertEqual(by_id["p-00002"]["schema_version"], "qa-review-context-unit.v2.2")
        self.assertEqual(by_id["p-00002"]["neighbor_unit_ids"], ["p-00001", "p-00003"])
        self.assertEqual(by_id["p-00002"]["same_cell_unit_ids"], ["p-00003"])
        self.assertEqual(by_id["p-00002"]["location_hint"], "table T1R1C1")
        self.assertTrue(by_id["p-00002"]["source_hash"].startswith("sha256:"))

    def test_export_context_v2_writes_manifest_and_multiple_parts(self):
        from export_agent_review_context import export_context_v2

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            document_map_path = root / "document_map.json"
            document_map_path.write_text(
                json.dumps(
                    {
                        "paragraphs": [
                            {"paragraph_id": "p-00001", "logical_index": 1, "xml_index": 1, "document_zone": "body", "text": "Alpha " * 20},
                            {"paragraph_id": "p-00002", "logical_index": 2, "xml_index": 2, "document_zone": "body", "text": "Beta " * 20},
                            {"paragraph_id": "p-00003", "logical_index": 3, "xml_index": 3, "document_zone": "body", "text": "Gamma " * 20},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            manifest_path = export_context_v2(document_map_path, root / "agent_review_context.txt", max_chars_per_part=900)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            combined_context = "\n".join((root / part).read_text(encoding="utf-8") for part in manifest["context_parts"])

            self.assertEqual(manifest["schema_version"], "qa-review-context-package.v2")
            self.assertEqual(manifest["unit_count"], 3)
            self.assertEqual(manifest["covered_unit_count"], 3)
            self.assertFalse(manifest["truncated"])
            self.assertEqual(manifest["unit_index"], "review_units.jsonl")
            self.assertEqual(manifest["block_index"], "review_blocks.jsonl")
            self.assertEqual(manifest["bilingual_pairs"], "bilingual_pairs.jsonl")
            self.assertGreater(len(manifest["context_parts"]), 1)
            for part in manifest["context_parts"]:
                self.assertTrue((root / part).exists())
            self.assertTrue((root / "review_units.jsonl").exists())
            self.assertTrue((root / "review_blocks.jsonl").exists())
            self.assertTrue((root / "bilingual_pairs.jsonl").exists())
            self.assertNotIn("[TRUNCATED]", combined_context)
            self.assertIn("### UNIT p-00001", combined_context)
            self.assertIn("### UNIT p-00002", combined_context)
            self.assertIn("### UNIT p-00003", combined_context)

    def test_export_context_v2_rejects_single_unit_larger_than_part_limit(self):
        from export_agent_review_context import export_context_v2

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            document_map_path = root / "document_map.json"
            document_map_path.write_text(
                json.dumps(
                    {
                        "paragraphs": [
                            {
                                "paragraph_id": "p-00001",
                                "logical_index": 1,
                                "xml_index": 1,
                                "document_zone": "body",
                                "text": "Alpha " * 80,
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "exceeds --max-chars-per-part"):
                export_context_v2(document_map_path, root / "agent_review_context.txt", max_chars_per_part=120)

    def test_table_units_include_compact_row_context(self):
        from review_units import build_review_units_v2

        document_map = {
            "paragraphs": [
                {"paragraph_id": "p-00001", "logical_index": 1, "xml_index": 1, "document_zone": "table", "table_index": 2, "row_index": 1, "cell_index": 1, "text": "检测项目"},
                {"paragraph_id": "p-00002", "logical_index": 2, "xml_index": 2, "document_zone": "table", "table_index": 2, "row_index": 1, "cell_index": 2, "text": "结果"},
                {"paragraph_id": "p-00003", "logical_index": 3, "xml_index": 3, "document_zone": "table", "table_index": 2, "row_index": 2, "cell_index": 1, "text": "纯度"},
                {"paragraph_id": "p-00004", "logical_index": 4, "xml_index": 4, "document_zone": "table", "table_index": 2, "row_index": 2, "cell_index": 2, "text": "98.0%"},
            ]
        }

        unit = {item["unit_id"]: item for item in build_review_units_v2(document_map)}["p-00004"]

        self.assertEqual(unit["row_hint"], "纯度 | 98.0%")
        self.assertEqual(unit["col_hint"], "结果")


if __name__ == "__main__":
    unittest.main()
