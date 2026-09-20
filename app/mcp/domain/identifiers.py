import re


MCP_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_PRINCIPAL_USER_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$"
)


def is_safe_mcp_id(value: object) -> bool:
    return isinstance(value, str) and MCP_SAFE_ID_PATTERN.fullmatch(value) is not None


def assert_safe_mcp_id(value: str, field_name: str) -> str:
    if not is_safe_mcp_id(value):
        raise ValueError(f"{field_name} contains unsupported characters")
    return value


def assert_safe_mcp_principal_user_id(
    value: str,
    field_name: str = "user_id",
) -> str:
    if not _SAFE_PRINCIPAL_USER_ID_PATTERN.fullmatch(value) or ".." in value:
        raise ValueError(f"{field_name} contains unsupported characters")
    return value
