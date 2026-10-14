"""Exercise the bundled CLI's real image transcript against a loopback-only model."""

import asyncio
import copy
import json
import os
import tempfile
from dataclasses import replace
import random
import struct
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


from app.context.domain.provider_media import split_provider_images, restore_provider_images
from app.execution.infrastructure.harness.claude.session_images import provider_session_image_fields


@pytest.fixture
def sdk_image_workspace():
    # The SDK nests an encoded cwd below another temp root during native resume.
    with tempfile.TemporaryDirectory(prefix="psi-") as directory:
        yield Path(directory)


def _synthetic_png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rng = random.Random(41)
    pixels = b"".join(b"\x00" + rng.randbytes(320 * 3) for _ in range(320))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 320, 320, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_cost_state", [False, True])
async def test_bundled_cli_image_transcript_round_trip(sdk_image_workspace, monkeypatch, legacy_cost_state):
    import claude_agent_sdk as sdk
    from claude_agent_sdk._internal import session_resume

    tmp_path = sdk_image_workspace
    monkeypatch.setattr(session_resume, "_copy_auth_files", lambda *_args: None)
    for key in tuple(os.environ):
        if key.startswith(("ANTHROPIC_", "CLAUDE_")) or key.upper() in {
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        }:
            monkeypatch.delenv(key, raising=False)
    image_path = tmp_path / "synthetic.png"
    image_path.write_bytes(_synthetic_png())
    entries, stored, loaded = [], [], []
    images = {}
    model_requests = []

    class Store:
        async def append(self, _key, batch):
            entries.extend(copy.deepcopy(batch))
            projection = split_provider_images(batch, image_fields=provider_session_image_fields)
            for digest, payload in projection.images.items():
                images[digest] = payload.content.decode("utf-8")
            for entry, refs in zip(projection.entries, projection.references, strict=True):
                for ref in refs:
                    ref["image_id"] = ref["sha256"]
                stored.append((entry, refs))

        async def load(self, _key):
            restored = [restore_provider_images(entry, refs, values=images) for entry, refs in stored]
            if restored:
                loaded.append(copy.deepcopy(restored))
            return restored or None

        async def list_subkeys(self, _key):
            return []

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            if self.path.split("?")[0].endswith("/count_tokens"):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"input_tokens": 100}')
                return
            if not self.path.split("?")[0].endswith("/messages"):
                self.send_error(404)
                return
            model_requests.append(body)
            first = len(model_requests) == 1
            block = (
                {"type": "tool_use", "id": "synthetic_read", "name": "Read", "input": {}}
                if first else {"type": "text", "text": ""}
            )
            delta = (
                {"type": "input_json_delta", "partial_json": json.dumps({"file_path": str(image_path)})}
                if first else {"type": "text_delta", "text": "Synthetic image received."}
            )
            message = {"id": f"synthetic_{len(model_requests)}", "type": "message", "role": "assistant",
                       "model": body["model"], "content": [], "stop_reason": None, "stop_sequence": None,
                       "usage": {"input_tokens": 100, "output_tokens": 1}}
            events = [
                ("message_start", {"type": "message_start", "message": message}),
                ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": block}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use" if first else "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 10}}),
                ("message_stop", {"type": "message_stop"}),
            ]
            encoded = "".join(f"event: {kind}\ndata: {json.dumps(payload)}\n\n" for kind, payload in events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    options = sdk.ClaudeAgentOptions(
        cwd=str(tmp_path), model="claude-sonnet-4-5-20250929", tools=["Read"],
        allowed_tools=["Read"], permission_mode="dontAsk", setting_sources=[],
        strict_mcp_config=True, session_store=Store(), session_store_flush="eager",
        max_turns=3,
        env={
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
            "ANTHROPIC_API_KEY": "synthetic-not-a-credential",
            "CLAUDE_CONFIG_DIR": str(config_dir), "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path), "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1", "DISABLE_TELEMETRY": "1",
        },
    )
    messages = []

    async def run(prompt, run_options):
        async for message in sdk.query(prompt=prompt, options=run_options):
            messages.append(message)

    try:
        await asyncio.wait_for(run(f"Read the synthetic image at {image_path}.", options), timeout=45)
        initial_entries = copy.deepcopy(entries)
        if legacy_cost_state:
            # Older stored transcripts do not contain the newer CLI cost-state record.
            stored[:] = [(entry, refs) for entry, refs in stored if entry.get("type") != "cost-state"]
            initial_entries = [entry for entry in initial_entries if entry.get("type") != "cost-state"]
        result = next(message for message in messages if isinstance(message, sdk.ResultMessage))
        assert not result.is_error
        await asyncio.wait_for(run("Recall the synthetic image from the previous turn.",
                                   replace(options, resume=result.session_id)), timeout=45)
        resumed_result = [message for message in messages if isinstance(message, sdk.ResultMessage)][-1]
        assert not resumed_result.is_error
        from app.execution.infrastructure.harness.claude.result_usage import ClaudeResultUsage

        observation = ClaudeResultUsage(session_id=result.session_id, prompt="Recall", resumed_entries=initial_entries)
        observation.observe_result(resumed_result)
        current = observation.project(resumed_result.usage, terminal_result=True)
        assert current["cost_coverage"] == "complete"
        assert current["total_cost_usd"] == pytest.approx(result.total_cost_usd / 2)
        assert current["input_tokens"] == 100 and current["output_tokens"] == 10
        assert sum(model["inputTokens"] for model in current["model_usage"].values()) == 100
        assert resumed_result.total_cost_usd == pytest.approx(result.total_cost_usd * (0.5 if legacy_cost_state else 1.5))
        assert loaded and loaded[-1] == initial_entries
        assert any("image" in json.dumps(request["messages"]) for request in model_requests[2:])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert len(model_requests) >= 3
    assert len(images) == 1
    assert all(len(json.dumps(entry)) < 256 * 1024 for entry, _refs in stored)
    assert any(isinstance(message, sdk.ResultMessage) and not message.is_error for message in messages)
    assert not any(isinstance(message, sdk.MirrorErrorMessage) for message in messages)
    assert entries
    image_entries = [entry for entry in entries if "base64" in json.dumps(entry)]
    assert image_entries
    # This file contains synthetic test data only; capture the real pinned CLI shape.
    (tmp_path / "synthetic-image-entries.json").write_text(json.dumps(image_entries), encoding="utf-8")
    assert max(len(json.dumps(entry).encode()) for entry in image_entries) > 256 * 1024
