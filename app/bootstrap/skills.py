from app.skills.api import configure_skill_display_version_persistence
from app.skills.infrastructure import postgres


def configure_skill_services() -> None:
    configure_skill_display_version_persistence(postgres)
