"""Version-track decisions for one tenant's Skill release policy."""

from collections.abc import Mapping


def previous_skill_version_for_promotion(
    policy: Mapping[str, object] | None, *, target_version: str,
    fallback_version: str | None = None,
) -> str | None:
    """Keep the predecessor when changing or retrying the current rollout."""
    if policy is None:
        candidate = fallback_version
    else:
        current = str(policy.get("current_version") or "")
        previous = str(policy.get("previous_version") or "")
        candidate = previous if current == target_version else current
    return candidate if candidate and candidate != target_version else None
