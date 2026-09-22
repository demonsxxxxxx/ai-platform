from __future__ import annotations

import json

import httpx
import pytest

from app.files.application.profile_drive import parse_profile_drive_file_import_request
from app.files.infrastructure.profile_drive import open_profile_drive_file


def test_profile_drive_import_source_is_closed_and_defaults_to_profile() -> None:
    assert parse_profile_drive_file_import_request({"path": "Documents/report.pdf"}).source_id == "profile"
    public = parse_profile_drive_file_import_request(
        {"source_id": "public", "path": "01-研发部/report.pdf"}
    )
    assert (public.source_id, public.path) == ("public", "01-研发部/report.pdf")

    with pytest.raises(ValueError, match="profile_drive_source_invalid"):
        parse_profile_drive_file_import_request(
            {"source_id": "//other-server/share", "path": "report.pdf"}
        )
    with pytest.raises(ValueError, match="profile_drive_source_invalid"):
        parse_profile_drive_file_import_request(
            {"source_id": "public", "path": "report.pdf", "server": "other-server"}
        )


@pytest.mark.asyncio
async def test_public_drive_transfer_selects_only_the_public_source(monkeypatch) -> None:
    captured: dict[str, object] = {}
    real_async_client = httpx.AsyncClient

    def upstream(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"Content-Length": "1", "Content-Type": "application/octet-stream"},
            stream=httpx.ByteStream(b"x"),
        )

    monkeypatch.setattr(
        "app.files.infrastructure.profile_drive.httpx.AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(upstream),
            **kwargs,
        ),
    )

    client, response, content_length, _ = await open_profile_drive_file(
        upstream="https://profile-drive.test",
        ca_cert_file="",
        jwt="company.jwt",
        path="01-研发部/report.pdf",
        source_id="public",
        max_bytes=16,
        require_nonempty=True,
        not_found_status=404,
        too_large_detail="file_too_large",
    )
    try:
        assert content_length == 1
        assert captured == {"source": "public", "path": "01-研发部/report.pdf"}
    finally:
        await response.aclose()
        await client.aclose()
