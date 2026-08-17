"""Pure values exchanged by the SSE v3 live fan-out plane."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LivePublication:
    channel: str
    redis_id: str
    envelope_json: str

    @property
    def byte_size(self) -> int:
        return len(self.channel.encode("utf-8")) + len(self.redis_id.encode("utf-8")) + len(
            self.envelope_json.encode("utf-8")
        )
