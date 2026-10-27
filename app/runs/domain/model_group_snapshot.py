"""Validation of the immutable group authorization carried by a Run."""
from __future__ import annotations

import re
from typing import Any


def validate_model_group_snapshot(
    *, group_id: Any, group_code: Any, group_type: Any, group_revision: Any,
    selection_mode: Any, usage_scene: Any, policy_version: Any,
) -> None:
    if not all(isinstance(v, str) and v.strip() == v and v for v in (
        group_id, group_code, group_type, selection_mode, usage_scene, policy_version,
    )):
        raise ValueError("run_model_group_snapshot_invalid")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", group_id) or not re.fullmatch(r"[a-z0-9_-]{1,64}", group_code):
        raise ValueError("run_model_group_snapshot_invalid")
    if type(group_revision) is not int or group_revision < 1:
        raise ValueError("run_model_group_snapshot_invalid")
    if group_type not in {"business", "ordinary_default"} or selection_mode not in {"auto", "explicit"}:
        raise ValueError("run_model_group_snapshot_invalid")
    if usage_scene not in {"chat", "expert", "skill", "independent"} or policy_version != "model-groups.v1":
        raise ValueError("run_model_group_snapshot_invalid")
    if (group_type == "ordinary_default") != (group_code == "default"):
        raise ValueError("run_model_group_snapshot_invalid")
    if group_type == "ordinary_default" and usage_scene in {"chat", "expert"}:
        raise ValueError("run_model_group_snapshot_invalid")
