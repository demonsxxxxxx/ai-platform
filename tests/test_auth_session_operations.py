import asyncio
from contextlib import asynccontextmanager
import json
import math
import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app import auth_sessions
from app.auth import AuthPrincipal
from app.main import create_app


AUTH_CONTEXT_MAX_EPOCH = (2**53) - 1


class FakeAuthRedis:
    """Small Redis/Lua model for auth-context operation tests."""

    def __init__(self) -> None:
        self.values: dict[str, tuple[str, float]] = {}
        self.available = True
        self.lock = threading.Lock()
        self.now = time.time()

    def _require_available(self) -> None:
        if not self.available:
            raise ConnectionError("redis unavailable")

    def _get(self, key: str, now: float) -> str | None:
        value = self.values.get(key)
        if value is None:
            return None
        raw, expires_at = value
        if expires_at <= now:
            self.values.pop(key, None)
            return None
        return raw

    def _set(self, key: str, raw: str, expires_at: float) -> None:
        self.values[key] = (raw, expires_at)

    @staticmethod
    def _is_valid_epoch(value: object) -> bool:
        if type(value) is int:
            return 0 <= value <= AUTH_CONTEXT_MAX_EPOCH
        if type(value) is float:
            return (
                math.isfinite(value)
                and 0 <= value <= AUTH_CONTEXT_MAX_EPOCH
                and value.is_integer()
            )
        return False

    @staticmethod
    def _is_valid_lease(value: object) -> bool:
        if type(value) is int:
            return value >= 0
        if type(value) is float:
            return math.isfinite(value) and value >= 0
        return False

    @classmethod
    def _is_valid_context_record(cls, record: object) -> bool:
        if not isinstance(record, dict):
            return False
        return (
            cls._is_valid_epoch(record.get("schema_version"))
            and record.get("schema_version") == 1
            and isinstance(record.get("nonce_binding"), str)
            and cls._is_valid_epoch(record.get("operation_epoch"))
            and cls._is_valid_epoch(record.get("tenant_user_subject_epoch"))
            and isinstance(record.get("operation_token"), str)
            and isinstance(record.get("operation_kind"), str)
            and cls._is_valid_lease(record.get("lease_until"))
            and (record.get("principal") is None or isinstance(record.get("principal"), dict))
        )

    @classmethod
    def _is_valid_v2_context_record(cls, record: object) -> bool:
        if not isinstance(record, dict):
            return False
        return (
            record.get("schema_version") == 2
            and record.get("protocol_version") == 2
            and isinstance(record.get("nonce_binding"), str)
            and isinstance(record.get("incarnation_digest"), str)
            and cls._is_valid_epoch(record.get("generation"))
            and record["generation"] >= 1
            and cls._is_valid_epoch(record.get("operation_epoch"))
            and cls._is_valid_epoch(record.get("tenant_user_subject_epoch"))
            and isinstance(record.get("operation_token"), str)
            and isinstance(record.get("operation_kind"), str)
            and cls._is_valid_lease(record.get("lease_until"))
            and (record.get("principal") is None or isinstance(record.get("principal"), dict))
        )

    @classmethod
    def _is_valid_v2_authority(cls, record: object) -> bool:
        if not isinstance(record, dict):
            return False
        generation = record.get("generation")
        return (
            record.get("schema_version") == 2
            and isinstance(record.get("incarnation_digest"), str)
            and cls._is_valid_epoch(generation)
            and generation >= 1
            and isinstance(record.get("context_handle"), str)
            and (
                (
                    record.get("rotation_ticket_digest") is None
                    and record.get("rotation_ticket_generation") is None
                    and record.get("rotation_ticket_deadline") is None
                )
                or (
                    isinstance(record.get("rotation_ticket_digest"), str)
                    and cls._is_valid_epoch(record.get("rotation_ticket_generation"))
                    and record["rotation_ticket_generation"] >= 1
                    and record["rotation_ticket_generation"] == generation
                    and cls._is_valid_lease(record.get("rotation_ticket_deadline"))
                )
            )
        )

    def _pttl(self, key: str, now: float) -> int:
        value = self.values.get(key)
        if value is None:
            return -2
        _raw, expires_at = value
        remaining = int((expires_at - now) * 1000)
        if remaining <= 0:
            self.values.pop(key, None)
            return -2
        return remaining

    def _v2_records_match(
        self,
        authority: object,
        context: object,
        incarnation_digest: object,
        generation: object,
        context_handle: object,
    ) -> bool:
        return (
            self._is_valid_v2_authority(authority)
            and self._is_valid_v2_context_record(context)
            and authority["incarnation_digest"] == incarnation_digest
            and authority["generation"] == generation
            and authority["context_handle"] == context_handle
            and context["incarnation_digest"] == incarnation_digest
            and context["generation"] == generation
        )

    def _v2_ttl_consistent(self, authority_key: str, context_key: str, now: float) -> bool:
        authority_ttl = self._pttl(authority_key, now)
        context_ttl = self._pttl(context_key, now)
        return authority_ttl > 0 and context_ttl > 0 and abs(authority_ttl - context_ttl) <= 1000

    @staticmethod
    def _is_pristine_anonymous(record: dict[str, object]) -> bool:
        return (
            record["principal"] is None
            and record["tenant_user_subject_epoch"] == 0
            and record["operation_epoch"] == 0
            and record["operation_token"] == ""
            and record["operation_kind"] == ""
            and record["lease_until"] == 0
        )

    async def eval(self, script: str, _numkeys: int, *args: object) -> str:
        self._require_available()
        with self.lock:
            if "ai-platform:auth-context-bootstrap:v2" in script:
                (
                    authority_key,
                    context_key,
                    incarnation_digest,
                    generation,
                    context_handle,
                    nonce_binding,
                    ttl_seconds,
                    cookie_kind,
                    cookie_incarnation_digest,
                    cookie_generation,
                    cookie_context_handle,
                    _ticket_digest,
                    _ticket_seconds,
                ) = args
                now_value = self.now
                if not self._is_valid_epoch(generation) or generation < 1:
                    return json.dumps({"status": "corrupt"})
                authority_raw = self._get(str(authority_key), now_value)
                if authority_raw is None:
                    if cookie_kind in {"v2", "v1_conflict", "invalid"}:
                        return json.dumps({"status": "stale"})
                    if generation != 1:
                        return json.dumps({"status": "generation_gap"})
                    context_raw = self._get(str(context_key), now_value)
                    expires_at = now_value + int(ttl_seconds)
                    if context_raw is None:
                        context = {
                            "schema_version": 2,
                            "protocol_version": 2,
                            "incarnation_digest": str(incarnation_digest),
                            "generation": int(generation),
                            "nonce_binding": str(nonce_binding),
                            "principal": None,
                            "tenant_user_subject_epoch": 0,
                            "operation_epoch": 0,
                            "operation_token": "",
                            "operation_kind": "",
                            "lease_until": 0,
                        }
                        status = "created"
                    else:
                        try:
                            context = json.loads(context_raw)
                        except json.JSONDecodeError:
                            return json.dumps({"status": "corrupt"})
                        if (
                            not self._is_valid_context_record(context)
                            or context.get("nonce_binding") != nonce_binding
                        ):
                            return json.dumps({"status": "stale"})
                        if cookie_kind != "v1_matching" and not self._is_pristine_anonymous(context):
                            return json.dumps({"status": "migration_conflict"})
                        _raw, expires_at = self.values[str(context_key)]
                        context.update(
                            {
                                "schema_version": 2,
                                "protocol_version": 2,
                                "incarnation_digest": str(incarnation_digest),
                                "generation": int(generation),
                            }
                        )
                        status = "created" if context["principal"] is None else "migrated"
                    authority = {
                        "schema_version": 2,
                        "incarnation_digest": str(incarnation_digest),
                        "generation": int(generation),
                        "context_handle": str(context_handle),
                    }
                    self._set(str(context_key), json.dumps(context), expires_at)
                    self._set(str(authority_key), json.dumps(authority), expires_at)
                    return json.dumps(
                        {
                            "status": status,
                            "ttl_milliseconds": max(1, int((expires_at - now_value) * 1000)),
                        }
                    )

                try:
                    authority = json.loads(authority_raw)
                    context_raw = self._get(str(context_key), now_value)
                    context = json.loads(context_raw) if context_raw is not None else None
                except json.JSONDecodeError:
                    return json.dumps({"status": "corrupt"})
                if not self._is_valid_v2_authority(authority):
                    return json.dumps({"status": "corrupt"})
                if context is None:
                    return json.dumps({"status": "generation_conflict"})
                if not self._is_valid_v2_context_record(context):
                    return json.dumps({"status": "corrupt"})
                if not self._v2_ttl_consistent(str(authority_key), str(context_key), now_value):
                    return json.dumps({"status": "stale"})
                if authority["incarnation_digest"] != incarnation_digest:
                    return json.dumps({"status": "stale"})
                if generation < authority["generation"]:
                    return json.dumps({"status": "stale"})
                if generation > authority["generation"]:
                    return json.dumps({"status": "generation_gap"})
                if authority["context_handle"] != context_handle:
                    current_cookie = (
                        cookie_kind == "v2"
                        and cookie_incarnation_digest == incarnation_digest
                        and cookie_generation == generation
                        and cookie_context_handle == authority["context_handle"]
                    )
                    if (
                        not current_cookie
                        or not isinstance(_ticket_digest, str)
                        or not _ticket_digest
                        or not self._is_valid_epoch(_ticket_seconds)
                        or _ticket_seconds < 1
                    ):
                        return json.dumps({"status": "generation_conflict"})
                    authority["rotation_ticket_digest"] = _ticket_digest
                    authority["rotation_ticket_generation"] = generation
                    authority["rotation_ticket_deadline"] = now_value + int(_ticket_seconds)
                    _raw, expires_at = self.values[str(authority_key)]
                    self._set(str(authority_key), json.dumps(authority), expires_at)
                    return json.dumps({"status": "rebootstrap_required"})
                if not self._v2_records_match(
                    authority,
                    context,
                    incarnation_digest,
                    generation,
                    context_handle,
                ) or context.get("nonce_binding") != nonce_binding:
                    return json.dumps({"status": "stale"})
                current_cookie = (
                    cookie_kind == "v2"
                    and cookie_incarnation_digest == incarnation_digest
                    and cookie_generation == generation
                    and cookie_context_handle == context_handle
                )
                return json.dumps(
                    {
                        "status": "existing" if current_cookie else "repair",
                        "ttl_milliseconds": self._pttl(str(authority_key), now_value),
                    }
                )

            if "ai-platform:auth-context-rotation-ticket:v2" in script:
                authority_key, context_key, digest, generation, handle, ticket_digest, ticket_seconds = args
                now_value = self.now
                authority_raw = self._get(str(authority_key), now_value)
                context_raw = self._get(str(context_key), now_value)
                if authority_raw is None or context_raw is None:
                    return json.dumps({"status": "stale"})
                try:
                    authority = json.loads(authority_raw)
                    context = json.loads(context_raw)
                except json.JSONDecodeError:
                    return json.dumps({"status": "corrupt"})
                if (
                    not self._v2_records_match(authority, context, digest, generation, handle)
                    or not self._v2_ttl_consistent(str(authority_key), str(context_key), now_value)
                ):
                    return json.dumps({"status": "stale"})
                authority["rotation_ticket_digest"] = ticket_digest
                authority["rotation_ticket_generation"] = generation
                authority["rotation_ticket_deadline"] = now_value + int(ticket_seconds)
                _raw, expires_at = self.values[str(authority_key)]
                self._set(str(authority_key), json.dumps(authority), expires_at)
                return json.dumps({"status": "issued"})

            if "ai-platform:auth-context-rotate-reconcile:v2" in script:
                authority_key, context_key, digest, generation, handle = args
                now_value = self.now
                authority_raw = self._get(str(authority_key), now_value)
                context_raw = self._get(str(context_key), now_value)
                if authority_raw is None or context_raw is None:
                    return json.dumps({"status": "stale"})
                try:
                    authority = json.loads(authority_raw)
                    context = json.loads(context_raw)
                except json.JSONDecodeError:
                    return json.dumps({"status": "stale"})
                if (
                    not self._v2_records_match(authority, context, digest, generation, handle)
                    or not self._v2_ttl_consistent(str(authority_key), str(context_key), now_value)
                ):
                    return json.dumps({"status": "stale"})
                return json.dumps({"status": "reconciled"})

            if "ai-platform:auth-context-rotate-target-repair:v2" in script:
                (
                    authority_key,
                    old_context_key,
                    target_context_key,
                    digest,
                    base_generation,
                    target_generation,
                    old_handle,
                    target_handle,
                ) = args
                now_value = self.now
                authority_raw = self._get(str(authority_key), now_value)
                old_raw = self._get(str(old_context_key), now_value)
                target_raw = self._get(str(target_context_key), now_value)
                if authority_raw is None or old_raw is None:
                    return json.dumps({"status": "stale"})
                try:
                    authority = json.loads(authority_raw)
                    old_context = json.loads(old_raw)
                    target_context = json.loads(target_raw) if target_raw is not None else None
                except json.JSONDecodeError:
                    return json.dumps({"status": "stale"})
                if (
                    not self._is_valid_v2_context_record(old_context)
                    or old_context.get("incarnation_digest") != digest
                    or old_context.get("generation") != base_generation
                ):
                    return json.dumps({"status": "stale"})
                if target_context is not None:
                    if (
                        not self._v2_records_match(
                            authority,
                            target_context,
                            digest,
                            target_generation,
                            target_handle,
                        )
                        or not self._v2_ttl_consistent(
                            str(authority_key),
                            str(target_context_key),
                            now_value,
                        )
                    ):
                        return json.dumps({"status": "stale"})
                    return json.dumps(
                        {
                            "status": "target_repaired",
                            "ttl_milliseconds": min(
                                self._pttl(str(authority_key), now_value),
                                self._pttl(str(target_context_key), now_value),
                            ),
                        }
                    )
                if (
                    self._v2_records_match(
                        authority,
                        old_context,
                        digest,
                        base_generation,
                        old_handle,
                    )
                    and self._v2_ttl_consistent(
                        str(authority_key),
                        str(old_context_key),
                        now_value,
                    )
                ):
                    return json.dumps({"status": "authority_base"})
                return json.dumps({"status": "stale"})

            if "ai-platform:auth-context-rotate:v2" in script:
                (
                    authority_key,
                    old_context_key,
                    new_context_key,
                    digest,
                    old_generation,
                    old_handle,
                    new_handle,
                    nonce_binding,
                    ttl_seconds,
                    ticket_digest,
                ) = args
                now_value = self.now
                authority_raw = self._get(str(authority_key), now_value)
                old_raw = self._get(str(old_context_key), now_value)
                if authority_raw is None or old_raw is None:
                    return json.dumps({"status": "stale"})
                if self._get(str(new_context_key), now_value) is not None:
                    return json.dumps({"status": "generation_conflict"})
                try:
                    authority = json.loads(authority_raw)
                    old_context = json.loads(old_raw)
                except json.JSONDecodeError:
                    return json.dumps({"status": "corrupt"})
                if (
                    not self._v2_records_match(authority, old_context, digest, old_generation, old_handle)
                    or not self._v2_ttl_consistent(str(authority_key), str(old_context_key), now_value)
                ):
                    return json.dumps({"status": "stale"})
                if (
                    authority.get("rotation_ticket_digest") != ticket_digest
                    or authority.get("rotation_ticket_generation") != old_generation
                    or authority.get("rotation_ticket_deadline", 0) < now_value
                ):
                    return json.dumps({"status": "invalid_ticket"})
                new_generation = int(old_generation) + 1
                if new_generation > AUTH_CONTEXT_MAX_EPOCH:
                    return json.dumps({"status": "corrupt"})
                new_context = {
                    "schema_version": 2,
                    "protocol_version": 2,
                    "incarnation_digest": str(digest),
                    "generation": new_generation,
                    "nonce_binding": str(nonce_binding),
                    "principal": None,
                    "tenant_user_subject_epoch": 0,
                    "operation_epoch": 0,
                    "operation_token": "",
                    "operation_kind": "",
                    "lease_until": 0,
                }
                new_authority = {
                    "schema_version": 2,
                    "incarnation_digest": str(digest),
                    "generation": new_generation,
                    "context_handle": str(new_handle),
                }
                # Mirror the production Lua PTTL carry-over: rotation fences
                # the browser context but never gives the session a new life.
                _authority_raw, expires_at = self.values[str(authority_key)]
                if expires_at <= now_value:
                    return json.dumps({"status": "stale"})
                self._set(str(new_context_key), json.dumps(new_context), expires_at)
                self._set(str(authority_key), json.dumps(new_authority), expires_at)
                return json.dumps(
                    {
                        "status": "rotated",
                        "generation": new_generation,
                        "ttl_milliseconds": max(1, int((expires_at - now_value) * 1000)),
                    }
                )

            if "ai-platform:auth-context-principal:v2" in script:
                authority_key, context_key, digest, generation, handle = args
                now_value = self.now
                authority_raw = self._get(str(authority_key), now_value)
                context_raw = self._get(str(context_key), now_value)
                if authority_raw is None or context_raw is None:
                    return json.dumps({"status": "stale"})
                try:
                    authority = json.loads(authority_raw)
                    context = json.loads(context_raw)
                except json.JSONDecodeError:
                    return json.dumps({"status": "corrupt"})
                if (
                    not self._v2_records_match(authority, context, digest, generation, handle)
                    or not self._v2_ttl_consistent(str(authority_key), str(context_key), now_value)
                ):
                    return json.dumps({"status": "stale"})
                return json.dumps({"status": "principal", "principal": context["principal"]})

            if "ai-platform:auth-context-begin:v2" in script:
                authority_key, context_key, digest, generation, handle, lease_seconds, token, kind = args
                now_value = self.now
                authority_raw = self._get(str(authority_key), now_value)
                context_raw = self._get(str(context_key), now_value)
                if authority_raw is None or context_raw is None:
                    return json.dumps({"status": "stale"})
                try:
                    authority = json.loads(authority_raw)
                    context = json.loads(context_raw)
                except json.JSONDecodeError:
                    return json.dumps({"status": "corrupt"})
                if (
                    not self._v2_records_match(authority, context, digest, generation, handle)
                    or not self._v2_ttl_consistent(str(authority_key), str(context_key), now_value)
                ):
                    return json.dumps({"status": "stale"})
                if context["operation_epoch"] >= AUTH_CONTEXT_MAX_EPOCH:
                    return json.dumps({"status": "corrupt"})
                context["operation_epoch"] += 1
                context["operation_token"] = str(token)
                context["operation_kind"] = str(kind)
                context["lease_until"] = now_value + int(lease_seconds)
                _raw, expires_at = self.values[str(context_key)]
                self._set(str(context_key), json.dumps(context), expires_at)
                return json.dumps(
                    {"status": "begun", "operation_epoch": context["operation_epoch"]}
                )

            if "ai-platform:auth-context-commit:v2" in script:
                authority_key, context_key, digest, generation, handle, epoch, token, principal_json = args
                now_value = self.now
                authority_raw = self._get(str(authority_key), now_value)
                context_raw = self._get(str(context_key), now_value)
                if authority_raw is None or context_raw is None:
                    return json.dumps({"status": "stale"})
                try:
                    authority = json.loads(authority_raw)
                    context = json.loads(context_raw)
                    principal = json.loads(str(principal_json))
                except (TypeError, ValueError, json.JSONDecodeError):
                    return json.dumps({"status": "corrupt"})
                if (
                    not self._v2_records_match(authority, context, digest, generation, handle)
                    or not self._v2_ttl_consistent(str(authority_key), str(context_key), now_value)
                ):
                    return json.dumps({"status": "stale"})
                if context["operation_epoch"] != epoch or context["operation_token"] != token:
                    return json.dumps({"status": "superseded"})
                if context["lease_until"] <= now_value:
                    return json.dumps({"status": "expired"})
                if principal is not None and not isinstance(principal, dict):
                    return json.dumps({"status": "corrupt"})
                if context["tenant_user_subject_epoch"] >= AUTH_CONTEXT_MAX_EPOCH:
                    return json.dumps({"status": "corrupt"})
                context["principal"] = principal
                context["tenant_user_subject_epoch"] += 1
                context["operation_token"] = ""
                context["operation_kind"] = ""
                context["lease_until"] = 0
                _raw, expires_at = self.values[str(context_key)]
                self._set(str(context_key), json.dumps(context), expires_at)
                return json.dumps(
                    {
                        "status": "committed",
                        "tenant_user_subject_epoch": context["tenant_user_subject_epoch"],
                    }
                )

            if "ai-platform:auth-oauth-state-issue:v2" in script:
                (
                    authority_key,
                    context_key,
                    state_key,
                    digest,
                    generation,
                    handle,
                    _provider,
                    epoch,
                    token,
                    state_json,
                    state_ttl,
                ) = args
                now_value = self.now
                authority_raw = self._get(str(authority_key), now_value)
                context_raw = self._get(str(context_key), now_value)
                if authority_raw is None or context_raw is None:
                    return json.dumps({"status": "stale"})
                try:
                    authority = json.loads(authority_raw)
                    context = json.loads(context_raw)
                except json.JSONDecodeError:
                    return json.dumps({"status": "corrupt"})
                if (
                    not self._v2_records_match(authority, context, digest, generation, handle)
                    or not self._v2_ttl_consistent(str(authority_key), str(context_key), now_value)
                ):
                    return json.dumps({"status": "stale"})
                if context["operation_epoch"] != epoch or context["operation_token"] != token:
                    return json.dumps({"status": "superseded"})
                self._set(str(state_key), str(state_json), now_value + int(state_ttl))
                return json.dumps({"status": "issued"})

            if "ai-platform:auth-oauth-state-consume:v2" in script:
                authority_key, context_key, state_key, digest, generation, handle, provider = args
                now_value = self.now
                authority_raw = self._get(str(authority_key), now_value)
                context_raw = self._get(str(context_key), now_value)
                state_raw = self._get(str(state_key), now_value)
                if authority_raw is None or context_raw is None:
                    return json.dumps({"status": "stale"})
                try:
                    authority = json.loads(authority_raw)
                    context = json.loads(context_raw)
                except json.JSONDecodeError:
                    return json.dumps({"status": "corrupt"})
                if (
                    not self._v2_records_match(authority, context, digest, generation, handle)
                    or not self._v2_ttl_consistent(str(authority_key), str(context_key), now_value)
                ):
                    return json.dumps({"status": "stale"})
                if state_raw is None:
                    return json.dumps({"status": "missing"})
                try:
                    state = json.loads(state_raw)
                except json.JSONDecodeError:
                    return json.dumps({"status": "stale"})
                self.values.pop(str(state_key), None)
                if (
                    not isinstance(state, dict)
                    or state.get("context_handle") != handle
                    or state.get("provider") != provider
                    or state.get("incarnation_digest") != digest
                    or state.get("generation") != generation
                    or not self._is_valid_epoch(state.get("operation_epoch"))
                    or state["operation_epoch"] < 1
                    or not isinstance(state.get("operation_token"), str)
                    or not state["operation_token"]
                ):
                    return json.dumps({"status": "invalid"})
                return json.dumps(
                    {
                        "status": "consumed",
                        "operation_epoch": state["operation_epoch"],
                        "operation_token": state["operation_token"],
                    }
                )

            if "ai-platform:auth-context-bootstrap:v1" in script:
                key, expected_binding, ttl_seconds, request_has_matching_context = args
                now_value = self.now
                if not self._is_valid_epoch(ttl_seconds) or ttl_seconds < 1:
                    return json.dumps({"status": "corrupt"})
                existing = self._get(str(key), now_value)
                if existing is None:
                    self._set(
                        str(key),
                        json.dumps(
                            {
                                "schema_version": 1,
                                "nonce_binding": str(expected_binding),
                                "principal": None,
                                "tenant_user_subject_epoch": 0,
                                "operation_epoch": 0,
                                "operation_token": "",
                                "operation_kind": "",
                                "lease_until": 0,
                            }
                        ),
                        now_value + int(ttl_seconds),
                    )
                    return json.dumps({"status": "created"})
                try:
                    record = json.loads(existing)
                except json.JSONDecodeError:
                    return json.dumps({"status": "corrupt"})
                if (
                    not self._is_valid_context_record(record)
                    or record.get("nonce_binding") != expected_binding
                ):
                    return json.dumps({"status": "corrupt"})
                if (
                    str(request_has_matching_context) == "1"
                    or self._is_pristine_anonymous(record)
                ):
                    return json.dumps({"status": "existing"})
                return json.dumps({"status": "rebootstrap_required"})

            if "ai-platform:auth-context-begin:v1" in script:
                key, lease_seconds, operation_token, operation_kind = args
                now_value = self.now
                if (
                    not self._is_valid_epoch(lease_seconds)
                    or lease_seconds < 1
                    or not isinstance(operation_token, str)
                    or not operation_token
                    or not isinstance(operation_kind, str)
                    or not operation_kind
                ):
                    return json.dumps({"status": "corrupt"})
                existing = self._get(str(key), now_value)
                if existing is None:
                    return json.dumps({"status": "missing"})
                try:
                    record = json.loads(existing)
                except json.JSONDecodeError:
                    return json.dumps({"status": "corrupt"})
                if not self._is_valid_context_record(record):
                    return json.dumps({"status": "corrupt"})
                if record["operation_epoch"] >= AUTH_CONTEXT_MAX_EPOCH:
                    return json.dumps({"status": "corrupt"})
                operation_epoch = record["operation_epoch"] + 1
                record["operation_epoch"] = operation_epoch
                record["operation_token"] = operation_token
                record["operation_kind"] = operation_kind
                record["lease_until"] = now_value + lease_seconds
                _, expires_at = self.values[str(key)]
                self._set(str(key), json.dumps(record), expires_at)
                return json.dumps(
                    {
                        "status": "begun",
                        "operation_epoch": operation_epoch,
                    }
                )

            if "ai-platform:auth-context-commit:v1" in script:
                key, operation_epoch, operation_token, principal_json = args
                now_value = self.now
                existing = self._get(str(key), now_value)
                if existing is None:
                    return json.dumps({"status": "missing"})
                try:
                    record = json.loads(existing)
                    principal = json.loads(str(principal_json))
                except (TypeError, ValueError, json.JSONDecodeError):
                    return json.dumps({"status": "corrupt"})
                if (
                    not self._is_valid_context_record(record)
                    or not self._is_valid_epoch(operation_epoch)
                    or operation_epoch < 1
                    or not isinstance(operation_token, str)
                    or not operation_token
                    or (principal is not None and not isinstance(principal, dict))
                ):
                    return json.dumps({"status": "corrupt"})
                if (
                    record.get("operation_epoch") != operation_epoch
                    or record.get("operation_token") != operation_token
                ):
                    return json.dumps({"status": "superseded"})
                if record["tenant_user_subject_epoch"] >= AUTH_CONTEXT_MAX_EPOCH:
                    return json.dumps({"status": "corrupt"})
                if record.get("lease_until") <= now_value:
                    return json.dumps({"status": "expired"})
                record["principal"] = principal
                record["tenant_user_subject_epoch"] += 1
                record["operation_token"] = ""
                record["operation_kind"] = ""
                record["lease_until"] = 0
                _, expires_at = self.values[str(key)]
                self._set(str(key), json.dumps(record), expires_at)
                return json.dumps({"status": "committed"})

            if "ai-platform:auth-oauth-state-consume:v1" in script:
                state_key, expected_context, expected_provider = args
                now_value = self.now
                existing = self._get(str(state_key), now_value)
                if existing is None:
                    return json.dumps({"status": "missing"})
                self.values.pop(str(state_key), None)
                try:
                    record = json.loads(existing)
                except json.JSONDecodeError:
                    return json.dumps({"status": "corrupt"})
                if (
                    not isinstance(record, dict)
                    or record.get("context_handle") != expected_context
                    or record.get("provider") != expected_provider
                ):
                    return json.dumps({"status": "invalid"})
                if (
                    not self._is_valid_epoch(record.get("operation_epoch"))
                    or record["operation_epoch"] < 1
                    or not isinstance(record.get("operation_token"), str)
                    or not record["operation_token"]
                ):
                    return json.dumps({"status": "corrupt"})
                return json.dumps(
                    {
                        "status": "consumed",
                        "operation_epoch": record["operation_epoch"],
                        "operation_token": record["operation_token"],
                    }
                )

            raise AssertionError(f"unexpected script: {script[:80]}")

    async def get(self, key: str) -> str | None:
        self._require_available()
        with self.lock:
            return self._get(key, self.now)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._require_available()
        with self.lock:
            self._set(key, value, self.now + int(ex or 60))
        return True

    async def aclose(self) -> None:
        return None


