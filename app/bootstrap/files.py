from app.files.api import (
    configure_file_upload_persistence,
    configure_xlsx_preview_image_extractor,
)
from app.files.infrastructure import postgres, xlsx_preview_images


def configure_file_upload_services() -> None:
    configure_file_upload_persistence(postgres)


def configure_file_preview_services() -> None:
    configure_xlsx_preview_image_extractor(
        xlsx_preview_images.extract_xlsx_preview_images
    )
