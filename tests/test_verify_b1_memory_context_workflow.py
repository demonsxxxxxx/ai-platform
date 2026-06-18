import importlib
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlparse


SCHEMA_VERSION = "ai-platform.b1-memory-context-workflow-smoke.v1"
ACCEPTANCE_GAP = "211_memory_enabled_document_workflow_smoke"


def load_smoke_module():
    try:
        return importlib.import_module("tools.verify_b1_memory_context_workflow")
    except ModuleNotFoundError as exc:
        raise AssertionError("tools.verify_b1_memory_context_workflow is missing") from exc


def run_server(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def public_context_payload(memory_count):
    return {
        "referenced_materials": {
            "message_count": 1,
            "file_count": 0,
            "artifact_count": 0,
            "memory_record_count": memory_count,
        },
        "used_context_summary": {
            "source": "manual_context_snapshot",
            "input_keys": ["task", "memory"],
            "memory_policy_source": "user",
            "long_term_memory_read": False,
        },
        "execution_tier": "sdk_only_writing",
        "context_pack_version": "2026-06-18",
        "context_pack_generated_at": "2026-06-18T00:00:00Z",
    }


class B1MemoryContextWorkflowHandler(BaseHTTPRequestHandler):
    records = {}
    policies = {}
    snapshots = []
    deleted_ids = set()
    run_id = "run-b1-smoke"
    session_id = "ses-b1-smoke"
    expected_secret = "test-secret"
    allow_create_when_disabled = False
    leak_private_context = False
    leak_deleted_memory_in_future_context = False

    def log_message(self, format, *args):  # noqa: A002
        return

    @classmethod
    def reset_state(cls):
        cls.records = {}
        cls.policies = {}
        cls.snapshots = []
        cls.deleted_ids = set()

    def do_POST(self):  # noqa: N802
        if not self._authorized():
            return
        if self.path == "/api/ai/runs":
            payload = self._read_json()
            self._send_json(
                200,
                {
                    "run_id": self.run_id,
                    "session_id": payload.get("session_id") or self.session_id,
                    "status": "queued",
                },
            )
            return
        if self.path == "/api/ai/memory/records":
            policy = self._current_policy()
            if not policy.get("memory_enabled", True) and not self.allow_create_when_disabled:
                self._send_json(403, {"detail": "memory_policy_disabled"})
                return
            payload = self._read_json()
            if payload.get("session_id") != self.session_id:
                self._send_json(404, {"detail": "session_not_found"})
                return
            record_id = "mem-b1-smoke"
            record = {
                "memory_record_id": record_id,
                "tenant_id": self._tenant_id(),
                "workspace_id": payload.get("workspace_id") or "default",
                "user_id": self._user_id(),
                "agent_id": payload.get("agent_id") or "general-agent",
                "session_id": payload.get("session_id"),
                "record_type": payload.get("record_type") or "task_note",
                "content": "public memory note",
                "metadata": {"source": "b1_smoke"},
                "status": "active",
            }
            self.records[record_id] = record
            self._send_json(200, {"memory_record": record})
            return
        if self.path == f"/api/ai/runs/{self.run_id}/context/snapshots":
            payload = self._read_json()
            included_memory_record_ids = list(payload.get("included_memory_record_ids") or [])
            memory_count = len(included_memory_record_ids)
            if self.leak_deleted_memory_in_future_context and not included_memory_record_ids:
                memory_count = 1
            snapshot_payload = public_context_payload(memory_count)
            if self.leak_private_context:
                snapshot_payload["executor_private_payload"] = {
                    "raw_storage_key": "tenants/default/runs/run-b1/private.json"
                }
            snapshot = {
                "context_snapshot_id": f"ctx-{len(self.snapshots) + 1}",
                "schema_version": "ai-platform.context-snapshot.v1",
                "tenant_id": self._tenant_id(),
                "workspace_id": payload.get("workspace_id") or "default",
                "user_id": self._user_id(),
                "session_id": self.session_id,
                "run_id": self.run_id,
                "trace_id": "trace-b1-smoke",
                "context_kind": payload.get("context_kind") or "executor",
                "redaction_summary": {"mode": "strict"},
                "payload": snapshot_payload,
            }
            self.snapshots.insert(0, snapshot)
            self._send_json(200, {"context_snapshot": snapshot})
            return
        self._send_json(404, {"detail": "not_found"})

    def do_PUT(self):  # noqa: N802
        if not self._authorized():
            return
        if self.path == "/api/ai/memory/policy":
            payload = self._read_json()
            policy = {
                "tenant_id": self._tenant_id(),
                "workspace_id": payload.get("workspace_id") or "default",
                "user_id": self._user_id(),
                "agent_id": payload.get("agent_id") or "general-agent",
                "memory_enabled": bool(payload.get("memory_enabled", True)),
                "long_term_memory_enabled": False,
                "retention_days": int(payload.get("retention_days") or 90),
                "redaction_mode": payload.get("redaction_mode") or "strict",
                "source": "user",
                "reason": payload.get("reason") or "b1 smoke",
            }
            self.policies[self._policy_key()] = policy
            self._send_json(200, {"memory_policy": policy})
            return
        self._send_json(404, {"detail": "not_found"})

    def do_DELETE(self):  # noqa: N802
        if not self._authorized():
            return
        record_id = self.path.split("?", 1)[0].rsplit("/", 1)[-1]
        record = self.records.get(record_id)
        if record is None or self._user_id() != record.get("user_id"):
            self._send_json(404, {"detail": "memory_record_not_found"})
            return
        self.deleted_ids.add(record_id)
        record = {**record, "status": "deleted", "deleted_at": "2026-06-18T00:00:01Z"}
        self.records[record_id] = record
        self._send_json(200, {"memory_record": record})

    def do_GET(self):  # noqa: N802
        if not self._authorized():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/ai/memory/policy":
            self._send_json(200, {"memory_policy": self._current_policy()})
            return
        if parsed.path == "/api/ai/memory/records":
            query = parse_qs(parsed.query)
            if self._user_id() != "b1-memory-smoke-user" or query.get("session_id", [""])[0] != self.session_id:
                self._send_json(404, {"detail": "session_not_found"})
                return
            records = [
                record
                for record_id, record in self.records.items()
                if record_id not in self.deleted_ids and record.get("user_id") == self._user_id()
            ]
            self._send_json(200, {"memory_records": records})
            return
        if parsed.path == f"/api/ai/runs/{self.run_id}/context/snapshots":
            if self._user_id() != "b1-memory-smoke-user":
                self._send_json(404, {"detail": "run_not_found"})
                return
            self._send_json(200, {"run_id": self.run_id, "context_snapshots": self.snapshots})
            return
        if parsed.path == f"/api/ai/runs/{self.run_id}/playback":
            if self._user_id() != "b1-memory-smoke-user":
                self._send_json(404, {"detail": "run_not_found"})
                return
            latest_payload = self.snapshots[0]["payload"] if self.snapshots else public_context_payload(0)
            payload = {
                "contract_version": "ai-platform.run-playback.v1",
                "run_id": self.run_id,
                "run": {
                    "run_id": self.run_id,
                    "session_id": self.session_id,
                    "agent_id": "general-agent",
                    "skill_id": None,
                    "status": "queued",
                },
                "events": [],
                "artifacts": [],
                "steps": [],
                "context_ref": latest_payload,
            }
            if self.leak_private_context:
                payload["context_ref"]["sandbox_workdir"] = "/tmp/ai-platform/private"
            self._send_json(200, payload)
            return
        self._send_json(404, {"detail": "not_found"})

    def _authorized(self):
        if self.headers.get("X-AI-Gateway-Secret") != self.expected_secret:
            self._send_json(403, {"detail": "invalid_gateway_principal_secret"})
            return False
        return True

    def _tenant_id(self):
        return self.headers.get("X-AI-Tenant-ID", "default")

    def _user_id(self):
        return self.headers.get("X-AI-User-ID", "")

    def _policy_key(self):
        return (self._tenant_id(), self._user_id())

    def _current_policy(self):
        return self.policies.get(
            self._policy_key(),
            {
                "tenant_id": self._tenant_id(),
                "workspace_id": "default",
                "user_id": self._user_id(),
                "agent_id": "general-agent",
                "memory_enabled": True,
                "long_term_memory_enabled": False,
                "retention_days": 90,
                "redaction_mode": "strict",
                "source": "default",
            },
        )

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _send_json(self, status, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class PolicyDisabledNotEnforcedHandler(B1MemoryContextWorkflowHandler):
    allow_create_when_disabled = True


class LeakyProjectionHandler(B1MemoryContextWorkflowHandler):
    leak_private_context = True


class DeletedMemoryLeakHandler(B1MemoryContextWorkflowHandler):
    leak_deleted_memory_in_future_context = True


def run_b1_smoke(handler_cls):
    handler_cls.reset_state()
    smoke = load_smoke_module()
    server = run_server(handler_cls)
    try:
        return smoke.build_b1_memory_context_workflow_smoke(
            base_url=f"http://127.0.0.1:{server.server_port}",
            gateway_secret="test-secret",
            commit_sha="3e86786",
            runtime_subject_commit_sha="fadbb83",
            image="ai-platform:fadbb83-b1-memory",
            timeout_seconds=5,
        )
    finally:
        server.shutdown()


def test_b1_memory_context_workflow_smoke_verifies_policy_context_delete_and_projection():
    payload = run_b1_smoke(B1MemoryContextWorkflowHandler)

    assert payload["ok"] is True
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["acceptance_gap"] == ACCEPTANCE_GAP
    assert payload["target"] == "211_api_memory_context_workflow"
    assert payload["redaction_scan_status"] == "passed"
    assert payload["source"]["commit_sha"] == "3e86786"
    assert payload["source"]["runtime_subject_commit_sha"] == "fadbb83"
    assert payload["checks"]["memory_policy_disabled_blocks_create"]["passed"] is True
    assert payload["checks"]["memory_policy_enabled_for_governed_scope"]["passed"] is True
    assert payload["checks"]["memory_record_create_and_list"]["passed"] is True
    assert payload["checks"]["context_snapshot_public_provenance"]["passed"] is True
    assert payload["checks"]["playback_public_projection"]["passed"] is True
    assert payload["checks"]["cross_user_context_denied"]["passed"] is True
    assert payload["checks"]["deleted_memory_absent_from_future_context"]["passed"] is True
    assert payload["checks"]["long_term_memory_fail_closed"]["passed"] is True
    assert payload["checks"]["no_private_projection_leakage"]["passed"] is True
    assert payload["non_expansion_invariants"] == {
        "long_term_cross_session_memory_enabled": False,
        "stores_private_executor_material_as_memory": False,
        "frontend_state_is_canonical_context": False,
        "gate_closure_claimed": False,
    }
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    assert "test-secret" not in serialized
    assert "executor_private_payload" not in serialized
    assert "raw_storage_key" not in serialized
    assert "sandbox_workdir" not in serialized


def test_b1_memory_context_workflow_smoke_fails_when_policy_disablement_does_not_block_writes():
    payload = run_b1_smoke(PolicyDisabledNotEnforcedHandler)

    assert payload["ok"] is False
    assert payload["checks"]["memory_policy_disabled_blocks_create"]["passed"] is False
    assert payload["checks"]["no_private_projection_leakage"]["passed"] is True


def test_b1_memory_context_workflow_smoke_fails_closed_on_private_projection_leak():
    payload = run_b1_smoke(LeakyProjectionHandler)

    assert payload["ok"] is False
    assert payload["redaction_scan_status"] == "failed"
    assert payload["checks"]["context_snapshot_public_provenance"]["passed"] is False
    assert payload["checks"]["playback_public_projection"]["passed"] is False
    assert payload["checks"]["no_private_projection_leakage"]["passed"] is False
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    assert "raw_storage_key" not in serialized
    assert "sandbox_workdir" not in serialized


def test_b1_memory_context_workflow_smoke_fails_when_deleted_memory_reappears_in_future_context():
    payload = run_b1_smoke(DeletedMemoryLeakHandler)

    assert payload["ok"] is False
    assert payload["checks"]["deleted_memory_absent_from_future_context"]["passed"] is False
    assert payload["checks"]["memory_record_create_and_list"]["passed"] is True


def test_b1_memory_context_workflow_smoke_cli_emits_safe_json_and_exit_status():
    B1MemoryContextWorkflowHandler.reset_state()
    server = run_server(B1MemoryContextWorkflowHandler)
    env = {**os.environ, "TEST_AI_PLATFORM_GATEWAY_SECRET": "test-secret"}
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(Path("tools") / "verify_b1_memory_context_workflow.py"),
                "--base-url",
                f"http://127.0.0.1:{server.server_port}",
                "--gateway-secret-env",
                "TEST_AI_PLATFORM_GATEWAY_SECRET",
                "--commit-sha",
                "3e86786",
                "--runtime-subject-commit-sha",
                "fadbb83",
                "--image",
                "ai-platform:fadbb83-b1-memory",
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )
    finally:
        server.shutdown()

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["schema_version"] == SCHEMA_VERSION
    assert "test-secret" not in result.stdout
