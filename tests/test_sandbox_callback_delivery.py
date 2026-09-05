"""Behavioral coverage for the runner's ordered callback delivery boundary."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from app.runtime.sandbox import executor_app as source


def delta(index: int, text: str = "x"):
    return source.ExecutorCallbackEvent(
        session_id="session-a", run_id="run-a", attempt_id="qat-attempt-a",
        callback_token_id="cbt_run-a", batch_id=f"batch-{index}",
        status="running", progress=20, state_patch={"stage": "agent_event"},
        events=[source.AgentEvent(
            type="message.delta", event_id=f"evt_{index}", run_id="run-a",
            message_id="msg-a", payload={"delta": text},
        )],
    )


def barrier(index: int):
    return delta(index).model_copy(update={"events": [], "state_patch": {"stage": "barrier"}})


class CallbackDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.buffers = []
        self.operations = []
        self.patches = []

    def buffer(self, deliver, *, wait=0):
        setting = patch.object(source, "_MESSAGE_DELTA_FLUSH_SECONDS", wait)
        setting.start()
        self.patches.append(setting)
        value = source._MessageDeltaCallbackBuffer(deliver)
        self.buffers.append(value)
        return value

    async def completes(self, operation):
        task = asyncio.create_task(operation)
        self.operations.append(task)
        done, _ = await asyncio.wait({task}, timeout=1)
        self.assertIn(task, done, "operation did not finish without its aggregation timer")
        return await task

    async def asyncTearDown(self):
        # Own all workers, including the worker of a failing pre-fix regression.
        for value in self.buffers:
            value._worker.cancel()
        await asyncio.gather(*(v._worker for v in self.buffers), return_exceptions=True)
        for task in self.operations:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.operations, return_exceptions=True)
        for setting in reversed(self.patches):
            setting.stop()

    async def test_deltas_and_barriers_share_one_delivery_task(self):
        owners = []
        async def deliver(callback):
            owners.append(asyncio.current_task())
            return True
        value = self.buffer(deliver)
        await value.enqueue(delta(1))
        self.assertTrue(await value.send(barrier(2)))
        await value.enqueue(delta(3))
        self.assertTrue(await value.close())
        self.assertEqual(len(owners), 3)
        self.assertEqual(len(set(owners)), 1)

    async def test_barrier_interrupts_coalescing_wait(self):
        batches = []
        async def deliver(callback):
            batches.append(callback.batch_id)
            return True
        value = self.buffer(deliver, wait=60)
        await value.enqueue(delta(1))
        await asyncio.sleep(0)
        self.assertTrue(await self.completes(value.send(barrier(2))))
        self.assertTrue(await value.close())
        self.assertEqual(batches, ["batch-1", "batch-2"])

    async def test_close_interrupts_coalescing_wait(self):
        batches = []
        async def deliver(callback):
            batches.append(callback.batch_id)
            return True
        value = self.buffer(deliver, wait=60)
        await value.enqueue(delta(1))
        await asyncio.sleep(0)
        self.assertTrue(await self.completes(value.close()))
        self.assertEqual(batches, ["batch-1"])
        self.assertFalse(await value.enqueue(delta(2)))

    async def test_cancel_discards_not_started_text_without_waiting_for_timer(self):
        batches = []
        async def deliver(callback):
            batches.append(callback.batch_id)
            return True
        value = self.buffer(deliver, wait=60)
        await value.enqueue(delta(1))
        await asyncio.sleep(0)
        await self.completes(value.cancel())
        self.assertEqual(batches, [])

    async def test_unexpected_delivery_error_propagates_to_sender_and_close(self):
        async def deliver(callback):
            raise RuntimeError("delivery bug")
        value = self.buffer(deliver)
        with self.assertRaisesRegex(RuntimeError, "delivery bug"):
            await value.send(barrier(1))
        with self.assertRaisesRegex(RuntimeError, "delivery bug"):
            await value.close()

    async def test_delivery_task_cancellation_releases_queued_work(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def deliver(callback):
            started.set()
            await release.wait()
            raise asyncio.CancelledError
        value = self.buffer(deliver)
        await value.enqueue(delta(1))
        await asyncio.wait_for(started.wait(), 1)
        await value.enqueue(delta(2))
        release.set()
        self.assertFalse(await self.completes(value.flush()))
        self.assertFalse(await value.enqueue(delta(3)))
