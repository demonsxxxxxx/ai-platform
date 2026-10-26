"""Real bundled Claude Code CLI lifecycle: two turns, stop, hard stop, resume.

The harness under ``tests/support/lifecycle_cli_harness.py`` spawns the real
bundled CLI for every round and hands it the production
``ClaudeSessionStoreAdapter``.  This file asserts the platform-relevant
invariants of that behaviour and writes a machine-readable evidence record.

Substitutions, and nothing else: the model wire
(``tests/support/lifecycle_model_peer.py``) and the MCP tool handler.
``connect``, resume materialization, ``interrupt``, subprocess teardown and the
provider-session callback wire all run as production code.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from tests.support.lifecycle_cli_harness import (
    CLI_VERSION,
    SDK_VERSION,
    SYNTHETIC_CREDENTIAL,
    CliRound,
    Control,
    new_session_id,
    run_round,
)
from tests.support.lifecycle_model_peer import Turn, synthetic_model_peer
from tests.support.provider_session_callback_peer import (
    DurableEntryStore,
    provider_session_callback_peer,
)

# Text the CLI synthesizes itself when a turn ends before a tool result exists.
# The platform must never author either notice.
CLI_TOOL_OUTCOME_UNKNOWN = "outcome is unknown"
CLI_TOOL_REJECTED = "the user doesn't want to proceed"
# The adapter's own code for any non-2xx callback response.
CALLBACK_REJECTED = "provider_session_callback_rejected"


@pytest.fixture
def cli_home(monkeypatch):
    """Short-path CLI home; the CLI copies transcripts under a nested temp root."""

    for name in tuple(os.environ):
        if name.startswith(("ANTHROPIC_", "CLAUDE_", "OTEL_")) or name.upper() in {
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        }:
            monkeypatch.delenv(name, raising=False)
    root = Path(tempfile.mkdtemp(prefix="lc-"))
    (root / "config").mkdir()
    (root / "work").mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


class Recorder:
    """Collect the rounds of one scenario and write a secret-free record."""

    def __init__(self, tmp_path: Path):
        self._path = tmp_path / "lifecycle-cli-evidence.json"
        self.rounds: list[CliRound] = []

    def add(self, round_outcome: CliRound) -> CliRound:
        self.rounds.append(round_outcome)
        return round_outcome

    def write(self, *, scenario: str, conditions: dict[str, str], callback_peer) -> dict:
        record = {
            "scenario": scenario,
            "sdk_version": SDK_VERSION,
            "cli_version": CLI_VERSION,
            "native_session_id": self.rounds[0].session_id if self.rounds else None,
            "rounds": [round_outcome.as_evidence() for round_outcome in self.rounds],
            "store": callback_peer.summary(),
            "conditions": conditions,
        }
        self._path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        return record


def _persisted(callback_peer, session_id: str) -> list[dict]:
    return callback_peer.entries(session_id, None)


def _assistant_blocks(entries: list[dict]) -> list[dict]:
    return [
        block
        for entry in entries
        if entry.get("type") == "assistant"
        for block in ((entry.get("message") or {}).get("content") or [])
        if isinstance(block, dict)
    ]


def _assert_no_credential(entries: list[dict]) -> None:
    assert SYNTHETIC_CREDENTIAL not in json.dumps(entries)


def _assert_result_success(round_outcome: CliRound) -> None:
    assert round_outcome.connect_error is None, round_outcome.connect_error
    # A resumed round can emit more than one result frame; every one must be a success.
    assert round_outcome.result_subtypes and all(
        item == ("success", False) for item in round_outcome.result_subtypes
    ), (
        round_outcome.result_subtypes,
        round_outcome.message_types,
        round_outcome.stream_error,
    )


async def test_two_ordinary_turns_continue_one_native_session(cli_home, tmp_path):
    """A second Round resumes the same native session with only this turn's text."""

    recorder = Recorder(tmp_path)
    store = DurableEntryStore(tmp_path / "native")
    session_id = new_session_id()
    script = [Turn.text("first answer", delay=0.0), Turn.text("second answer", delay=0.0)]

    with provider_session_callback_peer(store) as callback_peer, \
            synthetic_model_peer(script) as model_peer:
        first = recorder.add(await run_round(
            root=cli_home, model_peer=model_peer, callback_peer=callback_peer,
            label="round-1", prompt="first question", session_id=session_id,
        ))
        _assert_result_success(first)
        assert first.assistant_text == "first answer"
        assert first.session_id == session_id, "the CLI must keep the platform session id"

        second = recorder.add(await run_round(
            root=cli_home, model_peer=model_peer, callback_peer=callback_peer,
            label="round-2", prompt="second question", session_id=session_id, resume=True,
        ))
        _assert_result_success(second)
        assert second.assistant_text == "second answer"

        entries = _persisted(callback_peer, session_id)
        # Only the CLI writes native entries; the harness never appends its own.
        assert [entry.get("type") for entry in entries].count("assistant") == 2
        assert second.accepted_final_sequence > first.accepted_final_sequence
        wire = second.wire_requests
        assert len(wire) == 1, wire
        assert wire[0]["turn_text"] == "second question"
        assert "first question" in wire[0]["prior_user_turns"]
        assert "first answer" in wire[0]["prior_assistant_text"]
        # No tool ran, so no tool_result may appear in the resumed history.
        assert wire[0]["tool_result_ids"] == []
        _assert_no_credential(entries)
        record = recorder.write(
            scenario="ordinary-two-turns",
            conditions={"platform_fabricated_history": "none", "resume_kind": "explicit_id"},
            callback_peer=callback_peer,
        )

    assert record["rounds"][1]["wire_requests"][0]["prior_user_turns"][0] == "first question"
    assert record["rounds"][0]["session_id"] == record["rounds"][1]["session_id"]


