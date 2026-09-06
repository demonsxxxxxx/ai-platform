from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

import httpx


DEFAULT_PROVIDER_SESSION_TIMEOUT_SECONDS = 10.0
MAX_PROVIDER_SESSION_TIMEOUT_SECONDS = 30.0
MIN_PROVIDER_SESSION_TIMEOUT_SECONDS = 0.1
MAX_PROVIDER_SESSION_REQUEST_BYTES = 2 * 1024 * 1024
MAX_PROVIDER_SESSION_RESPONSE_BYTES = 8 * 1024 * 1024 + 64 * 1024


class ClaudeSessionStoreTransportError(RuntimeError):
    """A bounded, detail-free failure from the host SessionStore callback."""


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

    def _payload(
        self,
        *,
        action: str,
        key: Mapping[str, Any] | str,
        entries: Sequence[Mapping[str, Any]] = (),
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
        }
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, OverflowError) as exc:
            raise ClaudeSessionStoreTransportError("provider_session_entry_not_json") from exc
        if len(encoded) > self._max_request_bytes:
            raise ClaudeSessionStoreTransportError("provider_session_request_too_large")
        return payload

    async def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    "POST",
                    self._callback_url,
                    json=payload,
                    headers={"X-AI-Platform-Callback-Token": self._callback_token},
                ) as response:
                    if response.status_code >= 400:
                        raise ClaudeSessionStoreTransportError(
                            "provider_session_callback_rejected"
                        )
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > self._max_response_bytes:
                            raise ClaudeSessionStoreTransportError(
                                "provider_session_response_too_large"
                            )
                        chunks.append(chunk)
        except ClaudeSessionStoreTransportError:
            raise
        except httpx.TimeoutException as exc:
            raise ClaudeSessionStoreTransportError("provider_session_callback_timeout") from exc
        except httpx.HTTPError as exc:
            raise ClaudeSessionStoreTransportError("provider_session_callback_unavailable") from exc

        try:
            body = json.loads(b"".join(chunks))
        except (TypeError, ValueError) as exc:
            raise ClaudeSessionStoreTransportError("provider_session_response_invalid") from exc
        if not isinstance(body, dict) or body.get("action") != payload["action"]:
            raise ClaudeSessionStoreTransportError("provider_session_response_invalid")
        if payload["action"] == "append" and (
            body.get("accepted") is not True
            or body.get("entry_count") != len(payload["entries"])
        ):
            raise ClaudeSessionStoreTransportError("provider_session_append_rejected")
        return body

    async def load(self, key: Mapping[str, Any] | str) -> list[dict[str, Any]] | None:
        session_id = self._session_id(key)
        subpath = self._subpath(key)
        cache_key = (session_id, subpath)
        if cache_key in self._load_cache:
            return self._load_cache[cache_key]
        body = await self._request(self._payload(action="load", key=key))
        entries = body.get("entries")
        if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
            raise ClaudeSessionStoreTransportError("provider_session_response_invalid")
        result = entries or None
        self._load_cache[cache_key] = result
        return result

    async def append(
        self,
        key: Mapping[str, Any] | str,
        entries: Sequence[Mapping[str, Any]],
    ) -> None:
        if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes, bytearray)):
            raise ClaudeSessionStoreTransportError("provider_session_entry_batch_invalid")
        await self._request(self._payload(action="append", key=key, entries=entries))
        self._load_cache.clear()

    async def list_subkeys(self, key: Mapping[str, Any] | str | None = None) -> list[str]:
        if key is None:
            if self._provider_session_id is None:
                raise ClaudeSessionStoreTransportError("provider_session_key_invalid")
            key = {"session_id": self._provider_session_id}
        body = await self._request(self._payload(action="list_subkeys", key=key))
        subpaths = body.get("subpaths")
        if not isinstance(subpaths, list) or any(
            not isinstance(subpath, str) for subpath in subpaths
        ):
            raise ClaudeSessionStoreTransportError("provider_session_response_invalid")
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
