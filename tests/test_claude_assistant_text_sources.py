"""Native delta/completed-block reconciliation; no provider or network."""

import unittest

from app.execution.infrastructure.harness.claude.assistant_text_sources import AssistantTextSourceBuffer


class AssistantTextSourceBufferTests(unittest.TestCase):
    def setUp(self):
        self.router = AssistantTextSourceBuffer()
        self.ordinal = 0

    def event(self, kind, **fields):
        self.ordinal += 1
        return self.router.accept_event(uuid=f"frame-{self.ordinal}", event={"type": kind, **fields})

    def start(self, identity="provider-1", index=0, kind="text"):
        self.event("message_start", message={"id": identity})
        self.event("content_block_start", index=index, content_block={"type": kind})

    def test_native_deltas_publish_before_complete_block_without_repeating_it(self):
        self.start()
        self.assertEqual(self.event("content_block_delta", index=0, delta={"type": "text_delta", "text": "Hello "}), (("provider-1", None), "Hello "))
        self.assertEqual(self.event("content_block_delta", index=0, delta={"type": "text_delta", "text": "world"}), (("provider-1", None), "world"))
        self.assertEqual(self.router.completed_text(("provider-1", None), "Hello world"), "")
        self.event("content_block_stop", index=0)
        self.event("message_stop")
        self.assertIsNone(self.router.message_key)
        self.assertNotIn("Hello", str(self.router.__dict__))

    def test_text_tool_text_blocks_stay_independent(self):
        self.start()
        self.event("content_block_delta", index=0, delta={"type": "text_delta", "text": "A"})
        self.router.completed_text(("provider-1", None), "A")
        self.event("content_block_stop", index=0)
        self.event("content_block_start", index=1, content_block={"type": "tool_use"})
        self.assertIsNone(self.event("content_block_delta", index=1, delta={"type": "input_json_delta", "partial_json": "PRIVATE"}))
        self.event("content_block_stop", index=1)
        self.event("content_block_start", index=2, content_block={"type": "text"})
        self.assertEqual(self.event("content_block_delta", index=2, delta={"type": "text_delta", "text": "A"})[1], "A")
        self.assertEqual(self.router.completed_text(("provider-1", None), "A"), "")

    def test_exact_frame_replay_is_ignored_and_conflicting_uuid_is_rejected(self):
        self.start()
        event = {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "A"}}
        self.assertIsNotNone(self.router.accept_event(uuid="stable", event=event))
        self.assertIsNone(self.router.accept_event(uuid="stable", event=event))
        with self.assertRaisesRegex(ValueError, "observation_invalid"):
            self.router.accept_event(uuid="stable", event={**event, "index": 1})

    def test_completed_only_text_preserves_whitespace_and_large_body(self):
        text = "\n\n" + "界" * (262_144 + 8_193)
        self.assertEqual(self.router.completed_text(("provider", None), text), text)

    def test_completed_body_conflict_is_not_appended_as_a_second_answer(self):
        self.start()
        self.event("content_block_delta", index=0, delta={"type": "text_delta", "text": "Prefix"})
        with self.assertRaisesRegex(ValueError, "text_conflict"):
            self.router.completed_text(("provider-1", None), "Different")

    def test_completed_block_rejects_late_duplicate_before_it_can_publish(self):
        self.start()
        self.event("content_block_delta", index=0, delta={"type": "text_delta", "text": "A"})
        self.assertEqual(self.router.completed_text(("provider-1", None), "A"), "")
        with self.assertRaisesRegex(ValueError, "text_conflict"):
            self.event("content_block_delta", index=0, delta={"type": "text_delta", "text": "A"})

    def test_typed_prefix_completion_allows_only_its_matching_remaining_delta(self):
        self.start()
        self.event("content_block_delta", index=0, delta={"type": "text_delta", "text": "A"})
        self.assertEqual(self.router.completed_text(("provider-1", None), "AB"), "")
        self.assertEqual(self.event("content_block_delta", index=0, delta={"type": "text_delta", "text": "B"})[1], "B")
        self.event("content_block_stop", index=0)

    def test_orphan_text_and_overlapping_blocks_are_rejected(self):
        with self.assertRaises(ValueError):
            self.event("content_block_delta", index=0, delta={"type": "text_delta", "text": "orphan"})
        self.start()
        with self.assertRaises(ValueError):
            self.event("content_block_start", index=1, content_block={"type": "text"})

    def test_reasoning_and_tool_json_never_become_prose(self):
        self.start(kind="thinking")
        self.assertIsNone(self.event("content_block_delta", index=0, delta={"type": "thinking_delta", "thinking": "PRIVATE"}))
        with self.assertRaises(ValueError):
            self.event("content_block_delta", index=0, delta={"type": "text_delta", "text": "PRIVATE"})
