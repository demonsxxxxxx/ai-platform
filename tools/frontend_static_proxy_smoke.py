#!/usr/bin/env python3
"""Smoke a deployed static AI Platform frontend proxy entry.

This verifier targets the non-Docker static-proxy frontend used for 211 preview
and official-entry switch checks. It does not perform real company login; use
``tools/verify_company_login_gate.py`` for credential-backed auth evidence.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from html.parser import HTMLParser
from typing import Any
from urllib import error, parse, request


SCHEMA_VERSION = "ai-platform.frontend-static-proxy-smoke.v1"
DEFAULT_ROUTES = ["/auth/login", "/chat", "/settings", "/mcp", "/notifications"]


class _AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.assets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value for key, value in attrs if value}
        if tag.lower() == "script" and values.get("src"):
            self.assets.append(values["src"])
        if tag.lower() == "link" and values.get("rel", "").lower() == "stylesheet" and values.get("href"):
            self.assets.append(values["href"])


def _url(base_url: str, path: str) -> str:
    return parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _fetch_json_or_text(url: str, *, timeout: int) -> dict[str, Any]:
    try:
        with request.urlopen(url, timeout=timeout) as response:
            body = response.read()
            status_code = response.status
            headers = dict(response.headers.items())
    except error.HTTPError as exc:
        body = exc.read()
        status_code = exc.code
        headers = dict(exc.headers.items())
    except OSError as exc:
        return {"ok": False, "status_code": None, "error": str(exc)}

    content_type = headers.get("Content-Type", "")
    text = body.decode("utf-8", "replace")
    parsed_body: Any = None
    if "json" in content_type.lower() or text.strip().startswith(("{", "[")):
        try:
            parsed_body = json.loads(text)
        except json.JSONDecodeError:
            parsed_body = None
    return {
        "ok": 200 <= int(status_code) < 400,
        "status_code": status_code,
        "content_type": content_type,
        "body": parsed_body if parsed_body is not None else text[:500],
        "body_text": text if parsed_body is None else "",
        "headers": {
            key: value
            for key, value in headers.items()
            if key.lower() in {"content-length", "last-modified", "content-type"}
        },
    }


def _extract_assets(index_body: object) -> list[str]:
    if not isinstance(index_body, str):
        return []
    parser = _AssetParser()
    parser.feed(index_body)
    return list(dict.fromkeys(asset for asset in parser.assets if asset.startswith("/")))


def _provenance_check(value: dict[str, Any], expected_commit: str | None) -> dict[str, Any]:
    body = value.get("body")
    ok = value.get("status_code") == 200 and isinstance(body, dict)
    if ok:
        ok = body.get("schema_version") == "ai-platform.frontend-build-provenance.v1"
    if ok and expected_commit:
        git = body.get("git") if isinstance(body, dict) else {}
        ok = isinstance(git, dict) and git.get("commit") == expected_commit and git.get("dirty") is False
    return {**value, "ok": bool(ok)}


def _health_check(value: dict[str, Any]) -> dict[str, Any]:
    body = value.get("body")
    ok = value.get("status_code") == 200 and isinstance(body, dict) and body.get("status") == "ok"
    return {**value, "ok": bool(ok)}


def _auth_me_check(value: dict[str, Any]) -> dict[str, Any]:
    body = value.get("body")
    detail = body.get("detail") if isinstance(body, dict) else None
    ok = value.get("status_code") == 401 and detail == "missing_authenticated_principal"
    return {**value, "ok": bool(ok)}


def _compact_check(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _compact_check(child) for key, child in value.items() if key != "body_text"}
    if isinstance(value, list):
        return [_compact_check(item) for item in value]
    return value


def run_static_proxy_smoke(
    base_url: str,
    *,
    routes: list[str] | None = None,
    expected_commit: str | None = None,
    timeout: int = 10,
) -> dict[str, Any]:
    """Run fail-closed static-proxy checks for a frontend base URL."""
    if expected_commit and not re.fullmatch(r"[0-9a-f]{40}", expected_commit):
        raise ValueError("expected_commit_must_be_40_hex_chars")

    routes = routes or list(DEFAULT_ROUTES)
    checks: dict[str, Any] = {}
    checks["index"] = _fetch_json_or_text(_url(base_url, "/"), timeout=timeout)

    assets = _extract_assets(checks["index"].get("body_text") or checks["index"].get("body"))
    asset_results = [
        {"path": asset, **_fetch_json_or_text(_url(base_url, asset), timeout=timeout)}
        for asset in assets
    ]
    checks["static_assets"] = {
        "ok": bool(asset_results) and all(item.get("status_code") == 200 for item in asset_results),
        "assets": asset_results,
    }

    route_results = {
        route: _fetch_json_or_text(_url(base_url, route), timeout=timeout)
        for route in routes
    }
    checks["spa_routes"] = {
        "ok": all(item.get("status_code") == 200 for item in route_results.values()),
        "routes": route_results,
    }

    checks["build_provenance"] = _provenance_check(
        _fetch_json_or_text(_url(base_url, "/ai-platform-build-provenance.json"), timeout=timeout),
        expected_commit,
    )
    checks["api_health"] = _health_check(_fetch_json_or_text(_url(base_url, "/api/ai/health"), timeout=timeout))
    checks["unauthenticated_auth_me"] = _auth_me_check(
        _fetch_json_or_text(_url(base_url, "/api/ai/auth/me"), timeout=timeout)
    )

    failed_checks = [name for name, value in checks.items() if not value.get("ok")]
    return {
        "schema_version": SCHEMA_VERSION,
        "base_url": base_url.rstrip("/"),
        "expected_commit": expected_commit,
        "status": "pass" if not failed_checks else "fail",
        "failed_checks": failed_checks,
        "checks": _compact_check(checks),
        "does_not_verify_real_company_login": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Frontend base URL, for example http://127.0.0.1:18001")
    parser.add_argument("--expected-commit", help="Expected frontend build commit from ai-platform-build-provenance.json")
    parser.add_argument("--route", action="append", dest="routes", help="SPA fallback route to check. May be repeated.")
    parser.add_argument("--timeout", type=int, default=10)
    args = parser.parse_args()

    try:
        result = run_static_proxy_smoke(
            args.base_url,
            routes=args.routes,
            expected_commit=args.expected_commit,
            timeout=args.timeout,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
