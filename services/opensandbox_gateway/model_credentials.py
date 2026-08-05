"""Attempt-bound model-route admission for the trusted OpenSandbox host broker."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
import sqlite3
from collections.abc import Mapping
from typing import Any

from .gateway import EXPECTED_BROKER_CREDENTIAL_ENV, PROVIDER_CREDENTIAL_ENV_KEYS, GatewayError, LeaseRecord


MODEL_ROUTE_TTL_SECONDS = 15.0
MODEL_ROUTE_REQUEST_LIMIT = 512
MODEL_ROUTE_PATHS = {
    "openai": frozenset({"/chat/completions", "/responses"}),
    "anthropic": frozenset({"/v1/messages", "/v1/messages/count_tokens"}),
}
ModelRouteBinding = tuple[str, str, str, str, str, str, str]


def model_id_sha256(model: str) -> str:
    """Encode the full SHA-256 digest within the provider's label length limit."""

    return base64.b32encode(hashlib.sha256(model.encode("utf-8")).digest()).decode("ascii").rstrip("=")


def parse_broker_environment_evidence(items: Any) -> tuple[dict[str, str], bool]:
    """Retain exact provider entries so duplicate or case-drifted secrets cannot hide."""

    env: dict[str, str] = {}
    provider_env = []
    for item in items:
        key, separator, value = str(item).partition("=")
        if key.upper() in PROVIDER_CREDENTIAL_ENV_KEYS:
            provider_env.append((key, value if separator else ""))
        if separator:
            env[key] = value
    return env, sorted(provider_env) == sorted(EXPECTED_BROKER_CREDENTIAL_ENV)


def consume_in_memory_model_route(
    records: Mapping[str, LeaseRecord],
    receipts: dict[str, ModelRouteBinding],
    **request: Any,
) -> None:
    record, binding = _record_and_binding(records.get(request["sandbox_id"]), **request)
    _reject_existing(receipts.get(request["request_id"]), binding)
    _validate_admission(record, request)
    if sum(value[:3] == binding[:3] for value in receipts.values()) >= request["request_limit"]:
        raise GatewayError(429, "model_route_limit_exceeded")
    receipts[request["request_id"]] = binding