@asynccontextmanager
async def fake_transaction():
    yield object()


def auth_settings(**overrides):
    values = {
        "ai_session_secret": "test-session-secret-with-at-least-32-bytes",
        "ai_session_max_age_seconds": 3600,
        "ai_session_cookie_name": "ai_platform_session",
        "ai_session_cookie_secure": False,
        "auth_context_cookie_name": "ai_platform_auth_context",
        "auth_context_cookie_secure": False,
        "auth_context_lease_seconds": 30,
        "auth_context_secret": "",
        "trusted_principal_secret": "gateway-secret",
        "frontend_poc_auth_enabled": False,
        "default_tenant_id": "default",
        "existing_auth_timeout_seconds": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def install_auth_context_dependencies(monkeypatch, redis: FakeAuthRedis, settings=None):
    settings = settings or auth_settings()

    async def fake_ensure_user(*_args, **_kwargs):
        return None

    async def fake_append_audit_log(*_args, **_kwargs):
        return "audit-id"

    monkeypatch.setattr("app.auth_sessions.get_redis", lambda: redis)
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.lambchat_compat.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.auth.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.auth.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.auth.append_audit_log", fake_append_audit_log)
    return settings


def bootstrap(client: TestClient, nonce: str = "A" * 43) -> str:
    response = client.post("/api/ai/auth/bootstrap", json={"nonce": nonce})
    assert response.status_code == 200, response.text
    cookie = response.cookies.get("ai_platform_auth_context")
    assert cookie
    return cookie


def install_company_login(monkeypatch, *, gate_a: threading.Event | None = None, release_a: threading.Event | None = None):
    async def fake_login(username: str, _password: str):
        if username == "user-a" and gate_a is not None and release_a is not None:
            gate_a.set()
            await asyncio.to_thread(release_a.wait, 5)
        return {
            "workId": username,
            "userName": username,
            "cnName": username.title(),
        }

    async def fake_user_info(_work_id: str):
        return {"roles": ["user"]}

    monkeypatch.setattr("app.routes.auth.call_existing_login", fake_login)
    monkeypatch.setattr("app.routes.auth.call_existing_user_info", fake_user_info)


def test_bootstrap_concurrent_and_late_requests_set_one_stable_context_cookie(monkeypatch):
    redis = FakeAuthRedis()
    install_auth_context_dependencies(monkeypatch, redis)
    clients = [TestClient(create_app()), TestClient(create_app())]
    responses = []

    def bootstrap_client(client: TestClient):
        responses.append(client.post("/api/ai/auth/bootstrap", json={"nonce": "A" * 43}))

    threads = [threading.Thread(target=bootstrap_client, args=(client,)) for client in clients]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    late_response = clients[0].post("/api/ai/auth/bootstrap", json={"nonce": "A" * 43})
    assert all(response.status_code == 200 for response in responses)
    cookies = [response.cookies["ai_platform_auth_context"] for response in [*responses, late_response]]
    assert len(set(cookies)) == 1
    assert len(redis.values) == 1


@pytest.mark.asyncio
async def test_v2_authority_rejects_a_physically_restored_old_cookie_and_repairs_current_identity(
    monkeypatch,
):
    """A late V2 header may arrive, but it cannot re-authorize old generation."""

    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(monkeypatch, redis)
    incarnation = "I" * 43
    first_nonce = "A" * 43
    created = await auth_sessions.bootstrap_auth_context_v2(
        first_nonce,
        incarnation,
        1,
        "",
        settings,
    )
    assert created.status == "ready"
    assert created.identity is not None
    old_cookie = auth_sessions.auth_context_v2_cookie_for_identity(created.identity, settings)

    ticket_result = await auth_sessions.bootstrap_auth_context_v2(
        "B" * 43,
        incarnation,
        1,
        old_cookie,
        settings,
    )
    assert ticket_result.status == "rebootstrap_required"
    assert ticket_result.rotation_ticket

    rotated = await auth_sessions.bootstrap_auth_context_v2(
        "C" * 43,
        incarnation,
        2,
        old_cookie,
        settings,
        rotation_ticket=ticket_result.rotation_ticket,
    )
    assert rotated.status == "ready"
    assert rotated.identity is not None
    current_cookie = auth_sessions.auth_context_v2_cookie_for_identity(rotated.identity, settings)

    with pytest.raises(auth_sessions.AuthContextError, match="auth_context_stale"):
        await auth_sessions.principal_for_cookie(old_cookie, settings)

    repaired = await auth_sessions.bootstrap_auth_context_v2(
        "C" * 43,
        incarnation,
        2,
        old_cookie,
        settings,
    )
    assert repaired.set_cookie is True
    assert auth_sessions.auth_context_v2_cookie_for_identity(repaired.identity, settings) == current_cookie


def test_v2_route_emits_only_authorized_generation_cookies_and_repairs_a_late_old_header(monkeypatch):
    redis = FakeAuthRedis()
    install_auth_context_dependencies(monkeypatch, redis)
    client = TestClient(create_app())
    incarnation = "I" * 43

    created = client.post(
        "/api/ai/auth/bootstrap",
        json={
            "nonce": "A" * 43,
            "protocol_version": 2,
            "browser_incarnation": incarnation,
            "generation": 1,
        },
    )
    assert created.status_code == 200, created.text
    old_cookie = created.cookies["ai_platform_auth_context"]
    assert old_cookie.startswith("v2.")

    exact = client.post(
        "/api/ai/auth/bootstrap",
        json={
            "nonce": "A" * 43,
            "protocol_version": 2,
            "browser_incarnation": incarnation,
            "generation": 1,
        },
    )
    assert exact.status_code == 200
    assert "set-cookie" not in exact.headers

    ticket = client.post(
        "/api/ai/auth/bootstrap",
        json={
            "nonce": "B" * 43,
            "protocol_version": 2,
            "browser_incarnation": incarnation,
            "generation": 1,
        },
    )
    assert ticket.status_code == 200
    assert ticket.json()["status"] == "rebootstrap_required"
    assert "set-cookie" not in ticket.headers

    rotated = client.post(
        "/api/ai/auth/bootstrap",
        json={
            "nonce": "C" * 43,
            "protocol_version": 2,
            "browser_incarnation": incarnation,
            "generation": 2,
            "rotation_ticket": ticket.json()["rotation_ticket"],
        },
    )
    assert rotated.status_code == 200, rotated.text
    current_cookie = rotated.cookies["ai_platform_auth_context"]
    assert current_cookie != old_cookie

    reconciled = client.post(
        "/api/ai/auth/bootstrap",
        json={
            "nonce": "C" * 43,
            "protocol_version": 2,
            "browser_incarnation": incarnation,
            "generation": 2,
            "rotation_ticket": ticket.json()["rotation_ticket"],
        },
    )
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json() == {"status": "ready", "protocol_version": 2, "generation": 2}
    assert "set-cookie" not in reconciled.headers

    stale_me = client.get(
        "/api/ai/auth/me",
        headers={"Cookie": f"ai_platform_auth_context={old_cookie}"},
    )
    assert stale_me.status_code == 409
    assert stale_me.json()["detail"] == "auth_context_stale"

    repaired = client.post(
        "/api/ai/auth/bootstrap",
        headers={"Cookie": f"ai_platform_auth_context={old_cookie}"},
        json={
            "nonce": "C" * 43,
            "protocol_version": 2,
            "browser_incarnation": incarnation,
            "generation": 2,
        },
    )
    assert repaired.status_code == 200, repaired.text
    assert repaired.cookies["ai_platform_auth_context"] == current_cookie


@pytest.mark.asyncio
async def test_v2_rotation_reconciles_only_the_exact_server_accepted_target(monkeypatch):
    """A lost local promotion can recover only from the exact target identity."""

    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(monkeypatch, redis)
    incarnation = "I" * 43
    old = await auth_sessions.bootstrap_auth_context_v2("A" * 43, incarnation, 1, "", settings)
    assert old.identity is not None
    old_cookie = auth_sessions.auth_context_v2_cookie_for_identity(old.identity, settings)

    ticket = await auth_sessions.bootstrap_auth_context_v2(
        "B" * 43,
        incarnation,
        1,
        old_cookie,
        settings,
    )
    assert ticket.rotation_ticket
    rotated = await auth_sessions.bootstrap_auth_context_v2(
        "C" * 43,
        incarnation,
        2,
        old_cookie,
        settings,
        rotation_ticket=ticket.rotation_ticket,
    )
    assert rotated.identity is not None
    target_cookie = auth_sessions.auth_context_v2_cookie_for_identity(rotated.identity, settings)

    reconciled = await auth_sessions.bootstrap_auth_context_v2(
        "C" * 43,
        incarnation,
        2,
        target_cookie,
        settings,
        rotation_ticket=ticket.rotation_ticket,
    )
    assert reconciled.status == "ready"
    assert reconciled.identity == rotated.identity
    assert reconciled.set_cookie is False

    with pytest.raises(auth_sessions.AuthContextError, match="auth_context_stale"):
        await auth_sessions.bootstrap_auth_context_v2(
            "D" * 43,
            incarnation,
            2,
            target_cookie,
            settings,
            rotation_ticket=ticket.rotation_ticket,
        )


@pytest.mark.asyncio
async def test_v2_target_repair_reissues_only_the_exact_committed_target_for_a_base_cookie(monkeypatch):
    """A lost rotation response repairs only the old handle that Redis moved from."""

    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(monkeypatch, redis)
    incarnation = "I" * 43
    old = await auth_sessions.bootstrap_auth_context_v2("A" * 43, incarnation, 1, "", settings)
    assert old.identity is not None
    old_cookie = auth_sessions.auth_context_v2_cookie_for_identity(old.identity, settings)
    ticket = await auth_sessions.bootstrap_auth_context_v2("B" * 43, incarnation, 1, old_cookie, settings)
    assert ticket.rotation_ticket
    rotated = await auth_sessions.bootstrap_auth_context_v2(
        "C" * 43,
        incarnation,
        2,
        old_cookie,
        settings,
        rotation_ticket=ticket.rotation_ticket,
    )
    assert rotated.identity is not None
    authority_key = next(key for key in redis.values if key.startswith("ai-platform:auth-browser-authority:"))
    original_authority = redis.values[authority_key]
    target_key = f"ai-platform:auth-context:{rotated.identity.context_handle}"
    original_target = redis.values[target_key]

    repaired = await auth_sessions.bootstrap_auth_context_v2("C" * 43, incarnation, 2, old_cookie, settings)
    assert repaired.status == "ready"
    assert repaired.identity == rotated.identity
    assert repaired.set_cookie is True
    assert repaired.cookie_max_age_seconds is not None
    assert redis.values[authority_key] == original_authority
    assert redis.values[target_key] == original_target

    forged_old_cookie = auth_sessions.auth_context_v2_cookie_for_identity(
        auth_sessions.V2AuthContextIdentity(
            incarnation=incarnation,
            incarnation_digest=rotated.identity.incarnation_digest,
            generation=1,
            context_handle=auth_sessions.auth_context_handle_for_nonce("D" * 43, settings),
        ),
        settings,
    )
    with pytest.raises(auth_sessions.AuthContextError, match="auth_context_stale"):
        await auth_sessions.bootstrap_auth_context_v2("C" * 43, incarnation, 2, forged_old_cookie, settings)


@pytest.mark.asyncio
async def test_v2_target_repair_rejects_wrong_target_and_exposes_only_the_base_reissue_branch(monkeypatch):
    """Only exact base authority may ask the client to reissue a base ticket."""

    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(monkeypatch, redis)
    incarnation = "I" * 43
    base = await auth_sessions.bootstrap_auth_context_v2("A" * 43, incarnation, 1, "", settings)
    assert base.identity is not None
    base_cookie = auth_sessions.auth_context_v2_cookie_for_identity(base.identity, settings)

    with pytest.raises(auth_sessions.AuthContextError, match="auth_context_rebootstrap_required"):
        await auth_sessions.bootstrap_auth_context_v2("B" * 43, incarnation, 2, base_cookie, settings)

    ticket = await auth_sessions.bootstrap_auth_context_v2("B" * 43, incarnation, 1, base_cookie, settings)
    assert ticket.rotation_ticket
    await auth_sessions.bootstrap_auth_context_v2(
        "C" * 43,
        incarnation,
        2,
        base_cookie,
        settings,
        rotation_ticket=ticket.rotation_ticket,
    )
    for nonce, request_incarnation, request_generation in (
        ("D" * 43, incarnation, 2),
        ("C" * 43, "J" * 43, 2),
        ("C" * 43, incarnation, 3),
    ):
        with pytest.raises(auth_sessions.AuthContextError, match="auth_context_stale"):
            await auth_sessions.bootstrap_auth_context_v2(
                nonce,
                request_incarnation,
                request_generation,
                base_cookie,
                settings,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "corrupt", "ttl_mismatch"])
