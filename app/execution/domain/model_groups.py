"""Pure policy for tenant model groups and deterministic model routing."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

DEFAULT_GROUP_CODE = "default"
DEFAULT_GROUP_TYPE = "ordinary_default"
BUSINESS_GROUP_TYPE = "business"
SELECTION_AUTO = "auto"
SELECTION_EXPLICIT = "explicit"
USAGE_SCENE_CHAT = "chat"
USAGE_SCENE_INDEPENDENT = "independent"
USAGE_SCENE_SKILL = "skill"
USAGE_SCENE_EXPERT = "expert"


@dataclass(frozen=True)
class ModelGroupSelection:
    group_id: str
    group_code: str
    group_type: str
    group_revision: int
    policy_version: str
    selection_mode: str
    model_id: str
    model_value: str
    gateway_revision: int
    auto_compact_window: int
    max_output_tokens: int
    priority: int | None = None
    weight: int | None = None


class ModelGroupPolicyError(ValueError):
    """Stable model-group policy error code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def normalize_group_name(value: object) -> str:
    if not isinstance(value, str):
        raise ModelGroupPolicyError("model_group_name_invalid")
    value = value.strip()
    if not value or len(value) > 160:
        raise ModelGroupPolicyError("model_group_name_invalid")
    return value


def normalize_group_code(value: object) -> str:
    if not isinstance(value, str):
        raise ModelGroupPolicyError("model_group_code_invalid")
    value = value.strip()
    if not re.fullmatch(r"[a-z0-9_-]{1,64}", value):
        raise ModelGroupPolicyError("model_group_code_invalid")
    return value


def validate_group_kind(*, code: str, group_type: str, group_id: str | None = None) -> None:
    if group_type == DEFAULT_GROUP_TYPE:
        if code != DEFAULT_GROUP_CODE or group_id is None:
            raise ModelGroupPolicyError("model_default_group_immutable")
    elif group_type != BUSINESS_GROUP_TYPE:
        raise ModelGroupPolicyError("model_group_type_invalid")


def choose_weighted_model(rows: Sequence[Mapping[str, Any]], *, rng: random.Random | None = None) -> Mapping[str, Any]:
    if not rows:
        raise ModelGroupPolicyError("group_models_unavailable")
    highest = max(int(row["priority"]) for row in rows)
    candidates = [row for row in rows if int(row["priority"]) == highest]
    total = sum(int(row["weight"]) for row in candidates)
    if total <= 0:
        raise ModelGroupPolicyError("group_models_unavailable")
    chooser = rng or random.SystemRandom()
    pick = chooser.uniform(0, total)
    for row in candidates:
        pick -= int(row["weight"])
        if pick <= 0:
            return row
    return candidates[-1]


def public_group_projection(row: Mapping[str, Any], models: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "code": str(row["code"]),
        "name": str(row["name"]),
        "type": str(row["type"]),
        "enabled": bool(row["enabled"]),
        "revision": int(row["revision"]),
        "models": [
            {
                "id": str(model["model_id"]),
                "value": str(model["model_value"]),
                "label": str(model.get("display_name") or model["model_value"]),
                "enabled": bool(model["enabled"]),
            }
            for model in models
        ],
    }
