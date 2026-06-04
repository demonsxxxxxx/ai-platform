#!/usr/bin/env python3
"""Verify Word review/translation skill artifacts through ai-platform.

This script uses only Python standard-library modules so it can run on the 211
server without installing additional packages. It exercises the platform API:

1. Upload a DOCX as User A.
2. Create a file-skill run.
3. Poll until the run finishes.
4. Verify the run exposes ai-platform artifact metadata.
5. Download the artifact as User A.
6. Verify User B cannot download User A's artifact.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request


DEFAULT_BASE_URL = "http://127.0.0.1:8020"
FINAL_STATUSES = {"succeeded", "failed", "cancelled"}
DENIED_STATUSES = {401, 403, 404}

SKILLS = {
    "review": {
        "agent_id": "qa-word-review",
        "skill_id": "qa-file-reviewer",
        "title": "artifact-gate-review",
        "input": {"mode": "review", "task": "artifact-gate-review"},
    },
    "translate": {
        "agent_id": "baoyu-translate",
        "skill_id": "baoyu-translate",
        "title": "artifact-gate-translate",
        "input": {
            "mode": "file",
            "source_language": "auto",
            "target_language": "zh-CN",
            "task": "artifact-gate-translate",
        },
    },
}


@dataclass(frozen=True)
class Principal:
    user_id: str
    display_name: str
    tenant_id: str = "default"
    roles: tuple[str, ...] = ("user",)
    permissions: tuple[str, ...] = (
        "agent:use",
        "chat:read",
        "chat:write",
        "session:read",
        "session:write",
        "artifact:download",
        "file:upload",
        "file:upload:document",
    )


@dataclass(frozen=True)
class HttpResult:
    status_code: int
    headers: dict[str, str]
    body: bytes
    elapsed_ms: int

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))

    def detail(self) -> str:
        try:
            parsed = self.json()
        except Exception:
            return self.body.decode("utf-8", errors="replace")[:300]
        if isinstance(parsed, dict) and parsed.get("detail") is not None:
            return str(parsed["detail"])
        return json.dumps(parsed, ensure_ascii=False)[:300]


def principal_headers(principal: Principal) -> dict[str, str]:
    return {
        "X-AI-User-ID": principal.user_id,
        "X-AI-User-Name": principal.display_name,
        "X-AI-Tenant-ID": principal.tenant_id,
        "X-AI-Roles": ",".join(principal.roles),
        "X-AI-Permissions": ",".join(principal.permissions),
    }


def encode_multipart_form(path: Path, fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = f"----codex-ai-platform-{uuid.uuid4().hex}"
    content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    body = bytearray()

    def add_line(value: str) -> None:
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")

    for name, value in fields.items():
        add_line(f"--{boundary}")
        add_line(f'Content-Disposition: form-data; name="{name}"')
        add_line("")
        add_line(value)
    add_line(f"--{boundary}")
    add_line(f'Content-Disposition: form-data; name="file"; filename="{path.name}"')
    add_line(f"Content-Type: {content_type}")
    add_line("")
    body.extend(path.read_bytes())
    body.extend(b"\r\n")
    add_line(f"--{boundary}--")
    return bytes(body), f"multipart/form-data; boundary={boundary}"


class Client:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        principal: Principal,
        payload: dict[str, Any] | None = None,
        body: bytes | None = None,
        content_type: str = "",
    ) -> HttpResult:
        headers = {"Accept": "application/json", **principal_headers(principal)}
        data = body
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        req = request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        started = time.perf_counter()
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                return HttpResult(
                    response.status,
                    dict(response.headers.items()),
                    response.read(),
                    int((time.perf_counter() - started) * 1000),
                )
        except error.HTTPError as exc:
            return HttpResult(
                exc.code,
                dict(exc.headers.items()),
                exc.read(),
                int((time.perf_counter() - started) * 1000),
            )

    def upload_file(self, *, path: Path, principal: Principal, workspace_id: str) -> HttpResult:
        body, content_type = encode_multipart_form(path, {"workspace_id": workspace_id})
        return self.request(
            "POST",
            "/api/ai/files",
            principal=principal,
            body=body,
            content_type=content_type,
        )


def poll_run(
    client: Client,
    *,
    principal: Principal,
    run_id: str,
    timeout_seconds: float,
    interval_seconds: float,
) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    latest: dict[str, Any] = {}
    while True:
        response = client.request("GET", f"/api/ai/runs/{run_id}", principal=principal)
        if response.ok and isinstance(response.json(), dict):
            latest = response.json()
            if latest.get("status") in FINAL_STATUSES:
                return latest
        if time.time() >= deadline:
            return latest or {"status": "timeout", "run_id": run_id}
        time.sleep(interval_seconds)


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def run_skill_gate(
    client: Client,
    *,
    file_path: Path,
    skill_name: str,
    user_a: Principal,
    user_b: Principal,
    workspace_id: str,
    poll_timeout: float,
    poll_interval: float,
) -> dict[str, Any]:
    shape = SKILLS[skill_name]
    upload = client.upload_file(path=file_path, principal=user_a, workspace_id=workspace_id)
    ensure(upload.ok, f"{skill_name}: upload failed HTTP {upload.status_code}: {upload.detail()}")
    file_id = upload.json()["file_id"]

    create = client.request(
        "POST",
        "/api/ai/runs",
        principal=user_a,
        payload={
            "workspace_id": workspace_id,
            "agent_id": shape["agent_id"],
            "skill_id": shape["skill_id"],
            "title": shape["title"],
            "input": shape["input"],
            "file_ids": [file_id],
        },
    )
    ensure(create.ok, f"{skill_name}: create run failed HTTP {create.status_code}: {create.detail()}")
    run_id = create.json()["run_id"]
    run_state = poll_run(
        client,
        principal=user_a,
        run_id=run_id,
        timeout_seconds=poll_timeout,
        interval_seconds=poll_interval,
    )
    ensure(run_state.get("status") == "succeeded", f"{skill_name}: run ended as {run_state.get('status')}")
    artifacts = run_state.get("artifacts") or []
    ensure(artifacts, f"{skill_name}: succeeded run returned no artifacts")
    artifact = artifacts[0]
    artifact_id = artifact["id"]
    ensure(int(artifact.get("size_bytes") or 0) > 0, f"{skill_name}: artifact size is empty")
    ensure(str(artifact.get("storage_key") or "").startswith("tenants/"), f"{skill_name}: artifact is not in platform namespace")

    own_download = client.request("GET", f"/api/ai/artifacts/{artifact_id}/download", principal=user_a)
    ensure(own_download.ok, f"{skill_name}: own artifact download failed HTTP {own_download.status_code}")
    ensure(len(own_download.body) > 0, f"{skill_name}: downloaded artifact body is empty")

    cross_download = client.request("GET", f"/api/ai/artifacts/{artifact_id}/download", principal=user_b)
    ensure(
        cross_download.status_code in DENIED_STATUSES,
        f"{skill_name}: cross-user artifact download was not denied, got HTTP {cross_download.status_code}",
    )

    return {
        "skill": skill_name,
        "run_id": run_id,
        "session_id": run_state.get("session_id"),
        "agent_id": run_state.get("agent_id"),
        "skill_id": run_state.get("skill_id"),
        "status": run_state.get("status"),
        "artifact": {
            "id": artifact_id,
            "label": artifact.get("label"),
            "content_type": artifact.get("content_type"),
            "size_bytes": artifact.get("size_bytes"),
            "storage_namespace": str(artifact.get("storage_key") or "").split("/", 1)[0],
        },
        "own_download_bytes": len(own_download.body),
        "cross_user_download_status": cross_download.status_code,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify review/translate Word skills and ai-platform artifact ownership.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--file", help="Default DOCX input file used for all selected skill gates.")
    parser.add_argument("--review-file", help="DOCX input file for the qa-file-reviewer gate.")
    parser.add_argument("--translate-file", help="DOCX input file for the baoyu-translate gate.")
    parser.add_argument("--workspace-id", default="default")
    parser.add_argument("--skills", default="review,translate", help="Comma-separated skill gates: review,translate.")
    parser.add_argument("--poll-timeout", type=float, default=180.0)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--user-a-id", default="artifact-gate-user-a")
    parser.add_argument("--user-b-id", default="artifact-gate-user-b")
    args = parser.parse_args()

    default_file = Path(args.file) if args.file else None
    review_file = Path(args.review_file) if args.review_file else default_file
    translate_file = Path(args.translate_file) if args.translate_file else default_file
    client = Client(args.base_url, timeout=30.0)
    user_a = Principal(args.user_a_id, "Artifact Gate User A")
    user_b = Principal(args.user_b_id, "Artifact Gate User B")
    skill_names = [item.strip() for item in args.skills.split(",") if item.strip()]
    for name in skill_names:
        ensure(name in SKILLS, f"unsupported skill gate: {name}")
    files_by_skill = {"review": review_file, "translate": translate_file}
    for name in skill_names:
        selected_file = files_by_skill[name]
        ensure(selected_file is not None, f"{name}: set --file or --{name}-file")
        ensure(selected_file.exists(), f"{name}: input file not found: {selected_file}")

    results = [
        run_skill_gate(
            client,
            file_path=files_by_skill[name],
            skill_name=name,
            user_a=user_a,
            user_b=user_b,
            workspace_id=args.workspace_id,
            poll_timeout=args.poll_timeout,
            poll_interval=args.poll_interval,
        )
        for name in skill_names
    ]
    print(json.dumps({"ok": True, "base_url": args.base_url, "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