async def test_v2_target_repair_rejects_missing_corrupt_or_ttl_mismatched_target(monkeypatch, failure):
    """Target repair has no best-effort path when its target proof is incomplete."""

    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(monkeypatch, redis)
    incarnation = "I" * 43
    base = await auth_sessions.bootstrap_auth_context_v2("A" * 43, incarnation, 1, "", settings)
    assert base.identity is not None
    base_cookie = auth_sessions.auth_context_v2_cookie_for_identity(base.identity, settings)
    ticket = await auth_sessions.bootstrap_auth_context_v2("B" * 43, incarnation, 1, base_cookie, settings)
    assert ticket.rotation_ticket
    rotated = await auth_sessions.bootstrap_auth_context_v2(
        "C" * 43,
        incarnation,
        2,
        base_cookie,
        settings,
        rotation_ticket=ticket.rotation_ticket,
    )
    assert rotated.identity is not None
    target_key = f"ai-platform:auth-context:{rotated.identity.context_handle}"
    if failure == "missing":
        redis.values.pop(target_key)
    else:
        target_raw, target_expiry = redis.values[target_key]
        if failure == "corrupt":
            redis.values[target_key] = ("{corrupt", target_expiry)
        else:
            redis.values[target_key] = (target_raw, target_expiry - 2)

    with pytest.raises(auth_sessions.AuthContextError, match="auth_context_stale"):
        await auth_sessions.bootstrap_auth_context_v2("C" * 43, incarnation, 2, base_cookie, settings)


