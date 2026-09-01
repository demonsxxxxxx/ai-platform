"""Agent Profile composition helpers."""

from collections.abc import Callable

from app.agent_apps.infrastructure import postgres as agent_profile_persistence


def configure_agent_profile_routes(configure_favorites: Callable[..., None]) -> None:
    configure_favorites(
        favorite_ids_loader=agent_profile_persistence.list_agent_profile_favorite_ids,
        favorite_setter=agent_profile_persistence.set_agent_profile_favorite,
    )
