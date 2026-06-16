import json
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import run_qa_review as qa  # noqa: E402


class AgentLocatorContractV2Tests(unittest.TestCase):
    def test_agent_issue_existing_but_uncovered_unit_id_is_rejected_when_unit_index_is_supplied(self):
        paragraphs = [
            {
                "paragraph_id": "p-00001",
                "text": "The Client sample met the criteria.",
                "logical_index": 1,
                "xml_index": 1,
                "type": "paragraph",
                "location_kind": "paragraph",
                "document_zone": "body",
            },
            {
                "paragraph_id": "p-00002",
                "text": "The Cilent sample met the criteria.",
                "logical_index": 2,
                "xml_index": 2,
                "type": "paragraph",
                "location_kind": "paragraph",
                "document_zone": "body",
            }
        ]
        raw_issue = {
            "category": "en_language",
            "severity": "主要",
            "unit_id": "p-00002",
            "anchor_quote": "Cilent",
            "original": "Cilent",
            "issue": "Typographical error.",
            "suggestion": "Replace `Cilent` with `Client`.",
            "evidence": "The source sentence contains `Cilent`.",
            "confidence": "high",
            "comment_intent": "suggest_change",
        }

        issue = qa.make_llm_issue(1, paragraphs, raw_issue, allowed_unit_ids={"p-00001"})

        self.assertIsNone(issue)

    def test_agent_review_loader_uses_allowed_unit_ids_for_real_merge_path(self):
        paragraphs = [
            {
                "paragraph_id": "p-00001",
                "text": "The Client sample met the criteria.",
                "logical_index": 1,
                "xml_index": 1,
                "type": "paragraph",
                "location_kind": "paragraph",
                "document_zone": "body",
            },
            {
                "paragraph_id": "p-00002",
                "text": "The Cilent sample met the criteria.",
                "logical_index": 2,
                "xml_index": 2,
                "type": "paragraph",
                "location_kind": "paragraph",
                "document_zone": "body",
            },
        ]
        payload = {
            "agent_role": "en_language",
            "issues": [
                {
                    "category": "en_language",
                    "severity": "主要",
                    "unit_id": "p-00002",
                    "anchor_quote": "Cilent",
                    "original": "Cilent",
                    "issue": "Typographical error.",
                    "suggestion": "Replace `Cilent` with `Client`.",
                    "evidence": "The source sentence contains `Cilent`.",
                    "confidence": "high",
                    "comment_intent": "suggest_change",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "agent_review.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            issues, metadata = qa.load_agent_review_issues(
                [str(path)],
                paragraphs,
                1,
                allowed_unit_ids={"p-00001"},
            )

        self.assertEqual(issues, [])
        self.assertEqual(metadata["skip_categories"].get("missing_unit_locator"), 1)

    def test_load_agent_context_unit_ids_reads_manifest_unit_index(self):
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
                        "copy from TEXT",
                        "",
                        "### UNIT p-00002",
                        "TEXT:",
                        "Beta",
                        "ANCHOR_RULE:",
                        "copy from TEXT",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (root / "review_units.jsonl").write_text(
                '{"unit_id":"p-00001","text":"Alpha"}\n{"unit_id":"p-00002","text":"Beta"}\n',
                encoding="utf-8",
            )
            (root / "review_blocks.jsonl").write_text(
                '{"context_part":"agent_review_context.txt","unit_ids":["p-00001","p-00002"],"unit_count":2}\n',
                encoding="utf-8",
            )
            (root / "bilingual_pairs.jsonl").write_text("", encoding="utf-8")

            unit_ids, metadata = qa.load_agent_context_unit_ids(manifest)

        self.assertEqual(unit_ids, {"p-00001", "p-00002"})
        self.assertTrue(metadata["context_manifest_found"])
        self.assertEqual(metadata["context_unit_count"], 2)

    def test_invalid_context_manifest_fails_semantic_branch_and_metadata_survives_gate(self):
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
            (root / "agent_review_context.txt").write_text("### UNIT p-00001\nTEXT:\nBeta\n", encoding="utf-8")
            (root / "review_units.jsonl").write_text('{"unit_id":"p-00001","text":"Alpha"}\n', encoding="utf-8")
            (root / "review_blocks.jsonl").write_text('{"unit_id":"p-00001"}\n', encoding="utf-8")
            (root / "bilingual_pairs.jsonl").write_text("", encoding="utf-8")

            unit_ids, metadata = qa.load_agent_context_unit_ids(manifest)
            entry = qa.build_manifest_entry(
                qa.LLM_REVIEW_BRANCH,
                [],
                1,
                error=str(metadata.get("context_manifest_error") or ""),
                status_override=str(metadata.get("status") or ""),
                branch_details={"agent_context": metadata},
            )
            payload = {
                "issues": [],
                "human_review_queue": [],
                "branch_execution_manifest": [
                    qa.build_manifest_entry("format", [], 1),
                    qa.build_manifest_entry("project_number", [], 1),
                    qa.build_manifest_entry(qa.CONTENT_CONSISTENCY_BRANCH, [], 1),
                    entry,
                ],
            }
            review_json = root / "review_result.json"
            validation_json = root / "validation_report.json"
            review_json.write_text(json.dumps(payload), encoding="utf-8")
            validation = qa.validate_pipeline(review_json, validation_json)

        self.assertEqual(unit_ids, set())
        self.assertFalse(metadata["context_manifest_valid"])
        self.assertEqual(metadata["status"], "failed")
        self.assertIn("context package validation failed", metadata["context_manifest_error"])
        self.assertEqual(entry["status"], "failed")
        self.assertIn("agent_context", entry)
        self.assertFalse(validation["passed"])
        self.assertTrue(any("failed branches detected: llm_full_review" in error for error in validation["errors"]))

    def test_missing_required_context_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "agent_context_manifest.json"

            unit_ids, metadata = qa.load_agent_context_unit_ids(missing, require_manifest=True)

        self.assertEqual(unit_ids, set())
        self.assertFalse(metadata["context_manifest_found"])
        self.assertFalse(metadata["context_manifest_valid"])
        self.assertEqual(metadata["status"], "failed")
        self.assertIn("not found", metadata["context_manifest_error"])

    def test_missing_default_context_manifest_uses_documented_legacy_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "agent_context_manifest.json"

            unit_ids, metadata = qa.load_agent_context_unit_ids(missing)

        self.assertIsNone(unit_ids)
        self.assertFalse(metadata["context_manifest_found"])
        self.assertEqual(metadata["context_manifest_mode"], "legacy_no_context_manifest")
        self.assertTrue(metadata["agent_merge_unrestricted_for_legacy_context"])

    def test_prompt_template_v2_uses_manifest_unit_sections_not_legacy_line_heads(self):
        prompt_text = (SKILL_DIR / "references" / "prompt_templates.md").read_text(encoding="utf-8")

        self.assertIn("context_parts", prompt_text)
        self.assertIn("### UNIT <unit_id>", prompt_text)
        self.assertIn("TEXT", prompt_text)
        self.assertIn("不是 v2 主定位来源", prompt_text)
        self.assertNotIn("必须直接来自 `agent_review_context.txt` 的 `[u:<unit_id> | ...]` 行头", prompt_text)


if __name__ == "__main__":
    unittest.main()
