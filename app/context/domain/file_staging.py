from __future__ import annotations


def context_stage_filename_fits(filename: str) -> bool:
    try:
        return len(filename.encode("utf-8")) <= 255
    except UnicodeEncodeError:
        return False
