"""Validate and bound the private native SessionStore callback response."""

from typing import Any, Literal

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_serializer

from app.context.application.provider_sessions import ProviderSessionOperationResult
from app.context.domain.provider_sessions import MAX_PROVIDER_SESSION_CALLBACK_RESPONSE_BYTES


class ProviderSessionCallbackResponse(BaseModel):
    """Private callback receipt carrying no platform scope claims."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["load", "append", "list_subkeys"]
    entries: list[dict[str, Any]] = Field(default_factory=list)
    subpaths: list[str] = Field(default_factory=list, max_length=4096)
    accepted: bool = True
    entry_count: int = Field(default=0, ge=0)
    next_sequence: int = Field(ge=1)
    last_sequence: int | None = Field(default=None, ge=1)
    child_entries: dict[str, list[dict[str, Any]]] | None = Field(default=None, max_length=4096)

    @model_serializer(mode="wrap")
    def serialize_optional_child_entries(self, handler):
        # Old clients retain the exact original fields, including other nulls.
        body = handler(self)
        if self.child_entries is None:
            body.pop("child_entries", None)
        return body


ProviderSessionCallbackWireResponse = ProviderSessionCallbackResponse | JSONResponse


def build_provider_session_callback_response(
    result: ProviderSessionOperationResult, *, include_child_entries: bool,
) -> ProviderSessionCallbackWireResponse:
    """Project the typed receipt and measure the exact optional-view wire body."""
    response = ProviderSessionCallbackResponse(
        action=result.action,
        entries=list(result.entries),
        subpaths=list(result.subpaths),
        accepted=result.accepted,
        entry_count=result.entry_count,
        next_sequence=result.next_sequence,
        last_sequence=result.last_sequence,
        child_entries=result.child_entries if include_child_entries else None,
    )
    if not include_child_entries:
        return response
    # Return these already validated, measured bytes, not a second encoding.
    wire_response = JSONResponse(content=jsonable_encoder(response))
    if len(wire_response.body) > MAX_PROVIDER_SESSION_CALLBACK_RESPONSE_BYTES:
        response.child_entries = None
        wire_response = JSONResponse(content=jsonable_encoder(response))
    return wire_response