@pytest.mark.asyncio
async def test_v2_partial_rotation_ticket_authority_fails_closed_for_all_fenced_operations(monkeypatch):
    """Ticket digest/generation/deadline form one all-or-nothing authority tuple."""

    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(monkeypatch, redis)
    incarnation = "I" * 43
    created = await auth_sessions.bootstrap_auth_context_v2("A" * 43, incarnation, 1, "", settings)
    assert created.identity is not None
    cookie = auth_sessions.auth_context_v2_cookie_for_identity(created.identity, settings)
    operation = await auth_sessions.begin_auth_operation_for_cookie(cookie, "oauth:github", settings)
    state = await auth_sessions.issue_oauth_state(
        operation.context_handle,
        "github",
        operation,
        settings,
    )
    ticket = await auth_sessions.bootstrap_auth_context_v2(
        "B" * 43,
        incarnation,
        1,
        cookie,
        settings,
    )
    assert ticket.rotation_ticket

    authority_key = next(key for key in redis.values if key.startswith("ai-platform:auth-browser-authority:"))
    raw_authority, expires_at = redis.values[authority_key]
    authority = json.loads(raw_authority)
    authority["rotation_ticket_digest"] = "T" * 43
    authority.pop("rotation_ticket_generation", None)
    authority.pop("rotation_ticket_deadline", None)
    redis.values[authority_key] = (json.dumps(authority), expires_at)

    for action in (
        lambda: auth_sessions.principal_for_cookie(cookie, settings),
        lambda: auth_sessions.begin_auth_operation_for_cookie(cookie, "login", settings),
        lambda: auth_sessions.commit_auth_operation(operation, None),
        lambda: auth_sessions.issue_oauth_state(operation.context_handle, "github", operation, settings),
        lambda: auth_sessions.consume_oauth_state_for_cookie(cookie, "github", state, settings),
        lambda: auth_sessions.bootstrap_auth_context_v2("C" * 43, incarnation, 2, cookie, settings, rotation_ticket=ticket.rotation_ticket),
    ):
        with pytest.raises(auth_sessions.AuthContextError, match="auth_context_stale"):
            await action()
    with pytest.raises(auth_sessions.AuthContextError, match="auth_context_unavailable"):
        await auth_sessions.bootstrap_auth_context_v2("A" * 43, incarnation, 1, cookie, settings)


