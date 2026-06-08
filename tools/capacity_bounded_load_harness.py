import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.capacity_bounded_load_harness import (
    OPERATOR_ACKNOWLEDGEMENT,
    render_capacity_bounded_load_harness_markdown,
    run_capacity_bounded_load_harness,
)


def main() -> int:
    """Run the bounded capacity harness CLI."""
    parser = argparse.ArgumentParser(
        description="Run a bounded, read-only capacity probe without raising production defaults.",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8020")
    parser.add_argument("--gate", default="api_read_write_burst")
    parser.add_argument("--requests", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--user-id", default="codex-capacity-audit")
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--roles", default="admin")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--operator-acknowledgement",
        default="",
        help=f"Required with --execute: {OPERATOR_ACKNOWLEDGEMENT}",
    )
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()

    payload = run_capacity_bounded_load_harness(
        base_url=args.base_url,
        gate=args.gate,
        request_count=args.requests,
        concurrency=args.concurrency,
        execute=args.execute,
        operator_acknowledgement=args.operator_acknowledgement or None,
        user_id=args.user_id,
        tenant_id=args.tenant_id,
        roles=args.roles,
        timeout_seconds=args.timeout_seconds,
    )
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_capacity_bounded_load_harness_markdown(payload))
    if args.execute and payload["status"].startswith("blocked_"):
        return 2
    if args.execute and payload["status"] != "probe_completed_not_gate_evidence":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