async def test_subagent_entries_are_persisted_under_a_subpath_and_resumed(cli_home, tmp_path):
    """A child agent transcript is mirrored under a subpath, not merged into the parent."""

    recorder = Recorder(tmp_path)
    store = DurableEntryStore(tmp_path / "native")
    session_id = new_session_id()
    script = [
        Turn.tool("Agent", description="child", prompt="answer with child-done",
                  subagent_type="general-purpose"),
        Turn.text("child-done", delay=0.0),
        Turn.text("parent answer", delay=0.0),
    ]

    with provider_session_callback_peer(store) as callback_peer, \
            synthetic_model_peer(script) as model_peer:
        first = recorder.add(await run_round(
            root=cli_home, model_peer=model_peer, callback_peer=callback_peer,
            label="child-round", prompt="delegate to a child agent",
            session_id=session_id, subagents=True,
        ))
        _assert_result_success(first)
        # A completed background child can cause another native notification turn.
        assert any(
            request["turn_text"] == "answer with child-done"
            for request in first.wire_requests
        ), first.wire_requests
        assert any(
            "toolu_synthetic_0" in request["tool_result_ids"]
            for request in first.wire_requests
        ), first.wire_requests

        subpaths = callback_peer.subpaths(session_id)
        assert subpaths, "a child transcript must be mirrored under a subpath"
        for subpath in subpaths:
            assert subpath.startswith("subagents/")
            assert callback_peer.entries(session_id, subpath)
        main_entries = _persisted(callback_peer, session_id)
        assert main_entries
        child_entries = {
            subpath: callback_peer.entries(session_id, subpath) for subpath in subpaths
        }
        assert any(
            block.get("text") == "child-done"
            for entries in child_entries.values()
            for block in _assistant_blocks(entries)
        ), child_entries
        requests_before_resume = len(callback_peer.requests)

        resumed = recorder.add(await run_round(
            root=cli_home, model_peer=model_peer, callback_peer=callback_peer,
            label="child-resume", prompt="next question", session_id=session_id,
            resume=True, subagents=True,
        ))
        _assert_result_success(resumed)
        assert resumed.wire_requests[0]["turn_text"] == "next question"
        assert resumed.wire_requests[0]["prior_user_turns"][0] == "delegate to a child agent"
        resume_requests = callback_peer.requests[requests_before_resume:]
        assert any(request["action"] == "list_subkeys" for request in resume_requests)
        assert {request["subpath"] for request in resume_requests
                if request["action"] == "load" and request.get("subpath")} == set(subpaths)
        for subpath, entries in child_entries.items():
            assert callback_peer.entries(session_id, subpath) == entries
        _assert_no_credential(_persisted(callback_peer, session_id) + [
            entry for entries in child_entries.values() for entry in entries
        ])
        record = recorder.write(
            scenario="subagent-subpath",
            conditions={"child_transcript": "persisted_subpath", "resume_kind": "explicit_id"},
            callback_peer=callback_peer,
        )

    assert record["store"]["subpaths"], "evidence must record the child subpath"


