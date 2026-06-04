#!/usr/bin/env python3
"""Wait for real company-login audit evidence, then run the strict POC gate."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import verify_poc_gate


def run_strict_gate(extra_args: list[str]) -> int:
    command = [sys.executable, "tools/verify_poc_gate.py", *extra_args]
    completed = subprocess.run(command, check=False)
    return completed.returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wait until ordinary/admin company login audits exist, then run strict POC gate.")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--interval-seconds", type=int, default=10)
    parser.add_argument("--postgres-container", default=verify_poc_gate.DEFAULT_POSTGRES_CONTAINER)
    parser.add_argument("--postgres-user", default=verify_poc_gate.DEFAULT_POSTGRES_USER)
    parser.add_argument("--postgres-db", default=verify_poc_gate.DEFAULT_POSTGRES_DB)
    parser.add_argument("--frontend-url", default=verify_poc_gate.DEFAULT_FRONTEND_URL)
    parser.add_argument("--api-url", default=verify_poc_gate.DEFAULT_API_URL)
    parser.add_argument("--frontend-dist", default=verify_poc_gate.DEFAULT_FRONTEND_DIST)
    parser.add_argument("--env-path", default=verify_poc_gate.DEFAULT_DEPLOY_ENV)
    args = parser.parse_args(argv)

    started = time.monotonic()
    deadline = started + max(1, args.timeout_seconds)
    interval = max(1, args.interval_seconds)
    last_evidence = {}
    poll = 0
    while time.monotonic() <= deadline:
        poll += 1
        gate = verify_poc_gate.check_auth_audit(
            args.postgres_container,
            args.postgres_user,
            args.postgres_db,
            allow_missing=False,
        )
        last_evidence = gate.evidence
        print(json.dumps({"poll": poll, "auth_gate_ok": gate.ok, "evidence": gate.evidence}, ensure_ascii=False), flush=True)
        if gate.ok:
            strict_args = [
                "--frontend-url",
                args.frontend_url,
                "--api-url",
                args.api_url,
                "--frontend-dist",
                args.frontend_dist,
                "--env-path",
                args.env_path,
                "--postgres-container",
                args.postgres_container,
                "--postgres-user",
                args.postgres_user,
                "--postgres-db",
                args.postgres_db,
            ]
            return run_strict_gate(strict_args)
        time.sleep(interval)

    print(
        json.dumps(
            {
                "ok": False,
                "error": "timeout_waiting_for_company_login_audit",
                "last_evidence": last_evidence,
            },
            ensure_ascii=False,
            indent=2,
        ),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
