import re
from collections.abc import Mapping

from app.mcp.domain.errors import McpRuntimeContextError


MCP_JWT_AUTHORIZATION_HEADER = "JWT-Authorization"
_HTTP_HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


def mcp_user_jwt_header(integration_kind: str = "mcp") -> str:
    if integration_kind not in {"mcp", "enterprise_gateway"}:
        raise McpRuntimeContextError("mcp_integration_invalid", status_code=400)
    return "Authorization" if integration_kind == "enterprise_gateway" else MCP_JWT_AUTHORIZATION_HEADER


def normalize_static_mcp_headers(
    headers: Mapping[str, str] | None, *, integration_kind: str = "mcp",
) -> dict[str, str]:
    """Normalize static headers and reserve the per-request JWT header."""

    mcp_user_jwt_header(integration_kind)
    reserved = {MCP_JWT_AUTHORIZATION_HEADER.casefold()}
    if integration_kind == "enterprise_gateway":
        reserved.update({"authorization", "cookie", "x-mcp-username", "x-mcp-password"})
    normalized: dict[str, str] = {}
    names_by_folded: set[str] = set()
    for raw_name, raw_value in (headers or {}).items():
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            raise McpRuntimeContextError("mcp_header_invalid", status_code=400)
        name = raw_name.strip()
        if not _HTTP_HEADER_NAME_PATTERN.fullmatch(name):
            raise McpRuntimeContextError("mcp_header_invalid", status_code=400)
        value = raw_value.strip()
        if any(not 0x20 <= ord(character) <= 0x7E for character in value):
            raise McpRuntimeContextError("mcp_header_invalid", status_code=400)
        folded = name.casefold()
        if folded in reserved:
            raise McpRuntimeContextError("mcp_header_conflict", status_code=400)
        if folded in names_by_folded:
            raise McpRuntimeContextError("mcp_header_duplicate", status_code=400)
        names_by_folded.add(folded)
        normalized[name] = value
    return normalized
