from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Callable, Protocol

from app.skills.api import restore_admitted_skill_manifest_authority


class ExecutorResult(Protocol):
    result: dict[str, Any]
    executor_payload: dict[str, Any]


_FORBIDDEN_ARTIFACT_MARKERS = ("/tmp/", "tenants/", "workspaces/", ":\\", ":/")
_FORBIDDEN_ARTIFACT_KEYS = {
    "storage_key",
    "local_path",
    "review_result",
    "artifact_path",
    "output_path",
    "runner",
    "runner_path",
    "executable_path",
    "cwd",
}


def sanitize_artifact_manifest(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            normalized_key = str(key).lower()
            if normalized_key in _FORBIDDEN_ARTIFACT_KEYS:
                continue
            sanitized = sanitize_artifact_manifest(item)
            if sanitized is not None:
                cleaned[key] = sanitized
        return cleaned
    if isinstance(value, list):
        cleaned_items = [sanitize_artifact_manifest(item) for item in value]
        return [item for item in cleaned_items if item is not None]
    if isinstance(value, str) and any(
        marker in value for marker in _FORBIDDEN_ARTIFACT_MARKERS
    ):
        return None
    return value


def int_payload_value(payload: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(payload.get(key) or default)
    except (TypeError, ValueError):
        return default


def _int_mapping_value(payload: dict[str, Any], *keys: str) -> int:
    for key in keys:
        try:
            value = payload.get(key)
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _usd_cost_to_minor_units(value: Any) -> int:
    try:
        minor_units = (Decimal(str(value)) * Decimal(100)).quantize(
            Decimal(1),
            rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, TypeError, ValueError):
        return 0
    return max(int(minor_units), 0)


def _sdk_usage_observability(executor_payload: dict[str, Any]) -> dict[str, Any]:
    usage = executor_payload.get("sdk_usage")
    if not isinstance(usage, dict):
        usage = {}
    input_tokens = _int_mapping_value(usage, "input_tokens", "input")
    input_tokens += _int_mapping_value(usage, "cache_creation_input_tokens")
    input_tokens += _int_mapping_value(usage, "cache_read_input_tokens")
    output_tokens = _int_mapping_value(usage, "output_tokens", "output")
    total_tokens = _int_mapping_value(usage, "total_tokens", "total")
    if total_tokens <= 0:
        total_tokens = input_tokens + output_tokens
    estimated_cost_minor = _int_mapping_value(
        usage, "estimated_cost_minor", "cost_minor"
    )
    if estimated_cost_minor <= 0:
        estimated_cost_minor = _usd_cost_to_minor_units(
            usage.get("total_cost_usd")
            or usage.get("cost_usd")
            or usage.get("estimated_cost_usd")
        )
    return {
        "token_counts": {
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens,
        },
        "cost": {"estimated_cost_minor": estimated_cost_minor},
    }


def _has_sdk_observability(executor_payload: dict[str, Any]) -> bool:
    sdk_observability = _sdk_usage_observability(executor_payload)
    token_counts = sdk_observability["token_counts"]
    return (
        token_counts["input"] > 0
        or token_counts["output"] > 0
        or token_counts["total"] > 0
        or sdk_observability["cost"]["estimated_cost_minor"] > 0
    )


def executor_observability(
    executor_payload: dict[str, Any],
    *,
    latency_ms: int,
) -> dict[str, Any]:
    sdk_observability = _sdk_usage_observability(executor_payload)
    sdk_token_counts = sdk_observability["token_counts"]
    input_tokens = int_payload_value(
        executor_payload, "input_token_count", sdk_token_counts["input"]
    )
    output_tokens = int_payload_value(
        executor_payload, "output_token_count", sdk_token_counts["output"]
    )
    total_default = sdk_token_counts["total"] or (input_tokens + output_tokens)
    total_tokens = int_payload_value(
        executor_payload, "total_token_count", total_default
    )
    return {
        "latency_ms": latency_ms,
        "token_counts": {
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens,
        },
        "cost": {
            "estimated_cost_minor": int_payload_value(
                executor_payload,
                "estimated_cost_minor",
                sdk_observability["cost"]["estimated_cost_minor"],
            ),
        },
    }


def event_observability_kwargs(
    observability: dict[str, Any], executor_payload: dict[str, Any]
) -> dict[str, Any]:
    metric_keys = {
        "input_token_count",
        "output_token_count",
        "total_token_count",
        "estimated_cost_minor",
    }
    if not any(
        key in executor_payload for key in metric_keys
    ) and not _has_sdk_observability(executor_payload):
        return {}
    token_counts = observability["token_counts"]
    return {
        "latency_ms": observability["latency_ms"],
        "input_token_count": token_counts["input"],
        "output_token_count": token_counts["output"],
        "total_token_count": token_counts["total"],
        "estimated_cost_minor": observability["cost"]["estimated_cost_minor"],
    }


def skill_snapshot_from_result(
    result: ExecutorResult,
    *,
    exact_invoked_skills: Callable[[dict[str, Any]], list[str]],
) -> dict[str, list[str]]:
    source = {**result.executor_payload, **result.result}
    snapshot: dict[str, list[str]] = {
        "allowed_skills": [],
        "staged_skills": [],
        "used_skills": [],
    }
    for key in ("allowed_skills", "staged_skills"):
        value = source.get(key)
        if isinstance(value, list):
            snapshot[key] = [str(item) for item in value]
    snapshot["used_skills"] = native_used_skills_from_result(
        result,
        exact_invoked_skills=exact_invoked_skills,
    )
    return snapshot


def native_used_skills_from_result(
    result: ExecutorResult,
    *,
    exact_invoked_skills: Callable[[dict[str, Any]], list[str]],
) -> list[str]:
    semantic_evidence = {**result.result, **result.executor_payload}
    exact_used = exact_invoked_skills(semantic_evidence)
    raw = semantic_evidence.get("used_skills")
    if not isinstance(raw, list):
        return []
    used: list[str] = []
    for item in raw:
        skill_name = str(item).strip()
        if skill_name in exact_used and skill_name not in used:
            used.append(skill_name)
    return used


def skill_manifests_from_result(
    result: ExecutorResult,
    *,
    exact_invoked_skills: Callable[[dict[str, Any]], list[str]],
) -> list[dict[str, Any]]:
    source = {**result.executor_payload, **result.result}
    raw = source.get("skill_manifests")
    if not isinstance(raw, list):
        return []
    used_skills = set(
        native_used_skills_from_result(
            result,
            exact_invoked_skills=exact_invoked_skills,
        )
    )
    used_skills_source = str(
        result.executor_payload.get("used_skills_source") or ""
    ).strip()
    manifests: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        manifest = dict(item)
        skill_id = str(manifest.get("skill_id") or "").strip()
        manifest["used"] = bool(skill_id and skill_id in used_skills)
        if manifest["used"]:
            manifest["used_skills_source"] = used_skills_source
        manifests.append(manifest)
    return manifests


def skill_manifests_for_persistence(
    result: ExecutorResult,
    admitted_manifests: list[dict[str, Any]],
    *,
    exact_invoked_skills: Callable[[dict[str, Any]], list[str]],
) -> list[dict[str, Any]]:
    return restore_admitted_skill_manifest_authority(
        skill_manifests_from_result(
            result,
            exact_invoked_skills=exact_invoked_skills,
        ),
        admitted_manifests=admitted_manifests,
    )


def dependency_ids_from_manifest(item: dict[str, Any]) -> list[str]:
    raw = item.get("dependency_ids")
    if not isinstance(raw, list):
        return []
    return [str(value) for value in raw]
