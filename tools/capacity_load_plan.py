import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.capacity_baseline import (
    LOAD_TEST_GATES,
    build_capacity_load_test_plan,
    render_capacity_load_test_plan_markdown,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Print the ai-platform #21 capacity load-test plan.")
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="Output format. Defaults to markdown.",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8020",
        help="Target API base URL used in the generated command manifest.",
    )
    parser.add_argument("--scenario", choices=tuple(LOAD_TEST_GATES))
    parser.add_argument("--tenants", type=int, default=3)
    parser.add_argument("--users-per-tenant", type=int, default=5)
    parser.add_argument("--runs-per-user", type=int, default=2)
    parser.add_argument("--duration-seconds", type=int, default=300)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Document that this invocation is plan-only. Real load is never executed by this tool.",
    )
    args = parser.parse_args()

    plan = build_capacity_load_test_plan(
        base_url=args.base_url,
        tenants=args.tenants,
        users_per_tenant=args.users_per_tenant,
        runs_per_user=args.runs_per_user,
        duration_seconds=args.duration_seconds,
        scenario=args.scenario,
    )
    if args.format == "json":
        print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(render_capacity_load_test_plan_markdown(plan))


if __name__ == "__main__":
    main()
