#!/usr/bin/env python3
"""Run the exact-main HTTP acceptance matrix for Run Control fixtures."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.verify_auth_rbac_smoke import _principal_headers, sanitize_base_url  # noqa: E402


SCHEMA_VERSION = "ai-platform.run-control-exact-main-acceptance.v1"
CASE_IDS = (
    "queued_cancel_no_execution",
    "running_cancel_fence_and_sandbox_cleanup",
    "retry_idempotency_and_lineage",
    "resume_eligibility_and_lineage",
    "sse_duplicate_order_replay",
    "refresh_terminal_hydration",
    "stale_principal_and_session_denial",
)
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
CANCEL_EFFECT_STATUSES = {"cancel_requested", "cancelled"}
POST_CANCEL_START_EVENT_TYPES = {
    "run_started",
    "running",
    "step_started",
    "worker_started",
    "execution_started",
}
FULL_GIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)

HttpRequest = Callable[..., tuple[int, Any]]


def _request_json(
    url: str,
    *,
    method: str,
    headers: dict[str, str],
    timeout_seconds: float,
) -> tuple[int, Any]:
    """Issue one JSON HTTP request without logging request headers or bodies."""

    request = Request(url, headers={"Accept": "application/json", **headers}, method=method)
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read()
            if not raw:
                return int(response.status), None
            text = raw.decode("utf-8")
            try:
                return int(response.status), json.loads(text)
            except json.JSONDecodeError:
                return int(response.status), text
    except HTTPError as exc:
        raw = exc.read()
        try:
            payload: Any = json.loads(raw.decode("utf-8")) if raw else None
        except Exception:
            payload = {"error": "non_json_error_response"}
        return int(exc.code), payload
    except URLError:
        return 0, {"error": "runtime_unreachable"}


def _as_dict(value: Any) -> dict[str, Any]:
    """Return a mapping only when the HTTP payload has object shape."""

    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    """Return a list only when the HTTP payload has array shape."""

    return value if isinstance(value, list) else []


def _route(base_url: str, *parts: str, query: str = "") -> str:
    """Build one Run Control route using encoded fixture IDs."""

    encoded = "/".join(quote(str(part), safe="-_") for part in parts)
    return f"{base_url}/api/ai/{encoded}{query}"


def _case(ok: bool, **observations: object) -> dict[str, object]:
    """Create a redacted, status-only acceptance case result."""

    return {"ok": ok, "evidence_scope": "runtime_http", "observations": observations}


def _blocked_cases(reason: str) -> dict[str, dict[str, object]]:
    """Describe every unrun case when mutation confirmation or identity is absent."""

    return {
        case_id: {"ok": False, "evidence_scope": "runtime_http", "status": "not_run", "reason": reason}
        for case_id in CASE_IDS
    }


def _events(body: Any) -> list[dict[str, Any]]:
    """Extract public event rows from the events endpoint response."""

    return [item for item in _as_list(_as_dict(body).get("events")) if isinstance(item, dict)]


def _steps(body: Any) -> list[dict[str, Any]]:
    """Extract public step rows from a Run Control response."""

    return [item for item in _as_list(_as_dict(body).get("steps")) if isinstance(item, dict)]


def _event_lineage_matches(events: list[dict[str, Any]], *, source_run_id: str) -> bool:
    """Check that a public copied-run event preserves the expected source lineage."""

    return any(
        _as_dict(event.get("payload")).get("copied_from_run_id") == source_run_id
        for event in events
    )


def _parse_sse(text: str) -> list[dict[str, object]]:
    """Parse the minimal SSE fields emitted by the Run Control stream route."""

    frames: list[dict[str, object]] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        fields: dict[str, object] = {}
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("id: "):
                fields["id"] = line[4:]
            elif line.startswith("event: "):
                fields["event"] = line[7:]
            elif line.startswith("data: "):
                data_lines.append(line[6:])
        if data_lines:
            try:
                fields["data"] = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                fields["data"] = {}
        if fields:
            frames.append(fields)
    return frames


def _stream_frames(body: Any) -> list[dict[str, object]]:
    """Parse a stream response only when its body was returned as text."""

    return _parse_sse(body) if isinstance(body, str) else []


def _same_terminal_hydration(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Require refreshes to retain terminal status and cancellation metadata."""

    return (
        str(first.get("status") or "") in TERMINAL_STATUSES
        and first.get("status") == second.get("status")
        and bool(first.get("cancel_requested_at"))
        and first.get("cancel_requested_at") == second.get("cancel_requested_at")
        and first.get("queue_position") is None
        and second.get("queue_position") is None
    )