@pytest.mark.asyncio
async def test_v2_fake_lua_model_enforces_ttl_consistency_for_bootstrap_rotation_and_operations(monkeypatch):
    """The deterministic eval model mirrors Lua fail-closed TTL fences, not Redis itself."""

    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(monkeypatch, redis)
    incarnation = "I" * 43
    created = await auth_sessions.bootstrap_auth_context_v2("A" * 43, incarnation, 1, "", settings)
    assert created.identity is not None
    cookie = auth_sessions.auth_context_v2_cookie_for_identity(created.identity, settings)
    operation = await auth_sessions.begin_auth_operation_for_cookie(cookie, "oauth:github", settings)
    state = await auth_sessions.issue_oauth_state(
        operation.context_handle,
        "github",
        operation,
        settings,
    )
    ticket = await auth_sessions.bootstrap_auth_context_v2(
        "B" * 43,
        incarnation,
        1,
        cookie,
        settings,
    )
    assert ticket.rotation_ticket

    authority_key = next(key for key in redis.values if key.startswith("ai-platform:auth-browser-authority:"))
    raw_authority, authority_expiry = redis.values[authority_key]
    redis.values[authority_key] = (raw_authority, authority_expiry - 2)

    for action in (
        lambda: auth_sessions.bootstrap_auth_context_v2("A" * 43, incarnation, 1, cookie, settings),
        lambda: auth_sessions.begin_auth_operation_for_cookie(cookie, "login", settings),
        lambda: auth_sessions.commit_auth_operation(operation, None),
        lambda: auth_sessions.issue_oauth_state(operation.context_handle, "github", operation, settings),
        lambda: auth_sessions.consume_oauth_state_for_cookie(cookie, "github", state, settings),
        lambda: auth_sessions.bootstrap_auth_context_v2("C" * 43, incarnation, 2, cookie, settings, rotation_ticket=ticket.rotation_ticket),
    ):
        with pytest.raises(auth_sessions.AuthContextError, match="auth_context_stale"):
            await action()


def test_v1_bootstrap_cannot_downgrade_a_signed_v2_cookie(monkeypatch):
    """A Web Locks V1 request must never overwrite a migrated V2 cookie."""

    redis = FakeAuthRedis()
    install_auth_context_dependencies(monkeypatch, redis)
    v2_client = TestClient(create_app())
    v2 = v2_client.post(
        "/api/ai/auth/bootstrap",
        json={
            "nonce": "A" * 43,
            "protocol_version": 2,
            "browser_incarnation": "I" * 43,
            "generation": 1,
        },
    )
    assert v2.status_code == 200, v2.text
    v2_cookie = v2.cookies["ai_platform_auth_context"]
    assert v2_cookie.startswith("v2.")

    downgraded = TestClient(create_app()).post(
        "/api/ai/auth/bootstrap",
        headers={"Cookie": f"ai_platform_auth_context={v2_cookie}"},
        json={"nonce": "Z" * 43},
    )
    assert downgraded.status_code == 409
    assert downgraded.json()["detail"] == "auth_context_stale"
    assert "set-cookie" not in downgraded.headers

    ordinary_v1 = TestClient(create_app()).post(
        "/api/ai/auth/bootstrap",
        json={"nonce": "Y" * 43},
    )
    assert ordinary_v1.status_code == 200
    assert ordinary_v1.cookies["ai_platform_auth_context"].startswith("v1.")


def test_v1_matching_context_migrates_without_extending_and_nonmatching_authenticated_v1_fails_closed(monkeypatch):
    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(monkeypatch, redis)
    install_company_login(monkeypatch)
    client = TestClient(create_app())
    legacy_cookie = bootstrap(client, "M" * 43)
    login = client.post("/api/ai/auth/login", json={"username": "user-a", "password": "test-password"})
    assert login.status_code == 200
    _raw, original_expiry = next(
        value
        for key, value in redis.values.items()
        if key.endswith(legacy_cookie)
    )

    migrated = client.post(
        "/api/ai/auth/bootstrap",
        json={
            "nonce": "M" * 43,
            "protocol_version": 2,
            "browser_incarnation": "I" * 43,
            "generation": 1,
        },
    )
    assert migrated.status_code == 200, migrated.text
    assert migrated.cookies["ai_platform_auth_context"].startswith("v2.")
    authority_expiry = next(
        expiry
        for key, (_raw, expiry) in redis.values.items()
        if key.startswith("ai-platform:auth-browser-authority:")
    )
    assert authority_expiry == original_expiry

    raw_v1_after_migration = client.get(
        "/api/ai/auth/me",
        headers={"Cookie": f"ai_platform_auth_context={legacy_cookie}"},
    )
    assert raw_v1_after_migration.status_code == 409
    assert raw_v1_after_migration.json()["detail"] == "auth_context_stale"

    conflicting = TestClient(create_app()).post(
        "/api/ai/auth/bootstrap",
        headers={"Cookie": f"ai_platform_auth_context={legacy_cookie}"},
        json={
            "nonce": "N" * 43,
            "protocol_version": 2,
            "browser_incarnation": "J" * 43,
            "generation": 1,
        },
    )
    assert conflicting.status_code == 409
    assert conflicting.json()["detail"] == "auth_context_stale"
    assert "set-cookie" not in conflicting.headers


