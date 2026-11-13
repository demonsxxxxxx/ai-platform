"""Run retry eligibility owned by the Runs bounded context."""

from app.runs.domain.run_status import RUN_RETRYABLE_STATUSES as RUN_RETRYABLE_STATUSES

# Kept as a name alias for callers of the existing public retry-preview contract.
RUN_CONTROL_RETRY_PREVIEW_STATUSES = RUN_RETRYABLE_STATUSES
_MCP_EXECUTION_UNCERTAIN_ERROR_CODES = frozenset(
    {
        "mcp_execution_succeeded_receipt_incomplete",
        "mcp_execution_outcome_unknown",
    }
)


def run_unconfirmed_execution_block_reason(error_code: object) -> str | None:
    if str(error_code or "") in _MCP_EXECUTION_UNCERTAIN_ERROR_CODES:
        return "execution_outcome_unconfirmed"
    return None


def run_retry_block_reason(status: object, error_code: object) -> str | None:
    if str(status or "") not in RUN_RETRYABLE_STATUSES:
        return "status_not_retryable"
    return run_unconfirmed_execution_block_reason(error_code)
