from app.files.api import (
    configure_file_upload_persistence,
    configure_profile_drive_streaming_response,
    configure_profile_drive_transfer,
    configure_xlsx_preview_image_extractor,
)
from app.files.infrastructure import postgres, profile_drive, xlsx_preview_images
from app.files.transport.profile_drive import profile_drive_streaming_response as build_profile_drive_streaming_response


def configure_file_upload_services() -> None:
    configure_file_upload_persistence(postgres)
    configure_profile_drive_transfer(profile_drive.ProfileDriveTransferAdapter())
    configure_profile_drive_streaming_response(build_profile_drive_streaming_response)


def configure_file_preview_services() -> None:
    configure_xlsx_preview_image_extractor(
        xlsx_preview_images.extract_xlsx_preview_images
    )