@pytest.mark.asyncio
async def test_v2_operation_and_oauth_state_are_fenced_by_the_authority_generation(monkeypatch):
    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(monkeypatch, redis)
    created = await auth_sessions.bootstrap_auth_context_v2(
        "A" * 43,
        "I" * 43,
        1,
        "",
        settings,
    )
    assert created.identity is not None
    cookie = auth_sessions.auth_context_v2_cookie_for_identity(created.identity, settings)

    older = await auth_sessions.begin_auth_operation_for_cookie(cookie, "login", settings)
    newer = await auth_sessions.begin_auth_operation_for_cookie(cookie, "login", settings)
    principal = {
        "user_id": "test-user-b",
        "display_name": "Test User B",
        "tenant_id": "test-tenant-b",
        "department_id": "",
        "roles": ["user"],
        "permissions": ["chat:read"],
        "source": "test",
    }
    assert await auth_sessions.commit_auth_operation(newer, principal) == "committed"
    assert await auth_sessions.commit_auth_operation(older, principal) == "superseded"

    oauth = await auth_sessions.begin_auth_operation_for_cookie(cookie, "oauth:github", settings)
    state = await auth_sessions.issue_oauth_state(oauth.context_handle, "github", oauth, settings)
    ticket = await auth_sessions.bootstrap_auth_context_v2(
        "B" * 43,
        "I" * 43,
        1,
        cookie,
        settings,
    )
    await auth_sessions.bootstrap_auth_context_v2(
        "C" * 43,
        "I" * 43,
        2,
        cookie,
        settings,
        rotation_ticket=ticket.rotation_ticket,
    )
    with pytest.raises(auth_sessions.AuthContextError, match="auth_context_stale"):
        await auth_sessions.consume_oauth_state_for_cookie(cookie, "github", state, settings)


@pytest.mark.asyncio
async def test_v2_same_generation_different_context_and_generation_gap_fail_closed(monkeypatch):
    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(monkeypatch, redis)
    created = await auth_sessions.bootstrap_auth_context_v2(
        "A" * 43,
        "I" * 43,
        1,
        "",
        settings,
    )
    assert created.identity is not None

    with pytest.raises(auth_sessions.AuthContextError, match="auth_context_stale"):
        await auth_sessions.bootstrap_auth_context_v2(
            "B" * 43,
            "I" * 43,
            1,
            "",
            settings,
        )
    with pytest.raises(auth_sessions.AuthContextError, match="auth_context_stale"):
        await auth_sessions.bootstrap_auth_context_v2(
            "A" * 43,
            "I" * 43,
            3,
            "",
            settings,
        )


@pytest.mark.asyncio
async def test_v2_rotation_ticket_reissue_expiry_and_consumption_never_extend_authority_ttl(monkeypatch):
    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(monkeypatch, redis)
    created = await auth_sessions.bootstrap_auth_context_v2(
        "A" * 43,
        "I" * 43,
        1,
        "",
        settings,
    )
    assert created.identity is not None
    cookie = auth_sessions.auth_context_v2_cookie_for_identity(created.identity, settings)
    authority_key = next(key for key in redis.values if key.startswith("ai-platform:auth-browser-authority:"))
    _raw, initial_expiry = redis.values[authority_key]

    first_ticket = await auth_sessions.bootstrap_auth_context_v2(
        "B" * 43,
        "I" * 43,
        1,
        cookie,
        settings,
    )
    second_ticket = await auth_sessions.bootstrap_auth_context_v2(
        "D" * 43,
        "I" * 43,
        1,
        cookie,
        settings,
    )
    assert first_ticket.rotation_ticket and second_ticket.rotation_ticket
    assert first_ticket.rotation_ticket != second_ticket.rotation_ticket
    assert redis.values[authority_key][1] == initial_expiry

    with pytest.raises(auth_sessions.AuthContextError, match="auth_context_stale"):
        await auth_sessions.bootstrap_auth_context_v2(
            "C" * 43,
            "I" * 43,
            2,
            cookie,
            settings,
            rotation_ticket=first_ticket.rotation_ticket,
        )

    redis.now += settings.auth_context_lease_seconds + 1
    with pytest.raises(auth_sessions.AuthContextError, match="auth_context_stale"):
        await auth_sessions.bootstrap_auth_context_v2(
            "C" * 43,
            "I" * 43,
            2,
            cookie,
            settings,
            rotation_ticket=second_ticket.rotation_ticket,
        )

    current_ticket = await auth_sessions.bootstrap_auth_context_v2(
        "E" * 43,
        "I" * 43,
        1,
        cookie,
        settings,
    )
    rotated = await auth_sessions.bootstrap_auth_context_v2(
        "F" * 43,
        "I" * 43,
        2,
        cookie,
        settings,
        rotation_ticket=current_ticket.rotation_ticket,
    )
    assert rotated.set_cookie is True
    # A generation change is a fence, not a new session.  The surviving
    # authority must retain the original context expiry rather than resetting
    # it to the configured max age at rotation time.
    assert redis.values[authority_key][1] == initial_expiry
    with pytest.raises(auth_sessions.AuthContextError, match="auth_context_stale"):
        await auth_sessions.bootstrap_auth_context_v2(
            "G" * 43,
            "I" * 43,
            2,
            cookie,
            settings,
            rotation_ticket=current_ticket.rotation_ticket,
        )


@pytest.mark.asyncio
async def test_v2_late_reissued_ticket_cannot_roll_back_the_newer_authority(monkeypatch):
    """Reverse ticket-response order leaves only the latest authority transition usable."""

    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(monkeypatch, redis)
    incarnation = "I" * 43
    created = await auth_sessions.bootstrap_auth_context_v2("A" * 43, incarnation, 1, "", settings)
    assert created.identity is not None
    cookie = auth_sessions.auth_context_v2_cookie_for_identity(created.identity, settings)
    first = await auth_sessions.bootstrap_auth_context_v2("B" * 43, incarnation, 1, cookie, settings)
    second = await auth_sessions.bootstrap_auth_context_v2("B" * 43, incarnation, 1, cookie, settings)
    assert first.rotation_ticket and second.rotation_ticket
    assert first.rotation_ticket != second.rotation_ticket

    with pytest.raises(auth_sessions.AuthContextError, match="auth_context_stale"):
        await auth_sessions.bootstrap_auth_context_v2(
            "C" * 43,
            incarnation,
            2,
            cookie,
            settings,
            rotation_ticket=first.rotation_ticket,
        )
    rotated = await auth_sessions.bootstrap_auth_context_v2(
        "C" * 43,
        incarnation,
        2,
        cookie,
        settings,
        rotation_ticket=second.rotation_ticket,
    )
    assert rotated.identity is not None
    target_cookie = auth_sessions.auth_context_v2_cookie_for_identity(rotated.identity, settings)

    with pytest.raises(auth_sessions.AuthContextError, match="auth_context_stale"):
        await auth_sessions.bootstrap_auth_context_v2("B" * 43, incarnation, 1, target_cookie, settings)


@pytest.mark.asyncio
async def test_v2_strict_cookie_ttl_mismatch_redis_loss_and_corruption_fail_closed(monkeypatch):
    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(monkeypatch, redis)
    created = await auth_sessions.bootstrap_auth_context_v2(
        "A" * 43,
        "I" * 43,
        1,
        "",
        settings,
    )
    assert created.identity is not None
    cookie = auth_sessions.auth_context_v2_cookie_for_identity(created.identity, settings)
    tampered = f"{cookie[:-1]}{'A' if cookie[-1] != 'A' else 'B'}"
    with pytest.raises(auth_sessions.AuthContextError, match="auth_context_stale"):
        auth_sessions.parse_auth_context_cookie(tampered, settings)

    authority_key = next(key for key in redis.values if key.startswith("ai-platform:auth-browser-authority:"))
    context_key = next(key for key in redis.values if key.startswith("ai-platform:auth-context:"))
    raw_authority, authority_expiry = redis.values[authority_key]
    redis.values[authority_key] = (raw_authority, authority_expiry - 2)
    with pytest.raises(auth_sessions.AuthContextError, match="auth_context_stale"):
        await auth_sessions.principal_for_cookie(cookie, settings)

    redis.available = False
    with pytest.raises(auth_sessions.AuthContextError, match="auth_context_unavailable"):
        await auth_sessions.bootstrap_auth_context_v2(
            "A" * 43,
            "I" * 43,
            1,
            cookie,
            settings,
        )
    redis.available = True
    _raw_context, context_expiry = redis.values[context_key]
    redis.values[context_key] = ("{corrupt", context_expiry)
    with pytest.raises(auth_sessions.AuthContextError, match="auth_context_unavailable"):
        await auth_sessions.principal_for_cookie(cookie, settings)


def test_late_v2_cookie_cannot_begin_login_logout_or_oauth_and_old_commit_is_fenced(monkeypatch):
    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(monkeypatch, redis)
    install_company_login(monkeypatch)
    client = TestClient(create_app())
    created = client.post(
        "/api/ai/auth/bootstrap",
        json={
            "nonce": "A" * 43,
            "protocol_version": 2,
            "browser_incarnation": "I" * 43,
            "generation": 1,
        },
    )
    assert created.status_code == 200
    old_cookie = created.cookies["ai_platform_auth_context"]

    async def old_operation():
        operation = await auth_sessions.begin_auth_operation_for_cookie(old_cookie, "login", settings)
        ticket = await auth_sessions.bootstrap_auth_context_v2(
            "B" * 43,
            "I" * 43,
            1,
            old_cookie,
            settings,
        )
        await auth_sessions.bootstrap_auth_context_v2(
            "C" * 43,
            "I" * 43,
            2,
            old_cookie,
            settings,
            rotation_ticket=ticket.rotation_ticket,
        )
        with pytest.raises(auth_sessions.AuthContextError, match="auth_context_stale"):
            await auth_sessions.commit_auth_operation(operation, None)

    asyncio.run(old_operation())
    headers = {"Cookie": f"ai_platform_auth_context={old_cookie}"}
    responses = [
        client.get("/api/ai/auth/me", headers=headers),
        client.post("/api/ai/auth/login", headers=headers, json={"username": "user-a", "password": "test-password"}),
        client.post("/api/ai/auth/logout", headers=headers),
        client.post("/api/ai/auth/oauth/github/begin", headers=headers),
        client.post(
            "/api/ai/auth/oauth/github/callback",
            headers=headers,
            json={"code": "test-code", "state": "S" * 43},
        ),
    ]
    assert all(response.status_code == 409 for response in responses)
    assert all(response.json()["detail"] == "auth_context_stale" for response in responses)
    assert all("set-cookie" not in response.headers for response in responses)


