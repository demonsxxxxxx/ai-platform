import re
from collections.abc import Mapping

from app.mcp.domain.errors import McpRuntimeContextError


MCP_JWT_AUTHORIZATION_HEADER = "JWT-Authorization"
_HTTP_HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


def normalize_static_mcp_headers(
    headers: Mapping[str, str] | None,
) -> dict[str, str]:
    """Normalize static headers and reserve the per-request JWT header."""

    normalized: dict[str, str] = {}
    names_by_folded: set[str] = set()
    for raw_name, raw_value in (headers or {}).items():
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            raise McpRuntimeContextError("mcp_header_invalid", status_code=400)
        name = raw_name.strip()
        if not _HTTP_HEADER_NAME_PATTERN.fullmatch(name):
            raise McpRuntimeContextError("mcp_header_invalid", status_code=400)
        if "\r" in raw_value or "\n" in raw_value or "\x00" in raw_value:
            raise McpRuntimeContextError("mcp_header_invalid", status_code=400)
        folded = name.casefold()
        if folded == MCP_JWT_AUTHORIZATION_HEADER.casefold():
            raise McpRuntimeContextError("mcp_header_conflict", status_code=400)
        if folded in names_by_folded:
            raise McpRuntimeContextError("mcp_header_duplicate", status_code=400)
        names_by_folded.add(folded)
        normalized[name] = raw_value
    return normalized
