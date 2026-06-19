#!/usr/bin/env python3
"""Render a dry-run plan for switching the official 211 frontend entry.

The plan is intentionally non-executing. It records preflight, switch, rollback,
and smoke commands so the operator can approve and run the same sequence without
inventing process-management steps during the cutover.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from typing import Any


SCHEMA_VERSION = "ai-platform.frontend-official-entry-switch-plan.v1"


def _assert_safe_arg(value: str) -> str:
    if not value or any(char in value for char in "\n\r\0"):
        raise ValueError("unsafe shell argument")
    if any(token in value for token in [";", "&&", "||", "$(", "`"]):
        raise ValueError("unsafe shell argument")
    return value


def _quote(value: str) -> str:
    return shlex.quote(_assert_safe_arg(value))


def _server_command(script: str, *, port: int, root: str, api_base: str) -> str:
    parts = [
        "python3",
        _quote(script),
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
        "--root",
        _quote(root),
        "--api-base",
        _quote(api_base),
    ]
    return " ".join(parts)


def _validate_commit(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("expected_commit_must_be_40_hex_chars")
    return value


def build_switch_plan(
    *,
    old_pid: int,
    old_command: str,
    new_server_script: str,
    new_root: str,
    api_base: str,
    port: int,
    expected_commit: str,
    log_path: str,
) -> dict[str, Any]:
    """Build a non-executing official-entry switch plan."""
    if old_pid <= 0:
        raise ValueError("old_pid_must_be_positive")
    if port <= 0 or port > 65535:
        raise ValueError("port_out_of_range")
    expected_commit = _validate_commit(expected_commit)
    _assert_safe_arg(old_command)
    _assert_safe_arg(log_path)

    new_command = _server_command(new_server_script, port=port, root=new_root, api_base=api_base)
    rollback_log = log_path[:-4] + ".rollback.log" if log_path.endswith(".log") else log_path + ".rollback.log"

    return {
        "schema_version": SCHEMA_VERSION,
        "requires_operator_approval": True,
        "does_not_execute": True,
        "target": {
            "port": port,
            "new_root": new_root,
            "api_base": api_base,
            "expected_commit": expected_commit,
            "log_path": log_path,
        },
        "current_entry": {
            "old_pid": old_pid,
            "old_command": old_command,
        },
        "preflight_checks": [
            "confirm_old_pid_matches_command",
            "confirm_new_root_has_index",
            "confirm_build_provenance_matches_expected_commit",
            "confirm_api_health_ok",
        ],
        "preflight_commands": [
            f"ps -p {old_pid} -o pid,args",
            f"test -f {_quote(new_root)}/index.html",
            f"python3 - <<'PY'\nimport json\nfrom pathlib import Path\np=Path({_quote(new_root)!r})/'ai-platform-build-provenance.json'\ndata=json.loads(p.read_text())\nassert data['git']['commit'] == {_quote(expected_commit)!r}\nassert data['git']['dirty'] is False\nPY",
            f"curl -fsS {_quote(api_base.rstrip('/') + '/api/ai/health')}",
        ],
        "switch_commands": [
            f"kill {old_pid}",
            f"nohup {new_command} > {_quote(log_path)} 2>&1 &",
        ],
        "post_switch_smoke_commands": [
            "python3 tools/frontend_static_proxy_smoke.py "
            f"--base-url http://127.0.0.1:{port} "
            f"--expected-commit {expected_commit} --timeout 8"
        ],
        "manual_company_login_gate": (
            "tools/verify_company_login_pair.sh "
            f"--base-url {api_base.rstrip('/')} "
            f"--frontend-url http://10.56.0.211:{port}"
        ),
        "rollback_commands": [
            f"pkill -f 'serve_ai_platform_frontend.py --host 0.0.0.0 --port {port}'",
            f"nohup {old_command} > {_quote(rollback_log)} 2>&1 &",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-pid", type=int, required=True)
    parser.add_argument("--old-command", required=True)
    parser.add_argument("--new-server-script", required=True)
    parser.add_argument("--new-root", required=True)
    parser.add_argument("--api-base", default="http://127.0.0.1:8020")
    parser.add_argument("--port", type=int, default=18001)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--log-path", required=True)
    args = parser.parse_args()
    try:
        plan = build_switch_plan(
            old_pid=args.old_pid,
            old_command=args.old_command,
            new_server_script=args.new_server_script,
            new_root=args.new_root,
            api_base=args.api_base,
            port=args.port,
            expected_commit=args.expected_commit,
            log_path=args.log_path,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
