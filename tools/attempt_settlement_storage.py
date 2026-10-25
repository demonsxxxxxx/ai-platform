"""Prepare and explicitly activate the durable Attempt settlement reader."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.db import close_pool, transaction  # noqa: E402
from app.runs.infrastructure.attempt_settlement_projection_postgres import (  # noqa: E402
    activate_attempt_settlement_storage,
    get_attempt_settlement_storage_version,
    prepare_attempt_settlement_storage_cutover,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Read the persistent settlement storage version")
    prepare = commands.add_parser("prepare", help="Backfill and compare without activating")
    prepare.add_argument("--page-size", type=int, default=100)
    prepare.add_argument("--max-attempts", type=int, default=10000)
    activate = commands.add_parser("activate", help="Recompare under writer fences and activate")
    activate.add_argument("--page-size", type=int, default=100)
    activate.add_argument("--max-attempts", type=int, default=10000)
    activate.add_argument("--lock-timeout-seconds", type=int, default=10)
    activate.add_argument("--statement-timeout-seconds", type=int, default=300)
    activate.add_argument("--confirm-run-and-sandbox-writers-drained", action="store_true", required=True)
    activate.add_argument("--confirm-all-source-writers-n40-compatible", action="store_true", required=True)
    activate.add_argument("--confirm-real-sdk-acceptance-complete", action="store_true", required=True)
    activate.add_argument("--confirm-real-sandbox-lifecycle-acceptance-complete", action="store_true", required=True)
    activate.add_argument("--confirm-fourteen-lifecycle-scenarios-complete", action="store_true", required=True)
    return parser


async def _run(args: argparse.Namespace) -> dict[str, object]:
    try:
        if args.command == "status":
            async with transaction() as conn:
                version = await get_attempt_settlement_storage_version(conn)
            return {"status": "current", "storage_version": version}
        if args.command == "prepare":
            return await prepare_attempt_settlement_storage_cutover(
                transaction,
                page_limit=args.page_size,
                max_attempts=args.max_attempts,
            )
        async with transaction() as conn:
            result = await activate_attempt_settlement_storage(
                conn,
                operator_confirmations={
                    "run_and_sandbox_writers_drained": args.confirm_run_and_sandbox_writers_drained,
                    "all_source_writers_n40_compatible": args.confirm_all_source_writers_n40_compatible,
                    "real_sdk_acceptance_complete": args.confirm_real_sdk_acceptance_complete,
                    "real_sandbox_lifecycle_acceptance_complete": args.confirm_real_sandbox_lifecycle_acceptance_complete,
                    "fourteen_lifecycle_scenarios_complete": args.confirm_fourteen_lifecycle_scenarios_complete,
                },
                page_limit=args.page_size,
                max_attempts=args.max_attempts,
                lock_timeout_seconds=args.lock_timeout_seconds,
                statement_timeout_seconds=args.statement_timeout_seconds,
            )
        return {
            **result,
            "operator_confirmations": {
                "run_and_sandbox_writers_drained": args.confirm_run_and_sandbox_writers_drained,
                "all_source_writers_n40_compatible": args.confirm_all_source_writers_n40_compatible,
                "real_sdk_acceptance_complete": args.confirm_real_sdk_acceptance_complete,
                "real_sandbox_lifecycle_acceptance_complete": args.confirm_real_sandbox_lifecycle_acceptance_complete,
                "fourteen_lifecycle_scenarios_complete": args.confirm_fourteen_lifecycle_scenarios_complete,
            },
            "operator_confirmations_verified_by_code": False,
        }
    finally:
        await close_pool()


def main() -> None:
    args = _parser().parse_args()
    result = asyncio.run(_run(args))
    print(json.dumps(result, sort_keys=True, default=str))
    raise SystemExit(0 if result.get("status") in {"current", "ready", "already_active", "activated"} else 1)


if __name__ == "__main__":
    main()
