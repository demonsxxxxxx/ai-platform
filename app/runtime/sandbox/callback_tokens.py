import hashlib
import hmac


def derive_callback_token(secret: str, token_id: str) -> str:
    return hmac.new(secret.encode("utf-8"), token_id.encode("utf-8"), hashlib.sha256).hexdigest()


def callback_token_matches(*, secret: str, token_id: str, provided_token: str | None) -> bool:
    if not provided_token:
        return False
    expected = derive_callback_token(secret, token_id)
    return hmac.compare_digest(provided_token, expected)


def callback_token_id_belongs_to_run(token_id: str, run_id: str) -> bool:
    expected = f"cbt_{run_id}"
    return token_id == expected or token_id.startswith(f"{expected}_")
