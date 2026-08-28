from __future__ import annotations

import re
from collections.abc import Mapping


_SAFE_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def legacy_queue_model_snapshot(input_json: object) -> tuple[str, str]:
    """Validate the model pair retained by a pre-snapshot queue payload."""

    if not isinstance(input_json, Mapping):
        raise ValueError("run_model_source_legacy_invalid")
    model_id = input_json.get("model_id")
    model_value = input_json.get("model_value")
    if (
        not isinstance(model_id, str)
        or _SAFE_MODEL_ID.fullmatch(model_id) is None
        or not isinstance(model_value, str)
        or not model_value
        or model_value != model_value.strip()
        or len(model_value.encode("utf-8")) > 512
        or any(ord(char) < 32 or ord(char) == 127 for char in model_value)
    ):
        raise ValueError("run_model_source_legacy_invalid")
    return model_id, model_value
