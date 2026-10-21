"""Platform model-token observations, independent of provider SDK and Run counters."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from dataclasses import asdict, dataclass
import re

TOKEN_FIELDS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")
BEIJING = timezone(timedelta(hours=8))
UsageReason = Literal["baseline_unknown", "report_missing", "counter_regressed", "observation_conflict", "scope_changed", "session_reset", "session_changed", "scope_overlap", "provider_identity_unknown"]
USAGE_REASONS = {"baseline_unknown", "report_missing", "counter_regressed", "observation_conflict",
                 "scope_changed", "session_reset", "session_changed", "scope_overlap", "provider_identity_unknown"}


def _validate_models(models: object) -> None:
    if not isinstance(models, dict) or len(models) > 32:
        raise ValueError("invalid_model_counts")
    for name, counts in models.items():
        if not isinstance(name, str) or not 1 <= len(name) <= 512 or any(ord(c) < 32 for c in name):
            raise ValueError("invalid_actual_model")
        name.encode("utf-8")
        if not isinstance(counts, dict) or set(counts) != set(TOKEN_FIELDS):
            raise ValueError("invalid_model_counts")
        if any(type(count) is not int or not 0 <= count <= 2**63 - 1 for count in counts.values()):
            raise ValueError("invalid_token_counter")


@dataclass(frozen=True, kw_only=True)
class ModelUsageSnapshot:
    scope_id: str
    observation_seq: int
    baseline: Literal["fresh", "restored", "unknown"]
    models: dict[str, dict[str, int]]
    coverage: Literal["reported_final", "partial", "unavailable"]
    provider_session_id: str | None = None
    baseline_models: dict[str, dict[str, int]] | None = None
    reason: UsageReason | None = None

    def __post_init__(self):
        if not isinstance(self.scope_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.scope_id) is None:
            raise ValueError("invalid_usage_scope")
        if type(self.observation_seq) is not int or not 1 <= self.observation_seq <= 2**63 - 1:
            raise ValueError("invalid_observation_seq")
        if self.baseline not in ("fresh", "restored", "unknown"):
            raise ValueError("invalid_usage_baseline")
        if self.coverage not in ("reported_final", "partial", "unavailable"):
            raise ValueError("invalid_usage_coverage")
        if self.provider_session_id is not None and (not isinstance(self.provider_session_id, str) or len(self.provider_session_id) > 128):
            raise ValueError("invalid_provider_session_id")
        if self.reason is not None and (not isinstance(self.reason, str) or self.reason not in USAGE_REASONS):
            raise ValueError("invalid_usage_reason")
        _validate_models(self.models)
        if self.baseline_models is not None:
            _validate_models(self.baseline_models)
        if self.baseline == "fresh" and self.baseline_models:
            raise ValueError("fresh_baseline_must_be_zero")
        if self.baseline == "unknown" and self.models:
            raise ValueError("unknown_baseline_has_no_counts")
        if self.coverage == "reported_final" and (not self.models or self.reason is not None):
            raise ValueError("final_report_requires_known_models")


def normalize_model_usage_snapshot(value: object) -> ModelUsageSnapshot:
    if isinstance(value, ModelUsageSnapshot):
        return ModelUsageSnapshot(**asdict(value))
    if not isinstance(value, dict):
        raise ValueError("invalid_usage_snapshot")
    try:
        return ModelUsageSnapshot(**value)
    except TypeError as exc:
        raise ValueError("invalid_usage_snapshot_fields") from exc


def cumulative_deltas(previous: dict, current: dict) -> dict | None:
    """Reject the entire observation on missing models or any counter regression."""
    if previous.keys() - current.keys():
        return None
    deltas = {}
    for model, counts in current.items():
        old = previous.get(model, {})
        delta = {field: counts[field] - old.get(field, 0) for field in TOKEN_FIELDS}
        if any(value < 0 for value in delta.values()):
            return None
        # A first explicit zero is evidence; a repeated zero adds no new row.
        if model not in previous or any(delta.values()):
            deltas[model] = delta
    return deltas


def window_utc(start: date | None, end: date | None, *, now: datetime | None = None):
    try:
        today = (now or datetime.now(timezone.utc)).astimezone(BEIJING).date()
        end = end or today
        start = start or (end - timedelta(days=29))
        if start > end or end == date.max:
            raise ValueError("usage_date_range_invalid")
        return start, end, datetime.combine(start, time.min, BEIJING).astimezone(timezone.utc), datetime.combine(end + timedelta(days=1), time.min, BEIJING).astimezone(timezone.utc)
    except OverflowError as exc:
        raise ValueError("usage_date_range_invalid") from exc


def token_totals(row: dict) -> dict:
    counts = {field: int(row[field]) for field in TOKEN_FIELDS}
    counts["input_total"] = sum(counts[field] for field in TOKEN_FIELDS[:3])
    counts["total_tokens"] = counts["input_total"] + counts["output_tokens"]
    return counts