def test_nonce_only_bootstrap_cannot_reissue_an_authenticated_context(monkeypatch):
    redis = FakeAuthRedis()
    install_auth_context_dependencies(monkeypatch, redis)
    install_company_login(monkeypatch)
    nonce = "N" * 43
    client_a = TestClient(create_app())
    context_cookie = bootstrap(client_a, nonce)

    login = client_a.post(
        "/api/ai/auth/login",
        json={"username": "user-a", "password": "safe-password"},
    )
    assert login.status_code == 200

    client_b = TestClient(create_app())
    replay = client_b.post("/api/ai/auth/bootstrap", json={"nonce": nonce})
    assert replay.status_code == 409
    assert replay.json()["detail"] == "auth_context_rebootstrap_required"
    assert "set-cookie" not in replay.headers
    assert client_b.get("/api/ai/auth/me").status_code == 401

    reload = client_a.post("/api/ai/auth/bootstrap", json={"nonce": nonce})
    assert reload.status_code == 200
    assert reload.cookies["ai_platform_auth_context"] == context_cookie
    assert client_a.get("/api/ai/auth/me").json()["user_id"] == "user-a"

    fresh = client_b.post("/api/ai/auth/bootstrap", json={"nonce": "F" * 43})
    assert fresh.status_code == 200
    assert fresh.cookies["ai_platform_auth_context"] != context_cookie
    assert client_b.get("/api/ai/auth/me").status_code == 401


def test_nonce_only_bootstrap_cannot_reissue_a_previously_operated_anonymous_context(monkeypatch):
    redis = FakeAuthRedis()
    install_auth_context_dependencies(monkeypatch, redis)
    nonce = "O" * 43
    client_a = TestClient(create_app())
    bootstrap(client_a, nonce)
    assert client_a.post("/api/ai/auth/logout").status_code == 200

    client_b = TestClient(create_app())
    replay = client_b.post("/api/ai/auth/bootstrap", json={"nonce": nonce})
    assert replay.status_code == 409
    assert replay.json()["detail"] == "auth_context_rebootstrap_required"
    assert "set-cookie" not in replay.headers


def test_browser_auth_mutations_without_a_context_fail_closed_without_cookie_mutation(monkeypatch):
    redis = FakeAuthRedis()
    install_auth_context_dependencies(monkeypatch, redis)
    client = TestClient(create_app())

    responses = [
        client.post(
            "/api/ai/auth/login",
            json={"username": "user-a", "password": "safe-password"},
        ),
        client.post("/api/ai/auth/logout"),
        client.post("/api/ai/auth/oauth/github/begin"),
    ]

    assert [response.status_code for response in responses] == [401, 401, 401]
    assert all(response.json()["detail"] == "auth_context_missing" for response in responses)
    assert all("set-cookie" not in response.headers for response in responses)


def test_oauth_mutation_responses_do_not_mutate_the_context_cookie(monkeypatch):
    redis = FakeAuthRedis()
    install_auth_context_dependencies(monkeypatch, redis)
    client = TestClient(create_app())
    bootstrap(client)

    begin = client.post("/api/ai/auth/oauth/github/begin")
    assert begin.status_code == 200
    assert begin.json()["state"]
    assert "set-cookie" not in begin.headers

    callback = client.post(
        "/api/ai/auth/oauth/github/callback",
        json={"code": "provider-code", "state": begin.json()["state"]},
    )
    assert callback.status_code == 503
    assert callback.json()["detail"] == "oauth_provider_unavailable"
    assert "set-cookie" not in callback.headers


def test_late_login_a_cannot_overwrite_newer_login_b_or_set_cookie(monkeypatch):
    redis = FakeAuthRedis()
    install_auth_context_dependencies(monkeypatch, redis)
    started_a = threading.Event()
    release_a = threading.Event()
    install_company_login(monkeypatch, gate_a=started_a, release_a=release_a)
    client_a = TestClient(create_app())
    context_cookie = bootstrap(client_a)
    client_b = TestClient(create_app())
    client_b.cookies.set("ai_platform_auth_context", context_cookie)
    responses: dict[str, object] = {}

    def login_a():
        responses["a"] = client_a.post(
            "/api/ai/auth/login",
            json={"username": "user-a", "password": "safe-password"},
        )

    thread = threading.Thread(target=login_a)
    thread.start()
    assert started_a.wait(timeout=5)
    response_b = client_b.post(
        "/api/ai/auth/login",
        json={"username": "user-b", "password": "safe-password"},
    )
    release_a.set()
    thread.join(timeout=5)
    response_a = responses["a"]

    assert response_b.status_code == 200
    assert response_a.status_code == 409
    assert response_a.json()["detail"] == "auth_operation_superseded"
    assert "set-cookie" not in response_a.headers
    assert "set-cookie" not in response_b.headers
    assert client_b.get("/api/ai/auth/me").json()["user_id"] == "user-b"


def test_superseded_login_rolls_back_user_and_audit_side_effects(monkeypatch):
    redis = FakeAuthRedis()
    install_auth_context_dependencies(monkeypatch, redis)
    started_a = threading.Event()
    release_a = threading.Event()
    install_company_login(monkeypatch, gate_a=started_a, release_a=release_a)
    committed_effects: list[tuple[str, str]] = []
    rolled_back_effects: list[list[tuple[str, str]]] = []

    @asynccontextmanager
    async def tracked_transaction():
        staged_effects: list[tuple[str, str]] = []
        try:
            yield staged_effects
        except Exception:
            rolled_back_effects.append(staged_effects)
            raise
        else:
            committed_effects.extend(staged_effects)

    async def tracked_ensure_user(conn, *, user_id, **_kwargs):
        conn.append(("user", user_id))

    async def tracked_append_audit_log(conn, *, user_id, **_kwargs):
        conn.append(("audit", user_id))
        return "audit-id"

    monkeypatch.setattr("app.routes.auth.transaction", tracked_transaction)
    monkeypatch.setattr("app.routes.auth.ensure_user", tracked_ensure_user)
    monkeypatch.setattr("app.routes.auth.append_audit_log", tracked_append_audit_log)
    client_a = TestClient(create_app())
    context_cookie = bootstrap(client_a)
    client_b = TestClient(create_app())
    client_b.cookies.set("ai_platform_auth_context", context_cookie)
    responses: dict[str, object] = {}

    def login_a():
        responses["a"] = client_a.post(
            "/api/ai/auth/login",
            json={"username": "user-a", "password": "safe-password"},
        )

    thread = threading.Thread(target=login_a)
    thread.start()
    assert started_a.wait(timeout=5)
    response_b = client_b.post(
        "/api/ai/auth/login",
        json={"username": "user-b", "password": "safe-password"},
    )
    release_a.set()
    thread.join(timeout=5)
    response_a = responses["a"]

    assert response_b.status_code == 200
    assert response_a.status_code == 409
    assert committed_effects == [("user", "user-b"), ("audit", "user-b")]
    assert rolled_back_effects == [[("user", "user-a"), ("audit", "user-a")]]


def test_late_logout_cannot_clear_newer_login_or_context_cookie(monkeypatch):
    redis = FakeAuthRedis()
    install_auth_context_dependencies(monkeypatch, redis)
    install_company_login(monkeypatch)
    client_a = TestClient(create_app())
    context_cookie = bootstrap(client_a)
    assert client_a.post(
        "/api/ai/auth/login",
        json={"username": "user-a", "password": "safe-password"},
    ).status_code == 200

    started_logout = threading.Event()
    release_logout = threading.Event()
    from app import routes

    original_commit = routes.auth.commit_auth_operation

    async def deferred_logout_commit(operation, principal):
        if operation.kind == "logout":
            started_logout.set()
            await asyncio.to_thread(release_logout.wait, 5)
        return await original_commit(operation, principal)

    monkeypatch.setattr("app.routes.auth.commit_auth_operation", deferred_logout_commit)
    client_b = TestClient(create_app())
    client_b.cookies.set("ai_platform_auth_context", context_cookie)
    responses: dict[str, object] = {}

    def logout_a():
        responses["logout"] = client_a.post("/api/ai/auth/logout")

    thread = threading.Thread(target=logout_a)
    thread.start()
    assert started_logout.wait(timeout=5)
    response_b = client_b.post(
        "/api/ai/auth/login",
        json={"username": "user-b", "password": "safe-password"},
    )
    release_logout.set()
    thread.join(timeout=5)
    response_a = responses["logout"]

    assert response_b.status_code == 200
    assert response_a.status_code == 409
    assert response_a.json()["detail"] == "auth_operation_superseded"
    assert "set-cookie" not in response_a.headers
    assert "set-cookie" not in response_b.headers
    assert client_b.get("/api/ai/auth/me").json()["user_id"] == "user-b"


