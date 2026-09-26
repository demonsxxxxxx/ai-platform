import hashlib
import hmac
from dataclasses import dataclass

from app.validation import assert_safe_id


def executor_callback_url(settings: object) -> str:
    return f"{str(settings.sandbox_callback_base_url).rstrip('/')}/api/ai/runtime/callbacks/executor"


@dataclass(frozen=True)
class CallbackTokenBinding:
    """Exact run-attempt ownership authenticated by one callback token."""

    run_id: str
    attempt_id: str
    owner_generation: int | None = None

    def __post_init__(self) -> None:
        assert_safe_id(self.run_id, "run_id")
        assert_safe_id(self.attempt_id, "attempt_id")
        if self.owner_generation is not None and (
            type(self.owner_generation) is not int or self.owner_generation < 1
        ):
            raise ValueError("callback_owner_generation_invalid")


def callback_token_id_for_binding(binding: CallbackTokenBinding) -> str:
    """Build the canonical token id; both subjects are compared as one value."""

    token_id = f"cbt:{binding.run_id}:{binding.attempt_id}"
    if binding.owner_generation is not None:
        token_id += f":g{binding.owner_generation}"
    return assert_safe_id(token_id, "callback_token_id")


def callback_token_id_matches_attempt(
    token_id: str, *, run_id: str, attempt_id: str
) -> bool:
    """Validate the encoded identity before the authoritative lease is read."""

    try:
        legacy_id = callback_token_id_for_binding(
            CallbackTokenBinding(run_id=run_id, attempt_id=attempt_id)
        )
        if hmac.compare_digest(token_id, legacy_id):
            return True
        prefix = f"{legacy_id}:g"
        if not token_id.startswith(prefix):
            return False
        generation = token_id[len(prefix):]
        if not generation.isascii() or not generation.isdecimal():
            return False
        binding = CallbackTokenBinding(
            run_id=run_id, attempt_id=attempt_id,
            owner_generation=int(generation),
        )
        return hmac.compare_digest(token_id, callback_token_id_for_binding(binding))
    except ValueError:
        return False


def derive_callback_token(secret: str, token_id: str) -> str:
    return hmac.new(secret.encode("utf-8"), token_id.encode("utf-8"), hashlib.sha256).hexdigest()


def callback_token_matches(*, secret: str, token_id: str, provided_token: str | None) -> bool:
    if not provided_token:
        return False
    expected = derive_callback_token(secret, token_id)
    return hmac.compare_digest(provided_token.encode("utf-8"), expected.encode("ascii"))


def callback_token_id_matches_binding(token_id: str, binding: CallbackTokenBinding) -> bool:
    """Compare a token id to the complete expected run-attempt binding."""

    try:
        expected = callback_token_id_for_binding(binding)
    except ValueError:
        return False
    return hmac.compare_digest(token_id, expected)
