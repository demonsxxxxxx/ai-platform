"""Inspect or operate one persisted execution-supervision domain."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any


_repository_root = Path(__file__).resolve().parents[1]
if str(_repository_root) not in sys.path:
    sys.path.insert(0, str(_repository_root))

from app.db import close_pool, transaction  # noqa: E402
from app.execution.domain.supervision import SupervisionDomain  # noqa: E402
from app.execution.infrastructure import supervision_postgres  # noqa: E402
from app.settings import get_settings  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status", help="show one domain and its claims")
    status.add_argument("domain", choices=[item.value for item in SupervisionDomain])
    drain = commands.add_parser("drain", help="stop new claims for one domain")
    drain.add_argument("domain", choices=[item.value for item in SupervisionDomain])
    drain.add_argument("--expected-mode", choices=("legacy", "unified"), required=True)
    drain.add_argument("--expected-version", type=int, required=True)
    activate = commands.add_parser("activate-unified", help="activate one drained domain")
    activate.add_argument("domain", choices=[item.value for item in SupervisionDomain])
    activate.add_argument("--expected-version", type=int, required=True)
    legacy = commands.add_parser("return-legacy", help="return one drained domain to legacy")
    legacy.add_argument("domain", choices=[item.value for item in SupervisionDomain])
    legacy.add_argument("--expected-version", type=int, required=True)
    recover = commands.add_parser(
        "recover-claim", help="release an expired claim after verifying its owner stopped"
    )
    recover.add_argument("domain", choices=[item.value for item in SupervisionDomain])
    recover.add_argument("--expected-domain-version", type=int, required=True)
    recover.add_argument("--expected-claim-owner-version", type=int, required=True)
    recover.add_argument("--claim-token", required=True)
    recover.add_argument("--worker-id", required=True)
    recover.add_argument("--operator-id", required=True)
    recover.add_argument(
        "--stop-evidence", required=True,
        help="non-secret reference to verified process or worker termination evidence",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    domain = SupervisionDomain(args.domain)
    if args.command in {"drain", "activate-unified", "return-legacy"} and not bool(
        getattr(get_settings(), "execution_supervision_enabled", False)
    ):
        raise RuntimeError("execution_supervision_disabled")
    try:
        async with transaction() as connection:
            if args.command == "status":
                control = await supervision_postgres.read_domain_control(connection, domain=domain)
                cursor = await connection.execute(
                    """select claim_token, owner_mode, worker_id, owner_version, expires_at,
                              recovered_at, recovered_by, recovery_evidence
                       from execution_supervision_claims where domain = %s
                       order by started_at, claim_token""",
                    (domain.value,),
                )
                result: Any = {"control": control, "claims": await cursor.fetchall()}
            elif args.command == "drain":
                result = await supervision_postgres.request_domain_drain(
                    connection,
                    domain=domain,
                    expected_version=args.expected_version,
                    expected_mode=args.expected_mode,
                )
            elif args.command == "activate-unified":
                result = await supervision_postgres.activate_unified_domain(
                    connection, domain=domain, expected_version=args.expected_version
                )
            elif args.command == "return-legacy":
                result = await supervision_postgres.return_domain_to_legacy(
                    connection, domain=domain, expected_version=args.expected_version
                )
            else:
                result = await supervision_postgres.recover_expired_domain_claim(
                    connection,
                    domain=domain,
                    expected_domain_version=args.expected_domain_version,
                    expected_claim_owner_version=args.expected_claim_owner_version,
                    claim_token=args.claim_token,
                    worker_id=args.worker_id,
                    operator_id=args.operator_id,
                    stop_evidence=args.stop_evidence,
                )
        print(json.dumps(result, sort_keys=True, default=str))
        return 0 if result is not None else 1
    finally:
        await close_pool()


def main() -> None:
    args = _parser().parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
