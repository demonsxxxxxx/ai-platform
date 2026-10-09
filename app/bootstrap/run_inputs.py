"""Composition root for durable Run interaction inputs."""

from app.platform.public_payload import sanitize_public_text
from app.runs.application.inputs import RunInputsService
from app.runs.infrastructure import inputs_postgres

# Assemble HTTP contracts for the existing router registration. The Runs
# in-process API remains independent of its HTTP transport.
from app.runs.transport.inputs import (
    RunInputSubmissionRequest as RunInputSubmissionRequest,
    RunInputsCallbackRequest as RunInputsCallbackRequest,
)


def build_run_inputs_service() -> RunInputsService:
    return RunInputsService(
        persistence=inputs_postgres,
        sanitize_text=sanitize_public_text,
    )
