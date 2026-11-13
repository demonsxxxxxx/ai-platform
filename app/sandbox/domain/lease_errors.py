"""Stable errors raised by Sandbox lease persistence operations."""


class SandboxLeaseReleaseScopeMismatchError(RuntimeError):
    """Raised when a release fence collides with another lease scope."""


class SandboxExecutorTerminalConflictError(RuntimeError):
    """Raised when one execution attempt reports two different terminal results."""
