"""Closed browser observation input; clients cannot supply provenance or text."""

from pydantic import BaseModel, ConfigDict, Field

from app.runs.domain.run_performance import BROWSER_LATENCY_MAX_MS


class BrowserRunLatencyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    duration_ms: int = Field(strict=True, ge=0, le=BROWSER_LATENCY_MAX_MS)
