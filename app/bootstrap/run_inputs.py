"""Composition root for durable Run interaction inputs."""

from app.runs.application.inputs import RunInputsService

# Assemble HTTP contracts for the existing router registration. The Runs
# in-process API remains independent of its HTTP transport.
from app.runs.transport.inputs import (
    RunInputSubmissionRequest as RunInputSubmissionRequest,
    RunInputsCallbackRequest as RunInputsCallbackRequest,
)


def build_run_inputs_service() -> RunInputsService:
    from app.bootstrap.recovery_operations import build_recovery_operations
    return build_recovery_operations()[1]
