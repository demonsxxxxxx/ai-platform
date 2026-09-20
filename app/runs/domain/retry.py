"""Run retry eligibility owned by the Runs bounded context."""


RUN_CONTROL_RETRY_PREVIEW_STATUSES = frozenset(
    {"failed", "dead-letter", "dead_letter", "dead-lettered"}
)
_MCP_EXECUTION_UNCERTAIN_ERROR_CODES = frozenset(
    {
        "mcp_execution_succeeded_receipt_incomplete",
        "mcp_execution_outcome_unknown",
    }
)


def run_retry_block_reason(status: object, error_code: object) -> str | None:
    if str(status or "") not in RUN_CONTROL_RETRY_PREVIEW_STATUSES:
        return "status_not_retryable"
    if str(error_code or "") in _MCP_EXECUTION_UNCERTAIN_ERROR_CODES:
        return "execution_outcome_unconfirmed"
    return None
