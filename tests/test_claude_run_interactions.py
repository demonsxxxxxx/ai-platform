"""Same-client Run continuation and native question cancellation contracts."""

import asyncio
from builtins import anext
import hashlib
import json
import os
import sys
from types import SimpleNamespace

import pytest

from app.execution.application.run_interaction import RunInputCommand, RunInputSnapshot
from app.execution.infrastructure.harness.claude.interaction import ClaudeRunInteractionActor
from app.executors.claude_agent_sdk_runner import run_claude_agent_sdk
from tests.test_claude_agent_sdk_runner import _fake_sdk, _settings
from tests.support.mcp_protocol_peers import local_mcp_peers
from tests.support.model_text import response_events


class Inputs:
    def __init__(self, commands=()):
        self.commands = list(commands)
        self.acks = []
        self.questions = []
        self.answer = None
        self.question_ready = asyncio.Event()

    async def open(self):
        return RunInputSnapshot("open")

    async def settle(self):
        return RunInputSnapshot("open", (self.commands[0],)) if self.commands else RunInputSnapshot("sealed")

    async def acknowledge(self, *, input_ids):
        self.acks.extend(input_ids)
        self.commands = [item for item in self.commands if item.input_id not in input_ids]
        return RunInputSnapshot("open")

    async def publish_question(self, *, question_id, questions):
        self.questions.append((question_id, questions))
        self.question_ready.set()
        return RunInputSnapshot("open", questions=tuple(questions))

    async def poll(self, *, question_id=None):
        return RunInputSnapshot("open", (self.answer,) if self.answer else ())


async def test_continuations_use_one_client_and_session_then_finish_input(monkeypatch, tmp_path):
    captured = {}
    sdk = _fake_sdk(captured, hook_invocations=[])
    port = Inputs([RunInputCommand("one", "text", text="second"), RunInputCommand("two", "text", text="last")])
    clients, queries, checkpoints = [], [], []

    class Client:
        def __init__(self, options):
            clients.append(self)
            self.stream = None
            self.input_done = asyncio.Event()
            self.initial = None

        async def connect(self, stream):
            self.stream = stream
            self.initial = await anext(stream)

        async def query(self, prompt, session_id):
            queries.append((prompt, session_id))

        async def receive_messages(self):
            for index, text in enumerate(["first", "second", "last"]):
                events = response_events(f"turn-{index}", [text])
                for event in events[:3]:
                    yield sdk.StreamEvent(event)
                yield sdk.AssistantMessage([sdk.TextBlock(text)], message_id=f"turn-{index}")
                for event in events[3:]:
                    yield sdk.StreamEvent(event)
                result = sdk.ResultMessage()
                result.result = text
                result.uuid = f"result-{index}"
                result.usage = {"input_tokens": 10, "output_tokens": 2}
                yield result
            with pytest.raises(StopAsyncIteration):
                await anext(self.stream)
            self.input_done.set()

        async def disconnect(self):
            await self.stream.aclose()

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)
    result = await run_claude_agent_sdk(
        prompt="first", cwd=tmp_path, skill_id=None, run_id="run", attempt_id="attempt",
        interaction_client=port, client_fn=Client, on_sdk_text=checkpoints.append,
    )
    assert result.error is None
    assert len(clients) == 1 and clients[0].input_done.is_set()
    assert clients[0].initial["message"]["content"] == "first"
    assert queries == [("second", "sdk-session"), ("last", "sdk-session")]
    assert port.acks == ["one", "two"]
    assert result.usage == {"input_tokens": 30, "output_tokens": 6}
    assert result.message.endswith("last")
    assert "AskUserQuestion" in captured["tools"]
    finals = [item for item in checkpoints if item["final"]]
    assert len(checkpoints) == 6 and len(finals) == 3
    assert len({item["call_ref"] for item in finals}) == 3
    for checkpoint, text in zip(finals, ["first", "second", "last"], strict=True):
        assert checkpoint["events"] == 1 and checkpoint["chars"] == len(text)
        assert checkpoint["sha256"] == hashlib.sha256(text.encode()).hexdigest()
        assert checkpoint["complete"] is True and checkpoint["coverage"] == "text_delta"


async def test_native_question_is_one_pending_batch_until_answered():
    port = Inputs()
    actor = ClaudeRunInteractionActor(port, run_id="run", attempt_id="attempt")
    await actor.open()
    tool_input = {"questions": [{
        "question": "Which sections?", "header": "Sections", "multiSelect": True,
        "options": [{"label": "Intro", "description": "Opening"}, {"label": "Results", "description": "Findings"}],
    }]}
    task = asyncio.create_task(actor.resolve_native_question(tool_call_id="call", tool_input=tool_input))
    await port.question_ready.wait()
    assert not task.done()
    question_id, questions = port.questions[0]
    port.answer = RunInputCommand("answer", "answer", question_id=question_id, answers={"q0": ["o0", "o1"]})
    try:
        result = await asyncio.wait_for(task, 1)
        assert result == {"questions": tool_input["questions"], "answers": {"Which sections?": ["Intro", "Results"]}}
        assert await actor.resolve_native_question(tool_call_id="call", tool_input=tool_input) == result
        assert len(port.questions) == 1 and port.acks == []
        await actor.acknowledge_native_question("call")
        assert port.acks == ["answer"]
    finally:
        await actor.cancel()