def initialize_sqlite_model_route_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS model_route_receipts (
            request_id TEXT PRIMARY KEY,
            sandbox_id TEXT NOT NULL,
            scope_json TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            model TEXT NOT NULL,
            consumed_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS model_route_receipts_attempt
            ON model_route_receipts(sandbox_id, attempt_id);
        """
    )


def consume_sqlite_model_route(db: sqlite3.Connection, **request: Any) -> None:
    db.isolation_level = None
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute(
            "SELECT record_json FROM leases WHERE sandbox_id = ?",
            (request["sandbox_id"],),
        ).fetchone()
        record = LeaseRecord(**json.loads(row[0])) if row else None
        record, binding = _record_and_binding(record, **request)
        existing = db.execute(
            "SELECT sandbox_id, scope_json, attempt_id, provider, method, path, model "
            "FROM model_route_receipts WHERE request_id = ?",
            (request["request_id"],),
        ).fetchone()
        _reject_existing(tuple(existing) if existing is not None else None, binding)
        _validate_admission(record, request)
        count = db.execute(
            "SELECT COUNT(*) FROM model_route_receipts WHERE sandbox_id = ? AND attempt_id = ?",
            (request["sandbox_id"], binding[2]),
        ).fetchone()[0]
        if int(count) >= request["request_limit"]:
            raise GatewayError(429, "model_route_limit_exceeded")
        db.execute(
            "INSERT INTO model_route_receipts("
            "request_id, sandbox_id, scope_json, attempt_id, provider, method, path, model, consumed_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (request["request_id"], *binding, request["now"]),
        )
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise


def authorize_model_request(
    store: Any,
    record: LeaseRecord | None,
    *,
    name: str,
    provider: str,
    method: str,
    path: str,
    query: str,
    body: bytes,
    created_at: float,
    now: float,
    credential: str,
) -> tuple[str, str]:
    """Consume one receipt and return the host-owned provider auth header."""

    if query:
        raise GatewayError(403, "model_route_path_not_allowed")
    if not credential:
        raise GatewayError(503, "model_provider_credential_unavailable")
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict) or not isinstance(payload.get("model"), str) or not payload["model"]:
            raise TypeError("model request body must contain a model")
        model = payload["model"]
    except (TypeError, ValueError, json.JSONDecodeError):
        raise GatewayError(400, "model_route_body_invalid") from None
    request_match = re.match(r"(?P<request_id>[0-9a-f]{32})\.json(?:\.[0-9a-f]{32}\.claim)?\Z", name)
    if request_match is None or record is None:
        raise GatewayError(403, "model_route_context_missing")
    store.consume_model_route(
        sandbox_id=record.sandbox_id,
        request_id=request_match.group("request_id"),
        provider=provider,
        method=method,
        path=path,
        model=model,
        created_at=created_at,
        now=now,
        ttl_seconds=MODEL_ROUTE_TTL_SECONDS,
        request_limit=MODEL_ROUTE_REQUEST_LIMIT,
        attempt_id=record.scope.get("attempt_id"),
    )
    return ("authorization", f"Bearer {credential}") if provider == "openai" else ("x-api-key", credential)


def _record_and_binding(record: LeaseRecord | None, **request: Any) -> tuple[LeaseRecord, ModelRouteBinding]:
    provider = request["provider"]
    if provider not in MODEL_ROUTE_PATHS:
        raise GatewayError(403, "model_route_provider_not_allowed")
    if request["method"] != "POST":
        raise GatewayError(403, "model_route_method_not_allowed")
    if request["path"] not in MODEL_ROUTE_PATHS[provider]:
        raise GatewayError(403, "model_route_path_not_allowed")
    model = request["model"]
    if (
        record is None
        or record.state != "active"
        or record.sandbox_id != request["sandbox_id"]
        or not isinstance(model, str)
        or not model
        or len(model.encode("utf-8")) > 512
    ):
        raise GatewayError(403, "model_route_lease_inactive")
    expected_attempt = record.scope.get("attempt_id", "")
    attempt_id = request.get("attempt_id")
    if attempt_id is not None and not hmac.compare_digest(attempt_id, expected_attempt):
        raise GatewayError(403, "model_route_attempt_mismatch")
    created_at = request["created_at"]
    now = request["now"]
    if (
        not re.fullmatch(r"[0-9a-f]{32}", request["request_id"])
        or not math.isfinite(created_at)
        or not math.isfinite(now)
        or created_at > now
    ):
        raise GatewayError(408, "model_route_expired")
    if not 1 <= request["request_limit"] <= 4096:
        raise GatewayError(500, "model_route_policy_invalid")
    binding = (
        record.sandbox_id,
        json.dumps(record.scope, sort_keys=True, separators=(",", ":"), allow_nan=False),
        expected_attempt,
        provider,
        request["method"],
        request["path"],
        model,
    )
    return record, binding


def _reject_existing(existing: ModelRouteBinding | None, binding: ModelRouteBinding) -> None:
    if existing is not None:
        code = "model_route_replayed" if existing == binding else "model_route_binding_mismatch"
        raise GatewayError(409, code)


def _validate_admission(record: LeaseRecord, request: Mapping[str, Any]) -> None:
    expected_model_hash = record.metadata.get("ai-platform.model_id_sha256", "")
    if not re.fullmatch(r"[A-Z2-7]{52}", expected_model_hash) or not hmac.compare_digest(
        model_id_sha256(request["model"]),
        expected_model_hash,
    ):
        raise GatewayError(403, "model_route_model_mismatch")
    ttl_seconds = request["ttl_seconds"]
    if not 1.0 <= ttl_seconds <= 60.0 or request["now"] - request["created_at"] > ttl_seconds:
        raise GatewayError(408, "model_route_expired")
