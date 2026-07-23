"""Deep, fail-closed OpenSandbox gateway application core.

Only :class:`GatewayApplication` is used by transports.  Lifecycle I/O,
container evidence, and durable state are injected so the exact same contract
can run behind the production HTTPS listener and the in-memory test adapter.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import contextvars
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
import json
import re
import secrets
import socket
import threading
import time
import urllib.parse
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator, Mapping, Protocol


CONTRACT_VERSION = "ai-platform.opensandbox.topology-attestation.v1"
CAPABILITY_VERSION = "ai-platform.opensandbox.external-egress-capability.v1"
API_KEY_HEADER = "OPEN-SANDBOX-API-KEY"
ROUTE_HEADER = "OPEN-SANDBOX-ROUTE-TOKEN"
MAX_BODY_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_METADATA_VALUE = 512
SAFE_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+\-=]{0,511}\Z")
SCOPE_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@+\-]{0,127}\Z")
SANDBOX_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
SHA_IMAGE = re.compile(r"[^\s/@]+(?:/[^\s/@]+)*@(?P<digest>sha256:[0-9a-f]{64})\Z")
SCOPE_KEYS = (
    "tenant_id",
    "workspace_id",
    "user_id",
    "session_id",
    "run_id",
    "attempt_id",
)
SCOPE_LABELS = {name: f"ai-platform.{name}" for name in SCOPE_KEYS}
REQUIRED_METADATA = {
    "ai-platform.owner": "sandbox-runtime",
    "ai-platform.provider_backend": "opensandbox",
    "ai-platform.external_egress.profile_version": "v1",
    "ai-platform.external_egress.runtime_identity": "runsc",
}
ALLOWED_METADATA_KEYS = {
    "ai-platform.owner",
    "ai-platform.tenant_id",
    "ai-platform.workspace_id",
    "ai-platform.user_id",
    "ai-platform.session_id",
    "ai-platform.run_id",
    "ai-platform.attempt_id",
    "ai-platform.sandbox_mode",
    "ai-platform.browser_enabled",
    "ai-platform.provider_backend",
    "ai-platform.executor.requested_image",
    "ai-platform.executor.requested_image_digest",
    "ai-platform.executor.user",
    "ai-platform.executor.uid",
    "ai-platform.executor.gid",
    "ai-platform.executor.identity_evidence",
    "ai-platform.external_egress.profile_version",
    "ai-platform.external_egress.profile_id",
    "ai-platform.external_egress.endpoint_sha256",
    "ai-platform.external_egress.runtime_identity",
    "ai-platform.runtime_subject",
    "ai-platform.external_egress.gateway_policy_subject",
    "ai-platform.external_egress.callback_boundary_subject",
    "ai-platform.external_egress.deny_audit_subject",
    "ai-platform.external_egress.deny_counter_subject",
    "ai-platform.external_egress.profile_requested_image",
    "ai-platform.external_egress.profile_requested_image_digest",
    "ai-platform.external_egress.profile_expires_at",
    "ai-platform.skill_mount.required",
    "ai-platform.skill_mount.fingerprint",
}
CREATE_KEYS = {
    "image",
    "timeout",
    "entrypoint",
    "env",
    "metadata",
    "resourceLimits",
    "resourceRequests",
    "platform",
    "networkPolicy",
    "credentialProxy",
    "extensions",
    "volumes",
    "secureAccess",
}
CALLBACK_PATHS = {
    "/api/ai/runtime/callbacks/executor",
    "/api/ai/runtime/callbacks/tool-permission",
    "/api/ai/runtime/callbacks/context-retrieval",
}
EXECD_ALLOWED = {
    ("GET", "/ping"),
    ("POST", "/files/upload"),
    ("GET", "/files/download"),
    ("POST", "/command"),
}
EXECUTOR_ALLOWED = {
    ("GET", "/health"),
    ("GET", "/health/runtime-identity"),
    ("POST", "/v1/tasks/execute"),
}


class DeadlineExceeded(TimeoutError):
    """Internal signal that one monotonic operation budget is exhausted."""


class _DeadlineTimer:
    """Idempotent ownership handle for one deadline interrupt timer."""

    def __init__(self, timer: threading.Timer) -> None:
        self._timer = timer
        self._lock = threading.Lock()
        self._cancelled = False

    def cancel(self) -> None:
        """Cancel the timer exactly once from any completion path."""

        with self._lock:
            if self._cancelled:
                return
            self._cancelled = True
            self._timer.cancel()


@dataclass(frozen=True)
class MonotonicDeadline:
    """One absolute monotonic budget shared by nested transport operations."""

    expires_at: float

    @classmethod
    def after(cls, timeout_seconds: float) -> "MonotonicDeadline":
        """Create a deadline without permitting non-positive budgets."""

        if timeout_seconds <= 0:
            raise DeadlineExceeded("deadline exhausted")
        return cls(time.monotonic() + timeout_seconds)

    def remaining(self) -> float:
        """Return the remaining budget or fail once the absolute time passed."""

        remaining = self.expires_at - time.monotonic()
        if remaining <= 0:
            raise DeadlineExceeded("deadline exhausted")
        return remaining

    def expired(self) -> bool:
        """Return whether the absolute expiry has passed."""

        return time.monotonic() >= self.expires_at

    def bounded(self, timeout_seconds: float) -> "MonotonicDeadline":
        """Apply a tighter operation ceiling without extending this deadline."""

        if timeout_seconds <= 0:
            raise DeadlineExceeded("deadline exhausted")
        return MonotonicDeadline(min(self.expires_at, time.monotonic() + timeout_seconds))

    def bind_socket(self, connection: socket.socket) -> None:
        """Set the next socket wait to no more than the remaining budget."""

        connection.settimeout(max(0.001, self.remaining()))

    def arm(self, interrupt: Callable[[], None]) -> _DeadlineTimer:
        """Interrupt a blocking implementation when the absolute expiry arrives."""

        timer = threading.Timer(self.remaining(), interrupt)
        timer.daemon = True
        timer.start()
        return _DeadlineTimer(timer)


_ACTIVE_DEADLINE: contextvars.ContextVar[MonotonicDeadline | None] = contextvars.ContextVar(
    "opensandbox_gateway_deadline",
    default=None,
)


@contextmanager
def deadline_scope(deadline: MonotonicDeadline) -> Iterator[MonotonicDeadline]:
    """Expose one inbound budget to every nested production adapter."""

    token = _ACTIVE_DEADLINE.set(deadline)
    try:
        yield deadline
    finally:
        _ACTIVE_DEADLINE.reset(token)


def operation_deadline(timeout_seconds: float) -> MonotonicDeadline:
    """Use the current inbound deadline, tightened by an operation ceiling."""

    active = _ACTIVE_DEADLINE.get()
    if active is None:
        return MonotonicDeadline.after(timeout_seconds)
    return active.bounded(timeout_seconds)


@dataclass(frozen=True)
class Request:
    """A transport-neutral inbound HTTP request."""

    method: str
    target: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""


@dataclass(frozen=True)
class Response:
    """A bounded transport-neutral HTTP response."""

    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""

    @classmethod
    def json(cls, status: int, value: Any) -> "Response":
        """Return canonical JSON with a fixed content type."""

        body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return cls(status, {"content-type": "application/json", "content-length": str(len(body))}, body)


@dataclass(frozen=True)
class GatewayConfig:
    """Validated gateway policy and secret material."""

    lifecycle_api_key: str
    capability_bearer_token: str
    record_signing_key: bytes
    proof_key_id: str
    profile_id: str
    public_authority: str
    lifecycle_endpoint: str
    executor_image: str
    runtime_subject: str
    gateway_policy_subject: str
    callback_boundary_subject: str
    deny_audit_subject: str
    deny_counter_subject: str
    workspace_root: str = "/data/opensandbox/workspaces"
    callback_upstream_base: str = ""
    openai_upstream_base: str = ""
    anthropic_upstream_base: str = ""
    executor_entrypoint: tuple[str, ...] = ("/app/docker-entrypoint.sh", "uvicorn")
    request_timeout_seconds: float = 5.0
    dispatch_timeout_seconds: float = 3600.0
    capability_ttl_seconds: int = 120
    max_body_bytes: int = MAX_BODY_BYTES
    max_response_bytes: int = MAX_RESPONSE_BYTES
    max_concurrent_handlers: int = 32

    def validate(self) -> None:
        """Reject ambiguous endpoints, weak secrets, and mutable subjects."""

        if len(self.lifecycle_api_key.encode()) < 24 or len(self.capability_bearer_token.encode()) < 24:
            raise ValueError("gateway authentication secrets must be at least 24 bytes")
        if len(self.record_signing_key) < 32:
            raise ValueError("record signing key must be at least 32 bytes")
        if self.lifecycle_endpoint != "http://127.0.0.1:8080":
            raise ValueError("OpenSandbox upstream must be the fixed loopback endpoint")
        if not re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]{1,5})?", self.public_authority):
            raise ValueError("public authority must be a hostname and optional port")
        public_host = self.public_authority.rsplit(":", 1)[0]
        try:
            public_address = ipaddress.ip_address(public_host)
        except ValueError:
            raise ValueError("public authority must be an approved literal IP") from None
        if not public_address.is_private or public_address.is_loopback or public_address.is_unspecified:
            raise ValueError("public authority must be an approved private IP")
        if not SHA_IMAGE.fullmatch(self.executor_image):
            raise ValueError("executor image must be immutable")
        for value in (
            self.proof_key_id,
            self.profile_id,
            self.runtime_subject,
            self.gateway_policy_subject,
            self.callback_boundary_subject,
            self.deny_audit_subject,
            self.deny_counter_subject,
        ):
            if not isinstance(value, str) or not SAFE_VALUE.fullmatch(value):
                raise ValueError("gateway subjects must be non-empty safe values")
        if self.workspace_root != "/data/opensandbox/workspaces":
            raise ValueError("workspace root must match the s72 scoped root")
        if not self.executor_entrypoint or any(not isinstance(item, str) or not item or len(item) > 256 for item in self.executor_entrypoint):
            raise ValueError("executor entrypoint is invalid")
        for base in (self.callback_upstream_base, self.openai_upstream_base, self.anthropic_upstream_base):
            _validate_https_base(base)
        if urllib.parse.urlsplit(self.callback_upstream_base).path not in ("", "/"):
            raise ValueError("callback base must not contain a path")
        if not 0.1 <= self.request_timeout_seconds <= 10.0:
            raise ValueError("request timeout is outside the bounded range")
        if not 1.0 <= self.dispatch_timeout_seconds <= 3600.0:
            raise ValueError("dispatch timeout is outside the bounded range")
        if not 30 <= self.capability_ttl_seconds <= 300:
            raise ValueError("capability TTL is outside the bounded range")
        if not 1024 <= self.max_body_bytes <= MAX_BODY_BYTES:
            raise ValueError("request body limit is outside the bounded range")
        if not 1024 <= self.max_response_bytes <= MAX_RESPONSE_BYTES:
            raise ValueError("response body limit is outside the bounded range")
        if not 1 <= self.max_concurrent_handlers <= 64:
            raise ValueError("handler concurrency is outside the bounded range")


@dataclass
class LeaseRecord:
    """Non-secret accepted-create facts sealed by the gateway."""

    sandbox_id: str
    scope: dict[str, str]
    metadata: dict[str, str]
    image: str
    image_digest: str
    workspace_host_path: str
    mounts: list[dict[str, Any]]
    canonical_request_hash: str
    executor_token_hash: str
    created_at: str
    state: str = "active"
    reservation_owner_token: str = ""
    signature: str = ""

    def unsigned(self) -> dict[str, Any]:
        """Return the canonical signed fields."""

        data = asdict(self)
        data.pop("signature", None)
        return data


@dataclass(frozen=True)
class RuntimeEvidence:
    """Verified live s72 container evidence used to create attestation."""

    sandbox_id: str
    runtime: str
    network_mode: str
    no_new_privileges: bool
    user: str
    uid: str
    gid: str
    image: str
    image_digest: str
    mounts: tuple[tuple[str, str, bool], ...]
    labels: Mapping[str, str]
    skill_mount_fingerprint: str = ""
    running: bool = True
    relay_active: bool = False


@dataclass(frozen=True)
class ReservationResult:
    """Atomic reservation decision and its opaque owner fence."""

    outcome: str
    record: LeaseRecord
    owner_token: str


class LifecycleTransport(Protocol):
    """Narrow loopback OpenSandbox lifecycle client."""

    def request(self, method: str, path: str, body: bytes = b"") -> Response:
        """Issue one bounded, no-redirect upstream request."""


class StateStore(Protocol):
    """Durable gateway lease and denial state."""

    def get(self, sandbox_id: str) -> LeaseRecord | None: ...
    def find_scope(self, scope: Mapping[str, str]) -> LeaseRecord | None: ...
    def reserve(self, record: LeaseRecord) -> ReservationResult: ...
    def activate(self, intent_id: str, owner_token: str, record: LeaseRecord) -> None: ...
    def save(self, record: LeaseRecord) -> None: ...
    def list(self, filters: Mapping[str, str]) -> list[LeaseRecord]: ...
    def begin_mailbox_claim(self, sandbox_id: str) -> str | None: ...
    def confirm_mailbox_outbound(self, sandbox_id: str, token: str) -> bool: ...
    def end_mailbox_outbound(self, sandbox_id: str, token: str) -> None: ...
    def transition_cleanup_pending(self, record: LeaseRecord, timeout_seconds: float) -> bool: ...
    def record_deny(self, subject: str, code: str) -> None: ...
    def deny_count(self, subject: str) -> int: ...
    def ready(self) -> bool: ...


class RuntimeAdapter(Protocol):
    """Narrow host-owned container evidence and broker interface."""

    def verify(self, record: LeaseRecord) -> RuntimeEvidence: ...
    def start_relay(self, record: LeaseRecord) -> None: ...
    def stop_relay(self, record: LeaseRecord) -> None: ...
    def proxy(self, record: LeaseRecord, port: int, request: Request) -> Response: ...
    def cleanup_mailbox(self, record: LeaseRecord) -> None: ...


class GatewayError(Exception):
    """A fixed public gateway error."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


