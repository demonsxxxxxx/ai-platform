from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class XlsxPreviewImageExtractor(Protocol):
    def __call__(
        self,
        staged_path: Path,
        *,
        max_sheets: int,
        max_rows_per_sheet: int,
        max_columns_per_sheet: int,
        max_images: int,
        max_image_bytes: int,
        max_total_image_bytes: int,
        max_image_emu: int,
    ) -> tuple[dict[int, list[dict[str, Any]]], list[str]]: ...


_image_extractor: XlsxPreviewImageExtractor | None = None


def configure_xlsx_preview_image_extractor(
    extractor: XlsxPreviewImageExtractor,
) -> None:
    global _image_extractor
    _image_extractor = extractor


def xlsx_preview_image_extractor() -> XlsxPreviewImageExtractor:
    if _image_extractor is None:
        raise RuntimeError("XLSX preview image extractor is not configured")
    return _image_extractor
