#!/usr/bin/env python3
"""Concurrent multi-user POC verification for ai-platform LambChat compatibility."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request


DEFAULT_API_URL = "http://127.0.0.1:8020"
DEFAULT_SAMPLE_DOCX = "/tmp/ai-platform-multiuser-poc-sample.docx"
DOWNLOAD_RE = re.compile(r"/api/ai/artifacts/(?P<artifact_id>art_[A-Za-z0-9_]+)/download")


@dataclass(frozen=True)
class Account:
    label: str
    username: str
    password: str


def ensure_default_sample_docx(docx_path: Path) -> Path:
    if docx_path.exists():
        return docx_path
    if str(docx_path) != DEFAULT_SAMPLE_DOCX:
        raise FileNotFoundError(f"sample docx not found: {docx_path}")
    docx_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from docx import Document

        document = Document()
        document.add_heading("AI Platform POC Sample", level=1)
        document.add_paragraph("This document contains text for concurrent review and translation validation.")
        document.add_paragraph("请将这段中文内容翻译为英文，并保留原始含义。")
        table = document.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "Field"
        table.cell(0, 1).text = "Value"
        table.cell(1, 0).text = "Purpose"
        table.cell(1, 1).text = "Multi-user POC validation"
        document.save(docx_path)
    except Exception:
        write_minimal_docx(docx_path)
    return docx_path


def write_minimal_docx(docx_path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>AI Platform POC Sample</w:t></w:r></w:p>
    <w:p><w:r><w:t>This document contains text for concurrent review and translation validation.</w:t></w:r></w:p>
    <w:p><w:r><w:t>请将这段中文内容翻译为英文，并保留原始含义。</w:t></w:r></w:p>
    <w:sectPr/>
  </w:body>
</w:document>
"""
    with zipfile.ZipFile(docx_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
""",
        )
        archive.writestr("word/document.xml", document_xml)


def json_request(method: str, url: str, payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: float = 30.0) -> tuple[int, Any]:
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = request.Request(
        url,
        data=data,
        headers={"Accept": "application/json", "Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            status, body = response.status, response.read()
    except error.HTTPError as exc:
        status, body = exc.code, exc.read()
    try:
        return status, json.loads(body.decode("utf-8"))
    except Exception:
        return status, body.decode("utf-8", errors="replace")


def multipart_file_post(url: str, *, filename: str, content: bytes, content_type: str, headers: dict[str, str], timeout: float = 60.0) -> tuple[int, Any]:
    boundary = "----ai-platform-multiuser-poc"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8"),
            f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
            content,
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    req = request.Request(
        url,
        data=body,
        headers={"Accept": "application/json", "Content-Type": f"multipart/form-data; boundary={boundary}", **headers},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    try:
        return status, json.loads(raw.decode("utf-8"))
    except Exception:
        return status, raw.decode("utf-8", errors="replace")


def get_bytes(url: str, headers: dict[str, str], timeout: float = 60.0) -> tuple[int, bytes]:
    req = request.Request(url, headers={"Accept": "*/*", **headers}, method="GET")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read()
    except error.HTTPError as exc:
        return exc.code, exc.read()


def login(api_url: str, account: Account) -> dict[str, str]:
    status, payload = json_request(
        "POST",
        f"{api_url.rstrip('/')}/api/auth/login",
        {"username": account.username, "password": account.password},
    )
    if status != 200 or not isinstance(payload, dict) or not payload.get("access_token"):
        raise RuntimeError(f"login failed for {account.label}: status={status} payload={payload}")
    token = str(payload["access_token"])
    return {"Authorization": f"Bearer {token}"}


def upload_docx(api_url: str, headers: dict[str, str], docx_path: Path) -> dict[str, Any]:
    status, payload = multipart_file_post(
        f"{api_url.rstrip('/')}/api/upload/file?folder=uploads",
        filename=docx_path.name,
        content=docx_path.read_bytes(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers=headers,
    )
    if status != 200 or not isinstance(payload, dict) or not str(payload.get("key") or "").startswith("file_"):
        raise RuntimeError(f"upload failed: status={status} payload={payload}")
    return payload


def submit_chat(api_url: str, headers: dict[str, str], *, agent_id: str, message: str, attachment: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"message": message, "workspace_id": "default"}
    if attachment:
        body["attachments"] = [attachment]
    status, payload = json_request("POST", f"{api_url.rstrip('/')}/api/chat/stream?agent_id={agent_id}", body, headers=headers)
    if status != 200 or not isinstance(payload, dict) or not payload.get("run_id"):
        raise RuntimeError(f"submit failed: status={status} payload={payload}")
    if not isinstance(payload.get("queue_position"), int) or int(payload["queue_position"]) < 1:
        raise RuntimeError(f"missing queue_position: payload={payload}")
    return payload


def wait_status(api_url: str, headers: dict[str, str], session_id: str, run_id: str, timeout_seconds: float = 240.0) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    latest: dict[str, Any] = {}
    while time.time() < deadline:
        status, payload = json_request(
            "GET",
            f"{api_url.rstrip('/')}/api/chat/sessions/{session_id}/status?run_id={run_id}",
            headers=headers,
            timeout=20,
        )
        latest = payload if isinstance(payload, dict) else {"payload": payload}
        if status == 200 and latest.get("status") in {"completed", "error"}:
            return latest
        time.sleep(2)
    raise TimeoutError(f"run did not finish: session={session_id} run={run_id} latest={latest}")


def stream_answer(api_url: str, headers: dict[str, str], session_id: str, run_id: str) -> str:
    req = request.Request(
        f"{api_url.rstrip('/')}/api/chat/sessions/{session_id}/stream?run_id={run_id}",
        headers={"Accept": "text/event-stream", **headers},
        method="GET",
    )
    with request.urlopen(req, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


def run_case(api_url: str, account: Account, case_name: str, agent_id: str, message: str, docx_path: Path | None) -> dict[str, Any]:
    headers = login(api_url, account)
    attachment = None
    if docx_path is not None:
        upload = upload_docx(api_url, headers, docx_path)
        attachment = {
            "key": upload["key"],
            "name": upload["name"],
            "type": "uploads",
            "mimeType": upload["mimeType"],
            "size": upload["size"],
        }
    submitted = submit_chat(api_url, headers, agent_id=agent_id, message=message, attachment=attachment)
    final_status = wait_status(api_url, headers, submitted["session_id"], submitted["run_id"])
    answer = stream_answer(api_url, headers, submitted["session_id"], submitted["run_id"])
    artifact_ids = sorted(set(match.group("artifact_id") for match in DOWNLOAD_RE.finditer(answer)))
    downloads = []
    for artifact_id in artifact_ids:
        owner_status, owner_body = get_bytes(f"{api_url.rstrip('/')}/api/ai/artifacts/{artifact_id}/download", headers)
        downloads.append({"artifact_id": artifact_id, "owner_status": owner_status, "owner_bytes": len(owner_body)})
    return {
        "account": account.label,
        "case": case_name,
        "agent_id": agent_id,
        "session_id": submitted["session_id"],
        "run_id": submitted["run_id"],
        "queue_position": submitted["queue_position"],
        "status": final_status.get("status"),
        "raw_status": final_status.get("raw_status"),
        "artifact_ids": artifact_ids,
        "downloads": downloads,
        "has_tmp_path": "/tmp/ai-platform-agent-workspaces/" in answer,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify concurrent multi-user ai-platform POC flows.")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--sample-docx", default=DEFAULT_SAMPLE_DOCX)
    parser.add_argument("--account", action="append", required=True, help="label=username:password")
    args = parser.parse_args()

    try:
        docx_path = ensure_default_sample_docx(Path(args.sample_docx))
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc
    accounts = []
    for item in args.account:
        label, rest = item.split("=", 1)
        username, password = rest.split(":", 1)
        accounts.append(Account(label=label, username=username, password=password))
    if len(accounts) < 2:
        raise SystemExit("provide at least two accounts")

    case_specs = []
    for account in accounts[:2]:
        case_specs.extend(
            [
                (account, "general-chat", "general-agent", f"{account.label} 并发通用聊天验收，请简短回复。", None),
                (account, "word-review", "general-agent", "审核一下这个文档", docx_path),
                (account, "word-translate", "baoyu-translate", "翻译一下这个文档", docx_path),
            ]
        )
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(case_specs)) as pool:
        futures = [pool.submit(run_case, args.api_url, *spec) for spec in case_specs]
        results = [future.result() for future in concurrent.futures.as_completed(futures)]

    failures = []
    for item in results:
        if item["status"] != "completed":
            failures.append({"case": item["case"], "account": item["account"], "reason": "not_completed", "status": item["status"]})
        if item["has_tmp_path"]:
            failures.append({"case": item["case"], "account": item["account"], "reason": "tmp_path_leaked"})
        if item["case"] in {"word-review", "word-translate"} and not item["artifact_ids"]:
            failures.append({"case": item["case"], "account": item["account"], "reason": "missing_artifact_link"})
        for download in item["downloads"]:
            if download["owner_status"] != 200 or download["owner_bytes"] <= 0:
                failures.append({"case": item["case"], "account": item["account"], "reason": "artifact_download_failed", **download})

    output = {"ok": not failures, "results": sorted(results, key=lambda row: (row["account"], row["case"])), "failures": failures}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