def build_exact_main_run_control_acceptance(
    *,
    base_url: str,
    gateway_secret: str,
    branch: str,
    commit_sha: str,
    runtime_subject_commit_sha: str,
    image: str,
    tenant_id: str,
    owner_user_id: str,
    admin_user_id: str,
    stale_principal_user_id: str,
    queued_run_id: str,
    running_run_id: str,
    retry_source_run_id: str,
    resume_source_run_id: str,
    stale_session_run_id: str,
    allow_mutations: bool,
    timeout_seconds: float = 10.0,
    request_json: HttpRequest = _request_json,
) -> dict[str, object]:
    """Execute the seven HTTP Run Control acceptance cases against one runtime."""

    safe_base_url = sanitize_base_url(base_url)
    source_identity_ok = (
        branch == "main"
        and FULL_GIT_SHA_PATTERN.fullmatch(commit_sha) is not None
        and FULL_GIT_SHA_PATTERN.fullmatch(runtime_subject_commit_sha) is not None
        and commit_sha == runtime_subject_commit_sha
    )
    source = {
        "branch": branch,
        "commit_sha": commit_sha,
        "runtime_subject_commit_sha": runtime_subject_commit_sha,
        "source_identity_attested": source_identity_ok,
        "image_supplied": bool(image),
        "base_url": safe_base_url,
        "tenant_id": tenant_id,
    }
    if not source_identity_ok:
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": False,
            "status": "blocked_source_identity",
            "source": source,
            "cases": _blocked_cases("exact_main_source_identity_required"),
            "open_gaps": ["exact_main_source_identity_required"],
            "runtime_browser_evidence": "not_applicable_not_run",
        }
    if not allow_mutations:
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": False,
            "status": "blocked_mutation_confirmation",
            "source": source,
            "cases": _blocked_cases("allow_mutations_required"),
            "open_gaps": ["allow_mutations_required"],
            "runtime_browser_evidence": "not_applicable_not_run",
        }
    if not gateway_secret:
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": False,
            "status": "blocked_gateway_secret",
            "source": source,
            "cases": _blocked_cases("gateway_secret_required"),
            "open_gaps": ["gateway_secret_required"],
            "runtime_browser_evidence": "not_applicable_not_run",
        }
    if owner_user_id == stale_principal_user_id:
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": False,
            "status": "blocked_invalid_stale_principal",
            "source": source,
            "cases": _blocked_cases("stale_principal_must_differ_from_owner"),
            "open_gaps": ["stale_principal_must_differ_from_owner"],
            "runtime_browser_evidence": "not_applicable_not_run",
        }

    def invoke(method: str, *parts: str, query: str = "", headers: dict[str, str]) -> tuple[int, Any]:
        return request_json(
            _route(safe_base_url, *parts, query=query),
            method=method,
            headers=headers,
            timeout_seconds=timeout_seconds,
        )

    owner_headers = _principal_headers(
        user_id=owner_user_id,
        roles="user",
        tenant_id=tenant_id,
        gateway_secret=gateway_secret,
    )
    admin_headers = _principal_headers(
        user_id=admin_user_id,
        roles="admin",
        tenant_id=tenant_id,
        gateway_secret=gateway_secret,
    )
    stale_headers = _principal_headers(
        user_id=stale_principal_user_id,
        roles="user",
        tenant_id=tenant_id,
        gateway_secret=gateway_secret,
    )

    queued_before_status, queued_before_payload = invoke("GET", "runs", queued_run_id, headers=owner_headers)
    queued_cancel_status, queued_cancel_payload = invoke("POST", "runs", queued_run_id, "cancel", headers=owner_headers)
    queued_after_status, queued_after_payload = invoke("GET", "runs", queued_run_id, headers=owner_headers)
    queued_steps_status, queued_steps_payload = invoke("GET", "runs", queued_run_id, "steps", headers=owner_headers)
    queued_after = _as_dict(queued_after_payload)
    queued_cancel = _as_dict(queued_cancel_payload)
    queued_steps = _steps(queued_steps_payload)
    queued_cancel_ok = (
        queued_before_status == 200
        and _as_dict(queued_before_payload).get("status") == "queued"
        and queued_cancel_status == 200
        and queued_cancel.get("status") == "cancelled"
        and queued_after_status == 200
        and queued_after.get("status") == "cancelled"
        and queued_steps_status == 200
        and all(str(step.get("status") or "") != "running" for step in queued_steps)
        and queued_after.get("queue_position") is None
    )

    running_cancel_status, running_cancel_payload = invoke("POST", "runs", running_run_id, "cancel", headers=owner_headers)
    running_after_status, running_after_payload = invoke("GET", "runs", running_run_id, headers=owner_headers)
    running_events_status, running_events_payload = invoke("GET", "runs", running_run_id, "events", query="?after_sequence=0", headers=owner_headers)
    running_admin_status, running_admin_payload = invoke("GET", "admin", "runs", running_run_id, headers=admin_headers)
    running_after = _as_dict(running_after_payload)
    running_events = _events(running_events_payload)
    cancellation_sequences = [
        int(event.get("sequence") or 0)
        for event in running_events
        if str(event.get("event_type") or "") == "cancel_requested"
    ]
    cancellation_sequence = min(cancellation_sequences) if cancellation_sequences else None
    post_cancel_start_events = [
        event
        for event in running_events
        if cancellation_sequence is not None
        and int(event.get("sequence") or 0) > cancellation_sequence
        and str(event.get("event_type") or "") in POST_CANCEL_START_EVENT_TYPES
    ]
    sandbox_leases = _as_list(_as_dict(running_admin_payload).get("sandbox_leases"))
    sandbox_cleanup_ok = isinstance(_as_dict(running_admin_payload).get("sandbox_leases"), list) and not any(
        _as_dict(lease).get("status") == "active" for lease in sandbox_leases
    )
    running_cancel_ok = (
        running_cancel_status == 200
        and str(_as_dict(running_cancel_payload).get("status") or "") in CANCEL_EFFECT_STATUSES
        and running_after_status == 200
        and str(running_after.get("status") or "") in CANCEL_EFFECT_STATUSES
        and bool(running_after.get("cancel_requested_at"))
        and running_events_status == 200
        and cancellation_sequence is not None
        and not post_cancel_start_events
        and running_admin_status == 200
        and sandbox_cleanup_ok
    )

    retry_operation_id = str(uuid4())
    retry_query = f"?operation_id={retry_operation_id}"
    retry_first_status, retry_first_payload = invoke("POST", "runs", retry_source_run_id, "retry", query=retry_query, headers=owner_headers)
    retry_second_status, retry_second_payload = invoke("POST", "runs", retry_source_run_id, "retry", query=retry_query, headers=owner_headers)
    retry_first = _as_dict(retry_first_payload)
    retry_second = _as_dict(retry_second_payload)
    retry_child_id = str(retry_first.get("run_id") or "")
    retry_operation_status, retry_operation_payload = invoke(
        "GET",
        "runs",
        retry_source_run_id,
        "control-operations",
        "retry",
        retry_operation_id,
        headers=owner_headers,
    )
    retry_events_status, retry_events_payload = (
        invoke("GET", "runs", retry_child_id, "events", query="?after_sequence=0", headers=owner_headers)
        if retry_child_id
        else (0, None)
    )
    retry_operation = _as_dict(retry_operation_payload)
    retry_ok = (
        retry_first_status == 200
        and retry_second_status == 200
        and retry_child_id != ""
        and retry_child_id == str(retry_second.get("run_id") or "")
        and retry_first.get("source_run_id") == retry_source_run_id
        and retry_first.get("action") == "retry"
        and retry_first.get("operation_id") == retry_operation_id
        and retry_operation_status == 200
        and retry_operation.get("run_id") == retry_child_id
        and retry_events_status == 200
        and _event_lineage_matches(_events(retry_events_payload), source_run_id=retry_source_run_id)
    )

    resume_readiness_status, resume_readiness_payload = invoke(
        "GET", "runs", resume_source_run_id, "control", "readiness", headers=owner_headers
    )
    resume_operation_id = str(uuid4())
    resume_query = f"?operation_id={resume_operation_id}"
    resume_first_status, resume_first_payload = invoke("POST", "runs", resume_source_run_id, "resume", query=resume_query, headers=owner_headers)
    resume_second_status, resume_second_payload = invoke("POST", "runs", resume_source_run_id, "resume", query=resume_query, headers=owner_headers)
    resume_first = _as_dict(resume_first_payload)
    resume_second = _as_dict(resume_second_payload)
    resume_child_id = str(resume_first.get("run_id") or "")
    resume_manifest_status, resume_manifest_payload = (
        invoke("GET", "runs", resume_child_id, "resume", "manifest", headers=owner_headers)
        if resume_child_id
        else (0, None)
    )
    resume_manifest = _as_dict(resume_manifest_payload)
    resume_action = _as_dict(_as_dict(resume_readiness_payload).get("actions")).get("resume")
    resume_ok = (
        resume_readiness_status == 200
        and _as_dict(resume_action).get("enabled") is True
        and resume_first_status == 200
        and resume_second_status == 200
        and resume_child_id != ""
        and resume_child_id == str(resume_second.get("run_id") or "")
        and resume_first.get("source_run_id") == resume_source_run_id
        and resume_first.get("action") == "resume"
        and resume_manifest_status == 200
        and resume_manifest.get("source_run_id") == resume_source_run_id
        and resume_manifest.get("resume_enabled") is True
        and int(_as_dict(resume_manifest.get("counts")).get("reuse_pending") or 0) > 0
    )

    queued_events_status, queued_events_payload = invoke(
        "GET", "runs", queued_run_id, "events", query="?after_sequence=0", headers=owner_headers
    )
    queued_stream_status, queued_stream_payload = invoke(
        "GET", "runs", queued_run_id, "events", "stream", query="?after_sequence=0", headers=owner_headers
    )
    queued_events = _events(queued_events_payload)
    stream_frames = _stream_frames(queued_stream_payload)
    stream_run_events = [frame for frame in stream_frames if frame.get("event") == "run_event"]
    stream_ids = [str(frame.get("id") or "") for frame in stream_run_events]
    stream_sequences = [int(_as_dict(frame.get("data")).get("sequence") or 0) for frame in stream_run_events]
    event_ids = [str(event.get("event_id") or "") for event in queued_events]
    event_sequences = [int(event.get("sequence") or 0) for event in queued_events]
    terminal_done = any(
        frame.get("event") == "done" and _as_dict(frame.get("data")).get("status") == "cancelled"
        for frame in stream_frames
    )
    sse_ok = (
        queued_events_status == 200
        and queued_stream_status == 200
        and bool(event_ids)
        and event_ids == stream_ids
        and event_sequences == stream_sequences
        and len(stream_ids) == len(set(stream_ids))
        and stream_sequences == sorted(stream_sequences)
        and terminal_done
    )

    refresh_first_status, refresh_first_payload = invoke("GET", "runs", queued_run_id, headers=owner_headers)
    refresh_second_status, refresh_second_payload = invoke("GET", "runs", queued_run_id, headers=owner_headers)
    refresh_readiness_status, refresh_readiness_payload = invoke(
        "GET", "runs", queued_run_id, "control", "readiness", headers=owner_headers
    )
    refresh_cancel_action = _as_dict(_as_dict(refresh_readiness_payload).get("actions")).get("cancel")
    refresh_ok = (
        refresh_first_status == 200
        and refresh_second_status == 200
        and _same_terminal_hydration(_as_dict(refresh_first_payload), _as_dict(refresh_second_payload))
        and refresh_readiness_status == 200
        and _as_dict(refresh_cancel_action).get("enabled") is False
    )

    stale_principal_status, stale_principal_payload = invoke("GET", "runs", retry_source_run_id, headers=stale_headers)
    stale_session_status, stale_session_payload = invoke("GET", "runs", stale_session_run_id, headers=owner_headers)
    stale_ok = (
        stale_principal_status == 404
        and _as_dict(stale_principal_payload).get("detail") == "run_not_found"
        and stale_session_status == 404
        and _as_dict(stale_session_payload).get("detail") == "run_not_found"
    )

    cases = {
        "queued_cancel_no_execution": _case(
            queued_cancel_ok,
            queued_read_status=queued_before_status,
            cancel_status=queued_cancel_status,
            hydrated_status=queued_after_status,
            steps_status=queued_steps_status,
            terminal=queued_after.get("status") == "cancelled",
            no_running_steps=all(str(step.get("status") or "") != "running" for step in queued_steps),
            queue_position_cleared=queued_after.get("queue_position") is None,
        ),
        "running_cancel_fence_and_sandbox_cleanup": _case(
            running_cancel_ok,
            cancel_status=running_cancel_status,
            hydrated_status=running_after_status,
            events_status=running_events_status,
            admin_detail_status=running_admin_status,
            cancellation_fence_observed=cancellation_sequence is not None,
            post_cancel_start_events_absent=not post_cancel_start_events,
            active_sandbox_leases_absent=sandbox_cleanup_ok,
        ),
        "retry_idempotency_and_lineage": _case(
            retry_ok,
            first_status=retry_first_status,
            replay_status=retry_second_status,
            operation_status=retry_operation_status,
            child_events_status=retry_events_status,
            same_child=bool(retry_child_id) and retry_child_id == str(retry_second.get("run_id") or ""),
            lineage_preserved=_event_lineage_matches(_events(retry_events_payload), source_run_id=retry_source_run_id),
        ),
        "resume_eligibility_and_lineage": _case(
            resume_ok,
            readiness_status=resume_readiness_status,
            first_status=resume_first_status,
            replay_status=resume_second_status,
            manifest_status=resume_manifest_status,
            readiness_enabled=_as_dict(resume_action).get("enabled") is True,
            same_child=bool(resume_child_id) and resume_child_id == str(resume_second.get("run_id") or ""),
            source_lineage_preserved=resume_manifest.get("source_run_id") == resume_source_run_id,
            reuse_pending=int(_as_dict(resume_manifest.get("counts")).get("reuse_pending") or 0) > 0,
        ),
        "sse_duplicate_order_replay": _case(
            sse_ok,
            events_status=queued_events_status,
            stream_status=queued_stream_status,
            replay_event_count=len(stream_ids),
            unique_event_ids=len(stream_ids) == len(set(stream_ids)),
            ordered_sequences=stream_sequences == sorted(stream_sequences),
            replay_matches_event_feed=event_ids == stream_ids and event_sequences == stream_sequences,
            terminal_done_observed=terminal_done,
        ),
        "refresh_terminal_hydration": _case(
            refresh_ok,
            first_refresh_status=refresh_first_status,
            second_refresh_status=refresh_second_status,
            readiness_status=refresh_readiness_status,
            terminal_hydrated=_same_terminal_hydration(_as_dict(refresh_first_payload), _as_dict(refresh_second_payload)),
            cancel_disabled=_as_dict(refresh_cancel_action).get("enabled") is False,
        ),
        "stale_principal_and_session_denial": _case(
            stale_ok,
            stale_principal_status=stale_principal_status,
            stale_session_status=stale_session_status,
            stale_principal_denied=_as_dict(stale_principal_payload).get("detail") == "run_not_found",
            stale_session_denied=_as_dict(stale_session_payload).get("detail") == "run_not_found",
        ),
    }
    open_gaps = [case_id for case_id, result in cases.items() if result["ok"] is not True]
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": not open_gaps,
        "status": "accepted_for_operator_review" if not open_gaps else "blocked_runtime_acceptance",
        "source": source,
        "cases": cases,
        "open_gaps": open_gaps,
        "local_evidence": "not_run_by_runtime_verifier",
        "integration_evidence": "not_run_by_runtime_verifier",
        "runtime_browser_evidence": "not_applicable_not_run",
        "does_not_create_or_cleanup_fixtures": True,
        "does_not_invoke_executor_sdk": True,
    }


