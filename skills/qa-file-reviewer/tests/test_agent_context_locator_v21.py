import json
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


class AgentContextLocatorV21Tests(unittest.TestCase):
    def test_validator_rejects_block_with_unknown_unit(self):
        from validate_agent_context_package import validate_package

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "agent_context_manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": "qa-review-context-package.v2",
                        "context_parts": ["agent_review_context.txt"],
                        "unit_index": "review_units.jsonl",
                        "block_index": "review_blocks.jsonl",
                        "bilingual_pairs": "bilingual_pairs.jsonl",
                        "unit_count": 1,
                        "covered_unit_count": 1,
                        "truncated": False,
                    }
                ),
                encoding="utf-8",
            )
            (root / "agent_review_context.txt").write_text(
                "### UNIT p-00001\nTEXT:\nAlpha\nANCHOR_RULE:\nanchor_quote must be copied from TEXT exactly.\n",
                encoding="utf-8",
            )
            (root / "review_units.jsonl").write_text('{"unit_id":"p-00001","text":"Alpha"}\n', encoding="utf-8")
            (root / "review_blocks.jsonl").write_text('{"block_id":"b-00001","unit_ids":["p-99999"]}\n', encoding="utf-8")
            (root / "bilingual_pairs.jsonl").write_text("", encoding="utf-8")

            errors = validate_package(root / "agent_context_manifest.json")

        self.assertTrue(any("unknown unit" in error for error in errors))

    def test_validator_rejects_block_unit_count_and_unit_block_id_mismatch(self):
        from validate_agent_context_package import validate_package

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "agent_context_manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": "qa-review-context-package.v2",
                        "context_parts": ["agent_review_context.txt"],
                        "unit_index": "review_units.jsonl",
                        "block_index": "review_blocks.jsonl",
                        "bilingual_pairs": "bilingual_pairs.jsonl",
                        "unit_count": 2,
                        "covered_unit_count": 2,
                        "truncated": False,
                    }
                ),
                encoding="utf-8",
            )
            (root / "agent_review_context.txt").write_text(
                "\n".join(
                    [
                        "### UNIT p-00001",
                        "TEXT:",
                        "Alpha",
                        "ANCHOR_RULE:",
                        "anchor_quote must be copied from TEXT exactly.",
                        "",
                        "### UNIT p-00002",
                        "TEXT:",
                        "Beta",
                        "ANCHOR_RULE:",
                        "anchor_quote must be copied from TEXT exactly.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (root / "review_units.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"unit_id": "p-00001", "block_id": "b-00099", "text": "Alpha"}),
                        json.dumps({"unit_id": "p-00002", "block_id": "b-00001", "text": "Beta"}),
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (root / "review_blocks.jsonl").write_text(
                json.dumps(
                    {
                        "schema_version": "qa-review-context-block.v2.1",
                        "block_id": "b-00001",
                        "block_kind": "body",
                        "review_domains": ["semantic_consistency"],
                        "unit_ids": ["p-00001", "p-00002"],
                        "unit_count": 3,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "bilingual_pairs.jsonl").write_text("", encoding="utf-8")

            errors = validate_package(root / "agent_context_manifest.json")

        self.assertTrue(any("unit_count mismatch for block b-00001" in error for error in errors))
        self.assertTrue(any("unit p-00001 block_id mismatch" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