async def test_first_turn_text_interrupt_persists_the_partial_prefix(cli_home, tmp_path):
    """Stop during the first text stream keeps a resumable native prefix."""

    recorder = Recorder(tmp_path)
    store = DurableEntryStore(tmp_path / "native")
    session_id = new_session_id()
    partial = tuple(f"partial-{index} " for index in range(120))

    with provider_session_callback_peer(store) as callback_peer, \
            synthetic_model_peer([Turn.text(*partial, delay=0.05)]) as model_peer:
        first = recorder.add(await run_round(
            root=cli_home, model_peer=model_peer, callback_peer=callback_peer,
            label="text-stop", prompt="interrupt me", session_id=session_id,
            control=Control(interrupt_after_text_deltas=5),
        ))
        assert first.connect_error is None
        assert [item.phase for item in first.interactions if item.action == "interrupt"] == [
            "text_streaming"
        ]
        entries = _persisted(callback_peer, session_id)
        assert any(entry.get("type") == "assistant" for entry in entries), (
            "an interrupted first turn must still persist its assistant prefix"
        )
        partial_persisted = "".join(
            block.get("text") or "" for block in _assistant_blocks(entries)
        )
        assert partial_persisted.startswith("partial-0 "), partial_persisted

        resumed = recorder.add(await run_round(
            root=cli_home, model_peer=model_peer, callback_peer=callback_peer,
            label="text-resume", prompt="continue after stop", session_id=session_id,
            resume=True,
        ))
        _assert_result_success(resumed)
        wire = resumed.wire_requests[0]
        assert wire["turn_text"] == "continue after stop"
        assert "interrupt me" in wire["prior_user_turns"]
        assert "partial-0 " in wire["prior_assistant_text"]
        assert wire["tool_result_ids"] == [], "no tool_result may be invented"
        _assert_no_credential(_persisted(callback_peer, session_id))
        record = recorder.write(
            scenario="first-turn-text-interrupt",
            conditions={
                "platform_fabricated_history": "none",
                "stop_kind": "sdk_interrupt_during_text",
                "resume_kind": "explicit_id",
            },
            callback_peer=callback_peer,
        )

    assert record["rounds"][0]["result_subtypes"] in ([], [["error_during_execution", True]])


async def test_first_turn_tool_interrupt_records_the_cli_rejection_fact(cli_home, tmp_path):
    """Stop while a tool runs; only the CLI may record the unresolved tool call."""

    recorder = Recorder(tmp_path)
    store = DurableEntryStore(tmp_path / "native")
    session_id = new_session_id()

    with provider_session_callback_peer(store) as callback_peer, \
            synthetic_model_peer([Turn.tool()]) as model_peer:
        first = recorder.add(await run_round(
            root=cli_home, model_peer=model_peer, callback_peer=callback_peer,
            label="tool-stop", prompt="use the blocking tool", session_id=session_id,
            control=Control(interrupt_when_tool_started=True),
        ))
        assert first.connect_error is None
        assert first.tool_calls == ["synthetic"], "the blocking tool must have started"
        assert [item.phase for item in first.interactions if item.action == "interrupt"] == [
            "tool_running"
        ]
        entries = _persisted(callback_peer, session_id)
        assert any(entry.get("type") == "assistant" for entry in entries)
        _assert_no_credential(entries)

        resumed = recorder.add(await run_round(
            root=cli_home, model_peer=model_peer, callback_peer=callback_peer,
            label="tool-resume", prompt="continue after the stop", session_id=session_id,
            resume=True,
        ))
        _assert_result_success(resumed)
        wire = resumed.wire_requests[0]
        assert wire["tool_result_ids"] == ["toolu_synthetic_0"]
        assert wire["tool_result_is_error"] == [True]
        # The rejection notice is the CLI's own fact; the platform supplies none.
        assert CLI_TOOL_REJECTED in wire["tool_result_texts"][0].lower()
        assert wire["turn_text"] == "continue after the stop"
        record = recorder.write(
            scenario="first-turn-tool-interrupt",
            conditions={
                "platform_fabricated_tool_result": "none",
                "stop_kind": "sdk_interrupt_during_tool",
                "resume_kind": "explicit_id",
            },
            callback_peer=callback_peer,
        )

    assert record["rounds"][1]["wire_requests"][0]["tool_result_is_error"] == [True]