@pytest.mark.asyncio
async def test_oauth_callback_inversion_only_commits_newest_context_operation(monkeypatch):
    from app import auth_sessions

    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(monkeypatch, redis)
    nonce = "B" * 43
    handle = auth_sessions.auth_context_handle_for_nonce(nonce, settings)
    assert await auth_sessions.bootstrap_auth_context(handle, nonce, settings) == "created"
    operation_a = await auth_sessions.begin_auth_operation(handle, "oauth:github", settings)
    state_a = await auth_sessions.issue_oauth_state(handle, "github", operation_a, settings)
    operation_b = await auth_sessions.begin_auth_operation(handle, "oauth:github", settings)
    assert (
        await auth_sessions.commit_auth_operation(
            operation_b,
            auth_sessions.principal_snapshot(
                AuthPrincipal("oauth-b", "OAuth B", "tenant-b", source="company-login")
            ),
        )
    ) == "committed"

    callback_a = await auth_sessions.consume_oauth_state(handle, "github", state_a, settings)
    assert (
        await auth_sessions.commit_auth_operation(
            callback_a,
            auth_sessions.principal_snapshot(
                AuthPrincipal("oauth-a", "OAuth A", "tenant-a", source="company-login")
            ),
        )
    ) == "superseded"
    assert (await auth_sessions.principal_for_context(handle, settings))["user_id"] == "oauth-b"


@pytest.mark.asyncio
async def test_expired_lease_allows_new_epoch_and_permanently_rejects_old_commit(monkeypatch):
    from app import auth_sessions

    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(
        monkeypatch,
        redis,
        auth_settings(auth_context_lease_seconds=5),
    )
    redis.now = 1000.0
    nonce = "C" * 43
    handle = auth_sessions.auth_context_handle_for_nonce(nonce, settings)
    await auth_sessions.bootstrap_auth_context(handle, nonce, settings)
    operation_a = await auth_sessions.begin_auth_operation(handle, "login", settings)
    redis.now += 5
    assert (
        await auth_sessions.commit_auth_operation(
            operation_a,
            auth_sessions.principal_snapshot(AuthPrincipal("user-a", "A", "tenant-a")),
        )
    ) == "expired"
    operation_b = await auth_sessions.begin_auth_operation(handle, "login", settings)
    assert (
        await auth_sessions.commit_auth_operation(
            operation_b,
            auth_sessions.principal_snapshot(AuthPrincipal("user-b", "B", "tenant-b")),
        )
    ) == "committed"
    assert (
        await auth_sessions.commit_auth_operation(
            operation_a,
            auth_sessions.principal_snapshot(AuthPrincipal("user-a", "A", "tenant-a")),
        )
    ) == "superseded"


@pytest.mark.asyncio
async def test_context_and_operation_token_substitution_are_rejected(monkeypatch):
    from app import auth_sessions

    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(monkeypatch, redis)
    handle_a = auth_sessions.auth_context_handle_for_nonce("D" * 43, settings)
    handle_b = auth_sessions.auth_context_handle_for_nonce("E" * 43, settings)
    await auth_sessions.bootstrap_auth_context(handle_a, "D" * 43, settings)
    await auth_sessions.bootstrap_auth_context(handle_b, "E" * 43, settings)
    operation_a = await auth_sessions.begin_auth_operation(handle_a, "login", settings)

    substituted_context = auth_sessions.AuthOperation(
        context_handle=handle_b,
        epoch=operation_a.epoch,
        token=operation_a.token,
        kind=operation_a.kind,
    )
    substituted_token = auth_sessions.AuthOperation(
        context_handle=handle_a,
        epoch=operation_a.epoch,
        token="wrong-operation-token",
        kind=operation_a.kind,
    )
    principal = auth_sessions.principal_snapshot(AuthPrincipal("user-a", "A", "tenant-a"))

    assert await auth_sessions.commit_auth_operation(substituted_context, principal) == "superseded"
    assert await auth_sessions.commit_auth_operation(substituted_token, principal) == "superseded"
    assert await auth_sessions.principal_for_context(handle_a, settings) is None


def test_redis_unavailable_lost_or_corrupt_context_fails_closed_without_cookie_mutation(monkeypatch):
    redis = FakeAuthRedis()
    install_auth_context_dependencies(monkeypatch, redis)
    client = TestClient(create_app())
    redis.available = False
    unavailable = client.post("/api/ai/auth/bootstrap", json={"nonce": "F" * 43})
    assert unavailable.status_code == 503
    assert "set-cookie" not in unavailable.headers

    redis.available = True
    context_cookie = bootstrap(client, "G" * 43)
    redis.values.clear()
    client.cookies.set("ai_platform_auth_context", context_cookie)
    lost = client.get("/api/ai/auth/me")
    assert lost.status_code == 401
    assert "set-cookie" not in lost.headers
    rebootstrap = client.post("/api/ai/auth/bootstrap", json={"nonce": "G" * 43})
    assert rebootstrap.status_code == 200
    assert rebootstrap.cookies["ai_platform_auth_context"] == context_cookie

    context_key = next(iter(redis.values))
    _, expiry = redis.values[context_key]
    redis.values[context_key] = ("not-json", expiry)
    corrupt = client.get("/api/ai/auth/me")
    assert corrupt.status_code == 503
    assert corrupt.json()["detail"] == "auth_context_unavailable"
    corrupt_bootstrap = client.post("/api/ai/auth/bootstrap", json={"nonce": "G" * 43})
    assert corrupt_bootstrap.status_code == 503
    assert "set-cookie" not in corrupt_bootstrap.headers


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("schema_version", 1.5),
        ("operation_epoch", True),
        ("operation_epoch", -1),
        ("operation_epoch", 1.5),
        ("tenant_user_subject_epoch", True),
        ("tenant_user_subject_epoch", -1),
        ("tenant_user_subject_epoch", 1.5),
        ("lease_until", True),
        ("lease_until", -1),
        ("lease_until", float("nan")),
        ("lease_until", float("inf")),
    ],
)
def test_me_rejects_corrupt_numeric_auth_context_state(monkeypatch, field, value):
    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(monkeypatch, redis)
    client = TestClient(create_app())
    context_cookie = bootstrap(client, "Q" * 43)
    context_key = next(iter(redis.values))
    raw, expiry = redis.values[context_key]
    record = json.loads(raw)
    record["principal"] = {
        "user_id": "numeric-user",
        "display_name": "Numeric User",
        "tenant_id": settings.default_tenant_id,
        "department_id": "",
        "roles": ["user"],
        "permissions": [],
        "source": "company-login",
    }
    record[field] = value
    redis.values[context_key] = (json.dumps(record), expiry)
    client.cookies.set("ai_platform_auth_context", context_cookie)

    response = client.get("/api/ai/auth/me")
    assert response.status_code == 503
    assert response.json()["detail"] == "auth_context_unavailable"


@pytest.mark.asyncio
async def test_fake_auth_redis_rejects_corrupt_records_without_numeric_coercion(monkeypatch):
    from app import auth_sessions

    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(monkeypatch, redis)
    handle = auth_sessions.auth_context_handle_for_nonce("R" * 43, settings)

    await auth_sessions.bootstrap_auth_context(handle, "R" * 43, settings)
    context_key = next(iter(redis.values))
    raw, expiry = redis.values[context_key]
    record = json.loads(raw)
    record["operation_epoch"] = True
    redis.values[context_key] = (json.dumps(record), expiry)

    with pytest.raises(auth_sessions.AuthContextError) as bootstrap_error:
        await auth_sessions.bootstrap_auth_context(handle, "R" * 43, settings)
    assert bootstrap_error.value.code == "auth_context_unavailable"

    with pytest.raises(auth_sessions.AuthContextError) as begin_error:
        await auth_sessions.begin_auth_operation(handle, "login", settings)
    assert begin_error.value.code == "auth_context_unavailable"

    commit_handle = auth_sessions.auth_context_handle_for_nonce("S" * 43, settings)
    await auth_sessions.bootstrap_auth_context(commit_handle, "S" * 43, settings)
    operation = await auth_sessions.begin_auth_operation(commit_handle, "login", settings)
    commit_key = next(key for key in redis.values if commit_handle in key)
    raw, expiry = redis.values[commit_key]
    record = json.loads(raw)
    record["lease_until"] = float("inf")
    redis.values[commit_key] = (json.dumps(record), expiry)

    with pytest.raises(auth_sessions.AuthContextError) as commit_error:
        await auth_sessions.commit_auth_operation(
            operation,
            auth_sessions.principal_snapshot(
                AuthPrincipal("user-a", "User A", settings.default_tenant_id)
            ),
        )
    assert commit_error.value.code == "auth_context_unavailable"


def test_weak_context_secret_fails_closed_without_cookie_mutation(monkeypatch):
    redis = FakeAuthRedis()
    install_auth_context_dependencies(
        monkeypatch,
        redis,
        auth_settings(ai_session_secret="too-short", auth_context_secret=""),
    )
    response = TestClient(create_app()).post(
        "/api/ai/auth/bootstrap",
        json={"nonce": "H" * 43},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "auth_context_unavailable"
    assert "set-cookie" not in response.headers


def test_old_principal_cookie_forces_relogin_while_trusted_headers_and_bearer_stay_compatible(monkeypatch):
    from app.auth import sign_principal_session

    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(monkeypatch, redis)
    client = TestClient(create_app())
    legacy_token = sign_principal_session(
        AuthPrincipal("legacy-user", "Legacy User", "default", source="company-login")
    )

    client.cookies.set(settings.ai_session_cookie_name, legacy_token)
    legacy = client.get("/api/ai/auth/me")
    assert legacy.status_code == 401
    assert "set-cookie" not in legacy.headers

    bearer = client.get("/api/ai/auth/me", headers={"Authorization": f"Bearer {legacy_token}"})
    assert bearer.status_code == 200
    assert bearer.json()["user_id"] == "legacy-user"

    trusted = client.get(
        "/api/ai/auth/me",
        headers={"X-AI-User-ID": "trusted-user", "X-AI-Gateway-Secret": "gateway-secret"},
    )
    assert trusted.status_code == 200
    assert trusted.json()["user_id"] == "trusted-user"


def test_browser_login_rejects_untrusted_tenant_substitution(monkeypatch):
    redis = FakeAuthRedis()
    install_auth_context_dependencies(monkeypatch, redis)
    client = TestClient(create_app())
    bootstrap(client)

    response = client.post(
        "/api/ai/auth/login",
        json={
            "username": "user-a",
            "password": "safe-password",
            "tenant_id": "attacker-tenant",
            "operation_epoch": 999,
        },
    )
    assert response.status_code == 422
    assert "set-cookie" not in response.headers
