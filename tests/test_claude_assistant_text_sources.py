"""Deterministic source routing tests; no SDK, provider or network dependency."""

import unittest
from app.execution.infrastructure.harness.claude.assistant_text_sources import (
    AssistantTextSourceBuffer,
)


class AssistantTextSourceBufferTests(unittest.TestCase):
    def setUp(self):
        self.router = AssistantTextSourceBuffer()
        self.first = ("provider-1", None)
        self.second = ("provider-2", None)

    def test_complete_blocks_preserve_leading_newlines_without_buffering_text(self):
        self.assertEqual(self.router.append_text(self.first, " First"), " First")
        self.router.take(self.first)
        self.assertEqual(
            self.router.append_text(self.second, "\n\n\n\nSecond"), "\n\n\n\nSecond"
        )
        self.assertTrue(self.router.take(self.second).has_text)
        self.assertIsNone(self.router.message_key)

    def test_same_provider_message_groups_complete_text_blocks(self):
        self.assertEqual(self.router.append_text(self.first, "Checking"), "Checking")
        self.assertEqual(
            self.router.append_text(self.first, "\n\n sources"), "\n\n sources"
        )
        self.assertEqual(self.router.message_key, self.first)
        self.assertEqual(self.router.take(self.first).has_text, True)

    def test_empty_tool_source_retires_its_classification_state(self):
        previous_text = ("provider-answer", None)
        self.router.append_text(previous_text, "Earlier.")
        self.router.mark_answer(previous_text)
        self.router.take(previous_text)
        self.router.begin(self.first)
        self.router.mark_tool(self.first)
        retired = self.router.take(self.first)
        self.assertEqual(retired.role, "work")
        self.assertFalse(retired.has_text)
        self.assertIsNone(self.router.role_for(self.first))
        for index in range(257):
            key = (f"empty-tool-{index}", None)
            self.router.begin(key)
            self.router.mark_tool(key)
            self.router.take(key)
        self.assertEqual(self.router.append_text(self.second, "\n\nFinal"), "\n\nFinal")

    def test_all_attempt_source_roles_survive_without_truncating_new_text(self):
        published = []
        for index in range(160):
            key = (f"provider-{index}", None)
            value = f"answer-{index}"
            published.append(self.router.append_text(key, value))
            self.router.mark_answer(key)
            self.router.take(key)
        self.assertEqual(
            "".join(published), "".join((f"answer-{index}" for index in range(160)))
        )
        self.assertEqual(len(self.router._sources), 160)
        self.assertEqual(self.router.role_for(("provider-0", None)), "answer")
        self.assertTrue(self.router.has_meaningful_answer_sources)

    def test_split_typed_tool_marks_existing_part_work(self):
        self.router.append_text(self.first, "Checking.")
        self.router.mark_tool(self.first)
        retired = self.router.take(self.first)
        self.assertEqual(retired.role, "work")
        self.assertTrue(retired.has_text)

    def test_long_suffix_is_returned_incrementally_without_content_retention(self):
        text = "界" * (256 * 1024 + 8193)
        output = []
        for offset in range(0, len(text), 4093):
            output.append(
                self.router.append_text(self.first, text[offset : offset + 4093])
            )
        self.assertEqual("".join(output), text)
        self.assertFalse(hasattr(self.router, "_chunks"))
        self.assertEqual(self.router.take(self.first).has_text, True)

    def test_real_whitespace_and_first_message_leading_newlines_survive(self):
        first_text = "\n\nIndented"
        self.assertEqual(self.router.append_text(self.first, first_text), first_text)
        self.router.take(self.first)
        self.assertEqual(
            self.router.append_text(self.second, "\n\n\n\nIndented"), "\n\n\n\nIndented"
        )

    def test_work_cannot_be_reclassified_as_answer(self):
        self.router.append_text(self.first, "Checking")
        self.router.mark_tool(self.first)
        with self.assertRaisesRegex(ValueError, "role_conflict"):
            self.router.mark_answer(self.first)
        self.assertEqual(self.router.role_for(self.first), "work")

    def test_meaningful_answer_excludes_whitespace_and_reclassified_work(self):
        self.router.append_text(self.first, " \t\n")
        self.router.mark_answer(self.first)
        self.assertEqual(self.router.role_for(self.first), "answer")
        self.assertFalse(self.router.has_meaningful_answer_sources)
        self.router.append_text(self.first, "Answer.")
        self.assertTrue(self.router.has_meaningful_answer_sources)
        self.router.mark_tool(self.first)
        self.assertFalse(self.router.has_meaningful_answer_sources)
        self.router.take(self.first)

    def test_parent_tool_identity_is_part_of_the_owner(self):
        parent_a = ("shared-provider", "parent-a")
        parent_b = ("shared-provider", "parent-b")
        self.router.begin(parent_a)
        with self.assertRaisesRegex(ValueError, "not_closed"):
            self.router.mark_tool(parent_b)
        self.assertIsNone(self.router.role_for(self.first))
        self.assertEqual(self.router.message_key, parent_a)

    def test_invalid_delta_is_atomic(self):
        with self.assertRaisesRegex(ValueError, "delta_invalid"):
            self.router.append_text(self.first, None)
        self.assertIsNone(self.router.message_key)

    def test_missing_key_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "source_invalid"):
            self.router.begin(None)

    def test_unhashable_key_is_rejected_before_mutation(self):
        with self.assertRaises(TypeError):
            self.router.begin([])
        self.assertIsNone(self.router.message_key)


if __name__ == "__main__":
    unittest.main()
