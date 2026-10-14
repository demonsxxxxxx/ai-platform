"""Locate image payloads in the pinned Claude Code transcript without changing it."""

from collections.abc import Iterator
from typing import Any


_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})


def provider_session_image_fields(
    entry: dict[str, Any],
) -> Iterator[tuple[tuple[str | int, ...], str, str]]:
    """Yield (JSON path, MIME type, exact encoded value), never inferred text."""

    def blocks(value: object, path: tuple[str | int, ...]):
        if not isinstance(value, list):
            return
        for index, block in enumerate(value):
            if not isinstance(block, dict):
                continue
            location = (*path, index)
            if block.get("type") == "image":
                source = block.get("source")
                if (
                    isinstance(source, dict)
                    and source.get("type") == "base64"
                    and isinstance(source.get("media_type"), str)
                    and source["media_type"] in _IMAGE_TYPES
                    and isinstance(source.get("data"), str)
                    and source["data"]
                ):
                    yield (*location, "source", "data"), source["media_type"], source["data"]
                elif (
                    isinstance(block.get("mimeType"), str)
                    and block["mimeType"] in _IMAGE_TYPES
                    and isinstance(block.get("data"), str)
                    and block["data"]
                ):
                    yield (*location, "data"), block["mimeType"], block["data"]
            elif block.get("type") == "tool_result":
                yield from blocks(block.get("content"), (*location, "content"))

    message = entry.get("message")
    if isinstance(message, dict):
        yield from blocks(message.get("content"), ("message", "content"))
    result = entry.get("toolUseResult")
    if isinstance(result, dict):
        yield from blocks(result.get("content"), ("toolUseResult", "content"))
        file = result.get("file")
        if (
            result.get("type") == "image"
            and isinstance(file, dict)
            and isinstance(file.get("type"), str)
            and file["type"] in _IMAGE_TYPES
            and isinstance(file.get("base64"), str)
            and file["base64"]
        ):
            yield ("toolUseResult", "file", "base64"), file["type"], file["base64"]
