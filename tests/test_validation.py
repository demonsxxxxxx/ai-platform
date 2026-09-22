from app.validation import (
    MAX_COMPOSED_EXECUTOR_SYSTEM_PROMPT_CHARS,
    MAX_SERVER_OWNED_SYSTEM_PROMPT_CHARS,
)


def test_profile_and_composed_system_prompt_limits_are_distinct_and_explicit():
    assert MAX_SERVER_OWNED_SYSTEM_PROMPT_CHARS == 16_000
    assert MAX_COMPOSED_EXECUTOR_SYSTEM_PROMPT_CHARS == 64_000
