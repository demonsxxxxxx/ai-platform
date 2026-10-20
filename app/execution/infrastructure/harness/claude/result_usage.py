"""Bounded cumulative ResultMessage observations for the pinned Claude CLI."""

from __future__ import annotations

import math
import re
from typing import Any


_MAX_COUNTER = 2**31 - 1
_MAX_COST_USD = _MAX_COUNTER / 100
_TOKEN_FIELDS = {
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "cacheReadInputTokens": "cache_read_input_tokens",
    "cacheCreationInputTokens": "cache_creation_input_tokens",
}
_RESET_COMMAND = re.compile(r"^/(?:clear|reset|new)(?:\s|$)")


def _cost(value: object) -> int | float | None:
    if type(value) not in {int, float} or not 0 <= value <= _MAX_COST_USD:
        return None
    return value if math.isfinite(value) else None


def _models(value: object) -> dict[str, dict[str, int | float]] | None:
    if not isinstance(value, dict) or not 1 <= len(value) <= 32:
        return None
    result = {}
    for name, usage in value.items():
        if (not isinstance(name, str) or not 1 <= len(name) <= 512
            or any(ord(char) < 32 for char in name) or not isinstance(usage, dict)):
            return None
        try:
            name.encode("utf-8")
        except UnicodeError:
            return None
        counts = {field: usage.get(field) for field in _TOKEN_FIELDS}
        if any(type(count) is not int or not 0 <= count <= _MAX_COUNTER for count in counts.values()):
            return None
        cost = _cost(usage.get("costUSD"))
        result[name] = {**counts, **({"costUSD": cost} if cost is not None else {})}
    return result


class ClaudeResultUsage:
    """Keep turn counters separate from call-cumulative cost/model snapshots.

    SDK 0.2.165 / CLI 2.1.294 restores cumulative totals from cost-state entries.
    Subtract that immutable preload baseline; ResultMessage.usage remains per turn.
    This is private telemetry, never billing or execution authority.
    """

    def __init__(self, *, session_id: str | None, prompt: str,
                 resumed_entries: list[dict[str, Any]] | None = None) -> None:
        self._session_id = session_id
        self._baseline_cost: int | float = 0
        self._baseline_models: dict[str, dict[str, int | float]] = {}
        self._baseline_valid = True
        for entry in reversed(resumed_entries or []):
            if entry.get("type") == "cost-state" and entry.get("sessionId") == session_id:
                cost, models = _cost(entry.get("totalCostUSD")), _models(entry.get("modelUsage"))
                if cost == 0 and entry.get("modelUsage") == {}:
                    models = {}
                self._baseline_valid = cost is not None and models is not None
                if cost is not None and models is not None:
                    self._baseline_cost, self._baseline_models = cost, models
                break
        self._total_cost: int | float | None = None
        self._model_usage: dict[str, dict[str, int | float]] = {}
        self._last_cost_valid = False
        self._last_models_valid = False
        self._main_usage_seen = False
        self._partial = not self._baseline_valid
        self.observe_input(prompt)

    def observe_input(self, text: str) -> None:
        # Do not change the input or guess a total spanning an explicit reset.
        if _RESET_COMMAND.match(text.lstrip()):
            self._partial = True

    def observe_result(self, message: object) -> None:
        try:
            self._observe_result(message)
        except Exception:
            # Optional telemetry must not interrupt an otherwise valid Run.
            self._partial = True
            self._last_cost_valid = self._last_models_valid = False

    def _observe_result(self, message: object) -> None:
        session = getattr(message, "session_id", None)
        if self._session_id is None:
            self._session_id = session
        elif session != self._session_id:
            self._partial = True
        usage = getattr(message, "usage", None)
        self._main_usage_seen |= isinstance(usage, dict) and bool(usage)
        cost = _cost(getattr(message, "total_cost_usd", None))
        models = _models(getattr(message, "model_usage", None))
        if not self._baseline_valid:
            cost = models = None
        else:
            if cost is not None:
                cost = _cost(cost - self._baseline_cost)
                self._partial |= cost is None
            if models is not None:
                for name, baseline in self._baseline_models.items():
                    if name not in models or any(models[name][key] < baseline[key] for key in _TOKEN_FIELDS):
                        models = None
                        self._partial = True
                        break
                    for key in _TOKEN_FIELDS:
                        models[name][key] -= baseline[key]
                    if "costUSD" in models[name]:
                        delta = _cost(models[name]["costUSD"] - baseline["costUSD"]) if "costUSD" in baseline else None
                        if delta is None:
                            models[name].pop("costUSD")
                            self._partial = True
                        else:
                            models[name]["costUSD"] = delta
        self._last_cost_valid = cost is not None
        self._last_models_valid = models is not None
        if getattr(message, "subtype", None) == "error_during_execution":
            # A crashed CLI can emit zeroed totals. Retain earlier observations
            # as partial rather than overwriting them with that false zero.
            self._partial = True
            if cost == 0:
                cost = None
            if models is not None and not any(any(row.values()) for row in models.values()):
                models = None
        if cost is not None:
            if self._total_cost is not None and cost < self._total_cost:
                self._partial = True
            else:
                self._total_cost = cost
        if models is not None:
            regressed = any(
                name not in models or any(models[name][key] < old[key] for key in _TOKEN_FIELDS)
                for name, old in self._model_usage.items()
            )
            if regressed:
                self._partial = True
            else:
                self._model_usage = models

    def project(self, turn_usage: dict[str, Any], *, terminal_result: bool) -> dict[str, Any]:
        """Produce the existing private sdk_usage carrier, without raw extras."""
        result = dict(turn_usage)
        try:
            if (not self._main_usage_seen and terminal_result and not self._partial
                and self._last_models_valid):
                counts = {
                    target: sum(row[source] for row in self._model_usage.values())
                    for source, target in _TOKEN_FIELDS.items()
                }
                if sum(counts.values()) <= _MAX_COUNTER:
                    result.update(counts)
            if self._total_cost is not None:
                result["total_cost_usd"] = self._total_cost
            if self._model_usage:
                result["model_usage"] = {name: dict(row) for name, row in self._model_usage.items()}
            result["cost_coverage"] = (
                "complete" if terminal_result and not self._partial and self._last_cost_valid
                else "partial" if self._total_cost is not None else "unavailable"
            )
        except Exception:
            result["cost_coverage"] = "partial"
        return result
