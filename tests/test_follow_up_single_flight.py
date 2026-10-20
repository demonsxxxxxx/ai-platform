"""The claim must outlive the whole paid request, or concurrent calls can overlap."""

from __future__ import annotations

from app.conversations.application.follow_up_suggestions import (
    FOLLOW_UP_CLAIM_TTL_SECONDS,
    FOLLOW_UP_SHARED_WAIT_SECONDS,
)
from app.execution.application.follow_up_completion import (
    FOLLOW_UP_COMPLETION_CLOSE_GRACE_SECONDS,
    FOLLOW_UP_COMPLETION_TIMEOUT_SECONDS,
)


def test_claim_covers_the_transport_timeout_and_its_bounded_shutdown() -> None:
    # A claim that expires mid-request would let a second caller start another
    # billed completion while the first transport is still shutting down.
    request_lifetime = FOLLOW_UP_COMPLETION_TIMEOUT_SECONDS + FOLLOW_UP_COMPLETION_CLOSE_GRACE_SECONDS

    assert FOLLOW_UP_CLAIM_TTL_SECONDS > request_lifetime
    # A follower that has to wait still gives up before the owner's own deadline.
    assert FOLLOW_UP_SHARED_WAIT_SECONDS < FOLLOW_UP_CLAIM_TTL_SECONDS
