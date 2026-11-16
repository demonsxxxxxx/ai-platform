"""Browser session Run status vocabulary; terminal facts remain Runs-owned."""

def normalize_session_run_status(status: str) -> str:
    return "cancelled" if status == "canceled" else status


def browser_session_run_status(status: str) -> str:
    status = normalize_session_run_status(status)
    return {
        "succeeded": "completed",
        "failed": "error",
        "queued": "pending",
        "running": "running",
    }.get(status, status)