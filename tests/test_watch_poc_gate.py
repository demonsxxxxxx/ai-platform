from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tools import watch_poc_gate
from tools.verify_poc_gate import Gate


def test_watch_poc_gate_runs_strict_gate_after_auth_audit_is_ready(monkeypatch):
    polls = [
        Gate("company_login_audit", False, {"missing_requirements": ["ordinary_company_login_audit"]}),
        Gate("company_login_audit", True, {"ordinary_user_count": 1, "admin_user_count": 1}),
    ]
    strict_gate_calls: list[list[str]] = []

    def fake_check_auth_audit(container: str, db_user: str, db_name: str, allow_missing: bool):
        assert allow_missing is False
        return polls.pop(0)

    def fake_run_strict_gate(extra_args: list[str]) -> int:
        strict_gate_calls.append(extra_args)
        return 0

    monkeypatch.setattr(watch_poc_gate.verify_poc_gate, "check_auth_audit", fake_check_auth_audit)
    monkeypatch.setattr(watch_poc_gate, "run_strict_gate", fake_run_strict_gate)
    monkeypatch.setattr(watch_poc_gate.time, "sleep", lambda seconds: None)

    exit_code = watch_poc_gate.main(["--timeout-seconds", "30", "--interval-seconds", "10", "--frontend-url", "http://frontend.local"])

    assert exit_code == 0
    assert strict_gate_calls
    assert strict_gate_calls[0][strict_gate_calls[0].index("--frontend-url") + 1] == "http://frontend.local"
    assert "--postgres-container" in strict_gate_calls[0]


def test_watch_poc_gate_times_out_without_running_strict_gate(monkeypatch):
    strict_gate_calls: list[list[str]] = []

    def fake_check_auth_audit(container: str, db_user: str, db_name: str, allow_missing: bool):
        return Gate("company_login_audit", False, {"all_auth_login_count": 0})

    monkeypatch.setattr(watch_poc_gate.verify_poc_gate, "check_auth_audit", fake_check_auth_audit)
    monkeypatch.setattr(watch_poc_gate, "run_strict_gate", lambda extra_args: strict_gate_calls.append(extra_args) or 0)
    monkeypatch.setattr(watch_poc_gate.time, "monotonic", iter([0, 1, 3, 5]).__next__)
    monkeypatch.setattr(watch_poc_gate.time, "sleep", lambda seconds: None)

    exit_code = watch_poc_gate.main(["--timeout-seconds", "3", "--interval-seconds", "1"])

    assert exit_code == 2
    assert strict_gate_calls == []


def test_watch_poc_gate_script_help_runs_as_file():
    script = Path(__file__).resolve().parents[1] / "tools" / "watch_poc_gate.py"
    completed = subprocess.run([sys.executable, str(script), "--help"], check=False, capture_output=True, text=True)

    assert completed.returncode == 0
    assert "Wait until ordinary/admin company login audits exist" in completed.stdout
