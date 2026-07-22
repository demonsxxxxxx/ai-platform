"""Local contract coverage for the external Run Control acceptance verifier."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from tools.acceptance.run_control.verify_exact_main_run_control import (
    CASE_IDS,
    build_exact_main_run_control_acceptance,
)


class ContractTransport:
    """Stateful HTTP-envelope fixture; it never starts an app or executor SDK."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.runs = {
            "queued-run": {"status": "queued", "cancel_requested_at": None, "steps": ["pending"]},
            "running-run": {"status": "running", "cancel_requested_at": None, "steps": ["running"]},
            "retry-source": {"status": "failed", "cancel_requested_at": None, "steps": []},
            "resume-source": {"status": "failed", "cancel_requested_at": None, "steps": []},
        }
        self.events = {
            "queued-run": [self._event("queued-event", 1, "queued")],
            "running-run": [self._event("running-event", 1, "run_started")],
            "retry-child": [self._event("retry-lineage", 1, "run_retry_created", "retry-source")],
            "resume-child": [self._event("resume-lineage", 1, "run_resume_created", "resume-source")],
        }
        self.retry_child_id = "retry-child"
        self.resume_child_id = "resume-child"

    @staticmethod
    def _event(event_id: str, sequence: int, event_type: str, source_run_id: str | None = None) -> dict[str, object]:
        payload: dict[str, object] = {"visible_to_user": True}
        if source_run_id:
            payload["copied_from_run_id"] = source_run_id
        return {
            "event_id": event_id,
            "id": event_id,
            "sequence": sequence,
            "event_type": event_type,
            "type": event_type,
            "payload": payload,
        }

    def _run_payload(self, run_id: str) -> tuple[int, dict[str, object]]:
        if run_id == "stale-session-run":
            return 404, {"detail": "run_not_found"}
        run = self.runs.get(run_id)
        if run is None:
            return 404, {"detail": "run_not_found"}
        payload: dict[str, object] = {
            "run_id": run_id,
            "status": run["status"],
            "cancel_requested_at": run["cancel_requested_at"],
        }
        if run["status"] == "queued":
            payload["queue_position"] = 2
        return 200, payload

    def _stream(self, run_id: str) -> tuple[int, str]:
        frames: list[str] = []
        for event in self.events.get(run_id, []):
            frames.append(
                "\n".join(
                    [
                        f"id: {event['event_id']}",
                        "event: run_event",
                        f"data: {json.dumps(event)}",
                    ]
                )
            )
        frames.append(f"id: {run_id}:done\nevent: done\ndata: {{\"status\": \"cancelled\"}}")
        return 200, "\n\n".join(frames) + "\n\n"

    def __call__(self, url: str, *, method: str, headers: dict[str, str], timeout_seconds: float) -> tuple[int, Any]:
        parsed = urlsplit(url)
        path = parsed.path.removeprefix("/api/ai/")
        query = parse_qs(parsed.query)
        actor = headers.get("X-AI-User-ID", "")
        self.calls.append((method, path, actor))
        parts = path.split("/")
        if parts[:2] == ["admin", "runs"] and len(parts) == 3 and method == "GET":
            return 200, {"sandbox_leases": [{"status": "released"}]}
        if parts[0] != "runs":
            return 404, {"detail": "run_not_found"}
        run_id = parts[1]
        if actor == "stale-user" and run_id == "retry-source":
            return 404, {"detail": "run_not_found"}
        if len(parts) == 2 and method == "GET":
            return self._run_payload(run_id)
        if parts[2:] == ["cancel"] and method == "POST":
            if run_id == "queued-run":
                self.runs[run_id].update(status="cancelled", cancel_requested_at="2026-07-22T01:00:00Z")
                self.events[run_id].append(self._event("queued-cancel", 2, "cancel_requested"))
                return 200, {"run_id": run_id, "status": "cancelled"}
            if run_id == "running-run":
                self.runs[run_id].update(status="cancel_requested", cancel_requested_at="2026-07-22T01:01:00Z")
                self.events[run_id].append(self._event("running-cancel", 2, "cancel_requested"))
                return 200, {"run_id": run_id, "status": "cancel_requested"}
            return 404, {"detail": "active_run_not_found"}
        if parts[2:] == ["steps"] and method == "GET":
            run = self.runs.get(run_id)
            if run is None:
                return 404, {"detail": "run_not_found"}
            return 200, {"steps": [{"status": status} for status in run["steps"]]}
        if parts[2:] == ["events"] and method == "GET":
            return 200, {"events": self.events.get(run_id, [])}
        if parts[2:] == ["events", "stream"] and method == "GET":
            return self._stream(run_id)
        if parts[2:] == ["control", "readiness"] and method == "GET":
            if run_id == "resume-source":
                return 200, {"actions": {"resume": {"enabled": True}}}
            if run_id == "queued-run":
                return 200, {"actions": {"cancel": {"enabled": False}}}
            return 200, {"actions": {}}
        if len(parts) == 5 and parts[2:4] == ["control-operations", "retry"] and method == "GET":
            return 200, {"run_id": self.retry_child_id, "operation_id": parts[4], "status": "queued"}
        if parts[2:] == ["retry"] and method == "POST":
            assert query["operation_id"]
            self.runs.setdefault(self.retry_child_id, {"status": "queued", "cancel_requested_at": None, "steps": []})
            return 200, {
                "source_run_id": "retry-source",
                "run_id": self.retry_child_id,
                "action": "retry",
                "operation_id": query["operation_id"][0],
            }
        if parts[2:] == ["resume"] and method == "POST":
            assert query["operation_id"]
            self.runs.setdefault(self.resume_child_id, {"status": "queued", "cancel_requested_at": None, "steps": []})
            return 200, {
                "source_run_id": "resume-source",
                "run_id": self.resume_child_id,
                "action": "resume",
                "operation_id": query["operation_id"][0],
            }
        if parts[2:] == ["resume", "manifest"] and method == "GET" and run_id == self.resume_child_id:
            return 200, {
                "source_run_id": "resume-source",
                "resume_enabled": True,
                "counts": {"reuse_pending": 1},
            }
        return 404, {"detail": "run_not_found"}


