"""Observe the Runs commit boundary without changing its application service."""

from __future__ import annotations

from typing import Any

from app.platform.tracing import TerminalStageTiming
from app.runs.application.worker_result_commit import (
    AsyncPort,
    WorkerAttemptCommitAuthority,
    WorkerResultCommitCommand,
    WorkerResultCommitOutcome,
    WorkerResultCommitService,
)


class TimedWorkerResultCommitService(WorkerResultCommitService):
    """Technical decorator retaining the application's constructor and contract."""

    async def commit(
        self,
        command: WorkerResultCommitCommand,
        *,
        attempt_authority: WorkerAttemptCommitAuthority,
        capabilities: Any,
        release_runtime_lease: AsyncPort,
    ) -> WorkerResultCommitOutcome:
        with TerminalStageTiming(
            "result_business_commit", run_id=command.payload.run_id,
            attempt_id=command.attempt_id, terminal_status=command.result.status,
        ) as timing:
            outcome = await super().commit(
                command,
                attempt_authority=attempt_authority,
                capabilities=capabilities,
                release_runtime_lease=release_runtime_lease,
            )
            timing.terminal_status = outcome.status
            return outcome


async def observe_worker_result_publication(
    publish: AsyncPort, capabilities: Any,
    command: WorkerResultCommitCommand, outcome: WorkerResultCommitOutcome,
) -> None:
    """Observe the existing post-commit publication without owning its order."""
    with TerminalStageTiming(
        "result_business_publication", run_id=command.payload.run_id,
        attempt_id=command.attempt_id, terminal_status=outcome.status,
    ):
        await publish(capabilities, tenant_id=command.payload.tenant_id, run_id=command.payload.run_id)