class GatewayApplication:
    """Authenticate, validate, attest, and broker the fixed lifecycle surface."""

    def __init__(self, config: GatewayConfig, lifecycle: LifecycleTransport, runtime: RuntimeAdapter, store: StateStore):
        config.validate()
        self.config = config
        self.lifecycle = lifecycle
        self.runtime = runtime
        self.store = store
        self._reconcile_intents()

    def handle(self, request: Request) -> Response:
        """Handle one request without exposing internal exception details."""

        try:
            return self._handle(request)
        except GatewayError as exc:
            self._record_deny(exc.code)
            return Response.json(exc.status, {"error": {"code": exc.code}})
        except Exception:
            self._record_deny("internal_error")
            return Response.json(500, {"error": {"code": "internal_error"}})

    def _record_deny(self, code: str) -> None:
        try:
            self.store.record_deny(self.config.deny_audit_subject, code)
        except Exception:
            # The public error contract remains fixed even when audit storage is
            # unavailable; readiness independently fails closed on store health.
            pass

    def _handle(self, request: Request) -> Response:
        method = request.method.upper()
        path, query = _safe_target(request.target)
        if len(request.body) > self.config.max_body_bytes:
            raise GatewayError(413, "request_too_large")
        if path == "/healthz" and method == "GET":
            return Response.json(200, {"status": "ok"})
        if path == "/readyz" and method == "GET":
            probe = self.lifecycle.request("GET", "/health")
            if probe.status != 200 or not self.store.ready():
                raise GatewayError(503, "not_ready")
            return Response.json(200, {"status": "ready"})
        if path == "/v1/capabilities/external-egress" and method == "GET":
            self._require_bearer(request.headers)
            return self._capability()
        self._require_api_key(request.headers)
        if path == "/v1/sandboxes" and method == "POST":
            return self._create(request)
        if path == "/v1/sandboxes" and method == "GET":
            return self._list(query)
        match = re.fullmatch(r"/v1/sandboxes/([^/]+)(?:/(.*))?", path)
        if not match or not SANDBOX_ID.fullmatch(match.group(1)):
            raise GatewayError(404, "not_found")
        sandbox_id, suffix = match.group(1), match.group(2) or ""
        if not suffix and method == "GET":
            return self._get(sandbox_id)
        if not suffix and method == "DELETE":
            return self._delete(sandbox_id)
        if suffix == "cancel" and method == "POST":
            return self._delete(sandbox_id)
        if suffix == "attestation" and method == "GET":
            record, _ = self._attest(sandbox_id)
            return Response.json(200, self._attestation_payload(record))
        endpoint = re.fullmatch(r"endpoints/([0-9]{1,5})", suffix)
        if endpoint and method == "GET":
            return self._endpoint(sandbox_id, int(endpoint.group(1)), query)
        proxy = re.fullmatch(r"proxy/([0-9]{1,5})(/.*)", suffix)
        if proxy:
            return self._proxy(sandbox_id, int(proxy.group(1)), proxy.group(2), query, request)
        raise GatewayError(404, "not_found")

    def _create(self, request: Request) -> Response:
        payload = _json_object(request.body)
        accepted = self._accept_create(payload)
        intent_id = "intent-" + hmac.new(
            self.config.record_signing_key,
            b"reservation-v1\0" + accepted["request_hash"].encode() + b"\0" + accepted["workspace_host_path"].encode(),
            hashlib.sha256,
        ).hexdigest()[:48]
        upstream_metadata = dict(accepted["metadata"])
        upstream_metadata["ai-platform.gateway.intent_id"] = intent_id
        accepted["upstream"]["metadata"] = upstream_metadata
        intent = LeaseRecord(
            sandbox_id=intent_id,
            scope=accepted["scope"],
            metadata=upstream_metadata,
            image=accepted["image"],
            image_digest=accepted["image_digest"],
            workspace_host_path=accepted["workspace_host_path"],
            mounts=accepted["mounts"],
            canonical_request_hash=accepted["request_hash"],
            executor_token_hash=accepted["executor_token_hash"],
            created_at=_now_iso(),
            state="uncertain_create",
            reservation_owner_token="reservation-" + secrets.token_hex(32),
        )
        intent.signature = self._sign_record(intent)
        reserved = self.store.reserve(intent)
        if reserved.outcome == "resume":
            if reserved.record.state == "active" and hmac.compare_digest(reserved.record.canonical_request_hash, accepted["request_hash"]):
                self._attest(reserved.record.sandbox_id)
                return Response.json(201, {"id": reserved.record.sandbox_id})
            raise GatewayError(409, "reservation_in_progress")
        if reserved.outcome != "winner" or reserved.owner_token != intent.reservation_owner_token:
            raise GatewayError(409, "scope_conflict")
        upstream_body = _canonical(accepted["upstream"])
        upstream = self.lifecycle.request("POST", "/v1/sandboxes", upstream_body)
        if upstream.status not in (200, 201, 202):
            raise GatewayError(502, "upstream_create_failed")
        result = _bounded_json(upstream, self.config.max_response_bytes)
        sandbox_id = result.get("id")
        if not isinstance(sandbox_id, str) or not SANDBOX_ID.fullmatch(sandbox_id):
            raise GatewayError(502, "upstream_invalid_response")
        record = LeaseRecord(
            sandbox_id=sandbox_id,
            scope=accepted["scope"],
            metadata=upstream_metadata,
            image=accepted["image"],
            image_digest=accepted["image_digest"],
            workspace_host_path=accepted["workspace_host_path"],
            mounts=accepted["mounts"],
            canonical_request_hash=accepted["request_hash"],
            executor_token_hash=accepted["executor_token_hash"],
            created_at=_now_iso(),
        )
        record.signature = self._sign_record(record)
        self.store.activate(intent_id, reserved.owner_token, record)
        try:
            self._attest(sandbox_id)
            self.runtime.start_relay(record)
        except Exception:
            record.state = "cleanup_pending"
            record.signature = self._sign_record(record)
            self.store.save(record)
            self._cleanup_pending(record)
            raise GatewayError(502, "post_create_verification_failed") from None
        return Response.json(upstream.status, result)

    def _accept_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) - CREATE_KEYS:
            raise GatewayError(400, "create_field_not_allowed")
        if payload.get("networkPolicy") is not None or payload.get("credentialProxy") not in (None, {}):
            raise GatewayError(400, "network_policy_not_allowed")
        if payload.get("secureAccess") not in (None, False) or payload.get("extensions") not in (None, {}):
            raise GatewayError(400, "create_extension_not_allowed")
        image_value = payload.get("image")
        if isinstance(image_value, str):
            image = image_value
        elif isinstance(image_value, dict) and set(image_value) <= {"image", "username", "password"}:
            image = image_value.get("image")
            if image_value.get("username") or image_value.get("password"):
                raise GatewayError(400, "inline_registry_secret_not_allowed")
        else:
            image = None
        match = SHA_IMAGE.fullmatch(image or "")
        if not match or image != self.config.executor_image:
            raise GatewayError(400, "immutable_image_mismatch")
        metadata = payload.get("metadata")
        env = payload.get("env")
        if not isinstance(metadata, dict) or not isinstance(env, dict):
            raise GatewayError(400, "create_metadata_invalid")
        if set(metadata) != ALLOWED_METADATA_KEYS or any(not isinstance(k, str) or not isinstance(v, str) or len(v) > MAX_METADATA_VALUE for k, v in metadata.items()):
            raise GatewayError(400, "create_metadata_invalid")
        for key, value in REQUIRED_METADATA.items():
            if metadata.get(key) != value:
                raise GatewayError(400, "create_metadata_mismatch")
        scope = {}
        for name, label in SCOPE_LABELS.items():
            value = metadata.get(label)
            if not isinstance(value, str) or not SCOPE_SEGMENT.fullmatch(value):
                raise GatewayError(400, "scope_invalid")
            scope[name] = value
        expected_subjects = {
            "ai-platform.runtime_subject": self.config.runtime_subject,
            "ai-platform.external_egress.gateway_policy_subject": self.config.gateway_policy_subject,
            "ai-platform.external_egress.callback_boundary_subject": self.config.callback_boundary_subject,
            "ai-platform.external_egress.deny_audit_subject": self.config.deny_audit_subject,
            "ai-platform.external_egress.deny_counter_subject": self.config.deny_counter_subject,
            "ai-platform.external_egress.profile_id": self.config.profile_id,
            "ai-platform.external_egress.profile_requested_image": image,
            "ai-platform.external_egress.profile_requested_image_digest": match.group("digest"),
            "ai-platform.executor.requested_image": image,
            "ai-platform.executor.requested_image_digest": match.group("digest"),
        }
        if any(metadata.get(k) != v for k, v in expected_subjects.items()):
            raise GatewayError(400, "attestation_subject_mismatch")
        if (
            metadata.get("ai-platform.executor.user") != "1000:1000"
            or metadata.get("ai-platform.executor.uid") != "1000"
            or metadata.get("ai-platform.executor.gid") != "1000"
            or metadata.get("ai-platform.executor.identity_evidence") != "authenticated-runtime-endpoint"
        ):
            raise GatewayError(400, "executor_identity_mismatch")
        endpoint = f"https://{self.config.public_authority}"
        if metadata.get("ai-platform.external_egress.endpoint_sha256") != hashlib.sha256(endpoint.encode()).hexdigest():
            raise GatewayError(400, "capability_endpoint_mismatch")
        try:
            expires_at = datetime.fromisoformat(metadata["ai-platform.external_egress.profile_expires_at"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            raise GatewayError(400, "capability_expiry_invalid") from None
        now = datetime.now(timezone.utc)
        if expires_at.tzinfo is None or not now + timedelta(seconds=15) < expires_at <= now + timedelta(seconds=300):
            raise GatewayError(400, "capability_expiry_invalid")
        if payload.get("entrypoint") != list(self.config.executor_entrypoint):
            raise GatewayError(400, "entrypoint_mismatch")
        if payload.get("platform") not in (None, {}) or payload.get("resourceRequests") not in (None, {}):
            raise GatewayError(400, "create_extension_not_allowed")
        resources = payload.get("resourceLimits")
        if not isinstance(resources, dict) or not resources or set(resources) - {"cpu", "memory", "pids", "storage"}:
            raise GatewayError(400, "resource_limits_invalid")
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?(?:Mi|Gi)?", value) for value in resources.values()):
            raise GatewayError(400, "resource_limits_invalid")
        timeout = payload.get("timeout")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 3600:
            raise GatewayError(400, "timeout_invalid")
        executor_token = env.get("AI_PLATFORM_EXECUTOR_AUTH_TOKEN")
        if not isinstance(executor_token, str) or len(executor_token.encode()) < 24:
            raise GatewayError(400, "executor_credential_invalid")
        if (
            env.get("AI_PLATFORM_SESSION_ID") != scope["session_id"]
            or env.get("AI_PLATFORM_RUN_ID") != scope["run_id"]
            or env.get("AI_PLATFORM_ATTEMPT_ID") != scope["attempt_id"]
        ):
            raise GatewayError(400, "environment_scope_mismatch")
        if env.get("AI_PLATFORM_CALLBACK_BASE_URL") != self.config.callback_upstream_base or env.get("SANDBOX_CALLBACK_BASE_URL") != self.config.callback_upstream_base:
            raise GatewayError(400, "callback_boundary_mismatch")
        if env.get("OPENAI_BASE_URL") != self.config.openai_upstream_base or env.get("ANTHROPIC_BASE_URL") != self.config.anthropic_upstream_base:
            raise GatewayError(400, "model_boundary_mismatch")
        if any(k.upper().endswith("_PROXY") for k in env):
            raise GatewayError(400, "proxy_environment_not_allowed")
        mounts, workspace = self._accept_volumes(payload.get("volumes"), scope, metadata)
        rewritten = json.loads(json.dumps(payload))
        rewritten_env = rewritten["env"]
        rewritten_env["AI_PLATFORM_CALLBACK_BASE_URL"] = "http://127.0.0.1:18888"
        rewritten_env["SANDBOX_CALLBACK_BASE_URL"] = "http://127.0.0.1:18888"
        rewritten_env["OPENAI_BASE_URL"] = "http://127.0.0.1:18888/model/openai"
        rewritten_env["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:18888/model/anthropic"
        request_hash = hashlib.sha256(_canonical(payload)).hexdigest()
        return {
            "upstream": rewritten,
            "scope": scope,
            "metadata": dict(metadata),
            "image": image,
            "image_digest": match.group("digest"),
            "workspace_host_path": workspace,
            "mounts": mounts,
            "request_hash": request_hash,
            "executor_token_hash": hmac.new(self.config.record_signing_key, executor_token.encode(), hashlib.sha256).hexdigest(),
        }

    def _accept_volumes(
        self,
        value: Any,
        scope: Mapping[str, str],
        metadata: Mapping[str, str],
    ) -> tuple[list[dict[str, Any]], str]:
        if not isinstance(value, list) or not value or len(value) > 2:
            raise GatewayError(400, "volume_policy_mismatch")
        expected_workspace = (
            f"{self.config.workspace_root}/tenants/{scope['tenant_id']}/workspaces/{scope['workspace_id']}"
            f"/users/{scope['user_id']}/sessions/{scope['session_id']}/runs/{scope['run_id']}/workspace"
        )
        expected_skill_mount = f"{expected_workspace}/.claude"
        expected_prefix = f"{self.config.workspace_root}/"
        accepted: list[dict[str, Any]] = []
        workspace = ""
        names: set[str] = set()
        mount_paths: set[str] = set()
        required = metadata.get("ai-platform.skill_mount.required")
        fingerprint = metadata.get("ai-platform.skill_mount.fingerprint")
        if required not in {"true", "false"}:
            raise GatewayError(400, "skill_mount_declaration_invalid")
        if required == "true":
            if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                raise GatewayError(400, "skill_mount_fingerprint_invalid")
        elif fingerprint != "":
            raise GatewayError(400, "skill_mount_fingerprint_invalid")
        for item in value:
            if not isinstance(item, dict) or set(item) - {"name", "mountPath", "host", "readOnly"}:
                raise GatewayError(400, "volume_policy_mismatch")
            host = item.get("host")
            if isinstance(host, dict):
                host_path = host.get("path") if set(host) <= {"path"} else None
            else:
                host_path = host
            mount_path, read_only = item.get("mountPath"), item.get("readOnly", False)
            name = item.get("name")
            if not isinstance(name, str) or name in names or mount_path in mount_paths:
                raise GatewayError(400, "volume_policy_mismatch")
            names.add(name)
            mount_paths.add(mount_path)
            if (
                not isinstance(host_path, str)
                or not host_path.startswith(expected_prefix)
                or ".." in host_path.split("/")
                or "." in host_path.split("/")
                or "//" in host_path
                or host_path.endswith("/")
            ):
                raise GatewayError(400, "host_path_not_scoped")
            if not isinstance(mount_path, str) or mount_path not in ("/workspace", "/workspace/.claude"):
                raise GatewayError(400, "volume_policy_mismatch")
            if mount_path == "/workspace":
                if name != "ai-platform-workspace" or read_only is not False or host_path != expected_workspace:
                    raise GatewayError(400, "workspace_must_be_writable")
                workspace = host_path
            elif name != "ai-platform-claude-skills" or read_only is not True or host_path != expected_skill_mount:
                raise GatewayError(400, "skill_mount_must_be_read_only")
            accepted.append({"host": host_path, "mountPath": mount_path, "readOnly": bool(read_only)})
        if not workspace:
            raise GatewayError(400, "workspace_mount_missing")
        if ("/workspace/.claude" in mount_paths) != (required == "true"):
            raise GatewayError(400, "skill_mount_declaration_mismatch")
        return accepted, workspace

    def _reconcile_intents(self) -> None:
        for active in self.store.list({"state": "active"}):
            self._verify_record(active)
            evidence = self.runtime.verify(active)
            self._validate_evidence(active, evidence)
            if not evidence.relay_active:
                self.runtime.start_relay(active)
        for pending in self.store.list({"state": "cleanup_pending"}):
            self._verify_record(pending)
            self._cleanup_pending(pending)
        for intent in self.store.list({"state": "uncertain_create"}) + self.store.list({"state": "reconciling"}):
            self._reconcile_intent(intent)

    def _reconcile_intent(self, intent: LeaseRecord) -> None:
        self._verify_record(intent)
        intent.state = "reconciling"
        intent.signature = self._sign_record(intent)
        self.store.save(intent)
        items = self._list_intent_sandboxes(intent.sandbox_id)
        if len(items) > 1:
            raise GatewayError(503, "reservation_reconciliation_ambiguous")
        if not items:
            intent.state = "deleted"
            intent.signature = self._sign_record(intent)
            self.store.save(intent)
            return
        sandbox_id = items[0].get("id") if isinstance(items[0], dict) else None
        if not isinstance(sandbox_id, str) or not SANDBOX_ID.fullmatch(sandbox_id):
            raise GatewayError(503, "reservation_reconciliation_ambiguous")
        record = LeaseRecord(**{**intent.unsigned(), "sandbox_id": sandbox_id, "state": "active"})
        record.signature = self._sign_record(record)
        self.store.activate(intent.sandbox_id, intent.reservation_owner_token, record)
        self._attest(sandbox_id)
        self.runtime.start_relay(record)

    def _reconcile_online(self, filters: Mapping[str, str]) -> None:
        """Run one bounded tenant-attempt reconciliation pass during inventory."""

        scoped = {key: value for key, value in filters.items() if key in set(SCOPE_LABELS.values())}
        for state in ("cleanup_pending", "uncertain_create", "reconciling", "active"):
            records = self.store.list({**scoped, "state": state})
            if len(records) > 100:
                raise GatewayError(503, "online_reconciliation_bounded")
            for record in records:
                if state == "cleanup_pending":
                    self._verify_record(record)
                    self._cleanup_pending(record)
                elif state in {"uncertain_create", "reconciling"}:
                    self._reconcile_intent(record)
                else:
                    self._verify_record(record)
                    evidence = self.runtime.verify(record)
                    self._validate_evidence(record, evidence)
                    if not evidence.relay_active:
                        self.runtime.start_relay(record)
        if not all(SCOPE_LABELS[name] in filters for name in ("tenant_id", "attempt_id")):
            return
        upstream = self._list_upstream_sandboxes(scoped)
        tracked = {record.sandbox_id for record in self.store.list({})}
        for item in upstream:
            sandbox_id = item["id"]
            if sandbox_id in tracked:
                continue
            metadata = item.get("metadata")
            if (
                not isinstance(metadata, Mapping)
                or metadata.get("ai-platform.owner") != "sandbox-runtime"
                or any(metadata.get(key) != value for key, value in scoped.items())
            ):
                raise GatewayError(503, "orphan_reconciliation_ambiguous")
            if not self._delete_upstream_and_verify(sandbox_id):
                raise GatewayError(503, "orphan_reconciliation_failed")

    def _list_intent_sandboxes(self, intent_id: str) -> list[dict[str, Any]]:
        return self._list_upstream_sandboxes({"ai-platform.gateway.intent_id": intent_id})

    def _list_upstream_sandboxes(self, metadata_filters: Mapping[str, str]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for page in range(1, 101):
            query = urllib.parse.urlencode(
                {
                    "metadata": "&".join(f"{key}={value}" for key, value in metadata_filters.items()),
                    "page": str(page),
                    "pageSize": "100",
                }
            )
            response = self.lifecycle.request("GET", f"/v1/sandboxes?{query}")
            if response.status != 200:
                raise GatewayError(503, "reservation_reconciliation_failed")
            payload = _bounded_json(response, self.config.max_response_bytes)
            page_items = payload.get("items")
            pagination = payload.get("pagination")
            if not isinstance(page_items, list) or not isinstance(pagination, Mapping):
                raise GatewayError(503, "reservation_reconciliation_ambiguous")
            if pagination.get("page") != page or pagination.get("pageSize") != 100 or not isinstance(pagination.get("hasNextPage"), bool):
                raise GatewayError(503, "reservation_reconciliation_ambiguous")
            for item in page_items:
                sandbox_id = item.get("id") if isinstance(item, Mapping) else None
                if not isinstance(sandbox_id, str) or not SANDBOX_ID.fullmatch(sandbox_id) or sandbox_id in seen:
                    raise GatewayError(503, "reservation_reconciliation_ambiguous")
                seen.add(sandbox_id)
                items.append(dict(item))
            if not pagination["hasNextPage"]:
                return items
        raise GatewayError(503, "reservation_reconciliation_ambiguous")

    def _delete_upstream_and_verify(self, sandbox_id: str) -> bool:
        deadline = operation_deadline(self.config.request_timeout_seconds)
        try:
            with deadline_scope(deadline):
                result = self.lifecycle.request("DELETE", f"/v1/sandboxes/{sandbox_id}")
                if result.status not in (200, 202, 204, 404):
                    return False
                for attempt in range(8):
                    current = self.lifecycle.request("GET", f"/v1/sandboxes/{sandbox_id}")
                    if current.status == 404:
                        return True
                    if current.status != 200:
                        return False
                    if attempt != 7:
                        time.sleep(min(0.05, deadline.remaining()))
        except (DeadlineExceeded, GatewayError):
            return False
        return False

    def _cleanup_pending(self, record: LeaseRecord) -> bool:
        if not self.store.transition_cleanup_pending(record, self.config.request_timeout_seconds):
            return False
        if not self._delete_upstream_and_verify(record.sandbox_id):
            return False
        self.runtime.stop_relay(record)
        self.runtime.cleanup_mailbox(record)
        record.state = "deleted"
        record.signature = self._sign_record(record)
        self.store.save(record)
        return True

    def _get(self, sandbox_id: str) -> Response:
        record, upstream = self._attest(sandbox_id)
        del record
        return upstream

    def _list(self, query: str) -> Response:
        try:
            pairs = urllib.parse.parse_qsl(query, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            raise GatewayError(400, "scope_filter_invalid") from None
        if [key for key, _ in pairs] != ["metadata", "page", "pageSize"]:
            raise GatewayError(400, "scope_filter_required")
        values = dict(pairs)
        if urllib.parse.urlencode(pairs) != query:
            raise GatewayError(400, "scope_filter_invalid")
        if not values["page"].isdigit() or not values["pageSize"].isdigit():
            raise GatewayError(400, "pagination_invalid")
        page, page_size = int(values["page"]), int(values["pageSize"])
        if str(page) != values["page"] or str(page_size) != values["pageSize"] or not 1 <= page <= 100000 or not 1 <= page_size <= 100:
            raise GatewayError(400, "pagination_invalid")
        filters: dict[str, str] = {}
        for pair in values["metadata"].split("&"):
            if "=" not in pair:
                raise GatewayError(400, "scope_filter_invalid")
            key, value = pair.split("=", 1)
            if key in filters or key not in set(SCOPE_LABELS.values()) | {"ai-platform.owner"} or not value:
                raise GatewayError(400, "scope_filter_invalid")
            filters[key] = value
        if filters.get("ai-platform.owner") != "sandbox-runtime" or not any(k in filters for k in SCOPE_LABELS.values()):
            raise GatewayError(400, "scope_filter_required")
        self._reconcile_online(filters)
        records = self.store.list(filters)
        total_items = len(records)
        start = (page - 1) * page_size
        items = []
        for record in records[start : start + page_size]:
            try:
                _, response = self._attest(record.sandbox_id)
                item = _bounded_json(response, self.config.max_response_bytes)
                items.append(item)
            except GatewayError:
                continue
        total_pages = (total_items + page_size - 1) // page_size
        return Response.json(
            200,
            {
                "items": items,
                "pagination": {
                    "page": page,
                    "pageSize": page_size,
                    "totalItems": total_items,
                    "totalPages": total_pages,
                    "hasNextPage": page < total_pages,
                },
            },
        )

    def _delete(self, sandbox_id: str) -> Response:
        record = self.store.get(sandbox_id)
        if record is None or record.state == "deleted":
            return Response(204, {"content-length": "0"}, b"")
        self._verify_record(record)
        if record.state != "cleanup_pending":
            record = replace(record, state="cleanup_pending")
            record.signature = self._sign_record(record)
        if not self._cleanup_pending(record):
            raise GatewayError(503, "cleanup_pending")
        return Response(204, {"content-length": "0"}, b"")

    def _endpoint(self, sandbox_id: str, port: int, query: str) -> Response:
        if port not in (44772, 18000, 18080):
            raise GatewayError(404, "endpoint_not_allowed")
        params = urllib.parse.parse_qs(query, strict_parsing=True)
        if params != {"use_server_proxy": ["true"]}:
            raise GatewayError(400, "server_proxy_required")
        record, _ = self._attest(sandbox_id)
        token = self._route_token(record, port)
        endpoint = f"{self.config.public_authority}/v1/sandboxes/{sandbox_id}/proxy/{port}"
        return Response.json(200, {"endpoint": endpoint, "headers": {ROUTE_HEADER: token}})

    def _proxy(self, sandbox_id: str, port: int, path: str, query: str, request: Request) -> Response:
        if query:
            path = f"{path}?{query}"
        record, _ = self._attest(sandbox_id)
        provided = _header(request.headers, ROUTE_HEADER)
        if not hmac.compare_digest(provided.encode(), self._route_token(record, port).encode()):
            raise GatewayError(401, "route_auth_failed")
        clean_path = path.split("?", 1)[0]
        allowed = EXECD_ALLOWED if port == 44772 else EXECUTOR_ALLOWED if port == 18000 else set()
        if (request.method.upper(), clean_path) not in allowed:
            raise GatewayError(404, "proxy_route_not_allowed")
        forwarded = request
        if port == 44772 and clean_path in {"/files/upload", "/files/download"}:
            self._validate_workspace_file_request(record, request, clean_path)
        if port == 44772 and clean_path == "/command":
            command = _json_object(request.body)
            if command != {"command": "test -f /workspace/.ai-platform-opensandbox-lease.json"}:
                raise GatewayError(400, "workspace_command_not_allowed")
        if port == 18000 and clean_path == "/v1/tasks/execute":
            task = _json_object(request.body)
            self._validate_task_scope(task, record)
            task["callback_base_url"] = "http://127.0.0.1:18888"
            task["callback_url"] = "http://127.0.0.1:18888/api/ai/runtime/callbacks/executor"
            forwarded = Request(request.method, path, request.headers, _canonical(task))
        result = self.runtime.proxy(record, port, forwarded)
        if len(result.body) > self.config.max_response_bytes:
            raise GatewayError(502, "upstream_response_too_large")
        return result

    def _validate_workspace_file_request(self, record: LeaseRecord, request: Request, path: str) -> None:
        sentinel = "/workspace/.ai-platform-opensandbox-lease.json"
        if path == "/files/download":
            try:
                pairs = urllib.parse.parse_qsl(urllib.parse.urlsplit(request.target).query, keep_blank_values=True, strict_parsing=True)
            except ValueError:
                raise GatewayError(400, "workspace_file_request_invalid") from None
            if pairs != [("path", sentinel)] or urllib.parse.urlencode(pairs) != urllib.parse.urlsplit(request.target).query:
                raise GatewayError(400, "workspace_file_request_invalid")
            return
        content_type = _header(request.headers, "content-type")
        if not content_type.startswith("multipart/form-data; boundary="):
            raise GatewayError(400, "workspace_file_request_invalid")
        boundary = content_type.split("boundary=", 1)[1]
        if not re.fullmatch(r"[A-Za-z0-9_'()+,./:=?-]{1,70}", boundary):
            raise GatewayError(400, "workspace_file_request_invalid")
        try:
            message = BytesParser(policy=policy.HTTP).parsebytes(
                f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + request.body
            )
            parts = list(message.iter_parts())
        except Exception:
            raise GatewayError(400, "workspace_file_request_invalid") from None
        if len(parts) != 2 or [part.get_param("name", header="content-disposition") for part in parts] != ["metadata", "file"]:
            raise GatewayError(400, "workspace_file_request_invalid")
        if any(part.get("content-transfer-encoding") for part in parts):
            raise GatewayError(400, "workspace_file_request_invalid")
        try:
            metadata = json.loads(parts[0].get_payload(decode=True))
            content = parts[1].get_payload(decode=True)
            sentinel_payload = json.loads(content)
        except Exception:
            raise GatewayError(400, "workspace_file_request_invalid") from None
        expected_payload = {"schema_version": "ai-platform.opensandbox-lease.v1", **record.scope}
        if (
            not isinstance(metadata, dict)
            or set(metadata) != {"path", "owner", "group", "mode"}
            or metadata.get("path") != sentinel
            or metadata.get("owner") != "1000"
            or metadata.get("group") != "1000"
            or metadata.get("mode") != "0600"
            or parts[1].get_filename() not in {sentinel, sentinel.rsplit("/", 1)[1]}
            or sentinel_payload != expected_payload
        ):
            raise GatewayError(400, "workspace_file_request_invalid")

    def _validate_task_scope(self, task: Mapping[str, Any], record: LeaseRecord) -> None:
        for name in SCOPE_KEYS:
            if task.get(name) != record.scope[name]:
                raise GatewayError(409, "dispatch_scope_mismatch")
        if (
            task.get("callback_base_url") != self.config.callback_upstream_base
            or task.get("callback_url") != self.config.callback_upstream_base.rstrip("/") + "/api/ai/runtime/callbacks/executor"
        ):
            raise GatewayError(409, "dispatch_callback_mismatch")

    def _attest(self, sandbox_id: str) -> tuple[LeaseRecord, Response]:
        record = self.store.get(sandbox_id)
        if record is None or record.state != "active":
            raise GatewayError(404, "sandbox_not_found")
        self._verify_record(record)
        upstream = self.lifecycle.request("GET", f"/v1/sandboxes/{sandbox_id}")
        if upstream.status != 200:
            raise GatewayError(409, "lifecycle_state_drift")
        info = _bounded_json(upstream, self.config.max_response_bytes)
        if info.get("id") != sandbox_id or not _metadata_matches(info.get("metadata"), record.metadata):
            raise GatewayError(409, "lifecycle_metadata_drift")
        evidence = self.runtime.verify(record)
        self._validate_evidence(record, evidence)
        return record, upstream

    def _validate_evidence(self, record: LeaseRecord, evidence: RuntimeEvidence) -> None:
        expected_mounts = tuple(sorted((m["host"], m["mountPath"], bool(m["readOnly"])) for m in record.mounts))
        expected_skill_fingerprint = record.metadata["ai-platform.skill_mount.fingerprint"]
        if (
            evidence.sandbox_id != record.sandbox_id
            or not evidence.running
            or evidence.runtime != "runsc"
            or evidence.network_mode != "none"
            or evidence.no_new_privileges is not True
            or evidence.user != "1000:1000"
            or evidence.uid != "1000"
            or evidence.gid != "1000"
            or evidence.image != record.image
            or evidence.image_digest != record.image_digest
            or tuple(sorted(evidence.mounts)) != expected_mounts
            or evidence.skill_mount_fingerprint != expected_skill_fingerprint
            or not _metadata_matches(evidence.labels, record.metadata)
        ):
            raise GatewayError(409, "runtime_attestation_drift")

    def _attestation_payload(self, record: LeaseRecord) -> dict[str, Any]:
        evidence = self.runtime.verify(record)
        self._validate_evidence(record, evidence)
        payload = {
            "contract_version": CONTRACT_VERSION,
            "provider": "opensandbox",
            "sandbox_id": record.sandbox_id,
            "scope_labels": {
                **record.scope,
                "lease_id": (
                    f"opensandbox:opensandbox-{record.scope['run_id']}-{record.scope['attempt_id']}:{record.sandbox_id}"
                ),
            },
            "runtime": {"identity": "runsc", "subject": self.config.runtime_subject},
            "network": {"mode": "none", "default_deny": True},
            "security": {
                "no_new_privileges": True,
                "user": evidence.user,
                "uid": evidence.uid,
                "gid": evidence.gid,
            },
            "image": {"subject": record.image, "digest": record.image_digest},
            "host_path_policy": {"subject": "scoped-workspace-only", "unscoped_host_paths_allowed": False},
            "subjects": {
                "gateway_policy": self.config.gateway_policy_subject,
                "callback_boundary": self.config.callback_boundary_subject,
                "capability": self.config.profile_id,
                "deny_audit": self.config.deny_audit_subject,
                "deny_counter": self.config.deny_counter_subject,
            },
            "signed_profile": {"id": self.config.profile_id, "version": "v1", "proof_key_id": self.config.proof_key_id},
        }
        payload["signed_profile"]["profile_signature"] = hmac.new(
            self.config.record_signing_key,
            _canonical(payload),
            hashlib.sha256,
        ).hexdigest()
        return payload

    def _capability(self) -> Response:
        now = datetime.now(timezone.utc)
        value = {
            "schema_version": CAPABILITY_VERSION,
            "provider": "opensandbox",
            "issued_at": now.isoformat().replace("+00:00", "Z"),
            "expires_at": (now + timedelta(seconds=self.config.capability_ttl_seconds)).isoformat().replace("+00:00", "Z"),
            "opensandbox_endpoint": self.config.lifecycle_endpoint.replace("http://127.0.0.1:8080", f"https://{self.config.public_authority}"),
            "runtime_identity": "runsc",
            "ai_platform_runtime_subject": self.config.runtime_subject,
            "gateway_policy_subject": self.config.gateway_policy_subject,
            "callback_boundary_subject": self.config.callback_boundary_subject,
            "executor_image_digest": self.config.executor_image.rsplit("@", 1)[1],
            "profile_id": self.config.profile_id,
            "deny_audit_subject": self.config.deny_audit_subject,
            "deny_counter_subject": self.config.deny_counter_subject,
            "proof_key_id": self.config.proof_key_id,
        }
        value["profile_signature"] = hmac.new(self.config.record_signing_key, _canonical(value), hashlib.sha256).hexdigest()
        return Response.json(200, value)

    def _require_api_key(self, headers: Mapping[str, str]) -> None:
        supplied = _header(headers, API_KEY_HEADER)
        if not hmac.compare_digest(supplied.encode(), self.config.lifecycle_api_key.encode()):
            raise GatewayError(401, "authentication_failed")

    def _require_bearer(self, headers: Mapping[str, str]) -> None:
        supplied = _header(headers, "authorization")
        expected = f"Bearer {self.config.capability_bearer_token}"
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            raise GatewayError(401, "authentication_failed")

    def _sign_record(self, record: LeaseRecord) -> str:
        return hmac.new(self.config.record_signing_key, _canonical(record.unsigned()), hashlib.sha256).hexdigest()

    def _verify_record(self, record: LeaseRecord) -> None:
        if not hmac.compare_digest(record.signature.encode(), self._sign_record(record).encode()):
            raise GatewayError(409, "lease_record_signature_invalid")

    def _route_token(self, record: LeaseRecord, port: int) -> str:
        material = f"route-v1\0{record.sandbox_id}\0{port}\0{record.signature}".encode()
        return hmac.new(self.config.record_signing_key, material, hashlib.sha256).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _json_object(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise GatewayError(400, "invalid_json") from None
    if not isinstance(value, dict):
        raise GatewayError(400, "invalid_json")
    return value


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _bounded_json(response: Response, limit: int) -> dict[str, Any]:
    if len(response.body) > limit:
        raise GatewayError(502, "upstream_response_too_large")
    try:
        value = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise GatewayError(502, "upstream_invalid_response") from None
    if not isinstance(value, dict):
        raise GatewayError(502, "upstream_invalid_response")
    return value


def _safe_target(target: str) -> tuple[str, str]:
    if len(target) > 4096 or not target.startswith("/") or "#" in target:
        raise GatewayError(400, "invalid_target")
    split = urllib.parse.urlsplit(target)
    lowered_path = split.path.lower()
    if "%2f" in lowered_path or "%5c" in lowered_path or "%2e" in lowered_path or "\\" in target:
        raise GatewayError(400, "invalid_target")
    if split.scheme or split.netloc or "//" in split.path or ".." in split.path.split("/"):
        raise GatewayError(400, "invalid_target")
    return split.path, split.query


def _header(headers: Mapping[str, str], name: str) -> str:
    wanted = name.lower()
    values = [str(value) for key, value in headers.items() if str(key).lower() == wanted]
    return values[0] if len(values) == 1 else ""


def _metadata_matches(value: Any, expected: Mapping[str, str]) -> bool:
    return isinstance(value, Mapping) and all(value.get(key) == item for key, item in expected.items())


def _validate_https_base(value: str) -> None:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise ValueError("broker upstream bases must be unambiguous HTTPS URLs")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