@pytest.fixture
def contract_transport() -> ContractTransport:
    """Provide the deterministic API envelope for local verifier coverage."""

    return ContractTransport()


def _run_matrix(transport: Callable[..., tuple[int, Any]], *, allow_mutations: bool) -> dict[str, object]:
    return build_exact_main_run_control_acceptance(
        base_url="https://acceptance.example.test",
        gateway_secret="local-test-secret",
        branch="main",
        commit_sha="a" * 40,
        runtime_subject_commit_sha="a" * 40,
        image="ai-platform:test",
        tenant_id="tenant-a",
        owner_user_id="owner-user",
        admin_user_id="admin-user",
        stale_principal_user_id="stale-user",
        queued_run_id="queued-run",
        running_run_id="running-run",
        retry_source_run_id="retry-source",
        resume_source_run_id="resume-source",
        stale_session_run_id="stale-session-run",
        allow_mutations=allow_mutations,
        request_json=transport,
    )


def test_matrix_requires_explicit_mutation_confirmation(contract_transport):
    evidence = _run_matrix(contract_transport, allow_mutations=False)

    assert evidence["status"] == "blocked_mutation_confirmation"
    assert evidence["ok"] is False
    assert set(evidence["cases"]) == set(CASE_IDS)
    assert contract_transport.calls == []


def test_matrix_exercises_all_r1_http_cases_without_executor_sdk(contract_transport):
    evidence = _run_matrix(contract_transport, allow_mutations=True)

    assert evidence["ok"] is True
    assert evidence["status"] == "accepted_for_operator_review"
    assert set(evidence["cases"]) == set(CASE_IDS)
    assert all(case["ok"] is True for case in evidence["cases"].values())
    assert evidence["does_not_invoke_executor_sdk"] is True
    assert evidence["runtime_browser_evidence"] == "not_applicable_not_run"
    assert ("POST", "runs/queued-run/cancel", "owner-user") in contract_transport.calls
    assert ("POST", "runs/running-run/cancel", "owner-user") in contract_transport.calls
    assert len([call for call in contract_transport.calls if call[1] == "runs/retry-source/retry"]) == 2
    assert len([call for call in contract_transport.calls if call[1] == "runs/resume-source/resume"]) == 2
