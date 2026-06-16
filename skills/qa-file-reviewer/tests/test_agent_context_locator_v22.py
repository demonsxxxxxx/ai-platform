import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


def source_hash(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_context_package(
    root,
    *,
    context_profile="locator-v2.2",
    include_metrics=True,
    unit_row=None,
    metrics=None,
):
    manifest = {
        "schema_version": "qa-review-context-package.v2",
        "context_profile": context_profile,
        "context_parts": ["agent_review_context.txt"],
        "unit_index": "review_units.jsonl",
        "block_index": "review_blocks.jsonl",
        "bilingual_pairs": "bilingual_pairs.jsonl",
        "unit_count": 1,
        "covered_unit_count": 1,
        "truncated": False,
    }
    if include_metrics:
        manifest["metrics"] = "agent_context_metrics.json"

    unit_row = unit_row or {
        "schema_version": "qa-review-context-unit.v2.2",
        "unit_id": "p-00001",
        "block_id": "b-00001",
        "text": "Alpha",
        "source_hash": source_hash("Alpha"),
        "review_domains": ["english"],
        "locator_safety": {
            "primary_rule": "anchor_quote must be copied from text",
            "short_anchor_risk": False,
        },
        "section_path": [],
    }
    metrics = metrics or {
        "schema_version": "qa-review-context-metrics.v2.2",
        "unit_count": 1,
        "block_count": 1,
        "part_count": 1,
        "domain_counts": {"english": 1},
        "table_unit_count": 0,
        "body_unit_count": 1,
        "short_anchor_risk_count": 0,
    }

    (root / "agent_context_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "agent_review_context.txt").write_text(
        "\n".join(
            [
                "# QA Review Context v2.2",
                "### UNIT p-00001",
                "TEXT:",
                "Alpha",
                "ANCHOR_RULE:",
                "anchor_quote must be copied from TEXT exactly.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "review_units.jsonl").write_text(json.dumps(unit_row, ensure_ascii=False) + "\n", encoding="utf-8")
    (root / "review_blocks.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "qa-review-context-block.v2.2",
                "block_id": "b-00001",
                "block_kind": "body",
                "review_domains": ["english"],
                "unit_ids": ["p-00001"],
                "unit_count": 1,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "bilingual_pairs.jsonl").write_text("", encoding="utf-8")
    if include_metrics:
        (root / "agent_context_metrics.json").write_text(json.dumps(metrics, ensure_ascii=False), encoding="utf-8")
    return root / "agent_context_manifest.json"


class AgentContextLocatorV22Tests(unittest.TestCase):
    def test_unit_metadata_covers_full_flow_not_only_bilingual(self):
        from review_units import build_review_units_v2

        document_map = {
            "paragraphs": [
                {
                    "paragraph_id": "p-00001",
                    "logical_index": 1,
                    "xml_index": 1,
                    "document_zone": "body",
                    "style_name": "Heading 1",
                    "text": "1 目的 PURPOSE",
                },
                {
                    "paragraph_id": "p-00002",
                    "logical_index": 2,
                    "xml_index": 2,
                    "document_zone": "body",
                    "text": "测试样品alpha注射液用于本次可比性研究。",
                },
                {
                    "paragraph_id": "p-00003",
                    "logical_index": 3,
                    "xml_index": 3,
                    "document_zone": "table",
                    "table_index": 1,
                    "row_index": 2,
                    "cell_index": 1,
                    "text": "Recovery = |A-B| / C x 100%",
                },
                {
                    "paragraph_id": "p-00004",
                    "logical_index": 4,
                    "xml_index": 4,
                    "document_zone": "body",
                    "text": "见 AB(C)-IP999-P-001-R01《偏差列表》。",
                },
            ]
        }

        units = {unit["unit_id"]: unit for unit in build_review_units_v2(document_map)}

        self.assertEqual(units["p-00002"]["schema_version"], "qa-review-context-unit.v2.2")
        self.assertIn("section_title", units["p-00001"]["review_domains"])
        self.assertIn("chinese", units["p-00002"]["review_domains"])
        self.assertIn("semantic_consistency", units["p-00002"]["review_domains"])
        self.assertIn("formula", units["p-00003"]["review_domains"])
        self.assertIn("numeric_consistency", units["p-00003"]["review_domains"])
        self.assertIn("reference", units["p-00004"]["review_domains"])
        self.assertEqual(units["p-00002"]["section_path"], ["1 目的 PURPOSE"])
        self.assertEqual(units["p-00002"]["locator_safety"]["primary_rule"], "anchor_quote must be copied from text")
        self.assertFalse(units["p-00002"]["locator_safety"]["short_anchor_risk"])

    def test_rendered_context_separates_problem_and_evidence_locator(self):
        from export_agent_review_context import render_context_v21

        units = [
            {
                "unit_id": "p-00002",
                "block_id": "b-00001",
                "zone": "body",
                "location_hint": "body",
                "text_kind": "zh",
                "review_domains": ["chinese", "semantic_consistency"],
                "section_hint": "1 目的 PURPOSE",
                "section_path": ["1 目的 PURPOSE"],
                "neighbor_unit_ids": ["p-00001", "p-00003"],
                "same_cell_unit_ids": [],
                "text": "测试样品alpha注射液用于本次可比性研究。",
            }
        ]
        blocks = [
            {
                "block_id": "b-00001",
                "block_kind": "body",
                "section_hint": "1 目的 PURPOSE",
                "review_domains": ["chinese", "semantic_consistency"],
                "unit_ids": ["p-00002"],
                "context_summary": "Review terminology and internal semantic consistency.",
            }
        ]

        text = render_context_v21(units, blocks, part_number=1, total_parts=1)

        self.assertIn("# QA Review Context v2.2", text)
        self.assertIn("full-flow QA, not only bilingual comparison", text)
        self.assertIn("Problem locator: unit_id + anchor_quote", text)
        self.assertIn("Evidence locator: evidence_unit_ids", text)
        self.assertIn("section_hint: 1 目的 PURPOSE", text)
        self.assertIn("section_path: 1 目的 PURPOSE", text)
        self.assertIn("anchor_quote must be copied from TEXT exactly", text)
        self.assertIn("Do not use evidence text, replacement text, or generic labels.", text)
        self.assertLess(text.index("## BLOCK b-00001"), text.index("### UNIT p-00002"))
        self.assertNotIn("P342", text)

    def test_export_writes_metrics_and_manifest_profile(self):
        from export_agent_review_context import export_context_v2
        from validate_agent_context_package import validate_package

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
                                "style_name": "Heading 1",
                                "text": "1 Scope",
                            },
                            {
                                "paragraph_id": "p-00002",
                                "logical_index": 2,
                                "xml_index": 2,
                                "document_zone": "table",
                                "table_index": 1,
                                "row_index": 2,
                                "cell_index": 1,
                                "text": "Result = |A-B| / C x 100%",
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            manifest_path = export_context_v2(document_map_path, root / "agent_review_context.txt")

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            metrics = json.loads((root / manifest["metrics"]).read_text(encoding="utf-8"))
            errors = validate_package(manifest_path)

        self.assertEqual(manifest["schema_version"], "qa-review-context-package.v2")
        self.assertEqual(manifest["context_profile"], "locator-v2.2")
        self.assertEqual(manifest["metrics"], "agent_context_metrics.json")
        self.assertEqual(metrics["schema_version"], "qa-review-context-metrics.v2.2")
        self.assertEqual(metrics["unit_count"], manifest["unit_count"])
        self.assertEqual(metrics["block_count"], 2)
        self.assertEqual(metrics["part_count"], 1)
        self.assertEqual(metrics["table_unit_count"], 1)
        self.assertEqual(metrics["body_unit_count"], 1)
        self.assertIn("formula", metrics["domain_counts"])
        self.assertEqual(errors, [])

    def test_validator_accepts_metrics_file_declared_by_manifest(self):
        from validate_agent_context_package import validate_package

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = write_context_package(root)

            errors = validate_package(manifest_path)

        self.assertEqual(errors, [])

    def test_validator_rejects_locator_v22_manifest_without_metrics(self):
        from validate_agent_context_package import validate_package

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = write_context_package(root, include_metrics=False)

            errors = validate_package(manifest_path)

        self.assertTrue(any("locator-v2.2 manifest must declare metrics" in error for error in errors))

    def test_validator_rejects_locator_v22_old_or_incomplete_unit_rows(self):
        from validate_agent_context_package import validate_package

        cases = [
            (
                "old schema",
                {
                    "schema_version": "qa-review-context-unit.v2",
                    "unit_id": "p-00001",
                    "block_id": "b-00001",
                    "text": "Alpha",
                    "source_hash": source_hash("Alpha"),
                    "review_domains": ["english"],
                    "locator_safety": {},
                    "section_path": [],
                },
                "schema_version must be qa-review-context-unit.v2.2",
            ),
            (
                "missing fields",
                {
                    "schema_version": "qa-review-context-unit.v2.2",
                    "unit_id": "p-00001",
                    "block_id": "b-00001",
                    "text": "Alpha",
                    "source_hash": source_hash("Alpha"),
                },
                "review_domains must be a non-empty list",
            ),
        ]

        for name, unit_row, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                manifest_path = write_context_package(root, unit_row=unit_row)

                errors = validate_package(manifest_path)

                self.assertTrue(any(expected in error for error in errors), errors)

    def test_validator_rejects_locator_v22_metrics_count_mismatches(self):
        from validate_agent_context_package import validate_package

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = write_context_package(
                root,
                metrics={
                    "schema_version": "qa-review-context-metrics.v2.2",
                    "unit_count": 2,
                    "block_count": 2,
                    "part_count": 2,
                    "domain_counts": {"english": 1},
                    "table_unit_count": 0,
                    "body_unit_count": 1,
                    "short_anchor_risk_count": 0,
                },
            )

            errors = validate_package(manifest_path)

        self.assertTrue(any("metrics unit_count must equal manifest unit_count" in error for error in errors), errors)
        self.assertTrue(any("metrics block_count must equal block row count" in error for error in errors), errors)
        self.assertTrue(any("metrics part_count must equal manifest context_parts count" in error for error in errors), errors)

    def test_validator_rejects_locator_v22_metrics_recomputed_content_mismatches(self):
        from validate_agent_context_package import validate_package

        cases = [
            (
                "domain counts",
                {"domain_counts": {"semantic_consistency": 1}},
                "metrics domain_counts must match unit review_domains",
            ),
            (
                "table unit count",
                {"table_unit_count": 1},
                "metrics table_unit_count must match unit rows",
            ),
            (
                "body unit count",
                {"body_unit_count": 0},
                "metrics body_unit_count must match unit rows",
            ),
            (
                "short anchor risk count",
                {"short_anchor_risk_count": 1},
                "metrics short_anchor_risk_count must match unit rows",
            ),
        ]

        for name, override, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                metrics = {
                    "schema_version": "qa-review-context-metrics.v2.2",
                    "unit_count": 1,
                    "block_count": 1,
                    "part_count": 1,
                    "domain_counts": {"english": 1},
                    "table_unit_count": 0,
                    "body_unit_count": 1,
                    "short_anchor_risk_count": 0,
                }
                metrics.update(override)
                manifest_path = write_context_package(root, metrics=metrics)

                errors = validate_package(manifest_path)

                self.assertTrue(any(expected in error for error in errors), errors)

    def test_large_body_block_uses_summarized_unit_ids_in_context_txt(self):
        from export_agent_review_context import export_context_v2
        from validate_agent_context_package import _load_jsonl, validate_package

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            document_map_path = root / "document_map.json"
            document_map = {
                "paragraphs": [
                    {
                        "paragraph_id": f"p-{index:05d}",
                        "logical_index": index,
                        "xml_index": index,
                        "document_zone": "body",
                        "text": f"Body line {index}",
                    }
                    for index in range(1, 35001)
                ]
            }
            document_map_path.write_text(json.dumps(document_map, ensure_ascii=False), encoding="utf-8")

            manifest_path = export_context_v2(document_map_path, root / "agent_review_context.txt", max_chars_per_part=240000)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            errors = validate_package(manifest_path)
            block_rows = _load_jsonl(root / manifest["block_index"])

            unit_id_lines = []
            for part_name in manifest["context_parts"]:
                for line in (root / part_name).read_text(encoding="utf-8").splitlines():
                    if line.startswith("unit_ids:"):
                        unit_id_lines.append(line)

        self.assertEqual(errors, [])
        self.assertEqual(manifest["unit_count"], 35000)
        self.assertEqual(manifest["covered_unit_count"], 35000)
        self.assertEqual(block_rows[0]["unit_count"], 35000)
        self.assertEqual(len(block_rows[0]["unit_ids"]), 35000)
        self.assertTrue(unit_id_lines)
        self.assertTrue(all(len(line) < 500 for line in unit_id_lines), unit_id_lines[:1])
        self.assertTrue(all("..." in line for line in unit_id_lines), unit_id_lines[:1])


if __name__ == "__main__":
    unittest.main()
