from app.files.api import configure_file_upload_persistence
from app.files.infrastructure import postgres


def configure_file_upload_services() -> None:
    configure_file_upload_persistence(postgres)
