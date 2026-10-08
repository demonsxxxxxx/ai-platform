"""Bind Worker execution ownership to durable Artifact cleanup."""

from functools import partial
from typing import Any

from app.execution.api import build_artifact_execution_owner, submit_run_until_cancelled
from app.executors.base import RunExecutionOwner
from app.persistence.artifacts import reserve_provisional_artifact_cleanup


submit_worker_run_until_cancelled = partial(
    submit_run_until_cancelled, owner_factory=RunExecutionOwner,
)


def build_worker_execution_owner(run_payload: Any, transaction_factory: Any) -> RunExecutionOwner:
    return build_artifact_execution_owner(
        run_payload, transaction_factory,
        reserve_provisional_artifact_cleanup, RunExecutionOwner,
    )
