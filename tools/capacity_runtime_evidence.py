import argparse
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.capacity_baseline import (
    build_capacity_evidence_snapshot,
    build_capacity_gate_readiness,
)


OVERVIEW_ROUTE = "/api/ai/admin/runtime/overview"
SCHEMA_VERSION = "ai-platform.capacity-runtime-evidence.v1"


def _safe_base_url(value: str) -> str:
    raw = str(value or "http://127.0.0.1:8020").strip()
    parsed = urlsplit(raw)
    if not parsed.scheme or not parsed.hostname:
        return "http://127.0.0.1:8020"
    netloc = parsed.hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))


def _overview_route(*, include_maintenance_cleanup: bool = True) -> str:
    if include_maintenance_cleanup:
        return OVERVIEW_ROUTE
    return f"{OVERVIEW_ROUTE}?{urlencode({'include_maintenance_cleanup': 'false'})}"


def _overview_url(base_url: str, *, include_maintenance_cleanup: bool = True) -> str:
    return f"{base_url.rstrip('/')}{_overview_route(include_maintenance_cleanup=include_maintenance_cleanup)}"


def _read_overview(
    *,
    base_url: str,
    user_id: str,
    tenant_id: str,
    roles: str,
    gateway_secret_env: str,
    timeout_seconds: float,
    include_maintenance_cleanup: bool,
) -> tuple[dict[str, object], int]:
    headers = {
        "X-AI-User-ID": user_id,
        "X-AI-User-Name": user_id,
        "X-AI-Tenant-ID": tenant_id,
        "X-AI-Roles": roles,
    }
    if gateway_secret_env:
        gateway_secret = os.environ.get(gateway_secret_env, "")
        if gateway_secret:
            headers["X-AI-Gateway-Secret"] = gateway_secret
    request = Request(
        _overview_url(base_url, include_maintenance_cleanup=include_maintenance_cleanup),
        headers=headers,
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = int(response.status)
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise SystemExit(f"admin runtime overview request failed: HTTP {exc.code}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("admin runtime overview JSON must be an object")
    return payload, status


def build_capacity_runtime_evidence(
    *,
    base_url: str,
    user_id: str,
    tenant_id: str,
    roles: str,
    gateway_secret_env: str = "",
    commit_sha: str = "unknown",
    runtime_profile: str = "unproven_default",
    timeout_seconds: float = 10.0,
    include_maintenance_cleanup: bool = True,
) -> dict[str, object]:
    safe_base_url = _safe_base_url(base_url)
    overview, http_status = _read_overview(
        base_url=safe_base_url,
        user_id=user_id,
        tenant_id=tenant_id,
        roles=roles,
        gateway_secret_env=gateway_secret_env,
        timeout_seconds=timeout_seconds,
        include_maintenance_cleanup=include_maintenance_cleanup,
    )
    snapshot = build_capacity_evidence_snapshot(
        overview,
        commit_sha=commit_sha,
        runtime_profile=runtime_profile,
    )
    readiness = build_capacity_gate_readiness(snapshot)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "base_url": safe_base_url,
            "overview_route": _overview_route(
                include_maintenance_cleanup=include_maintenance_cleanup,
            ),
            "http_status": http_status,
            "mode": "admin_runtime_overview_capture",
        },
        "snapshot": snapshot,
        "readiness": readiness,
    }


def render_capacity_runtime_evidence_markdown(evidence: dict[str, object]) -> str:
    source = evidence["source"]
    readiness = evidence["readiness"]
    missing_sections = "\n".join(
        f"- {section}" for section in readiness["admin_runtime_evidence"]["missing_sections"]
    ) or "- none"
    missing_gates = "\n".join(f"- {gate}" for gate in readiness["missing_load_test_gates"]) or "- none"
    return (
        "# ai-platform Capacity Runtime Evidence\n\n"
        f"Schema: `{evidence['schema_version']}`\n\n"
        f"Source: `{source['base_url']}{source['overview_route']}`\n\n"
        f"HTTP status: `{source['http_status']}`\n\n"
        f"Gate status: `{readiness['status']}`\n\n"
        "## Missing Admin Runtime Sections\n\n"
        f"{missing_sections}\n\n"
        "## Missing Load-Test Gates\n\n"
        f"{missing_gates}\n\n"
        "## Production Default Decision\n\n"
        f"{readiness['production_default_decision']}\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture a secret-safe #21 capacity snapshot and gate verdict from Admin Runtime overview.",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8020")
    parser.add_argument("--user-id", default="codex-capacity-audit")
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--roles", default="admin")
    parser.add_argument(
        "--gateway-secret-env",
        default="",
        help="Optional environment variable name containing X-AI-Gateway-Secret. The value is never printed.",
    )
    parser.add_argument("--commit-sha", default="unknown")
    parser.add_argument("--runtime-profile", default="unproven_default")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument(
        "--skip-maintenance-cleanup",
        action="store_true",
        help=(
            "Capture /admin/runtime/overview with include_maintenance_cleanup=false. "
            "Use for read-only capacity snapshots in the default stack where the API "
            "must not require a mounted Docker socket."
        ),
    )
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args()

    evidence = build_capacity_runtime_evidence(
        base_url=args.base_url,
        user_id=args.user_id,
        tenant_id=args.tenant_id,
        roles=args.roles,
        gateway_secret_env=args.gateway_secret_env,
        commit_sha=args.commit_sha,
        runtime_profile=args.runtime_profile,
        timeout_seconds=args.timeout_seconds,
        include_maintenance_cleanup=not args.skip_maintenance_cleanup,
    )
    if args.format == "json":
        print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(render_capacity_runtime_evidence_markdown(evidence))


if __name__ == "__main__":
    main()
