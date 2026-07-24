import hashlib
import hmac
from dataclasses import dataclass

from app.validation import assert_safe_id


@dataclass(frozen=True)
class CallbackTokenBinding:
    """Exact run-attempt identity authenticated by one callback token."""

    run_id: str
    attempt_id: str

    def __post_init__(self) -> None:
        assert_safe_id(self.run_id, "run_id")
        assert_safe_id(self.attempt_id, "attempt_id")


def callback_token_id_for_binding(binding: CallbackTokenBinding) -> str:
    """Build the canonical token id; both subjects are compared as one value."""

    return assert_safe_id(f"cbt:{binding.run_id}:{binding.attempt_id}", "callback_token_id")


def derive_callback_token(secret: str, token_id: str) -> str:
    return hmac.new(secret.encode("utf-8"), token_id.encode("utf-8"), hashlib.sha256).hexdigest()


def callback_token_matches(*, secret: str, token_id: str, provided_token: str | None) -> bool:
    if not provided_token:
        return False
    expected = derive_callback_token(secret, token_id)
    return hmac.compare_digest(provided_token, expected)


def callback_token_id_matches_binding(token_id: str, binding: CallbackTokenBinding) -> bool:
    """Compare a token id to the complete expected run-attempt binding."""

    try:
        expected = callback_token_id_for_binding(binding)
    except ValueError:
        return False
    return hmac.compare_digest(token_id, expected)
