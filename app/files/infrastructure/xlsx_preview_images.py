from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from zipfile import ZipFile


_IMAGE_MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
}


def extract_xlsx_preview_images(
    staged_path: Path,
    *,
    max_sheets: int,
    max_rows_per_sheet: int,
    max_columns_per_sheet: int,
    max_images: int,
    max_image_bytes: int,
    max_total_image_bytes: int,
    max_image_emu: int,
) -> tuple[dict[int, list[dict[str, Any]]], list[str]]:
    """Extract bounded, local worksheet images without resolving external content."""

    try:
        with ZipFile(staged_path) as archive:
            if not any(
                entry.filename.replace("\\", "/").casefold().startswith("xl/drawings/")
                for entry in archive.infolist()
            ):
                return {}, []
        from openpyxl import load_workbook

        workbook = load_workbook(
            staged_path,
            read_only=False,
            data_only=False,
            keep_links=False,
        )
    except Exception:
        return {}, ["images_not_rendered"]

    images_by_sheet: dict[int, list[dict[str, Any]]] = {}
    warnings: list[str] = []
    total_bytes = 0
    image_count = 0
    images_seen = 0
    try:
        for sheet_index, worksheet in enumerate(workbook.worksheets[:max_sheets]):
            for image in getattr(worksheet, "_images", []):
                images_seen += 1
                if images_seen > max_images:
                    warnings.append("images_truncated")
                    return images_by_sheet, list(dict.fromkeys(warnings))
                image_payload = _image_payload(
                    image=image,
                    sheet_index=sheet_index,
                    order=image_count,
                    remaining_bytes=max_total_image_bytes - total_bytes,
                    max_rows_per_sheet=max_rows_per_sheet,
                    max_columns_per_sheet=max_columns_per_sheet,
                    max_image_bytes=max_image_bytes,
                    max_image_emu=max_image_emu,
                )
                if image_payload is None:
                    warnings.extend(("images_not_rendered", "images_truncated"))
                    continue
                image_bytes = len(
                    base64.b64decode(image_payload["data_url"].split(",", 1)[1])
                )
                total_bytes += image_bytes
                image_count += 1
                images_by_sheet.setdefault(sheet_index, []).append(image_payload)
    except Exception:
        warnings.append("images_not_rendered")
    finally:
        try:
            workbook.close()
        except Exception:
            pass
    return images_by_sheet, list(dict.fromkeys(warnings))


def _image_payload(
    *,
    image: Any,
    sheet_index: int,
    order: int,
    remaining_bytes: int,
    max_rows_per_sheet: int,
    max_columns_per_sheet: int,
    max_image_bytes: int,
    max_image_emu: int,
) -> dict[str, Any] | None:
    anchor = getattr(image, "anchor", None)
    origin = _anchor_point(
        getattr(anchor, "_from", None),
        max_rows_per_sheet=max_rows_per_sheet,
        max_columns_per_sheet=max_columns_per_sheet,
        max_image_emu=max_image_emu,
    )
    if origin is None:
        return None
    endpoint = _anchor_point(
        getattr(anchor, "to", None),
        max_rows_per_sheet=max_rows_per_sheet,
        max_columns_per_sheet=max_columns_per_sheet,
        max_image_emu=max_image_emu,
    )
    extent = getattr(anchor, "ext", None)
    image_extent = None
    if endpoint is None:
        width = _bounded_emu(getattr(extent, "cx", None), max_image_emu)
        height = _bounded_emu(getattr(extent, "cy", None), max_image_emu)
        if width is None or height is None:
            return None
        image_extent = {"width_emu": width, "height_emu": height}
    elif (
        endpoint["row"] < origin["row"]
        or endpoint["col"] < origin["col"]
        or (
            endpoint["row"] == origin["row"]
            and endpoint["row_offset_emu"] <= origin["row_offset_emu"]
        )
        or (
            endpoint["col"] == origin["col"]
            and endpoint["col_offset_emu"] <= origin["col_offset_emu"]
        )
    ):
        return None

    try:
        image_bytes = image._data()
    except Exception:
        return None
    if (
        not isinstance(image_bytes, bytes)
        or not image_bytes
        or len(image_bytes) > max_image_bytes
        or len(image_bytes) > remaining_bytes
    ):
        return None
    mime_type = _image_mime_type(image, image_bytes)
    if mime_type is None:
        return None
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return {
        "id": f"sheet-{sheet_index}-image-{order}",
        "name": _bounded_text(getattr(image, "name", None), "Embedded image"),
        "description": _bounded_text(getattr(image, "description", None), ""),
        "mime_type": mime_type,
        "data_url": f"data:{mime_type};base64,{encoded}",
        "anchor_from": origin,
        "anchor_to": endpoint,
        "extent": image_extent,
        "order": order,
    }


def _anchor_point(
    point: Any,
    *,
    max_rows_per_sheet: int,
    max_columns_per_sheet: int,
    max_image_emu: int,
) -> dict[str, int] | None:
    if point is None:
        return None
    values = {
        "col": getattr(point, "col", None),
        "row": getattr(point, "row", None),
        "col_offset_emu": getattr(point, "colOff", None),
        "row_offset_emu": getattr(point, "rowOff", None),
    }
    if any(not isinstance(value, int) or value < 0 for value in values.values()):
        return None
    if (
        values["col"] >= max_columns_per_sheet
        or values["row"] >= max_rows_per_sheet
        or any(value > max_image_emu for value in values.values())
    ):
        return None
    return values


def _bounded_emu(value: Any, maximum: int) -> int | None:
    if not isinstance(value, int) or value <= 0:
        return None
    return value if value <= maximum else None


def _image_mime_type(image: Any, image_bytes: bytes) -> str | None:
    mime_type = _IMAGE_MIME_TYPES.get(
        str(getattr(image, "format", "") or "").casefold()
    )
    signatures = {
        "image/png": image_bytes.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": image_bytes.startswith(b"\xff\xd8\xff"),
        "image/gif": image_bytes.startswith((b"GIF87a", b"GIF89a")),
        "image/webp": image_bytes.startswith(b"RIFF")
        and image_bytes[8:12] == b"WEBP",
        "image/bmp": image_bytes.startswith(b"BM"),
    }
    if mime_type is None or not signatures.get(mime_type, False):
        return None
    return mime_type


def _bounded_text(value: Any, fallback: str) -> str:
    text = str(value or "")
    safe = "".join(character if character >= " " else " " for character in text)
    return safe[:256].strip() or fallback
