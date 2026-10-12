class McpRuntimeContextError(ValueError):
    """A public-safe MCP runtime context failure."""

    def __init__(self, code: str, *, status_code: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class McpToolDiscoveryError(ValueError):
    """Represent a bounded, public-safe MCP discovery failure category."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
