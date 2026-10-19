from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import httpx

from app.execution.domain.runtime_debug import normalize_execution_debug

from app.context.api import (
    MAX_PROVIDER_SESSION_CALLBACK_RESPONSE_BYTES as MAX_PROVIDER_SESSION_RESPONSE_BYTES,
    MAX_PROVIDER_SESSION_BATCH_BYTES,
    MAX_PROVIDER_SESSION_ENTRY_BYTES,
    PROVIDER_SESSION_READ_VIEW_HEADER,
    PROVIDER_SESSION_CHILD_READ_VIEW,
)

DEFAULT_PROVIDER_SESSION_TIMEOUT_SECONDS = 10.0
MAX_PROVIDER_SESSION_TIMEOUT_SECONDS = 30.0
MIN_PROVIDER_SESSION_TIMEOUT_SECONDS = 0.1
MAX_PROVIDER_SESSION_REQUEST_BYTES = MAX_PROVIDER_SESSION_BATCH_BYTES
MAX_PROVIDER_SESSION_APPEND_ENTRIES = 128
MAX_PROVIDER_SESSION_APPEND_ENTRY_BYTES = MAX_PROVIDER_SESSION_ENTRY_BYTES


class ClaudeSessionStoreTransportError(RuntimeError):
    """A bounded, detail-free failure from the host SessionStore callback."""

    def __init__(self, code: str, *, debug: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.debug = normalize_execution_debug(debug)


@dataclass
class _PendingAppend:
    signature: str
    payloads: list[dict[str, Any]]
    next_chunk: int = 0


class ClaudeSessionStoreAdapter:
    """Duck-typed Claude SessionStore backed by the authenticated host callback."""

    def __init__(
        self,
        *,
        callback_url: str,
        callback_token: str,
        callback_token_id: str,
        run_id: str,
        attempt_id: str,
        provider_session_id: str | None = None,
        timeout_seconds: float = DEFAULT_PROVIDER_SESSION_TIMEOUT_SECONDS,
        max_request_bytes: int = MAX_PROVIDER_SESSION_REQUEST_BYTES,
        max_response_bytes: int = MAX_PROVIDER_SESSION_RESPONSE_BYTES,
    ) -> None:
        if not callback_url or not callback_token or not callback_token_id:
            raise ValueError("provider_session_callback_configuration_invalid")
        if not run_id or not attempt_id:
            raise ValueError("provider_session_run_identity_invalid")
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool):
            raise ValueError("provider_session_timeout_invalid")
        if not math.isfinite(float(timeout_seconds)) or not (
            MIN_PROVIDER_SESSION_TIMEOUT_SECONDS
            <= float(timeout_seconds)
            <= MAX_PROVIDER_SESSION_TIMEOUT_SECONDS
        ):
            raise ValueError("provider_session_timeout_invalid")
        if (
            type(max_request_bytes) is not int
            or max_request_bytes < 1
            or max_request_bytes > MAX_PROVIDER_SESSION_REQUEST_BYTES
        ):
            raise ValueError("provider_session_body_limit_invalid")
        if (
            type(max_response_bytes) is not int
            or max_response_bytes < 1
            or max_response_bytes > MAX_PROVIDER_SESSION_RESPONSE_BYTES
        ):
            raise ValueError("provider_session_response_limit_invalid")
        if provider_session_id is not None and not provider_session_id:
            raise ValueError("provider_session_identity_invalid")
        self._callback_url = callback_url
        self._callback_token = callback_token
        self._callback_token_id = callback_token_id
        self._run_id = run_id
        self._attempt_id = attempt_id
        self._provider_session_id = provider_session_id
        self._timeout_seconds = float(timeout_seconds)
        self._max_request_bytes = max_request_bytes
        self._max_response_bytes = max_response_bytes
        self._load_cache: dict[tuple[str, str | None], list[dict[str, Any]] | None] = {}
        self._next_sequence: int | None = None
        self._pending_batch: _PendingAppend | None = None
        self._append_lock = asyncio.Lock()
        self._completed_sequence: int | None = None
        self.diagnostic_debug: dict[str, str | int] = {}

    def _failure(self, code: str, *, action: str, phase: str, **facts: Any) -> ClaudeSessionStoreTransportError:
        self.diagnostic_debug = normalize_execution_debug({
            "component": "provider_session", "operation": action, "phase": phase, **facts,
        })
        return ClaudeSessionStoreTransportError(code, debug=self.diagnostic_debug)

    @property
    def accepted_final_sequence(self) -> int | None:
        return self._completed_sequence

    @staticmethod
    def _key_value(key: Mapping[str, Any] | str, name: str) -> Any:
        if isinstance(key, Mapping):
            return key.get(name)
        if name == "session_id" and isinstance(key, str):
            return key
        return None

    def _session_id(self, key: Mapping[str, Any] | str) -> str:
        session_id = self._key_value(key, "session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ClaudeSessionStoreTransportError("provider_session_key_invalid")
        session_id = session_id.strip()
        if self._provider_session_id is not None and session_id != self._provider_session_id:
            raise ClaudeSessionStoreTransportError("provider_session_identity_mismatch")
        return session_id

    @staticmethod
    def _subpath(key: Mapping[str, Any] | str) -> str | None:
        if not isinstance(key, Mapping):
            return None
        value = key.get("subpath")
        if value is None or value == "":
            return None
        if not isinstance(value, str) or not value.strip():
            raise ClaudeSessionStoreTransportError("provider_session_subpath_invalid")
        return value.strip()

    @staticmethod
    def _json_size(value: Any) -> int:
        try:
            return len(json.dumps(
                value, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
            ).encode("utf-8"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ClaudeSessionStoreTransportError("provider_session_entry_not_json") from exc

    def _payload(
        self,
        *,
        action: str,
        key: Mapping[str, Any] | str,
        entries: Sequence[Mapping[str, Any]] = (),
        expected_sequence: int | None = None,
    ) -> dict[str, Any]:
        session_id = self._session_id(key)
        if action == "append":
            encoded_entries: list[dict[str, Any]] = []
            for entry in entries:
                if not isinstance(entry, Mapping):
                    raise ClaudeSessionStoreTransportError("provider_session_entry_shape_invalid")
                encoded_entries.append(dict(entry))
        else:
            encoded_entries = []
        payload = {
            "action": action,
            "run_id": self._run_id,
            "attempt_id": self._attempt_id,
            "callback_token_id": self._callback_token_id,
            "provider_session_id": session_id,
            "subpath": self._subpath(key),
            "entries": encoded_entries,
            "expected_sequence": expected_sequence,
        }
        size = self._json_size(payload)
        if size > self._max_request_bytes:
            raise self._failure("provider_session_request_too_large", action=action, phase="entry_validation",
                                failure_class="size_limit", observed_bytes=size, limit_bytes=self._max_request_bytes)
        return payload

    async def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"X-AI-Platform-Callback-Token": self._callback_token}
        if payload["action"] == "list_subkeys":
            headers[PROVIDER_SESSION_READ_VIEW_HEADER] = PROVIDER_SESSION_CHILD_READ_VIEW
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    "POST",
                    self._callback_url,
                    json=payload,
                    headers=headers,
                ) as response:
                    if response.status_code >= 400:
                        raise self._failure(
                            "provider_session_callback_rejected", action=payload["action"], phase="response",
                            failure_class="http_error", http_status=response.status_code,
                            entry_count=len(payload["entries"]), expected_sequence=payload.get("expected_sequence"),
                        )
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > self._max_response_bytes:
                            raise self._failure("provider_session_response_too_large", action=payload["action"], phase="stream",
                                                failure_class="size_limit", observed_bytes=total, limit_bytes=self._max_response_bytes)
                        chunks.append(chunk)
        except ClaudeSessionStoreTransportError:
            raise
        except httpx.TimeoutException as exc:
            raise self._failure("provider_session_callback_timeout", action=payload["action"], phase="connect" if isinstance(exc, httpx.ConnectTimeout) else "stream",
                                failure_class="connect_timeout" if isinstance(exc, httpx.ConnectTimeout) else "read_timeout", timeout_ms=int(self._timeout_seconds * 1000)) from exc
        except httpx.HTTPError as exc:
            raise self._failure("provider_session_callback_unavailable", action=payload["action"], phase="connect" if isinstance(exc, httpx.ConnectError) else "stream",
                                failure_class="connect_error" if isinstance(exc, httpx.ConnectError) else "transport_error") from exc

        try:
            body = json.loads(b"".join(chunks))
        except (TypeError, ValueError) as exc:
            raise self._failure("provider_session_response_invalid", action=payload["action"], phase="response", failure_class="invalid_response") from exc
        if not isinstance(body, dict) or body.get("action") != payload["action"]:
            raise self._failure("provider_session_response_invalid", action=payload["action"], phase="response", failure_class="invalid_response")
        if "child_entries" in body and payload["action"] != "list_subkeys":
            raise self._failure("provider_session_response_invalid", action=payload["action"], phase="response", failure_class="invalid_response")
        sequence = body.get("next_sequence")
        if type(sequence) is not int or sequence < 1:
            raise self._failure("provider_session_response_invalid", action=payload["action"], phase="response", failure_class="invalid_response")
        if payload["action"] == "append" and (
            body.get("accepted") is not True
            or body.get("entry_count") != len(payload["entries"])
            or body.get("last_sequence") != payload["expected_sequence"] + len(payload["entries"]) - 1
            or sequence != body["last_sequence"] + 1
        ):
            raise self._failure("provider_session_append_rejected", action="append", phase="response", failure_class="invalid_response",
                                expected_sequence=payload["expected_sequence"], accepted_sequence=sequence, entry_count=len(payload["entries"]))
        return body

    async def load(self, key: Mapping[str, Any] | str) -> list[dict[str, Any]] | None:
        async with self._append_lock:
            return await self._load_locked(key)

    async def _load_locked(self, key: Mapping[str, Any] | str) -> list[dict[str, Any]] | None:
        if self._pending_batch is not None:
            raise ClaudeSessionStoreTransportError("provider_session_append_pending")
        session_id = self._session_id(key)
        subpath = self._subpath(key)
        cache_key = (session_id, subpath)
        if cache_key in self._load_cache:
            # SDK writes each child synchronously after awaiting load. A cache
            # hit must still let delivered task cancellation stop materialization.
            await asyncio.sleep(0)
            self.diagnostic_debug = {}
            return deepcopy(self._load_cache[cache_key])
        body = await self._request(self._payload(action="load", key=key))
        sequence = body["next_sequence"]
        if self._next_sequence is not None and sequence != self._next_sequence:
            raise self._failure("provider_session_sequence_conflict", action="load", phase="response", failure_class="invalid_response",
                                expected_sequence=self._next_sequence, accepted_sequence=sequence)
        entries = body.get("entries")
        if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
            raise self._failure("provider_session_response_invalid", action="load", phase="response", failure_class="invalid_response")
        result = entries or None
        self._next_sequence = sequence
        self._completed_sequence = sequence - 1
        self._load_cache[cache_key] = result
        self.diagnostic_debug = {}
        return deepcopy(result)

    def _append_input(
        self, key: Mapping[str, Any] | str, entries: Sequence[Mapping[str, Any]],
    ) -> tuple[str, list[dict[str, Any]], list[int]]:
        if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes, bytearray)):
            raise ClaudeSessionStoreTransportError("provider_session_entry_batch_invalid")
        if any(not isinstance(entry, Mapping) for entry in entries):
            raise ClaudeSessionStoreTransportError("provider_session_entry_shape_invalid")
        try:
            # Freeze caller-owned nested data for deterministic retries.
            encoded = json.dumps(
                [self._session_id(key), self._subpath(key), [dict(entry) for entry in entries]],
                ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
            )
            frozen = json.loads(encoded)[2]
        except (TypeError, ValueError, OverflowError) as exc:
            raise ClaudeSessionStoreTransportError("provider_session_entry_not_json") from exc
        entry_sizes: list[int] = []
        for index, entry in enumerate(frozen):
            entry_bytes = self._json_size(entry)
            if entry_bytes > MAX_PROVIDER_SESSION_APPEND_ENTRY_BYTES:
                from app.execution.infrastructure.harness.claude.session_images import provider_session_image_fields
                image_sizes = [len(value.encode("utf-8")) for _path, _mime, value in provider_session_image_fields(entry)]
                raise self._failure(
                    "provider_session_entry_too_large", action="append", phase="entry_validation", failure_class="size_limit",
                    entry_index=index, entry_count=len(frozen), observed_bytes=entry_bytes,
                    limit_bytes=MAX_PROVIDER_SESSION_APPEND_ENTRY_BYTES, entry_kind=entry.get("type", "other"),
                    scope="child" if self._subpath(key) else "main", image_count=len(image_sizes),
                    encoded_image_bytes=sum(image_sizes), largest_encoded_image_bytes=max(image_sizes, default=0),
                )
            entry_sizes.append(entry_bytes)
        return encoded, frozen, entry_sizes

    def _append_chunks(
        self, key: Mapping[str, Any] | str, entries: list[dict[str, Any]],
        entry_sizes: list[int], start: int,
    ) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        chunk: list[dict[str, Any]] = []
        chunk_bytes = 0
        for entry, entry_bytes in zip(entries, entry_sizes, strict=True):
            if chunk and (
                len(chunk) == MAX_PROVIDER_SESSION_APPEND_ENTRIES
                or chunk_bytes + entry_bytes + 1 > self._max_request_bytes
            ):
                payloads.append(self._payload(action="append", key=key, entries=chunk, expected_sequence=start))
                start += len(chunk)
                chunk = []
            if not chunk:
                # Use the real envelope at this sequence, including digit growth.
                chunk_bytes = self._json_size(self._payload(
                    action="append", key=key, expected_sequence=start,
                ))
            # The empty envelope includes []; only entries and separating commas grow.
            candidate_bytes = chunk_bytes + entry_bytes + (1 if chunk else 0)
            if candidate_bytes > self._max_request_bytes:
                raise self._failure("provider_session_request_too_large", action="append", phase="entry_validation",
                                    failure_class="size_limit", observed_bytes=candidate_bytes, limit_bytes=self._max_request_bytes)
            chunk.append(entry)
            chunk_bytes = candidate_bytes
        if chunk:
            payloads.append(self._payload(action="append", key=key, entries=chunk, expected_sequence=start))
        return payloads

    async def append(
        self, key: Mapping[str, Any] | str, entries: Sequence[Mapping[str, Any]],
    ) -> None:
        signature, frozen, entry_sizes = self._append_input(key, entries)
        async with self._append_lock:
            pending = self._pending_batch
            if pending is not None and pending.signature != signature:
                raise ClaudeSessionStoreTransportError("provider_session_append_pending")
            if not frozen:
                self.diagnostic_debug = {}
                return
            if self._next_sequence is None:
                raise ClaudeSessionStoreTransportError("provider_session_sequence_uninitialized")
            if pending is None:
                session_id, subpath, _entries = json.loads(signature)
                frozen_key = {"session_id": session_id, "subpath": subpath}
                pending = _PendingAppend(signature, self._append_chunks(frozen_key, frozen, entry_sizes, self._next_sequence))
                self._pending_batch = pending
                self._load_cache.clear()
            while pending.next_chunk < len(pending.payloads):
                # An uncertain ACK keeps the exact chunk and expected_sequence.
                body = await self._request(pending.payloads[pending.next_chunk])
                self._next_sequence = body["next_sequence"]
                pending.next_chunk += 1
                self._load_cache.clear()
            self._completed_sequence = self._next_sequence - 1
            self._pending_batch = None
            self.diagnostic_debug = {}

    async def list_subkeys(self, key: Mapping[str, Any] | str | None = None) -> list[str]:
        async with self._append_lock:
            return await self._list_subkeys_locked(key)

    async def _list_subkeys_locked(self, key: Mapping[str, Any] | str | None) -> list[str]:
        if self._pending_batch is not None:
            raise ClaudeSessionStoreTransportError("provider_session_append_pending")
        if key is None:
            if self._provider_session_id is None:
                raise ClaudeSessionStoreTransportError("provider_session_key_invalid")
            key = {"session_id": self._provider_session_id}
        session_id = self._session_id(key)
        body = await self._request(self._payload(action="list_subkeys", key=key))
        if self._next_sequence is not None and body["next_sequence"] != self._next_sequence:
            raise self._failure("provider_session_sequence_conflict", action="list_subkeys", phase="response", failure_class="invalid_response",
                                expected_sequence=self._next_sequence, accepted_sequence=body["next_sequence"])
        subpaths = body.get("subpaths")
        if not isinstance(subpaths, list) or any(
            not isinstance(subpath, str) for subpath in subpaths
        ):
            raise self._failure("provider_session_response_invalid", action="list_subkeys", phase="response", failure_class="invalid_response")
        if "child_entries" in body:
            children = body["child_entries"]
            if (
                not isinstance(children, dict)
                or len(subpaths) > 4096
                or len(set(subpaths)) != len(subpaths)
                or set(children) != set(subpaths)
                or any(not path or path != path.strip() or len(path) > 512 for path in subpaths)
                or any(
                    not isinstance(entries, list) or not entries
                    or any(not isinstance(entry, dict) for entry in entries)
                    for entries in children.values()
                )
            ):
                raise self._failure("provider_session_response_invalid", action="list_subkeys", phase="response", failure_class="invalid_response")
            child_count = sum(len(entries) for entries in children.values())
            main_key = (session_id, None)
            if (
                child_count > 4096
                or body["next_sequence"] > 4097
                or child_count > body["next_sequence"] - 1
                or (
                    main_key in self._load_cache
                    and child_count + len(self._load_cache[main_key] or []) != body["next_sequence"] - 1
                )
            ):
                raise self._failure("provider_session_response_invalid", action="list_subkeys", phase="response", failure_class="invalid_response")
            # Install only a complete validated view from this locked read.
            self._load_cache.update({(session_id, path): entries for path, entries in children.items()})
        self._next_sequence = body["next_sequence"]
        self._completed_sequence = self._next_sequence - 1
        self.diagnostic_debug = {}
        return subpaths


# Keep the adapter name obvious to callers that refer to the SDK boundary.
ClaudeSessionStore = ClaudeSessionStoreAdapter


__all__ = [
    "ClaudeSessionStore",
    "ClaudeSessionStoreAdapter",
    "ClaudeSessionStoreTransportError",
    "DEFAULT_PROVIDER_SESSION_TIMEOUT_SECONDS",
    "MAX_PROVIDER_SESSION_REQUEST_BYTES",
    "MAX_PROVIDER_SESSION_RESPONSE_BYTES",
    "MAX_PROVIDER_SESSION_TIMEOUT_SECONDS",
    "MIN_PROVIDER_SESSION_TIMEOUT_SECONDS",
]