async def test_hard_stop_without_result_persists_a_prefix_recoverable_from_the_same_candidate(
    cli_home, tmp_path,
):
    """SIGKILL during a tool call: no terminal frame, prefix survives, resume works."""

    recorder = Recorder(tmp_path)
    store = DurableEntryStore(tmp_path / "native")
    session_id = new_session_id()

    with provider_session_callback_peer(store) as callback_peer, \
            synthetic_model_peer([Turn.tool()]) as model_peer:
        killed = recorder.add(await run_round(
            root=cli_home, model_peer=model_peer, callback_peer=callback_peer,
            label="hard-stop", prompt="use the blocking tool", session_id=session_id,
            control=Control(kill_when_tool_started=True),
        ))
        assert killed.connect_error is None
        assert killed.produced_result is False, "a hard stop must not invent a terminal frame"
        assert [item.action for item in killed.interactions if item.action == "kill"] == ["kill"]
        prefix = _persisted(callback_peer, session_id)
        persisted_tool_uses = [
            block for block in _assistant_blocks(prefix) if block.get("type") == "tool_use"
        ]
        assert persisted_tool_uses, "the assistant tool_use must be persisted before the kill"
        assert killed.accepted_final_sequence == len(prefix)
        _assert_no_credential(prefix)

        recovered = recorder.add(await run_round(
            root=cli_home, model_peer=model_peer, callback_peer=callback_peer,
            label="hard-stop-recovery", prompt="continue after the crash",
            session_id=session_id, resume=True,
        ))
        _assert_result_success(recovered)
        wire = recovered.wire_requests[0]
        assert wire["tool_result_ids"] == [persisted_tool_uses[0]["id"]]
        assert wire["tool_result_is_error"] == [True]
        assert CLI_TOOL_OUTCOME_UNKNOWN in wire["tool_result_texts"][0].lower(), (
            "the CLI must mark the unresolved call as unknown, not the platform"
        )
        _assert_no_credential(_persisted(callback_peer, session_id))
        record = recorder.write(
            scenario="hard-stop-no-result",
            conditions={
                "recovery_class": "recovery_candidate",
                "terminal_frame": "absent",
                "resume_kind": "explicit_id_from_persisted_prefix",
            },
            callback_peer=callback_peer,
        )

    assert record["rounds"][0]["result_subtypes"] == []


async def test_failed_resume_attempt_leaves_the_candidate_intact_for_a_retry(cli_home, tmp_path):
    """A store outage during resume rejects the attempt without mutating history."""

    recorder = Recorder(tmp_path)
    store = DurableEntryStore(tmp_path / "native")
    session_id = new_session_id()

    with provider_session_callback_peer(store) as callback_peer, \
            synthetic_model_peer([Turn.text("baseline answer", delay=0.0)]) as model_peer:
        baseline = recorder.add(await run_round(
            root=cli_home, model_peer=model_peer, callback_peer=callback_peer,
            label="baseline", prompt="baseline question", session_id=session_id,
        ))
        _assert_result_success(baseline)
        candidate = _persisted(callback_peer, session_id)
        candidate_bytes = callback_peer.entries_path.read_bytes()

        rejected = recorder.add(await run_round(
            root=cli_home, model_peer=model_peer, callback_peer=callback_peer,
            label="rejected", prompt="retry question", session_id=session_id,
            resume=True, fail_preload=True,
        ))
        assert rejected.connect_error is not None
        assert CALLBACK_REJECTED in rejected.connect_error
        assert rejected.request_count == 0, "a rejected resume must not reach the model"
        assert callback_peer.entries_path.read_bytes() == candidate_bytes, (
            "a rejected resume must not change the persisted candidate"
        )

        retried = recorder.add(await run_round(
            root=cli_home, model_peer=model_peer, callback_peer=callback_peer,
            label="retried", prompt="retry question", session_id=session_id,
            resume=True,
        ))
        _assert_result_success(retried)
        retried_entries = _persisted(callback_peer, session_id)
        assert retried_entries[: len(candidate)] == candidate, (
            "the retry must extend the same candidate, not rebuild it"
        )
        assert retried.wire_requests[0]["prior_assistant_text"].startswith("baseline answer")
        assert retried.wire_requests[0]["turn_text"] == "retry question"
        record = recorder.write(
            scenario="failed-resume-then-retry",
            conditions={
                "failed_attempt": "rejected_before_spawn",
                "retry_source": "same_candidate",
                "resume_kind": "explicit_id",
            },
            callback_peer=callback_peer,
        )

    assert record["rounds"][1]["model_requests"] == 0
