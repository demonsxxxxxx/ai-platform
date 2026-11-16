"""Real-CLI lifecycle probe: interrupt, hard stop, and native resume.

Every round spawns the bundled Claude Code CLI (its declared version is recorded
alongside the SDK version) and gives it the *production*
:class:`~app.execution.infrastructure.harness.claude.session_store.ClaudeSessionStoreAdapter`
pointed at a loopback provider-session callback peer.  Only two things are
substituted: the model wire (a scripted Anthropic-compatible peer) and the MCP
tool (an in-process SDK MCP server whose handler blocks).  ``connect``,
``materialize_resume_session``, ``interrupt`` and subprocess teardown all run for
real.

One round = one Run/Attempt: a fresh CLI process, a fresh adapter with its own
callback identity, and — when resuming — only the opaque entries an earlier round
already persisted into the shared durable directory.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import _cli_version, _version

from app.execution.infrastructure.harness.claude.session_store import (
    ClaudeSessionStoreAdapter,
)

SDK_VERSION = _version.__version__
CLI_VERSION = _cli_version.__cli_version__

SYNTHETIC_CREDENTIAL = "synthetic-not-a-credential"
SYNTHETIC_MCP_SERVER = "synthetic"
BLOCKING_TOOL = f"mcp__{SYNTHETIC_MCP_SERVER}__blocking_lookup"
SYNTHETIC_MODEL = "claude-sonnet-4-5-20250929"
SUBAGENT_TOOL = "Agent"
CALLBACK_TOKEN = "synthetic-callback-token"


@dataclass
class Interaction:
    """One control action the harness took, with the phase it took it in."""

    action: str
    phase: str
    detail: str = ""

    def as_evidence(self) -> dict[str, str]:
        return {"action": self.action, "phase": self.phase, "detail": self.detail}


@dataclass
class CliRound:
    """What one CLI process produced, persisted, and sent on the wire."""

    label: str
    session_id: str
    prompt: str
    resume_from: str | None = None
    result_subtypes: list[tuple[str, bool]] = field(default_factory=list)
    message_types: list[str] = field(default_factory=list)
    assistant_text: str = ""
    connect_error: str | None = None
    stream_error: str | None = None
    interactions: list[Interaction] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    store_entries: int = 0
    store_entry_types: list[str] = field(default_factory=list)
    accepted_final_sequence: int | None = None
    model_request_start: int = 0
    model_request_end: int = 0
    wire_requests: list[dict[str, Any]] = field(default_factory=list)

    @property
    def produced_result(self) -> bool:
        return bool(self.result_subtypes)

    @property
    def request_count(self) -> int:
        return self.model_request_end - self.model_request_start

    def as_evidence(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "session_id": self.session_id,
            "resume_from": self.resume_from,
            "prompt": self.prompt,
            "message_types": self.message_types,
            "result_subtypes": [list(item) for item in self.result_subtypes],
            "assistant_text": self.assistant_text[:400],
            "connect_error": self.connect_error,
            "stream_error": self.stream_error,
            "interactions": [item.as_evidence() for item in self.interactions],
            "tool_calls": self.tool_calls,
            "store_entries": self.store_entries,
            "store_entry_types": self.store_entry_types,
            "accepted_final_sequence": self.accepted_final_sequence,
            "model_requests": self.request_count,
            "wire_requests": self.wire_requests,
        }


@dataclass(frozen=True)
class Control:
    """When to stop one round instead of letting it finish."""

    interrupt_after_text_deltas: int | None = None
    interrupt_when_tool_started: bool = False
    kill_when_tool_started: bool = False

    @property
    def interrupts(self) -> bool:
        return (
            self.interrupt_after_text_deltas is not None
            or self.interrupt_when_tool_started
        )


FINISH = Control()


def new_session_id() -> str:
    """Session id the platform allocates before the first Run."""

    return str(uuid.uuid4())


def build_round_options(*, root: Path, model_url: str, store: Any,
                        session_id: str, resume: bool, mcp_server: Any,
                        subagents: bool = False):
    import claude_agent_sdk as sdk

    # The SDK materializes a resume under a temp dir whose project key embeds this
    # absolute cwd. Windows MAX_PATH (260) then bounds the workspace path; the
    # platform workspace path is short, so a deep test root cannot prove anything.
    materialized = len(str(root / "work")) + 120
    if os.name == "nt" and materialized > 259:
        raise AssertionError(
            f"cli workspace path leaves no room for the SDK resume file: {root / 'work'}"
        )

    subagent_options: dict[str, Any] = {"tools": [], "allowed_tools": [BLOCKING_TOOL]}
    if subagents:
        subagent_options = {
            "tools": [SUBAGENT_TOOL],
            "allowed_tools": [SUBAGENT_TOOL, BLOCKING_TOOL],
            "agents": {
                "general-purpose": sdk.AgentDefinition(
                    description="Synthetic child agent for the lifecycle probe.",
                    prompt="You are a synthetic child agent. Answer with one short sentence.",
                )
            },
        }
    return sdk.ClaudeAgentOptions(
        cwd=str(root / "work"),
        model=SYNTHETIC_MODEL,
        mcp_servers={SYNTHETIC_MCP_SERVER: mcp_server},
        permission_mode="dontAsk",
        setting_sources=[],
        strict_mcp_config=True,
        session_store=store,
        session_store_flush="eager",
        max_turns=6,
        include_partial_messages=True,
        **subagent_options,
        **({"resume": session_id} if resume else {"session_id": session_id}),
        env={
            "ANTHROPIC_BASE_URL": model_url,
            "ANTHROPIC_API_KEY": SYNTHETIC_CREDENTIAL,
            "CLAUDE_CONFIG_DIR": str(root / "config"),
            "HOME": str(root),
            "USERPROFILE": str(root),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
            "DISABLE_TELEMETRY": "1",
            "NO_PROXY": "127.0.0.1,localhost",
        },
    )


def _blocking_server(tool_started: asyncio.Event, tool_calls: list[str], release: asyncio.Event):
    import claude_agent_sdk as sdk

    @sdk.tool(
        "blocking_lookup",
        "Synthetic lookup whose result never arrives while a stop is in flight.",
        {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    )
    async def blocking_lookup(arguments: dict[str, Any]):
        tool_calls.append(str(arguments.get("query")))
        tool_started.set()
        await release.wait()
        return {"content": [{"type": "text", "text": "synthetic release"}]}

    return sdk.create_sdk_mcp_server(name=SYNTHETIC_MCP_SERVER, tools=[blocking_lookup])


async def _text_deltas(messages: list[Any]) -> int:
    import claude_agent_sdk as sdk

    return sum(
        1
        for message in messages
        if isinstance(message, sdk.StreamEvent)
        and message.event.get("type") == "content_block_delta"
    )


async def _wait_for(predicate, deadline: float, *, label: str, what: str) -> None:
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"{label}: timed out waiting for {what}")


async def run_round(
    *,
    root: Path,
    model_peer,
    callback_peer,
    label: str,
    prompt: str,
    session_id: str,
    control: Control = FINISH,
    resume: bool = False,
    fail_preload: bool = False,
    subagents: bool = False,
    settle_seconds: float = 12.0,
    timeout_seconds: float = 90.0,
) -> CliRound:
    """Run exactly one CLI process (one Run/Attempt) and record its outcome."""

    import claude_agent_sdk as sdk

    # options.env binds auth/config reads to the synthetic config directory;
    # the SDK's real resume materialization and auth-copy path stay untouched.
    outcome = CliRound(
        label=label, session_id=session_id, prompt=prompt,
        resume_from=session_id if resume else None,
        model_request_start=len(model_peer.requests),
    )
    tool_calls: list[str] = []
    tool_started = asyncio.Event()
    release = asyncio.Event()
    adapter = ClaudeSessionStoreAdapter(
        callback_url=callback_peer.url,
        callback_token=CALLBACK_TOKEN,
        callback_token_id=f"cbt:run-{label}:attempt-{label}",
        run_id=f"run-{label}",
        attempt_id=f"attempt-{label}",
        provider_session_id=session_id,
    )

    if fail_preload:
        callback_peer.fail_next("load")
    try:
        # The platform preloads the exact session before authorizing the SDK to
        # write, so the adapter learns the sequence cursor here.
        await adapter.load({"session_id": session_id})
    except Exception as exc:  # noqa: BLE001 - recorded as the recovery gate result
        outcome.connect_error = f"{type(exc).__name__}: {str(exc)[:300]}"
        outcome.interactions.append(
            Interaction("preload", "before_spawn", type(exc).__name__)
        )
        outcome.model_request_end = len(model_peer.requests)
        return outcome
    outcome.interactions.append(Interaction("preload", "before_spawn", "accepted"))

    mcp_server = _blocking_server(tool_started, tool_calls, release)
    options = build_round_options(
        root=root, model_url=model_peer.url, store=adapter,
        session_id=session_id, resume=resume, mcp_server=mcp_server,
        subagents=subagents,
    )
    client = sdk.ClaudeSDKClient(options)
    messages: list[Any] = []

    async def collect() -> None:
        try:
            async for message in client.receive_messages():
                messages.append(message)
        except Exception as exc:  # noqa: BLE001 - recorded as stream evidence
            outcome.stream_error = f"{type(exc).__name__}: {str(exc)[:300]}"

    reader: asyncio.Task[None] | None = None
    try:
        try:
            await asyncio.wait_for(client.connect(prompt), timeout=60)
        except Exception as exc:  # noqa: BLE001 - recorded as connect evidence
            outcome.connect_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            return outcome
        reader = asyncio.create_task(collect())
        deadline = time.monotonic() + timeout_seconds
        if control.kill_when_tool_started:
            await _wait_for(lambda: tool_started.is_set(), deadline,
                            label=label, what="the blocking MCP tool to start")
            # The platform kills only after it has read the persisted evidence of
            # the call in flight; wait for that entry the same way.
            await _wait_for(
                lambda: any(
                    entry.get("type") == "assistant"
                    for entry in callback_peer.entries(session_id, None)
                ),
                deadline, label=label, what="the assistant tool_use entry to persist",
            )
            process = getattr(client._transport, "_process", None)
            assert process is not None, f"{label}: CLI process unavailable for a hard stop"
            process.kill()
            outcome.interactions.append(Interaction("kill", "tool_running", "cli_sigkill"))
            await asyncio.sleep(1.0)
        elif control.interrupts:
            await _interrupt(client, outcome, messages, control, tool_started, deadline)
            # A graceful stop may or may not produce a terminal ResultMessage; the
            # difference is platform-visible, so it is measured, not assumed.
            await _settle(outcome, messages, settle_seconds)
        else:
            await _wait_for(
                lambda: any(isinstance(message, sdk.ResultMessage) for message in messages),
                deadline, label=label, what="the terminal ResultMessage",
            )
            # Let eager SessionStore flushes land before the process closes.
            await asyncio.sleep(1.0)
    finally:
        release.set()
        if reader is not None:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        try:
            await asyncio.wait_for(client.disconnect(), timeout=45)
        except Exception as exc:  # noqa: BLE001 - teardown recorded, not asserted
            outcome.stream_error = outcome.stream_error or f"disconnect: {type(exc).__name__}"
        _record(outcome, messages, adapter, callback_peer, model_peer, tool_calls)
    return outcome


async def _interrupt(
    client: Any,
    outcome: CliRound,
    messages: list[Any],
    control: Control,
    tool_started: asyncio.Event,
    deadline: float,
) -> None:
    if control.interrupt_when_tool_started:
        await _wait_for(lambda: tool_started.is_set(), deadline,
                        label=outcome.label, what="the blocking MCP tool to start")
        outcome.interactions.append(Interaction("interrupt", "tool_running", "sdk_interrupt"))
    else:
        threshold = control.interrupt_after_text_deltas or 1

        async def enough_deltas() -> bool:
            return await _text_deltas(messages) >= threshold

        while time.monotonic() < deadline:
            if await enough_deltas():
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError(f"{outcome.label}: text deltas never reached {threshold}")
        outcome.interactions.append(
            Interaction("interrupt", "text_streaming", f"after_{threshold}_deltas")
        )
    await client.interrupt()
    # Interrupt returned; whether the CLI then closes the turn is measured in
    # _settle() rather than assumed here.
    outcome.interactions.append(Interaction("interrupt_returned", "accepted", "sdk_interrupt"))


async def _settle(outcome: CliRound, messages: list[Any], settle_seconds: float) -> None:
    """Wait a bounded time for the turn to close after a stop."""

    import claude_agent_sdk as sdk

    deadline = time.monotonic() + settle_seconds
    while time.monotonic() < deadline:
        if any(isinstance(message, sdk.ResultMessage) for message in messages):
            outcome.interactions.append(
                Interaction("terminal_frame", "after_stop", "result_received")
            )
            await asyncio.sleep(0.5)
            return
        await asyncio.sleep(0.02)
    outcome.interactions.append(
        Interaction("terminal_frame", "after_stop", "no_result_within_settle")
    )


def _record(
    outcome: CliRound,
    messages: list[Any],
    adapter: ClaudeSessionStoreAdapter,
    callback_peer,
    model_peer,
    tool_calls: list[str],
) -> None:
    import claude_agent_sdk as sdk

    text_block = getattr(sdk, "TextBlock", ())
    outcome.message_types = [type(message).__name__ for message in messages]
    outcome.result_subtypes = [
        (message.subtype, bool(message.is_error))
        for message in messages
        if isinstance(message, sdk.ResultMessage)
    ]
    outcome.assistant_text = "".join(
        block.text
        for message in messages
        if isinstance(message, sdk.AssistantMessage)
        for block in message.content
        if isinstance(block, text_block)
    )
    for message in messages:
        if isinstance(message, sdk.ResultMessage) and message.session_id:
            outcome.session_id = message.session_id
    outcome.tool_calls = list(tool_calls)
    outcome.accepted_final_sequence = adapter.accepted_final_sequence
    outcome.model_request_end = len(model_peer.requests)
    outcome.wire_requests = [
        request.as_evidence()
        for request in model_peer.requests[outcome.model_request_start:]
    ]
    entries = callback_peer.entries(outcome.session_id, None)
    outcome.store_entries = len(entries)
    outcome.store_entry_types = [entry.get("type") for entry in entries]
    subpaths = callback_peer.subpaths(outcome.session_id)
    if subpaths:
        outcome.interactions.append(Interaction("subpaths", "persisted", json.dumps(subpaths)))


def stored_entry_types(callback_peer, session_id: str) -> list[str]:
    return [entry.get("type") for entry in callback_peer.entries(session_id, None)]


def read_serviceable_store_marker(callback_peer, session_id: str) -> dict[str, Any]:
    """Compact, secret-free view of the durable native record for one session."""

    return {
        "session_id": session_id,
        "entries": callback_peer.entries(session_id, None).__len__(),
        "types": stored_entry_types(callback_peer, session_id),
        "subpaths": callback_peer.subpaths(session_id),
        "total_entries_in_store": callback_peer.total_entries(),
    }


__all__ = [
    "BLOCKING_TOOL",
    "CLI_VERSION",
    "CALLBACK_TOKEN",
    "CliRound",
    "Control",
    "FINISH",
    "Interaction",
    "SDK_VERSION",
    "SUBAGENT_TOOL",
    "SYNTHETIC_MODEL",
    "build_round_options",
    "new_session_id",
    "read_serviceable_store_marker",
    "run_round",
    "stored_entry_types",
]
