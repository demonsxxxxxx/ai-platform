"""Shared PostgreSQL persistence errors."""


class RepositoryConflictError(ValueError):
    """Signal a persistence invariant or optimistic-fence conflict."""

    pass


class RepositoryNotFoundError(ValueError):
    """Signal that a requested persistence record does not exist."""

    pass


class RepositoryAuthorizationError(ValueError):
    """Signal a fail-closed repository capability authorization denial."""

    def __init__(self, message: str, *, denial: object | None = None) -> None:
        super().__init__(message)
        self.denial = denial