def main() -> int:
    """Parse operator inputs, run the matrix, and return a CI-friendly result."""

    parser = argparse.ArgumentParser(description="Verify exact-main Run Control acceptance fixtures.")
    parser.add_argument("--base-url", default=os.environ.get("AI_PLATFORM_BASE_URL", "http://127.0.0.1:8020"))
    parser.add_argument("--gateway-secret-env", default="AI_PLATFORM_GATEWAY_SECRET")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--runtime-subject-commit-sha", required=True)
    parser.add_argument("--image", default="")
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--owner-user-id", required=True)
    parser.add_argument("--admin-user-id", required=True)
    parser.add_argument("--stale-principal-user-id", required=True)
    parser.add_argument("--queued-run-id", required=True)
    parser.add_argument("--running-run-id", required=True)
    parser.add_argument("--retry-source-run-id", required=True)
    parser.add_argument("--resume-source-run-id", required=True)
    parser.add_argument("--stale-session-run-id", required=True)
    parser.add_argument("--allow-mutations", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    args = parser.parse_args()

    evidence = build_exact_main_run_control_acceptance(
        base_url=args.base_url,
        gateway_secret=os.environ.get(args.gateway_secret_env, ""),
        branch=args.branch,
        commit_sha=args.commit_sha,
        runtime_subject_commit_sha=args.runtime_subject_commit_sha,
        image=args.image,
        tenant_id=args.tenant_id,
        owner_user_id=args.owner_user_id,
        admin_user_id=args.admin_user_id,
        stale_principal_user_id=args.stale_principal_user_id,
        queued_run_id=args.queued_run_id,
        running_run_id=args.running_run_id,
        retry_source_run_id=args.retry_source_run_id,
        resume_source_run_id=args.resume_source_run_id,
        stale_session_run_id=args.stale_session_run_id,
        allow_mutations=args.allow_mutations,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