async def test_repeated_question_close_preserves_interrupted_waiter_finalizers():
    port = Inputs()
    polling, finalizing, release, finalized = (
        asyncio.Event(), asyncio.Event(), asyncio.Event(), asyncio.Event()
    )

    async def poll(**_kwargs):
        polling.set()
        try:
            await asyncio.Event().wait()
        finally:
            finalizing.set()
            await release.wait()
            finalized.set()

    port.poll = poll
    actor = ClaudeRunInteractionActor(port, run_id="run", attempt_id="attempt")
    question = asyncio.create_task(actor.resolve_native_question(
        tool_call_id="question", tool_input={"questions": [{
            "question": "Proceed?", "header": "Next", "options": [], "multiSelect": False,
        }]},
    ))
    first = second = None
    try:
        await polling.wait()
        first = asyncio.create_task(actor.cancel())
        await finalizing.wait()
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        second = asyncio.create_task(actor.cancel())
        await asyncio.sleep(0)
        assert not second.done() and not finalized.is_set()
        release.set()
        await asyncio.wait_for(second, 1)
        assert finalized.is_set()
    finally:
        release.set()
        await asyncio.gather(
            question, *(task for task in (first, second) if task is not None),
            return_exceptions=True,
        )


async def test_uncertain_ack_never_replays_accepted_query():
    port = Inputs()
    attempts = 0

    class LostReceipt(RuntimeError):
        retryable = True

    async def acknowledge(**_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise LostReceipt()
        return RunInputSnapshot("open")

    port.acknowledge = acknowledge
    queries = []

    class Client:
        async def query(self, prompt, session_id):
            queries.append((prompt, session_id))

    actor = ClaudeRunInteractionActor(port, run_id="run", attempt_id="attempt")
    await actor.apply_text_at_result(Client(), RunInputCommand("input", "text", text="continue"), session_id="native-session")
    assert attempts == 2 and queries == [("continue", "native-session")]


async def test_stop_interrupts_native_client_and_releases_question_waiter(monkeypatch, tmp_path):
    captured = {}
    sdk = _fake_sdk(captured, hook_invocations=[])
    port = Inputs()
    interrupted, disconnected = asyncio.Event(), asyncio.Event()

    class Client:
        def __init__(self, _options):
            self.stream = None

        async def connect(self, stream):
            self.stream = stream
            await anext(stream)

        async def receive_messages(self):
            hook = captured["hooks"]["PreToolUse"][0].hooks[0]
            await hook({"tool_name": "AskUserQuestion", "tool_use_id": "question-call", "tool_input": {
                "questions": [{"question": "Proceed?", "header": "Next", "options": [], "multiSelect": False}],
            }}, "question-call", {})
            yield sdk.ResultMessage()

        async def interrupt(self):
            interrupted.set()

        async def disconnect(self):
            await self.stream.aclose()
            disconnected.set()

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)
    task = asyncio.create_task(run_claude_agent_sdk(
        prompt="ask", cwd=tmp_path, skill_id=None, run_id="run", attempt_id="attempt",
        interaction_client=port, client_fn=Client,
    ))
    await asyncio.wait_for(port.question_ready.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert interrupted.is_set() and disconnected.is_set()


@pytest.mark.parametrize("private_labels", [False, True])
async def test_installed_cli_waits_for_native_answer_and_continues_in_one_session(
    monkeypatch, tmp_path, private_labels,
):
    from app.executors import claude_agent_sdk_runner as runner

    port = Inputs()
    task = None
    with local_mcp_peers() as peer:
        peer.sdk_tool = "AskUserQuestion"
        peer.tool_input = {"questions": [{
            "question": "Read /app/private?" if private_labels else "Which sections?",
            "header": ".claude/private" if private_labels else "Sections", "multiSelect": True,
            "options": [{"label": "Intro /app/private" if private_labels else "Intro", "description": "Opening"},
                        {"label": "Results /tmp/private" if private_labels else "Results", "description": "Findings"}],
        }]}
        settings = SimpleNamespace(
            claude_agent_sdk_enabled=True, claude_agent_sdk_max_turns=4,
            claude_agent_sdk_timeout_seconds=20, claude_agent_sdk_skills="",
            claude_agent_permission_mode="dontAsk", claude_agent_model="claude-sonnet-4-6",
            anthropic_model="", anthropic_base_url=peer.url,
            anthropic_auth_token="synthetic-model-token", openai_api_key="",
        )
        monkeypatch.setattr(runner, "get_settings", lambda: settings)
        real_env = runner.build_sdk_env

        def local_env(*, cwd, model_max_output_tokens=None):
            env = real_env(cwd=cwd, model_max_output_tokens=model_max_output_tokens)
            env.update({"ANTHROPIC_BASE_URL": peer.url, "ANTHROPIC_AUTH_TOKEN": "synthetic-model-token",
                        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "NO_PROXY": "127.0.0.1,localhost"})
            for name in ("TEMP", "TMP", "APPDATA", "LOCALAPPDATA"):
                if name in os.environ:
                    env[name] = os.environ[name]
            return env

        monkeypatch.setattr(runner, "build_sdk_env", local_env)
        try:
            task = asyncio.create_task(run_claude_agent_sdk(
                prompt="Ask which sections to include, then complete the summary.",
                cwd=tmp_path, skill_id=None, run_id="run", attempt_id="attempt",
                interaction_client=port,
            ))
            await asyncio.wait_for(port.question_ready.wait(), 10)
            assert not task.done() and len(peer.models) == 1
            assert "AskUserQuestion" in {item["name"] for item in peer.models[0]["tools"]}
            question_id = port.questions[0][0]
            if private_labels:
                public = port.questions[0][1]
                assert "/app/" not in str(public) and "/tmp/" not in str(public) and ".claude/" not in str(public)
                assert [item["label"] for item in public[0]["options"]] == ["Option 1", "Option 2"]
            port.commands.append(RunInputCommand("continue", "text", text="Also add a Chinese summary"))
            port.answer = RunInputCommand("answer", "answer", question_id=question_id,
                                          answers={"q0": ["o0", "o1"]})
            result = await asyncio.wait_for(task, 10)
            assert result.error is None, result.runtime_diagnostics
            assert port.acks == ["answer", "continue"]
            assert len(peer.models) == 3
            tool_results = [block for message in peer.models[1]["messages"]
                            for block in message["content"] if block.get("type") == "tool_result"]
            native_answer = json.dumps(tool_results)
            assert "Intro" in native_answer and "Results" in native_answer
            if private_labels:
                assert "Intro /app/private" in native_answer and "Results /tmp/private" in native_answer
            assert not any(block.get("is_error") for block in tool_results)
            assert any("Also add a Chinese summary" in json.dumps(message)
                       for message in peer.models[2]["messages"])
            assert result.usage["input_tokens"] == 30 and result.usage["output_tokens"] == 60
        finally:
            if task is not None and not task.done():
                task.cancel()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("multi", [False, True])
async def test_native_question_uses_original_local_identity_after_redaction_collision(multi):
    port = Inputs()
    actor = ClaudeRunInteractionActor(port, run_id="run", attempt_id="attempt")
    questions = [{"question": f"Send to {email}?", "header": "Contact", "multiSelect": multi,
                  "options": [{"label": "alice@example.test", "description": ""},
                              {"label": "bob@example.test", "description": ""}]}
                 for email in ("alice@example.test", "bob@example.test")]
    task = asyncio.create_task(actor.resolve_native_question(tool_call_id="call", tool_input={"questions": questions}))
    try:
        await port.question_ready.wait()
        question_id, public = port.questions[0]
        assert "example.test" not in str(public)
        port.answer = RunInputCommand("answer", "answer", question_id=question_id,
                                     answers={"q0": ["o1", "o0"] if multi else "o1", "q1": {"text": "o0"}})
        result = await asyncio.wait_for(task, 1)
        assert result["questions"] == questions
        assert result["answers"] == {questions[0]["question"]: ["bob@example.test", "alice@example.test"] if multi else "bob@example.test",
                                      questions[1]["question"]: "o0"}
    finally:
        await actor.close()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("marker", ["/app/private", "/tmp/private", ".claude/skills/private"])
async def test_native_question_filters_private_paths_without_changing_execution_identity(marker):
    port = Inputs()
    actor = ClaudeRunInteractionActor(port, run_id="run", attempt_id="attempt")
    questions = [{"question": f"Read {marker}?", "header": marker, "multiSelect": False,
                  "options": [{"label": f"{marker}/first", "description": marker},
                              {"label": f"{marker}/second", "description": marker}]}]
    task = asyncio.create_task(actor.resolve_native_question(tool_call_id="call", tool_input={"questions": questions}))
    try:
        await port.question_ready.wait()
        question_id, public = port.questions[0]
        assert marker not in str(public)
        assert public[0]["question"] == "Question 1"
        assert [item["label"] for item in public[0]["options"]] == ["Option 1", "Option 2"]
        port.answer = RunInputCommand("answer", "answer", question_id=question_id, answers={"q0": "o1"})
        result = await asyncio.wait_for(task, 1)
        assert result == {"questions": questions, "answers": {questions[0]["question"]: f"{marker}/second"}}
    finally:
        await actor.close()
        await asyncio.gather(task, return_exceptions=True)
