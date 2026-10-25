"""Execution-owned bounds for server prompts and upstream model identifiers."""


MAX_SERVER_OWNED_SYSTEM_PROMPT_CHARS = 16_000
MAX_COMPOSED_EXECUTOR_SYSTEM_PROMPT_CHARS = 64_000


def assert_upstream_model_id(value: str, field_name: str = "model_value") -> str:
    if (
        not value
        or value != value.strip()
        or len(value.encode("utf-8")) > 512
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError(f"{field_name} contains unsupported characters")
    return value
