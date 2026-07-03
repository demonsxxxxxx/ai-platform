import re


SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
SAFE_PRINCIPAL_USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")


def assert_safe_id(value: str, field_name: str) -> str:
    if not SAFE_ID_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} contains unsupported characters")
    return value


def assert_safe_principal_user_id(value: str, field_name: str = "user_id") -> str:
    if not SAFE_PRINCIPAL_USER_ID_PATTERN.fullmatch(value) or ".." in value:
        raise ValueError(f"{field_name} contains unsupported characters")
    return value
