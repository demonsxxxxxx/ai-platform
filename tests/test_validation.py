from app.validation import MAX_SERVER_OWNED_SYSTEM_PROMPT_CHARS


def test_server_owned_system_prompt_limit_is_explicit_and_shared():
    assert MAX_SERVER_OWNED_SYSTEM_PROMPT_CHARS == 16_000
