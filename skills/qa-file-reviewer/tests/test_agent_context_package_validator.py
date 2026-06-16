import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


def source_hash(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class AgentContextPackageValidatorTests(unittest.TestCase):
    def test_validator_rejects_missing_context_part(self):
        from validate_agent_context_package import validate_package

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = root / "agent_context_manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "qa-review-context-package.v2",
                        "context_parts": ["missing.txt"],
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
            (root / "review_units.jsonl").write_text('{"unit_id":"p-00001","text":"Alpha"}\n', encoding="utf-8")
            (root / "review_blocks.jsonl").write_text('{"unit_id":"p-00001"}\n', encoding="utf-8")
            (root / "bilingual_pairs.jsonl").write_text("", encoding="utf-8")

            errors = validate_package(manifest)

        self.assertTrue(any("missing context part" in error for error in errors))

    def test_validator_rejects_truncated_or_incomplete_coverage(self):
        from validate_agent_context_package import validate_package

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = root / "agent_context_manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "qa-review-context-package.v2",
                        "context_parts": ["agent_review_context.txt"],
                        "unit_index": "review_units.jsonl",
                        "block_index": "review_blocks.jsonl",
                        "bilingual_pairs": "bilingual_pairs.jsonl",
                        "unit_count": 2,
                        "covered_unit_count": 1,
                        "truncated": True,
                    }
                ),
                encoding="utf-8",
            )
            (root / "agent_review_context.txt").write_text("### UNIT p-00001\nTEXT:\nAlpha\n", encoding="utf-8")
            (root / "review_units.jsonl").write_text('{"unit_id":"p-00001","text":"Alpha"}\n', encoding="utf-8")
            (root / "review_blocks.jsonl").write_text('{"unit_id":"p-00001"}\n', encoding="utf-8")
            (root / "bilingual_pairs.jsonl").write_text("", encoding="utf-8")

            errors = validate_package(manifest)

        self.assertTrue(any("must not be truncated" in error for error in errors))
        self.assertTrue(any("covered_unit_count must equal unit_count" in error for error in errors))
        self.assertTrue(any("unit_count mismatch" in error for error in errors))

    def test_validator_accepts_complete_context_package(self):
        from validate_agent_context_package import validate_package

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = root / "agent_context_manifest.json"
            manifest.write_text(
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
            (root / "agent_review_context.txt").write_text("### UNIT p-00001\nTEXT:\nAlpha\n", encoding="utf-8")
            (root / "review_units.jsonl").write_text(
                json.dumps({"unit_id": "p-00001", "text": "Alpha", "source_hash": source_hash("Alpha")}) + "\n",
                encoding="utf-8",
            )
            (root / "review_blocks.jsonl").write_text(
                json.dumps({"block_id": "part-001", "context_part": "agent_review_context.txt", "unit_ids": ["p-00001"], "unit_count": 1}) + "\n",
                encoding="utf-8",
            )
            (root / "bilingual_pairs.jsonl").write_text("", encoding="utf-8")

            errors = validate_package(manifest)

        self.assertEqual(errors, [])

    def test_validator_rejects_context_unit_text_source_hash_and_block_mismatch(self):
        from validate_agent_context_package import validate_package

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = root / "agent_context_manifest.json"
            manifest.write_text(
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
                "\n".join(
                    [
                        "### UNIT p-00001",
                        "TEXT:",
                        "Beta",
                        "ANCHOR_RULE:",
                        "copy from TEXT",
                        "",
                        "### UNIT p-extra",
                        "TEXT:",
                        "Extra",
                        "ANCHOR_RULE:",
                        "copy from TEXT",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (root / "review_units.jsonl").write_text(
                json.dumps({"unit_id": "p-00001", "text": "Alpha", "source_hash": source_hash("Wrong")}) + "\n",
                encoding="utf-8",
            )
            (root / "review_blocks.jsonl").write_text(
                json.dumps({"block_id": "part-001", "context_part": "agent_review_context.txt", "unit_ids": ["p-00001"], "unit_count": 1}) + "\n",
                encoding="utf-8",
            )
            (root / "bilingual_pairs.jsonl").write_text("", encoding="utf-8")

            errors = validate_package(manifest)

        self.assertTrue(any("text mismatch for unit p-00001" in error for error in errors))
        self.assertTrue(any("source_hash mismatch for unit p-00001" in error for error in errors))
        self.assertTrue(any("context contains units not present in unit index: p-extra" in error for error in errors))
        self.assertTrue(any("covered_unit_count mismatch" in error for error in errors))
        self.assertTrue(any("block index units do not match context units" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
