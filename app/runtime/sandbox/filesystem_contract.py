"""Filesystem mode contracts for the OpenSandbox execd adapter."""

from __future__ import annotations


def encode_execd_mode(mode: int) -> int:
    """Encode Unix permission bits as the decimal digits that execd parses in base eight.

    The OpenSandbox SDK exposes ``mode`` as an integer, while deployed execd
    parses its wire value as an octal string. This returns the integer that
    preserves the intended permission bits through that contract.
    """

    if type(mode) is not int or not 0 <= mode <= 0o777:
        raise ValueError("OpenSandbox filesystem mode is invalid")
    return int(f"{mode:o}")
